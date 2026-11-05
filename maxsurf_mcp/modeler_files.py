"""Closed Modeler file adapters; native format fidelity needs live acceptance."""
from __future__ import annotations

import hashlib
from typing import Any

import pythoncom

from . import audit, com, workspace
from .audit import Channel, Classification
from .errors import MaxsurfCOMError, MaxsurfSafetyError, MaxsurfValidationError
from .guard import guarded, register

# Exact IDesign names from the installed BentleyModeler 1.1 typelib.
# nuShallo/PDstrip suffixes are not prescribed by the automation metadata.
EXPORTS = {
    "3dm": ("Export3DM", (".3dm",)),
    "iges": ("ExportIGES", (".igs", ".iges")),
    "dgn": ("ExportDGN", (".dgn",)),
    "dxf": ("ExportOrthogonalViewDXF", (".dxf",)),
    "pdstrip": ("ExportPDstrip", None),
    "moses": ("SaveMOSESmodelAs", (".dat",)),
    "nushallo": ("ExportTrimeshNuShallo", None),
    "wamit": ("ExportTrimeshWAMIT", (".gdf",)),
    "off": ("ExportTrimeshOFF", (".off",)),
    "ply": ("ExportTrimeshPLY", (".ply",)),
    "obj": ("ExportTrimeshOBJ", (".obj",)),
    "stl": ("ExportTrimeshSTL", (".stl",)),
}
IMPORTS = {
    "3dm": ("Import3DM", (".3dm",)),
    "nushallo": ("ImportNuShalloTrimesh", None),
    "wamit": ("ImportWAMITTrimesh", (".gdf",)),
    "off": ("ImportOFFTrimesh", (".off",)),
    "ply": ("ImportPLYTrimesh", (".ply",)),
    "obj": ("ImportOBJTrimesh", (".obj",)),
    "stl": ("ImportSTLTrimesh", (".stl",)),
}
VIEWS = {"body_plan": 2, "profile": 3, "plan": 4}
PRECISIONS = ("msSPLowest", "msSPLow", "msSPMedium", "msSPHigh", "msSPHighest")


def _boolean(name: str, value: Any) -> None:
    if type(value) is not bool:
        raise MaxsurfValidationError(f"{name} must be a Boolean")


def _format(value: str, mapping: dict) -> tuple:
    if not isinstance(value, str) or value not in mapping:
        raise MaxsurfValidationError("unsupported format", available=sorted(mapping))
    return mapping[value]


def _state(design: Any) -> dict:
    return {"path": str(design.Path), "name": str(design.Name),
            "surface_count": int(design.Surfaces.Count),
            "trimesh_count": int(design.Trimeshs.Count)}


def _evidence(path: Any) -> dict | None:
    if not path.is_file():
        return None
    stat = path.stat()
    return {"path": str(path), "bytes": stat.st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns}


def _precision(design: Any, name: str) -> int:
    """Resolve the enum from ExportPDstrip's owning argument type, not globals."""
    ti = design._oleobj_.GetTypeInfo()
    for index in range(ti.GetTypeAttr().cFuncs):
        fd = ti.GetFuncDesc(index)
        if ti.GetNames(fd.memid)[0] != "ExportPDstrip":
            continue
        descriptor = fd.args[1][0]
        if not isinstance(descriptor, tuple) or descriptor[0] != pythoncom.VT_USERDEFINED:
            break
        enum = ti.GetRefTypeInfo(descriptor[1])
        if enum.GetDocumentation(-1)[0] != "msSurfacePrecision":
            break
        for member_index in range(enum.GetTypeAttr().cVars):
            member = enum.GetVarDesc(member_index)
            if enum.GetNames(member[0])[0] == name:
                value = member[1]
                if type(value) is int:
                    return value
        break
    raise MaxsurfCOMError("native PDstrip precision enum could not be resolved",
                          requested=name)


@guarded(classification=Classification.WRITE_FILE, module="modeler",
         channel=Channel.MCP_COM)
