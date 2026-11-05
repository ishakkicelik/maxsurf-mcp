"""Exact BentleyModeler 1.1 trimesh contracts and readback helpers.

Inspected from Maxsurf 2026's embedded typelib, without activating Modeler.
ITrimesh has counts but no node-coordinate or triangle-connectivity accessors.
Consequently aggregate statistics do not prove translation or rotation.
"""
from __future__ import annotations

import math
from typing import Any

import pythoncom

from . import com, validation
from .errors import MaxsurfCOMError, MaxsurfValidationError

STATS = (
    "NumVertices", "NumConnectedComponents", "NumBorderEdges",
    "IsSelfIntersecting", "Genus", "Area", "SignedVolume", "NumFaces",
    "NumDegenerateFaces", "NumHoles", "FaceMinArea", "FaceMaxArea",
    "FaceMedianArea", "FaceMeanArea", "FaceMinAltitude", "FaceMinAR",
    "FaceMaxAR", "FaceMeanAR", "NumEdges", "NumDegenerateEdges",
    "EdgeMinLength", "EdgeMaxLength", "EdgeMedianLength", "EdgeMeanLength",
    "TriMinAngle", "TriMaxAngle", "TriMeanAngle",
)
PROPERTIES = {
    "name": ("Name", "name"), "assembly": ("Assembly", "integer"),
    "color": ("Color", "color"), "use": ("Use", "msSurfaceUse"),
    "locked": ("Locked", "bool"), "symmetrical": ("Symmetrical", "bool"),
    "visible": ("Visible", "bool"), "thickness": ("Thickness", "thickness"),
    "transparency": ("Transparency", "transparency"),
    "skin_direction": ("SkinDirection", "msSurfaceSkinDirection"),
}


def integer(value: Any, field: str, minimum: int = 1,
            maximum: int = 2147483647) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise MaxsurfValidationError("integer out of bounds", field=field,
                                     minimum=minimum, maximum=maximum)
    return value


def boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise MaxsurfValidationError("expected Boolean", field=field)
    return value


def number(value: Any, field: str, **bounds: Any) -> float:
    if type(value) not in (int, float):
        raise MaxsurfValidationError("expected finite number", field=field)
    return validation.number(value, field, **bounds)


def enum_members(app: Any, enum_type: str) -> dict[str, int]:
    """Resolve the actual enum type, not a coincidentally matching prefix."""
    library = app._oleobj_.GetTypeInfo().GetContainingTypeLib()[0]
    for i in range(library.GetTypeInfoCount()):
        ti = library.GetTypeInfo(i)
        if ti.GetDocumentation(-1)[0] == enum_type:
            attr = ti.GetTypeAttr()
            if attr.typekind != pythoncom.TKIND_ENUM:
                break
            return {ti.GetNames(v.memid)[0]: int(v.value)
                    for v in (ti.GetVarDesc(j) for j in range(attr.cVars))}
    raise MaxsurfCOMError("native enum type unavailable", enum_type=enum_type)


def enum(app: Any, enum_type: str, value: str) -> int:
    value = validation.name(value, enum_type)
    members = enum_members(app, enum_type)
    key = next((k for k in members if k.casefold() == value.casefold()), None)
    if key is None:
        raise MaxsurfValidationError("unknown native enum member",
                                     enum_type=enum_type, requested=value,
                                     available=sorted(members))
    return members[key]


def collection() -> tuple[Any, Any]:
    app = com.connect("modeler")
    return app, app.Design.Trimeshs


def at(meshes: Any, index: int) -> Any:
    integer(index, "index", maximum=int(meshes.Count))
    return meshes.Item(index)


def summary(mesh: Any) -> dict[str, Any]:
    return {"id": int(mesh.ID), "index": int(mesh.Index),
            "name": str(mesh.Name), "nodes": int(mesh.NumNodes),
            "triangles": int(mesh.NumTriangles),
            "properties": {key: getattr(mesh, member)
                           for key, (member, _) in PROPERTIES.items()},
            "assembly_name": str(mesh.AssemblyName),
            "is_linked_geometry": bool(mesh.IsLinkedGeom),
            "linked_geometry_basis_index": int(mesh.LinkedGeomBasisIndex),
            "linked_geometry_transform_matrix": mesh.LinkedGeomTransformMatrix}


def statistics(mesh: Any) -> dict[str, Any]:
    mesh.StatsCalc()
    result = {key: getattr(mesh, "Stats" + key) for key in STATS}
    for key, value in result.items():
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise MaxsurfCOMError("invalid native mesh statistic", field=key,
                                  actual=value)
    # Preserve the native Int instead of coercing unknown states to bool.
    if result["IsSelfIntersecting"] not in (0, 1):
        raise MaxsurfCOMError("unknown native self-intersection status",
                              actual=result["IsSelfIntersecting"])
    return result


def require(condition: bool, message: str, **details: Any) -> None:
    if not condition:
        raise MaxsurfCOMError(message, partial_state_possible=True, **details)


def created(meshes: Any, before: int, name: str,
            deleted_ids: tuple[int, ...] = ()) -> dict[str, Any]:
    expected = before + 1 - len(deleted_ids)
    require(int(meshes.Count) == expected, "mesh collection count mismatch",
            expected=expected, actual=int(meshes.Count))
    matches = [meshes.Item(i) for i in range(1, int(meshes.Count) + 1)
               if str(meshes.Item(i).Name) == name]
    require(len(matches) == 1, "created mesh identity mismatch", name=name)
    result = summary(matches[0])
    require(result["nodes"] > 0 and result["triangles"] > 0,
            "native meshing produced empty geometry", mesh=result)
    surviving = {int(meshes.Item(i).ID)
                 for i in range(1, int(meshes.Count) + 1)}
    require(not surviving.intersection(deleted_ids),
            "native operation retained originals", deleted_ids=deleted_ids)
    return result


def new_name(meshes: Any, value: str) -> str:
    value = validation.name(value)
    if any(str(meshes.Item(i).Name).casefold() == value.casefold()
           for i in range(1, int(meshes.Count) + 1)):
        raise MaxsurfValidationError("mesh name already exists", name=value)
    return value
