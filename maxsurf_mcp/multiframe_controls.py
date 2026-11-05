"""Explicit Multiframe controls verified against installed 1.3 native metadata.

All numeric writes use the existing native unit snapshot. Array readback proves
stored values, not the engineering interpretation of an unaccepted native build.
"""
from __future__ import annotations

from typing import Any
from . import multiframe as m, validation as val, com
from .audit import Channel, Classification
from .errors import MaxsurfValidationError, MaxsurfSafetyError, MaxsurfCOMError
from .guard import guarded, register

SHAPES = ("mfLoadshapePoint", "mfLoadshapeUniform", "mfLoadshapeLinear",
          "mfLoadshapeTriangular", "mfLoadshapeTriLinear")
SOLVERS = ("mfModalSolverSSI", "mfModalSolverJacobi", "mfModalSolverInverseIteration")
SCALINGS = ("mfModalScalingUnitDisplacement", "mmfModalScalingUnitMass")
UNITSETS = tuple("mfUnitset"+n for n in
                 ("American", "Australian", "British", "Canadian", "European", "Japanese"))
BEAM_FIELDS = ("Orientation", "Releases1", "Releases2", "offset1", "offset2",
               "OffsetsLocal", "UseStaticSelfWeight", "UseDynamicSelfWeight")
OPTIONAL = ("Plates", "Patches", "SpringElements", "LoadPanels")

def _choice(value, choices, field):
    if not isinstance(value, str) or value not in choices:
        raise MaxsurfValidationError("Unsupported native choice", field=field, allowed=choices)
    return value

def _array(value, field, size, boolean=False):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise MaxsurfValidationError("Invalid native array length", field=field, size=size)
    return tuple(m._boolean(v, field) if boolean else val.number(v, field) for v in value)

def _array_equal(actual, wanted, field):
    if not isinstance(actual, (list, tuple)) or len(actual) != len(wanted):
        raise MaxsurfCOMError("Native array readback mismatch", field=field, partial_state_possible=True)
    for a, b in zip(actual, wanted):
        if isinstance(b, bool) and type(a) is not bool:
            raise MaxsurfCOMError("Native Boolean array readback mismatch", field=field)
        m._equal(a, b, field)

def _item(coll, index, label, field="Label"):
    m._integer(index, "index", 1)
    if index > m._count(coll):
        raise MaxsurfValidationError("Native object does not exist", index=index)
    obj = m._read("Item", lambda: coll.Item(index))
    if not isinstance(label, str) or getattr(obj, field) != label:
        raise MaxsurfSafetyError("Native object identity changed", index=index)
    return obj

@guarded(classification=Classification.READ, module="multiframe", channel=Channel.MCP_COM)
def get_multiframe_element_controls(element_index: int, element_label: str) -> str:
    """READ. Beam orientation (Angle), offsets (Length), six native release slots and self-weight flags."""
    app, frame = m._app()
    obj = _item(frame.Elements, element_index, element_label)
    values = {n:m._read(n, lambda n=n:getattr(obj, n)) for n in BEAM_FIELDS}
    return com.to_json({"index":element_index, "controls":values, **m._unit_state(app)})

@guarded(classification=Classification.WRITE_STATE, module="multiframe", channel=Channel.MCP_COM)
def set_multiframe_element_controls(element_index: int, element_label: str,
                                    controls_json: str, unit_token: str) -> str:
    """WRITE_STATE. Set explicit native beam fields; omitted fields are retained.

    Fields: Orientation, Releases1/2 (six Booleans), offset1/2 (three Length
    values), OffsetsLocal, UseStaticSelfWeight, UseDynamicSelfWeight.
    Release slots are passed in native order without correcting manual errata.
    Offsets require explicit OffsetsLocal to avoid accidental axis assumptions.
    """
    raw = val.parsed(controls_json, dict, "controls_json")
    if not raw or set(raw)-set(BEAM_FIELDS):
        raise MaxsurfValidationError("Unsupported or empty beam controls", allowed=BEAM_FIELDS)
    if any(n in raw for n in ("offset1", "offset2")) and "OffsetsLocal" not in raw:
        raise MaxsurfValidationError("Offsets require explicit OffsetsLocal")
    values = {}
    for n, v in raw.items():
        values[n] = (_array(v,n,6,True) if n.startswith("Releases") else
                     _array(v,n,3) if n.startswith("offset") else
                     val.number(v,n) if n=="Orientation" else m._boolean(v,n))
    app, frame = m._app(); state = m._unit_guard(app, unit_token)
    obj = _item(frame.Elements, element_index, element_label)
    # Set the coordinate system before its offsets, then verify final state
    # after all interdependent properties have been written.
    for n in ("OffsetsLocal",)+tuple(n for n in BEAM_FIELDS if n!="OffsetsLocal"):
        if n not in values: continue
        v = values[n]
        m._read(n, lambda n=n,v=v:setattr(obj,n,v))
    for n,v in values.items():
        actual = m._read(n,lambda n=n:getattr(obj,n))
        if isinstance(v, tuple): _array_equal(actual,v,n)
        else: m._equal(actual,v,n)
    return com.to_json({"index":element_index,"stored":values,**state})

