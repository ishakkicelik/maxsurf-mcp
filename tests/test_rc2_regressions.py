"""Acceptance guards reproduced without licensed applications."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from maxsurf_mcp import com, stability, surfaces, motions
from maxsurf_mcp.errors import MaxsurfAnalysisError, MaxsurfCOMError, MaxsurfSafetyError
from .test_surfaces import SurfaceTestCase, _FakeSurface, _FakeSurfaces


class GZValidationTests(unittest.TestCase):
    def row(self, **changes):
        values = dict(Converged=True, Heel=0, GZ=0, Displacement=2000000, DraftAP=4, DraftFP=4)
        return {name: stability._scalar(NS(**(values | changes)), name) for name in values}

    def test_zero_gz_is_not_itself_failure(self):
        stability._validate_gz_rows([self.row()])

    def test_zero_displacement_is_failure_even_if_converged(self):
        with self.assertRaises(MaxsurfAnalysisError) as caught:
            stability._validate_gz_rows([self.row(), self.row(Heel=60, Displacement=0)])
        self.assertEqual(caught.exception.details["invalid_rows"][0]["row"], 2)
        self.assertEqual(len(caught.exception.details["gz_table"]), 2)

    def test_unconverged_row_is_failure(self):
        with self.assertRaises(MaxsurfAnalysisError):
            stability._validate_gz_rows([self.row(Converged=False)])

    def test_unreadable_convergence_is_failure(self):
        with self.assertRaises(MaxsurfAnalysisError):
            stability._validate_gz_rows([self.row(Converged=None)])

    def test_nonfinite_gz_is_failure(self):
        with self.assertRaises(MaxsurfAnalysisError):
            stability._validate_gz_rows([self.row(GZ=float("nan"))])

    def test_empty_table_is_failure(self):
        with self.assertRaises(MaxsurfAnalysisError):
            stability._validate_gz_rows([])


class BondReadbackTests(SurfaceTestCase):
    def test_native_noop_bond_is_not_success(self):
        collection = self.patch(_FakeSurfaces([_FakeSurface("a"), _FakeSurface("b")]))
        collection.Bond = Mock()
        with self.assertRaises(MaxsurfCOMError):
            surfaces.bond_surfaces(1, "msEdgeRight", 2, "msEdgeLeft")

    def test_one_sided_bond_is_not_success(self):
        collection = self.patch(_FakeSurfaces([_FakeSurface("a"), _FakeSurface("b")]))
        collection.Bond = lambda *a: collection.items[0].bonds.update({3: ("b", 2)})
        with self.assertRaises(MaxsurfCOMError):
            surfaces.bond_surfaces(1, "msEdgeRight", 2, "msEdgeLeft")



class StabilitySaveTests(unittest.TestCase):
    def test_noop_overwrite_preserves_old_rooms_and_refreshes_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "rooms.htk"
            target.write_bytes(b"old rooms")
            design = NS(Tanks=NS(Count=0), Compartments=NS(Count=0), SaveAsRooms=Mock())
            owner = Mock()
            with patch.dict("os.environ", {"MAXSURF_MCP_WORKSPACE_ROOTS": tmp}), \
                    patch.object(com, "connect", return_value=NS(Design=design)), \
                    patch.object(com, "session_for", return_value=owner):
                with self.assertRaises(MaxsurfCOMError):
                    stability.save_stability_rooms.__wrapped__(str(target), overwrite=True)
            self.assertEqual(target.read_bytes(), b"old rooms")
            self.assertEqual(list(Path(tmp).iterdir()), [target])
            owner.refresh_design_identity.assert_called_once()

    def test_identical_rooms_write_uses_final_path_and_refreshes_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "rooms.htk"
            target.write_bytes(b"native rooms")
            os.utime(target, ns=(1000000000, 1000000000))
            def save(path, overwrite):
                self.assertEqual(Path(path), target)
                self.assertTrue(overwrite)
                Path(path).write_bytes(b"native rooms")
                os.utime(path, ns=(2000000000, 2000000000))
            design = NS(Tanks=NS(Count=0), Compartments=NS(Count=0),
                        SaveAsRooms=Mock(side_effect=save))
            owner = Mock()
            with patch.dict("os.environ", {"MAXSURF_MCP_WORKSPACE_ROOTS": tmp}), \
                    patch.object(com, "connect", return_value=NS(Design=design)), \
                    patch.object(com, "session_for", return_value=owner):
                result = json.loads(stability.save_stability_rooms.__wrapped__(str(target), True))
            self.assertTrue(result["file_verified"])
            self.assertEqual(target.read_bytes(), b"native rooms")
            owner.refresh_design_identity.assert_called_once()

    def test_file_checked_and_identity_refreshed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "rooms.htk"
            design = NS(Tanks=NS(Count=2), Compartments=NS(Count=3))
            design.SaveAsRooms = lambda p, overwrite: Path(p).write_bytes(b"fake native file")
            owner = Mock()
            with patch.dict("os.environ", {"MAXSURF_MCP_WORKSPACE_ROOTS": tmp}), patch.object(com, "connect", return_value=NS(Design=design)), patch.object(com, "session_for", return_value=owner):
                result = json.loads(stability.save_stability_rooms.__wrapped__(str(target)))
                self.assertTrue(result["file_verified"])
                self.assertFalse(result["reopen_verified"])
                owner.refresh_design_identity.assert_called_once()
                with self.assertRaises(MaxsurfSafetyError):
                    stability.save_stability_rooms.__wrapped__(str(target))

    def test_no_file_is_failure_and_identity_still_refreshed(self):
        with tempfile.TemporaryDirectory() as tmp:
            design = NS(Tanks=NS(Count=0), Compartments=NS(Count=0), SaveAsRooms=lambda *a: None)
            owner = Mock()
            with patch.dict("os.environ", {"MAXSURF_MCP_WORKSPACE_ROOTS": tmp}), patch.object(com, "connect", return_value=NS(Design=design)), patch.object(com, "session_for", return_value=owner):
                with self.assertRaises(MaxsurfCOMError):
                    stability.save_stability_rooms.__wrapped__(str(Path(tmp) / "missing.htk"))
                owner.refresh_design_identity.assert_called_once()

    def test_room_only_backup_cannot_authorize_loadcase_replacement(self):
        for scope in ("loadcases", "keypoints"):
            with self.subTest(scope=scope), self.assertRaises(MaxsurfSafetyError):
                stability._before_replace(NS(), True, True, "rooms.htk", scope=scope)

    def test_native_save_exception_still_refreshes_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "rooms.htk"
            target.write_bytes(b"old rooms")
            design = NS(Tanks=NS(Count=0), Compartments=NS(Count=0),
                        SaveAsRooms=Mock(side_effect=RuntimeError("native save failed")))
            owner = Mock()
            with patch.dict("os.environ", {"MAXSURF_MCP_WORKSPACE_ROOTS": tmp}), \
                    patch.object(com, "connect", return_value=NS(Design=design)), \
                    patch.object(com, "session_for", return_value=owner), \
                    self.assertRaises(RuntimeError):
                stability.save_stability_rooms.__wrapped__(str(target), True)
            owner.refresh_design_identity.assert_called_once()
            self.assertEqual(target.read_bytes(), b"old rooms")


class MotionsExportTests(unittest.TestCase):
    def test_overwrite_requires_fresh_valid_native_output(self):
        data = b"Heave\t0.0\tm\r\n"
        for output, error in ((None, MaxsurfCOMError), (b"", MaxsurfCOMError),
                              (b"Heave\tunits\r\n", MaxsurfAnalysisError),
                              (data, None)):
            with self.subTest(output=output), tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "out.txt"
                target.write_bytes(data)
                def export(table, path):
                    self.assertFalse(Path(path).exists())
                    if output is not None:
                        Path(path).write_bytes(output)
                design = NS(ExportResults=Mock(side_effect=export))
                with patch.dict("os.environ", {"MAXSURF_MCP_WORKSPACE_ROOTS": tmp}), \
                        patch.object(com, "connect"), \
                        patch.object(motions, "_require_ready", return_value=design), \
                        patch("maxsurf_mcp.constants.discover", return_value={"constants": {"skTabGlobalRAOs": 1}}):
                    if error:
                        with self.assertRaises(error):
                            motions.export_motions_table.__wrapped__(str(target), "skTabGlobalRAOs", True)
                    else:
                        result = json.loads(motions.export_motions_table.__wrapped__(
                            str(target), "skTabGlobalRAOs", True))
                        self.assertTrue(result["file_verified"])
                self.assertEqual(target.read_bytes(), data)
                self.assertEqual(list(Path(tmp).iterdir()), [target])
                table, staged = design.ExportResults.call_args.args
                self.assertEqual(table, 1)
                self.assertEqual(Path(staged).parent, target.parent)
                self.assertEqual(Path(staged).suffix, ".txt")
                self.assertNotEqual(Path(staged), target)

    def test_summary_metadata_and_native_failure_preserve_previous_output(self):
        for raises in (False, True):
            with self.subTest(raises=raises), tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "summary.txt"
                target.write_bytes(b"previous summary")
                def export(table, path):
                    Path(path).write_bytes(b"Speed\t10\tknots\r\n")
                    if raises:
                        raise RuntimeError("native export failed")
                design = NS(ExportResults=Mock(side_effect=export))
                with patch.dict("os.environ", {"MAXSURF_MCP_WORKSPACE_ROOTS": tmp}), \
                        patch.object(com, "connect"), \
                        patch.object(motions, "_require_ready", return_value=design), \
                        patch("maxsurf_mcp.constants.discover", return_value={"constants": {"skTabSummary": 1}}), \
                        self.assertRaises(RuntimeError if raises else MaxsurfAnalysisError):
                    motions.export_motions_table.__wrapped__(str(target), "skTabSummary", True)
                self.assertEqual(target.read_bytes(), b"previous summary")
                self.assertEqual(list(Path(tmp).iterdir()), [target])

    def test_invalid_extension_refused_before_com(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"MAXSURF_MCP_WORKSPACE_ROOTS": tmp}), patch.object(com, "connect") as connect:
            with self.assertRaises(MaxsurfSafetyError):
                motions.export_motions_table.__wrapped__(str(Path(tmp) / "out.exe"), "skTabSummary")
            connect.assert_not_called()

    def test_native_export_is_not_reformatted(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.txt"
            data = b"1\tHeave motion\t1\tm^2\t1\tm\t2\tm\r\n2\tRoll motion\t0\tdeg^2\t0\tdeg\t0\tdeg\r\n3\tPitch motion\t1\tdeg^2\t1\tdeg\t2\tdeg\r\n"
            design = NS(ExportResults=lambda tab, p: Path(p).write_bytes(data))
            with patch.dict("os.environ", {"MAXSURF_MCP_WORKSPACE_ROOTS": tmp}), patch.object(com, "connect"), patch.object(motions, "_require_ready", return_value=design), patch("maxsurf_mcp.constants.discover", return_value={"constants": {"skTabSummary": 1}}):
                result = json.loads(motions.export_motions_table.__wrapped__(str(target), "skTabSummary"))
                self.assertTrue(result["file_verified"])
                self.assertEqual(result["bytes"], len(data))
                self.assertEqual(target.read_bytes(), data)

    def test_headers_only_native_table_is_not_an_analysis_result(self):
        with self.assertRaises(MaxsurfAnalysisError):
            motions._require_numeric_table(b"Item\tm0\tRMS\tunits\r\n" * 18)

    def test_zero_native_value_is_readable(self):
        motions._require_numeric_table(b"Heave\t0.0\tm\r\n")
