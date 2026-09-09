"""MCP bootstrap for the modular Maxsurf application integrations.

Only bounded, guarded adapters are registered by default; native acceptance
limits are documented separately. Two tiers stay out of the
production tool surface unless they are explicitly enabled:

* ``MAXSURF_MCP_ENABLE_DIAGNOSTICS=1`` exposes raw COM access
  (``com_get`` / ``com_set`` / ``com_invoke``).
* ``MAXSURF_MCP_ENABLE_LEGACY=1`` exposes quarantined tools whose COM members
  live discovery did not find.
"""

from __future__ import annotations

from maxsurf_mcp import comcache

# Configure pywin32 before importing session/com modules. A corrupt wrapper in
# the user's shared Temp directory must not disable an otherwise healthy,
# already-running Maxsurf application.
COM_CACHE = comcache.configure()

from mcp.server.fastmcp import FastMCP

from maxsurf_mcp import audit, com, comthread, config, diagnostics, guard, legacy
from maxsurf_mcp import constants as comconstants
from maxsurf_mcp import frame, grids, hydrostatics, modeler, modeler_data
from maxsurf_mcp import motions, resistance, session, stability, surfaces
from maxsurf_mcp import typeinfo, workspace, display, multiframe


VERSION = "0.2.0-rc.5"
mcp = FastMCP("maxsurf")
# SDK 1.29 FastMCP has no version constructor argument.
mcp._mcp_server.version = VERSION


# Keep the original Python import surface stable for tests and scripts. Note
# that presence here does not mean a tool is registered with MCP: registration
# is decided per tier in register_tools().
_ok = com.to_json
_try = com.first_available

connect_maxsurf = com.connect_maxsurf
reset_connection = com.reset_connection
session_status = com.session_status
list_progids = com.list_progids
com_describe = com.com_describe
com_typeinfo = typeinfo.com_typeinfo
com_constants = comconstants.com_constants

surface_summary = modeler.surface_summary
open_design = modeler.open_design
save_design = modeler.save_design
list_surfaces = modeler.list_surfaces
get_control_net = modeler.get_control_net
set_control_point = modeler.set_control_point
create_library_surface = modeler.create_library_surface
delete_surface = modeler.delete_surface
set_control_points = modeler.set_control_points
refresh_modeler_view = modeler.refresh_modeler_view

move_surface = surfaces.move_surface
rotate_surface = surfaces.rotate_surface
rescale_surface = surfaces.rescale_surface
flip_surface = surfaces.flip_surface
invert_surface_normal = surfaces.invert_surface_normal
get_surface_properties = surfaces.get_surface_properties
set_surface_properties = surfaces.set_surface_properties
set_assembly_visibility = surfaces.set_assembly_visibility
list_surface_library = surfaces.list_surface_library
add_quadrilateral_surface = surfaces.add_quadrilateral_surface
add_parametric_surface = surfaces.add_parametric_surface
bond_surfaces = surfaces.bond_surfaces
unbond_surface = surfaces.unbond_surface

inspect_surface_topology = modeler_data.inspect_surface_topology
list_modeler_grid = modeler_data.list_modeler_grid
add_modeler_grid_line = modeler_data.add_modeler_grid_line
set_modeler_grid_line = modeler_data.set_modeler_grid_line
replace_modeler_grid = modeler_data.replace_modeler_grid
set_section_split = modeler_data.set_section_split
list_markers = modeler_data.list_markers
add_marker = modeler_data.add_marker
set_marker = modeler_data.set_marker
marker_fit_report = modeler_data.marker_fit_report

get_frame_of_reference = frame.get_frame_of_reference
set_frame_of_reference = frame.set_frame_of_reference

calculate_hydrostatics = hydrostatics.calculate_hydrostatics
transfer_modeler_to_stability = stability.transfer_modeler_to_stability
save_stability_rooms = stability.save_stability_rooms
run_stability_equilibrium = stability.run_stability_equilibrium
define_subdivision = stability.define_subdivision
set_heel_range = stability.set_heel_range
define_loadcase = stability.define_loadcase
define_damage_cases = stability.define_damage_cases
add_downflooding_points = stability.add_downflooding_points
run_stability_gz = stability.run_stability_gz
list_criteria_libraries = stability.list_criteria_libraries
evaluate_stability_criteria = stability.evaluate_stability_criteria
get_resistance_status = resistance.get_resistance_status
transfer_modeler_to_resistance = resistance.transfer_modeler_to_resistance
configure_resistance_analysis = resistance.configure_resistance_analysis
run_resistance_analysis = resistance.run_resistance_analysis
get_resistance_results = resistance.get_resistance_results
export_resistance_results = resistance.export_resistance_results
calculate_free_surface = resistance.calculate_free_surface
get_motions_status = motions.get_motions_status
transfer_modeler_to_motions = motions.transfer_modeler_to_motions
configure_motions_analysis = motions.configure_motions_analysis
run_motions_analysis = motions.run_motions_analysis
get_motions_results = motions.get_motions_results
export_motions_table = motions.export_motions_table
open_motions_design = motions.open_motions_design
load_motions_data = motions.load_motions_data
save_motions_state = motions.save_motions_state
export_stability_image = stability.export_stability_image
inspect_modeler_display = display.inspect_modeler_display
configure_modeler_rendering = display.configure_modeler_rendering
capture_modeler_view = display.capture_modeler_view

