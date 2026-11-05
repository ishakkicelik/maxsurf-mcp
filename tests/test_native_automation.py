from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pythoncom

from maxsurf_mcp import native_automation as na, native_inventory as ni
from maxsurf_mcp.errors import MaxsurfCOMError, MaxsurfSafetyError, MaxsurfValidationError
from maxsurf_mcp.guard import REGISTRY
from tests.test_native_types import Info, Library, function


class Dispatch:
    def __init__(self, info, result=None):
        self.info = info
        self.result = result
        self.calls = []
        self._oleobj_ = self

    def GetTypeInfo(self):
        return self.info

    def InvokeTypes(self, *args):
        self.calls.append(args)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class Enumerator:
    def __init__(self, values=(), position=0):
        self.values = tuple(values)
        self.position = position
        self.queries = []
        self.calls = []

    def QueryInterface(self, iid):
        self.queries.append(iid)
        if iid != pythoncom.IID_IEnumVARIANT:
            raise RuntimeError("not IDispatch")
        return self

    def Next(self, count):
        self.calls.append(("next", count))
        values = self.values[self.position:self.position + count]
        self.position += len(values)
        return values

    def Skip(self, count):
        self.calls.append(("skip", count))
        self.position = min(len(self.values), self.position + count)

    def Reset(self):
        self.calls.append(("reset",))
        self.position = 0

    def Clone(self):
        self.calls.append(("clone",))
        return Enumerator(self.values, self.position)


