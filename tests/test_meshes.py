"""Offline native-boundary regressions; no Bentley application is activated."""
from __future__ import annotations

import copy
import json
import math
import types
from unittest import mock

from maxsurf_mcp import meshes
from maxsurf_mcp.mesh_common import PROPERTIES, STATS
from maxsurf_mcp.errors import MaxsurfCOMError, MaxsurfValidationError

from .support import IsolatedEnvTestCase


# Exact enum members inspected from the installed BentleyModeler 1.1 typelib.
ENUMS = {
    "msMeshType": {"msMTTriIso": 1, "msMTTriRA": 2, "msMTQuadDom": 3},
    "msMeshDiagonalLayoutType": {
        "msMDLTFirstDiagonal": 1, "msMDLTSecondDiagonal": 2,
        "msMDLTSymmetricalDiagonal": 3},
    "msMeshRepairMethod": {"msMRepairFill": 1, "msMRepairFillRefine": 2,
                           "msMRepairFillRefineFair": 3},
    "msDirectionType": {"msDTLongitudinal": 1, "msDTTransverse": 2,
                        "msDTVertical": 3},
    "msSurfaceUse": {"msSUHull": 1, "msSUStructure": 2},
    "msSurfaceSkinDirection": {"msSSDOutside": 1, "msSSDCentered": 2,
                               "msSSDInside": 3},
}


class EnumType:
    def __init__(self, name, members):
        self.name, self.members = name, list(members.items())

    def GetDocumentation(self, memid):
        return (self.name, None, None, None)

    def GetTypeAttr(self):
        return types.SimpleNamespace(typekind=0, cVars=len(self.members))

    def GetVarDesc(self, index):
        return types.SimpleNamespace(memid=index, value=self.members[index][1])

    def GetNames(self, memid):
        return (self.members[memid][0],)


class Library:
    def __init__(self):
        self.types = [EnumType(name, members) for name, members in ENUMS.items()]
        # Similar prefixes from unrelated enums must not leak into resolution.
        self.types.append(EnumType("unrelated", {"msMTUnsupported": 99}))

    def GetTypeInfoCount(self):
        return len(self.types)

    def GetTypeInfo(self, index):
        return self.types[index]


