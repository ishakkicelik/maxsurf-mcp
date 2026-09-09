# Default tool catalog: 0.2.0-rc.5

90 tools. Default surface only; diagnostics and legacy mutation remain opt-in.
See the client schema for all arguments and safety preconditions, and
[capabilities](CAPABILITIES.md) for native acceptance limits. No design rules included.

## com

| Tool | Purpose |
| --- | --- |
| `connect_maxsurf` | WRITE_STATE. Connect to an already-running Maxsurf module. Call this first. |
| `reset_connection` | WRITE_STATE. Drop cached Maxsurf sessions so the next call reconnects. |
| `session_status` | READ-ONLY. READ. Report sessions, the COM worker, the policy, and Maxsurf processes. |
| `list_progids` | READ-ONLY. READ. List Maxsurf-related ProgIDs found in the registry (diagnostic). |
| `com_describe` | READ-ONLY. READ. Describe the COM object model from generated wrapper metadata. |

## constants

| Tool | Purpose |
| --- | --- |
| `com_constants` | READ-ONLY. Discover numeric constants from the generated type library. |

## typeinfo

| Tool | Purpose |
| --- | --- |
| `com_typeinfo` | READ-ONLY. Read COM ITypeInfo/FUNCDESC metadata directly. |

## modeler

| Tool | Purpose |
| --- | --- |
| `surface_summary` | READ-ONLY. Summarize one surface through proven ISurface properties. |
| `open_design` | WRITE_STATE. Open a .msd design through the proven Design.Open. |
| `save_design` | WRITE_FILE. Save the design to an explicit path through Design.SaveAs. |
| `list_surfaces` | READ-ONLY. List surfaces using the proven semantic summary fields. |
| `get_control_net` | READ-ONLY. Return every point using ISurface.GetControlPoint. |
| `set_control_point` | WRITE. Validate bounds, then make exactly one SetControlPoint call. |
| `create_library_surface` | WRITE. Create one library surface from a dynamically resolved msSL name. |
| `delete_surface` | WRITE. Delete one surface. The exact current name is required. |
| `set_control_points` | WRITE. Bulk-update points through proven Get/SetControlPoint methods. |
| `refresh_modeler_view` | WRITE_STATE. Make Modeler repaint so live changes become visible. |

## surfaces

| Tool | Purpose |
| --- | --- |
| `move_surface` | WRITE. Translate one surface, optionally leaving duplicates behind. |
| `rotate_surface` | WRITE. Rotate one surface about a point, optionally duplicating it. |
| `rescale_surface` | WRITE. Scale one surface about a centre point. Never duplicates. |
| `flip_surface` | WRITE. Mirror one surface about a plane, optionally keeping the original. |
| `invert_surface_normal` | WRITE. Flip which side of the surface faces outwards. |
| `get_surface_properties` | READ-ONLY. Report every proven property of one surface. |
| `set_surface_properties` | WRITE_STATE. Set surface properties, verifying each one by reading it back. |
| `set_assembly_visibility` | WRITE_STATE. Show or hide every surface in one assembly. |
| `list_surface_library` | READ-ONLY. List every msSurfaceLibrary shape this build actually offers. |
| `add_quadrilateral_surface` | WRITE. Create a surface from four corner points. |
| `add_parametric_surface` | WRITE. Create a surface from one of the parametric generators. |
| `bond_surfaces` | WRITE. Bond one surface edge to another so the skin stays continuous. |
| `unbond_surface` | WRITE. Release one bonded edge. Geometry stays; the constraint goes. |

## modeler_data

| Tool | Purpose |
| --- | --- |
| `inspect_surface_topology` | READ-ONLY. Return assemblies, control nets, and the surface bond graph. |
| `list_modeler_grid` | READ-ONLY. List Modeler construction grid lines with units. |
| `add_modeler_grid_line` | WRITE_STATE. Add one verified section/waterline/buttock/diagonal. |
| `set_modeler_grid_line` | WRITE_STATE. Replace one grid line guarded by its current label. |
| `replace_modeler_grid` | WRITE_STATE. Atomically-as-possible replace one complete grid category. |
| `set_section_split` | WRITE_STATE. Set the body-plan split section with an optimistic guard. |
| `list_markers` | READ-ONLY. List offset/reference markers without hiding failed getters. |
| `add_marker` | WRITE. Add one marker and verify the new collection item. |
| `set_marker` | WRITE. Edit one marker, guarded by its current name and read back. |
| `marker_fit_report` | READ-ONLY. Report RMS/max SurfaceError for associated markers. |

## frame

| Tool | Purpose |
| --- | --- |
| `get_frame_of_reference` | READ-ONLY. Report the Modeler frame of reference and its hull-derived values. |
| `set_frame_of_reference` | WRITE_STATE. Set the Modeler frame of reference. This moves the datum. |

## hydrostatics

| Tool | Purpose |
| --- | --- |
| `calculate_hydrostatics` | EXECUTE_ANALYSIS. Run Modeler hydrostatics and return the proven results. |

## grids

| Tool | Purpose |
| --- | --- |
| `get_stability_loadcase_table_headers` | READ-ONLY. Return live Stability load-case headings and unit labels. |
| `get_results_table` | READ-ONLY. Read an analysis result table, preferring XMLGrid when present. |

## stability

