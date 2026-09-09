"""Tests for the connection-facing MCP tools.

``connect_maxsurf`` used to report only a class name and a version, and warned
that it might have launched Maxsurf and could not tell you which instance it
had reached. It now reports the session it established, and ``reset_connection``
gives an operator a way out of a stale or mis-identified connection without
restarting the server.
"""

from __future__ import annotations

import json
import unittest

from maxsurf_mcp import audit, com, config, session
from maxsurf_mcp.errors import (
    MaxsurfConnectionError,
    MaxsurfSafetyError,
    MaxsurfValidationError,
)

from .support import (
    ComFake,
    FakeGencache,
    FakeProcesses,
    FakeWin32Process,
    IsolatedEnvTestCase,
)

MODELER_PROGID = "BentleyModeler.Application"
STABILITY_PROGID = "BentleyStability.Application"


class ConnectMaxsurfTests(IsolatedEnvTestCase):
    def test_connecting_reports_the_established_session(self):
        self.install_com_environment(
            running={MODELER_PROGID: ComFake(Version="25.00.02.339",
                                             Design=ComFake())},
            clsids={MODELER_PROGID: "{1234ABCD-0000-0000-0000-000000000000}"},
        )

        payload = json.loads(com.connect_maxsurf("modeler"))

        self.assertEqual(payload["connected"], "modeler")
        self.assertEqual(payload["session"]["progid"], MODELER_PROGID)
        self.assertEqual(payload["session"]["clsid"],
                         "{1234ABCD-0000-0000-0000-000000000000}")
        self.assertEqual(payload["session"]["attach_method"], session.ROT_ATTACH)
        self.assertEqual(payload["session"]["liveness"], session.ALIVE)
        self.assertEqual(payload["policy"]["configured_default"],
                         config.ACTIVATE_EXISTING)
        self.assertEqual(payload["policy"]["effective"],
                         config.ACTIVATE_EXISTING)
        self.assertIsNone(payload["policy"]["requested"])

    def test_the_reported_warnings_name_what_could_not_be_proven(self):
        self.install_com_environment(
            running={MODELER_PROGID: ComFake(Version="25", Design=ComFake())})

        payload = json.loads(com.connect_maxsurf("modeler"))
        warnings = " ".join(payload["warnings"])

        self.assertIn("process id", warnings)
        self.assertIn("open design cannot be identified", warnings)

    def test_a_fully_identified_session_reports_no_warnings(self):
        app = ComFake(Version="25",
                      Design=ComFake(Path="C:/ws/hull.msd", Name="hull"))
        app._oleobj_.GetWindow = lambda: 0x99
        self.install_com_environment(running={STABILITY_PROGID: app},
                                     process=FakeWin32Process(pid=7))

        payload = json.loads(com.connect_maxsurf("stability"))

        self.assertEqual(payload["warnings"], [])
        self.assertEqual(payload["session"]["com_host_pid"], 7)
        self.assertEqual(payload["session"]["com_host_pid_status"], "proven")
        self.assertEqual(payload["session"]["design_path"], "C:/ws/hull.msd")

    def test_connecting_when_nothing_is_running_raises(self):
        self.install_com_environment(running={})
        with self.assertRaises(MaxsurfConnectionError):
            com.connect_maxsurf("modeler")

    def test_connecting_is_audited_as_a_state_change(self):
        self.install_com_environment(
            running={MODELER_PROGID: ComFake(Version="25", Design=ComFake())})
        com.connect_maxsurf("modeler")

        records = self.records_for("connect_maxsurf")
        self.assertEqual([r["stage"] for r in records], ["intent", "complete"])
        self.assertEqual(records[0]["classification"],
                         audit.Classification.WRITE_STATE)
        self.assertEqual(records[-1]["outcome"], audit.Outcome.SUCCESS)

    def test_a_disallowed_module_is_refused_by_the_tool(self):
        # motions is allowed by default now, so restrict the allow-list to
        # prove the refusal path with a module the configuration excludes.
        self.install_com_environment(running={})
        self.set_env(config.ALLOWED_MODULES_VAR, "modeler,resistance")
        with self.assertRaises(MaxsurfSafetyError):
            com.connect_maxsurf("motions")

    def test_an_explicit_policy_argument_overrides_the_configured_default(self):
        """The caller can reach Maxsurf without reconfiguring the server."""
        app = ComFake(Version="25.00.02.339", Design=ComFake())
        gencache = FakeGencache(creatable={MODELER_PROGID: app})
        self.install_com_environment(
            running={}, creatable={MODELER_PROGID: app}, gencache=gencache,
            processes=FakeProcesses(table={"maxsurfmodeler.exe": [6456]}))

        payload = json.loads(
            com.connect_maxsurf("modeler", policy=config.ACTIVATE_EXISTING))

        self.assertEqual(gencache.ensure_calls, [MODELER_PROGID])
        self.assertEqual(payload["policy"]["requested"],
                         config.ACTIVATE_EXISTING)
        self.assertEqual(payload["policy"]["effective"],
                         config.ACTIVATE_EXISTING)
        self.assertEqual(payload["policy"]["configured_default"],
                         config.ACTIVATE_EXISTING)
        self.assertEqual(payload["session"]["attach_method"],
                         session.COM_ACTIVATION)
        self.assertEqual(
            payload["session"]["activation"]["candidate_process_pids_before"],
            [6456])

    def test_the_payload_never_passes_activation_off_as_an_attach(self):
        app = ComFake(Version="25", Design=ComFake())
        self.install_com_environment(
            running={}, creatable={MODELER_PROGID: app},
            gencache=FakeGencache(creatable={MODELER_PROGID: app}),
            processes=FakeProcesses(table={"maxsurfmodeler.exe": [6456]}))

        payload = json.loads(
            com.connect_maxsurf("modeler", policy=config.ACTIVATE_EXISTING))

        self.assertTrue(
            any("came from COM activation" in warning
                for warning in payload["warnings"]), payload["warnings"])

    def test_an_unexpected_new_process_surfaces_in_the_tool_payload(self):
        app = ComFake(Version="25", Design=ComFake())
        procs = FakeProcesses(table={"maxsurfmodeler.exe": [6456]})
        self.install_com_environment(
            running={}, creatable={MODELER_PROGID: app},
            gencache=FakeGencache(
                creatable={MODELER_PROGID: app},
                on_ensure=lambda _p: procs.spawn("MaxsurfModeler.exe", 31337)),
            processes=procs)

        payload = json.loads(
            com.connect_maxsurf("modeler", policy=config.ACTIVATE_EXISTING))

        self.assertEqual(payload["session"]["activation"]["status"],
                         session.UNEXPECTED_PROCESS)
        self.assertTrue(any("31337" in warning
                            for warning in payload["warnings"]),
                        payload["warnings"])

    def test_an_unknown_policy_argument_is_a_validation_error(self):
        self.install_com_environment(running={})
        with self.assertRaises(MaxsurfValidationError):
            com.connect_maxsurf("modeler", policy="just_start_it")


