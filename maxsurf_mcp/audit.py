"""Append-only, tamper-evident audit log for every MCP operation.

Nothing this server did used to be provable after the fact. Every tool now
emits at least one JSONL record to ``runtime/audit/<UTC date>.jsonl``.

Two properties make the log evidence rather than decoration:

* **Hash chain.** Each record carries the ``record_hash`` of its predecessor,
  so a deleted or edited line breaks verification (see :func:`verify_file`).
* **Intent before action.** Mutating and diagnostic operations write an
  ``intent`` record *before* the operation runs and a ``complete`` record
  after. If the intent record cannot be written the operation is refused, so
  an unprovable write never happens.

Read-only operations emit a single ``complete`` record and never block on a
logging failure, because refusing a read on a full disk would be worse than
losing one read record.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config
from .errors import MaxsurfError, MaxsurfSafetyError


class Channel:
    """Where an operation happened. Keeps COM work distinguishable from shell work."""

    MCP_COM = "mcp_com"
    LOCAL = "local"
    UI = "ui"
    INTERNAL = "internal"

    ALL = frozenset({MCP_COM, LOCAL, UI, INTERNAL})


class Classification:
    """What an operation is allowed to affect."""

    READ = "READ"
    WRITE_GEOMETRY = "WRITE_GEOMETRY"
    WRITE_FILE = "WRITE_FILE"
    WRITE_STATE = "WRITE_STATE"
    EXECUTE_ANALYSIS = "EXECUTE_ANALYSIS"
    DIAGNOSTIC = "DIAGNOSTIC"

    ALL = frozenset({
        READ, WRITE_GEOMETRY, WRITE_FILE, WRITE_STATE,
        EXECUTE_ANALYSIS, DIAGNOSTIC,
    })

    #: Classifications that must be provable. An operation in this set is
    #: refused when its intent record cannot be persisted.
    MUST_AUDIT = frozenset({
        WRITE_GEOMETRY, WRITE_FILE, WRITE_STATE,
        EXECUTE_ANALYSIS, DIAGNOSTIC,
    })


class Outcome:
    SUCCESS = "success"
    FAILURE = "failure"
    BLOCKED = "blocked"


GENESIS_HASH = "sha256:genesis"

#: Argument keys whose values are never written to the log.
_REDACT_KEYS = ("password", "passwd", "secret", "token", "credential", "apikey",
                "api_key", "licence_key", "license_key")

#: Long argument strings are replaced by a digest so a bulk control-net edit
#: cannot bloat the log while staying verifiable.
_MAX_ARG_TEXT = 512

_design_path: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "maxsurf_audit_design_path", default=None
)
_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "maxsurf_audit_request_id", default=None
)
_classification: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "maxsurf_audit_classification", default=None
)
#: True only inside a tool call scope. Gates writing state back from a worker
#: job so audit context can never outlive the call it belongs to.
_scope_active: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "maxsurf_audit_scope_active", default=False
)

_SESSION_ID = secrets.token_hex(8)


def new_id() -> str:
    """Return a time-sortable identifier."""
    return f"{int(time.time() * 1000):013d}-{secrets.token_hex(6)}"


def set_design_path(path: str | None) -> None:
    """Record the design a tool is acting on, for the current call."""
    _design_path.set(str(path) if path is not None else None)


def current_design_path() -> str | None:
    return _design_path.get()


def set_request_id(request_id: str | None) -> None:
    _request_id.set(request_id)


def current_request_id() -> str | None:
    return _request_id.get()


def current_classification() -> str | None:
    """Return the classification of the tool call in progress, if any.

    Lets a shared helper such as the session layer see whether its caller is
    performing a read or a mutation, without every tool having to declare it a
    second time.
    """
    return _classification.get()


_SCOPE_VARS = (_design_path, _request_id, _classification, _scope_active)


def begin_call_scope(request_id: str, classification: str) -> tuple[Any, ...]:
    """Open a per-call scope for the audit context variables."""
    return (
        _design_path.set(None),
        _request_id.set(request_id),
        _classification.set(classification),
        _scope_active.set(True),
    )


def end_call_scope(tokens: tuple[Any, ...]) -> None:
    """Close a scope opened by :func:`begin_call_scope`."""
    for var, token in zip(_SCOPE_VARS, tokens):
        var.reset(token)


def capture_scope(context: contextvars.Context) -> dict[str, Any]:
    """Extract audit state that a job set while running in its own context.

    The COM worker runs each job in a copied context, so values a tool sets
    there are invisible to the thread that is waiting for it. This lifts them
    back out so the completion record still names the design that was touched.
    """
    return {"design_path": context.get(_design_path, None)}


def apply_scope(values: dict[str, Any]) -> None:
    """Apply state returned by :func:`capture_scope` to the current context.

    A no-op outside a tool call scope: without one there is nothing the state
    belongs to and nothing that will reset it, so applying it would let one
    call's design path bleed into the next.
    """
    if not _scope_active.get():
        return
    design_path = values.get("design_path")
    if design_path is not None:
        _design_path.set(design_path)


def _redact(value: Any, *, key: str = "") -> Any:
    lowered = key.casefold()
    if any(marker in lowered for marker in _REDACT_KEYS):
        return "[redacted]"
    if isinstance(value, str) and len(value) > _MAX_ARG_TEXT:
        digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()
        return {
            "truncated": True,
            "length": len(value),
            "sha256": digest,
            "head": value[:_MAX_ARG_TEXT],
        }
    if isinstance(value, dict):
        return {str(k): _redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    return f"<{type(value).__name__}>"


def redact_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return a log-safe copy of a tool's arguments."""
    return {str(k): _redact(v, key=str(k)) for k, v in arguments.items()}


