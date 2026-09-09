"""Proven Maxsurf Modeler lifecycle and geometry semantic tools.

Every member used here was read from live ``ITypeInfo`` and is recorded in
``private discovery notes (not distributed)``. Tools whose members could not be proven were moved to
:mod:`maxsurf_mcp.legacy` rather than left in the production surface.
"""

from __future__ import annotations

import json
import os
from typing import Any

from . import audit, com, workspace
from . import constants as comconstants
from .audit import Channel, Classification
from .errors import MaxsurfCOMError, MaxsurfSafetyError, MaxsurfValidationError
from .guard import guarded, register


def _surface_at(surfaces: Any, index: int) -> tuple[Any, int]:
    """Return a one-based surface after validating it against Count."""
    if isinstance(index, bool) or not isinstance(index, int):
        raise MaxsurfValidationError(
            "surface index must be an integer", received=repr(index)
        )
    count = int(surfaces.Count)
    if index < 1 or index > count:
        raise MaxsurfValidationError(
            "surface index is outside the valid range",
            index=index,
            valid_range=f"1..{count}",
        )
    return surfaces(index), count


def _control_point_limits(surface: Any) -> tuple[int, int]:
    """Read the proven ISurface.ControlPointLimits out values."""
    rows, columns = surface.ControlPointLimits()
    return int(rows), int(columns)


def _control_point(surface: Any, row: int, column: int) -> dict[str, Any]:
    """Read one point through the proven ISurface.GetControlPoint method."""
    values = surface.GetControlPoint(row, column, 0.0, 0.0, 0.0)
    if not isinstance(values, (tuple, list)) or len(values) != 3:
        raise MaxsurfCOMError(
            "GetControlPoint did not return longitudinal/offset/vertical",
            row=row,
            column=column,
            returned_type=type(values).__name__,
        )
    return {
        "row": row,
        "column": column,
        "longitudinal": float(values[0]),
        "offset": float(values[1]),
        "vertical": float(values[2]),
    }


def _validate_control_point(
        row: int, column: int, rows: int, columns: int) -> None:
    if isinstance(row, bool) or not isinstance(row, int):
        raise MaxsurfValidationError("row must be an integer", received=repr(row))
    if isinstance(column, bool) or not isinstance(column, int):
        raise MaxsurfValidationError(
            "column must be an integer", received=repr(column)
        )
    if row < 1 or row > rows:
        raise MaxsurfValidationError(
            "row is outside the valid range", row=row, valid_range=f"1..{rows}"
        )
    if column < 1 or column > columns:
        raise MaxsurfValidationError(
            "column is outside the valid range",
            column=column,
            valid_range=f"1..{columns}",
        )


def _enum_name(app: Any, prefix: str, value: Any) -> str | None:
    """Resolve an enum value through generated COM constants at runtime."""
    discovered = comconstants.discover(app, name_filter=prefix)
    for name, number in discovered["constants"].items():
        if name.startswith(prefix) and number == value:
            return name
    return None


def _surface_summary_payload(app: Any, surface: Any, index: int) -> dict[str, Any]:
    rows, columns = _control_point_limits(surface)
    surface_type = surface.Type
    surface_use = surface.Use
    return {
        "index": index,
        "name": surface.Name,
        "type": {
            "value": surface_type,
            "enum_name": _enum_name(app, "msST", surface_type),
        },
        "use": {
            "value": surface_use,
            "enum_name": _enum_name(app, "msSU", surface_use),
        },
        "symmetrical": bool(surface.Symmetrical),
        "visible": bool(surface.Visible),
        "rows": rows,
        "columns": columns,
    }


def _accept_design_identity(expected_path: str) -> dict[str, Any]:
    """Refresh the managed session after this module changed the open file.

    The session layer deliberately treats an unexpected design-path change as a
    possible user/application swap.  ``Design.Open`` and ``Design.SaveAs`` are
    the two exceptions: this tool made the change itself and knows the exact
    sandbox-resolved path it requested.  Accept the new identity only when the
    path Maxsurf reports matches that path; never silently bless an arbitrary
    design that appeared while the COM call was running.

    Offline unit tests commonly patch :func:`com.connect` without installing a
    managed session.  That is reported as ``unmanaged`` rather than guessed.
    """
    established = com.session_for("modeler")
    if established is None:
        return {
            "status": "unmanaged",
            "expected_path": expected_path,
            "actual_path": None,
        }

    established.refresh_design_identity()
    actual = established.design_path
    matches = bool(actual) and os.path.normcase(os.path.normpath(actual)) == \
        os.path.normcase(os.path.normpath(expected_path))
    if not matches:
        established.mark_stale(
            "Design.Open/SaveAs returned with an unexpected design identity"
        )
        raise MaxsurfSafetyError(
            "Maxsurf did not report the design path requested by this tool; "
            "the new identity was not accepted",
            expected_design=expected_path,
            actual_design=actual,
            actual_status=established.design_path_status,
            hint="call reset_connection and inspect the design visible in "
                 "Maxsurf before making another change",
        )
    return {
        "status": "accepted",
        "expected_path": expected_path,
        "actual_path": actual,
        "swap_detection": established.design_swap_detection,
    }


