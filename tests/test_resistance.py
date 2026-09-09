from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from maxsurf_mcp import resistance
from maxsurf_mcp.audit import Classification


CATALOG = {"hsMTHoltrop": 5, "hsMTSlenderBody": 12}


class _Methods:
    def __init__(self):
        self.selected: set[int] = set()
        self.SlenderBodyOnePlusK = -1.0

    def IsSelected(self, value):  # noqa: N802 - COM spelling
        return value in self.selected

    def SetSelect(self, value, selected):  # noqa: N802 - COM spelling
        if selected:
            self.selected.add(value)
        else:
            self.selected.discard(value)

    @property
    def SelectAll(self):  # noqa: N802 - COM spelling
        return False

    @SelectAll.setter
    def SelectAll(self, selected):  # noqa: N802 - COM spelling
        if not selected:
            self.selected.clear()


def _result(speed: float, rt: float, power: float):
    return SimpleNamespace(
        v=speed, Fn=0.2, Rt=rt, Power=power, Rf=0.6 * rt,
        Rr=0.4 * rt, Rv=0.0, Rw=0.0, Ct=0.004, Cf=0.002,
        Cr=0.002, Cv=0.0, Cw=0.0, Ccorr=0.0, TrimRun=0.0,
    )


class _Results:
    Count = 2

    def __init__(self):
        # Power is Maxsurf's installed-power field, deliberately NOT equal to
        # Rt*v here (as if ~62.5% efficiency were applied) so the test can tell
        # effective power (Rt*v) from installed power (the field).
        self.rows = [_result(5.0, 100000.0, 800000.0),
                     _result(10.0, 400000.0, 6400000.0)]

    def Velocity(self, index):  # noqa: N802 - COM spelling
        return self.rows[index].v

    def Item(self, method, index):  # noqa: N802 - COM spelling
        if method not in CATALOG.values():
            raise IndexError(method)
        return self.rows[index]


