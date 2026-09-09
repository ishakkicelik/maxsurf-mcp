"""Environment-driven server policy.

Every switch here fails closed: the safe value is the default, and an unsafe
tier has to be turned on explicitly by whoever runs the server. Values are
read on each call rather than cached at import so tests and hosts can change
policy without reimporting the package.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Enables the raw COM diagnostics tier (com_get / com_set / com_invoke).
ENABLE_DIAGNOSTICS_VAR = "MAXSURF_MCP_ENABLE_DIAGNOSTICS"

#: Enables quarantined tools whose COM members are not proven to exist.
ENABLE_LEGACY_VAR = "MAXSURF_MCP_ENABLE_LEGACY"

#: os.pathsep-separated list of directories that file operations may touch.
WORKSPACE_ROOTS_VAR = "MAXSURF_MCP_WORKSPACE_ROOTS"

#: Directory that receives append-only audit JSONL files.
AUDIT_DIR_VAR = "MAXSURF_MCP_AUDIT_DIR"

#: Set to "0" to disable audit writes. Auditing is on by default.
AUDIT_ENABLED_VAR = "MAXSURF_MCP_AUDIT"

#: How a session may obtain its application object. See ATTACH_POLICIES.
ATTACH_POLICY_VAR = "MAXSURF_MCP_ATTACH_POLICY"

#: Comma-separated list of module names that may be connected to at all.
ALLOWED_MODULES_VAR = "MAXSURF_MCP_ALLOWED_MODULES"

#: Seconds a normal COM operation may occupy the worker before the wait is
#: abandoned.
COM_TIMEOUT_VAR = "MAXSURF_MCP_COM_TIMEOUT"

#: Seconds an analysis may run. Separate from COM_TIMEOUT because a solver
#: legitimately takes far longer than a property read.
ANALYSIS_TIMEOUT_VAR = "MAXSURF_MCP_ANALYSIS_TIMEOUT"

#: Seconds to wait for the COM worker thread to drain during shutdown.
SHUTDOWN_TIMEOUT_VAR = "MAXSURF_MCP_COM_SHUTDOWN_TIMEOUT"

#: Seconds a liveness result stays trusted before the session is re-probed.
LIVENESS_INTERVAL_VAR = "MAXSURF_MCP_LIVENESS_INTERVAL"

#: Seconds to wait for the interprocess audit lock before refusing to append.
AUDIT_LOCK_TIMEOUT_VAR = "MAXSURF_MCP_AUDIT_LOCK_TIMEOUT"

#: Running Object Table only. Never performs COM activation, so it can never
#: start a process. Maxsurf does not publish its Application object in the ROT,
#: so this policy cannot reach Maxsurf; it remains as the strictest option and
#: as a diagnostic for what the ROT actually contains.
ATTACH_ONLY = "attach_only"

#: ROT attachment first, then COM activation, but *only* after the operating
#: system confirms the module's executable is already running. If it is not,
#: the operation is refused before any activation call is made. This is the
#: policy that reaches a running Maxsurf.
ACTIVATE_EXISTING = "activate_existing"

#: Attach if possible, otherwise start the application. Opt-in only, and the
#: only policy under which a Maxsurf process may come into existence.
LAUNCH_IF_ABSENT = "launch_if_absent"

#: As ATTACH_ONLY, and additionally refuse unless the host process id can be
#: proven for the object that was attached.
REQUIRE_PID = "require_pid"

ATTACH_POLICIES = (ATTACH_ONLY, ACTIVATE_EXISTING, LAUNCH_IF_ABSENT,
                   REQUIRE_PID)

#: Policies permitted to call into COM activation at all.
ACTIVATING_POLICIES = (ACTIVATE_EXISTING, LAUNCH_IF_ABSENT)

#: The default. Live-proven against Modeler and Stability 25.00.02.339: process
#: gate satisfied, EnsureDispatch connected, no new process appeared. Chosen over
#: ATTACH_ONLY because ATTACH_ONLY cannot reach Maxsurf at all, and over
#: LAUNCH_IF_ABSENT because this one cannot start an application.
DEFAULT_ATTACH_POLICY = ACTIVATE_EXISTING

#: Modules with proven COM support. Resistance includes a live-proven licensed
#: transfer, measurement, method-selection, calculation and result boundary.
#: Motions is the seakeeping boundary: BentleyMotions.Application, its XMLGrid
#: helper, and the analysis workflow discovered against the live application.
DEFAULT_ALLOWED_MODULES = (
    "modeler", "stability", "stability_xmlgrid", "resistance",
    "motions", "motions_xmlgrid", "multiframe",
)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().casefold()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return default


def package_root() -> Path:
    """Return the repository root that owns this package."""
    return Path(__file__).resolve().parent.parent


def runtime_root() -> Path:
    """Return the runtime directory. Kept out of version control."""
    configured = os.environ.get("MAXSURF_MCP_RUNTIME_DIR", "").strip()
    return Path(configured).expanduser().resolve() if configured else package_root() / "runtime"


def diagnostics_enabled() -> bool:
    """True when the raw COM diagnostics tier may be registered and called."""
    return _flag(ENABLE_DIAGNOSTICS_VAR, default=False)


def legacy_enabled() -> bool:
    """True when quarantined, unproven tools may be registered and called."""
    return _flag(ENABLE_LEGACY_VAR, default=False)


def audit_enabled() -> bool:
    """True when audit records are written to disk."""
    return _flag(AUDIT_ENABLED_VAR, default=True)


def audit_dir() -> Path:
    """Return the directory that holds append-only audit logs."""
    raw = os.environ.get(AUDIT_DIR_VAR)
    if raw and raw.strip():
        return Path(raw.strip()).expanduser().resolve()
    return runtime_root() / "audit"


def workspace_roots() -> tuple[Path, ...]:
    """Return every directory that file-capable tools are allowed to touch.

    Defaults to ``runtime/`` inside the repository so a fresh install cannot
    read or overwrite an arbitrary design on the host.
    """
    raw = os.environ.get(WORKSPACE_ROOTS_VAR)
    if not raw or not raw.strip():
        return (runtime_root().resolve(),)
    roots = []
    for part in raw.split(os.pathsep):
        candidate = part.strip()
        if not candidate:
            continue
        roots.append(Path(candidate).expanduser().resolve())
    if not roots:
        return (runtime_root().resolve(),)
    return tuple(roots)


def _positive_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        return default
    return value if value > 0 else default


def attach_policy() -> str:
    """Return the effective attach policy.

    The default is :data:`ACTIVATE_EXISTING`, which is live-proven to reach a
    running Maxsurf Modeler and Stability and which refuses, before touching COM,
    when the module's executable is not running. It cannot start an application.
    :data:`ATTACH_ONLY` remains available as the strict ROT-only diagnostic, but
    cannot reach Maxsurf, which does not publish itself in the ROT.

    An unrecognised value falls back to the default rather than failing to start,
    but :func:`describe` reports what was actually applied.
    """
    raw = (os.environ.get(ATTACH_POLICY_VAR) or "").strip().casefold()
    return raw if raw in ATTACH_POLICIES else DEFAULT_ATTACH_POLICY


def allowed_modules() -> tuple[str, ...]:
    """Return the module names a session may be opened for."""
    raw = os.environ.get(ALLOWED_MODULES_VAR)
    if not raw or not raw.strip():
        return DEFAULT_ALLOWED_MODULES
    names = tuple(
        part.strip().casefold() for part in raw.split(",") if part.strip()
    )
    return names or DEFAULT_ALLOWED_MODULES


def com_timeout() -> float:
    """Seconds a normal COM operation may take."""
    return _positive_float(COM_TIMEOUT_VAR, 120.0)


def analysis_timeout() -> float:
    """Seconds an analysis may take."""
    return _positive_float(ANALYSIS_TIMEOUT_VAR, 1800.0)


def shutdown_timeout() -> float:
    """Seconds to wait for the COM worker to finish during shutdown."""
    return _positive_float(SHUTDOWN_TIMEOUT_VAR, 20.0)


def liveness_interval() -> float:
    """Seconds a liveness probe result stays trusted."""
    return _positive_float(LIVENESS_INTERVAL_VAR, 5.0)


def audit_lock_timeout() -> float:
    """Seconds to wait for the interprocess audit lock."""
    return _positive_float(AUDIT_LOCK_TIMEOUT_VAR, 10.0)


def describe() -> dict[str, object]:
    """Return the effective policy for diagnostics and status reporting."""
    return {
        "diagnostics_enabled": diagnostics_enabled(),
        "legacy_enabled": legacy_enabled(),
        "audit_enabled": audit_enabled(),
        "audit_dir": str(audit_dir()),
        "workspace_roots": [str(root) for root in workspace_roots()],
        "attach_policy": attach_policy(),
        "allowed_modules": list(allowed_modules()),
        "com_timeout_s": com_timeout(),
        "analysis_timeout_s": analysis_timeout(),
        "shutdown_timeout_s": shutdown_timeout(),
        "liveness_interval_s": liveness_interval(),
        "audit_lock_timeout_s": audit_lock_timeout(),
    }