class ResetConnectionTests(IsolatedEnvTestCase):
    def test_reset_reports_what_it_dropped_and_closes_nothing(self):
        self.install_com_environment(
            running={MODELER_PROGID: ComFake(Version="25", Design=ComFake())})
        com.connect_maxsurf("modeler")

        payload = json.loads(com.reset_connection())

        self.assertEqual(payload["reset"], ["modeler"])
        self.assertIn("modeler", payload["previous_sessions"])
        self.assertIn("no application was closed", payload["note"])
        self.assertIsNone(com.session_for("modeler"))

    def test_reset_can_target_a_single_module(self):
        self.install_com_environment(
            running={MODELER_PROGID: ComFake(Version="25", Design=ComFake()),
                     STABILITY_PROGID: ComFake(Version="25",
                                               Design=ComFake(Path="p",
                                                              Name="n"))})
        com.connect_maxsurf("modeler")
        com.connect_maxsurf("stability")

        payload = json.loads(com.reset_connection("stability"))

        self.assertEqual(payload["reset"], ["stability"])
        self.assertIsNotNone(com.session_for("modeler"))

    def test_reset_lets_the_next_call_reattach(self):
        first = ComFake(Version="25", Design=ComFake())
        client = self.install_com_environment(running={MODELER_PROGID: first})
        com.connect_maxsurf("modeler")

        second = ComFake(Version="26", Design=ComFake())
        client.running[MODELER_PROGID] = second
        com.reset_connection("modeler")

        self.assertIs(com.connect("modeler"), second)
        self.assertEqual(com.session_for("modeler").version, "26")

    def test_reset_is_audited_as_a_state_change(self):
        com.reset_connection()
        records = self.records_for("reset_connection")

        self.assertEqual([r["stage"] for r in records], ["intent", "complete"])
        self.assertEqual(records[0]["classification"],
                         audit.Classification.WRITE_STATE)

    def test_resetting_with_no_sessions_succeeds(self):
        payload = json.loads(com.reset_connection())
        self.assertEqual(payload["reset"], [])


