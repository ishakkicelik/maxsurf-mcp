"""Read-only inspection of running Windows processes.

This exists because of a Maxsurf-specific fact proven on the live system: both
``MaxsurfModeler.exe`` and ``MaxsurfStability.exe`` can be visibly running, in
the same Windows session as the MCP server, and still be absent from the Running
Object Table. ``GetActiveObject`` returns ``MK_E_UNAVAILABLE`` for both. Maxsurf
does not register its Application object in the ROT.

So "is the application already running?" cannot be answered by COM. It is
answered here, by asking the operating system which processes exist, and that
answer is what gates the COM activation policy in :mod:`maxsurf_mcp.session`.

Nothing here touches COM, starts a process, or signals a process.
"""

from __future__ import annotations

from typing import Any

try:  # pragma: no cover - exercised on Windows only
    import win32api
    import win32process
except ImportError:  # importable on non-Windows for offline reading
    win32api = None
    win32process = None

#: PROCESS_QUERY_LIMITED_INFORMATION. Enough to read a process image path and
#: available for processes this account may not fully open. Verified on the live
#: system to resolve more processes than PROCESS_QUERY_INFORMATION|VM_READ.
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

#: The older pairing, used only if the limited right is refused.
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010


def available() -> bool:
    """True when process inspection is possible at all."""
    return win32api is not None and win32process is not None


def _image_path(pid: int) -> str | None:
    """Return a process's image path, or None if it cannot be read.

    A process that cannot be opened is not an error: protected and
    higher-integrity processes are expected to refuse, and Maxsurf is neither.
    """
    for access in (PROCESS_QUERY_LIMITED_INFORMATION,
                   PROCESS_QUERY_INFORMATION | PROCESS_VM_READ):
        try:
            handle = win32api.OpenProcess(access, False, pid)
        except Exception:  # noqa: BLE001 - access denied or already exited
            continue
        try:
            return win32process.GetModuleFileNameEx(handle, 0)
        except Exception:  # noqa: BLE001 - exited between open and query
            continue
        finally:
            handle.Close()
    return None


def find(executables: tuple[str, ...] | list[str]) -> dict[str, Any]:
    """Return every running process whose image name matches ``executables``.

    ``supported`` distinguishes "no matching process is running" from "this
    build cannot tell", which matters: a policy that gates COM activation on a
    process being present must refuse rather than guess when it cannot look.
    """
    wanted = {name.casefold() for name in executables if name}
    result: dict[str, Any] = {
        "supported": available(),
        "requested": sorted(wanted),
        "matches": [],
        "pids": (),
        "inspected": 0,
        "detail": None,
    }
    if not wanted:
        result["detail"] = "no executable name is known for this module"
        return result
    if not available():
        result["detail"] = "pywin32 process inspection is unavailable"
        return result

    try:
        pids = win32process.EnumProcesses()
    except Exception as exc:  # noqa: BLE001
        result["supported"] = False
        result["detail"] = f"process enumeration failed: {str(exc)[:200]}"
        return result

    matches = []
    for pid in pids:
        path = _image_path(pid)
        if path is None:
            continue
        name = path.replace("/", "\\").rsplit("\\", 1)[-1]
        if name.casefold() in wanted:
            matches.append({"pid": int(pid), "name": name, "path": path})
    matches.sort(key=lambda entry: entry["pid"])
    result["matches"] = matches
    result["pids"] = tuple(entry["pid"] for entry in matches)
    result["inspected"] = len(pids)
    return result


def pids_of(executables: tuple[str, ...] | list[str]) -> tuple[int, ...]:
    """Return just the matching process ids."""
    return tuple(find(executables)["pids"])
