"""Raw COM access tools. DIAGNOSTIC tier, disabled by default.

These three tools can read, write, and invoke any member reachable in the COM
object graph. They exist because API discovery needs them, and they are the
reason the object-path resolver had to be hardened, but they are far too broad
for routine production use: a single ``com_invoke`` can close a design without
saving, overwrite a file, or exit the application.

They are therefore registered and callable only when
``MAXSURF_MCP_ENABLE_DIAGNOSTICS=1``. When the tier is disabled the attempt is
recorded in the audit log as ``blocked`` and a
:class:`~maxsurf_mcp.errors.MaxsurfSafetyError` is raised.

Proven capabilities must not be driven through this tier. Codify them as
narrow, typed tools in the owning application module instead.
"""

from __future__ import annotations

import json
from typing import Any

from . import com, objectpath
from .audit import Channel, Classification
from .errors import MaxsurfCOMError, MaxsurfValidationError
from .guard import TIER_DIAGNOSTICS, guarded, register

_LITERALS = {"true": True, "false": False, "null": None, "none": None}


def coerce_value(raw: str) -> Any:
    """Coerce a text value to bool, None, int, float, or str, in that order."""
    if not isinstance(raw, str):
        return raw
    token = raw.strip()
    unquoted = (
        len(token) >= 2 and token[0] == token[-1] and token[0] in "'\""
    )
    if unquoted:
        return token[1:-1]
    lowered = token.casefold()
    if lowered in _LITERALS:
        return _LITERALS[lowered]
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    return token


@guarded(classification=Classification.DIAGNOSTIC, module="diagnostics",
         channel=Channel.MCP_COM, tier=TIER_DIAGNOSTICS)
def com_get(path: str, module: str = "modeler") -> str:
    """DIAGNOSTIC. Read any COM value through a dotted path.

    Example: 'Design.Surfaces.Count', 'Design.Surfaces(1).Name'.
    Requires MAXSURF_MCP_ENABLE_DIAGNOSTICS=1.
    """
    app = com.connect(module)
    value = objectpath.resolve(app, path)
    kind = objectpath.classify(value)
    if kind == "com":
        return com.to_json({
            "path": path,
            "type": type(value).__name__,
            "note": "an object was returned; inspect it with com_describe",
        })
    return com.to_json({"path": path, "value": value, "kind": kind})


@guarded(classification=Classification.DIAGNOSTIC, module="diagnostics",
         channel=Channel.MCP_COM, tier=TIER_DIAGNOSTICS)
def com_set(path: str, value: str, module: str = "modeler") -> str:
    """DIAGNOSTIC. Write a COM property. Numeric and boolean text is coerced.

    Requires MAXSURF_MCP_ENABLE_DIAGNOSTICS=1. There is no semantic guard on
    what is written, so prefer a proven, typed tool wherever one exists.
    """
    app = com.connect(module)
    parent, leaf = objectpath.resolve_parent(app, path)
    coerced = coerce_value(value)
    try:
        setattr(parent, leaf, coerced)
    except AttributeError as exc:
        raise MaxsurfValidationError(
            "COM property is not writable or does not exist",
            path=path,
            member=leaf,
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise MaxsurfCOMError(
            "COM property write failed", cause=exc, path=path, member=leaf
        ) from exc
    return com.to_json({
        "path": path,
        "written": coerced,
        "written_type": type(coerced).__name__,
    })


@guarded(classification=Classification.DIAGNOSTIC, module="diagnostics",
         channel=Channel.MCP_COM, tier=TIER_DIAGNOSTICS)
def com_invoke(path: str, args_json: str = "[]",
               module: str = "modeler") -> str:
    """DIAGNOSTIC. Call a COM method. args_json must be a JSON array.

    Example: com_invoke("Design.Surfaces(1).SetControlPoint", "[1,2,0.5,1.2,3.0]")
    Requires MAXSURF_MCP_ENABLE_DIAGNOSTICS=1.
    """
    app = com.connect(module)
    parent, leaf = objectpath.resolve_parent(app, path)
    try:
        args = json.loads(args_json)
    except ValueError as exc:
        raise MaxsurfValidationError(
            "args_json must be valid JSON", args_json=args_json[:200]
        ) from exc
    if not isinstance(args, list):
        raise MaxsurfValidationError(
            "args_json must be a JSON array", received=type(args).__name__
        )
    try:
        member = getattr(parent, leaf)
    except AttributeError as exc:
        raise MaxsurfValidationError(
            "COM member does not exist", path=path, member=leaf
        ) from exc
    if not callable(member):
        raise MaxsurfValidationError(
            "COM member is not callable; use com_get to read a property",
            path=path,
            member=leaf,
        )
    try:
        result = member(*args)
    except Exception as exc:  # noqa: BLE001
        raise MaxsurfCOMError(
            "COM method invocation failed", cause=exc, path=path, member=leaf
        ) from exc
    kind = objectpath.classify(result)
    return com.to_json({
        "path": path,
        "result": result if kind in ("scalar", "sequence") else None,
        "result_kind": kind,
        "result_type": type(result).__name__,
    })


TOOLS = (com_get, com_set, com_invoke)


def register_tools(mcp: Any) -> list[str]:
    """Register the raw COM tier only when it is explicitly enabled."""
    return register(mcp, TOOLS)
