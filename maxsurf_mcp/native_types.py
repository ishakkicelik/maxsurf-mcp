"""Structured native descriptors and strict, non-coercing value validation."""
from __future__ import annotations

import math
from typing import Any, Callable

from .errors import MaxsurfValidationError


VARTYPES = {
    0: "EMPTY", 1: "NULL", 2: "I2", 3: "I4", 4: "R4", 5: "R8",
    6: "CY", 7: "DATE", 8: "BSTR", 9: "DISPATCH", 10: "ERROR",
    11: "BOOL", 12: "VARIANT", 13: "UNKNOWN", 14: "DECIMAL", 16: "I1",
    17: "UI1", 18: "UI2", 19: "UI4", 20: "I8", 21: "UI8",
    22: "INT", 23: "UINT", 24: "VOID", 25: "HRESULT", 26: "PTR",
    27: "SAFEARRAY", 28: "CARRAY", 29: "USERDEFINED", 30: "LPSTR",
    31: "LPWSTR", 36: "RECORD",
}
TYPEKINDS = {
    0: "enum", 1: "record", 2: "module", 3: "interface", 4: "dispatch",
    5: "coclass", 6: "alias", 7: "union",
}
INTEGER_RANGES = {
    2: (-2**15, 2**15-1), 3: (-2**31, 2**31-1),
    16: (-2**7, 2**7-1), 17: (0, 2**8-1), 18: (0, 2**16-1),
    19: (0, 2**32-1), 20: (-2**63, 2**63-1), 21: (0, 2**64-1),
    22: (-2**31, 2**31-1), 23: (0, 2**32-1),
    10: (-2**31, 2**31-1), 25: (-2**31, 2**31-1),
}


def error_record(exc: Exception, operation: str) -> dict:
    return {"code": "metadata_error", "operation": operation,
            "type": type(exc).__name__, "message": str(exc),
            "hresult": getattr(exc, "hresult", None)}


def type_identity(info: Any) -> dict:
    attr = info.GetTypeAttr()
    lib, index = info.GetContainingTypeLib()
    guid, lcid, syskind, major, minor, flags = lib.GetLibAttr()
    return {"library_guid": str(guid), "library_version": [major, minor],
            "library_lcid": lcid, "library_syskind": syskind,
            "type_index": index, "guid": str(attr.iid),
            "name": info.GetDocumentation(-1)[0], "typekind": attr.typekind}


def identity_key(identity: dict) -> str:
    return ":".join(str(identity[key]) for key in (
        "library_guid", "library_version", "library_lcid",
        "library_syskind", "type_index"))


def descriptor(owner: Any, raw: Any, *, enqueue: Callable | None = None,
               seen: frozenset[str] = frozenset()) -> dict:
    """Resolve HREFTYPE on its owner; cycles retain identity rather than truncate."""
    try:
        vt = raw if isinstance(raw, int) else raw[0]
        result = {"vartype": vt, "vartype_name": VARTYPES.get(vt, "UNSUPPORTED"),
                  "kind": "scalar", "supported": vt in VARTYPES}
        if vt & 0x6000:
            base = vt & ~0x6000
            result.update(kind="array" if vt & 0x2000 else "pointer",
                          byref=bool(vt & 0x4000),
                          element=descriptor(owner, base, enqueue=enqueue, seen=seen))
            result["supported"] = result["element"]["supported"]
        elif vt in (26, 27, 28):
            result["kind"] = {26: "pointer", 27: "array", 28: "array"}[vt]
            element = raw[1]
            if vt == 28:
                # pywin32 ARRAYDESC is (element TYPEDESC, (count, lower_bound), ...).
                result["bounds"] = [
                    {"count": bound[0], "lower_bound": bound[1]}
                    for bound in element[1:]
                ]
                element = element[0]
            result["element"] = descriptor(owner, element, enqueue=enqueue, seen=seen)
            result["supported"] = result["element"]["supported"]
            if vt == 26 and result["element"].get("vartype") == 24:
                result.update(supported=False, unsupported_reason="untyped_native_pointer")
        elif vt == 29:
            handle = raw[1]
            info = owner.GetRefTypeInfo(handle)
            identity = type_identity(info)
            key = identity_key(identity)
            result.update(kind=TYPEKINDS.get(identity["typekind"], "unsupported"),
                          reference_handle=handle, reference=identity, type_key=key)
            if enqueue is not None:
                enqueue(info)
            if key in seen:
                result.update(recursive=True, supported=False,
                              unsupported_reason="recursive_value_descriptor")
            elif identity["typekind"] == 0:
                result["values"] = []
                result["errors"] = []
                for index in range(info.GetTypeAttr().cVars):
                    try:
                        var = info.GetVarDesc(index)
                        result["values"].append({
                            "name": info.GetNames(var[0])[0], "value": var[1],
                            "dispid": var[0]})
                    except Exception as exc:
                        result["errors"].append(error_record(exc, f"enum[{index}]"))
                result["supported"] = not result["errors"]
            elif identity["typekind"] == 6:
                result["target"] = descriptor(
                    info, info.GetTypeAttr().tdescAlias,
                    enqueue=enqueue, seen=seen | {key})
                result["supported"] = result["target"]["supported"]
            elif identity["typekind"] not in (3, 4):
                result.update(supported=False, unsupported_reason="non_automation_value")
        elif vt in (9, 13):
            result["kind"] = "object"
        elif vt not in INTEGER_RANGES and vt not in (0, 1, 4, 5, 8, 11, 12, 24):
            result.update(supported=False, unsupported_reason="unsupported_scalar_marshaling")
        return result
    except Exception as exc:
        return {"kind": "unresolved", "supported": False,
                "error": error_record(exc, "resolve_descriptor")}