def export_modeler_file(path: str, format: str, overwrite: bool = False,
                        view: str = "", precision: str = "") -> str:
    """WRITE_FILE. Export 3dm, iges, dgn, dxf, pdstrip, moses or native meshes.

    DXF requires view=body_plan/profile/plan; PDstrip requires an exact
    msSurfacePrecision name. Mesh formats export existing meshes without
    implicit NURBS tessellation. Nonempty changed file evidence is not fidelity.
    """
    member, suffixes = _format(format, EXPORTS)
    _boolean("overwrite", overwrite)
    if not isinstance(view, str) or not isinstance(precision, str):
        raise MaxsurfValidationError("view and precision must be strings")
    if (format == "dxf" and view not in VIEWS) or (format != "dxf" and view):
        raise MaxsurfValidationError("view is required only for DXF", available=list(VIEWS))
    if ((format == "pdstrip" and precision not in PRECISIONS)
            or (format != "pdstrip" and precision)):
        raise MaxsurfValidationError("precision is required only for PDstrip",
                                      available=list(PRECISIONS))
    target = workspace.resolve_target(path, purpose="export_modeler_file",
                                      overwrite=overwrite, allowed_suffixes=suffixes)
    app = com.connect("modeler")
    design = app.Design
    before = _state(design)
    old_file = _evidence(target)
    args: list[Any] = [str(target)]
    if format == "dxf":
        args.append(VIEWS[view])
    elif format == "pdstrip":
        args.append(_precision(design, precision))
    elif format == "moses":
        args.append(overwrite)
    try:
        getattr(design, member)(*args)
        after = _state(design)
        output = _evidence(target)
        if not output or output["bytes"] <= 0 or output == old_file:
            raise MaxsurfCOMError("export did not produce attributable nonempty output",
                                  before=old_file, after=output, native_member=member)
    finally:
        managed = com.session_for("modeler")
        if managed is not None:
            managed.refresh_design_identity()
    return com.to_json({"native_member": member, "native_arguments": args,
                        "format": format, "file": output,
                        "replaced_existing_file": old_file is not None,
                        "before": before, "after": after,
                        "output_verified": True, "fidelity_verified": False})


@guarded(classification=Classification.WRITE_STATE, module="modeler",
         channel=Channel.MCP_COM)
def import_modeler_file(path: str, format: str, confirm_replace: bool = False,
                        acknowledge_unverified_semantics: bool = False) -> str:
    """WRITE_STATE. Import Rhino 3DM or a native mesh file.

    Native import methods have no merge/save controls. Both explicit replacement
    consent and acknowledgment of unverified merge/replacement semantics are
    required. Save current work first. Counts/state are observations, not proof
    of import completeness, geometric fidelity, or replacement versus merge.
    """
    member, suffixes = _format(format, IMPORTS)
    _boolean("confirm_replace", confirm_replace)
    _boolean("acknowledge_unverified_semantics", acknowledge_unverified_semantics)
    if not confirm_replace or not acknowledge_unverified_semantics:
        raise MaxsurfSafetyError("import requires replacement consent and acknowledgment "
                                 "of unverified merge/replacement semantics")
    source = workspace.resolve_existing(path, purpose="import_modeler_file",
                                        allowed_suffixes=suffixes)
    evidence = _evidence(source)
    if not evidence or evidence["bytes"] <= 0:
        raise MaxsurfValidationError("import source must be nonempty")
    design = com.connect("modeler").Design
    before = _state(design)
    try:
        getattr(design, member)(str(source))
        after = _state(design)
    except Exception as exc:
        raise MaxsurfCOMError("native import failed; design may be partially changed",
                              cause=exc, before=before, native_member=member) from exc
    finally:
        managed = com.session_for("modeler")
        if managed is not None:
            managed.refresh_design_identity()
    audit.set_design_path(after["path"])
    return com.to_json({"native_member": member, "format": format, "source": evidence,
                        "before": before, "after": after, "native_call_completed": True,
                        "semantics_verified": False, "fidelity_verified": False,
                        "import_completeness_verified": False})


TOOLS = (export_modeler_file, import_modeler_file)


def register_tools(mcp: Any) -> list[str]:
    return register(mcp, TOOLS)
