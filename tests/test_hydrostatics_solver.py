"""Offline tests for calculate_hydrostatics.

The fake IHydrostatics is built from the proven getter list, and its default
numbers are a physically self-consistent hull so that a validation failure in a
test means the rule fired, not that the fixture was nonsense.
"""

from __future__ import annotations

import json
import types
import unittest
from typing import Any
from unittest import mock

import server
from maxsurf_mcp import hydrostatics as hydro
from maxsurf_mcp.audit import Classification, Outcome
from maxsurf_mcp.errors import MaxsurfConnectionError, MaxsurfValidationError

from .support import IsolatedEnvTestCase

DENSITY = 1025.0

#: A small, internally consistent hull: Displacement == density * Volume, every
#: coefficient in range, draft and KB positive.
VOLUME = 400.0
BASE_RESULTS: dict[str, float] = {
    "Displacement": DENSITY * VOLUME,
    "Volume": VOLUME,
    "Draft": 3.2,
    "ImmersedDepth": 3.2,
    "LWL": 56.5,
    "BeamWL": 10.4,
    "WSA": 620.0,
    "WaterplaneArea": 470.0,
    "MaxCrossSectArea": 31.0,
    "Cb": 0.49,
    "Cp": 0.62,
    "Cm": 0.79,
    "Cwp": 0.80,
    "LCB": 27.1,
    "LCBasFraction": 0.48,
    "LCF": 26.0,
    "LCFasFraction": 0.46,
    "KB": 1.85,
    "KG": 0.0,
    "BMt": 4.9,
    "BMl": 180.0,
    "GMt": 6.75,
    "GMl": 181.85,
    "KMt": 6.75,
    "KMl": 181.85,
    "MTc": 13200.0,
    "TPC": 4817.5,
    "RM": 0.0,
    "LBG": 56.0,
    "AG": 4.1,
    "FG": 4.3,
    "Flare": 12.0,
}


class _FakeHydrostatics:
    def __init__(self, results: dict[str, float] | None = None,
                 fail: bool = False, returns: Any = True,
                 failing_reads: set[str] | None = None):
        self._results = dict(BASE_RESULTS if results is None else results)
        self._fail = fail
        self._returns = returns
        self._failing = set(failing_reads or ())
        self.calls: list[tuple[float, float]] = []

    def Calculate(self, density, vcg):  # noqa: N802 - COM naming
        self.calls.append((density, vcg))
        if self._fail:
            raise OSError("the solver refused")
        return self._returns

    def __getattr__(self, name: str) -> Any:
        results = object.__getattribute__(self, "_results")
        if name in object.__getattribute__(self, "_failing"):
            raise OSError(f"{name} is unavailable")
        if name in results:
            return results[name]
        raise AttributeError(name)


class _FakeSurface:
    def __init__(self, name: str, assembly: str, visible: bool):
        self.Name = name
        self.AssemblyName = assembly
        self.Visible = visible
        self.Use = 1


class _FakeSurfaces:
    def __init__(self, items: list[_FakeSurface]):
        self.items = list(items)

    @property
    def Count(self) -> int:  # noqa: N802 - COM naming
        return len(self.items)

    def __call__(self, index: int) -> _FakeSurface:
        return self.items[index - 1]


DEFAULT_SURFACES = [
    _FakeSurface("Hull", "Hull", True),
    _FakeSurface("Deck", "Deck", False),
    _FakeSurface("Skeg", "Skeg", True),
]


class _FakePreferences:
    """Precision is documented put-only, so reading it must not silently work."""

    def __init__(self, readable: bool = False):
        self._readable = readable

    @property
    def Precision(self):  # noqa: N802 - COM naming
        if self._readable:
            return 3
        raise OSError("Precision is write-only")


class _FakeApp:
    def __init__(self, hydrostatics, surfaces=None, trimming=False,
                 trimming_writable=True, preferences=None):
        self.Design = types.SimpleNamespace(
            Hydrostatics=hydrostatics,
            Surfaces=_FakeSurfaces(
                DEFAULT_SURFACES if surfaces is None else surfaces),
        )
        self._trimming = trimming
        self._trimming_writable = trimming_writable
        self.Preferences = _FakePreferences() if preferences is None \
            else preferences

    @property
    def Trimming(self):  # noqa: N802 - COM naming
        return self._trimming

    @Trimming.setter
    def Trimming(self, value):  # noqa: N802 - COM naming
        if not self._trimming_writable:
            raise OSError("Trimming is read-only on this build")
        self._trimming = bool(value)


