"""Release boundary tests, using fakes only. No application is opened."""
import inspect,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from maxsurf_mcp import com,display,modeler,motions
from maxsurf_mcp.errors import MaxsurfAnalysisError,MaxsurfSafetyError,MaxsurfValidationError

class DisplayTests(unittest.TestCase):
    def test_disabled_module_refused_before_native_ui(self):
        with patch.object(display.config,"allowed_modules",return_value=("motions",)), self.assertRaises(MaxsurfSafetyError):
            display._window()
    def button(self, checked=False, enabled=True, refused=False):
        state=[display.CHECKED if checked else 0]
        button=NS(is_enabled=lambda:enabled,legacy_properties=lambda:{"State":state[0]})
        button.invoke=Mock(side_effect=lambda: None if refused else state.__setitem__(0,state[0]^display.CHECKED))
        return button
    def test_enable_reads_back(self):
        b=self.button(); r=display._set_checked(b,True)
        self.assertTrue(r["after"]); b.invoke.assert_called_once()
    def test_idempotent_enable_never_toggles_off(self):
        b=self.button(True); self.assertFalse(display._set_checked(b,True)["changed"])
        b.invoke.assert_not_called()
    def test_disable_is_verified(self):
        self.assertFalse(display._set_checked(self.button(True),False)["after"])
    def test_refused_toggle_fails(self):
        with self.assertRaises(MaxsurfSafetyError): display._set_checked(self.button(refused=True),True)
    def test_disabled_control_is_not_invoked(self):
        b=self.button(enabled=False)
        with self.assertRaises(MaxsurfSafetyError): display._set_checked(b,True)
        b.invoke.assert_not_called()
    def test_mixed_or_unreadable_state_is_not_guessed(self):
        for state in (None,True,"16",display.MIXED):
            with self.subTest(state=state), self.assertRaises(MaxsurfSafetyError):
                display._checked(NS(legacy_properties=lambda:{"State":state}))
    def test_arbitrary_control_is_refused(self):
        with self.assertRaises(MaxsurfSafetyError): display._button(None,"File","Open")
    def test_title_mismatch_is_refused(self):
        gui=NS(GetWindowText=lambda h:"different",IsWindowEnabled=lambda h:True)
        with self.assertRaises(MaxsurfSafetyError): display._expected(gui,1,"expected")
    def test_modal_is_refused(self):
        gui=NS(GetWindowText=lambda h:"expected",IsWindowEnabled=lambda h:False)
        with self.assertRaises(MaxsurfSafetyError): display._expected(gui,1,"expected")
    def test_ambiguous_control_is_refused(self):
        node=NS(element_info=NS(name="Rendering"),is_visible=lambda:True)
        with self.assertRaises(MaxsurfSafetyError): display._unique([node,node],"Rendering")
    def test_screenshot_bad_path_precedes_ui(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ",{"MAXSURF_MCP_WORKSPACE_ROOTS":tmp}),patch.object(display,"_run") as run:
            with self.assertRaises(MaxsurfSafetyError): display.capture_modeler_view.__wrapped__(str(Path(tmp)/"a.exe"),"Title")
            run.assert_not_called()
    def test_screenshot_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ",{"MAXSURF_MCP_WORKSPACE_ROOTS":tmp}),patch.object(display,"_run") as run:
            target=Path(tmp)/"a.png"; target.write_bytes(b"old")
            with self.assertRaises(MaxsurfSafetyError): display.capture_modeler_view.__wrapped__(str(target),"Title")
            self.assertEqual(target.read_bytes(),b"old");run.assert_not_called()

class OpenConsentTests(unittest.TestCase):
    def test_default_requests_save(self):
        self.assertIs(inspect.signature(modeler.open_design).parameters["save_current"].default,True)
    def test_discard_needs_separate_consent_before_com(self):
        with patch.object(com,"connect") as connect,self.assertRaises(MaxsurfSafetyError):
            modeler.open_design.__wrapped__("anything.msd",save_current=False)
        connect.assert_not_called()

class MotionsAcceptanceTests(unittest.TestCase):
    def design(self,available=True,active=True):
        coll=NS(Count=1,NumberTested=1,Item=lambda i:NS(Analyse=active,Speed=1,Heading=0))
        return NS(AnalysisType=1,AnalysisTypeIsAvailable=Mock(return_value=available),
                  Headings=coll,Speeds=coll,Spectra=coll,CalculateGeometry=Mock(),
                  CalculateMesh=Mock(),CalculateSeakeeping=Mock())
    def run_design(self,d):
        with patch.object(com,"connect"),patch.object(motions,"_require_ready",return_value=d):
            return motions.run_motions_analysis.__wrapped__()
    def test_unavailable_method_is_refused_before_solver(self):
        d=self.design(False)
        with self.assertRaises(MaxsurfAnalysisError): self.run_design(d)
        d.CalculateGeometry.assert_not_called()
    def test_unknown_availability_is_refused(self):
        d=self.design();d.AnalysisTypeIsAvailable.side_effect=RuntimeError("unreadable")
        with self.assertRaises(MaxsurfAnalysisError): self.run_design(d)
        d.CalculateGeometry.assert_not_called()
    def test_unselected_inputs_are_refused(self):
        d=self.design(active=False)
        with self.assertRaises(MaxsurfValidationError): self.run_design(d)
        d.CalculateGeometry.assert_not_called()
    def test_excessive_input_count_is_refused(self):
        d=self.design();d.Headings.Count=201
        with self.assertRaises(MaxsurfValidationError): self.run_design(d)
        d.CalculateGeometry.assert_not_called()
    def test_numeric_placeholders_cannot_bypass_native_export(self):
        d=self.design();d.GlobalStatistics=NS(Item=Mock())
        export=Mock(side_effect=MaxsurfAnalysisError("empty native Summary"))
        with patch.object(com,"connect"),patch.object(motions,"_require_ready",return_value=d),patch.object(motions,"export_motions_table",NS(__wrapped__=export)):
            with self.assertRaises(MaxsurfAnalysisError): motions.get_motions_results.__wrapped__()
        d.GlobalStatistics.Item.assert_not_called()
    def test_empty_export_still_fails_completed_solver_calls(self):
        d=self.design()
        with patch.object(motions,"get_motions_results",NS(__wrapped__=Mock(side_effect=MaxsurfAnalysisError("empty native table")))):
            with self.assertRaises(MaxsurfAnalysisError):self.run_design(d)
        d.CalculateSeakeeping.assert_called_once()
    def test_strip_runner_does_not_request_panel_mesh(self):
        d=self.design()
        with patch.object(motions,"get_motions_results",NS(__wrapped__=Mock(return_value='{"native_summary":{}}'))):
            self.run_design(d)
        d.CalculateGeometry.assert_called_once();d.CalculateMesh.assert_not_called();d.CalculateSeakeeping.assert_called_once()
    def test_panel_runner_requests_mesh(self):
        d=self.design();d.AnalysisType=2
        with patch.object(motions,"get_motions_results",NS(__wrapped__=Mock(return_value='{"native_summary":{}}'))):
            self.run_design(d)
        d.CalculateMesh.assert_called_once()
    def test_external_workflow_refused_before_solver(self):
        d=self.design();d.AnalysisType=4
        with self.assertRaises(MaxsurfValidationError):self.run_design(d)
        d.CalculateGeometry.assert_not_called()
    def test_invalid_limit_is_rejected_before_com(self):
        for value in (0,True,201,1.5):
            with patch.object(com,"connect") as connect,self.assertRaises(MaxsurfValidationError):motions.get_motions_results.__wrapped__(value)
            connect.assert_not_called()
