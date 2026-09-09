"""Modeler topology, construction grids, and marker reference data.

The Modeler automation interface exposes three pieces of design information
that are essential for disciplined hull construction but were previously only
reachable through raw diagnostics:

* ``ISurface.BondedTo*`` describes the patch adjacency graph;
* ``IGrids`` owns sections, waterlines, buttock lines, and diagonals; and
* ``IMarkers`` carries offset/reference points used during fairing.

Every COM member used here is present in the Maxsurf 2025 type library and in
``ModelerAutomation.chm``.  Operations that the interface does *not* expose are
not invented: notably, ``IMarkers`` has no remove method and ``IGrids`` has no
single-line delete.  Whole-grid replacement therefore requires an explicit
confirmation and performs a best-effort rollback from a complete snapshot.
"""

from __future__ import annotations

import json
import math
from typing import Any

from . import com
from . import constants as comconstants
from .audit import Channel, Classification
from .errors import MaxsurfCOMError, MaxsurfSafetyError, MaxsurfValidationError
from .guard import guarded, register
from .modeler import _control_point, _control_point_limits


GRID_TYPES: dict[str, str] = {
    "sections": "msGTSections",
    "waterlines": "msGTWaterlines",
    "buttocklines": "msGTButtocklines",
    "diagonals": "msGTDiagonals",
}

EDGE_NAMES: dict[int, str] = {
    0: "msEdgeNull",
    1: "msEdgeTop",
    2: "msEdgeLeft",
    3: "msEdgeRight",
    4: "msEdgeBottom",
}

MARKER_PROPERTIES: dict[str, tuple[str, str]] = {
    "name": ("Name", "str"),
    "position": ("Position", "float"),
    "offset": ("Offset", "float"),
    "height": ("Height", "float"),
    "station": ("Station", "int"),
    "surface_id": ("SurfaceID", "int"),
    "type": ("Type", "enum"),
}

# ``msMT`` is reused by the type library for both msMarkerType and msMeshType.
# Keep the marker enum explicit so mesh members such as msMTTriIso cannot be
# accepted as marker types or overwrite the reverse lookup for values 1..3.
MARKER_TYPE_NAMES = (
    "msMTInternal",
    "msMTTopEdge",
    "msMTBottomEdge",
    "msMTLeftEdge",
    "msMTRightEdge",
    "msMTTopRight",
    "msMTTopLeft",
    "msMTBottomRight",
    "msMTBottomLeft",
)


def _finite(field: str, value: Any) -> float:
    if isinstance(value, bool):
        raise MaxsurfValidationError(
            f"{field} must be a finite number", field=field, received=repr(value)
        )
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MaxsurfValidationError(
            f"{field} must be a finite number", field=field, received=repr(value)
        ) from exc
    if not math.isfinite(number):
        raise MaxsurfValidationError(
            f"{field} must be finite", field=field, received=repr(value)
        )
    return number


