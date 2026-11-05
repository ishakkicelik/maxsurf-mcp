"""Opt-in, manifest/typeinfo-validated native automation on the guarded STA.

No Python attribute is a callable contract. Object traversal uses manifest-backed
IDispatch calls; enumeration uses the fixed IEnumVARIANT vtable operations.
Returned objects are bounded, session-owned capabilities, not Python paths.
"""
from __future__ import annotations

from collections import OrderedDict
import json
import secrets
from typing import Any

import pythoncom
import win32com.client

from . import com, native_inventory, native_types, objectpath
from .audit import Channel, Classification
from .errors import MaxsurfCOMError, MaxsurfSafetyError, MaxsurfValidationError
from .guard import TIER_DIAGNOSTICS, guarded, register
from .native_call_policy import PROTECTED_WORKFLOWS, apply_file_policy, policy_blocker


MAX_ENUM_BATCH = 256
# One full object page and its active enumerator must remain usable together.
MAX_HANDLES = MAX_ENUM_BATCH + 1
MAX_VALUES = 100000
MAX_DEPTH = 32
_handles: OrderedDict[str, tuple] = OrderedDict()


class _NativeEnumerator:
    """Keep an IEnumVARIANT distinguishable from ordinary IDispatch handles."""
    def __init__(self, interface: Any):
        self.interface = interface


def _retain(value: Any, module: str, stamp: tuple) -> str:
    token = secrets.token_urlsafe(24)
    while len(_handles) >= MAX_HANDLES:
        _handles.popitem(last=False)
    _handles[token] = (module, stamp, value)
    return token


def _session_stamp(session: Any) -> tuple:
    return (session.session_id, session.reconnect_count, id(session.app),
            session.design_path, session.design_name)


def _expire(module: str, stamp: tuple | None = None) -> None:
    for token, (owner, previous, _obj) in list(_handles.items()):
        if owner == module and (stamp is None or previous != stamp):
            del _handles[token]


def _identity(obj: Any) -> tuple[Any, dict]:
    if not objectpath.is_com_object(obj):
        raise MaxsurfSafetyError("native target is not a restricted COM object")
    try:
        info = obj._oleobj_.GetTypeInfo()
        return info, native_types.type_identity(info)
    except Exception as exc:
        raise MaxsurfCOMError("native target has no readable interface identity",
                              cause=exc) from exc


def _owned(token: Any, module: str, stamp: tuple, desc: dict | None = None,
           *, enumerator: bool = False) -> Any:
    if token is None and desc and desc.get("kind") in ("interface", "dispatch", "object"):
        return None
    if isinstance(token, dict) and set(token) == {"native_handle"}:
        token = token["native_handle"]
    if not isinstance(token, str) or token not in _handles:
        raise MaxsurfSafetyError("unknown, expired, or stale native object handle")
    owner, previous, obj = _handles[token]
    if owner != module or previous != stamp:
        raise MaxsurfSafetyError("foreign or stale native object handle")
    if isinstance(obj, _NativeEnumerator) != enumerator:
        raise MaxsurfValidationError("native handle has the wrong interface")
    if desc and desc.get("reference"):
        _info, identity = _identity(obj)
        expected = desc["reference"]
        if (identity["guid"] != expected["guid"] or
                native_types.identity_key(identity) != native_types.identity_key(expected)):
            raise MaxsurfValidationError("native object handle has the wrong interface")
    return obj


def _bounded(value: Any, depth: int = 0, budget: list | None = None) -> None:
    if budget is None:
        budget = [MAX_VALUES]
    budget[0] -= 1
    if budget[0] < 0 or depth > MAX_DEPTH:
        raise MaxsurfValidationError("native value exceeds bounded size or nesting")
    if isinstance(value, (list, tuple)):
        for item in value:
            _bounded(item, depth + 1, budget)
    elif isinstance(value, dict):
        for item in value.values():
            _bounded(item, depth + 1, budget)
    elif isinstance(value, str) and len(value) > 1048576:
        raise MaxsurfValidationError("native string exceeds 1048576 characters")