#: Bytes read from the end of the file when recovering the chain head. Large
#: enough that one read normally suffices; widened automatically if not.
_TAIL_CHUNK = 8192

try:  # pragma: no cover - platform specific
    import msvcrt
except ImportError:
    msvcrt = None

try:  # pragma: no cover - platform specific
    import fcntl
except ImportError:
    fcntl = None


def _lock_path(path: Path) -> Path:
    """Return the sidecar lock file for an audit file.

    A sidecar rather than a range lock on the JSONL itself, so the append handle
    is never entangled with the lock and the log file stays a plain, readable,
    append-only artefact.
    """
    return path.with_suffix(path.suffix + ".lock")


def _try_acquire(handle: Any) -> bool:
    """Attempt to take the OS lock without blocking."""
    if msvcrt is not None:
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    if fcntl is not None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True
    # No interprocess locking primitive: the in-process lock is all there is.
    return True


def _release(handle: Any) -> None:
    if msvcrt is not None:
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return
    if fcntl is not None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass


class _InterprocessLock:
    """Serialises audit appends across processes, not just across threads.

    Held across the whole read-tail/compute-hash/append/flush/fsync sequence, so
    no other writer can interleave between one process reading the chain head
    and writing the record that extends it.
    """

    def __init__(self, path: Path, timeout: float | None = None) -> None:
        self._path = path
        self._timeout = timeout
        self._handle: Any = None

    def __enter__(self) -> _InterprocessLock:
        budget = (config.audit_lock_timeout() if self._timeout is None
                  else self._timeout)
        self._handle = self._path.open("a+b")
        deadline = time.monotonic() + budget
        delay = 0.001
        while True:
            if _try_acquire(self._handle):
                return self
            if time.monotonic() >= deadline:
                self._handle.close()
                self._handle = None
                raise MaxsurfSafetyError(
                    "could not acquire the audit lock, so the record cannot be "
                    "chained safely and the operation is refused",
                    lock_file=str(self._path),
                    waited_s=round(budget, 3),
                )
            time.sleep(delay)
            delay = min(delay * 2, 0.05)

    def __exit__(self, *_exc: Any) -> None:
        if self._handle is not None:
            _release(self._handle)
            self._handle.close()
            self._handle = None


