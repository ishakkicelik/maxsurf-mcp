"""Surface transforms, properties, and construction.

This is the module that makes building a hull possible rather than merely
editing one. :mod:`maxsurf_mcp.modeler` owns the design lifecycle and the
control-net editing that was proven in Phase 1; everything here is the layer
above it -- moving and mirroring whole surfaces, reading and setting their
properties, grouping them into assemblies, and bonding them into a continuous
skin.

Every COM member used here was read from the type library, either through live
``ITypeInfo`` (recorded in ``private discovery notes (not distributed)``) or from the decompiled
``ModelerAutomation.chm``. The signatures matter and are not the obvious ones:

```text
Surface.Move(X, Y, Z, bDup: Boolean, nTimes: Long)
Surface.Rotate(dRoll, dPitch, dYaw, dLongCentre, dTransCentre, dVertCentre,
               bDup: Boolean, nTimes: Long)
Surface.ReScale(sfX, sfY, sfZ, centreX, centreY, centreZ)
Surface.Flip(direction: msDirectionType, planeLocation, duplicate: Boolean)
Surface.InvertOutsideArrowDirection()
```

``Move``, ``Rotate`` and ``Flip`` can *duplicate* rather than transform in
place, which turns a transform into a creation operation. That is exposed
deliberately, because mirroring a half-hull is how a symmetric model is built,
but it means the collection count has to be checked either side of every call.

One correction worth stating: ``Surface.Split`` is **not** a transform. It is a
Boolean property, "whether or not the surface is split in the body plan view" --
a drawing setting. It is exposed with the other properties, not here.

Writability is never assumed. The help prints ``[= value]`` on every property
page including read-only ones, so each property put is attempted, verified by
reading the value back, and reported per field. Nothing raises merely because
Maxsurf declined to store something.
"""

from __future__ import annotations

import json
from typing import Any

from . import com
from . import constants as comconstants
from .audit import Channel, Classification
from .errors import MaxsurfCOMError, MaxsurfSafetyError, MaxsurfValidationError
from .guard import guarded, register
from .modeler import _control_point, _control_point_limits, _surface_at

#: Payload field -> (COM member, kind, enum prefix). ``kind`` drives both the
#: cast on read and the coercion on write. Every member and type here came from
#: the type library, not from a guess.
SURFACE_PROPERTIES: dict[str, tuple[str, str, str | None]] = {
    "name": ("Name", "str", None),
    "type": ("Type", "enum", "msST"),
    "use": ("Use", "enum", "msSU"),
    "skin_direction": ("SkinDirection", "enum", "msSSD"),
    "symmetrical": ("Symmetrical", "bool", None),
    "visible": ("Visible", "bool", None),
    "locked": ("Locked", "bool", None),
    "split": ("Split", "bool", None),
    "color": ("Color", "int", None),
    "material": ("Material", "int", None),
    "transparency": ("Transparency", "int", None),
    "thickness": ("Thickness", "float", None),
    "assembly_name": ("AssemblyName", "str", None),
    "assembly": ("Assembly", "int", None),
    "longitudinal_stiffness": ("LongitudinalStiffness", "int", None),
    "transverse_stiffness": ("TransverseStiffness", "int", None),
    "id": ("ID", "int", None),
}

#: Properties that describe the surface rather than configure it. Reported, and
#: refused on write so a caller cannot believe they renumbered a surface.
READ_ONLY_PROPERTIES = ("id",)

#: Enum families resolved from the generated type library at runtime. Values are
#: never hard-coded; only the prefixes appear here.
ENUM_PREFIXES = {
    "direction": "msDT",
    "symmetry": "msSST",
    "edge": "msEdge",
    "continuity": "msBond",
    "library": "msSL",
}


def _enum_map(app: Any, prefix: str) -> dict[str, int]:
    discovered = comconstants.discover(app, name_filter=prefix)
    return {
        name: int(number)
        for name, number in discovered["constants"].items()
        if name.startswith(prefix) and not isinstance(number, bool)
    }


def _enum_name(members: dict[str, int], value: Any) -> str | None:
    for name, number in members.items():
        if number == value:
            return name
    return None


def _resolve_enum(app: Any, prefix: str, requested: str,
                  field: str) -> dict[str, Any]:
    """Turn an enum member name into its runtime number, case-insensitively."""
    if not isinstance(requested, str) or not requested.strip():
        raise MaxsurfValidationError(
            f"{field} must be a non-empty enum member name", field=field
        )
    members = _enum_map(app, prefix)
    canonical = next(
        (name for name in members
         if name.casefold() == requested.strip().casefold()),
        None,
    )
    if canonical is None:
        raise MaxsurfValidationError(
            f"unknown {field} enum member", field=field, requested=requested,
            available=sorted(members),
        )
    return {"enum_name": canonical, "value": members[canonical]}


