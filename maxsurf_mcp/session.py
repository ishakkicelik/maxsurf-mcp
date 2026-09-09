"""Managed Maxsurf COM sessions.

The old lifecycle was a dictionary keyed by ``module:progid:early_binding``
holding whatever object ``GetActiveObject`` or ``EnsureDispatch`` happened to
return. It recorded nothing about what it had attached to, silently launched
Maxsurf when nothing was running, and never noticed when that process went
away: after a restart, every cached pointer raised until the MCP server itself
was restarted.

A :class:`MaxsurfSession` replaces that with an object that knows what it is
connected to and can prove it:

* **Attach policy.** ``attach_only`` uses the Running Object Table alone and
  performs no COM activation whatsoever. That is the strictest possible
  behaviour, and on this application it is also insufficient: Maxsurf does not
  publish its Application object in the ROT, so ``GetActiveObject`` returns
  ``MK_E_UNAVAILABLE`` even while ``MaxsurfModeler.exe`` is plainly running.
  ``activate_existing`` therefore exists: it asks the operating system whether
  the module's executable is running, refuses outright if it is not, and only
  then performs COM activation, comparing process snapshots either side of the
  call so an activation that created something new is reported rather than
  passed off as an attach. ``launch_if_absent`` remains the only policy under
  which a Maxsurf process may legitimately come into existence, and
  ``require_pid`` is ROT-only plus a proven host process id.
* **Identity.** Every session records its ProgID, CLSID, version, host process
  id, the interface identity it verified, and -- for the modules where the
  property is proven -- the open design path. Reconnecting compares the new
  fingerprint against the old one and refuses on any mismatch, so a restarted
  Maxsurf holding a different design cannot be mistaken for the original.
* **Liveness.** A cheap ``IUnknown`` round trip distinguishes a live object
  from a stale pointer, and the RPC error codes that mean "the server is gone"
  are recognised so recovery is deliberate rather than accidental.

Only facts proven by this project's own live ``ITypeInfo`` discovery, recorded
in ``private discovery notes (not distributed)``, are relied upon. Where a property is not proven for a
module, the session reports it as ``not_proven`` rather than guessing a name.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import audit, comthread, config, processes
from .audit import Channel, Classification, Outcome
from .errors import (
    MaxsurfConnectionError,
    MaxsurfSafetyError,
    MaxsurfValidationError,
)

try:  # pragma: no cover - exercised on Windows only
    import pythoncom
    import win32com.client
    from win32com.client import gencache
except ImportError:  # importable on non-Windows for offline reading
    pythoncom = None
    win32com = None
    gencache = None

try:  # pragma: no cover - optional, only used to establish a process id
    import win32process
except ImportError:
    win32process = None


@dataclass(frozen=True)
class ModuleSpec:
    """What is known, and proven, about one connectable module."""

    name: str
    progids: tuple[str, ...]
    #: ``application`` objects are long-lived processes the user owns.
    #: ``helper`` objects are lightweight coclasses created on demand.
    kind: str = "application"
    #: IApplication IID proven by live ITypeInfo discovery, if any.
    application_iid: str | None = None
    #: Design members proven to exist on this module. Empty means not proven.
    design_object_member: str = "Design"
    design_path_members: tuple[str, ...] = ()
    design_name_members: tuple[str, ...] = ()
    #: A helper may only be created while this module already has a session,
    #: which is what stops creating it from starting an application.
    requires_session: str | None = None
    #: Image names observed for this module on the live system. Empty means the
    #: executable is not proven, and the process-gated activation policy then
    #: refuses rather than guessing a name.
    executables: tuple[str, ...] = ()


#: Proven IApplication IIDs, read from live type information and recorded in
#: private discovery notes (not distributed). Used as identity evidence, never invented.
MODULES: dict[str, ModuleSpec] = {
    "modeler": ModuleSpec(
        name="modeler",
        progids=("BentleyModeler.Application", "Maxsurf.Application",
                 "MaxsurfModeler.Application", "Modeler.Application"),
        application_iid="{B2B50074-E514-4866-B6A3-23DAFD8622FC}",
        # Path and Name are property getters on Modeler's IDesign, proven by the
        # registered type library. An earlier note here claimed they did not
        # exist, having read only the 34-entry *method* list, which by
        # construction excludes property getters.
        design_path_members=("Path",),
        design_name_members=("Name",),
        executables=("MaxsurfModeler.exe",),
    ),
    "stability": ModuleSpec(
        name="stability",
        progids=("BentleyStability.Application", "MaxsurfStability.Application",
                 "Hydromax.Application", "Stability.Application"),
        application_iid="{F7073155-8020-4522-B800-7155D1E0FC5B}",
        # Stability IDesign.Path and .Name are proven property-gets.
        design_path_members=("Path",),
        design_name_members=("Name",),
        executables=("MaxsurfStability.exe",),
    ),
    "stability_xmlgrid": ModuleSpec(
        name="stability_xmlgrid",
        progids=("BentleyStability.XMLGrid",),
        kind="helper",
        requires_session="stability",
    ),
    "resistance": ModuleSpec(
        name="resistance",
        progids=("BentleyResistance.Application",
                 "MaxsurfResistance.Application", "Hullspeed.Application",
                 "Resistance.Application"),
        application_iid="{AE8F737F-0D6C-4203-9C8B-46701C14DD44}",
        design_path_members=("DesignPath",),
        design_name_members=("DesignName",),
        executables=("MaxsurfResistance.exe",),
    ),
    "motions": ModuleSpec(
        name="motions",
        # BentleyMotions.Application is the registered ProgID (CLSID
        # {17618094-EBE1-49FC-838E-B61B79BE04CC}, LocalServer32
        # MaxsurfMotions.exe). The earlier three names were never registered,
        # which is why every Motions connection failed before it began.
        progids=("BentleyMotions.Application", "MaxsurfMotions.Application",
                 "Seakeeper.Application"),
        application_iid="{5AD4004B-B9DA-4DAD-94A1-FF87B721B5B5}",
        design_path_members=("DesignPath",),
        design_name_members=("DesignName",),
        executables=("MaxsurfMotions.exe",),
    ),
    "multiframe": ModuleSpec(
        name="multiframe",
        progids=("Multiframe.Application",),
        application_iid="{AA8FF5B2-3899-11D4-9214-00104B939EBF}",
        design_object_member="Frame",
        design_path_members=("FullName",),
        design_name_members=("Name",),
        executables=("Multiframe.exe",),
    ),
    "motions_xmlgrid": ModuleSpec(
        name="motions_xmlgrid",
        progids=("BentleyMotions.XMLGrid",),
        kind="helper",
        requires_session="motions",
    ),
}

#: Candidate members for the application version. Probed, never assumed: the
#: live connection response reported a version for both Modeler and Stability,
#: but only Stability's IApplication.Version is proven by ITypeInfo.
VERSION_MEMBERS = ("Version", "VersionString", "AppVersion")

#: HRESULTs that mean the COM server is gone rather than merely unhappy.
STALE_HRESULTS = frozenset({
    0x800706BA,  # RPC_S_SERVER_UNAVAILABLE
    0x800706BE,  # RPC_S_CALL_FAILED
    0x800706BF,  # RPC_S_CALL_FAILED_DNE
    0x80010108,  # RPC_E_DISCONNECTED
    0x80010007,  # RPC_E_SERVER_DIED
    0x80010012,  # RPC_E_SERVER_DIED_DNE
    0x800401FD,  # CO_E_OBJNOTCONNECTED
    0x80080005,  # CO_E_SERVER_EXEC_FAILURE
})

#: ``E_NOINTERFACE``. The only QueryInterface failure that actually means the
#: object does not implement an interface.
E_NOINTERFACE = 0x80004002

ALIVE = "alive"
STALE = "stale"
UNKNOWN = "unknown"

#: The object's identity matches the IID proven for the requested module.
IDENTITY_VERIFIED = "verified"

#: Nothing could be established. Explicitly *not* evidence of a mismatch.
IDENTITY_UNVERIFIED = "unverified"

#: The object belongs to a different Maxsurf module. The only state that refuses.
IDENTITY_CONFLICTING = "conflicting"

#: The object was published in the Running Object Table by a running
#: application, and simply picked up. Maxsurf does not do this.
ROT_ATTACH = "rot_attach"

#: The object was produced by COM activation. Whether that reached the running
#: application or created something new is established by comparing process
#: snapshots taken either side of the call.
COM_ACTIVATION = "com_activation"

#: Activation made a new process appear under a policy that did not ask for one.
#: Reported, never hidden, never "fixed" by killing anything.
UNEXPECTED_PROCESS = "unexpected_process_created"

#: Activation made a new process appear under ``launch_if_absent``, which is what
#: that policy is for.
PROCESS_CREATED = "process_created"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _unsigned_hresult(exc: BaseException) -> int | None:
    value = getattr(exc, "hresult", None)
    if value is None:
        args = getattr(exc, "args", ())
        value = args[0] if args and isinstance(args[0], int) else None
    if value is None:
        return None
    return int(value) & 0xFFFFFFFF


def is_stale_error(exc: BaseException) -> bool:
    """True when an exception means the COM server is no longer reachable.

    Unknown failures deliberately return False: reconnecting on a transient
    error risks attaching to a different instance, and that is worse than
    reporting the original error.
    """
    hresult = _unsigned_hresult(exc)
    return hresult is not None and hresult in STALE_HRESULTS


def _oleobj_of(app: Any) -> Any:
    return getattr(app, "_oleobj_", None)


def probe_liveness(app: Any) -> tuple[str, str | None]:
    """Return ``(state, detail)`` for a COM object.

    Uses an ``IUnknown`` round trip: always implemented, never triggers an
    application calculation, and never opens a dialog. Compare this with
    reading an application property, which on some Maxsurf builds does both.
    """
    oleobj = _oleobj_of(app)
    if oleobj is None:
        return UNKNOWN, "object exposes no COM interface pointer"
    query = getattr(oleobj, "QueryInterface", None)
    if query is None or pythoncom is None:
        return UNKNOWN, "QueryInterface is unavailable on this object"
    try:
        query(pythoncom.IID_IUnknown)
    except Exception as exc:  # noqa: BLE001
        if is_stale_error(exc):
            return STALE, str(exc)[:300]
        return UNKNOWN, str(exc)[:300]
    return ALIVE, None


def probe_member(obj: Any, *names: str) -> dict[str, Any]:
    """Read the first available member, distinguishing absent from failed."""
    attempts: list[dict[str, Any]] = []
    for name in names:
        try:
            value = getattr(obj, name)
        except AttributeError:
            attempts.append({"member": name, "status": "absent"})
            continue
        except Exception as exc:  # noqa: BLE001
            attempts.append({"member": name, "status": "failed",
                             "error": str(exc)[:200]})
            continue
        if value is None:
            attempts.append({"member": name, "status": "null"})
            continue
        return {"value": value, "status": "read", "source_member": name,
                "attempts": attempts}
    return {"value": None, "status": "unavailable", "source_member": None,
            "attempts": attempts}


def declared_interface_iid(app: Any) -> dict[str, Any]:
    """Ask the object which interface its own type information describes.

    ``IDispatch::GetTypeInfo`` followed by ``ITypeInfo::GetTypeAttr`` yields the
    IID of the interface the object itself declares. This is the mechanism that
    produced the IIDs recorded in ``private discovery notes (not distributed)``, so it is proven to work
    against both Maxsurf applications, and unlike ``QueryInterface`` it does not
    depend on how the interface is marshalled or on what pywin32 can wrap.
    """
    oleobj = _oleobj_of(app)
    if oleobj is None:
        return {"iid": None, "status": "unavailable",
                "detail": "object exposes no COM interface pointer"}
    if not hasattr(oleobj, "GetTypeInfo"):
        return {"iid": None, "status": "unavailable",
                "detail": "object exposes no GetTypeInfo"}
    try:
        info = oleobj.GetTypeInfo()
        attr = info.GetTypeAttr()
    except Exception as exc:  # noqa: BLE001 - server may withhold type info
        return {"iid": None, "status": "unavailable", "detail": str(exc)[:200]}
    iid = getattr(attr, "iid", None)
    if iid is None:
        try:
            iid = attr[0]
        except Exception:  # noqa: BLE001
            iid = None
    if iid is None:
        return {"iid": None, "status": "unavailable",
                "detail": "type information reported no interface id"}
    return {"iid": str(iid), "status": "read", "detail": None}


def query_interface_support(app: Any, iid_text: str) -> dict[str, Any]:
    """Ask COM whether an object supports one interface.

    The second argument to ``QueryInterface`` matters enormously here. Without
    it, pywin32 looks for a Python wrapper class registered for the IID and
    raises ``TypeError`` when there is none -- *after* COM has already answered
    that the interface is supported. Maxsurf's ``IApplication`` is exactly such
    an interface, so the naive call reports "not supported" for an interface the
    object does support. Wrapping the result as ``IUnknown`` avoids the lookup
    and leaves COM's own answer intact.

    Returns status ``supported``, ``absent`` (a real ``E_NOINTERFACE``),
    ``inconclusive``, or ``unavailable``. Only ``absent`` is evidence.
    """
    oleobj = _oleobj_of(app)
    query = getattr(oleobj, "QueryInterface", None)
    if query is None or pythoncom is None:
        return {"status": "unavailable",
                "detail": "QueryInterface is unavailable on this object"}
    try:
        iid = pythoncom.MakeIID(iid_text)
    except Exception as exc:  # noqa: BLE001 - malformed IID text
        return {"status": "unavailable", "detail": str(exc)[:200]}
    try:
        query(iid, pythoncom.IID_IUnknown)
    except Exception as exc:  # noqa: BLE001
        hresult = getattr(exc, "hresult", None)
        if hresult is not None and (int(hresult) & 0xFFFFFFFF) == E_NOINTERFACE:
            return {"status": "absent", "detail": "E_NOINTERFACE",
                    "hresult": "0x%08X" % E_NOINTERFACE}
        return {"status": "inconclusive", "detail": str(exc)[:200],
                "error_type": type(exc).__name__,
                "hresult": None if hresult is None
                           else "0x%08X" % (int(hresult) & 0xFFFFFFFF)}
    return {"status": "supported", "detail": None}


def verify_interface_identity(app: Any, spec: ModuleSpec) -> dict[str, Any]:
    """Establish which Maxsurf module this object actually is.

    Reports one of three states, because the previous boolean could not tell
    "this is the wrong application" apart from "this could not be determined",
    and reported the latter as if it were the former:

    ``verified``
        The object's own declared interface id, or a positive COM answer, matches
        the IID proven for this module.
    ``conflicting``
        The object declares, or answers to, another Maxsurf module's proven
        IApplication interface. This is the only state that refuses a connection.
    ``unverified``
        Nothing could be established either way. Not evidence of anything.
    """
    result: dict[str, Any] = {
        "expected_iid": spec.application_iid,
        "status": IDENTITY_UNVERIFIED,
        "evidence": None,
        "declared_iid": None,
        "declared_iid_status": None,
        "query_interface": None,
        "conflicting_module": None,
        "detail": None,
    }
    if spec.application_iid is None:
        result["detail"] = f"no proven IApplication IID for {spec.name}"
        return result

    expected = _normalise_iid(spec.application_iid)
    declared = declared_interface_iid(app)
    result["declared_iid"] = declared["iid"]
    result["declared_iid_status"] = declared["status"]

    if declared["status"] == "read":
        actual = _normalise_iid(declared["iid"])
        if actual == expected:
            result["status"] = IDENTITY_VERIFIED
            result["evidence"] = "declared_interface_iid"
            return result
        other = _module_owning_iid(actual, exclude=spec.name)
        if other is not None:
            result["status"] = IDENTITY_CONFLICTING
            result["conflicting_module"] = other
            result["evidence"] = "declared_interface_iid"
            result["detail"] = (
                f"the object declares the proven {other} IApplication interface "
                f"{declared['iid']}, not {spec.name}'s {spec.application_iid}"
            )
            return result
        result["detail"] = (
            f"the object declares interface {declared['iid']}, which is neither "
            f"{spec.name}'s proven IApplication interface nor any other known "
            f"module's"
        )
    else:
        result["detail"] = (
            f"the object's own type information was unreadable "
            f"({declared['detail']})"
        )

    # Type information settled nothing; ask COM directly.
    probe = query_interface_support(app, spec.application_iid)
    result["query_interface"] = probe
    if probe["status"] == "supported":
        result["status"] = IDENTITY_VERIFIED
        result["evidence"] = "query_interface"
        result["detail"] = None
        return result
    if probe["status"] == "absent":
        for other in MODULES.values():
            if other.name == spec.name or not other.application_iid:
                continue
            if query_interface_support(app, other.application_iid)["status"] \
                    == "supported":
                result["status"] = IDENTITY_CONFLICTING
                result["conflicting_module"] = other.name
                result["evidence"] = "query_interface"
                result["detail"] = (
                    f"the object answers the proven {other.name} IApplication "
                    f"interface and explicitly refuses {spec.name}'s"
                )
                return result
    return result


def _normalise_iid(text: str | None) -> str | None:
    return text.strip().upper() if isinstance(text, str) else None


def _module_owning_iid(iid: str | None, *, exclude: str) -> str | None:
    if iid is None:
        return None
    for spec in MODULES.values():
        if spec.name == exclude or not spec.application_iid:
            continue
        if _normalise_iid(spec.application_iid) == iid:
            return spec.name
    return None


def resolve_clsid(progid: str) -> str | None:
    if pythoncom is None:
        return None
    try:
        return str(pythoncom.CLSIDFromProgID(progid))
    except Exception:  # noqa: BLE001 - not registered
        return None


def resolve_com_host_pid(app: Any) -> dict[str, Any]:
    """Try to establish which process actually hosts this COM object.

    This is a much stronger claim than "a matching executable is running", and
    the two must never be conflated. The process gate proves a
    ``MaxsurfModeler.exe`` exists; only this function can prove that *this*
    object lives inside it.

    Uses ``IOleWindow::GetWindow`` to obtain a window handle owned by the server,
    then asks the OS which process owns it. Any step being unavailable yields
    ``None`` with the reason recorded, which is what makes the ``require_pid``
    policy meaningful rather than decorative.
    """
    def unproven(detail: str) -> dict[str, Any]:
        return {"pid": None, "status": "unproven", "source": None,
                "detail": detail}

    if pythoncom is None or win32process is None:
        return unproven("pywin32 process helpers are unavailable")
    iid = getattr(pythoncom, "IID_IOleWindow", None)
    if iid is None:
        return unproven("IOleWindow is not exposed by this pywin32 build")
    oleobj = _oleobj_of(app)
    query = getattr(oleobj, "QueryInterface", None)
    if query is None:
        return unproven("object exposes no QueryInterface")
    try:
        window = query(iid)
        handle = window.GetWindow()
        if not handle:
            return unproven("IOleWindow returned no window handle")
        _thread_id, pid = win32process.GetWindowThreadProcessId(handle)
    except Exception as exc:  # noqa: BLE001
        return unproven(str(exc)[:200])
    if not pid:
        return unproven("no process id for the reported window")
    return {"pid": int(pid), "status": "proven",
            "source": "IOleWindow.GetWindow", "detail": None}


@dataclass
class MaxsurfSession:
    """One managed connection to one Maxsurf module."""

    module: str
    spec: ModuleSpec
    app: Any
    progid: str
    attach_policy: str
    attach_method: str
    clsid: str | None = None
    version: str | None = None
    version_source: str | None = None
    #: The process this COM object is proven to live in. ``None`` unless proven;
    #: never populated from the process gate's candidate pids.
    com_host_pid: int | None = None
    com_host_pid_status: str = "unproven"
    com_host_pid_source: str | None = None
    com_host_pid_detail: str | None = None
    interface_identity: dict[str, Any] = field(default_factory=dict)
    #: How the object was obtained, with the process snapshots that prove it.
    activation: dict[str, Any] = field(default_factory=dict)
    #: Things a caller must know about this session but that are not errors.
    warnings: list[str] = field(default_factory=list)
    early_binding: str = "unknown"
    connected_at: str = field(default_factory=_now)
    liveness: str = UNKNOWN
    liveness_detail: str | None = None
    liveness_checked_at: float = field(default_factory=time.monotonic)
    design_path: str | None = None
    design_path_status: str = "not_proven"
    design_name: str | None = None
    design_identity_detail: str | None = None
    reconnect_count: int = 0
    session_id: str = field(default_factory=audit.new_id)

    # -- identity ----------------------------------------------------------
    def read_design_identity(self) -> dict[str, Any]:
        """Read the open design's identity where the members are proven.

        An empty path is reported as ``empty`` rather than as a successful read.
        A design that has never been saved has no path, and treating ``""`` as an
        identity would make design-swap detection appear to work while comparing
        nothing against nothing.
        """
        if not self.spec.design_path_members:
            return {"path": None, "status": "not_proven", "name": None,
                    "reason": f"no Design path member is proven for "
                              f"{self.module}"}
        design = probe_member(self.app, self.spec.design_object_member)
        if design["status"] != "read":
            return {"path": None, "status": "no_design", "name": None,
                    "reason": "the application exposes no Design object"}
        path = probe_member(design["value"], *self.spec.design_path_members)
        name = probe_member(design["value"], *self.spec.design_name_members) \
            if self.spec.design_name_members else {"value": None}
        value = str(path["value"]) if path["status"] == "read" else None
        status = path["status"]
        reason = None
        if status == "read" and not (value or "").strip():
            value, status = None, "empty"
            reason = ("the design has no path yet, which normally means it has "
                      "never been saved")
        return {
            "path": value,
            "status": status,
            "name": str(name["value"]) if name.get("value") is not None else None,
            "reason": reason,
        }

    def refresh_design_identity(self) -> None:
        identity = self.read_design_identity()
        self.design_path = identity["path"]
        self.design_path_status = identity["status"]
        self.design_name = identity["name"]
        self.design_identity_detail = identity["reason"]

    @property
    def design_swap_detection(self) -> str:
        """Whether a design swap under this session could actually be noticed."""
        return "active" if self.design_path else "unavailable"

    def design_identity(self) -> dict[str, Any]:
        """An honest account of what is known about the open design."""
        if not self.spec.design_path_members:
            reason = (f"no Design path member is proven for {self.module}, so "
                      f"the open design cannot be identified at all")
        elif self.design_path:
            reason = None
        else:
            reason = self.design_identity_detail or (
                f"the design path could not be read ({self.design_path_status})")
        return {
            "path": self.design_path,
            "name": self.design_name,
            "status": self.design_path_status,
            "path_members": list(self.spec.design_path_members),
            "swap_detection": self.design_swap_detection,
            "detail": reason,
        }

    def fingerprint(self) -> dict[str, Any]:
        """The comparable identity of this session."""
        return {
            "module": self.module,
            "progid": self.progid,
            "clsid": self.clsid,
            "com_host_pid": self.com_host_pid,
            "design_path": self.design_path,
            "design_path_status": self.design_path_status,
        }

    # -- liveness ----------------------------------------------------------
    def is_alive(self, *, force: bool = False) -> bool:
        """Probe the connection, reusing a recent result unless forced."""
        age = time.monotonic() - self.liveness_checked_at
        if not force and self.liveness == ALIVE and age < config.liveness_interval():
            return True
        state, detail = probe_liveness(self.app)
        self.liveness = state
        self.liveness_detail = detail
        self.liveness_checked_at = time.monotonic()
        return state != STALE

    def mark_stale(self, detail: str | None = None) -> None:
        self.liveness = STALE
        self.liveness_detail = detail
        self.liveness_checked_at = time.monotonic()

    def describe(self) -> dict[str, Any]:
        """Return everything known about this session, safe to serialise."""
        return {
            "session_id": self.session_id,
            "module": self.module,
            "kind": self.spec.kind,
            "progid": self.progid,
            "clsid": self.clsid,
            "version": self.version,
            "version_source": self.version_source,
            "com_host_pid": self.com_host_pid,
            "com_host_pid_status": self.com_host_pid_status,
            "com_host_pid_source": self.com_host_pid_source,
            "com_host_pid_detail": self.com_host_pid_detail,
            "attach_policy": self.attach_policy,
            "attach_method": self.attach_method,
            "activation": self.activation,
            "warnings": list(self.warnings),
            "early_binding": self.early_binding,
            "interface_identity": self.interface_identity,
            "connected_at": self.connected_at,
            "liveness": self.liveness,
            "liveness_detail": self.liveness_detail,
            "design_identity": self.design_identity(),
            "design_path": self.design_path,
            "design_path_status": self.design_path_status,
            "design_name": self.design_name,
            "reconnect_count": self.reconnect_count,
            "wrapper_class": type(self.app).__name__,
        }


class SessionManager:
    """Creates, verifies, reuses, and recycles :class:`MaxsurfSession` objects.

    All COM contact happens on the dedicated STA worker. Every method here is
    safe to call from a tool body that is already running on that worker,
    because :func:`maxsurf_mcp.comthread.run` executes inline in that case.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, MaxsurfSession] = {}
        self._lock = threading.RLock()

    # -- policy ------------------------------------------------------------
    def _spec(self, module: str, progid: str | None) -> ModuleSpec:
        key = (module or "").strip().casefold()
        if not key:
            raise MaxsurfValidationError("a module name is required")
        spec = MODULES.get(key)
        if spec is None:
            if not progid:
                raise MaxsurfValidationError(
                    "unknown module and no ProgID supplied",
                    module=module,
                    known_modules=sorted(MODULES),
                )
            spec = ModuleSpec(name=key, progids=(progid,))
        if key not in config.allowed_modules():
            raise MaxsurfSafetyError(
                "this module is not in the allowed set, so no connection was "
                "attempted",
                module=key,
                allowed_modules=list(config.allowed_modules()),
                hint=f"set {config.ALLOWED_MODULES_VAR} to permit it",
            )
        return spec

    # -- public API --------------------------------------------------------
    def get(self, module: str = "modeler", progid: str | None = None, *,
            policy: str | None = None, for_write: bool | None = None) -> Any:
        """Return a live application object for ``module``.

        Reuses an existing session when it is provably alive, reconnects when
        the pointer has gone stale, and verifies identity before permitting a
        mutating operation.
        """
        spec = self._spec(module, progid)
        effective_policy = policy or config.attach_policy()
        if for_write is None:
            for_write = _classification_mutates(audit.current_classification())

        with self._lock:
            existing = self._sessions.get(spec.name)
        if existing is not None:
            return self._revalidate(existing, for_write=for_write).app

        session = self._open(spec, progid, effective_policy)
        with self._lock:
            self._sessions[spec.name] = session
        return session.app

    def session_for(self, module: str) -> MaxsurfSession | None:
        with self._lock:
            return self._sessions.get((module or "").strip().casefold())

    def reset(self, module: str | None = None) -> dict[str, Any]:
        """Drop cached sessions so the next call reconnects from scratch."""
        with self._lock:
            if module:
                key = module.strip().casefold()
                dropped = [key] if key in self._sessions else []
                removed = {key: self._sessions.pop(key)} if dropped else {}
            else:
                dropped = sorted(self._sessions)
                removed = dict(self._sessions)
                self._sessions.clear()
        summary = {
            "dropped": dropped,
            "previous": {name: s.describe() for name, s in removed.items()},
        }
        _record("session_reset", Classification.WRITE_STATE, Outcome.SUCCESS,
                summary)
        return summary

    def describe(self) -> dict[str, Any]:
        with self._lock:
            sessions = {name: s.describe() for name, s in self._sessions.items()}
        return {
            "attach_policy": config.attach_policy(),
            "allowed_modules": list(config.allowed_modules()),
            "sessions": sessions,
        }

    # -- internals ---------------------------------------------------------
    def _revalidate(self, session: MaxsurfSession, *,
                    for_write: bool) -> MaxsurfSession:
        """Return a usable session, reconnecting if the pointer went stale."""
        alive = comthread.run(
            lambda: session.is_alive(force=for_write),
            description=f"liveness:{session.module}",
        )
        if not alive:
            _record("session_stale", Classification.READ, Outcome.FAILURE,
                    {"module": session.module,
                     "detail": session.liveness_detail,
                     "previous": session.fingerprint()})
            with self._lock:
                self._sessions.pop(session.module, None)
            return self._reconnect(session)

        if for_write:
            self._require_stable_identity(session)
        return session

    def _require_stable_identity(self, session: MaxsurfSession) -> None:
        """Refuse a mutation if the session is no longer what it claims to be."""
        if not session.spec.design_path_members:
            return
        before = session.fingerprint()
        comthread.run(session.refresh_design_identity,
                      description=f"identity:{session.module}")
        after = session.fingerprint()
        if before.get("design_path") == after.get("design_path"):
            return
        session.mark_stale("the open design changed under this session")
        blocked = MaxsurfSafetyError(
            "the design open in this application changed since the session was "
            "established; refusing a mutating operation against an unexpected "
            "design",
            module=session.module,
            expected_design=before.get("design_path"),
            actual_design=after.get("design_path"),
            hint="call reset_connection and re-establish the intended design",
        )
        _record("session_identity_mismatch", Classification.WRITE_STATE,
                Outcome.BLOCKED, {"module": session.module,
                                  "expected": before, "actual": after},
                error=audit.error_payload(blocked))
        raise blocked

    def _reconnect(self, previous: MaxsurfSession) -> MaxsurfSession:
        """Re-establish a session and refuse to silently swap identity."""
        fresh = self._open(previous.spec, previous.progid,
                           previous.attach_policy, reconnect=True)
        mismatch = _identity_mismatch(previous.fingerprint(), fresh.fingerprint())
        if mismatch:
            blocked = MaxsurfSafetyError(
                "reconnected to a different Maxsurf instance or design than the "
                "session was originally established against",
                module=previous.module,
                mismatch=mismatch,
                expected=previous.fingerprint(),
                actual=fresh.fingerprint(),
                hint="call reset_connection to accept the new instance "
                     "deliberately",
            )
            _record("session_identity_mismatch", Classification.WRITE_STATE,
                    Outcome.BLOCKED,
                    {"module": previous.module, "mismatch": mismatch,
                     "expected": previous.fingerprint(),
                     "actual": fresh.fingerprint()},
                    error=audit.error_payload(blocked))
            raise blocked

        fresh.reconnect_count = previous.reconnect_count + 1
        with self._lock:
            self._sessions[fresh.module] = fresh
        _record("session_reconnect", Classification.WRITE_STATE,
                Outcome.SUCCESS, fresh.describe())
        return fresh

    def _open(self, spec: ModuleSpec, progid: str | None, policy: str,
              *, reconnect: bool = False) -> MaxsurfSession:
        """Attach to, or under an explicit policy create, the COM object."""
        if pythoncom is None:
            raise MaxsurfConnectionError(
                "pywin32 is unavailable; this server only runs on Windows",
                module=spec.name,
            )
        if policy not in config.ATTACH_POLICIES:
            raise MaxsurfValidationError(
                "unknown attach policy", policy=policy,
                supported=list(config.ATTACH_POLICIES),
            )
        if spec.requires_session and self.session_for(spec.requires_session) is None:
            raise MaxsurfConnectionError(
                f"{spec.name} may only be created while {spec.requires_session} "
                f"already has a session, so that creating it cannot start an "
                f"application",
                module=spec.name,
                requires_session=spec.requires_session,
            )

        candidates = [progid] if progid else list(spec.progids)
        session = comthread.run(
            lambda: self._attach(spec, candidates, policy),
            description=f"connect:{spec.name}",
            timeout=config.com_timeout(),
        )
        if not reconnect:
            # A reconnect is recorded by the caller, after identity has been
            # verified against the previous session.
            _record("session_connect", Classification.WRITE_STATE,
                    Outcome.SUCCESS, session.describe())
        return session

    def _attach(self, spec: ModuleSpec, candidates: list[str],
                policy: str) -> MaxsurfSession:
        """Runs on the COM worker thread. Performs the actual attach.

        Two distinct ways of obtaining the object, kept distinguishable in the
        session because they carry different guarantees. ROT attachment proves
        the object was already published by a running application. COM
        activation does not, which is why it is gated on the executable being
        present and audited with a process snapshot on both sides.
        """
        gate = self._process_gate(spec, policy)
        attempts: list[dict[str, Any]] = []
        for candidate in candidates:
            if not candidate:
                continue
            app, evidence, detail = self._obtain(spec, candidate, policy, gate)
            if app is None:
                attempts.append({"progid": candidate, "error": detail})
                continue
            session = MaxsurfSession(
                module=spec.name,
                spec=spec,
                app=app,
                progid=candidate,
                attach_policy=policy,
                attach_method=evidence["method"],
                clsid=resolve_clsid(candidate),
                activation=evidence,
            )
            if evidence.get("status") == UNEXPECTED_PROCESS:
                session.warnings.append(
                    "COM activation caused an additional "
                    f"{spec.name} process to appear "
                    f"(new pids {list(evidence['new_pids'])}); this was NOT a "
                    "plain attach to the process that was already running, and "
                    "no process has been terminated"
                )
            self._describe_attached(session, policy)
            return session

        raise MaxsurfConnectionError(
            "could not connect to the requested Maxsurf module"
            + self._failure_hint(policy, gate),
            module=spec.name,
            attach_policy=policy,
            may_activate=gate["may_activate"],
            process_gate=gate,
            attempted=attempts,
            hint="confirm the application is running and that the licence "
                 "includes COM automation (Advanced/Ultimate)",
        )

    def _process_gate(self, spec: ModuleSpec, policy: str) -> dict[str, Any]:
        """Decide whether COM activation is permitted, and prove why.

        Maxsurf never publishes its Application object in the ROT, so the only
        evidence that the user's application is already running is the operating
        system's process list. This is where that evidence is gathered, before
        any COM call.

        These are *candidate* process ids: processes whose image name matches the
        module. That a matching process exists is all they prove. Which process
        ends up hosting the COM object is a separate question, answered -- if at
        all -- by :func:`resolve_com_host_pid`.
        """
        gate: dict[str, Any] = {
            "policy": policy,
            "required": policy == config.ACTIVATE_EXISTING,
            "executables": list(spec.executables),
            "state": "not_required",
            "candidate_process_pids_before": (),
            "may_activate": policy in config.ACTIVATING_POLICIES
            or spec.kind == "helper",
            "detail": None,
        }
        if policy != config.ACTIVATE_EXISTING:
            # Not gating, but still snapshot where possible: without a before
            # picture, the after picture would report every running instance as
            # newly created.
            if (gate["may_activate"] and spec.executables
                    and spec.kind != "helper"):
                gate["candidate_process_pids_before"] = processes.pids_of(
                    spec.executables)
            return gate
        if spec.kind == "helper":
            # A helper is already gated by requires_session, which means the
            # application it belongs to has a live session.
            gate["state"] = "delegated_to_required_session"
            return gate

        found = processes.find(spec.executables)
        gate["inspected_processes"] = found["inspected"]
        if not spec.executables:
            gate["state"] = "unprovable"
            gate["may_activate"] = False
            gate["detail"] = (
                f"no executable name is proven for {spec.name}, so its presence "
                f"cannot be verified before activation"
            )
            return gate
        if not found["supported"]:
            gate["state"] = "unprovable"
            gate["may_activate"] = False
            gate["detail"] = found["detail"]
            return gate

        gate["candidate_process_pids_before"] = found["pids"]
        gate["matches"] = found["matches"]
        if not found["pids"]:
            gate["state"] = "absent"
            gate["may_activate"] = False
            gate["detail"] = (
                f"no {' or '.join(spec.executables)} process is running"
            )
            return gate

        gate["state"] = "satisfied"
        return gate

    def _failure_hint(self, policy: str, gate: dict[str, Any]) -> str:
        if policy == config.ATTACH_ONLY:
            return ("; attach_only uses the Running Object Table only and never "
                    "performs COM activation, and Maxsurf does not publish its "
                    "Application object there -- use "
                    f"{config.ATTACH_POLICY_VAR}={config.ACTIVATE_EXISTING}")
        if policy == config.REQUIRE_PID:
            return ("; require_pid uses the Running Object Table only and never "
                    "performs COM activation")
        if gate.get("state") == "absent":
            return ("; activate_existing refused before any COM activation "
                    "because the application is not running -- open it first")
        if gate.get("state") == "unprovable":
            return ("; activate_existing could not prove the application is "
                    f"running ({gate.get('detail')}), so it refused before any "
                    "COM activation")
        return ""

    def _obtain(self, spec: ModuleSpec, progid: str, policy: str,
                gate: dict[str, Any]) -> tuple[Any, dict[str, Any], str | None]:
        """Get the COM object, recording exactly how it was obtained."""
        evidence: dict[str, Any] = {
            "method": ROT_ATTACH,
            "call": "win32com.client.GetActiveObject",
            "process_gate": gate["state"],
            "candidate_process_pids_before":
                list(gate["candidate_process_pids_before"]),
            "candidate_process_pids_after": [],
            "new_pids": [],
            "status": "clean",
            "rot_error": None,
        }
        try:
            app = win32com.client.GetActiveObject(progid)
            return app, evidence, None
        except Exception as exc:  # noqa: BLE001 - not in the ROT
            rot_error = str(exc)[:200]
        evidence["rot_error"] = rot_error

        if not gate["may_activate"]:
            return None, evidence, f"not in the running object table ({rot_error})"

        return self._activate(spec, progid, policy, gate, evidence, rot_error)

    def _activate(self, spec: ModuleSpec, progid: str, policy: str,
                  gate: dict[str, Any], evidence: dict[str, Any],
                  rot_error: str) -> tuple[Any, dict[str, Any], str | None]:
        """Perform COM activation and prove what it did to the process list."""
        evidence["method"] = COM_ACTIVATION
        before = tuple(gate["candidate_process_pids_before"])

        # Recorded before the activation call, so the process identity that
        # justified activating is on record even if activation then fails.
        _record("session_activation_intent", Classification.WRITE_STATE,
                Outcome.SUCCESS,
                {"module": spec.name, "progid": progid, "policy": policy,
                 "process_gate": gate["state"],
                 "candidate_process_pids_before": list(before),
                 "rot_error": rot_error},
                stage="intent")

        app = None
        errors: list[str] = []
        # EnsureDispatch is the historically verified call for reaching a
        # running Maxsurf; plain Dispatch is the fallback when makepy cannot
        # generate a wrapper.
        factories: list[tuple[str, Any]] = []
        if gencache is not None:
            factories.append(("win32com.client.gencache.EnsureDispatch",
                              lambda: gencache.EnsureDispatch(progid)))
        factories.append(("win32com.client.Dispatch",
                          lambda: win32com.client.Dispatch(progid)))
        for call, factory in factories:
            try:
                app = factory()
                evidence["call"] = call
                break
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{call}: {str(exc)[:200]}")

        after = processes.pids_of(spec.executables) if spec.executables else ()
        evidence["candidate_process_pids_after"] = list(after)
        new_pids = sorted(set(after) - set(before))
        evidence["new_pids"] = new_pids
        if new_pids:
            # Under launch_if_absent a new process is the point of the policy.
            # Under any other policy it means activation did something nobody
            # asked for, and that must not be reported in the same words.
            evidence["status"] = (
                PROCESS_CREATED if policy == config.LAUNCH_IF_ABSENT
                else UNEXPECTED_PROCESS
            )

        if app is None:
            _record("session_activation_failed", Classification.WRITE_STATE,
                    Outcome.FAILURE,
                    {"module": spec.name, "progid": progid, **evidence,
                     "errors": errors})
            return None, evidence, (
                f"not in the running object table ({rot_error}); "
                f"activation failed ({'; '.join(errors)})"
            )

        _record(
            "session_activation_unexpected_process" if new_pids
            else "session_activated",
            Classification.WRITE_STATE,
            Outcome.SUCCESS,
            {"module": spec.name, "progid": progid, **evidence},
        )
        return app, evidence, None

    def _describe_attached(self, session: MaxsurfSession, policy: str) -> None:
        """Collect identity evidence for a freshly attached object."""
        session.early_binding = _ensure_early_binding(session)
        version = probe_member(session.app, *VERSION_MEMBERS)
        session.version = (
            str(version["value"]) if version["status"] == "read" else None
        )
        session.version_source = version["source_member"]

        identity = verify_interface_identity(session.app, session.spec)
        session.interface_identity = identity
        if identity["status"] == IDENTITY_CONFLICTING:
            raise MaxsurfSafetyError(
                "connected object is not the requested Maxsurf application",
                module=session.module,
                progid=session.progid,
                conflicting_module=identity["conflicting_module"],
                identity_status=identity["status"],
                detail=identity["detail"],
            )

        host = resolve_com_host_pid(session.app)
        session.com_host_pid = host["pid"]
        session.com_host_pid_status = host["status"]
        session.com_host_pid_source = host["source"]
        session.com_host_pid_detail = host["detail"]
        if policy == config.REQUIRE_PID and session.com_host_pid is None:
            raise MaxsurfConnectionError(
                "the require_pid policy is active but the COM host process id "
                "could not be proven for this object; the process gate's "
                "candidate process ids are not a substitute, because they only "
                "prove a matching executable was running",
                module=session.module,
                progid=session.progid,
                candidate_process_pids=list(
                    session.activation.get("candidate_process_pids_before") or []
                ),
                detail=host["detail"],
            )

        state, detail = probe_liveness(session.app)
        session.liveness = state
        session.liveness_detail = detail
        session.liveness_checked_at = time.monotonic()
        session.refresh_design_identity()


