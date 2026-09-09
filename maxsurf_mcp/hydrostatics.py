"""Native Modeler hydrostatics and numerical consistency checks; no design rules."""

from __future__ import annotations

import json
import math
from typing import Any

from . import com
from .audit import Channel, Classification
from .errors import MaxsurfConnectionError, MaxsurfValidationError
from .guard import guarded, register

#: Field name -> candidate member names. No name here is invented; these are
#: the original candidates, retained so behaviour is unchanged while the
#: proven Design.Hydrostatics adapter is still to be built.
FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "loa": ("LengthOverall", "LOA", "Length"),
    "lwl": ("LengthWaterline", "LWL"),
    "beam": ("Beam", "BeamWaterline", "BMax"),
    "draft": ("Draft", "Draught"),
    "displacement": ("Displacement", "Disp"),
    "cb": ("BlockCoefficient", "Cb"),
    "cp": ("PrismaticCoefficient", "Cp"),
    "cm": ("MidshipCoefficient", "Cm"),
    "cw": ("WaterplaneCoefficient", "Cw"),
    "lcb": ("LCB", "LongitudinalCentreOfBuoyancy"),
    "wetted_area": ("WettedSurfaceArea", "WettedArea"),
}










#: The result getters read after Calculate. Every name is proven on
#: ``IHydrostatics`` by live ITypeInfo discovery and recorded in private discovery notes.
#: All are read-only R8. The interface has no property-put at all, which is why
#: draft, heel and trim cannot be set from here.
RESULT_FIELDS: tuple[str, ...] = (
    "Displacement", "Volume", "Draft", "ImmersedDepth",
    "LWL", "BeamWL", "WSA", "WaterplaneArea", "MaxCrossSectArea",
    "Cb", "Cp", "Cm", "Cwp",
    "LCB", "LCBasFraction", "LCF", "LCFasFraction",
    "KB", "KG", "BMt", "BMl", "GMt", "GMl", "KMt", "KMl",
    "MTc", "TPC", "RM", "LBG", "AG", "FG", "Flare",
)

#: Units from the Modeler automation manual and live dimensional checks.
#:
#: TPC is the deliberate exception to the otherwise SI-oriented interface:
#: page 35 of ``ModelerAutomation.pdf`` explicitly calls it "Tonnes per
#: centimetre Immersion".  The live Cargo Vessel result proves the same scale:
#: ``WaterplaneArea * density * 0.01 / 1000`` reproduces TPC.  MTc follows the
#: matching Maxsurf hydrostatics convention of tonne.m/cm.  Labelling either as
#: kg-based makes a design loop wrong by a factor of one thousand.
RESULT_UNITS: dict[str, str] = {
    "Displacement": "kg", "Volume": "m^3",
    "WSA": "m^2", "WaterplaneArea": "m^2", "MaxCrossSectArea": "m^2",
    "Cb": "-", "Cp": "-", "Cm": "-", "Cwp": "-",
    "LCBasFraction": "-", "LCFasFraction": "-",
    "TPC": "tonne/cm", "MTc": "tonne.m/cm", "RM": "kg.m",
    "Flare": "deg",
}
DEFAULT_UNIT = "m"

SOLVER_OK = "ok"
SOLVER_FAILED = "failed"

VALIDATION_OK = "ok"
VALIDATION_SUSPECT = "suspect"


def _unit(field: str) -> str:
    return RESULT_UNITS.get(field, DEFAULT_UNIT)


def _read_results(hydrostatics: Any) -> dict[str, Any]:
    """Read every result getter, keeping unreadable distinct from zero."""
    results: dict[str, Any] = {}
    for field in RESULT_FIELDS:
        probed = com.probe(hydrostatics, field)
        if probed["status"] == "read":
            try:
                value: Any = float(probed["value"])
                status = "read"
            except (TypeError, ValueError):
                value, status = None, "uncastable"
        else:
            value = None
            status = (probed["attempts"] or [{}])[0].get(
                "status", probed["status"])
        results[field] = {"value": value, "status": status,
                          "unit": _unit(field)}
    return results


