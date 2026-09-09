"""Shared offline test scaffolding.

Nothing here touches Bentley COM. Fake COM objects are recognised by the
object-path resolver purely because they carry ``_oleobj_``, which is the same
duck type every real pywin32 wrapper exposes.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest import mock

from maxsurf_mcp import audit, comthread, config, session


class ComError(Exception):
    """Stands in for ``pythoncom.com_error``, which carries an HRESULT."""

    def __init__(self, hresult: int, message: str = "com failure") -> None:
        super().__init__(hresult, message)
        self.hresult = hresult


#: RPC_S_SERVER_UNAVAILABLE: what a caller sees once Maxsurf has exited.
SERVER_GONE = 0x800706BA


class OleObjFake:
    """Stands in for ``_oleobj_``, the raw interface pointer on a wrapper.

    ``QueryInterface`` is the member the session layer uses for its liveness
    probe and for interface identity, so it is the one that has to behave like
    the real thing, including failing with an HRESULT.
    """

    def __init__(self, *, alive: bool = True, hresult: int = SERVER_GONE,
                 supported_iids: tuple[str, ...] = (),
                 declared_iid: str | None = None,
                 typeinfo_error: BaseException | None = None,
                 unwrappable_iids: tuple[str, ...] = ()) -> None:
        self.alive = alive
        self.hresult = hresult
        self.supported_iids = tuple(supported_iids)
        #: The IID this object's own type information reports, as a real
        #: ``IDispatch::GetTypeInfo`` would.
        self.declared_iid = declared_iid
        self.typeinfo_error = typeinfo_error
        #: IIDs that COM supports but pywin32 cannot wrap without an explicit
        #: wrap IID. Reproduces the exact quirk that made expected_ok false for
        #: Maxsurf: a bare QueryInterface raises TypeError from pywin32's
        #: interface registry *after* COM has already said yes.
        self.unwrappable_iids = tuple(unwrappable_iids)
        self.query_count = 0
        self.query_calls: list[tuple[str, str | None]] = []
        self.typeinfo_calls = 0

    def QueryInterface(self, iid: Any,
                       wrap_iid: Any = None) -> Any:  # noqa: N802 - COM naming
        self.query_count += 1
        text = str(iid)
        self.query_calls.append((text, None if wrap_iid is None else str(wrap_iid)))
        if not self.alive:
            raise ComError(self.hresult, "the object is no longer connected")
        if self.supported_iids and text not in self.supported_iids:
            raise ComError(0x80004002, f"no interface {text}")
        if text in self.unwrappable_iids and wrap_iid is None:
            raise TypeError(
                "There is no interface object registered that supports this IID")
        return self

    def GetTypeInfo(self, *_args: Any) -> Any:  # noqa: N802 - COM naming
        self.typeinfo_calls += 1
        if self.typeinfo_error is not None:
            raise self.typeinfo_error
        if self.declared_iid is None:
            raise ComError(0x80004001, "type information is not supported")
        return TypeInfoFake(self.declared_iid)


class TypeAttrFake:
    """The one field of ``TYPEATTR`` the identity check reads."""

    def __init__(self, iid: str, typekind: int = 4) -> None:
        self.iid = iid
        self.typekind = typekind


class TypeInfoFake:
    """Stands in for ``ITypeInfo`` for identity verification."""

    def __init__(self, iid: str) -> None:
        self.iid = iid

    def GetTypeAttr(self) -> TypeAttrFake:  # noqa: N802 - COM naming
        return TypeAttrFake(self.iid)


class ComFake:
    """A stand-in for a pywin32 COM wrapper.

    Members are supplied as keyword arguments. ``_oleobj_`` is what makes the
    resolver treat this as a COM object, exactly as it treats a real
    ``DispatchBaseClass`` or ``CDispatch`` instance.
    """

    def __init__(self, declared_iid: str | None = None, **members: Any) -> None:
        self._oleobj_ = OleObjFake(declared_iid=declared_iid)
        for name, value in members.items():
            setattr(self, name, value)

    def go_away(self, hresult: int = SERVER_GONE) -> None:
        """Simulate the hosting application exiting."""
        self._oleobj_.alive = False
        self._oleobj_.hresult = hresult


class ComCollectionFake(ComFake):
    """A COM collection supporting both ``Items(1)`` and ``Items[1]`` access."""

    def __init__(self, items: list[Any], by_name: dict[str, Any] | None = None,
                 subscript: bool = True, **members: Any) -> None:
        super().__init__(**members)
        self.items = list(items)
        self._by_name = dict(by_name or {})
        self._subscript = subscript

    @property
    def Count(self) -> int:
        return len(self.items)

    def __call__(self, index: Any) -> Any:
        if isinstance(index, str):
            return self._by_name[index]
        return self.items[int(index) - 1]

    def __getitem__(self, index: Any) -> Any:
        if not self._subscript:
            raise TypeError("subscripting is not supported")
        return self.__call__(index)


class SubscriptOnlyCollectionFake(ComFake):
    """A COM collection that is not callable, only subscriptable."""

    def __init__(self, items: list[Any], **members: Any) -> None:
        super().__init__(**members)
        self.items = list(items)

    def __getitem__(self, index: Any) -> Any:
        return self.items[int(index) - 1]


class FakeWin32Client:
    """Stands in for ``win32com.client`` at the attach/create boundary.

    Separating "already running" from "creatable" is the whole point: it is what
    makes it observable whether a policy attached to the user's application or
    started a new one.
    """

    def __init__(self, running: dict[str, Any] | None = None,
                 creatable: dict[str, Any] | None = None) -> None:
        self.running = dict(running or {})
        self.creatable = dict(creatable or {})
        self.get_active_calls: list[str] = []
        self.dispatch_calls: list[Any] = []

    def GetActiveObject(self, progid: str) -> Any:  # noqa: N802 - COM naming
        self.get_active_calls.append(progid)
        if progid not in self.running:
            raise ComError(0x800401E3, f"{progid} is not running")
        return self.running[progid]

    def Dispatch(self, progid_or_object: Any) -> Any:  # noqa: N802 - COM naming
        self.dispatch_calls.append(progid_or_object)
        if not isinstance(progid_or_object, str):
            return progid_or_object
        if progid_or_object not in self.creatable:
            raise ComError(0x80040154, f"{progid_or_object} is not registered")
        created = self.creatable[progid_or_object]
        self.running[progid_or_object] = created
        return created


class FakeWin32Namespace:
    """Provides the ``win32com.client`` attribute path the session layer uses."""

    def __init__(self, client: FakeWin32Client) -> None:
        self.client = client


class FakePythoncom:
    """The handful of ``pythoncom`` members the session layer touches."""

    IID_IUnknown = "{00000000-0000-0000-C000-000000000046}"
    IID_IDispatch = "{00020400-0000-0000-C000-000000000046}"
    IID_IOleWindow = "{00000114-0000-0000-C000-000000000046}"
    COINIT_APARTMENTTHREADED = 0x2

    def __init__(self, clsids: dict[str, str] | None = None) -> None:
        self.clsids = dict(clsids or {})

    def MakeIID(self, text: str) -> str:  # noqa: N802 - COM naming
        return text

    def CLSIDFromProgID(self, progid: str) -> str:  # noqa: N802 - COM naming
        if progid not in self.clsids:
            raise ComError(0x800401F3, f"{progid} is not registered")
        return self.clsids[progid]


class FakeGencache:
    """Stands in for ``win32com.client.gencache``.

    ``on_ensure`` is how a test makes activation have a side effect on the
    process table, which is the only way to reproduce "COM activation started a
    second Maxsurf" without starting one.
    """

    def __init__(self, creatable: dict[str, Any] | None = None,
                 on_ensure: Callable[[str], None] | None = None) -> None:
        self.creatable = dict(creatable or {})
        self.on_ensure = on_ensure
        self.ensure_calls: list[str] = []

    def EnsureDispatch(self, progid: str) -> Any:  # noqa: N802 - COM naming
        self.ensure_calls.append(progid)
        if self.on_ensure is not None:
            self.on_ensure(progid)
        if progid not in self.creatable:
            raise ComError(0x80040154, f"{progid} has no type library")
        return self.creatable[progid]

    def EnsureModule(self, *args: Any, **kwargs: Any) -> None:  # noqa: N802
        return None


class FakeProcesses:
    """Stands in for :mod:`maxsurf_mcp.processes` with a scriptable table.

    Lets a test say "MaxsurfModeler.exe is running as pid 6456" without any
    process existing, and observe exactly when the policy looked.
    """

    def __init__(self, table: dict[str, list[int]] | None = None,
                 supported: bool = True) -> None:
        self.table = {name.casefold(): list(pids)
                      for name, pids in (table or {}).items()}
        self.supported = supported
        self.find_calls: list[tuple[str, ...]] = []

    def available(self) -> bool:
        return self.supported

    def spawn(self, executable: str, pid: int) -> None:
        """Make a new process appear, as COM activation might."""
        self.table.setdefault(executable.casefold(), []).append(pid)

    def find(self, executables: Any) -> dict[str, Any]:
        wanted = tuple(executables)
        self.find_calls.append(wanted)
        names = {name.casefold() for name in wanted if name}
        result: dict[str, Any] = {
            "supported": self.supported,
            "requested": sorted(names),
            "matches": [],
            "pids": (),
            "inspected": len(self.table),
            "detail": None,
        }
        if not names:
            result["detail"] = "no executable name is known for this module"
            return result
        if not self.supported:
            result["detail"] = "pywin32 process inspection is unavailable"
            return result
        pids = sorted(pid for name in names for pid in self.table.get(name, ()))
        result["pids"] = tuple(pids)
        result["matches"] = [{"pid": pid, "name": sorted(names)[0],
                              "path": f"C:/fake/{sorted(names)[0]}"}
                             for pid in pids]
        return result

    def pids_of(self, executables: Any) -> tuple[int, ...]:
        return tuple(self.find(executables)["pids"])


class FakeWin32Process:
    """Stands in for ``win32process`` when a process id must be establishable."""

    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid

    def GetWindowThreadProcessId(self, handle: int):  # noqa: N802 - COM naming
        return (1, self.pid)


class ToolCollector:
    """Minimal stand-in for FastMCP that records what a module registers."""

    def __init__(self) -> None:
        self.registered: list[str] = []
        self.functions: dict[str, Callable[..., Any]] = {}

    def tool(self, **metadata) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorate(func: Callable[..., Any]) -> Callable[..., Any]:
            self.registered.append(func.__name__)
            self.functions[func.__name__] = func
            return func

        return decorate


class FakeComModule:
    """Stands in for ``pythoncom`` so apartment handling can be asserted.

    Records the exact initialise/uninitialise sequence and the thread each call
    was made from, which is the only way to prove the pairing without a real
    apartment.
    """

    COINIT_APARTMENTTHREADED = 0x2

    def __init__(self, fail_init: bool = False) -> None:
        self.events: list[tuple[str, Any]] = []
        self.threads: list[int] = []
        self.pumps = 0
        self.fail_init = fail_init
        self.lock = threading.Lock()

    def CoInitializeEx(self, flags: int) -> None:  # noqa: N802 - COM naming
        if self.fail_init:
            raise OSError("apartment refused")
        with self.lock:
            self.events.append(("CoInitializeEx", flags))
            self.threads.append(threading.get_ident())

    def CoUninitialize(self) -> None:  # noqa: N802 - COM naming
        with self.lock:
            self.events.append(("CoUninitialize", None))
            self.threads.append(threading.get_ident())

    def PumpWaitingMessages(self) -> None:  # noqa: N802 - COM naming
        with self.lock:
            self.pumps += 1

    def names(self) -> list[str]:
        with self.lock:
            return [name for name, _ in self.events]


class FakeWorker:
    """A COM worker that runs jobs inline and records them.

    Used by the default isolated test case so tool tests neither need a real
    apartment nor pay for a thread hand-off, while still proving which calls
    were routed to the worker at all. Tests about thread ownership use the real
    :class:`~maxsurf_mcp.comthread.ComWorker` instead.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.timeouts: list[float | None] = []

    def run(self, function: Callable[[], Any], *, timeout: float | None = None,
            description: str = "com_operation") -> Any:
        self.calls.append(description)
        self.timeouts.append(timeout)
        return function()

    def is_worker_thread(self) -> bool:
        return False

    def is_running(self) -> bool:
        return True

    def thread_ident(self) -> int | None:
        return None

    def start(self) -> None:
        return None

    def shutdown(self, timeout: float | None = None) -> dict[str, Any]:
        return self.describe()

    def describe(self) -> dict[str, Any]:
        return {"worker": "fake-inline", "running": True, "healthy": True,
                "completed": len(self.calls)}


