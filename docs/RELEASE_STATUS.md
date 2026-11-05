# Release status: 0.2.0-rc.5

Review: 2026-09-08 UTC. **Technical preview, not a fully accepted production release.**
No GitHub upload, catalogue submission or client-settings change is performed.

## Expanded source verification: 2026-10-11

This checkout extends the original preview to 144 default tools and seven opt-in
diagnostic tools. Source/locked-runtime protocol validation and expanded offline
regressions were run locally. Installed 2026 wrapper checks are separately
executable; native Modeler MCP geometry, files, persistence and validated
automation calls were exercised. All four other analysis applications reached
their documented initialization state while their sessions were retained.

This does not replace the historical numerical acceptance below or claim every
solver/GUI workflow was executed. The exhaustive inventory and documented
boundaries are in [Native automation](NATIVE_AUTOMATION.md). No existing public
archive was uploaded or replaced.

## Recorded acceptance scope

The original release build exposed 90 default tools. Native static beam analysis, result export,
.mfd save/open/reanalysis and a two-mode calculation were tested through MCP.
Stability load-case handling and native PNG dimension reporting are also covered
by the recorded checks. See [verification](VERIFICATION.md) and
[Multiframe](MULTIFRAME.md) for the evidence and limits.

A private 49-surface unarmed concept was created through MCP, with separate
hull/structure surfaces and design-grid contours. Native shaded Modeler capture,
Resistance transfer/calculation, Stability transfer/equilibrium/intact GZ and
native PNG export were checked. This is a concept and software smoke test, not a
finished warship, approved loading manual or verified complete structural model.
The preserved Motions sample is a DIFFERENT hull; do not imply one-vessel validation.

## Remaining full-release gates

1. Exhaustive manual/figure/API review. Larger manuals/CHM remain selected review;
   indexing the corpus is not completing semantic or technical review.
2. Clean Windows-user Claude and Codex GUI install/configure/update/remove/reinstall,
   absent-app and unsupported-entitlement tests. Isolated stdio checks are separate.
3. Modeler geometry fairing/topology/curvature acceptance and save/reopen equivalence;
   complete Control Box/shader/camera workflows remain outside the display adapter.
4. Complete Stability project persistence/restore, realistic mass inventory,
   subdivision, downflooding and independently compared damaged cases. .htk is
   rooms only. A point-load intact test is not compartment/damage acceptance.
5. Motions multi-condition/RAO/remote-location comparison and reliable unattended
   binary-result persistence. Native .skr save previously timed out; the owner
   saved manually. That recovery is not unattended acceptance. MOSES/MARTEC and
   imported-RAO execution remain unimplemented.
6. Repeatable SAME-vessel transfer/reanalysis using consistent geometry, coordinate
   datums, draft/trim/mass/loading in every applicable program. Multiframe is not
   automatically derived from hull surfaces, and its beam fixture is not a ship FE model.
7. Modeler VCG/GM convention: input +1 returned KG=3.2 m, while Stability returned
   KG=5.2 m for the same input on this concept. This discrepancy is NOT resolved.
8. Multiframe third-mode access, oriented-support persistence, material assignment,
   broader structural result families and additional build/license acceptance.

## Publishing decision

RC5 may be published under **MIT** as a **Pre-release** with these gates
visible. Do not call it complete suite coverage, a production-ready engineering
system or a finished vessel design. Original code, documentation and assets are MIT licensed, Copyright (c) 2026 Ismail Hakki Celik.
Vendor software/manuals/samples, private designs and training/engineering recipes
are excluded. Do not publish the parent development workspace or private runtime.
