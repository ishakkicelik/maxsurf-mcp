"""Bentley Resistance transfer, configuration, execution and results.

The 2025 automation library exposes a complete, licensed analysis path:

``Modeler Design.SaveAs -> Resistance DesignOpen -> MeasureHull ->
CalculateResistance -> ResistanceResults``.

COM values stay in documented SI units.  User-facing speed arguments and
readbacks also include knots because the Resistance GUI presents knots.
Applicability checks are engineering warnings from the Resistance manual, not
class approval or a substitute for model testing/CFD.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

from . import audit, com, config, workspace
from . import validation as val
from . import constants as comconstants
from .audit import Channel, Classification
from .errors import (
    MaxsurfAnalysisError,
    MaxsurfCOMError,
    MaxsurfValidationError,
)
from .guard import guarded, register


MPS_PER_KNOT = 0.5144444444444445
TRANSFER_SUBDIR = "transfer"

RESULT_FIELDS: tuple[str, ...] = (
    "v", "Fn", "Rt", "Power", "Rf", "Rr", "Rv", "Rw",
    "Ct", "Cf", "Cr", "Cv", "Cw", "Ccorr", "TrimRun",
)
RESULT_UNITS = {
    "v": "m/s", "Fn": "-", "Rt": "N", "Power": "W",
    "Rf": "N", "Rr": "N", "Rv": "N", "Rw": "N",
    "Ct": "-", "Cf": "-", "Cr": "-", "Cv": "-", "Cw": "-",
    "Ccorr": "-", "TrimRun": "deg",
}

VESSEL_FIELDS: tuple[str, ...] = (
    "LWL", "Beam", "Draft", "DraftFP", "DisplacedVolume",
    "PrismaticCoeff", "WaterplaneAreaCoeff", "WettedArea",
    "MaxSectArea", "HalfAngleOfEntrance", "LCGFromMidships",
    "BulbTransverseArea", "BulbHeightFromKeel", "Deadrise",
    "TransomArea", "TransomBeamWL", "TransomDraft", "WaterDensity",
    "KinematicViscosity", "CorrelationAllowance", "AppendageArea",
    "NominalAppendageLength", "AppendageFactor", "FrontalArea",
    "DragCoeff", "AirDensity", "Headwind",
)
VESSEL_UNITS = {
    "LWL": "m", "Beam": "m", "Draft": "m", "DraftFP": "m",
    "DisplacedVolume": "m^3", "PrismaticCoeff": "-",
    "WaterplaneAreaCoeff": "-", "WettedArea": "m^2",
    "MaxSectArea": "m^2", "HalfAngleOfEntrance": "deg",
    "LCGFromMidships": "m", "BulbTransverseArea": "m^2",
    "BulbHeightFromKeel": "m", "Deadrise": "deg", "TransomArea": "m^2",
    "TransomBeamWL": "m", "TransomDraft": "m", "WaterDensity": "kg/m^3",
    "KinematicViscosity": "m^2/s", "CorrelationAllowance": "-",
    "AppendageArea": "m^2", "NominalAppendageLength": "m",
    "AppendageFactor": "-", "FrontalArea": "m^2", "DragCoeff": "-",
    "AirDensity": "kg/m^3", "Headwind": "m/s",
}

def _scalar(obj: Any, member: str, cast: Any = None) -> dict[str, Any]:
    probed=com.probe(obj,member)
    if probed["status"]!="read":
        return {"value":None,"status":probed["status"],"detail":probed.get("detail")}
    try:
        value=cast(probed["value"]) if cast else probed["value"]
        if isinstance(value,float) and not math.isfinite(value):
            return {"value":None,"status":"non_finite"}
    except (TypeError,ValueError):
        return {"value":None,"status":"unreadable"}
    return {"value":value,"status":"read"}


def _constant(app: Any, name: str) -> int:
    discovered = comconstants.discover(app, name_filter=name)
    value = discovered.get("constants", {}).get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise MaxsurfValidationError(
            "Resistance method constant could not be resolved from the live "
            "or generated type library",
            method=name, source=discovered.get("source"),
        )
    return int(value)


def _method_catalog(app: Any) -> dict[str, int]:
    discovered = comconstants.discover(app, name_filter="hsMT")
    return {
        name: int(value)
        for name, value in discovered.get("constants", {}).items()
        if name.startswith("hsMT") and isinstance(value, int)
        and not isinstance(value, bool)
    }


def _require_ready(app: Any, *, loaded: bool = True) -> Any:
    initialized = _scalar(app, "IsInitializedCorrectly", bool)
    if initialized["status"] != "read" or initialized["value"] is not True:
        raise MaxsurfCOMError(
            "Resistance automation is not initialized correctly",
            initialized=initialized,
        )
    design = app.Design
    if loaded:
        path = _scalar(design, "DesignPath", str)
        if path["status"] != "read" or not path["value"]:
            raise MaxsurfValidationError(
                "Resistance has no loaded design; transfer a non-empty Modeler "
                "design first",
                design_path=path,
            )
    return design


def _selected_methods(app: Any, design: Any) -> list[dict[str, Any]]:
    selected = []
    for name, value in sorted(_method_catalog(app).items(), key=lambda x: x[1]):
        try:
            is_selected = bool(design.Methods.IsSelected(value))
        except Exception as exc:  # noqa: BLE001
            raise MaxsurfCOMError("Cannot verify resistance method selection",
                                  method=name, detail=str(exc)) from exc
        if is_selected:
            selected.append({"name": name, "value": value, "selected": True,
                             "status": "read"})
    return selected


def _vessel_snapshot(design: Any) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for field in VESSEL_FIELDS:
        entry = _scalar(design.Vessel, field, float)
        entry["unit"] = VESSEL_UNITS[field]
        snapshot[field] = entry
    return snapshot








def _digest(path: Any) -> dict[str, Any]:
    data = path.read_bytes()
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _parse_methods(methods_json: str) -> list[str]:
    try:
        methods = json.loads(methods_json)
    except ValueError as exc:
        raise MaxsurfValidationError(
            "methods_json must be a JSON list of hsMT method names",
            head=methods_json[:200],
        ) from exc
    if not isinstance(methods, list) or not methods:
        raise MaxsurfValidationError("methods_json must be a non-empty JSON list")
    if any(not isinstance(name, str) or not name.startswith("hsMT")
           for name in methods):
        raise MaxsurfValidationError(
            "every method must be a string beginning with hsMT", methods=methods)
    if len(set(methods)) != len(methods):
        raise MaxsurfValidationError("methods_json contains duplicates")
    return methods


def _set_select(methods: Any, method: int, selected: bool) -> None:
    setter = getattr(methods, "SetSelect", None)
    if callable(setter):
        setter(method, selected)
    else:
        methods.Select(method, selected)


@guarded(classification=Classification.READ, module="resistance",
         channel=Channel.MCP_COM)
def get_resistance_status() -> str:
    """READ-ONLY. Report Resistance readiness, design, speeds and methods."""
    app = com.connect("resistance")
    design = app.Design
    initialized = _scalar(app, "IsInitializedCorrectly", bool)
    license_level = _scalar(app, "LicenseLevel", str)
    version = _scalar(app, "Version", str)
    design_path = _scalar(design, "DesignPath", str)
    design_name = _scalar(design, "DesignName", str)

    velocities = design.Velocities
    low, high = (_scalar(velocities, name, float) for name in ("Low", "High"))
    for entry in (low, high):
        entry["unit"] = "m/s"
        if entry["status"] == "read":
            entry["knots"] = entry["value"] / MPS_PER_KNOT

    methods = []
    for name, value in sorted(_method_catalog(app).items(), key=lambda x: x[1]):
        try:
            methods.append({"name": name, "value": value,
                            "selected": bool(design.Methods.IsSelected(value)),
                            "status": "read"})
        except Exception as exc:  # noqa: BLE001
            methods.append({"name": name, "value": value, "selected": None,
                            "status": "unreadable", "detail": str(exc)[:200]})

    ready = initialized["status"] == "read" and initialized["value"] is True
    loaded = design_path["status"] == "read" and bool(design_path["value"])
    warnings = []
    if not ready:
        warnings.append("Resistance automation is not initialized correctly")
    if not loaded:
        warnings.append("Resistance automation reports no loaded design")
    return com.to_json({
        "module": "resistance", "automation_ready": ready,
        "automation_initialized": initialized, "license_level": license_level,
        "version": version,
        "design": {"loaded": loaded, "path": design_path, "name": design_name},
        "velocities": {"low": low, "high": high,
                       "com_unit": "m/s", "gui_unit": "kn"},
        "methods": methods,
        "analysis_available": ready,
        "analysis_blocker": None if ready else "automation_not_initialized",
        "warnings": warnings,
    })


@guarded(classification=Classification.WRITE_FILE, module="resistance",
         channel=Channel.MCP_COM)
def transfer_modeler_to_resistance(overwrite: bool = False,
                                  confirm_replace: bool = False) -> str:
    """Save a unique Modeler copy and verify the destination design path.

    Save destination edits first. confirm_replace=true explicitly authorizes
    replacing them. The source file on disk is not overwritten.
    """
    from .transfer import transfer
    return transfer("resistance", overwrite=overwrite, confirm_replace=confirm_replace)


@guarded(classification=Classification.WRITE_STATE, module="resistance", channel=Channel.MCP_COM)
def configure_resistance_analysis(low_knots: float, high_knots: float,
    methods_json: str, replace: bool = False,
    slender_body_one_plus_k: float | None = None) -> str:
    """Set explicit speed limits and type-library method names; verify readback."""
    low=val.number(low_knots,"low_knots",minimum=0)
    high=val.number(high_knots,"high_knots",positive=True)
    if high<=low:
        raise MaxsurfValidationError("high_knots must exceed low_knots")
    requested=_parse_methods(methods_json)
    factor=None if slender_body_one_plus_k is None else val.number(
        slender_body_one_plus_k,"slender_body_one_plus_k",positive=True)
    app=com.connect("resistance")
    design=_require_ready(app)
    catalog=_method_catalog(app)
    unknown=set(requested)-set(catalog)
    if unknown:
        raise MaxsurfValidationError("unavailable methods",names=sorted(unknown))
    if replace:
        design.Methods.SelectAll=False
    for method in requested:
        _set_select(design.Methods,catalog[method],True)
    if factor is not None:
        val.stored(design.Methods,"SlenderBodyOnePlusK",factor)
    val.stored(design.Velocities,"Low",low*MPS_PER_KNOT)
    val.stored(design.Velocities,"High",high*MPS_PER_KNOT)
    selected=_selected_methods(app,design)
    selected_names={m["name"] for m in selected}
    if not set(requested)<=selected_names or (replace and selected_names!=set(requested)):
        raise MaxsurfCOMError("method selection readback mismatch")
    return com.to_json({"module":"resistance","methods":selected,
        "speeds":{"low_knots":low,"high_knots":high},"verified":True})


def _result_indices(results: Any, count: int) -> tuple[list[int], int]:
    errors: list[str] = []
    for base in (0, 1):
        indices = list(range(base, base + count))
        try:
            velocities = [float(results.Velocity(index)) for index in indices]
        except Exception as exc:  # noqa: BLE001
            errors.append(f"base {base}: {str(exc)[:120]}")
            continue
        if all(math.isfinite(value) for value in velocities):
            return indices, base
    raise MaxsurfCOMError(
        "Resistance result collection index base could not be proven",
        count=count, attempts=errors,
    )


def _read_result(result: Any) -> dict[str, dict[str, Any]]:
    row: dict[str, dict[str, Any]] = {}
    for field in RESULT_FIELDS:
        entry = _scalar(result, field, float)
        entry["unit"] = RESULT_UNITS[field]
        if field == "v" and entry["status"] == "read":
            entry["knots"] = entry["value"] / MPS_PER_KNOT
        row[field] = entry
    return row


def _efficiency_percent(design: Any) -> float | None:
    """Overall propulsive efficiency the Resistance model currently holds.

    Read live rather than assumed. The Resistance default is 100 %, which
    means effective power equals installed power, i.e. no propulsion losses
    -- exactly the kind of silent default that turns a preliminary powering
    estimate into an under-prediction. The analysis tool sets a real value;
    this reader reports whatever is stored so the caller can see it.
    """
    probed = _scalar(design.Efficiency, "Efficiency", float)
    return probed["value"] if probed["status"] == "read" else None


def _read_results(app: Any, design: Any, max_points: int) -> dict[str, Any]:
    selected = _selected_methods(app, design)
    if not selected:
        raise MaxsurfValidationError(
            "no Resistance methods are selected; configure the analysis first")
    results = design.ResistanceResults
    count = int(results.Count)
    if count <= 0:
        raise MaxsurfAnalysisError(
            "Resistance returned no result points", result_count=count)
    if count > max_points:
        raise MaxsurfValidationError(
            "result count exceeds max_points; increase it explicitly",
            result_count=count, max_points=max_points,
        )
    indices, base = _result_indices(results, count)

    eff_pct = _efficiency_percent(design)

    method_rows = []
    for method in selected:
        rows = []
        for index in indices:
            row = _read_result(results.Item(method["value"], index))
            if any(row[f]["status"] != "read" for f in ("v", "Rt", "Power")):
                raise MaxsurfAnalysisError("required Resistance outputs are non-finite or unreadable")
            collection_v = float(results.Velocity(index))
            row["collection_velocity"] = {
                "value": collection_v, "unit": "m/s",
                "knots": collection_v / MPS_PER_KNOT, "status": "read",
            }
            # Effective power is Rt*v and does not depend on efficiency.
            # Maxsurf's own Power field is the installed/delivered power: at
            # 100% efficiency it equals Rt*v, and at 65% it equals Rt*v/0.65,
            # so the field already incorporates the efficiency. Installed
            # power is therefore read straight from that field and effective
            # power is recomputed from Rt*v -- never divided a second time.
            rt_entry = row["Rt"]
            if rt_entry["status"] == "read":
                pe_val = float(rt_entry["value"]) * collection_v
                row["effective_power"] = {
                    "value": pe_val, "unit": "W", "kW": pe_val / 1000.0,
                    "basis": "Rt * v", "status": "read"}
            else:
                row["effective_power"] = {"value": None, "unit": "W",
                                          "status": "unavailable"}
            pd = row["Power"]
            row["installed_power"] = {
                "value": float(pd["value"]) if pd["status"] == "read" else None,
                "unit": "W",
                "kW": (float(pd["value"]) / 1000.0
                       if pd["status"] == "read" else None),
                "basis": ("Maxsurf Power field, which already includes the "
                          "propulsive efficiency"),
                "status": pd["status"]}
            row["index"] = index
            rows.append(row)

        def series(field: str) -> list[float]:
            return [float(row[field]["value"]) for row in rows
                    if row[field]["status"] == "read"
                    and math.isfinite(float(row[field]["value"]))]

        rt = series("Rt")
        effective = series("effective_power")
        installed = series("installed_power")
        method_rows.append({
            "method": method,
            "point_count": len(rows),
            "summary": {
                "max_total_resistance_N": max(rt) if rt else None,
                "max_effective_power_W": max(effective) if effective else None,
                "max_effective_power_kW": (max(effective) / 1000.0
                                           if effective else None),
                "max_installed_power_W": max(installed) if installed else None,
                "max_installed_power_kW": (max(installed) / 1000.0
                                           if installed else None),
                "max_speed_knots": max(
                    row["collection_velocity"]["knots"] for row in rows
                ),
            },
            "rows": rows,
        })
    return {
        "result_count_per_method": count, "index_base": base,
        "propulsion": {
            "efficiency_percent": eff_pct,
            "power_basis": (
                "effective power = Rt * v (efficiency-independent); "
                "reported power is Maxsurf's Power field, which "
                "already includes the efficiency, so effective = reported * "
                "(efficiency/100)"
            ),
        },
        "methods": method_rows,
    }


@guarded(classification=Classification.EXECUTE_ANALYSIS, module="resistance", channel=Channel.MCP_COM)
def run_resistance_analysis(
    water_density_kg_m3: float, kinematic_viscosity_m2_s: float,
    correlation_allowance: float, propulsive_efficiency_percent: float,
    measure_hull: bool = True, appendage_area_m2: float = 0.0,
    nominal_appendage_length_m: float = 0.0, appendage_factor: float = 1.0,
    frontal_area_m2: float = 0.0, drag_coefficient: float = 0.0,
    air_density_kg_m3: float = 0.0, headwind_m_per_s: float = 0.0,
    max_points: int = 500,
) -> str:
    """Measure and run the operator-selected native Resistance methods.

    Density, viscosity, correlation allowance and efficiency are required.
    No method-selection guidance, vessel recipe or class rules are embedded.
    Optional zero areas explicitly disable those extra resistance allowances.
    """
    settings={
        "WaterDensity":val.number(water_density_kg_m3,"density",positive=True),
        "KinematicViscosity":val.number(kinematic_viscosity_m2_s,"viscosity",positive=True),
        "CorrelationAllowance":val.number(correlation_allowance,"correlation",minimum=0),
        "AppendageArea":val.number(appendage_area_m2,"appendage_area",minimum=0),
        "NominalAppendageLength":val.number(nominal_appendage_length_m,"appendage_length",minimum=0),
        "AppendageFactor":val.number(appendage_factor,"appendage_factor",minimum=0),
        "FrontalArea":val.number(frontal_area_m2,"frontal_area",minimum=0),
        "DragCoeff":val.number(drag_coefficient,"drag_coefficient",minimum=0),
        "AirDensity":val.number(air_density_kg_m3,"air_density",minimum=0),
        "Headwind":val.number(headwind_m_per_s,"headwind"),
    }
    efficiency=val.number(propulsive_efficiency_percent,"efficiency",positive=True,maximum=100)
    max_points=int(val.number(max_points,"max_points",positive=True,maximum=10000))
    app=com.connect("resistance")
    design=_require_ready(app)
    if not _selected_methods(app,design):
        raise MaxsurfValidationError("select methods before calculating")
    if measure_hull:
        design.MeasureHull()
    for field,value in settings.items():
        val.stored(design.Vessel,field,value)
    val.stored(design.Efficiency,"Efficiency",efficiency)
    try:
        design.CalculateResistance()
        results=_read_results(app,design,max_points)
    except Exception as exc:
        raise MaxsurfAnalysisError("Resistance analysis failed",detail=str(exc)) from exc
    return com.to_json({"module":"resistance","solver_status":"ok",
        "run_id":audit.new_id(),"measured_hull":measure_hull,
        "settings":settings,"propulsive_efficiency_percent":efficiency,
        "vessel":_vessel_snapshot(design),"results":results,
        "engineering_verdict":"not_evaluated",
        "warnings":["Method applicability is the operator's responsibility."]})


@guarded(classification=Classification.READ, module="resistance",
         channel=Channel.MCP_COM)
def get_resistance_results(max_points: int = 500) -> str:
    """READ-ONLY. Return the current Resistance result collection in SI."""
    if int(max_points) <= 0:
        raise MaxsurfValidationError("max_points must be positive")
    app = com.connect("resistance")
    design = _require_ready(app)
    selected = _selected_methods(app, design)
    return com.to_json({
        "module": "resistance", "vessel": _vessel_snapshot(design),
        "freshness": "current_application_state_not_independently_verified",
        "results": _read_results(app, design, int(max_points)),
        "engineering_scope": "preliminary calm-water prediction",
    })


@guarded(classification=Classification.WRITE_FILE, module="resistance",
         channel=Channel.MCP_COM)
def export_resistance_results(path: str, overwrite: bool = False,
                              max_points: int = 500) -> str:
    """WRITE_FILE. Write the current speed-resistance-power curve to CSV.

    The Resistance result collection exposes no table-export member, so the
    curve is read point by point and written here: one row per method and
    speed, with total resistance, its components, effective power and the
    derived installed power. The target must be a ``.csv`` inside the
    configured workspace sandbox and is never allowed to overwrite silently.
    """
    if int(max_points) <= 0:
        raise MaxsurfValidationError("max_points must be positive")
    target = workspace.resolve_target(
        path, purpose="export_resistance_results", overwrite=overwrite,
        allowed_suffixes=(".csv",),
    )
    app = com.connect("resistance")
    design = _require_ready(app)
    results = _read_results(app, design, int(max_points))
    efficiency = results["propulsion"]["efficiency_percent"]

    columns = ["method", "index", "speed_kn", "speed_ms", "Fn",
               "Rt_N", "Rf_N", "Rr_N", "Rw_N", "Ct",
               "effective_power_kW", "installed_power_kW", "trim_deg"]

    def cell(row: dict[str, Any], field: str, key: str = "value") -> Any:
        entry = row.get(field) or {}
        value = entry.get(key)
        return "" if value is None else value

    rows_written = 0
    with open(target, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for method in results["methods"]:
            name = method["method"]["name"]
            for row in method["rows"]:
                effective = row["effective_power"]
                installed = row["installed_power"]
                writer.writerow([
                    name, row.get("index"),
                    cell(row, "collection_velocity", "knots"),
                    cell(row, "collection_velocity", "value"),
                    cell(row, "Fn"), cell(row, "Rt"), cell(row, "Rf"),
                    cell(row, "Rr"), cell(row, "Rw"), cell(row, "Ct"),
                    (effective["value"] / 1000.0
                     if effective["status"] == "read" else ""),
                    (installed["value"] / 1000.0
                     if installed["status"] == "read" else ""),
                    cell(row, "TrimRun"),
                ])
                rows_written += 1
    return com.to_json({
        "module": "resistance",
        "file": {"path": str(target), **_digest(target)},
        "rows_written": rows_written,
        "methods": [m["method"]["name"] for m in results["methods"]],
        "points_per_method": results["result_count_per_method"],
        "propulsive_efficiency_percent": efficiency,
        "columns": columns,
        "engineering_scope": "preliminary calm-water prediction",
        "warnings": [],
    })


@guarded(classification=Classification.EXECUTE_ANALYSIS, module="resistance",
         channel=Channel.MCP_COM)
def calculate_free_surface(path: str, speed_knots: float,
                           overwrite: bool = False,
                           grid_longitudinal: int = 160,
                           grid_transverse: int = 80,
                           extent_fwd: float = 1.0, extent_aft: float = 3.0,
                           extent_side: float = 2.0) -> str:
    """EXECUTE_ANALYSIS. Compute the free-surface wave pattern and write the
    wave-elevation grid to a CSV.

    This is the thin-ship (Slender Body) free-surface calculation, not a
    statistical method: it produces the wave field around the hull, the
    coloured pattern the Resistance GUI shows. The method is selected here
    because Holtrop and the other regressions carry no wave field. The CSV is
    long format ``x,y,elevation`` in metres, ready to contour. The target must
    be a ``.csv`` inside the workspace sandbox.
    """
    speed = float(speed_knots)
    if speed <= 0:
        raise MaxsurfValidationError("speed_knots must be positive",
                                     speed_knots=speed)
    n_long, n_trans = int(grid_longitudinal), int(grid_transverse)
    if not 8 <= n_long <= 400 or not 8 <= n_trans <= 200:
        raise MaxsurfValidationError(
            "grid must be 8-400 longitudinal by 8-200 transverse",
            grid_longitudinal=n_long, grid_transverse=n_trans)
    target = workspace.resolve_target(
        path, purpose="calculate_free_surface", overwrite=overwrite,
        allowed_suffixes=(".csv",))

    app = com.connect("resistance")
    design = _require_ready(app)
    catalog = _method_catalog(app)
    if "hsMTSlenderBody" not in catalog:
        raise MaxsurfValidationError(
            "the Slender Body method, which drives the free-surface field, is "
            "not available in this Resistance build", available=sorted(catalog))
    _set_select(design.Methods, catalog["hsMTSlenderBody"], True)
    # The free-surface field is built from the thin-ship source strengths the
    # Slender Body resistance run computes, so the hull must be measured and
    # the resistance calculated at the target speed before the wave field can
    # be asked for -- calling CalculateFreeSurface cold raises "error
    # calculating the free-surface".
    design.MeasureHull()
    design.Velocities.Low = max(speed * MPS_PER_KNOT * 0.5, 0.1)
    design.Velocities.High = speed * MPS_PER_KNOT
    design.CalculateResistance()

    fsp = design.FreeSurfaceParameters
    fsp.Velocity = speed * MPS_PER_KNOT
    fsp.GridPointsLongitudinal = n_long
    fsp.GridPointsTransverse = n_trans
    fsp.GridExtentsShipLengthsFwd = float(extent_fwd)
    fsp.GridExtentsShipLengthsAft = float(extent_aft)
    fsp.GridExtentsShipLengthsPort = float(extent_side)
    fsp.GridExtentsShipLengthsStbd = float(extent_side)
    fsp.GridExtentsMirrorPS = True

    design.CalculateFreeSurface()
    res = design.FreeSurfaceResults

    # Prove the index base rather than assume it (the result collection was
    # 1-based; the free-surface grid is proven the same way here).
    base = None
    for candidate in (1, 0):
        try:
            float(res.Position(candidate))
            float(res.Elevation(candidate, candidate))
            base = candidate
            break
        except Exception:  # noqa: BLE001
            continue
    if base is None:
        raise MaxsurfCOMError("free-surface grid index base could not be proven")

    xs = [float(res.Position(base + i)) for i in range(n_long)]
    ys = [float(res.Offset(base + j)) for j in range(n_trans)]
    lo = hi = None
    written = 0
    with open(target, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["x_m", "y_m", "elevation_m"])
        for i in range(n_long):
            for j in range(n_trans):
                z = float(res.Elevation(base + i, base + j))
                writer.writerow([round(xs[i], 4), round(ys[j], 4),
                                 round(z, 5)])
                written += 1
                lo = z if lo is None else min(lo, z)
                hi = z if hi is None else max(hi, z)

    fn = float(fsp.Fn)
    return com.to_json({
        "module": "resistance", "analysis": "free_surface",
        "method": "hsMTSlenderBody (thin-ship)",
        "speed_knots": speed, "froude_number": fn,
        "grid": {"longitudinal": n_long, "transverse": n_trans,
                 "points": written,
                 "x_range_m": [min(xs), max(xs)],
                 "y_range_m": [min(ys), max(ys)]},
        "wave_elevation_m": {"min": lo, "max": hi,
                             "peak_to_trough": (hi - lo) if lo is not None
                             else None},
        "file": {"path": str(target), **_digest(target)},
        "engineering_scope": (
            "thin-ship wave pattern; a qualitative wave-making picture and "
            "wave resistance comparison, not a viscous CFD free surface"),
        "warnings": [],
    })


TOOLS = (
    get_resistance_status,
    transfer_modeler_to_resistance,
    configure_resistance_analysis,
    run_resistance_analysis,
    get_resistance_results,
    export_resistance_results,
    calculate_free_surface,
)


def register_tools(mcp: Any) -> list[str]:
    """Register the proven Resistance workflow."""
    return register(mcp, TOOLS)
