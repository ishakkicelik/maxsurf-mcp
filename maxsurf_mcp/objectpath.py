"""Safe resolution of dotted COM paths such as ``Design.Surfaces(1).Name``.

The previous resolver applied unrestricted ``getattr`` plus subscript access to
caller-supplied strings. Because pywin32 wrapper classes are ordinary Python
classes, that allowed a path to step out of the COM object graph and into the
interpreter, for example
``__class__.__init__.__globals__[sys].modules[os].system``, which combined with
``com_invoke`` gave arbitrary command execution.

This module replaces it with a resolver that enforces three rules:

1. **Name rules.** A component must look like a COM member name. Leading
   underscores and any ``__`` sequence are refused outright, so no dunder is
   reachable and pywin32 internals such as ``_oleobj_`` stay hidden.
2. **Graph rules.** Every intermediate value must be a live COM object. A
   value that turns out to be a Python type, module, function, mapping, or
   other plain object terminates resolution, so traversal cannot leave the COM
   graph even if a future Maxsurf build exposes something unexpected.
3. **Member rules.** pywin32 plumbing that would let a caller escape the COM
   abstraction (``Invoke``, ``SetProperty``, ``QueryInterface``, ...) is
   refused even though those names contain no underscore.

Maxsurf collection access keeps working: ``Surfaces(1)``, ``Surfaces[1]`` and
``ItemByName("Deck")`` all resolve, because indexing is applied only to
objects that already passed the COM check.
"""

from __future__ import annotations

import datetime
import re
from types import BuiltinFunctionType, FunctionType, MethodType, ModuleType
from typing import Any

from .errors import MaxsurfCOMError, MaxsurfSafetyError, MaxsurfValidationError

#: A COM member name: starts with a letter, then letters/digits/underscores.
#: Underscores are allowed in the interior because Maxsurf really does expose
#: members such as ``CONNECT_SetUndefinedProject``.
_MEMBER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

#: Characters permitted inside a string index (for example ItemByName keys).
_INDEX_TEXT_RE = re.compile(r"^[A-Za-z0-9 ._\-+#/\\]*$")

#: pywin32 / IDispatch plumbing. These carry no underscore, so the name rules
#: alone would let them through, but each one is a way to leave the safe
#: abstraction: Invoke and InvokeTypes dispatch arbitrary DISPIDs, SetProperty
#: writes arbitrary properties, QueryInterface hands back a raw interface.
_BLOCKED_MEMBERS = frozenset({
    "AddRef",
    "ApplyTypes",
    "GetIDsOfNames",
    "GetProperty",
    "GetTypeInfo",
    "GetTypeInfoCount",
    "Invoke",
    "InvokeTypes",
    "QueryInterface",
    "Rebind",
    "Release",
    "SetProperty",
})

_SCALAR_TYPES = (
    bool,
    int,
    float,
    complex,
    str,
    bytes,
    type(None),
    datetime.datetime,
    datetime.date,
    datetime.time,
)

_NEVER_COM_TYPES = (
    type,
    ModuleType,
    FunctionType,
    MethodType,
    BuiltinFunctionType,
    dict,
    list,
    tuple,
    set,
    frozenset,
)


def is_com_object(value: Any) -> bool:
    """Return True when ``value`` is a live COM dispatch wrapper.

    Both early-bound (``DispatchBaseClass``) and late-bound (``CDispatch``)
    pywin32 wrappers carry ``_oleobj_``. Python types, modules, callables, and
    containers never do, which is what keeps traversal inside the COM graph.
    ``_oleobj_`` itself is unreachable through a path because the name rules
    reject leading underscores.
    """
    if isinstance(value, _SCALAR_TYPES) or isinstance(value, _NEVER_COM_TYPES):
        return False
    if isinstance(value, type):
        return False
    try:
        return getattr(value, "_oleobj_", None) is not None
    except Exception:  # noqa: BLE001 - a hostile __getattr__ is not a COM object
        return False


def _is_scalar(value: Any) -> bool:
    return isinstance(value, _SCALAR_TYPES)


def _is_scalar_sequence(value: Any) -> bool:
    """True for COM out-parameter tuples and SAFEARRAYs of scalars."""
    if not isinstance(value, (tuple, list)):
        return False
    return all(
        _is_scalar(item) or _is_scalar_sequence(item) for item in value
    )


def classify(value: Any) -> str:
    """Classify a resolved value as ``com``, ``scalar``, ``sequence`` or ``forbidden``."""
    if is_com_object(value):
        return "com"
    if _is_scalar(value):
        return "scalar"
    if _is_scalar_sequence(value):
        return "sequence"
    return "forbidden"


def validate_member_name(name: str, *, path: str) -> str:
    """Raise unless ``name`` is an acceptable COM member name."""
    if not isinstance(name, str) or not name:
        raise MaxsurfValidationError(
            "COM path component must be a non-empty string", path=path
        )
    if name.startswith("_"):
        raise MaxsurfSafetyError(
            "COM path components may not start with an underscore",
            path=path,
            component=name,
        )
    if "__" in name:
        raise MaxsurfSafetyError(
            "dunder access is not permitted in a COM path",
            path=path,
            component=name,
        )
    if not _MEMBER_RE.match(name):
        raise MaxsurfSafetyError(
            "COM path component is not a valid COM member name",
            path=path,
            component=name,
        )
    if name in _BLOCKED_MEMBERS:
        raise MaxsurfSafetyError(
            "COM infrastructure members are not reachable through a path",
            path=path,
            component=name,
        )
    return name


