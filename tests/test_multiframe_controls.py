"""Offline contracts: native selectors, shapes, arrays and stored readbacks."""
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
from tests import test_multiframe as fixture
from maxsurf_mcp import multiframe as m, multiframe_controls as c
from maxsurf_mcp.errors import MaxsurfValidationError, MaxsurfSafetyError, MaxsurfCOMError

class Settings:
    """Native mass setters select their own formulation regardless of False."""
    def __init__(self):
        self.selected="lumped"
        self.Modes=1;self.Solver=1;self.Scaling=1;self.MaxIterations=10;self.Tolerance=.001
        self.writes=[]
    @property
    def DistributedMass(self):return self.selected=="distributed"
    @DistributedMass.setter
    def DistributedMass(self,value):
        self.writes.append(("DistributedMass",value));self.selected="distributed"
    @property
    def LumpedMass(self):return self.selected=="lumped"
    @LumpedMass.setter
    def LumpedMass(self,value):
        self.writes.append(("LumpedMass",value));self.selected="lumped"

class ControlsTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.MultiframeTests("test_unit_state_is_explicit")
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.built()
        self.app=self.f.app;self.frame=self.f.frame;self.token=self.f.token
        for names in (c.SHAPES,c.SOLVERS,c.SCALINGS,c.UNITSETS,
                      ("mfSpringStandard","mfSpringTensionOnly","mfSpringCompressionOnly")):
            self.f.values.update({n:i for i,n in enumerate(names,1)})
        self.e=self.frame.Elements.Item(1)
        self.e.Orientation=0.
        self.e.Releases1=self.e.Releases2=(False,)*6
        self.e.offset1=self.e.offset2=(0.,)*3
        self.e.OffsetsLocal=False;self.e.UseStaticSelfWeight=False
        self.frame.Analysis.ModalSettings=Settings()
        self.frame.Analysis.Analyse=Mock(return_value=None)
    def controls(self, values, token=None):
        return c.set_multiframe_element_controls.__wrapped__(1,"E",json.dumps(values),token or self.token)
    def load(self, **changes):
        row=dict(element=1,element_label="E",dof="mfDOFy",shape="mfLoadshapeLinear",
                 value1=-2,distance1=1,value2=-3,distance2=2,**{"global":False})
        row.update(changes);return row
    def loads(self, rows, count=0):
        return c.add_multiframe_element_loads.__wrapped__(1,"Default",json.dumps(rows),count,self.token)
    def prepare_loads(self):
        coll=self.f.default.ElementLoads
        def add(e,dof,shape,v1,d1,v2,d2,glob):
            return coll.add(NS(Element=e,DOF=dof,Shape=shape,Value1=v1,Dist1=d1,
                               Value2=v2,Dist2=d2,Global=glob))
        coll.AddLoad=Mock(side_effect=add)
        return coll
    def test_beam_controls_and_native_array_readback(self):
        values=dict(Orientation=45,Releases1=[False,False,False,True,True,False],
                    offset1=[1,2,3],OffsetsLocal=True,UseStaticSelfWeight=True,
                    UseDynamicSelfWeight=False)
        out=json.loads(self.controls(values))
        self.assertEqual(self.e.offset1,(1.,2.,3.));self.assertTrue(self.e.UseStaticSelfWeight)
        self.assertEqual(out["stored"]["Releases1"],values["Releases1"])
        read=json.loads(c.get_multiframe_element_controls.__wrapped__(1,"E"))
        self.assertEqual(read["controls"]["Orientation"],45)
    def test_beam_invalid_inputs_prevent_any_change(self):
        invalid=[{},{"Orientation":True},{"Releases1":[True]*5},
                 {"Releases2":[0]*6},{"offset1":[1,2,3]},{"OffsetsLocal":"yes"},
                 {"Orientation":12,"Unknown":1}]
        for v in invalid:
            with self.subTest(v=v),self.assertRaises(MaxsurfValidationError):self.controls(v)
        self.assertEqual(self.e.Orientation,0)
    def test_identity_and_unit_guards(self):
        with self.assertRaises(MaxsurfSafetyError):
            c.set_multiframe_element_controls.__wrapped__(1,"wrong",'{"Orientation":1}',self.token)
        self.f.units[1]="changed"
        with self.assertRaises(MaxsurfSafetyError):self.controls({"Orientation":1})
        self.assertEqual(self.e.Orientation,0)
    def test_native_array_mismatch_is_not_success(self):
        class Refusal:
            Label="E"
            @property
            def Releases1(self):return (False,)*6
            @Releases1.setter
            def Releases1(self,v):pass
        self.frame.Elements.rows[0]=Refusal()
        with self.assertRaises(MaxsurfCOMError):self.controls({"Releases1":[True]*6})
    def test_native_exception_is_typed(self):
        class Broken:
            Label="E"
            def __setattr__(self,n,v):raise RuntimeError("native")
        self.frame.Elements.rows[0]=Broken()
        with self.assertRaisesRegex(MaxsurfCOMError,"Orientation"):self.controls({"Orientation":1})
    def test_offsets_are_written_after_axes_and_verified_in_final_state(self):
        class Element:
            Label="E"
            def __init__(self):
                self.local=False;self.offset=(0.,)*3
            @property
            def OffsetsLocal(self):return self.local
            @OffsetsLocal.setter
            def OffsetsLocal(self,v):
                self.local=v;self.offset=(99.,)*3
            @property
            def offset1(self):return self.offset
            @offset1.setter
            def offset1(self,v):self.offset=v
        obj=Element();self.frame.Elements.rows[0]=obj
        self.controls({"offset1":[1,2,3],"OffsetsLocal":True})
        self.assertEqual(obj.offset,(1.,2.,3.))
    def test_member_load_uses_exact_eight_argument_contract(self):
        coll=self.prepare_loads();out=json.loads(self.loads([self.load()]))
        coll.AddLoad.assert_called_once_with(self.e,2,3,-2.,1.,-3.,2.,False)
        self.assertEqual(out["created"],[1])
    def test_all_shapes_and_ignored_parameter_contracts(self):
        coll=self.prepare_loads()
        rows=[self.load(shape="mfLoadshapeUniform",distance1=0,value2=0,distance2=0),
              self.load(shape="mfLoadshapePoint",value2=0,distance2=0),
              self.load(shape="mfLoadshapeTriangular",value2=0,distance2=0),
              self.load(shape="mfLoadshapeTriLinear")]
        self.loads(rows);self.assertEqual(coll.Count,4)
    def test_later_invalid_member_load_prevents_entire_batch(self):
        coll=self.prepare_loads()
        for bad in (self.load(element_label="changed"),self.load(distance2=11),
                    self.load(**{"global":1}),self.load(shape="invented"),self.load(shape="mfLoadshapeUniform")):
            with self.subTest(bad=bad),self.assertRaises((MaxsurfValidationError,MaxsurfSafetyError)):
                self.loads([self.load(),bad])
        coll.AddLoad.assert_not_called()
    def test_member_load_count_guard(self):
        coll=self.prepare_loads()
        with self.assertRaises(MaxsurfSafetyError):self.loads([self.load()],1)
        coll.AddLoad.assert_not_called()
    def test_member_load_readback_mismatch(self):
        coll=self.prepare_loads()
        coll.AddLoad.side_effect=lambda *a:NS(DOF=2,Shape=3,Value1=99)
        with self.assertRaises(MaxsurfCOMError):self.loads([self.load()])
    def test_modal_only_writes_selected_toggle(self):
        settings=self.frame.Analysis.ModalSettings
        for formulation,selected in (("distributed","DistributedMass"),("lumped","LumpedMass")):
            out=json.loads(c.set_multiframe_modal_controls.__wrapped__(
                2,"mfModalSolverJacobi","mmfModalScalingUnitMass",formulation,100,.0001,self.token))
            self.assertEqual(settings.writes[-1],(selected,True))
            self.assertEqual(out["mass_formulation"],formulation)
        self.assertEqual(settings.writes,[("DistributedMass",True),("LumpedMass",True)])
    def test_modal_invalid_choice_and_tolerance(self):
        for solver,scaling,tolerance in (("bad",c.SCALINGS[0],.1),(c.SOLVERS[0],"mfModalScalingUnitMass",.1),
                                          (c.SOLVERS[0],c.SCALINGS[0],0)):
            with self.assertRaises(MaxsurfValidationError):
                c.set_multiframe_modal_controls.__wrapped__(2,solver,scaling,"distributed",10,tolerance,self.token)
        self.assertEqual(self.frame.Analysis.ModalSettings.writes,[])
    def test_unit_change_returns_new_token_and_invalidates_old(self):
        self.app.Preferences.SetUnit=Mock(side_effect=lambda n,v:self.f.units.__setitem__(n,v))
        out=json.loads(c.set_multiframe_units.__wrapped__('{"Length":"mm"}',self.token))
        self.assertNotEqual(out["unit_token"],self.token)
        with self.assertRaises(MaxsurfSafetyError):self.controls({"Orientation":1})
        self.controls({"Orientation":1},out["unit_token"])
    def test_unit_rejection_and_native_refusal(self):
        self.app.Preferences.SetUnit=Mock(return_value=None)
        with self.assertRaises(MaxsurfValidationError):
            c.set_multiframe_units.__wrapped__('{"Bogus":"mm"}',self.token)
        self.app.Preferences.SetUnit.assert_not_called()
        with self.assertRaises(MaxsurfCOMError):
            c.set_multiframe_units.__wrapped__('{"Length":"invented"}',self.token)
    def test_new_unit_categories_participate_in_snapshot(self):
        self.f.units[self.f.values["mfUnitRotationalMass"]]="changed"
        with self.assertRaises(MaxsurfSafetyError):self.controls({"Orientation":1})
    def test_requested_three_modes_returned_two_is_partial(self):
        case=self.f.results(modal=True)
        self.frame.Results.Modal.Cases=fixture.Collection([case,case])
        out=json.loads(m.run_multiframe_analysis.__wrapped__(self.token,"modal",3))
        self.assertFalse(out["coverage_complete"]);self.assertEqual(out["missing_cases"],[3])
        self.assertEqual(out["requested_modes"],3)
    def test_all_static_family_cases_are_accounted_for(self):
        case=self.f.results()
        self.frame.LoadCases.rows.extend([NS(Type=2),NS(Type=3)])
        self.frame.Results.Linear.Cases=fixture.Collection([case,NS(Solved=False),case])
        out=json.loads(m.run_multiframe_analysis.__wrapped__(self.token))
        self.assertEqual(out["requested_cases"],[1,2,3]);self.assertEqual(out["missing_cases"],[2])
        self.assertEqual(out["coverage_status"],"partial")
    def test_runner_lumped_selector_is_exclusive(self):
        self.f.results(modal=True)
        out=json.loads(m.run_multiframe_analysis.__wrapped__(self.token,"modal",1,"lumped"))
        self.assertTrue(out["coverage_complete"])
        self.assertEqual(self.frame.Analysis.ModalSettings.writes,[("LumpedMass",True)])
    def test_mass_array_native_contract(self):
        coll=self.frame.nodeMasses
        coll.AddMass=Mock(side_effect=lambda n,v:coll.add(NS(NodeIndex=n.Index,Masses=v)))
        out=json.loads(c.add_multiframe_node_masses.__wrapped__(
            '[{"node":1,"node_label":"A","masses":[1,2,3,4,5,6]}]',0,self.token))
        coll.AddMass.assert_called_once_with(self.frame.nodes.Item(1),(1.,2.,3.,4.,5.,6.))
        self.assertEqual(out["created"],[1])
    def test_invalid_mass_array_precedes_native_write(self):
        coll=self.frame.nodeMasses;coll.AddMass=Mock()
        for values in ([0]*6,[-1,0,0,0,0,0],[True]*6,[1]*5):
            with self.assertRaises(MaxsurfValidationError):
                c.add_multiframe_node_masses.__wrapped__(json.dumps([dict(node=1,node_label="A",masses=values)]),0,self.token)
        coll.AddMass.assert_not_called()
    def test_spring_contract_and_units(self):
        coll=self.frame.springs
        coll.AddSpring=Mock(side_effect=lambda n,d,s:coll.add(NS(NodeIndex=n.Index,DOF=d,Value=s,Global=False,Type=1)))
        row=dict(node=2,node_label="B",dof="mfDOFthetaz",stiffness=3,type="mfSpringTensionOnly",**{"global":True})
        out=json.loads(c.add_multiframe_springs.__wrapped__(json.dumps([row]),0,self.token))
        self.assertTrue(coll.Item(1).Global);self.assertEqual(coll.Item(1).Type,2)
        self.assertIn("TorsionSpring",out["units"])
    def test_link_native_contract_and_readback(self):
        coll=self.frame.nodeLinkGroups
        def add(label,indices,master,dof):
            return coll.add(NS(Label=label,nodes=fixture.Collection([self.frame.nodes.Item(i) for i in indices]),
                               MasterNode=master,LinkedDOF=dof))
        coll.AddGroup=Mock(side_effect=add)
        nodes='[{"node":1,"node_label":"A"},{"node":2,"node_label":"B"}]'
        dofs='[true,false,true,false,true,false]'
        out=json.loads(c.add_multiframe_node_link.__wrapped__("Link",nodes,1,"A",dofs,0,self.token))
        self.assertEqual(out["index"],1)
        coll.AddGroup.assert_called_once_with("Link",(1,2),self.frame.nodes.Item(1),(True,False,True,False,True,False))
    def test_link_identity_failure_precedes_write(self):
        coll=self.frame.nodeLinkGroups;coll.AddGroup=Mock()
        with self.assertRaises(MaxsurfSafetyError):
            c.add_multiframe_node_link.__wrapped__("Link",'[{"node":1,"node_label":"A"},{"node":2,"node_label":"wrong"}]',
                                                   1,"A",'[true,false,false,false,false,false]',0,self.token)
        coll.AddGroup.assert_not_called()
    def test_optional_objects_only_read_documented_identity(self):
        self.frame.Plates=fixture.Collection([NS(Label="P")])
        out=json.loads(c.read_multiframe_auxiliary_objects.__wrapped__("Plates"))
        self.assertEqual(out["rows"],[{"index":1,"label":"P"}])
        with self.assertRaises(MaxsurfCOMError):c.read_multiframe_auxiliary_objects.__wrapped__("Patches")
    def test_module_registration_returns_guarded_tool_names(self):
        names=[]
        class MCP:
            def tool(self,**kwargs):
                def decorate(fn):names.append(fn.__name__);return fn
                return decorate
        out=c.register_tools(MCP())
        self.assertEqual(out,[fn.__name__ for fn in c.TOOLS])
        self.assertEqual(names,out)

if __name__=="__main__":unittest.main()
