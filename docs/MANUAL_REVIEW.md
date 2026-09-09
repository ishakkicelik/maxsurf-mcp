# Installed documentation review and coverage

Review: 2026-09-08 UTC. Scope is the 23 files supplied by the owner for Maxsurf
2025. The Bentley installation is read-only. This file is an audit, not a ship
recipe, extracted textbook, rules library or training dataset.

## Important distinction

**The requested exhaustive technical/figure/API review is NOT complete.**
All attached PDFs were text-indexed and all five CHM containers were extracted
and indexed privately. Indexing is not reading, and a registered COM member is
not an implemented or accepted MCP tool. The table separates full text review
from selected review. No claim of having learned every technical detail is made.
The larger discovery also found extra PDFs; these are not silently counted as
reviewed attached documents. No extracted content is distributed in this package.

## File-by-file coverage

Page numbers below are physical PDF pages, not necessarily printed folios.
Full text review does not assert that every screenshot or equation was visually
verified. Selected automated text-search results are not full chapter reviews.

| Attached file | Review performed | Consequence / remaining work |
| --- | --- | --- |
| BentleyCONNECTEditionOverview.pdf | All 22 pages of extracted text | CONNECT project/cloud context; historical optional-sign-in FAQ must not override current SES requirements |
| Readme.pdf | All 29 pages of extracted text | 25.00.02 fixes, removed RADIF solver, native Stability SaveImage and Motions remote-RAO frequency correction identified |
| InstallationGuide.pdf | All 48 pages of extracted text | Current and legacy instructions distinguished; side-by-side versions, public libraries, UI settings and licensing; screenshots not all visually checked |
| eula.pdf | All 13 pages of extracted text | Interoperability does not authorize feature unlocking, redistribution, shared hosting or academic production use; deployment-specific interpretation requires appropriate advice |
| Legal_Notice_Maxsurf.pdf | Both pages of extracted text | Bentley proprietary/trademark/third-party boundaries; no vendor content included |
| ValidationSecurityStatement.pdf | Both pages of extracted text | Statement names MOSES 2024; not certification of this MCP or its 2025 workflows |
| RhinoPlugInManual.pdf | Both pages of extracted text | Assembly plug-in workflow and legacy example paths; no Rhino MCP adapter |
| VPPManual.pdf | All 35 pages of extracted text; rig-parameter diagram on physical page 30 visually inspected | Separate .spd measurements, hull/rig state, VCG below DWL, longest-surface assumptions, closed appendages, no Undo; no VPP MCP adapter |
| ModelerManual.pdf | Text indexed; selected grid, contours, display, Control Box and geometry sections reviewed | Full 427-page technical/figure review remains; structural/detail/curve workflows not fully exposed |
| ModelerAutomation.pdf | Text indexed; selected automation/object-model sections reviewed | Full 77-page review and member-by-member adapter coverage remain |
| ModelerAutomation.chm | 812 HTML topics indexed; selected surface/grid/transform contracts reviewed | Rotation is degrees, unlike Motions headings; not 812 implemented tools |
| MotionsManual.pdf | Text indexed; selected setup, mapping, spectra, results and persistence pages reviewed (including 46, 50, 52, 68-70, 106-107) | Full 259-page review remains; modern solver setup and independent RAO comparison incomplete |
| MotionsAutomation.chm | 566 HTML topics indexed; lifecycle, heading, tested-condition and selected statistics contracts reviewed | Radians bug fixed; NumberTested/TestedItem used; .skd save accepted, .skr save disabled after timeout |
| ResistanceManual.pdf | Text indexed; selected measurement/method/applicability sections reviewed | Full 66-page technical review remains; native calculation may continue outside empirical validity ranges |
| ResistanceAutomation.chm | 251 HTML topics indexed; selected transfer/configuration/result contracts reviewed | Current adapter is bounded, not all resistance/propulsion/freesurface workflows |
| StabilityManual.pdf | Text indexed; selected subdivision/load/damage/criteria/persistence sections reviewed | Full 514-page review remains; .htk alone is not a complete restorable project |
| StabilityAutomation.chm | 1573 HTML topics indexed; selected room/load/criteria/image/angle contracts reviewed | Native heel angles are degrees; SaveImage loaded-model PNG accepted in RC5; actual/requested dimensions reported |
| CriteriaHelp.html | Parsed as inert text; contents, generic parameter/result API and selected criterion definitions reviewed | Full 216099-character semantic review remains; rules are external, not synthesized by MCP |
| MultiframeManual.pdf | Text indexed; selected scope/reference material inspected | Full 350-page review remains; RC5 adds a bounded beam/static/experimental modal adapter |
| MultiframeAutomation.pdf | Text indexed; selected units, lifecycle, structure, analysis and results contracts reviewed | Full 140-page review remains; native model distinguishes nodes/elements/design members; time-history/design/UI limitations documented |
| MultiframeAutomation.chm | 2352 HTML topics indexed; selected native units, nodes/elements/restraints/loading, files and result members reviewed | Full semantic/member review remains; 14 RC5 tools are not full coverage |
| ShapeEditorManual.pdf | Text indexed; pages 15-20 reviewed for shape/section/library semantics | Full 113-page review remains; partial overlap, approximate properties and shared-library state need explicit handling in any future adapter |
| BentleyStandardProductCatalog.cat | Read-only size/hash and Authenticode inspection; Windows reported Valid Bentley signature | Binary security catalog, not API/manual/extension manifest; catalog-member verification is not claimed |

