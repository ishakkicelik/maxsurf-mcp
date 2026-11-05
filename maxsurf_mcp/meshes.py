"""Guarded native Modeler trimesh controls.

Native source: MaxsurfModeler.exe BentleyModeler 1.1, Maxsurf 2026.
Official ModelerManual.pdf pp.171-174 documents join, Boolean suitability and
repair. The GUI's Difference option has no automation counterpart here.
No native node or triangle accessor exists; this adapter does not fabricate one.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from . import com, validation
from .audit import Channel, Classification
from .errors import MaxsurfValidationError
from .guard import guarded, register
from .mesh_common import (
    PROPERTIES, at, boolean, collection, created, enum, enum_members, integer,
    new_name, number, require, statistics, summary,
)


@guarded(classification=Classification.READ, module="modeler",
         channel=Channel.MCP_COM)
def list_meshes() -> str:
    """READ. List native trimeshes, counts and stored properties."""
    _, meshes = collection()
    return com.to_json({"count": int(meshes.Count), "meshes": [
        summary(meshes.Item(i)) for i in range(1, int(meshes.Count) + 1)]})


@guarded(classification=Classification.READ, module="modeler",
         channel=Channel.MCP_COM)
def get_mesh_statistics(index: int) -> str:
    """READ. Calculate and read every native mesh statistic."""
    _, meshes = collection()
    mesh = at(meshes, index)
    return com.to_json({"mesh": summary(mesh), "statistics": statistics(mesh)})


@guarded(classification=Classification.READ, module="modeler",
         channel=Channel.MCP_COM)
def get_mesh_enums() -> str:
    """READ. Discover actual installed native mesh metadata enums."""
    app, _ = collection()
    return com.to_json({kind: enum_members(app, kind) for kind in (
        "msMeshType", "msMeshDiagonalLayoutType", "msMeshRepairMethod",
        "msDirectionType", "msSurfaceUse", "msSurfaceSkinDirection")})


@guarded(classification=Classification.WRITE_GEOMETRY, module="modeler",
         channel=Channel.MCP_COM)
def mesh_surface_edge_length(name: str, surface_index: int,
                             minimum_edge_length: float,
                             maximum_edge_length: float,
                             mesh_type: str = "msMTTriIso",
                             preserve_feature_lines: bool = True) -> str:
    """WRITE_GEOMETRY. Mesh one NURB surface using edge lengths in metres."""
    lo = number(minimum_edge_length, "minimum_edge_length", positive=True)
    hi = number(maximum_edge_length, "maximum_edge_length", minimum=lo)
    boolean(preserve_feature_lines, "preserve_feature_lines")
    app, meshes = collection()
    integer(surface_index, "surface_index",
            maximum=int(app.Design.Surfaces.Count))
    name = new_name(meshes, name)
    kind = enum(app, "msMeshType", mesh_type)
    before = int(meshes.Count)
    meshes.MeshFromSurfaceEdgeLength(name, surface_index, lo, hi, kind,
                                    preserve_feature_lines)
    return com.to_json({"mesh": created(meshes, before, name),
                        "verified_postconditions": ["count", "name", "nonempty"]})


@guarded(classification=Classification.WRITE_GEOMETRY, module="modeler",
         channel=Channel.MCP_COM)
def mesh_surface_parametric(name: str, surface_index: int,
                            longitudinal_triangles: int,
                            transverse_triangles: int,
                            diagonal_layout: str,
                            shortest_diagonal_override_percent: float = 0.0,
                            mesh_type: str = "msMTTriIso") -> str:
    """WRITE_GEOMETRY. Parametrically mesh one native NURB surface."""
    longitudinal_triangles = integer(longitudinal_triangles, "longitudinal_triangles")
    transverse_triangles = integer(transverse_triangles, "transverse_triangles")
    percent = number(shortest_diagonal_override_percent, "override_percent",
                     minimum=0, maximum=100)
    app, meshes = collection()
    integer(surface_index, "surface_index",
            maximum=int(app.Design.Surfaces.Count))
    name = new_name(meshes, name)
    layout = enum(app, "msMeshDiagonalLayoutType", diagonal_layout)
    kind = enum(app, "msMeshType", mesh_type)
    before = int(meshes.Count)
    meshes.MeshFromSurfaceParametric(name, surface_index, longitudinal_triangles,
                                    transverse_triangles, layout, percent, kind)
    return com.to_json({"mesh": created(meshes, before, name),
                        "verified_postconditions": ["count", "name", "nonempty"]})


@guarded(classification=Classification.WRITE_GEOMETRY, module="modeler",
         channel=Channel.MCP_COM)
def set_mesh_properties(index: int, properties_json: str) -> str:
    """WRITE_GEOMETRY. Validate all property edits, then verify stored values."""
    edits = validation.parsed(properties_json, dict, "properties_json")
    if not edits or set(edits) - PROPERTIES.keys():
        raise MaxsurfValidationError("unknown or empty mesh properties",
                                     available=sorted(PROPERTIES))
    app, meshes = collection()
    mesh = at(meshes, index)
    prepared = {}
    for key, value in edits.items():
        member, kind = PROPERTIES[key]
        if kind == "name":
            value = validation.name(value)
            if value != str(mesh.Name):
                value = new_name(meshes, value)
        elif kind == "bool":
            value = boolean(value, key)
        elif kind in ("integer", "color", "transparency"):
            value = integer(value, key, minimum=0,
                            maximum={"color": 0xFFFFFF, "transparency": 100}.get(
                                kind, 2147483647))
        elif kind == "thickness":
            value = number(value, key, minimum=0)
        else:
            value = enum(app, kind, value)
        prepared[member] = value
    for member, value in prepared.items():
        validation.stored(mesh, member, value)
    return com.to_json({"mesh": summary(mesh), "verified": True})


def _transform(index: int, member: str, args: tuple[Any, ...],
               duplicate: bool = False, copies: int = 0,
               area_factor: float | None = None,
               volume_factor: float | None = None) -> str:
    _, meshes = collection()
    mesh = at(meshes, index)
    before_count = int(meshes.Count)
    before = summary(mesh)
    stats_before = statistics(mesh)
    getattr(mesh, member)(*args)
    require(int(meshes.Count) == before_count + copies,
            "transform duplication count mismatch", expected=before_count + copies,
            actual=int(meshes.Count))
    source = at(meshes, index)
    require(int(source.ID) == before["id"], "transform changed source identity")
    targets = ([meshes.Item(i) for i in range(before_count + 1,
                                             int(meshes.Count) + 1)]
               if duplicate else [source])
    after = []
    for target in targets:
        info, stats = summary(target), statistics(target)
        require(info["nodes"] == before["nodes"] and
                info["triangles"] == before["triangles"],
                "transform changed topology counts")
        if area_factor is not None:
            require(math.isclose(stats["Area"], stats_before["Area"] * area_factor,
                                 rel_tol=1e-7, abs_tol=1e-8),
                    "transform area postcondition mismatch")
        # Signed volume only has a geometric interpretation on closed meshes.
        if volume_factor is not None and stats_before["NumBorderEdges"] == 0:
            require(math.isclose(stats["SignedVolume"],
                                 stats_before["SignedVolume"] * volume_factor,
                                 rel_tol=1e-7, abs_tol=1e-8),
                    "transform volume postcondition mismatch")
        after.append({"mesh": info, "statistics": stats})
    return com.to_json({
        "operation": member, "before": before, "statistics_before": stats_before,
        "after": after, "duplicate": duplicate,
        "verified_postconditions": ["collection_count", "source_identity",
                                     "topology_counts", "aggregate_statistics"],
        "coordinate_verification": "unavailable_native_accessors",
    })


def _duplicates(duplicate: bool, times: int) -> int:
    boolean(duplicate, "duplicate")
    integer(times, "times")
    if not duplicate and times != 1:
        raise MaxsurfValidationError("times only applies to duplication")
    return times if duplicate else 0


@guarded(classification=Classification.WRITE_GEOMETRY, module="modeler",
         channel=Channel.MCP_COM)
def move_mesh(index: int, x: float, y: float, z: float,
              duplicate: bool = False, times: int = 1) -> str:
    """WRITE_GEOMETRY. Translate metres; native coordinates cannot be read back."""
    copies = _duplicates(duplicate, times)
    xyz = tuple(number(v, k) for k, v in zip(("x", "y", "z"), (x, y, z)))
    return _transform(index, "Move", (*xyz, duplicate, copies), duplicate,
                      copies, area_factor=1, volume_factor=1)


@guarded(classification=Classification.WRITE_GEOMETRY, module="modeler",
         channel=Channel.MCP_COM)
def rotate_mesh(index: int, roll: float, pitch: float, yaw: float,
                centre_x: float = 0.0, centre_y: float = 0.0,
                centre_z: float = 0.0, duplicate: bool = False,
                times: int = 1) -> str:
    """WRITE_GEOMETRY. Native roll/pitch/yaw degrees about a centre in metres."""
    copies = _duplicates(duplicate, times)
    args = tuple(number(v, k) for k, v in zip(
        ("roll", "pitch", "yaw", "centre_x", "centre_y", "centre_z"),
        (roll, pitch, yaw, centre_x, centre_y, centre_z)))
    return _transform(index, "Rotate", (*args, duplicate, copies), duplicate,
                      copies, area_factor=1, volume_factor=1)


@guarded(classification=Classification.WRITE_GEOMETRY, module="modeler",
         channel=Channel.MCP_COM)
def scale_mesh(index: int, scale_x: float, scale_y: float, scale_z: float,
               centre_x: float = 0.0, centre_y: float = 0.0,
               centre_z: float = 0.0) -> str:
    """WRITE_GEOMETRY. Rescale about a centre, checking aggregate geometry."""
    scales = tuple(number(v, k, positive=True) for k, v in zip(
        ("scale_x", "scale_y", "scale_z"), (scale_x, scale_y, scale_z)))
    centre = tuple(number(v, k) for k, v in zip(
        ("centre_x", "centre_y", "centre_z"), (centre_x, centre_y, centre_z)))
    area = scales[0] ** 2 if scales[0] == scales[1] == scales[2] else None
    return _transform(index, "ReScale", (*scales, *centre), area_factor=area,
                      volume_factor=math.prod(scales))


@guarded(classification=Classification.WRITE_GEOMETRY, module="modeler",
         channel=Channel.MCP_COM)
def flip_mesh(index: int, direction: str, plane_location: float = 0.0,
              duplicate: bool = False) -> str:
    """WRITE_GEOMETRY. Mirror in a native axis plane, optionally duplicating."""
    boolean(duplicate, "duplicate")
    location = number(plane_location, "plane_location")
    app, _ = collection()
    axis = enum(app, "msDirectionType", direction)
    return _transform(index, "Flip", (axis, location, duplicate), duplicate,
                      int(duplicate), area_factor=1)


@guarded(classification=Classification.WRITE_GEOMETRY, module="modeler",
         channel=Channel.MCP_COM)
def invert_mesh_normals(index: int) -> str:
    """WRITE_GEOMETRY. Invert outside arrows, checking closed signed volume."""
    return _transform(index, "InvertOutsideArrowDirection", (),
                      area_factor=1, volume_factor=-1)


@guarded(classification=Classification.WRITE_GEOMETRY, module="modeler",
         channel=Channel.MCP_COM)
def repair_mesh(index: int, repair_method: str) -> str:
    """WRITE_GEOMETRY. Fill native mesh holes; require closed result readback."""
    app, meshes = collection()
    method = enum(app, "msMeshRepairMethod", repair_method)
    mesh = at(meshes, index)
    before = statistics(mesh)
    identity, count = int(mesh.ID), int(meshes.Count)
    mesh.Repair(method)
    mesh = at(meshes, index)
    after = statistics(mesh)
    require(int(meshes.Count) == count and int(mesh.ID) == identity,
            "repair changed mesh identity or collection count")
    require(after["NumHoles"] == 0 and after["NumBorderEdges"] == 0,
            "native repair left open boundaries", statistics=after)
    return com.to_json({"mesh": summary(mesh), "before": before, "after": after,
                        "verified_postconditions": ["identity", "closed_boundaries"]})


@guarded(classification=Classification.WRITE_GEOMETRY, module="modeler",
         channel=Channel.MCP_COM)
def clean_mesh(index: int, merge_node_tolerance: float = 1e-5,
               skinny_triangle_tolerance: float = 1e-5,
               compact_nodes: bool = True, remove_degenerate_triangles: bool = True,
               remove_duplicate_triangles: bool = True,
               remove_skinny_triangles: bool = True) -> str:
    """WRITE_GEOMETRY. Clean using documented native UInt8 flags 1,2,4,8."""
    lo = number(merge_node_tolerance, "merge_node_tolerance", minimum=0)
    hi = number(skinny_triangle_tolerance, "skinny_triangle_tolerance", minimum=0)
    flags = sum(bit for key, enabled, bit in (
        ("compact_nodes", compact_nodes, 1),
        ("remove_degenerate_triangles", remove_degenerate_triangles, 2),
        ("remove_duplicate_triangles", remove_duplicate_triangles, 4),
        ("remove_skinny_triangles", remove_skinny_triangles, 8))
        if boolean(enabled, key))
    if not flags:
        raise MaxsurfValidationError("at least one cleaning operation is required")
    _, meshes = collection()
    mesh = at(meshes, index)
    identity, count = int(mesh.ID), int(meshes.Count)
    before = statistics(mesh)
    mesh.Clean(lo, hi, flags)
    mesh = at(meshes, index)
    after = statistics(mesh)
    require(int(meshes.Count) == count and int(mesh.ID) == identity,
            "clean changed mesh identity or collection count")
    if remove_degenerate_triangles:
        require(after["NumDegenerateFaces"] == 0,
                "native cleaning left degenerate triangles")
    return com.to_json({"mesh": summary(mesh), "before": before, "after": after,
                        "clean_method": flags, "message": str(mesh.CleanResultMessage),
                        "verified_postconditions": ["identity", "statistics"],
                        "connectivity_verification": "unavailable_native_accessors"})


@guarded(classification=Classification.WRITE_GEOMETRY, module="modeler",
         channel=Channel.MCP_COM)
def combine_meshes(name: str, index_a: int, index_b: int,
                   operation: Literal["join", "union", "intersection"],
                   delete_originals: bool = False, clean_result: bool = False,
                   merge_node_tolerance: float = 1e-5,
                   skinny_triangle_tolerance: float = 1e-5) -> str:
    """WRITE_GEOMETRY. Native join/union/intersection; no BooleanDifference."""
    members = {"join": "Join", "union": "BooleanUnion",
               "intersection": "BooleanIntersection"}
    if not isinstance(operation, str) or operation not in members:
        raise MaxsurfValidationError("unsupported mesh combination",
                                     available=sorted(members))
    boolean(delete_originals, "delete_originals")
    boolean(clean_result, "clean_result")
    lo = number(merge_node_tolerance, "merge_node_tolerance", minimum=0)
    hi = number(skinny_triangle_tolerance, "skinny_triangle_tolerance", minimum=0)
    _, meshes = collection()
    a, b = at(meshes, index_a), at(meshes, index_b)
    if index_a == index_b:
        raise MaxsurfValidationError("combination requires two distinct meshes")
    name = new_name(meshes, name)
    sa, sb = statistics(a), statistics(b)
    if operation != "join":
        for idx, stats in ((index_a, sa), (index_b, sb)):
            if not (stats["NumConnectedComponents"] == 1 and
                    stats["NumBorderEdges"] == stats["NumDegenerateFaces"] ==
                    stats["NumHoles"] == stats["IsSelfIntersecting"] == 0 and
                    stats["SignedVolume"] > 0):
                raise MaxsurfValidationError("mesh is unsuitable for native Boolean",
                                             index=idx, statistics=stats)
    ids = (int(a.ID), int(b.ID))
    before = int(meshes.Count)
    getattr(meshes, members[operation])(name, index_a, index_b, delete_originals,
                                       clean_result, lo, hi)
    result = created(meshes, before, name, ids if delete_originals else ())
    require(result["id"] not in ids, "combination reused source mesh identity")
    after = statistics(at(meshes, result["index"]))
    if not delete_originals:
        surviving = {int(meshes.Item(i).ID)
                     for i in range(1, int(meshes.Count) + 1)}
        require(set(ids) <= surviving, "combination removed originals")
    if operation != "join":
        require(after["NumBorderEdges"] == 0 and
                after["NumDegenerateFaces"] == 0 and
                after["NumHoles"] == 0 and after["IsSelfIntersecting"] == 0,
                "Boolean result is not a closed valid mesh", statistics=after)
        volume = after["SignedVolume"]
        lower, upper = ((max(sa["SignedVolume"], sb["SignedVolume"]),
                         sa["SignedVolume"] + sb["SignedVolume"])
                        if operation == "union" else
                        (0, min(sa["SignedVolume"], sb["SignedVolume"])))
        tolerance = max(1e-8, upper * 1e-7)
        require(lower - tolerance <= volume <= upper + tolerance,
                "Boolean volume postcondition mismatch", volume=volume,
                minimum=lower, maximum=upper)
    return com.to_json({"operation": operation, "mesh": result,
                        "statistics": after,
                        "verified_postconditions": ["identity", "collection_count",
                                                     "nonempty", "statistics"],
                        "connectivity_verification": "unavailable_native_accessors"})


@guarded(classification=Classification.WRITE_GEOMETRY, module="modeler",
         channel=Channel.MCP_COM)
def delete_mesh(index: int, confirm: bool = False) -> str:
    """WRITE_GEOMETRY. Delete one native mesh after explicit confirmation."""
    if not boolean(confirm, "confirm"):
        raise MaxsurfValidationError("confirm=true is required to delete a mesh")
    _, meshes = collection()
    mesh = at(meshes, index)
    identity, count = int(mesh.ID), int(meshes.Count)
    mesh.Delete()
    require(int(meshes.Count) == count - 1, "mesh deletion count mismatch")
    require(all(int(meshes.Item(i).ID) != identity
                for i in range(1, int(meshes.Count) + 1)),
            "deleted mesh identity remains")
    return com.to_json({"deleted_id": identity, "verified": True})


TOOLS = (
    list_meshes, get_mesh_statistics, get_mesh_enums, mesh_surface_edge_length,
    mesh_surface_parametric, set_mesh_properties, move_mesh, rotate_mesh,
    scale_mesh, flip_mesh, invert_mesh_normals, repair_mesh, clean_mesh,
    combine_meshes, delete_mesh,
)


def register_tools(mcp: Any) -> list[str]:
    return register(mcp, TOOLS)
