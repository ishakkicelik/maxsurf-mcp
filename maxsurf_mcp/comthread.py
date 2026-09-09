"""A single owned Windows STA thread that executes every COM operation.

Before this module existed, COM ran on whichever thread FastMCP happened to
call a tool from, and ``CoInitialize`` was invoked per thread with no matching
``CoUninitialize``. That worked only by accident: FastMCP calls synchronous
tools inline on the event loop, so in practice one thread did all the work.

Now one thread owns the apartment for the life of the process:

* it calls ``CoInitializeEx(COINIT_APARTMENTTHREADED)`` on entry and
  ``CoUninitialize`` in a ``finally``, so the apartment is always torn down;
* every COM operation is submitted to it and executed one at a time, so
  concurrent MCP calls serialise instead of racing;
* no raw COM pointer is ever touched from another thread;
* the idle loop pumps the Windows message queue, which an STA is required to do;
* shutdown is explicit and joins the thread.

**Timeouts do not cancel anything.** COM offers no safe way to abort an
in-flight call, and forcing one would leave Maxsurf holding a half-applied
operation. When a wait expires, the job keeps running on the worker and the
worker enters the ``abandoned`` state: further submissions are refused with a
clear explanation rather than silently queueing behind a call that may never
return. The state clears if and when the abandoned job finishes.
"""

from __future__ import annotations

import contextvars
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from . import audit, config
from .audit import Channel, Classification, Outcome
from .errors import MaxsurfError, MaxsurfSafetyError, MaxsurfTimeoutError

try:  # pragma: no cover - exercised on Windows only
    import pythoncom
except ImportError:  # importable on non-Windows so the suite can be read
    pythoncom = None

#: How long the idle loop waits for work before pumping messages again. An STA
#: that hosts no COM server has nothing latency-sensitive in its queue, so a
#: coarse interval keeps the thread almost entirely asleep.
PUMP_INTERVAL_S = 0.05

_SHUTDOWN = object()


class _Job:
    """One unit of work owned by the worker thread."""

    __slots__ = ("function", "context", "description", "done", "result",
                 "error", "submitted_at", "started_at", "finished_at",
                 "abandoned")

    def __init__(self, function: Callable[[], Any], description: str) -> None:
        self.function = function
        # Copy the caller's context so audit state such as the request id and
        # classification travels with the job onto the worker thread.
        self.context = contextvars.copy_context()
        self.description = description
        self.done = threading.Event()
        self.result: Any = None
        self.error: BaseException | None = None
        self.submitted_at = time.monotonic()
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.abandoned = False


