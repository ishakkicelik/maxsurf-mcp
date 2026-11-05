"""Explicit Stability policies and native readback helpers.

Contracts checked against the registered Maxsurf 2026 embedded typelib.
StabilityManual.pdf physical p261 defines specific gravity relative to
1000 kg/m^3; SetDensity's automation units are not established.
"""
from __future__ import annotations

import math
from typing import Any

from . import com, validation as val
from .audit import Channel, Classification
from .errors import MaxsurfCOMError, MaxsurfSafetyError, MaxsurfValidationError
from .guard import guarded, register
from .stability import _constant

TRIM_MODES = {
    "fixed": ("hmTMFixed", "FixedTrimValue"),
    "loadcase": ("hmTMFreeToLoadcase", None),
    "initial": ("hmTMFreeWithInitialTrim", "InitialTrimValue"),
    "lcg": ("hmTMFreeToLCG", "TrimToLCGValue"),
}
FLUID_MODES = {
    "corrected_vcg": "hmFMCorrectedVCG",
    "simulated_movement": "hmFMSimulatedFluidMovement",
}
FLUID_TYPES = (
    "hmFSeaWater", "hmFWaterBallast", "hmFFreshWater", "hmFDiesel",
    "hmFFuelOil", "hmFLubeOil", "hmFGasolineLeaded", "hmFGasolineUnleaded",
    "hmFANSCrude", "hmFDMA", "hmFDMB", "hmFDMC", "hmFJFA", "hmFMTBE",
    "hmFGasoil", "hmFSlops",
) + tuple(f"hmFCustom{i}" for i in range(1, 21))


def _boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise MaxsurfValidationError(f"{field} must be a Boolean")
    return value


def _verify(obj: Any, expected: dict[str, Any]) -> dict[str, Any]:
    """Compare getters without assigning them; never repair a failed creation."""
    actual = {}
    for member, target in expected.items():
        value = getattr(obj, member)
        if isinstance(target, (int, float)) and not isinstance(target, bool):
            value = val.number(value, member)
            matches = math.isclose(value, target, rel_tol=1e-8, abs_tol=1e-8)
        else:
            matches = type(value) is type(target) and value == target
        actual[member] = value
        if not matches:
            raise MaxsurfCOMError("native readback mismatch", field=member,
                                  requested=target, actual=value,
                                  partial_state_possible=True)
    return actual


def _verify_room(item: Any, row: dict[str, Any]) -> dict[str, Any]:
    expected = {"Name": row["name"], "Type": row["type_value"],
                "aft": row["aft"], "fwd": row["fwd"]}
    for axis, members in {
        "port": ("aftPort", "fwdPort"), "stbd": ("AftStbd", "fwdStbd"),
        "top": ("aftTop", "fwdTop"), "bottom": ("aftBottom", "fwdBottom"),
    }.items():
        expected.update({member: row[axis] for member in members})
    if row["kind"] == "tank":
        expected.update(intactPermeability=row["intact_perm"],
                        damagedPermeability=row["damaged_perm"])
        if "fluid_value" in row:
            expected["FluidType"] = row["fluid_value"]
        if "relative_density" in row:
            expected["RelativeDensity"] = row["relative_density"]
    else:
        expected["Permeability"] = row["permeability"]
    return _verify(item, expected)


@guarded(classification=Classification.WRITE_STATE, module="stability", channel=Channel.MCP_COM)
def set_stability_trim(mode: str, value_m: float | None = None) -> str:
    """Set fixed trim, loadcase LCG, initial trim or specified LCG policy.

    Trim applies except equilibrium, specified condition, floodable length and
    longitudinal strength. Values are metres, not angles.
    """
    mode = val.name(mode, "mode")
    if mode not in TRIM_MODES:
        raise MaxsurfValidationError("unknown trim mode")
    constant, member = TRIM_MODES[mode]
    if (member is None) != (value_m is None):
        raise MaxsurfValidationError("value_m required only for fixed, initial or lcg")
    value = val.number(value_m, "value_m") if member else None
    app = com.connect("stability")
    native_mode = _constant(app, constant)
    trim = app.Design.Trim
    if member:
        val.stored(trim, member, value)
    val.stored(trim, "TrimMode", native_mode)
    expected = {"TrimMode": native_mode}
    if member:
        expected[member] = value
    return com.to_json({"mode": mode, "stored": _verify(trim, expected), "verified": True})


