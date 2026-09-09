"""Tests for the managed Maxsurf session lifecycle.

The behaviours asserted here are the ones a user notices going wrong: Maxsurf
being started when nobody asked, a reconnect quietly landing on a different
design, or a stale pointer forcing a server restart. Every COM binding is faked,
so no Maxsurf process is involved.
"""

from __future__ import annotations

import unittest

from maxsurf_mcp import audit, config, session
from maxsurf_mcp.errors import (
    MaxsurfConnectionError,
    MaxsurfSafetyError,
    MaxsurfValidationError,
)

from .support import (
    ComError,
    ComFake,
    FakeGencache,
    FakeProcesses,
    FakeWin32Process,
    IsolatedEnvTestCase,
    OleObjFake,
    SERVER_GONE,
)

MODELER_PROGID = "BentleyModeler.Application"
STABILITY_PROGID = "BentleyStability.Application"
XMLGRID_PROGID = "BentleyStability.XMLGrid"


def modeler_app(version: str = "25.00.02.339") -> ComFake:
    return ComFake(Version=version, Design=ComFake())


def stability_app(path: str = "C:/ws/hull.msd",
                  name: str = "hull") -> ComFake:
    return ComFake(Version="25.00.02.339",
                   Design=ComFake(Path=path, Name=name))


