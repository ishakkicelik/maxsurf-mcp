"""
COM connection layer.

Connection lifecycle, identity, and liveness live in
:mod:`maxsurf_mcp.session`; every COM call is executed on the single owned STA
thread in :mod:`maxsurf_mcp.comthread`. This module is the thin facade the
application modules use, plus the shared JSON and property-probing helpers.

Path resolution is delegated to :mod:`maxsurf_mcp.objectpath`, which keeps
traversal inside the COM object graph. Raw property/method access lives in
:mod:`maxsurf_mcp.diagnostics` and is disabled by default.
"""
from __future__ import annotations

import json
from typing import Any

from . import comthread, config, objectpath
from . import session as session_module
from . import typeinfo
from .audit import Channel, Classification
from .guard import guarded, register

try:
    import winreg
except ImportError:  # importable on non-Windows for offline tests
    winreg = None

#: Kept for compatibility: the ProgID candidates now live on the module specs
#: in :mod:`maxsurf_mcp.session`, which is the single source of truth.
PROGID_HINTS = {
    name: list(spec.progids)
    for name, spec in session_module.MODULES.items()
    if spec.kind == "application"
}

KEYWORDS = ("maxsurf", "bentleymodeler", "bentleystability",
            "bentleyresistance", "bentleymotions", "formsys", "hydromax",
            "hullspeed", "seakeeper", "multiframe", "workshop")


def scan_registry() -> list[str]:
    """List every Maxsurf-related ProgID under HKCR.

    This is the first diagnostic to reach for when a connection fails.
    """
    if winreg is None:
        return []
    found = []
    root = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "")
    n_keys = winreg.QueryInfoKey(root)[0]
    for i in range(n_keys):
        try:
            name = winreg.EnumKey(root, i)
        except OSError:
            continue
        low = name.lower()
        if any(k in low for k in KEYWORDS) and "." in name:
            found.append(name)
    return sorted(found)


def connect(module: str = "modeler", progid: str | None = None,
            early_binding: bool = True, policy: str | None = None):
    """Return a live application object for ``module``.

    Delegates to the session manager, which verifies liveness, reconnects a
    stale pointer, and refuses a mutating operation whose application or design
    identity has changed. ``early_binding`` is accepted for compatibility and
    ignored: under ROT attachment the type-library wrapper is built from the
    attached object's own type library rather than by creating a second
    instance, and under COM activation ``EnsureDispatch`` already provides it.
    """
    del early_binding
    return session_module.get_manager().get(module, progid, policy=policy)


def session_for(module: str) -> Any:
    """Return the :class:`~maxsurf_mcp.session.MaxsurfSession` for a module."""
    return session_module.get_manager().session_for(module)


def reset(module: str | None = None) -> dict[str, Any]:
    """Drop cached sessions so the next call reconnects from scratch."""
    return session_module.get_manager().reset(module)


def resolve(root: Any, path: str) -> Any:
    """Resolve a dotted COM path such as ``Design.Surfaces(3).Name``.

    Delegates to the hardened resolver so traversal cannot leave the COM
    object graph.
    """
    return objectpath.resolve(root, path)


def to_json(payload: Any) -> str:
    """Serialize an MCP result consistently across application modules."""
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str,
                      allow_nan=False)


def first_available(obj: Any, *names: str) -> Any:
    """Return the first readable non-null COM property from ``names``.

    Note that a missing member and a failing getter are indistinguishable in
    the return value. Use :func:`probe` when that difference matters.
    """
    for name in names:
        try:
            value = getattr(obj, name)
            if value is not None:
                return value
        except Exception:  # noqa: BLE001 - COM getters can fail by design
            continue
    return None


def probe(obj: Any, *names: str) -> dict[str, Any]:
    """Read the first available member and report how it was obtained.

    Unlike :func:`first_available` this distinguishes "no candidate member
    exists on this build" from "a getter raised", which is the difference
    between a missing feature and a broken call. The session layer uses the
    same primitive to establish version and design identity.
    """
    return session_module.probe_member(obj, *names)


@guarded(classification=Classification.WRITE_STATE, module="com",
         channel=Channel.MCP_COM)
