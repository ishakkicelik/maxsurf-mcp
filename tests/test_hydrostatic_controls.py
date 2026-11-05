"""Offline contract tests; no native application or model is modified."""

import json
import types
from unittest import mock

from maxsurf_mcp import hydrostatic_controls as controls
from maxsurf_mcp.audit import Classification
from maxsurf_mcp.errors import MaxsurfCOMError, MaxsurfValidationError
from maxsurf_mcp.guard import REGISTRY

from .support import IsolatedEnvTestCase
from .test_hydrostatics_solver import _FakeHydrostatics, _FakeApp


class NativeHydro(_FakeHydrostatics):
    def __init__(self, fail_method=None):
        super().__init__()
        self.sequence = []
        self.fail_method = fail_method

    def Calculate(self, *args):
        self.sequence.append(("Calculate", args))
        return super().Calculate(*args)

    def __getattr__(self, name):
        if name in {"Transform", "InitAdvancedTransform", "AdvancedTransform"} \
                or name.startswith("SetTarget"):
            def call(*args):
                self.sequence.append((name, args))
                if name == self.fail_method:
                    raise OSError("native rejected target")
                return None
            return call
        if name in controls.TRANSFORM_FIELDS:
            return 0.5
        return super().__getattr__(name)


class ControlsTests(IsolatedEnvTestCase):
    def connect(self, hydro=None, app=None):
        self.hydro = hydro or NativeHydro()
        app = app or _FakeApp(self.hydro, preferences=types.SimpleNamespace(Precision=3))
        patcher = mock.patch.object(controls.com, "connect", return_value=app)
        self.connection = patcher.start()
        self.addCleanup(patcher.stop)
        return app

    def transform(self, **overrides):
        args = dict(target_lcb=27.0, target_coefficient=0.6, target_mac=0.8,
                    target_displacement=410000.0, target_immersed_depth=3.2,
                    target_beam=10.4, target_lwl=56.5, constrain_mac=True,
                    constrain_displacement=False, constrain_lwl=True,
                    constrain_beam=False, constrain_immersed_depth=True,
                    optimise_block_coefficient=False)
        args.update(overrides)
        return json.loads(controls.transform_hydrostatics(**args))

    def test_settings_native_read_and_write(self):
        app = self.connect()
        initial = json.loads(controls.get_hydrostatic_settings())
        self.assertEqual(initial["precision"]["value"], 3)
        result = json.loads(controls.set_hydrostatic_settings(
            trimming=True, precision="highest"))
        self.assertTrue(result["verified"])
        self.assertTrue(app.Trimming)
        self.assertEqual(app.Preferences.Precision, 5)
        self.assertEqual(result["after"]["precision"]["value"], 5)

    def test_all_precision_enum_values(self):
        self.connect()
        for name, value in controls.PRECISIONS.items():
            result = json.loads(controls.set_hydrostatic_settings(precision=name))
            self.assertEqual(result["after"]["precision"]["value"], value)

    def test_validation_happens_before_connect(self):
        self.connect()
        for args in ({}, {"trimming": 1}, {"precision": "ultra"},
                     {"precision": []}):
            with self.subTest(args=args), self.assertRaises(MaxsurfValidationError):
                controls.set_hydrostatic_settings(**args)
        for args in ({"target_lcb": True}, {"target_lwl": float("nan")},
                     {"target_mac": 1.1}, {"constrain_mac": 1},
                     {"density": 0}, {"target_beam": "10"}):
            with self.subTest(args=args), self.assertRaises(MaxsurfValidationError):
                self.transform(**args)
        self.connection.assert_not_called()

    def test_setting_failure_retains_partial_write_evidence(self):
        class Preferences:
            @property
            def Precision(self):
                return 3

            @Precision.setter
            def Precision(self, value):
                raise OSError("cannot write")
        self.connect(app=_FakeApp(NativeHydro(), preferences=Preferences()))
        with self.assertRaises(MaxsurfCOMError) as raised:
            controls.set_hydrostatic_settings(trimming=True, precision="high")
        self.assertEqual(raised.exception.details["completed"], ["Application.Trimming"])
        self.assertTrue(raised.exception.details["after"]["trimming"]["value"])
        self.assertEqual(raised.exception.details["operation_state"], "may_be_partial")

    def test_ignored_write_is_not_success(self):
        class Preferences:
            @property
            def Precision(self):
                return 3

            @Precision.setter
            def Precision(self, value):
                pass
        self.connect(app=_FakeApp(NativeHydro(), preferences=Preferences()))
        with self.assertRaises(MaxsurfCOMError) as raised:
            controls.set_hydrostatic_settings(precision="high")
        self.assertEqual(raised.exception.details["mismatched"], ["precision"])

    def test_cached_results_do_not_claim_freshness(self):
        self.connect()
        result = json.loads(controls.get_hydrostatic_results())
        self.assertEqual(self.hydro.sequence, [])
        self.assertEqual(result["freshness"], "unknown")
        self.assertEqual(result["vcg_datum_status"], "unproven")
        self.assertEqual(set(result["transformation_results"]), set(controls.TRANSFORM_FIELDS))

    def test_failed_native_result_getter_is_retained_not_zeroed(self):
        class FailedRead(NativeHydro):
            def __getattr__(self, name):
                if name == "TransformMAC":
                    raise OSError("getter failed")
                return super().__getattr__(name)
        self.connect(FailedRead())
        result = json.loads(controls.get_hydrostatic_results())
        entry = result["transformation_results"]["TransformMAC"]
        self.assertNotEqual(entry["status"], "read")
        self.assertIsNone(entry["value"])

    def test_transform_uses_exact_thirteen_argument_order(self):
        self.connect()
        result = self.transform()
        self.assertEqual(self.hydro.sequence, [
            ("Calculate", (1025.0, 0.0)),
            ("Transform", (27.0, 0.6, 0.8, 410000.0, 3.2, 10.4, 56.5,
                           True, False, True, False, True, False)),
            ("Calculate", (1025.0, 0.0)),
        ])
        self.assertEqual(result["native_call_status"], "completed")
        self.assertEqual(result["target_achievement"], "unverified")
        self.assertEqual(result["before"]["Volume"]["status"], "read")
        self.assertEqual(result["after"]["Volume"]["status"], "read")

    def test_advanced_calls_init_setters_and_execute_in_order(self):
        self.connect()
        targets = {"displacement": 410000, "girths": [4.1, 4.3], "block": 0.6}
        controls.advanced_transform_hydrostatics(json.dumps(targets))
        self.assertEqual(self.hydro.sequence, [
            ("Calculate", (1025.0, 0.0)),
            ("InitAdvancedTransform", ()),
            ("SetTargetDisp", (410000.0,)),
            ("SetTargetGirths", (4.1, 4.3)),
            ("SetTargetBlock", (0.6,)),
            ("AdvancedTransform", ()),
            ("Calculate", (1025.0, 0.0)),
        ])

    def test_every_supported_advanced_signature(self):
        self.connect()
        for name, (method, count) in controls.TARGETS.items():
            self.hydro.sequence.clear()
            value = [1.0, 2.0] if count == 2 else 0.5
            controls.advanced_transform_hydrostatics(json.dumps({name: value}))
            expected = tuple(value) if count == 2 else (value,)
            self.assertIn((method, expected), self.hydro.sequence)

    def test_unsupported_prerequisites_and_bad_targets_refused_before_connect(self):
        self.connect()
        for targets in ('{}', '[]', 'invalid', '{"lcf":1}', '{"midbody":[0,1]}',
                        '{"beam":true}', '{"beam":-1}', '{"wpac":1.1}',
                        '{"girths":[1]}', '{"girths":[1,"x"]}', '{"other":1}'):
            with self.subTest(targets=targets), self.assertRaises(MaxsurfValidationError):
                controls.advanced_transform_hydrostatics(targets)
        self.connection.assert_not_called()

    def test_advanced_failure_stops_and_reports_completed_steps(self):
        self.connect(NativeHydro(fail_method="SetTargetBeam"))
        with self.assertRaises(MaxsurfCOMError) as raised:
            controls.advanced_transform_hydrostatics('{"lwl":50,"beam":10}')
        self.assertEqual(raised.exception.details["completed"],
                         ["Calculate", "InitAdvancedTransform", "SetTargetLWL"])
        self.assertNotIn(("AdvancedTransform", ()), self.hydro.sequence)
        self.assertFalse(raised.exception.details["rollback_performed"])

    def test_transform_failure_does_not_claim_rollback(self):
        self.connect(NativeHydro(fail_method="Transform"))
        with self.assertRaises(MaxsurfCOMError) as raised:
            self.transform()
        self.assertEqual(raised.exception.details["completed"], ["Calculate"])
        self.assertEqual(raised.exception.details["operation_state"], "may_be_partial")

    def test_tools_are_guarded_with_actual_mutation_classifications(self):
        for tool in controls.TOOLS:
            expected = Classification.READ if tool.__name__.startswith("get_") \
                else Classification.WRITE_STATE if tool.__name__.startswith("set_") \
                else Classification.WRITE_GEOMETRY
            self.assertEqual(REGISTRY[tool.__name__]["classification"], expected)