def _integer(field: str, value: Any, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MaxsurfValidationError(
            f"{field} must be an integer", field=field, received=repr(value)
        )
    if minimum is not None and value < minimum:
        raise MaxsurfValidationError(
            f"{field} must be at least {minimum}", field=field, received=value
        )
    return int(value)


def _enum_members(app: Any, prefix: str) -> dict[str, int]:
    discovered = comconstants.discover(app, name_filter=prefix)
    return {
        name: int(value)
        for name, value in discovered.get("constants", {}).items()
        if name.startswith(prefix)
        and isinstance(value, int)
        and not isinstance(value, bool)
    }


def _resolve_enum(app: Any, prefix: str, requested: str,
                  field: str) -> dict[str, Any]:
    if not isinstance(requested, str) or not requested.strip():
        raise MaxsurfValidationError(
            f"{field} must be a non-empty enum name", field=field
        )
    members = _enum_members(app, prefix)
    key = requested.strip().casefold()
    canonical = next((name for name in members if name.casefold() == key), None)
    if canonical is None:
        raise MaxsurfValidationError(
            f"unknown {field}", field=field, requested=requested,
            available=sorted(members),
        )
    return {"enum_name": canonical, "value": members[canonical]}


def _marker_type_members(app: Any) -> dict[str, int]:
    discovered = _enum_members(app, "msMT")
    return {name: discovered[name] for name in MARKER_TYPE_NAMES
            if name in discovered}


def _resolve_marker_type(app: Any, requested: str) -> int:
    members = _marker_type_members(app)
    canonical = next((name for name in members
                      if name.casefold() == requested.strip().casefold()), None)
    if canonical is None:
        raise MaxsurfValidationError(
            "unknown marker type", field="type", requested=requested,
            available=sorted(members),
        )
    return members[canonical]


def _grid_request(app: Any, requested: str) -> dict[str, Any]:
    if not isinstance(requested, str) or not requested.strip():
        raise MaxsurfValidationError(
            "grid_type must name sections, waterlines, buttocklines, or diagonals",
            available=sorted(GRID_TYPES),
        )
    raw = requested.strip()
    key = raw.casefold().replace("_", "").replace(" ", "")
    aliases = {
        "section": "sections", "sections": "sections", "msgtsections": "sections",
        "waterline": "waterlines", "waterlines": "waterlines",
        "msgtwaterlines": "waterlines",
        "buttock": "buttocklines", "buttocks": "buttocklines",
        "buttockline": "buttocklines", "buttocklines": "buttocklines",
        "msgtbuttocklines": "buttocklines",
        "diagonal": "diagonals", "diagonals": "diagonals",
        "msgtdiagonals": "diagonals",
    }
    canonical = aliases.get(key)
    if canonical is None:
        raise MaxsurfValidationError(
            "unknown grid_type", requested=requested,
            available=sorted(GRID_TYPES),
        )
    resolved = _resolve_enum(app, "msGT", GRID_TYPES[canonical], "grid_type")
    return {"name": canonical, **resolved}


def _grid_line(grids: Any, grid_value: int, index: int) -> dict[str, Any]:
    returned = grids.GetGridLine(grid_value, index)
    if not isinstance(returned, (tuple, list)) or len(returned) != 3:
        raise MaxsurfCOMError(
            "GetGridLine did not return label, position, and angle",
            index=index, returned_type=type(returned).__name__,
        )
    return {
        "index": index,
        "label": str(returned[0]),
        "position": float(returned[1]),
        "position_unit": "m",
        "angle": float(returned[2]),
        "angle_unit": "deg",
    }


def _all_grid_lines(grids: Any, grid_value: int) -> list[dict[str, Any]]:
    count = int(grids.LineCount(grid_value))
    return [_grid_line(grids, grid_value, index)
            for index in range(1, count + 1)]


def _line_values(line: dict[str, Any]) -> tuple[str, float, float]:
    return str(line["label"]), float(line["position"]), float(line["angle"])


def _normalise_lines_json(lines_json: str) -> list[dict[str, Any]]:
    try:
        raw = json.loads(lines_json)
    except (TypeError, ValueError) as exc:
        raise MaxsurfValidationError("lines_json must be valid JSON") from exc
    if not isinstance(raw, list):
        raise MaxsurfValidationError("lines_json must be a JSON array")
    normalised: list[dict[str, Any]] = []
    labels: set[str] = set()
    for offset, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise MaxsurfValidationError(
                "each grid line must be an object", item_index=offset
            )
        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            raise MaxsurfValidationError(
                "each grid line needs a non-empty label", item_index=offset
            )
        folded = label.strip().casefold()
        if folded in labels:
            raise MaxsurfValidationError(
                "grid line labels must be unique within a replacement",
                duplicate_label=label.strip(),
            )
        labels.add(folded)
        normalised.append({
            "label": label.strip(),
            "position": _finite("position", item.get("position")),
            "angle": _finite("angle", item.get("angle", 0.0)),
        })
    return normalised


@guarded(classification=Classification.READ, module="modeler_data",
         channel=Channel.MCP_COM)
def inspect_surface_topology(include_bounds: bool = True) -> str:
    """READ-ONLY. Return assemblies, control nets, and the surface bond graph.

    Bond continuity (C0/C1) is not exposed by ``ISurface`` after a bond has been
    made, so it is reported as unknown rather than inferred. The adjacency
    graph is not a geometric continuity or watertightness certificate.
    """
    app = com.connect("modeler")
    design = app.Design
    surfaces = design.Surfaces
    names: dict[str, list[int]] = {}
    rows_out: list[dict[str, Any]] = []
    directed: list[dict[str, Any]] = []

    for index in range(1, int(surfaces.Count) + 1):
        surface = surfaces(index)
        name = str(surface.Name)
        names.setdefault(name, []).append(index)
        rows, columns = _control_point_limits(surface)
        surface_id = com.probe(surface, "ID")
        assembly_id = com.probe(surface, "Assembly")
        assembly_name = com.probe(surface, "AssemblyName")
        visible = com.probe(surface, "Visible")
        symmetrical = com.probe(surface, "Symmetrical")
        entry: dict[str, Any] = {
            "index": index,
            "id": int(surface_id["value"])
            if surface_id["status"] == "read" else None,
            "id_status": surface_id["status"],
            "name": name,
            "assembly_id": int(assembly_id["value"])
            if assembly_id["status"] == "read" else None,
            "assembly_id_status": assembly_id["status"],
            "assembly": str(assembly_name["value"])
            if assembly_name["status"] == "read" else None,
            "assembly_status": assembly_name["status"],
            "rows": rows,
            "columns": columns,
            "control_point_count": rows * columns,
            "visible": bool(visible["value"])
            if visible["status"] == "read" else None,
            "symmetrical": bool(symmetrical["value"])
            if symmetrical["status"] == "read" else None,
        }
        if include_bounds:
            points = [_control_point(surface, row, column)
                      for row in range(1, rows + 1)
                      for column in range(1, columns + 1)]
            entry["bounds"] = {
                axis: [min(point[field] for point in points),
                       max(point[field] for point in points)]
                for axis, field in (("longitudinal", "longitudinal"),
                                    ("offset", "offset"),
                                    ("vertical", "vertical"))
            }
        rows_out.append(entry)

        for edge_value in range(1, 5):
            target = str(surface.BondedToWhichSurface(edge_value))
            target_edge = int(surface.BondedToWhichEdge(edge_value))
            if not target or target.casefold() == "unbonded" or target_edge == 0:
                continue
            directed.append({
                "surface_index": index,
                "surface_name": name,
                "edge": EDGE_NAMES[edge_value],
                "target_surface_name": target,
                "target_surface_indices": names.get(target, []),
                "target_edge": EDGE_NAMES.get(target_edge, str(target_edge)),
                "description": str(surface.BondedTo(edge_value)),
                "continuity": "unknown_not_exposed_after_bond",
            })

    # The first pass cannot resolve target indices whose surface appears later.
    for bond in directed:
        bond["target_surface_indices"] = names.get(
            bond["target_surface_name"], [])

    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for bond in directed:
        left = f"{bond['surface_name']}[{bond['edge']}]"
        right = f"{bond['target_surface_name']}[{bond['target_edge']}]"
        key = tuple(sorted((left, right)))
        unique.setdefault(key, {
            "first": key[0], "second": key[1],
            "continuity": "unknown_not_exposed_after_bond",
        })

    assemblies: dict[str, dict[str, int]] = {}
    for surface in rows_out:
        key = surface["assembly"] or (
            f"assembly_id:{surface['assembly_id']}"
            if surface["assembly_id"] is not None
            else "(unavailable_or_unnamed)"
        )
        bucket = assemblies.setdefault(key, {"surfaces": 0, "control_points": 0})
        bucket["surfaces"] += 1
        bucket["control_points"] += surface["control_point_count"]

    collections: dict[str, Any] = {}
    for member in ("Trimeshs", "Markers", "Plates", "Decks", "Stringers",
                   "MOPlatePs"):
        probed = com.probe(design, member)
        if probed["status"] != "read":
            collections[member] = None
            continue
        count = com.probe(probed["value"], "Count")
        collections[member] = int(count["value"]) \
            if count["status"] == "read" else None

    return com.to_json({
        "surface_count": len(rows_out),
        "control_point_count": sum(row["control_point_count"] for row in rows_out),
        "assembly_count": len(assemblies),
        "assemblies": assemblies,
        "surfaces": rows_out,
        "directed_bonds": directed,
        "unique_bond_count": len(unique),
        "unique_bonds": list(unique.values()),
        "collections": collections,
        "limitations": [
            "the saved C0/C1 bond continuity grade is not readable through "
            "the Modeler automation interface",
            "independent curve entities are not exposed on IDesign",
        ],
    })


@guarded(classification=Classification.READ, module="modeler_data",
         channel=Channel.MCP_COM)
def list_modeler_grid(grid_type: str = "all", start: int = 1,
                      limit: int = 500) -> str:
    """READ-ONLY. List Modeler construction grid lines with units.

    grid_type accepts sections, waterlines, buttocklines, diagonals, or ``all``.
    Pagination is one-based and never hides the total count.
    """
    app = com.connect("modeler")
    start = _integer("start", start, minimum=1)
    limit = _integer("limit", limit, minimum=1)
    if limit > 5000:
        raise MaxsurfValidationError("limit may not exceed 5000", limit=limit)
    requested = str(grid_type).strip().casefold()
    kinds = list(GRID_TYPES) if requested in ("", "all") else \
        [_grid_request(app, grid_type)["name"]]
    grids = app.Design.Grids
    output: dict[str, Any] = {}
    for kind in kinds:
        resolved = _grid_request(app, kind)
        count = int(grids.LineCount(resolved["value"]))
        stop = min(count, start + limit - 1)
        lines = [] if start > count else [
            _grid_line(grids, resolved["value"], index)
            for index in range(start, stop + 1)
        ]
        output[kind] = {
            "enum_name": resolved["enum_name"],
            "count": count,
            "start": start,
            "returned": len(lines),
            "truncated": stop < count,
            "lines": lines,
        }
    return com.to_json({
        "grids": output,
        "section_split": int(grids.SectionSplit),
    })


@guarded(classification=Classification.WRITE_STATE, module="modeler_data",
         channel=Channel.MCP_COM)
def add_modeler_grid_line(grid_type: str, label: str, position: float,
                          angle: float = 0.0) -> str:
    """WRITE_STATE. Add one verified section/waterline/buttock/diagonal."""
    app = com.connect("modeler")
    resolved = _grid_request(app, grid_type)
    if not isinstance(label, str) or not label.strip():
        raise MaxsurfValidationError("label must be a non-empty string")
    position = _finite("position", position)
    angle = _finite("angle", angle)
    grids = app.Design.Grids
    before = _all_grid_lines(grids, resolved["value"])
    grids.AddGridLine(resolved["value"], label.strip(), position, angle)
    after = _all_grid_lines(grids, resolved["value"])
    if len(after) != len(before) + 1:
        raise MaxsurfCOMError(
            "AddGridLine did not increase the grid count by one",
            before_count=len(before), after_count=len(after),
        )
    matching = [line for line in after
                if line["label"] == label.strip()
                and math.isclose(line["position"], position, abs_tol=1e-9)
                and math.isclose(line["angle"], angle, abs_tol=1e-9)]
    if not matching:
        raise MaxsurfCOMError(
            "the added grid line could not be verified by readback",
            requested={"label": label.strip(), "position": position,
                       "angle": angle},
        )
    return com.to_json({
        "grid_type": resolved,
        "before_count": len(before),
        "after_count": len(after),
        "added": matching,
    })


@guarded(classification=Classification.WRITE_STATE, module="modeler_data",
         channel=Channel.MCP_COM)
def set_modeler_grid_line(grid_type: str, index: int, expected_label: str,
                          label: str, position: float,
                          angle: float = 0.0) -> str:
    """WRITE_STATE. Replace one grid line guarded by its current label."""
    app = com.connect("modeler")
    resolved = _grid_request(app, grid_type)
    grids = app.Design.Grids
    count = int(grids.LineCount(resolved["value"]))
    index = _integer("index", index, minimum=1)
    if index > count:
        raise MaxsurfValidationError(
            "grid index is outside the valid range", index=index,
            valid_range=f"1..{count}",
        )
    before = _grid_line(grids, resolved["value"], index)
    if before["label"] != expected_label:
        raise MaxsurfSafetyError(
            "grid line label no longer matches the caller's guard",
            index=index, expected_label=expected_label,
            actual_label=before["label"],
        )
    if not isinstance(label, str) or not label.strip():
        raise MaxsurfValidationError("label must be a non-empty string")
    position = _finite("position", position)
    angle = _finite("angle", angle)
    grids.SetGridLine(resolved["value"], index, label.strip(), position, angle)
    after_all = _all_grid_lines(grids, resolved["value"])
    matching = [line for line in after_all
                if line["label"] == label.strip()
                and math.isclose(line["position"], position, abs_tol=1e-9)
                and math.isclose(line["angle"], angle, abs_tol=1e-9)]
    if len(after_all) != count or not matching:
        raise MaxsurfCOMError(
            "SetGridLine could not be verified by readback",
            before=before, requested={"label": label.strip(),
                                      "position": position, "angle": angle},
        )
    return com.to_json({
        "grid_type": resolved,
        "index_before_sort": index,
        "before": before,
        "stored": matching,
        "count": count,
        "note": "Maxsurf may reorder grid lines by position; stored contains "
                "the post-write index",
    })


@guarded(classification=Classification.WRITE_STATE, module="modeler_data",
         channel=Channel.MCP_COM)
def replace_modeler_grid(grid_type: str, lines_json: str,
                         expected_count: int,
                         confirm_delete_all: bool = False) -> str:
    """WRITE_STATE. Atomically-as-possible replace one complete grid category.

    ``IGrids`` exposes only DeleteAllLines, not a single-line delete.  The tool
    snapshots every old line, requires the expected count and an explicit
    confirmation, then restores the snapshot if any add or verification fails.
    """
    app = com.connect("modeler")
    resolved = _grid_request(app, grid_type)
    expected_count = _integer("expected_count", expected_count, minimum=0)
    if confirm_delete_all is not True:
        raise MaxsurfSafetyError(
            "whole-grid replacement requires confirm_delete_all=true",
            grid_type=resolved["name"],
        )
    wanted = _normalise_lines_json(lines_json)
    grids = app.Design.Grids
    before = _all_grid_lines(grids, resolved["value"])
    if len(before) != expected_count:
        raise MaxsurfSafetyError(
            "grid count changed since the replacement was planned",
            expected_count=expected_count, actual_count=len(before),
        )

    rollback = {"attempted": False, "restored": None, "detail": None}
    try:
        grids.DeleteAllLines(resolved["value"])
        for line in wanted:
            grids.AddGridLine(resolved["value"], *_line_values(line))
        after = _all_grid_lines(grids, resolved["value"])
        actual = {(x["label"], x["position"], x["angle"]) for x in after}
        expected = {_line_values(x) for x in wanted}
        if len(after) != len(wanted) or actual != expected:
            raise RuntimeError("replacement readback does not match requested lines")
    except Exception as exc:  # noqa: BLE001 - rollback must catch COM errors
        rollback["attempted"] = True
        try:
            grids.DeleteAllLines(resolved["value"])
            for line in before:
                grids.AddGridLine(resolved["value"], *_line_values(line))
            restored = _all_grid_lines(grids, resolved["value"])
            rollback["restored"] = (len(restored) == len(before) and
                sorted(_line_values(x) for x in restored) ==
                sorted(_line_values(x) for x in before))
        except Exception as rollback_exc:  # noqa: BLE001
            rollback["restored"] = False
            rollback["detail"] = str(rollback_exc)[:300]
        raise MaxsurfCOMError(
            "whole-grid replacement failed",
            cause=exc, grid_type=resolved["name"], rollback=rollback,
        ) from exc

    return com.to_json({
        "grid_type": resolved,
        "before_count": len(before),
        "after_count": len(after),
        "lines": after,
        "rollback": rollback,
    })


@guarded(classification=Classification.WRITE_STATE, module="modeler_data",
         channel=Channel.MCP_COM)
def set_section_split(index: int, expected_before: int) -> str:
    """WRITE_STATE. Set the body-plan split section with an optimistic guard."""
    app = com.connect("modeler")
    grids = app.Design.Grids
    index = _integer("index", index, minimum=0)
    expected_before = _integer("expected_before", expected_before, minimum=0)
    before = int(grids.SectionSplit)
    if before != expected_before:
        raise MaxsurfSafetyError(
            "SectionSplit changed since the operation was planned",
            expected_before=expected_before, actual_before=before,
        )
    section_count = int(grids.LineCount(
        _grid_request(app, "sections")["value"]))
    if index > section_count:
        raise MaxsurfValidationError(
            "SectionSplit may not exceed the section count",
            index=index, section_count=section_count,
        )
    grids.SectionSplit = index
    after = int(grids.SectionSplit)
    if after != index:
        raise MaxsurfCOMError(
            "SectionSplit write did not persist", requested=index, stored=after
        )
    return com.to_json({"before": before, "after": after,
                        "section_count": section_count})


def _read_marker(app: Any, marker: Any, index: int,
                 *, read_surface_error: bool) -> dict[str, Any]:
    marker_types = _marker_type_members(app)
    reverse_types = {value: name for name, value in marker_types.items()}
    output: dict[str, Any] = {"collection_index": index}
    fields = {
        "index": ("Index", int), "name": ("Name", str),
        "position": ("Position", float), "offset": ("Offset", float),
        "height": ("Height", float), "station": ("Station", int),
        "surface_id": ("SurfaceID", int), "type": ("Type", int),
    }
    for key, (member, cast) in fields.items():
        probed = com.probe(marker, member)
        if probed["status"] == "read":
            output[key] = cast(probed["value"])
        else:
            output[key] = None
            output[f"{key}_status"] = probed["status"]
    output["type_enum_name"] = reverse_types.get(output.get("type"))
    output["coordinate_unit"] = "m"

    if read_surface_error:
        probed = com.probe(marker, "SurfaceError")
        if probed["status"] == "read":
            output["surface_error"] = float(probed["value"])
            output["surface_error_status"] = "read"
        else:
            attempt = (probed.get("attempts") or [{}])[0]
            output["surface_error"] = None
            output["surface_error_status"] = probed["status"]
            output["surface_error_detail"] = attempt.get("error")
        output["surface_error_unit"] = "m"
    return output


@guarded(classification=Classification.READ, module="modeler_data",
         channel=Channel.MCP_COM)
def list_markers(start: int = 1, limit: int = 500, surface_id: int = -1,
                 include_surface_error: bool = False) -> str:
    """READ-ONLY. List offset/reference markers without hiding failed getters.

    surface_id=-1 returns all markers; 0 selects unassociated markers.  A
    SurfaceError getter can fail for unassociated markers, so it is opt-in and
    its status is reported per marker.
    """
    app = com.connect("modeler")
    start = _integer("start", start, minimum=1)
    limit = _integer("limit", limit, minimum=1)
    surface_id = _integer("surface_id", surface_id, minimum=-1)
    if limit > 5000:
        raise MaxsurfValidationError("limit may not exceed 5000", limit=limit)
    markers = app.Design.Markers
    selected = []
    for index in range(1, int(markers.Count) + 1):
        marker = _read_marker(app, markers(index), index,
                              read_surface_error=include_surface_error)
        if surface_id >= 0 and marker.get("surface_id") != surface_id:
            continue
        selected.append(marker)
    window = selected[start - 1:start - 1 + limit]
    return com.to_json({
        "total_count": int(markers.Count),
        "filtered_count": len(selected),
        "surface_id_filter": surface_id,
        "start": start,
        "returned": len(window),
        "truncated": start - 1 + len(window) < len(selected),
        "markers": window,
    })


@guarded(classification=Classification.WRITE_GEOMETRY, module="modeler_data",
         channel=Channel.MCP_COM)
def add_marker(position: float, offset: float, height: float,
               station_index: int, name: str = "") -> str:
    """WRITE. Add one marker and verify the new collection item."""
    app = com.connect("modeler")
    position = _finite("position", position)
    offset = _finite("offset", offset)
    height = _finite("height", height)
    station_index = _integer("station_index", station_index, minimum=0)
    if not isinstance(name, str):
        raise MaxsurfValidationError("name must be a string")
    markers = app.Design.Markers
    before = int(markers.Count)
    returned = markers.Add(position, offset, height, station_index, name)
    after = int(markers.Count)
    if after != before + 1:
        raise MaxsurfCOMError(
            "Markers.Add did not increase Count by one",
            before_count=before, after_count=after, returned=returned,
        )
    candidates = [
        _read_marker(app, markers(index), index, read_surface_error=False)
        for index in range(1, after + 1)
    ]
    matching = [
        marker for marker in candidates
        if marker.get("name") == name
        and marker.get("station") == station_index
        and math.isclose(marker.get("position", math.inf), position,
                         rel_tol=1e-7, abs_tol=1e-6)
        and math.isclose(marker.get("offset", math.inf), offset,
                         rel_tol=1e-7, abs_tol=1e-6)
        and math.isclose(marker.get("height", math.inf), height,
                         rel_tol=1e-7, abs_tol=1e-6)
    ]
    if not matching:
        raise MaxsurfCOMError(
            "the new marker did not match the requested values",
            requested={"position": position, "offset": offset,
                       "height": height, "station": station_index,
                       "name": name},
            note="Maxsurf may reorder the marker collection after insertion",
        )
    return com.to_json({
        "before_count": before, "after_count": after,
        "solver_returned": returned, "added": matching[-1],
    })


def _marker_value(app: Any, field: str, value: Any) -> Any:
    _member, kind = MARKER_PROPERTIES[field]
    if kind == "str":
        if not isinstance(value, str):
            raise MaxsurfValidationError(f"{field} must be a string", field=field)
        return value
    if kind == "float":
        return _finite(field, value)
    if kind == "int":
        return _integer(field, value, minimum=0)
    return _resolve_marker_type(app, str(value))


@guarded(classification=Classification.WRITE_GEOMETRY, module="modeler_data",
         channel=Channel.MCP_COM)
def set_marker(index: int, expected_name: str, properties_json: str) -> str:
    """WRITE. Edit one marker, guarded by its current name and read back."""
    app = com.connect("modeler")
    markers = app.Design.Markers
    index = _integer("index", index, minimum=1)
    if index > int(markers.Count):
        raise MaxsurfValidationError(
            "marker index is outside the valid range", index=index,
            valid_range=f"1..{int(markers.Count)}",
        )
    marker = markers(index)
    before = _read_marker(app, marker, index, read_surface_error=False)
    if before.get("name") != expected_name:
        raise MaxsurfSafetyError(
            "marker name no longer matches the caller's guard",
            index=index, expected_name=expected_name,
            actual_name=before.get("name"),
        )
    try:
        requested = json.loads(properties_json)
    except (TypeError, ValueError) as exc:
        raise MaxsurfValidationError("properties_json must be valid JSON") from exc
    if not isinstance(requested, dict) or not requested:
        raise MaxsurfValidationError(
            "properties_json must be a non-empty JSON object"
        )
    unknown = sorted(set(requested) - set(MARKER_PROPERTIES))
    if unknown:
        raise MaxsurfValidationError(
            "unknown marker properties", unknown=unknown,
            available=sorted(MARKER_PROPERTIES),
        )
    applied = []
    for field, raw in requested.items():
        member, _kind = MARKER_PROPERTIES[field]
        wanted = _marker_value(app, field, raw)
        old = getattr(marker, member)
        setattr(marker, member, wanted)
        stored = getattr(marker, member)
        matches = math.isclose(float(stored), float(wanted),
                               rel_tol=1e-7, abs_tol=1e-6) \
            if isinstance(wanted, float) else stored == wanted
        applied.append({"field": field, "member": member, "before": old,
                        "requested": wanted, "after": stored,
                        "stored": matches})
    failed = [row["field"] for row in applied if not row["stored"]]
    if failed:
        raise MaxsurfCOMError(
            "one or more marker properties did not persist", failed=failed,
            applied=applied,
        )
    return com.to_json({
        "index": index, "before": before, "applied": applied,
        "after": _read_marker(app, marker, index, read_surface_error=False),
    })


@guarded(classification=Classification.READ, module="modeler_data",
         channel=Channel.MCP_COM)
def marker_fit_report(surface_id: int = 0, max_items: int = 5000) -> str:
    """READ-ONLY. Report RMS/max SurfaceError for associated markers.

    surface_id=0 includes every marker whose SurfaceID is non-zero.  Unassociated
    markers are counted separately because Maxsurf correctly refuses to compute
    their SurfaceError.
    """
    app = com.connect("modeler")
    surface_id = _integer("surface_id", surface_id, minimum=0)
    max_items = _integer("max_items", max_items, minimum=1)
    if max_items > 100000:
        raise MaxsurfValidationError("max_items may not exceed 100000")
    markers = app.Design.Markers
    errors: list[dict[str, Any]] = []
    unassociated = 0
    unreadable = 0
    considered = 0
    for index in range(1, min(int(markers.Count), max_items) + 1):
        marker = markers(index)
        sid_probe = com.probe(marker, "SurfaceID")
        sid = int(sid_probe["value"]) if sid_probe["status"] == "read" else None
        if not sid:
            unassociated += 1
            continue
        if surface_id and sid != surface_id:
            continue
        considered += 1
        error_probe = com.probe(marker, "SurfaceError")
        if error_probe["status"] != "read":
            unreadable += 1
            continue
        errors.append({
            "index": index,
            "surface_id": sid,
            "error": abs(float(error_probe["value"])),
            "unit": "m",
        })
    values = sorted(row["error"] for row in errors)
    rms = math.sqrt(sum(value * value for value in values) / len(values)) \
        if values else None
    percentile_95 = values[min(len(values) - 1,
                               math.ceil(0.95 * len(values)) - 1)] \
        if values else None
    worst = sorted(errors, key=lambda row: row["error"], reverse=True)[:20]
    return com.to_json({
        "marker_count": int(markers.Count),
        "scanned": min(int(markers.Count), max_items),
        "surface_id_filter": surface_id,
        "considered_associated": considered,
        "unassociated": unassociated,
        "unreadable_surface_error": unreadable,
        "measured": len(values),
        "rms_error": rms,
        "max_error": values[-1] if values else None,
        "percentile_95_error": percentile_95,
        "unit": "m",
        "worst": worst,
        "status": "measured" if values else "no_associated_measurements",
    })


TOOLS = (
    inspect_surface_topology,
    list_modeler_grid,
    add_modeler_grid_line,
    set_modeler_grid_line,
    replace_modeler_grid,
    set_section_split,
    list_markers,
    add_marker,
    set_marker,
    marker_fit_report,
)


def register_tools(mcp: Any) -> list[str]:
    """Register Modeler topology, grid, and marker tools."""
    return register(mcp, TOOLS)
