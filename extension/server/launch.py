"""Isolated MCPB entry point. Does not install software or start Maxsurf."""
from __future__ import annotations
import os
from pathlib import Path
import runpy
import site
import struct
import sys

if sys.platform != "win32" or sys.version_info[:2] != (3, 12) or struct.calcsize("P") != 8:
    raise SystemExit("Maxsurf MCP bundle requires Windows x64 and CPython 3.12 x64.")

HERE = Path(__file__).resolve().parent
LIB = HERE / "lib"
if not (LIB / "mcp").is_dir():
    raise SystemExit("Incomplete MCPB: bundled dependencies are missing. Rebuild the extension.")

# -I -S prevents accidental imports from user/site environments. Only the
# reviewed bundle's .pth files (needed by pywin32) are processed here.
site.addsitedir(str(LIB))
sys.path.insert(0, str(HERE))
runtime = os.environ.get("MAXSURF_MCP_RUNTIME_DIR", "").strip()
if not runtime:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise SystemExit("LOCALAPPDATA is unavailable; configure a writable runtime directory.")
    runtime = str(Path(local) / "MaxsurfMCP" / "runtime")
os.environ["MAXSURF_MCP_RUNTIME_DIR"] = runtime
workspace = os.environ.get("MAXSURF_MCP_WORKSPACE_ROOTS", "").strip()
os.environ["MAXSURF_MCP_WORKSPACE_ROOTS"] = (
    workspace + os.pathsep + runtime if workspace else runtime)

if "--check" in sys.argv:
    import json
    import pythoncom
    import win32api
    import comtypes
    import pywinauto
    import PIL
    import server
    print(json.dumps({"python": sys.version.split()[0], "pythoncom": pythoncom.__file__,
                      "server": server.__file__, "tools": len(server.REGISTERED_TOOLS),
                      "runtime": runtime, "pywinauto": pywinauto.__file__,
                      "pillow": PIL.__version__, "comtypes": comtypes.__version__}))
    server.shutdown()
else:
    runpy.run_path(str(HERE / "server.py"), run_name="__main__")