class AttachPolicyTests(IsolatedEnvTestCase):
    """attach_only must never start Maxsurf; launching is opt-in only.

    These tests pin ``attach_only`` explicitly because it is no longer the
    default; the default is the process-gated activation policy, asserted
    separately below.
    """

    def setUp(self):
        super().setUp()
        self.set_env(config.ATTACH_POLICY_VAR, config.ATTACH_ONLY)

    def test_attach_only_uses_a_running_instance(self):
        app = modeler_app()
        client = self.install_com_environment(
            running={MODELER_PROGID: app},
            clsids={MODELER_PROGID: "{11111111-0000-0000-0000-000000000000}"},
        )

        self.assertIs(self.sessions.get("modeler"), app)
        self.assertEqual(client.dispatch_calls, [])
        established = self.sessions.session_for("modeler")
        self.assertEqual(established.attach_method, session.ROT_ATTACH)
        self.assertEqual(established.attach_policy, config.ATTACH_ONLY)

    def test_attach_only_never_launches_and_says_so(self):
        client = self.install_com_environment(
            running={}, creatable={MODELER_PROGID: modeler_app()},
        )

        with self.assertRaises(MaxsurfConnectionError) as caught:
            self.sessions.get("modeler")

        self.assertEqual(client.dispatch_calls, [],
                         "attach_only created a COM object")
        self.assertFalse(caught.exception.details["may_activate"])
        self.assertIn("never performs COM activation", str(caught.exception))

    def test_attach_only_never_activates_even_when_maxsurf_is_running(self):
        """The strictness of attach_only is what makes it worth keeping.

        Everything activation needs is available here -- the executable is
        running, EnsureDispatch would succeed -- and attach_only must still
        refuse, because the object is not in the ROT.
        """
        app = modeler_app()
        gencache = FakeGencache(creatable={MODELER_PROGID: app})
        client = self.install_com_environment(
            running={}, creatable={MODELER_PROGID: app}, gencache=gencache,
            processes=FakeProcesses(table={"maxsurfmodeler.exe": [6456]}),
        )

        with self.assertRaises(MaxsurfConnectionError) as caught:
            self.sessions.get("modeler", policy=config.ATTACH_ONLY)

        self.assertEqual(gencache.ensure_calls, [])
        self.assertEqual(client.dispatch_calls, [])
        self.assertEqual(client.get_active_calls,
                         list(session.MODULES["modeler"].progids))
        self.assertIn("Running Object Table only", str(caught.exception))
        self.assertIn(config.ACTIVATE_EXISTING, str(caught.exception))

    def test_require_pid_never_activates_either(self):
        app = modeler_app()
        gencache = FakeGencache(creatable={MODELER_PROGID: app})
        client = self.install_com_environment(
            running={}, creatable={MODELER_PROGID: app}, gencache=gencache,
            processes=FakeProcesses(table={"maxsurfmodeler.exe": [6456]}),
        )
        self.set_env(config.ATTACH_POLICY_VAR, config.REQUIRE_PID)

        with self.assertRaises(MaxsurfConnectionError):
            self.sessions.get("modeler")

        self.assertEqual(gencache.ensure_calls, [])
        self.assertEqual(client.dispatch_calls, [])

    def test_attach_only_does_not_consult_the_process_list(self):
        """It has no need to: it never activates, so it has nothing to gate."""
        procs = FakeProcesses(table={"maxsurfmodeler.exe": [6456]})
        self.install_com_environment(running={}, processes=procs)

        with self.assertRaises(MaxsurfConnectionError):
            self.sessions.get("modeler", policy=config.ATTACH_ONLY)
        self.assertEqual(procs.find_calls, [])

    def test_every_candidate_progid_is_tried_before_failing(self):
        client = self.install_com_environment(running={})
        with self.assertRaises(MaxsurfConnectionError) as caught:
            self.sessions.get("modeler")

        self.assertEqual(client.get_active_calls,
                         list(session.MODULES["modeler"].progids))
        self.assertEqual(
            len(caught.exception.details["attempted"]),
            len(session.MODULES["modeler"].progids),
        )

    def test_launch_if_absent_launches_only_when_explicitly_selected(self):
        app = modeler_app()
        client = self.install_com_environment(
            running={}, creatable={MODELER_PROGID: app},
        )
        self.set_env(config.ATTACH_POLICY_VAR, config.LAUNCH_IF_ABSENT)

        self.assertIs(self.sessions.get("modeler"), app)
        self.assertEqual(client.dispatch_calls, [MODELER_PROGID])
        self.assertEqual(
            self.sessions.session_for("modeler").attach_method,
            session.COM_ACTIVATION,
        )

    def test_launch_if_absent_records_that_it_created_a_process(self):
        """Creating one is this policy's job, so it is not called unexpected."""
        app = modeler_app()
        procs = FakeProcesses(table={})
        self.install_com_environment(
            running={}, creatable={MODELER_PROGID: app},
            gencache=FakeGencache(
                creatable={MODELER_PROGID: app},
                on_ensure=lambda _p: procs.spawn("MaxsurfModeler.exe", 555)),
            processes=procs)
        self.set_env(config.ATTACH_POLICY_VAR, config.LAUNCH_IF_ABSENT)

        self.sessions.get("modeler")
        established = self.sessions.session_for("modeler")

        self.assertEqual(established.activation["new_pids"], [555])
        self.assertEqual(established.activation["status"],
                         session.PROCESS_CREATED)
        self.assertEqual(established.warnings, [])

    def test_launch_if_absent_does_not_call_a_running_instance_new(self):
        """A before-snapshot is what stops that misreport."""
        app = modeler_app()
        procs = FakeProcesses(table={"maxsurfmodeler.exe": [6456]})
        self.install_com_environment(
            running={}, creatable={MODELER_PROGID: app},
            gencache=FakeGencache(creatable={MODELER_PROGID: app}),
            processes=procs)
        self.set_env(config.ATTACH_POLICY_VAR, config.LAUNCH_IF_ABSENT)

        self.sessions.get("modeler")
        activation = self.sessions.session_for("modeler").activation

        self.assertEqual(activation["candidate_process_pids_before"], [6456])
        self.assertEqual(activation["new_pids"], [])
        self.assertEqual(activation["status"], "clean")
        self.assertEqual(self.sessions.session_for("modeler").warnings, [])

    def test_launch_if_absent_still_prefers_a_running_instance(self):
        running = modeler_app("running")
        client = self.install_com_environment(
            running={MODELER_PROGID: running},
            creatable={MODELER_PROGID: modeler_app("created")},
        )
        self.set_env(config.ATTACH_POLICY_VAR, config.LAUNCH_IF_ABSENT)

        self.assertIs(self.sessions.get("modeler"), running)
        self.assertEqual(client.dispatch_calls, [])

    def test_an_unrecognised_policy_falls_back_to_the_default(self):
        self.set_env(config.ATTACH_POLICY_VAR, "start_whatever_you_like")
        self.assertEqual(config.attach_policy(), config.DEFAULT_ATTACH_POLICY)
        self.assertNotEqual(config.attach_policy(), config.LAUNCH_IF_ABSENT,
                            "an unreadable policy must never permit launching")

    def test_require_pid_refuses_when_the_process_cannot_be_proven(self):
        self.install_com_environment(running={MODELER_PROGID: modeler_app()})
        self.set_env(config.ATTACH_POLICY_VAR, config.REQUIRE_PID)

        with self.assertRaises(MaxsurfConnectionError) as caught:
            self.sessions.get("modeler")
        self.assertIn("process id", str(caught.exception))

    def test_require_pid_accepts_a_provable_process_id(self):
        app = modeler_app()
        app._oleobj_.GetWindow = lambda: 0x1234
        self.install_com_environment(
            running={MODELER_PROGID: app}, process=FakeWin32Process(pid=9182),
        )
        self.set_env(config.ATTACH_POLICY_VAR, config.REQUIRE_PID)

        self.sessions.get("modeler")
        established = self.sessions.session_for("modeler")
        self.assertEqual(established.com_host_pid, 9182)
        self.assertEqual(established.com_host_pid_status, "proven")
        self.assertEqual(established.com_host_pid_source,
                         "IOleWindow.GetWindow")

    def test_an_unknown_policy_argument_is_rejected(self):
        self.install_com_environment(running={MODELER_PROGID: modeler_app()})
        with self.assertRaises(MaxsurfValidationError):
            self.sessions.get("modeler", policy="do_whatever")

    def test_the_policy_inventory_is_what_it_claims_to_be(self):
        self.assertEqual(
            set(config.ATTACH_POLICIES),
            {config.ATTACH_ONLY, config.ACTIVATE_EXISTING,
             config.LAUNCH_IF_ABSENT, config.REQUIRE_PID},
        )
        self.assertEqual(set(config.ACTIVATING_POLICIES),
                         {config.ACTIVATE_EXISTING, config.LAUNCH_IF_ABSENT})
        self.assertNotIn(config.ATTACH_ONLY, config.ACTIVATING_POLICIES)
        self.assertNotIn(config.REQUIRE_PID, config.ACTIVATING_POLICIES)

    def test_the_default_policy_is_activate_existing(self):
        """Process-gated activation is the default; attach_only is diagnostic."""
        self.assertEqual(config.DEFAULT_ATTACH_POLICY, config.ACTIVATE_EXISTING)

    def test_only_launch_if_absent_may_start_maxsurf(self):
        """The two activating policies are not interchangeable.

        Nothing is running here. launch_if_absent is permitted to create the
        application; activate_existing must refuse before touching COM.
        """
        app = modeler_app()
        for policy, expect_activation in (
            (config.ACTIVATE_EXISTING, False),
            (config.LAUNCH_IF_ABSENT, True),
        ):
            with self.subTest(policy=policy):
                self.sessions.reset()
                gencache = FakeGencache(creatable={MODELER_PROGID: app})
                self.install_com_environment(
                    running={}, creatable={MODELER_PROGID: app},
                    gencache=gencache, processes=FakeProcesses(table={}))

                if expect_activation:
                    self.assertIs(self.sessions.get("modeler", policy=policy),
                                  app)
                    self.assertEqual(gencache.ensure_calls, [MODELER_PROGID])
                else:
                    with self.assertRaises(MaxsurfConnectionError):
                        self.sessions.get("modeler", policy=policy)
                    self.assertEqual(gencache.ensure_calls, [])


