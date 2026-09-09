"""Bounded Multiframe structural automation. All numbers use reported native units.

No section-library edits, design-code policy, hull-to-FE inference or implicit
replacement. Native model/solver changes are not atomic and cannot be cancelled.
"""
from __future__ import annotations
import hashlib
import json
import math
import os
from typing import Any
from . import com, constants, validation as val, workspace
from .audit import Channel, Classification
from .errors import MaxsurfAnalysisError, MaxsurfCOMError, MaxsurfSafetyError, MaxsurfValidationError
from .guard import guarded, register

UNIT_NAMES = ("Length", "Angle", "Displacement", "AngularDisplacement", "Force", "Moment",
              "Udl", "Stress", "Mass", "MassPerLength", "Area", "Inertia", "Modulus")
COUNTS = ("nodes", "Elements", "restraints", "springs", "nodeMasses", "nodeLinkGroups", "LoadCases")
FLAGS = ("Linear", "Nonlinear", "Modal", "Buckling", "TimeHistory")
DOFS = ("mfDOFx", "mfDOFy", "mfDOFz", "mfDOFthetax", "mfDOFthetay", "mfDOFthetaz")
NODE_VALUES = ("dx", "dy", "dz", "thetax", "thetay", "thetaz", "rx", "ry", "rz", "Mx", "My", "Mz")
END_VALUES = tuple(prefix + end for end in ("1", "2") for prefix in ("Px", "Vy", "Vz", "Tx", "My", "Mz"))

def _integer(x, name, lo=0, hi=1000000):
    if type(x) is not int or not lo <= x <= hi:
        raise MaxsurfValidationError(f"{name} must be an integer in [{lo}, {hi}]")
    return x

def _boolean(x, name):
    if type(x) is not bool: raise MaxsurfValidationError(f"{name} must be Boolean")
    return x

def _enum(app, name):
    values = constants.discover(app)["constants"]
    if name not in values or type(values[name]) is not int:
        raise MaxsurfValidationError("Native constant is unavailable", constant=name)
    return values[name]

def _native_completed(value, operation):
    # Installed 2025 ITypeInfo declares VT_VOID for lifecycle/Analyse methods,
    # despite legacy CHM Boolean descriptions. Completion still needs readbacks.
    if value is not None and value is not True:
        raise MaxsurfCOMError("Native operation did not complete", operation=operation,
                              native_return=value, partial_state_possible=True)
    return value


def _count(coll): return _integer(coll.Count, "native count")

def _app():
    app = com.connect("multiframe")
    if app.IsInitializedCorrectly is not True:
        raise MaxsurfCOMError("Multiframe automation is not initialized")
    return app, app.Frame

def _unit_state(app):
    units = {}
    for name in UNIT_NAMES:
        value = app.Preferences.GetUnit(_enum(app, "mfUnit" + name))
        if not isinstance(value, str) or not value.strip():
            raise MaxsurfCOMError("Cannot establish native units", category=name)
        units[name] = value
    token = hashlib.sha256(json.dumps(units, sort_keys=True).encode()).hexdigest()
    return {"units": units, "unit_token": token, "basis": "current Multiframe units, NOT fixed SI"}

def _unit_guard(app, token):
    state = _unit_state(app)
    if not isinstance(token, str) or token != state["unit_token"]:
        raise MaxsurfSafetyError("Unit snapshot is missing or changed; read get_multiframe_status first", current=state)
    return state

def _equal(actual, expected, field):
    if isinstance(expected, float):
        good = not isinstance(actual, bool) and math.isfinite(float(actual)) and math.isclose(float(actual), expected, rel_tol=1e-8, abs_tol=1e-8)
    else: good = actual == expected
    if not good: raise MaxsurfCOMError("Native readback mismatch", field=field, expected=expected, actual=actual, partial_state_possible=True)

def _read(field, callback):
    try:return callback()
    except Exception as exc:raise MaxsurfCOMError("Native member failed",member=field,cause=exc) from exc