@guarded(classification=Classification.WRITE_STATE, module="multiframe", channel=Channel.MCP_COM)
def add_multiframe_element_loads(case_index: int, case_name: str, loads_json: str,
                                 expected_count: int, unit_token: str) -> str:
    """WRITE_STATE. Append native member loads with explicit axes and shape.

    Rows: element,element_label,dof,shape,value1,distance1,value2,distance2,global.
    Distance1 is from start; Distance2 from end (Length). Native manual specifies
    current force/moment units for magnitudes; no implicit Udl/SI conversion.
    Unused shape parameters must be zero. A failed native batch can be partial.
    """
    rows=m._batch(loads_json,("element","element_label","dof","shape","value1",
                            "distance1","value2","distance2","global"))
    for r in rows:
        m._integer(r["element"],"element",1)
        _choice(r["dof"],m.DOFS,"dof"); _choice(r["shape"],SHAPES,"shape")
        m._boolean(r["global"],"global")
        for n in ("value1","value2","distance1","distance2"):
            r[n]=val.number(r[n],n,minimum=0 if n.startswith("distance") else None)
        unused = (("distance1","value2","distance2") if r["shape"]=="mfLoadshapeUniform"
                  else ("value2","distance2") if r["shape"] in ("mfLoadshapePoint","mfLoadshapeTriangular") else ())
        if any(r[n]!=0 for n in unused):
            raise MaxsurfValidationError("Unused native shape parameters must be zero", fields=unused)
    app,f=m._app();state=m._unit_guard(app,unit_token)
    lc=_item(f.LoadCases,case_index,case_name,"Name")
    if lc.Type != m._enum(app,"mflcStatic"):
        raise MaxsurfValidationError("Member load tool requires a static case")
    m._expected(lc.ElementLoads,expected_count)
    prepared=[]
    for r in rows:
        e=_item(f.Elements,r["element"],r["element_label"])
        length=val.number(e.Length,"Length",positive=True)
        if r["distance1"]+r["distance2"]>length:
            raise MaxsurfValidationError("Load distances exceed element length")
        prepared.append((r,e,m._enum(app,r["dof"]),m._enum(app,r["shape"])))
    created=[]
    for r,e,dof,shape in prepared:
        obj=m._read("ElementLoads.AddLoad",lambda:lc.ElementLoads.AddLoad(
            e,dof,shape,r["value1"],r["distance1"],r["value2"],r["distance2"],r["global"]))
        for n,v in (("DOF",dof),("Shape",shape),("Value1",r["value1"]),
                    ("Dist1",r["distance1"]),("Value2",r["value2"]),
                    ("Dist2",r["distance2"]),("Global",r["global"])):
            m._equal(m._read(n,lambda n=n:getattr(obj,n)),v,n)
        m._equal(obj.Element.Index,r["element"],"element")
        created.append(obj.Index)
    m._equal(m._count(lc.ElementLoads),expected_count+len(rows),"element load count")
    return com.to_json({"created":created,**state})

@guarded(classification=Classification.WRITE_STATE, module="multiframe", channel=Channel.MCP_COM)
def set_multiframe_modal_controls(modes: int, solver: str, scaling: str,
                                  mass_formulation: str, max_iterations: int,
                                  tolerance: float, unit_token: str) -> str:
    """WRITE_STATE. Set modal solver/scaling and only the selected exclusive mass toggle.

    Unit-mass scaling uses the native spelling mmfModalScalingUnitMass.
    Pass the same modes and mass_formulation to run_multiframe_analysis, which
    explicitly selects those two settings. This tool does not run analysis.
    """
    m._integer(modes,"modes",1,30);m._integer(max_iterations,"max_iterations",1,32767)
    tolerance=val.number(tolerance,"tolerance",positive=True)
    _choice(solver,SOLVERS,"solver");_choice(scaling,SCALINGS,"scaling")
    _choice(mass_formulation,("distributed","lumped"),"mass_formulation")
    app,f=m._app();state=m._unit_guard(app,unit_token)
    values={"Modes":modes,"Solver":m._enum(app,solver),"Scaling":m._enum(app,scaling),
            "MaxIterations":max_iterations,"Tolerance":tolerance}
    obj=f.Analysis.ModalSettings
    selected="DistributedMass" if mass_formulation=="distributed" else "LumpedMass"
    for n,v in {**values,selected:True}.items():
        m._read(n,lambda n=n,v=v:val.stored(obj,n,v))
    m._equal(getattr(obj,"LumpedMass" if selected=="DistributedMass" else "DistributedMass"),False,"opposite mass selector")
    return com.to_json({"stored":values,"mass_formulation":mass_formulation,**state})