get_multiframe_status = multiframe.get_multiframe_status
list_multiframe_sections = multiframe.list_multiframe_sections
read_multiframe_model = multiframe.read_multiframe_model
new_multiframe_model = multiframe.new_multiframe_model
open_multiframe_model = multiframe.open_multiframe_model
save_multiframe_model = multiframe.save_multiframe_model
add_multiframe_nodes = multiframe.add_multiframe_nodes
add_multiframe_elements = multiframe.add_multiframe_elements
add_multiframe_restraints = multiframe.add_multiframe_restraints
add_multiframe_loadcase = multiframe.add_multiframe_loadcase
run_multiframe_analysis = multiframe.run_multiframe_analysis
get_multiframe_results = multiframe.get_multiframe_results
export_multiframe_results = multiframe.export_multiframe_results
refresh_multiframe_view = multiframe.refresh_multiframe_view

get_results_table = grids.get_results_table
get_stability_loadcase_table_headers = grids.get_stability_loadcase_table_headers

# DIAGNOSTIC tier: importable, but only registered when explicitly enabled.
com_get = diagnostics.com_get
com_set = diagnostics.com_set
com_invoke = diagnostics.com_invoke

# Quarantined: unproven COM members, only registered when explicitly enabled.
parametric_transform = legacy.parametric_transform
export_model = legacy.export_model
run_analysis = legacy.run_analysis


#: Modules that own tools, in registration order.
TOOL_MODULES = (
    com,
    comconstants,
    typeinfo,
    modeler,
    surfaces,
    modeler_data,
    frame,
    hydrostatics,
    grids,
    stability,
    resistance,
    motions,
    display,
    multiframe,
    diagnostics,
    legacy,
)


def register_tools() -> list[str]:
    """Register every enabled tool exactly once from its owning module."""
    registered: list[str] = []
    for module in TOOL_MODULES:
        registered.extend(module.register_tools(mcp) or [])
    return registered


REGISTERED_TOOLS = register_tools()


def interpreter_report() -> dict[str, object]:
    """Report the facts that make COM binding work, or silently not work.

    A 32-bit interpreter cannot bind to Maxsurf's 64-bit COM servers, and the
    failure looks like a missing registration rather than an architecture
    mismatch, so it is stated up front.
    """
    import struct
    import sys

    bits = struct.calcsize("P") * 8
    return {
        "python_version": sys.version.split()[0],
        "bits": bits,
        "meets_64bit_requirement": bits == 64,
        "platform": sys.platform,
    }


def startup_report() -> dict[str, object]:
    """Describe the effective policy and tool surface."""
    return {
        "server": "maxsurf",
        "version": VERSION,
        "com_cache": COM_CACHE,
        "interpreter": interpreter_report(),
        "registered_tools": sorted(REGISTERED_TOOLS),
        "tool_count": len(REGISTERED_TOOLS),
        "policy": config.describe(),
        "sandbox": workspace.describe(),
        "sessions": session.get_manager().describe(),
        "com_worker": comthread.describe(),
        "classifications": {
            name: guard.REGISTRY[name]["classification"]
            for name in sorted(REGISTERED_TOOLS)
            if name in guard.REGISTRY
        },
    }


def shutdown() -> dict[str, object] | None:
    """Release COM sessions and tear the apartment down deliberately.

    The session drop is submitted to the worker so the final ``Release`` on each
    interface pointer happens inside the apartment that created it, rather than
    on whichever thread the interpreter is shutting down from.
    """
    manager = session.get_manager()
    worker = comthread.peek_worker()
    if worker is not None and worker.is_running():
        # Never queue a release behind an abandoned native call or release its
        # interface from the wrong apartment. A timeout did NOT cancel Maxsurf.
        if not worker.describe().get("abandoned_operations"):
            worker.run(manager.reset, description="release_sessions")
    else:
        manager.reset()
    return comthread.shutdown()


def main():
    # stdout carries the JSON-RPC stream and must stay clean, so all diagnostic
    # output goes to stderr. It is kept to a single short line on purpose: a
    # large blob written to stderr before the transport starts can fill the
    # pipe buffer and deadlock the launch if the client is slow to drain it.
    # The full startup report still goes to the audit log, not to the stream.
    import sys

    report = startup_report()
    interp = report["interpreter"]
    print(
        f"maxsurf-mcp ready: {report['tool_count']} tools | "
        f"attach_policy={report['policy'].get('attach_policy')} | "
        f"python {interp['python_version']} {interp['bits']}-bit "
        f"{'ok' if interp['meets_64bit_requirement'] else 'NOT 64-bit'}",
        file=sys.stderr, flush=True,
    )
    audit.get_log().record(
        stage="complete",
        tool="server_start",
        module="server",
        classification=audit.Classification.READ,
        channel=audit.Channel.INTERNAL,
        outcome=audit.Outcome.SUCCESS,
        result_summary=report,
    )
    try:
        mcp.run(transport="stdio")
    finally:
        shutdown()


if __name__ == "__main__":
    main()