def _fields(obj, names):
    return {n: val.number(_read(n,lambda: getattr(obj,n)), n) for n in names}

def _batch(raw, required, optional=()):
    rows = val.parsed(raw, list, "rows_json")
    if not 1 <= len(rows) <= 500: raise MaxsurfValidationError("Batch size must be 1..500")
    for row in rows:
        if not isinstance(row, dict) or set(row) - set(required) - set(optional) or set(required) - set(row):
            raise MaxsurfValidationError("Unexpected or missing batch fields", required=list(required), optional=list(optional))
    return rows

def _expected(coll, expected):
    _integer(expected, "expected_count")
    if _count(coll) != expected: raise MaxsurfSafetyError("Collection changed", expected=expected, actual=_count(coll))

def _node(frame, index, label):
    _integer(index, "node index", 1)
    if index > _count(frame.nodes): raise MaxsurfValidationError("Node does not exist", index=index)
    node = frame.nodes.Item(index)
    if not isinstance(label, str) or node.Label != label:
        raise MaxsurfSafetyError("Node label changed", index=index)
    return node

def _beam_nodes(element):
    nodes=element.nodes
    if _count(nodes)!=2:raise MaxsurfCOMError("Expected exactly two beam nodes")
    return [_integer(nodes.Item(i).Index,"node index",1) for i in (1,2)]


def _material_name(element):
    try:return {"available":True,"name":element.material.Name}
    except Exception:return {"available":False,"name":None,
        "note":"No separate material is readable; section E/G are reported, not a material certification."}


def _identity_refresh():
    established = com.session_for("multiframe")
    if established is not None: established.refresh_design_identity()

def _clean(frame):
    if frame.Modified is not False:
        raise MaxsurfSafetyError("Save the modified native frame before replacing it")

def _empty(frame):
    _clean(frame)
    if frame.FullName or any(_count(getattr(frame,n)) for n in COUNTS if n != "LoadCases"):
        raise MaxsurfSafetyError("Existing frame requires explicit replacement consent")
    if _count(frame.LoadCases) != 1: raise MaxsurfSafetyError("Unexpected existing load cases")
    lc=frame.LoadCases.Item(1)
    if any(_count(getattr(lc,n)) for n in ("NodeLoads", "ElementLoads", "PrescribedDisps", "thermalLoads")):
        raise MaxsurfSafetyError("Existing load data must be preserved")

@guarded(classification=Classification.READ, module="multiframe", channel=Channel.MCP_COM)
def get_multiframe_status() -> str:
    """READ. Report native readiness, frame identity/counts, current units and analysis flags.

    Pass the returned unit_token to numeric writes; values are in these native
    units. Never assume metres/newtons from the other Maxsurf adapters.
    """
    app, f = _app()
    optional = {}
    for name in ("Plates", "Patches", "SpringElements", "LoadPanels"):
        try: optional[name] = {"available": True, "count": _count(getattr(f,name))}
        except Exception: optional[name] = {"available": False, "count": None}
    return com.to_json({"module":"multiframe", "version":str(app.Version), "ready":True,
        "frame":{"path":f.FullName, "name":f.Name, "modified":f.Modified},
        "counts":{n:_count(getattr(f,n)) for n in COUNTS}, "optional_objects":optional,
        "analysis_flags":{n:getattr(f.Analysis,n) for n in FLAGS},
        "tension_positive":app.Preferences.TensionPositive, **_unit_state(app),
        "limitations":["No design-code, plate, buckling, nonlinear or time-history adapter.",
                       "No automatic Modeler hull to structural FE conversion."]})

