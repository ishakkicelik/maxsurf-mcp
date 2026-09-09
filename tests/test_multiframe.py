"""Offline Multiframe contracts; all COM, files and units use isolated fakes."""
import json, os, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from maxsurf_mcp import multiframe as m, com, constants, session, config
from maxsurf_mcp.errors import MaxsurfValidationError, MaxsurfSafetyError, MaxsurfCOMError, MaxsurfAnalysisError

class Collection:
    def __init__(self, rows=()):self.rows=list(rows)
    @property
    def Count(self):return len(self.rows)
    def Item(self,i):return self.rows[i-1]
    def add(self,obj):
        obj.Index=len(self.rows)+1;self.rows.append(obj);return obj

class MultiframeTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        p=patch.dict(os.environ,{"MAXSURF_MCP_WORKSPACE_ROOTS":tmp.name,"MAXSURF_MCP_RUNTIME_DIR":tmp.name});p.start();self.addCleanup(p.stop)
        self.values={"mfUnit"+n:i for i,n in enumerate(m.UNIT_NAMES,1)}
        self.values.update({n:i for i,n in enumerate(m.DOFS,1)});self.values.update(mfRestraintFixed=2,mflcStatic=1)
        self.units={v:"u"+str(v) for k,v in self.values.items() if k.startswith("mfUnit")}
        self.default=NS(Name="Default",Label="Default",NodeLoads=Collection(),ElementLoads=Collection(),PrescribedDisps=Collection(),thermalLoads=Collection(),Type=1)
        self.frame=NS(FullName="",Name="",Modified=False,**{n:Collection() for n in m.COUNTS})
        self.frame.LoadCases=Collection([self.default]);self.frame.Analysis=NS(**{n:False for n in m.FLAGS},ModalSettings=NS(DistributedMass=False,LumpedMass=True,Modes=1))
        self.frame.New=Mock(return_value=True);self.frame.Open=Mock(return_value=False);self.frame.SaveAs=Mock(return_value=False)
        self.app=NS(Frame=self.frame,IsInitializedCorrectly=True,Version="test",Preferences=NS(GetUnit=lambda i:self.units[i],TensionPositive=False))
        for target,value in (("connect",self.app),("session_for",Mock())):
            p=patch.object(com,target,return_value=value);p.start();self.addCleanup(p.stop)
        p=patch.object(constants,"discover",return_value={"constants":self.values});p.start();self.addCleanup(p.stop)
        self.token=m._unit_state(self.app)["unit_token"]
        def add(x,y,z):return self.frame.nodes.add(NS(x=x,y=y,z=z,Label="",Pinned=False))
        self.frame.nodes.AddNode=Mock(side_effect=add)
    def nodes(self):
        return m.add_multiframe_nodes.__wrapped__(json.dumps([{"label":"A","x":0,"y":0,"z":0},{"label":"B","x":10,"y":0,"z":0}]),0,self.token)
    def test_unit_state_is_explicit(self):self.assertEqual(m._unit_state(self.app)["units"]["Length"],"u1")
    def test_units_change_invalidates_token(self):
        self.units[1]="changed"
        with self.assertRaises(MaxsurfSafetyError):self.nodes()
        self.frame.nodes.AddNode.assert_not_called()
    def test_missing_units_fail_closed(self):
        self.units[1]=""
        with self.assertRaises(MaxsurfCOMError):m._unit_state(self.app)
    def test_default_allowlist_and_identity(self):
        self.assertIn("multiframe",config.DEFAULT_ALLOWED_MODULES)
        self.assertEqual(session.MODULES["multiframe"].design_object_member,"Frame")
        self.assertEqual(session.MODULES["modeler"].design_object_member,"Design")
    def test_status_does_not_hide_unavailable_optional_objects(self):
        p=json.loads(m.get_multiframe_status.__wrapped__());self.assertFalse(p["optional_objects"]["Plates"]["available"])
    def test_uninitialized_refused(self):
        self.app.IsInitializedCorrectly=False
        with self.assertRaises(MaxsurfCOMError):m.get_multiframe_status.__wrapped__()
    def test_add_nodes_and_readback(self):
        p=json.loads(self.nodes());self.assertEqual([r["index"] for r in p["created"]],[1,2]);self.assertEqual(self.frame.nodes.Item(2).x,10)
    def test_invalid_batch_precedes_writes(self):
        for rows in ([],[{"label":"A","x":0,"y":0,"z":0},{"label":"B","x":"bad","y":0,"z":0}], [{"label":"A","x":0,"y":0,"z":0,"extra":1}]):
            with self.subTest(rows=rows),self.assertRaises(MaxsurfValidationError):m.add_multiframe_nodes.__wrapped__(json.dumps(rows),0,self.token)
        self.frame.nodes.AddNode.assert_not_called()
    def test_nan_and_boolean_refused(self):
        for v in (float("nan"),float("inf"),True):
            with self.subTest(v=v),self.assertRaises(MaxsurfValidationError):m.add_multiframe_nodes.__wrapped__(json.dumps([{"label":"A","x":v,"y":0,"z":0}]),0,self.token)
    def test_duplicate_labels_refused(self):
        rows=[{"label":"A","x":0,"y":0,"z":0}]*2
        with self.assertRaises(MaxsurfValidationError):m.add_multiframe_nodes.__wrapped__(json.dumps(rows),0,self.token)
        self.frame.nodes.AddNode.assert_not_called()
    def test_count_guard(self):
        with self.assertRaises(MaxsurfSafetyError):m.add_multiframe_nodes.__wrapped__('[{"label":"A","x":0,"y":0,"z":0}]',1,self.token)
    def test_boolean_count_is_not_index(self):
        with self.assertRaises(MaxsurfValidationError):m._integer(True,"index")
    def test_node_label_guard(self):
        self.nodes()
        with self.assertRaises(MaxsurfSafetyError):m._node(self.frame,1,"wrong")
    def test_partial_node_failure_not_reported_success(self):
        self.frame.nodes.AddNode.side_effect=[NS(Index=1,x=0,y=0,z=0,Label=""),RuntimeError("native")]
        with self.assertRaises(RuntimeError):self.nodes()
    def test_new_refuses_existing_modified_work(self):
        self.frame.Modified=True
        with self.assertRaises(MaxsurfSafetyError):m.new_multiframe_model.__wrapped__(True)
        self.frame.New.assert_not_called()
    def test_new_refuses_existing_without_consent(self):
        self.frame.FullName="existing.mfd"
        with self.assertRaises(MaxsurfSafetyError):m.new_multiframe_model.__wrapped__()
    def test_new_empty(self):
        self.assertTrue(json.loads(m.new_multiframe_model.__wrapped__())["new"])
        self.frame.New.assert_called_once_with(False)
    def test_new_false_native_result_refused(self):
        self.frame.New.return_value=False
        with self.assertRaises(MaxsurfCOMError):m.new_multiframe_model.__wrapped__()
    def test_new_requires_real_boolean(self):
        with self.assertRaises(MaxsurfValidationError):m.new_multiframe_model.__wrapped__("yes")
    def test_open_missing_file_never_calls_native(self):
        with self.assertRaises(MaxsurfValidationError):m.open_multiframe_model.__wrapped__(str(self.root/"missing.mfd"))
        self.frame.Open.assert_not_called()
    def test_open_saved_model_identity(self):
        p=self.root/"in.mfd";p.write_bytes(b"test")
        def load(path,save):self.frame.FullName=path;return True
        self.frame.Open.side_effect=load
        self.assertTrue(json.loads(m.open_multiframe_model.__wrapped__(str(p)))["identity_verified"])
    def test_open_wrong_identity_fails(self):
        p=self.root/"in.mfd";p.write_bytes(b"test");self.frame.Open.return_value=True
        with self.assertRaises(MaxsurfCOMError):m.open_multiframe_model.__wrapped__(str(p))
    def test_save_no_overwrite(self):
        p=self.root/"old.mfd";p.write_bytes(b"old")
        with self.assertRaises(MaxsurfSafetyError):m.save_multiframe_model.__wrapped__(str(p))
        self.frame.SaveAs.assert_not_called();self.assertEqual(p.read_bytes(),b"old")
    def test_save_verifies_file_and_identity(self):
        p=self.root/"new.mfd"
        def save(path,replace):Path(path).write_bytes(b"saved");self.frame.FullName=path;return True
        self.frame.SaveAs.side_effect=save
        out=json.loads(m.save_multiframe_model.__wrapped__(str(p)));self.assertEqual(out["bytes"],5);self.assertFalse(out["reopen_verified"])
    def test_save_true_without_file_fails(self):
        self.frame.SaveAs.return_value=True
        with self.assertRaises(MaxsurfCOMError):m.save_multiframe_model.__wrapped__(str(self.root/"new.mfd"))
    def test_save_disallowed_suffix(self):
        with self.assertRaises(MaxsurfSafetyError):m.save_multiframe_model.__wrapped__(str(self.root/"bad.exe"))
    def test_missing_enum_refused(self):
        with self.assertRaises(MaxsurfValidationError):m._enum(self.app,"madeUp")
    def test_runner_unsupported_precedes_connection(self):
        com.connect.reset_mock()
        with self.assertRaises(MaxsurfValidationError):m.run_multiframe_analysis.__wrapped__(self.token,"time_history")
        com.connect.assert_not_called()
    def test_runner_empty_model_refused(self):
        with self.assertRaises(MaxsurfValidationError):m.run_multiframe_analysis.__wrapped__(self.token)
    def test_unsolved_results_refused(self):
        self.frame.Results=NS(Linear=NS(Cases=Collection([NS(Solved=False)])))
        with self.assertRaises(MaxsurfAnalysisError):m.get_multiframe_results.__wrapped__()
    def test_result_nonfinite_refused(self):
        with self.assertRaises(MaxsurfValidationError):m._fields(NS(dx=float("nan")),("dx",))
    def test_saved_frame_root_identity(self):
        spec=session.MODULES["multiframe"]
        # Exercise the same method without instantiating a native session.
        holder=NS(spec=spec,app=NS(Frame=NS(FullName="C:/fixture.mfd",Name="fixture")),module="multiframe")
        result=session.MaxsurfSession.read_design_identity(holder)
        self.assertEqual(result["path"],"C:/fixture.mfd")

    def structure(self):
        self.nodes()
        section=NS(Name="S",Area=2.,Ix=3.,Iy=4.,Mass=5.,E=6.,G=7.)
        self.app.SectionsLibrary=NS(GetSection=Mock(return_value=section))
        def add(a,b):
            e=NS(Label="",nodes=Collection([a,b]),section=section,UseDynamicSelfWeight=False,Length=10.)
            e.SetSection=Mock(return_value=None)
            return self.frame.Elements.add(e)
        self.frame.Elements.AddElement=Mock(side_effect=add)
        request=[dict(label="E",node1=1,node1_label="A",node2=2,node2_label="B",section_group="G",section="S")]
        return request
    def built(self):
        request=self.structure()
        m.add_multiframe_elements.__wrapped__(json.dumps(request),0,self.token)
        def support(n,t):return self.frame.restraints.add(NS(NodeIndex=n.Index,Type=t,Global=False,Label=""))
        self.frame.restraints.AddRestraint=Mock(side_effect=support)
        m.add_multiframe_restraints.__wrapped__('[{"node":1,"node_label":"A","type":"mfRestraintFixed"}]',0,self.token)
    def results(self,modal=False):
        node=NS(Solved=True,NodeIndex=1,**{k:float(i) for i,k in enumerate(m.NODE_VALUES[:6] if modal else m.NODE_VALUES)})
        element=NS(Solved=True,ElementIndex=1)
        for name in ("Px","Vy","Vz","Tx","My","Mz"):setattr(element,name,Mock(side_effect=lambda end:float(end)))
        case=NS(Solved=True,Name="case",NodeResults=Collection([node]),ElementResults=Collection([element]))
        family=NS(Cases=Collection([case]),Frequency=lambda i:2.,Period=lambda i:.5)
        self.frame.Results=NS(Linear=family,Modal=family)
        self.frame.Analysis.ModalSettings.Scaling=1
        return case
    def test_void_native_completion_needs_readback(self):
        self.frame.New.return_value=None
        self.assertTrue(json.loads(m.new_multiframe_model.__wrapped__())["new"])
    def test_native_unknown_return_refused(self):
        for v in (False,0,1,"yes"):
            with self.subTest(v=v),self.assertRaises(MaxsurfCOMError):m._native_completed(v,"test")
    def test_void_open_wrong_identity_still_refused(self):
        p=self.root/"in.mfd";p.write_bytes(b"test");self.frame.Open.return_value=None
        with self.assertRaises(MaxsurfCOMError):m.open_multiframe_model.__wrapped__(str(p))
    def test_beam_creation_uses_supported_node_list(self):
        request=self.structure();out=json.loads(m.add_multiframe_elements.__wrapped__(json.dumps(request),0,self.token))
        self.assertEqual(out["created"][0]["label"],"E")
        self.assertFalse(out["created"][0]["material"]["available"])
        self.assertEqual(m._beam_nodes(self.frame.Elements.Item(1)),[1,2])
    def test_invalid_later_beam_prevents_whole_batch(self):
        request=self.structure();request.append(dict(request[0],label="F",node2_label="changed"))
        with self.assertRaises(MaxsurfSafetyError):m.add_multiframe_elements.__wrapped__(json.dumps(request),0,self.token)
        self.frame.Elements.AddElement.assert_not_called()
    def test_wrong_section_prevents_creation(self):
        request=self.structure();self.app.SectionsLibrary.GetSection.return_value=NS(Name="wrong")
        with self.assertRaises(MaxsurfValidationError):m.add_multiframe_elements.__wrapped__(json.dumps(request),0,self.token)
        self.frame.Elements.AddElement.assert_not_called()
    def test_support_creation_and_duplicate_refusal(self):
        self.built();self.assertTrue(self.frame.restraints.Item(1).Global)
        with self.assertRaises(MaxsurfValidationError):m.add_multiframe_restraints.__wrapped__('[{"node":1,"node_label":"A","type":"mfRestraintFixed"}]',1,self.token)
    def test_load_creation_readback(self):
        self.built()
        def addcase(t,name):
            lc=NS(Name=name,Type=t,NodeLoads=Collection())
            lc.NodeLoads.AddLoad=lambda n,d,v,g:lc.NodeLoads.add(NS(Node=n,DOF=d,Value=v,Global=g))
            return self.frame.LoadCases.add(lc)
        self.frame.LoadCases.AddCase=Mock(side_effect=addcase)
        out=json.loads(m.add_multiframe_loadcase.__wrapped__("Test",'[{"node":2,"node_label":"B","dof":"mfDOFx","value":1}]',1,self.token))
        self.assertEqual(out["index"],2);self.assertEqual(out["loads"],1)
    def test_linear_void_runner_requires_solved_cases(self):
        self.built();self.results();self.frame.Analysis.Analyse=Mock(return_value=None)
        out=json.loads(m.run_multiframe_analysis.__wrapped__(self.token))
        self.assertIsNone(out["native_return"]);self.assertEqual(out["solved_cases"],[1])
        self.assertFalse(self.frame.Analysis.TimeHistory)
    def test_solver_without_solved_results_refused(self):
        self.built();case=self.results();case.Solved=False;self.frame.Analysis.Analyse=Mock(return_value=None)
        with self.assertRaises(MaxsurfAnalysisError):m.run_multiframe_analysis.__wrapped__(self.token)
    def test_native_analysis_exception_names_stage(self):
        self.built();self.frame.Analysis.Analyse=Mock(side_effect=RuntimeError("native"))
        with self.assertRaisesRegex(MaxsurfCOMError,"Analysis.Analyse"):m.run_multiframe_analysis.__wrapped__(self.token)
    def test_linear_results_use_indexed_end_getters(self):
        self.results();out=json.loads(m.get_multiframe_results.__wrapped__())
        row=out["rows"]["ElementResults"][0];self.assertEqual(row["Px1"],1);self.assertEqual(row["Px2"],2)
    def test_modal_results_never_read_static_reactions(self):
        self.results(modal=True);out=json.loads(m.get_multiframe_results.__wrapped__("modal"))
        self.assertNotIn("rx",out["rows"]["NodeResults"][0]);self.assertNotIn("ElementResults",out["rows"])
        self.assertEqual(out["modal"]["frequency_native"],2)
    def test_modal_only_sets_selected_mass_formulation(self):
        self.built();self.results(modal=True)
        class Settings:
            Modes=1;Scaling=1
            def __init__(self):self.distributed=False
            @property
            def DistributedMass(self):return self.distributed
            @DistributedMass.setter
            def DistributedMass(self,v):self.distributed=v
            @property
            def LumpedMass(self):return not self.distributed
            @LumpedMass.setter
            def LumpedMass(self,v):raise AssertionError("Do not set the opposite toggle")
        self.frame.Analysis.ModalSettings=Settings();self.frame.Analysis.Analyse=Mock(return_value=None)
        out=json.loads(m.run_multiframe_analysis.__wrapped__(self.token,"modal",1))
        self.assertEqual(out["solved_cases"],[1]);self.assertTrue(self.frame.Analysis.Modal)
    def test_result_export_exclusive(self):
        self.results();p=self.root/"results.json"
        out=json.loads(m.export_multiframe_results.__wrapped__(str(p)))
        self.assertTrue(out["page_only"]);self.assertGreater(p.stat().st_size,0)
        with self.assertRaises(MaxsurfSafetyError):m.export_multiframe_results.__wrapped__(str(p))
    def test_native_field_error_reports_name(self):
        with self.assertRaisesRegex(MaxsurfCOMError,"dx"):m._fields(NS(),("dx",))
