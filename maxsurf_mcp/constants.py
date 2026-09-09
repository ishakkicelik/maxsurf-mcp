"""Read-only discovery of constants from pywin32 makepy modules.

The connected COM object is used only to identify its generated Python
wrapper class.  Discovery deliberately avoids COM attribute access,
ITypeInfo calls, property getters, and application methods.
"""
from __future__ import annotations

import importlib
from collections.abc import Mapping
from types import ModuleType
from typing import Any

from .audit import Channel, Classification
from .guard import guarded, register

try:
    from win32com.client import constants as client_constants
    from win32com.client import gencache
except ImportError:  # Windows disi ortamda import edilebilsin
    client_constants = None
    gencache = None


_MAKEPY_PREFIX = "win32com.gen_py."
_MAKEPY_METADATA = {"LCID", "LibraryFlags", "MajorVersion", "MinorVersion"}


def _numeric_constants(namespace: Mapping[str, Any]) -> dict[str, int | float]:
    """Return public numeric values while excluding makepy metadata."""
    found: dict[str, int | float] = {}
    for name, value in namespace.items():
        if name.startswith("_") or name in _MAKEPY_METADATA:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        found[name] = value
    return found


def _class_value(cls: type[Any], name: str) -> Any:
    """Read a generated wrapper class field without touching the COM object."""
    for base in cls.__mro__:
        namespace = vars(base)
        if name in namespace:
            return namespace[name]
    return None


def _root_makepy_name(module_name: str) -> str | None:
    if not module_name.startswith(_MAKEPY_PREFIX):
        return None
    package = module_name[len(_MAKEPY_PREFIX):].split(".", 1)[0]
    return f"{_MAKEPY_PREFIX}{package}" if package else None


def _import_module(name: str | None) -> ModuleType | None:
    if not name:
        return None
    try:
        return importlib.import_module(name)
    except (ImportError, ModuleNotFoundError):
        return None


def _append_module(modules: list[ModuleType], module: Any) -> None:
    if not isinstance(module, ModuleType):
        return
    if all(existing.__name__ != module.__name__ for existing in modules):
        modules.append(module)


def _generated_modules(app: Any) -> list[ModuleType]:
    """Locate makepy modules solely from static Python wrapper metadata."""
    wrapper_class = type(app)
    wrapper_module_name = wrapper_class.__module__
    modules: list[ModuleType] = []

    wrapper_module = _import_module(wrapper_module_name)
    _append_module(modules, wrapper_module)
    _append_module(modules, _import_module(_root_makepy_name(wrapper_module_name)))

    clsid = _class_value(wrapper_class, "CLSID")
    if clsid is not None and gencache is not None:
        try:
            cached_module = gencache.GetModuleForCLSID(clsid)
        except Exception:  # noqa: BLE001 - fall back to loaded/global metadata
            cached_module = None
        _append_module(modules, cached_module)
        cached_name = getattr(cached_module, "__name__", "")
        _append_module(modules, _import_module(_root_makepy_name(cached_name)))

    return modules


def _module_constants(module: ModuleType) -> dict[str, int | float]:
    """Extract enum values from common makepy module layouts."""
    namespace = vars(module)
    found: dict[str, int | float] = {}

    constants_container = namespace.get("constants")
    if isinstance(constants_container, type):
        found.update(_numeric_constants(vars(constants_container)))
    elif constants_container is not None:
        try:
            found.update(_numeric_constants(vars(constants_container)))
        except TypeError:
            pass

    # Some generators expose enum containers or values directly in the module.
    for value in namespace.values():
        if isinstance(value, type) and value is not constants_container:
            for name, number in _numeric_constants(vars(value)).items():
                found.setdefault(name, number)
    for name, number in _numeric_constants(namespace).items():
        found.setdefault(name, number)

    return found


def _global_constants() -> dict[str, int | float]:
    """Read pywin32's aggregate constants dictionaries without COM access."""
    if client_constants is None:
        return {}
    dictionaries = vars(client_constants).get("__dicts__", ())
    found: dict[str, int | float] = {}
    for namespace in dictionaries:
        if not isinstance(namespace, Mapping):
            continue
        for name, number in _numeric_constants(namespace).items():
            # Match win32com.client.constants lookup precedence: first wins.
            found.setdefault(name, number)
    return found


def discover(app: Any, name_filter: str = "") -> dict[str, Any]:
    """Discover numeric constants for a connected early-bound COM wrapper."""
    modules = _generated_modules(app)
    constants: dict[str, int | float] = {}
    for module in modules:
        for name, number in _module_constants(module).items():
            constants.setdefault(name, number)

    source = "generated_makepy"
    if not constants:
        constants = _global_constants()
        source = "win32com.client.constants"

    needle = name_filter.casefold().strip()
    if needle:
        constants = {
            name: value
            for name, value in constants.items()
            if needle in name.casefold()
        }

    ordered = dict(sorted(constants.items(), key=lambda item: item[0].casefold()))
    return {
        "source": source,
        "generated_modules": [module.__name__ for module in modules],
        "filter": name_filter or None,
        "count": len(ordered),
        "constants": ordered,
    }


@guarded(classification=Classification.READ, module="constants",
         channel=Channel.MCP_COM)
def com_constants(filter: str = "", module: str = "modeler") -> str:
    """READ-ONLY. Discover numeric constants from the generated type library.

    filter is a case-insensitive fragment of a constant name, for example
    msSST, msDT, msSL, Edge or Bond. No Maxsurf property getter or COM method
    is called.
    """
    from . import com

    app = com.connect(module)
    return com.to_json({"module": module,
                        **discover(app, name_filter=filter)})


TOOLS = (com_constants,)


def register_tools(mcp: Any) -> list[str]:
    """Register the constant-discovery tool."""
    return register(mcp, TOOLS)
