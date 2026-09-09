"""Typed error taxonomy for the Maxsurf MCP server.

Every failure path raises one of these exceptions so FastMCP converts it into
an MCP protocol error with ``isError=True``. Tools must never return a
successful payload that merely contains an ``error`` key: an agent cannot
distinguish that from a real result, which is how a failed write becomes a
data point in a design loop.
"""

from __future__ import annotations

from typing import Any


class MaxsurfError(Exception):
    """Base class for every error this server raises deliberately."""

    #: Short machine-readable discriminator included in the message.
    code = "maxsurf_error"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = dict(details)

    def __str__(self) -> str:
        if not self.details:
            return f"[{self.code}] {self.message}"
        rendered = ", ".join(
            f"{key}={value!r}" for key, value in sorted(self.details.items())
        )
        return f"[{self.code}] {self.message} ({rendered})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "type": type(self).__name__,
            "message": self.message,
            "details": self.details,
        }


class MaxsurfConnectionError(MaxsurfError):
    """No usable connection to the requested Maxsurf application."""

    code = "connection_error"


class MaxsurfValidationError(MaxsurfError):
    """Caller-supplied arguments are malformed or out of range."""

    code = "validation_error"


class MaxsurfSafetyError(MaxsurfError):
    """A guard, policy, or sandbox rule refused the operation.

    Raised for blocked traversal, disabled tool tiers, and rejected file
    paths. Distinct from validation because the arguments may be well formed
    while the operation itself is not permitted.
    """

    code = "safety_error"


class MaxsurfCOMError(MaxsurfError):
    """A COM call reached Maxsurf and failed there."""

    code = "com_error"

    def __init__(self, message: str, *, cause: BaseException | None = None,
                 **details: Any) -> None:
        if cause is not None:
            details.setdefault("com_source", type(cause).__name__)
            details.setdefault("com_message", str(cause))
        super().__init__(message, **details)
        self.cause = cause


class MaxsurfAnalysisError(MaxsurfError):
    """An analysis could not be prepared, executed, or trusted."""

    code = "analysis_error"


class MaxsurfTimeoutError(MaxsurfError):
    """A COM operation did not return within its budget.

    Deliberately not a :class:`MaxsurfCOMError`: the call may still be running
    inside Maxsurf. COM offers no safe way to cancel an in-flight call, so this
    reports that the *wait* was abandoned, never that the operation was
    cancelled. ``details["operation_state"]`` records which of the two it is.
    """

    code = "timeout_error"


__all__ = [
    "MaxsurfError",
    "MaxsurfConnectionError",
    "MaxsurfValidationError",
    "MaxsurfSafetyError",
    "MaxsurfCOMError",
    "MaxsurfAnalysisError",
    "MaxsurfTimeoutError",
]