def _value(results: dict[str, Any], field: str) -> float | None:
    entry = results.get(field) or {}
    return entry["value"] if entry.get("status") == "read" else None


def _validate(results: dict[str, Any], density: float,
              vcg: float = 0.0) -> dict[str, Any]:
    """Apply the agreed physical checks. Reasons are codes, not prose."""
    reasons: list[str] = []
    displacement = _value(results, "Displacement")
    volume = _value(results, "Volume")
    draft = _value(results, "Draft")
    immersed = _value(results, "ImmersedDepth")
    kb = _value(results, "KB")
    mtc = _value(results, "MTc")

    closure = None
    if displacement is not None and volume is not None and displacement > 0:
        closure = abs(displacement - density * volume) / abs(displacement)
        if closure > 1e-6:
            reasons.append("density_volume_mismatch")

    if (displacement is not None and displacement <= 0) or \
            (volume is not None and volume <= 0):
        reasons.append("no_immersed_volume")

    if draft is not None and immersed is not None \
            and draft <= 0 and immersed > 0:
        reasons.append("datum_not_established")

    if kb is not None and kb < 0:
        reasons.append("datum_below_hull")

    for coefficient in ("Cb", "Cp", "Cm", "Cwp"):
        value = _value(results, coefficient)
        if value is not None and (value > 1 or value < 0):
            reasons.append("coefficient_out_of_range")
            break

    if mtc is not None and mtc == 0 and displacement is not None \
            and displacement > 0:
        reasons.append("suspicious_zero")

    # Live Maxsurf 2025 acceptance showed that Calculate(..., +1.1) can return
    # KG=-1.1 when the active vertical datum is the baseline.  The automation
    # manual states the argument's unit but does not expose enough datum/sign
    # metadata to turn a conventional positive-up KG into this coordinate
    # safely.  Geometry-only hydrostatics at vcg=0 remain valid; any non-zero
    # input must be labelled suspect until that transform is proven.
    if abs(vcg) > 1e-12:
        reasons.append("vcg_sign_convention_unproven")

    unreadable = sorted(f for f, e in results.items() if e["status"] != "read")
    return {
        "validation_status": VALIDATION_OK if not reasons else VALIDATION_SUSPECT,
        "validation_reasons": sorted(set(reasons)),
        "density_closure_relative_error": closure,
        "unreadable_fields": unreadable,
    }


def _set_trimming(app: Any) -> dict[str, Any]:
    """Turn trimming on, and report honestly whether that worked.

    The Modeler manual states trimming affects hydrostatic results, not only the
    drawing, so an untrimmed model can produce a displacement that includes
    geometry the real hull does not have. The help calls Trimming a Boolean
    property, but its ``[= value]`` syntax line is page boilerplate present on
    read-only members too, so writability is not proven and a failure here is
    reported rather than raised.
    """
    before = com.probe(app, "Trimming")
    report: dict[str, Any] = {
        "before": bool(before["value"]) if before["status"] == "read" else None,
        "before_status": before["status"],
        "write_attempted": False,
        "write_error": None,
        "after": None,
        "effective": None,
    }
    if before["status"] == "read" and bool(before["value"]):
        report["after"] = True
        report["effective"] = True
        return report
    report["write_attempted"] = True
    try:
        app.Trimming = True
    except Exception as exc:  # noqa: BLE001 - writability is not proven
        report["write_error"] = str(exc)[:200]
    after = com.probe(app, "Trimming")
    report["after"] = bool(after["value"]) if after["status"] == "read" else None
    report["effective"] = report["after"]
    return report


def _read_precision(app: Any) -> dict[str, Any]:
    """Try to report the precision setting, which drives the station count.

    ``Preferences.Precision`` is documented as *"Sets the app's level of
    precision"* with no return value and an ``msSurfacePrecision`` argument, so
    it is a put-only parameterised property: the station count that governs
    hydrostatic accuracy cannot be read back through automation. Attempted
    anyway, and reported as unavailable rather than omitted, because a caller
    comparing two runs needs to know this was not verified.
    """
    preferences = com.probe(app, "Preferences")
    if preferences["status"] != "read":
        return {"value": None, "status": preferences["status"],
                "detail": "Application.Preferences is not reachable"}
    probed = com.probe(preferences["value"], "Precision")
    if probed["status"] == "read" and not callable(probed["value"]):
        return {"value": probed["value"], "status": "read", "detail": None}
    return {
        "value": None,
        "status": "unavailable",
        "detail": "Preferences.Precision is documented as a put-only "
                  "parameterised property (msSurfacePrecision argument, no "
                  "return value), so the active precision cannot be read back",
    }


