"""Explicit native Motions settings, with stored-value verification.

Frequency range setter units are not specified by the installed typelib.
Expose native values rather than silently converting Hz, periods or rad/s.
"""
from __future__ import annotations

from typing import Any

from . import com, constants, validation as val
from .audit import Channel, Classification
from .errors import MaxsurfCOMError, MaxsurfValidationError
from .guard import guarded, register
from .motions import _require_ready


def _integer(value: Any, field: str, maximum: int = 2147483647) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise MaxsurfValidationError(f"{field} must be an integer in 1..{maximum}")
    return value


def _enum(app: Any, name: str, allowed: tuple[str, ...]) -> int:
    if not isinstance(name, str) or name not in allowed:
        raise MaxsurfValidationError("unknown native formulation", available=allowed)
    catalog = constants.discover(app)["constants"]
    value = catalog.get(name)
    if type(value) is not int:
        raise MaxsurfValidationError("native enum is unavailable", enum=name)
    return value


@guarded(classification=Classification.WRITE_STATE, module="motions", channel=Channel.MCP_COM)
def set_motions_frequency_range(automatic: bool, count: int,
                               start_native: float = 0.0,
                               end_native: float = 0.0) -> str:
    """Set native frequency spacing; manual range units need live acceptance.

    No Hz/rad/s/period conversion is implied. Automatic mode requires zero
    endpoints and leaves native endpoints untouched.
    """
    if type(automatic) is not bool:
        raise MaxsurfValidationError("automatic must be a boolean")
    count = _integer(count, "count")
    start = val.number(start_native, "start_native", minimum=0)
    end = val.number(end_native, "end_native", minimum=0)
    if (automatic and (start or end)) or (not automatic and not 0 < start < end):
        raise MaxsurfValidationError("automatic requires zero endpoints; manual requires 0 < start < end")
    options = _require_ready(com.connect("motions")).AnalysisOptions
    written = {}
    try:
        for field, value in (("FrequenciesAuto", automatic), ("NumOfFrequencies", count)):
            written[field] = val.stored(options, field, value)
        if not automatic:
            for field, value in (("FrequenciesStart", start), ("FrequenciesEnd", end)):
                written[field] = val.stored(options, field, value)
    except Exception as exc:
        raise MaxsurfCOMError("frequency update failed", cause=exc, partial_state_possible=True) from exc
    return com.to_json({"stored": written, "storage_verified": True,
                        "frequency_unit": "native; physical unit not independently established",
                        "analysis_run": False})


@guarded(classification=Classification.WRITE_STATE, module="motions", channel=Channel.MCP_COM)
def set_motions_formulation(use_transom_terms: bool, wave_force_method: str,
                           added_resistance_method: str) -> str:
    """Set explicit installed strip-theory formulation enum names."""
    if type(use_transom_terms) is not bool:
        raise MaxsurfValidationError("use_transom_terms must be a boolean")
    app = com.connect("motions")
    wave = _enum(app, wave_force_method,
                 ("skWFArbitraryWaveHeading", "skWFHeadSeasApproximation"))
    resistance = _enum(app, added_resistance_method,
                       ("skARSalvesen", "skARGerritsmaBeukelmanI",
                        "skARGerritsmaBeukelmanII", "skARHavelock"))
    options = _require_ready(app).AnalysisOptions
    try:
        stored = {field: val.stored(options, field, value) for field, value in (
            ("UseTransomTerms", use_transom_terms), ("WaveForceMethod", wave),
            ("AddedResistanceMethod", resistance))}
    except Exception as exc:
        raise MaxsurfCOMError("formulation update failed", cause=exc, partial_state_possible=True) from exc
    return com.to_json({"stored": stored, "storage_verified": True, "analysis_run": False})


@guarded(classification=Classification.WRITE_STATE, module="motions", channel=Channel.MCP_COM)
def set_motions_vessel_settings(draft_aft_m: float, draft_fwd_m: float,
                               additional_heave_pitch_damping_nondim: float,
                               demihull_spacing_m: float,
                               sections: int, mapping_terms: int) -> str:
    """Set unequal drafts, damping, demihull spacing and mapping discretization."""
    values = {
        "DraftAft": val.number(draft_aft_m, "draft_aft_m", positive=True),
        "DraftFwd": val.number(draft_fwd_m, "draft_fwd_m", positive=True),
        "AdditionalHeavePitchDampingNonDim": val.number(
            additional_heave_pitch_damping_nondim, "damping", minimum=0),
        "DemihullSpacing": val.number(demihull_spacing_m, "spacing", minimum=0),
        "numOfSections": _integer(sections, "sections", 200),
        "NumOfMappingTerms": _integer(mapping_terms, "mapping_terms", 15),
    }
    vessel = _require_ready(com.connect("motions")).Vessel
    try:
        stored = {field: val.stored(vessel, field, value) for field, value in values.items()}
    except Exception as exc:
        raise MaxsurfCOMError("vessel update failed", cause=exc, partial_state_possible=True) from exc
    return com.to_json({"stored": stored, "storage_verified": True, "analysis_run": False})


TOOLS = (set_motions_frequency_range, set_motions_formulation, set_motions_vessel_settings)


def register_tools(mcp: Any) -> list[str]:
    return register(mcp, TOOLS)
