from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from maxsurf_mcp import native_inventory as ni
from maxsurf_mcp.errors import MaxsurfCOMError, MaxsurfValidationError
from tests.test_native_types import Info, Library, function


class NativeInventoryTests(unittest.TestCase):
    def test_metadata_admission_reconciles_without_operational_claim(self):
        library, _, _ = self.fixture()
        snapshot = self.snapshot(library)
        ledger = [{"key": row["key"], "status": "metadata_admitted",
                   "route": "native_call", "classification": "DIAGNOSTIC",
                   "evidence": ["installed_metadata_only"]}
                  for row in snapshot["entries"] if row["entry_kind"] == "member"]
        result = ni.reconcile_coverage(snapshot, ledger)
        self.assertTrue(result["complete"])
        self.assertEqual(result["totals"], {"metadata_admitted": 5})
        self.assertTrue(all(not row["reachability"]["operationally_validated"]
                            for row in result["rows"]))

    def setUp(self):
        self.env = patch.dict(os.environ, {"MAXSURF_MCP_AUDIT": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.register = patch.object(ni, "_coclass_registration",
                                     return_value={"registered": False})
        self.register.start()
        self.addCleanup(self.register.stop)

    def fixture(self, *, failure=False):
        enum = Info("Mode", 0, variables=[(1, "First", 1, 3), (2, "Second", 2, 3)])
        child = Info("IChild", funcs=[
            function("Value", 10, 2, returns=5),
            function("Value", 10, 4, args=[(5, 1, None)]),
        ])
        root = Info("IApplication", funcs=[
            function("Child", 20, 2, returns=(26, (29, 681))),
            function("Configure", 21, 1, args=[((29, 992), 1, None), (11, 49, False)]),
            function("Output", 22, 1, args=[((26, 5), 2, None)]),
        ], references={681: child, 992: enum})
        root.attr.iid = ni.session.MODULES["modeler"].application_iid
        coclass = Info("Helper", 5, implementations=[(683, 1)],
                       references={683: child})
        if failure:
            child.funcs.append(RuntimeError("descriptor denied"))
            child.attr.cFuncs += 1
        lib = Library([enum, child, root, coclass])
        return lib, root, child

    def snapshot(self, library):
        with patch.object(ni, "_registered_sources",
                          return_value=[{"module": "modeler", "path": "installed.exe"}]), \
             patch.object(ni, "_source_identity", return_value={"sha256": "file-digest"}), \
             patch.object(ni.pythoncom, "LoadTypeLib", return_value=library), \
             patch.dict(ni.LIBRARIES, {"modeler": "{LIBRARY}"}):
            return ni.get_inventory("modeler")

    def test_all_declared_indices_and_helper_interfaces_are_retained(self):
        library, root, child = self.fixture()
        result = ni.inspect_library(library, "modeler")
        self.assertTrue(result["complete"])
        self.assertEqual(result["declared_type_total"], 4)
        self.assertEqual(len(result["types"]), 4)
        self.assertIn(681, root.requested_handles)
        self.assertIn(992, root.requested_handles)
        rows = {row["name"]: row for row in result["types"]}
        self.assertEqual(len(rows["Helper"]["implementations"]), 1)
        self.assertEqual(len(rows["IChild"]["members"]), 2)
        self.assertEqual(rows["IChild"]["reachability"]["status"], "metadata_reachable")
        self.assertEqual(rows["IChild"]["reachability"]["path"][-1]["member"], "Child")
        self.assertFalse(rows["IChild"]["reachability"]["operationally_validated"])
        self.assertNotIn("PROPRIETARY", json.dumps(result))
        self.assertEqual(rows["IApplication"]["members"][1]["arguments"][1]["default"], False)

    def test_member_failures_preserve_slot_totals_and_incomplete_state(self):
        library, _, _ = self.fixture(failure=True)
        snapshot = self.snapshot(library)
        self.assertFalse(snapshot["complete"])
        self.assertEqual(snapshot["declared_totals"]["member"], 6)
        self.assertEqual(snapshot["totals"]["member"], 6)
        errors = [row for row in snapshot["entries"] if "error" in row]
        self.assertEqual(errors[0]["index"], 2)
        self.assertEqual(errors[0]["error"]["message"], "descriptor denied")
        self.assertEqual(errors[0]["contract_status"], "metadata_error")

    def test_pagination_is_bounded_and_never_truncated_complete(self):
        library, _, _ = self.fixture()
        snapshot = self.snapshot(library)
        with patch.object(ni, "get_inventory", return_value=snapshot):
            first = json.loads(ni.discover_native_api.__wrapped__(
                "modeler", limit=2, kind="member"))
            self.assertEqual(first["total"], 5)
            self.assertEqual(first["next_offset"], 2)
            self.assertFalse(first["complete"])
            self.assertFalse(first["page_complete"])
            self.assertTrue(first["inventory_complete"])
            all_rows = first["entries"]
            cursor = first["next_offset"]
            while cursor is not None:
                page = json.loads(ni.discover_native_api.__wrapped__(
                    "modeler", offset=cursor, limit=2, kind="member",
                    inventory_digest=first["inventory_digest"]))
                self.assertFalse(page["complete"])
                all_rows.extend(page["entries"])
                cursor = page["next_offset"]
            self.assertEqual(len(all_rows), 5)
            self.assertEqual(len({row["key"] for row in all_rows}), 5)
            with self.assertRaises(MaxsurfValidationError):
                ni.discover_native_api.__wrapped__("modeler", inventory_digest="stale")

    def test_exact_member_selection_separates_get_put_and_rejects_bool_dispid(self):
        library, _, _ = self.fixture()
        snapshot = self.snapshot(library)
        get = ni.find_member("modeler", "IChild", "Value", "property_get", inventory=snapshot)
        put = ni.find_member("modeler", "IChild", 10, 4, inventory=snapshot)
        self.assertNotEqual(get["key"], put["key"])
        self.assertEqual(get["return"]["vartype"], 5)
        self.assertEqual(put["arguments"][0]["type"]["vartype"], 5)
        for name, kind in (("value", 2), ("Value", 1), (True, 2)):
            with self.subTest(name=name, kind=kind), self.assertRaises(MaxsurfValidationError):
                ni.find_member("modeler", "IChild", name, kind, inventory=snapshot)
        snapshot["libraries"].append(snapshot["libraries"][0])
        with self.assertRaises(MaxsurfValidationError):
            ni.find_member("modeler", "IChild", "Value", 2, inventory=snapshot)

    def test_registered_helper_root_and_event_sink_are_distinct(self):
        sink = Info("IEvents", funcs=[function("Event")])
        helper = Info("IXMLGrid", funcs=[function("Columns", returns=3)])
        coclass = Info("XMLGrid", 5, implementations=[(10, 1), (20, 2)],
                       references={10: helper, 20: sink})
        library = Library([sink, helper, coclass])
        with patch.object(ni, "_coclass_registration",
                          return_value={"registered": True, "ProgID": "Bentley.XMLGrid"}):
            result = ni.inspect_library(library, "modeler")
        rows = {row["name"]: row for row in result["types"]}
        self.assertEqual(rows["IXMLGrid"]["reachability"]["status"], "metadata_reachable")
        self.assertEqual(rows["IXMLGrid"]["reachability"]["path"][0]["kind"],
                         "registered_coclass")
        self.assertEqual(rows["IEvents"]["reachability"]["status"], "not_metadata_reachable")

    def test_inherited_and_external_reference_types_are_enumerated_once(self):
        base = Info("IBase", funcs=[function("BaseMethod")])
        external = Library([base])
        external.GetLibAttr = lambda: ("{EXTERNAL}", 0, 3, 1, 0, 0)
        root = Info("IApplication", implementations=[(999, 0)], references={999: base})
        root.attr.iid = ni.session.MODULES["modeler"].application_iid
        library = Library([root])
        result = ni.inspect_library(library, "modeler")
        self.assertEqual(len(result["types"]), 2)
        base_row = next(row for row in result["types"] if row["name"] == "IBase")
        self.assertEqual(base_row["library_guid"], "{EXTERNAL}")
        self.assertEqual(base_row["reachability"]["status"], "metadata_reachable")

    def test_vpp_application_and_helper_paths_include_verified_session_names(self):
        application = Info("IApplication")
        application.attr.iid = "{7BD743F7-C109-4D2E-91B5-68ECD7203A63}"
        grid = Info("IXMLGrid", funcs=[function("Columns", returns=3)])
        grid.attr.iid = "{673C093B-49AD-42E4-B27B-CF44955BF7D5}"
        result = ni.inspect_library(Library([application, grid]), "vpp")
        app_root = result["types"][0]["reachability"]["path"][0]
        helper_root = result["types"][1]["reachability"]["path"][0]
        self.assertEqual(app_root["progid"], "BentleyVPP.Application")
        self.assertEqual(app_root["executables"], ["MaxsurfVPP.exe"])
        self.assertEqual(app_root["design_object_member"], "")
        self.assertEqual(helper_root["module"], "vpp_xmlgrid")
        self.assertEqual(helper_root["progid"], "BentleyVPP.XMLGrid.1")
        self.assertEqual(helper_root["requires_session"], "vpp")
        self.assertEqual(helper_root["root_kind"], "helper")
        self.assertFalse(result["types"][1]["reachability"]["operationally_validated"])

    def test_shape_editor_root_is_registered_xmlgrid_without_engineering_design(self):
        grid = Info("IXMLGrid", funcs=[function("Columns", returns=3)])
        grid.attr.iid = "{5B1535B3-AE67-4C54-85F2-547813DC1848}"
        result = ni.inspect_library(Library([grid]), "shape_editor")
        root = result["types"][0]["reachability"]["path"][0]
        self.assertEqual(root["module"], "shape_editor")
        self.assertEqual(root["progid"], "SectionMaker.XMLGrid.1")
        self.assertEqual(root["executables"], ["ShapeEditor.exe"])
        self.assertEqual(root["design_object_member"], "")
        self.assertEqual(result["types"][0]["name"], "IXMLGrid")
        self.assertFalse(result["types"][0]["reachability"]["operationally_validated"])

    def test_unknown_graph_is_not_labelled_unreachable(self):
        owner = Info("IApplication", funcs=[function("Child", returns=(29, 999))])
        owner.attr.iid = ni.session.MODULES["modeler"].application_iid
        orphan = Info("IOrphan", funcs=[function("Unknown")])
        result = ni.inspect_library(Library([owner, orphan]), "modeler")
        self.assertFalse(result["complete"])
        self.assertEqual(result["types"][1]["reachability"]["status"], "reachability_unknown")

    def test_type_failure_and_enum_entry_failure_keep_declared_slots(self):
        enum = Info("BrokenEnum", 0, variables=[(1, "Good", 1, 3),
                                             RuntimeError("enum denied")])
        root = Info("IApplication", funcs=[
            function("Configure", args=[((29, 901), 1, None)])],
                    references={901: enum})
        root.attr.iid = ni.session.MODULES["modeler"].application_iid
        library = Library([root, enum])
        native_get = library.GetTypeInfo

        def get_info(index):
            if index == 1:
                raise RuntimeError("type denied")
            return native_get(index)

        library.GetTypeInfo = get_info
        result = ni.inspect_library(library, "modeler")
        self.assertFalse(result["complete"])
        self.assertEqual(result["types"][0]["type_index"], 1)
        self.assertEqual(result["types"][0]["error"]["message"], "type denied")
        enum_row = next(row for row in result["types"] if row.get("name") == "BrokenEnum")
        self.assertEqual(len(enum_row["variables"]), 2)
        self.assertEqual(enum_row["variables"][1]["index"], 1)
        root_row = next(row for row in result["types"] if row.get("name") == "IApplication")
        self.assertEqual(len(root_row["members"][0]["arguments"][0]["type"]["values"]), 1)
        self.assertEqual(len(root_row["members"][0]["arguments"][0]["type"]["errors"]), 1)

    def test_get_tool_preserves_metadata_error_and_valid_return_contract(self):
        snapshot = self.snapshot(self.fixture()[0])
        with patch.object(ni, "get_inventory", return_value=snapshot):
            row = json.loads(ni.get_native_api_member("modeler", "IChild", "Value", 2))
            self.assertEqual(row["dispid"], 10)
            self.assertEqual(row["kind"], "property_get")
            row = ni.find_member("modeler", "IChild", "Value", 2, inventory=snapshot)
            row["errors"].append({"code": "metadata_error", "operation": "native descriptor"})
            with self.assertRaises(MaxsurfCOMError):
                ni.get_native_api_member("modeler", "IChild", "Value", 2)

    def test_library_identity_mismatch_is_not_complete(self):
        library, _, _ = self.fixture()
        with patch.object(ni, "_registered_sources",
                          return_value=[{"module": "modeler", "path": "installed.exe"}]), \
             patch.object(ni, "_source_identity", return_value={"sha256": "file"}), \
             patch.object(ni.pythoncom, "LoadTypeLib", return_value=library):
            snapshot = ni.get_inventory("modeler")
        self.assertFalse(snapshot["complete"])
        self.assertEqual(snapshot["libraries"][0]["errors"][-1]["code"],
                         "library_identity_mismatch")

    def test_coverage_reconciliation_keeps_open_and_unknown_rows(self):
        snapshot = self.snapshot(self.fixture()[0])
        members = [row for row in snapshot["entries"] if row["entry_kind"] == "member"]
        ledger = [{"key": members[0]["key"], "status": "unsupported",
                   "blocker": "requires marshaling", "evidence": "native descriptor"}]
        result = ni.reconcile_coverage(snapshot, ledger)
        self.assertFalse(result["complete"])
        self.assertTrue(result["unresolved_keys"])
        self.assertEqual(result["totals"]["uninvestigated"], 4)
        ledger.append({"key": "absent-native-key", "status": "uninvestigated"})
        result = ni.reconcile_coverage(snapshot, ledger)
        self.assertEqual(result["unknown_keys"], ["absent-native-key"])
        with self.assertRaises(MaxsurfValidationError):
            ni.reconcile_coverage(snapshot, [{"key": members[0]["key"], "status": "unsupported"}])

    def test_every_tool_validates_inputs_with_typed_errors(self):
        invalid = [
            {"module": "unknown"}, {"offset": True}, {"offset": -1},
            {"limit": 0}, {"limit": 201}, {"limit": 2.5},
            {"kind": "unknown"}, {"inventory_digest": 5},
        ]
        with patch.object(ni, "get_inventory") as get:
            for kwargs in invalid:
                with self.subTest(kwargs=kwargs), self.assertRaises(MaxsurfValidationError):
                    ni.discover_native_api(**kwargs)
            get.assert_not_called()
        with self.assertRaises(MaxsurfValidationError):
            ni.get_native_api_member("modeler", "", "Value")
        with self.assertRaises(MaxsurfValidationError):
            ni.get_native_api_member("modeler", "IChild", "Value", True)

    def test_tools_register_through_existing_read_only_adapter(self):
        registered = []

        class MCP:
            def tool(self, *, annotations):
                def decorate(adapter):
                    registered.append(SimpleNamespace(
                        name=adapter.__name__, adapter=adapter, annotations=annotations))
                    return adapter
                return decorate

        names = ni.register_tools(MCP())
        self.assertEqual(names, ["discover_native_api", "get_native_api_member"])
        self.assertEqual([tool.name for tool in registered], names)
        for tool in registered:
            self.assertTrue(tool.annotations.readOnlyHint)
            self.assertFalse(tool.annotations.destructiveHint)
            self.assertEqual(tool.adapter.maxsurf_classification, ni.Classification.READ)

    def test_metadata_load_failure_is_explicit_and_does_not_activate(self):
        with patch.object(ni, "_registered_sources",
                          return_value=[{"module": "modeler", "path": "installed.exe"}]), \
             patch.object(ni, "_source_identity", return_value={"sha256": "file"}), \
             patch.object(ni.pythoncom, "LoadTypeLib", side_effect=RuntimeError("bad resource")):
            result = ni.get_inventory("modeler")
        self.assertFalse(result["complete"])
        self.assertEqual(result["libraries"][0]["errors"][0]["message"], "bad resource")
        self.assertFalse(result["activated_applications"])

    def test_full_path_loader_and_digest_are_stable(self):
        library, _, _ = self.fixture()
        with patch.object(ni, "_registered_sources",
                          return_value=[{"module": "modeler", "path": "installed.exe"}]), \
             patch.object(ni, "_source_identity", return_value={"sha256": "file"}), \
             patch.object(ni.pythoncom, "LoadTypeLib", return_value=library) as loader, \
             patch.dict(ni.LIBRARIES, {"modeler": "{LIBRARY}"}):
            first = ni.get_inventory("modeler")
            second = ni.get_inventory("modeler")
        self.assertEqual(first["inventory_digest"], second["inventory_digest"])
        loader.assert_called_with(str(Path("installed.exe").resolve()))


if __name__ == "__main__":
    unittest.main()
