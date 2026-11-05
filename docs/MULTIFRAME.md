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
load derivation, load combinations, plate/patch creation, nonlinear, buckling,
time-history or design-code checking is exposed by the original fourteen tools.
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

## Explicit controls module (offline verified, live acceptance pending)

`maxsurf_mcp.multiframe_controls` exposes `TOOLS` and `register_tools(mcp)` for
integration. Its nine tools reuse the guarded STA/session boundary:

| Tool | Contract |
| --- | --- |
| get_multiframe_element_controls | Read orientation, both release/offset arrays, offset axes and static/dynamic self-weight flags |
| set_multiframe_element_controls | Validate all supplied fields before mutation; retain omitted fields; explicit offset axes required |
| add_multiframe_element_loads | Append point/uniform/linear/triangular/tri-linear loads to an existing static case with explicit DOF/axes and expected load count |
| set_multiframe_modal_controls | Explicit modes, solver, scaling, selected mass formulation, iteration limit and tolerance |
| set_multiframe_units | Native unit-set/category changes; stored readback and fresh token |
| read_multiframe_auxiliary_objects | Paged native identity for plates/patches/spring elements/load panels; spring, mass and link definitions |
| add_multiframe_node_masses | Six nonnegative Mass/RotationalMass values and node identity; expected count |
| add_multiframe_springs | Explicit node, DOF, positive stiffness, axes and native spring type |
| add_multiframe_node_link | Distinct node indices/labels including master; six Boolean linked DOFs; unique name and expected count |

`run_multiframe_analysis` now reports `requested_cases`, `missing_cases`,
`coverage_complete` and `coverage_status`. Linear coverage includes every frame
case: native `mfLoadCaseType` contains static, combined and self-weight cases.
Modal coverage compares solved/accessibly enumerated cases against the requested
mode indices, not merely one solved case. Requested three/returned two is
`partial`; no solved cases remains an analysis error. Inaccessible native cases
raise typed COM errors. This does not fix the previously observed third-mode
native exception. The runner accepts explicit `mass_formulation` (`distributed`
or `lumped`); only the selected toggle is set to True. Pass the same modes and
formulation used in the modal controls tool; the runner explicitly sets these
two fields.

Native evidence independently inspected on 2026-10-11:

- `Multiframe.exe` embedded library 1.3 was loaded read-only with
  `pythoncom.LoadTypeLib`; no application activation or mutation occurred.
  `IElement.Orientation` is R8 RW; releases/offsets are Variant RW;
  offset axes and self-weight fields are Boolean RW.
- Installed `MultiframeAutomation.pdf` pages 46-48 specifies current Angle units
  for orientation, three current Length values for offsets and six Boolean
  release slots. Its prose says only slots 3, 4 and 6 are significant but its
  adjacent pinned-end example sets slots 4 and 5. The adapter deliberately passes
  the six native slots without inventing a corrected release interpretation.
- `IElementLoads.AddLoad(el:Variant, DOF:mfDOF, Shape:mfLoadShape, F1:R8,
  Distance1:R8, F2:R8, Distance2:R8, bGlobal:BOOL)` returns `IElementLoad`.
  Readback uses `Element`, `DOF`, `Shape`, `Value1`, `Value2`, `Dist1`, `Dist2`,
  and `Global`. Pages 74-78 specify Distance1 from the first end and Distance2
  from the second end. They describe magnitudes as current force/moment units,
  even for distributed loads. No implicit conversion from Udl is performed;
  native magnitude interpretation remains a live acceptance item.
- Native shapes are `mfLoadshapePoint=1`, `mfLoadshapeUniform=2`,
  `mfLoadshapeLinear=3`, `mfLoadshapeTriangular=4`, `mfLoadshapeTriLinear=5`.
  All eight arguments are explicit. Shape-ignored arguments must be zero, so
  native ignored values cannot produce misleading stored-value claims.
- Solvers are `mfModalSolverSSI=1`, `mfModalSolverJacobi=2`,
  `mfModalSolverInverseIteration=3`; scalings are
  `mfModalScalingUnitDisplacement=1`, **`mmfModalScalingUnitMass=2`** (native
  spelling). Modes and MaxIterations are VT_INT; Tolerance is R8.
- `IPreferences.UnitSet` is `mfUnitSets` RW; `SetUnit(unitType:mfUnitType,
  unitname:BSTR)` returns Void; `GetUnit` returns BSTR. All 22 numeric categories
  now participate in the token, including Spring, TorsionSpring, RotationalMass,
  Density, Temperature, TemperatureCoeff, Velocity, Acceleration and Warping.
  A native unit-set change may alter multiple categories. A rejected string can
  leave earlier requested changes in place; reread status after failure.
- `INodeMasses.AddMass(Node:Variant, Mass:Variant)` returns `INodeMass`;
  pages 57-59 documents six values and `Masses` readback.
  `INodeLinkGroups.AddGroup(Label:BSTR, nodes:Variant, master:Variant,
  LinkedDOF:Variant)` returns `INodeLinkGroup`; pages 55-56 documents index arrays
  and six Boolean linked DOFs. Tuples are used as Python Variant-array inputs,
  and returned list/tuple values are compared slot by slot. Actual SAFEARRAY
  marshalling on the installed application still requires live acceptance.
- `ISprings.AddSpring(Node:Variant, DOF:mfDOF, stiffness:R8)` returns `ISpring`.
  Native types are `mfSpringStandard=1`, `mfSpringTensionOnly=2`,
  `mfSpringCompressionOnly=3`. Translational stiffness uses Spring units;
  rotational stiffness uses TorsionSpring units.
- `IPlates`, `IPatches`, `ISpringElements`, `ILoadPanels` expose only Item,
  Count and enumeration. Their individual objects expose application/frame,
  Index/Number/ID/Label, but no physical geometry, material, stiffness, pressure,
  connectivity or creation members. The read tool exposes existing identities;
  no unsupported creation or physical-property signatures are fabricated.

Focused fake-boundary tests establish validation, native argument order, unit
invalidation, mutually exclusive selectors, stored-value verification and honest
coverage reporting. They do not establish native numerical results, array lower
bounds/marshalling, release-slot interpretation, distributed-load unit semantics
or a save/reopen round trip for these new controls.
