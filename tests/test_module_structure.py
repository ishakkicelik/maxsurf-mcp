from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import server
from maxsurf_mcp import config, guard

MODULES = (
    "com",
    "native_inventory",
    "constants",
    "typeinfo",
    "modeler",
    "modeler_files",
    "surfaces",
    "surface_extras",
    "meshes",
    "modeler_data",
    "frame",
    "hydrostatics",
    "hydrostatic_controls",
    "grids",
    "stability",
    "stability_controls",
    "stability_files",
    "resistance",
    "resistance_controls",
    "resistance_files",
    "motions",
    "motions_controls",
    "motions_files",
    "display",
    "multiframe",
    "multiframe_controls",
    "diagnostics",
    "native_automation",
    "native_objects",
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
    "discover_native_api": "maxsurf_mcp.native_inventory",
    "get_native_api_member": "maxsurf_mcp.native_inventory",
    "get_hydrostatic_settings": "maxsurf_mcp.hydrostatic_controls",
    "set_hydrostatic_settings": "maxsurf_mcp.hydrostatic_controls",
    "get_hydrostatic_results": "maxsurf_mcp.hydrostatic_controls",
    "transform_hydrostatics": "maxsurf_mcp.hydrostatic_controls",
    "advanced_transform_hydrostatics": "maxsurf_mcp.hydrostatic_controls",
    "set_stability_trim": "maxsurf_mcp.stability_controls",
    "set_stability_fluid_mode": "maxsurf_mcp.stability_controls",
    "set_stability_density": "maxsurf_mcp.stability_controls",
    "define_conditional_keypoints": "maxsurf_mcp.stability_controls",
    "export_stability_results_table": "maxsurf_mcp.stability_files",
    "open_stability_companion": "maxsurf_mcp.stability_files",
    "set_motions_frequency_range": "maxsurf_mcp.motions_controls",
    "set_motions_formulation": "maxsurf_mcp.motions_controls",
    "set_motions_vessel_settings": "maxsurf_mcp.motions_controls",
    "export_motions_condition_table": "maxsurf_mcp.motions_files",
    "export_motions_rao_text": "maxsurf_mcp.motions_files",
    "import_motions_rao_text": "maxsurf_mcp.motions_files",
    "get_multiframe_element_controls": "maxsurf_mcp.multiframe_controls",
    "set_multiframe_element_controls": "maxsurf_mcp.multiframe_controls",
    "add_multiframe_element_loads": "maxsurf_mcp.multiframe_controls",
    "set_multiframe_modal_controls": "maxsurf_mcp.multiframe_controls",
    "set_multiframe_units": "maxsurf_mcp.multiframe_controls",
    "read_multiframe_auxiliary_objects": "maxsurf_mcp.multiframe_controls",
    "add_multiframe_node_masses": "maxsurf_mcp.multiframe_controls",
    "add_multiframe_springs": "maxsurf_mcp.multiframe_controls",
    "add_multiframe_node_link": "maxsurf_mcp.multiframe_controls",
    "export_modeler_file": "maxsurf_mcp.modeler_files",
    "import_modeler_file": "maxsurf_mcp.modeler_files",
    "get_surface_linked_geometry": "maxsurf_mcp.surface_extras",
    "get_surface_area_properties": "maxsurf_mcp.surface_extras",
    "create_linked_geometry_surface": "maxsurf_mcp.surface_extras",
    "list_meshes": "maxsurf_mcp.meshes",
    "get_mesh_statistics": "maxsurf_mcp.meshes",
    "get_mesh_enums": "maxsurf_mcp.meshes",
    "mesh_surface_edge_length": "maxsurf_mcp.meshes",
    "mesh_surface_parametric": "maxsurf_mcp.meshes",
    "set_mesh_properties": "maxsurf_mcp.meshes",
    "move_mesh": "maxsurf_mcp.meshes",
    "rotate_mesh": "maxsurf_mcp.meshes",
    "scale_mesh": "maxsurf_mcp.meshes",
    "flip_mesh": "maxsurf_mcp.meshes",
    "invert_mesh_normals": "maxsurf_mcp.meshes",
    "repair_mesh": "maxsurf_mcp.meshes",
    "clean_mesh": "maxsurf_mcp.meshes",
    "combine_meshes": "maxsurf_mcp.meshes",
    "delete_mesh": "maxsurf_mcp.meshes",
    "set_resistance_measurements": "maxsurf_mcp.resistance_controls",
    "set_resistance_form_factor": "maxsurf_mcp.resistance_controls",
    "open_resistance_measurements": "maxsurf_mcp.resistance_files",
    "save_resistance_measurements": "maxsurf_mcp.resistance_files",
    "import_resistance_free_surface": "maxsurf_mcp.resistance_files",
    "export_resistance_free_surface": "maxsurf_mcp.resistance_files",
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
    "native_call": ("maxsurf_mcp.native_automation", guard.TIER_DIAGNOSTICS),
    "native_enumerate": ("maxsurf_mcp.native_automation", guard.TIER_DIAGNOSTICS),
    "release_native_handles": ("maxsurf_mcp.native_automation", guard.TIER_DIAGNOSTICS),
    "create_native_object": ("maxsurf_mcp.native_objects", guard.TIER_DIAGNOSTICS),
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
    def test_exact_tier_surfaces_in_fresh_processes(self):
        script = """
import inspect
import json
import server
try:
    tools = server.mcp._tool_manager.list_tools()
    print(json.dumps({
        tool.name: {
            "owner": tool.fn.__module__,
            "export": tool.fn.maxsurf_tool is getattr(server, tool.name),
            "async": inspect.iscoroutinefunction(tool.fn) and tool.is_async,
            "tier": tool.fn.maxsurf_tier,
            "schema": tool.parameters,
        } for tool in tools
    }))
finally:
    server.shutdown()
"""
        for diagnostics, legacy in ((False, False), (True, False),
                                    (False, True), (True, True)):
            with self.subTest(diagnostics=diagnostics, legacy=legacy):
                environment = dict(os.environ)
                environment[config.ENABLE_DIAGNOSTICS_VAR] = str(int(diagnostics))
                environment[config.ENABLE_LEGACY_VAR] = str(int(legacy))
                process = subprocess.run(
                    [sys.executable, "-c", script],
                    cwd=Path(__file__).resolve().parents[1],
                    env=environment, capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(process.returncode, 0, process.stderr)
                actual = json.loads(process.stdout)
                expected = {name: (owner, None)
                            for name, owner in EXPECTED_OWNERS.items()}
                expected.update({
                    name: (owner, tier)
                    for name, (owner, tier) in GATED_OWNERS.items()
                    if ((tier == guard.TIER_DIAGNOSTICS and diagnostics)
                        or (tier == guard.TIER_LEGACY and legacy))
                })
                self.assertEqual(len(EXPECTED_OWNERS), 144)
                self.assertEqual(set(actual), set(expected))
                for name, (owner, tier) in expected.items():
                    with self.subTest(tool=name):
                        self.assertEqual(actual[name]["owner"], owner)
                        self.assertEqual(actual[name]["tier"], tier)
                        self.assertTrue(actual[name]["export"])
                        self.assertTrue(actual[name]["async"])
                        self.assertIs(actual[name]["schema"]["additionalProperties"], False)
                if diagnostics:
                    self.assertEqual(actual["native_enumerate"]["schema"], {
                        "properties": {
                            "enum_handle": {"title": "Enum Handle", "type": "string"},
                            "operation": {"default": "next", "title": "Operation", "type": "string"},
                            "count": {"default": 1, "title": "Count", "type": "integer"},
                            "module": {"default": "modeler", "title": "Module", "type": "string"},
                            "acknowledge_native_effects": {
                                "default": False, "title": "Acknowledge Native Effects",
                                "type": "boolean",
                            },
                        },
                        "required": ["enum_handle"],
                        "title": "native_enumerateArguments",
                        "type": "object",
                        "additionalProperties": False,
                    })

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
