from __future__ import annotations

import json
import types
import unittest
from unittest import mock

import server
from maxsurf_mcp.audit import Classification, Outcome
from maxsurf_mcp.errors import (
    MaxsurfCOMError,
    MaxsurfSafetyError,
    MaxsurfValidationError,
)

from .support import IsolatedEnvTestCase


_CONSTANTS = {
    "constants": {
        "msSLDefault": 1,
        "msSLSimpleYacht": 2,
        "msSTBSpline": 1,
        "msSTNURB": 2,
        "msSUHull": 1,
        "msSUStructure": 2,
    }
}


class _FakeSurface:
    def __init__(self, name: str, points: dict[tuple[int, int], tuple[float, ...]]):
        self.Name = name
        self.Type = 1
        self.Use = 1
        self.Symmetrical = True
        self.Visible = True
        self.points = dict(points)
        self.set_calls: list[tuple[object, ...]] = []
        self.get_calls: list[tuple[object, ...]] = []
        self.delete_calls = 0
        self.owner: _FakeSurfaces | None = None
        self.fail_set_at: tuple[int, int] | None = None

    def ControlPointLimits(self):
        rows = max(row for row, _column in self.points)
        columns = max(column for _row, column in self.points)
        return rows, columns

    def GetControlPoint(self, row, column, x_value, y_value, z_value):
        self.get_calls.append((row, column, x_value, y_value, z_value))
        return self.points[(row, column)]

    def SetControlPoint(self, row, column, x_value, y_value, z_value):
        if self.fail_set_at == (row, column):
            raise OSError("Maxsurf refused the point")
        self.set_calls.append((row, column, x_value, y_value, z_value))
        self.points[(row, column)] = (x_value, y_value, z_value)

    def Delete(self):
        self.delete_calls += 1
        assert self.owner is not None
        self.owner.items.remove(self)


class _FakeSurfaces:
    def __init__(self, items=(), add_delta: int = 1):
        self.items = list(items)
        self.add_calls: list[int] = []
        self.add_delta = add_delta
        for item in self.items:
            item.owner = self

    @property
    def Count(self):
        return len(self.items)

    def __call__(self, index):
        return self.items[index - 1]

    def Add(self, shape):
        self.add_calls.append(shape)
        for _ in range(self.add_delta):
            surface = _FakeSurface(
                "Simple yacht",
                {
                    (1, 1): (0.0, 0.0, 0.0),
                    (1, 2): (1.0, 0.0, 0.0),
                    (2, 1): (0.0, 1.0, 1.0),
                    (2, 2): (1.0, 1.0, 1.0),
                },
            )
            surface.owner = self
            self.items.append(surface)


def _application(surfaces: _FakeSurfaces):
    return types.SimpleNamespace(
        Design=types.SimpleNamespace(Surfaces=surfaces)
    )


def _rectangle(name: str = "test") -> _FakeSurface:
    return _FakeSurface(
        name,
        {
            (1, 1): (0.0, 2.0, 0.0),
            (1, 2): (10.0, 2.0, 0.0),
            (2, 1): (0.0, 2.0, 2.0),
            (2, 2): (10.0, 2.0, 2.0),
        },
    )


class GeometryToolTestCase(IsolatedEnvTestCase):
    def patch(self, surfaces):
        connect = mock.patch.object(
            server.com, "connect", return_value=_application(surfaces)
        )
        constants = mock.patch.object(
            server.comconstants, "discover", return_value=_CONSTANTS
        )
        connect.start()
        constants.start()
        self.addCleanup(connect.stop)
        self.addCleanup(constants.stop)


class ReadToolTests(GeometryToolTestCase):
    def test_surface_summary_resolves_type_and_use_enum_names(self):
        self.patch(_FakeSurfaces([_rectangle("summary")]))
        payload = json.loads(server.surface_summary())

        self.assertEqual(payload["index"], 1)
        self.assertEqual(payload["name"], "summary")
        self.assertEqual(
            payload["type"], {"value": 1, "enum_name": "msSTBSpline"}
        )
        self.assertEqual(payload["use"], {"value": 1, "enum_name": "msSUHull"})
        self.assertTrue(payload["symmetrical"])
        self.assertTrue(payload["visible"])
        self.assertEqual((payload["rows"], payload["columns"]), (2, 2))

    def test_get_control_net_uses_get_control_point_for_every_point(self):
        surface = _rectangle()
        self.patch(_FakeSurfaces([surface]))
        payload = json.loads(server.get_control_net())

        self.assertEqual((payload["rows"], payload["columns"]), (2, 2))
        self.assertEqual(
            payload["points"],
            [
                {"row": 1, "column": 1, "longitudinal": 0.0,
                 "offset": 2.0, "vertical": 0.0},
                {"row": 1, "column": 2, "longitudinal": 10.0,
                 "offset": 2.0, "vertical": 0.0},
                {"row": 2, "column": 1, "longitudinal": 0.0,
                 "offset": 2.0, "vertical": 2.0},
                {"row": 2, "column": 2, "longitudinal": 10.0,
                 "offset": 2.0, "vertical": 2.0},
            ],
        )
        self.assertEqual(len(surface.get_calls), 4)
        self.assertEqual(surface.set_calls, [])

    def test_list_surfaces_reports_every_surface(self):
        self.patch(_FakeSurfaces([_rectangle("a"), _rectangle("b")]))
        payload = json.loads(server.list_surfaces())
        self.assertEqual([s["name"] for s in payload["surfaces"]], ["a", "b"])

    def test_an_out_of_range_index_raises_before_item_access(self):
        self.patch(_FakeSurfaces([_rectangle()]))
        with self.assertRaises(MaxsurfValidationError) as caught:
            server.get_control_net(index=2)
        self.assertIn("outside the valid range", str(caught.exception))

    def test_a_non_integer_index_raises(self):
        self.patch(_FakeSurfaces([_rectangle()]))
        for bad in (True, 1.5):
            with self.subTest(index=bad):
                with self.assertRaises(MaxsurfValidationError):
                    server.surface_summary(index=bad)