def _ensure_early_binding(session: MaxsurfSession) -> str:
    """Generate the type-library wrapper for an already-attached object.

    Attaching with ``GetActiveObject`` yields a late-bound wrapper when makepy
    has never run for the type library, and constant discovery depends on the
    generated module. Building it from the attached object's own type library
    gets early binding without creating a second application instance, which is
    what ``EnsureDispatch(progid)`` would do. Best effort: a failure here costs
    discovery detail, not the connection.
    """
    if gencache is None or pythoncom is None:
        return "unavailable"
    oleobj = _oleobj_of(session.app)
    if oleobj is None or not hasattr(oleobj, "QueryInterface"):
        return "unavailable"
    try:
        dispatch = oleobj.QueryInterface(pythoncom.IID_IDispatch)
        type_info = dispatch.GetTypeInfo()
        library, _index = type_info.GetContainingTypeLib()
        attributes = library.GetLibAttr()
        gencache.EnsureModule(
            attributes[0], attributes[1], attributes[3], attributes[4]
        )
        session.app = win32com.client.Dispatch(dispatch)
    except Exception as exc:  # noqa: BLE001
        return f"unavailable ({str(exc)[:120]})"
    return "generated"


def _identity_mismatch(before: dict[str, Any],
                       after: dict[str, Any]) -> list[str]:
    """List the identity fields that changed, ignoring unknowns.

    A field that was never established on either side cannot prove a mismatch,
    so only fields known on both sides are compared.
    """
    mismatched = []
    for key in ("progid", "clsid", "com_host_pid", "design_path"):
        old, new = before.get(key), after.get(key)
        if old is None or new is None:
            continue
        if old != new:
            mismatched.append(key)
    return mismatched


