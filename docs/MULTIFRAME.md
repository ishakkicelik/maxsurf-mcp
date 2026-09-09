# Multiframe adapter: RC5

Fourteen default tools now address the running Multiframe application. This is
bounded structural automation, not all Multiframe protocols or a hull-to-FE
converter. Linear beam testing passed on Maxsurf 2025 build 25.00.02.339;
modal support is experimental. Other builds/licenses remain unaccepted.

## Start and inspect

1. Start licensed Multiframe under the same Windows user/privileges as the client.
2. Call `connect_maxsurf(module="multiframe")`.
3. Call `get_multiframe_status` and `read_multiframe_model` before making changes.
4. Use `list_multiframe_sections` to find an EXISTING library section. The adapter
   never changes the shared Bentley section library or creates material rules.
5. Read the full schema. Pass the current `unit_token`, expected collection count
   and node index/label pairs to mutations. Reread state after any failed write.

A running window alone does not prove an automation entitlement. The native root
is `Application.Frame`, not `Application.Design`; file identity is `Frame.FullName`,
not its directory-only `Path`. Missing optional objects are reported unavailable,
not as empty collections. The GUI calls nodes joints and elements members;
automation design members are a separate object type.

## Tool workflow

| Stage | Tools | Safety/acceptance boundary |
| --- | --- | --- |
| Inspect | get_multiframe_status, list_multiframe_sections, read_multiframe_model | Units, identity, counts, availability; paged reads |
| Native files | new_multiframe_model, open_multiframe_model, save_multiframe_model | Modified work is refused; explicit replacement consent; new .mfd targets only |
| Structure | add_multiframe_nodes, add_multiframe_elements, add_multiframe_restraints | Whole-batch validation, labels/counts/units, native readbacks |
| Loading | add_multiframe_loadcase | New static case, explicit global nodal forces/moments |
| Solve | run_multiframe_analysis | Linear or experimental distributed-mass modal; other flags off/read back |
| Results | get_multiframe_results, export_multiframe_results | Solved/nonempty/finite outputs; paged JSON; no overwrite |
| View | refresh_multiframe_view | Native screen updating and refresh, not a camera or rendering editor |

No automatic geometric conversion, scantling selection, material assignment,
load derivation, load combinations, plates/patches, releases, nonlinear, buckling,
time-history or design-code checking is exposed by these fourteen tools.
Multiframe's automation manual specifically excludes access to time-history
results; a discoverable enum is not an implemented workflow.

## Units and result interpretation

**Never assume SI.** Numeric values use the current native preferences. The
verified local instance used ft, in, kip and ksi. The same token becomes invalid
if any reported unit changes. Node coordinates and nodal displacements can use
different length units. A token verifies the unit snapshot, not the model revision.

| Field | Native unit category |
| --- | --- |
| Node x/y/z, beam length | Length |
| Node dx/dy/dz | Displacement |
| Node thetax/thetay/thetaz | AngularDisplacement |
| Global translational nodal loads, rx/ry/rz, beam Px/Vy/Vz | Force |
| Global rotational nodal loads, Mx/My/Mz, beam Tx/My/Mz | Moment |
| Section Area | Area |
| Section Ix/Iy | Inertia |
| Section E/G | Stress (NOT the category named Modulus) |
| Section Mass | MassPerLength |
| Category named Modulus | Section modulus, not elastic modulus |

Native beam end actions use member-local axes and the reported tension preference.
Nodal axes follow the native model conventions. Modal displacements are scaled
eigenvectors, not physical vibration amplitudes. Their native scaling is returned;
frequency/period are labelled native until that unit contract is independently
accepted. Static reactions/end actions are not requested for modal shapes.

## Verified native compatibility and unresolved cases

- Installed ITypeInfo declares VT_VOID for New/Open/SaveAs, SetSection and Analyse,
  although legacy CHM describes Boolean returns. None is accepted only alongside
  operation-specific native postconditions. Explicit False still fails.
- Beam endpoints are read through `element.nodes.Item(1/2)`. Results identify nodes
  and elements via NodeIndex/ElementIndex; end actions use indexed methods.
- Modal mass settings are mutually exclusive selectors. Set DistributedMass=True
  only; writing LumpedMass=False actually selected lumped mass in the native test.
- A disposable axial beam's linear displacement/reaction matched the analytical
  benchmark. Native .mfd save/open and reanalysis succeeded. However, the restraint
  Global flag changed on reopen. The fully fixed support was insensitive to that
  change; oriented/partial supports have NOT passed an equivalent round trip.
- Two-mode analysis and both shape reads passed. Requesting three modes produced
  a native server exception accessing the third result case even though counts
  reported three. No failed mode is silently fabricated or accepted. Treat modal
  execution as experimental; two-mode success does not resolve the third-mode bug.
- Section E/G were readable, while the separate material object was unavailable
  for the tested section. The payload reports this explicitly; no material
  assignment or independent material acceptance is implied.
- Save/export verification is not a complete restoration guarantee. Timeout does
  not cancel native analysis. Never repeatedly retry a timed-out mutation.

Raw native evidence and fixture files remain private, outside source and bundles.
The shared section-library SHA-256 was unchanged before/after these tests.
