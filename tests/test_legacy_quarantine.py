"""Tests for the quarantine of tools whose COM members are not proven.

``parametric_transform``, ``export_model`` and ``run_analysis`` target members
that this project's own live ``ITypeInfo`` records show do not exist on the
interfaces they address. They must be absent from the production tool surface
and must refuse to run until the legacy tier is explicitly enabled.
"""

from __future__ import annotations

import unittest
from unittest import mock

from maxsurf_mcp import config, legacy
from maxsurf_mcp.audit import Classification, Outcome
from maxsurf_mcp.errors import (
    MaxsurfAnalysisError,
    MaxsurfCOMError,
    MaxsurfSafetyError,
)

from .support import ComFake, IsolatedEnvTestCase, ToolCollector

QUARANTINED = ("parametric_transform", "export_model", "run_analysis")


class QuarantineTests(IsolatedEnvTestCase):
    def test_the_legacy_tier_is_off_by_default(self):
        self.assertFalse(config.legacy_enabled())

    def test_quarantined_tools_are_not_registered_by_default(self):
        collector = ToolCollector()
        self.assertEqual(legacy.register_tools(collector), [])
        self.assertEqual(collector.registered, [])

    def test_quarantined_tools_are_absent_from_the_production_surface(self):
        import server

        names = {tool.name for tool in server.mcp._tool_manager.list_tools()}
        for name in QUARANTINED:
            with self.subTest(tool=name):
                self.assertNotIn(name, names)

    def test_calling_a_quarantined_tool_is_blocked_and_audited(self):
        calls = (
            (legacy.parametric_transform, (), Classification.WRITE_GEOMETRY),
            (legacy.export_model, ("C:/x/out.igs",), Classification.WRITE_FILE),
            (legacy.run_analysis, ("stability", "Equilibrium"),
             Classification.EXECUTE_ANALYSIS),
        )
        for tool, args, classification in calls:
            with self.subTest(tool=tool.__name__):
                with self.assertRaises(MaxsurfSafetyError) as caught:
                    tool(*args)
                self.assertIn(config.ENABLE_LEGACY_VAR, str(caught.exception))
                record = self.records_for(tool.__name__)[-1]
                self.assertEqual(record["outcome"], Outcome.BLOCKED)
                self.assertEqual(record["classification"], classification)

    def test_a_blocked_quarantined_tool_never_touches_com(self):
        with mock.patch.object(legacy.com, "connect") as connect:
            for tool, args in (
                (legacy.parametric_transform, ()),
                (legacy.run_analysis, ("stability", "Equilibrium")),
            ):
                with self.assertRaises(MaxsurfSafetyError):
                    tool(*args)
        connect.assert_not_called()

    def test_the_python_surface_still_exposes_them_for_reproduction(self):
        import server

        for name in QUARANTINED:
            with self.subTest(tool=name):
                self.assertTrue(callable(getattr(server, name)))
                self.assertEqual(
                    getattr(server, name).__module__, "maxsurf_mcp.legacy"
                )