def _marshal_type(desc: dict) -> int:
    """Match pywin32 build._ResolveType, but refuse shapes it cannot marshal."""
    if not desc.get("supported"):
        raise MaxsurfValidationError("unrepresentable native type", native_type=desc)
    kind, vt = desc["kind"], desc.get("vartype")
    if kind == "alias":
        return _marshal_type(desc["target"])
    if kind == "enum":
        return pythoncom.VT_I4
    if kind in ("dispatch", "interface"):
        return pythoncom.VT_DISPATCH if kind == "dispatch" else pythoncom.VT_UNKNOWN
    if kind == "pointer":
        element = desc["element"]
        native = _marshal_type(element)
        if element.get("vartype") == 29 and native in (9, 13):
            return native
        if native & pythoncom.VT_BYREF:
            raise MaxsurfValidationError("multiple native pointer indirections are unsupported")
        return native | pythoncom.VT_BYREF
    if kind == "array":
        if vt == 28 or desc.get("bounds"):
            raise MaxsurfValidationError(
                "fixed CARRAY/lower-bound descriptors are not marshaled by pywin32 InvokeTypes")
        element = _marshal_type(desc["element"])
        if element & (pythoncom.VT_ARRAY | pythoncom.VT_BYREF):
            raise MaxsurfValidationError("nested typed SAFEARRAY descriptors are unsupported")
        return (pythoncom.VT_ARRAY | element
                | (pythoncom.VT_BYREF if desc.get("byref") else 0))
    return {22: 3, 23: 19, 25: 3}.get(vt, vt)


def _typed_value(value: Any, desc: dict, field: str, object_validator: Any) -> Any:
    if desc.get("kind") in ("alias", "pointer"):
        return _typed_value(value, desc.get("target", desc.get("element")),
                            field, object_validator)
    if desc.get("kind") == "array" and desc.get("supported") and not desc.get("bounds"):
        def dimension(items: Any) -> tuple:
            if not isinstance(items, (list, tuple)):
                raise MaxsurfValidationError("SAFEARRAY requires an array", field=field)
            nested = [isinstance(item, (list, tuple)) for item in items]
            if any(nested):
                if not all(nested):
                    raise MaxsurfValidationError("SAFEARRAY has inconsistent rank", field=field)
                children = [dimension(item) for item in items]
                if any(shape != children[0][1] for _values, shape in children):
                    raise MaxsurfValidationError("SAFEARRAY must be rectangular", field=field)
                return [child[0] for child in children], (len(items),) + children[0][1]
            return [native_types.validate_value(
                item, desc["element"], field, object_validator=object_validator)
                for item in items], (len(items),)
        return dimension(value)[0]
    return native_types.validate_value(value, desc, field, object_validator=object_validator)


def _validate_arguments(entry: dict, arguments: dict | list, object_validator: Any) -> dict:
    # Reuse the inventory's direction/optional/name/count contract independently
    # of value validation; add rectangular multidimensional SAFEARRAY support.
    inputs = [p for p in entry["arguments"]
              if p["input"] and not p["retval"] and not p["lcid"]]
    shape = {**entry, "arguments": [
        {**p, "type": {"kind": "scalar", "vartype": 12, "supported": True}}
        for p in entry["arguments"]]}
    placeholders = ([None] * len(arguments) if isinstance(arguments, list)
                    else {key: None for key in arguments})
    supplied = native_types.validate_arguments(shape, placeholders)
    if isinstance(arguments, list):
        arguments = {p["name"]: value for p, value in zip(inputs, arguments)}
    return {p["name"]: _typed_value(arguments[p["name"]], p["type"], p["name"],
                                     object_validator)
            for p in inputs if p["name"] in supplied}


def _enum_contract(member: dict) -> bool:
    return (member["name"] == "_NewEnum"
            and member["dispid"] == pythoncom.DISPID_NEWENUM
            and member["invocation_kind"] == pythoncom.DISPATCH_PROPERTYGET
            and not member["arguments"]
            and member["return"].get("vartype") == pythoncom.VT_UNKNOWN)


