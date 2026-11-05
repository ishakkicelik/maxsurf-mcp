"""Exhaustive installed typelib discovery, without application activation."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, deque
from pathlib import Path
from typing import Any

import pythoncom
import winreg

from . import session
from .audit import Channel, Classification
from .errors import MaxsurfCOMError, MaxsurfValidationError
from .guard import guarded, register
from .native_types import (
    TYPEKINDS, descriptor, error_record, identity_key, parameter, type_identity,
    validate_arguments, validate_value,
)


# Embedded GUIDs independently read from the installed executable resources.
LIBRARIES = {
    "modeler": "{E1F5C655-FE67-431B-AF09-CBCBD9E19B4D}",
    "stability": "{A55FAB0F-71A2-4141-BFFE-487A7F99E1D8}",
    "resistance": "{9D132543-7412-4797-ABE2-9A49822FC6B8}",
    "motions": "{594B0C05-5E5D-4DAE-A071-4E40C9D941F2}",
    "multiframe": "{D0E25EDC-CF0D-4C65-81DB-FCE5797A2125}",
    "vpp": "{C9CF14F6-38CD-458D-80B9-D0A62C8594D3}",
    "shape_editor": "{0C7E3187-60F4-43C1-92CB-32551545D856}",
}
HELPERS = {"stability_xmlgrid": "stability", "motions_xmlgrid": "motions",
           "vpp_xmlgrid": "vpp"}
INVOCATIONS = {1: "method", 2: "property_get", 4: "property_put",
               8: "property_putref"}


def _module(module: str) -> str:
    if not isinstance(module, str) or module not in (*LIBRARIES, *HELPERS, "all"):
        raise MaxsurfValidationError("unknown native inventory module", module=module)
    return HELPERS.get(module, module)


def _subkeys(key: Any) -> list[str]:
    return [winreg.EnumKey(key, index)
            for index in range(winreg.QueryInfoKey(key)[0])]


def _registered_sources(module: str) -> list[dict]:
    """Read both registry views and all registered versions/locales/platforms."""
    result = []
    seen = set()
    errors = []
    guid = LIBRARIES[module]
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f"TypeLib\\{guid}",
                                0, winreg.KEY_READ | view) as root:
                for version in _subkeys(root):
                    with winreg.OpenKey(root, version) as version_key:
                        for locale in _subkeys(version_key):
                            if locale in ("FLAGS", "HELPDIR"):
                                continue
                            with winreg.OpenKey(version_key, locale) as locale_key:
                                for platform in _subkeys(locale_key):
                                    if platform not in ("win32", "win64"):
                                        continue
                                    try:
                                        with winreg.OpenKey(locale_key, platform) as path_key:
                                            path = winreg.QueryValueEx(path_key, "")[0]
                                        key = (version, locale, platform, str(path).casefold())
                                        if key not in seen:
                                            seen.add(key)
                                            result.append({
                                                "module": module, "path": path,
                                                "registered_guid": guid,
                                                "registered_version": version,
                                                "registered_lcid": locale,
                                                "registered_platform": platform})
                                    except OSError as exc:
                                        errors.append(error_record(
                                            exc, f"registry:{guid}/{version}/{locale}/{platform}"))
        except FileNotFoundError:
            continue
        except OSError as exc:
            errors.append(error_record(exc, f"registry:{guid}:{view}"))
    if errors:
        result.append({"module": module, "errors": errors})
    if not result:
        result.append({"module": module, "errors": [{
            "code": "not_installed", "operation": "registered_typelib",
            "library_guid": guid}]})
    return result


def _coclass_registration(guid: str) -> dict:
    """Registration evidence only: no CLSID activation or application pointer."""
    result: dict[str, Any] = {"registered": False}
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f"CLSID\\{guid}",
                                0, winreg.KEY_READ | view) as key:
                result["registered"] = True
                for name in ("ProgID", "VersionIndependentProgID",
                             "LocalServer32", "InprocServer32"):
                    try:
                        with winreg.OpenKey(key, name) as child:
                            result[name] = winreg.QueryValueEx(child, "")[0]
                    except FileNotFoundError:
                        continue
        except FileNotFoundError:
            continue
        except OSError as exc:
            result.setdefault("errors", []).append(
                error_record(exc, f"coclass_registration:{guid}:{view}"))
    return result


def _help_reference(info: Any, memid: int) -> dict:
    name, _proprietary_help, context, help_file = info.GetDocumentation(memid)
    return {"name": name, "help_context": context, "help_file": help_file}


def _descriptor_errors(value: Any) -> list[dict]:
    result = []
    if isinstance(value, dict):
        if "error" in value:
            result.append(value["error"])
        result.extend(value.get("errors", []))
        for key, child in value.items():
            if key not in ("error", "errors"):
                result.extend(_descriptor_errors(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(_descriptor_errors(child))
    return result


def _shape_supported(value: dict) -> bool:
    if not value.get("supported", False):
        return False
    return all(_shape_supported(value[key]) for key in ("element", "target") if key in value)


def member_key(identity: dict, dispid: int, invocation_kind: int) -> str:
    """Stable coverage identity; GET and PUT are deliberately distinct."""
    return f"{identity_key(identity)}:{identity['guid']}:{dispid}:{invocation_kind}"


def _output_references(value: dict) -> list[str]:
    result = []
    if value.get("kind") in ("interface", "dispatch"):
        result.append(value["type_key"])
    for key in ("element", "target"):
        if key in value:
            result.extend(_output_references(value[key]))
    return result


def _read_type(info: Any, enqueue: Any) -> dict:
    identity = type_identity(info)
    attr = info.GetTypeAttr()
    key = identity_key(identity)
    result = {**identity, "type_key": key, "entry_kind": "type",
              "typekind_name": TYPEKINDS.get(attr.typekind, "unsupported"),
              "type_flags": attr.wTypeFlags,
              "declared_totals": {"member": attr.cFuncs, "variable": attr.cVars,
                                  "implementation": attr.cImplTypes},
              "members": [], "variables": [], "implementations": [], "errors": []}
    try:
        result.update(_help_reference(info, -1))
    except Exception as exc:
        result["errors"].append(error_record(exc, "type_documentation"))
    if attr.typekind == 5:
        result["registration"] = _coclass_registration(str(attr.iid))
        result["errors"].extend(result["registration"].get("errors", []))
    if attr.typekind == 6:
        result["alias"] = descriptor(info, attr.tdescAlias, enqueue=enqueue,
                                     seen=frozenset({key}))
    for index in range(attr.cImplTypes):
        try:
            handle = info.GetRefTypeOfImplType(index)
            ref = info.GetRefTypeInfo(handle)
            enqueue(ref)
            target = type_identity(ref)
            result["implementations"].append({
                "entry_kind": "implementation", "index": index,
                "type_key": key, "reference_handle": handle,
                "flags": info.GetImplTypeFlags(index),
                "reference": target, "target_key": identity_key(target)})
        except Exception as exc:
            result["implementations"].append({
                "entry_kind": "implementation", "index": index, "type_key": key,
                "error": error_record(exc, f"implementation[{index}]")})
    for index in range(attr.cFuncs):
        row = {"entry_kind": "member", "index": index, "type_key": key}
        try:
            func = info.GetFuncDesc(index)
            names = info.GetNames(func.memid)
            arguments = [
                parameter(info, raw, names[position + 1]
                          if len(names) > position + 1 else f"arg{position + 1}",
                          position, enqueue=enqueue)
                for position, raw in enumerate(func.args)]
            # Native unnamed put-value arguments are common, and remain argN.
            row.update(
                key=member_key(identity, func.memid, func.invkind),
                name=names[0], interface=identity["name"], interface_guid=identity["guid"],
                library_guid=identity["library_guid"],
                library_version=identity["library_version"],
                dispid=func.memid, invocation_kind=func.invkind,
                kind=INVOCATIONS.get(func.invkind, "unsupported"),
                function_kind=func.funckind, function_flags=func.wFuncFlags,
                callconv=func.callconv, optional_count=func.cParamsOpt,
                arguments=arguments,
                **{"return": descriptor(info, func.rettype[0], enqueue=enqueue)})
            row["return_flags"] = func.rettype[1]
            row.update(_help_reference(info, func.memid))
            supported = (func.invkind in INVOCATIONS and func.cParamsOpt != -1
                         and _shape_supported(row["return"])
                         and all(_shape_supported(arg["type"])
                                 and (arg["input"] or arg["output"]
                                      or arg["retval"] or arg["lcid"])
                                 for arg in arguments))
            row["contract_status"] = (
                "metadata_validated" if supported else "unsupported_value_shape")
            row["errors"] = _descriptor_errors(arguments) + _descriptor_errors(row["return"])
        except Exception as exc:
            row["error"] = error_record(exc, f"member[{index}]")
            row["contract_status"] = "metadata_error"
        result["members"].append(row)
    for index in range(attr.cVars):
        row = {"entry_kind": "variable", "index": index, "type_key": key}
        try:
            var = info.GetVarDesc(index)
            row.update(dispid=var[0], value=var[1], flags=var[3], variable_kind=var[4],
                       type=descriptor(info, var[2][0], enqueue=enqueue))
            row.update(_help_reference(info, var[0]))
        except Exception as exc:
            row["error"] = error_record(exc, f"variable[{index}]")
        result["variables"].append(row)
    return result


def _reachability(types: list[dict], module: str) -> None:
    by_key = {row["type_key"]: row for row in types if "type_key" in row}
    paths = {}
    edges = {}
    roots = [spec for name, spec in session.MODULES.items()
             if name == module or spec.requires_session == module]
    for key, row in by_key.items():
        edges[key] = []
        for spec in roots:
            if spec.application_iid and row["guid"].casefold() == spec.application_iid.casefold():
                paths[key] = [{
                    "kind": "session_root", "module": spec.name,
                    "interface_guid": spec.application_iid,
                    "progid": spec.progids[0], "progids": list(spec.progids),
                    "root_kind": spec.kind, "requires_session": spec.requires_session,
                    "executables": list(spec.executables),
                    "design_object_member": spec.design_object_member,
                    "requires_live_acceptance": True}]
        for impl in row["implementations"]:
            if "error" not in impl and not impl["flags"] & 2:  # FSOURCE is event sink
                edges[key].append((impl["target_key"],
                                   {"kind": "implemented_interface", "type_key": key,
                                    "index": impl["index"], "flags": impl["flags"]}))
                registration = row.get("registration", {})
                if row["typekind"] == 5 and registration.get("registered"):
                    paths.setdefault(impl["target_key"], [{
                        "kind": "registered_coclass", "coclass_guid": row["guid"],
                        "registration": registration,
                        "requires_live_acceptance": True}])
        for member in row["members"]:
            if "error" in member:
                continue
            outputs = [member["return"]] + [
                arg["type"] for arg in member["arguments"] if arg["output"]]
            for output in outputs:
                for target in _output_references(output):
                    edges[key].append((target, {
                        "kind": "member_result", "member_key": member["key"],
                        "member": member["name"],
                        "indexed": any(arg["input"] for arg in member["arguments"])}))
    queue = deque(paths)
    while queue:
        key = queue.popleft()
        for target, edge in edges.get(key, []):
            if target not in paths:
                paths[target] = paths[key] + [edge]
                queue.append(target)
    graph_errors = any(_descriptor_errors(row) for row in types)
    for key, row in by_key.items():
        status = ("metadata_reachable" if key in paths else
                  "reachability_unknown" if graph_errors else "not_metadata_reachable")
        row["reachability"] = {
            "status": status, "path": paths.get(key, []),
            "operationally_validated": False}
        for member in row["members"]:
            member["reachability"] = row["reachability"]


def inspect_library(library: Any, module: str, source: dict | None = None) -> dict:
    """Public fake/native boundary; exhaust every declared index and referenced type."""
    module = _module(module)
    if module == "all":
        raise MaxsurfValidationError("one application module is required for a library")
    guid, lcid, syskind, major, minor, flags = library.GetLibAttr()
    result = {"entry_kind": "library", "module": module,
              "library_guid": str(guid), "version": [major, minor], "lcid": lcid,
              "syskind": syskind, "flags": flags, "source": source or {},
              "declared_type_total": library.GetTypeInfoCount(),
              "types": [], "errors": []}
    pending = deque()
    queued = set()

    def enqueue(info: Any) -> None:
        key = identity_key(type_identity(info))
        if key not in queued:
            queued.add(key)
            pending.append(info)

    for index in range(result["declared_type_total"]):
        try:
            enqueue(library.GetTypeInfo(index))
        except Exception as exc:
            result["types"].append({
                "entry_kind": "type", "type_index": index,
                "error": error_record(exc, f"type[{index}]")})
    while pending:
        info = pending.popleft()
        try:
            result["types"].append(_read_type(info, enqueue))
        except Exception as exc:
            result["types"].append({
                "entry_kind": "type", "error": error_record(exc, "read_referenced_type")})
    _reachability(result["types"], module)
    result["errors"] = _descriptor_errors(result["types"])
    result["complete"] = not result["errors"]
    return result


def _source_identity(path: str) -> dict:
    file = Path(path)
    stat = file.stat()
    digest = hashlib.sha256()
    with file.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(file.resolve()), "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns, "sha256": digest.hexdigest()}


def get_inventory(module: str = "all") -> dict:
    """Read installed type resources on the caller's owned STA, never a session."""
    requested = module
    module = _module(module)
    selected = tuple(LIBRARIES) if module == "all" else (module,)
    libraries = []
    seen = set()
    for application in selected:
        for source in _registered_sources(application):
            if "path" not in source:
                libraries.append({"entry_kind": "library", "module": application,
                                  "source": source, "errors": source["errors"],
                                  "types": [], "complete": False})
                continue
            # The same embedded resource can be registered for several platforms.
            path_key = (application, str(source["path"]).casefold())
            if path_key in seen:
                continue
            seen.add(path_key)
            try:
                identity = _source_identity(source["path"])
                # LoadTypeLib never registers when a full path is supplied.
                # pywin32 exposes neither LoadTypeLibEx nor REGKIND_NONE.
                path = Path(source["path"]).resolve()
                if path.suffix.lower() not in (".exe", ".dll", ".tlb", ".olb", ".ocx"):
                    raise MaxsurfValidationError("unsupported installed typelib resource",
                                                 path=str(path))
                library = pythoncom.LoadTypeLib(str(path))
                item = inspect_library(library, application, {**source, **identity})
                if item["library_guid"].casefold() != LIBRARIES[application].casefold():
                    item["errors"].append({
                        "code": "library_identity_mismatch",
                        "expected": LIBRARIES[application],
                        "actual": item["library_guid"]})
                    item["complete"] = False
                libraries.append(item)
            except Exception as exc:
                libraries.append({"entry_kind": "library", "module": application,
                                  "source": source, "types": [], "complete": False,
                                  "errors": [error_record(exc, "load_typelib")]})
    entries = []
    for library in libraries:
        entries.append({key: value for key, value in library.items() if key != "types"})
        for row in library["types"]:
            entries.append({key: value for key, value in row.items()
                            if key not in ("members", "variables", "implementations")})
            for category in ("members", "variables", "implementations"):
                entries.extend(row.get(category, []))
    totals = dict(Counter(row["entry_kind"] for row in entries))
    expected = Counter()
    for library in libraries:
        expected["type"] += library.get("declared_type_total", 0)
        for row in library["types"]:
            expected.update(row.get("declared_totals", {}))
    result = {"schema_version": 1, "module": requested, "libraries": libraries,
              "entries": entries, "totals": totals, "declared_totals": dict(expected),
              "complete": all(row["complete"] for row in libraries),
              "evidence": "installed_metadata_only", "activated_applications": False}
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
    result["inventory_digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return result


def _invocation(value: int | str) -> int:
    if isinstance(value, str):
        value = next((key for key, name in INVOCATIONS.items() if name == value), None)
    if type(value) is not int or value not in INVOCATIONS:
        raise MaxsurfValidationError("unknown native invocation kind", invocation_kind=value)
    return value


def find_member(module: str, interface: str, member: str | int,
                invocation_kind: int | str = 1, *, inventory: dict | None = None) -> dict:
    """Resolve an exact native entry, refusing ambiguity and metadata errors."""
    _module(module)
    if not isinstance(interface, str) or not interface:
        raise MaxsurfValidationError("native interface must be an exact name or GUID")
    if type(member) not in (str, int) or member == "":
        raise MaxsurfValidationError("native member must be an exact name or DISPID")
    invkind = _invocation(invocation_kind)
    snapshot = get_inventory(module) if inventory is None else inventory
    if snapshot.get("module") not in (module, _module(module), "all"):
        raise MaxsurfValidationError("inventory module does not match requested member")
    candidates = []
    for library in snapshot["libraries"]:
        if library["module"] != _module(module) and module != "all":
            continue
        for row in library["types"]:
            if interface not in (row.get("name"), row.get("guid"), row.get("type_key")):
                continue
            for item in row.get("members", []):
                if (item.get("invocation_kind") == invkind and
                    member == (item.get("dispid") if type(member) is int else item.get("name"))):
                    candidates.append(item)
    if len(candidates) != 1:
        raise MaxsurfValidationError("native member is absent or ambiguous",
                                     interface=interface, member=member,
                                     invocation_kind=invkind, matches=len(candidates))
    result = candidates[0]
    if result.get("error") or result.get("errors"):
        raise MaxsurfCOMError("native member metadata is incomplete", member=result)
    return result


def reconcile_coverage(inventory: dict, ledger: list[dict]) -> dict:
    """Join complete member keys, keeping missing investigation and unknown keys visible."""
    if not isinstance(ledger, list) or any(not isinstance(row, dict) for row in ledger):
        raise MaxsurfValidationError("coverage ledger must be a list of entries")
    indexed = {}
    for row in ledger:
        key = row.get("key")
        if not isinstance(key, str) or not key or key in indexed:
            raise MaxsurfValidationError("coverage ledger keys must be unique native keys")
        status = row.get("status")
        if status not in ("uninvestigated", "metadata_admitted", "contract_validated",
                          "operationally_validated", "unsupported"):
            raise MaxsurfValidationError("unknown coverage status", status=status)
        if status == "unsupported" and not (row.get("blocker") and row.get("evidence")):
            raise MaxsurfValidationError("unsupported coverage requires blocker and evidence")
        if status in ("metadata_admitted", "contract_validated", "operationally_validated") and not (
                row.get("route") and row.get("classification") in Classification.ALL
                and row.get("evidence")):
            raise MaxsurfValidationError("validated coverage requires route, classification and evidence")
        indexed[key] = row
    members = [row for row in inventory["entries"]
               if row["entry_kind"] == "member" and "key" in row]
    keys = {row["key"] for row in members}
    rows = []
    for member in members:
        record = indexed.get(member["key"], {"key": member["key"], "status": "uninvestigated"})
        rows.append({**record, "reachability": member["reachability"],
                     "contract_status": member["contract_status"]})
    unknown = sorted(set(indexed) - keys)
    unresolved = [row["key"] for row in rows
                  if row["reachability"]["status"] in (
                      "metadata_reachable", "reachability_unknown")
                  and row["status"] == "uninvestigated"]
    return {"rows": rows, "unknown_keys": unknown, "unresolved_keys": unresolved,
            "totals": dict(Counter(row["status"] for row in rows)),
            "complete": inventory["complete"] and not unknown and not unresolved,
            "inventory_digest": inventory["inventory_digest"]}


@guarded(classification=Classification.READ, module="native_inventory",
         channel=Channel.MCP_COM)
def discover_native_api(module: str = "all", offset: int = 0, limit: int = 100,
                        kind: str = "all", inventory_digest: str = "") -> str:
    """READ-ONLY. Paginate all installed native metadata; never activate an application."""
    _module(module)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
        raise MaxsurfValidationError("inventory pagination requires offset >= 0 and limit 1..200")
    if kind not in ("all", "library", "type", "member", "variable", "implementation"):
        raise MaxsurfValidationError("unknown inventory entry kind", kind=kind)
    if not isinstance(inventory_digest, str):
        raise MaxsurfValidationError("inventory_digest must be a string")
    inventory = get_inventory(module)
    if inventory_digest and inventory_digest != inventory["inventory_digest"]:
        raise MaxsurfValidationError("installed inventory changed between pages",
                                     inventory_digest=inventory["inventory_digest"])
    entries = [row for row in inventory["entries"]
               if kind == "all" or row["entry_kind"] == kind]
    page = entries[offset:offset + limit]
    next_offset = offset + len(page) if offset + len(page) < len(entries) else None
    return json.dumps({
        "schema_version": 1, "module": module, "kind": kind, "entries": page,
        "offset": offset, "limit": limit, "total": len(entries),
        "totals": inventory["totals"], "declared_totals": inventory["declared_totals"],
        "next_offset": next_offset, "page_complete": next_offset is None,
        "inventory_complete": inventory["complete"],
        "complete": inventory["complete"] and offset == 0 and next_offset is None,
        "inventory_digest": inventory["inventory_digest"],
        "evidence": inventory["evidence"], "activated_applications": False,
        "errors": [error for library in inventory["libraries"] for error in library["errors"]],
    }, allow_nan=False)


@guarded(classification=Classification.READ, module="native_inventory",
         channel=Channel.MCP_COM)
def get_native_api_member(module: str, interface: str, member: str | int,
                          invocation_kind: int | str = 1) -> str:
    """READ-ONLY. Get exact installed member metadata, retaining GET/PUT separation."""
    return json.dumps(find_member(module, interface, member, invocation_kind),
                      allow_nan=False)


TOOLS = (discover_native_api, get_native_api_member)


def register_tools(mcp: Any) -> list[str]:
    return register(mcp, TOOLS)
