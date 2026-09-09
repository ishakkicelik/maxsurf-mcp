"""Server-wide tests for the unified error contract.

Before Phase 0, some tools raised on failure while others returned a JSON
payload containing an ``error`` key. An MCP client sees the second form as a
*successful* tool call, so a failed write could silently become a data point in
a design loop. Every registered tool must now fail by raising.
"""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest import mock

import server
from maxsurf_mcp import audit, com, errors, guard
from maxsurf_mcp.audit import Classification, Outcome
from maxsurf_mcp.errors import MaxsurfCOMError, MaxsurfError

from .support import IsolatedEnvTestCase

#: Classification prefixes that a tool description may open with.
DESCRIPTION_PREFIXES = (
    "READ-ONLY.", "READ.", "WRITE.", "WRITE_FILE.", "WRITE_STATE.",
    "EXECUTE_ANALYSIS.", "DIAGNOSTIC.", "QUARANTINED / UNPROVEN.",
)

#: Tools whose entire purpose is to report or repair a broken connection. They
#: touch no COM member, so an unreachable Maxsurf is not a failure for them --
#: it is the situation they exist to handle. They are still bound by the rest of
#: the contract: they must never return a payload containing an ``error`` key.
CONNECTION_INDEPENDENT_TOOLS = frozenset({
    "reset_connection", "session_status",
    # lists the shipped .hcr criteria files on disk; no COM session needed
    "list_criteria_libraries",
})


def _registered_tools() -> dict[str, Any]:
    return {tool.name: tool for tool in server.mcp._tool_manager.list_tools()}


def _dummy_arguments(schema: dict[str, Any]) -> dict[str, Any]:
    """Build minimally valid arguments for every required parameter."""
    arguments: dict[str, Any] = {}
    properties = schema.get("properties", {})
    for name in schema.get("required", []):
        spec = properties.get(name, {})
        kind = spec.get("type")
        if kind == "integer":
            arguments[name] = 1
        elif kind == "number":
            arguments[name] = 1.0
        elif kind == "boolean":
            arguments[name] = False
        else:
            arguments[name] = "unusable-placeholder"
    return arguments