@guarded(classification=Classification.READ, module="multiframe", channel=Channel.MCP_COM)
def list_multiframe_sections(group: str = "", start: int = 1, limit: int = 50) -> str:
    """READ. Page the existing section-library groups or one group's sections; never edits the library."""
    _integer(start,"start",1);_integer(limit,"limit",1,200)
    app, _ = _app();lib=app.SectionsLibrary
    if not group:
        coll=lib.groups
        rows=[{"index":i,"name":coll.Item(i).Name,"sections":_count(coll.Item(i).sections)} for i in range(start,min(_count(coll),start+limit-1)+1)]
    else:
        names=[lib.groups.Item(i).Name for i in range(1,_count(lib.groups)+1)]
        if group not in names:raise MaxsurfValidationError("Section group not found",group=group)
        coll=lib.groups.Item(names.index(group)+1).sections
        rows=[{"index":i,"name":coll.Item(i).Name,**_fields(coll.Item(i),("Area","Ix","Iy","E","G","Mass"))} for i in range(start,min(_count(coll),start+limit-1)+1)]
    return com.to_json({"group":group,"count":_count(coll),"start":start,"rows":rows,**_unit_state(app)})

@guarded(classification=Classification.WRITE_STATE, module="multiframe", channel=Channel.MCP_COM)
def new_multiframe_model(confirm_replace: bool = False) -> str:
    """WRITE_STATE. Start an empty frame; refuse modified work, require consent for an existing saved frame."""
    _boolean(confirm_replace,"confirm_replace");_,f=_app();_clean(f)
    if not confirm_replace:_empty(f)
    try:
        _native_completed(f.New(False),"Frame.New")
        _empty(f)
        return com.to_json({"new":True,"nodes":0,"elements":0,"saved":False})
    finally:_identity_refresh()

@guarded(classification=Classification.WRITE_STATE, module="multiframe", channel=Channel.MCP_COM)
def open_multiframe_model(path: str, confirm_replace: bool = False) -> str:
    """WRITE_STATE. Open a workspace .mfd, refusing modified work and verifying native FullName.

    Does not silently discard work, save an old frame or load a section library.
    """
    _boolean(confirm_replace,"confirm_replace")
    source=workspace.resolve_existing(path,purpose="open Multiframe",allowed_suffixes=(".mfd",))
    _,f=_app();_clean(f)
    if not confirm_replace:_empty(f)
    try:
        _native_completed(f.Open(str(source),False),"Frame.Open")
        _equal(os.path.normcase(os.path.normpath(f.FullName)),os.path.normcase(str(source)),"FullName")
        return com.to_json({"path":str(source),"identity_verified":True,"nodes":_count(f.nodes),"elements":_count(f.Elements),"results_verified":False})
    finally:_identity_refresh()

@guarded(classification=Classification.WRITE_FILE, module="multiframe", channel=Channel.MCP_COM)
def save_multiframe_model(path: str) -> str:
    """WRITE_FILE. Save a new .mfd without overwrite; verify native identity and nonempty file.

    This changes the active SaveAs path. File existence is not a reopen or
    complete analysis-result restoration test. Section libraries remain separate.
    """
    dest=workspace.resolve_target(path,purpose="save Multiframe",overwrite=False,allowed_suffixes=(".mfd",))
    _,f=_app()
    try:
        _native_completed(f.SaveAs(str(dest),False),"Frame.SaveAs")
        if not dest.is_file() or not dest.stat().st_size:raise MaxsurfCOMError("Native save produced no nonempty file")
        _equal(os.path.normcase(os.path.normpath(f.FullName)),os.path.normcase(str(dest)),"FullName")
        with dest.open("rb") as stream: digest=hashlib.file_digest(stream,"sha256").hexdigest()
        return com.to_json({"path":str(dest),"bytes":dest.stat().st_size,"sha256":digest,"identity_verified":True,"reopen_verified":False,"results_restoration_verified":False})
    finally:_identity_refresh()

