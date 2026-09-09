"""Release regressions use fake COM only; no engineering acceptance claims."""
import inspect
import json
import math
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import patch

import server
from maxsurf_mcp import com, config, motions, resistance, stability, transfer
from maxsurf_mcp import validation as val
from maxsurf_mcp.errors import MaxsurfAnalysisError, MaxsurfCOMError, MaxsurfSafetyError, MaxsurfValidationError


class InputValidationTests(unittest.TestCase):
    def test_nonfinite_numbers_and_booleans_are_rejected(self):
        for value in (math.nan, math.inf, -math.inf, True, False, None, "NaN"):
            with self.subTest(value=value), self.assertRaises(MaxsurfValidationError):
                val.number(value, "x")

    def test_finite_number_and_bounds(self):
        self.assertEqual(val.number("2.5", "x", positive=True), 2.5)
        for value in (-1, 0):
            with self.assertRaises(MaxsurfValidationError): val.number(value, "x", positive=True)

    def test_json_rejects_nonfinite_tokens(self):
        for raw in ('[NaN]', '[Infinity]', '[-Infinity]', '{}'):
            with self.assertRaises(MaxsurfValidationError): val.parsed(raw, list, "x")

    def test_json_serializer_never_emits_nan(self):
        with self.assertRaises(ValueError): com.to_json({"x": math.nan})

    def test_refused_property_storage_raises(self):
        class Refuses:
            @property
            def Value(self): return 1
            @Value.setter
            def Value(self, value): pass
        with self.assertRaises(MaxsurfCOMError): val.stored(Refuses(), "Value", 2)

    def test_native_boolean_result_is_readable(self):
        self.assertEqual(stability._scalar(NS(Converged=True), "Converged")["value"], 1)

    def test_native_nonfinite_result_is_not_a_number(self):
        self.assertIsNone(stability._scalar(NS(GZ=math.nan), "GZ")["value"])

    def test_schedule_rejects_reversal_zero_step_and_nan(self):
        base = dict(start=0,first_end=10,first_step=5,second_end=20,second_step=5,third_end=30,third_step=5)
        for update in ({"third_end": 5}, {"first_step": 0}, {"start": math.nan}):
            with self.assertRaises(MaxsurfValidationError): stability._validated_schedule(base | update)

    def test_runtime_override_is_not_checkout_dependent(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'MAXSURF_MCP_RUNTIME_DIR': tmp}):
            self.assertEqual(config.runtime_root(), Path(tmp).resolve())


class PreMutationTests(unittest.TestCase):
    def test_transfer_requires_consent_before_connect(self):
        with patch.object(com, 'connect') as connect, self.assertRaises(MaxsurfSafetyError):
            transfer.transfer('stability', confirm_replace=False)
        connect.assert_not_called()

    def test_room_validation_precedes_destructive_clear(self):
        row = dict(name='A', kind='compartment', aft=0,fwd=2,port=-1,stbd=1,bottom=0,top=2,permeability=0.9)
        for update in ({'fwd': -1}, {'permeability': 3}, {'bottom': math.nan}):
            with patch.object(com, 'connect') as connect, self.assertRaises(MaxsurfValidationError):
                stability.define_subdivision.__wrapped__(json.dumps([row | update]), clear_first=True)
            connect.assert_not_called()

    def test_invalid_later_room_does_not_mutate_first(self):
        row = dict(name='A', kind='compartment', aft=0,fwd=2,port=-1,stbd=1,bottom=0,top=2,permeability=0.9)
        with patch.object(com, 'connect') as connect, self.assertRaises(MaxsurfValidationError):
            stability.define_subdivision.__wrapped__(json.dumps([row, {'name': 'B'}]), clear_first=True)
        connect.assert_not_called()

    def test_missing_room_permeability_is_not_assumed(self):
        row = dict(name='A', kind='compartment', aft=0,fwd=2,port=-1,stbd=1,bottom=0,top=2)
        with patch.object(com, 'connect') as connect, self.assertRaises(MaxsurfValidationError):
            stability.define_subdivision.__wrapped__(json.dumps([row]))
        connect.assert_not_called()

    def test_bad_mass_does_not_clear_case(self):
        row = dict(name='M', mass_t=-3, lcg=1,tcg=0,vcg=1)
        with patch.object(com, 'connect') as connect, self.assertRaises(MaxsurfValidationError):
            stability.define_loadcase.__wrapped__('L', '{}', json.dumps([row]),clear_first=True)
        connect.assert_not_called()

    def test_replace_requires_backup_and_confirmation(self):
        for confirmed, path in ((False,''),(True,''),(False,'backup.hmd')):
            with self.assertRaises(MaxsurfSafetyError): stability._before_replace(NS(),True,confirmed,path)

    def test_intact_is_selected_explicitly(self):
        design = NS(ActiveDamageCaseByName='Old damage')
        stability._select_case(design,'L','')
        self.assertEqual(design.ActiveDamageCaseByName, 'Intact')
        self.assertEqual(design.ActiveLoadCaseByName, 'L')

    def test_bad_resistance_input_rejected_before_connection(self):
        with patch.object(com, 'connect') as connect, self.assertRaises(MaxsurfValidationError):
            resistance.run_resistance_analysis.__wrapped__(math.nan,1e-6,0,70)
        connect.assert_not_called()

    def test_unreadable_method_selection_is_not_selected(self):
        methods = NS(IsSelected=lambda n: (_ for _ in ()).throw(RuntimeError('unreadable')))
        with patch.object(resistance,'_method_catalog',return_value={'hsMTHoltrop':5}), self.assertRaises(MaxsurfCOMError):
            resistance._selected_methods(NS(),NS(Methods=methods))

    def test_bad_motions_input_rejected_before_connection(self):
        with patch.object(com, 'connect') as connect, self.assertRaises(MaxsurfValidationError):
            motions.configure_motions_analysis.__wrapped__('[NaN]','[4]','[]',1025,2,1,1,3,3,0.1,'skATStripTheory','skVTMonohull')
        connect.assert_not_called()

    def test_nonfinite_motions_statistics_are_failure(self):
        item = NS(_prop_map_get_={'Heave': None}, Heave=math.inf)
        with self.assertRaises(MaxsurfAnalysisError): motions._read_result_item(item)


