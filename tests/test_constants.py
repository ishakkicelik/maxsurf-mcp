from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

import server
from maxsurf_mcp import constants as comconstants


class _GuardedApplication:
    """Fail if discovery attempts any instance-level attribute access."""

    __module__ = "win32com.gen_py.TESTLIB.IApplication"

    def __getattribute__(self, name: str):
        raise AssertionError(f"unexpected COM attribute access: {name}")


class ConstantsDiscoveryTests(unittest.TestCase):
    def test_generated_module_constants_are_filtered_case_insensitively(self):
        root = types.ModuleType("win32com.gen_py.TESTLIB")
        child = types.ModuleType("win32com.gen_py.TESTLIB.IApplication")
        constant_bag = type(
            "constants",
            (),
            {
                "msSymmetric": 1,
                "msNoSymmetry": 0,
                "msShapeBox": 7,
            },
        )
        root.constants = constant_bag
        root.MajorVersion = 25  # makepy metadata is not an enum constant

        modules = {
            root.__name__: root,
            child.__name__: child,
        }
        with mock.patch.dict(sys.modules, modules), mock.patch.object(
            comconstants, "gencache", None
        ):
            result = comconstants.discover(_GuardedApplication(), "SyM")

        self.assertEqual(result["source"], "generated_makepy")
        self.assertEqual(
            result["constants"], {"msNoSymmetry": 0, "msSymmetric": 1}
        )
        self.assertNotIn("MajorVersion", result["constants"])

    def test_global_win32com_constants_are_used_as_fallback(self):
        fallback = types.SimpleNamespace(
            __dicts__=[
                {"msHullShape": 12, "msSurfaceShape": 13},
                {"msHullShape": 99},
            ]
        )
        app_type = type("Application", (), {"__module__": "missing.wrapper"})

        with mock.patch.object(comconstants, "gencache", None), mock.patch.object(
            comconstants, "client_constants", fallback
        ):
            result = comconstants.discover(app_type(), "hull")

        self.assertEqual(result["source"], "win32com.client.constants")
        self.assertEqual(result["constants"], {"msHullShape": 12})


class ToolRegistrationTests(unittest.TestCase):
    def test_com_constants_is_registered(self):
        names = {tool.name for tool in server.mcp._tool_manager.list_tools()}
        self.assertIn("com_constants", names)


if __name__ == "__main__":
    unittest.main()