@guarded(classification=Classification.READ, module="multiframe", channel=Channel.MCP_COM)
def read_multiframe_model(start: int = 1, limit: int = 100) -> str:
    """READ. Page nodes, beams, supports and load-case definitions in current native units."""
    _integer(start,"start",1);_integer(limit,"limit",1,500);app,f=_app()
    out={"counts":{},"start":start,"limit":limit,"rows":{},**_unit_state(app)}
    for name in ("nodes","Elements","restraints","LoadCases"):
        coll=getattr(f,name);count=_count(coll);out["counts"][name]=count;rows=[]
        for i in range(start,min(count,start+limit-1)+1):
            o=coll.Item(i);row={"index":i,"label":o.Label}
            if name=="nodes":row.update(_fields(o,("x","y","z")));row["pinned"]=o.Pinned
            elif name=="Elements":
                row.update({"node1":_beam_nodes(o)[0],"node2":_beam_nodes(o)[1],"section":o.section.Name,
                            "length":val.number(o.Length,"length"),"material":_material_name(o),
                            "section_properties":_fields(o.section,("Area","Ix","Iy","Mass","E","G"))})
            elif name=="restraints":row.update({"node":o.NodeIndex,"type":o.Type,"global":o.Global})
            else:row.update({"name":o.Name,"type":o.Type,"node_loads":_count(o.NodeLoads),"element_loads":_count(o.ElementLoads)})
            rows.append(row)
        out["rows"][name]=rows
    return com.to_json(out)

@guarded(classification=Classification.WRITE_STATE, module="multiframe", channel=Channel.MCP_COM)
def add_multiframe_nodes(nodes_json: str, expected_count: int, unit_token: str) -> str:
    """WRITE_STATE. Append nodes [{label,x,y,z}]; validate the complete batch and unit/count snapshots first.

    Labels must be unique. Coordinates use the reported Length unit, not assumed metres.
    A failed native write may leave a partial batch; inspect before retrying.
    """
    rows=_batch(nodes_json,("label","x","y","z"))
    specs=[{"label":val.name(r["label"]),**{a:val.number(r[a],a) for a in ("x","y","z")}} for r in rows]
    if len({r["label"] for r in specs})!=len(specs):raise MaxsurfValidationError("Duplicate labels in batch")
    app,f=_app();state=_unit_guard(app,unit_token);_expected(f.nodes,expected_count)
    old={f.nodes.Item(i).Label for i in range(1,expected_count+1)}
    if old & {r["label"] for r in specs}:raise MaxsurfValidationError("Node label already exists")
    created=[]
    for r in specs:
        n=f.nodes.AddNode(r["x"],r["y"],r["z"])
        val.stored(n,"Label",r["label"])
        for a in ("x","y","z"):_equal(getattr(n,a),r[a],a)
        created.append({"index":n.Index,**r})
    _equal(_count(f.nodes),expected_count+len(specs),"node count")
    return com.to_json({"created":created,**state})

