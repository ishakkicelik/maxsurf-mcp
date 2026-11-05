"""Native selected-condition table and RAO text workflows.

Selectors are methods, not properties. Bounds use tested condition counts;
the toolbar mapping and physical result freshness still need live acceptance.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from . import com, constants, workspace
from .audit import Channel, Classification
from .errors import MaxsurfCOMError, MaxsurfSafetyError, MaxsurfValidationError
from .guard import guarded, register
from .motions import _digest, _require_numeric_table, _require_ready
from .motions_controls import _integer


def _conditions(design: Any, speed: int, heading: int,
                spectrum: int | None = None, location: int | None = None,
                frequency: int | None = None, section: int | None = None) -> dict[str, int]:
    selected = {}
    for member, collection, value in (
        ("ResultsSpeed", "Speeds", speed), ("ResultsHeading", "Headings", heading),
        ("ResultsSpectrum", "Spectra", spectrum),
    ):
        if value is None:
            continue
        value = _integer(value, member)
        count = int(getattr(design, collection).NumberTested)
        if value > count:
            raise MaxsurfValidationError("result index exceeds tested conditions",
                                         selector=member, index=value, count=count)
        selected[member] = value
    for member, value in (("ResultsLocation", location),
                          ("ResultsFrequency", frequency), ("ResultsSection", section)):
        if value is None:
            continue
        value = _integer(value, member)
        count = int(design.Locations.Count if member == "ResultsLocation" else
                    design.AnalysisOptions.NumOfFrequencies if member == "ResultsFrequency" else
                    design.SectionMappings.Count)
        if value > count:
            raise MaxsurfValidationError("result index exceeds native axis count",
                                         selector=member, index=value, count=count)
        selected[member] = value
    return selected


def _select(design: Any, selected: dict[str, int]) -> None:
    for member, index in selected.items():
        if getattr(design, member)(index) is False:
            raise MaxsurfCOMError("native result selector was refused", selector=member, index=index)


def _export(design: Any, target: Any, member: str,
            selected: dict[str, int], table: int | None = None,
            summary: bool = False) -> dict[str, Any]:
    staged = target.with_name(target.stem + "." + uuid4().hex + target.suffix)
    try:
        _select(design, selected)
        result = (getattr(design, member)(table, str(staged)) if table is not None
                  else getattr(design, member)(str(staged)))
        if result is False or not staged.is_file() or staged.stat().st_size == 0:
            raise MaxsurfCOMError("native export did not create nonempty output")
        _require_numeric_table(staged.read_bytes(), summary=summary)
        after = _digest(staged)
        staged.replace(target)
    except Exception as exc:
        raise MaxsurfCOMError("Motions export failed", native_member=member,
                              cause=exc, partial_state_possible=True) from exc
    finally:
        if staged.exists():
            staged.unlink()
    return {"path": str(target), **after, "native_member": member,
            "requested_selectors": selected, "file_verified": True,
            "selector_mapping_verified": False, "result_freshness_verified": False,
            "engineering_verdict": "not_evaluated"}


@guarded(classification=Classification.WRITE_FILE, module="motions", channel=Channel.MCP_COM)
def export_motions_condition_table(path: str, table: str, speed_index: int,
                                   heading_index: int, spectrum_index: int,
                                   overwrite: bool = False,
                                   location_index: int | None = None,
                                   frequency_index: int | None = None,
                                   section_index: int | None = None) -> str:
    """Select bounded tested condition indices via methods, then export a table.

    Indices are one-based tested-axis requests. Native toolbar mapping has not
    been independently accepted; output does not assert condition verification.
    """
    if type(overwrite) is not bool:
        raise MaxsurfValidationError("overwrite must be a boolean")
    for field, value in (("speed", speed_index), ("heading", heading_index), ("spectrum", spectrum_index)):
        _integer(value, field)
    for field, value in (("location", location_index), ("frequency", frequency_index), ("section", section_index)):
        if value is not None:
            _integer(value, field)
    target = workspace.resolve_target(path, purpose="export_motions_condition_table",
                                      overwrite=overwrite, allowed_suffixes=(".txt",))
    app = com.connect("motions")
    catalog = constants.discover(app, name_filter="skTab")["constants"]
    if not isinstance(table, str) or not table.startswith("skTab") or type(catalog.get(table)) is not int:
        raise MaxsurfValidationError("unknown native results table")
    design = _require_ready(app)
    selected = _conditions(design, speed_index, heading_index, spectrum_index,
                           location_index, frequency_index, section_index)
    return com.to_json(_export(design, target, "ExportResults", selected,
                               table=catalog[table], summary=table == "skTabSummary"))


@guarded(classification=Classification.WRITE_FILE, module="motions", channel=Channel.MCP_COM)
def export_motions_rao_text(path: str, speed_index: int = 1,
                           heading_index: int = 1, multi_condition: bool = False,
                           overwrite: bool = False) -> str:
    """Export native RAO text for requested selectors or all native conditions."""
    if type(multi_condition) is not bool or type(overwrite) is not bool:
        raise MaxsurfValidationError("multi_condition and overwrite must be booleans")
    _integer(speed_index, "speed_index")
    _integer(heading_index, "heading_index")
    if multi_condition and (speed_index != 1 or heading_index != 1):
        raise MaxsurfValidationError("multi-condition export does not accept selected indices")
    target = workspace.resolve_target(path, purpose="export_motions_rao_text",
                                      overwrite=overwrite, allowed_suffixes=(".txt",))
    design = _require_ready(com.connect("motions"))
    selected = {} if multi_condition else _conditions(design, speed_index, heading_index)
    member = "ExportRAOTextMultiCondition" if multi_condition else "ExportRAOText"
    return com.to_json(_export(design, target, member, selected))


@guarded(classification=Classification.WRITE_STATE, module="motions", channel=Channel.MCP_COM)
def import_motions_rao_text(path: str, multi_condition: bool = False,
                           confirm_replace: bool = False) -> str:
    """Import native RAO text with explicit consent; no solver is launched.

    Native call completion does not prove import completeness, units, geometry
    compatibility or freshness. Imported-RAO analysis execution is unsupported.
    """
    if type(multi_condition) is not bool or type(confirm_replace) is not bool:
        raise MaxsurfValidationError("multi_condition and confirm_replace must be booleans")
    if not confirm_replace:
        raise MaxsurfSafetyError("RAO import requires confirm_replace=True; save native data first")
    target = workspace.resolve_existing(path, purpose="import_motions_rao_text",
                                        allowed_suffixes=(".txt",))
    if target.stat().st_size == 0:
        raise MaxsurfValidationError("RAO import source must be nonempty")
    evidence = _digest(target)
    design = _require_ready(com.connect("motions"))
    member = "ImportRAOTextMultiCondition" if multi_condition else "ImportRAOText"
    try:
        if getattr(design, member)(str(target)) is False:
            raise MaxsurfCOMError("native RAO import was refused")
    except Exception as exc:
        raise MaxsurfCOMError("Motions RAO import failed", native_member=member,
                              cause=exc, partial_state_possible=True) from exc
    finally:
        managed = com.session_for("motions")
        if managed is not None:
            managed.refresh_design_identity()
    return com.to_json({"path": str(target), **evidence, "native_member": member,
                        "native_call_completed": True, "import_completeness_verified": False,
                        "result_freshness_verified": False, "analysis_run": False})


TOOLS = (export_motions_condition_table, export_motions_rao_text, import_motions_rao_text)


def register_tools(mcp: Any) -> list[str]:
    return register(mcp, TOOLS)
