from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pythoncom

from maxsurf_mcp import modeler_files as files
from maxsurf_mcp.errors import MaxsurfCOMError, MaxsurfSafetyError, MaxsurfValidationError
from .support import IsolatedEnvTestCase


class PrecisionInfo:
    def GetDocumentation(self, memid):
        return ("msSurfacePrecision", "", 0, "")

    def GetTypeAttr(self):
        return SimpleNamespace(cVars=5)

    def GetVarDesc(self, index):
        # Deliberately different numbers: prove there is no hard-coded enum value.
        return (index, index + 21)

    def GetNames(self, memid):
        return (files.PRECISIONS[memid],)


class DesignInfo:
    def GetTypeAttr(self):
        return SimpleNamespace(cFuncs=1)

    def GetFuncDesc(self, index):
        return SimpleNamespace(memid=7, args=((8, 1, None),
                                             ((pythoncom.VT_USERDEFINED, 777), 1, None)))

    def GetNames(self, memid):
        return ("ExportPDstrip", "sFileName", "Precision")

    def GetRefTypeInfo(self, href):
        assert href == 777
        return PrecisionInfo()


class FileToolsTests(IsolatedEnvTestCase):
    def setUp(self):
        super().setUp()
        self.calls = []
        self.design = SimpleNamespace(
            Path="existing.msd", Name="existing", Surfaces=SimpleNamespace(Count=2),
            Trimeshs=SimpleNamespace(Count=3),
            _oleobj_=SimpleNamespace(GetTypeInfo=lambda: DesignInfo()))
        self.managed = mock.Mock()
        for name, (member, _) in files.EXPORTS.items():
            def export(path, _member=member):
                self.calls.append((_member, (path,)))
                Path(path).write_bytes(b"native-output")
            def export_option(path, option, _member=member):
                self.calls.append((_member, (path, option)))
                Path(path).write_bytes(b"native-output")
            setattr(self.design, member, export_option if name in
                    ("dxf", "pdstrip", "moses") else export)
        for name, (member, _) in files.IMPORTS.items():
            def import_file(path, _member=member):
                self.calls.append((_member, (path,)))
                self.design.Trimeshs.Count += 1
            setattr(self.design, member, import_file)
        for patcher in (
            mock.patch.object(files.com, "connect", return_value=SimpleNamespace(Design=self.design)),
            mock.patch.object(files.com, "session_for", return_value=self.managed),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def target(self, format):
        suffixes = files.EXPORTS[format][1]
        return self.workspace_root / ("output" + (suffixes[0] if suffixes else ".dat"))

    def test_every_export_exact_member_and_arity(self):
        for format, (member, _) in files.EXPORTS.items():
            with self.subTest(format=format):
                path = self.target(format)
                if path.exists():
                    path.unlink()
                options = ({"view": "profile"} if format == "dxf" else
                           {"precision": "msSPHigh"} if format == "pdstrip" else {})
                result = json.loads(files.export_modeler_file(str(path), format, **options))
                expected = (str(path),)
                if format == "dxf":
                    expected += (3,)
                elif format == "pdstrip":
                    expected += (24,)
                elif format == "moses":
                    expected += (False,)
                self.assertEqual(self.calls[-1], (member, expected))
                self.assertEqual(result["file"]["bytes"], 13)
                self.assertEqual(len(result["file"]["sha256"]), 64)
                self.assertTrue(result["output_verified"])
                self.assertFalse(result["fidelity_verified"])

    def test_all_dxf_views(self):
        for view, number in files.VIEWS.items():
            files.export_modeler_file(str(self.target("dxf")), "dxf",
                                      overwrite=True, view=view)
            self.assertEqual(self.calls[-1][1][-1], number)

    def test_every_import_observes_counts_without_fidelity_claim(self):
        for format, (member, suffixes) in files.IMPORTS.items():
            with self.subTest(format=format):
                source = self.workspace_file("source" + (suffixes[0] if suffixes else ".dat"))
                result = json.loads(files.import_modeler_file(
                    str(source), format, confirm_replace=True,
                    acknowledge_unverified_semantics=True))
                self.assertEqual(self.calls[-1], (member, (str(source),)))
                self.assertEqual(result["after"]["trimesh_count"],
                                 result["before"]["trimesh_count"] + 1)
                self.assertFalse(result["semantics_verified"])
                self.assertFalse(result["import_completeness_verified"])
                self.assertFalse(result["fidelity_verified"])

    def test_import_noop_is_observation_not_verified_import(self):
        self.design.Import3DM = lambda path: None
        source = self.workspace_file("source.3dm")
        result = json.loads(files.import_modeler_file(
            str(source), "3dm", True, True))
        self.assertEqual(result["before"], result["after"])
        self.assertFalse(result["import_completeness_verified"])

    def test_import_requires_both_consents(self):
        for consent, acknowledgment in ((False, False), (True, False), (False, True)):
            with self.assertRaises(MaxsurfSafetyError):
                files.import_modeler_file("missing.3dm", "3dm", consent, acknowledgment)
        self.assertEqual(self.calls, [])

    def test_strict_inputs_and_closed_mapping(self):
        for format in ("iges", "dgn", "dxf", "Import3DM", "", None):
            with self.assertRaises(MaxsurfValidationError):
                files.import_modeler_file("missing", format, True, True)
        for options in ({"overwrite": 1}, {"view": 2}, {"precision": None},
                        {"view": "plan"}, {"precision": "msSPHigh"}):
            with self.assertRaises(MaxsurfValidationError):
                files.export_modeler_file(str(self.target("3dm")), "3dm", **options)
        for options in ({}, {"precision": 4}, {"precision": "msSPInvented"}):
            with self.assertRaises(MaxsurfValidationError):
                files.export_modeler_file(str(self.target("pdstrip")), "pdstrip", **options)
        for value in (1, "true", None):
            with self.assertRaises(MaxsurfValidationError):
                files.import_modeler_file("missing", "3dm", value, True)
        self.assertEqual(self.calls, [])

    def test_workspace_suffix_and_overwrite_before_native_call(self):
        target = self.workspace_file("existing.3dm")
        for path in (str(target), "relative.3dm", str(self.workspace_root / "wrong.exe"),
                     str(self.workspace_root.parent / "outside.3dm")):
            with self.assertRaises(MaxsurfSafetyError):
                files.export_modeler_file(path, "3dm")
        self.assertEqual(self.calls, [])
        files.export_modeler_file(str(target), "3dm", overwrite=True)
        self.assertEqual(target.read_bytes(), b"native-output")

    def test_missing_empty_and_stale_output_refused(self):
        target = self.target("3dm")
        self.design.Export3DM = lambda path: None
        with self.assertRaises(MaxsurfCOMError):
            files.export_modeler_file(str(target), "3dm")
        target.write_bytes(b"old")
        with self.assertRaises(MaxsurfCOMError):
            files.export_modeler_file(str(target), "3dm", overwrite=True)
        self.design.Export3DM = lambda path: Path(path).write_bytes(b"")
        with self.assertRaises(MaxsurfCOMError):
            files.export_modeler_file(str(target), "3dm", overwrite=True)

    def test_empty_source_and_unresolved_enum_refused(self):
        source = self.workspace_file("empty.3dm", "")
        with self.assertRaises(MaxsurfValidationError):
            files.import_modeler_file(str(source), "3dm", True, True)
        ti = DesignInfo()
        ti.GetRefTypeInfo = lambda href: SimpleNamespace(
            GetDocumentation=lambda memid: ("wrongEnum", "", 0, ""))
        self.design._oleobj_.GetTypeInfo = lambda: ti
        with self.assertRaises(MaxsurfCOMError):
            files.export_modeler_file(str(self.target("pdstrip")), "pdstrip",
                                      precision="msSPHigh")
        self.assertEqual(self.calls, [])

    def test_native_failure_typed_and_session_refreshed(self):
        def fail(path):
            raise OSError("native failure")
        self.design.Import3DM = fail
        source = self.workspace_file("source.3dm")
        with self.assertRaises(MaxsurfCOMError):
            files.import_modeler_file(str(source), "3dm", True, True)
        self.managed.refresh_design_identity.assert_called_once()
        self.design.Export3DM = fail
        with self.assertRaises(MaxsurfCOMError):
            files.export_modeler_file(str(self.target("3dm")), "3dm")
        self.assertEqual(self.managed.refresh_design_identity.call_count, 2)

    def test_registration_uses_guarded_tools(self):
        mcp = mock.Mock()
        self.assertEqual(files.register_tools(mcp),
                         ["export_modeler_file", "import_modeler_file"])
        self.assertEqual(mcp.tool.call_count, 2)