@guarded(classification=Classification.WRITE_STATE, module="multiframe", channel=Channel.MCP_COM)
def add_multiframe_elements(elements_json: str, expected_count: int, unit_token: str) -> str:
    """WRITE_STATE. Append beams with explicit nodes and existing library sections.

    Each row: label,node1,node1_label,node2,node2_label,section_group,section.
    Optional dynamic_mass Boolean defaults true. Does not edit section libraries
    or invent scantlings. Read the resulting material and section before analysis.
    """
    rows=_batch(elements_json,("label","node1","node1_label","node2","node2_label","section_group","section"),("dynamic_mass",))
    specs=[]
    for r in rows:
        q=dict(r)
        for key in ("label","section_group","section"):q[key]=val.name(r[key],key)
        for key in ("node1","node2"):_integer(r[key],key,1)
        q["dynamic_mass"]=_boolean(r.get("dynamic_mass",True),"dynamic_mass")
        if r["node1"]==r["node2"]:raise MaxsurfValidationError("Beam endpoints must differ")
        specs.append(q)
    if len({q["label"] for q in specs})!=len(specs):raise MaxsurfValidationError("Duplicate labels")
    app,f=_app();state=_unit_guard(app,unit_token);_expected(f.Elements,expected_count)
    old={f.Elements.Item(i).Label for i in range(1,expected_count+1)}
    if old & {q["label"] for q in specs}:raise MaxsurfValidationError("Element label already exists")
    resolved=[]
    for q in specs:
        n1=_node(f,q["node1"],q["node1_label"]);n2=_node(f,q["node2"],q["node2_label"])
        if math.dist([n1.x,n1.y,n1.z],[n2.x,n2.y,n2.z])<=1e-10:raise MaxsurfValidationError("Zero-length beam")
        section=app.SectionsLibrary.GetSection(q["section_group"],q["section"])
        if section is None or section.Name!=q["section"]:raise MaxsurfValidationError("Section not found")
        resolved.append((q,n1,n2))
    created=[]
    for q,n1,n2 in resolved:
        e=f.Elements.AddElement(n1,n2)
        _native_completed(e.SetSection(q["section"],q["section_group"]),"Element.SetSection")
        val.stored(e,"Label",q["label"]);val.stored(e,"UseDynamicSelfWeight",q["dynamic_mass"])
        _equal(_beam_nodes(e),[q["node1"],q["node2"]],"element node indices")
        _equal(e.section.Name,q["section"],"section name")
        created.append({"index":e.Index,"label":e.Label,"section":e.section.Name,"material":_material_name(e)})
    _equal(_count(f.Elements),expected_count+len(specs),"element count")
    return com.to_json({"created":created,**state})

@guarded(classification=Classification.WRITE_STATE, module="multiframe", channel=Channel.MCP_COM)
def add_multiframe_restraints(restraints_json: str, expected_count: int, unit_token: str) -> str:
    """WRITE_STATE. Append supports [{node,node_label,type}] using named mfRestraint constants in global axes."""
    rows=_batch(restraints_json,("node","node_label","type"))
    for r in rows:
        _integer(r["node"],"node",1)
        if not isinstance(r["type"],str) or not r["type"].startswith("mfRestraint") or r["type"]=="mfRestraintUnknown":
            raise MaxsurfValidationError("Use a named native mfRestraint type")
    if len({r["node"] for r in rows})!=len(rows):raise MaxsurfValidationError("Duplicate support nodes")
    app,f=_app();state=_unit_guard(app,unit_token);_expected(f.restraints,expected_count)
    existing={f.restraints.Item(i).NodeIndex for i in range(1,expected_count+1)}
    if existing & {r["node"] for r in rows}:raise MaxsurfValidationError("Node already has a restraint")
    prepared=[(r,_node(f,r["node"],r["node_label"]),_enum(app,r["type"])) for r in rows]
    for r,n,t in prepared:
        o=f.restraints.AddRestraint(n,t);val.stored(o,"Global",True)
        _equal(o.NodeIndex,r["node"],"support node");_equal(o.Type,t,"support type")
    _equal(_count(f.restraints),expected_count+len(rows),"support count")
    return com.to_json({"added":len(rows),"count":_count(f.restraints),**state})