@guarded(classification=Classification.WRITE_STATE, module="multiframe", channel=Channel.MCP_COM)
def set_multiframe_units(units_json: str, unit_token: str, unit_set: str = "") -> str:
    """WRITE_STATE. Change native units; return a fresh token, invalidating prior numeric snapshots.

    units_json maps reported category names to exact native unit strings. Optional
    unit_set is a native mfUnitset name. Unknown unit strings are rejected by native
    readback; failures can leave a partially changed unit configuration.
    """
    units=val.parsed(units_json,dict,"units_json")
    if not isinstance(unit_set,str):
        raise MaxsurfValidationError("unit_set must be a native name or empty string")
    if set(units)-set(m.UNIT_NAMES) or (not units and not unit_set):
        raise MaxsurfValidationError("Unsupported or empty unit changes",allowed=m.UNIT_NAMES)
    units={n:val.name(v,n) for n,v in units.items()}
    if unit_set: _choice(unit_set,UNITSETS,"unit_set")
    app,_=m._app();m._unit_guard(app,unit_token)
    resolved={n:m._enum(app,"mfUnit"+n) for n in units}
    if unit_set:
        native=m._enum(app,unit_set)
        m._read("UnitSet",lambda:val.stored(app.Preferences,"UnitSet",native))
    for n,v in units.items():
        m._native_completed(m._read("SetUnit",lambda:app.Preferences.SetUnit(resolved[n],v)),"SetUnit")
        m._equal(m._read("GetUnit",lambda:app.Preferences.GetUnit(resolved[n])),v,n)
    return com.to_json({"changed":True,**m._unit_state(app)})

@guarded(classification=Classification.READ, module="multiframe", channel=Channel.MCP_COM)
def read_multiframe_auxiliary_objects(collection: str, start: int = 1, limit: int = 100) -> str:
    """READ. Inspect accessible plates/patches/spring elements/load panels, springs, masses or links.

    Optional FE collections expose identity only in installed native 1.3 metadata;
    no physical geometry or creation signature is invented.
    """
    _choice(collection,OPTIONAL+("springs","nodeMasses","nodeLinkGroups"),"collection")
    m._integer(start,"start",1);m._integer(limit,"limit",1,500)
    app,f=m._app();coll=m._read(collection,lambda:getattr(f,collection));rows=[]
    for i in range(start,min(m._count(coll),start+limit-1)+1):
        obj=coll.Item(i);row={"index":i,"label":obj.Label}
        if collection=="springs":
            row.update({n:m._read(n,lambda n=n:getattr(obj,n)) for n in ("NodeIndex","Value","DOF","Global","Type","Active")})
        elif collection=="nodeMasses":
            row.update(node=obj.NodeIndex,masses=m._read("Masses",lambda:obj.Masses))
        elif collection=="nodeLinkGroups":
            row.update(master=obj.MasterNode.Index,linked_dof=obj.LinkedDOF,
                       nodes=[obj.nodes.Item(j).Index for j in range(1,m._count(obj.nodes)+1)])
        rows.append(row)
    return com.to_json({"collection":collection,"count":m._count(coll),"rows":rows,**m._unit_state(app)})

@guarded(classification=Classification.WRITE_STATE, module="multiframe", channel=Channel.MCP_COM)
def add_multiframe_node_masses(masses_json: str, expected_count: int, unit_token: str) -> str:
    """WRITE_STATE. Append masses [{node,node_label,masses}] with six nonnegative values.

    Slots x/y/z use Mass; rotations use RotationalMass. Native AddMass accepts a
    node Variant and a six-value Variant array in DOF order. Existing masses remain.
    """
    rows=m._batch(masses_json,("node","node_label","masses"))
    for r in rows:
        m._integer(r["node"],"node",1)
        r["masses"]=_array(r["masses"],"masses",6)
        if any(v<0 for v in r["masses"]) or not any(r["masses"]):
            raise MaxsurfValidationError("Masses must be nonnegative with at least one positive slot")
    app,f=m._app();state=m._unit_guard(app,unit_token);m._expected(f.nodeMasses,expected_count)
    prepared=[(r,m._node(f,r["node"],r["node_label"])) for r in rows]
    created=[]
    for r,node in prepared:
        obj=m._read("NodeMasses.AddMass",lambda:f.nodeMasses.AddMass(node,r["masses"]))
        m._equal(obj.NodeIndex,r["node"],"mass node")
        _array_equal(m._read("Masses",lambda:obj.Masses),r["masses"],"Masses")
        created.append(obj.Index)
    m._equal(m._count(f.nodeMasses),expected_count+len(rows),"mass count")
    return com.to_json({"created":created,**state})