def contract_blocker(module: str, member: dict,
                     allow_unmapped_effects: bool = False) -> str | None:
    """Metadata/effect route assessment for coverage, never operational acceptance."""
    try:
        # These names can be real native members. InvokeTypes dispatches their
        # verified DISPID; it never accesses Python's GetProperty/SetProperty.
        if member["name"] not in ("GetProperty", "SetProperty") and not _enum_contract(member):
            objectpath.validate_member_name(member["name"], path=member["name"])
        if member.get("contract_status") != "metadata_validated":
            return "native metadata is incomplete or has an unsupported value shape"
        if member.get("optional_count") == -1:
            return "variadic native signatures have no validated argument projection"
        _marshal_type(member["return"])
        for param in member["arguments"]:
            _marshal_type(param["type"])
            if param["retval"] or param["lcid"]:
                return "explicit vtable retval/LCID parameters require an IDispatch projection"
            if param.get("default_supported") is False:
                return "native default has no JSON/Automation scalar representation"
        blocker = policy_blocker(module, member)
        protected = (module.removesuffix("_xmlgrid"), member["interface"],
                     member["name"]) in PROTECTED_WORKFLOWS
        return blocker if not allow_unmapped_effects or protected else None
    except (MaxsurfValidationError, MaxsurfSafetyError) as exc:
        return str(exc)


def _live_contract(obj: Any, member: dict) -> tuple[Any, int]:
    info, identity = _identity(obj)
    if (identity["guid"] != member["interface_guid"] or
            native_types.identity_key(identity) != member["type_key"]):
        raise MaxsurfSafetyError("target interface identity does not match the native manifest",
                                 actual=identity, expected=member["type_key"])
    matches = []
    for index in range(info.GetTypeAttr().cFuncs):
        func = info.GetFuncDesc(index)
        if func.memid == member["dispid"] and func.invkind == member["invocation_kind"]:
            matches.append(func)
    if len(matches) != 1:
        raise MaxsurfSafetyError("member invocation is absent or ambiguous in live native typeinfo")
    func = matches[0]
    names = info.GetNames(func.memid)
    args = [native_types.parameter(info, raw,
            names[index + 1] if len(names) > index + 1 else f"arg{index + 1}", index)
            for index, raw in enumerate(func.args)]
    if (names[0] != member["name"] or args != member["arguments"] or
            native_types.descriptor(info, func.rettype[0]) != member["return"] or
            func.cParamsOpt != member["optional_count"] or
            func.rettype[1] != member["return_flags"] or
            func.callconv != member["callconv"] or
            func.funckind != member["function_kind"] or
            func.wFuncFlags != member["function_flags"]):
        raise MaxsurfSafetyError("live native signature differs from the installed manifest")
    return info, identity["library_lcid"]


def _wrap_object(value: Any) -> Any:
    if isinstance(value, pythoncom.TypeIIDs[pythoncom.IID_IUnknown]):
        value = value.QueryInterface(pythoncom.IID_IDispatch)
    if isinstance(value, pythoncom.TypeIIDs[pythoncom.IID_IDispatch]):
        # Wrapping an existing interface pointer does not activate a coclass.
        value = win32com.client.Dispatch(value)
    return value


def _encode(value: Any, desc: dict, module: str, stamp: tuple) -> Any:
    _bounded(value)
    kind = desc.get("kind")
    if kind in ("alias", "pointer"):
        return _encode(value, desc.get("target", desc.get("element")), module, stamp)
    value = _wrap_object(value)
    if kind in ("dispatch", "interface", "object") or (
            desc.get("vartype") == 12 and objectpath.is_com_object(value)):
        if value is None:
            return None  # COM null interface pointer, not a fabricated handle.
        _info, identity = _identity(value)
        expected = desc.get("reference")
        if expected and (identity["guid"] != expected["guid"] or
                         native_types.identity_key(identity) != native_types.identity_key(expected)):
            raise MaxsurfCOMError("native result interface differs from declared return type")
        token = _retain(value, module, stamp)
        return {"native_handle": token, "interface": identity,
                "ownership": "session", "engineering_acceptance": False}
    if isinstance(value, (tuple, list)):
        if kind == "array":
            # Validate rank and typed leaves before allocating object handles.
            _typed_value(value, desc, "native result", lambda item, _desc: item)
            element = desc["element"]
            return [_encode(item, desc if isinstance(item, (list, tuple)) else element,
                            module, stamp) for item in value]
        if desc.get("vartype") == 12:
            return [_encode(item, desc, module, stamp) for item in value]
    return _typed_value(value, desc, "native result", None)