@guarded(classification=Classification.WRITE_STATE, module="multiframe", channel=Channel.MCP_COM)
def add_multiframe_loadcase(name: str, loads_json: str, expected_count: int, unit_token: str) -> str:
    """WRITE_STATE. Append a static load case with nodal loads [{node,node_label,dof,value}].

    Named mfDOF translations take Force units; rotations take Moment units.
    Loads are global, not local. Existing load cases and library data are retained.
    """
    name=val.name(name);rows=_batch(loads_json,("node","node_label","dof","value"))
    for r in rows:
        _integer(r["node"],"node",1)
        if r["dof"] not in DOFS:raise MaxsurfValidationError("Unsupported DOF",allowed=DOFS)
        r["value"]=val.number(r["value"],"value")
    app,f=_app();state=_unit_guard(app,unit_token);_expected(f.LoadCases,expected_count)
    if name in [f.LoadCases.Item(i).Name for i in range(1,expected_count+1)]:raise MaxsurfValidationError("Load-case name exists")
    prepared=[(r,_node(f,r["node"],r["node_label"]),_enum(app,r["dof"])) for r in rows]
    case_type=_enum(app,"mflcStatic")
    lc=f.LoadCases.AddCase(case_type,name);_equal(lc.Name,name,"loadcase name");_equal(lc.Type,case_type,"loadcase type")
    for r,n,dof in prepared:
        o=lc.NodeLoads.AddLoad(n,dof,r["value"],True)
        _equal(o.Value,r["value"],"load value");_equal(o.DOF,dof,"load DOF");_equal(o.Global,True,"global load")
        _equal(o.Node.Index,r["node"],"load node")
    _equal(_count(lc.NodeLoads),len(rows),"load count");_equal(_count(f.LoadCases),expected_count+1,"loadcase count")
    return com.to_json({"index":lc.Index,"name":name,"loads":len(rows),**state})

@guarded(classification=Classification.EXECUTE_ANALYSIS, module="multiframe", channel=Channel.MCP_COM)
def run_multiframe_analysis(unit_token: str, method: str = "linear", modes: int = 3) -> str:
    """EXECUTE_ANALYSIS. Run a 3D linear static or distributed-mass modal analysis.

    Other analysis flags are turned off and read back. Modal needs element or
    nodal mass. Native success is not a design-code or ship-structure verdict.
    """
    if method not in ("linear","modal"):raise MaxsurfValidationError("Only linear and modal runners are implemented")
    _integer(modes,"modes",1,30);app,f=_app();state=_unit_guard(app,unit_token)
    if _count(f.nodes)<2 or _count(f.Elements)<1 or _count(f.restraints)<1:raise MaxsurfValidationError("Model needs nodes, beams and supports")
    before={n:getattr(f.Analysis,n) for n in FLAGS}
    for n in FLAGS:setattr(f.Analysis,n,n==("Linear" if method=="linear" else "Modal"))
    for n in FLAGS:_equal(getattr(f.Analysis,n),n==("Linear" if method=="linear" else "Modal"),n)
    if method=="modal":
        val.stored(f.Analysis.ModalSettings,"DistributedMass",True)
        _equal(f.Analysis.ModalSettings.LumpedMass,False,"LumpedMass")
        val.stored(f.Analysis.ModalSettings,"Modes",modes)
    native_return = _native_completed(_read("Analysis.Analyse",lambda:f.Analysis.Analyse(True)),"Analysis.Analyse")
    family=f.Results.Linear if method=="linear" else f.Results.Modal
    count=_count(family.Cases)
    solved=[i for i in range(1,count+1) if _read(f"{method}.Cases({i}).Solved",lambda:family.Cases.Item(i).Solved) is True]
    if not solved:raise MaxsurfAnalysisError("Solver returned without any solved result case")
    return com.to_json({"method":method,"native_return":native_return,"solved_cases":solved,
                       "previous_flags":before,"engineering_verdict":"not_evaluated",**state})

