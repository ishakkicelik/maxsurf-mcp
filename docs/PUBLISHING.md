# Maintainer release and publication checklist

Publish this standalone tree, not its parent development workspace. The source
ZIP is the safest reviewed upload source. Keep `.github`, `.agents` and plugin
metadata; exclude local `runtime`, `build`, `dist`, `.venv` and generated wrappers.
Do not include manuals, samples, designs, credentials, private logs or engineering
training/rule/recipe files. Preserve the MIT license and Copyright (c) 2026 Ismail Hakki Celik.

## Prepare

1. Check requirements, client manifests, server version and schema together.
2. Run all offline tests and `scripts/validate_release.py`. The source validator
   and builder enforce the authorized MIT notice, manifests, metadata and badges.
3. Audit the locked dependencies against current advisory data. Retain all bundled
   third-party notices/license metadata. A clean advisory scan is not certification.
4. Build to a fresh `dist` subdirectory with `scripts/build_release.py`.
5. Extract both final bundles elsewhere and run the real stdio smoke checks.
6. Validate the MCPB manifest and Codex plugin. Check archive CRCs, version/tool
   count, dependency imports, relative paths and source/private-content exclusions.
7. Compare artifact SHA-256 hashes to the generated checksum JSON. Do not rebuild
   after publishing hashes without producing a new version/artifact set.

## First GitHub release

This project has not had a public GitHub release. Present the initial package
with its purpose, requirements, installation routes and verified limitations.
Do not present internal candidate iterations as previously published releases.
Keep feature-change summaries for subsequent public releases.

## GitHub release layout

Use tag/title **0.2.0-rc.5 / Maxsurf MCP 0.2.0-rc.5** and mark **Pre-release**.
Copy `docs/RELEASE_NOTES_0.2.0-rc.5.md` into the release description and convert
relative links to this repository's actual documentation URLs. Do not invent a
repository URL before its owner/name has been selected.
Attach the Claude MCPB, Codex ZIP, source ZIP and checksum JSON. Include
`LICENSE_AUDIT.json` and the dated `ARTIFACT_VERIFICATION.json` report. A Python
wheel may be attached for package installation. These are release assets, not files to
commit as source. No automated Git push, GitHub release upload or catalog listing
is performed by the build scripts.

The requested full-release label is blocked by `docs/RELEASE_STATUS.md`. Close
those gates with evidence before removing the prerelease designation. The owner has authorized MIT for original project code and assets. Keep LICENSE,
package metadata, both client manifests and README license badges aligned.
Third-party dependency notices remain under their own licenses.

## Adding a module or tool

Keep the MCP boundary in a small module with `TOOLS` and `register_tools`.
Use the owned COM STA, explicit module/policy checks, classifications and durable
mutation auditing. Validate complete input batches before any writes. Restrict
files to authorized workspaces, use explicit overwrite/replacement consent and
verify native readback. Never infer success from a void COM method return.

Add fake-COM failure/partial-state tests, schema snapshot changes, tool docs and
native disposable-fixture acceptance. Keep diagnostics/legacy tools opt-in.
Do not turn general COM invocation into a replacement for a reviewed adapter.
Do not embed engineering formulas/rules or vessel design recipes in transport
code; callers supply engineering intent and licensed native solvers execute it.