class SessionStatusTests(IsolatedEnvTestCase):
    def test_status_reports_sessions_the_worker_and_the_policy(self):
        self.install_com_environment(
            running={MODELER_PROGID: ComFake(Version="25", Design=ComFake())})
        com.connect_maxsurf("modeler")

        payload = json.loads(com.session_status())

        self.assertIn("modeler", payload["sessions"]["sessions"])
        self.assertEqual(payload["policy"]["attach_policy"],
                         config.ACTIVATE_EXISTING)
        self.assertIn("com_worker", payload)

    def test_status_touches_no_com_member(self):
        app = ComFake(Version="25", Design=ComFake())
        self.install_com_environment(running={MODELER_PROGID: app})
        com.connect_maxsurf("modeler")
        before = app._oleobj_.query_count

        com.session_status()

        self.assertEqual(app._oleobj_.query_count, before)
        self.assertEqual(self.worker.calls.count("session_status"), 0)

    def test_status_works_with_no_sessions_at_all(self):
        payload = json.loads(com.session_status())
        self.assertEqual(payload["sessions"]["sessions"], {})

    def test_status_reports_running_maxsurf_executables(self):
        """"Running but unreachable" must be distinguishable from "not running"."""
        self.install_com_environment(
            running={},
            processes=FakeProcesses(table={"maxsurfmodeler.exe": [6456]}))

        payload = json.loads(com.session_status())

        self.assertEqual(payload["processes"]["modeler"],
                         {"executables": ["MaxsurfModeler.exe"],
                          "running": True, "pids": [6456],
                          "inspectable": True, "detail": None})
        self.assertFalse(payload["processes"]["stability"]["running"])
        self.assertEqual(payload["sessions"]["sessions"], {})


class CompatibilityTests(IsolatedEnvTestCase):
    def test_the_early_binding_argument_is_still_accepted(self):
        app = ComFake(Version="25", Design=ComFake())
        self.install_com_environment(running={MODELER_PROGID: app})

        self.assertIs(com.connect("modeler", early_binding=False), app)

    def test_the_progid_hints_still_describe_the_application_modules(self):
        self.assertEqual(sorted(com.PROGID_HINTS),
                         ["modeler", "motions", "multiframe", "resistance", "stability"])
        self.assertEqual(com.PROGID_HINTS["modeler"][0], MODELER_PROGID)

    def test_probe_still_distinguishes_absent_from_failing_members(self):
        class Awkward(ComFake):
            @property
            def Explodes(self):  # noqa: N802 - mirrors a COM property name
                raise RuntimeError("getter failed")

        obj = Awkward(Present="value")
        self.assertEqual(com.probe(obj, "Missing", "Present")["status"], "read")
        failed = com.probe(obj, "Explodes")
        self.assertEqual(failed["status"], "unavailable")
        self.assertEqual(failed["attempts"][0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
