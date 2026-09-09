"""Native file wrappers tested offline; no real application is touched."""
import json, os, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from maxsurf_mcp import com, constants, motions, stability
from maxsurf_mcp.errors import MaxsurfCOMError, MaxsurfSafetyError, MaxsurfValidationError, MaxsurfAnalysisError

class NativeFilesTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        env=patch.dict(os.environ,{"MAXSURF_MCP_WORKSPACE_ROOTS":tmp.name,"MAXSURF_MCP_RUNTIME_DIR":tmp.name});env.start();self.addCleanup(env.stop)
        self.design=NS(DesignPath="",SKDataPath="",SKResultsPath="",**{k:NS(Count=0) for k in ("Speeds","Headings","Spectra","Locations","SectionMappings")})
        self.app=NS(IsInitializedCorrectly=True,Design=self.design)
        for target,value in (("connect",self.app),("session_for",Mock())):
            p=patch.object(com,target,return_value=value);p.start();self.addCleanup(p.stop)
    def source(self,suffix):
        p=self.root/("input"+suffix);p.write_bytes(b"fake");return str(p)
    def target(self,suffix):return str(self.root/("output"+suffix))
    def load_hull(self):self.design.DesignPath=self.source(".msd")
    def test_empty_open_identity_verified(self):
        self.design.DesignOpen=Mock(side_effect=lambda p:setattr(self.design,"DesignPath",p))
        out=json.loads(motions.open_motions_design.__wrapped__(self.source(".msd")))
        self.assertTrue(out["identity_verified"]);self.assertFalse(out["analysis_run"])
    def test_nonempty_open_refused(self):
        self.design.DesignPath="existing.msd";self.design.DesignOpen=Mock()
        with self.assertRaises(MaxsurfSafetyError):motions.open_motions_design.__wrapped__(self.source(".msd"))
        self.design.DesignOpen.assert_not_called()
    def test_unreadable_state_refused(self):
        del self.design.SKDataPath
        with self.assertRaises(MaxsurfSafetyError):motions.open_motions_design.__wrapped__(self.source(".msd"))
    def test_unsaved_inputs_refused(self):
        self.design.Speeds.Count=1
        with self.assertRaises(MaxsurfSafetyError):motions.open_motions_design.__wrapped__(self.source(".msd"))
    def test_explicit_open_replacement(self):
        self.load_hull();self.design.DesignOpen=Mock(side_effect=lambda p:setattr(self.design,"DesignPath",p))
        motions.open_motions_design.__wrapped__(self.source(".msd"),True)
        self.design.DesignOpen.assert_called_once()
    def test_false_open_fails(self):
        self.design.DesignOpen=Mock(return_value=False)
        with self.assertRaises(MaxsurfCOMError):motions.open_motions_design.__wrapped__(self.source(".msd"))
    def test_success_return_wrong_identity_fails(self):
        self.design.DesignOpen=Mock(return_value=True)
        with self.assertRaises(MaxsurfCOMError):motions.open_motions_design.__wrapped__(self.source(".msd"))
    def test_invalid_consent_type_fails(self):
        with self.assertRaises(MaxsurfValidationError):motions.open_motions_design.__wrapped__("anything",1)
    def test_companion_always_requires_consent(self):
        with self.assertRaises(MaxsurfSafetyError):motions.load_motions_data.__wrapped__(self.source(".skd"))
    def test_companion_requires_hull(self):
        with self.assertRaises(MaxsurfValidationError):motions.load_motions_data.__wrapped__(self.source(".skd"),True)
    def test_load_skd_and_skr_use_distinct_native_members(self):
        self.load_hull()
        for ext,method,member in ((".skd","SKDataOpen","SKDataPath"),(".skr","SKResultsOpen","SKResultsPath")):
            with self.subTest(extension=ext):
                setattr(self.design,method,Mock(side_effect=lambda p,m=member:setattr(self.design,m,p)))
                out=json.loads(motions.load_motions_data.__wrapped__(self.source(ext),True))
                self.assertTrue(out["identity_verified"]);self.assertEqual(out["result_freshness"],"not_verified")
    def test_load_false_fails(self):
        self.load_hull();self.design.SKDataOpen=Mock(return_value=False)
        with self.assertRaises(MaxsurfCOMError):motions.load_motions_data.__wrapped__(self.source(".skd"),True)
    def saver(self,method,member):
        def save(p):Path(p).write_bytes(b"native mock data");setattr(self.design,member,p)
        setattr(self.design,method,Mock(side_effect=save))
    def test_save_settings_checks_file_and_identity(self):
        self.load_hull();self.saver("SKDataSaveAs","SKDataPath")
        out=json.loads(motions.save_motions_state.__wrapped__(self.target(".skd")))
        self.assertTrue(out["file_verified"]);self.assertFalse(out["reopen_verified"])
    def test_save_missing_file_fails(self):
        self.load_hull();self.design.SKDataSaveAs=Mock(return_value=True)
        with self.assertRaises(MaxsurfCOMError):motions.save_motions_state.__wrapped__(self.target(".skd"))
    def test_save_no_overwrite(self):
        existing=self.source(".skd")
        with self.assertRaises(MaxsurfSafetyError):motions.save_motions_state.__wrapped__(existing)
        self.assertEqual(Path(existing).read_bytes(),b"fake")
    def test_unverified_binary_result_save_refused_before_com(self):
        with patch.object(com,"connect") as connect, self.assertRaises(MaxsurfSafetyError):
            motions.save_motions_state.__wrapped__(self.target(".skr"))
        connect.assert_not_called()
    def image(self,behavior="valid"):
        def save(view,p,w,h):
            if behavior=="false":return False
            if behavior=="none":return None
            if behavior=="invalid":Path(p).write_bytes(b"not png");return True
            from PIL import Image
            Image.new("RGB",(w,h) if behavior=="valid" else (1,1)).save(p)
        self.design.Path="loaded.msd"
        self.design.SaveImage=Mock(side_effect=save)
        p=patch.object(constants,"discover",return_value={"constants":{"hmViewPerspective":0}});p.start();self.addCleanup(p.stop)
    def test_stability_image_native_png_verified(self):
        self.image();out=json.loads(stability.export_stability_image.__wrapped__(self.target(".png")))
        self.assertTrue(out["image_verified"]);self.design.SaveImage.assert_called_once_with(0,self.target(".png"),1200,800)
    def test_stability_image_reports_actual_native_client_size(self):
        self.image()
        def save(view,p,w,h):
            from PIL import Image
            Image.new("RGB",(1148,734)).save(p)
        self.design.SaveImage.side_effect=save
        out=json.loads(stability.export_stability_image.__wrapped__(self.target(".png")))
        self.assertEqual((out["width"],out["height"]),(1148,734))
        self.assertEqual((out["requested_width"],out["requested_height"]),(1200,800))
        self.assertFalse(out["dimensions_honored"])
        self.assertTrue(out["image_verified"])
    def test_stability_image_validates_dimensions_before_com(self):
        for size in (0,63,4097,True,12.5):
            with self.subTest(size=size),self.assertRaises(MaxsurfValidationError):stability.export_stability_image.__wrapped__(self.target(".png"),width=size)
    def test_stability_image_unknown_view_refused(self):
        self.image()
        with self.assertRaises(MaxsurfValidationError):stability.export_stability_image.__wrapped__(self.target(".png"),"anything")
        self.design.SaveImage.assert_not_called()
    def test_stability_image_invalid_output_fails(self):
        for behavior in ("false","none","invalid","wrong_size"):
            with self.subTest(behavior=behavior):
                self.image(behavior)
                with self.assertRaises(MaxsurfCOMError):stability.export_stability_image.__wrapped__(str(self.root/(behavior+".png")))
    def test_stability_image_no_overwrite(self):
        self.image()
        with self.assertRaises(MaxsurfSafetyError):stability.export_stability_image.__wrapped__(self.source(".png"))
        self.design.SaveImage.assert_not_called()
    def test_unsupported_file_types_refused(self):
        with self.assertRaises(MaxsurfSafetyError):motions.save_motions_state.__wrapped__(self.target(".exe"))
        with self.assertRaises(MaxsurfSafetyError):stability.export_stability_image.__wrapped__(self.target(".jpg"))

