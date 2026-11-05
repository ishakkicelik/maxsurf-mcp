"""Native Stability table export and bounded companion input workflows."""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from . import com, workspace
from . import validation as val
from .audit import Channel, Classification
from .errors import MaxsurfCOMError, MaxsurfSafetyError, MaxsurfValidationError
from .guard import guarded, register
from .stability import _constant, _digest, _loadcase_names
from .stability_controls import _boolean

RESULT_TABLES = (
    "hmResultHydrostatics", "hmResultStability", "hmResultEquilibrium",
    "hmResultSpecifiedCondition", "hmResultKN", "hmResultLimitingKG",
    "hmResultFloodableLength", "hmResultLongitudinalStrength",
    "hmResultProbabilisticDamage", "hmResultTankCalibration", "hmResultMARPOL",
    "hmResultCrossFlood", "hmResultCriteria", "hmResultKeyPoints",
    "hmResultCompartments", "hmResultMaxHeelArm",
)
COMPANIONS = {
    "rooms": ("OpenRooms", ".htk"),
    "cargo": ("OpenCargo", ".cgo"),
    "damage": ("OpenDamageCases", ".dcs"),
}


@guarded(classification=Classification.WRITE_FILE, module="stability", channel=Channel.MCP_COM)
def export_stability_results_table(table: str, path: str, overwrite: bool = False) -> str:
    """Save the caller-selected native results table without running analysis.

    File evidence does not establish solver convergence, freshness or approval.
    .txt preserves native output; no CSV reformatting or unit conversion occurs.
    """
    table = val.name(table, "table")
    if table not in RESULT_TABLES:
        raise MaxsurfValidationError("unknown native result table")
    _boolean(overwrite, "overwrite")
    target = workspace.resolve_target(path, purpose="stability_results_table",
                                      overwrite=overwrite, allowed_suffixes=(".txt",))
    app = com.connect("stability")
    value = _constant(app, table)
    # A unique absent destination prevents an old file from proving a no-op
    # export, and avoids native overwrite dialogs (the method has no flag).
    staged = target.with_name(target.stem + "." + uuid4().hex + ".txt")
    try:
        app.Design.SaveResultsTable(value, str(staged))
        if not staged.is_file() or staged.stat().st_size == 0:
            raise MaxsurfCOMError("native table export did not write a nonempty file", path=str(target))
        staged.replace(target)
    finally:
        if staged.exists():
            staged.unlink()
    return com.to_json({"path": str(target), **_digest(target), "table": table,
                        "native_member": "Design.SaveResultsTable", "file_verified": True,
                        "analysis_run": False, "result_freshness": "not_verified"})


@guarded(classification=Classification.WRITE_STATE, module="stability", channel=Channel.MCP_COM)
def open_stability_companion(kind: str, path: str, confirm_replace: bool = False) -> str:
    """Load rooms (.htk), cargo (.cgo) or damage (.dcs) only into empty state.

    Explicit confirmation is mandatory. Existing affected/dependent state is
    refused because the room-only backup cannot restore a complete project.
    Import counts/identity are reported, not equivalence to the source file.
    """
    kind = val.name(kind, "kind")
    if kind not in COMPANIONS:
        raise MaxsurfValidationError("unknown companion kind")
    if _boolean(confirm_replace, "confirm_replace") is not True:
        raise MaxsurfSafetyError("companion import requires confirm_replace=true")
    member, suffix = COMPANIONS[kind]
    source = workspace.resolve_existing(path, purpose="stability_companion",
                                        allowed_suffixes=(suffix,))
    if source.stat().st_size == 0:
        raise MaxsurfValidationError("companion input is empty")
    app = com.connect("stability")
    design = app.Design
    cases = _loadcase_names(design)
    counts = {name: int(getattr(design, name).Count) for name in
              ("Tanks", "Compartments", "DamageCases", "KeyPoints")}
    if kind == "rooms" and (counts["Tanks"] or counts["Compartments"] or
                            counts["KeyPoints"] or counts["DamageCases"] > 1 or cases):
        raise MaxsurfSafetyError("room replacement disabled: dependent state cannot be backed up")
    if kind == "cargo" and cases:
        raise MaxsurfSafetyError("cargo replacement disabled: existing loadcases cannot be backed up")
    if kind == "damage" and counts["DamageCases"] > 1:
        raise MaxsurfSafetyError("damage replacement disabled: existing cases cannot be backed up")
    evidence = _digest(source)
    try:
        getattr(design, member)(str(source))
        after = {name: int(getattr(design, name).Count) for name in counts}
        loaded_cases = sorted(_loadcase_names(design))
        populated = (after["Tanks"] + after["Compartments"] > 0 if kind == "rooms"
                     else bool(loaded_cases) if kind == "cargo"
                     else after["DamageCases"] > 1)
        if not populated:
            raise MaxsurfCOMError("companion import produced no defined input data")
    except Exception as exc:
        raise MaxsurfCOMError("companion import failed", cause=exc,
                              partial_state_possible=True) from exc
    finally:
        com.session_for("stability").refresh_design_identity()
    return com.to_json({"path": str(source), **evidence, "kind": kind,
                        "native_member": "Design." + member, "counts_before": counts,
                        "counts_after": after, "loadcases": loaded_cases,
                        "import_observed": True, "source_equivalence_verified": False})


TOOLS = (export_stability_results_table, open_stability_companion)


def register_tools(mcp: Any) -> list[str]:
    return register(mcp, TOOLS)
