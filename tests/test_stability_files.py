"""Filesystem and state boundaries for Stability native companion workflows."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from maxsurf_mcp import com, stability_files as files
from maxsurf_mcp import guard
from maxsurf_mcp.errors import MaxsurfCOMError, MaxsurfSafetyError, MaxsurfValidationError
from tests.test_stability_controls import Points


class Cases(Points):
    __call__ = Points.Item


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"MAXSURF_MCP_WORKSPACE_ROOTS": self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)

    def design(self):
        design = NS(Tanks=Points(), Compartments=Points(), DamageCases=Points([NS(Name="Intact")]),
                    KeyPoints=Points(), LoadCases=Cases([NS(IsDefined=False) for _ in range(8)]))
        design.OpenRooms = Mock(side_effect=lambda p: design.Compartments.rows.append(NS(Name="room")))
        design.OpenCargo = Mock(side_effect=lambda p: design.LoadCases.rows.append(NS(IsDefined=True, Name="cargo")))
        design.OpenDamageCases = Mock(side_effect=lambda p: design.DamageCases.rows.append(NS(Name="damage")))
        return design

    def test_export_calls_two_argument_contract_and_checks_file(self):
        design = NS(SaveResultsTable=Mock(side_effect=lambda table, p: Path(p).write_bytes(b"native\tdata\n")))
        with patch.object(com, "connect", return_value=NS(Design=design)), \
                patch.object(files, "_constant", return_value=31):
            result = json.loads(files.export_stability_results_table.__wrapped__(
                "hmResultCriteria", str(self.root / "criteria.txt")))
        design.SaveResultsTable.assert_called_once()
        args = design.SaveResultsTable.call_args.args
        self.assertEqual(args[0], 31)
        self.assertEqual(Path(args[1]).parent, self.root)
        self.assertEqual(Path(args[1]).suffix, ".txt")
        self.assertFalse(Path(args[1]).exists())
        self.assertTrue((self.root / "criteria.txt").is_file())
        self.assertTrue(result["file_verified"])
        self.assertEqual(result["bytes"], 12)
        self.assertFalse(result["analysis_run"])

    def test_empty_export_and_absent_export_fail(self):
        for create in (False, True):
            design = NS(SaveResultsTable=Mock(side_effect=lambda t, p: Path(p).touch() if create else None))
            with patch.object(com, "connect", return_value=NS(Design=design)), \
                    patch.object(files, "_constant", return_value=31), self.assertRaises(MaxsurfCOMError):
                files.export_stability_results_table.__wrapped__(
                    "hmResultCriteria", str(self.root / "empty.txt"), overwrite=True)

    def test_noop_export_cannot_use_existing_file_as_proof(self):
        target = self.root / "old.txt"
        target.write_bytes(b"old")
        design = NS(SaveResultsTable=Mock())
        with patch.object(com, "connect", return_value=NS(Design=design)), \
                patch.object(files, "_constant", return_value=31), self.assertRaises(MaxsurfCOMError):
            files.export_stability_results_table.__wrapped__("hmResultCriteria", str(target), True)
        self.assertEqual(target.read_bytes(), b"old")
        self.assertEqual(list(self.root.iterdir()), [target])

    def test_export_unknown_table_bad_suffix_and_overwrite_precede_com(self):
        path = self.root / "old.txt"
        path.write_bytes(b"old")
        for table, target, error in (
            ("unknown", path, MaxsurfValidationError),
            ("hmResultCriteria", self.root / "bad.exe", MaxsurfSafetyError),
            ("hmResultCriteria", path, MaxsurfSafetyError),
        ):
            with patch.object(com, "connect") as connect, self.assertRaises(error):
                files.export_stability_results_table.__wrapped__(table, str(target))
            connect.assert_not_called()
        self.assertEqual(path.read_bytes(), b"old")

    def import_file(self, kind, design, confirm=True):
        suffix = files.COMPANIONS[kind][1]
        path = self.root / ("input" + suffix)
        path.write_bytes(b"native")
        with patch.object(com, "connect", return_value=NS(Design=design)), \
                patch.object(com, "session_for", return_value=NS(refresh_design_identity=Mock())):
            return json.loads(files.open_stability_companion.__wrapped__(kind, str(path), confirm))

    def test_each_input_member_takes_filename_alone_into_empty_state(self):
        for kind, (member, suffix) in files.COMPANIONS.items():
            design = self.design()
            result = self.import_file(kind, design)
            getattr(design, member).assert_called_once_with(str(self.root / ("input" + suffix)))
            self.assertTrue(result["import_observed"])
            self.assertFalse(result["source_equivalence_verified"])

    def test_refusal_of_existing_affected_or_dependent_state(self):
        for kind, field in (("rooms", "Compartments"), ("rooms", "KeyPoints"),
                            ("rooms", "LoadCases"), ("cargo", "LoadCases"),
                            ("damage", "DamageCases")):
            design = self.design()
            getattr(design, field).rows.append(NS(Name="existing", IsDefined=True))
            with self.subTest(kind=kind, field=field), self.assertRaises(MaxsurfSafetyError):
                self.import_file(kind, design)
            getattr(design, files.COMPANIONS[kind][0]).assert_not_called()

    def test_consent_and_suffix_and_empty_input_precede_connection(self):
        path = self.root / "empty.htk"
        path.touch()
        for kind, target, confirm, error in (
            ("rooms", path, False, MaxsurfSafetyError),
            ("rooms", path, True, MaxsurfValidationError),
            ("cargo", path, True, MaxsurfSafetyError),
            ("unknown", path, True, MaxsurfValidationError),
        ):
            with patch.object(com, "connect") as connect, self.assertRaises(error):
                files.open_stability_companion.__wrapped__(kind, str(target), confirm)
            connect.assert_not_called()

    def test_noop_import_is_failure_and_identity_is_refreshed(self):
        design = self.design()
        design.OpenRooms.side_effect = None
        path = self.root / "input.htk"
        path.write_bytes(b"native")
        refresh = Mock()
        with patch.object(com, "connect", return_value=NS(Design=design)), \
                patch.object(com, "session_for", return_value=NS(refresh_design_identity=refresh)), \
                self.assertRaises(MaxsurfCOMError):
            files.open_stability_companion.__wrapped__("rooms", str(path), True)
        refresh.assert_called_once()

    def test_guard_normalizes_native_errors_for_both_file_tools(self):
        source = self.root / "input.htk"
        source.write_bytes(b"native")
        calls = (
            (files.export_stability_results_table,
             ("hmResultCriteria", str(self.root / "output.txt"))),
            (files.open_stability_companion, ("rooms", str(source), True)),
        )
        for tool, args in calls:
            with self.subTest(tool=tool.__name__), \
                    patch.object(guard.audit, "get_log", return_value=Mock()), \
                    patch.object(guard.comthread, "run", side_effect=lambda fn, **kwargs: fn()), \
                    patch.object(com, "connect", side_effect=RuntimeError("native unavailable")), \
                    self.assertRaises(MaxsurfCOMError):
                tool(*args)