class Mesh:
    def __init__(self, owner, identity, name):
        self.owner, self.ID, self.Name = owner, identity, name
        self.NumNodes, self.NumTriangles = 8, 12
        self.Assembly, self.Color, self.Use = 1, 255, 1
        self.Locked, self.Symmetrical, self.Visible = False, False, True
        self.Thickness, self.Transparency, self.SkinDirection = 0.0, 0, 1
        self.AssemblyName = "Hull"
        self.IsLinkedGeom, self.LinkedGeomBasisIndex = False, 0
        self.LinkedGeomTransformMatrix = None
        self.CleanResultMessage = ""
        self.calls, self.ignored = [], set()
        self.stats = {key: 1.0 for key in STATS}
        self.stats.update(NumVertices=8, NumFaces=12, NumEdges=18,
                          NumConnectedComponents=1, NumBorderEdges=0,
                          NumDegenerateFaces=0, NumDegenerateEdges=0,
                          NumHoles=0, IsSelfIntersecting=0, Genus=0,
                          Area=6.0, SignedVolume=1.0)
        self.calculated = False

    @property
    def Index(self):
        return self.owner.items.index(self) + 1

    def __setattr__(self, name, value):
        if name in self.__dict__.get("ignored", set()):
            return
        object.__setattr__(self, name, value)

    def __getattr__(self, name):
        if name.startswith("Stats") and name[5:] in self.stats:
            if not self.calculated:
                raise RuntimeError("StatsCalc must precede getters")
            return self.stats[name[5:]]
        raise AttributeError(name)

    def StatsCalc(self):
        self.calls.append(("StatsCalc",))
        self.calculated = True

    def Move(self, x, y, z, duplicate, times):
        self.calls.append(("Move", x, y, z, duplicate, times))
        if duplicate:
            for _ in range(times):
                self.owner.clone(self)

    def Rotate(self, roll, pitch, yaw, cx, cy, cz, duplicate, times):
        self.calls.append(("Rotate", roll, pitch, yaw, cx, cy, cz, duplicate, times))
        if duplicate:
            for _ in range(times):
                self.owner.clone(self)

    def ReScale(self, sx, sy, sz, cx, cy, cz):
        self.calls.append(("ReScale", sx, sy, sz, cx, cy, cz))
        if "ReScale" not in self.ignored:
            self.stats["Area"] = 2 * (sx * sy + sy * sz + sz * sx)
            self.stats["SignedVolume"] *= sx * sy * sz

    def Flip(self, direction, location, duplicate):
        self.calls.append(("Flip", direction, location, duplicate))
        if duplicate:
            self.owner.clone(self)

    def InvertOutsideArrowDirection(self):
        self.calls.append(("InvertOutsideArrowDirection",))
        if "InvertOutsideArrowDirection" not in self.ignored:
            self.stats["SignedVolume"] *= -1

    def Repair(self, method):
        self.calls.append(("Repair", method))
        if "Repair" not in self.ignored:
            self.stats["NumHoles"] = self.stats["NumBorderEdges"] = 0

    def Clean(self, merge, skinny, flags):
        self.calls.append(("Clean", merge, skinny, flags))
        if flags & 2 and "Clean" not in self.ignored:
            self.stats["NumDegenerateFaces"] = 0
        self.CleanResultMessage = "native result"

    def Delete(self):
        self.calls.append(("Delete",))
        if "Delete" not in self.ignored:
            self.owner.items.remove(self)


class MeshCollection:
    def __init__(self):
        self.items, self.calls, self.next_id = [], [], 1
        self.ignore_creation = False
        self.bad_count = False

    @property
    def Count(self):
        return len(self.items)

    def Item(self, index):
        if type(index) is not int or not 1 <= index <= self.Count:
            raise IndexError(index)
        return self.items[index - 1]

    def add(self, name):
        mesh = Mesh(self, self.next_id, name)
        self.next_id += 1
        self.items.append(mesh)
        return mesh

    def clone(self, mesh):
        clone = self.add(mesh.Name + " copy")
        clone.stats = copy.deepcopy(mesh.stats)
        return clone

    def MeshFromSurfaceEdgeLength(self, name, index, minimum, maximum, kind, lines):
        self.calls.append(("MeshFromSurfaceEdgeLength", name, index, minimum,
                           maximum, kind, lines))
        if not self.ignore_creation:
            self.add(name)

    def MeshFromSurfaceParametric(self, name, index, long, trans, diagonal,
                                  percent, kind):
        self.calls.append(("MeshFromSurfaceParametric", name, index, long, trans,
                           diagonal, percent, kind))
        if not self.ignore_creation:
            self.add(name)

    def _combine(self, member, name, ia, ib, delete, clean, merge, skinny):
        self.calls.append((member, name, ia, ib, delete, clean, merge, skinny))
        a, b = self.Item(ia), self.Item(ib)
        if self.ignore_creation:
            return
        result = self.add(name)
        if member == "Join":
            result.NumNodes = a.NumNodes + b.NumNodes
            result.NumTriangles = a.NumTriangles + b.NumTriangles
        result.stats["SignedVolume"] = (
            a.stats["SignedVolume"] + b.stats["SignedVolume"]
            if member in ("Join", "BooleanUnion") else
            min(a.stats["SignedVolume"], b.stats["SignedVolume"]))
        if delete and not self.bad_count:
            self.items.remove(a)
            self.items.remove(b)

    def Join(self, name, ia, ib, delete, clean, merge, skinny):
        self._combine("Join", name, ia, ib, delete, clean, merge, skinny)

    def BooleanUnion(self, name, ia, ib, delete, clean, merge, skinny):
        self._combine("BooleanUnion", name, ia, ib, delete, clean, merge, skinny)

    def BooleanIntersection(self, name, ia, ib, delete, clean, merge, skinny):
        self._combine("BooleanIntersection", name, ia, ib, delete, clean, merge, skinny)