class ProcessGatedActivationTests(IsolatedEnvTestCase):
    """activate_existing: the policy that actually reaches Maxsurf.

    Maxsurf does not publish its Application object in the ROT, so
    GetActiveObject returns MK_E_UNAVAILABLE even while MaxsurfModeler.exe is
    plainly running. This policy answers "is it running?" from the operating
    system's process list instead, refuses if the answer is no, and only then
    performs COM activation.
    """

    def setUp(self):
        super().setUp()
        self.set_env(config.ATTACH_POLICY_VAR, config.ACTIVATE_EXISTING)

    def environment(self, table=None, on_ensure=None, app=None,
                    supported=True):
        app = app if app is not None else modeler_app()
        procs = FakeProcesses(table=table, supported=supported)
        gencache = FakeGencache(creatable={MODELER_PROGID: app},
                               on_ensure=on_ensure)
        client = self.install_com_environment(
            running={},
            creatable={MODELER_PROGID: app},
            clsids={MODELER_PROGID: "{11111111-0000-0000-0000-000000000000}"},
            gencache=gencache,
            processes=procs,
        )
        return client, procs, gencache, app

    # --- refusal before any COM activation -------------------------------

    def test_it_refuses_before_activation_when_the_executable_is_absent(self):
        client, procs, gencache, _app = self.environment(table={})

        with self.assertRaises(MaxsurfConnectionError) as caught:
            self.sessions.get("modeler")

        self.assertEqual(gencache.ensure_calls, [],
                         "EnsureDispatch ran with no Maxsurf process present")
        self.assertEqual(client.dispatch_calls, [],
                         "Dispatch ran with no Maxsurf process present")
        gate = caught.exception.details["process_gate"]
        self.assertEqual(gate["state"], "absent")
        self.assertFalse(gate["may_activate"])
        self.assertIn("not running", str(caught.exception))
        self.assertTrue(procs.find_calls, "the process list was never consulted")

    def test_it_refuses_when_the_process_list_cannot_be_read(self):
        """Unable to look is not the same as looked and found nothing."""
        _client, _procs, gencache, _app = self.environment(
            table={"maxsurfmodeler.exe": [6456]}, supported=False)

        with self.assertRaises(MaxsurfConnectionError) as caught:
            self.sessions.get("modeler")

        self.assertEqual(gencache.ensure_calls, [])
        self.assertEqual(caught.exception.details["process_gate"]["state"],
                         "unprovable")

    def test_it_refuses_a_module_whose_process_is_absent(self):
        # Motions now carries a proven executable (MaxsurfMotions.exe), so the
        # gate that once reported "unprovable" for it now reports "absent" when
        # no such process is running, and still refuses before any activation.
        app = ComFake(Version="25")
        procs = FakeProcesses(table={"anything.exe": [1]})
        gencache = FakeGencache(
            creatable={"BentleyMotions.Application": app})
        self.install_com_environment(
            running={}, creatable={"BentleyMotions.Application": app},
            gencache=gencache, processes=procs)
        self.set_env(config.ALLOWED_MODULES_VAR, "modeler,motions")

        with self.assertRaises(MaxsurfConnectionError) as caught:
            self.sessions.get("motions")

        self.assertEqual(gencache.ensure_calls, [])
        gate = caught.exception.details["process_gate"]
        self.assertEqual(gate["state"], "absent")
        self.assertIn("MaxsurfMotions.exe", gate["detail"])

    # --- activation when the process is present --------------------------

    def test_it_activates_through_ensure_dispatch_when_maxsurf_is_running(self):
        _client, procs, gencache, app = self.environment(
            table={"maxsurfmodeler.exe": [6456]})

        self.assertIs(self.sessions.get("modeler"), app)
        self.assertEqual(gencache.ensure_calls, [MODELER_PROGID],
                         "the historically proven activation call was not used")

        established = self.sessions.session_for("modeler")
        self.assertEqual(established.attach_method, session.COM_ACTIVATION)
        self.assertEqual(established.attach_policy, config.ACTIVATE_EXISTING)
        self.assertEqual(established.version, "25.00.02.339")
        self.assertEqual(established.progid, MODELER_PROGID)
        self.assertEqual(established.clsid,
                         "{11111111-0000-0000-0000-000000000000}")

    def test_the_rot_is_still_tried_first(self):
        app = modeler_app()
        procs = FakeProcesses(table={"maxsurfmodeler.exe": [6456]})
        gencache = FakeGencache(creatable={MODELER_PROGID: app})
        self.install_com_environment(
            running={MODELER_PROGID: app}, gencache=gencache, processes=procs)

        self.assertIs(self.sessions.get("modeler"), app)
        self.assertEqual(gencache.ensure_calls, [],
                         "activation happened even though the ROT had the object")
        self.assertEqual(self.sessions.session_for("modeler").attach_method,
                         session.ROT_ATTACH)

    def test_dispatch_is_the_fallback_when_ensure_dispatch_fails(self):
        app = modeler_app()
        procs = FakeProcesses(table={"maxsurfmodeler.exe": [6456]})
        gencache = FakeGencache(creatable={})  # no type library
        client = self.install_com_environment(
            running={}, creatable={MODELER_PROGID: app},
            gencache=gencache, processes=procs)

        self.assertIs(self.sessions.get("modeler"), app)
        self.assertEqual(gencache.ensure_calls, [MODELER_PROGID])
        self.assertEqual(client.dispatch_calls, [MODELER_PROGID])
        self.assertEqual(
            self.sessions.session_for("modeler").activation["call"],
            "win32com.client.Dispatch")

    # --- process snapshots ----------------------------------------------

    def test_snapshots_are_taken_before_and_after_activation(self):
        _client, procs, _gencache, _app = self.environment(
            table={"maxsurfmodeler.exe": [6456]})
        self.sessions.get("modeler")

        activation = self.sessions.session_for("modeler").activation
        self.assertEqual(activation["candidate_process_pids_before"], [6456])
        self.assertEqual(activation["candidate_process_pids_after"], [6456])
        self.assertEqual(activation["new_pids"], [])
        self.assertEqual(activation["status"], "clean")
        self.assertGreaterEqual(len(procs.find_calls), 2,
                                "the process list was not snapshotted twice")

    def test_an_unexpected_new_process_is_reported_not_hidden(self):
        """If activation started a second Maxsurf, say so."""
        procs_holder = {}

        def spawn(_progid):
            procs_holder["procs"].spawn("MaxsurfModeler.exe", 31337)

        _client, procs, _gencache, _app = self.environment(
            table={"maxsurfmodeler.exe": [6456]}, on_ensure=spawn)
        procs_holder["procs"] = procs

        self.sessions.get("modeler")
        established = self.sessions.session_for("modeler")

        activation = established.activation
        self.assertEqual(activation["candidate_process_pids_before"], [6456])
        self.assertEqual(activation["candidate_process_pids_after"],
                         [6456, 31337])
        self.assertEqual(activation["new_pids"], [31337])
        self.assertEqual(activation["status"], session.UNEXPECTED_PROCESS)
        self.assertTrue(
            any("additional modeler process" in warning
                for warning in established.warnings),
            established.warnings)
        self.assertTrue(any("31337" in warning
                            for warning in established.warnings))

    def test_no_process_is_ever_terminated(self):
        procs_holder = {}
        _client, procs, _gencache, _app = self.environment(
            table={"maxsurfmodeler.exe": [6456]},
            on_ensure=lambda _p: procs_holder["procs"].spawn(
                "MaxsurfModeler.exe", 31337))
        procs_holder["procs"] = procs

        self.sessions.get("modeler")
        self.assertEqual(procs.pids_of(("MaxsurfModeler.exe",)), (6456, 31337),
                         "a process was terminated to tidy up the warning")

    # --- audit -----------------------------------------------------------

    def test_the_intent_to_activate_is_audited_with_the_process_identity(self):
        self.environment(table={"maxsurfmodeler.exe": [6456]})
        self.sessions.get("modeler")

        records = self.audit_records()
        intents = [r for r in records
                   if r["tool"] == "session_activation_intent"]
        self.assertEqual(len(intents), 1, [r["tool"] for r in records])
        intent = intents[0]
        self.assertEqual(intent["stage"], "intent")
        self.assertEqual(
            intent["result_summary"]["candidate_process_pids_before"], [6456])
        self.assertEqual(intent["result_summary"]["process_gate"], "satisfied")
        self.assertIsNotNone(intent["result_summary"]["rot_error"])

        activated = [r for r in records if r["tool"] == "session_activated"]
        self.assertEqual(len(activated), 1)
        self.assertEqual(activated[0]["result_summary"]["new_pids"], [])

        # The intent must precede the activation, or it proves nothing.
        order = [r["tool"] for r in records]
        self.assertLess(order.index("session_activation_intent"),
                        order.index("session_activated"))

    def test_an_unexpected_process_gets_its_own_audit_record(self):
        procs_holder = {}
        _client, procs, _gencache, _app = self.environment(
            table={"maxsurfmodeler.exe": [6456]},
            on_ensure=lambda _p: procs_holder["procs"].spawn(
                "MaxsurfModeler.exe", 31337))
        procs_holder["procs"] = procs

        self.sessions.get("modeler")

        flagged = [r for r in self.audit_records()
                   if r["tool"] == "session_activation_unexpected_process"]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["result_summary"]["new_pids"], [31337])
        self.assertEqual(
            flagged[0]["result_summary"]["candidate_process_pids_before"],
            [6456])

    def test_a_refusal_writes_no_activation_record(self):
        self.environment(table={})
        with self.assertRaises(MaxsurfConnectionError):
            self.sessions.get("modeler")

        tools = [r["tool"] for r in self.audit_records()]
        self.assertNotIn("session_activation_intent", tools)
        self.assertNotIn("session_activated", tools)

    def test_activation_is_refused_when_its_intent_cannot_be_recorded(self):
        """Activation is a state change, so it must be provable to happen."""
        _client, _procs, gencache, _app = self.environment(
            table={"maxsurfmodeler.exe": [6456]})
        self.set_env(config.AUDIT_ENABLED_VAR, "0")

        with self.assertRaises(MaxsurfSafetyError):
            self.sessions.get("modeler")
        self.assertEqual(gencache.ensure_calls, [],
                         "activation proceeded without a recorded intent")

    def test_a_failed_activation_is_recorded_as_a_failure(self):
        procs = FakeProcesses(table={"maxsurfmodeler.exe": [6456]})
        gencache = FakeGencache(creatable={})
        self.install_com_environment(running={}, creatable={},
                                     gencache=gencache, processes=procs)

        with self.assertRaises(MaxsurfConnectionError):
            self.sessions.get("modeler")

        failures = [r for r in self.audit_records()
                    if r["tool"] == "session_activation_failed"]
        self.assertTrue(failures)
        self.assertEqual(failures[0]["outcome"], audit.Outcome.FAILURE)

    # --- identity is still verified --------------------------------------

    def test_the_activated_object_must_still_be_the_requested_module(self):
        """A process gate is not a substitute for checking what COM returned."""
        wrong = stability_app()
        wrong._oleobj_ = OleObjFake(
            supported_iids=(session.MODULES["stability"].application_iid,
                            "{00000000-0000-0000-C000-000000000046}"),
        )
        procs = FakeProcesses(table={"maxsurfmodeler.exe": [6456]})
        self.install_com_environment(
            running={}, creatable={MODELER_PROGID: wrong},
            gencache=FakeGencache(creatable={MODELER_PROGID: wrong}),
            processes=procs)

        with self.assertRaises(MaxsurfSafetyError) as caught:
            self.sessions.get("modeler")
        self.assertEqual(caught.exception.details["conflicting_module"],
                         "stability")

    def test_no_session_is_registered_when_activation_is_refused(self):
        self.environment(table={})
        with self.assertRaises(MaxsurfConnectionError):
            self.sessions.get("modeler")
        self.assertIsNone(self.sessions.session_for("modeler"))

    # --- threading -------------------------------------------------------

    def test_activation_happens_on_the_com_worker_thread(self):
        import threading

        seen: list[int] = []
        app = modeler_app()
        procs = FakeProcesses(table={"maxsurfmodeler.exe": [6456]})
        gencache = FakeGencache(
            creatable={MODELER_PROGID: app},
            on_ensure=lambda _p: seen.append(threading.get_ident()))
        self.install_com_environment(running={}, creatable={MODELER_PROGID: app},
                                     gencache=gencache, processes=procs)
        worker = self.use_real_worker()

        self.sessions.get("modeler")

        self.assertEqual(seen, [worker.thread_ident()],
                         "COM activation left the owned STA thread")


