"""
Raw COM type-library introspection for Maxsurf.

Unlike Python-level dir()/inspect()-based discovery, this module asks the
IDispatch/ITypeInfo metadata directly. It does not invoke Maxsurf getters or
methods on the object it receives, so it is suitable for read-only API
discovery on an empty design. A caller may still need property access to
resolve a path to that object before passing it here.
"""

from __future__ import annotations

import inspect
from numbers import Integral, Real
from typing import Any

import pythoncom

from .audit import Channel, Classification
from .guard import guarded, register


_INVOKE_KIND = {
    pythoncom.INVOKE_FUNC: "method",
    pythoncom.INVOKE_PROPERTYGET: "property_get",
    pythoncom.INVOKE_PROPERTYPUT: "property_put",
    pythoncom.INVOKE_PROPERTYPUTREF: "property_putref",
}

_TYPE_KIND = {
    pythoncom.TKIND_ENUM: "TKIND_ENUM",
    pythoncom.TKIND_RECORD: "TKIND_RECORD",
    pythoncom.TKIND_MODULE: "TKIND_MODULE",
    pythoncom.TKIND_INTERFACE: "TKIND_INTERFACE",
    pythoncom.TKIND_DISPATCH: "TKIND_DISPATCH",
    pythoncom.TKIND_COCLASS: "TKIND_COCLASS",
    pythoncom.TKIND_ALIAS: "TKIND_ALIAS",
    pythoncom.TKIND_UNION: "TKIND_UNION",
}

SKIP_PREFIXES = ("_", "CLSID", "coclass_", "com_interface")


def _public(name: str) -> bool:
    return bool(name) and not name.startswith(SKIP_PREFIXES)


def _safe_signature(obj: Any, name: str) -> str | None:
    """Return a best-effort bound-method signature without invoking it."""
    try:
        member = getattr(obj, name)
    except Exception:
        return None
    if not callable(member):
        return None
    try:
        return str(inspect.signature(member))
    except Exception:
        return None


def _ole_maps(obj: Any) -> tuple[set[str], set[str], set[str]]:
    """Collect function/get/put names from pywin32's OleRepr metadata."""
    funcs: set[str] = set()
    gets: set[str] = set()
    puts: set[str] = set()

    ole = getattr(obj, "_olerepr_", None)
    if ole is not None:
        for attr, target in (
            ("mapFuncs", funcs),
            ("propMap", gets),
            ("propMapGet", gets),
            ("propMapPut", puts),
        ):
            mapping = getattr(ole, attr, None)
            if isinstance(mapping, dict):
                target.update(
                    str(key) for key in mapping if _public(str(key))
                )

    for attr, target in (
        ("_prop_map_get_", gets),
        ("_prop_map_put_", puts),
    ):
        mapping = getattr(obj, attr, None)
        if isinstance(mapping, dict):
            target.update(str(key) for key in mapping if _public(str(key)))

    return funcs, gets, puts


def describe_python(
    obj: Any,
    sample_values: bool = False,
    max_items: int = 600,
) -> dict:
    """Describe generated COM metadata without invoking methods."""
    info: dict[str, Any] = {
        "type": type(obj).__name__,
        "properties": [],
        "methods": [],
    }

    funcs, gets, puts = _ole_maps(obj)
    class_method_names: set[str] = set()
    try:
        for name in dir(type(obj)):
            if not _public(name):
                continue
            try:
                raw = getattr(type(obj), name, None)
                if callable(raw):
                    class_method_names.add(name)
            except Exception:
                pass
    except Exception:
        pass

    method_names = funcs | class_method_names
    method_names -= {
        "QueryInterface", "InvokeTypes", "GetTypeInfo", "GetIDsOfNames",
        "Release", "AddRef", "GetTypeInfoCount",
    }
    property_names = (gets | puts) - method_names

    if not method_names and not property_names:
        try:
            names = [name for name in dir(obj) if _public(name)]
        except Exception:
            names = []
        for name in names:
            try:
                raw = getattr(type(obj), name, None)
                if callable(raw):
                    method_names.add(name)
                else:
                    property_names.add(name)
            except Exception:
                property_names.add(name)

    for name in sorted(method_names)[:max_items]:
        row: dict[str, Any] = {"name": name, "kind": "method"}
        signature = _safe_signature(obj, name)
        if signature:
            row["signature"] = signature
        info["methods"].append(row)

    for name in sorted(property_names)[:max_items]:
        row: dict[str, Any] = {
            "name": name,
            "kind": "property",
            "readable": name in gets or not gets,
            "writable": name in puts,
        }
        if sample_values:
            try:
                value = getattr(obj, name)
                if isinstance(value, (int, float, str, bool)):
                    row["value"] = value
                elif value is None:
                    row["value"] = None
                else:
                    row["value"] = f"<{type(value).__name__}>"
            except Exception as exc:
                row["error"] = str(exc)[:160]
        info["properties"].append(row)

    info["counts"] = {
        "properties": len(info["properties"]),
        "methods": len(info["methods"]),
    }
    return info


