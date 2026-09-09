from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from maxsurf_mcp import comcache


class COMCacheTests(unittest.TestCase):
    def test_default_cache_is_inside_runtime(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(comcache.GEN_PY_DIR_VAR, None)
            self.assertEqual(
                comcache.cache_dir(),
                (comcache.config.runtime_root() / "gen_py").resolve(),
            )

    def test_configure_updates_win32com_and_loaded_gen_package(self):
        with tempfile.TemporaryDirectory() as directory:
            win32com = SimpleNamespace(__gen_path__="old")
            gen_py = SimpleNamespace(__path__=["old"])
            with patch.dict(os.environ, {
                comcache.GEN_PY_DIR_VAR: directory,
            }), patch.dict(sys.modules, {
                "win32com": win32com,
                "win32com.gen_py": gen_py,
            }):
                report = comcache.configure()

            expected = str(Path(directory).resolve())
            self.assertEqual(win32com.__gen_path__, expected)
            self.assertEqual(gen_py.__path__, [expected])
            self.assertEqual(report["path"], expected)
            self.assertTrue(report["isolated"])


if __name__ == "__main__":
    unittest.main()
