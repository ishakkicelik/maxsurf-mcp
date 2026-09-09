from __future__ import annotations

import json
import unittest
from unittest import mock

from maxsurf_mcp import com, session
from maxsurf_mcp.errors import MaxsurfConnectionError, MaxsurfValidationError

from .support import ComFake, IsolatedEnvTestCase


class ProgIdHintsTests(unittest.TestCase):
    def test_stability_prefers_registered_bentley_progid(self):
        self.assertEqual(
            com.PROGID_HINTS["stability"][0],
            "BentleyStability.Application",
        )


class ConnectErrorContractTests(IsolatedEnvTestCase):
    """com.connect is now a facade over the session manager.

    The error contract it presented in Phase 0 is unchanged; only the machinery
    behind it moved, so these tests exercise it through the session layer's COM
    bindings instead of the connection cache that no longer exists.
    """

    def test_an_unknown_module_raises_a_validation_error(self):
        self.install_com_environment(running={})
        with self.assertRaises(MaxsurfValidationError) as caught:
            com.connect("nonexistent_module")
        self.assertIn("known_modules", caught.exception.details)

    def test_a_failed_connection_raises_a_connection_error(self):
        self.install_com_environment(running={})
        with self.assertRaises(MaxsurfConnectionError) as caught:
            com.connect("modeler")
        self.assertIn("attempted", caught.exception.details)
        self.assertIn("licence", str(caught.exception))

    def test_missing_pywin32_raises_a_connection_error(self):
        with mock.patch.object(session, "pythoncom", None):
            with self.assertRaises(MaxsurfConnectionError):
                com.connect("modeler")

    def test_reset_clears_the_established_sessions(self):
        self.install_com_environment(
            running={"BentleyModeler.Application": ComFake(Version="25")})
        com.connect("modeler")
        self.assertIsNotNone(com.session_for("modeler"))

        com.reset()

        self.assertIsNone(com.session_for("modeler"))


class ProbeTests(unittest.TestCase):
    def test_probe_distinguishes_read_absent_null_and_failed(self):
        class Design(ComFake):
            @property
            def Broken(self):
                raise OSError("getter exploded")

        design = Design(Empty=None, Good=12.5)

        read = com.probe(design, "Missing", "Empty", "Broken", "Good")
        self.assertEqual(read["status"], "read")
        self.assertEqual(read["value"], 12.5)
        self.assertEqual(read["source_member"], "Good")
        self.assertEqual(
            [attempt["status"] for attempt in read["attempts"]],
            ["absent", "null", "failed"],
        )

        unavailable = com.probe(design, "Missing", "AlsoMissing")
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertIsNone(unavailable["value"])
        self.assertIsNone(unavailable["source_member"])

    def test_first_available_returns_the_first_non_null_value(self):
        design = ComFake(A=None, B=3)
        self.assertEqual(com.first_available(design, "A", "B"), 3)
        self.assertIsNone(com.first_available(design, "Missing"))


class ResolveDelegationTests(unittest.TestCase):
    def test_com_resolve_uses_the_hardened_resolver(self):
        from maxsurf_mcp.errors import MaxsurfSafetyError

        app = ComFake(Design=ComFake(Draft=2.0))
        self.assertEqual(com.resolve(app, "Design.Draft"), 2.0)
        with self.assertRaises(MaxsurfSafetyError):
            com.resolve(app, "__class__.__init__.__globals__")


class ConnectToolTests(IsolatedEnvTestCase):
    def test_connect_maxsurf_reports_the_session_it_established(self):
        self.install_com_environment(
            running={"BentleyModeler.Application": ComFake(Version="25.00")})

        payload = json.loads(com.connect_maxsurf("modeler"))

        self.assertEqual(payload["connected"], "modeler")
        self.assertEqual(payload["session"]["version"], "25.00")
        self.assertEqual(payload["session"]["attach_method"], session.ROT_ATTACH)

    def test_connect_maxsurf_is_audited_as_a_state_change(self):
        with mock.patch.object(com, "connect", return_value=ComFake()):
            com.connect_maxsurf("modeler")
        records = self.records_for("connect_maxsurf")
        self.assertEqual([r["stage"] for r in records], ["intent", "complete"])
        self.assertEqual(records[0]["classification"], "WRITE_STATE")

    def test_a_connection_failure_propagates_as_an_mcp_error(self):
        with mock.patch.object(
            com, "connect", side_effect=MaxsurfConnectionError("nope")
        ):
            with self.assertRaises(MaxsurfConnectionError):
                com.connect_maxsurf("modeler")


class ComDescribeTests(IsolatedEnvTestCase):
    def test_describes_the_object_at_a_safe_path(self):
        app = ComFake(Design=ComFake(Draft=2.0))
        with mock.patch.object(com, "connect", return_value=app), \
                mock.patch.object(
                    com.typeinfo, "describe_python",
                    return_value={"members": ["Draft"]},
                ) as describe:
            payload = json.loads(com.com_describe("Design"))
        self.assertEqual(payload["path"], "Design")
        self.assertEqual(payload["members"], ["Draft"])
        self.assertIs(describe.call_args.args[0], app.Design)

    def test_an_empty_path_describes_the_application(self):
        app = ComFake()
        with mock.patch.object(com, "connect", return_value=app), \
                mock.patch.object(
                    com.typeinfo, "describe_python", return_value={}
                ):
            payload = json.loads(com.com_describe(""))
        self.assertEqual(payload["path"], "Application")

    def test_an_unsafe_path_is_refused_before_description(self):
        from maxsurf_mcp.errors import MaxsurfSafetyError

        app = ComFake(Design=ComFake())
        with mock.patch.object(com, "connect", return_value=app), \
                mock.patch.object(com.typeinfo, "describe_python") as describe:
            with self.assertRaises(MaxsurfSafetyError):
                com.com_describe("__class__")
        describe.assert_not_called()

    def test_a_scalar_path_is_refused_because_it_is_not_an_object(self):
        app = ComFake(Version="25.0")
        with mock.patch.object(com, "connect", return_value=app):
            with self.assertRaises(MaxsurfValidationError):
                com.com_describe("Version")


if __name__ == "__main__":
    unittest.main()
