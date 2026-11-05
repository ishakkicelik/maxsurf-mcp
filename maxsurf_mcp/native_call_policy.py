"""Closed file-effect policy for the diagnostic native extension.

Keys are exact (module, interface, member), never filename/name heuristics.
Unmapped text-bearing calls and UI execution have no generic file policy.
"""
from __future__ import annotations

from . import workspace
from .errors import MaxsurfSafetyError


# (path argument, read/write, suffixes, native overwrite argument).
# This is a sandbox contract, not format/round-trip or engineering acceptance.
FILE_POLICIES = {
    ("modeler", "IDesign", "ExportIGES"): ("sFileName", "write", (".igs", ".iges"), None),
    ("modeler", "IDesign", "ExportDGN"): ("sFileName", "write", (".dgn",), None),
    ("modeler", "IDesign", "Export3DM"): ("sFileName", "write", (".3dm",), None),
    ("modeler", "IDesign", "ExportOrthogonalViewDXF"):
        ("sFileName", "write", (".dxf",), None),
    ("modeler", "IDesign", "ExportTrimeshOFF"): ("sFileName", "write", (".off",), None),
    ("modeler", "IDesign", "ExportTrimeshPLY"): ("sFileName", "write", (".ply",), None),
    ("modeler", "IDesign", "ExportTrimeshOBJ"): ("sFileName", "write", (".obj",), None),
    ("modeler", "IDesign", "ExportTrimeshSTL"): ("sFileName", "write", (".stl",), None),
    ("modeler", "IDesign", "ImportOFFTrimesh"): ("sFileName", "read", (".off",), None),
    ("modeler", "IDesign", "ImportPLYTrimesh"): ("sFileName", "read", (".ply",), None),
    ("modeler", "IDesign", "ImportOBJTrimesh"): ("sFileName", "read", (".obj",), None),
    ("modeler", "IDesign", "ImportSTLTrimesh"): ("sFileName", "read", (".stl",), None),
    ("modeler", "IDesign", "SaveAs"):
        ("sFileName", "write", (".msd",), "bOverwrite"),
    ("motions", "IDesign", "SKDataOpen"): ("sFileName", "read", (".skd",), None),
    ("motions", "IDesign", "SKResultsOpen"): ("sFileName", "read", (".skr",), None),
    ("motions", "IDesign", "SKDataSaveAs"): ("fileName", "write", (".skd",), None),
    ("motions", "IDesign", "ExportSTLMesh"): ("fileName", "write", (".stl",), None),
    ("motions", "IDesign", "ExportRAOText"): ("fileName", "write", (".txt",), None),
    ("motions", "IDesign", "ExportRAOTextMultiCondition"):
        ("fileName", "write", (".txt",), None),
    ("stability", "IDesign", "ExportOrthogonalViewDXF"):
        ("sFileName", "write", (".dxf",), None),
    ("stability", "IDesign", "SaveResultsTable"):
        ("sFileName", "write", (".txt",), None),
    ("multiframe", "IFrame", "SaveAs"):
        ("sFileName", "write", (".mfd",), "bReplaceExisting"),
}
for _module in ("modeler", "stability", "resistance", "motions", "multiframe", "vpp",
                "shape_editor"):
    FILE_POLICIES[(_module, "IXMLGrid", "Save")] = (
        "pFileName", "write", (".xml",), None)

