import json
import unittest
from unittest.mock import patch

from maxsurf_mcp import resistance_controls as controls
from maxsurf_mcp.errors import MaxsurfValidationError, MaxsurfCOMError
from tests import test_resistance


class ControlsTests(unittest.TestCase):
    def setUp(self):
        self.app = test_resistance.ResistanceTests().app(initialized=True, loaded=True)
        self.app.Design.Methods.selected.add(12)
        self.connection = patch.object(controls.com, "connect", return_value=self.app)
        self.connection.start()
        self.addCleanup(self.connection.stop)

    def test_writable_measurements_and_chine_readback(self):
        with patch.object(controls, "_constant", return_value=1):
            result = json.loads(controls.set_resistance_measurements(
                '{"Draft": 5, "DraftFP": 4.5, "LCGFromMidships": -2}',
                "hsCTHardChine"))
        self.assertEqual(self.app.Design.Vessel.Draft, 5)
        self.assertEqual(self.app.Design.Vessel.ChineType, 1)
        self.assertEqual(result["written"]["LCGFromMidships"], -2)
        self.assertTrue(result["verified"])

    def test_all_inputs_validated_before_writes(self):
        for raw in ('{"Draft": 5, "bad": 2}', '{"Draft": true}',
                    '{"Draft": NaN}', '{}'):
            with self.subTest(raw=raw), self.assertRaises(MaxsurfValidationError):
                controls.set_resistance_measurements(raw)
        self.assertEqual(self.app.Design.Vessel.Draft, 4)

    def test_noop_setter_is_rejected(self):
        class Vessel:
            @property
            def Draft(self):
                return 4

            @Draft.setter
            def Draft(self, value):
                pass
        self.app.Design.Vessel = Vessel()
        with self.assertRaises(MaxsurfCOMError):
            controls.set_resistance_measurements('{"Draft": 5}')

    def test_automatic_and_manual_factors(self):
        with patch.object(controls, "_constant", return_value=12):
            self.assertEqual(json.loads(controls.set_resistance_form_factor(
                "automatic"))["one_plus_k"], -1)
            self.assertEqual(json.loads(controls.set_resistance_form_factor(
                "manual", 1.15))["one_plus_k"], 1.15)

    def test_factor_validation_and_applicability(self):
        for mode, factor in (("manual", None), ("manual", True),
                             ("manual", 0), ("automatic", 1), ("bad", None)):
            with self.subTest(mode=mode, factor=factor):
                with self.assertRaises(MaxsurfValidationError):
                    controls.set_resistance_form_factor(mode, factor)
        self.app.Design.Methods.selected.clear()
        with patch.object(controls, "_constant", return_value=12):
            with self.assertRaises(MaxsurfValidationError):
                controls.set_resistance_form_factor("automatic")