| Tool | Purpose |
| --- | --- |
| `export_stability_image` | WRITE_FILE. Save a native Stability view/graph to a NEW workspace PNG (Maxsurf 2025). |
| `transfer_modeler_to_stability` | WRITE_FILE. Save a unique Modeler copy and verify the destination design path. |
| `save_stability_rooms` | WRITE_FILE. Save native Stability tank and compartment definitions to a .htk file. |
| `run_stability_equilibrium` | EXECUTE_ANALYSIS. Run native equilibrium for an existing case, or create a new point-load case. |
| `define_subdivision` | WRITE_STATE. Create caller-specified prismatic rooms. Coordinates m, permeability fraction. |
| `set_heel_range` | WRITE_STATE. Set an explicit native heel schedule in degrees; verify every property. |
| `define_loadcase` | WRITE_STATE. Create an explicitly specified load case. Mass t, lengths m, FSM t.m. |
| `define_damage_cases` | WRITE_STATE. Add new named damage cases from explicit room names; never merge silently. |
| `add_downflooding_points` | WRITE_STATE. Add explicit native key points. Destructive replacement is currently disabled. |
| `run_stability_gz` | EXECUTE_ANALYSIS. Run native large-angle stability for the explicitly selected condition. |
| `list_criteria_libraries` | READ-ONLY. List the Maxsurf criteria standards available to import. |
| `evaluate_stability_criteria` | EXECUTE_ANALYSIS. Import the caller-selected installed .hcr library and run Maxsurf criteria. |

## resistance

| Tool | Purpose |
| --- | --- |
| `get_resistance_status` | READ-ONLY. Report Resistance readiness, design, speeds and methods. |
| `transfer_modeler_to_resistance` | WRITE_FILE. Save a unique Modeler copy and verify the destination design path. |
| `configure_resistance_analysis` | WRITE_STATE. Set explicit speed limits and type-library method names; verify readback. |
| `run_resistance_analysis` | EXECUTE_ANALYSIS. Measure and run the operator-selected native Resistance methods. |
| `get_resistance_results` | READ-ONLY. Return the current Resistance result collection in SI. |
| `export_resistance_results` | WRITE_FILE. Write the current speed-resistance-power curve to CSV. |
| `calculate_free_surface` | EXECUTE_ANALYSIS. Compute the free-surface wave pattern and write the |

## motions

| Tool | Purpose |
| --- | --- |
| `get_motions_status` | READ-ONLY. Report Motions readiness, design, vessel and analysis inputs. |
| `transfer_modeler_to_motions` | WRITE_FILE. Save a unique Modeler copy and verify the destination design path. |
| `configure_motions_analysis` | WRITE_STATE. Set explicit native seakeeping inputs, validating the entire request first. |
| `run_motions_analysis` | EXECUTE_ANALYSIS. Run strip theory or the legacy RADIF panel workflow; require numeric Summary. |
| `get_motions_results` | WRITE_FILE. Export/validate a native Summary before reading global seakeeping statistics. |
| `export_motions_table` | WRITE_FILE. Export a caller-selected native Motions result table to a text file. |
| `open_motions_design` | WRITE_STATE. Open an existing workspace .msd in Motions without changing Modeler. |
| `load_motions_data` | WRITE_STATE. Load native .skd inputs/settings or .skr inputs/results for an open hull. |
| `save_motions_state` | WRITE_FILE. Save native Motions inputs/settings to a NEW workspace .skd file. |

## display

| Tool | Purpose |
| --- | --- |
| `inspect_modeler_display` | READ-ONLY. Read only Modeler's known rendering/contour controls and view names. |
| `configure_modeler_rendering` | WRITE_STATE. Activate Perspective and set rendering/transparency/contour toggles idempotently. |
| `capture_modeler_view` | WRITE_FILE. Capture only Modeler's own window as PNG without global screen capture. |

## multiframe

See [native units and acceptance limits](MULTIFRAME.md).

| Tool | Purpose |
| --- | --- |
| `get_multiframe_status` | READ-ONLY. READ. Report native readiness, frame identity/counts, current units and analysis flags. |
| `list_multiframe_sections` | READ-ONLY. READ. Page the existing section-library groups or one group's sections; never edits the library. |
| `read_multiframe_model` | READ-ONLY. READ. Page nodes, beams, supports and load-case definitions in current native units. |
| `new_multiframe_model` | WRITE_STATE. Start an empty frame; refuse modified work, require consent for an existing saved frame. |
| `open_multiframe_model` | WRITE_STATE. Open a workspace .mfd, refusing modified work and verifying native FullName. |
| `save_multiframe_model` | WRITE_FILE. Save a new .mfd without overwrite; verify native identity and nonempty file. |
| `add_multiframe_nodes` | WRITE_STATE. Append nodes [{label,x,y,z}]; validate the complete batch and unit/count snapshots first. |
| `add_multiframe_elements` | WRITE_STATE. Append beams with explicit nodes and existing library sections. |
| `add_multiframe_restraints` | WRITE_STATE. Append supports [{node,node_label,type}] using named mfRestraint constants in global axes. |
| `add_multiframe_loadcase` | WRITE_STATE. Append a static load case with nodal loads [{node,node_label,dof,value}]. |
| `run_multiframe_analysis` | EXECUTE_ANALYSIS. Run a 3D linear static or distributed-mass modal analysis. |
| `get_multiframe_results` | READ-ONLY. READ. Read a solved linear case or modal shape, with native units and bounded rows. |
| `export_multiframe_results` | WRITE_FILE. Export a bounded solved-result page as JSON, with units; no overwrite. |
| `refresh_multiframe_view` | WRITE_STATE. Enable native screen updates and refresh; no clipboard or arbitrary UI commands. |