class HydrostaticsTestCase(IsolatedEnvTestCase):
    def patch(self, app: _FakeApp) -> _FakeApp:
        patcher = mock.patch.object(server.com, "connect", return_value=app)
        patcher.start()
        self.addCleanup(patcher.stop)
        return app

    def run_tool(self, app: _FakeApp, **kwargs: Any) -> dict[str, Any]:
        self.patch(app)
        return json.loads(server.calculate_hydrostatics(**kwargs))


class SolverTests(HydrostaticsTestCase):
    def test_calculate_is_driven_with_the_supplied_density_and_vcg(self):
        solver = _FakeHydrostatics()
        payload = self.run_tool(_FakeApp(solver), density=1025.0, vcg=2.5)
        self.assertEqual(solver.calls, [(1025.0, 2.5)])
        self.assertEqual(payload["solver_status"], "ok")
        self.assertEqual(payload["inputs"],
                         {"density": 1025.0, "density_unit": "kg/m^3",
                          "vcg": 2.5, "vcg_unit": "m"})

    def test_every_proven_getter_is_reported_with_its_unit(self):
        payload = self.run_tool(_FakeApp(_FakeHydrostatics()))
        self.assertEqual(set(payload["results"]), set(hydro.RESULT_FIELDS))
        self.assertEqual(payload["results"]["Displacement"]["unit"], "kg")
        self.assertEqual(payload["results"]["Volume"]["unit"], "m^3")
        self.assertEqual(payload["results"]["WSA"]["unit"], "m^2")
        self.assertEqual(payload["results"]["Cb"]["unit"], "-")
        self.assertEqual(payload["results"]["Draft"]["unit"], "m")
        self.assertEqual(payload["results"]["TPC"]["unit"], "tonne/cm")
        self.assertEqual(payload["results"]["MTc"]["unit"], "tonne.m/cm")

    def test_a_solver_failure_is_reported_rather_than_raised(self):
        payload = self.run_tool(_FakeApp(_FakeHydrostatics(fail=True)))
        self.assertEqual(payload["solver_status"], "failed")
        self.assertIn("refused", payload["solver_error"])
        self.assertEqual(payload["validation_reasons"], ["solver_failed"])
        self.assertEqual(payload["results"], {})

    def test_the_boolean_return_is_recorded_but_never_trusted(self):
        # "Return Type: A Boolean value" is on every method page in all four
        # modules, so it is template text, not a success signal.
        payload = self.run_tool(_FakeApp(_FakeHydrostatics(returns=False)))
        self.assertIs(payload["solver_returned"], False)
        self.assertEqual(payload["solver_status"], "ok")
        self.assertEqual(payload["validation_status"], "ok")

    def test_a_failing_getter_is_not_reported_as_zero(self):
        solver = _FakeHydrostatics(failing_reads={"MTc"})
        payload = self.run_tool(_FakeApp(solver))
        self.assertIsNone(payload["results"]["MTc"]["value"])
        self.assertEqual(payload["results"]["MTc"]["status"], "failed")
        self.assertEqual(payload["unreadable_fields"], ["MTc"])

    def test_a_non_positive_density_is_refused_before_connecting(self):
        solver = _FakeHydrostatics()
        self.patch(_FakeApp(solver))
        for bad in (0.0, -1.0):
            with self.subTest(density=bad):
                with self.assertRaises(MaxsurfValidationError):
                    server.calculate_hydrostatics(density=bad)
        self.assertEqual(solver.calls, [])

    def test_non_finite_inputs_are_refused_before_connecting(self):
        solver = _FakeHydrostatics()
        self.patch(_FakeApp(solver))
        for kwargs in ({"density": float("nan")},
                       {"vcg": float("inf")},
                       {"vcg": float("nan")}):
            with self.subTest(arguments=kwargs):
                with self.assertRaises(MaxsurfValidationError):
                    server.calculate_hydrostatics(**kwargs)
        self.assertEqual(solver.calls, [])

    def test_a_missing_design_raises_a_connection_error(self):
        app = types.SimpleNamespace()
        patcher = mock.patch.object(server.com, "connect", return_value=app)
        patcher.start()
        self.addCleanup(patcher.stop)
        with self.assertRaises(MaxsurfConnectionError):
            server.calculate_hydrostatics()