class AllowedModuleTests(IsolatedEnvTestCase):
    def test_motions_is_connectable_by_default(self):
        # Motions became a first-class module once BentleyMotions.Application
        # (the registered ProgID) and MaxsurfMotions.exe were wired in.
        self.assertIn("motions", config.allowed_modules())
        self.assertIn("motions_xmlgrid", config.allowed_modules())

    def test_the_allow_list_is_configurable(self):
        app = ComFake(Version="25")
        self.install_com_environment(
            running={"BentleyResistance.Application": app})
        self.set_env(config.ALLOWED_MODULES_VAR, "modeler,resistance")

        self.assertIs(self.sessions.get("resistance"), app)

    def test_an_unknown_module_without_a_progid_is_a_validation_error(self):
        self.install_com_environment(running={})
        with self.assertRaises(MaxsurfValidationError):
            self.sessions.get("nonexistent_module")

    def test_an_empty_module_name_is_rejected(self):
        with self.assertRaises(MaxsurfValidationError):
            self.sessions.get("")


class HelperModuleTests(IsolatedEnvTestCase):
    """The XMLGrid helper is created, so creating it must not start Stability."""

    def test_the_helper_requires_an_existing_stability_session(self):
        client = self.install_com_environment(
            running={}, creatable={XMLGRID_PROGID: ComFake()},
        )
        with self.assertRaises(MaxsurfConnectionError) as caught:
            self.sessions.get("stability_xmlgrid", progid=XMLGRID_PROGID)

        self.assertEqual(caught.exception.details["requires_session"],
                         "stability")
        self.assertEqual(client.dispatch_calls, [])

    def test_the_helper_is_created_once_stability_is_connected(self):
        grid = ComFake()
        self.install_com_environment(
            running={STABILITY_PROGID: stability_app()},
            creatable={XMLGRID_PROGID: grid},
        )
        self.sessions.get("stability")

        self.assertIs(
            self.sessions.get("stability_xmlgrid", progid=XMLGRID_PROGID), grid
        )
        self.assertEqual(
            self.sessions.session_for("stability_xmlgrid").attach_method,
            session.COM_ACTIVATION,
        )


