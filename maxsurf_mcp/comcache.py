"""Configure an isolated pywin32 generated-wrapper cache.

pywin32 normally writes generated COM wrappers below the user's temporary
directory. A partially written wrapper there can make ``EnsureDispatch`` fail
even though Maxsurf is already running and correctly registered. The MCP server
keeps its generated wrappers inside the repository runtime sandbox so it
neither depends on nor modifies that shared temporary cache.

This module must be configured before importing modules that import
``win32com.client.gencache`` (notably :mod:`maxsurf_mcp.session`).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from . import config


GEN_PY_DIR_VAR = "MAXSURF_MCP_GEN_PY_DIR"


def cache_dir() -> Path:
    """Return the generated-wrapper directory used by this process."""
    configured = (os.environ.get(GEN_PY_DIR_VAR) or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (config.runtime_root() / "gen_py").resolve()


def configure() -> dict[str, Any]:
    """Point pywin32 at the isolated cache and return the applied facts."""
    target = cache_dir()
    target.mkdir(parents=True, exist_ok=True)

    try:
        import win32com  # type: ignore[import-not-found]
    except ImportError:
        if sys.platform != 'win32':
            return {'path': str(target), 'isolated': True, 'available': False}
        raise

    win32com.__gen_path__ = str(target)
    package = sys.modules.get("win32com.gen_py")
    if package is not None:
        package.__path__ = [str(target)]

    return {
        "path": str(target),
        "environment_variable": GEN_PY_DIR_VAR,
        "isolated": True,
    }
