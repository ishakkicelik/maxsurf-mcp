# Verification evidence: 0.2.0-rc.5

Native evidence recorded 2026-09-08 UTC; software and MIT publication checks updated
2026-09-09 UTC. Software tests are not engineering certification.

## Software checks

- 654 offline tests passed, including the previous 648 tests and six release
  licensing regressions. The added tests reject conflicting license grants,
  client manifests, package metadata and README badges before packaging.
- 90 default tools; exact argument schemas and explicit module ownership checked.
- Consult the final `ARTIFACT_VERIFICATION.json` for actual archive CRCs, hashes,
  source consistency, dependency/license inventory, relocated imports and stdio.
- Locked runtime: 35 distributions. The dated advisory scan is time-dependent,
  not protection against unknown vulnerabilities; wheel hashes are not pinned.
- SDK forward-reference and deprecated MakeIID warnings remain nonfatal. Tests
  and protocol checks must still pass; warnings must not be mistaken for solver errors.
- No proprietary manuals/samples, native models, private runtime or LLM training included.

## Direct native MCP checks

The release MCP stdio server performed these operations. Native files and raw
protocol evidence remain private. Maxsurf 2025 build 25.00.02.339 was used.

| Module/check | Observed result | Limit |
| --- | --- | --- |
| All five connections | Modeler, Stability, Resistance, Motions, Multiframe attached | One local build/user/license; not a clean-client GUI install |
| Multiframe linear beam | End displacement 0.00834041765 in; reaction -1 kip, independently matched axial benchmark | One disposable beam, not vessel FE acceptance |
| Multiframe persistence | New .mfd, verified path/hash, open and static reanalysis passed | Native restraint Global flag changed on reopen; oriented/partial support equivalence unresolved |
| Multiframe modal | Two-mode run and both shape reads passed; native frequencies 0.704788755 and 3.523943775 | A three-mode request produced third-case native exceptions; modal remains experimental |
| Multiframe library | Shared library digest unchanged before/after | No library or material editing attempted |
| Modeler concept | 49 separate surfaces; 43 stations, 20 waterlines, 7 buttocks; shaded native PNG verified | Unarmed concept, no fairness/topology/class approval; full render/Control Box/camera coverage absent |
| Modeler hydrostatics | Native displacement about 2703.324 t, draft 4.2 m | Validation remains suspect; VCG/GM discrepancy versus Stability unresolved |
| Resistance concept | Same new geometry transferred; fresh Holtrop run, 81 rows at 8-16 kn | Explicit smoke-test physical inputs; empirical applicability and full powering not accepted |
| Stability concept | Transfer, assumed point-load equilibrium, seven intact GZ rows 0-30 deg | No tank, damage, downflooding or complete loading-manual acceptance |
| Stability image | Decoded native PNG 1148 x 734, requested 1200 x 800 | Actual size reported, no resampling; wireframe native view, not shaded-render acceptance |
| Motions preserved sample | Fresh strip-theory geometry/seakeeping rerun and numeric Summary/readback passed | Different hull from new concept; only one condition, no full RAO/multicondition validation |

The Resistance test returned 56.761 kN at 8 kn and 242.461 kN at 16 kn.
The 16-kn effective power was 1995.721 kW; the native reported power at the explicit
60% efficiency was 3326.201 kW. These are outputs under test assumptions, not
an installed-power specification or optimized-performance claim.

Stability's test mass was 2703323.7805 kg with explicit CG coordinates. Equilibrium
converged with draft 4.2 m and KG about 5.2 m. Modeler reported KG 3.2 m for the same
+1 m VCG input. This establishes a discrepancy, NOT a resolved sign convention.
Do not transfer GM/VCG claims between modules without resolving the datum contract.

## Failures found, fixed or still open

- Fresh Stability has eight undefined slots. Reading their Name caused "Loadcase
  is not open". The wrapper now checks IsDefined; a defined but unreadable case
  still fails closed. Successful native equilibrium followed the fix.
- Native SaveImage can ignore requested dimensions and use the view client area.
  PNG format/decode/bounds remain checked; actual/requested sizes are returned.
- Multiframe legacy help and installed return types differ. VT_VOID completion
  requires native postconditions, not a truthy return. Endpoint and result getter
  mappings were corrected against the installed interfaces.
- Modal mass selectors are exclusive; only the selected True property is set.
  Third-case access still fails natively and is never reported as success.
- Motions .skr saving previously timed out and required the owner to save manually.
  RC5 retains the guard blocking unattended .skr saves. No blind retry or file
  replacement was performed. The current sample rerun does not prove file restore.

## Reproduce software checks

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -q
.\.venv\Scripts\python.exe scripts/validate_release.py
.\.venv\Scripts\python.exe scripts/smoke_stdio.py --python ".\.venv\Scripts\python.exe" --entry ".\server.py"
```

Build only after source is finalized. Verify every archive, source match,
dependency/license inventory, manifest and relocated stdio launch. Real client
GUI lifecycle and the [full-release gates](RELEASE_STATUS.md) remain separate.

## MIT publication checks

The original project is MIT licensed, Copyright (c) 2026 Ismail Hakki Celik.
Both client manifests, Python package metadata and README badges use MIT.
The release builder validates the authorized notice before creating any archive
and writes `LICENSE_AUDIT.json` alongside the artifacts. The Codex plugin carries
its own copy of the canonical license so it remains available after installation.
Separately licensed dependency notices remain intact.

The 2026-09-09 licensing changes affect documentation, packaging and regression
checks. They do not change the runtime tools or add native solver acceptance.
The dated `ARTIFACT_VERIFICATION.json` shipped with the release assets records
the final archive/license/hash and extracted-bundle protocol checks.
