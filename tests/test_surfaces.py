"""Offline tests for surface transforms, properties and construction.

The fake surface carries a real control net so a transform can be verified the
way the tool verifies it: by reading the net back and comparing bounding boxes.
"""

from __future__ import annotations

import json
import types
import unittest
from typing import Any
from unittest import mock

import server
from maxsurf_mcp import surfaces as surf
from maxsurf_mcp.audit import Classification, Outcome
from maxsurf_mcp.errors import (
    MaxsurfCOMError,
    MaxsurfSafetyError,
    MaxsurfValidationError,
)

from .support import IsolatedEnvTestCase

#: Every enum family the module resolves at runtime, with the values the live
#: type library actually reports on this build.
_CONSTANTS = {
    "constants": {
        "msSTBSpline": 1, "msSTNURB": 2, "msSTDevelopable": 3, "msSTConic": 4,
        "msSUHull": 1, "msSUStructure": 2,
        "msSSDOutside": 1, "msSSDCentered": 2, "msSSDInside": 3,
        "msSSTAsymmetric": 1, "msSSTSymmetricalHalf": 2,
        "msSSTSymmetricalFull": 3,
        "msDTLongitudinal": 1, "msDTTransverse": 2, "msDTVertical": 3,
        "msEdgeNull": 0, "msEdgeTop": 1, "msEdgeLeft": 2, "msEdgeRight": 3,
        "msEdgeBottom": 4,
        "msBondC0": 1, "msBondC1approx": 2, "msBondC1exact": 3,
        "msSLDefault": 1, "msSLSimpleYacht": 2, "msSLBox": 8,
        "msSLCylinder6": 17,
    }
}

_DEFAULTS = {
    "Name": "surface", "Type": 1, "Use": 1, "SkinDirection": 1,
    "Symmetrical": False, "Visible": True, "Locked": False, "Split": False,
    "Color": 255, "Material": 0, "Transparency": 0, "Thickness": 0.0,
    "AssemblyName": "Hull", "Assembly": 1,
    "LongitudinalStiffness": 3, "TransverseStiffness": 3, "ID": 7,
}

_PROPS = set(_DEFAULTS)


class _FakeSurface:
    """A surface with a real 2x2 control net and settable properties."""

    def __init__(self, name: str = "surface", assembly: str = "Hull",
                 points: dict[tuple[int, int], tuple[float, float, float]]
                 | None = None, **overrides: Any):
        object.__setattr__(self, "_values", dict(_DEFAULTS))
        self._values["Name"] = name
        self._values["AssemblyName"] = assembly
        self._values.update(overrides)
        object.__setattr__(self, "points", dict(points or {
            (1, 1): (0.0, 0.0, 0.0),
            (1, 2): (10.0, 0.0, 0.0),
            (2, 1): (0.0, 2.0, 3.0),
            (2, 2): (10.0, 2.0, 3.0),
        }))
        object.__setattr__(self, "calls", [])
        object.__setattr__(self, "readonly", set())
        object.__setattr__(self, "owner", None)
        object.__setattr__(self, "bonds", {})

    def BondedToWhichSurface(self, edge):
        return self.bonds.get(edge, ("Unbonded", 0))[0]

    def BondedToWhichEdge(self, edge):
        return self.bonds.get(edge, ("Unbonded", 0))[1]

    # -- properties --------------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        values = object.__getattribute__(self, "_values")
        if name in values:
            return values[name]
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _PROPS:
            if name in self.readonly:
                raise OSError(f"{name} is read-only")
            self._values[name] = value
            return
        object.__setattr__(self, name, value)

    # -- control net -------------------------------------------------------
    def ControlPointLimits(self):  # noqa: N802 - COM naming
        return (max(r for r, _c in self.points),
                max(c for _r, c in self.points))

    def GetControlPoint(self, row, column, x, y, z):  # noqa: N802
        return self.points[(row, column)]

    def SetControlPoint(self, row, column, x, y, z):  # noqa: N802
        self.points[(row, column)] = (x, y, z)

    # -- transforms --------------------------------------------------------
    def _map(self, fn):
        self.points = {k: fn(v) for k, v in self.points.items()}

    def Move(self, x, y, z, dup, times):  # noqa: N802
        self.calls.append(("Move", x, y, z, dup, times))
        if dup and self.owner is not None:
            for n in range(1, times + 1):
                copy = _FakeSurface(
                    f"{self._values['Name']} copy {n}",
                    points={k: (v[0] + x * n, v[1] + y * n, v[2] + z * n)
                            for k, v in self.points.items()})
                self.owner.append(copy)
        else:
            self._map(lambda v: (v[0] + x, v[1] + y, v[2] + z))
        return True

    def Rotate(self, roll, pitch, yaw, lc, tc, vc, dup, times):  # noqa: N802
        self.calls.append(("Rotate", roll, pitch, yaw, lc, tc, vc, dup, times))
        if dup and self.owner is not None:
            self.owner.append(_FakeSurface("rotated copy",
                                           points=dict(self.points)))
        else:
            # A 180 deg yaw about the vertical centre is enough to move points.
            self._map(lambda v: (2 * lc - v[0], 2 * tc - v[1], v[2]))
        return True

    def ReScale(self, sx, sy, sz, cx, cy, cz):  # noqa: N802
        self.calls.append(("ReScale", sx, sy, sz, cx, cy, cz))
        self._map(lambda v: (cx + (v[0] - cx) * sx,
                             cy + (v[1] - cy) * sy,
                             cz + (v[2] - cz) * sz))
        return True

    def Flip(self, direction, location, duplicate):  # noqa: N802
        self.calls.append(("Flip", direction, location, duplicate))
        axis = {1: 0, 2: 1, 3: 2}[direction]

        def mirror(v):
            out = list(v)
            out[axis] = 2 * location - out[axis]
            return tuple(out)

        if duplicate and self.owner is not None:
            self.owner.append(_FakeSurface(
                "mirrored", points={k: mirror(v)
                                    for k, v in self.points.items()}))
        else:
            self._map(mirror)
        return True

    def InvertOutsideArrowDirection(self):  # noqa: N802
        self.calls.append(("InvertOutsideArrowDirection",))
        return True