class HeadingUnitTests(unittest.TestCase):
    def test_configuration_converts_degrees_before_native_add(self):
        import math
        coll=NS(Count=0)
        item=NS(Heading=0,Analyse=False)
        def add(name,value): item.Heading=value;coll.Count+=1
        coll.Add=Mock(side_effect=add);coll.Item=lambda i:item
        speed=NS(Count=0)
        speeditem=NS(Speed=0,Analyse=False)
        def adds(name,value):speeditem.Speed=value;speed.Count+=1
        speed.Add=adds;speed.Item=lambda i:speeditem
        specitem=NS(Name="",Type=0,CharacteristicWaveHeight=0,ModalPeriod=0,Analyse=False)
        specs=NS(Count=0);specs.Add=lambda:setattr(specs,"Count",specs.Count+1);specs.Item=lambda i:specitem
        vessel=NS(**{name:0 for name in ("VesselType","DraftAft","DraftFwd","VCG","GyradiusRoll","GyradiusPitch","GyradiusYaw","RollDampingNonDim")})
        d=NS(AnalysisType=0,Vessel=vessel,AnalysisOptions=NS(WaterDensity=0),Headings=coll,Speeds=speed,Spectra=specs)
        with patch.object(com,"connect"),patch.object(motions,"_require_ready",return_value=d),patch.object(stability,"_constant",return_value=1):
            motions.configure_motions_analysis.__wrapped__("[180]","[0]",'[ {"type":"skSpectrumJONSWAP","hs_m":4,"tp_s":10} ]',1025,2.5,2.5,2,11,11,.075,"skATStripTheory","skVTMonohull")
        self.assertAlmostEqual(coll.Add.call_args.args[1],math.pi)
    def test_results_use_tested_axes_and_return_degrees(self):
        import math
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{"MAXSURF_MCP_RUNTIME_DIR":tmp}):
            speed=NS(Count=3,NumberTested=1,TestedItem=Mock(return_value=NS(Speed=2)))
            heading=NS(Count=4,NumberTested=1,TestedItem=Mock(return_value=NS(Heading=math.pi)))
            spectrum=NS(Count=2,NumberTested=1,TestedItem=Mock(return_value=NS(Name="tested")))
            stats=NS(Item=Mock(return_value=NS(_prop_map_get_={"Heave":None},Heave=.2)))
            d=NS(Speeds=speed,Headings=heading,Spectra=spectrum,GlobalStatistics=stats)
            with patch.object(com,"connect"),patch.object(motions,"_require_ready",return_value=d),patch.object(motions,"export_motions_table",NS(__wrapped__=Mock(return_value='{"file_verified":true}'))):
                out=json.loads(motions.get_motions_results.__wrapped__())
            self.assertEqual(out["combinations"],1);self.assertEqual(out["results"][0]["heading_deg"],180)
            self.assertEqual(out["results"][0]["spectrum_name"],"tested");stats.Item.assert_called_once_with(1,1,1)