@guarded(classification=Classification.WRITE_STATE, module="stability", channel=Channel.MCP_COM)
def set_stability_fluid_mode(mode: str) -> str:
    """Choose corrected VCG or native simulation of fluid movement explicitly."""
    mode = val.name(mode, "mode")
    if mode not in FLUID_MODES:
        raise MaxsurfValidationError("unknown fluid mode")
    app = com.connect("stability")
    value = _constant(app, FLUID_MODES[mode])
    val.stored(app.Design.Fluids, "FluidMode", value)
    return com.to_json({"mode": mode, "native_value": value, "verified": True})


@guarded(classification=Classification.WRITE_STATE, module="stability", channel=Channel.MCP_COM)
def set_stability_density(fluid: str, density_kg_m3: float) -> str:
    """Set fluid density through dimensionless specific gravity and verify it.

    Sea Water controls floating medium even for fresh water operation. Changing
    a fluid updates tanks, calibrations and load conditions using that fluid.
    """
    fluid = val.name(fluid, "fluid")
    if fluid not in FLUID_TYPES:
        raise MaxsurfValidationError("unknown global fluid type")
    density = val.number(density_kg_m3, "density_kg_m3", positive=True)
    app = com.connect("stability")
    value = _constant(app, fluid)
    native = app.Design.Density
    native.SetSpecificGravity(value, density / 1000.0)
    actual = val.number(native.SpecificGravity(value), "SpecificGravity", positive=True)
    if not math.isclose(actual, density / 1000.0, rel_tol=1e-8, abs_tol=1e-8):
        raise MaxsurfCOMError("density readback mismatch", requested=density,
                              actual_kg_m3=actual * 1000, partial_state_possible=True)
    return com.to_json({"fluid": fluid, "density_kg_m3": actual * 1000,
                        "specific_gravity": actual, "verified": True,
                        "native_member": "Density.SetSpecificGravity"})


@guarded(classification=Classification.WRITE_STATE, module="stability", channel=Channel.MCP_COM)
def define_conditional_keypoints(points_json: str) -> str:
    """Add conditional flooding points with explicit room links and use policies.

    Compartments/tanks also require damage selection; NBVs do not. This tool
    defines inputs only, not a flooding prediction or a damage-case selection.
    Existing points are never replaced. FillInto applies to outflow points,
    not conditional flooding, and is not accepted here.
    """
    points = val.parsed(points_json, list, "points_json")
    if not points:
        raise MaxsurfValidationError("points_json must not be empty")
    planned, seen = [], set()
    for point in points:
        if not isinstance(point, dict) or set(point) != {
            "name", "x", "y", "z", "linked_to",
            "use_intact", "use_damage", "use_intermediate_damage",
        }:
            raise MaxsurfValidationError("conditional point requires exact coordinates, link and use policies")
        name = val.name(point["name"])
        if name in seen:
            raise MaxsurfValidationError("duplicate point name")
        seen.add(name)
        planned.append({
            "Name": name,
            **{member: val.number(point[key], key) for key, member in
               (("x", "Position"), ("y", "Offset"), ("z", "Height"))},
            "LinkedTo": val.name(point["linked_to"], "linked_to"),
            **{member: _boolean(point[key], key) for key, member in
               (("use_intact", "UseIntact"), ("use_damage", "UseDamage"),
                ("use_intermediate_damage", "UseIntermediateDamage"))},
        })
    from .stability import _room_names
    app = com.connect("stability")
    design = app.Design
    points = design.KeyPoints
    before = int(points.Count)
    existing = {str(points.Item(i).Name) for i in range(1, before + 1)}
    if seen & existing:
        raise MaxsurfSafetyError("keypoint names already exist", names=sorted(seen & existing))
    known = _room_names(design)
    unknown = {row["LinkedTo"] for row in planned} - known
    if unknown:
        raise MaxsurfValidationError("unknown linked room", rooms=sorted(unknown))
    kind = _constant(app, "hmKTConditionalFlooding")
    stored = []
    try:
        for index, row in enumerate(planned, before + 1):
            points.Add(row["Name"], row["Position"], row["Offset"], row["Height"], kind)
            item = points.Item(index)
            for member in ("LinkedTo", "UseIntact", "UseDamage", "UseIntermediateDamage"):
                val.stored(item, member, row[member])
            stored.append(_verify(item, dict(row, Type=kind)))
        if int(points.Count) != before + len(planned):
            raise MaxsurfCOMError("keypoint count mismatch")
    except Exception as exc:
        raise MaxsurfCOMError("conditional keypoint update failed", cause=exc,
                              readback=stored, partial_state_possible=True) from exc
    return com.to_json({"points": stored, "verified": True, "analysis_run": False})


TOOLS = (set_stability_trim, set_stability_fluid_mode, set_stability_density,
         define_conditional_keypoints)


def register_tools(mcp: Any) -> list[str]:
    return register(mcp, TOOLS)