# The following exact interfaces contain structural/geometry value arguments,
# not filename arguments. Named file methods on IDesign/IFrame are NOT admitted
# by this table. New interfaces fail closed for text/VARIANT input.
VALUE_INTERFACES = {
    "modeler": frozenset("""
        ISurfaces ISurface ITrimeshs ITrimesh ITrimeshColumn IMeshParams
        IMarkers IMarker IPlates IPlate IFrames IFrame IOpening IDecks IDeck
        IStringers IStringer IStringerPoints ILongGirders ILongGirder IGrids
        ISurfaceList ITrimeshList IMarkerList
    """.split()),
    "stability": frozenset("""
        ICriterion ILoadCases ILoadCaseMassItem ILoadCaseTankItem IDamageCases
        IDamageCase ICompartments ICompartment ITanks ITank IKeyPoints IKeyPoint
        IRoomResults IRoomResult
    """.split()),
    "resistance": frozenset("IShipflowGroups IShipflowGroup".split()),
    "motions": frozenset("""
        ILocations ILocation ISpeeds ISpeed IHeadings IHeading ISpectra ISpectrum
    """.split()),
    "multiframe": frozenset("""
        ISpringElements ISpringElement ILoadPanels ILoadPanel IPlates IPlate
        IPatches IPatch INodes INode IElementList IElement INodeList ISpringList
        ISpring IRestraintList IRestraint INodeMassList INodeMass INodeLoadList
        INodeLoad ILoadCase INodeLoads IElementLoads IElementLoad IElementLoadList
        INodePreDisps INodePreDisp INodePreDispList IThermalLoads IThermalLoad
        IThermalLoadList ISection ISectionGroups ISectionGroup ISections
        ISectionList ISectionGroupList IMaterialGroups IMaterialGroup IMaterials
        IMaterial IMaterialList IMaterialGroupList IElementResult INodeResults
        INodeResultList IElementResults IElementResultList IMemberResults
        IMemberResult IMember IMemberResultList IMemberList ISpringElementResults
        ISpringElementResultList ISpringElementList IPlateResults IPlateResultList
        IPlateList IPatchResults IPatchResultList IPatchList IElements IMembers
        ISprings INodeMasses INodeLinkGroups INodeLinkGroup INodeLinkGroupList
        ILoadCases ILoadCaseList IRestraints IGroupSets IGroupSet IGroups IGroup
        IGroupList IGroupSetList IZones IZone IZoneList ISelection IEnvelopeCases
        IEnvelopeCase IEnvelopeCaseList INonlinearSettings IProjectInfo IResults
        IResultCases ILoadPanelList IPanelLoad IPanelLoads IPanelLoadList IPlateLoad
        IPlateLoads IPlateLoadList IPatchLoad IPatchLoads IPatchLoadList IResultCaseList
    """.split()),
}

VALUE_MEMBERS = {
    ("modeler", "IApplication"): {"Title"},
    ("stability", "IDesign"): {"ActiveLoadCaseByName", "ActiveDamageCaseByName"},
    ("stability", "IDensity"): {"SetName", "SetCode"},
    ("stability", "ICriteria"): {"ItemByName"},
    ("stability", "ILoadCase"): {
        "Name", "MassItemByName", "MassItemByNameExists", "TankItemByName",
        "TankItemByNameExists", "AddMassItem", "DeleteMassItemByName"},
    ("multiframe", "IFrame"): {"ActiveLoadCase"},
    ("multiframe", "IPreferences"): {"SetUnit"},
    ("multiframe", "ISectionsLibrary"): {"GetSection", "GetMaterial"},
}

# Implicit-path writes, state replacement with implicit saves, command dispatch
# and persistent external output cannot be made safe by validating scalar types.
BLOCKED_MEMBERS = frozenset("""
    Save MeasurementsSave SKDataSave SKResultsSave SKResultsSaveAs
    Close New Open DesignOpen DesignClose MeasurementsClose SKDataClose
    SKResultsClose Quit Exit TraceToFile SQLdbDeleteAllResults SQLdbReloadMostRecentResults
    SQLdbSendAllResultsToReport OutputResultsToSQLdb AutoRestoreResultsFromSQLdb
    ProbDamLogFile WriteProbDamLogFile ReportTextFile CONNECT_ProjectGUID
""".split())
UI_INTERFACES = frozenset("""
    IUserInterface IUIMenuItem IAccessible IUICXTPControls IUIToolbar
    IUICXTPComboControl IUICXTPControl
""".split())

PROTECTED_WORKFLOWS = frozenset({
    ("stability", "IKeyPoints", "DeleteAll"),
    ("stability", "ITanks", "DeleteAll"),
    ("stability", "ICompartments", "DeleteAll"),
    # Equivalent installed index/name/bulk and import routes must not bypass
    # preservation simply because they have no text inputs or use another name.
    *{("stability", interface, name) for interface, names in {
        "ILoadCases": ("Remove", "RemoveByName", "RemoveAll"),
        "ICompartment": ("Delete",), "ITank": ("Delete",),
        "IDamageCases": ("Remove", "RemoveAll"),
        "ILoadCase": ("OpenLoadcase", "DeleteAllMassItems", "DeleteMassItem",
                      "DeleteMassItemByName"),
        "IDesign": ("Open", "Close", "OpenCargo", "OpenRooms", "OpenDamageCases",
                    "OpenProbDamage", "OpenKeyPoints", "OpenCrossFlood",
                    "OpenMarginLine", "OpenModulus", "OpenBulkheads",
                    "OpenNuShallo", "OpenGHS", "SQLdbDeleteAllResults"),
        "IPermeability": ("RemoveAll",), "IDensity": ("Import",),
        "ICriteria": ("Import", "DeleteAll"),
    }.items() for name in names},
    *{("motions", "IDesign", name) for name in (
        "SKResultsSave", "SKResultsSaveAs", "CalculateMesh", "CalculateSeakeeping")},
})


