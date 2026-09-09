"""The single chokepoint every MCP tool passes through.

One decorator applies four things that used to be missing or inconsistent:

* **Policy gating.** A tool in a disabled tier (raw diagnostics, quarantined
  legacy) is refused before it runs.
* **Audit.** Mutating and diagnostic tools write an intent record before the
  operation and a completion record after; reads write one record. Refusals
  are recorded as ``blocked`` so the log shows what was attempted, not only
  what succeeded.
* **Error normalisation.** Any escaping exception becomes a typed
  :class:`~maxsurf_mcp.errors.MaxsurfError`, so FastMCP always reports
  ``isError=True`` instead of a tool returning a success payload that happens
  to contain an ``error`` key.
* **Classification.** Every tool declares what it may affect, and the
  declaration is visible in its description and in the audit record.
* **Thread ownership.** Tool bodies that talk to COM are executed on the single
  owned STA thread, so COM is serialised and no pointer is ever touched from
  the thread that happened to deliver the request.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import inspect
import json
import time
from collections.abc import Callable
from typing import Any

from . import audit, comthread, config
from .audit import Channel, Classification, Outcome
from .errors import (
    MaxsurfCOMError,
    MaxsurfError,
    MaxsurfSafetyError,
    MaxsurfValidationError,
)

#: Tier names that a tool may require before it is allowed to run.
TIER_DIAGNOSTICS = "diagnostics"
TIER_LEGACY = "legacy"

_TIER_CHECKS: dict[str, Callable[[], bool]] = {
    TIER_DIAGNOSTICS: config.diagnostics_enabled,
    TIER_LEGACY: config.legacy_enabled,
}

_TIER_VARS = {
    TIER_DIAGNOSTICS: config.ENABLE_DIAGNOSTICS_VAR,
    TIER_LEGACY: config.ENABLE_LEGACY_VAR,
}

#: Populated by the decorator so the server can register tools by tier without
#: each module repeating the policy logic.
REGISTRY: dict[str, dict[str, Any]] = {}


def tier_enabled(tier: str | None) -> bool:
    if tier is None:
        return True
    check = _TIER_CHECKS.get(tier)
    if check is None:
        raise MaxsurfSafetyError("unknown tool tier", tier=tier)
    return check()


def _summarize(result: Any) -> dict[str, Any]:
    """Describe a tool result without copying it into the log."""
    if not isinstance(result, str):
        return {"result_type": type(result).__name__}
    summary: dict[str, Any] = {
        "payload_bytes": len(result),
        "payload_sha256": hashlib.sha256(result.encode("utf-8")).hexdigest(),
    }
    try:
        parsed = json.loads(result)
    except ValueError:
        return summary
    if isinstance(parsed, dict):
        summary["payload_keys"] = sorted(str(key) for key in parsed)
    return summary


def _bind_arguments(signature: inspect.Signature, args: tuple[Any, ...],
                    kwargs: dict[str, Any]) -> tuple[dict[str, Any], TypeError | None]:
    """Bind a call to its declared parameters for the audit record.

    Defaults are applied so the log shows the values that actually took effect,
    which matters for guards such as ``overwrite``. A binding failure is
    returned rather than raised so the caller can audit it before reporting it.
    """
    try:
        bound = signature.bind(*args, **kwargs)
    except TypeError as exc:
        best_effort: dict[str, Any] = {"positional": list(args)}
        best_effort.update(kwargs)
        return best_effort, exc
    bound.apply_defaults()
    return dict(bound.arguments), None


def guarded(
    *,
    classification: str,
    module: str | None,
    channel: str = Channel.MCP_COM,
    tier: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Wrap a tool with policy gating, auditing, and a typed error contract."""
    if classification not in Classification.ALL:
        raise MaxsurfSafetyError(
            "unknown operation classification", classification=classification
        )

    def decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        tool_name = func.__name__
        must_audit = classification in Classification.MUST_AUDIT
        signature = inspect.signature(func)
        uses_com = channel == Channel.MCP_COM
        timeout = (
            config.analysis_timeout
            if classification == Classification.EXECUTE_ANALYSIS
            else config.com_timeout
        )

        def execute(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
            """Run the tool body, on the COM worker when COM is involved.

            Local-channel tools stay on the calling thread deliberately: they
            touch no COM, and keeping them off the worker leaves diagnostics
            such as ``session_status`` answerable while a COM call is stuck.
            """
            if not uses_com:
                return func(*args, **kwargs)
            return comthread.run(
                lambda: func(*args, **kwargs),
                timeout=timeout(),
                description=tool_name,
            )

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            log = audit.get_log()
            arguments, binding_error = _bind_arguments(signature, args, kwargs)
            audit_id = audit.new_id()
            scope = audit.begin_call_scope(audit_id, classification)
            started = time.perf_counter()

            def elapsed_ms() -> float:
                return round((time.perf_counter() - started) * 1000, 3)

            try:
                if not tier_enabled(tier):
                    blocked = MaxsurfSafetyError(
                        f"tool {tool_name!r} belongs to the {tier} tier, which is "
                        f"disabled; set {_TIER_VARS[tier]}=1 to enable it",
                        tool=tool_name,
                        tier=tier,
                        classification=classification,
                    )
                    log.record(
                        stage="complete",
                        audit_id=audit_id,
                        tool=tool_name,
                        module=module,
                        classification=classification,
                        channel=channel,
                        arguments=arguments,
                        outcome=Outcome.BLOCKED,
                        error=audit.error_payload(blocked),
                        duration_ms=elapsed_ms(),
                    )
                    raise blocked

                if binding_error is not None:
                    invalid = MaxsurfValidationError(
                        f"arguments do not match the signature of {tool_name}",
                        tool=tool_name,
                        expected=str(signature),
                        reason=str(binding_error),
                    )
                    log.record(
                        stage="complete",
                        audit_id=audit_id,
                        tool=tool_name,
                        module=module,
                        classification=classification,
                        channel=channel,
                        arguments=arguments,
                        outcome=Outcome.FAILURE,
                        error=audit.error_payload(invalid),
                        duration_ms=elapsed_ms(),
                    )
                    raise invalid

                if must_audit:
                    log.record(
                        stage="intent",
                        audit_id=audit_id,
                        tool=tool_name,
                        module=module,
                        classification=classification,
                        channel=channel,
                        arguments=arguments,
                    )

                try:
                    result = execute(args, kwargs)
                except MaxsurfError as exc:
                    log.record(
                        stage="complete",
                        audit_id=audit_id,
                        tool=tool_name,
                        module=module,
                        classification=classification,
                        channel=channel,
                        arguments=arguments,
                        outcome=Outcome.FAILURE,
                        error=audit.error_payload(exc),
                        duration_ms=elapsed_ms(),
                    )
                    raise
                except Exception as exc:  # noqa: BLE001 - normalise to the taxonomy
                    if channel == Channel.MCP_COM:
                        wrapped: MaxsurfError = MaxsurfCOMError(
                            f"{tool_name} failed with an unexpected error",
                            cause=exc,
                            tool=tool_name,
                            unexpected=True,
                        )
                    else:
                        wrapped = MaxsurfError(
                            f"{tool_name} failed with an unexpected error",
                            tool=tool_name,
                            unexpected=True,
                            cause_type=type(exc).__name__,
                            cause_message=str(exc)[:500],
                        )
                    log.record(
                        stage="complete",
                        audit_id=audit_id,
                        tool=tool_name,
                        module=module,
                        classification=classification,
                        channel=channel,
                        arguments=arguments,
                        outcome=Outcome.FAILURE,
                        error=audit.error_payload(wrapped),
                        duration_ms=elapsed_ms(),
                    )
                    raise wrapped from exc

                log.record(
                    stage="complete",
                    audit_id=audit_id,
                    tool=tool_name,
                    module=module,
                    classification=classification,
                    channel=channel,
                    arguments=arguments,
                    outcome=Outcome.SUCCESS,
                    duration_ms=elapsed_ms(),
                    result_summary=_summarize(result),
                )
                return result
            finally:
                audit.end_call_scope(scope)

        prefix = {Classification.READ: "READ-ONLY", Classification.WRITE_GEOMETRY: "WRITE"}.get(classification, classification)
        if not (wrapper.__doc__ or "").lstrip().startswith((prefix + ".", "QUARANTINED / UNPROVEN.")):
            wrapper.__doc__ = prefix + ". " + (wrapper.__doc__ or "")

        wrapper.maxsurf_classification = classification  # type: ignore[attr-defined]
        wrapper.maxsurf_module = module  # type: ignore[attr-defined]
        wrapper.maxsurf_channel = channel  # type: ignore[attr-defined]
        wrapper.maxsurf_tier = tier  # type: ignore[attr-defined]
        REGISTRY[tool_name] = {
            "tool": tool_name,
            "module": module,
            "classification": classification,
            "channel": channel,
            "tier": tier,
        }
        return wrapper

    return decorate


def make_adapter(tool: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a guarded tool in the coroutine FastMCP will actually await.

    FastMCP calls a synchronous tool inline on the event loop, so a COM call
    lasting minutes would freeze the whole server, including its ability to
    answer anything else. Handing the guarded call to a worker thread and
    awaiting it keeps the loop free while COM runs on its own STA thread.

    The guarded function itself stays synchronous and directly callable, which
    is what the offline suite exercises.
    """

    @functools.wraps(tool)
    async def adapter(*args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(tool, *args, **kwargs)

    # inspect.signature and the docstring already follow __wrapped__, so FastMCP
    # derives an identical schema from the adapter.
    adapter.maxsurf_tool = tool  # type: ignore[attr-defined]
    for attribute in ("maxsurf_classification", "maxsurf_module",
                      "maxsurf_channel", "maxsurf_tier"):
        setattr(adapter, attribute, getattr(tool, attribute, None))
    return adapter


def register(mcp: Any, tools: tuple[Callable[..., Any], ...]) -> list[str]:
    """Register only the tools whose tier is currently enabled.

    Disabled tiers are absent from ``tools/list`` entirely, so an agent is not
    tempted by a tool it cannot use.
    """
    registered = []
    for tool in tools:
        tier = getattr(tool, "maxsurf_tier", None)
        if not tier_enabled(tier):
            continue
        from mcp.types import ToolAnnotations
        classification = tool.maxsurf_classification
        read_only = classification == Classification.READ and tool.__name__ not in {
            "connect_maxsurf", "reset_connection"}
        mcp.tool(annotations=ToolAnnotations(
            readOnlyHint=read_only, destructiveHint=not read_only,
            idempotentHint=read_only, openWorldHint=True,
        ))(make_adapter(tool))
        registered.append(tool.__name__)
    return registered