def _parse_index(raw: str, *, path: str, component: str) -> Any:
    """Coerce an index token to an int, float, or restricted string."""
    token = raw.strip()
    if not token:
        raise MaxsurfValidationError(
            "collection index is empty", path=path, component=component
        )
    if any(bracket in token for bracket in "()[]"):
        raise MaxsurfSafetyError(
            "nested collection indexing is not supported",
            path=path,
            component=component,
        )
    quoted = (
        len(token) >= 2
        and token[0] == token[-1]
        and token[0] in "'\""
    )
    if quoted:
        token = token[1:-1]
    else:
        try:
            return int(token)
        except ValueError:
            pass
        try:
            return float(token)
        except ValueError:
            pass
    if "__" in token:
        raise MaxsurfSafetyError(
            "dunder access is not permitted in a collection index",
            path=path,
            component=component,
        )
    if not _INDEX_TEXT_RE.match(token):
        raise MaxsurfSafetyError(
            "collection index contains unsupported characters",
            path=path,
            component=component,
        )
    return token


def _split_component(part: str, *, path: str) -> tuple[str, Any | None]:
    """Split ``Surfaces(1)`` or ``Surfaces[1]`` into a name and an index."""
    component = part.strip()
    if not component:
        raise MaxsurfValidationError(
            "COM path contains an empty component", path=path
        )
    for opener, closer in (("(", ")"), ("[", "]")):
        if opener in component:
            if not component.endswith(closer):
                raise MaxsurfSafetyError(
                    "unbalanced collection index in COM path",
                    path=path,
                    component=component,
                )
            name = component[: component.index(opener)]
            raw = component[component.index(opener) + 1 : -1]
            index = _parse_index(raw, path=path, component=component)
            return validate_member_name(name, path=path), index
    return validate_member_name(component, path=path), None


def _getattr_com(obj: Any, name: str, *, path: str) -> Any:
    try:
        return getattr(obj, name)
    except AttributeError as exc:
        raise MaxsurfValidationError(
            f"COM member {name!r} does not exist on this object",
            path=path,
            component=name,
            object_type=type(obj).__name__,
        ) from exc
    except Exception as exc:  # noqa: BLE001 - a live COM getter failed
        raise MaxsurfCOMError(
            f"reading COM member {name!r} failed",
            cause=exc,
            path=path,
            component=name,
        ) from exc


def _apply_index(obj: Any, index: Any, *, path: str, component: str) -> Any:
    if not is_com_object(obj):
        raise MaxsurfSafetyError(
            "collection indexing is only allowed on COM objects",
            path=path,
            component=component,
            resolved_type=type(obj).__name__,
        )
    try:
        if callable(obj):
            return obj(index)
        return obj[index]
    except Exception as exc:  # noqa: BLE001 - out-of-range or unsupported index
        raise MaxsurfCOMError(
            "COM collection index access failed",
            cause=exc,
            path=path,
            component=component,
            index=index,
        ) from exc


def split_path(path: str) -> list[str]:
    """Split a dotted COM path into its raw components."""
    if path is None:
        return []
    if not isinstance(path, str):
        raise MaxsurfValidationError("COM path must be a string")
    return [part for part in path.split(".") if part.strip()]


def resolve(root: Any, path: str, *, allow_leaf_value: bool = True) -> Any:
    """Resolve a dotted COM path starting at ``root``.

    ``root`` must already be a COM object. Every intermediate value must also
    be a COM object; the final value may additionally be a scalar or a scalar
    sequence when ``allow_leaf_value`` is true.
    """
    if not is_com_object(root):
        raise MaxsurfSafetyError(
            "COM path resolution requires a COM object as its root",
            path=path,
            root_type=type(root).__name__,
        )

    components = split_path(path)
    obj: Any = root
    for position, part in enumerate(components):
        name, index = _split_component(part, path=path)
        if not is_com_object(obj):
            raise MaxsurfSafetyError(
                "COM path traversal left the COM object graph",
                path=path,
                component=part,
                resolved_type=type(obj).__name__,
            )
        obj = _getattr_com(obj, name, path=path)
        if index is not None:
            obj = _apply_index(obj, index, path=path, component=part)

        kind = classify(obj)
        is_last = position == len(components) - 1
        if kind == "forbidden":
            raise MaxsurfSafetyError(
                "COM path resolved to a value outside the COM object graph",
                path=path,
                component=part,
                resolved_type=type(obj).__name__,
            )
        if kind != "com" and not is_last:
            raise MaxsurfSafetyError(
                "only the final component of a COM path may be a value",
                path=path,
                component=part,
                resolved_type=type(obj).__name__,
            )
        if kind != "com" and is_last and not allow_leaf_value:
            raise MaxsurfValidationError(
                "COM path resolved to a value where an object was required",
                path=path,
                resolved_type=type(obj).__name__,
            )
    return obj


def resolve_parent(root: Any, path: str) -> tuple[Any, str]:
    """Resolve everything before the final component and return (parent, leaf).

    The parent is guaranteed to be a COM object and the leaf name is validated
    but not read, so this is the entry point for property writes and method
    invocation.
    """
    components = split_path(path)
    if not components:
        raise MaxsurfValidationError(
            "a COM member path is required", path=path
        )
    leaf_name, leaf_index = _split_component(components[-1], path=path)
    if leaf_index is not None:
        raise MaxsurfSafetyError(
            "an indexed component cannot be the target of a write or call",
            path=path,
            component=components[-1],
        )
    parent_path = ".".join(components[:-1])
    parent = resolve(root, parent_path, allow_leaf_value=False)
    if not is_com_object(parent):
        raise MaxsurfSafetyError(
            "the parent of a COM member must be a COM object",
            path=path,
            resolved_type=type(parent).__name__,
        )
    return parent, leaf_name