@guarded(classification=Classification.WRITE_STATE, module="multiframe", channel=Channel.MCP_COM)
def add_multiframe_springs(springs_json: str, expected_count: int, unit_token: str) -> str:
    """WRITE_STATE. Append nodal springs with explicit axes/type.

    Rows: node,node_label,dof,stiffness,global,type. Stiffness uses Spring for
    translations and TorsionSpring for rotations. type is mfSpringStandard,
    mfSpringTensionOnly or mfSpringCompressionOnly; no analysis is run.
    """
    rows=m._batch(springs_json,("node","node_label","dof","stiffness","global","type"))
    types=("mfSpringStandard","mfSpringTensionOnly","mfSpringCompressionOnly")
    for r in rows:
        m._integer(r["node"],"node",1);_choice(r["dof"],m.DOFS,"dof")
        _choice(r["type"],types,"type");m._boolean(r["global"],"global")
        r["stiffness"]=val.number(r["stiffness"],"stiffness",positive=True)
    app,f=m._app();state=m._unit_guard(app,unit_token);m._expected(f.springs,expected_count)
    prepared=[(r,m._node(f,r["node"],r["node_label"]),m._enum(app,r["dof"]),
               m._enum(app,r["type"])) for r in rows]
    created=[]
    for r,node,dof,kind in prepared:
        obj=m._read("Springs.AddSpring",lambda:f.springs.AddSpring(node,dof,r["stiffness"]))
        for n,v in (("Global",r["global"]),("Type",kind)):
            m._read(n,lambda n=n,v=v:val.stored(obj,n,v))
        for n,v in (("NodeIndex",r["node"]),("DOF",dof),("Value",r["stiffness"])):
            m._equal(m._read(n,lambda n=n:getattr(obj,n)),v,n)
        created.append(obj.Index)
    m._equal(m._count(f.springs),expected_count+len(rows),"spring count")
    return com.to_json({"created":created,**state})

@guarded(classification=Classification.WRITE_STATE, module="multiframe", channel=Channel.MCP_COM)
def add_multiframe_node_link(name: str, nodes_json: str, master_index: int,
                             master_label: str, linked_dof_json: str,
                             expected_count: int, unit_token: str) -> str:
    """WRITE_STATE. Append a named master/slave group with explicit six Boolean DOFs.

    nodes_json is [{node,node_label}] including the master. Native AddGroup accepts
    the label, array of node indices, master node Variant and six-Boolean array.
    """
    name=val.name(name);rows=m._batch(nodes_json,("node","node_label"))
    linked=_array(val.parsed(linked_dof_json,list,"linked_dof_json"),"linked_dof",6,True)
    m._integer(master_index,"master_index",1)
    indices=[m._integer(r["node"],"node",1) for r in rows]
    if len(indices)<2 or len(set(indices))!=len(indices) or master_index not in indices or not any(linked):
        raise MaxsurfValidationError("Link needs distinct nodes including master and at least one linked DOF")
    app,f=m._app();state=m._unit_guard(app,unit_token);m._expected(f.nodeLinkGroups,expected_count)
    if any(f.nodeLinkGroups.Item(i).Label==name for i in range(1,expected_count+1)):
        raise MaxsurfValidationError("Link group name exists")
    for r in rows:m._node(f,r["node"],r["node_label"])
    master=m._node(f,master_index,master_label)
    obj=m._read("NodeLinkGroups.AddGroup",lambda:f.nodeLinkGroups.AddGroup(name,tuple(indices),master,linked))
    m._equal(obj.Label,name,"link label");m._equal(obj.MasterNode.Index,master_index,"link master")
    actual=[obj.nodes.Item(i).Index for i in range(1,m._count(obj.nodes)+1)]
    m._equal(sorted(actual),sorted(indices),"linked nodes")
    _array_equal(obj.LinkedDOF,linked,"LinkedDOF")
    m._equal(m._count(f.nodeLinkGroups),expected_count+1,"link count")
    return com.to_json({"index":obj.Index,"name":name,**state})

TOOLS = (get_multiframe_element_controls, set_multiframe_element_controls,
         add_multiframe_element_loads, set_multiframe_modal_controls,
         set_multiframe_units, read_multiframe_auxiliary_objects,
         add_multiframe_node_masses, add_multiframe_springs, add_multiframe_node_link)

def register_tools(mcp: Any) -> list[str]:
    return register(mcp,TOOLS)