class IdentityTests(IsolatedEnvTestCase):
    def test_a_session_records_what_it_connected_to(self):
        self.install_com_environment(
            running={STABILITY_PROGID: stability_app("C:/ws/yacht.msd", "yacht")},
            clsids={STABILITY_PROGID: "{ABCD0000-0000-0000-0000-000000000000}"},
        )
        self.sessions.get("stability")
        described = self.sessions.session_for("stability").describe()

        self.assertEqual(described["progid"], STABILITY_PROGID)
        self.assertEqual(described["clsid"],
                         "{ABCD0000-0000-0000-0000-000000000000}")
        self.assertEqual(described["version"], "25.00.02.339")
        self.assertEqual(described["version_source"], "Version")
        self.assertEqual(described["design_path"], "C:/ws/yacht.msd")
        self.assertEqual(described["design_path_status"], "read")
        self.assertEqual(described["design_name"], "yacht")
        self.assertEqual(described["liveness"], session.ALIVE)
        self.assertIsNotNone(described["connected_at"])

    def test_an_object_answering_another_modules_interface_is_refused(self):
        # A Stability application reachable under the Modeler ProgID.
        wrong = stability_app()
        wrong._oleobj_ = OleObjFake(
            supported_iids=(session.MODULES["stability"].application_iid,
                            "{00000000-0000-0000-C000-000000000046}"),
        )
        self.install_com_environment(running={MODELER_PROGID: wrong})

        with self.assertRaises(MaxsurfSafetyError) as caught:
            self.sessions.get("modeler")
        self.assertEqual(caught.exception.details["conflicting_module"],
                         "stability")
        self.assertEqual(caught.exception.details["identity_status"],
                         session.IDENTITY_CONFLICTING)


