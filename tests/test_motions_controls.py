"""Offline native-boundary regressions; never activate a Bentley application."""
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from maxsurf_mcp import motions, motions_controls as controls, stability
from maxsurf_mcp.errors import MaxsurfCOMError, MaxsurfValidationError


class Collection:
    def __init__(self, fields=(), refuse=False, coerce=False):
        self.rows = []
        self.fields = fields
        self.refuse = refuse
        self.coerce = coerce

    @property
    def Count(self):
        return len(self.rows)

    def Item(self, index):
        return self.rows[index-1]

    def Add(self, *args):
        if not self.refuse:
            item = NS(**dict(zip(self.fields, args)))
            if self.coerce:
                item.Position = 999
            self.rows.append(item)

    def RemoveAll(self):
        self.rows.clear()


class LocationTests(unittest.TestCase):
    def design(self, **kwargs):
        return NS(Vessel=NS(), AnalysisOptions=NS(),
                  Headings=Collection(("Name", "Heading")),
                  Speeds=Collection(("Name", "Speed")),
                  Spectra=Collection(),
                  Locations=Collection(("Name", "Position", "Offset", "Height"), **kwargs))

    def configure(self, design, locations=None, spectra=None, replace=False):
        with patch.object(motions.com, "connect"), patch.object(
            motions, "_require_ready", return_value=design
        ), patch.object(stability, "_constant", return_value=1):
            return json.loads(motions.configure_motions_analysis.__wrapped__(
                "[180]", "[4]", json.dumps(spectra or [
                    {"name":"sea","type":"skSpectrumJONSWAP","hs_m":2,"tp_s":8}
                ]), 1025, 2, 1, 1, 2, 2, .1, "skATStripTheory",
                "skVTMonohull", json.dumps(locations or []), replace))

    def test_location_readback_uses_actual_native_fields(self):
        result = self.configure(self.design(), [{"name":"bow","x":12,"y":-2,"z":3}])
        self.assertEqual(result["location_readbacks"],
                         [{"index":1,"name":"bow","x_m":12,"y_m":-2,"z_m":3}])
        self.assertTrue(result["verified"])

    def test_ignored_or_coerced_creation_fails(self):
        for flags in ({"refuse":True}, {"coerce":True}):
            with self.subTest(flags=flags), self.assertRaises(MaxsurfCOMError):
                self.configure(self.design(**flags), [{"name":"bow","x":1,"y":2,"z":3}])

    def test_duplicate_supplied_locations_are_rejected_before_connect(self):
        with patch.object(motions.com, "connect") as connect:
            with self.assertRaises(MaxsurfValidationError):
                motions.configure_motions_analysis.__wrapped__(
                    "[180]", "[0]", '[{"type":"skSpectrumJONSWAP","hs_m":2,"tp_s":8}]',
                    1025,2,1,1,2,2,.1,"skATStripTheory","skVTMonohull",
                    '[{"name":"Bow","x":1,"y":2,"z":3},{"name":"bow","x":2,"y":2,"z":3}]')
            connect.assert_not_called()

    def test_duplicate_spectra_fail_before_mutation(self):
        d = self.design()
        spec = {"name":"sea","type":"skSpectrumJONSWAP","hs_m":2,"tp_s":8}
        with self.assertRaises(MaxsurfValidationError):
            self.configure(d, spectra=[spec, dict(spec)])
        self.assertEqual(vars(d.Vessel), {})

    def test_existing_name_fails_before_any_settings_change(self):
        d = self.design()
        d.Locations.Add("Bow", 1, 2, 3)
        with self.assertRaises(MaxsurfValidationError):
            self.configure(d, [{"name":"bow","x":1,"y":2,"z":3}])
        self.assertEqual(vars(d.Vessel), {})

    def test_replace_allows_existing_name_and_verifies_new_location(self):
        d = self.design()
        d.Locations.Add("bow", 1, 2, 3)
        r = self.configure(d, [{"name":"bow","x":4,"y":5,"z":6}], replace=True)
        self.assertEqual(r["location_readbacks"][0]["x_m"], 4)
        self.assertEqual(d.Locations.Count, 1)


class SettingsTests(unittest.TestCase):
    def invoke(self, fn, *args, design=None):
        d = design or NS(AnalysisOptions=NS(), Vessel=NS())
        with patch.object(controls.com, "connect"), patch.object(
            controls, "_require_ready", return_value=d
        ):
            return json.loads(fn.__wrapped__(*args))

    def test_manual_frequency_values_are_stored_without_conversion(self):
        r = self.invoke(controls.set_motions_frequency_range, False, 31, .2, 3)
        self.assertEqual(r["stored"], {"FrequenciesAuto":False,"NumOfFrequencies":31,
                                      "FrequenciesStart":.2,"FrequenciesEnd":3})
        self.assertFalse(r["analysis_run"])

    def test_automatic_does_not_overwrite_endpoints(self):
        d = NS(AnalysisOptions=NS(FrequenciesStart=.4, FrequenciesEnd=2))
        self.invoke(controls.set_motions_frequency_range, True, 10, design=d)
        self.assertEqual(d.AnalysisOptions.FrequenciesStart, .4)

    def test_invalid_frequency_requests_never_connect(self):
        for args in ((1,10), (True,True), (True,1.5), (False,10,3,2),
                     (False,10,float("nan"),2), (True,10,1,2)):
            with self.subTest(args=args), patch.object(controls.com,"connect") as connect:
                with self.assertRaises(MaxsurfValidationError):
                    controls.set_motions_frequency_range.__wrapped__(*args)
                connect.assert_not_called()

    def test_native_enum_values_are_discovered_not_guessed(self):
        with patch.object(controls.constants,"discover", return_value={"constants":{
            "skWFArbitraryWaveHeading":1,"skARHavelock":4
        }}):
            r = self.invoke(controls.set_motions_formulation, True,
                            "skWFArbitraryWaveHeading", "skARHavelock")
        self.assertEqual(r["stored"]["AddedResistanceMethod"],4)

    def test_wrong_enum_family_is_rejected(self):
        with patch.object(controls.com,"connect"), self.assertRaises(MaxsurfValidationError):
            controls.set_motions_formulation.__wrapped__(True,"skATStripTheory","skARHavelock")

    def test_vessel_bounds_validate_before_connect(self):
        for sections, terms in ((201,3),(10,16),(True,3),(10,0),(1.5,3)):
            with patch.object(controls.com,"connect") as connect:
                with self.assertRaises(MaxsurfValidationError):
                    controls.set_motions_vessel_settings.__wrapped__(2,3,.1,4,sections,terms)
                connect.assert_not_called()

    def test_vessel_settings_store_unequal_drafts(self):
        r = self.invoke(controls.set_motions_vessel_settings, 2,3,.1,4,20,5)
        self.assertEqual(r["stored"]["DraftAft"],2)
        self.assertEqual(r["stored"]["DraftFwd"],3)

    def test_refused_storage_is_typed_error(self):
        class Refused:
            FrequenciesAuto = False
            def __setattr__(self, key, value):
                pass
        with self.assertRaises(MaxsurfCOMError):
            self.invoke(controls.set_motions_frequency_range, True,10,
                        design=NS(AnalysisOptions=Refused()))


if __name__ == "__main__":
    unittest.main()
