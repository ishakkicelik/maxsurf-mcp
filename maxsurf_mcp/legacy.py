"""Quarantined tools whose COM members are not proven to exist.

The Phase 0 audit established, from this project's own live ``ITypeInfo``
records in ``private discovery notes (not distributed)``, that these three implementations target members
which are absent from the interfaces they address:

``parametric_transform``
    Searches for ``ParametricTransformation`` / ``Transformation`` /
    ``Parametric`` on the Application object. Modeler ``IApplication`` exposes
    only ``Exit``, ``TraceToConsole``, ``TraceToFile``, ``Refresh`` and
    ``ResetViewZoom``. The real API is ``Design.Hydrostatics.Transform(...)``
    plus the ``InitAdvancedTransform`` / ``SetTarget*`` / ``AdvancedTransform``
    sequence. The docstring's Lackenby claim is also unsupported.

``export_model``
    Tries ``Export`` / ``ExportFile`` / ``SaveAs`` on the Application object.
    The proven API is per-format and lives on ``Design``: ``ExportIGES``,
    ``Export3DM``, ``ExportDGN``, ``ExportOrthogonalViewDXF``,
    ``ExportTrimesh*``.

``run_analysis``
    Writes arbitrary caller-supplied properties onto whichever of
    ``Application`` / ``Analysis`` / ``Design`` accepts them, then looks for a
    trigger on the Application. The proven trigger is ``Design.RunAnalysis()``
    with no arguments.

They are kept verbatim rather than repaired, because repairing them would mean
guessing. They are unregistered by default and refuse to run unless
``MAXSURF_MCP_ENABLE_LEGACY=1``. Correct replacements belong in the owning
application adapters and are out of scope for Phase 0.
"""

from __future__ import annotations

import json
from typing import Any

from . import audit, com, workspace
from .audit import Channel, Classification
from .errors import MaxsurfAnalysisError, MaxsurfCOMError
from .guard import TIER_LEGACY, guarded, register

UNPROVEN_WARNING = (
    "this tool targets COM members that live discovery has not proven to "
    "exist; results and side effects are unverified"
)


@guarded(classification=Classification.WRITE_GEOMETRY, module="legacy",
         channel=Channel.MCP_COM, tier=TIER_LEGACY)
def parametric_transform(target_lwl: float = 0, target_beam: float = 0,
                         target_draft: float = 0,
                         target_displacement: float = 0,
                         target_lcb: float = 0, target_cp: float = 0) -> str:
    """QUARANTINED / UNPROVEN. Attempt a parametric transformation.

    Targets an Application-level transformation object that live ITypeInfo
    discovery did not find. Disabled unless MAXSURF_MCP_ENABLE_LEGACY=1. Use
    the proven Design.Hydrostatics transform API once it is implemented.
    Targets left at 0 are unconstrained.
    """

    app = com.connect("modeler")
    wanted = {"LWL": target_lwl, "Beam": target_beam, "Draft": target_draft,
              "Displacement": target_displacement, "LCB": target_lcb,
              "Cp": target_cp}
    applied, notes = {}, []
    transformation = com.first_available(
        app, "ParametricTransformation", "Transformation", "Parametric"
    )
    if transformation is None:
        raise MaxsurfCOMError(
            "no parametric transformation object is exposed by this build",
            hint="the proven API is Design.Hydrostatics.Transform; inspect "
                 "members with com_describe",
        )
    for key, value in wanted.items():
        if not value:
            continue
        for name in (f"Target{key}", key, f"Desired{key}"):
            try:
                setattr(transformation, name, float(value))
                applied[key] = value
                break
            except Exception:  # noqa: BLE001
                continue
        else:
            notes.append(f"could not write target {key}")
    for run in ("Apply", "Transform", "Execute", "Run"):
        try:
            getattr(transformation, run)()
            break
        except Exception:  # noqa: BLE001
            continue
    else:
        raise MaxsurfCOMError(
            "no transformation trigger method could be invoked",
            applied_targets=applied,
            notes=notes,
        )
    after = {"status": "unavailable_in_release"}
    return com.to_json({
        "trust": "unproven",
        "warnings": [UNPROVEN_WARNING],
        "applied": applied,
        "notes": notes,
        "result": after,
    })


