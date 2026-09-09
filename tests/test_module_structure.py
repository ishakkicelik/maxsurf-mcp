from __future__ import annotations

import importlib
import json
import unittest
from pathlib import Path

import server
from maxsurf_mcp import config, guard

MODULES = (
    "com",
    "constants",
    "typeinfo",
    "modeler",
    "surfaces",
    "modeler_data",
    "frame",
    "hydrostatics",
    "grids",
    "stability",
    "resistance",
    "motions",
    "display",
    "multiframe",
    "diagnostics",
    "legacy",
)

CORE_MODULES = (
    "errors", "comcache",
    "config",
    "objectpath",
    "workspace",
    "audit",
    "guard",
    "comthread",
    "processes",
    "session",
)

#: The production tool surface: everything registered with no tier gate.
EXPECTED_OWNERS = {
    "get_multiframe_status": "maxsurf_mcp.multiframe",
    "list_multiframe_sections": "maxsurf_mcp.multiframe",
    "read_multiframe_model": "maxsurf_mcp.multiframe",
    "new_multiframe_model": "maxsurf_mcp.multiframe",
    "open_multiframe_model": "maxsurf_mcp.multiframe",
    "save_multiframe_model": "maxsurf_mcp.multiframe",
    "add_multiframe_nodes": "maxsurf_mcp.multiframe",
    "add_multiframe_elements": "maxsurf_mcp.multiframe",
    "add_multiframe_restraints": "maxsurf_mcp.multiframe",
    "add_multiframe_loadcase": "maxsurf_mcp.multiframe",
    "run_multiframe_analysis": "maxsurf_mcp.multiframe",
    "get_multiframe_results": "maxsurf_mcp.multiframe",
    "export_multiframe_results": "maxsurf_mcp.multiframe",
    "refresh_multiframe_view": "maxsurf_mcp.multiframe",

    "open_motions_design": "maxsurf_mcp.motions",
    "load_motions_data": "maxsurf_mcp.motions",
    "save_motions_state": "maxsurf_mcp.motions",
    "export_stability_image": "maxsurf_mcp.stability",
    "inspect_modeler_display": "maxsurf_mcp.display",
    "configure_modeler_rendering": "maxsurf_mcp.display",
    "capture_modeler_view": "maxsurf_mcp.display",
    "connect_maxsurf": "maxsurf_mcp.com",
    "reset_connection": "maxsurf_mcp.com",
    "session_status": "maxsurf_mcp.com",
    "list_progids": "maxsurf_mcp.com",
    "com_describe": "maxsurf_mcp.com",
    "com_constants": "maxsurf_mcp.constants",
    "com_typeinfo": "maxsurf_mcp.typeinfo",
    "surface_summary": "maxsurf_mcp.modeler",
    "open_design": "maxsurf_mcp.modeler",
    "save_design": "maxsurf_mcp.modeler",
    "list_surfaces": "maxsurf_mcp.modeler",
    "get_control_net": "maxsurf_mcp.modeler",
    "set_control_point": "maxsurf_mcp.modeler",
    "create_library_surface": "maxsurf_mcp.modeler",
    "delete_surface": "maxsurf_mcp.modeler",
    "set_control_points": "maxsurf_mcp.modeler",
    "refresh_modeler_view": "maxsurf_mcp.modeler",
    "move_surface": "maxsurf_mcp.surfaces",
    "rotate_surface": "maxsurf_mcp.surfaces",
    "rescale_surface": "maxsurf_mcp.surfaces",
    "flip_surface": "maxsurf_mcp.surfaces",
    "invert_surface_normal": "maxsurf_mcp.surfaces",
    "get_surface_properties": "maxsurf_mcp.surfaces",
    "set_surface_properties": "maxsurf_mcp.surfaces",
    "set_assembly_visibility": "maxsurf_mcp.surfaces",
    "list_surface_library": "maxsurf_mcp.surfaces",
    "add_quadrilateral_surface": "maxsurf_mcp.surfaces",
    "add_parametric_surface": "maxsurf_mcp.surfaces",
    "bond_surfaces": "maxsurf_mcp.surfaces",
    "unbond_surface": "maxsurf_mcp.surfaces",
    "inspect_surface_topology": "maxsurf_mcp.modeler_data",
    "list_modeler_grid": "maxsurf_mcp.modeler_data",
    "add_modeler_grid_line": "maxsurf_mcp.modeler_data",
    "set_modeler_grid_line": "maxsurf_mcp.modeler_data",
    "replace_modeler_grid": "maxsurf_mcp.modeler_data",
    "set_section_split": "maxsurf_mcp.modeler_data",
    "list_markers": "maxsurf_mcp.modeler_data",
    "add_marker": "maxsurf_mcp.modeler_data",
    "set_marker": "maxsurf_mcp.modeler_data",
    "marker_fit_report": "maxsurf_mcp.modeler_data",
    "get_frame_of_reference": "maxsurf_mcp.frame",
    "set_frame_of_reference": "maxsurf_mcp.frame",
    "calculate_hydrostatics": "maxsurf_mcp.hydrostatics",
    "transfer_modeler_to_stability": "maxsurf_mcp.stability",
    "run_stability_equilibrium": "maxsurf_mcp.stability",
    "save_stability_rooms": "maxsurf_mcp.stability",
    "define_subdivision": "maxsurf_mcp.stability",
    "set_heel_range": "maxsurf_mcp.stability",
    "define_loadcase": "maxsurf_mcp.stability",
    "define_damage_cases": "maxsurf_mcp.stability",
    "add_downflooding_points": "maxsurf_mcp.stability",
    "run_stability_gz": "maxsurf_mcp.stability",
    "list_criteria_libraries": "maxsurf_mcp.stability",
    "evaluate_stability_criteria": "maxsurf_mcp.stability",
    "get_resistance_status": "maxsurf_mcp.resistance",
    "transfer_modeler_to_resistance": "maxsurf_mcp.resistance",
    "configure_resistance_analysis": "maxsurf_mcp.resistance",
    "run_resistance_analysis": "maxsurf_mcp.resistance",
    "get_resistance_results": "maxsurf_mcp.resistance",
    "export_resistance_results": "maxsurf_mcp.resistance",
    "calculate_free_surface": "maxsurf_mcp.resistance",
    "get_motions_status": "maxsurf_mcp.motions",
    "transfer_modeler_to_motions": "maxsurf_mcp.motions",
    "configure_motions_analysis": "maxsurf_mcp.motions",
    "run_motions_analysis": "maxsurf_mcp.motions",
    "get_motions_results": "maxsurf_mcp.motions",
    "export_motions_table": "maxsurf_mcp.motions",
    "get_results_table": "maxsurf_mcp.grids",
    "get_stability_loadcase_table_headers": "maxsurf_mcp.grids",
}