@guarded(classification=Classification.READ, module="modeler",
         channel=Channel.MCP_COM)
def surface_summary(index: int = 1) -> str:
    """READ-ONLY. Summarize one surface through proven ISurface properties."""
    app = com.connect("modeler")
    surface, _count = _surface_at(app.Design.Surfaces, index)
    return com.to_json(_surface_summary_payload(app, surface, index))


@guarded(classification=Classification.WRITE_STATE, module="modeler",
         channel=Channel.MCP_COM)
def open_design(path: str, merge: bool = False,
                save_current: bool = True, confirm_discard: bool = False) -> str:
    """WRITE_STATE. Open a .msd design through the proven Design.Open.

    The path must be absolute and inside a configured workspace root. Note
    that save_current=False discards unsaved changes in the currently open
    model; confirm_discard=True is also required. Defaults request saving.
    Native save dialogs can block the operation; do not retry blindly.
    """
    if not save_current and not merge and not confirm_discard:
        raise MaxsurfSafetyError("discarding the current design requires explicit confirm_discard=True")
    source = workspace.resolve_existing(
        path, purpose="open_design",
        allowed_suffixes=workspace.DESIGN_SUFFIXES,
    )
    audit.set_design_path(str(source))
    app = com.connect("modeler")
    app.Design.Open(str(source), merge, save_current)
    identity = _accept_design_identity(str(source))
    return com.to_json({
        "opened": str(source),
        "via": "Design.Open",
        "merge": merge,
        "save_current": save_current,
        "session_identity": identity,
        "warnings": (
            [] if save_current
            else ["unsaved changes in the previously open model were discarded"]
        ),
    })


@guarded(classification=Classification.WRITE_FILE, module="modeler",
         channel=Channel.MCP_COM)
def save_design(path: str = "", overwrite: bool = False) -> str:
    """WRITE_FILE. Save the design to an explicit path through Design.SaveAs.

    In-place Design.Save() is deliberately disabled: it overwrites the user's
    open file with no backup, and the Modeler IDesign property that would
    reveal the current path has not been proven, so a backup cannot be taken
    safely yet. Always pass an absolute path inside a workspace root; an
    existing file requires overwrite=True.
    """
    if not path or not path.strip():
        raise MaxsurfSafetyError(
            "in-place save is disabled; pass an explicit target path",
            reason="Design.Save() overwrites the open file with no backup and "
                   "no proven way to locate it first",
            required="absolute .msd path inside a workspace root",
        )
    target = workspace.resolve_target(
        path, purpose="save_design", overwrite=overwrite,
        allowed_suffixes=workspace.DESIGN_SUFFIXES,
    )
    audit.set_design_path(str(target))
    existed = target.exists()
    app = com.connect("modeler")
    app.Design.SaveAs(str(target), overwrite)
    identity = _accept_design_identity(str(target))
    return com.to_json({
        "saved": str(target),
        "via": "Design.SaveAs",
        "overwrite": overwrite,
        "replaced_existing_file": existed,
        "session_identity": identity,
    })


@guarded(classification=Classification.READ, module="modeler",
         channel=Channel.MCP_COM)
def list_surfaces() -> str:
    """READ-ONLY. List surfaces using the proven semantic summary fields."""
    app = com.connect("modeler")
    surfaces = app.Design.Surfaces
    output = [
        _surface_summary_payload(app, surfaces(i), i)
        for i in range(1, int(surfaces.Count) + 1)
    ]
    return com.to_json({"surfaces": output})


@guarded(classification=Classification.READ, module="modeler",
         channel=Channel.MCP_COM)