@guarded(classification=Classification.WRITE_FILE, module="legacy",
         channel=Channel.MCP_COM, tier=TIER_LEGACY)
def export_model(path: str, fmt: str = "iges", overwrite: bool = False) -> str:
    """QUARANTINED / UNPROVEN. Attempt to export the model to a file.

    Targets Application-level Export/ExportFile/SaveAs members that live
    discovery did not find; the proven exporters are per-format methods on
    Design. Disabled unless MAXSURF_MCP_ENABLE_LEGACY=1. The path is still
    sandboxed and an existing file still requires overwrite=True.
    """
    target = workspace.resolve_target(
        path, purpose="export_model", overwrite=overwrite
    )
    audit.set_design_path(str(target))
    app = com.connect("modeler")
    attempts = []
    for name in ("Export", "ExportFile"):
        try:
            getattr(app, name)(str(target), fmt.upper())
            return com.to_json({
                "trust": "unproven",
                "warnings": [UNPROVEN_WARNING],
                "exported": str(target),
                "format": fmt,
                "via": name,
            })
        except Exception as exc:  # noqa: BLE001
            attempts.append(f"{name}: {exc}")
    raise MaxsurfCOMError(
        "no Application-level export member accepted this call",
        attempted=attempts,
        hint="the proven exporters are Design.ExportIGES / Export3DM / "
             "ExportDGN / ExportTrimesh*",
    )


@guarded(classification=Classification.EXECUTE_ANALYSIS, module="legacy",
         channel=Channel.MCP_COM, tier=TIER_LEGACY)
def run_analysis(module: str, analysis: str, params_json: str = "{}") -> str:
    """QUARANTINED / UNPROVEN. Attempt to run an analysis by name.

    Writes caller-supplied properties onto the first object that accepts them
    and then searches the Application for a trigger. The proven trigger is
    Design.RunAnalysis() with no arguments. Disabled unless
    MAXSURF_MCP_ENABLE_LEGACY=1.
    """
    app = com.connect(module)
    params = json.loads(params_json)
    written, notes = {}, []
    for key, value in params.items():
        for target in (
            app,
            com.first_available(app, "Analysis"),
            com.first_available(app, "Design"),
        ):
            if target is None:
                continue
            try:
                setattr(target, key, value)
                written[key] = value
                break
            except Exception:  # noqa: BLE001
                continue
        else:
            notes.append(f"could not write parameter {key}")
    result = None
    for name in (f"Run{analysis}", f"Analyse{analysis}", "RunAnalysis",
                 "Analyse", "Calculate"):
        try:
            function = getattr(app, name)
            result = (
                function(analysis)
                if name in ("RunAnalysis", "Analyse")
                else function()
            )
            notes.append(f"invoked: {name}")
            break
        except Exception:  # noqa: BLE001
            continue
    else:
        raise MaxsurfAnalysisError(
            "no analysis trigger could be invoked on the Application object",
            module=module,
            analysis=analysis,
            params_written=written,
            notes=notes,
            hint="the proven trigger is Design.RunAnalysis() with no arguments",
        )
    return com.to_json({
        "trust": "unproven",
        "warnings": [UNPROVEN_WARNING],
        "module": module,
        "analysis": analysis,
        "params_written": written,
        "notes": notes,
        "result": result,
    })


TOOLS = (parametric_transform, export_model, run_analysis)


def register_tools(mcp: Any) -> list[str]:
    """Register quarantined tools only when the legacy tier is enabled."""
    return register(mcp, TOOLS)