def _visible_surfaces(design: Any) -> dict[str, Any]:
    """Report which surfaces the solver could see, grouped by assembly.

    Hydrostatics integrates visible surfaces only. On a model carrying a deck,
    superstructure, thruster, duct, skeg and tanks alongside the hull, a
    displacement figure means nothing until it is known which of those were
    switched on.
    """
    surfaces = com.probe(design, "Surfaces")
    if surfaces["status"] != "read":
        return {"status": surfaces["status"], "surfaces": [], "assemblies": {},
                "detail": "Design.Surfaces is not reachable"}
    collection = surfaces["value"]
    listed: list[dict[str, Any]] = []
    try:
        count = int(collection.Count)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "surfaces": [], "assemblies": {},
                "detail": str(exc)[:200]}
    for index in range(1, count + 1):
        surface = collection(index)
        entry: dict[str, Any] = {"index": index}
        for key, member in (("name", "Name"), ("assembly", "AssemblyName"),
                            ("visible", "Visible"), ("use", "Use")):
            probed = com.probe(surface, member)
            entry[key] = probed["value"] if probed["status"] == "read" else None
        entry["visible"] = None if entry["visible"] is None \
            else bool(entry["visible"])
        listed.append(entry)

    assemblies: dict[str, Any] = {}
    for entry in listed:
        name = entry["assembly"] or "(unnamed)"
        bucket = assemblies.setdefault(name, {"total": 0, "visible": 0})
        bucket["total"] += 1
        if entry["visible"]:
            bucket["visible"] += 1
    return {
        "status": "read",
        "count": count,
        "visible_count": sum(1 for e in listed if e["visible"]),
        "surfaces": listed,
        "assemblies": assemblies,
        "note": "hydrostatics integrates visible surfaces only",
    }


@guarded(classification=Classification.EXECUTE_ANALYSIS, module="hydrostatics",
         channel=Channel.MCP_COM)