class ValidationTests(HydrostaticsTestCase):
    def results(self, **overrides: float) -> dict[str, float]:
        merged = dict(BASE_RESULTS)
        merged.update(overrides)
        return merged

    def reasons(self, **overrides: float) -> list[str]:
        payload = self.run_tool(
            _FakeApp(_FakeHydrostatics(self.results(**overrides))))
        return payload["validation_reasons"]

    def test_a_consistent_hull_validates_clean(self):
        payload = self.run_tool(_FakeApp(_FakeHydrostatics()))
        self.assertEqual(payload["validation_status"], "ok")
        self.assertEqual(payload["validation_reasons"], [])
        self.assertLess(payload["density_closure_relative_error"], 1e-6)

    def test_nonzero_vcg_is_suspect_until_the_sign_transform_is_proven(self):
        payload = self.run_tool(
            _FakeApp(_FakeHydrostatics()), density=1025.0, vcg=1.1)
        self.assertEqual(payload["validation_status"], "suspect")
        self.assertIn("vcg_sign_convention_unproven",
                      payload["validation_reasons"])
        self.assertEqual(payload["vcg_contract"]["status"], "unproven")

    def test_density_closure_catches_a_mismatched_displacement(self):
        self.assertIn("density_volume_mismatch",
                      self.reasons(Displacement=DENSITY * VOLUME * 1.01))

    def test_density_closure_tolerates_float_noise(self):
        self.assertNotIn(
            "density_volume_mismatch",
            self.reasons(Displacement=DENSITY * VOLUME * (1 + 1e-9)),
        )

    def test_no_immersed_volume_is_caught(self):
        self.assertIn("no_immersed_volume", self.reasons(Volume=0.0))
        self.assertIn("no_immersed_volume", self.reasons(Displacement=-1.0))

    def test_a_non_positive_draft_with_immersion_means_no_datum(self):
        reasons = self.reasons(Draft=-0.70, ImmersedDepth=0.026)
        self.assertIn("datum_not_established", reasons)

    def test_a_negative_kb_means_the_datum_sits_below_the_hull(self):
        self.assertIn("datum_below_hull", self.reasons(KB=-0.71))

    def test_each_coefficient_is_range_checked(self):
        for field in ("Cb", "Cp", "Cm", "Cwp"):
            with self.subTest(coefficient=field):
                self.assertIn("coefficient_out_of_range",
                              self.reasons(**{field: 1.4}))
                self.assertIn("coefficient_out_of_range",
                              self.reasons(**{field: -0.1}))

    def test_a_zero_mtc_on_a_floating_hull_is_suspicious(self):
        self.assertIn("suspicious_zero", self.reasons(MTc=0.0))

    def test_a_zero_mtc_without_displacement_is_not_flagged_as_suspicious(self):
        # no_immersed_volume already explains it; suspicious_zero would be noise.
        self.assertNotIn("suspicious_zero",
                         self.reasons(MTc=0.0, Displacement=0.0, Volume=0.0))

    def test_the_phase_3c2_numbers_reproduce_the_datum_failures(self):
        # The live Stability run that started this whole line of work.
        reasons = self.reasons(
            Displacement=6.6303074250800265, Volume=0.006468592609834172,
            Draft=-0.7031905762482751, ImmersedDepth=0.026079046782653048,
            KB=-0.7097675557168508, Cb=0.29850904253607985,
        )
        self.assertIn("datum_not_established", reasons)
        self.assertIn("datum_below_hull", reasons)
        self.assertNotIn("density_volume_mismatch", reasons)


