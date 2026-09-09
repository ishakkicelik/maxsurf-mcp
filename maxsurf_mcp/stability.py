"""Bentley Stability integration: file-mediated transfer and equilibrium.

The transfer boundary is a file, and that is not a design choice. Live
``ITypeInfo`` discovery established that no Stability member accepts a Modeler
``IDesign``, ``IDispatch`` or any other live interface pointer, and that
Stability's only design-loading member is ``Open(sFileName)``. So the current
Modeler geometry has to be written to disk and read back:

```text
Modeler   Design.SaveAs(sFileName, bOverwrite)
Stability Design.Open(sFileName)          <- one argument, unlike Modeler's
```

Note the asymmetry: Modeler's ``Open`` takes ``(sFileName, bMerge, bSave)`` and
Stability's takes the filename alone. Calling either with the other's arity
fails at the COM boundary.

Units on the Stability automation interface are SI and the help says so per
member: ``AddMassItem`` is annotated ``Units: [m], [kg], [kg.m]`` and
``EQResult.Displacement`` is ``[kg]``. The ``tonne`` label the XMLGrid reader
returns is an interface *display* unit.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

from . import audit, com, config, workspace
from . import validation as val
from .errors import MaxsurfSafetyError
from .audit import Channel, Classification
from .errors import (
    MaxsurfAnalysisError,
    MaxsurfCOMError,
    MaxsurfValidationError,
)
from .guard import guarded, register

#: Where transferred designs are written. Inside the runtime sandbox, never
#: beside the user's own file.
TRANSFER_SUBDIR = "transfer"

#: Proven analysis modes (hmAnalysisMode). Resolved by name at call time so the
#: number is never hard-coded into a request.
EQUILIBRIUM_MODE_NAME = "hmAMEquilibrium"
STABILITY_MODE_NAME = "hmAMStability"

#: Proven load-case type (hmLoadcaseType).
LOADCASE_TYPE_NAME = "hmLTLoadcase"

#: The registered XMLGrid coclass reads result tables the IResult collection
#: does not expose cell by cell -- notably the criteria results table, which
#: carries the criterion names that ICriterion itself has no getter for.
STABILITY_XMLGRID_PROGID = "BentleyStability.XMLGrid"

#: Maxsurf ships one .hcr criteria file per authority under this directory.
#: Overridable because the install path carries the version.
CRITERIA_LIBRARY_ENV = "MAXSURF_MCP_CRITERIA_DIR"
DEFAULT_CRITERIA_LIBRARY = (
    r"C:\Program Files\Bentley\Offshore\Maxsurf 2025\bin\win64"
    r"\StabilitySpecificCriteria"
)

#: Large-angle stability result getters proven on IResult.
GZ_FIELDS: tuple[str, ...] = (
    "Converged", "Heel", "GZ", "GZAreaFromZero", "Downflooded", "Displacement",
    "DraftAP", "DraftFP", "Trim", "GMT", "KG",
)

#: Key-point kinds, resolved by name at call time.
KEYPOINT_KINDS = {
    "downflooding": "hmKTDownflooding",
    "potential": "hmKTPotentialDownflooding",
    "potential_downflooding": "hmKTPotentialDownflooding",
    "conditional": "hmKTConditionalFlooding",
    "embarkation": "hmKTEmbarkation",
}

#: EQResult getters read after the analysis. Every name is proven on IResult by
#: live ITypeInfo and recorded in private discovery notes.
EQ_FIELDS: tuple[str, ...] = (
    "Converged", "NumOfIterations",
    "Displacement", "Volume", "DensitySeawater",
    "DraftAP", "DraftFP", "DraftAmidships", "Trim", "TrimAngle", "Heel",
    # Stability's IResult spells these in capitals -- GMT, not GMt.
    # Modeler's IHydrostatics uses GMt/KMt for the same quantities, and
    # reading Stability with Modeler's spelling silently returns nothing.
    "KB", "KG", "GMT", "GML", "KMT", "KML",
    "LCGship", "TCGship", "VCGship", "LCBship",
    "WaterplaneArea", "WettedSurfaceArea",
)

EQ_UNITS = {
    "Displacement": "kg", "Volume": "m^3", "DensitySeawater": "kg/m^3",
    "WaterplaneArea": "m^2", "WettedSurfaceArea": "m^2",
    "Trim": "m", "TrimAngle": "deg", "Heel": "deg",
    "Converged": "-", "NumOfIterations": "-",
}


def _constant(app: Any, name: str) -> int:
    """Resolve one generated constant, refusing rather than guessing a number."""
    from . import constants as comconstants

    discovered = comconstants.discover(app, name_filter=name)
    value = discovered["constants"].get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise MaxsurfValidationError(
            f"the {name} constant could not be resolved from the generated "
            f"type library",
            constant=name, source=discovered.get("source"),
        )
    return int(value)


def _digest(path: Any) -> dict[str, Any]:
    data = path.read_bytes()
    return {"bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


@guarded(classification=Classification.WRITE_FILE, module="stability",
         channel=Channel.MCP_COM)
def transfer_modeler_to_stability(overwrite: bool = False,
                                  confirm_replace: bool = False) -> str:
    """Save a unique Modeler copy and verify the destination design path.

    Save destination edits first. confirm_replace=true explicitly authorizes
    replacing them. The source file on disk is not overwritten.
    """
    from .transfer import transfer
    return transfer("stability", overwrite=overwrite, confirm_replace=confirm_replace)


def _read_equilibrium(result):
    values={}
    for field in EQ_FIELDS:
        entry=_scalar(result,field)
        entry["unit"]=EQ_UNITS.get(field,"m")
        values[field]=entry
    return values


@guarded(classification=Classification.EXECUTE_ANALYSIS, module="stability", channel=Channel.MCP_COM)
def run_stability_equilibrium(loadcase_name: str, mass: float | None = None,
                              lcg: float | None = None, tcg: float = 0.0,
                              vcg: float | None = None) -> str:
    """Run native equilibrium for an existing case, or create a new point-load case.

    If mass is supplied, kg and metre centres must be explicit. An existing
    load case is never overwritten. No stability approval is inferred.
    """
    loadcase_name=val.name(loadcase_name)
    if mass is not None:
        mass=val.number(mass,"mass",positive=True)
        xyz=[val.number(v,k) for k,v in (("lcg",lcg),("tcg",tcg),("vcg",vcg))]
    app=com.connect("stability")
    design=app.Design
    if mass is not None:
        if loadcase_name in _loadcase_names(design):
            raise MaxsurfSafetyError("refusing to overwrite existing loadcase")
        if int(design.Tanks.Count):
            raise MaxsurfValidationError("use define_loadcase with explicit tank fills")
        design.LoadCases.Add(loadcase_name,_constant(app,LOADCASE_TYPE_NAME))
        val.stored(design,"ActiveLoadCaseByName",loadcase_name)
        design.LoadCases(int(design.ActiveLoadCase)).AddMassItem(
            loadcase_name+" total",1,mass,*xyz,0,0,0)
    _select_case(design,loadcase_name,"")
    val.stored(design,"ActiveAnalysisMode",_constant(app,EQUILIBRIUM_MODE_NAME))
    try:
        design.RunAnalysis()
        count=int(design.EquilibriumResults.Count)
        if count<1:
            raise MaxsurfAnalysisError("no equilibrium results")
        results=_read_equilibrium(design.EquilibriumResults(1))
        required=("Converged","Displacement","Volume","DraftAP","DraftFP")
        if any(results[f]["status"]!="read" for f in required):
            raise MaxsurfAnalysisError("required equilibrium outputs are unreadable",results=results)
        if results["Converged"]["value"]!=1:
            raise MaxsurfAnalysisError("equilibrium did not converge",results=results)
        if any(results[f]["value"] <= 0 for f in ("Displacement", "Volume")):
            raise MaxsurfAnalysisError("nonpositive equilibrium displacement or volume", results=results)
    except MaxsurfAnalysisError:
        raise
    except Exception as exc:
        raise MaxsurfAnalysisError("equilibrium solver failed",detail=str(exc)) from exc
    return com.to_json({"module":"stability","loadcase":loadcase_name,
        "damage_case":"Intact","solver_status":"ok","result_count":count,
        "results":results,"engineering_verdict":"not_evaluated"})


#: Room kinds the subdivision tool can create, and the member behind each.
ROOM_KINDS = {
    "tank": "Tanks.Add",
    "compartment": "Compartments.Add",
    "non_buoyant": "Compartments.AddNBV",
}

#: The heel schedule has three segments. Leaving them unset is what produced a
#: 22-row large-angle result whose GZ was zero at every angle.
HEEL_FIELDS = ("StartAngle", "FirstEndAngle", "FirstAngleStep",
               "SecondEndAngle", "SecondAngleStep",
               "ThirdEndAngle", "ThirdAngleStep")


@guarded(classification=Classification.WRITE_STATE, module="stability", channel=Channel.MCP_COM)
def define_subdivision(rooms_json: str, clear_first: bool = False,
                       confirm_replace: bool = False, backup_path: str = "") -> str:
    """Create caller-specified prismatic rooms. Coordinates m, permeability fraction.

    No permeability/fluid/design assumptions are supplied. Replacing rooms
    requires confirm_replace and a new workspace .htk backup path.
    """
    rooms = val.parsed(rooms_json, list, "rooms_json")
    if not rooms:
        raise MaxsurfValidationError("rooms_json must not be empty")
    validated, seen = [], set()
    for room in rooms:
        if not isinstance(room, dict):
            raise MaxsurfValidationError("each room must be an object")
        row = dict(room)
        row["name"] = val.name(row.get("name"))
        if row["name"] in seen:
            raise MaxsurfValidationError("duplicate room name")
        seen.add(row["name"])
        row["kind"] = row.get("kind", "compartment")
        if row["kind"] not in ROOM_KINDS:
            raise MaxsurfValidationError("unknown room kind")
        for key in ("aft","fwd","port","stbd","top","bottom"):
            row[key] = val.number(row.get(key), key)
        if not (row["aft"] < row["fwd"] and row["port"] < row["stbd"]
                and row["bottom"] < row["top"]):
            raise MaxsurfValidationError("room extents must be ordered")
        for key in (("intact_perm","damaged_perm") if row["kind"]=="tank"
                    else ("permeability",)):
            row[key] = val.number(row.get(key), key, minimum=0, maximum=1)
        if "relative_density" in row:
            row["relative_density"] = val.number(row["relative_density"], "relative_density", positive=True)
        validated.append(row)
    app = com.connect("stability")
    design = app.Design
    for row in validated:
        if row.get("fluid"):
            row["fluid_value"] = _constant(app, val.name(row["fluid"], "fluid"))
    tanks, comps = design.Tanks, design.Compartments
    before = {"tanks": int(tanks.Count), "compartments": int(comps.Count)}
    existing = _room_names(design)
    if not clear_first and seen & existing:
        raise MaxsurfValidationError("room names already exist", names=sorted(seen & existing))
    backup = _before_replace(design, clear_first, confirm_replace, backup_path)
    created = []
    try:
        if clear_first:
            tanks.DeleteAll()
            comps.DeleteAll()
        for row in validated:
            args = [row[key] for key in ("name","aft","fwd","port","stbd","top","bottom")]
            if row["kind"] == "tank":
                tanks.Add(*args, row["intact_perm"], row["damaged_perm"])
                item = tanks.ItemByName(row["name"])
                if "fluid_value" in row:
                    val.stored(item, "FluidType", row["fluid_value"])
                if "relative_density" in row:
                    val.stored(item, "RelativeDensity", row["relative_density"])
            else:
                getattr(comps, "AddNBV" if row["kind"]=="non_buoyant" else "Add")(
                    *args, row["permeability"])
            created.append(row["name"])
        missing = seen - _room_names(design)
        if missing:
            raise MaxsurfCOMError("room readback missing", names=sorted(missing))
    except Exception as exc:
        raise MaxsurfCOMError("subdivision update failed", cause=exc,
                              created=created, backup=backup, partial_state_possible=True) from exc
    return com.to_json({"created": created, "count_before": before,
                        "count_after": {"tanks": int(tanks.Count), "compartments": int(comps.Count)},
                        "backup": backup, "verified": True})


@guarded(classification=Classification.WRITE_STATE, module="stability", channel=Channel.MCP_COM)
def set_heel_range(start: float, first_end: float, first_step: float,
                   second_end: float, second_step: float, third_end: float,
                   third_step: float, to_starboard: bool = True) -> str:
    """Set an explicit native heel schedule in degrees; verify every property."""
    schedule=_validated_schedule(dict(start=start,first_end=first_end,first_step=first_step,
        second_end=second_end,second_step=second_step,third_end=third_end,third_step=third_step))
    app=com.connect("stability")
    val.stored(app.Design,"ActiveAnalysisMode",_constant(app,STABILITY_MODE_NAME))
    _set_stability_heel(app.Design,schedule,to_starboard)
    return com.to_json({"heel_schedule":schedule,"to_starboard":to_starboard,"verified":True})


def _criteria_library_dir() -> str:
    return os.environ.get(CRITERIA_LIBRARY_ENV, DEFAULT_CRITERIA_LIBRARY)


def _available_standards(library_dir: str) -> list[str]:
    if not os.path.isdir(library_dir):
        return []
    return sorted(os.path.splitext(name)[0] for name in os.listdir(library_dir)
                  if name.lower().endswith(".hcr"))


def _resolve_hcr(standard: str) -> str:
    """Resolve an installed library name or an allowed workspace .hcr file."""
    from pathlib import Path
    standard = val.name(standard, "standard")
    root = Path(_criteria_library_dir()).resolve()
    if Path(standard).name == standard and "/" not in standard and "\\" not in standard:
        stem = standard[:-4] if standard.lower().endswith(".hcr") else standard
        candidate = (root / (stem + ".hcr")).resolve()
        if candidate.parent == root and candidate.is_file():
            return str(candidate)
    return str(workspace.resolve_existing(standard, purpose="criteria import", allowed_suffixes=(".hcr",)))


def _read_xmlgrid_table(table_value: int, max_rows: int = 2000) -> dict[str, Any]:
    """Read a whole Stability result table through the XMLGrid coclass.

    The index base is not assumed: headings are read at base 0 then 1, and the
    base that yields a non-empty first heading is used for the cell body too.
    """
    com.connect("stability")
    grid = com.connect("stability_xmlgrid", progid=STABILITY_XMLGRID_PROGID)
    grid.SetCurrentGrid(int(table_value))
    for populate in ("PopulateWithActive", "Populate"):
        try:
            getattr(grid, populate)()
            break
        except Exception:  # noqa: BLE001 - try the next filler
            continue
    ncol, nrow = int(grid.GetNoOfColumns()), int(grid.GetNoOfRows())

    def headings(base: int) -> list[str]:
        return [str(grid.ColumnHeading(base + i) or "") for i in range(ncol)]

    base, cols = 0, []
    for candidate_base in (0, 1):
        try:
            cols = headings(candidate_base)
            if any(text.strip() for text in cols):
                base = candidate_base
                break
        except Exception:  # noqa: BLE001
            continue
    rows = []
    for r in range(base, base + min(nrow, max_rows)):
        line = []
        for c in range(base, base + ncol):
            try:
                line.append(str(grid.CellString(r, c) or ""))
            except Exception:  # noqa: BLE001
                line.append("")
        rows.append(line)
    return {"columns": cols, "rows": rows,
            "row_count": nrow, "column_count": ncol}


def _set_stability_heel(design, schedule, to_starboard):
    values=_validated_schedule(schedule)
    for key,member in zip(("start","first_end","first_step","second_end","second_step","third_end","third_step"),HEEL_FIELDS):
        val.stored(design.Heel,member,values[key])
    val.stored(design.Heel,"HeelToStarboard",bool(to_starboard))




@guarded(classification=Classification.WRITE_STATE, module="stability", channel=Channel.MCP_COM)
def define_loadcase(name: str, tank_fills_json: str, mass_items_json: str,
                    clear_first: bool = False, confirm_replace: bool = False,
                    backup_path: str = "") -> str:
    """Create an explicitly specified load case. Mass t, lengths m, FSM t.m.

    Every defined tank must have an explicit fill percentage; no hidden fill
    defaults. Existing case replacement requires a backup and confirmation.
    """
    case_name = val.name(name)
    fills = val.parsed(tank_fills_json, dict, "tank_fills_json")
    items = val.parsed(mass_items_json, list, "mass_items_json")
    fills = {val.name(k): val.number(v,k,minimum=0,maximum=100) for k,v in fills.items()}
    mass_rows, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise MaxsurfValidationError("mass items must be objects")
        item_name=val.name(item.get("name"))
        if item_name in seen:
            raise MaxsurfValidationError("duplicate mass item name")
        seen.add(item_name)
        mass=val.number(item.get("mass_t"),"mass_t",positive=True)*1000
        xyz=[val.number(item.get(k),k) for k in ("lcg","tcg","vcg")]
        lengths=[val.number(item.get(k,0),k,minimum=0) for k in ("aft","fwd","fsm")]
        mass_rows.append([item_name,1,mass,*xyz,*lengths[:2],lengths[2]*1000])
    design=com.connect("stability").Design
    tank_names={str(design.Tanks.Item(i).Name) for i in range(1,int(design.Tanks.Count)+1)}
    if set(fills)!=tank_names:
        raise MaxsurfValidationError("explicit fills must cover exactly all tanks",
                                     missing=sorted(tank_names-set(fills)),unknown=sorted(set(fills)-tank_names))
    exists=case_name in _loadcase_names(design)
    if exists and not clear_first:
        raise MaxsurfSafetyError("loadcase already exists; choose a new name")
    backup=_before_replace(design,exists,confirm_replace,backup_path, scope="loadcases")
    try:
        if exists:
            design.LoadCases.RemoveByName(case_name)
        design.LoadCases.Add(case_name,_constant(com.connect("stability"),LOADCASE_TYPE_NAME))
        val.stored(design,"ActiveLoadCaseByName",case_name)
        lc=design.LoadCases(int(design.ActiveLoadCase))
        for tank,pct in fills.items():
            val.stored(lc.TankItemByName(tank),"PercentFull",pct)
        for row in mass_rows:
            lc.AddMassItem(*row)
        if int(lc.MassItemCount)!=len(mass_rows):
            raise MaxsurfCOMError("mass item count mismatch")
        total=val.number(lc.Mass,"readback_mass",positive=True)
    except Exception as exc:
        raise MaxsurfCOMError("loadcase update failed",cause=exc,backup=backup,
                              partial_state_possible=True) from exc
    return com.to_json({"loadcase":case_name,"mass_kg":total,"tank_fills":fills,
                        "mass_item_count":len(mass_rows),"backup":backup})


@guarded(classification=Classification.WRITE_STATE, module="stability", channel=Channel.MCP_COM)
def define_damage_cases(cases_json: str) -> str:
    """Add new named damage cases from explicit room names; never merge silently."""
    cases=val.parsed(cases_json,list,"cases_json")
    if not cases:
        raise MaxsurfValidationError("cases_json must not be empty")
    planned,seen=[],set()
    for case in cases:
        if not isinstance(case,dict):
            raise MaxsurfValidationError("each damage case must be an object")
        name=val.name(case.get("name"))
        rooms=case.get("rooms")
        if name in seen or not isinstance(rooms,list) or not rooms:
            raise MaxsurfValidationError("unique names and nonempty rooms are required")
        seen.add(name)
        planned.append((name,[val.name(r,"room") for r in rooms]))
    design=com.connect("stability").Design
    existing={str(design.DamageCases.Item(i).Name) for i in range(1,int(design.DamageCases.Count)+1)}
    known=_room_names(design)
    if seen & existing:
        raise MaxsurfSafetyError("damage case already exists",names=sorted(seen & existing))
    for name,rooms in planned:
        if set(rooms)-known:
            raise MaxsurfValidationError("unknown damaged room",rooms=sorted(set(rooms)-known))
    created=[]
    try:
        for name,rooms in planned:
            design.DamageCases.Add(name)
            item=design.DamageCases.ItemByName(name)
            for room in rooms:
                item.SetIsDamagedByName(room,True)
                if not bool(item.IsDamagedByName(room)):
                    raise MaxsurfCOMError("damage flag readback mismatch",room=room)
            created.append(name)
    except Exception as exc:
        raise MaxsurfCOMError("damage case update failed",cause=exc,created=created,
                              partial_state_possible=True) from exc
    return com.to_json({"created":created,"verified":True})


@guarded(classification=Classification.WRITE_STATE, module="stability", channel=Channel.MCP_COM)
def add_downflooding_points(points_json: str, clear_first: bool = False,
                            confirm_replace: bool = False, backup_path: str = "") -> str:
    """Add explicit native key points. Destructive replacement is currently disabled."""
    points=val.parsed(points_json,list,"points_json")
    planned,seen=[],set()
    for pt in points:
        if not isinstance(pt,dict):
            raise MaxsurfValidationError("key points must be objects")
        name=val.name(pt.get("name"))
        kind=pt.get("kind")
        if name in seen or kind not in KEYPOINT_KINDS:
            raise MaxsurfValidationError("unique point names and explicit known kind required")
        seen.add(name)
        planned.append((name,*[val.number(pt.get(k),k) for k in ("x","y","z")],kind))
    if not planned:
        raise MaxsurfValidationError("points_json must not be empty")
    app=com.connect("stability")
    design=app.Design
    values={p[-1]:_constant(app,KEYPOINT_KINDS[p[-1]]) for p in planned}
    backup=_before_replace(design,clear_first,confirm_replace,backup_path, scope="keypoints")
    before=int(design.KeyPoints.Count)
    try:
        if clear_first: design.KeyPoints.DeleteAll()
        for name,x,y,z,kind in planned:
            design.KeyPoints.Add(name,x,y,z,values[kind])
        if int(design.KeyPoints.Count)!=(0 if clear_first else before)+len(planned):
            raise MaxsurfCOMError("keypoint count mismatch")
    except Exception as exc:
        raise MaxsurfCOMError("keypoint update failed",cause=exc,backup=backup,
                              partial_state_possible=True) from exc
    return com.to_json({"created":[p[0] for p in planned],"backup":backup,"verified":True})


@guarded(classification=Classification.EXECUTE_ANALYSIS, module="stability", channel=Channel.MCP_COM)
def run_stability_gz(loadcase_name: str, start: float, first_end: float,
                     first_step: float, second_end: float, second_step: float,
                     third_end: float, third_step: float, damage_case: str = "",
                     to_starboard: bool = True, max_points: int = 400) -> str:
    """Run native large-angle stability for the explicitly selected condition.

    Returns native values only, without handwritten class-rule metrics.
    An empty damage_case explicitly selects the built-in Intact case.
    """
    schedule=_validated_schedule(dict(start=start,first_end=first_end,first_step=first_step,
        second_end=second_end,second_step=second_step,third_end=third_end,third_step=third_step))
    max_points=int(val.number(max_points,"max_points",positive=True,maximum=2000))
    app=com.connect("stability")
    design=app.Design
    _select_case(design,loadcase_name,damage_case)
    val.stored(design,"ActiveAnalysisMode",_constant(app,STABILITY_MODE_NAME))
    _set_stability_heel(design,schedule,to_starboard)
    try:
        design.RunAnalysis()
        res=design.LargeAngleStabilityResults
        count=int(res.Count)
        if not 0<count<=max_points:
            raise MaxsurfAnalysisError("empty or oversized GZ result",count=count,max_points=max_points)
        rows=[{f:_scalar(res(i),f) for f in GZ_FIELDS} for i in range(1,count+1)]
        _validate_gz_rows(rows)
    except MaxsurfAnalysisError:
        raise
    except Exception as exc:
        raise MaxsurfAnalysisError("GZ solver failed",detail=str(exc)) from exc
    return com.to_json({"module":"stability","loadcase":loadcase_name,
        "damage_case":damage_case or "Intact","solver_status":"ok",
        "point_count":count,"gz_table":rows,"heel_schedule":schedule,
        "validation_status":"native_rows_validated",
        "field_units":{"Heel":"deg", "GZ":"m", "Displacement":"kg",
                       "DraftAP":"m", "DraftFP":"m", "Trim":"m", "GMT":"m", "KG":"m",
                       "Converged":"boolean", "GZAreaFromZero":"native_unit_unverified",
                       "Downflooded":"native_semantics_unverified"},
        "warnings":["Downflooded is a native numeric field, not a Boolean or a flooding angle.",
                    "Native row validation is not a class-rule or seaworthiness approval."],
        "engineering_verdict":"not_evaluated"})


def _validate_gz_rows(rows):
    """Reject solver placeholders; preserve all native rows in the error."""
    invalid = []
    for index, row in enumerate(rows, 1):
        reasons = []
        required = ("Converged", "Heel", "GZ", "Displacement", "DraftAP", "DraftFP")
        for field in required:
            if row[field]["status"] != "read":
                reasons.append(field + " unreadable")
        if not reasons:
            if row["Converged"]["value"] != 1:
                reasons.append("solver did not converge")
            if row["Displacement"]["value"] <= 0:
                reasons.append("nonpositive displacement")
        if reasons:
            invalid.append({"row": index, "reasons": reasons})
    if not rows or invalid:
        raise MaxsurfAnalysisError("invalid native GZ rows; curve is not validated",
                                  invalid_rows=invalid, gz_table=rows,
                                  engineering_verdict="not_evaluated")


def _scalar(obj, member):
    probed=com.probe(obj,member)
    if probed["status"]!="read":
        return {"value":None,"status":probed["status"]}
    try:
        raw = probed["value"]
        value=val.number(int(raw) if isinstance(raw, bool) else raw,member)
    except MaxsurfValidationError:
        return {"value":None,"status":"non_finite_or_nonnumeric"}
    return {"value":value,"status":"read"}


@guarded(classification=Classification.READ, module="stability",
         channel=Channel.MCP_COM)
def list_criteria_libraries() -> str:
    """READ-ONLY. List the Maxsurf criteria standards available to import.

    These are the per-authority .hcr files Maxsurf ships (IMO, MCA, the Red
    Ensign Group Large Yacht Code, class and naval sets). The name is what
    evaluate_stability_criteria accepts as its ``standard`` argument.
    """
    library_dir = _criteria_library_dir()
    standards = _available_standards(library_dir)
    return com.to_json({
        "module": "stability", "library_dir": library_dir,
        "available": standards, "count": len(standards),
        "warnings": ([f"no .hcr files under {library_dir}"]
                     if not standards else []),
    })


@guarded(classification=Classification.EXECUTE_ANALYSIS, module="stability", channel=Channel.MCP_COM)
def evaluate_stability_criteria(standard: str, loadcase_name: str,
                                start: float, first_end: float, first_step: float,
                                second_end: float, second_step: float,
                                third_end: float, third_step: float,
                                damage_case: str = "", intact: bool = True,
                                to_starboard: bool = True,
                                confirm_replace: bool = False) -> str:
    """Import the caller-selected installed .hcr library and run Maxsurf criteria.

    No rule thresholds are embedded. Replaces the current criteria selection
    only with explicit confirmation. Missing/invalid/uncomputed criteria can
    never yield PASS. This is a native-engine result, not vessel approval.
    """
    if confirm_replace is not True:
        raise MaxsurfSafetyError("criteria replacement requires confirm_replace=true")
    if intact == bool(damage_case):
        raise MaxsurfValidationError("intact requires no damage_case; damage requires a case name")
    hcr=_resolve_hcr(standard)
    schedule=_validated_schedule(dict(start=start,first_end=first_end,first_step=first_step,
        second_end=second_end,second_step=second_step,third_end=third_end,third_step=third_step))
    app=com.connect("stability")
    design=app.Design
    _select_case(design,loadcase_name,damage_case)
    val.stored(design,"ActiveAnalysisMode",_constant(app,STABILITY_MODE_NAME))
    _set_stability_heel(design,schedule,to_starboard)
    criteria=design.Criteria
    criteria.DeleteAll()
    criteria.Import(hcr)
    included=0
    for i in range(1,int(criteria.Count)+1):
        item=criteria.Item(i)
        want=bool(item.IncludeForIntact if intact else item.IncludeForDamage)
        val.stored(item,"IncludeForAnalysis",want)
        included+=int(want)
    if included==0:
        raise MaxsurfAnalysisError("no criteria included for the selected condition")
    try:
        design.RunAnalysis()
        criteria.Calculate()
    except Exception as exc:
        raise MaxsurfAnalysisError("native criteria analysis failed",detail=str(exc)) from exc
    tally={k:0 for k in ("pass","fail","not_analysed","invalid","unknown")}
    for i in range(1,int(criteria.Count)+1):
        item=criteria.Item(i)
        if bool(item.IncludeForAnalysis):
            tally[{1:"pass",2:"fail",3:"not_analysed",4:"invalid"}.get(int(item.Status),"unknown")]+=1
    success=_criteria_success(criteria.AnalysisSuccessful(""))
    try:
        table=_read_xmlgrid_table(_constant(app,"hmResultCriteria"))
    except Exception as exc:
        table={"unavailable":str(exc)[:200]}
    complete=(success is True and sum(tally.values())==included and
              not any(tally[k] for k in ("not_analysed","invalid","unknown")))
    verdict=("pass" if complete and tally["pass"]==included else
             "fail" if complete and tally["fail"] else "inconclusive")
    return com.to_json({"module":"stability","standard":standard,
        "loadcase":loadcase_name,"damage_case":damage_case or "Intact",
        "mode":"intact" if intact else "damage","criteria_included":included,
        "analysis_successful":success,"tally":tally,"verdict":verdict,
        "results_table":table,"solver_status":"ok",
        "engineering_scope":"native criteria only; applicability and vessel approval are external"})


def _room_names(design):
    return {str(coll.Item(i).Name) for coll in (design.Tanks,design.Compartments)
            for i in range(1,int(coll.Count)+1)}

def _loadcase_names(design):
    # Maxsurf exposes eight undefined slots in a fresh design. Their Name getter
    # raises "Loadcase is not open"; only IsDefined is valid until a case exists.
    names = set()
    for i in range(1, int(design.LoadCases.Count) + 1):
        case = design.LoadCases(i)
        defined = case.IsDefined
        if type(defined) is not bool:
            raise MaxsurfCOMError("Invalid load-case definition status", index=i)
        if defined:
            names.add(str(case.Name))
    return names

@guarded(classification=Classification.WRITE_FILE, module="stability", channel=Channel.MCP_COM)
def save_stability_rooms(path: str, overwrite: bool = False) -> str:
    """Save native Stability tank and compartment definitions to a .htk file.

    The .htk is companion data, not a replacement for the hull .msd.
    File existence and digest are verified; reopening is a separate acceptance
    test, never performed implicitly because it would replace the live model.
    """
    target = workspace.resolve_target(path, purpose="save_stability_rooms",
                                      overwrite=overwrite, allowed_suffixes=(".htk",))
    app = com.connect("stability")
    design = app.Design
    counts = {name: int(getattr(design, name).Count)
              for name in ("Tanks", "Compartments")}
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        design.SaveAsRooms(str(target), bool(overwrite))
        if not target.is_file() or target.stat().st_size == 0:
            raise MaxsurfCOMError("Stability did not write a nonempty .htk", path=str(target))
    finally:
        com.session_for("stability").refresh_design_identity()
    return com.to_json({"module": "stability", "path": str(target), **_digest(target),
                        "native_member": "Design.SaveAsRooms", "counts_at_save": counts,
                        "file_verified": True, "reopen_verified": False,
                        "warning": "Rooms only: retain hull .msd. Load cases, damage cases, key points and results are NOT backed up."})


def _before_replace(design, requested, confirmed, backup_path, scope="rooms"):
    if not requested:
        return None
    if confirmed is not True or not backup_path:
        raise MaxsurfSafetyError("replacement requires confirm_replace and a new backup_path")
    if scope != "rooms" or int(design.LoadCases.Count) or int(design.DamageCases.Count) > 1:
        raise MaxsurfSafetyError("replacement disabled: room-only backup cannot preserve dependent load/damage/keypoint data; save in Maxsurf and use a new case name")
    target=workspace.resolve_target(backup_path,purpose="stability_checkpoint",
                                    overwrite=False,allowed_suffixes=(".htk",))
    target.parent.mkdir(parents=True,exist_ok=True)
    try:
        design.SaveAsRooms(str(target),False)
    finally:
        com.session_for("stability").refresh_design_identity()
    if not target.is_file() or target.stat().st_size==0:
        raise MaxsurfCOMError("Stability did not write a nonempty checkpoint")
    return {"path":str(target),**_digest(target)}

def _validated_schedule(schedule):
    out={k:val.number(v,k) for k,v in schedule.items()}
    ends=[out[k] for k in ("start","first_end","second_end","third_end")]
    if ends!=sorted(ends) or ends[0]<0 or ends[-1]>180 or ends[-1]<=ends[0]:
        raise MaxsurfValidationError("heel endpoints must be ordered in [0,180]")
    steps=[out[k] for k in ("first_step","second_step","third_step")]
    if min(steps)<=0 or sum((b-a)/s for a,b,s in zip(ends,ends[1:],steps))>2000:
        raise MaxsurfValidationError("heel steps must be positive, with at most 2000 points")
    return out

def _select_case(design, loadcase, damage):
    val.stored(design,"ActiveLoadCaseByName",val.name(loadcase))
    # This is the native built-in case name verified on Maxsurf 2025, not a
    # stability rule or a synthesized damage condition.
    val.stored(design,"ActiveDamageCaseByName",damage or "Intact")

def _criteria_success(value):
    if isinstance(value,bool):
        return value
    if isinstance(value,(tuple,list)) and value and isinstance(value[0],bool):
        return value[0]
    return None



@guarded(classification=Classification.WRITE_FILE, module="stability", channel=Channel.MCP_COM)
def export_stability_image(path: str, view: str = "hmViewPerspective",
                           width: int = 1200, height: int = 800) -> str:
    """Save a native Stability view/graph to a NEW workspace PNG (Maxsurf 2025).

    view must be a discovered hmView... enum name. Does not change rendering,
    camera, load case or analysis. A valid image is not evidence of valid or
    current analysis results. No desktop screenshot or clipboard is used.
    """
    for name, value in (("width",width),("height",height)):
        if type(value) is not int or not 64 <= value <= 4096:
            raise MaxsurfValidationError(name+" must be an integer in 64..4096")
    target = workspace.resolve_target(path, purpose="export_stability_image", overwrite=False,
                                      allowed_suffixes=(".png",))
    app = com.connect("stability")
    from . import constants as comconstants
    views = {k:v for k,v in comconstants.discover(app,name_filter="hmView")["constants"].items()
             if k.startswith("hmView") and type(v) is int}
    if view not in views:
        raise MaxsurfValidationError("unknown native Stability view", available=sorted(views))
    identity = com.probe(app.Design, "Path")
    if identity["status"] != "read" or not identity["value"]:
        raise MaxsurfValidationError("Stability has no verified loaded design; open a model before exporting a view")
    result = app.Design.SaveImage(views[view], str(target), width, height)
    if result is False or not target.is_file() or target.stat().st_size == 0:
        raise MaxsurfCOMError("Stability did not write a nonempty image", path=str(target))
    from PIL import Image
    try:
        with Image.open(target) as rendered:
            actual_width, actual_height = rendered.size
            if rendered.format != "PNG" or not all(64 <= n <= 4096 for n in rendered.size):
                raise ValueError("unexpected image format or dimensions")
            rendered.verify()
        with Image.open(target) as rendered:
            rendered.load()
    except Exception as exc:
        raise MaxsurfCOMError("native image validation failed", path=str(target), detail=str(exc)) from exc
    return com.to_json({"module":"stability", "path":str(target), **_digest(target),
        "view":view, "width":actual_width, "height":actual_height,
        "requested_width":width, "requested_height":height,
        "dimensions_honored":(actual_width,actual_height)==(width,height),
        "dimension_note":"Native view may use its client-area size; no resampling is performed.",
        "native_member":"Design.SaveImage",
        "image_verified":True, "analysis_run":False, "result_freshness":"not_verified"})


TOOLS = (export_stability_image, transfer_modeler_to_stability, save_stability_rooms, run_stability_equilibrium,
         define_subdivision, set_heel_range,
         define_loadcase, define_damage_cases, add_downflooding_points,
         run_stability_gz, list_criteria_libraries,
         evaluate_stability_criteria)


def register_tools(mcp: Any) -> list[str]:
    """Register the Stability transfer and equilibrium tools."""
    return register(mcp, TOOLS)