def calculate_hydrostatics(density: float = 1025.0, vcg: float = 0.0) -> str:
    """EXECUTE_ANALYSIS. Run Modeler hydrostatics and return the proven results.

    density is kg/m^3 and vcg is metres; Displacement comes back in kg, Volume
    in m^3 and every length in metres. The BentleyModeler automation interface
    is SI whatever units the Maxsurf Modeler window is set to display.

    Before calculating, trimming is switched on, because the Modeler manual
    states it changes hydrostatic results and not merely the drawing. The
    envelope records the trimming state, the precision setting if it can be
    read, and which surfaces were visible, since hydrostatics integrates visible
    surfaces only and a displacement figure is meaningless without knowing which
    assemblies were switched on.

    validation_status is suspect when any physical check fails; the codes are in
    validation_reasons. convention_status is always unknown: the dialog that
    picks the coefficient length (LWL or LPP) and the LCB/LCF origin and sign is
    not exposed on IHydrostatics, so results are only comparable within a single
    session with the settings left alone.
    """
    density = float(density)
    vcg = float(vcg)
    if not math.isfinite(density) or density <= 0:
        raise MaxsurfValidationError(
            "density must be finite and positive, in kg/m^3", density=density
        )
    if not math.isfinite(vcg):
        raise MaxsurfValidationError("vcg must be finite, in metres", vcg=vcg)

    app = com.connect("modeler")
    design = com.probe(app, "Design")
    if design["status"] != "read":
        raise MaxsurfConnectionError(
            "no Design object is available; open a design first",
            module="modeler",
        )
    hydrostatics = com.probe(design["value"], "Hydrostatics")
    if hydrostatics["status"] != "read":
        raise MaxsurfConnectionError(
            "Design.Hydrostatics is not reachable", module="modeler",
        )

    trimming = _set_trimming(app)
    precision = _read_precision(app)
    surfaces = _visible_surfaces(design["value"])

    solver: dict[str, Any] = {"status": SOLVER_OK, "returned": None,
                              "error": None}
    try:
        # The help prints "Return Type: A Boolean value" on every method page in
        # all four modules, including Exit and DeleteAllLines, so it is template
        # text. The value is recorded and never used as a success signal.
        solver["returned"] = hydrostatics["value"].Calculate(density, vcg)
    except Exception as exc:  # noqa: BLE001
        solver["status"] = SOLVER_FAILED
        solver["error"] = str(exc)[:300]

    results = _read_results(hydrostatics["value"]) if solver["status"] == SOLVER_OK \
        else {}
    validation = _validate(results, density, vcg) if results else {
        "validation_status": VALIDATION_SUSPECT,
        "validation_reasons": ["solver_failed"],
        "density_closure_relative_error": None,
        "unreadable_fields": list(RESULT_FIELDS),
    }

    return com.to_json({
        "module": "modeler",
        "via": "Design.Hydrostatics.Calculate",
        "inputs": {"density": density, "density_unit": "kg/m^3",
                   "vcg": vcg, "vcg_unit": "m"},
        "vcg_contract": {
            "status": "neutral" if abs(vcg) <= 1e-12 else "unproven",
            "detail": "vcg=0 does not depend on an unresolved sign transform"
            if abs(vcg) <= 1e-12 else
            "live Maxsurf 2025 returned KG=-vcg with the baseline active; "
            "do not use KG, GM or RM as a stability conclusion until the "
            "vertical-coordinate transform is proven for this frame",
        },
        "solver_status": solver["status"],
        "solver_returned": solver["returned"],
        "solver_error": solver["error"],
        **validation,
        "results": results,
        "preconditions": {
            "trimming": trimming,
            "precision": precision,
            "visibility": surfaces,
        },
        "convention_status": "unknown",
        "convention_detail": (
            "the Hydrostatic Coefficient Calculation Parameters dialog selects "
            "the coefficient length (LWL or LPP) and the LCB/LCF origin and "
            "sign convention. Those settings are not exposed on IHydrostatics "
            "and cannot be read through automation, so two results are only "
            "comparable when they come from the same session with the dialog "
            "untouched"
        ),
        "warnings": _envelope_warnings(trimming, precision, surfaces,
                                       validation),
    })


def _envelope_warnings(trimming: dict[str, Any], precision: dict[str, Any],
                       surfaces: dict[str, Any],
                       validation: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if trimming["effective"] is not True:
        warnings.append(
            f"trimming is not confirmed on (state {trimming['effective']!r}); "
            f"the Modeler manual states trimming changes hydrostatic results, "
            f"so an untrimmed model can integrate geometry the hull does not "
            f"have"
        )
    if precision["status"] != "read":
        warnings.append(
            "the precision setting could not be read, so the station count "
            "behind these results is unknown and two runs cannot be shown to "
            "have used the same discretisation"
        )
    if surfaces.get("status") == "read":
        hidden = surfaces["count"] - surfaces["visible_count"]
        if hidden:
            warnings.append(
                f"{hidden} of {surfaces['count']} surfaces were hidden and did "
                f"not contribute; see preconditions.visibility.assemblies for "
                f"which parts of the model these results describe"
            )
    else:
        warnings.append(
            "the surface list could not be read, so it is unknown which "
            "geometry these results describe"
        )
    if validation["validation_reasons"]:
        warnings.append(
            f"physical validation failed: "
            f"{', '.join(validation['validation_reasons'])}"
        )
    warnings.append(
        "coefficient length and LCB/LCF sign convention are not readable "
        "through automation; see convention_detail"
    )
    return warnings


TOOLS = (calculate_hydrostatics,)


def register_tools(mcp: Any) -> list[str]:
    """Register hull summary and hydrostatics tools."""
    return register(mcp, TOOLS)