class SetControlPointTests(GeometryToolTestCase):
    def test_bounds_are_validated_before_the_single_write(self):
        surface = _rectangle()
        self.patch(_FakeSurfaces([surface]))

        with self.assertRaises(MaxsurfValidationError) as caught:
            server.set_control_point(1, 3, 2, 10.0, 3.0, 2.0)
        self.assertIn("outside the valid range", str(caught.exception))
        self.assertEqual(surface.set_calls, [])

        payload = json.loads(server.set_control_point(1, 2, 2, 10.0, 3.0, 2.0))
        self.assertEqual(surface.set_calls, [(2, 2, 10.0, 3.0, 2.0)])
        self.assertEqual(payload["before"]["offset"], 2.0)
        self.assertEqual(payload["after"]["offset"], 3.0)
        self.assertEqual(payload["set_control_point_calls"], 1)

    def test_the_write_is_audited_with_intent_first(self):
        self.patch(_FakeSurfaces([_rectangle()]))
        server.set_control_point(1, 2, 2, 10.0, 3.0, 2.0)
        records = self.records_for("set_control_point")
        self.assertEqual([r["stage"] for r in records], ["intent", "complete"])
        self.assertEqual(
            records[0]["classification"], Classification.WRITE_GEOMETRY
        )
        self.assertEqual(records[0]["arguments"]["row"], 2)
        self.assertEqual(records[-1]["outcome"], Outcome.SUCCESS)


class BulkControlPointTests(GeometryToolTestCase):
    def test_all_points_are_validated_before_any_write(self):
        surface = _rectangle()
        self.patch(_FakeSurfaces([surface]))
        points = json.dumps([
            {"r": 1, "c": 1, "x": 1.0},
            {"r": 9, "c": 9, "x": 2.0},
        ])
        with self.assertRaises(MaxsurfValidationError):
            server.set_control_points(1, points)
        self.assertEqual(surface.set_calls, [])

    def test_omitted_axes_keep_their_current_value(self):
        surface = _rectangle()
        self.patch(_FakeSurfaces([surface]))
        payload = json.loads(
            server.set_control_points(1, json.dumps([{"r": 1, "c": 1, "x": 5.0}]))
        )
        self.assertEqual(payload, {"requested": 1, "updated": 1})
        self.assertEqual(surface.points[(1, 1)], (5.0, 2.0, 0.0))

    def test_a_partial_failure_reports_the_true_applied_count(self):
        surface = _rectangle()
        surface.fail_set_at = (2, 1)
        self.patch(_FakeSurfaces([surface]))
        points = json.dumps([
            {"r": 1, "c": 1, "x": 1.0},
            {"r": 1, "c": 2, "x": 2.0},
            {"r": 2, "c": 1, "x": 3.0},
        ])
        with self.assertRaises(MaxsurfCOMError) as caught:
            server.set_control_points(1, points)
        details = caught.exception.details
        self.assertEqual(details["applied_before_failure"], 2)
        self.assertEqual(details["requested"], 3)
        self.assertEqual(details["failed_at"], {"row": 2, "column": 1})
        self.assertEqual(len(surface.set_calls), 2)

    def test_malformed_payloads_raise_validation_errors(self):
        self.patch(_FakeSurfaces([_rectangle()]))
        for payload in ("not json", '{"r": 1}', '[{"x": 1.0}]', '[3]'):
            with self.subTest(payload=payload):
                with self.assertRaises(MaxsurfValidationError):
                    server.set_control_points(1, payload)