def typelib_dump(app: Any) -> dict:
    """List generated makepy classes for the connected type library."""
    try:
        module = __import__(type(app).__module__, fromlist=["*"])
    except Exception as exc:
        return {"error": f"type library could not be loaded: {exc}"}
    classes = [
        name for name in dir(module)
        if name and name[0].isupper() and not name.startswith("CLSID")
    ]
    return {"module": module.__name__, "classes": sorted(classes)[:1000]}


def _typeinfo_of(obj: Any):
    ole = getattr(obj, "_oleobj_", None)
    if ole is None:
        raise TypeError(f"{type(obj).__name__} has no _oleobj_; not a COM dispatch wrapper")
    return ole.GetTypeInfo()


def _safe_doc(ti, memid: int) -> str | None:
    try:
        doc = ti.GetDocumentation(memid)
        if doc and doc[0]:
            return str(doc[0])
    except Exception:
        pass
    return None


def _format_signature(name: str, params: list[str], optional_count: int) -> str:
    required = max(0, len(params) - max(0, optional_count))
    rendered = []
    for i, p in enumerate(params):
        p = p or f"arg{i+1}"
        rendered.append(p if i < required else f"{p}=?")
    return f"{name}({', '.join(rendered)})"


def _type_name(ti) -> str | None:
    try:
        name = ti.GetDocumentation(-1)[0]
        return str(name) if name else None
    except Exception:
        return None


def _find_user_defined_handles(type_descriptor: Any):
    """Yield HREFTYPEs embedded in a TYPEDESC without assuming their values."""
    if not isinstance(type_descriptor, (tuple, list)):
        return

    if len(type_descriptor) >= 2:
        try:
            vartype = int(type_descriptor[0])
        except (TypeError, ValueError):
            vartype = None
        if vartype == pythoncom.VT_USERDEFINED:
            try:
                yield int(type_descriptor[1])
            except (TypeError, ValueError):
                return
            return

    for nested in type_descriptor:
        yield from _find_user_defined_handles(nested)