class PreconditionTests(HydrostaticsTestCase):
    def test_trimming_is_switched_on_before_calculating(self):
        app = _FakeApp(_FakeHydrostatics(), trimming=False)
        payload = self.run_tool(app)
        trimming = payload["preconditions"]["trimming"]
        self.assertTrue(trimming["write_attempted"])
        self.assertTrue(trimming["effective"])
        self.assertTrue(app.Trimming)

    def test_trimming_already_on_is_left_alone(self):
        payload = self.run_tool(_FakeApp(_FakeHydrostatics(), trimming=True))
        trimming = payload["preconditions"]["trimming"]
        self.assertFalse(trimming["write_attempted"])
        self.assertTrue(trimming["effective"])

    def test_an_unwritable_trimming_is_reported_not_raised(self):
        payload = self.run_tool(_FakeApp(
            _FakeHydrostatics(), trimming=False, trimming_writable=False))
        trimming = payload["preconditions"]["trimming"]
        self.assertIsNotNone(trimming["write_error"])
        self.assertFalse(trimming["effective"])
        self.assertEqual(payload["solver_status"], "ok")
        self.assertTrue(any("trimming is not confirmed on" in w
                            for w in payload["warnings"]))

    def test_precision_is_reported_as_unavailable_with_the_reason(self):
        payload = self.run_tool(_FakeApp(_FakeHydrostatics()))
        precision = payload["preconditions"]["precision"]
        self.assertEqual(precision["status"], "unavailable")
        self.assertIn("put-only", precision["detail"])
        self.assertTrue(any("precision setting could not be read" in w
                            for w in payload["warnings"]))

    def test_a_readable_precision_is_reported_when_the_build_allows_it(self):
        payload = self.run_tool(_FakeApp(
            _FakeHydrostatics(), preferences=_FakePreferences(readable=True)))
        self.assertEqual(payload["preconditions"]["precision"]["status"], "read")
        self.assertEqual(payload["preconditions"]["precision"]["value"], 3)

    def test_visible_surfaces_are_reported_grouped_by_assembly(self):
        payload = self.run_tool(_FakeApp(_FakeHydrostatics()))
        visibility = payload["preconditions"]["visibility"]
        self.assertEqual(visibility["count"], 3)
        self.assertEqual(visibility["visible_count"], 2)
        self.assertEqual(visibility["assemblies"]["Deck"],
                         {"total": 1, "visible": 0})
        self.assertEqual(visibility["assemblies"]["Hull"],
                         {"total": 1, "visible": 1})

    def test_hidden_surfaces_are_warned_about(self):
        payload = self.run_tool(_FakeApp(_FakeHydrostatics()))
        self.assertTrue(any("1 of 3 surfaces were hidden" in w
                            for w in payload["warnings"]))

    def test_an_all_visible_model_produces_no_hidden_warning(self):
        surfaces = [_FakeSurface("Hull", "Hull", True)]
        payload = self.run_tool(_FakeApp(_FakeHydrostatics(),
                                         surfaces=surfaces))
        self.assertFalse(any("were hidden" in w for w in payload["warnings"]))


class EnvelopeTests(HydrostaticsTestCase):
    def test_the_coefficient_convention_is_always_declared_unknown(self):
        payload = self.run_tool(_FakeApp(_FakeHydrostatics()))
        self.assertEqual(payload["convention_status"], "unknown")
        self.assertIn("LWL or LPP", payload["convention_detail"])
        self.assertTrue(any("sign convention" in w
                            for w in payload["warnings"]))

    def test_the_run_is_audited_as_an_analysis_with_intent_first(self):
        self.run_tool(_FakeApp(_FakeHydrostatics()))
        records = self.records_for("calculate_hydrostatics")
        self.assertEqual([r["stage"] for r in records], ["intent", "complete"])
        self.assertEqual(records[0]["classification"],
                         Classification.EXECUTE_ANALYSIS)
        self.assertEqual(records[-1]["outcome"], Outcome.SUCCESS)


class RegistrationTests(unittest.TestCase):
    def test_the_tool_declares_that_it_executes_an_analysis(self):
        tools = {t.name: t for t in server.mcp._tool_manager.list_tools()}
        self.assertTrue(tools["calculate_hydrostatics"].description
                        .startswith("EXECUTE_ANALYSIS."))

    def test_unproven_screening_tools_are_not_published(self):
        tools = {t.name for t in server.mcp._tool_manager.list_tools()}
        self.assertFalse({"hull_summary", "evaluate_design"} & tools)

    def test_every_reported_field_is_on_the_proven_getter_list(self):
        # private discovery notes records 45 read-only R8 getters on IHydrostatics; none of
        # the names below may be invented.
        proven = {
            "Displacement", "Volume", "Draft", "LWL", "BeamWL", "WSA",
            "MaxCrossSectArea", "WaterplaneArea", "Cp", "Cb", "Cm", "Cwp",
            "LCB", "LCF", "LCBasFraction", "LCFasFraction", "KB", "KG",
            "BMt", "BMl", "GMt", "GMl", "KMt", "KMl", "ImmersedDepth",
            "TPC", "MTc", "RM", "LBG", "AG", "FG", "ABC", "FBC", "Flare",
        }
        self.assertTrue(set(hydro.RESULT_FIELDS) <= proven)


if __name__ == "__main__":
    unittest.main()