class CreateLibrarySurfaceTests(GeometryToolTestCase):
    def test_the_enum_name_is_resolved_without_a_magic_number(self):
        surfaces = _FakeSurfaces([_rectangle("existing")])
        self.patch(surfaces)
        payload = json.loads(
            server.create_library_surface("msslsimpleyacht", "new hull")
        )
        self.assertEqual(surfaces.add_calls, [2])
        self.assertEqual(payload["shape"]["enum_name"], "msSLSimpleYacht")
        self.assertEqual(payload["created_index"], 2)
        self.assertEqual(payload["name"], "new hull")

    def test_an_unknown_enum_raises_and_lists_the_options(self):
        surfaces = _FakeSurfaces()
        self.patch(surfaces)
        with self.assertRaises(MaxsurfValidationError) as caught:
            server.create_library_surface("msSLNotARealShape")
        self.assertIn(
            "msSLSimpleYacht", caught.exception.details["available_shapes"]
        )
        self.assertEqual(surfaces.add_calls, [])

    def test_an_empty_shape_raises_without_touching_com(self):
        surfaces = _FakeSurfaces()
        self.patch(surfaces)
        with self.assertRaises(MaxsurfValidationError):
            server.create_library_surface("   ")
        self.assertEqual(surfaces.add_calls, [])

    def test_an_unexpected_count_delta_raises(self):
        surfaces = _FakeSurfaces([], add_delta=2)
        self.patch(surfaces)
        with self.assertRaises(MaxsurfCOMError) as caught:
            server.create_library_surface("msSLSimpleYacht")
        self.assertIn("exactly one", str(caught.exception))


class DeleteSurfaceTests(GeometryToolTestCase):
    def test_expected_name_is_a_required_parameter(self):
        tools = {t.name: t for t in server.mcp._tool_manager.list_tools()}
        schema = tools["delete_surface"].parameters
        self.assertIn("expected_name", schema["required"])
        self.assertIn("index", schema["required"])

    def test_there_is_no_index_only_delete_signature(self):
        surface = _rectangle("guarded")
        self.patch(_FakeSurfaces([surface]))
        with self.assertRaises(MaxsurfValidationError) as caught:
            server.delete_surface(1)
        self.assertIn("expected_name", str(caught.exception))
        self.assertEqual(surface.delete_calls, 0)

    def test_a_blank_expected_name_is_refused(self):
        surface = _rectangle("guarded")
        self.patch(_FakeSurfaces([surface]))
        for blank in ("", "   "):
            with self.subTest(name=blank):
                with self.assertRaises(MaxsurfValidationError):
                    server.delete_surface(1, blank)
        self.assertEqual(surface.delete_calls, 0)

    def test_a_mismatched_name_blocks_the_delete(self):
        surface = _rectangle("guarded")
        self.patch(_FakeSurfaces([surface]))
        with self.assertRaises(MaxsurfSafetyError) as caught:
            server.delete_surface(1, "different")
        self.assertIn("name guard failed", str(caught.exception))
        self.assertEqual(caught.exception.details["actual_name"], "guarded")
        self.assertEqual(surface.delete_calls, 0)

    def test_a_matching_name_deletes_and_verifies_the_count_delta(self):
        surface = _rectangle("guarded")
        self.patch(_FakeSurfaces([surface]))
        payload = json.loads(server.delete_surface(1, "guarded"))
        self.assertEqual(surface.delete_calls, 1)
        self.assertEqual(payload["count_before"], 1)
        self.assertEqual(payload["count_after"], 0)
        self.assertEqual(payload["deleted_name"], "guarded")

    def test_an_unexpected_count_delta_raises(self):
        class StubbornSurface(_FakeSurface):
            def Delete(self):
                self.delete_calls += 1

        surface = StubbornSurface("guarded", {(1, 1): (0.0, 0.0, 0.0)})
        self.patch(_FakeSurfaces([surface]))
        with self.assertRaises(MaxsurfCOMError) as caught:
            server.delete_surface(1, "guarded")
        self.assertIn("exactly one", str(caught.exception))

    def test_a_blocked_delete_is_audited(self):
        self.patch(_FakeSurfaces([_rectangle("guarded")]))
        with self.assertRaises(MaxsurfSafetyError):
            server.delete_surface(1, "different")
        records = self.records_for("delete_surface")
        self.assertEqual(records[-1]["outcome"], Outcome.FAILURE)
        self.assertEqual(records[-1]["error"]["code"], "safety_error")


class GeometryToolRegistrationTests(unittest.TestCase):
    def test_tool_descriptions_declare_mutability(self):
        tools = {
            tool.name: tool
            for tool in server.mcp._tool_manager.list_tools()
        }
        expected = {
            "surface_summary": "READ-ONLY.",
            "get_control_net": "READ-ONLY.",
            "list_surfaces": "READ-ONLY.",
            "set_control_point": "WRITE.",
            "create_library_surface": "WRITE.",
            "delete_surface": "WRITE.",
            "set_control_points": "WRITE.",
            "open_design": "WRITE_STATE.",
            "save_design": "WRITE_FILE.",
        }
        for name, prefix in expected.items():
            with self.subTest(tool=name):
                self.assertIn(name, tools)
                self.assertTrue(tools[name].description.startswith(prefix))


if __name__ == "__main__":
    unittest.main()
