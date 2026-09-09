# Capability and acceptance matrix

Version 0.2.0-rc.5 exposes **90 default tools**. Tool count is not a percentage of
Maxsurf coverage. Hundreds of native API members and GUI workflows remain
outside these bounded adapters. No claim of "every use of every application"
is made. Complete schemas are in `tests/data/tool_schemas.json`.

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
| Multiframe | 14 tools: native units/files, nodes/beams/supports/nodal loading, linear and experimental modal analysis, paged JSON export | Static benchmark/save/open/reanalysis and two-mode test; third-mode access, oriented-support persistence and broader FE workflows unresolved; see [scope](MULTIFRAME.md) |
| Other suite programs | Installation/help catalogued for Shape Editor, VPP and Rhino integration | No default adapters for these products; scanning manuals does not add tool coverage |

## What the bridge does not supply

- Ship recipes, class-rule design decisions, automatic compliant tank/compartment
  assignment, optimized hull generation or LLM training datasets.
- Every native file import/export, independent curve fitting/editing, frames/
  decks/stringers/plates fabrication workflows or arbitrary UI automation.
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