class InterfaceIdentityTests(IsolatedEnvTestCase):
    """The identity check that reported expected_ok=false on a working session.

    A live Modeler session returned the correct ProgID and version, drove real
    geometry, and stayed properly separated from Stability, yet the old check
    reported its expected interface as absent. The cause was pywin32, not COM:
    a bare QueryInterface for an IID with no registered Python wrapper class
    raises TypeError *after* COM has already answered that the interface is
    supported. The old check caught every exception and called it False, so an
    inconclusive result was indistinguishable from a wrong application.
    """

    MODELER_IID = "{B2B50074-E514-4866-B6A3-23DAFD8622FC}"
    STABILITY_IID = "{F7073155-8020-4522-B800-7155D1E0FC5B}"
    IUNKNOWN = "{00000000-0000-0000-C000-000000000046}"

    def setUp(self):
        super().setUp()
        self.spec = session.MODULES["modeler"]

    def test_the_proven_iids_match_the_registered_type_libraries(self):
        """These came from live type info; they must not drift."""
        self.assertEqual(self.spec.application_iid, self.MODELER_IID)
        self.assertEqual(session.MODULES["stability"].application_iid,
                         self.STABILITY_IID)

    def test_the_declared_interface_id_verifies_the_module(self):
        app = ComFake(declared_iid=self.MODELER_IID)
        result = session.verify_interface_identity(app, self.spec)

        self.assertEqual(result["status"], session.IDENTITY_VERIFIED)
        self.assertEqual(result["evidence"], "declared_interface_iid")
        self.assertEqual(result["declared_iid"], self.MODELER_IID)
        self.assertIsNone(result["conflicting_module"])

    def test_iid_comparison_ignores_case(self):
        app = ComFake(declared_iid=self.MODELER_IID.lower())
        self.assertEqual(
            session.verify_interface_identity(app, self.spec)["status"],
            session.IDENTITY_VERIFIED)

    def test_a_pywin32_wrapper_failure_is_not_reported_as_a_mismatch(self):
        """The exact regression: TypeError from the interface registry."""
        app = ComFake()
        app._oleobj_ = OleObjFake(unwrappable_iids=(self.MODELER_IID,))

        result = session.verify_interface_identity(app, self.spec)

        self.assertEqual(result["status"], session.IDENTITY_VERIFIED)
        self.assertEqual(result["evidence"], "query_interface")
        self.assertEqual(result["query_interface"]["status"], "supported")
        self.assertIsNone(result["conflicting_module"])

    def test_the_query_is_wrapped_as_iunknown_to_avoid_that_failure(self):
        app = ComFake()
        app._oleobj_ = OleObjFake(unwrappable_iids=(self.MODELER_IID,))
        session.verify_interface_identity(app, self.spec)

        wrapped = [wrap for iid, wrap in app._oleobj_.query_calls
                   if iid == self.MODELER_IID]
        self.assertTrue(wrapped)
        self.assertTrue(all(wrap is not None for wrap in wrapped),
                        app._oleobj_.query_calls)

    def test_an_unreadable_type_library_falls_back_to_query_interface(self):
        app = ComFake()
        app._oleobj_ = OleObjFake(
            typeinfo_error=ComError(0x80004001, "not supported"),
            supported_iids=(self.MODELER_IID, self.IUNKNOWN))

        result = session.verify_interface_identity(app, self.spec)

        self.assertEqual(result["status"], session.IDENTITY_VERIFIED)
        self.assertEqual(result["evidence"], "query_interface")
        self.assertEqual(result["declared_iid_status"], "unavailable")

    def test_a_wrong_module_declared_in_type_info_is_conflicting(self):
        app = ComFake(declared_iid=self.STABILITY_IID)
        result = session.verify_interface_identity(app, self.spec)

        self.assertEqual(result["status"], session.IDENTITY_CONFLICTING)
        self.assertEqual(result["conflicting_module"], "stability")
        self.assertIn("stability", result["detail"])

    def test_a_wrong_module_answering_query_interface_is_conflicting(self):
        app = ComFake()
        app._oleobj_ = OleObjFake(
            supported_iids=(self.STABILITY_IID, self.IUNKNOWN))

        result = session.verify_interface_identity(app, self.spec)

        self.assertEqual(result["status"], session.IDENTITY_CONFLICTING)
        self.assertEqual(result["conflicting_module"], "stability")
        self.assertEqual(result["query_interface"]["status"], "absent")

    def test_an_unknown_interface_is_unverified_not_conflicting(self):
        """A third-party wrapper interface proves nothing either way."""
        app = ComFake(declared_iid="{99999999-0000-0000-0000-000000000000}")
        app._oleobj_ = OleObjFake(
            declared_iid="{99999999-0000-0000-0000-000000000000}",
            supported_iids=("{99999999-0000-0000-0000-000000000000}",))

        result = session.verify_interface_identity(app, self.spec)

        self.assertEqual(result["status"], session.IDENTITY_UNVERIFIED)
        self.assertIsNone(result["conflicting_module"])
        self.assertIn("neither", result["detail"])

    def test_no_type_info_and_no_query_interface_is_unverified(self):
        app = ComFake()
        app._oleobj_ = None
        result = session.verify_interface_identity(app, self.spec)

        self.assertEqual(result["status"], session.IDENTITY_UNVERIFIED)
        self.assertIsNone(result["conflicting_module"])

    def test_a_module_without_a_proven_iid_is_unverified(self):
        app = ComFake(declared_iid=self.MODELER_IID)
        result = session.verify_interface_identity(
            app, __import__("dataclasses").replace(session.MODULES["motions"], application_iid=None))

        self.assertEqual(result["status"], session.IDENTITY_UNVERIFIED)
        self.assertIn("no proven IApplication IID", result["detail"])

    def test_only_conflicting_refuses_a_connection(self):
        for declared, expect_error in (
            (self.MODELER_IID, False),
            (None, False),
            ("{99999999-0000-0000-0000-000000000000}", False),
            (self.STABILITY_IID, True),
        ):
            with self.subTest(declared=declared):
                self.sessions.reset()
                app = ComFake(declared_iid=declared, Version="25",
                              Design=ComFake(Path="C:/ws/h.msd", Name="h"))
                self.install_com_environment(running={MODELER_PROGID: app})
                if expect_error:
                    with self.assertRaises(MaxsurfSafetyError):
                        self.sessions.get("modeler")
                else:
                    self.assertIs(self.sessions.get("modeler"), app)

    def test_the_status_is_reported_in_the_session_description(self):
        app = ComFake(declared_iid=self.MODELER_IID, Version="25",
                      Design=ComFake(Path="C:/ws/h.msd", Name="h"))
        self.install_com_environment(running={MODELER_PROGID: app})
        self.sessions.get("modeler")

        identity = self.sessions.session_for("modeler").describe()[
            "interface_identity"]
        self.assertEqual(identity["status"], session.IDENTITY_VERIFIED)
        self.assertNotIn("expected_ok", identity,
                         "the boolean that conflated three states is back")

    def test_a_real_e_noninterface_is_distinguished_from_a_wrapper_failure(self):
        absent = ComFake()
        absent._oleobj_ = OleObjFake(supported_iids=(self.IUNKNOWN,))
        self.assertEqual(
            session.query_interface_support(absent, self.MODELER_IID)["status"],
            "absent")

        unwrappable = ComFake()
        unwrappable._oleobj_ = OleObjFake(
            unwrappable_iids=(self.MODELER_IID,))
        self.assertEqual(
            session.query_interface_support(
                unwrappable, self.MODELER_IID)["status"],
            "supported")


