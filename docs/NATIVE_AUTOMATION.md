# Native API coverage and advanced automation

This expanded source checkout keeps the release identifier `0.2.0-rc.5`, but
exposes **144 default tools** rather than the original 90. Enabling diagnostics
adds seven tools: the original `com_get`, `com_set`, `com_invoke`, and the new
`native_call`, `native_enumerate`, `create_native_object`, `release_native_handles`. Published older
archives do not automatically acquire these source changes; build the local
archive or install this checkout.

## Discovery and coverage

`discover_native_api` reads registered type libraries without activating an
application. Its pages include libraries, types, GET/PUT/PUTREF/method members,
enums, aliases, arrays, defaults, parameter directions, coclass registrations
and object-graph evidence. `get_native_api_member` retrieves one exact contract.
Use the returned inventory digest for subsequent pages or calls. A last page is
not a complete inventory if metadata errors remain.

The installed 2026 audit found seven application libraries: Modeler, Stability,
Resistance, Motions, Multiframe, VPP and Shape Editor. The complete inventory
contains 805 type entries including referenced external types, 848 variables,
7,142 member descriptors and 688 implementation references. These counts include
shared references and COM plumbing, not 7,142 distinct engineering operations.

Generate a fresh machine-readable reconciliation against the installed version:

```powershell
.\.venv\Scripts\python.exe .\scripts\check_native_coverage.py `
  --output .\runtime\native-coverage.json
```

The output must be a new file. Every unique member receives a route or an
explicit boundary; missing investigation and unknown keys remain visible.
`metadata_admitted` means installed metadata, marshaling and effect-policy checks
admit a candidate route. It does not mean a target was constructed or invoked.
Each row records that runtime verification is required and that live signature
verification, operational validation and engineering acceptance have not been
performed by this inventory command. Incomplete descriptor slots retain their
diagnostics, produce an incomplete artifact and make the command exit 1.
Actual `native_call` invocations verify the live signature before dispatch.

## Validated diagnostic calls

Configure `MAXSURF_MCP_ENABLE_DIAGNOSTICS=1` in the MCP client's server environment
and restart that server. Every new diagnostic invocation requires
`acknowledge_native_effects=true`. Routine work should use the semantic tools.

For example, `native_call` can read the application's version:

```json
{
  "module": "modeler",
  "interface": "IApplication",
  "member": "Version",
  "invocation_kind": "property_get",
  "acknowledge_native_effects": true
}
```

Read `IApplication.Design`, then pass its returned `native_handle` as
`target_handle` when reading `IDesign.Surfaces`. Native collections commonly
expose `Item` as a **method**, not a property: use the inventory's invocation
kind and exact argument names, or positional JSON such as `[1]`.

`native_call` verifies installed metadata against the live interface before
`InvokeTypes`. It validates finite native scalars, integer ranges, enums,
rectangular SAFEARRAYs, aliases, optional/default inputs, out-only parameters
and session-owned object references. Missing or extra arguments, Python-only
attributes, wrong interfaces, unsupported shapes and stale references fail
before dispatch. Return/out tuples follow pywin32's native convention.

Methods and property puts invalidate older handles, even if native execution
fails. Reacquire references after them. Handles belong to one module/session/
design identity, have a bounded cache, and are released on reset/shutdown on
the owning STA. `release_native_handles` permits explicit cleanup.

### Native collection enumeration

For collections with `_NewEnum`, call `native_call` with that exact member and
`invocation_kind="property_get"` on the collection handle. Only the installed
DISPID_NEWENUM (-4), no-argument, IUnknown return contract is admitted; arbitrary
underscore names remain refused. Its result contains `native_enum_handle`.

Pass that token to `native_enumerate` as `enum_handle`, with `operation="next"`,
`count=8`, `module="modeler"` and `acknowledge_native_effects=true`.
`next` and `skip` accept integer counts from 1 to 256. `reset` and `clone`
require the default count 1. Clones have independent cursors. A short `next`
batch reports `exhausted=true`; a full batch does not prove another item exists.
COM items have ordinary `native_handle` tokens for subsequent calls.
The MCP builds batches with repeated `Next(1)` calls: the inspected Maxsurf 2026
enumerator loses items and fills remaining slots with null when asked for several
items in one native `Next(n)` call. Batch size remains bounded by 256.
Enumerator tokens share session ownership, mutation expiry, bounded retention
and explicit release with object tokens, but the token kinds are not
interchangeable. Operations use pywin32's actual IEnumVARIANT interface.
The cache retains an active enumerator while its next page is encoded, with
capacity for one maximum object page plus that enumerator. Older item handles
may expire as later pages are read; consume a page before requesting the next.

`create_native_object` constructs only an installed, registered native coclass
with its verified default interface. Its application must pass the configured
connection policy. Application-root requests reuse the managed instance.
Independent helper objects may require native initialization or binding:
constructing a `Surface` proxy does **not** insert a surface into the design.
Use collection creation tools for new geometry.

## File and effect boundaries

Known filename operations retain workspace, suffix and overwrite checks.
The default native route refuses implicit saves, unreviewed text/VARIANT
effects and UI command execution.

For advanced diagnosis, `allow_unmapped_effects=true` explicitly permits those
unmapped operations while retaining ABI, argument, object-ownership and
stale-handle validation. Its response reports
`file_policy.mode="unmapped_diagnostic"` and `sandbox_validated=false`.
Known filename mappings cannot be bypassed by this flag. This mode shares the
unrestricted-effect purpose of the original raw diagnostics; it is not a
routine sandboxed workflow.

Motions binary `.skr` saving and generic solver shortcuts remain refused by the
validated extension. Use the existing semantic analysis/data tools for accepted
sequences. Original raw diagnostics remain available, but do not certify a
workflow as reliable or safe.

Protected Stability keypoint/tank/compartment deletion and dependent project-state
replacement also remain refused, including index, name, bulk and import aliases
in unmapped-effect mode: the available backup does not restore a complete
project. Use supported additive and semantic companion-file tools instead.

## Explicit limitations

- No fabricated API substitutes exist for mesh node/triangle mutation,
  Boolean difference, GUI-only fairing, or undocumented Shape Editor section
  construction. Mesh tools expose the members actually present.
- Native COM transport plumbing is not an engineering tool. Collections support
  their installed `Count`/`Item` contracts and, where present, bounded
  `_NewEnum`/IEnumVARIANT traversal.
- Some Multiframe load-panel/plate-load/patch-load interfaces have no declared
  constructor or static return route in the inspected library. A runtime
  VARIANT may reveal another interface; discovery does not prove that such an
  object can be obtained in every project.
- VPP's native root exposes UI/accessibility, not a proven vessel/rig solver
  `Design`. Shape Editor exposes XMLGrid, not a full engineering Application.
- Unregistered/vtable-only objects, unresolved signatures and native values
  outside supported Automation/JSON representations produce explicit errors.
- Unsaved designs lack a reliable path identity. External edits that preserve
  the same design path are not a complete revision-tracking mechanism.
- Native initialization is `IsInitializedCorrectly`. A successful connection
  can precede initialization; inspect readiness before analysis. A timeout
  abandons the wait, not the native calculation.
- Numeric/format fidelity, regulatory acceptance, solver coverage and all
  combinations of speed/heading/spectrum still need independent native evidence.

## Verification

Offline tests run without a Bentley installation:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -q
.\.venv\Scripts\python.exe .\scripts\validate_release.py
.\.venv\Scripts\python.exe .\scripts\smoke_stdio.py `
  --python .\.venv\Scripts\python.exe --entry .\server.py
