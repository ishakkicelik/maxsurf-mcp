"""Modeler controls proven by the installed BentleyModeler 1.1 type library.

ModelerAutomation.pdf pp. 24-26 defines the advanced transformation sequence.
The executable's signature, not the older twelve-argument PDF example, defines
Transform: seven doubles followed by six Booleans, including DoConstrainMac.
All numbers use native automation units; no datum/sign conversion is inferred.
"""

from __future__ import annotations

import json
import math
from typing import Any

from . import com, hydrostatics
from .audit import Classification
from .errors import MaxsurfCOMError, MaxsurfConnectionError, MaxsurfValidationError
from .guard import guarded, register

PRECISIONS = {"lowest": 1, "low": 2, "medium": 3, "high": 4, "highest": 5}
TRANSFORM_FIELDS = (
    "TransformDisp", "TransformBeam", "TransformLWL", "TransformDraft",
    "TransformBlock", "TransformPrismatic", "TransformMAC", "TransformWPAC",
    "TransformLCB", "TransformLCF", "TransformFlare", "ABC", "FBC",
)
# Each entry is an independently inspected native signature, not a guessed alias.
TARGETS = {
    "displacement": ("SetTargetDisp", 1),
    "lwl": ("SetTargetLWL", 1),
    "lbg": ("SetTargetLBG", 1),
    "girths": ("SetTargetGirths", 2),
    "beam": ("SetTargetBeam", 1),
    "draft": ("SetTargetDraft", 1),
    "lcb": ("SetTargetLCB", 1),
    "block": ("SetTargetBlock", 1),
    "prismatic": ("SetTargetPrismatic", 1),
    "mac": ("SetTargetMAC", 1),
    "flare": ("SetTargetFlare", 1),
    "wpac": ("SetTargetWPAC", 1),
}