class ComWorker:
    """Owns one STA thread and serialises COM work onto it."""

    def __init__(self, name: str = "maxsurf-com-sta",
                 com_module: Any | None = None) -> None:
        self._name = name
        # Injectable so apartment initialisation can be tested without a real
        # apartment. Defaults to the real pythoncom.
        self._com = com_module if com_module is not None else pythoncom
        self._queue: queue.Queue[Any] = queue.Queue()
        self._thread: threading.Thread | None = None
        # Reentrant: describe() takes the lock and is called from inside other
        # locked sections such as shutdown().
        self._lock = threading.RLock()
        self._started = threading.Event()
        self._start_error: BaseException | None = None
        self._stopping = False
        self._abandoned: list[_Job] = []
        self._apartment_initialised = False
        self._apartment_uninitialised = False
        self._completed = 0

    # -- lifecycle ---------------------------------------------------------
    @property
    def name(self) -> str:
        return self._name

    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def thread_ident(self) -> int | None:
        thread = self._thread
        return thread.ident if thread else None

    def is_worker_thread(self) -> bool:
        """True when the caller is already executing on the worker thread."""
        return threading.get_ident() == self.thread_ident()

    def start(self) -> None:
        """Start the worker thread and wait until its apartment is ready."""
        with self._lock:
            if self._stopping:
                raise MaxsurfSafetyError(
                    "the COM worker is shutting down and cannot be restarted",
                    worker=self._name,
                )
            if self.is_running():
                return
            if self._com is None:
                raise MaxsurfSafetyError(
                    "pywin32 is unavailable, so no COM apartment can be created",
                    worker=self._name,
                )
            self._started.clear()
            self._start_error = None
            self._thread = threading.Thread(
                target=self._run, name=self._name, daemon=True
            )
            self._thread.start()
        self._started.wait(timeout=30.0)
        if self._start_error is not None:
            raise MaxsurfSafetyError(
                "the COM apartment could not be initialised",
                worker=self._name,
                reason=str(self._start_error),
            )
        self._record("com_worker_start", Outcome.SUCCESS,
                     result_summary=self.describe())

    def _run(self) -> None:
        """Thread body. Owns the apartment for its entire lifetime."""
        try:
            self._co_initialize()
            self._apartment_initialised = True
        except BaseException as exc:  # noqa: BLE001 - reported to start()
            self._start_error = exc
            self._started.set()
            return
        self._started.set()
        try:
            self._loop()
        finally:
            try:
                self._com.CoUninitialize()
            finally:
                self._apartment_uninitialised = True

    def _co_initialize(self) -> None:
        """Enter an explicitly single-threaded apartment."""
        flags = getattr(self._com, "COINIT_APARTMENTTHREADED", 0x2)
        self._com.CoInitializeEx(flags)

    def _loop(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=PUMP_INTERVAL_S)
            except queue.Empty:
                self._pump()
                continue
            if item is _SHUTDOWN:
                self._queue.task_done()
                return
            self._execute(item)
            self._queue.task_done()

    def _pump(self) -> None:
        """Service the apartment's message queue.

        An STA must pump, otherwise cross-apartment marshalling and any
        window-based notification Maxsurf posts would stall.
        """
        pump = getattr(self._com, "PumpWaitingMessages", None)
        if pump is None:
            return
        try:
            pump()
        except Exception:  # noqa: BLE001 - a pump failure must not kill the worker
            pass

    def _execute(self, job: _Job) -> None:
        job.started_at = time.monotonic()
        try:
            job.result = job.context.run(job.function)
        except BaseException as exc:  # noqa: BLE001 - relayed to the caller
            job.error = exc
        finally:
            job.finished_at = time.monotonic()
            self._completed += 1
            job.done.set()
            if job.abandoned:
                self._release_abandoned(job)

    def _release_abandoned(self, job: _Job) -> None:
        with self._lock:
            if job in self._abandoned:
                self._abandoned.remove(job)
        waited = (job.finished_at or 0) - (job.started_at or 0)
        self._record(
            "com_abandoned_job_finished",
            Outcome.SUCCESS if job.error is None else Outcome.FAILURE,
            result_summary={
                "operation": job.description,
                "ran_for_s": round(waited, 3),
                "still_blocking": len(self._abandoned),
            },
        )

    def shutdown(self, timeout: float | None = None) -> dict[str, Any]:
        """Stop the worker and wait for the apartment to be torn down."""
        with self._lock:
            self._stopping = True
            thread = self._thread
            if thread is None or not thread.is_alive():
                self._thread = None
                return {**self.describe(), "joined": True}
        budget = config.shutdown_timeout() if timeout is None else timeout
        self._queue.put(_SHUTDOWN)
        thread.join(timeout=budget)
        joined = not thread.is_alive()
        with self._lock:
            if joined:
                self._thread = None
        summary = self.describe()
        summary["joined"] = joined
        self._record(
            "com_worker_shutdown",
            Outcome.SUCCESS if joined else Outcome.FAILURE,
            result_summary=summary,
        )
        return summary

    # -- submission --------------------------------------------------------
    def run(self, function: Callable[[], Any], *, timeout: float | None = None,
            description: str = "com_operation") -> Any:
        """Execute ``function`` on the worker thread and return its result.

        Executes inline when the caller is already the worker thread, which is
        what makes nesting safe: a tool body running on the worker can call
        :func:`maxsurf_mcp.com.connect` without deadlocking on itself.
        """
        if self.is_worker_thread():
            return function()

        self._refuse_if_blocked(description)
        if not self.is_running():
            self.start()

        budget = config.com_timeout() if timeout is None else timeout
        job = _Job(function, description)
        self._queue.put(job)

        if not job.done.wait(timeout=budget):
            self._abandon(job, budget)

        # Propagate audit state the job set while running in its own context.
        audit.apply_scope(audit.capture_scope(job.context))

        if job.error is not None:
            raise job.error
        return job.result

    def _refuse_if_blocked(self, description: str) -> None:
        with self._lock:
            blocking = list(self._abandoned)
        if not blocking:
            return
        oldest = min(job.started_at or job.submitted_at for job in blocking)
        raise MaxsurfSafetyError(
            "the COM worker is still blocked by an operation whose wait was "
            "abandoned; COM cannot be interrupted safely, so no further COM "
            "work is accepted until Maxsurf returns or the server is restarted",
            worker=self._name,
            requested=description,
            blocked_by=[job.description for job in blocking],
            blocked_for_s=round(time.monotonic() - oldest, 1),
        )

    def _abandon(self, job: _Job, budget: float) -> None:
        job.abandoned = True
        started = job.started_at is not None
        with self._lock:
            self._abandoned.append(job)
        state = "still_running_in_maxsurf" if started else "queued_not_started"
        self._record(
            "com_timeout",
            Outcome.FAILURE,
            result_summary={
                "operation": job.description,
                "timeout_s": budget,
                "operation_state": state,
            },
        )
        raise MaxsurfTimeoutError(
            f"COM operation {job.description!r} did not return within "
            f"{budget:g}s; the wait was abandoned but the operation was NOT "
            f"cancelled",
            operation=job.description,
            timeout_s=budget,
            operation_state=state,
            worker=self._name,
        )

    # -- introspection -----------------------------------------------------
    def describe(self) -> dict[str, Any]:
        """Return the worker's observable state for status and audit records."""
        with self._lock:
            blocking = [job.description for job in self._abandoned]
        return {
            "worker": self._name,
            "running": self.is_running(),
            "thread_ident": self.thread_ident(),
            "apartment": "COINIT_APARTMENTTHREADED",
            "apartment_initialised": self._apartment_initialised,
            "apartment_uninitialised": self._apartment_uninitialised,
            "queued": self._queue.qsize(),
            "completed": self._completed,
            "abandoned_operations": blocking,
            "healthy": self.is_running() and not blocking,
        }

    def _record(self, event: str, outcome: str, **fields: Any) -> None:
        """Write a worker lifecycle record. Never allowed to break the worker."""
        try:
            audit.get_log().record(
                stage="complete",
                tool=event,
                module="comthread",
                classification=Classification.READ,
                channel=Channel.INTERNAL,
                outcome=outcome,
                **fields,
            )
        except MaxsurfError:
            pass


