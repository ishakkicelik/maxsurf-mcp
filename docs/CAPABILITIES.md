# Capability and acceptance matrix

This expanded 0.2.0-rc.5 source exposes **144 default tools**, plus seven diagnostic
tools when explicitly enabled. Tool count is not a percentage of Maxsurf
coverage. Installed native contracts are exhaustively discoverable and callable
through the validated extension where their Automation representation and
object route exist. GUI-only and unsupported workflows remain explicit rather
than fabricated. See [native coverage and effect boundaries](NATIVE_AUTOMATION.md).
Complete default schemas are in `tests/data/tool_schemas.json`.

## Source expansion

| Area | Added controls | Evidence boundary |
| --- | --- | --- |
| Geometry files | Rhino 3DM, IGES, DGN, DXF, PDstrip, MOSES, native OBJ/STL/OFF/PLY/GDF/nuShallo trimesh formats | Closed member mappings, workspace/overwrite checks, file evidence; not universal dialect fidelity |
| Modeling | Four native tube generators, linked geometry, area properties, stricter control-net readback and transform/duplicate checks | Native control-point and translation checks; API limits remain visible |
| Trimeshes | Surface meshing, properties/statistics, transforms, normals, cleaning/repair, join/union/intersection and deletion | No fabricated node/triangle mutation or Boolean difference |
| Hydrostatics | Settings/results, exact 13-argument transform and advanced transforms | Native units/unsupported targets stated; target achievement is separate acceptance |
| Stability | Trim/fluid/density policies, conditional keypoints, result exports, companion inputs, current-install criteria | Stored values verified; regulatory approval not claimed |
| Resistance | Measurements/form factor, measurements persistence, free-surface import/export, velocity cross-checks | Solver and format acceptance remain project-specific |
| Motions | Frequency/formulation/vessel settings, selected-condition/RAO import/export, location readback | Protected legacy/solver sequences remain refused |
| Multiframe | Element controls/loads, units/modal controls, masses/springs/node links, auxiliary reads, truthful result coverage | Array/unit interpretation and full numerical acceptance need native evidence |
| Native access | Paginated seven-library inventory, exact member contracts, validated invocation, scoped object handles and registered helper construction | ABI validation is not engineering acceptance; advanced unmapped effects are explicitly marked |

| Area | Implemented adapter scope | Verification and limits |
| --- | --- | --- |
| Connection/runtime | Process discovery, COM registration/type information/constants, per-module sessions, owned STA worker, audit, workspace checks | Local protocol and offline tests; exact COM window/PID binding not guaranteed |
| Modeler geometry | Surface library, quadrilateral/parametric surfaces, control-net reads/edits, transforms, symmetry/appearance properties, bonds | Native edit/readback evidence; not an automatic fairness or watertightness solver |
| Modeler coordinates/lines | Frame of reference, stations/waterlines/buttocks grid, split position, markers | Grid contours intersect surfaces; they are not independent editable structural frames |
| Modeler files | Guarded Open/SaveAs | Default save-current and explicit discard consent; native save/reopen round trip still not accepted |
| Modeler display | Rendering/transparency, contour visibility, control-net hiding, app-window PNG | Observed English 2025 UI checked states and colored PNG; full shader selection, Control Box setup and camera composition not covered |
| Modeler hydrostatics | Native upright values | VCG/GM convention and independent numerical comparison still open |
| Stability subdivision | Explicit tanks/compartments, permeability and bounds; native room save | `.htk` preserves rooms only; does not automatically position them to regulations |
| Stability loading/damage | Caller-provided loads, tank fills, damage cases, downflooding points | Replacement refused when safe preservation of dependent state is not established |
| Stability image | Native SaveImage PNG export with decoded-file checks | Native loaded-model PNG accepted; actual dimensions can differ from requested; no clipboard or desktop capture |
| Stability analysis | Equilibrium, heel schedule, GZ, caller-selected native criteria library | RC5 new-concept equilibrium/seven GZ rows tested; full project restore and independent damaged-stability acceptance open |
| Resistance | Transfer, method/speed selection, physical inputs, native calculation, result read/export, free surface | RC5 new-concept 81-point curve rerun; full same-vessel loading and empirical applicability acceptance remain open |
| Motions | Direct open/transfer, companion-data load, .skd save, explicit inputs, strip/legacy RADIF runner, native table export | One sample strip run and selected Summary/COM values matched; .skr save disabled after timeout; modern MOSES execution not implemented |
| Motions external workflows | Discovery can report MOSES/MARTEC/imported-RAO enums | Runner deliberately refuses those workflows; their native setup is not implemented |
| Multiframe | Native units/files, nodes/beams/supports, nodal/element loading, structural auxiliaries, linear and experimental modal analysis, paged JSON export | Static benchmark/save/open/reanalysis and two-mode test; third-mode access, oriented-support persistence and broader FE workflows unresolved; see [scope](MULTIFRAME.md) |
| Other suite programs | Installed VPP and Shape Editor contracts included in discovery and diagnostic automation | No semantic vessel/rig or section-construction runner is established; metadata availability does not prove engineering execution |

## What the bridge does not supply

- Ship recipes, class-rule design decisions, automatic compliant tank/compartment
  assignment, optimized hull generation or LLM training datasets.
- Uniform format fidelity or dedicated semantic adapters for every native file
  format, independent curve fitting/editing, or complete frames/decks/stringers/
  plates fabrication workflows. Accessible native contracts remain discoverable
  through diagnostic automation; that route does not establish workflow acceptance.
- A complete native analysis project backup/restore, transaction rollback,
  solver cancellation, or guaranteed result freshness after external GUI edits.
- Seaworthiness, regulatory certification, optimum performance or engineering
  validation inferred from tool success, a rendered image or a numeric table.

## Why a reference image can look different

A lines plan is produced by surface intersections with a design grid. Increasing
contour visibility does not refine the underlying surface. Shaded colors depend
on the native shader and surface appearance; curvature coloring is a different
mode from ordinary surface color. The display adapter can show existing solid surfaces and their
contours, but it does not create a well-faired hull or choose every shader.
The Body Plan Control Box is a native navigation interface, not a new hull
surface. It is not currently exposed as a dedicated configuration tool.

RC5 native PNG shows opaque colored hull panels and superstructure in Modeler;
COM screen updating must be refreshed after edits. Camera composition, full contour
visibility and detailed presentation acceptance remain separate from geometry.

## Extension boundary

Claude and Codex host the same server tool surface. The MCPB and Codex ZIP are
not interchangeable installers. The source plugin skeleton is not a standalone
bundled plugin. Client feature restrictions, Bentley licensing and native
application state remain independent prerequisites.

See [manual review coverage](MANUAL_REVIEW.md): a complete textual index is not a complete technical review.