class MeshTests(IsolatedEnvTestCase):
    def setUp(self):
        super().setUp()
        self.collection = MeshCollection()
        self.mesh = self.collection.add("first")
        self.collection.add("second")
        self.library = Library()
        typeinfo = types.SimpleNamespace(GetContainingTypeLib=lambda: (self.library, 0))
        self.app = types.SimpleNamespace(
            _oleobj_=types.SimpleNamespace(GetTypeInfo=lambda: typeinfo),
            Design=types.SimpleNamespace(Trimeshs=self.collection,
                                         Surfaces=types.SimpleNamespace(Count=2)))
        patcher = mock.patch.object(meshes.com, "connect", return_value=self.app)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_listing_and_all_statistics_use_native_count_and_precalculation(self):
        result = json.loads(meshes.list_meshes())
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["meshes"][0]["nodes"], 8)
        self.assertEqual(set(result["meshes"][0]["properties"]), set(PROPERTIES))
        stats = json.loads(meshes.get_mesh_statistics(1))["statistics"]
        self.assertEqual(set(stats), set(STATS))
        self.assertIs(type(stats["IsSelfIntersecting"]), int)
        self.assertEqual(self.mesh.calls, [("StatsCalc",)])

    def test_exact_enum_type_resolution_and_discovery(self):
        result = json.loads(meshes.get_mesh_enums())
        self.assertEqual(result, ENUMS)
        with self.assertRaises(MaxsurfValidationError):
            meshes.mesh_surface_edge_length("new", 1, 0.1, 1,
                                             mesh_type="msMTUnsupported")
        self.assertEqual(self.collection.calls, [])

    def test_edge_length_exact_call_and_created_identity(self):
        result = json.loads(meshes.mesh_surface_edge_length(
            "new", 2, 0.1, 2.0, "msMTQuadDom", False))
        self.assertEqual(self.collection.calls, [
            ("MeshFromSurfaceEdgeLength", "new", 2, 0.1, 2.0, 3, False)])
        self.assertEqual(result["mesh"]["name"], "new")
        self.assertEqual(result["mesh"]["index"], 3)

    def test_parametric_exact_native_call(self):
        meshes.mesh_surface_parametric("new", 1, 12, 6,
                                       "msMDLTSecondDiagonal", 40, "msMTTriRA")
        self.assertEqual(self.collection.calls, [
            ("MeshFromSurfaceParametric", "new", 1, 12, 6, 2, 40.0, 2)])

    def test_creation_validation_before_mutation(self):
        cases = [
            lambda: meshes.mesh_surface_edge_length("new", True, 0.1, 1),
            lambda: meshes.mesh_surface_edge_length("new", 1.5, 0.1, 1),
            lambda: meshes.mesh_surface_edge_length("new", 3, 0.1, 1),
            lambda: meshes.mesh_surface_edge_length("new", 1, True, 1),
            lambda: meshes.mesh_surface_edge_length("new", 1, 2, 1),
            lambda: meshes.mesh_surface_edge_length("new", 1, 0.1, math.inf),
            lambda: meshes.mesh_surface_edge_length("new", 1, 0.1, 1, preserve_feature_lines=1),
            lambda: meshes.mesh_surface_edge_length(" FIRST ", 1, 0.1, 1),
            lambda: meshes.mesh_surface_parametric("new", 1, 1.5, 2, "msMDLTFirstDiagonal"),
            lambda: meshes.mesh_surface_parametric("new", 1, 1, 2, "msMDLTFirstDiagonal", 101),
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(MaxsurfValidationError):
                case()
        self.assertEqual(self.collection.calls, [])

    def test_silent_creation_refusal_is_typed_error(self):
        self.collection.ignore_creation = True
        with self.assertRaises(MaxsurfCOMError):
            meshes.mesh_surface_edge_length("new", 1, 0.1, 1)

    def test_empty_created_mesh_is_typed_error(self):
        original = self.collection.add
        def empty(name):
            mesh = original(name)
            mesh.NumTriangles = 0
            return mesh
        with mock.patch.object(self.collection, "add", side_effect=empty):
            with self.assertRaises(MaxsurfCOMError):
                meshes.mesh_surface_parametric("new", 1, 4, 3, "msMDLTFirstDiagonal")

    def test_properties_readback_and_all_inputs_prevalidated(self):
        meshes.set_mesh_properties(1, '{"name":"renamed","use":"msSUStructure",'
                                   '"visible":false,"thickness":0.2}')
        self.assertEqual((self.mesh.Name, self.mesh.Use, self.mesh.Visible,
                          self.mesh.Thickness), ("renamed", 2, False, 0.2))
        for raw in ('{"visible":true,"assembly_name":"x"}',
                    '{"visible":true,"transparency":101}', '{}',
                    '{"visible":true,"locked":1}', '{"thickness":NaN}'):
            with self.subTest(raw=raw), self.assertRaises(MaxsurfValidationError):
                meshes.set_mesh_properties(1, raw)
        self.assertFalse(self.mesh.Visible)

    def test_silent_property_refusal_is_typed_error_with_partial_state(self):
        self.mesh.ignored.add("Name")
        with self.assertRaises(MaxsurfCOMError) as caught:
            meshes.set_mesh_properties(1, '{"name":"refused"}')
        self.assertTrue(caught.exception.details["partial_state_possible"])

    def test_move_duplicates_exact_count_and_explicit_coordinate_boundary(self):
        result = json.loads(meshes.move_mesh(1, 2, 3, 4, True, 3))
        self.assertIn(("Move", 2.0, 3.0, 4.0, True, 3), self.mesh.calls)
        self.assertEqual(self.collection.Count, 5)
        self.assertEqual(len(result["after"]), 3)
        self.assertNotIn("verified", result)
        self.assertEqual(result["coordinate_verification"], "unavailable_native_accessors")

    def test_duplicate_silent_refusal_is_not_success(self):
        with mock.patch.object(self.mesh, "Move", return_value=None):
            with self.assertRaises(MaxsurfCOMError):
                meshes.move_mesh(1, 1, 0, 0, True, 2)

    def test_rotate_exact_arguments_and_flip_native_enum(self):
        meshes.rotate_mesh(1, 10, 20, 30, 1, 2, 3)
        self.assertIn(("Rotate", 10.0, 20.0, 30.0, 1.0, 2.0, 3.0, False, 0),
                      self.mesh.calls)
        meshes.flip_mesh(1, "msDTTransverse", 2, True)
        self.assertIn(("Flip", 2, 2.0, True), self.mesh.calls)
        self.assertEqual(self.collection.Count, 3)

    def test_rescale_aggregate_geometry_and_silent_refusal(self):
        result = json.loads(meshes.scale_mesh(1, 2, 2, 2))
        self.assertEqual(result["after"][0]["statistics"]["Area"], 24)
        self.assertEqual(result["after"][0]["statistics"]["SignedVolume"], 8)
        self.mesh.ignored.add("ReScale")
        with self.assertRaises(MaxsurfCOMError):
            meshes.scale_mesh(1, 3, 3, 3)

    def test_normal_inversion_verifies_closed_signed_volume(self):
        meshes.invert_mesh_normals(1)
        self.assertEqual(self.mesh.stats["SignedVolume"], -1)
        self.mesh.ignored.add("InvertOutsideArrowDirection")
        with self.assertRaises(MaxsurfCOMError):
            meshes.invert_mesh_normals(1)

    def test_transform_invalid_inputs_do_not_write(self):
        cases = [
            lambda: meshes.move_mesh(True, 1, 0, 0),
            lambda: meshes.move_mesh(1, math.nan, 0, 0),
            lambda: meshes.move_mesh(1, 1, 0, 0, duplicate=1),
            lambda: meshes.move_mesh(1, 1, 0, 0, times=2),
            lambda: meshes.move_mesh(1, 1, 0, 0, True, False),
            lambda: meshes.rotate_mesh(1, True, 0, 0),
            lambda: meshes.scale_mesh(1, 0, 1, 1),
            lambda: meshes.flip_mesh(1, "invalid"),
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(MaxsurfValidationError):
                case()
        self.assertEqual(self.mesh.calls, [])

    def test_repair_resolves_native_enum_and_verifies_boundaries(self):
        self.mesh.stats.update(NumHoles=1, NumBorderEdges=3)
        meshes.repair_mesh(1, "msMRepairFillRefineFair")
        self.assertIn(("Repair", 3), self.mesh.calls)
        self.mesh.stats.update(NumHoles=1, NumBorderEdges=3)
        self.mesh.ignored.add("Repair")
        with self.assertRaises(MaxsurfCOMError):
            meshes.repair_mesh(1, "msMRepairFill")

    def test_clean_bitmask_and_degenerate_postcondition(self):
        self.mesh.stats["NumDegenerateFaces"] = 2
        result = json.loads(meshes.clean_mesh(1, compact_nodes=False,
                                              remove_skinny_triangles=False))
        self.assertEqual(result["clean_method"], 6)
        self.assertIn(("Clean", 1e-5, 1e-5, 6), self.mesh.calls)
        self.mesh.stats["NumDegenerateFaces"] = 2
        self.mesh.ignored.add("Clean")
        with self.assertRaises(MaxsurfCOMError):
            meshes.clean_mesh(1)

    def test_clean_and_repair_validation(self):
        with self.assertRaises(MaxsurfValidationError):
            meshes.clean_mesh(1, compact_nodes=1)
        with self.assertRaises(MaxsurfValidationError):
            meshes.clean_mesh(1, -1)
        with self.assertRaises(MaxsurfValidationError):
            meshes.clean_mesh(1, compact_nodes=False,
                               remove_degenerate_triangles=False,
                               remove_duplicate_triangles=False,
                               remove_skinny_triangles=False)
        with self.assertRaises(MaxsurfValidationError):
            meshes.repair_mesh(1, "not_native")
        self.assertEqual(self.mesh.calls, [])

    def test_join_preserves_sources_and_returns_new_mesh(self):
        result = json.loads(meshes.combine_meshes("joined", 1, 2, "join"))
        self.assertEqual(result["mesh"]["nodes"], 16)
        self.assertEqual(self.collection.Count, 3)
        self.assertEqual(self.collection.calls[-1],
                         ("Join", "joined", 1, 2, False, False, 1e-5, 1e-5))

    def test_union_deletes_sources_and_handles_shifted_indices(self):
        result = json.loads(meshes.combine_meshes("union", 1, 2, "union", True, True))
        self.assertEqual(self.collection.Count, 1)
        self.assertEqual(result["mesh"]["index"], 1)
        self.assertEqual(result["statistics"]["SignedVolume"], 2)
        self.assertEqual(self.collection.calls[-1],
                         ("BooleanUnion", "union", 1, 2, True, True, 1e-5, 1e-5))

    def test_intersection_native_call_and_volume_bound(self):
        result = json.loads(meshes.combine_meshes("intersection", 1, 2, "intersection"))
        self.assertEqual(result["statistics"]["SignedVolume"], 1)
        self.assertEqual(self.collection.calls[-1][0], "BooleanIntersection")

    def test_boolean_rejects_unsuitable_inputs_without_mutation(self):
        for key, value in (("NumConnectedComponents", 2), ("NumBorderEdges", 3),
                           ("NumDegenerateFaces", 1), ("NumHoles", 1),
                           ("IsSelfIntersecting", 1), ("SignedVolume", -1)):
            original = self.mesh.stats[key]
            self.mesh.stats[key] = value
            with self.subTest(key=key), self.assertRaises(MaxsurfValidationError):
                meshes.combine_meshes("union", 1, 2, "union")
            self.mesh.stats[key] = original
        self.assertEqual(self.collection.calls, [])

    def test_combine_validation_refuses_difference_and_duplicate_source(self):
        for args in (("difference", 1, 2), ("join", 1, 1), ("join", True, 2)):
            operation, ia, ib = args
            with self.subTest(args=args), self.assertRaises(MaxsurfValidationError):
                meshes.combine_meshes("new", ia, ib, operation)
        self.assertEqual(self.collection.calls, [])

    def test_native_combination_silent_refusal_and_failed_deletion(self):
        self.collection.ignore_creation = True
        with self.assertRaises(MaxsurfCOMError):
            meshes.combine_meshes("new", 1, 2, "join")
        self.collection.ignore_creation = False
        self.collection.bad_count = True
        with self.assertRaises(MaxsurfCOMError):
            meshes.combine_meshes("new", 1, 2, "join", True)

    def test_invalid_boolean_result_volume_is_typed_error(self):
        original = self.collection._combine
        def invalid(*args):
            original(*args)
            self.collection.items[-1].stats["SignedVolume"] = 100
        with mock.patch.object(self.collection, "_combine", side_effect=invalid):
            with self.assertRaises(MaxsurfCOMError):
                meshes.combine_meshes("new", 1, 2, "union")

    def test_statistics_reject_unknown_self_intersection_and_nonfinite(self):
        self.mesh.stats["IsSelfIntersecting"] = -1
        with self.assertRaises(MaxsurfCOMError):
            meshes.get_mesh_statistics(1)
        self.mesh.stats["IsSelfIntersecting"] = 0
        self.mesh.stats["Area"] = math.nan
        with self.assertRaises(MaxsurfCOMError):
            meshes.get_mesh_statistics(1)

    def test_delete_confirmation_and_identity_readback(self):
        with self.assertRaises(MaxsurfValidationError):
            meshes.delete_mesh(1)
        with self.assertRaises(MaxsurfValidationError):
            meshes.delete_mesh(1, 1)
        self.mesh.ignored.add("Delete")
        with self.assertRaises(MaxsurfCOMError):
            meshes.delete_mesh(1, True)
        self.mesh.ignored.clear()
        result = json.loads(meshes.delete_mesh(1, True))
        self.assertEqual(result["deleted_id"], 1)
        self.assertEqual(self.collection.Count, 1)

    def test_registration_keeps_guarded_typed_explicit_signatures(self):
        from mcp.server.fastmcp import FastMCP
        server = FastMCP("mesh-tests")
        names = meshes.register_tools(server)
        self.assertEqual(names, [tool.__name__ for tool in meshes.TOOLS])
        self.assertEqual(len(names), 15)
        self.assertTrue(all(tool.maxsurf_module == "modeler"
                            for tool in meshes.TOOLS))
        combine = server._tool_manager.get_tool("combine_meshes")
        schema = combine.parameters
        self.assertEqual(schema["properties"]["operation"]["enum"],
                         ["join", "union", "intersection"])

    def test_native_failure_is_normalized_by_real_guard(self):
        with mock.patch.object(self.mesh, "StatsCalc", side_effect=OSError("native failure")):
            with self.assertRaises(MaxsurfCOMError):
                meshes.get_mesh_statistics(1)
        self.assertEqual(self.records_for("get_mesh_statistics")[-1]["outcome"],
                         "failure")
