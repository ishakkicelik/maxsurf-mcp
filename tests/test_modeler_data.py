"""Offline contract tests for Modeler grids, markers, and topology."""

from __future__ import annotations

import json
import types
import unittest
from unittest import mock

import server
from maxsurf_mcp.errors import MaxsurfSafetyError, MaxsurfValidationError

from .support import IsolatedEnvTestCase


CONSTANTS = {"constants": {
    "msGTSections": 1, "msGTWaterlines": 2,
    "msGTButtocklines": 3, "msGTDiagonals": 4,
    "msMTInternal": 1, "msMTTopEdge": 2, "msMTBottomEdge": 3,
    "msMTLeftEdge": 4, "msMTRightEdge": 5, "msMTTopRight": 6,
    "msMTTopLeft": 7, "msMTBottomRight": 8, "msMTBottomLeft": 9,
    # These deliberately share the marker prefix and numeric values.
    "msMTTriIso": 1, "msMTTriRA": 2, "msMTQuadDom": 3,
}}


class _Grids:
    def __init__(self):
        self.lines = {
            1: [["AP", 0.0, 0.0], ["FP", 7.0, 0.0]],
            2: [["DWL", 0.46, 0.0]], 3: [], 4: [],
        }
        self.SectionSplit = 1

    def LineCount(self, kind):  # noqa: N802
        return len(self.lines[kind])

    def GetGridLine(self, kind, index):  # noqa: N802
        return tuple(self.lines[kind][index - 1])

    def AddGridLine(self, kind, label, position, angle):  # noqa: N802
        self.lines[kind].append([label, position, angle])
        self.lines[kind].sort(key=lambda row: row[1])

    def SetGridLine(self, kind, index, label, position, angle):  # noqa: N802
        self.lines[kind][index - 1] = [label, position, angle]
        self.lines[kind].sort(key=lambda row: row[1])

    def DeleteAllLines(self, kind):  # noqa: N802
        self.lines[kind].clear()


class _Marker:
    def __init__(self, position, offset, height, station, name,
                 surface_id=0, marker_type=1, error=None):
        self.Position = position
        self.Offset = offset
        self.Height = height
        self.Station = station
        self.Name = name
        self.SurfaceID = surface_id
        self.Type = marker_type
        self.Index = 0
        self._error = error

    @property
    def SurfaceError(self):  # noqa: N802
        if self._error is None:
            raise OSError("marker is not associated with a surface")
        return self._error


class _Markers:
    def __init__(self, items=None):
        self.items = list(items or [])
        self._renumber()

    def _renumber(self):
        for index, marker in enumerate(self.items, 1):
            marker.Index = index

    @property
    def Count(self):  # noqa: N802
        return len(self.items)

    def __call__(self, index):
        return self.items[index - 1]

    def Add(self, position, offset, height, station, name):  # noqa: N802
        # Reordering proves the production tool does not assume the new item is
        # necessarily the last item in the COM collection.
        self.items.insert(0, _Marker(position, offset, height, station, name))
        self._renumber()
        return True


class _Surface:
    def __init__(self, name, surface_id, points, bonds=None):
        self.Name = name
        self.ID = surface_id
        self.Assembly = 3
        self.AssemblyName = "Hull"
        self.Visible = True
        self.Symmetrical = False
        self.points = points
        self.bonds = bonds or {}

    def ControlPointLimits(self):  # noqa: N802
        return (max(row for row, _ in self.points),
                max(column for _, column in self.points))

    def GetControlPoint(self, row, column, x, y, z):  # noqa: N802
        return self.points[(row, column)]

    def BondedToWhichSurface(self, edge):  # noqa: N802
        return self.bonds.get(edge, ("Unbonded", 0))[0]

    def BondedToWhichEdge(self, edge):  # noqa: N802
        return self.bonds.get(edge, ("Unbonded", 0))[1]

    def BondedTo(self, edge):  # noqa: N802
        target, target_edge = self.bonds.get(edge, ("Unbonded", 0))
        return f"{target}:{target_edge}" if target_edge else "Unbonded"


class _Surfaces:
    def __init__(self, items):
        self.items = items

    @property
    def Count(self):  # noqa: N802
        return len(self.items)

    def __call__(self, index):
        return self.items[index - 1]