def _encode_enum(value: Any, module: str, stamp: tuple) -> dict:
    try:
        # InvokeTypes returns a raw IUnknown for NEWENUM, never an IDispatch.
        interface = value.QueryInterface(pythoncom.IID_IEnumVARIANT)
        if not isinstance(interface, pythoncom.TypeIIDs[pythoncom.IID_IEnumVARIANT]):
            raise TypeError("QueryInterface did not return IEnumVARIANT")
    except Exception as exc:
        raise MaxsurfCOMError("native NEWENUM result does not support IEnumVARIANT",
                              cause=exc) from exc
    token = _retain(_NativeEnumerator(interface), module, stamp)
    return {"native_enum_handle": token, "interface": "IEnumVARIANT",
            "ownership": "session", "engineering_acceptance": False}


def _result(result: Any, entry: dict, module: str, stamp: tuple) -> Any:
    if _enum_contract(entry):
        return _encode_enum(result, module, stamp)
    outputs = []
    if entry["return"].get("vartype") not in (0, 24):
        outputs.append(entry["return"])
    outputs.extend(p["type"] for p in entry["arguments"] if p["output"])
    if not outputs:
        if result is not None:
            raise MaxsurfCOMError("native void call returned an unexpected value")
        return None
    if len(outputs) == 1:
        return _encode(result, outputs[0], module, stamp)
    if not isinstance(result, (tuple, list)) or len(result) != len(outputs):
        raise MaxsurfCOMError("native return/out tuple does not match the declared signature")
    return [_encode(value, desc, module, stamp) for value, desc in zip(result, outputs)]


def _arguments_json(raw: str) -> dict | list:
    try:
        arguments = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise MaxsurfValidationError("arguments_json must be a JSON object or array") from exc
    if not isinstance(arguments, (dict, list)):
        raise MaxsurfValidationError("arguments_json must be a JSON object or array")
    _bounded(arguments)
    return arguments


@guarded(classification=Classification.DIAGNOSTIC, module="native_automation",
         channel=Channel.MCP_COM, tier=TIER_DIAGNOSTICS)