def get_control_net(index: int = 1) -> str:
    """READ-ONLY. Return every point using ISurface.GetControlPoint."""
    app = com.connect("modeler")
    surface, _count = _surface_at(app.Design.Surfaces, index)
    rows, columns = _control_point_limits(surface)
    points = [
        _control_point(surface, row, column)
        for row in range(1, rows + 1)
        for column in range(1, columns + 1)
    ]
    return com.to_json({
        "index": index,
        "rows": rows,
        "columns": columns,
        "points": points,
    })


@guarded(classification=Classification.WRITE_GEOMETRY, module="modeler",
         channel=Channel.MCP_COM)
def set_control_point(index: int, row: int, column: int,
                      longitudinal: float, offset: float,
                      vertical: float) -> str:
    """WRITE. Validate bounds, then make exactly one SetControlPoint call."""
    app = com.connect("modeler")
    surface, _count = _surface_at(app.Design.Surfaces, index)
    rows, columns = _control_point_limits(surface)
    _validate_control_point(row, column, rows, columns)
    before = _control_point(surface, row, column)
    target = (float(longitudinal), float(offset), float(vertical))
    surface.SetControlPoint(row, column, *target)
    after = _control_point(surface, row, column)
    return com.to_json({
        "index": index,
        "row": row,
        "column": column,
        "before": before,
        "after": after,
        "set_control_point_calls": 1,
    })


@guarded(classification=Classification.WRITE_GEOMETRY, module="modeler",
         channel=Channel.MCP_COM)
def create_library_surface(shape: str, name: str | None = None) -> str:
    """WRITE. Create one library surface from a dynamically resolved msSL name.

    Calls ISurfaces.Add exactly once. If name is supplied, the proven Name
    property-put is applied to the newly created surface.
    """
    if not isinstance(shape, str) or not shape.strip():
        raise MaxsurfValidationError("shape must be a non-empty msSL enum name")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise MaxsurfValidationError(
            "name must be a non-empty string when supplied"
        )

    app = com.connect("modeler")
    surfaces = app.Design.Surfaces
    discovered = comconstants.discover(app, name_filter="msSL")
    library = {
        enum_name: number
        for enum_name, number in discovered["constants"].items()
        if enum_name.startswith("msSL")
    }
    canonical = next(
        (enum_name for enum_name in library
         if enum_name.casefold() == shape.strip().casefold()),
        None,
    )
    if canonical is None:
        raise MaxsurfValidationError(
            "unknown msSurfaceLibrary enum name",
            shape=shape,
            available_shapes=sorted(library),
        )

    count_before = int(surfaces.Count)
    surfaces.Add(library[canonical])
    count_after = int(surfaces.Count)
    if count_after != count_before + 1:
        raise MaxsurfCOMError(
            "ISurfaces.Add did not increase Count by exactly one",
            shape=canonical,
            count_before=count_before,
            count_after=count_after,
        )

    created_index = count_after
    surface = surfaces(created_index)
    if name is not None:
        surface.Name = name
    rows, columns = _control_point_limits(surface)
    return com.to_json({
        "shape": {"enum_name": canonical, "value": library[canonical]},
        "created_index": created_index,
        "name": surface.Name,
        "rows": rows,
        "columns": columns,
        "count_before": count_before,
        "count_after": count_after,
    })


@guarded(classification=Classification.WRITE_GEOMETRY, module="modeler",
         channel=Channel.MCP_COM)
def delete_surface(index: int, expected_name: str) -> str:
    """WRITE. Delete one surface. The exact current name is required.

    expected_name is mandatory: a stale index alone must never be able to
    delete a surface, and indices shift whenever the collection changes.
    """
    if not isinstance(expected_name, str) or not expected_name.strip():
        raise MaxsurfValidationError(
            "expected_name is required and must be a non-empty string",
            index=index,
        )
    app = com.connect("modeler")
    surfaces = app.Design.Surfaces
    surface, count_before = _surface_at(surfaces, index)
    actual_name = surface.Name
    if actual_name != expected_name:
        raise MaxsurfSafetyError(
            "surface name guard failed; nothing was deleted",
            index=index,
            expected_name=expected_name,
            actual_name=actual_name,
            count_before=count_before,
        )
    surface.Delete()
    count_after = int(surfaces.Count)
    if count_after != count_before - 1:
        raise MaxsurfCOMError(
            "ISurface.Delete did not decrease Count by exactly one",
            index=index,
            count_before=count_before,
            count_after=count_after,
        )
    return com.to_json({
        "deleted_index": index,
        "deleted_name": actual_name,
        "count_before": count_before,
        "count_after": count_after,
    })