class FailureAlwaysRaisesTests(IsolatedEnvTestCase):
    def setUp(self):
        super().setUp()
        # These server-wide tests exercise all default tools. The UI adapter
        # does not use com.connect, so isolate its native backend separately.
        # Without this mock an installed pywinauto can reach the real desktop.
        patcher = mock.patch("maxsurf_mcp.display._window",
                             side_effect=errors.MaxsurfConnectionError("UI backend unavailable in offline test"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_registered_tool_raises_when_native_backends_are_unavailable(self):
        """No tool may absorb a connection failure into a success payload."""
        tools = _registered_tools()
        self.assertGreater(len(tools), 0)
        failure = errors.MaxsurfConnectionError("Maxsurf is not running")

        with mock.patch.object(com, "connect", side_effect=failure), \
                mock.patch.object(com, "scan_registry", side_effect=OSError("no registry")):
            for name, tool in sorted(tools.items()):
                if name in CONNECTION_INDEPENDENT_TOOLS:
                    continue
                with self.subTest(tool=name):
                    arguments = _dummy_arguments(tool.parameters)
                    with self.assertRaises(MaxsurfError) as caught:
                        getattr(server, name)(**arguments)
                    self.assertIsInstance(caught.exception, MaxsurfError)
                    self.assertNotIsInstance(caught.exception.args[0], dict)

    def test_the_recovery_tools_stay_usable_while_maxsurf_is_unreachable(self):
        """Reporting and resetting a connection must not need a connection."""
        tools = _registered_tools()
        failure = errors.MaxsurfConnectionError("Maxsurf is not running")

        with mock.patch.object(com, "connect", side_effect=failure), \
                mock.patch.object(com, "scan_registry", side_effect=OSError("no registry")):
            for name in sorted(CONNECTION_INDEPENDENT_TOOLS):
                with self.subTest(tool=name):
                    self.assertIn(name, tools)
                    result = getattr(server, name)(
                        **_dummy_arguments(tools[name].parameters))
                    self.assertNotIn("error", json.loads(result))

    def test_no_tool_returns_a_payload_with_a_top_level_error_key(self):
        tools = _registered_tools()
        failure = errors.MaxsurfConnectionError("Maxsurf is not running")
        returned: list[str] = []

        with mock.patch.object(com, "connect", side_effect=failure), \
                mock.patch.object(com, "scan_registry", side_effect=OSError("no registry")):
            for name, tool in sorted(tools.items()):
                try:
                    result = getattr(server, name)(**_dummy_arguments(tool.parameters))
                except MaxsurfError:
                    continue
                returned.append(name)
                payload = json.loads(result)
                self.assertNotIn("error", payload, f"{name} returned an error payload")
        self.assertEqual(returned, sorted(CONNECTION_INDEPENDENT_TOOLS),
                         f"unexpected tools did not raise: {returned}")

    def test_a_raw_exception_inside_a_tool_becomes_a_typed_error(self):
        @guard.guarded(classification=Classification.READ, module="probe")
        def _leaky() -> str:
            raise KeyError("a bare python error")

        with self.assertRaises(MaxsurfCOMError) as caught:
            _leaky()
        self.assertTrue(caught.exception.details["unexpected"])

    def test_every_failure_is_recorded_in_the_audit_log(self):
        failure = errors.MaxsurfConnectionError("Maxsurf is not running")
        with mock.patch.object(com, "connect", side_effect=failure):
            with self.assertRaises(MaxsurfError):
                server.surface_summary(1)
        record = self.records_for("surface_summary")[-1]
        self.assertEqual(record["outcome"], Outcome.FAILURE)
        self.assertEqual(record["error"]["code"], "connection_error")


class ErrorTaxonomyTests(unittest.TestCase):
    def test_the_taxonomy_is_complete_and_rooted(self):
        expected = {
            "MaxsurfConnectionError": "connection_error",
            "MaxsurfValidationError": "validation_error",
            "MaxsurfSafetyError": "safety_error",
            "MaxsurfCOMError": "com_error",
            "MaxsurfAnalysisError": "analysis_error",
        }
        for name, code in expected.items():
            with self.subTest(error=name):
                cls = getattr(errors, name)
                self.assertTrue(issubclass(cls, MaxsurfError))
                self.assertEqual(cls.code, code)

    def test_details_are_rendered_and_serialisable(self):
        error = errors.MaxsurfSafetyError("blocked", path="C:/x", index=2)
        text = str(error)
        self.assertIn("safety_error", text)
        self.assertIn("path=", text)
        payload = error.to_dict()
        self.assertEqual(payload["type"], "MaxsurfSafetyError")
        self.assertEqual(payload["details"]["index"], 2)
        json.dumps(payload)

    def test_a_com_error_records_its_cause(self):
        cause = OSError("0x80020009")
        error = errors.MaxsurfCOMError("failed", cause=cause, path="Design")
        self.assertEqual(error.details["com_source"], "OSError")
        self.assertIn("0x80020009", error.details["com_message"])
        self.assertIs(error.cause, cause)

    def test_error_payloads_redact_secret_details(self):
        error = errors.MaxsurfSafetyError("blocked", api_key="s3cret", path="x")
        payload = audit.error_payload(error)
        self.assertEqual(payload["details"]["api_key"], "[redacted]")
        self.assertEqual(payload["details"]["path"], "x")

    def test_a_foreign_exception_is_described_safely(self):
        payload = audit.error_payload(ValueError("boom"))
        self.assertEqual(payload["code"], "unexpected_error")
        self.assertEqual(payload["type"], "ValueError")


class ToolSurfaceContractTests(unittest.TestCase):
    def test_every_registered_tool_is_guarded_and_classified(self):
        for name in _registered_tools():
            with self.subTest(tool=name):
                function = getattr(server, name)
                self.assertIn(name, guard.REGISTRY)
                self.assertIn(
                    function.maxsurf_classification, Classification.ALL
                )
                self.assertIsNone(
                    function.maxsurf_tier,
                    "a gated tier must not appear in the production surface",
                )

    def test_every_tool_declares_its_classification_in_its_description(self):
        for name, tool in _registered_tools().items():
            with self.subTest(tool=name):
                self.assertTrue(
                    tool.description.startswith(DESCRIPTION_PREFIXES),
                    f"{name} description does not open with a classification",
                )

    def test_every_tool_returns_a_json_string(self):
        for name in _registered_tools():
            with self.subTest(tool=name):
                function = getattr(server, name)
                self.assertEqual(
                    function.__annotations__.get("return"), "str"
                )

    def test_the_startup_report_states_the_effective_policy(self):
        report = server.startup_report()
        self.assertEqual(report["tool_count"], len(report["registered_tools"]))
        self.assertFalse(report["policy"]["diagnostics_enabled"])
        self.assertFalse(report["policy"]["legacy_enabled"])
        self.assertTrue(report["sandbox"]["workspace_roots"])
        self.assertEqual(
            set(report["classifications"]), set(report["registered_tools"])
        )


if __name__ == "__main__":
    unittest.main()
