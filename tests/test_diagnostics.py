"""Tests for the DIAGNOSTIC tier: raw COM access, disabled by default."""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from maxsurf_mcp import config, diagnostics
from maxsurf_mcp.audit import Classification, Outcome
from maxsurf_mcp.errors import (
    MaxsurfCOMError,
    MaxsurfSafetyError,
    MaxsurfValidationError,
)

from .support import ComCollectionFake, ComFake, IsolatedEnvTestCase, ToolCollector


def _app() -> ComFake:
    hull = ComFake(Name="Hull")
    surfaces = ComCollectionFake([hull])
    return ComFake(Design=ComFake(Surfaces=surfaces, Draft=2.5), Version="25.0")


class DisabledByDefaultTests(IsolatedEnvTestCase):
    def test_the_tier_is_off_unless_the_flag_is_set(self):
        self.assertFalse(config.diagnostics_enabled())

    def test_diagnostic_tools_are_not_registered_by_default(self):
        collector = ToolCollector()
        registered = diagnostics.register_tools(collector)
        self.assertEqual(registered, [])
        self.assertEqual(collector.registered, [])

    def test_calling_a_diagnostic_tool_is_blocked_and_audited(self):
        for tool, args in (
            (diagnostics.com_get, ("Design.Draft",)),
            (diagnostics.com_set, ("Design.Draft", "3.0")),
            (diagnostics.com_invoke, ("Design.Save", "[]")),
        ):
            with self.subTest(tool=tool.__name__):
                with self.assertRaises(MaxsurfSafetyError) as caught:
                    tool(*args)
                self.assertIn("diagnostics tier", str(caught.exception).lower())
                self.assertIn(
                    config.ENABLE_DIAGNOSTICS_VAR, str(caught.exception)
                )
                records = self.records_for(tool.__name__)
                self.assertEqual(records[-1]["outcome"], Outcome.BLOCKED)
                self.assertEqual(
                    records[-1]["classification"], Classification.DIAGNOSTIC
                )

    def test_a_blocked_write_never_touches_com(self):
        with mock.patch.object(diagnostics.com, "connect") as connect:
            with self.assertRaises(MaxsurfSafetyError):
                diagnostics.com_set("Design.Draft", "3.0")
        connect.assert_not_called()


class EnabledTierTests(IsolatedEnvTestCase):
    env_overrides = {config.ENABLE_DIAGNOSTICS_VAR: "1"}

    def test_tools_are_registered_once_enabled(self):
        collector = ToolCollector()
        registered = diagnostics.register_tools(collector)
        self.assertEqual(registered, ["com_get", "com_set", "com_invoke"])

    def test_com_get_reads_a_scalar(self):
        with mock.patch.object(diagnostics.com, "connect", return_value=_app()):
            payload = json.loads(diagnostics.com_get("Design.Draft"))
        self.assertEqual(payload["value"], 2.5)
        self.assertEqual(payload["kind"], "scalar")

    def test_com_get_reports_objects_without_leaking_them(self):
        with mock.patch.object(diagnostics.com, "connect", return_value=_app()):
            payload = json.loads(diagnostics.com_get("Design.Surfaces"))
        self.assertIn("com_describe", payload["note"])
        self.assertNotIn("value", payload)

    def test_com_set_coerces_and_writes(self):
        app = _app()
        with mock.patch.object(diagnostics.com, "connect", return_value=app):
            payload = json.loads(diagnostics.com_set("Design.Draft", "3.25"))
        self.assertEqual(app.Design.Draft, 3.25)
        self.assertEqual(payload["written"], 3.25)
        self.assertEqual(payload["written_type"], "float")

    def test_com_set_supports_booleans_and_quoted_strings(self):
        app = ComFake(Design=ComFake(Flag=False, Label="old"))
        with mock.patch.object(diagnostics.com, "connect", return_value=app):
            diagnostics.com_set("Design.Flag", "true")
            diagnostics.com_set("Design.Label", "'123'")
        self.assertIs(app.Design.Flag, True)
        self.assertEqual(app.Design.Label, "123")

    def test_com_invoke_calls_a_method_with_json_arguments(self):
        calls = []

        class Design(ComFake):
            def SetControlPoint(self, *args):
                calls.append(args)
                return 42

        app = ComFake(Design=Design())
        with mock.patch.object(diagnostics.com, "connect", return_value=app):
            payload = json.loads(
                diagnostics.com_invoke("Design.SetControlPoint", "[1,2,0.5]")
            )
        self.assertEqual(calls, [(1, 2, 0.5)])
        self.assertEqual(payload["result"], 42)

    def test_com_invoke_rejects_malformed_arguments(self):
        app = _app()
        with mock.patch.object(diagnostics.com, "connect", return_value=app):
            with self.assertRaises(MaxsurfValidationError):
                diagnostics.com_invoke("Design.Save", "not json")
            with self.assertRaises(MaxsurfValidationError):
                diagnostics.com_invoke("Design.Save", '{"a": 1}')

    def test_com_invoke_rejects_a_non_callable_member(self):
        app = _app()
        with mock.patch.object(diagnostics.com, "connect", return_value=app):
            with self.assertRaises(MaxsurfValidationError):
                diagnostics.com_invoke("Design.Draft", "[]")

    def test_a_failing_com_method_raises_a_com_error(self):
        class Design(ComFake):
            def Save(self):
                raise OSError("Maxsurf said no")

        app = ComFake(Design=Design())
        with mock.patch.object(diagnostics.com, "connect", return_value=app):
            with self.assertRaises(MaxsurfCOMError):
                diagnostics.com_invoke("Design.Save", "[]")

    def test_enabling_the_tier_does_not_reopen_the_rce_path(self):
        """The tier being enabled must not make the interpreter reachable."""
        app = _app()
        exploits = (
            "__class__.__init__.__globals__[sys].modules[os].system",
            "Design.__class__.__init__.__globals__[sys].modules[os].system",
            "_oleobj_.Invoke",
            "Design.__dict__",
        )
        with mock.patch.object(diagnostics.com, "connect", return_value=app):
            for path in exploits:
                with self.subTest(path=path):
                    with self.assertRaises(MaxsurfSafetyError):
                        diagnostics.com_get(path)
                    with self.assertRaises(MaxsurfSafetyError):
                        diagnostics.com_invoke(path, "[]")
                    with self.assertRaises(MaxsurfSafetyError):
                        diagnostics.com_set(path, "1")

    def test_every_diagnostic_call_is_audited(self):
        with mock.patch.object(diagnostics.com, "connect", return_value=_app()):
            diagnostics.com_get("Design.Draft")
        records = self.records_for("com_get")
        self.assertEqual([r["stage"] for r in records], ["intent", "complete"])
        self.assertEqual(records[-1]["outcome"], Outcome.SUCCESS)


class CoerceValueTests(unittest.TestCase):
    def test_coercion_order_is_bool_none_int_float_then_string(self):
        cases = {
            "true": True, "False": False, "null": None, "none": None,
            "12": 12, "-3": -3, "2.5": 2.5, "hull": "hull",
            "'12'": "12", '"true"': "true",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(diagnostics.coerce_value(raw), expected)


if __name__ == "__main__":
    unittest.main()