@guarded(classification=Classification.WRITE_GEOMETRY, module="modeler",
         channel=Channel.MCP_COM)
def set_control_points(surface_index: int, points_json: str) -> str:
    """WRITE. Bulk-update points through proven Get/SetControlPoint methods.

    points_json: [{"r":1,"c":2,"x":10.0,"y":2.5,"z":1.0}, ...]
    Axes that are omitted keep their current value. Every point is validated
    before any write. If a write fails part-way the applied count is reported
    exactly, because a silently half-applied edit is worse than a failure.
    """
    app = com.connect("modeler")
    surface, _count = _surface_at(app.Design.Surfaces, surface_index)
    rows, columns = _control_point_limits(surface)
    try:
        points = json.loads(points_json)
    except ValueError as exc:
        raise MaxsurfValidationError(
            "points_json must be valid JSON", head=points_json[:200]
        ) from exc
    if not isinstance(points, list):
        raise MaxsurfValidationError(
            "points_json must be a JSON array", received=type(points).__name__
        )

    prepared = []
    for position, point in enumerate(points):
        if not isinstance(point, dict):
            raise MaxsurfValidationError(
                "each point must be a JSON object", position=position
            )
        if "r" not in point or "c" not in point:
            raise MaxsurfValidationError(
                "each point requires 'r' and 'c'", position=position
            )
        row = int(point["r"])
        column = int(point["c"])
        _validate_control_point(row, column, rows, columns)
        before = _control_point(surface, row, column)
        prepared.append((
            row,
            column,
            float(point.get("x", before["longitudinal"])),
            float(point.get("y", before["offset"])),
            float(point.get("z", before["vertical"])),
        ))

    applied = 0
    for row, column, x_value, y_value, z_value in prepared:
        try:
            surface.SetControlPoint(row, column, x_value, y_value, z_value)
        except Exception as exc:  # noqa: BLE001
            raise MaxsurfCOMError(
                "SetControlPoint failed part-way through a bulk update; the "
                "geometry is partially modified",
                cause=exc,
                surface_index=surface_index,
                failed_at={"row": row, "column": column},
                applied_before_failure=applied,
                requested=len(prepared),
            ) from exc
        applied += 1
    return com.to_json({"requested": len(prepared), "updated": applied})


@guarded(classification=Classification.WRITE_STATE, module="modeler",
         channel=Channel.MCP_COM)
def refresh_modeler_view(reset_zoom: bool = True) -> str:
    """WRITE_STATE. Make Modeler repaint so live changes become visible.

    Maxsurf leaves ``Application.ScreenUpdating`` off when it is driven through
    COM, so geometry that has been opened or built is present in the model but
    is not repainted in the window -- the view looks frozen or empty even
    though the design is correct. This turns screen updating back on,
    optionally zooms the view to the geometry (``ResetViewZoom``) and forces a
    redraw (``Refresh``). It changes only what is displayed, never the design.
    """
    app = com.connect("modeler")
    before = None
    try:
        before = bool(app.ScreenUpdating)
    except Exception:  # noqa: BLE001 - property may be absent on a build
        pass
    steps: dict[str, str] = {}
    try:
        app.ScreenUpdating = True
        steps["screen_updating"] = "on"
    except Exception as exc:  # noqa: BLE001
        steps["screen_updating"] = f"unavailable: {str(exc)[:80]}"
    if reset_zoom:
        try:
            app.ResetViewZoom()
            steps["reset_zoom"] = "ok"
        except Exception as exc:  # noqa: BLE001
            steps["reset_zoom"] = f"unavailable: {str(exc)[:80]}"
    try:
        app.Refresh()
        steps["refresh"] = "ok"
    except Exception as exc:  # noqa: BLE001
        steps["refresh"] = f"unavailable: {str(exc)[:80]}"
    return com.to_json({
        "module": "modeler",
        "screen_updating_before": before,
        "steps": steps,
        "note": ("Maxsurf disables ScreenUpdating under COM automation; call "
                 "this after opening or building geometry to see it in the "
                 "window."),
    })


TOOLS = (
    surface_summary,
    open_design,
    save_design,
    list_surfaces,
    get_control_net,
    set_control_point,
    create_library_surface,
    delete_surface,
    set_control_points,
    refresh_modeler_view,
)


def register_tools(mcp: Any) -> list[str]:
    """Register proven Modeler tools."""
    return register(mcp, TOOLS)