@guarded(classification=Classification.READ, module="multiframe", channel=Channel.MCP_COM)
def get_multiframe_results(method: str = "linear", case_index: int = 1, start: int = 1, limit: int = 100) -> str:
    """READ. Read a solved linear case or modal shape, with native units and bounded rows.

    Node reaction/displacement axes and beam-local end-action signs follow native
    conventions. No SI conversion or automatic structural compliance verdict.
    """
    if method not in ("linear","modal"):raise MaxsurfValidationError("Only linear and modal result mappings are implemented")
    _integer(case_index,"case_index",1);_integer(start,"start",1);_integer(limit,"limit",1,500)
    app,f=_app();state=_unit_state(app);family=f.Results.Linear if method=="linear" else f.Results.Modal
    if case_index>_count(family.Cases):raise MaxsurfValidationError("Result case does not exist")
    case=_read(f"{method}.Cases({case_index})",lambda:family.Cases.Item(case_index))
    if _read(f"{method}.Cases({case_index}).Solved",lambda:case.Solved) is not True:raise MaxsurfAnalysisError("Requested native result case is unsolved")
    data={"method":method,"case_index":case_index,"name":case.Name,"solved":True,"rows":{},"counts":{},
          "start":start,"limit":limit,"tension_positive":app.Preferences.TensionPositive,
          "engineering_verdict":"not_evaluated",**state}
    if method=="modal":data["modal"]={"frequency_native":val.number(_read("Modal.Frequency",lambda:family.Frequency(case_index)),"frequency",positive=True),"period_native":val.number(_read("Modal.Period",lambda:family.Period(case_index)),"period",positive=True)}
    collections=(("NodeResults",NODE_VALUES),("ElementResults",END_VALUES))
    if method=="modal":
        collections=(("NodeResults",NODE_VALUES[:6]),)
        data["modal"]["scaling_native"]=f.Analysis.ModalSettings.Scaling
        data["modal"]["note"]="Scaled eigenvector, not physical response amplitude. Static reactions/end actions are not requested."
    for cname,fields in collections:
        coll=getattr(case,cname);count=_count(coll);data["counts"][cname]=count;out=[]
        if count==0:raise MaxsurfAnalysisError("Solved case has an empty result collection",collection=cname)
        for i in range(start,min(count,start+limit-1)+1):
            row=coll.Item(i)
            if _read(f"{cname}({i}).Solved",lambda:row.Solved) is not True:raise MaxsurfAnalysisError("Result row is unsolved",index=i)
            if cname=="NodeResults":
                identity=row.NodeIndex;values=_fields(row,fields)
            else:
                identity=row.ElementIndex
                values={key:val.number(_read(key,lambda:getattr(row,key[:-1])(int(key[-1]))),key) for key in fields}
            out.append({"index":i,"object_index":_integer(identity,"result object index",1),**values})
        data["rows"][cname]=out
    return com.to_json(data)

@guarded(classification=Classification.WRITE_FILE, module="multiframe", channel=Channel.MCP_COM)
def export_multiframe_results(path: str, method: str = "linear", case_index: int = 1, start: int = 1, limit: int = 100) -> str:
    """WRITE_FILE. Export a bounded solved-result page as JSON, with units; no overwrite."""
    dest=workspace.resolve_target(path,purpose="Multiframe results",overwrite=False,allowed_suffixes=(".json",))
    payload=get_multiframe_results.__wrapped__(method,case_index,start,limit)
    with dest.open("x",encoding="utf-8") as stream:stream.write(payload)
    with dest.open("rb") as stream:digest=hashlib.file_digest(stream,"sha256").hexdigest()
    return com.to_json({"path":str(dest),"bytes":dest.stat().st_size,"sha256":digest,"page_only":True})

@guarded(classification=Classification.WRITE_STATE, module="multiframe", channel=Channel.MCP_COM)
def refresh_multiframe_view() -> str:
    """WRITE_STATE. Enable native screen updates and refresh; no clipboard or arbitrary UI commands."""
    app,_=_app();val.stored(app,"ScreenUpdating",True)
    result=app.UpdateAll()
    if result is False:raise MaxsurfCOMError("Native UpdateAll failed")
    return com.to_json({"screen_updating":app.ScreenUpdating,"refreshed":True,"visual_quality_verified":False})

TOOLS = (get_multiframe_status, list_multiframe_sections, read_multiframe_model,
         new_multiframe_model, open_multiframe_model, save_multiframe_model,
         add_multiframe_nodes, add_multiframe_elements, add_multiframe_restraints,
         add_multiframe_loadcase, run_multiframe_analysis, get_multiframe_results,
         export_multiframe_results, refresh_multiframe_view)

def register_tools(mcp: Any) -> list[str]:
    return register(mcp, TOOLS)