def connect_maxsurf(module: str = "modeler", progid: str = "",
                    policy: str = "") -> str:
    """WRITE_STATE. Connect to an already-running Maxsurf module. Call this first.

    module: modeler | stability | resistance | motions | multiframe.
    Leave progid empty for registry discovery.

    policy selects how the object is obtained. It defaults to the configured
    MAXSURF_MCP_ATTACH_POLICY, itself defaulting to activate_existing:
      activate_existing (default) - verifies the Maxsurf executable is already
        running, refuses before any COM call if it is not, and only then performs
        COM activation. This is the policy that reaches Maxsurf, because Maxsurf
        does not publish its Application object in the Running Object Table.
      attach_only - Running Object Table only, no COM activation of any kind.
        Strict, and unable to reach Maxsurf; useful as a diagnostic.
      require_pid - as attach_only, plus a proven COM host process id.
      launch_if_absent - permits Maxsurf to be started. Opt-in.

    No policy other than launch_if_absent can bring a Maxsurf process into
    existence. The returned session records how the object was obtained,
    including candidate process snapshots taken either side of any COM
    activation, what its identity could be verified against, and whether the
    open design can be identified at all.
    """
    connect(module, progid or None, policy=policy or None)
    established = session_for(module)
    payload: dict[str, Any] = {
        "connected": module,
        "session": established.describe() if established else None,
        "policy": {
            "requested": policy or None,
            "effective": established.attach_policy if established else None,
            "configured_default": config.attach_policy(),
            "allowed_modules": list(config.allowed_modules()),
        },
        "warnings": [],
    }
    if established is not None:
        payload["warnings"].extend(established.warnings)
        identity = established.interface_identity or {}
        if identity.get("status") == session_module.IDENTITY_UNVERIFIED:
            payload["warnings"].append(
                "the application interface identity could not be verified "
                f"({identity.get('detail')}); this is not evidence of a wrong "
                "application, only that nothing could be established"
            )
        if established.com_host_pid is None:
            payload["warnings"].append(
                "the COM host process id could not be proven for this object. "
                "The candidate process ids under session.activation prove only "
                "that a matching executable was running, not which process "
                "serves this object"
            )
        if established.design_swap_detection != "active":
            payload["warnings"].append(
                f"the open design cannot be identified "
                f"({established.design_identity()['detail']}), so design-swap "
                f"detection is NOT active for {module} and a mutating call "
                f"cannot verify it is addressing the same design"
            )
        if established.attach_method == session_module.COM_ACTIVATION:
            payload["warnings"].append(
                "this object came from COM activation, not from the Running "
                "Object Table; activation was permitted only because the "
                "executable was already running, and the process snapshots in "
                "session.activation record what the call did"
            )
    return to_json(payload)


@guarded(classification=Classification.WRITE_STATE, module="com",
         channel=Channel.MCP_COM)
def reset_connection(module: str = "") -> str:
    """WRITE_STATE. Drop cached Maxsurf sessions so the next call reconnects.

    Use this after restarting Maxsurf, or to deliberately accept a different
    instance or design after an identity mismatch was refused. Leave module
    empty to reset every session. This releases this server's references only;
    it does not close or modify anything inside Maxsurf.
    """
    summary = reset(module or None)
    return to_json({
        "reset": summary["dropped"],
        "previous_sessions": summary["previous"],
        "com_worker": comthread.describe(),
        "note": "no application was closed; the next call re-attaches under "
                f"the {config.attach_policy()} policy",
    })


@guarded(classification=Classification.READ, module="com",
         channel=Channel.LOCAL)
def list_progids() -> str:
    """READ. List Maxsurf-related ProgIDs found in the registry (diagnostic)."""
    return to_json({"progids": scan_registry()})


@guarded(classification=Classification.READ, module="com",
         channel=Channel.LOCAL)
def session_status() -> str:
    """READ. Report sessions, the COM worker, the policy, and Maxsurf processes.

    Touches no COM member, so it stays usable when a connection is broken. The
    process section answers the question COM cannot: whether Maxsurf is actually
    running, independently of whether it is reachable.
    """
    return to_json({
        "sessions": session_module.get_manager().describe(),
        "com_worker": comthread.describe(),
        "policy": config.describe(),
        "processes": session_module.describe_processes(),
    })


@guarded(classification=Classification.READ, module="com",
         channel=Channel.MCP_COM)
def com_describe(path: str = "", module: str = "modeler",
                 with_values: bool = False) -> str:
    """READ. Describe the COM object model from generated wrapper metadata.

    An empty path describes the Application object, 'Design' the design, and
    'Design.Surfaces(1)' the first surface. Traversal is restricted to the COM
    object graph. with_values=true reads real property values, which on some
    builds triggers a calculation, so leave it off unless needed.
    """
    app = connect(module)
    obj = objectpath.resolve(app, path, allow_leaf_value=False)
    return to_json({"path": path or "Application",
                    **typeinfo.describe_python(
                        obj, sample_values=with_values
                    )})


TOOLS = (
    connect_maxsurf,
    reset_connection,
    session_status,
    list_progids,
    com_describe,
)


def register_tools(mcp: Any) -> list[str]:
    """Register the shared connection and description tools."""
    return register(mcp, TOOLS)
