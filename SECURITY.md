# Security and operational scope

The default transport is local stdio. The server has no remote listener,
telemetry uploader, credential collector, or cloud model integration.
Your MCP client can send tool inputs and outputs to its model provider;
review that client's privacy settings before using confidential designs.

The extension runs Python with the user's Windows privileges. Its workspace
checks limit normal file tools; they are not an OS sandbox. Native Maxsurf COM
methods, and especially opt-in raw diagnostics, can affect application state.
Do not enable diagnostics or legacy tools for an untrusted agent.

Default connection policy is activate_existing: it requires an existing process
before COM activation. Maxsurf may itself behave differently across versions;
the server records before/after process evidence and never kills a process to
hide an unexpected launch. It does not guarantee exact window/PID attachment.
Run the client and Maxsurf under the same Windows user and privilege level.

Mutating operations require a durable intent audit record. Native COM mutations
are not transactions: a failure can leave partial changes. Save a separate
checkpoint before editing. Transfers replace destination state only when
confirm_replace=true; Modeler SaveAs also changes its active path.

COM calls are serialized on one STA worker. A timeout does not stop a native
calculation or undo it. Do not blindly retry a timed-out write. Inspect Maxsurf
and reconnect deliberately. Runtime logs and transfer files are local and can
contain design paths and input data; restrict their Windows permissions.

Do not post credentials, private geometry, client names, full logs, or Bentley
manuals in public issues. Report suspected vulnerabilities privately to the
repository owner using their verified profile contact or GitHub private
vulnerability reporting if enabled. No reporting address is invented here.

## Bounded display access

Three default tools use Windows accessibility/PrintWindow for Modeler because
rendering is not exposed by its COM API. They are labelled UI channel, honor the
allowed-module policy, require one visible matching process and restrict controls
to a fixed English toolbar allowlist. Mutations/capture require the exact inspected
window title and refuse modal state. No arbitrary keystrokes, file dialogs,
command IDs, global desktop screenshot or other application is targeted.

A captured app window can contain confidential geometry, titles and paths.
Capture is opt-in, workspace-restricted and non-overwriting; inspect the image
before sharing. OpenGL, remote sessions and minimization can make it incomplete.
UI controls may differ across language/version/layout and are refused if ambiguous.

The RC3 locked dependencies were scanned with pip-audit; Pillow was upgraded to
12.3.0 following upstream security fixes. Re-scan before each publication. Package
pins and release hashes are not publisher signatures or a sandbox. No updater,
license bypass, privileged service or automatic Bentley installer is included.
