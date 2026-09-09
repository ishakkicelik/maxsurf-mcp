from __future__ import annotations

import json
import unittest
from unittest import mock

from maxsurf_mcp import grids
from maxsurf_mcp.audit import Outcome
from maxsurf_mcp.errors import MaxsurfCOMError, MaxsurfValidationError

from .support import ComFake, IsolatedEnvTestCase


class _FakeGrid:
    TableCaption = "Current Loadcase"

    def __init__(self) -> None:
        self.selected_table = None

    def SetCurrentGrid(self, table: int) -> None:
        self.selected_table = table

    def GetNoOfColumns(self) -> int:
        return 2

    def ColumnHeading(self, col: int) -> str:
        return {0: "Unit Mass", 1: "Long. Arm"}[col]

    def ColumnUnits(self, col: int) -> str:
        return {0: "tonne", 1: "m"}[col]


class StabilityLoadcaseGridTests(IsolatedEnvTestCase):
    @mock.patch("maxsurf_mcp.grids.comconstants.discover")
    @mock.patch("maxsurf_mcp.grids.com.connect")
    def test_returns_live_headers_without_guessing_index_base(
        self,
        connect: mock.Mock,
        discover: mock.Mock,
    ) -> None:
        app = object()
        grid = _FakeGrid()
        connect.side_effect = [app, grid]
        discover.return_value = {
            "constants": {"hmInputCurrentLoadcase": 0},
        }

        payload = json.loads(grids.get_stability_loadcase_table_headers())

        self.assertEqual(grid.selected_table, 0)
        self.assertEqual(payload["column_count"], 2)
        self.assertEqual(payload["selected"]["index_base"], 0)
        self.assertEqual(
            payload["selected"]["columns"],
            [
                {"index": 0, "heading": "Unit Mass", "units": "tonne"},
                {"index": 1, "heading": "Long. Arm", "units": "m"},
            ],
        )
        connect.assert_has_calls([
            mock.call("stability"),
            mock.call(
                "stability_xmlgrid",
                progid="BentleyStability.XMLGrid",
            ),
        ])

    @mock.patch("maxsurf_mcp.grids.comconstants.discover")
    @mock.patch("maxsurf_mcp.grids.com.connect")
    def test_raises_before_xmlgrid_when_table_enum_is_missing(
        self,
        connect: mock.Mock,
        discover: mock.Mock,
    ) -> None:
        connect.return_value = object()
        discover.return_value = {"constants": {}}

        with self.assertRaises(MaxsurfValidationError):
            grids.get_stability_loadcase_table_headers()
        connect.assert_called_once_with("stability")

    @mock.patch("maxsurf_mcp.grids.comconstants.discover")
    @mock.patch("maxsurf_mcp.grids.com.connect")
    def test_the_failure_is_audited_rather_than_returned(
        self,
        connect: mock.Mock,
        discover: mock.Mock,
    ) -> None:
        connect.return_value = object()
        discover.return_value = {"constants": {}}

        with self.assertRaises(MaxsurfValidationError):
            grids.get_stability_loadcase_table_headers()
        record = self.records_for("get_stability_loadcase_table_headers")[-1]
        self.assertEqual(record["outcome"], Outcome.FAILURE)
        self.assertEqual(record["error"]["code"], "validation_error")


class ResultsTableTests(IsolatedEnvTestCase):
    def test_raises_when_no_result_member_is_reachable(self):
        with mock.patch.object(grids.com, "connect", return_value=ComFake()):
            with self.assertRaises(MaxsurfCOMError) as caught:
                grids.get_results_table("stability")
        self.assertIn("XMLGrid coclass", str(caught.exception))

    def test_reads_xml_from_the_first_available_member(self):
        app = ComFake(XMLGrid=ComFake(XML="<grid/>"))
        with mock.patch.object(grids.com, "connect", return_value=app):
            payload = json.loads(grids.get_results_table("stability"))
        self.assertEqual(payload["via"], "XMLGrid")
        self.assertEqual(payload["xml"], "<grid/>")


if __name__ == "__main__":
    unittest.main()