class LivenessTests(IsolatedEnvTestCase):
    def test_a_live_object_is_reported_alive(self):
        app = modeler_app()
        self.assertEqual(session.probe_liveness(app),
                         (session.ALIVE, None))

    def test_the_rpc_codes_that_mean_the_server_is_gone_are_recognised(self):
        for hresult in sorted(session.STALE_HRESULTS):
            with self.subTest(hresult=hex(hresult)):
                self.assertTrue(session.is_stale_error(ComError(hresult)))

    def test_an_unrecognised_failure_is_not_treated_as_a_dead_server(self):
        # Reconnecting on a transient error risks silently switching instances.
        self.assertFalse(session.is_stale_error(ComError(0x80020009)))
        self.assertFalse(session.is_stale_error(RuntimeError("boom")))

    def test_a_dead_object_is_reported_stale(self):
        app = modeler_app()
        app.go_away()
        state, detail = session.probe_liveness(app)

        self.assertEqual(state, session.STALE)
        self.assertIsNotNone(detail)

    def test_a_recent_result_is_reused_instead_of_re_probing(self):
        app = modeler_app()
        self.install_com_environment(running={MODELER_PROGID: app})
        self.sessions.get("modeler")
        before = app._oleobj_.query_count

        self.sessions.get("modeler")
        self.assertEqual(app._oleobj_.query_count, before)

    def test_a_mutating_call_always_re_probes(self):
        app = modeler_app()
        self.install_com_environment(running={MODELER_PROGID: app})
        self.sessions.get("modeler")
        before = app._oleobj_.query_count

        self.sessions.get("modeler", for_write=True)
        self.assertGreater(app._oleobj_.query_count, before)


class ReconnectTests(IsolatedEnvTestCase):
    def test_a_restarted_maxsurf_is_recovered_without_restarting_the_server(self):
        first = stability_app("C:/ws/hull.msd")
        client = self.install_com_environment(
            running={STABILITY_PROGID: first})
        self.sessions.get("stability")

        # Stability exits and is reopened with the same design.
        first.go_away()
        second = stability_app("C:/ws/hull.msd")
        client.running[STABILITY_PROGID] = second

        self.assertIs(self.sessions.get("stability", for_write=True), second)
        recovered = self.sessions.session_for("stability")
        self.assertEqual(recovered.reconnect_count, 1)
        self.assertEqual(recovered.liveness, session.ALIVE)

    def test_a_stale_pointer_and_its_recovery_are_both_audited(self):
        first = stability_app()
        client = self.install_com_environment(running={STABILITY_PROGID: first})
        self.sessions.get("stability")
        first.go_away()
        client.running[STABILITY_PROGID] = stability_app()

        self.sessions.get("stability", for_write=True)

        self.assertEqual(len(self.records_for("session_stale")), 1)
        self.assertEqual(len(self.records_for("session_reconnect")), 1)
        stale = self.records_for("session_stale")[0]
        self.assertEqual(stale["channel"], audit.Channel.INTERNAL)
        self.assertEqual(stale["outcome"], audit.Outcome.FAILURE)

    def test_reconnecting_onto_a_different_design_is_refused(self):
        first = stability_app("C:/ws/hull.msd")
        client = self.install_com_environment(running={STABILITY_PROGID: first})
        self.sessions.get("stability")

        first.go_away()
        client.running[STABILITY_PROGID] = stability_app("C:/ws/other.msd")

        with self.assertRaises(MaxsurfSafetyError) as caught:
            self.sessions.get("stability", for_write=True)

        details = caught.exception.details
        self.assertEqual(details["mismatch"], ["design_path"])
        self.assertEqual(details["expected"]["design_path"], "C:/ws/hull.msd")
        self.assertEqual(details["actual"]["design_path"], "C:/ws/other.msd")
        self.assertEqual(len(self.records_for("session_identity_mismatch")), 1)

    def test_reconnecting_onto_a_different_process_is_refused(self):
        first = stability_app()
        first._oleobj_.GetWindow = lambda: 0x1
        client = self.install_com_environment(
            running={STABILITY_PROGID: first}, process=FakeWin32Process(101))
        self.sessions.get("stability")
        self.assertEqual(self.sessions.session_for("stability").com_host_pid,
                         101)

        first.go_away()
        replacement = stability_app()
        replacement._oleobj_.GetWindow = lambda: 0x2
        client.running[STABILITY_PROGID] = replacement
        # A different process now answers under the same ProgID.
        import maxsurf_mcp.session as session_module
        session_module.win32process.pid = 202

        with self.assertRaises(MaxsurfSafetyError) as caught:
            self.sessions.get("stability", for_write=True)
        self.assertIn("com_host_pid", caught.exception.details["mismatch"])

    def test_an_unknown_field_on_one_side_cannot_prove_a_mismatch(self):
        # A field never established on either side cannot prove a mismatch.
        self.assertEqual(
            session._identity_mismatch(
                {"progid": "A", "com_host_pid": None, "design_path": None},
                {"progid": "A", "com_host_pid": 5, "design_path": "C:/ws/a.msd"},
            ),
            [],
        )

    def test_a_reconnect_that_cannot_attach_reports_a_connection_error(self):
        app = stability_app()
        client = self.install_com_environment(running={STABILITY_PROGID: app})
        self.sessions.get("stability")

        app.go_away()
        client.running.clear()

        with self.assertRaises(MaxsurfConnectionError):
            self.sessions.get("stability", for_write=True)
        self.assertIsNone(self.sessions.session_for("stability"))