class _FakeSurfaces:
    def __init__(self, items: list[_FakeSurface] | None = None):
        self.items = list(items or [])
        self.bond_calls: list[tuple] = []
        self.unbond_calls: list[tuple] = []
        self.quad_calls: list[tuple] = []
        self.quad_delta = 1
        for item in self.items:
            item.owner = self.items

    @property
    def Count(self) -> int:  # noqa: N802
        return len(self.items)

    def __call__(self, index: int) -> _FakeSurface:
        return self.items[index - 1]

    def Bond(self, i1, e1, i2, e2, continuity, insert):  # noqa: N802
        self.bond_calls.append((i1, e1, i2, e2, continuity, insert))
        self.items[i1 - 1].bonds[e1] = (self.items[i2 - 1].Name, e2)
        self.items[i2 - 1].bonds[e2] = (self.items[i1 - 1].Name, e1)
        if insert:
            surface = _FakeSurface("connector")
            surface.owner = self.items
            self.items.append(surface)

    def Unbond(self, index, edge):  # noqa: N802
        self.unbond_calls.append((index, edge))

    def AddQuadrilateralFromCorners(self, name, symmetry, *coords):  # noqa: N802
        self.quad_calls.append((name, symmetry, coords))
        for _ in range(self.quad_delta):
            surface = _FakeSurface(name)
            surface.owner = self.items
            self.items.append(surface)


class SurfaceTestCase(IsolatedEnvTestCase):
    def patch(self, collection: _FakeSurfaces) -> _FakeSurfaces:
        app = types.SimpleNamespace(
            Design=types.SimpleNamespace(Surfaces=collection))
        connect = mock.patch.object(server.com, "connect", return_value=app)
        constants = mock.patch.object(
            server.comconstants, "discover", return_value=_CONSTANTS)
        connect.start()
        constants.start()
        self.addCleanup(connect.stop)
        self.addCleanup(constants.stop)
        return collection

    def one(self, **kwargs: Any) -> tuple[_FakeSurfaces, _FakeSurface]:
        surface = _FakeSurface(**kwargs)
        collection = self.patch(_FakeSurfaces([surface]))
        return collection, surface


