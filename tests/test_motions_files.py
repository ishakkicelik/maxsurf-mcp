"""Offline selector and native RAO file boundary tests."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from maxsurf_mcp import motions_files as files
from maxsurf_mcp.errors import MaxsurfCOMError, MaxsurfSafetyError, MaxsurfValidationError


class FilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/"rao.txt"
        self.env = patch.dict(os.environ, {"MAXSURF_MCP_WORKSPACE_ROOTS":self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.calls = []
        def export(path):
            self.calls.append(("export",path))
            Path(path).write_text("Omega\tHeave\n0.2\t1.0\n")
        self.design = NS(Speeds=NS(NumberTested=2),Headings=NS(NumberTested=3),
                         Spectra=NS(NumberTested=1),
                         Locations=NS(Count=2), AnalysisOptions=NS(NumOfFrequencies=31),
                         SectionMappings=NS(Count=20),
                         ResultsLocation=Mock(), ResultsFrequency=Mock(), ResultsSection=Mock(),
                         ResultsSpeed=Mock(side_effect=lambda i:self.calls.append(("speed",i))),
                         ResultsHeading=Mock(side_effect=lambda i:self.calls.append(("heading",i))),
                         ResultsSpectrum=Mock(side_effect=lambda i:self.calls.append(("spectrum",i))),
                         ExportRAOText=Mock(side_effect=export),
                         ExportRAOTextMultiCondition=Mock(side_effect=export),
                         ImportRAOText=Mock(),ImportRAOTextMultiCondition=Mock(),
                         ExportResults=Mock(side_effect=lambda table,path:export(path)))
        for p in (patch.object(files.com,"connect"),
                  patch.object(files,"_require_ready",return_value=self.design),
                  patch.object(files.constants,"discover",return_value={"constants":{"skTabGlobalRAOs":7}}),
                  patch.object(files.com,"session_for",return_value=NS(refresh_design_identity=Mock()))):
            p.start()
            self.addCleanup(p.stop)

    def test_selected_export_invokes_methods_before_native_export(self):
        r=json.loads(files.export_motions_condition_table.__wrapped__(
            str(self.path),"skTabGlobalRAOs",2,3,1))
        self.assertEqual(self.calls[:3],[("speed",2),("heading",3),("spectrum",1)])
        self.design.ExportResults.assert_called_once()
        table, staged = self.design.ExportResults.call_args.args
        self.assertEqual(table, 7)
        self.assertEqual(Path(staged).parent, self.path.parent)
        self.assertNotEqual(Path(staged), self.path)
        self.assertEqual(Path(staged).suffix, ".txt")
        self.assertTrue(r["file_verified"])
        self.assertFalse(r["selector_mapping_verified"])
        self.assertFalse(r["result_freshness_verified"])

    def test_all_indices_validate_before_any_selector(self):
        for idx in (True,1.5,0,4):
            with self.subTest(idx=idx), self.assertRaises(MaxsurfValidationError):
                files.export_motions_condition_table.__wrapped__(
                    str(self.path),"skTabGlobalRAOs",1,idx,1)
            self.assertEqual(self.calls,[])

    def test_table_enum_validates_before_selectors(self):
        with self.assertRaises(MaxsurfValidationError):
            files.export_motions_condition_table.__wrapped__(
                str(self.path),"skATStripTheory",1,1,1)
        self.assertEqual(self.calls,[])

    def test_optional_axes_use_methods_and_native_counts(self):
        files.export_motions_condition_table.__wrapped__(
            str(self.path),"skTabGlobalRAOs",1,1,1,
            location_index=2,frequency_index=31,section_index=20)
        self.design.ResultsLocation.assert_called_once_with(2)
        self.design.ResultsFrequency.assert_called_once_with(31)
        self.design.ResultsSection.assert_called_once_with(20)

    def test_optional_axis_bounds_fail_before_any_selector(self):
        with self.assertRaises(MaxsurfValidationError):
            files.export_motions_condition_table.__wrapped__(
                str(self.path),"skTabGlobalRAOs",1,1,1,section_index=21)
        self.assertEqual(self.calls,[])

    def test_selector_refusal_never_exports(self):
        self.design.ResultsHeading.side_effect=None
        self.design.ResultsHeading.return_value=False
        with self.assertRaises(MaxsurfCOMError):
            files.export_motions_rao_text.__wrapped__(str(self.path))
        self.design.ExportRAOText.assert_not_called()

    def test_selected_rao_export_uses_single_method(self):
        files.export_motions_rao_text.__wrapped__(str(self.path),2,3)
        self.design.ExportRAOText.assert_called_once()
        self.design.ExportRAOTextMultiCondition.assert_not_called()

    def test_multi_condition_export_does_not_select_one_condition(self):
        r=json.loads(files.export_motions_rao_text.__wrapped__(
            str(self.path),multi_condition=True))
        self.assertEqual(r["requested_selectors"],{})
        self.design.ResultsSpeed.assert_not_called()
        self.design.ExportRAOTextMultiCondition.assert_called_once()

    def test_import_requires_consent_and_actual_nonempty_source(self):
        with self.assertRaises(MaxsurfSafetyError):
            files.import_motions_rao_text.__wrapped__(str(self.path))
        self.path.write_bytes(b"")
        with self.assertRaises(MaxsurfValidationError):
            files.import_motions_rao_text.__wrapped__(str(self.path),confirm_replace=True)
        self.design.ImportRAOText.assert_not_called()

    def test_import_uses_exact_native_method_without_running_solver(self):
        self.path.write_text("native RAO source")
        r=json.loads(files.import_motions_rao_text.__wrapped__(
            str(self.path),multi_condition=True,confirm_replace=True))
        self.design.ImportRAOTextMultiCondition.assert_called_once_with(str(self.path))
        self.assertFalse(r["import_completeness_verified"])
        self.assertFalse(r["analysis_run"])
        files.com.session_for("motions").refresh_design_identity.assert_called_once()

    def test_export_noop_and_header_only_are_typed_failures(self):
        self.design.ExportRAOText.side_effect=lambda p:None
        with self.assertRaises(MaxsurfCOMError):
            files.export_motions_rao_text.__wrapped__(str(self.path))
        self.design.ExportRAOText.side_effect=lambda p:Path(p).write_text("Omega\tHeave\n")
        with self.assertRaises(MaxsurfCOMError):
            files.export_motions_rao_text.__wrapped__(str(self.path))

    def test_unchanged_overwrite_cannot_claim_attributable_export(self):
        self.path.write_text("Omega\tHeave\n0.2\t1.0\n")
        self.design.ExportRAOText.side_effect=lambda p:None
        with self.assertRaises(MaxsurfCOMError):
            files.export_motions_rao_text.__wrapped__(str(self.path),overwrite=True)
        self.assertEqual(self.path.read_text(), "Omega\tHeave\n0.2\t1.0\n")

    def test_deterministic_reexport_can_replace_identical_content(self):
        # Given: the existing output equals a legitimate deterministic export.
        self.path.write_text("Omega\tHeave\n0.2\t1.0\n")

        # When: native writes identical content to a fresh staging target.
        result = json.loads(files.export_motions_rao_text.__wrapped__(
            str(self.path), overwrite=True))

        # Then: the new native output is attributable despite identical bytes.
        self.assertTrue(result["file_verified"])
        self.assertEqual(self.path.read_text(), "Omega\tHeave\n0.2\t1.0\n")
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_native_failure_retains_typed_boundary(self):
        self.path.write_text("native input")
        self.design.ImportRAOText.side_effect=RuntimeError("native failure")
        with self.assertRaises(MaxsurfCOMError):
            files.import_motions_rao_text.__wrapped__(str(self.path),confirm_replace=True)
        files.com.session_for("motions").refresh_design_identity.assert_called_once()


if __name__ == "__main__":
    unittest.main()