## Confirmed findings affecting implementation

1. Motions `Headings.Add` and `Heading.Heading` use radians. RC3 sent degree values
   directly; RC4 converts at the boundary and returns both degrees and radians.
   Existing Motions cases must be checked/recalculated, not relabeled as correct.
2. Motions has tested-condition collections separate from all configured rows.
   RC4 uses `NumberTested` / `TestedItem` for result axes.
3. `.skd` stores Motions settings/input data; `.skr` additionally stores results;
   the hull remains separate. Native `.skr` saving timed out and is refused by RC4.
4. Native Summary input rows contain numbers even without solved responses. RC4
   requires finite heave, roll and pitch response rows, accepting genuine zeros.
5. Native spectrum setters may round periods. Strict defaults remain; callers
   can explicitly supply spectrum-only tolerance and inspect requested/read values.
6. Maxsurf 2025 removed the old RADIF executable. Its modern MOSES replacement
   is not covered by the old panel runner. Do not reintroduce a removed solver.
7. Stability `Design.SaveImage` is documented and present in the installed type
   library. RC5 verified a loaded-model PNG and found that native dimensions can
   differ from those requested; actual dimensions are reported, not fabricated.
8. Shape Editor and VPP have independent data/coordinate/library assumptions.
   Reusing Modeler defaults blindly would be incorrect; they need real adapters.
9. Rendered room surfaces do not prove correct section-based room calculations.
   Native criteria pass/fail must not be reconstructed from a generic margin sign.

## Completion criteria for the remaining review

Read each remaining chapter/topic with page/topic-level coverage; visually inspect
relevant equations and diagrams; map every proposed feature to its native API or
explicit UI-only limitation; add tests for units, indices, defaults, persistence,
errors and unsupported licences. Test it through the MCP protocol against a
preserved/disposable native case. Keep engineering policy and proprietary content
outside the distribution. Do not label discovery metadata as full-suite support.

## RC5 focused Multiframe review

Reviewed selected MultiframeAutomation PDF pages 11-26, 51, 58 and 86-97, plus
related CHM topics and installed ITypeInfo. Earlier page 9-10 extraction was
partial. Review covered units, lifecycle, nodes/elements/restraints, static nodal
loads, sections/material separation, modal settings, result case/row hierarchy,
end-action indexing and automation limitations. Installed interface evidence
overrides unsupported legacy getter assumptions. No claim of all 140 PDF pages
or all 2352 CHM topics being fully reviewed is made.