def native_call(interface: str, member: str, invocation_kind: str = "method",
                arguments_json: str = "{}", module: str = "modeler",
                target_handle: str = "", acknowledge_native_effects: bool = False,
                overwrite: bool = False, inventory_digest: str = "",
                allow_unmapped_effects: bool = False) -> str:
    """DIAGNOSTIC. Invoke an installed native get/put/putref/method contract.

    All calls require acknowledge_native_effects=True and diagnostics enabled.
    Empty target_handle means the managed application root. Obtain child handles
    with validated member calls, not dotted Python paths. Inputs use exact native
    names or positional input lists; omit out-only arguments. Pass object inputs
    as {"native_handle":"..."}; do not pass the entire returned object description.
    PUT inputs include indices followed by the value, exactly as in the inventory.
    Methods and puts invalidate earlier handles, including on native failure.
    allow_unmapped_effects additionally permits diagnostic UI/implicit-file/text
    operations without a sandbox guarantee; known filename policies still apply.
    Protected Stability replacement and Motions solver/.skr workflows stay refused.
    Exact DISPID_NEWENUM property gets return native_enum_handle tokens for
    native_enumerate, not ordinary native_handle object arguments.
    """
    if acknowledge_native_effects is not True:
        raise MaxsurfSafetyError("explicit acknowledge_native_effects=True is required")
    if type(overwrite) is not bool:
        raise MaxsurfValidationError("overwrite must be Boolean")
    if type(allow_unmapped_effects) is not bool:
        raise MaxsurfValidationError("allow_unmapped_effects must be Boolean")
    if module not in native_inventory.session.MODULES:
        raise MaxsurfValidationError("native calls require an exact supported session module")
    arguments = _arguments_json(arguments_json)
    snapshot = native_inventory.get_inventory(module)
    if inventory_digest and inventory_digest != snapshot["inventory_digest"]:
        raise MaxsurfSafetyError("installed native inventory changed")
    entry = native_inventory.find_member(module, interface, member, invocation_kind,
                                         inventory=snapshot)
    blocker = contract_blocker(module, entry, allow_unmapped_effects)
    if blocker:
        raise MaxsurfSafetyError(blocker, member=entry["key"])
    # Scalar validation and file policy precede even connecting to an application.
    # Handles are resolved after managed-session identity/liveness revalidation.
    def token_shape(value: Any, _desc: dict) -> Any:
        if value is None:
            return None
        if not isinstance(value, dict) or set(value) != {"native_handle"}:
            raise MaxsurfValidationError("object arguments require a native_handle token")
        return value
    values = _validate_arguments(entry, arguments, token_shape)
    file_policy = apply_file_policy(
        module, entry, values, overwrite, allow_unmapped_effects)
    app = com.connect(module)
    managed = com.session_for(module)
    if managed is None:
        raise MaxsurfSafetyError("native invocation requires a managed session")
    stamp = _session_stamp(managed)
    _expire(module, stamp)
    target = _owned(target_handle, module, stamp) if target_handle else app
    _info, lcid = _live_contract(target, entry)
    values = _validate_arguments(
        entry, values, lambda value, desc: _owned(value, module, stamp, desc))
    call_values = []
    for param in entry["arguments"]:
        if param["name"] in values:
            value = values[param["name"]]
        elif param["output"] and not param["input"]:
            value = pythoncom.Missing
        elif param["has_default"]:
            value = native_types.validate_value(param["default"], param["type"], param["name"])
        else:
            value = pythoncom.Missing
        call_values.append(value)
    return_type = (_marshal_type(entry["return"]), entry.get("return_flags", 0))
    arg_types = tuple((_marshal_type(p["type"]), p["flags"]) for p in entry["arguments"])
    try:
        result = target._oleobj_.InvokeTypes(
            entry["dispid"], lcid, entry["invocation_kind"], return_type,
            arg_types, *call_values)
    except Exception as exc:
        raise MaxsurfCOMError("validated native invocation failed", cause=exc,
                              member=entry["key"], partial_state_possible=True) from exc
    finally:
        # Item is a method in some installed collections, a GET in others.
        # Linked-room creation really is a GET, so invocation kind is not an
        # effect classification. All these tools remain DIAGNOSTIC.
        if not ((entry["invocation_kind"] == 2 and entry["name"] not in
                 ("AddLinked", "AddExLinked")) or
                (entry["invocation_kind"] == 1 and entry["name"] == "Item")):
            _expire(module)
    encoded = _result(result, entry, module, stamp)
    return com.to_json({
        "module": module, "member_key": entry["key"],
        "inventory_digest": snapshot["inventory_digest"],
        "invocation_kind": entry["invocation_kind"], "result": encoded,
        "return_type": entry["return"],
        "output_arguments": [p["name"] for p in entry["arguments"] if p["output"]],
        "output_types": [p["type"] for p in entry["arguments"] if p["output"]],
        "result_convention": "pywin32 InvokeTypes: return value then out arguments; single value unboxed",
        "file_policy": file_policy, "contract_validated": True,
        "engineering_acceptance": False,
    })


@guarded(classification=Classification.DIAGNOSTIC, module="native_automation",
         channel=Channel.MCP_COM, tier=TIER_DIAGNOSTICS)