class ResistanceTests(unittest.TestCase):
    def app(self, *, initialized=False, loaded=False):
        methods = _Methods()
        velocities = SimpleNamespace(Low=0.0, High=10.28888888888889)
        vessel = SimpleNamespace(
            LWL=90.0, Beam=14.0, Draft=4.0, DraftFP=4.0,
            DisplacedVolume=2100.0, PrismaticCoeff=0.62,
            WaterplaneAreaCoeff=0.75, WettedArea=1700.0,
            MaxSectArea=45.0, HalfAngleOfEntrance=18.0,
            BulbTransverseArea=2.0, BulbHeightFromKeel=1.2,
            Deadrise=12.0, TransomArea=7.0, TransomBeamWL=8.0,
            TransomDraft=1.0, WaterDensity=1025.9,
            KinematicViscosity=1.18831e-6, CorrelationAllowance=0.0004,
            AppendageArea=0.0, NominalAppendageLength=0.0,
            AppendageFactor=1.0, FrontalArea=0.0, DragCoeff=0.8,
            AirDensity=1.225, Headwind=0.0, LCGFromMidships=2.0,
        )
        design = SimpleNamespace(
            DesignPath="C:/runtime/hull.msd" if loaded else "",
            DesignName="hull" if loaded else "", Velocities=velocities,
            Methods=methods, Vessel=vessel, ResistanceResults=_Results(),
            Efficiency=SimpleNamespace(Efficiency=100.0),
            measured=0, calculated=0,
        )

        def measure():
            design.measured += 1

        def calculate():
            design.calculated += 1

        design.MeasureHull = measure
        design.CalculateResistance = calculate
        return SimpleNamespace(
            IsInitializedCorrectly=initialized,
            LicenseLevel="Maxsurf", Version="25.00.02.339", Design=design,
        )

    def discovery(self, app, name_filter=None):
        values = CATALOG
        if name_filter and name_filter != "hsMT":
            values = {name: value for name, value in CATALOG.items()
                      if name == name_filter}
        return {"constants": values, "source": "generated_makepy"}

    def test_status_exposes_initialization_and_both_speed_units(self):
        app = self.app()
        with patch.object(resistance.com, "connect", return_value=app), \
             patch.object(resistance.comconstants, "discover",
                          side_effect=self.discovery):
            payload = json.loads(resistance.get_resistance_status())

        self.assertFalse(payload["automation_ready"])
        self.assertFalse(payload["design"]["loaded"])
        self.assertAlmostEqual(payload["velocities"]["high"]["knots"], 20.0)
        self.assertFalse(payload["analysis_available"])
        self.assertTrue(payload["warnings"])

    def test_initialized_session_exposes_analysis_workflow(self):
        app = self.app(initialized=True, loaded=True)
        with patch.object(resistance.com, "connect", return_value=app), \
             patch.object(resistance.comconstants, "discover",
                          side_effect=self.discovery):
            payload = json.loads(resistance.get_resistance_status())

        self.assertTrue(payload["automation_ready"])
        self.assertTrue(payload["design"]["loaded"])
        self.assertTrue(payload["analysis_available"])
        self.assertIsNone(payload["analysis_blocker"])

    def test_configuration_converts_knots_and_selects_named_methods(self):
        app = self.app(initialized=True, loaded=True)
        with patch.object(resistance.com, "connect", return_value=app), \
             patch.object(resistance.comconstants, "discover",
                          side_effect=self.discovery):
            payload = json.loads(resistance.configure_resistance_analysis(
                8.0, 30.0, '["hsMTHoltrop", "hsMTSlenderBody"]'))

        self.assertAlmostEqual(app.Design.Velocities.Low,
                               8.0 * resistance.MPS_PER_KNOT)
        self.assertAlmostEqual(app.Design.Velocities.High,
                               30.0 * resistance.MPS_PER_KNOT)
        self.assertEqual(app.Design.Methods.selected, {5, 12})
        self.assertEqual({m["name"] for m in payload["methods"]},
                         set(CATALOG))

    def test_run_measures_calculates_and_returns_si_results(self):
        app = self.app(initialized=True, loaded=True)
        app.Design.Methods.selected.add(5)
        with patch.object(resistance.com, "connect", return_value=app), \
             patch.object(resistance.comconstants, "discover",
                          side_effect=self.discovery):
            payload = json.loads(resistance.run_resistance_analysis(1025.0, 1.19e-6, 0.0004, 65.0))

        self.assertEqual(app.Design.measured, 1)
        self.assertEqual(app.Design.calculated, 1)
        self.assertEqual(payload["solver_status"], "ok")
        self.assertEqual(payload["results"]["index_base"], 0)
        method = payload["results"]["methods"][0]
        self.assertEqual(method["point_count"], 2)
        self.assertEqual(method["summary"]["max_total_resistance_N"], 400000.0)
        self.assertEqual(method["rows"][0]["Rt"]["unit"], "N")
        self.assertEqual(method["rows"][0]["Power"]["unit"], "W")
        # Effective power is Rt*v; installed power is Maxsurf's Power field
        # (which already includes efficiency). Installed must NOT be effective
        # divided by efficiency a second time.
        row0 = method["rows"][0]
        self.assertAlmostEqual(row0["effective_power"]["value"], 500000.0)
        self.assertAlmostEqual(row0["installed_power"]["value"], 800000.0)
        self.assertAlmostEqual(
            method["summary"]["max_effective_power_W"], 4000000.0)
        self.assertAlmostEqual(
            method["summary"]["max_installed_power_W"], 6400000.0)
        self.assertEqual(payload["propulsive_efficiency_percent"], 65.0)
        self.assertEqual(
            payload["results"]["propulsion"]["efficiency_percent"], 65.0)
        self.assertIn("CorrelationAllowance", payload["settings"])
        self.assertIn("NominalAppendageLength", payload["settings"])
        self.assertEqual(app.Design.Efficiency.Efficiency, 65.0)

    def test_caller_selected_efficiency_is_retained(self):
        app = self.app(initialized=True, loaded=True)
        app.Design.Methods.selected.add(5)
        with patch.object(resistance.com, "connect", return_value=app), \
             patch.object(resistance.comconstants, "discover",
                          side_effect=self.discovery):
            payload = json.loads(resistance.run_resistance_analysis(
                1025.0, 1.19e-6, 0.0004, propulsive_efficiency_percent=100.0))
        row0 = payload["results"]["methods"][0]["rows"][0]
        self.assertEqual(row0["installed_power"]["value"], row0["Power"]["value"])
        self.assertEqual(payload["propulsive_efficiency_percent"], 100.0)

    def test_export_writes_csv_with_installed_power(self):
        app = self.app(initialized=True, loaded=True)
        app.Design.Methods.selected.add(5)
        app.Design.Efficiency.Efficiency = 65.0
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "curve.csv"
            with patch.object(resistance.com, "connect", return_value=app), \
                 patch.object(resistance.comconstants, "discover",
                              side_effect=self.discovery), \
                 patch.object(resistance.workspace, "resolve_target",
                              return_value=target):
                payload = json.loads(
                    resistance.export_resistance_results("curve.csv"))
            self.assertTrue(target.exists())
            lines = target.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(lines[0].split(",")[0], "method")
        self.assertEqual(len(lines), 3)          # header + two speed points
        self.assertEqual(payload["rows_written"], 2)
        self.assertIn("installed_power_kW", payload["columns"])

    def test_tools_have_explicit_effect_classifications(self):
        expected = {
            resistance.get_resistance_status: Classification.READ,
            resistance.transfer_modeler_to_resistance: Classification.WRITE_FILE,
            resistance.configure_resistance_analysis: Classification.WRITE_STATE,
            resistance.run_resistance_analysis: Classification.EXECUTE_ANALYSIS,
            resistance.get_resistance_results: Classification.READ,
            resistance.export_resistance_results: Classification.WRITE_FILE,
        }
        for tool, classification in expected.items():
            with self.subTest(tool=tool.__name__):
                self.assertEqual(tool.maxsurf_classification, classification)


if __name__ == "__main__":
    unittest.main()