def _canonical(record: dict[str, Any]) -> bytes:
    return json.dumps(
        record, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def _hash_record(record: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(record)).hexdigest()


class AuditLog:
    """Append-only JSONL sink with a per-file hash chain."""

    def __init__(self, directory: Path | str | None = None,
                 enabled: bool | None = None) -> None:
        self._directory = Path(directory) if directory is not None else None
        self._enabled = enabled
        self._lock = threading.Lock()
        self._last_hash: str | None = None
        self._last_file: Path | None = None

    @property
    def directory(self) -> Path:
        return self._directory if self._directory is not None else config.audit_dir()

    @property
    def enabled(self) -> bool:
        return config.audit_enabled() if self._enabled is None else self._enabled

    def current_file(self) -> Path:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.directory / f"{day}.jsonl"

    def _tail_hash(self, path: Path) -> str:
        """Read the chain head from the file itself.

        Called on *every* append while the interprocess lock is held, never
        cached. A cached head is only valid if this process is the sole writer,
        and it is not: two MCP server processes share one daily file, and each
        believing its own head was authoritative is what broke the chain.

        Reads backwards in chunks so the cost does not grow with the file.
        """
        try:
            size = path.stat().st_size
        except OSError:
            return GENESIS_HASH
        if size == 0:
            return GENESIS_HASH

        window = _TAIL_CHUNK
        with path.open("rb") as handle:
            while True:
                start = max(0, size - window)
                handle.seek(start)
                block = handle.read(size - start)
                lines = [line for line in block.split(b"\n") if line.strip()]
                # Without a newline before the first retained line, that line
                # may be truncated, so widen the window and look again.
                if lines and (start == 0 or block.startswith(b"\n")
                              or len(lines) > 1):
                    last = lines[-1]
                    break
                if start == 0:
                    return GENESIS_HASH
                window *= 4
        try:
            return str(json.loads(last.decode("utf-8"))["record_hash"])
        except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
            raise MaxsurfSafetyError(
                "the audit file's last record is unreadable, so the hash chain "
                "cannot be continued without hiding the damage",
                audit_file=str(path),
                reason=str(exc)[:200],
            ) from exc

    def _append(self, record: dict[str, Any]) -> dict[str, Any]:
        path = self.current_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Thread lock first, then the interprocess lock: threads of this process
        # queue locally instead of contending for the OS lock.
        with self._lock, _InterprocessLock(_lock_path(path)):
            record["prev_hash"] = self._tail_hash(path)
            record["record_hash"] = _hash_record(record)
            line = json.dumps(record, ensure_ascii=False, default=str)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._last_hash = record["record_hash"]
            self._last_file = path
        return record

    def write(self, record: dict[str, Any], *, must_audit: bool) -> dict[str, Any] | None:
        """Persist one record. Raises for must-audit records that cannot be written."""
        if not self.enabled:
            if must_audit:
                raise MaxsurfSafetyError(
                    "auditing is disabled, so this operation cannot be proven "
                    "and is refused",
                    classification=record.get("classification"),
                    tool=record.get("tool"),
                )
            return None
        try:
            return self._append(record)
        except MaxsurfError:
            raise
        except Exception as exc:  # noqa: BLE001 - disk/permission failure
            if must_audit:
                raise MaxsurfSafetyError(
                    "audit record could not be written; refusing the operation",
                    tool=record.get("tool"),
                    classification=record.get("classification"),
                    audit_error=str(exc),
                ) from exc
            return None

    def record(
        self,
        *,
        stage: str,
        tool: str,
        module: str | None,
        classification: str,
        channel: str = Channel.MCP_COM,
        arguments: dict[str, Any] | None = None,
        outcome: str | None = None,
        error: dict[str, Any] | None = None,
        duration_ms: float | None = None,
        result_summary: dict[str, Any] | None = None,
        design_path: str | None = None,
        audit_id: str | None = None,
        warnings: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Build and persist a single audit record."""
        if classification not in Classification.ALL:
            raise MaxsurfSafetyError(
                "unknown operation classification", classification=classification
            )
        if channel not in Channel.ALL:
            raise MaxsurfSafetyError("unknown audit channel", channel=channel)

        record: dict[str, Any] = {
            "audit_id": audit_id or new_id(),
            "stage": stage,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(
                timespec="microseconds"
            ),
            "monotonic_ns": time.monotonic_ns(),
            "channel": channel,
            "session_id": _SESSION_ID,
            "request_id": current_request_id(),
            "tool": tool,
            "module": module,
            "classification": classification,
            "arguments": redact_arguments(arguments or {}),
            "design_path": design_path if design_path is not None else current_design_path(),
            "outcome": outcome,
            "error": error,
            "duration_ms": duration_ms,
            "result_summary": result_summary,
            "policy": {
                "diagnostics_enabled": config.diagnostics_enabled(),
                "legacy_enabled": config.legacy_enabled(),
            },
            "warnings": warnings or [],
        }
        if extra:
            record.update(extra)
        must_audit = (
            classification in Classification.MUST_AUDIT and stage == "intent"
        )
        return self.write(record, must_audit=must_audit)


_default_log = AuditLog()


def get_log() -> AuditLog:
    """Return the process-wide audit log."""
    return _default_log


def set_log(log: AuditLog) -> AuditLog:
    """Replace the process-wide audit log. Used by tests."""
    global _default_log
    previous = _default_log
    _default_log = log
    return previous


def error_payload(exc: BaseException) -> dict[str, Any]:
    """Return a log-safe description of an exception."""
    if isinstance(exc, MaxsurfError):
        payload = exc.to_dict()
        payload["details"] = redact_arguments(payload.get("details") or {})
        return payload
    return {
        "code": "unexpected_error",
        "type": type(exc).__name__,
        "message": str(exc)[:1000],
        "details": {},
    }


def read_records(path: Path | str) -> list[dict[str, Any]]:
    """Read every record from one audit file."""
    file_path = Path(path)
    if not file_path.exists():
        return []
    records = []
    with file_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


def verify_file(path: Path | str) -> dict[str, Any]:
    """Verify the hash chain of one audit file.

    Returns the first broken index, if any, so tampering is detectable without
    trusting the log itself.
    """
    records = read_records(path)
    expected = GENESIS_HASH
    for index, record in enumerate(records):
        stored_hash = record.get("record_hash")
        if record.get("prev_hash") != expected:
            return {"valid": False, "reason": "prev_hash_mismatch",
                    "index": index, "records": len(records)}
        body = {k: v for k, v in record.items() if k != "record_hash"}
        if _hash_record(body) != stored_hash:
            return {"valid": False, "reason": "record_hash_mismatch",
                    "index": index, "records": len(records)}
        expected = stored_hash
    return {"valid": True, "records": len(records), "head": expected}