```

Installed 2026 wrapper checks are a separate explicit integration command,
not skipped tests hidden inside the offline suite:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.native_wrapper_contract -v
```

`scripts/smoke_native_modeler.py` exercises real MCP geometry, meshes, exports,
import and save/reopen on a copy of an already open, saved disposable `.msd`
fixture. Supply `--disposable-fixture` and
`--confirm-saved-disposable-fixture`; populated saved test ships are supported.
The confirmation attests that the saved file contains all current changes,
because no reliable dirty-state getter is established. An unrelated or unsaved
document is refused before edits. The original fixture is never the mutation
target. Success and failure paths reopen it and compare identity, full surface
control nets, mesh summaries/statistics, markers, grids and reference frame.
Evidence includes restoration or explicit manual-recovery status; the user's
application is left open. See [exact invocation](VERIFICATION.md).

On this machine, Modeler 26.00.00.433 passed native MCP control-point edits,
surface/mesh translation, meshing/statistics, IGES/3DM/OBJ export, OBJ import,
native save/reopen summary equality, metadata-backed getters/collection methods,
out arguments and optional defaults. Registered helper construction and managed
root reuse were observed; an unbound Surface correctly reported its missing
index rather than pretending to be inserted geometry. Other analysis applications
were connected and reached documented initialization; that is not proof of
their numerical solvers.

Official entry points:
[Bentley MAXSURF manuals](https://bentleysystems.service-now.com/community?id=kb_article&sysparm_article=KB0027613)
and [COM troubleshooting](https://bentleysystems.service-now.com/community?id=kb_article_view&sysparm_article=KB0054308).
Installed type-library and manual versions remain the authority for a call.
