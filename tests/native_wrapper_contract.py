"""Explicit installed-Maxsurf integration checks, separate from offline discovery."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import pythoncom
import win32api
from win32com.client import build

from maxsurf_mcp import native_automation as na
from maxsurf_mcp.errors import MaxsurfValidationError
from tests.test_native_automation import Dispatch


TYPELIB_OVERRIDE = "MAXSURF_MCP_MODELER_TYPELIB"


def modeler_typelib_path():
    override = os.environ.get(TYPELIB_OVERRIDE)
    try:
        path = override if override is not None else pythoncom.QueryPathOfRegTypeLib(
            pythoncom.MakeIID("{E1F5C655-FE67-431B-AF09-CBCBD9E19B4D}"), 1, 1, 0)
        selected = Path(path.rstrip("\x00")).resolve()
        if not selected.is_file():
            raise FileNotFoundError(selected)
        return selected
    except Exception as exc:
        raise RuntimeError(
            f"Modeler typelib unavailable; register Modeler or set {TYPELIB_OVERRIDE}: {exc}") from exc


class TypelibResolutionTests(unittest.TestCase):
    def test_registered_relocated_path_and_override(self):
        # This file stands in for a registered path without any installation assumption.
        selected = Path(__file__).resolve()
        with patch.dict(os.environ, {}, clear=True), patch.object(
                pythoncom, "QueryPathOfRegTypeLib", return_value=str(selected) + "\x00") as query:
            self.assertEqual(modeler_typelib_path(), selected)
            query.assert_called_once()
        with patch.dict(os.environ, {TYPELIB_OVERRIDE: str(selected)}), patch.object(
                pythoncom, "QueryPathOfRegTypeLib") as query:
            self.assertEqual(modeler_typelib_path(), selected)
            query.assert_not_called()

    def test_missing_installation_is_a_failure_not_skip(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(
                pythoncom, "QueryPathOfRegTypeLib", side_effect=RuntimeError("unregistered")):
            with self.assertRaisesRegex(RuntimeError, TYPELIB_OVERRIDE):
                modeler_typelib_path()


class InstalledWrapperContractTests(unittest.TestCase):
    """Read-only installed typelib + generated wrapper mechanics, no application."""

    @classmethod
    def setUpClass(cls):
        pythoncom.CoInitialize()
        cls.addClassCleanup(pythoncom.CoUninitialize)
        selected = modeler_typelib_path()
        cls.library = pythoncom.LoadTypeLib(str(selected))
        version = win32api.GetFileVersionInfo(str(selected), "\\")
        print(json.dumps({"modeler_typelib": str(selected),
                          "typelib_identity": str(cls.library.GetLibAttr()),
                          "file_version_ms": version["FileVersionMS"],
                          "file_version_ls": version["FileVersionLS"]}))

    def info(self, name):
        for index in range(self.library.GetTypeInfoCount()):
            info = self.library.GetTypeInfo(index)
            if info.GetDocumentation(-1)[0] == name:
                return info
        self.fail(f"installed interface missing: {name}")

    def test_explicit_relocated_typelib_loads_without_default_install_path(self):
        with tempfile.TemporaryDirectory(prefix="relocated Modeler typelib ") as directory:
            relocated = Path(directory) / "Modeler.exe"
            shutil.copy2(modeler_typelib_path(), relocated)
            with patch.dict(os.environ, {TYPELIB_OVERRIDE: str(relocated)}):
                selected = modeler_typelib_path()
                self.assertEqual(selected, relocated.resolve())
                self.assertEqual(pythoncom.LoadTypeLib(str(selected)).GetLibAttr(),
                                 self.library.GetLibAttr())

    def test_native_marshaling_matches_pywin32_generator(self):
        for interface in ("ISurface", "IApplication", "ISurfaces", "ITrimeshs"):
            info = self.info(interface)
            for index in range(info.GetTypeAttr().cFuncs):
                func = info.GetFuncDesc(index)
                for raw in [func.rettype[0]] + [p[0] for p in func.args]:
                    desc = na.native_types.descriptor(info, raw)
                    if desc.get("supported"):
                        with self.subTest(interface=interface, dispid=func.memid, raw=raw):
                            if raw == (26, (26, 16)):
                                with self.assertRaises(MaxsurfValidationError):
                                    na._marshal_type(desc)
                            else:
                                self.assertEqual(na._marshal_type(desc), build._ResolveType(raw, info)[0])

    def test_actual_generated_out_and_default_wrapper_argument_behavior(self):
        for interface, name, arguments, expected in (
                ("ISurface", "GetControlPoint", [1, 2],
                 (1, 2, pythoncom.Missing, pythoncom.Missing, pythoncom.Missing)),
                ("IApplication", "ResetViewZoom", [], (100,)),
                ("ISurface", "Move", [1.0, 2.0, 3.0], (1.0, 2.0, 3.0, False, 0))):
            info = self.info(interface)
            item = build.DispatchItem(info, info.GetTypeAttr())
            entry = item.mapFuncs[name]
            lines = item.MakeFuncMethod(entry, name, bMakeClass=False)
            namespace = {"pythoncom": pythoncom, "LCID": 0}
            exec("\n".join(lines), namespace)
            sink = Dispatch(info)
            sink._ApplyTypes_ = lambda dispid, kind, ret, args, _name, _clsid, *values: (
                sink.InvokeTypes(dispid, 0, kind, ret, args, *values))
            namespace[name](sink, *arguments)
            with self.subTest(member=name):
                self.assertEqual(sink.calls[-1][5:], expected)


if __name__ == "__main__":
    unittest.main()