class NativeAutomationTests(unittest.TestCase):
    def setUp(self):
        na._handles.clear()
        self.addCleanup(na._handles.clear)
        self.env = patch.dict(os.environ, {"MAXSURF_MCP_AUDIT": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def setup_contract(self, funcs, interface="IApplication", module="modeler",
                       references=None, result=None):
        self.module, self.interface = module, interface
        self.info = Info(interface, funcs=funcs, references=references)
        self.library = Library([self.info] + list((references or {}).values()))
        self.app = Dispatch(self.info, result)
        library = ni.inspect_library(self.library, module)
        self.snapshot = {"module": module, "libraries": [library],
                         "inventory_digest": "test-digest"}
        self.session = SimpleNamespace(session_id="session-one", reconnect_count=0,
                                       app=self.app, design_path="design.msd",
                                       design_name="design")
        for target, name, value in (
                (ni, "get_inventory", self.snapshot),
                (na.com, "connect", self.app),
                (na.com, "session_for", self.session)):
            mock = patch.object(target, name, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)

    def call(self, member="Value", kind="method", args=None, **kwargs):
        return json.loads(na.native_call.__wrapped__(
            self.interface, member, kind, json.dumps({} if args is None else args),
            self.module, acknowledge_native_effects=True, **kwargs))

    def test_registration_is_diagnostic_opt_in(self):
        self.assertEqual(REGISTRY["native_call"]["classification"], "DIAGNOSTIC")
        self.assertEqual(REGISTRY["native_call"]["tier"], "diagnostics")
        with patch.dict(os.environ, {"MAXSURF_MCP_ENABLE_DIAGNOSTICS": "0"}):
            with patch.object(na.com, "connect") as connect:
                with self.assertRaises(MaxsurfSafetyError):
                    na.native_call("IApplication", "Value",
                                   acknowledge_native_effects=True)
                connect.assert_not_called()

    def test_acknowledgement_is_exact_boolean(self):
        for value in (False, 1, "true", None):
            with self.subTest(value=value), self.assertRaises(MaxsurfSafetyError):
                na.native_call.__wrapped__("IApplication", "Value",
                                           acknowledge_native_effects=value)

    def test_valid_scalar_method_uses_native_dispid_not_python_member(self):
        self.setup_contract([function(args=[(5, 1, None), (11, 1, None)])])
        self.call(args=[1.25, False])
        self.assertEqual(self.app.calls, [(7, 0, 1, (24, 0), ((5, 1), (11, 1)), 1.25, False)])
        self.assertFalse(hasattr(self.app, "Value"))

    def test_python_only_member_is_never_invoked(self):
        self.setup_contract([function()])
        self.app.PythonOnly = lambda: self.fail("Python method executed")
        with self.assertRaises(MaxsurfValidationError):
            self.call("PythonOnly")
        self.assertEqual(self.app.calls, [])

    def test_wrong_invocation_kind_refused(self):
        self.setup_contract([function(invkind=2, returns=3)], result=1)
        with self.assertRaises(MaxsurfValidationError):
            self.call(kind="property_put", args=[2])
        self.assertEqual(self.app.calls, [])

    def test_live_identity_and_live_signature_must_match_manifest(self):
        self.setup_contract([function(args=[(5, 1, None)])])
        self.info.attr.iid = "{FOREIGN}"
        with self.assertRaises(MaxsurfSafetyError):
            self.call(args=[1])
        self.info.attr.iid = "{IApplication}"
        self.info.funcs[0].args = [(3, 1, None)]
        with self.assertRaises(MaxsurfSafetyError):
            self.call(args=[1])
        self.assertEqual(self.app.calls, [])

    def test_changed_inventory_digest_and_metadata_errors_refused(self):
        self.setup_contract([function()])
        with self.assertRaises(MaxsurfSafetyError):
            self.call(inventory_digest="old-digest")
        entry = self.snapshot["libraries"][0]["types"][0]["members"][0]
        entry["errors"] = [{"code": "metadata_error"}]
        with self.assertRaises(MaxsurfCOMError):
            self.call()
        self.assertEqual(self.app.calls, [])

    def test_invalid_scalar_arguments_refused_before_connection(self):
        cases = [(3, True), (3, 1.5), (3, 2**31), (11, 1), (5, True),
                 (5, float("nan")), (5, float("inf")), (4, 1e39)]
        for vt, value in cases:
            with self.subTest(vt=vt, value=value):
                self.setup_contract([function(args=[(vt, 1, None)])])
                with self.assertRaises(MaxsurfValidationError):
                    self.call(args=[value])
                na.com.connect.assert_not_called()

    def test_enum_validates_declared_values_and_preserves_type(self):
        enum = Info("Mode", kind=0, variables=[(1, "First", 1, 3), (2, "Third", 3, 3)])
        self.setup_contract([function(args=[((29, 941), 1, None)])], references={941: enum})
        self.call(args=[3])
        self.assertEqual(self.app.calls[-1][4], ((3, 1),))
        for value in (True, 2, "First"):
            with self.subTest(value=value), self.assertRaises(MaxsurfValidationError):
                self.call(args=[value])
        self.assertEqual(len(self.app.calls), 1)

    def test_typed_array_rejects_invalid_elements_and_shape(self):
        self.setup_contract([function(args=[((27, 5), 1, None)])])
        self.call(args=[[1, 2.5]])
        self.assertEqual(self.app.calls[-1][4], ((pythoncom.VT_ARRAY | 5, 1),))
        self.call(args=[[[1, 2], [3, 4]]])
        self.assertEqual(self.app.calls[-1][-1], [[1, 2], [3, 4]])
        for value in ([1, True], [1, float("inf")], [[1], [2, 3]], "array"):
            with self.subTest(value=value), self.assertRaises(MaxsurfValidationError):
                self.call(args=[value])
        self.assertEqual(len(self.app.calls), 2)

    def test_variant_arrays_and_inout_arguments(self):
        self.setup_contract([function(args=[((26, 12), 3, None)])],
                            interface="IElement", module="multiframe", result=[True, False])
        result = self.call(args=[[True, False]])
        self.assertEqual(self.app.calls[-1][4], ((pythoncom.VT_BYREF | 12, 3),))
        self.assertEqual(result["result"], [True, False])
        with self.assertRaises(MaxsurfValidationError):
            self.call(args=[{"arbitrary": "dictionary"}])

    def test_indexed_get_and_put_are_distinct_native_operations(self):
        self.setup_contract([
            function("Coordinate", 42, 2, [(3, 1, None)], returns=5),
            function("Coordinate", 42, 4, [(3, 1, None), (5, 1, None)])], result=3.5)
        self.assertEqual(self.call("Coordinate", "property_get", [2])["result"], 3.5)
        self.app.result = None
        self.call("Coordinate", "property_put", [2, 8.25])
        self.assertEqual(self.app.calls[0][2], 2)
        self.assertEqual(self.app.calls[1][2], 4)
        self.assertEqual(self.app.calls[1][-2:], (2, 8.25))
        for args in ([True], [1.5], [1, 2]):
            with self.subTest(args=args), self.assertRaises(MaxsurfValidationError):
                self.call("Coordinate", "property_get", args)

    def test_optional_defaults_and_omitted_holes_use_native_placeholders(self):
        self.setup_contract([function(args=[
            (22, 49, 100), (3, 17, None), (11, 49, False)])])
        self.call(args={"value2": True})
        self.assertEqual(self.app.calls[-1][-3:], (100, pythoncom.Missing, True))
        self.assertEqual(self.app.calls[-1][4][0], (3, 49))

    def test_out_only_arguments_cannot_be_supplied(self):
        self.setup_contract([function(args=[
            (3, 1, None), ((26, 5), 2, None), ((26, 5), 2, None)])],
            result=(2.0, 3.0))
        result = self.call(args=[1])
        self.assertEqual(self.app.calls[-1][-3:], (1, pythoncom.Missing, pythoncom.Missing))
        self.assertEqual(result["output_arguments"], ["value1", "value2"])
        self.assertEqual(result["result"], [2.0, 3.0])
        for args in ([1, 2], {"value0": 1, "value1": 2}):
            with self.subTest(args=args), self.assertRaises(MaxsurfValidationError):
                self.call(args=args)

    def test_unsupported_vtable_and_carray_contracts_have_blockers(self):
        for args in ([((28, (3, (2, 1))), 1, None)],
                     [((26, 5), 10, None)], [(3, 5, None)]):
            with self.subTest(args=args):
                self.setup_contract([function(args=args)])
                with self.assertRaises(MaxsurfSafetyError):
                    self.call()
                self.assertEqual(self.app.calls, [])

    def test_forbidden_traversal_and_com_infrastructure_are_refused(self):
        for name in ("__class__", "InvokeTypes", "_oleobj_", "Design.Save"):
            with self.subTest(name=name):
                self.setup_contract([function(name)])
                with self.assertRaises(MaxsurfSafetyError):
                    self.call(name)
                self.assertEqual(self.app.calls, [])

    def test_handles_are_session_owned_typed_and_bounded(self):
        child = Info("IChild", funcs=[function("Count", 1, 2, returns=3)])
        self.setup_contract([function("Child", 1, 2, returns=(26, (29, 52)))],
                            references={52: child})
        target = Dispatch(child, 7)
        self.app.result = target
        result = self.call("Child", "property_get")["result"]
        token = result["native_handle"]
        self.interface = "IChild"
        self.assertEqual(self.call("Count", "property_get", target_handle=token)["result"], 7)
        self.session.session_id = "other-session"
        with self.assertRaises(MaxsurfSafetyError):
            self.call("Count", "property_get", target_handle=token)
        self.assertEqual(len(target.calls), 1)
        self.interface = "IApplication"
        with patch.object(na, "MAX_HANDLES", 2):
            tokens = [self.call("Child", "property_get")["result"]["native_handle"]
                      for _ in range(3)]
            self.assertEqual(len(na._handles), 2)
            self.assertNotIn(tokens[0], na._handles)

    def test_stale_design_and_foreign_handles_refused(self):
        self.setup_contract([function()])
        na._handles["foreign"] = ("motions", na._session_stamp(self.session), self.app)
        with self.assertRaises(MaxsurfSafetyError):
            self.call(target_handle="foreign")
        na._handles["old"] = ("modeler", na._session_stamp(self.session), self.app)
        self.session.design_path = "replacement.msd"
        with self.assertRaises(MaxsurfSafetyError):
            self.call(target_handle="old")
        self.assertEqual(self.app.calls, [])

    def test_object_inputs_require_owned_correct_interface_handles(self):
        child = Info("IChild")
        self.setup_contract([function(args=[((26, (29, 52)), 1, None)])],
                            references={52: child})
        obj = Dispatch(child)
        na._handles["child"] = ("modeler", na._session_stamp(self.session), obj)
        self.call(args=[{"native_handle": "child"}])
        self.assertIs(self.app.calls[-1][-1], obj)
        self.assertEqual(self.app.calls[-1][4], ((9, 1),))
        self.assertNotIn("child", na._handles)
        with self.assertRaises(MaxsurfSafetyError):
            self.call(args=[{"native_handle": "child"}])
        na._handles["wrong"] = ("modeler", na._session_stamp(self.session), self.app)
        with self.assertRaises(MaxsurfValidationError):
            self.call(args=[{"native_handle": "wrong"}])

    def test_native_failure_is_typed_and_expires_handles(self):
        self.setup_contract([function()], result=RuntimeError("native failure"))
        na._handles["old"] = ("modeler", na._session_stamp(self.session), self.app)
        with self.assertRaises(MaxsurfCOMError) as caught:
            self.call(target_handle="old")
        self.assertIn("native invocation failed", str(caught.exception))
        self.assertNotIn("old", na._handles)
        self.assertEqual(len(self.app.calls), 1)

    def test_putref_and_null_object_arguments(self):
        child = Info("IChild")
        self.setup_contract([function("Child", 8, 8, [((26, (29, 52)), 1, None)])],
                            references={52: child})
        obj = Dispatch(child)
        na._handles["child"] = ("modeler", na._session_stamp(self.session), obj)
        self.call("Child", "property_putref", [{"native_handle": "child"}])
        self.call("Child", "property_putref", [None])
        self.assertEqual([call[2] for call in self.app.calls], [8, 8])
        self.assertIsNone(self.app.calls[-1][-1])

    def test_return_and_out_tuple_is_typed_and_shape_checked(self):
        self.setup_contract([function(returns=3, args=[((26, 5), 2, None)])],
                            result=(4, 2.5))
        self.assertEqual(self.call()["result"], [4, 2.5])
        self.app.result = (4,)
        with self.assertRaises(MaxsurfCOMError):
            self.call()
        self.app.result = (4, True)
        with self.assertRaises(MaxsurfValidationError):
            self.call()

    def test_typed_object_array_and_null_result(self):
        child = Info("IChild")
        self.setup_contract([function("Children", 1, 2, returns=(27, (26, (29, 52))))],
                            references={52: child})
        self.app.result = [Dispatch(child), None]
        result = self.call("Children", "property_get")["result"]
        self.assertIn("native_handle", result[0])
        self.assertIsNone(result[1])

    def test_unknown_file_policy_is_refused_before_connect(self):
        self.setup_contract([function("ExportUnknown", args=[(8, 1, None)])],
                            interface="IDesign")
        with self.assertRaises(MaxsurfSafetyError):
            self.call("ExportUnknown", args=["C:/outside.unknown"])
        na.com.connect.assert_not_called()

    def test_flagged_byref_safearray_preserves_its_native_marshaling_bits(self):
        raw = pythoncom.VT_ARRAY | pythoncom.VT_BYREF | pythoncom.VT_R8
        descriptor = na.native_types.descriptor(Info(), raw)
        self.assertEqual(na._marshal_type(descriptor), raw)

    def test_live_return_and_function_flags_must_match_manifest(self):
        for field, changed in (("rettype", (3, 2, None)), ("callconv", 5),
                               ("funckind", 6), ("wFuncFlags", 1)):
            with self.subTest(field=field):
                func = function(returns=3)
                self.setup_contract([func], result=3)
                setattr(func, field, changed)
                with self.assertRaises(MaxsurfSafetyError):
                    self.call()
                self.assertEqual(self.app.calls, [])

    def test_protected_stability_replacement_is_not_unmapped_diagnosis(self):
        for interface, name, args in (
                ("IKeyPoints", "DeleteAll", []), ("ITanks", "DeleteAll", []),
                ("ICompartments", "DeleteAll", []),
                ("ILoadCases", "RemoveByName", [(8, 1, None)])):
            with self.subTest(interface=interface, member=name):
                self.setup_contract([function(name, args=args)], interface=interface,
                                    module="stability")
                with self.assertRaises(MaxsurfSafetyError):
                    self.call(name, args=["case"] if args else [],
                              allow_unmapped_effects=True)
                na.com.connect.assert_not_called()

    def test_stability_destructive_and_import_aliases_refused_before_connect(self):
        aliases = {
            "ILoadCases": ("Remove", "RemoveAll", "RemoveByName"),
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
        }
        for interface, names in aliases.items():
            for name in names:
                for override in (False, True):
                    with self.subTest(interface=interface, name=name, override=override):
                        vt = 8 if name.startswith("Open") or name in (
                            "Import", "RemoveByName", "DeleteMassItemByName") else 3
                        args = [] if name in (
                            "RemoveAll", "Delete", "DeleteAll", "DeleteAllMassItems",
                            "SQLdbDeleteAllResults") else [(vt, 1, None)]
                        self.setup_contract([function(name, args=args)],
                                            interface=interface, module="stability")
                        with self.assertRaises(MaxsurfSafetyError):
                            self.call(name, args=[] if not args else [
                                "input" if vt == 8 else 1],
                                allow_unmapped_effects=override)
                        na.com.connect.assert_not_called()

    def open_enum(self, values=()):
        self.enum = Enumerator(values)
        backend = patch.dict(pythoncom.TypeIIDs, {pythoncom.IID_IEnumVARIANT: Enumerator})
        backend.start()
        self.addCleanup(backend.stop)
        self.setup_contract([function("_NewEnum", -4, 2, returns=13)],
                            interface="ISurfaces", result=self.enum)
        return self.call("_NewEnum", "property_get")["result"]

    def enumerate(self, handle, operation="next", count=1, **kwargs):
        return json.loads(na.native_enumerate.__wrapped__(
            handle, operation, count, self.module,
            acknowledge_native_effects=True, **kwargs))

    def test_newenum_uses_exact_dispid_get_and_real_enum_interface(self):
        handle = self.open_enum([1, "two"])
        self.assertEqual(handle["interface"], "IEnumVARIANT")
        self.assertEqual(handle["ownership"], "session")
        self.assertFalse(handle["engineering_acceptance"])
        self.assertEqual(self.enum.queries, [pythoncom.IID_IEnumVARIANT])
        self.assertEqual(self.app.calls, [(-4, 0, 2, (13, 0), ())])
        result = self.enumerate(handle["native_enum_handle"], count=3)
        self.assertEqual(result["result"], [1, "two"])
        self.assertEqual(result["fetched"], 2)
        self.assertTrue(result["exhausted"])
        self.assertEqual(REGISTRY["native_enumerate"]["classification"], "DIAGNOSTIC")
        self.assertEqual(REGISTRY["native_enumerate"]["tier"], "diagnostics")
        with patch.dict(os.environ, {"MAXSURF_MCP_ENABLE_DIAGNOSTICS": "0"}):
            with self.assertRaises(MaxsurfSafetyError):
                na.native_enumerate(handle["native_enum_handle"],
                                    acknowledge_native_effects=True)

    def test_real_pywin32_enum_backend_next_skip_reset_clone(self):
        from win32com.server.util import NewEnum

        # An in-process COM gateway exercises PyIEnumVARIANT without Bentley
        # activation or a model. None of its operations uses InvokeTypes.
        self.setup_contract([function("_NewEnum", -4, 2, returns=13)],
                            interface="ISurfaces", result=NewEnum([1, "two", 3]))
        handle = self.call("_NewEnum", "property_get")["result"]["native_enum_handle"]
        self.assertEqual(self.enumerate(handle)["result"], [1])
        clone = self.enumerate(handle, "clone")["result"]["native_enum_handle"]
        self.enumerate(handle, "skip")
        self.assertEqual(self.enumerate(handle, count=2)["result"], [3])
        self.assertEqual(self.enumerate(clone)["result"], ["two"])
        self.enumerate(handle, "reset")
        self.assertEqual(self.enumerate(handle)["result"], [1])
        self.assertEqual(len(self.app.calls), 1)

    def test_native_enum_batches_do_not_lose_items_when_server_only_fills_one_slot(self):
        token = self.open_enum(["first", "second", "third"])["native_enum_handle"]
        native_next = self.enum.Next

        def one_slot_next(count):
            values = native_next(count)
            return (values[-1],) + (None,) * (len(values) - 1) if values else ()

        with patch.object(self.enum, "Next", side_effect=one_slot_next):
            batch = self.enumerate(token, count=4)
        self.assertEqual(batch["result"], ["first", "second", "third"])
        self.assertEqual(batch["fetched"], 3)
        self.assertTrue(batch["exhausted"])

    def test_full_object_pages_keep_enumerator_and_returned_handles_alive(self):
        child = Dispatch(Info("IChild"))
        token = self.open_enum([child] * 300)["native_enum_handle"]
        first = self.enumerate(token, count=256)
        self.assertEqual(first["fetched"], 256)
        self.assertIn(token, na._handles)
        for item in first["result"]:
            self.assertIs(na._owned(item["native_handle"], self.module,
                                   na._session_stamp(self.session)), child)
        second = self.enumerate(token, count=256)
        self.assertEqual(second["fetched"], 44)
        self.assertTrue(second["exhausted"])
        self.assertIn(token, na._handles)
        for item in second["result"]:
            self.assertIs(na._owned(item["native_handle"], self.module,
                                   na._session_stamp(self.session)), child)
        self.assertLessEqual(len(na._handles), na.MAX_HANDLES)

    def test_only_exact_newenum_contract_bypasses_underscore_restriction(self):
        for name, dispid, kind, args, returns in (
                ("_NewEnum", 7, 2, [], 13), ("_NewEnum", -4, 1, [], 13),
                ("_NewEnum", -4, 2, [(3, 1, None)], 13),
                ("_NewEnum", -4, 2, [], 9), ("_Other", -4, 2, [], 13)):
            with self.subTest(name=name, dispid=dispid, kind=kind):
                self.setup_contract([function(name, dispid, kind, args, returns=returns)])
                with self.assertRaises(MaxsurfSafetyError):
                    self.call(name, "property_get" if kind == 2 else "method")
                na.com.connect.assert_not_called()

    def test_enumeration_skip_reset_clone_and_owned_com_items(self):
        child = Dispatch(Info("IChild"))
        handle = self.open_enum([child, 2, 3])["native_enum_handle"]
        item = self.enumerate(handle)["result"][0]
        self.assertIn("native_handle", item)
        self.assertIs(na._owned(item["native_handle"], self.module,
                               na._session_stamp(self.session)), child)
        clone = self.enumerate(handle, "clone")["result"]["native_enum_handle"]
        self.enumerate(handle, "skip")
        self.assertEqual(self.enumerate(handle)["result"], [3])
        self.assertEqual(self.enumerate(clone)["result"], [2])
        self.enumerate(handle, "reset")
        self.assertEqual(self.enumerate(handle)["result"][0]["interface"]["guid"], "{IChild}")
        self.assertEqual(len(self.app.calls), 1)

    def test_enumeration_validates_tokens_bounds_and_consent_before_operations(self):
        token = self.open_enum([1])["native_enum_handle"]
        for count in (0, -1, True, 257, 1.5):
            with self.subTest(count=count), self.assertRaises(MaxsurfValidationError):
                self.enumerate(token, count=count)
        for operation in ("Next", "InvokeTypes", "__class__", ""):
            with self.subTest(operation=operation), self.assertRaises(MaxsurfValidationError):
                self.enumerate(token, operation)
        with self.assertRaises(MaxsurfValidationError):
            self.enumerate(token, "clone", 2)
        with self.assertRaises(MaxsurfSafetyError):
            na.native_enumerate.__wrapped__(token)
        with self.assertRaises(MaxsurfValidationError):
            na._owned(token, self.module, na._session_stamp(self.session))
        na._handles["ordinary"] = (self.module, na._session_stamp(self.session), self.app)
        for wrong in ("ordinary", "missing"):
            with self.subTest(token=wrong), self.assertRaises(
                    (MaxsurfSafetyError, MaxsurfValidationError)):
                self.enumerate(wrong)
        na._handles["foreign"] = ("motions", na._session_stamp(self.session),
                                  na._handles[token][2])
        with self.assertRaises(MaxsurfSafetyError):
            self.enumerate("foreign")
        self.session.reconnect_count += 1
        with self.assertRaises(MaxsurfSafetyError):
            self.enumerate(token)
        self.assertEqual(self.enum.calls, [])

    def test_enum_contract_live_signature_and_queryinterface_failures(self):
        self.open_enum()
        self.info.funcs[0].wFuncFlags = 99
        with self.assertRaises(MaxsurfSafetyError):
            self.call("_NewEnum", "property_get")
        self.info.funcs[0].wFuncFlags = 0
        self.app.result = object()
        with self.assertRaises(MaxsurfCOMError):
            self.call("_NewEnum", "property_get")

    def test_enum_cleanup_uses_existing_handle_lifecycle(self):
        token = self.open_enum()["native_enum_handle"]
        self.assertEqual(na.clear_handles("motions"), 0)
        self.assertIn(token, na._handles)
        self.assertEqual(na.clear_handles(self.module), 1)
        with self.assertRaises(MaxsurfSafetyError):
            self.enumerate(token)
        token = self.call("_NewEnum", "property_get")["result"]["native_enum_handle"]
        na.release_native_handles.__wrapped__(
            json.dumps([token]), acknowledge_native_effects=True)
        self.assertNotIn(token, na._handles)
        self.call("_NewEnum", "property_get")
        self.assertEqual(na.clear_handles(), 1)

    def test_enum_capacity_and_native_failures_remain_bounded(self):
        token = self.open_enum([1])["native_enum_handle"]
        with patch.object(self.enum, "Next", side_effect=RuntimeError("COM failure")):
            with self.assertRaises(MaxsurfCOMError):
                self.enumerate(token)
        with patch.object(self.enum, "Next", return_value=(1, 2)):
            with self.assertRaises(MaxsurfCOMError):
                self.enumerate(token)
        with patch.object(na, "MAX_HANDLES", 2):
            for _ in range(2):
                self.enumerate(token, "clone")
            self.assertNotIn(token, na._handles)
            self.assertEqual(len(na._handles), 2)

    def test_enum_tokens_cannot_be_targets_or_survive_native_mutations(self):
        token = self.open_enum()["native_enum_handle"]
        with self.assertRaises(MaxsurfValidationError):
            self.call("_NewEnum", "property_get", target_handle=token)
        self.assertEqual(len(self.app.calls), 1)
        self.setup_contract([function("Value")])
        self.call()
        self.assertNotIn(token, na._handles)

    def test_string_limit_counts_characters_for_ascii_and_multibyte_text(self):
        for character in ("a", "\u00e9", "\U0001f600"):
            with self.subTest(character=character):
                na._bounded(character * 1048576)
                with self.assertRaises(MaxsurfValidationError):
                    na._bounded(character * 1048577)

    def test_explicit_unmapped_effects_retains_contract_validation(self):
        # Given: an installed text operation without a reviewed effect policy.
        self.setup_contract([function("SetLabel", args=[(8, 1, None)])])
        # When: diagnostic mode explicitly acknowledges the unmapped effects.
        result = self.call("SetLabel", args=["label"], allow_unmapped_effects=True)
        # Then: dispatch is real, while lack of a sandbox guarantee is explicit.
        self.assertEqual(self.app.calls[-1][-1], "label")
        self.assertFalse(result["file_policy"]["sandbox_validated"])
        self.assertTrue(result["contract_validated"])

    def test_declared_native_getproperty_is_not_python_attribute_access(self):
        # Given: the real native contract uses a name reserved by Python paths.
        self.setup_contract([function("GetProperty", args=[(8, 1, None)], returns=8)],
                            result="stored value")
        # When: its verified DISPID is invoked in explicit diagnostic mode.
        result = self.call("GetProperty", args=["label"], allow_unmapped_effects=True)
        # Then: only the native dispatch occurs; no Python property API is used.
        self.assertEqual(result["result"], "stored value")
        self.assertEqual(len(self.app.calls), 1)

    def test_unmapped_effects_never_bypasses_a_known_filename_policy(self):
        # Given: an installed export with an exact reviewed filename mapping.
        func = function("ExportIGES", args=[(8, 1, None)])
        func.names = ("ExportIGES", "sFileName")
        self.setup_contract([func], interface="IDesign")
        # When: diagnostic effect consent accompanies an out-of-workspace file.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(na.apply_file_policy.__globals__["workspace"].config,
                              "workspace_roots", return_value=(root,)):
                with self.assertRaises(MaxsurfSafetyError):
                    self.call("ExportIGES", args=[str(root.parent / "outside.igs")],
                              allow_unmapped_effects=True)
        # Then: known file boundaries still prevent native execution.
        self.assertEqual(self.app.calls, [])

    def test_file_sandbox_and_explicit_overwrite(self):
        func = function("ExportIGES", args=[(8, 1, None)])
        func.names = ("ExportIGES", "sFileName")
        self.setup_contract([func], interface="IDesign")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "native.igs"
            with patch.object(na.apply_file_policy.__globals__["workspace"].config,
                              "workspace_roots", return_value=(root,)):
                for path in ("relative.igs", str(root / ".." / "outside.igs"),
                             str(root.parent / "outside.igs"), str(root / "wrong.exe")):
                    with self.subTest(path=path), self.assertRaises(MaxsurfSafetyError):
                        self.call("ExportIGES", args=[path])
                result = self.call("ExportIGES", args=[str(target)])
                self.assertEqual(result["file_policy"]["mode"], "write")
                target.touch()
                with self.assertRaises(MaxsurfSafetyError):
                    self.call("ExportIGES", args=[str(target)])
                self.call("ExportIGES", args=[str(target)], overwrite=True)
                self.assertEqual(len(self.app.calls), 2)

    def test_native_overwrite_flag_cannot_bypass_consent(self):
        func = function("SaveAs", args=[(8, 1, None), (11, 1, None)])
        func.names = ("SaveAs", "sFileName", "bOverwrite")
        self.setup_contract([func], interface="IDesign")
        with self.assertRaises(MaxsurfSafetyError):
            self.call("SaveAs", args=["C:/unsafe.msd", True], overwrite=False)
        na.com.connect.assert_not_called()

    def test_binary_skr_and_obsolete_solver_routes_remain_refused(self):
        for name in ("SKResultsSave", "SKResultsSaveAs", "CalculateMesh", "CalculateSeakeeping"):
            with self.subTest(name=name):
                self.setup_contract([function(name)], interface="IDesign", module="motions")
                with self.assertRaises(MaxsurfSafetyError):
                    self.call(name)
                na.com.connect.assert_not_called()

    def test_ui_command_and_implicit_save_routes_refused(self):
        for interface, name in (("IUserInterface", "Execute"), ("IDesign", "Save"),
                                ("IApplication", "Quit"), ("IApplication", "TraceToFile"),
                                ("IApplication", "Exit"), ("IXMLGrid", "SetCell")):
            with self.subTest(interface=interface, name=name):
                self.setup_contract([function(name)], interface=interface)
                with self.assertRaises(MaxsurfSafetyError):
                    self.call(name)
                self.assertEqual(self.app.calls, [])

    def test_input_and_result_bounds_and_nonfinite_results(self):
        self.setup_contract([function(returns=5)], result=float("nan"))
        with self.assertRaises(MaxsurfValidationError):
            self.call()
        with patch.object(na, "MAX_VALUES", 2):
            with self.assertRaises(MaxsurfValidationError):
                self.call(args=[1, 2, 3])


class NativeHandleCleanupTests(unittest.TestCase):
    def setUp(self):
        na._handles.clear()
        na._handles["valid"] = ("modeler", ("session", "design"), object())

    def tearDown(self):
        na._handles.clear()

    def test_release_validates_entire_selection_before_cleanup(self):
        # Given: one valid reference and one unknown reference.
        # When: the caller requests a mixed valid/invalid selection.
        with self.assertRaises(MaxsurfValidationError):
            na.release_native_handles.__wrapped__(
                '["valid", "unknown"]', acknowledge_native_effects=True)
        # Then: validation does not partially release the valid reference.
        self.assertIn("valid", na._handles)

    def test_release_all_drops_cached_native_references(self):
        # Given: a retained native object on the server's owning STA.
        # When: the caller explicitly releases all references.
        result = json.loads(na.release_native_handles.__wrapped__(
            release_all=True, acknowledge_native_effects=True))
        # Then: the cache no longer prevents native object release.
        self.assertEqual(result, {"released": 1, "remaining": 0})
        self.assertEqual(len(na._handles), 0)

    def test_module_cleanup_preserves_other_session_references(self):
        # Given: cached objects owned by two distinct managed sessions.
        na._handles["other"] = ("motions", ("session", "design"), object())
        # When: a Modeler reset releases its own references.
        released = na.clear_handles("modeler")
        # Then: another session's reference remains available.
        self.assertEqual(released, 1)
        self.assertEqual(list(na._handles), ["other"])


if __name__ == "__main__":
    unittest.main()