def _enum_value(value: Any) -> Any:
    """Normalize COM numeric scalars while preserving their exact value."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        return float(value)
    return value


def _enum_members(ti, count: int) -> list[dict]:
    members = []
    for index in range(count):
        try:
            vd = ti.GetVarDesc(index)
            memid = int(vd[0])
            names = list(ti.GetNames(memid))
            name = str(names[0]) if names else _safe_doc(ti, memid)
            member = {
                "name": name or f"memid_{memid}",
                "value": _enum_value(vd[1]),
                "memid": memid,
            }
            if len(vd) > 3:
                member["var_flags"] = int(vd[3])
            if len(vd) > 4:
                member["varkind"] = int(vd[4])
            members.append(member)
        except Exception as exc:
            members.append({"index": index, "error": str(exc)[:200]})
    return members


def _describe_referenced_type(
    owner_ti, href: int, *, visited: set[tuple[int, int]] | None = None
) -> dict:
    """Resolve one HREFTYPE using the ITypeInfo that owns its TYPEDESC."""
    visited = visited if visited is not None else set()
    key = (id(owner_ti), int(href))
    if key in visited:
        return {"cycle": True}
    visited.add(key)

    try:
        ref_ti = owner_ti.GetRefTypeInfo(href)
        ta = ref_ti.GetTypeAttr()
    except Exception as exc:
        return {"error": str(exc)[:200]}

    typekind = int(ta.typekind)
    result = {
        "type_name": _type_name(ref_ti),
        "typekind": typekind,
        "typekind_name": _TYPE_KIND.get(typekind, f"TKIND_{typekind}"),
        "iid": str(ta.iid),
        "type_flags": int(ta.wTypeFlags),
        "cFuncs": int(ta.cFuncs),
        "cVars": int(ta.cVars),
    }
    if typekind == pythoncom.TKIND_ENUM:
        result["enum_members"] = _enum_members(ref_ti, int(ta.cVars))

    if typekind == pythoncom.TKIND_ALIAS:
        alias_targets = []
        for target_href in _find_user_defined_handles(ta.tdescAlias):
            alias_targets.append({
                "reference_handle": target_href,
                **_describe_referenced_type(
                    ref_ti, target_href, visited=visited
                ),
            })
        if alias_targets:
            result["alias_targets"] = alias_targets

    return result


def _argument_type_references(
    ti,
    member_name: str,
    member_kind: str,
    params: list[str],
    args: list[Any],
    referenced_types: dict[int, dict],
    uses: dict[int, list[dict]],
) -> list[dict]:
    argument_refs = []

    for index, arg_descriptor in enumerate(args):
        # FUNCDESC.args contains ELEMDESC tuples: (TYPEDESC, flags, default).
        type_descriptor = (
            arg_descriptor[0]
            if isinstance(arg_descriptor, (tuple, list))
            and len(arg_descriptor) == 3
            else arg_descriptor
        )
        seen_for_argument = set()
        for href in _find_user_defined_handles(type_descriptor):
            if href in seen_for_argument:
                continue
            seen_for_argument.add(href)
            if href not in referenced_types:
                referenced_types[href] = _describe_referenced_type(ti, href)

            metadata = referenced_types[href]
            argument = params[index] if index < len(params) else f"arg{index + 1}"
            use = {
                "member": member_name,
                "member_kind": member_kind,
                "argument": argument,
                "argument_index": index,
                "argument_position": index + 1,
                "reference_handle": href,
            }
            uses.setdefault(href, []).append(use)
            argument_refs.append({
                "argument": argument,
                "argument_index": index,
                "argument_position": index + 1,
                "reference_handle": href,
                "referenced_type_name": metadata.get("type_name"),
                "typekind": metadata.get("typekind"),
                "typekind_name": metadata.get("typekind_name"),
            })

    return argument_refs


def _collect_from_typeinfo(ti, *, depth: int, visited: set[str]) -> dict:
    ta = ti.GetTypeAttr()
    iid = str(ta.iid)
    if iid in visited:
        return {"iid": iid, "cycle": True, "members": [], "bases": []}
    visited.add(iid)

    type_name = _type_name(ti)

    members = []
    referenced_types: dict[int, dict] = {}
    referenced_type_uses: dict[int, list[dict]] = {}
    for i in range(int(ta.cFuncs)):
        try:
            fd = ti.GetFuncDesc(i)
            names = list(ti.GetNames(fd.memid))
        except Exception as exc:
            members.append({"index": i, "error": str(exc)[:200]})
            continue

        name = str(names[0]) if names else f"memid_{fd.memid}"
        params = [str(x) for x in names[1:]]
        kind = _INVOKE_KIND.get(fd.invkind, f"invoke_{fd.invkind}")
        row = {
            "name": name,
            "kind": kind,
            "memid": int(fd.memid),
            "signature": _format_signature(name, params, int(fd.cParamsOpt)),
            "params": params,
            "optional_params": int(fd.cParamsOpt),
            "funckind": int(fd.funckind),
            "invkind": int(fd.invkind),
            "wFuncFlags": int(fd.wFuncFlags),
        }
        doc = _safe_doc(ti, fd.memid)
        if doc and doc != name:
            row["doc"] = doc
        try:
            args = list(fd.args)
            row["arg_descriptors"] = [str(x) for x in args]
            row["return_descriptor"] = str(fd.rettype)
            argument_refs = _argument_type_references(
                ti,
                name,
                kind,
                params,
                args,
                referenced_types,
                referenced_type_uses,
            )
            if argument_refs:
                row["referenced_argument_types"] = argument_refs
        except Exception:
            pass
        members.append(row)

    bases = []
    if depth > 0:
        for i in range(int(ta.cImplTypes)):
            try:
                href = ti.GetRefTypeOfImplType(i)
                ref = ti.GetRefTypeInfo(href)
                flags = int(ti.GetImplTypeFlags(i))
                base = _collect_from_typeinfo(
                    ref, depth=depth - 1, visited=visited
                )
                base["impl_flags"] = flags
                bases.append(base)
            except Exception as exc:
                bases.append({"index": i, "error": str(exc)[:200]})

    return {
        "type_name": type_name,
        "iid": iid,
        "typekind": int(ta.typekind),
        "type_flags": int(ta.wTypeFlags),
        "cFuncs": int(ta.cFuncs),
        "cVars": int(ta.cVars),
        "cImplTypes": int(ta.cImplTypes),
        "members": members,
        "referenced_types": [
            {
                "reference_handle": href,
                **metadata,
                "used_by": referenced_type_uses.get(href, []),
            }
            for href, metadata in referenced_types.items()
        ],
        "bases": bases,
    }


def describe(obj: Any, inherited_depth: int = 2) -> dict:
    """Return direct COM ITypeInfo/FUNCDESC metadata without invoking members."""
    ti = _typeinfo_of(obj)
    raw = _collect_from_typeinfo(ti, depth=inherited_depth, visited=set())

    # Flatten and deduplicate into convenient method/property lists.
    flat = []
    referenced_types = []

    def walk(node: dict):
        flat.extend(node.get("members", []))
        for referenced_type in node.get("referenced_types", []):
            referenced_types.append({
                "owner_type": node.get("type_name"),
                **referenced_type,
            })
        for b in node.get("bases", []):
            if isinstance(b, dict):
                walk(b)

    walk(raw)

    seen = set()
    methods = []
    properties = []
    for m in flat:
        if "name" not in m:
            continue
        key = (m.get("name"), m.get("kind"), m.get("signature"))
        if key in seen:
            continue
        seen.add(key)
        if m.get("kind") == "method":
            methods.append(m)
        elif str(m.get("kind", "")).startswith("property_"):
            properties.append(m)

    return {
        "typeinfo": raw,
        "methods": methods,
        "properties": properties,
        "referenced_types": referenced_types,
        "counts": {
            "methods": len(methods),
            "properties": len(properties),
        },
    }


@guarded(classification=Classification.READ, module="typeinfo",
         channel=Channel.MCP_COM)
def com_typeinfo(path: str = "", module: str = "modeler",
                 inherited_depth: int = 2) -> str:
    """READ-ONLY. Read COM ITypeInfo/FUNCDESC metadata directly.

    Uses the COM type library rather than Python dir()/inspect(). Resolves
    VT_USERDEFINED parameter types through GetRefTypeInfo, reporting enum
    names, TYPEKIND information, members, and numeric values. Apart from the
    property reads needed to resolve the target path, no method or property of
    the inspected interface is called, so this is safe for read-only API
    discovery. An empty path inspects the Application object;
    'Design.Surfaces' inspects that object.
    """
    from . import com, objectpath

    app = com.connect(module)
    obj = objectpath.resolve(app, path, allow_leaf_value=False)
    return com.to_json({"path": path or "Application",
                        **describe(obj, inherited_depth=inherited_depth)})


TOOLS = (com_typeinfo,)


def register_tools(mcp: Any) -> list[str]:
    """Register the raw ITypeInfo discovery tool."""
    return register(mcp, TOOLS)
