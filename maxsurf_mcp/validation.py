"""Input/storage validation only; no naval-design rules."""
from __future__ import annotations
import json
import math
from typing import Any
from .errors import MaxsurfCOMError, MaxsurfValidationError

def number(value: Any, field: str, *, positive: bool = False,
           minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool):
        raise MaxsurfValidationError(f"{field} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MaxsurfValidationError(f"{field} must be a finite number") from exc
    if not math.isfinite(result) or (positive and result <= 0) or (
        minimum is not None and result < minimum
    ) or (maximum is not None and result > maximum):
        raise MaxsurfValidationError(f"{field} is non-finite or out of bounds")
    return result

def name(value: Any, field: str = "name") -> str:
    if not isinstance(value, str) or not value.strip():
        raise MaxsurfValidationError(f"{field} must be a non-empty string")
    return value.strip()

def parsed(raw: str, kind: type, field: str) -> Any:
    def invalid(token):
        raise ValueError(f"non-JSON number {token}")
    try:
        result = json.loads(raw, parse_constant=invalid)
    except (ValueError, TypeError) as exc:
        raise MaxsurfValidationError(f"{field} must be valid finite JSON") from exc
    if not isinstance(result, kind):
        raise MaxsurfValidationError(f"{field} must be a {kind.__name__}")
    return result

def stored(obj: Any, field: str, value: Any) -> Any:
    setattr(obj, field, value)
    actual = getattr(obj, field)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        matches = math.isfinite(float(actual)) and math.isclose(
            float(actual), float(value), rel_tol=1e-8, abs_tol=1e-8)
    else:
        matches = actual == value
    if not matches:
        raise MaxsurfCOMError("property readback mismatch", field=field,
                              requested=value, actual=actual,
                              partial_state_possible=True)
    return actual