def parameter(owner: Any, raw: Any, name: str, index: int,
              *, enqueue: Callable | None = None) -> dict:
    type_raw, flags, default = raw
    result = {"name": name, "index": index, "flags": flags,
              # PARAMFLAG_NONE is an ordinary input in generated pywin32 calls.
              "input": bool(flags & 1) or flags == 0, "output": bool(flags & 2),
              "lcid": bool(flags & 4), "retval": bool(flags & 8),
              "optional": bool(flags & 16), "has_default": bool(flags & 32),
              "type": descriptor(owner, type_raw, enqueue=enqueue)}
    if flags & 32:
        if (default is None or isinstance(default, (str, bool, int))
                or isinstance(default, float) and math.isfinite(default)):
            result["default"] = default
        else:
            result["default_repr"] = repr(default)
            result["default_supported"] = False
    return result


def validate_value(value: Any, type_descriptor: dict, field: str = "value",
                   *, object_validator: Callable | None = None) -> Any:
    """Validate without coercion; object references require a session-owned validator."""
    def invalid(reason: str) -> None:
        raise MaxsurfValidationError(reason, field=field,
                                     native_type=type_descriptor)

    if not type_descriptor.get("supported", False):
        invalid("native type is unsupported or unresolved")
    kind = type_descriptor["kind"]
    vt = type_descriptor.get("vartype")
    if kind == "alias":
        return validate_value(value, type_descriptor["target"], field,
                              object_validator=object_validator)
    if kind == "pointer":
        return validate_value(value, type_descriptor["element"], field,
                              object_validator=object_validator)
    if kind == "enum":
        if type(value) is not int or value not in [
            row["value"] for row in type_descriptor.get("values", [])]:
            invalid("value is not a declared native enum value")
    elif kind in ("interface", "dispatch", "object"):
        if object_validator is None:
            invalid("native object requires a session-owned handle validator")
        return object_validator(value, type_descriptor)
    elif kind == "array":
        if not isinstance(value, (list, tuple)):
            invalid("native array requires a sequence")
        bounds = type_descriptor.get("bounds", [])

        def array(items: Any, dimension: int) -> list:
            if not isinstance(items, (list, tuple)):
                invalid("native array has inconsistent rank")
            if bounds and len(items) != bounds[dimension]["count"]:
                invalid("native array dimension does not match declared bounds")
            if bounds and dimension + 1 < len(bounds):
                return [array(item, dimension + 1) for item in items]
            return [validate_value(item, type_descriptor["element"],
                                   f"{field}[{index}]", object_validator=object_validator)
                    for index, item in enumerate(items)]
        return array(value, 0)
    elif vt in INTEGER_RANGES:
        low, high = INTEGER_RANGES[vt]
        if type(value) is not int or not low <= value <= high:
            invalid("native integer is not integral or is out of range")
    elif vt in (4, 5):
        if type(value) not in (int, float):
            invalid("native real requires a finite number")
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite or (vt == 4 and abs(value) > 3.402823466e38):
            invalid("native real is non-finite or out of range")
    elif vt == 8:
        if not isinstance(value, str):
            invalid("native BSTR requires a string")
    elif vt == 11:
        if type(value) is not bool:
            invalid("native Boolean requires true or false")
    elif vt in (0, 1, 24):
        if value is not None:
            invalid("native empty/null/void requires null")
    elif vt == 12:
        # JSON-compatible VARIANT values only. Object tokens require explicit ownership.
        if value is None or type(value) in (str, bool):
            return value
        if type(value) in (int, float):
            scalar = 20 if type(value) is int else 5
            return validate_value(value, {"kind": "scalar", "vartype": scalar,
                                         "supported": True}, field)
        if isinstance(value, (list, tuple)):
            return [validate_value(item, type_descriptor, f"{field}[{index}]",
                                   object_validator=object_validator)
                    for index, item in enumerate(value)]
        if isinstance(value, dict) and object_validator is not None:
            return object_validator(value, type_descriptor)
        invalid("VARIANT shape requires an explicit supported value or owned handle")
    else:
        invalid("native type has no validated value representation")
    return value


def validate_arguments(member: dict, arguments: dict | list,
                       *, object_validator: Callable | None = None) -> dict:
    """Return supplied input values by native name; preserve omitted optional values."""
    if member.get("optional_count") == -1:
        raise MaxsurfValidationError("variadic native calls are unsupported")
    if any(not (param["input"] or param["output"] or param["retval"] or param["lcid"])
           for param in member["arguments"]):
        raise MaxsurfValidationError("native argument direction is unspecified")
    params = [param for param in member["arguments"]
              if param["input"] and not param["retval"] and not param["lcid"]]
    if isinstance(arguments, list):
        if len(arguments) > len(params):
            raise MaxsurfValidationError("too many native arguments")
        arguments = {param["name"]: value for param, value in zip(params, arguments)}
    if not isinstance(arguments, dict) or any(not isinstance(key, str) for key in arguments):
        raise MaxsurfValidationError("native arguments must be an object or positional list")
    unknown = set(arguments) - {param["name"] for param in params}
    if unknown:
        raise MaxsurfValidationError("unknown or output-only native arguments",
                                     arguments=sorted(unknown))
    result = {}
    for param in params:
        name = param["name"]
        if name not in arguments:
            if param["optional"] or param["has_default"]:
                continue
            raise MaxsurfValidationError("missing required native argument", argument=name)
        result[name] = validate_value(arguments[name], param["type"], name,
                                      object_validator=object_validator)
    return result