class ModelerDataTests(IsolatedEnvTestCase):
    def setUp(self):
        super().setUp()
        points = {
            (1, 1): (0.0, 0.0, 0.0), (1, 2): (7.0, 0.0, 0.0),
            (2, 1): (0.0, 1.0, 1.0), (2, 2): (7.0, 1.0, 1.0),
        }
        self.grids = _Grids()
        self.markers = _Markers([
            _Marker(1.0, 0.5, 0.2, 3, "offset", 0),
            _Marker(2.0, 0.7, 0.3, 4, "fitted", 11, error=-0.004),
        ])
        first = _Surface("aft", 11, points, {3: ("fwd", 2)})
        second = _Surface("fwd", 12, points, {2: ("aft", 3)})
        self.design = types.SimpleNamespace(
            Grids=self.grids, Markers=self.markers,
            Surfaces=_Surfaces([first, second]),
        )
        app = types.SimpleNamespace(Design=self.design)
        connect = mock.patch.object(server.com, "connect", return_value=app)
        constants = mock.patch.object(
            server.comconstants, "discover", return_value=CONSTANTS)
        connect.start()
        constants.start()
        self.addCleanup(connect.stop)
        self.addCleanup(constants.stop)

    def test_topology_reports_unique_bond_and_bounds(self):
        payload = json.loads(server.inspect_surface_topology())
        self.assertEqual(payload["surface_count"], 2)
        self.assertEqual(payload["unique_bond_count"], 1)
        self.assertEqual(payload["assemblies"]["Hull"]["control_points"], 8)
        self.assertEqual(payload["surfaces"][0]["assembly_id"], 3)
        self.assertEqual(payload["surfaces"][0]["bounds"]["longitudinal"],
                         [0.0, 7.0])

    def test_grid_list_add_set_and_guard(self):
        listed = json.loads(server.list_modeler_grid("sections"))
        self.assertEqual(listed["grids"]["sections"]["count"], 2)
        added = json.loads(server.add_modeler_grid_line(
            "sections", "MS", 3.5))
        self.assertEqual(added["after_count"], 3)
        changed = json.loads(server.set_modeler_grid_line(
            "sections", 2, "MS", "MID", 3.6))
        self.assertEqual(changed["stored"][0]["label"], "MID")
        with self.assertRaises(MaxsurfSafetyError):
            server.set_modeler_grid_line("sections", 2, "stale", "X", 3.0)

    def test_whole_grid_replacement_requires_confirmation_and_count(self):
        wanted = json.dumps([
            {"label": "WL0", "position": 0.0},
            {"label": "DWL", "position": 0.46},
        ])
        with self.assertRaises(MaxsurfSafetyError):
            server.replace_modeler_grid("waterlines", wanted, 1)
        with self.assertRaises(MaxsurfSafetyError):
            server.replace_modeler_grid(
                "waterlines", wanted, 99, confirm_delete_all=True)
        payload = json.loads(server.replace_modeler_grid(
            "waterlines", wanted, 1, confirm_delete_all=True))
        self.assertEqual(payload["after_count"], 2)

    def test_section_split_has_optimistic_guard(self):
        payload = json.loads(server.set_section_split(2, expected_before=1))
        self.assertEqual(payload["after"], 2)
        with self.assertRaises(MaxsurfSafetyError):
            server.set_section_split(1, expected_before=0)

    def test_marker_error_is_opt_in_and_failures_are_visible(self):
        plain = json.loads(server.list_markers())
        self.assertNotIn("surface_error", plain["markers"][0])
        detailed = json.loads(server.list_markers(include_surface_error=True))
        self.assertIsNone(detailed["markers"][0]["surface_error"])
        self.assertEqual(detailed["markers"][1]["surface_error"], -0.004)

    def test_marker_add_handles_collection_reordering(self):
        payload = json.loads(server.add_marker(
            3.0, 0.8, 0.4, 5, "new marker"))
        self.assertEqual(payload["added"]["collection_index"], 1)
        self.assertEqual(payload["added"]["name"], "new marker")

    def test_marker_type_uses_marker_enum_not_same_prefix_mesh_enum(self):
        payload = json.loads(server.set_marker(
            1, "offset", json.dumps({"type": "msMTTopEdge"})))
        self.assertEqual(payload["after"]["type_enum_name"], "msMTTopEdge")
        with self.assertRaises(MaxsurfValidationError):
            server.set_marker(
                1, "offset", json.dumps({"type": "msMTTriIso"}))

    def test_marker_fit_report_uses_absolute_errors(self):
        payload = json.loads(server.marker_fit_report())
        self.assertEqual(payload["unassociated"], 1)
        self.assertEqual(payload["measured"], 1)
        self.assertEqual(payload["max_error"], 0.004)


if __name__ == "__main__":
    unittest.main()