_worker: ComWorker | None = None
_worker_lock = threading.Lock()


def get_worker() -> ComWorker:
    """Return the process-wide COM worker, starting it on first use."""
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = ComWorker()
    if not _worker.is_running():
        _worker.start()
    return _worker


def set_worker(worker: ComWorker | None) -> ComWorker | None:
    """Replace the process-wide worker. Used by tests."""
    global _worker
    with _worker_lock:
        previous = _worker
        _worker = worker
    return previous


def peek_worker() -> ComWorker | None:
    """Return the current worker without starting one."""
    return _worker


def run(function: Callable[[], Any], *, timeout: float | None = None,
        description: str = "com_operation") -> Any:
    """Execute ``function`` on the process-wide COM worker."""
    return get_worker().run(function, timeout=timeout, description=description)


def shutdown(timeout: float | None = None) -> dict[str, Any] | None:
    """Shut down the process-wide worker if one was ever started."""
    global _worker
    with _worker_lock:
        worker = _worker
        _worker = None
    if worker is None:
        return None
    return worker.shutdown(timeout=timeout)


def describe() -> dict[str, Any]:
    """Describe the worker without starting one."""
    if _worker is None:
        return {"worker": None, "running": False, "started": False}
    summary = _worker.describe()
    summary["started"] = True
    return summary
