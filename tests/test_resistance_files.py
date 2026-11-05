import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from maxsurf_mcp import resistance_files as files
from maxsurf_mcp.errors import MaxsurfCOMError, MaxsurfSafetyError, MaxsurfValidationError
from tests import test_resistance


class FilesTests(unittest.TestCase):
    def setUp(self):
        self.app = test_resistance.ResistanceTests().app(initialized=True, loaded=True)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "data.native"
        for member, value in (
            ("connect", self.app), ("session_for", None)
        ):
            patcher = patch.object(files.com, member, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_exports_use_exact_single_argument_methods(self):
        for tool, member in (
            (files.save_resistance_measurements, "MeasurementsSaveAs"),
            (files.export_resistance_free_surface, "FreeSurfaceExport")
        ):
            def save(path):
                self.path.write_bytes(member.encode())
                self.app.Design.MeasurementsPath = path
            native = Mock(side_effect=save)
            setattr(self.app.Design, member, native)
            with patch.object(files.workspace, "resolve_target", return_value=self.path):
                result = json.loads(tool(str(self.path), overwrite=True))
            native.assert_called_once_with(str(self.path))
            self.assertTrue(result["output_verified"])
            self.assertFalse(result["round_trip_verified"])

    def test_imports_use_exact_methods_and_report_unverified_fidelity(self):
        self.path.write_bytes(b"native")
        for tool, member in (
            (files.open_resistance_measurements, "MeasurementsOpen"),
            (files.import_resistance_free_surface, "FreeSurfaceImport")
        ):
            def load(path):
                self.app.Design.MeasurementsPath = path
            native = Mock(side_effect=load)
            setattr(self.app.Design, member, native)
            with patch.object(files.workspace, "resolve_existing", return_value=self.path):
                result = json.loads(tool(str(self.path), confirm_replace=True))
            native.assert_called_once_with(str(self.path))
            self.assertFalse(result["import_completeness_verified"])

    def test_import_consent_and_boolean_validation(self):
        with self.assertRaises(MaxsurfSafetyError):
            files.open_resistance_measurements(str(self.path))
        with self.assertRaises(MaxsurfValidationError):
            files.export_resistance_free_surface(str(self.path), overwrite=1)

    def test_noop_export_and_measurement_path_mismatch_fail(self):
        self.path.write_bytes(b"old")
        self.app.Design.FreeSurfaceExport = Mock()
        with patch.object(files.workspace, "resolve_target", return_value=self.path):
            with self.assertRaises(MaxsurfCOMError):
                files.export_resistance_free_surface(str(self.path), True)
        self.app.Design.MeasurementsOpen = Mock()
        self.app.Design.MeasurementsPath = str(self.path.parent / "wrong")
        with patch.object(files.workspace, "resolve_existing", return_value=self.path):
            with self.assertRaises(MaxsurfCOMError):
                files.open_resistance_measurements(str(self.path), True)

    def test_workspace_rejects_relative_path_before_native_connection(self):
        with patch.object(files.com, "connect") as connect:
            with self.assertRaises(MaxsurfSafetyError):
                files.save_resistance_measurements("relative.native")
            connect.assert_not_called()