class CleanupTests(unittest.TestCase):
    def test_capture_release_order(self):
        from maxsurf_mcp import display
        order=[]
        gui=NS(DeleteObject=lambda h:order.append("bitmap"),ReleaseDC=lambda a,b:order.append("release"))
        dest=NS(DeleteDC=lambda:order.append("dc"));bmp=NS(GetHandle=lambda:1)
        user32=NS(SetThreadDpiAwarenessContext=lambda c:order.append("dpi"))
        display._release_capture(gui,1,2,dest,bmp,user32,3)
        self.assertEqual(order,["dc","bitmap","release","dpi"])
    def test_capture_failure_still_restores_dpi(self):
        from maxsurf_mcp import display
        user32=NS(SetThreadDpiAwarenessContext=Mock())
        gui=NS(DeleteObject=Mock(side_effect=RuntimeError("failed")),ReleaseDC=Mock())
        with self.assertRaises(MaxsurfSafetyError):display._release_capture(gui,1,2,None,NS(GetHandle=lambda:3),user32,4)
        gui.ReleaseDC.assert_called_once();user32.SetThreadDpiAwarenessContext.assert_called_once_with(4)
    def test_blocked_shutdown_does_not_queue_or_release_elsewhere(self):
        import server
        manager=Mock();worker=Mock();worker.is_running.return_value=True
        worker.describe.return_value={"abandoned_operations":["save"]}
        with patch.object(server.session,"get_manager",return_value=manager),patch.object(server.comthread,"peek_worker",return_value=worker),patch.object(server.comthread,"shutdown",return_value={"joined":False}):
            self.assertFalse(server.shutdown()["joined"])
        manager.reset.assert_not_called();worker.run.assert_not_called()

class SummaryEvidenceTests(unittest.TestCase):
    def test_metadata_numbers_are_not_response_evidence(self):
        with self.assertRaises(MaxsurfAnalysisError):motions._require_numeric_table(b"1\tWave heading\t180\tdeg",summary=True)
    def test_legitimate_zero_roll_does_not_fail(self):
        data="\n".join("1\t"+name+"\t0\tm^2\t0\tm\t0\tm" for name in ("Heave motion","Roll motion","Pitch motion"))
        motions._require_numeric_table(data.encode(),summary=True)
    def test_nonfinite_response_is_refused(self):
        data="\n".join("1\t"+name+"\tnan\tm^2\t0\tm\t0\tm" for name in ("Heave motion","Roll motion","Pitch motion"))
        with self.assertRaises(MaxsurfAnalysisError):motions._require_numeric_table(data.encode(),summary=True)
    def test_incomplete_response_is_refused(self):
        with self.assertRaises(MaxsurfAnalysisError):motions._require_numeric_table(b"1\tHeave motion\t1\tm^2\t1\tm\t2\tm",summary=True)