class IsolatedEnvTestCase(unittest.TestCase):
    """Base case that isolates server policy, the sandbox, and the audit log.

    Every subclass gets a private temporary workspace root and audit directory,
    with both optional tiers switched off, so no test can depend on developer
    environment variables or write into the repository.
    """

    #: Overridden by subclasses that need a tier enabled.
    env_overrides: dict[str, str] = {}

    def setUp(self) -> None:
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="maxsurf_mcp_test_"))
        self.workspace_root = self.tmp / "workspace"
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.audit_dir = self.tmp / "audit"

        self._saved_env = {
            key: os.environ.get(key)
            for key in (
                config.ENABLE_DIAGNOSTICS_VAR,
                config.ENABLE_LEGACY_VAR,
                config.WORKSPACE_ROOTS_VAR,
                config.AUDIT_DIR_VAR,
                config.AUDIT_ENABLED_VAR,
                config.ATTACH_POLICY_VAR,
                config.ALLOWED_MODULES_VAR,
                config.COM_TIMEOUT_VAR,
                config.ANALYSIS_TIMEOUT_VAR,
                config.SHUTDOWN_TIMEOUT_VAR,
                config.LIVENESS_INTERVAL_VAR,
            )
        }
        for key in (config.ENABLE_DIAGNOSTICS_VAR, config.ENABLE_LEGACY_VAR,
                    config.ATTACH_POLICY_VAR, config.ALLOWED_MODULES_VAR,
                    config.COM_TIMEOUT_VAR, config.ANALYSIS_TIMEOUT_VAR,
                    config.SHUTDOWN_TIMEOUT_VAR,
                    config.LIVENESS_INTERVAL_VAR):
            os.environ.pop(key, None)
        os.environ[config.WORKSPACE_ROOTS_VAR] = str(self.workspace_root)
        os.environ[config.AUDIT_DIR_VAR] = str(self.audit_dir)
        os.environ[config.AUDIT_ENABLED_VAR] = "1"
        os.environ.update(self.env_overrides)

        self.audit_log = audit.AuditLog(directory=self.audit_dir)
        self._saved_log = audit.set_log(self.audit_log)

        # A fresh session manager per test: sessions are process-wide state, and
        # one test's cached connection must never satisfy another's.
        self.sessions = session.SessionManager()
        self._saved_manager = session.set_manager(self.sessions)

        # COM work runs inline unless a test asks for the real STA thread.
        self.worker = FakeWorker()
        self._saved_worker = comthread.set_worker(self.worker)

    def use_real_worker(self, com_module: Any | None = None) -> Any:
        """Install a genuine STA worker for this test and stop it afterwards."""
        worker = comthread.ComWorker(name="test-com-sta", com_module=com_module)
        comthread.set_worker(worker)
        self.worker = worker
        self.addCleanup(worker.shutdown, 5.0)
        return worker

    def install_com_environment(
        self,
        running: dict[str, Any] | None = None,
        creatable: dict[str, Any] | None = None,
        clsids: dict[str, str] | None = None,
        process: Any = None,
        gencache: Any = None,
        processes: Any = None,
    ) -> FakeWin32Client:
        """Replace the session layer's COM bindings with controllable fakes.

        ``gencache`` defaults to None so the early-binding step is skipped: it
        is a best-effort optimisation and has its own tests. ``processes``
        defaults to an empty process table, so a test that does not set one up
        sees "Maxsurf is not running" rather than the real machine's state.
        """
        client = FakeWin32Client(running=running, creatable=creatable)
        patches = {
            "win32com": FakeWin32Namespace(client),
            "pythoncom": FakePythoncom(clsids=clsids),
            "gencache": gencache,
            "win32process": process,
            "processes": processes if processes is not None else FakeProcesses(),
        }
        for name, value in patches.items():
            patcher = mock.patch.object(session, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return client

    def set_env(self, name: str, value: str) -> None:
        """Set an environment variable for the duration of the test."""
        if name not in self._saved_env:
            self._saved_env[name] = os.environ.get(name)
        os.environ[name] = value

    def tearDown(self) -> None:
        comthread.set_worker(self._saved_worker)
        session.set_manager(self._saved_manager)
        audit.set_log(self._saved_log)
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    # -- audit helpers -----------------------------------------------------
    def audit_records(self) -> list[dict[str, Any]]:
        """Return every audit record written during the test, in order."""
        records: list[dict[str, Any]] = []
        if not self.audit_dir.exists():
            return records
        for path in sorted(self.audit_dir.glob("*.jsonl")):
            records.extend(audit.read_records(path))
        return records

    def audit_files(self) -> list[Path]:
        if not self.audit_dir.exists():
            return []
        return sorted(self.audit_dir.glob("*.jsonl"))

    def records_for(self, tool: str) -> list[dict[str, Any]]:
        return [r for r in self.audit_records() if r.get("tool") == tool]

    # -- workspace helpers -------------------------------------------------
    def workspace_file(self, name: str, contents: str = "design") -> Path:
        path = self.workspace_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
        return path

    def workspace_path(self, name: str) -> Path:
        return self.workspace_root / name