def _number(field: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MaxsurfValidationError(
            f"{field} must be a number", field=field, received=repr(value)
        )
    import math

    number = float(value)
    if not math.isfinite(number):
        raise MaxsurfValidationError(
            f"{field} must be finite", field=field, received=repr(value)
        )
    return number


def _positive_int(field: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MaxsurfValidationError(
            f"{field} must be an integer of at least 1", field=field,
            received=repr(value),
        )
    return int(value)


def _net_extent(surface: Any) -> dict[str, Any]:
    """Summarise the control net as a bounding box, centroid and point count.

    This is the readback that proves a transform did what it claimed. Reading
    every point costs one COM call per point, and the count is reported so that
    cost is visible rather than hidden.
    """
    rows, columns = _control_point_limits(surface)
    points = [
        _control_point(surface, row, column)
        for row in range(1, rows + 1)
        for column in range(1, columns + 1)
    ]
    axes = ("longitudinal", "offset", "vertical")
    extent: dict[str, Any] = {"rows": rows, "columns": columns,
                              "points": len(points)}
    for axis in axes:
        values = [point[axis] for point in points]
        extent[axis] = {
            "min": min(values),
            "max": max(values),
            "centroid": sum(values) / len(values),
        }
    return extent


def _extent_delta(before: dict[str, Any],
                  after: dict[str, Any]) -> dict[str, Any]:
    delta: dict[str, Any] = {}
    for axis in ("longitudinal", "offset", "vertical"):
        delta[axis] = {
            "min": after[axis]["min"] - before[axis]["min"],
            "max": after[axis]["max"] - before[axis]["max"],
            "centroid": after[axis]["centroid"] - before[axis]["centroid"],
            "span_before": before[axis]["max"] - before[axis]["min"],
            "span_after": after[axis]["max"] - after[axis]["min"],
        }
    return delta


def _transform(app: Any, index: int, operation: str, call: Any,
               arguments: dict[str, Any], duplicate: bool) -> str:
    """Run one surface transform, proving what it did on both sides.

    ``duplicate=True`` makes Move, Rotate and Flip create surfaces rather than
    move them, so the collection count is checked either side and the created
    indices are reported. A transform that silently multiplied the model would
    otherwise be indistinguishable from one that did not.
    """
    surfaces = app.Design.Surfaces
    surface, count_before = _surface_at(surfaces, index)
    name = surface.Name
    before = _net_extent(surface)

    returned = call(surface)

    count_after = int(surfaces.Count)
    created = count_after - count_before
    if duplicate and created < 1:
        raise MaxsurfCOMError(
            f"{operation} was asked to duplicate but the surface count did not "
            f"grow",
            operation=operation, index=index,
            count_before=count_before, count_after=count_after,
        )
    if not duplicate and created != 0:
        raise MaxsurfCOMError(
            f"{operation} was not asked to duplicate but the surface count "
            f"changed",
            operation=operation, index=index,
            count_before=count_before, count_after=count_after,
        )

    after = _net_extent(surfaces(index))
    return com.to_json({
        "operation": operation,
        "index": index,
        "name": name,
        "arguments": arguments,
        # "Return Type: A Boolean value" is printed on every method page in all
        # four automation modules, so it is template text. Recorded, not trusted.
        "returned": returned,
        "duplicate": duplicate,
        "count_before": count_before,
        "count_after": count_after,
        "created_indices": list(range(count_before + 1, count_after + 1)),
        "extent_before": before,
        "extent_after": after,
        "extent_delta": _extent_delta(before, after),
    })


# -- G1: transforms ----------------------------------------------------------

@guarded(classification=Classification.WRITE_GEOMETRY, module="surfaces",
         channel=Channel.MCP_COM)
def move_surface(index: int, x: float, y: float, z: float,
                 duplicate: bool = False, times: int = 1) -> str:
    """WRITE. Translate one surface, optionally leaving duplicates behind.

    Translations are metres. duplicate=true makes this a creation operation:
    times copies are produced, each offset a further (x, y, z), and the original
    stays put. The control-net bounding box is reported before and after.
    """
    app = com.connect("modeler")
    dx, dy, dz = (_number("x", x), _number("y", y), _number("z", z))
    count = _positive_int("times", times)
    return _transform(
        app, index, "Move",
        lambda surface: surface.Move(dx, dy, dz, bool(duplicate), count),
        {"x": dx, "y": dy, "z": dz, "duplicate": bool(duplicate),
         "times": count},
        bool(duplicate),
    )


@guarded(classification=Classification.WRITE_GEOMETRY, module="surfaces",
         channel=Channel.MCP_COM)
def rotate_surface(index: int, roll: float, pitch: float, yaw: float,
                   long_centre: float, trans_centre: float,
                   vert_centre: float, duplicate: bool = False,
                   times: int = 1) -> str:
    """WRITE. Rotate one surface about a point, optionally duplicating it.

    Angles are degrees, the centre is in metres. duplicate=true produces times
    copies at successive rotations and leaves the original in place.
    """
    app = com.connect("modeler")
    angles = (_number("roll", roll), _number("pitch", pitch),
              _number("yaw", yaw))
    centre = (_number("long_centre", long_centre),
              _number("trans_centre", trans_centre),
              _number("vert_centre", vert_centre))
    count = _positive_int("times", times)
    return _transform(
        app, index, "Rotate",
        lambda surface: surface.Rotate(*angles, *centre, bool(duplicate),
                                       count),
        {"roll": angles[0], "pitch": angles[1], "yaw": angles[2],
         "long_centre": centre[0], "trans_centre": centre[1],
         "vert_centre": centre[2], "duplicate": bool(duplicate),
         "times": count},
        bool(duplicate),
    )


@guarded(classification=Classification.WRITE_GEOMETRY, module="surfaces",
         channel=Channel.MCP_COM)
def rescale_surface(index: int, scale_x: float, scale_y: float, scale_z: float,
                    centre_x: float = 0.0, centre_y: float = 0.0,
                    centre_z: float = 0.0) -> str:
    """WRITE. Scale one surface about a centre point. Never duplicates.

    Scale factors are dimensionless; the centre is metres. A factor of zero
    would collapse the surface into a plane and is refused.
    """
    app = com.connect("modeler")
    factors = (_number("scale_x", scale_x), _number("scale_y", scale_y),
               _number("scale_z", scale_z))
    for axis, factor in zip(("scale_x", "scale_y", "scale_z"), factors):
        if factor == 0:
            raise MaxsurfValidationError(
                f"{axis} of zero would collapse the surface onto a plane",
                field=axis,
            )
    centre = (_number("centre_x", centre_x), _number("centre_y", centre_y),
              _number("centre_z", centre_z))
    return _transform(
        app, index, "ReScale",
        lambda surface: surface.ReScale(*factors, *centre),
        {"scale_x": factors[0], "scale_y": factors[1], "scale_z": factors[2],
         "centre_x": centre[0], "centre_y": centre[1], "centre_z": centre[2]},
        False,
    )


@guarded(classification=Classification.WRITE_GEOMETRY, module="surfaces",
         channel=Channel.MCP_COM)
def flip_surface(index: int, direction: str, plane_location: float,
                 duplicate: bool = False) -> str:
    """WRITE. Mirror one surface about a plane, optionally keeping the original.

    direction is an msDirectionType member name: msDTLongitudinal,
    msDTTransverse or msDTVertical, resolved from the type library at runtime.
    plane_location is where that plane sits, in metres.

    duplicate=true is how a symmetric hull gets its other half while keeping
    the one that was modelled. Note that a hull modelled on one side and marked
    Symmetrical is the other route, and the cheaper one: see
    set_surface_properties.
    """
    app = com.connect("modeler")
    resolved = _resolve_enum(app, ENUM_PREFIXES["direction"], direction,
                             "direction")
    location = _number("plane_location", plane_location)
    return _transform(
        app, index, "Flip",
        lambda surface: surface.Flip(resolved["value"], location,
                                     bool(duplicate)),
        {"direction": resolved, "plane_location": location,
         "duplicate": bool(duplicate)},
        bool(duplicate),
    )


@guarded(classification=Classification.WRITE_GEOMETRY, module="surfaces",
         channel=Channel.MCP_COM)
def invert_surface_normal(index: int) -> str:
    """WRITE. Flip which side of the surface faces outwards.

    Equivalent to clicking the end of the surface's outside arrow in the GUI.
    This decides which side is 'wet', so it changes what hydrostatics
    integrates, not merely how the surface is shaded.
    """
    app = com.connect("modeler")
    return _transform(
        app, index, "InvertOutsideArrowDirection",
        lambda surface: surface.InvertOutsideArrowDirection(),
        {}, False,
    )


# -- G2: properties and assemblies -------------------------------------------

def _read_property(app: Any, surface: Any, field: str) -> dict[str, Any]:
    member, kind, prefix = SURFACE_PROPERTIES[field]
    probed = com.probe(surface, member)
    entry: dict[str, Any] = {"member": member, "kind": kind}
    if probed["status"] != "read":
        attempt = (probed["attempts"] or [{}])[0]
        entry.update({"value": None,
                      "status": attempt.get("status") or probed["status"],
                      "detail": attempt.get("error")})
        return entry
    raw = probed["value"]
    try:
        if kind == "bool":
            value: Any = bool(raw)
        elif kind == "int" or kind == "enum":
            value = int(raw)
        elif kind == "float":
            value = float(raw)
        else:
            value = str(raw)
    except (TypeError, ValueError) as exc:
        return {**entry, "value": None, "status": "uncastable",
                "detail": str(exc)[:200]}
    entry.update({"value": value, "status": "read", "detail": None})
    if kind == "enum" and prefix:
        entry["enum_name"] = _enum_name(_enum_map(app, prefix), value)
    return entry


def _properties_payload(app: Any, surface: Any, index: int) -> dict[str, Any]:
    return {
        "index": index,
        "properties": {
            field: _read_property(app, surface, field)
            for field in SURFACE_PROPERTIES
        },
    }


@guarded(classification=Classification.READ, module="surfaces",
         channel=Channel.MCP_COM)
def get_surface_properties(index: int = 1) -> str:
    """READ-ONLY. Report every proven property of one surface.

    Each field is {value, status, member, kind}, with enum_name added where the
    value is an enum, so an unreadable property is never reported as a zero or
    an empty string. Two fields decide what hydrostatics measures: use
    (msSUHull vs msSUStructure) and visible.
    """
    app = com.connect("modeler")
    surface, _count = _surface_at(app.Design.Surfaces, index)
    return com.to_json(_properties_payload(app, surface, index))


def _coerce(app: Any, field: str, value: Any) -> dict[str, Any]:
    """Turn a caller's value into what the COM property expects."""
    member, kind, prefix = SURFACE_PROPERTIES[field]
    if kind == "bool":
        if not isinstance(value, bool):
            raise MaxsurfValidationError(
                f"{field} must be true or false", field=field,
                received=repr(value),
            )
        return {"value": value, "enum_name": None}
    if kind == "str":
        if not isinstance(value, str) or not value.strip():
            raise MaxsurfValidationError(
                f"{field} must be a non-empty string", field=field,
                received=repr(value),
            )
        return {"value": value, "enum_name": None}
    if kind == "float":
        return {"value": _number(field, value), "enum_name": None}
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise MaxsurfValidationError(
                f"{field} must be an integer", field=field,
                received=repr(value),
            )
        return {"value": int(value), "enum_name": None}
    # enum: accept the member name, never a bare number
    resolved = _resolve_enum(app, prefix or "", str(value), field)
    return {"value": resolved["value"], "enum_name": resolved["enum_name"]}


def _apply_properties(app: Any, surface: Any,
                      requested: dict[str, Any]) -> list[dict[str, Any]]:
    """Write each property, then read it back. Writability is not assumed.

    The help prints ``[= value]`` on read-only properties too, so a refusal is a
    real possibility for any of these and is reported per field rather than
    raised. A caller gets a truthful account of which of its changes landed.
    """
    validated = {field: _coerce(app, field, value)
                 for field, value in requested.items()}
    applied: list[dict[str, Any]] = []
    for field, value in requested.items():
        member, kind, _prefix = SURFACE_PROPERTIES[field]
        before = _read_property(app, surface, field)
        coerced = validated[field]
        record: dict[str, Any] = {
            "field": field, "member": member,
            "before": before["value"], "requested": coerced["value"],
            "requested_enum_name": coerced["enum_name"],
            "write_error": None,
        }
        try:
            setattr(surface, member, coerced["value"])
        except Exception as exc:  # noqa: BLE001 - writability is not proven
            record["write_error"] = str(exc)[:200]
        after = _read_property(app, surface, field)
        record["after"] = after["value"]
        record["after_enum_name"] = after.get("enum_name")
        record["stored"] = (after["status"] == "read"
                            and after["value"] == coerced["value"])
        applied.append(record)
    return applied


@guarded(classification=Classification.WRITE_STATE, module="surfaces",
         channel=Channel.MCP_COM)
def set_surface_properties(index: int, properties_json: str) -> str:
    """WRITE_STATE. Set surface properties, verifying each one by reading it back.

    properties_json: {"visible": false, "use": "msSUStructure", "name": "Hull"}
    Enum fields take member names, never numbers: type (msST...), use (msSU...),
    skin_direction (msSSD...). Unknown names are refused with the list of valid
    members.

    symmetrical matters more than it looks: a hull modelled on one side only
    displaces half of what it should unless it is set. Nothing here raises
    because Maxsurf declined a write; each field reports stored true or false.
    """
    try:
        requested = json.loads(properties_json)
    except ValueError as exc:
        raise MaxsurfValidationError(
            "properties_json must be valid JSON",
            head=properties_json[:200],
        ) from exc
    if not isinstance(requested, dict) or not requested:
        raise MaxsurfValidationError(
            "properties_json must be a non-empty JSON object",
            known_properties=sorted(SURFACE_PROPERTIES),
        )
    unknown = sorted(set(requested) - set(SURFACE_PROPERTIES))
    if unknown:
        raise MaxsurfValidationError(
            "unknown surface property", unknown=unknown,
            known_properties=sorted(SURFACE_PROPERTIES),
        )
    refused = sorted(set(requested) & set(READ_ONLY_PROPERTIES))
    if refused:
        raise MaxsurfValidationError(
            "these properties describe the surface and are not settable here",
            read_only=refused,
        )

    app = com.connect("modeler")
    surface, _count = _surface_at(app.Design.Surfaces, index)
    applied = _apply_properties(app, surface, requested)
    not_stored = [record["field"] for record in applied if not record["stored"]]
    if not_stored:
        raise MaxsurfCOMError("surface properties were not stored",
                              not_stored=not_stored, applied=applied,
                              partial_state_possible=True)
    return com.to_json({
        "index": index,
        "requested": sorted(requested),
        "applied": applied,
        "not_stored": not_stored,
        "warnings": (
            [f"Maxsurf did not store: {', '.join(not_stored)}; the automation "
             f"help marks every property as settable but that annotation is "
             f"boilerplate, so a refusal here is a real answer"]
            if not_stored else []
        ),
    })


@guarded(classification=Classification.WRITE_STATE, module="surfaces",
         channel=Channel.MCP_COM)
def set_assembly_visibility(assembly_name: str, visible: bool) -> str:
    """WRITE_STATE. Show or hide every surface in one assembly.

    Hydrostatics integrates visible surfaces only, so this is what decides
    whether a displacement figure describes the hull or the hull plus its deck,
    superstructure and appendages. The match on AssemblyName is exact and
    case-sensitive; if nothing matches, nothing is written and the available
    names are returned.

    Maxsurf exposes no bulk visibility setter -- SurfaceList carries only Name,
    Type and Color -- so this iterates, and reports each surface it touched.
    """
    if not isinstance(assembly_name, str) or not assembly_name.strip():
        raise MaxsurfValidationError("assembly_name must be a non-empty string")
    if not isinstance(visible, bool):
        raise MaxsurfValidationError(
            "visible must be true or false", received=repr(visible)
        )

    app = com.connect("modeler")
    surfaces = app.Design.Surfaces
    count = int(surfaces.Count)
    matched: list[dict[str, Any]] = []
    available: dict[str, int] = {}
    for index in range(1, count + 1):
        surface = surfaces(index)
        probed = com.probe(surface, "AssemblyName")
        name = str(probed["value"]) if probed["status"] == "read" else None
        key = name if name is not None else "(unreadable)"
        available[key] = available.get(key, 0) + 1
        if name != assembly_name:
            continue
        before = com.probe(surface, "Visible")
        error = None
        try:
            surface.Visible = visible
        except Exception as exc:  # noqa: BLE001
            error = str(exc)[:200]
        after = com.probe(surface, "Visible")
        matched.append({
            "index": index,
            "name": com.first_available(surface, "Name"),
            "before": bool(before["value"]) if before["status"] == "read"
            else None,
            "after": bool(after["value"]) if after["status"] == "read"
            else None,
            "stored": after["status"] == "read"
            and bool(after["value"]) is visible,
            "write_error": error,
        })

    if not matched:
        raise MaxsurfValidationError(
            "no surface belongs to that assembly; nothing was written",
            assembly_name=assembly_name,
            available_assemblies=sorted(available),
        )
    not_stored = [record["index"] for record in matched if not record["stored"]]
    return com.to_json({
        "assembly_name": assembly_name,
        "requested_visible": visible,
        "surfaces_in_design": count,
        "matched": matched,
        "not_stored": not_stored,
        "assemblies_seen": available,
    })


# -- G3: construction --------------------------------------------------------

@guarded(classification=Classification.READ, module="surfaces",
         channel=Channel.MCP_COM)
def list_surface_library() -> str:
    """READ-ONLY. List every msSurfaceLibrary shape this build actually offers.

    Resolved from the generated type library rather than from documentation,
    because the numbering is build-specific and getting it wrong creates the
    wrong shape silently. Pass any of these names to create_library_surface.
    """
    app = com.connect("modeler")
    shapes = _enum_map(app, ENUM_PREFIXES["library"])
    return com.to_json({
        "count": len(shapes),
        "shapes": [{"enum_name": name, "value": value}
                   for name, value in sorted(shapes.items(),
                                             key=lambda item: item[1])],
        "note": "pass enum_name to create_library_surface; the numbers are "
                "resolved at runtime and must not be hard-coded",
    })


@guarded(classification=Classification.WRITE_GEOMETRY, module="surfaces",
         channel=Channel.MCP_COM)
def add_quadrilateral_surface(name: str, symmetry: str,
                              corners_json: str) -> str:
    """WRITE. Create a surface from four corner points.

    corners_json names the four corners explicitly, because their order is the
    one thing that silently produces a twisted surface:
      {"bottom_right": [x, y, z], "bottom_left": [...],
       "top_left": [...], "top_right": [...]}
    symmetry is an msShapeSymmetryType member name: msSSTAsymmetric,
    msSSTSymmetricalHalf or msSSTSymmetricalFull.

    Coordinates are metres, in the order longitudinal, offset, vertical.
    """
    if not isinstance(name, str) or not name.strip():
        raise MaxsurfValidationError("name must be a non-empty string")
    try:
        corners = json.loads(corners_json)
    except ValueError as exc:
        raise MaxsurfValidationError(
            "corners_json must be valid JSON", head=corners_json[:200]
        ) from exc
    order = ("bottom_right", "bottom_left", "top_left", "top_right")
    if not isinstance(corners, dict):
        raise MaxsurfValidationError(
            "corners_json must be a JSON object", required_corners=list(order)
        )
    missing = [corner for corner in order if corner not in corners]
    if missing:
        raise MaxsurfValidationError(
            "every corner is required", missing=missing,
            required_corners=list(order),
        )
    values: list[float] = []
    for corner in order:
        point = corners[corner]
        if not isinstance(point, (list, tuple)) or len(point) != 3:
            raise MaxsurfValidationError(
                f"{corner} must be [longitudinal, offset, vertical]",
                corner=corner, received=repr(point),
            )
        values.extend(_number(f"{corner}[{axis}]", point[axis])
                      for axis in range(3))

    app = com.connect("modeler")
    resolved = _resolve_enum(app, ENUM_PREFIXES["symmetry"], symmetry,
                             "symmetry")
    surfaces = app.Design.Surfaces
    count_before = int(surfaces.Count)
    surfaces.AddQuadrilateralFromCorners(name, resolved["value"], *values)
    count_after = int(surfaces.Count)
    if count_after != count_before + 1:
        raise MaxsurfCOMError(
            "AddQuadrilateralFromCorners did not add exactly one surface",
            count_before=count_before, count_after=count_after,
        )
    created = surfaces(count_after)
    rows, columns = _control_point_limits(created)
    return com.to_json({
        "created_index": count_after,
        "name": created.Name,
        "symmetry": resolved,
        "corners": {corner: corners[corner] for corner in order},
        "rows": rows,
        "columns": columns,
        "count_before": count_before,
        "count_after": count_after,
    })


#: The parametric adders, with their argument names in call order. Every
#: signature was read from the decompiled ModelerAutomation.chm. ``orient`` is an
#: msDirectionType, ``symmetry`` an msShapeSymmetryType; both are named in
#: ENUM_ARGUMENTS so they are resolved from names rather than passed as numbers.
PARAMETRIC_SHAPES: dict[str, tuple[str, ...]] = {
    "box": ("name", "orient", "close_ends", "symmetry", "length", "width",
            "height", "centre_long", "centre_offset", "centre_height"),
    "box_start_end": ("name", "close_ends", "symmetry", "width", "height",
                      "start_long", "start_offset", "start_height",
                      "end_long", "end_offset", "end_height"),
    "cylinder180": ("name", "orient", "close_ends", "symmetry", "length",
                    "radius", "centre_long", "centre_offset", "centre_height"),
    "cylinder180_start_end": ("name", "close_ends", "symmetry", "radius",
                              "start_long", "start_offset", "start_height",
                              "end_long", "end_offset", "end_height"),
    "cylinder90": ("name", "orient", "close_ends", "symmetry", "length",
                   "radius", "centre_long", "centre_offset", "centre_height"),
    "cylinder90_start_end": ("name", "close_ends", "symmetry", "radius",
                             "start_long", "start_offset", "start_height",
                             "end_long", "end_offset", "end_height"),
    "spheroid": ("name", "orient", "symmetry", "radius_long", "radius_width",
                 "radius_height", "centre_long", "centre_offset",
                 "centre_height"),
    "hemispheroid": ("name", "orient", "close_ends", "symmetry", "radius_long",
                     "radius_width", "radius_height", "centre_long",
                     "centre_offset", "centre_height"),
}

#: COM member behind each shape.
PARAMETRIC_MEMBERS: dict[str, str] = {
    "box": "AddBox",
    "box_start_end": "AddBoxStartEnd",
    "cylinder180": "AddCylinder180d",
    "cylinder180_start_end": "AddCylinder180dStartEnd",
    "cylinder90": "AddCylinder90d",
    "cylinder90_start_end": "AddCylinder90dStartEnd",
    "spheroid": "AddSpheroid",
    "hemispheroid": "AddHemispheroid",
}

#: Arguments that are enum member names rather than numbers.
ENUM_ARGUMENTS = {"orient": "direction", "symmetry": "symmetry"}

#: Arguments that are booleans.
BOOL_ARGUMENTS = {"close_ends"}


@guarded(classification=Classification.WRITE_GEOMETRY, module="surfaces",
         channel=Channel.MCP_COM)
def add_parametric_surface(shape: str, parameters_json: str) -> str:
    """WRITE. Create a surface from one of the parametric generators.

    This is the direct construction route: the shape is placed where it belongs
    with the dimensions it should have, rather than created at some default size
    and then moved and scaled into position.

    shape is one of: box, box_start_end, cylinder180, cylinder180_start_end,
    cylinder90, cylinder90_start_end, spheroid, hemispheroid.

    parameters_json supplies that shape's arguments by name. Call with an
    unknown shape, or with arguments missing, to be told exactly which names
    that shape expects and in what order. orient and symmetry take enum member
    names (msDT..., msSST...), never numbers; lengths are metres.
    """
    key = (shape or "").strip().casefold()
    if key not in PARAMETRIC_SHAPES:
        raise MaxsurfValidationError(
            "unknown parametric shape", requested=shape,
            available=sorted(PARAMETRIC_SHAPES),
        )
    expected = PARAMETRIC_SHAPES[key]
    try:
        supplied = json.loads(parameters_json)
    except ValueError as exc:
        raise MaxsurfValidationError(
            "parameters_json must be valid JSON", head=parameters_json[:200]
        ) from exc
    if not isinstance(supplied, dict):
        raise MaxsurfValidationError(
            "parameters_json must be a JSON object", shape=key,
            expected_parameters=list(expected),
        )
    missing = [name for name in expected if name not in supplied]
    unknown = sorted(set(supplied) - set(expected))
    if missing or unknown:
        raise MaxsurfValidationError(
            "parameters do not match this shape", shape=key,
            missing=missing, unknown=unknown,
            expected_parameters=list(expected),
        )

    app = com.connect("modeler")
    surfaces = app.Design.Surfaces
    arguments: list[Any] = []
    resolved: dict[str, Any] = {}
    for name in expected:
        value = supplied[name]
        if name == "name":
            if not isinstance(value, str) or not value.strip():
                raise MaxsurfValidationError("name must be a non-empty string")
            arguments.append(value)
            resolved[name] = value
        elif name in BOOL_ARGUMENTS:
            if not isinstance(value, bool):
                raise MaxsurfValidationError(
                    f"{name} must be true or false", field=name,
                    received=repr(value),
                )
            arguments.append(value)
            resolved[name] = value
        elif name in ENUM_ARGUMENTS:
            enum = _resolve_enum(app, ENUM_PREFIXES[ENUM_ARGUMENTS[name]],
                                 str(value), name)
            arguments.append(enum["value"])
            resolved[name] = enum
        else:
            number = _number(name, value)
            arguments.append(number)
            resolved[name] = number

    member = PARAMETRIC_MEMBERS[key]
    count_before = int(surfaces.Count)
    getattr(surfaces, member)(*arguments)
    count_after = int(surfaces.Count)
    if count_after <= count_before:
        raise MaxsurfCOMError(
            f"{member} did not add a surface", shape=key,
            count_before=count_before, count_after=count_after,
        )
    # These generators do NOT create one surface. Proven live: AddBox with
    # close_ends=True produced five (BoxAft, BoxFwd, BoxTop, BoxBottom and the
    # named body) and AddHemispheroid produced two. Every created index is
    # returned, because assuming one leaves the rest carrying whatever Use they
    # were born with -- and a stray msSUHull panel is integrated by
    # hydrostatics as though it were part of the hull.
    created = []
    for index in range(count_before + 1, count_after + 1):
        surface = surfaces(index)
        rows, columns = _control_point_limits(surface)
        created.append({
            "index": index,
            "name": surface.Name,
            "rows": rows,
            "columns": columns,
            "use": com.first_available(surface, "Use"),
        })
    return com.to_json({
        "shape": key,
        "member": member,
        "parameters": resolved,
        "created": created,
        "created_indices": [entry["index"] for entry in created],
        "created_index": count_after,
        "count_before": count_before,
        "count_after": count_after,
        "surfaces_created": count_after - count_before,
        "warnings": (
            [f"{member} created {count_after - count_before} surfaces, not one; "
             f"set the intended Use on every index in created_indices, or "
             f"hydrostatics will integrate the ones left as msSUHull"]
            if count_after - count_before > 1 else []
        ),
    })


@guarded(classification=Classification.WRITE_GEOMETRY, module="surfaces",
         channel=Channel.MCP_COM)
def bond_surfaces(first_index: int, first_edge: str, second_index: int,
                  second_edge: str, continuity: str = "msBondC0",
                  insert_connecting_surface: bool = False) -> str:
    """WRITE. Bond one surface edge to another so the skin stays continuous.

    Edges are msSurfaceEdge member names: msEdgeTop, msEdgeLeft, msEdgeRight,
    msEdgeBottom. continuity is msSurfaceBondContinuity: msBondC0 for position
    only, msBondC1approx or msBondC1exact for tangent continuity as well.

    insert_connecting_surface=true adds a surface between the two edges rather
    than joining them directly, which changes the model's surface count. Both
    counts are reported.
    """
    app = com.connect("modeler")
    surfaces = app.Design.Surfaces
    first, count_before = _surface_at(surfaces, first_index)
    second, _count = _surface_at(surfaces, second_index)
    if first_index == second_index and first_edge == second_edge:
        raise MaxsurfSafetyError(
            "an edge cannot be bonded to itself",
            index=first_index, edge=first_edge,
        )
    edges = {
        "first_edge": _resolve_enum(app, ENUM_PREFIXES["edge"], first_edge,
                                    "first_edge"),
        "second_edge": _resolve_enum(app, ENUM_PREFIXES["edge"], second_edge,
                                     "second_edge"),
    }
    bond = _resolve_enum(app, ENUM_PREFIXES["continuity"], continuity,
                         "continuity")
    names = (first.Name, second.Name)
    surfaces.Bond(first_index, edges["first_edge"]["value"], second_index,
                  edges["second_edge"]["value"], bond["value"],
                  bool(insert_connecting_surface))
    count_after = int(surfaces.Count)
    first_target = {"surface": str(first.BondedToWhichSurface(edges["first_edge"]["value"])),
                    "edge": int(first.BondedToWhichEdge(edges["first_edge"]["value"]))}
    second_target = {"surface": str(second.BondedToWhichSurface(edges["second_edge"]["value"])),
                     "edge": int(second.BondedToWhichEdge(edges["second_edge"]["value"]))}
    if insert_connecting_surface:
        verified = (count_after > count_before and
                    first_target["surface"] not in ("", "Unbonded") and first_target["edge"] in range(1, 5) and
                    second_target["surface"] not in ("", "Unbonded") and second_target["edge"] in range(1, 5))
    else:
        verified = (count_after == count_before and
                    first_target == {"surface": names[1], "edge": edges["second_edge"]["value"]} and
                    second_target == {"surface": names[0], "edge": edges["first_edge"]["value"]})
    if not verified:
        raise MaxsurfCOMError("native bond request was not verified by reciprocal readback",
                              first_target=first_target, second_target=second_target,
                              count_before=count_before, count_after=count_after,
                              partial_state_possible=True)
    return com.to_json({
        "first": {"index": first_index, "name": names[0],
                  "edge": edges["first_edge"]},
        "second": {"index": second_index, "name": names[1],
                   "edge": edges["second_edge"]},
        "continuity": bond,
        "bond_readback_verified": True,
        "continuity_readback": "not_exposed_by_native_api",
        "insert_connecting_surface": bool(insert_connecting_surface),
        "count_before": count_before,
        "count_after": count_after,
        "surfaces_created": count_after - count_before,
    })


@guarded(classification=Classification.WRITE_GEOMETRY, module="surfaces",
         channel=Channel.MCP_COM)
def unbond_surface(index: int, edge: str) -> str:
    """WRITE. Release one bonded edge. Geometry stays; the constraint goes."""
    app = com.connect("modeler")
    surfaces = app.Design.Surfaces
    surface, count_before = _surface_at(surfaces, index)
    resolved = _resolve_enum(app, ENUM_PREFIXES["edge"], edge, "edge")
    name = surface.Name
    surfaces.Unbond(index, resolved["value"])
    return com.to_json({
        "index": index,
        "name": name,
        "edge": resolved,
        "count_before": count_before,
        "count_after": int(surfaces.Count),
    })


TOOLS = (
    move_surface,
    rotate_surface,
    rescale_surface,
    flip_surface,
    invert_surface_normal,
    get_surface_properties,
    set_surface_properties,
    set_assembly_visibility,
    list_surface_library,
    add_quadrilateral_surface,
    add_parametric_surface,
    bond_surfaces,
    unbond_surface,
)


def register_tools(mcp: Any) -> list[str]:
    """Register the surface transform, property and construction tools."""
    return register(mcp, TOOLS)
