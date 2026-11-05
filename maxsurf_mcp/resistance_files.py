"""Guarded native measurements and free-surface persistence.

The installed IDesign metadata takes one filename, without overwrite flags.
No measurement suffix is prescribed by the inspected metadata/manual. Free
surface text data uses current application length units, not assumed SI.
"""
from __future__ import annotations

import os
import hashlib
from typing import Any

from . import com, workspace
from .audit import Channel, Classification
from .errors import MaxsurfCOMError, MaxsurfSafetyError, MaxsurfValidationError
from .guard import guarded, register
from .resistance import _require_ready


def _evidence(path: Any) -> dict | None:
    if not path.is_file():
        return None
    stat = path.stat()
    return {"path": str(path), "bytes": stat.st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns}


def _file(path: str, member: str, *, writing: bool, consent: bool) -> str:
    if type(consent) is not bool:
        raise MaxsurfValidationError("overwrite/confirm_replace must be Boolean")
    if not writing and not consent:
        raise MaxsurfSafetyError("import requires confirm_replace=True; save current data first")
    target = (workspace.resolve_target(path, purpose=member, overwrite=consent)
              if writing else workspace.resolve_existing(path, purpose=member))
    before = _evidence(target)
    if not writing and (not before or before["bytes"] <= 0):
        raise MaxsurfValidationError("import source must be nonempty")
    design = _require_ready(com.connect("resistance"))
    try:
        getattr(design, member)(str(target))
        after = _evidence(target)
        if writing and (not after or after["bytes"] <= 0 or after == before):
            raise MaxsurfCOMError("native save produced no attributable nonempty file",
                                  native_member=member, before=before, after=after)
        measurement_path = None
        if member in ("MeasurementsOpen", "MeasurementsSaveAs"):
            measurement_path = str(design.MeasurementsPath)
            if os.path.normcase(os.path.realpath(measurement_path)) != os.path.normcase(
                os.path.realpath(str(target))
            ):
                raise MaxsurfCOMError("measurement path readback mismatch",
                                      requested=str(target), actual=measurement_path)
    except Exception as exc:
        raise MaxsurfCOMError("native Resistance persistence failed",
                              native_member=member, cause=exc,
                              partial_state_possible=True) from exc
    finally:
        managed = com.session_for("resistance")
        if managed is not None:
            managed.refresh_design_identity()
    return com.to_json({"native_member": member, "file": after,
                        "measurements_path": measurement_path,
                        "native_call_completed": True,
                        "output_verified": writing,
                        "round_trip_verified": False,
                        "import_completeness_verified": False,
                        "free_surface_length_unit": "current application length units"
                        if member.startswith("FreeSurface") else None})


@guarded(classification=Classification.WRITE_STATE, module="resistance",
         channel=Channel.MCP_COM)
def open_resistance_measurements(path: str, confirm_replace: bool = False) -> str:
    """Load measurements using MeasurementsOpen; explicitly replace current data."""
    return _file(path, "MeasurementsOpen", writing=False, consent=confirm_replace)


@guarded(classification=Classification.WRITE_FILE, module="resistance",
         channel=Channel.MCP_COM)
def save_resistance_measurements(path: str, overwrite: bool = False) -> str:
    """Save measurements using MeasurementsSaveAs, not hull Design.SaveAs."""
    return _file(path, "MeasurementsSaveAs", writing=True, consent=overwrite)


@guarded(classification=Classification.WRITE_STATE, module="resistance",
         channel=Channel.MCP_COM)
def import_resistance_free_surface(path: str, confirm_replace: bool = False) -> str:
    """Import existing wave data; completion does not prove numerical fidelity."""
    return _file(path, "FreeSurfaceImport", writing=False, consent=confirm_replace)


@guarded(classification=Classification.WRITE_FILE, module="resistance",
         channel=Channel.MCP_COM)
def export_resistance_free_surface(path: str, overwrite: bool = False) -> str:
    """Export existing wave data without launching a calculation."""
    return _file(path, "FreeSurfaceExport", writing=True, consent=overwrite)


TOOLS = (open_resistance_measurements, save_resistance_measurements,
         import_resistance_free_surface, export_resistance_free_surface)


def register_tools(mcp: Any) -> list[str]:
    return register(mcp, TOOLS)
