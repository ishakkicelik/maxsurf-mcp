"""Bounded English-UI Modeler display controls; no arbitrary keys or commands.

Rendering is not exposed by the Modeler COM API. These audited UI tools use
observed accessibility controls and run on the owned STA worker. No file menu,
model geometry, license setting or other application is manipulated.
"""
from __future__ import annotations
import hashlib
from . import com, comthread, config, processes, workspace
from .audit import Channel, Classification
from .errors import MaxsurfSafetyError
from .guard import guarded, register

# STATE_SYSTEM_CHECKED, documented MSAA state bit; do not infer from colour.
CHECKED = 0x10
MIXED = 0x20
ALLOWED = {
    "Render": ("Rendering", "Render Transparent", "Light1"),
    "Visibility": ("Sections", "Waterlines", "Buttocks", "Edges"),
    "Display": ("Surf.Net",),
}


def _run(call):
    return comthread.run(call, timeout=config.com_timeout(), description="Modeler display UI")


def _window():
    if "modeler" not in config.allowed_modules():
        raise MaxsurfSafetyError("Modeler is disabled by the allowed-modules policy")
    import win32gui as gui
    import win32process
    found = processes.find(("MaxsurfModeler.exe",))
    if len(found["pids"]) != 1:
        raise MaxsurfSafetyError("exactly one running Modeler process required", pids=found["pids"])
    pid = found["pids"][0]
    windows = []
    def visit(hwnd, _):
        if gui.IsWindowVisible(hwnd) and win32process.GetWindowThreadProcessId(hwnd)[1] == pid:
            if "maxsurf modeler" in gui.GetWindowText(hwnd).casefold() and gui.GetClassName(hwnd) != "#32770":
                windows.append(hwnd)
    gui.EnumWindows(visit, None)
    if len(windows) != 1:
        raise MaxsurfSafetyError("unique visible Modeler window not found", count=len(windows))
    return gui, windows[0], pid


def _expected(gui, hwnd, title):
    if not title or gui.GetWindowText(hwnd) != title or not gui.IsWindowEnabled(hwnd):
        raise MaxsurfSafetyError("Modeler title changed or a modal dialog is open; inspect before retrying")


def _uia(hwnd):
    from pywinauto import Desktop
    return Desktop(backend="uia").window(handle=hwnd).wrapper_object()


def _unique(nodes, name):
    matches = [n for n in nodes if n.element_info.name == name and n.is_visible()]
    if len(matches) != 1:
        raise MaxsurfSafetyError("unique visible English UI control required", control=name, count=len(matches))
    return matches[0]


def _button(window, toolbar, name):
    if toolbar not in ALLOWED or name not in ALLOWED[toolbar]:
        raise MaxsurfSafetyError("display control is not allowlisted")
    bar = _unique(window.descendants(control_type="ToolBar", depth=2), toolbar)
    return _unique(bar.children(), name)


def _checked(button):
    state = button.legacy_properties().get("State")
    if type(state) is not int or state & MIXED:
        raise MaxsurfSafetyError("native toggle state unavailable or indeterminate")
    return bool(state & CHECKED)


def _set_checked(button, desired):
    if not button.is_enabled():
        raise MaxsurfSafetyError("native display control is disabled")
    before = _checked(button)
    if before != desired:
        button.invoke()
    after = _checked(button)
    if after != desired:
        raise MaxsurfSafetyError("native display toggle did not retain requested state", requested=desired, actual=after)
    return {"before": before, "after": after, "changed": before != after}


@guarded(classification=Classification.READ, module="modeler", channel=Channel.UI)
def inspect_modeler_display() -> str:
    """Read only Modeler's known rendering/contour controls and view names."""
    def operation():
        gui, hwnd, pid = _window()
        window = _uia(hwnd)
        states = {}
        for toolbar, names in ALLOWED.items():
            for name in names:
                key = toolbar + "/" + name
                try:
                    button = _button(window, toolbar, name)
                    states[key] = {"checked": _checked(button), "enabled": button.is_enabled()}
                except Exception as exc:
                    states[key] = {"unavailable": str(exc)}
        return com.to_json({"pid": pid, "window_title": gui.GetWindowText(hwnd),
            "window_enabled": bool(gui.IsWindowEnabled(hwnd)), "controls": states,
            "views": [n.element_info.name for n in window.descendants(control_type="Window", depth=2)
                      if n.element_info.name in ("Perspective", "Plan", "Profile", "Body Plan")],
            "toolbar_buttons": {bar.element_info.name:[b.element_info.name for b in bar.children()]
                for bar in window.descendants(control_type="ToolBar",depth=2)
                if bar.element_info.name in ALLOWED},
            "channel": "ui", "geometry_changed": False})
    return _run(operation)