class NativeCriteriaTests(unittest.TestCase):
    def evaluate(self, statuses, success=True):
        items=[NS(IncludeForIntact=True,IncludeForDamage=True,IncludeForAnalysis=False,Status=s) for s in statuses]
        criteria=NS(Count=len(items),Item=lambda i:items[i-1],DeleteAll=lambda:None,
                    Import=lambda p:None,Calculate=lambda:None,AnalysisSuccessful=lambda message:success)
        design=NS(Criteria=criteria,RunAnalysis=lambda:None)
        with patch.object(com,'connect',return_value=NS(Design=design)), patch.object(stability,'_resolve_hcr',return_value='fake.hcr'), patch.object(stability,'_constant',return_value=1), patch.object(stability,'_set_stability_heel'), patch.object(stability,'_read_xmlgrid_table',return_value={'rows':[]}):
            return json.loads(stability.evaluate_stability_criteria.__wrapped__('S','L',0,10,5,20,5,30,5,confirm_replace=True))

    def test_all_native_passes_with_success_can_pass(self):
        self.assertEqual(self.evaluate([1,1],(True,''))['verdict'],'pass')

    def test_unanalysed_invalid_unknown_never_pass(self):
        for statuses in ([1,3],[1,4],[1,99]):
            self.assertEqual(self.evaluate(statuses)['verdict'],'inconclusive')

    def test_false_analysis_success_never_passes(self):
        self.assertEqual(self.evaluate([1],(False,'failed'))['verdict'],'inconclusive')

    def test_completed_native_failure_is_reported(self):
        self.assertEqual(self.evaluate([1,2])['verdict'],'fail')

    def test_criteria_paths_cannot_escape_workspace(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'MAXSURF_MCP_WORKSPACE_ROOTS':tmp}), self.assertRaises(MaxsurfSafetyError):
            stability._resolve_hcr(str(Path(tmp).parent/'forbidden.hcr'))


class ReleaseContractTests(unittest.TestCase):
    def test_engineering_inputs_are_required(self):
        for function, fields in ((resistance.run_resistance_analysis,('water_density_kg_m3','propulsive_efficiency_percent')), (motions.configure_motions_analysis,('analysis_type','roll_damping_nondim','gyradius_roll_m'))):
            for field in fields:
                self.assertIs(inspect.signature(function).parameters[field].default,inspect.Parameter.empty)

    def test_default_surface_excludes_recipes_and_legacy(self):
        names=set(server.REGISTERED_TOOLS)
        self.assertFalse(names & {'plan_motor_yacht','plan_usv_catamaran','evaluate_design','hull_summary','com_invoke','run_analysis'})

    def test_tool_hints_are_explicit(self):
        for tool in server.mcp._tool_manager.list_tools():
            self.assertIsNotNone(tool.annotations)
        tool=next(t for t in server.mcp._tool_manager.list_tools() if t.name=='transfer_modeler_to_stability')
        self.assertFalse(tool.annotations.readOnlyHint)
        self.assertTrue(tool.annotations.destructiveHint)

    def test_wire_server_version_is_project_version(self):
        self.assertEqual(server.mcp._mcp_server.version,'0.2.0-rc.5')
