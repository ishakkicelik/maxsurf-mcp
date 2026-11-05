"""Construct registered native helper classes from the installed manifest."""
from __future__ import annotations

from typing import Any

import pythoncom

from . import com, native_automation as automation, native_inventory as inventory
from .audit import Channel, Classification
from .errors import MaxsurfCOMError, MaxsurfSafetyError, MaxsurfValidationError
from .guard import TIER_DIAGNOSTICS, guarded, register


@guarded(classification=Classification.DIAGNOSTIC, module="native_objects",
         channel=Channel.MCP_COM, tier=TIER_DIAGNOSTICS)
def create_native_object(module: str, coclass: str,
                         acknowledge_native_effects: bool = False,
                         inventory_digest: str = "") -> str:
    """DIAGNOSTIC. Create a registered helper in an already authorized application.

    Select a coclass name or GUID from discover_native_api. Application roots
    reuse connect_maxsurf's managed instance rather than creating another one.
    Returned references use native_call's session ownership and stale checks.
    """
    if acknowledge_native_effects is not True:
        raise MaxsurfSafetyError("explicit acknowledge_native_effects=True is required")
    if module not in inventory.LIBRARIES:
        raise MaxsurfValidationError("constructor requires an exact application module")
    if not isinstance(coclass, str) or not coclass.strip():
        raise MaxsurfValidationError("coclass must be a nonempty manifest name or GUID")
    snapshot = inventory.get_inventory(module)
    if inventory_digest and inventory_digest != snapshot["inventory_digest"]:
        raise MaxsurfSafetyError("installed native inventory changed")
    matches = [row for library in snapshot["libraries"] for row in library["types"]
               if row["typekind"] == 5 and
               coclass.casefold() in (row["name"].casefold(), row["guid"].casefold())]
    if len(matches) != 1:
        raise MaxsurfValidationError("native coclass is absent or ambiguous",
                                     coclass=coclass, matches=len(matches))
    definition = matches[0]
    if not definition.get("registration", {}).get("registered"):
        raise MaxsurfValidationError("native coclass is not registered", coclass=coclass)
    defaults = [item["reference"] for item in definition["implementations"]
                if item["flags"] & 1 and not item["flags"] & 2]
    if len(defaults) != 1:
        raise MaxsurfValidationError("native coclass has no unique default interface")
    expected = defaults[0]
    spec = inventory.session.MODULES[module]
    managed_root = expected["guid"].casefold() == str(spec.application_iid).casefold()

    com.connect(module)  # Retains the configured process gate and activation policy.
    session = com.session_for(module)
    before = automation._session_stamp(session)
    try:
        raw = session.app if managed_root else pythoncom.CoCreateInstance(
            definition["guid"], None,
            pythoncom.CLSCTX_INPROC_SERVER | pythoncom.CLSCTX_LOCAL_SERVER,
            pythoncom.IID_IDispatch)
        value = automation._wrap_object(raw)
        _info, identity = automation._identity(value)
        if (identity["guid"] != expected["guid"]
                or identity["library_guid"] != expected["library_guid"]
                or identity["library_version"] != expected["library_version"]):
            raise MaxsurfSafetyError("constructed native interface differs from the manifest")
        session.refresh_design_identity()
        stamp = automation._session_stamp(session)
        if stamp != before:
            automation._expire(module, stamp)
        handle = automation._encode(
            value, {"kind": "object", "vartype": 9, "supported": True}, module, stamp,
        )
    except MaxsurfSafetyError:
        raise
    except Exception as exc:
        raise MaxsurfCOMError("native helper construction failed",
                              coclass=coclass, cause=exc) from exc
    return com.to_json({
        "module": module, "coclass": definition["name"], "clsid": definition["guid"],
        "created": not managed_root,
        "result": handle, "inventory_digest": snapshot["inventory_digest"],
        "contract_validated": True, "engineering_acceptance": False,
    })


TOOLS = (create_native_object,)


def register_tools(mcp: Any) -> list[str]:
    return register(mcp, TOOLS)