def _text_shape(desc: dict) -> bool:
    return (desc.get("vartype") in (8, 12) or
            any(_text_shape(desc[key]) for key in ("element", "target") if key in desc))


def policy_blocker(module: str, member: dict) -> str | None:
    """Return an explicit effect-policy blocker, for calls and coverage consumers."""
    interface, name, kind = member["interface"], member["name"], member["invocation_kind"]
    owner = module.removesuffix("_xmlgrid")
    if (owner, interface, name) in PROTECTED_WORKFLOWS and owner == "stability":
        return ("protected Stability replacement has no complete restorable project "
                "backup; use the supported additive semantic tools")
    if owner == "motions" and name in (
            "SKResultsSave", "SKResultsSaveAs", "CalculateMesh", "CalculateSeakeeping"):
        return ("binary .skr saving and generic solver execution are refused; use "
                "save_motions_data or run_motions_analysis; obsolete RADIF and "
                "unaccepted MOSES/MARTEC/imported-RAO sequences are not bypassed")
    if (owner, interface, name) in FILE_POLICIES:
        return None if kind == 1 else "file-effect mapping requires the exact method invocation kind"
    if name in BLOCKED_MEMBERS:
        return ("implicit file/state-replacement policy is unavailable; use the "
                "module's semantic lifecycle, persistence, or analysis adapter")
    if kind == 2 and not any(p["input"] for p in member["arguments"]):
        return None
    if interface in UI_INTERFACES or interface.startswith("IUI"):
        return ("UI/accessibility execution has no closed file-effect policy; "
                "use a supported semantic adapter instead of menu execution")
    if interface == "IXMLGrid":
        if name in ("PopulateWithPropSheet", "Populate", "PopulateWithActive",
                    "GetXML", "GetNoOfRows", "GetNoOfColumns", "SetCurrentGrid", "SelectRow"):
            return None
        return "XMLGrid mutation/persistence requires a semantic file adapter"
    if any(_text_shape(p["type"]) for p in member["arguments"] if p["input"]):
        if interface in VALUE_INTERFACES.get(owner, ()):
            return None
        if name in VALUE_MEMBERS.get((owner, interface), ()):
            return None
        return ("unknown native file policy for text/VARIANT input; use a supported "
                "semantic file adapter (export_modeler_file, save_motions_data, "
                "or the module lifecycle adapter), or add an exact reviewed mapping")
    return None


def apply_file_policy(module: str, member: dict, values: dict,
                      overwrite: bool, allow_unmapped_effects: bool = False) -> dict | None:
    blocker = policy_blocker(module, member)
    if blocker:
        protected = (module.removesuffix("_xmlgrid"), member["interface"],
                     member["name"]) in PROTECTED_WORKFLOWS
        if allow_unmapped_effects and not protected:
            return {"mode": "unmapped_diagnostic", "sandbox_validated": False,
                    "explicit_unmapped_effects": True, "default_blocker": blocker}
        raise MaxsurfSafetyError(blocker, member=member["key"])
    policy = FILE_POLICIES.get(
        (module.removesuffix("_xmlgrid"), member["interface"], member["name"]))
    if policy is None:
        return None
    argument, mode, suffixes, native_overwrite = policy
    if argument not in values:
        raise MaxsurfSafetyError("file policy requires an explicit filename", argument=argument)
    if native_overwrite and values.get(native_overwrite, False) is not overwrite:
        raise MaxsurfSafetyError(
            "native overwrite argument must match explicit overwrite consent",
            argument=native_overwrite)
    if mode == "read":
        path = workspace.resolve_existing(values[argument], purpose=member["name"],
                                          allowed_suffixes=suffixes)
    else:
        path = workspace.resolve_target(values[argument], purpose=member["name"],
                                        overwrite=overwrite, allowed_suffixes=suffixes,
                                        create_parents=False)
        if not path.parent.is_dir():
            raise MaxsurfSafetyError("native output parent directory must already exist")
    values[argument] = str(path)
    return {"argument": argument, "mode": mode, "path": str(path),
            "overwrite": overwrite, "evidence": "sandbox_validated_only"}