class EnabledLegacyTierTests(IsolatedEnvTestCase):
    env_overrides = {config.ENABLE_LEGACY_VAR: "1"}

    def test_tools_are_registered_once_enabled(self):
        collector = ToolCollector()
        self.assertEqual(
            legacy.register_tools(collector), list(QUARANTINED)
        )

    def test_parametric_transform_raises_when_no_object_is_exposed(self):
        """The audited failure mode: the member simply is not there."""
        app = ComFake(Design=ComFake())
        with mock.patch.object(legacy.com, "connect", return_value=app):
            with self.assertRaises(MaxsurfCOMError) as caught:
                legacy.parametric_transform(target_lwl=24.5)
        self.assertIn("Design.Hydrostatics.Transform", str(caught.exception))

    def test_export_model_still_enforces_the_sandbox(self):
        outside = str(self.tmp / "escape.igs")
        with mock.patch.object(legacy.com, "connect") as connect:
            with self.assertRaises(MaxsurfSafetyError):
                legacy.export_model(outside, "iges")
        connect.assert_not_called()

    def test_export_model_still_requires_explicit_overwrite(self):
        existing = self.workspace_file("out.igs", "old")
        with mock.patch.object(legacy.com, "connect") as connect:
            with self.assertRaises(MaxsurfSafetyError) as caught:
                legacy.export_model(str(existing), "iges")
        self.assertIn("overwrite=True", str(caught.exception))
        connect.assert_not_called()

    def test_export_model_raises_when_no_export_member_exists(self):
        target = str(self.workspace_path("out.igs"))
        with mock.patch.object(legacy.com, "connect", return_value=ComFake()):
            with self.assertRaises(MaxsurfCOMError) as caught:
                legacy.export_model(target, "iges")
        self.assertIn("Design.ExportIGES", str(caught.exception))

    def test_run_analysis_raises_when_no_trigger_exists(self):
        app = ComFake(Design=ComFake())
        with mock.patch.object(legacy.com, "connect", return_value=app):
            with self.assertRaises(MaxsurfAnalysisError) as caught:
                legacy.run_analysis("stability", "Equilibrium", "{}")
        self.assertIn("Design.RunAnalysis()", str(caught.exception))

    def test_an_enabled_failure_is_still_audited_as_a_failure(self):
        app = ComFake(Design=ComFake())
        with mock.patch.object(legacy.com, "connect", return_value=app):
            with self.assertRaises(MaxsurfAnalysisError):
                legacy.run_analysis("stability", "Equilibrium", "{}")
        records = self.records_for("run_analysis")
        self.assertEqual([r["stage"] for r in records], ["intent", "complete"])
        self.assertEqual(records[-1]["outcome"], Outcome.FAILURE)


class StabilityBoundaryTests(IsolatedEnvTestCase):
    def test_the_stability_module_registers_its_proven_tools(self):
        """The placeholder is gone: transfer and equilibrium are now proven.

        The generic ``run_analysis`` stays quarantined regardless. It wrote
        caller-supplied properties onto whichever object accepted them and then
        hunted the Application for a trigger, whereas the real trigger is
        ``Design.RunAnalysis()`` with no arguments, which is what
        ``run_stability_equilibrium`` calls.
        """
        from maxsurf_mcp import stability

        collector = ToolCollector()
        expected = sorted([
            "transfer_modeler_to_stability", "run_stability_equilibrium",
            "define_subdivision", "set_heel_range", "define_loadcase",
            "define_damage_cases", "add_downflooding_points",
            "run_stability_gz", "list_criteria_libraries",
            "evaluate_stability_criteria", "save_stability_rooms", "export_stability_image"])
        self.assertEqual(sorted(stability.register_tools(collector)), expected)
        self.assertEqual(sorted(collector.registered), expected)

    def test_resistance_registers_workflow_and_motions_registers_nothing(self):
        from maxsurf_mcp import motions, resistance

        collector = ToolCollector()
        expected = [
            "get_resistance_status",
            "transfer_modeler_to_resistance",
            "configure_resistance_analysis",
            "run_resistance_analysis",
            "get_resistance_results",
            "export_resistance_results",
            "calculate_free_surface",
        ]
        self.assertEqual(resistance.register_tools(collector), expected)
        self.assertEqual(collector.registered, expected)

        collector = ToolCollector()
        motions_expected = [
            "get_motions_status", "transfer_modeler_to_motions",
            "configure_motions_analysis", "run_motions_analysis",
            "get_motions_results", "export_motions_table",
            "open_motions_design", "load_motions_data", "save_motions_state"]
        self.assertEqual(motions.register_tools(collector), motions_expected)
        self.assertEqual(collector.registered, motions_expected)


if __name__ == "__main__":
    unittest.main()