class TransformTests(SurfaceTestCase):
    def test_move_translates_and_proves_it_with_the_control_net(self):
        _c, surface = self.one()
        payload = json.loads(server.move_surface(1, 5.0, 0.0, -1.0))
        self.assertEqual(surface.calls[0], ("Move", 5.0, 0.0, -1.0, False, 1))
        delta = payload["extent_delta"]
        self.assertAlmostEqual(delta["longitudinal"]["centroid"], 5.0)
        self.assertAlmostEqual(delta["vertical"]["centroid"], -1.0)
        self.assertAlmostEqual(delta["offset"]["centroid"], 0.0)
        # A pure translation must not change any span.
        for axis in ("longitudinal", "offset", "vertical"):
            self.assertAlmostEqual(delta[axis]["span_before"],
                                   delta[axis]["span_after"])

    def test_move_with_duplicate_reports_the_created_indices(self):
        collection, _surface = self.one()
        payload = json.loads(
            server.move_surface(1, 0.0, 4.0, 0.0, duplicate=True, times=2))
        self.assertEqual(collection.Count, 3)
        self.assertEqual(payload["created_indices"], [2, 3])
        self.assertEqual(payload["count_before"], 1)
        self.assertTrue(payload["duplicate"])

    def test_a_transform_that_unexpectedly_multiplies_the_model_raises(self):
        class Sneaky(_FakeSurface):
            def Move(self, x, y, z, dup, times):  # noqa: N802
                self.owner.append(_FakeSurface("uninvited"))
                return True

        surface = Sneaky()
        collection = self.patch(_FakeSurfaces([surface]))
        with self.assertRaises(MaxsurfCOMError) as caught:
            server.move_surface(1, 1.0, 0.0, 0.0)
        self.assertIn("not asked to duplicate", str(caught.exception))
        self.assertEqual(collection.Count, 2)

    def test_a_duplicate_that_created_nothing_raises(self):
        class Lazy(_FakeSurface):
            def Move(self, x, y, z, dup, times):  # noqa: N802
                return True

        self.patch(_FakeSurfaces([Lazy()]))
        with self.assertRaises(MaxsurfCOMError) as caught:
            server.move_surface(1, 1.0, 0.0, 0.0, duplicate=True)
        self.assertIn("count did not grow", str(caught.exception))

    def test_rescale_changes_spans_by_the_scale_factors(self):
        _c, _s = self.one()
        payload = json.loads(server.rescale_surface(1, 2.0, 1.0, 1.0))
        delta = payload["extent_delta"]["longitudinal"]
        self.assertAlmostEqual(delta["span_after"], delta["span_before"] * 2.0)

    def test_a_zero_scale_factor_is_refused_before_any_call(self):
        _c, surface = self.one()
        for axis in range(3):
            factors = [1.0, 1.0, 1.0]
            factors[axis] = 0.0
            with self.subTest(axis=axis):
                with self.assertRaises(MaxsurfValidationError):
                    server.rescale_surface(1, *factors)
        self.assertEqual(surface.calls, [])

    def test_flip_resolves_the_direction_enum_by_name(self):
        _c, surface = self.one()
        payload = json.loads(
            server.flip_surface(1, "msdttransverse", 0.0))
        self.assertEqual(surface.calls[0], ("Flip", 2, 0.0, False))
        self.assertEqual(payload["arguments"]["direction"]["enum_name"],
                         "msDTTransverse")
        # Mirroring about y=0 must invert the offset extent.
        self.assertLess(payload["extent_after"]["offset"]["min"], 0.0)

    def test_flip_with_duplicate_is_how_a_half_hull_is_mirrored(self):
        collection, _surface = self.one()
        payload = json.loads(
            server.flip_surface(1, "msDTTransverse", 0.0, duplicate=True))
        self.assertEqual(collection.Count, 2)
        self.assertEqual(payload["created_indices"], [2])

    def test_an_unknown_direction_lists_the_valid_members(self):
        _c, surface = self.one()
        with self.assertRaises(MaxsurfValidationError) as caught:
            server.flip_surface(1, "msDTSideways", 0.0)
        self.assertIn("msDTLongitudinal", caught.exception.details["available"])
        self.assertEqual(surface.calls, [])

    def test_invert_normal_calls_exactly_once_and_moves_nothing(self):
        _c, surface = self.one()
        payload = json.loads(server.invert_surface_normal(1))
        self.assertEqual(surface.calls, [("InvertOutsideArrowDirection",)])
        for axis in ("longitudinal", "offset", "vertical"):
            self.assertAlmostEqual(
                payload["extent_delta"][axis]["centroid"], 0.0)

    def test_rotate_passes_every_argument_in_the_documented_order(self):
        _c, surface = self.one()
        server.rotate_surface(1, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
        self.assertEqual(surface.calls[0],
                         ("Rotate", 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, False, 1))

    def test_non_finite_and_bad_counts_are_refused(self):
        _c, surface = self.one()
        for bad in (float("nan"), float("inf"), True, "1"):
            with self.subTest(value=bad):
                with self.assertRaises(MaxsurfValidationError):
                    server.move_surface(1, bad, 0.0, 0.0)
        for bad in (0, -1, True, 1.5):
            with self.subTest(times=bad):
                with self.assertRaises(MaxsurfValidationError):
                    server.move_surface(1, 1.0, 0.0, 0.0, duplicate=True,
                                        times=bad)
        self.assertEqual(surface.calls, [])

    def test_an_out_of_range_index_is_refused(self):
        self.one()
        with self.assertRaises(MaxsurfValidationError):
            server.move_surface(2, 1.0, 0.0, 0.0)

    def test_a_transform_is_audited_with_intent_first(self):
        self.one()
        server.move_surface(1, 1.0, 0.0, 0.0)
        records = self.records_for("move_surface")
        self.assertEqual([r["stage"] for r in records], ["intent", "complete"])
        self.assertEqual(records[0]["classification"],
                         Classification.WRITE_GEOMETRY)
        self.assertEqual(records[-1]["outcome"], Outcome.SUCCESS)


class PropertyTests(SurfaceTestCase):
    def test_every_proven_property_is_reported_with_enum_names(self):
        self.one(Type=2, Use=2, SkinDirection=3)
        payload = json.loads(server.get_surface_properties(1))
        props = payload["properties"]
        self.assertEqual(set(props), set(surf.SURFACE_PROPERTIES))
        self.assertEqual(props["type"]["enum_name"], "msSTNURB")
        self.assertEqual(props["use"]["enum_name"], "msSUStructure")
        self.assertEqual(props["skin_direction"]["enum_name"], "msSSDInside")
        self.assertIs(props["visible"]["value"], True)
        self.assertEqual(props["assembly_name"]["value"], "Hull")

    def test_an_unreadable_property_is_not_reported_as_a_default(self):
        _c, surface = self.one()
        del surface._values["Thickness"]
        payload = json.loads(server.get_surface_properties(1))
        entry = payload["properties"]["thickness"]
        self.assertIsNone(entry["value"])
        self.assertEqual(entry["status"], "absent")

    def test_setting_properties_verifies_each_by_reading_it_back(self):
        _c, surface = self.one()
        payload = json.loads(server.set_surface_properties(
            1, json.dumps({"symmetrical": True, "name": "Hull port"})))
        self.assertTrue(surface.Symmetrical)
        self.assertEqual(surface.Name, "Hull port")
        self.assertTrue(all(r["stored"] for r in payload["applied"]))
        self.assertEqual(payload["not_stored"], [])

    def test_enum_properties_take_member_names_not_numbers(self):
        _c, surface = self.one()
        server.set_surface_properties(1, json.dumps({"use": "msSUStructure"}))
        self.assertEqual(surface.Use, 2)
        with self.assertRaises(MaxsurfValidationError):
            server.set_surface_properties(1, json.dumps({"use": 2}))

    def test_a_refused_write_raises_an_error(self):
        from maxsurf_mcp.errors import MaxsurfCOMError
        _c, surface = self.one()
        surface.readonly.add("Visible")
        with self.assertRaises(MaxsurfCOMError):
            server.set_surface_properties(1, json.dumps({"visible": False}))

    def test_unknown_and_read_only_properties_are_refused(self):
        _c, surface = self.one()
        with self.assertRaises(MaxsurfValidationError) as caught:
            server.set_surface_properties(1, json.dumps({"colour": 1}))
        self.assertEqual(caught.exception.details["unknown"], ["colour"])
        with self.assertRaises(MaxsurfValidationError) as caught:
            server.set_surface_properties(1, json.dumps({"id": 4}))
        self.assertEqual(caught.exception.details["read_only"], ["id"])
        self.assertEqual(surface._values["Name"], "surface")

    def test_type_mismatches_are_refused(self):
        self.one()
        for payload in ({"visible": "yes"}, {"thickness": "thick"},
                        {"name": ""}, {"material": 1.5}):
            with self.subTest(payload=payload):
                with self.assertRaises(MaxsurfValidationError):
                    server.set_surface_properties(1, json.dumps(payload))

    def test_a_malformed_payload_is_refused(self):
        self.one()
        for bad in ("not json", "[]", "{}"):
            with self.subTest(payload=bad):
                with self.assertRaises(MaxsurfValidationError):
                    server.set_surface_properties(1, bad)


class AssemblyVisibilityTests(SurfaceTestCase):
    def collection(self) -> _FakeSurfaces:
        return self.patch(_FakeSurfaces([
            _FakeSurface("hull aft", "Hull"),
            _FakeSurface("hull fwd", "Hull"),
            _FakeSurface("deck", "Deck"),
        ]))

    def test_only_the_named_assembly_is_touched(self):
        collection = self.collection()
        payload = json.loads(server.set_assembly_visibility("Deck", False))
        self.assertEqual([m["index"] for m in payload["matched"]], [3])
        self.assertTrue(collection(1).Visible)
        self.assertTrue(collection(2).Visible)
        self.assertFalse(collection(3).Visible)

    def test_every_surface_in_the_assembly_is_reported(self):
        self.collection()
        payload = json.loads(server.set_assembly_visibility("Hull", False))
        self.assertEqual(len(payload["matched"]), 2)
        self.assertTrue(all(m["stored"] for m in payload["matched"]))
        self.assertEqual(payload["assemblies_seen"], {"Hull": 2, "Deck": 1})

    def test_an_unmatched_assembly_writes_nothing_and_lists_the_names(self):
        collection = self.collection()
        with self.assertRaises(MaxsurfValidationError) as caught:
            server.set_assembly_visibility("Superstructure", False)
        self.assertEqual(caught.exception.details["available_assemblies"],
                         ["Deck", "Hull"])
        self.assertTrue(all(collection(i).Visible for i in (1, 2, 3)))

    def test_the_match_is_case_sensitive(self):
        self.collection()
        with self.assertRaises(MaxsurfValidationError):
            server.set_assembly_visibility("hull", False)

    def test_a_refused_write_is_reported_per_surface(self):
        collection = self.collection()
        collection(1).readonly.add("Visible")
        payload = json.loads(server.set_assembly_visibility("Hull", False))
        self.assertEqual(payload["not_stored"], [1])
        self.assertIsNotNone(payload["matched"][0]["write_error"])
        self.assertTrue(payload["matched"][1]["stored"])


class ConstructionTests(SurfaceTestCase):
    CORNERS = {
        "bottom_right": [0.0, 1.0, 0.0], "bottom_left": [0.0, -1.0, 0.0],
        "top_left": [10.0, -1.0, 5.0], "top_right": [10.0, 1.0, 5.0],
    }

    def test_the_library_list_comes_from_the_type_library(self):
        self.patch(_FakeSurfaces())
        payload = json.loads(server.list_surface_library())
        names = [s["enum_name"] for s in payload["shapes"]]
        self.assertIn("msSLSimpleYacht", names)
        self.assertIn("msSLCylinder6", names)
        # Sorted by value, not by name.
        self.assertEqual([s["value"] for s in payload["shapes"]],
                         sorted(s["value"] for s in payload["shapes"]))

    def test_a_quadrilateral_is_created_with_corners_in_the_proven_order(self):
        collection = self.patch(_FakeSurfaces())
        payload = json.loads(server.add_quadrilateral_surface(
            "transom", "msSSTSymmetricalHalf", json.dumps(self.CORNERS)))
        name, symmetry, coords = collection.quad_calls[0]
        self.assertEqual(name, "transom")
        self.assertEqual(symmetry, 2)
        self.assertEqual(list(coords), [
            0.0, 1.0, 0.0, 0.0, -1.0, 0.0, 10.0, -1.0, 5.0, 10.0, 1.0, 5.0])
        self.assertEqual(payload["created_index"], 1)

    def test_a_missing_corner_is_refused_before_any_call(self):
        collection = self.patch(_FakeSurfaces())
        corners = dict(self.CORNERS)
        del corners["top_left"]
        with self.assertRaises(MaxsurfValidationError) as caught:
            server.add_quadrilateral_surface("x", "msSSTAsymmetric",
                                             json.dumps(corners))
        self.assertEqual(caught.exception.details["missing"], ["top_left"])
        self.assertEqual(collection.quad_calls, [])

    def test_a_malformed_corner_is_refused(self):
        collection = self.patch(_FakeSurfaces())
        corners = dict(self.CORNERS, top_left=[1.0, 2.0])
        with self.assertRaises(MaxsurfValidationError):
            server.add_quadrilateral_surface("x", "msSSTAsymmetric",
                                             json.dumps(corners))
        self.assertEqual(collection.quad_calls, [])

    def test_an_unexpected_count_delta_raises(self):
        collection = self.patch(_FakeSurfaces())
        collection.quad_delta = 2
        with self.assertRaises(MaxsurfCOMError) as caught:
            server.add_quadrilateral_surface("x", "msSSTAsymmetric",
                                             json.dumps(self.CORNERS))
        self.assertIn("exactly one", str(caught.exception))

    def test_bonding_resolves_both_edges_and_the_continuity(self):
        collection = self.patch(
            _FakeSurfaces([_FakeSurface("a"), _FakeSurface("b")]))
        payload = json.loads(server.bond_surfaces(
            1, "msEdgeRight", 2, "msEdgeLeft", "msBondC1exact"))
        self.assertEqual(collection.bond_calls[0], (1, 3, 2, 2, 3, False))
        self.assertEqual(payload["continuity"]["enum_name"], "msBondC1exact")
        self.assertEqual(payload["surfaces_created"], 0)

    def test_bonding_with_a_connecting_surface_reports_the_new_count(self):
        collection = self.patch(
            _FakeSurfaces([_FakeSurface("a"), _FakeSurface("b")]))
        payload = json.loads(server.bond_surfaces(
            1, "msEdgeRight", 2, "msEdgeLeft", "msBondC0",
            insert_connecting_surface=True))
        self.assertEqual(collection.Count, 3)
        self.assertEqual(payload["surfaces_created"], 1)

    def test_bonding_an_edge_to_itself_is_refused(self):
        collection = self.patch(_FakeSurfaces([_FakeSurface("a")]))
        with self.assertRaises(MaxsurfSafetyError):
            server.bond_surfaces(1, "msEdgeTop", 1, "msEdgeTop")
        self.assertEqual(collection.bond_calls, [])

    def test_an_unknown_edge_is_refused_with_the_valid_members(self):
        collection = self.patch(
            _FakeSurfaces([_FakeSurface("a"), _FakeSurface("b")]))
        with self.assertRaises(MaxsurfValidationError) as caught:
            server.bond_surfaces(1, "msEdgeSide", 2, "msEdgeLeft")
        self.assertIn("msEdgeTop", caught.exception.details["available"])
        self.assertEqual(collection.bond_calls, [])

    def test_unbond_passes_the_resolved_edge(self):
        collection = self.patch(_FakeSurfaces([_FakeSurface("a")]))
        payload = json.loads(server.unbond_surface(1, "msEdgeBottom"))
        self.assertEqual(collection.unbond_calls, [(1, 4)])
        self.assertEqual(payload["edge"]["enum_name"], "msEdgeBottom")


class RegistrationTests(unittest.TestCase):
    def test_the_tools_declare_their_classification(self):
        tools = {t.name: t for t in server.mcp._tool_manager.list_tools()}
        expected = {
            "move_surface": "WRITE.", "rotate_surface": "WRITE.",
            "rescale_surface": "WRITE.", "flip_surface": "WRITE.",
            "invert_surface_normal": "WRITE.",
            "add_quadrilateral_surface": "WRITE.",
            "bond_surfaces": "WRITE.", "unbond_surface": "WRITE.",
            "get_surface_properties": "READ-ONLY.",
            "list_surface_library": "READ-ONLY.",
            "set_surface_properties": "WRITE_STATE.",
            "set_assembly_visibility": "WRITE_STATE.",
        }
        for name, prefix in expected.items():
            with self.subTest(tool=name):
                self.assertIn(name, tools)
                self.assertTrue(tools[name].description.startswith(prefix))

    def test_split_is_a_property_and_not_a_transform(self):
        # The help calls it "whether or not the surface is split in the body
        # plan view": a drawing setting, not a geometry operation.
        tools = {t.name for t in server.mcp._tool_manager.list_tools()}
        self.assertNotIn("split_surface", tools)
        self.assertIn("split", surf.SURFACE_PROPERTIES)
        self.assertEqual(surf.SURFACE_PROPERTIES["split"][1], "bool")


if __name__ == "__main__":
    unittest.main()
