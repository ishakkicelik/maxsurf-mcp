"""Bentley Motions (seakeeping) transfer, configuration, run and results.

The registered application is ``BentleyMotions.Application`` (CLSID
{17618094-EBE1-49FC-838E-B61B79BE04CC}, LocalServer32 MaxsurfMotions.exe); the
three names the module carried before were never registered, which is why every
Motions connection failed before it began.

The workflow, native members inspected against the live application; numeric acceptance remains incomplete:

```text
Modeler Design.SaveAs -> Motions Design.DesignOpen
Design.Vessel.VCG / GyradiusRoll / GyradiusPitch / GyradiusYaw / RollDampingNonDim
Design.AnalysisOptions.WaterDensity / Frequencies*
Design.Headings.Add(name, heading_rad)
Design.Speeds.Add(name, speed_m_s)
Design.Spectra.Add(); item.Type / CharacteristicWaveHeight / ModalPeriod / ...
Design.Locations.Add(name, x, y, z)
Design.CalculateGeometry() -> CalculateMesh() -> CalculateSeakeeping()
Design.GlobalStatistics.Item(speed, heading, spectrum)
```

COM speeds are SI (m/s); user-facing speeds are also given and read in knots
because the Motions GUI presents knots. Seakeeping results are a preliminary
strip-theory/panel prediction, not a substitute for model tests.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from typing import Any

from . import audit, com, config, workspace
from . import validation as val
from .audit import Channel, Classification
from .errors import (
    MaxsurfAnalysisError,
    MaxsurfCOMError,
    MaxsurfSafetyError,
    MaxsurfValidationError,
)
from .guard import guarded, register


MPS_PER_KNOT = 0.5144444444444445
TRANSFER_SUBDIR = "transfer"

#: Vessel inputs the analysis depends on, in call order for the snapshot.
VESSEL_FIELDS: tuple[str, ...] = (
    "VesselType", "DraftAft", "DraftFwd", "VCG", "GyradiusRoll",
    "GyradiusPitch", "GyradiusYaw", "RollDampingNonDim",
    "AdditionalHeavePitchDampingNonDim", "DemihullSpacing", "numOfSections",
    "NumOfMappingTerms",
)
VESSEL_UNITS = {
    "VesselType": "-", "DraftAft": "m", "DraftFwd": "m", "VCG": "m",
    "GyradiusRoll": "m", "GyradiusPitch": "m", "GyradiusYaw": "m",
    "RollDampingNonDim": "-", "AdditionalHeavePitchDampingNonDim": "-",
    "DemihullSpacing": "m", "numOfSections": "-", "NumOfMappingTerms": "-",
}

OPTION_FIELDS: tuple[str, ...] = (
    "WaterDensity", "FrequenciesAuto", "FrequenciesStart", "FrequenciesEnd",
    "NumOfFrequencies", "UseTransomTerms", "WaveForceMethod",
    "AddedResistanceMethod",
)
OPTION_UNITS = {
    "WaterDensity": "kg/m^3", "FrequenciesAuto": "-", "FrequenciesStart":
    "rad/s", "FrequenciesEnd": "rad/s", "NumOfFrequencies": "-",
    "UseTransomTerms": "-", "WaveForceMethod": "-", "AddedResistanceMethod": "-",
}

#: Spectrum item properties written from a spectrum spec.
SPECTRUM_PROPS = {
    "type": "Type", "hs_m": "CharacteristicWaveHeight",
    "tp_s": "ModalPeriod", "tz_s": "ZeroCrossingPeriod",
    "gamma": "PeakEnhancementFactor", "wind_speed_m_s": "CharacteristicWindSpeed",
}


def _scalar(obj: Any, member: str, cast: Any = None) -> dict[str, Any]:
    probed = com.probe(obj, member)
    if probed["status"] != "read":
        return {"value": None, "status": probed["status"]}
    value = probed["value"]
    if cast is not None and value is not None:
        try:
            value = cast(value)
        except (TypeError, ValueError) as exc:
            return {"value": None, "status": "unreadable", "detail": str(exc)}
    return {"value": value, "status": "read"}


def _digest(path: Any) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return {"bytes": size, "sha256": digest.hexdigest()}


def _require_ready(app: Any, *, loaded: bool = True) -> Any:
    initialized = _scalar(app, "IsInitializedCorrectly", bool)
    if initialized["status"] != "read" or initialized["value"] is not True:
        raise MaxsurfCOMError("Motions automation is not initialized correctly",
                              initialized=initialized)
    design = app.Design
    if loaded:
        path = _scalar(design, "DesignPath", str)
        if path["status"] != "read" or not path["value"]:
            raise MaxsurfValidationError(
                "Motions has no loaded design; transfer a Modeler design first",
                design_path=path)
    return design


def _snapshot(obj: Any, fields: tuple[str, ...],
              units: dict[str, str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for field in fields:
        entry = _scalar(obj, field, float)
        entry["unit"] = units.get(field, "-")
        out[field] = entry
    return out


@guarded(classification=Classification.READ, module="motions",
         channel=Channel.MCP_COM)
def get_motions_status() -> str:
    """READ-ONLY. Report Motions readiness, design, vessel and analysis inputs."""
    app = com.connect("motions")
    design = app.Design
    initialized = _scalar(app, "IsInitializedCorrectly", bool)
    ready = initialized["status"] == "read" and initialized["value"] is True
    design_path = _scalar(design, "DesignPath", str)
    loaded = design_path["status"] == "read" and bool(design_path["value"])

    def count(name: str) -> int | None:
        try:
            return int(getattr(design, name).Count)
        except Exception:  # noqa: BLE001
            return None

    try:
        availability = {"value": bool(design.AnalysisTypeIsAvailable(int(design.AnalysisType))), "status": "read"}
    except Exception as exc:
        availability = {"value": None, "status": "unreadable", "detail": str(exc)}
    selected_inputs = {}
    for collection, fields in (("Speeds", ("Name", "Speed", "Analyse")),
                               ("Headings", ("Name", "Heading", "Analyse")),
                               ("Spectra", ("Name", "Type", "CharacteristicWaveHeight", "ModalPeriod", "Analyse"))):
        try:
            items = getattr(design, collection)
            total = int(items.Count)
            selected_inputs[collection] = {"count": total, "truncated": total > 200,
                "rows": [{field: _scalar(items.Item(i), field) for field in fields}
                         for i in range(1, min(total, 200)+1)]}
        except Exception as exc:
            selected_inputs[collection] = {"status": "unreadable", "detail": str(exc)}
    warnings = []
    if not ready:
        warnings.append("Motions automation is not initialized correctly")
    if not loaded:
        warnings.append("Motions reports no loaded design")
    return com.to_json({
        "module": "motions", "automation_ready": ready,
        "automation_initialized": initialized,
        "license_level": _scalar(app, "LicenseLevel", str),
        "version": _scalar(app, "Version", str),
        "design": {"loaded": loaded, "path": design_path,
                   "name": _scalar(design, "DesignName", str)},
        "companion_files": {"inputs":_scalar(design,"SKDataPath",str),
                            "results":_scalar(design,"SKResultsPath",str)},
        "analysis_type": _scalar(design, "AnalysisType", int),
        "inputs": {"headings": count("Headings"), "speeds": count("Speeds"),
                   "spectra": count("Spectra"), "locations": count("Locations")},
        "section_mappings": {"count": count("SectionMappings"),
            "note": "Zero mappings are not evidence of a prepared strip-theory hull."},
        "vessel": _snapshot(design.Vessel, VESSEL_FIELDS, VESSEL_UNITS),
        "analysis_options": _snapshot(design.AnalysisOptions, OPTION_FIELDS,
                                      OPTION_UNITS),
        "analysis_available": availability,
        "input_rows": selected_inputs,
        "input_row_units": {"Headings.Heading":"rad", "Speeds.Speed":"m/s",
                            "Spectra.CharacteristicWaveHeight":"m", "Spectra.ModalPeriod":"s"},
        "warnings": warnings,
    })


@guarded(classification=Classification.WRITE_FILE, module="motions",
         channel=Channel.MCP_COM)
def transfer_modeler_to_motions(overwrite: bool = False,
                                  confirm_replace: bool = False) -> str:
    """Save a unique Modeler copy and verify the destination design path.

    Save destination edits first. confirm_replace=true explicitly authorizes
    replacing them. The source file on disk is not overwritten.
    """
    from .transfer import transfer
    return transfer("motions", overwrite=overwrite, confirm_replace=confirm_replace)


def _json_list(raw: str, label: str) -> list[Any]:
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise MaxsurfValidationError(f"{label} must be valid JSON",
                                     head=raw[:200]) from exc
    if not isinstance(value, list):
        raise MaxsurfValidationError(f"{label} must be a JSON list")
    return value


@guarded(classification=Classification.WRITE_STATE, module="motions", channel=Channel.MCP_COM)
def configure_motions_analysis(
    headings_deg_json: str, speeds_knots_json: str, spectra_json: str,
    water_density_kg_m3: float, draft_m: float, vcg_m: float,
    gyradius_roll_m: float, gyradius_pitch_m: float, gyradius_yaw_m: float,
    roll_damping_nondim: float, analysis_type: str, vessel_type: str,
    locations_json: str = "[]", replace: bool = False,
    spectrum_readback_rel_tol: float = 1e-8,
) -> str:
    """Set explicit native seakeeping inputs, validating the entire request first.

    Headings are degrees at this boundary, radians in native COM (180 = head seas).
    Spectrum type uses a skSpectrum... enum name. No sea state, vessel type,
    damping, gyradii or analysis method is chosen by this server.
    replace=true explicitly authorizes replacing input collections.
    Spectrum period setters can round through native period conversion. The
    default readback tolerance is strict; an explicitly supplied relative
    tolerance (0..0.01) applies only to spectrum numeric fields. Requested and
    stored values are returned; other geometry/mass checks are not relaxed.
    """
    spectrum_tolerance=val.number(spectrum_readback_rel_tol,"spectrum_readback_rel_tol",minimum=0,maximum=0.01)
    headings=[val.number(x,"heading",minimum=0,maximum=360) for x in val.parsed(headings_deg_json,list,"headings")]
    speeds=[val.number(x,"speed",minimum=0) for x in val.parsed(speeds_knots_json,list,"speeds")]
    spectra=val.parsed(spectra_json,list,"spectra")
    locations=val.parsed(locations_json,list,"locations")
    if not headings or not speeds or not spectra or len(headings)*len(speeds)*len(spectra)>200:
        raise MaxsurfValidationError("nonempty inputs with at most 200 combinations required")
    written={"DraftAft":val.number(draft_m,"draft",positive=True),
             "DraftFwd":val.number(draft_m,"draft",positive=True),
             "VCG":val.number(vcg_m,"vcg"),
             "GyradiusRoll":val.number(gyradius_roll_m,"gyradius_roll",positive=True),
             "GyradiusPitch":val.number(gyradius_pitch_m,"gyradius_pitch",positive=True),
             "GyradiusYaw":val.number(gyradius_yaw_m,"gyradius_yaw",positive=True),
             "RollDampingNonDim":val.number(roll_damping_nondim,"roll_damping",minimum=0)}
    density=val.number(water_density_kg_m3,"water_density",positive=True)
    spec_rows=[]
    for spec in spectra:
        if not isinstance(spec,dict) or set(spec)-set(SPECTRUM_PROPS)-{"name"}:
            raise MaxsurfValidationError("unknown spectrum field")
        kind=val.name(spec.get("type"),"spectrum type")
        if not kind.startswith("skSpectrum"):
            raise MaxsurfValidationError("spectrum type must be a skSpectrum enum name")
        if "gamma" in spec:
            raise MaxsurfValidationError("PeakEnhancementFactor is read-only in this API; do not supply gamma")
        values={k:val.number(v,k,positive=True) for k,v in spec.items() if k not in ("type","name")}
        if "hs_m" not in values or len({"tp_s","tz_s","wind_speed_m_s"} & set(values)) != 1:
            raise MaxsurfValidationError("spectrum requires hs_m and exactly one period or wind speed")
        spec_rows.append((val.name(spec.get("name",f"S{len(spec_rows)+1}")),kind,values))
    loc_rows=[]
    for loc in locations:
        if not isinstance(loc,dict): raise MaxsurfValidationError("location must be an object")
        loc_rows.append((val.name(loc.get("name")),*[val.number(loc.get(k),k) for k in ("x","y","z")]))
    app=com.connect("motions")
    design=_require_ready(app)
    from .stability import _constant
    atype=_constant(app,val.name(analysis_type))
    vtype=_constant(app,val.name(vessel_type))
    if not analysis_type.startswith("skAT") or not vessel_type.startswith("skVT"):
        raise MaxsurfValidationError("wrong analysis/vessel enum family")
    types={kind:_constant(app,kind) for _,kind,_ in spec_rows}
    spectrum_readbacks=[]
    try:
        val.stored(design,"AnalysisType",atype)
        val.stored(design.Vessel,"VesselType",vtype)
        for field,value in written.items():
            val.stored(design.Vessel,field,value)
        val.stored(design.AnalysisOptions,"WaterDensity",density)
        if replace:
            for coll in ("Headings","Speeds","Spectra","Locations"):
                collection=getattr(design,coll)
                collection.RemoveAll()
                if int(collection.Count): raise MaxsurfCOMError("collection refused clear",collection=coll)
        for i,h in enumerate(headings):
            heading_rad = math.radians(h)
            design.Headings.Add(f"H{int(design.Headings.Count)+1}",heading_rad)
            item=design.Headings.Item(int(design.Headings.Count))
            val.stored(item,"Analyse",True)
            if not math.isclose(float(item.Heading),heading_rad,abs_tol=1e-10): raise MaxsurfCOMError("heading mismatch")
        for i,s in enumerate(speeds):
            design.Speeds.Add(f"V{int(design.Speeds.Count)+1}",s*MPS_PER_KNOT)
            item=design.Speeds.Item(int(design.Speeds.Count))
            val.stored(item,"Analyse",True)
            if not math.isclose(float(item.Speed),s*MPS_PER_KNOT,abs_tol=1e-8): raise MaxsurfCOMError("speed mismatch")
        for name,kind,values in spec_rows:
            design.Spectra.Add()
            item=design.Spectra.Item(int(design.Spectra.Count))
            val.stored(item,"Name",name)
            val.stored(item,"Type",types[kind])
            for key,value in values.items():
                setattr(item,SPECTRUM_PROPS[key],value)
            readback={}
            for key,value in values.items():
                actual=float(getattr(item,SPECTRUM_PROPS[key]))
                if not math.isfinite(actual) or not math.isclose(actual,value,rel_tol=spectrum_tolerance,abs_tol=1e-8):
                    raise MaxsurfCOMError("spectrum readback mismatch",field=key,requested=value,actual=actual,relative_tolerance=spectrum_tolerance,partial_state_possible=True)
                readback[key]={"requested":value,"stored":actual}
            spectrum_readbacks.append({"name":name,"fields":readback,"relative_tolerance":spectrum_tolerance})
            val.stored(item,"Analyse",True)
        for row in loc_rows:
            design.Locations.Add(*row)
    except Exception as exc:
        raise MaxsurfCOMError("Motions input update failed",cause=exc,
                              partial_state_possible=True) from exc
    return com.to_json({"module":"motions","vessel_written":written,
        "headings_deg":headings,"speeds_knots":speeds,"spectra_count":int(design.Spectra.Count),
        "analysis_type":analysis_type,"vessel_type":vessel_type,"verified":True,
        "spectrum_readbacks":spectrum_readbacks})


@guarded(classification=Classification.EXECUTE_ANALYSIS, module="motions", channel=Channel.MCP_COM)
def run_motions_analysis() -> str:
    """Run strip theory or the legacy RADIF panel workflow; require numeric Summary.

    Maxsurf 2025 no longer ships RADIF. Its MOSES replacement is not implemented
    by this runner; do not install an obsolete solver to bypass that limitation.
    Strip theory measures geometry without requesting a panel mesh. External
    MOSES/MARTEC and imported-RAO workflows are not supported by this runner.
    """
    app=com.connect("motions")
    design=_require_ready(app)
    try:
        available = bool(design.AnalysisTypeIsAvailable(int(design.AnalysisType)))
    except Exception as exc:
        raise MaxsurfAnalysisError("native analysis availability could not be verified", detail=str(exc)) from exc
    if not available:
        raise MaxsurfAnalysisError("selected analysis type is unavailable in this native installation")
    # Installed type-library values, also documented in skAnalysisType.
    # MOSES/MARTEC and user-defined RAO workflows need separate native setup;
    # do not pretend the built-in geometry/mesh sequence supports them.
    analysis_type = int(design.AnalysisType)
    if analysis_type not in (1, 2):
        raise MaxsurfValidationError("run workflow supports strip theory or legacy RADIF only; Maxsurf 2025 MOSES panel, MARTEC and imported-RAO execution are not implemented", analysis_type=analysis_type)
    sequence = ("CalculateGeometry", "CalculateSeakeeping") if analysis_type == 1 else ("CalculateGeometry", "CalculateMesh", "CalculateSeakeeping")
    combinations = 1
    for name in ("Headings", "Speeds", "Spectra"):
        collection = getattr(design, name)
        count = int(collection.Count)
        if not 1 <= count <= 200:
            raise MaxsurfValidationError("analysis input count must be 1..200", collection=name)
        active = sum(bool(collection.Item(i).Analyse) for i in range(1, count+1))
        if not active:
            raise MaxsurfValidationError("no input rows selected for analysis", collection=name)
        combinations *= count
    if combinations > 200:
        raise MaxsurfValidationError("analysis exceeds 200 input combinations")
    stages={}
    try:
        for stage in sequence:
            getattr(design,stage)()
            stages[stage]="ok"
        results=json.loads(get_motions_results.__wrapped__())
        native_summary=results["native_summary"]
    except Exception as exc:
        raise MaxsurfAnalysisError("Motions analysis failed",stages=stages,detail=str(exc)) from exc
    return com.to_json({"module":"motions","solver_status":"ok","run_id":audit.new_id(),
        "stages":stages,"results":results,"native_summary":native_summary,
        "engineering_verdict":"not_evaluated"})


def _read_result_item(item):
    out={}
    for field in sorted(getattr(item,"_prop_map_get_",{})):
        if field.startswith("_"):
            continue
        value=getattr(item,field)
        if isinstance(value,(int,float)) and not isinstance(value,bool):
            if not math.isfinite(float(value)):
                raise MaxsurfAnalysisError("non-finite Motions result",field=field)
            out[field]=float(value)
    if not out:
        raise MaxsurfAnalysisError("Motions statistics contain no numeric outputs")
    return out


@guarded(classification=Classification.WRITE_FILE, module="motions",
         channel=Channel.MCP_COM)
def get_motions_results(max_combinations: int = 200) -> str:
    """Export/validate a native Summary before reading global seakeeping statistics.

    Writes a uniquely named diagnostic Summary inside the runtime workspace.
    Header-only exports are rejected even when COM returns numeric placeholders.
    A nonempty table is not an independent freshness or physical-unit guarantee.

    Uses NumberTested/TestedItem for condition axes, never all configured rows.
    GlobalStatistics.Item(speed, heading, spectrum) returns the significant
    motion statistics for that condition. Values are reported as the result
    object exposes them (significant single amplitudes and RMS of heave, roll,
    pitch and the coupled responses).
    """
    if type(max_combinations) is not int or not 1 <= max_combinations <= 200:
        raise MaxsurfValidationError("max_combinations must be an integer in 1..200")
    app = com.connect("motions")
    design = _require_ready(app)
    n_speed = int(design.Speeds.NumberTested)
    n_head = int(design.Headings.NumberTested)
    n_spec = int(design.Spectra.NumberTested)
    if min(n_speed, n_head, n_spec) <= 0:
        raise MaxsurfAnalysisError(
            "no seakeeping inputs are defined; configure and run first",
            speeds=n_speed, headings=n_head, spectra=n_spec)
    if n_speed * n_head * n_spec > max_combinations:
        raise MaxsurfValidationError(
            "combination count exceeds max_combinations; raise it explicitly",
            combinations=n_speed * n_head * n_spec,
            max_combinations=max_combinations)

    native_summary = json.loads(export_motions_table.__wrapped__(
        str(config.runtime_root() / "exports" / ("motions-summary-" + audit.new_id() + ".txt")),
        "skTabSummary"))
    stats = design.GlobalStatistics
    rows, errors = [], []
    for s in range(1, n_speed + 1):
        speed_ms = float(design.Speeds.TestedItem(s).Speed)
        for h in range(1, n_head + 1):
            heading = float(design.Headings.TestedItem(h).Heading)
            for sp in range(1, n_spec + 1):
                try:
                    item = stats.Item(s, h, sp)
                    rows.append({
                        "speed_knots": speed_ms / MPS_PER_KNOT,
                        "speed_m_s": speed_ms, "heading_deg": math.degrees(heading),
                        "heading_rad": heading,
                        "spectrum_index": sp,
                        "spectrum_name": str(design.Spectra.TestedItem(sp).Name),
                        "index_basis":"1-based tested conditions, not all configured rows",
                        "statistics": _read_result_item(item),
                    })
                except Exception as exc:  # noqa: BLE001
                    errors.append({"speed": s, "heading": h, "spectrum": sp,
                                   "error": str(exc)[:120]})
    if not rows or errors:
        raise MaxsurfAnalysisError(
            "no seakeeping statistics were readable; run the analysis first",
            errors=errors[:5])
    return com.to_json({
        "module": "motions",
        "combinations": len(rows),
        "value_fields": sorted(rows[0]["statistics"]),
        "results": rows, "errors": errors, "native_summary": native_summary,
        "engineering_scope": "raw native statistics; numerical mapping not independently accepted",
        "validation_status": "unverified_native_mapping",
        "warnings": ([f"{len(errors)} combinations unreadable"]
                     if errors else ["Numeric readability does not validate axis/condition mapping or physical units. Compare with the native results table before engineering use."]),
    })




def _require_numeric_table(data, *, summary=False):
    """Header-only native exports are not evidence of computed responses."""
    encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    text = data.decode(encoding, errors="replace")
    if summary:
        # A Summary can contain numbered input/metadata rows without any solved
        # response. Require the native response rows, not merely any number.
        found=set()
        for line in text.splitlines():
            cells=line.split("\t")
            if len(cells)<7 or cells[1].strip().casefold() not in {"heave motion","roll motion","pitch motion"}:
                continue
            try: valid=all(math.isfinite(float(cells[i])) and float(cells[i])>=0 for i in (2,4,6))
            except ValueError: valid=False
            if valid: found.add(cells[1].strip().casefold())
        if found!={"heave motion","roll motion","pitch motion"}:
            raise MaxsurfAnalysisError("native Summary lacks finite heave/roll/pitch response rows; metadata is not a calculated result")
        return
    for line in text.splitlines():
        for cell in line.split("\t"):
            try:
                value = float(cell.strip())
            except ValueError:
                continue
            if math.isfinite(value):
                return
    raise MaxsurfAnalysisError("native Motions table has no numeric data rows; result mapping is not validated")


@guarded(classification=Classification.WRITE_FILE, module="motions", channel=Channel.MCP_COM)
def export_motions_table(path: str, table: str, overwrite: bool = False) -> str:
    """Export a caller-selected native Motions result table to a text file.

    table must be a discovered skTab... enum name. Uses Design.ExportResults;
    no reformatted COM statistics or invented physical units. Exporting a file
    does not prove the table is current or numerically correct.
    """
    target = workspace.resolve_target(path, purpose="export_motions_table",
                                      overwrite=overwrite, allowed_suffixes=(".txt",))
    app = com.connect("motions")
    design = _require_ready(app)
    from . import constants as comconstants
    catalog = {k: v for k, v in comconstants.discover(app, name_filter="skTab")["constants"].items()
               if k.startswith("skTab") and isinstance(v, int) and not isinstance(v, bool)}
    if table not in catalog:
        raise MaxsurfValidationError("unknown native Motions results table", available=sorted(catalog))
    target.parent.mkdir(parents=True, exist_ok=True)
    design.ExportResults(catalog[table], str(target))
    if not target.is_file() or target.stat().st_size == 0:
        raise MaxsurfCOMError("native Motions export did not write a nonempty file", path=str(target))
    data = target.read_bytes()
    _require_numeric_table(data, summary=table=="skTabSummary")
    return com.to_json({"module": "motions", "path": str(target), "table": table,
                       "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                       "source": "Design.ExportResults", "file_verified": True,
                       "engineering_verdict": "not_evaluated"})



def _same_native_path(design, member, target):
    observed = _scalar(design, member, str)
    if (observed["status"] != "read" or not observed["value"] or
            os.path.normcase(os.path.abspath(observed["value"])) !=
            os.path.normcase(str(target))):
        raise MaxsurfCOMError("native file identity was not confirmed; state may have changed",
                              member=member, observed=observed, expected=str(target))


def _require_empty_motions(design):
    """Fail closed: an unreadable or nonempty session is not disposable."""
    for member in ("DesignPath", "SKDataPath", "SKResultsPath"):
        value = _scalar(design, member, str)
        if value["status"] != "read" or value["value"] != "":
            raise MaxsurfSafetyError("Motions contains current or unreadable state; save it in Maxsurf and explicitly authorize replacement", member=member)
    for member in ("Speeds", "Headings", "Spectra", "Locations", "SectionMappings"):
        try:
            count = getattr(design, member).Count
            empty = type(count) is int and count == 0
        except Exception:
            empty = False
        if not empty:
            raise MaxsurfSafetyError("Motions contains current or unreadable inputs; explicit replacement consent is required", collection=member)


@guarded(classification=Classification.WRITE_STATE, module="motions", channel=Channel.MCP_COM)
def open_motions_design(path: str, confirm_replace: bool = False) -> str:
    """Open an existing workspace .msd in Motions without changing Modeler.

    Without explicit confirm_replace, only a verifiably empty Motions session
    may be opened. Replacement can discard unsaved native state; save it first.
    This tool does not close a model, save other applications, or compute results.
    """
    if type(confirm_replace) is not bool:
        raise MaxsurfValidationError("confirm_replace must be a boolean")
    target = workspace.resolve_existing(path, purpose="open_motions_design", allowed_suffixes=(".msd",))
    app = com.connect("motions")
    design = _require_ready(app, loaded=False)
    if not confirm_replace:
        _require_empty_motions(design)
    try:
        result = design.DesignOpen(str(target))
        if result is False:
            raise MaxsurfCOMError("Motions refused the design file")
        _same_native_path(design, "DesignPath", target)
    finally:
        com.session_for("motions").refresh_design_identity()
    return com.to_json({"module":"motions", "path":str(target), "native_member":"Design.DesignOpen",
                        "identity_verified":True, "analysis_run":False, "modeler_modified":False})


@guarded(classification=Classification.WRITE_STATE, module="motions", channel=Channel.MCP_COM)
def load_motions_data(path: str, confirm_replace: bool = False) -> str:
    """Load native .skd inputs/settings or .skr inputs/results for an open hull.

    Native Open discards the current corresponding data without saving it.
    Therefore explicit confirm_replace=True is always required. Choose a file
    belonging to this hull. File identity does not prove geometry compatibility,
    result freshness, or numerical correctness; imported results are not rerun.
    """
    if confirm_replace is not True:
        raise MaxsurfSafetyError("loading Motions companion data requires confirm_replace=True; save current inputs/results first")
    target = workspace.resolve_existing(path, purpose="load_motions_data", allowed_suffixes=(".skd", ".skr"))
    app = com.connect("motions")
    design = _require_ready(app)
    method, member = (("SKDataOpen", "SKDataPath") if target.suffix.lower()==".skd"
                      else ("SKResultsOpen", "SKResultsPath"))
    try:
        result = getattr(design, method)(str(target))
        if result is False:
            raise MaxsurfCOMError("Motions refused the companion file", native_member=method)
        _same_native_path(design, member, target)
    finally:
        com.session_for("motions").refresh_design_identity()
    return com.to_json({"module":"motions", "path":str(target), "native_member":"Design."+method,
                        "identity_verified":True, "analysis_run":False,
                        "geometry_compatibility":"not_verified", "result_freshness":"not_verified"})


@guarded(classification=Classification.WRITE_FILE, module="motions", channel=Channel.MCP_COM)
def save_motions_state(path: str) -> str:
    """Save native Motions inputs/settings to a NEW workspace .skd file.

    Retain the separate hull .msd. Changes the native companion file identity.
    Never overwrites a file. Binary .skr saving is deliberately unavailable:
    native SKResultsSaveAs timed out during acceptance. Use native table export
    through MCP, or save binary results interactively in Maxsurf instead.
    """
    target = workspace.resolve_target(path, purpose="save_motions_state", overwrite=False,
                                      allowed_suffixes=(".skd",))
    app = com.connect("motions")
    design = _require_ready(app)
    try:
        result = design.SKDataSaveAs(str(target))
        if result is False or not target.is_file() or target.stat().st_size == 0:
            raise MaxsurfCOMError("Motions did not confirm a nonempty settings file", path=str(target))
        _same_native_path(design, "SKDataPath", target)
    finally:
        com.session_for("motions").refresh_design_identity()
    return com.to_json({"module":"motions", "path":str(target), **_digest(target),
        "native_member":"Design.SKDataSaveAs", "file_verified":True, "identity_verified":True,
        "reopen_verified":False, "scope":"inputs/settings",
        "warning":"Retain the hull .msd separately; results are NOT backed up."})


TOOLS = (
    get_motions_status,
    transfer_modeler_to_motions,
    configure_motions_analysis,
    run_motions_analysis,
    get_motions_results,
    export_motions_table,
    open_motions_design,
    load_motions_data,
    save_motions_state,
)


def register_tools(mcp: Any) -> list[str]:
    """Register the bounded Motions adapter; numerical acceptance is separate."""
    return register(mcp, TOOLS)
