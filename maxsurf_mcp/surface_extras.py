"""Installed Modeler linked-geometry and raw area-property adapters."""

from __future__ import annotations

import json
from typing import Any

from . import com
from .audit import Channel, Classification
from .errors import MaxsurfCOMError, MaxsurfValidationError
from .guard import guarded, register
from .modeler import _surface_at
from .surfaces import _number, _resolve_enum


# Exact input order from ISurface in the installed Modeler 2026 typelib.
LINKED_ARGUMENTS = {
    "rotations": (
        "basisOriginLongPos", "basisOriginOffset", "basisOriginHeight",
        "cloneOriginLongPos", "cloneOriginOffset", "cloneOriginHeight",
        "dRoll", "dPitch", "dYaw", "scaleLong", "scaleTrans", "scaleVert",
    ),
    "coords": (
        "basisOriginLongPos", "basisOriginOffset", "basisOriginHeight",
        "basisAxisDir1Long", "basisAxisDir1Trans", "basisAxisDir1Vert",
        "basisAxisDir2Long", "basisAxisDir2Trans", "basisAxisDir2Vert",
        "cloneOriginLongPos", "cloneOriginOffset", "cloneOriginHeight",
        "cloneAxisDir1Long", "cloneAxisDir1Trans", "cloneAxisDir1Vert",
        "cloneAxisDir2Long", "cloneAxisDir2Trans", "cloneAxisDir2Vert",
        "scaleLong", "scaleTrans", "scaleVert",
    ),
}
LINKED_MEMBERS = {
    "rotations": "CreateLinkedGeomSurfaceRotations",
    "coords": "CreateLinkedGeomSurfaceCoords",
}


def _linked_readback(surface: Any) -> dict[str, Any]:
    return {
        "is_linked": bool(surface.IsLinkedGeom),
        "basis_index": int(surface.LinkedGeomBasisIndex),
        "native_transform_matrix": surface.LinkedGeomTransformMatrix,
        # Native help explicitly identifies this different coordinate system.
        "matrix_coordinates": {"x": "forward", "y": "starboard", "z": "down"},
    }


@guarded(classification=Classification.READ, module="surfaces",
         channel=Channel.MCP_COM)
def get_surface_linked_geometry(index: int) -> str:
    """READ-ONLY. Read native linked status, basis index and transform Variant."""
    app = com.connect("modeler")
    surface, _ = _surface_at(app.Design.Surfaces, index)
    return com.to_json({"index": index, **_linked_readback(surface)})


@guarded(classification=Classification.READ, module="surfaces",
         channel=Channel.MCP_COM)
def get_surface_area_properties(
    index: int, area_type: str = "msATTotal",
    projection_type: str = "msAPT3DTrueSurfaceArea",
) -> str:
    """READ-ONLY. Return native AreaProperties Variant without guessed fields.

    area_type is an msAT enum name; projection_type is an msAPT enum name.
    The native Variant layout is not interpreted as named physical quantities.
    """
    app = com.connect("modeler")
    surface, _ = _surface_at(app.Design.Surfaces, index)
    area = _resolve_enum(app, "msAT", area_type, "area_type")
    projection = _resolve_enum(app, "msAPT", projection_type, "projection_type")
    return com.to_json({
        "index": index, "area_type": area, "projection_type": projection,
        "native_area_properties": surface.AreaProperties(
            area["value"], projection["value"]),
    })


@guarded(classification=Classification.WRITE_GEOMETRY, module="surfaces",
         channel=Channel.MCP_COM)
def create_linked_geometry_surface(
    basis_index: int, definition: str, parameters_json: str,
) -> str:
    """WRITE. Create a native linked clone using rotations or local coords.

    parameters_json uses the exact installed native argument names. Missing
    arguments report the complete required list in native call order. Values
    are finite numbers; coordinate axes must be nonzero and nonparallel.
    Rotations are passed in native units without a guessed angle conversion.
    """
    if not isinstance(definition, str) or definition not in LINKED_ARGUMENTS:
        raise MaxsurfValidationError(
            "definition must be rotations or coords",
            available=sorted(LINKED_ARGUMENTS),
        )
    if not isinstance(parameters_json, str):
        raise MaxsurfValidationError("parameters_json must be a JSON string")
    try:
        supplied = json.loads(parameters_json)
    except ValueError as exc:
        raise MaxsurfValidationError("parameters_json must be valid JSON") from exc
    expected = LINKED_ARGUMENTS[definition]
    if not isinstance(supplied, dict):
        raise MaxsurfValidationError("parameters_json must be a JSON object")
    missing = [name for name in expected if name not in supplied]
    unknown = sorted(set(supplied) - set(expected))
    if missing or unknown:
        raise MaxsurfValidationError(
            "parameters do not match the native linked definition",
            missing=missing, unknown=unknown, expected_parameters=list(expected),
        )
    values = {name: _number(name, supplied[name]) for name in expected}
    if any(values[name] == 0 for name in ("scaleLong", "scaleTrans", "scaleVert")):
        raise MaxsurfValidationError("linked scale factors must be nonzero")
    if definition == "coords":
        for prefix in ("basis", "clone"):
            first, second = (
                [values[f"{prefix}AxisDir{n}{axis}"]
                 for axis in ("Long", "Trans", "Vert")]
                for n in (1, 2)
            )
            cross = [first[1] * second[2] - first[2] * second[1],
                     first[2] * second[0] - first[0] * second[2],
                     first[0] * second[1] - first[1] * second[0]]
            if not any(cross):
                raise MaxsurfValidationError(
                    "local coordinate axes must be nonzero and nonparallel",
                    coordinate_system=prefix,
                )
    app = com.connect("modeler")
    surfaces = app.Design.Surfaces
    basis, count_before = _surface_at(surfaces, basis_index)
    getattr(basis, LINKED_MEMBERS[definition])(
        *(values[name] for name in expected))
    count_after = int(surfaces.Count)
    if count_after != count_before + 1:
        raise MaxsurfCOMError(
            "linked creation did not add exactly one surface",
            count_before=count_before, count_after=count_after,
            partial_state_possible=True,
        )
    readback = _linked_readback(surfaces(count_after))
    if not readback["is_linked"] or readback["basis_index"] != basis_index:
        raise MaxsurfCOMError(
            "linked creation did not retain the requested basis",
            requested_basis=basis_index, readback=readback,
            partial_state_possible=True,
        )
    return com.to_json({
        "created_index": count_after, "definition": definition,
        "member": LINKED_MEMBERS[definition], "parameters": values,
        "count_before": count_before, "count_after": count_after,
        "link_readback_verified": True, **readback,
    })


TOOLS = (get_surface_linked_geometry, get_surface_area_properties,
         create_linked_geometry_surface)


def register_tools(mcp: Any) -> list[str]:
    """Register linked-geometry and raw area-property adapters."""
    return register(mcp, TOOLS)