def _classification_mutates(classification: str | None) -> bool:
    return classification in {
        Classification.WRITE_GEOMETRY,
        Classification.WRITE_FILE,
        Classification.WRITE_STATE,
        Classification.EXECUTE_ANALYSIS,
        Classification.DIAGNOSTIC,
    }


def describe_processes() -> dict[str, Any]:
    """Report which Maxsurf executables are running, without touching COM.

    This is the diagnostic that distinguishes "Maxsurf is not running" from
    "Maxsurf is running but not reachable through the Running Object Table" --
    a distinction that, unavailable, cost a working connection workflow.
    """
    report: dict[str, Any] = {}
    for name in config.allowed_modules():
        spec = MODULES.get(name)
        if spec is None or not spec.executables:
            continue
        found = processes.find(spec.executables)
        report[name] = {
            "executables": list(spec.executables),
            "running": bool(found["pids"]),
            "pids": list(found["pids"]),
            "inspectable": bool(found["supported"]),
            "detail": found["detail"],
        }
    return report


def _record(event: str, classification: str, outcome: str,
            summary: dict[str, Any], error: dict[str, Any] | None = None,
            stage: str = "complete") -> None:
    """Write a session lifecycle record on the internal channel.

    ``stage="intent"`` makes the record must-audit, so an operation that cannot
    be recorded is refused rather than performed unrecorded.
    """
    audit.get_log().record(
        stage=stage,
        tool=event,
        module="session",
        classification=classification,
        channel=Channel.INTERNAL,
        outcome=outcome,
        error=error,
        result_summary=summary,
    )


_manager = SessionManager()


def get_manager() -> SessionManager:
    return _manager


def set_manager(manager: SessionManager) -> SessionManager:
    """Replace the process-wide session manager. Used by tests."""
    global _manager
    previous = _manager
    _manager = manager
    return previous