@guarded(classification=Classification.WRITE_STATE, module="modeler", channel=Channel.UI)
def configure_modeler_rendering(expected_window_title: str, rendered: bool = True,
                                 transparent: bool = False, show_lines: bool = True) -> str:
    """Activate Perspective and set rendering/transparency/contour toggles idempotently.

    Requires the exact title from inspect_modeler_display and visible English
    Render, Visibility and Display toolbars. Leaves surface colours and the
    user's shading algorithm unchanged. show_lines controls sections, waterlines,
    buttocks and edges; hides the control net. No geometry is created or edited.
    Partial display changes are possible on error; inspect before retrying.
    """
    def operation():
        gui, hwnd, pid = _window()
        _expected(gui, hwnd, expected_window_title)
        window = _uia(hwnd)
        perspective = _unique(window.descendants(control_type="Window", depth=2), "Perspective")
        specs = [("Render", "Rendering", rendered), ("Render", "Render Transparent", transparent),
                 ("Display", "Surf.Net", False)]
        specs += [("Visibility", name, show_lines) for name in ALLOWED["Visibility"]]
        # Resolve all controls before changing focus/settings.
        controls = [(bar, name, value, _button(window, bar, name)) for bar, name, value in specs]
        perspective.set_focus()
        changes = {}
        for bar, name, value, button in controls:
            _expected(gui, hwnd, expected_window_title)
            changes[bar + "/" + name] = _set_checked(button, value)
        return com.to_json({"window_title": gui.GetWindowText(hwnd), "controls": changes,
            "verified": "native_checked_states", "geometry_changed": False,
            "shading_algorithm": "unchanged; this tool does not configure curvature colouring",
            "channel": "ui"})
    return _run(operation)


def _capture_native(gui, hwnd, target):
    import ctypes
    from ctypes import wintypes
    import win32ui
    from PIL import Image
    user32 = ctypes.windll.user32
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    previous = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    if not previous:
        raise MaxsurfSafetyError("could not establish physical-pixel capture context")
    hdc = source = dest = bmp = None
    try:
        left, top, right, bottom = gui.GetWindowRect(hwnd)
        width, height = right-left, bottom-top
        if gui.IsIconic(hwnd) or width < 100 or height < 100 or width*height > 30000000:
            raise MaxsurfSafetyError("window minimized or capture dimensions out of bounds")
        hdc = gui.GetWindowDC(hwnd)
        source = win32ui.CreateDCFromHandle(hdc)
        dest = source.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(source, width, height)
        dest.SelectObject(bmp)
        user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        user32.PrintWindow.restype = wintypes.BOOL
        if not user32.PrintWindow(hwnd, dest.GetSafeHdc(), 2):
            raise MaxsurfSafetyError("native PrintWindow failed")
        img = Image.frombuffer("RGB", (width, height), bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            img.save(stream, format="PNG")
    finally:
        _release_capture(gui, hwnd, hdc, dest, bmp, user32, previous)


@guarded(classification=Classification.WRITE_FILE, module="modeler", channel=Channel.UI)
def capture_modeler_view(path: str, expected_window_title: str) -> str:
    """Capture only Modeler's own window as PNG without global screen capture.

    Must be in an allowed workspace; never overwrites. OpenGL/remote/minimized
    window capture may be incomplete. A PNG is not numerical validation.
    """
    target = workspace.resolve_target(path, purpose="capture_modeler_view", overwrite=False, allowed_suffixes=(".png",))
    def operation():
        gui, hwnd, pid = _window()
        _expected(gui, hwnd, expected_window_title)
        _capture_native(gui, hwnd, target)
        data = target.read_bytes()
        return com.to_json({"path": str(target), "window_title": gui.GetWindowText(hwnd),
            "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "capture_method": "PrintWindow", "geometry_changed": False})
    return _run(operation)


TOOLS = (inspect_modeler_display, configure_modeler_rendering, capture_modeler_view)


def register_tools(mcp):
    return register(mcp, TOOLS)


def _release_capture(gui, hwnd, hdc, dest, bmp, user32, previous):
    """Delete only the owned compatible DC; always restore thread DPI state."""
    failures=[]
    actions=[]
    # Delete the memory DC first so its selected bitmap is no longer selected.
    if dest is not None: actions.append(dest.DeleteDC)
    if bmp is not None: actions.append(lambda:gui.DeleteObject(bmp.GetHandle()))
    # GetWindowDC is borrowed: ReleaseDC, never source.DeleteDC.
    if hdc is not None: actions.append(lambda:gui.ReleaseDC(hwnd,hdc))
    actions.append(lambda:user32.SetThreadDpiAwarenessContext(previous))
    for action in actions:
        try: action()
        except Exception as exc: failures.append(str(exc))
    if failures:
        raise MaxsurfSafetyError("native capture cleanup failed", details=failures)