#: Tools that exist in Python but are kept out of the default MCP surface.
GATED_OWNERS = {
    "com_get": ("maxsurf_mcp.diagnostics", guard.TIER_DIAGNOSTICS),
    "com_set": ("maxsurf_mcp.diagnostics", guard.TIER_DIAGNOSTICS),
    "com_invoke": ("maxsurf_mcp.diagnostics", guard.TIER_DIAGNOSTICS),
    "parametric_transform": ("maxsurf_mcp.legacy", guard.TIER_LEGACY),
    "export_model": ("maxsurf_mcp.legacy", guard.TIER_LEGACY),
    "run_analysis": ("maxsurf_mcp.legacy", guard.TIER_LEGACY),
}

#: A readable snapshot of the registered input schemas. Committed as JSON
#: rather than as a hash so that any intentional schema change shows up as a
#: reviewable diff instead of one unexplainable hex string.
SCHEMA_SNAPSHOT = Path(__file__).with_name("data") / "tool_schemas.json"


def _registered() -> dict[str, object]:
    return {tool.name: tool for tool in server.mcp._tool_manager.list_tools()}


class ModuleStructureTests(unittest.TestCase):
    def test_application_modules_import_and_expose_registration(self):
        for module_name in MODULES:
            with self.subTest(module=module_name):
                module = importlib.import_module(f"maxsurf_mcp.{module_name}")
                self.assertTrue(callable(module.register_tools))

    def test_core_safety_modules_import(self):
        for module_name in CORE_MODULES:
            with self.subTest(module=module_name):
                importlib.import_module(f"maxsurf_mcp.{module_name}")

    def test_registered_tools_are_owned_by_the_expected_modules(self):
        tools = _registered()
        self.assertEqual(set(tools), set(EXPECTED_OWNERS))
        for name, module_name in EXPECTED_OWNERS.items():
            with self.subTest(tool=name):
                self.assertEqual(tools[name].fn.__module__, module_name)
                # FastMCP is given a coroutine adapter so a COM call cannot
                # block the event loop; the guarded function it wraps is the one
                # exported for direct use.
                self.assertIs(tools[name].fn.maxsurf_tool, getattr(server, name))

    def test_every_registered_tool_is_awaited_rather_than_run_inline(self):
        import inspect

        for name, tool in _registered().items():
            with self.subTest(tool=name):
                self.assertTrue(inspect.iscoroutinefunction(tool.fn))
                self.assertTrue(tool.is_async)

    def test_gated_tools_are_importable_but_unregistered(self):
        tools = _registered()
        for name, (module_name, tier) in GATED_OWNERS.items():
            with self.subTest(tool=name):
                self.assertNotIn(name, tools)
                function = getattr(server, name)
                self.assertEqual(function.__module__, module_name)
                self.assertEqual(function.maxsurf_tier, tier)

    def test_every_tool_module_is_listed_in_the_registration_order(self):
        listed = {module.__name__.split(".")[-1] for module in server.TOOL_MODULES}
        self.assertEqual(listed, set(MODULES))

    def test_no_tool_is_registered_twice(self):
        names = [tool.name for tool in server.mcp._tool_manager.list_tools()]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(sorted(names), sorted(server.REGISTERED_TOOLS))


