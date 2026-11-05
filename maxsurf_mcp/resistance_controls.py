"""Explicit native Resistance measurement and form-factor controls."""
from __future__ import annotations

from typing import Any

from . import com, validation as val
from .audit import Channel, Classification
from .errors import MaxsurfValidationError
from .guard import guarded, register
from .resistance import (
    VESSEL_FIELDS, VESSEL_UNITS, _constant, _require_ready, _vessel_snapshot,
)


@guarded(classification=Classification.WRITE_STATE, module="resistance",
         channel=Channel.MCP_COM)
def set_resistance_measurements(measurements_json: str, chine_type: str = "") -> str:
    """Set explicit IVessel names in documented SI units; angles are degrees.

    LCGFromMidships is positive forward. Chine names are hsCTHardChine or
    hsCTRoundBilge. MeasureHull overwrites measured inputs; run with
    measure_hull=False to retain these explicit measurements.
    """
    values = val.parsed(measurements_json, dict, "measurements_json")
    if not isinstance(chine_type, str) or chine_type not in (
        "", "hsCTHardChine", "hsCTRoundBilge"
    ):
        raise MaxsurfValidationError("unsupported chine_type")
    if not values and not chine_type:
        raise MaxsurfValidationError("at least one measurement or chine is required")
    unknown = sorted(set(values) - set(VESSEL_FIELDS))
    if unknown:
        raise MaxsurfValidationError("unknown writable vessel measurements",
                                      fields=unknown)
    # Storage validation, not a vessel recipe or method applicability rule.
    targets = {field: val.number(value, field) for field, value in values.items()}
    app = com.connect("resistance")
    design = _require_ready(app)
    chine = _constant(app, chine_type) if chine_type else None
    for field, value in targets.items():
        val.stored(design.Vessel, field, value)
    if chine is not None:
        val.stored(design.Vessel, "ChineType", chine)
    return com.to_json({"measurements": _vessel_snapshot(design),
                        "written": targets, "units": VESSEL_UNITS,
                        "chine_type": chine_type or None,
                        "chine_value": chine, "verified": True})


@guarded(classification=Classification.WRITE_STATE, module="resistance",
         channel=Channel.MCP_COM)
def set_resistance_form_factor(mode: str, one_plus_k: float | None = None) -> str:
    """Select automatic Holtrop factor or explicit positive slender-body 1+k."""
    if mode not in ("automatic", "manual"):
        raise MaxsurfValidationError("mode must be automatic or manual")
    if mode == "automatic":
        if one_plus_k is not None:
            raise MaxsurfValidationError("automatic mode does not accept one_plus_k")
        target = -1.0
    else:
        target = val.number(one_plus_k, "one_plus_k", positive=True)
    app = com.connect("resistance")
    design = _require_ready(app)
    method = _constant(app, "hsMTSlenderBody")
    if not design.Methods.IsSelected(method):
        raise MaxsurfValidationError("select hsMTSlenderBody before setting its form factor")
    actual = val.stored(design.Methods, "SlenderBodyOnePlusK", target)
    return com.to_json({"mode": mode, "one_plus_k": actual,
                        "automatic_basis": "Holtrop" if mode == "automatic" else None,
                        "verified": True})


TOOLS = (set_resistance_measurements, set_resistance_form_factor)


def register_tools(mcp: Any) -> list[str]:
    return register(mcp, TOOLS)