def _number(value: Any, name: str, *, positive: bool = False,
            coefficient: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MaxsurfValidationError(f"{name} must be a number", argument=name)
    value = float(value)
    if not math.isfinite(value) or (positive and value <= 0) \
            or (coefficient and not 0 < value <= 1):
        raise MaxsurfValidationError(f"{name} is outside its finite valid range",
                                     argument=name, value=value)
    return value


def _hydro(app: Any) -> Any:
    design = com.probe(app, "Design")
    if design["status"] != "read":
        raise MaxsurfConnectionError("Modeler Design is not reachable")
    hydro = com.probe(design["value"], "Hydrostatics")
    if hydro["status"] != "read":
        raise MaxsurfConnectionError("Design.Hydrostatics is not reachable")
    return hydro["value"]


def _settings(app: Any) -> dict[str, Any]:
    trimming = com.probe(app, "Trimming")
    return {
        "trimming": {"value": trimming["value"] if trimming["status"] == "read"
                     else None, "status": trimming["status"]},
        "precision": hydrostatics._read_precision(app),
    }


@guarded(classification=Classification.READ, module="modeler")
def get_hydrostatic_settings() -> str:
    """Read native Application.Trimming and Preferences.Precision (1..5)."""
    return com.to_json({"module": "modeler", **_settings(com.connect("modeler")),
                        "precision_values": PRECISIONS})


@guarded(classification=Classification.WRITE_STATE, module="modeler")
def set_hydrostatic_settings(trimming: bool | None = None,
                            precision: str | None = None) -> str:
    """Set trimming/precision and verify both through native getters.

    Precision accepts lowest, low, medium, high, highest. Neither preference
    changes automation's SI units. A partial write is never reported as success.
    """
    if trimming is not None and not isinstance(trimming, bool):
        raise MaxsurfValidationError("trimming must be a Boolean")
    if precision is not None and (not isinstance(precision, str)
                                  or precision not in PRECISIONS):
        raise MaxsurfValidationError("unknown precision", allowed=list(PRECISIONS))
    if trimming is None and precision is None:
        raise MaxsurfValidationError("supply trimming or precision")
    app = com.connect("modeler")
    before = _settings(app)
    completed: list[str] = []
    try:
        if trimming is not None:
            app.Trimming = trimming
            completed.append("Application.Trimming")
        if precision is not None:
            app.Preferences.Precision = PRECISIONS[precision]
            completed.append("Preferences.Precision")
    except Exception as exc:
        raise MaxsurfCOMError("hydrostatic setting write failed", cause=exc,
                              completed=completed, before=before,
                              after=_settings(app), operation_state="may_be_partial") from exc
    after = _settings(app)
    expected = {"trimming": trimming, "precision": PRECISIONS.get(precision)}
    mismatch = [key for key, value in expected.items() if value is not None
                and (after[key]["status"] != "read" or after[key]["value"] != value)]
    if mismatch:
        raise MaxsurfCOMError("hydrostatic setting readback did not match",
                              completed=completed, before=before, after=after,
                              mismatched=mismatch, operation_state="may_be_partial")
    return com.to_json({"before": before, "after": after, "verified": True})


def _transform_results(hydro: Any) -> dict[str, Any]:
    return {name: com.probe(hydro, name) for name in TRANSFORM_FIELDS}


@guarded(classification=Classification.READ, module="modeler")
def get_hydrostatic_results() -> str:
    """Read cached native results without calculating or claiming freshness."""
    hydro = _hydro(com.connect("modeler"))
    return com.to_json({"results": hydrostatics._read_results(hydro),
                        "transformation_results": _transform_results(hydro),
                        "freshness": "unknown", "vcg_datum_status": "unproven"})


def _execute_transform(hydro: Any, steps: list[tuple[str, tuple[Any, ...]]],
                       density: float) -> str:
    completed: list[str] = []
    before = hydrostatics._read_results(hydro)
    try:
        # Fresh geometry-only hydrostatics establishes the current native state,
        # without supplying an unproven conventional VCG.
        hydro.Calculate(density, 0.0)
        completed.append("Calculate")
        before = hydrostatics._read_results(hydro)
        for method, arguments in steps:
            getattr(hydro, method)(*arguments)
            completed.append(method)
        transform_results = _transform_results(hydro)
        hydro.Calculate(density, 0.0)
        completed.append("Calculate")
    except Exception as exc:
        raise MaxsurfCOMError("hydrostatic transformation failed", cause=exc,
                              completed=completed, before=before,
                              after=hydrostatics._read_results(hydro),
                              operation_state="may_be_partial",
                              rollback_performed=False) from exc
    after = hydrostatics._read_results(hydro)
    return com.to_json({
        "completed": completed, "before": before, "after": after,
        "transformation_results": transform_results,
        "validation": hydrostatics._validate(after, density),
        "native_call_status": "completed", "target_achievement": "unverified",
        "vcg_datum_status": "unproven", "rollback_performed": False,
    })


@guarded(classification=Classification.WRITE_GEOMETRY, module="modeler")
def transform_hydrostatics(
    target_lcb: float, target_coefficient: float, target_mac: float,
    target_displacement: float, target_immersed_depth: float, target_beam: float,
    target_lwl: float, constrain_mac: bool, constrain_displacement: bool,
    constrain_lwl: bool, constrain_beam: bool, constrain_immersed_depth: bool,
    optimise_block_coefficient: bool, density: float = 1025.0,
) -> str:
    """Run the installed thirteen-argument native Transform.

    LCB is a native datum-relative metre value, dimensions are metres,
    displacement is kg, MAC and the selected block/prismatic coefficient are
    fractions. All six constraints/mode Booleans are required explicitly.
    Completion is not proof that optimisation reached its requested targets.
    """
    values = (
        _number(target_lcb, "target_lcb"),
        _number(target_coefficient, "target_coefficient", coefficient=True),
        _number(target_mac, "target_mac", coefficient=True),
        _number(target_displacement, "target_displacement", positive=True),
        _number(target_immersed_depth, "target_immersed_depth", positive=True),
        _number(target_beam, "target_beam", positive=True),
        _number(target_lwl, "target_lwl", positive=True),
    )
    flags = (constrain_mac, constrain_displacement, constrain_lwl, constrain_beam,
             constrain_immersed_depth, optimise_block_coefficient)
    if any(not isinstance(flag, bool) for flag in flags):
        raise MaxsurfValidationError("all transformation flags must be Booleans")
    density = _number(density, "density", positive=True)
    return _execute_transform(_hydro(com.connect("modeler")),
                              [("Transform", values + flags)], density)


@guarded(classification=Classification.WRITE_GEOMETRY, module="modeler")
def advanced_transform_hydrostatics(targets_json: str,
                                   density: float = 1025.0) -> str:
    """Initialise, set explicit native targets, then run AdvancedTransform.

    Targets: displacement (kg); lwl/lbg/beam/draft/lcb and two girths (m);
    block/prismatic/mac/wpac (fractions); flare (degrees). LCF targeting is
    documented disabled. Midbody positions lack a proven unit/datum contract
    and are refused. InitAdvancedTransform resets targets each call.
    """
    try:
        targets = json.loads(targets_json)
    except (ValueError, TypeError) as exc:
        raise MaxsurfValidationError("targets_json must be a JSON object") from exc
    if not isinstance(targets, dict) or not targets:
        raise MaxsurfValidationError("supply a nonempty target object")
    steps: list[tuple[str, tuple[Any, ...]]] = [("InitAdvancedTransform", ())]
    for name, raw in targets.items():
        if name == "lcf":
            raise MaxsurfValidationError("SetTargetLCF is documented not enabled")
        if name == "midbody":
            raise MaxsurfValidationError(
                "SetTargetMidbody unit and datum prerequisites are unproven")
        if name not in TARGETS:
            raise MaxsurfValidationError("unsupported target", target=name,
                                         allowed=list(TARGETS))
        method, count = TARGETS[name]
        values = raw if count == 2 else [raw]
        if not isinstance(values, list) or len(values) != count:
            raise MaxsurfValidationError("target requires two girth values", target=name)
        args = tuple(_number(v, name, positive=name not in ("lcb", "flare"),
                             coefficient=name in ("block", "prismatic", "mac", "wpac"))
                     for v in values)
        steps.append((method, args))
    steps.append(("AdvancedTransform", ()))
    density = _number(density, "density", positive=True)
    return _execute_transform(_hydro(com.connect("modeler")), steps, density)


TOOLS = (get_hydrostatic_settings, set_hydrostatic_settings,
         get_hydrostatic_results, transform_hydrostatics,
         advanced_transform_hydrostatics)


def register_tools(mcp: Any) -> list[str]:
    """Register explicit Modeler hydrostatic controls with the shared guard."""
    return register(mcp, TOOLS)