class SchemaSnapshotTests(unittest.TestCase):
    """Guards the wire contract, field by field.

    A deliberate change here is expected to be accompanied by an update to
    ``tests/data/tool_schemas.json`` in the same commit.
    """

    def setUp(self) -> None:
        self.expected = json.loads(SCHEMA_SNAPSHOT.read_text(encoding="utf-8"))
        self.actual = {
            name: tool.parameters for name, tool in _registered().items()
        }

    def test_the_registered_tool_set_matches_the_snapshot(self):
        self.assertEqual(sorted(self.actual), sorted(self.expected))

    def test_each_parameter_schema_matches_the_snapshot(self):
        for name in sorted(self.expected):
            with self.subTest(tool=name):
                self.assertEqual(self.actual[name], self.expected[name])

    def test_delete_surface_requires_its_name_guard(self):
        required = self.expected["delete_surface"]["required"]
        self.assertIn("expected_name", required)

    def test_write_tools_expose_an_overwrite_flag(self):
        self.assertIn("overwrite", self.expected["save_design"]["properties"])


class PolicyDefaultTests(unittest.TestCase):
    def test_optional_tiers_are_off_in_a_clean_configuration(self):
        import os

        for variable in (config.ENABLE_DIAGNOSTICS_VAR, config.ENABLE_LEGACY_VAR):
            with self.subTest(variable=variable):
                saved = os.environ.pop(variable, None)
                try:
                    self.assertFalse(guard.tier_enabled(
                        guard.TIER_DIAGNOSTICS
                        if variable == config.ENABLE_DIAGNOSTICS_VAR
                        else guard.TIER_LEGACY
                    ))
                finally:
                    if saved is not None:
                        os.environ[variable] = saved

    def test_the_runtime_directory_is_the_default_sandbox_root(self):
        import os

        saved = os.environ.pop(config.WORKSPACE_ROOTS_VAR, None)
        try:
            roots = config.workspace_roots()
            self.assertEqual(roots, (config.runtime_root().resolve(),))
        finally:
            if saved is not None:
                os.environ[config.WORKSPACE_ROOTS_VAR] = saved


if __name__ == "__main__":
    unittest.main()