def native_enumerate(enum_handle: str, operation: str = "next", count: int = 1,
                     module: str = "modeler",
                     acknowledge_native_effects: bool = False) -> str:
    """DIAGNOSTIC. Next/skip/reset/clone an owned native IEnumVARIANT.

    Obtain enum_handle from an exact manifest _NewEnum property get via
    native_call. next/skip accept 1..256 items; reset/clone require count=1.
    COM items use ordinary native_handle tokens. Clones have independent cursors.
    A short next batch reports exhausted=True; skip does not report exhaustion.
    """
    if acknowledge_native_effects is not True:
        raise MaxsurfSafetyError("explicit acknowledge_native_effects=True is required")
    if module not in native_inventory.session.MODULES:
        raise MaxsurfValidationError("native calls require an exact supported session module")
    if operation not in ("next", "skip", "reset", "clone"):
        raise MaxsurfValidationError("operation must be next, skip, reset, or clone")
    if type(count) is not int or not 1 <= count <= MAX_ENUM_BATCH:
        raise MaxsurfValidationError("enumeration count must be an integer from 1 to 256")
    if operation in ("reset", "clone") and count != 1:
        raise MaxsurfValidationError("reset and clone require count=1")
    if not isinstance(enum_handle, str) or not enum_handle:
        raise MaxsurfValidationError("enum_handle must be a nonempty token string")
    com.connect(module)
    managed = com.session_for(module)
    if managed is None:
        raise MaxsurfSafetyError("native enumeration requires a managed session")
    stamp = _session_stamp(managed)
    _expire(module, stamp)
    interface = _owned(enum_handle, module, stamp, enumerator=True).interface
    try:
        if operation == "next":
            _handles.move_to_end(enum_handle)
            # Installed Maxsurf 2026 fills only the first slot of Next(n) when
            # n > 1, losing earlier elements. Build bounded batches from the
            # working, standard single-item call instead.
            result = []
            for _ in range(count):
                batch = interface.Next(1)
                if not isinstance(batch, (tuple, list)) or len(batch) > 1:
                    raise MaxsurfCOMError("native IEnumVARIANT returned an invalid single-item batch")
                if not batch:
                    break
                result.extend(batch)
        elif operation == "skip":
            interface.Skip(count)
            result = None
        elif operation == "reset":
            interface.Reset()
            result = None
        else:
            result = interface.Clone()
    except Exception as exc:
        raise MaxsurfCOMError("native IEnumVARIANT operation failed", cause=exc,
                              operation=operation) from exc
    response = {"module": module, "operation": operation, "result": None,
                "engineering_acceptance": False}
    if operation == "next":
        if not isinstance(result, (tuple, list)) or len(result) > count:
            raise MaxsurfCOMError("native IEnumVARIANT returned an invalid batch")
        _bounded(result)
        response.update(
            result=[_encode(item, {"kind": "scalar", "vartype": 12, "supported": True},
                            module, stamp) for item in result],
            fetched=len(result), exhausted=len(result) < count)
    elif operation == "clone":
        response["result"] = _encode_enum(result, module, stamp)
    return com.to_json(response)


def clear_handles(module: str | None = None) -> int:
    """Release cached COM objects on the owning STA during session cleanup."""
    keys = [token for token, entry in _handles.items()
            if module is None or entry[0] == module]
    for token in keys:
        del _handles[token]
    return len(keys)


@guarded(classification=Classification.DIAGNOSTIC, module="native_automation",
         channel=Channel.MCP_COM, tier=TIER_DIAGNOSTICS)
def release_native_handles(handles_json: str = "[]", release_all: bool = False,
                           acknowledge_native_effects: bool = False) -> str:
    """DIAGNOSTIC. Release named native handles, or all handles on this server."""
    if acknowledge_native_effects is not True:
        raise MaxsurfSafetyError("explicit acknowledge_native_effects=True is required")
    if type(release_all) is not bool:
        raise MaxsurfValidationError("release_all must be Boolean")
    try:
        handles = json.loads(handles_json)
    except (TypeError, ValueError) as exc:
        raise MaxsurfValidationError("handles_json must be a JSON array") from exc
    if (not isinstance(handles, list)
            or any(type(token) is not str or not token for token in handles)):
        raise MaxsurfValidationError("handles_json must contain nonempty handle strings")
    if len(handles) != len(set(handles)) or (release_all and handles):
        raise MaxsurfValidationError("use unique named handles or release_all, not both")
    missing = [token for token in handles if token not in _handles]
    if missing:
        raise MaxsurfValidationError("unknown or stale native handles", handles=missing)
    if release_all:
        count = clear_handles()
    else:
        for token in handles:
            del _handles[token]
        count = len(handles)
    return com.to_json({"released": count, "remaining": len(_handles)})


TOOLS = (native_call, native_enumerate, release_native_handles)


def register_tools(mcp: Any) -> list[str]:
    return register(mcp, TOOLS)