class DesignSwapTests(IsolatedEnvTestCase):
    """A live session whose design changed must not be written through."""

    def test_a_mutation_is_refused_after_the_design_changed(self):
        app = stability_app("C:/ws/hull.msd")
        self.install_com_environment(running={STABILITY_PROGID: app})
        self.sessions.get("stability")

        app.Design.Path = "C:/ws/somebody_else.msd"

        with self.assertRaises(MaxsurfSafetyError) as caught:
            self.sessions.get("stability", for_write=True)

        self.assertEqual(caught.exception.details["expected_design"],
                         "C:/ws/hull.msd")
        self.assertEqual(caught.exception.details["actual_design"],
                         "C:/ws/somebody_else.msd")
        blocked = self.records_for("session_identity_mismatch")
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["outcome"], audit.Outcome.BLOCKED)

    def test_a_read_after_a_design_change_is_not_blocked(self):
        app = stability_app("C:/ws/hull.msd")
        self.install_com_environment(running={STABILITY_PROGID: app})
        self.sessions.get("stability")
        app.Design.Path = "C:/ws/other.msd"

        self.assertIs(self.sessions.get("stability", for_write=False), app)

    def test_the_classification_of_the_call_decides_whether_writes_are_checked(self):
        for classification, mutating in (
            (audit.Classification.READ, False),
            (audit.Classification.WRITE_GEOMETRY, True),
            (audit.Classification.WRITE_FILE, True),
            (audit.Classification.WRITE_STATE, True),
            (audit.Classification.EXECUTE_ANALYSIS, True),
            (audit.Classification.DIAGNOSTIC, True),
        ):
            with self.subTest(classification=classification):
                self.assertEqual(
                    session._classification_mutates(classification), mutating
                )

    def test_a_mutating_tool_scope_triggers_the_identity_check(self):
        app = stability_app("C:/ws/hull.msd")
        self.install_com_environment(running={STABILITY_PROGID: app})
        self.sessions.get("stability")
        app.Design.Path = "C:/ws/other.msd"

        scope = audit.begin_call_scope(
            "req", audit.Classification.WRITE_GEOMETRY)
        try:
            with self.assertRaises(MaxsurfSafetyError):
                self.sessions.get("stability")
        finally:
            audit.end_call_scope(scope)


class ResetTests(IsolatedEnvTestCase):
    def test_reset_drops_every_session_and_records_what_it_dropped(self):
        self.install_com_environment(
            running={MODELER_PROGID: modeler_app(),
                     STABILITY_PROGID: stability_app()})
        self.sessions.get("modeler")
        self.sessions.get("stability")

        summary = self.sessions.reset()

        self.assertEqual(summary["dropped"], ["modeler", "stability"])
        self.assertIsNone(self.sessions.session_for("modeler"))
        self.assertEqual(len(self.records_for("session_reset")), 1)

    def test_reset_can_target_one_module(self):
        self.install_com_environment(
            running={MODELER_PROGID: modeler_app(),
                     STABILITY_PROGID: stability_app()})
        self.sessions.get("modeler")
        self.sessions.get("stability")

        summary = self.sessions.reset("modeler")

        self.assertEqual(summary["dropped"], ["modeler"])
        self.assertIsNone(self.sessions.session_for("modeler"))
        self.assertIsNotNone(self.sessions.session_for("stability"))

    def test_reset_lets_a_different_design_be_accepted_deliberately(self):
        first = stability_app("C:/ws/hull.msd")
        client = self.install_com_environment(running={STABILITY_PROGID: first})
        self.sessions.get("stability")

        first.go_away()
        client.running[STABILITY_PROGID] = stability_app("C:/ws/other.msd")
        with self.assertRaises(MaxsurfSafetyError):
            self.sessions.get("stability", for_write=True)

        self.sessions.reset("stability")
        self.sessions.get("stability", for_write=True)

        self.assertEqual(
            self.sessions.session_for("stability").design_path,
            "C:/ws/other.msd",
        )

    def test_resetting_nothing_is_harmless(self):
        summary = self.sessions.reset()
        self.assertEqual(summary["dropped"], [])

    def test_describe_reports_the_policy_and_the_sessions(self):
        self.install_com_environment(running={MODELER_PROGID: modeler_app()})
        self.sessions.get("modeler")
        described = self.sessions.describe()

        self.assertEqual(described["attach_policy"], config.ACTIVATE_EXISTING)
        self.assertIn("modeler", described["sessions"])
        self.assertIn("resistance", described["allowed_modules"])


class WorkerRoutingTests(IsolatedEnvTestCase):
    """Session COM contact must go through the COM worker, not the caller."""

    def test_connecting_and_probing_are_submitted_to_the_worker(self):
        self.install_com_environment(running={MODELER_PROGID: modeler_app()})
        self.sessions.get("modeler")
        self.sessions.get("modeler", for_write=True)

        self.assertIn("connect:modeler", self.worker.calls)
        self.assertIn("liveness:modeler", self.worker.calls)

    def test_the_pywin32_free_case_reports_a_connection_error(self):
        import maxsurf_mcp.session as session_module
        from unittest import mock

        with mock.patch.object(session_module, "pythoncom", None):
            with self.assertRaises(MaxsurfConnectionError) as caught:
                self.sessions.get("modeler")
        self.assertIn("Windows", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
