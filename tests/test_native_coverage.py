from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import check_native_coverage as coverage
from tests import test_native_inventory
from tests.test_native_types import function


class NativeCoverageTests(unittest.TestCase):
    def snapshot(self, *, failure=False, enum=False):
        fixture = test_native_inventory.NativeInventoryTests()
        library, root, _ = fixture.fixture(failure=failure)
        if enum:
            root.funcs.append(function("_NewEnum", -4, 2, returns=13))
            root.attr.cFuncs += 1
        with patch.object(coverage.native_inventory, "_coclass_registration",
                          return_value={"registered": False}):
            return fixture.snapshot(library)

    def run_fixture(self, snapshot, output):
        with patch.object(coverage.native_inventory, "get_inventory",
                          return_value=snapshot), \
             patch.dict(coverage.native_inventory.LIBRARIES, {"modeler": "{LIBRARY}"}), \
             patch.object(coverage.native_automation, "_live_contract",
                          side_effect=AssertionError("metadata assessment activated live verification")):
            return coverage.run(output)

    def test_complete_metadata_is_admission_not_live_evidence(self):
        snapshot = self.snapshot(enum=True)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "coverage.json"
            summary = self.run_fixture(snapshot, output)
            artifact = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(summary["reconciliation_complete"])
            admitted = [row for row in artifact["members"]
                        if row["status"] == "metadata_admitted"]
            self.assertTrue(admitted)
            for row in admitted:
                self.assertTrue(row["runtime_verification_required"])
                self.assertFalse(row["live_signature_verified"])
                self.assertFalse(row["operationally_validated"])
                self.assertFalse(row["engineering_acceptance"])
                self.assertEqual(row["evidence"][0], "installed_metadata_only")
            enum = next(row for row in admitted if row["member"] == "_NewEnum")
            self.assertEqual(enum["route"], "native_call -> native_enumerate (IEnumVARIANT)")
            original = output.read_bytes()
            with self.assertRaises(FileExistsError):
                self.run_fixture(snapshot, output)
            self.assertEqual(output.read_bytes(), original)

    def test_failed_descriptor_retains_keyless_slot_and_cli_fails(self):
        snapshot = self.snapshot(failure=True)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "coverage.json"
            with patch.object(coverage.native_inventory, "get_inventory",
                              return_value=snapshot), \
                 patch.dict(coverage.native_inventory.LIBRARIES, {"modeler": "{LIBRARY}"}), \
                 patch.object(coverage.native_automation, "_live_contract",
                              side_effect=AssertionError("must remain metadata-only")), \
                 patch("sys.argv", ["check_native_coverage.py", "--output", str(output)]), \
                 contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(coverage.main(), 1)
            artifact = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(artifact["summary"]["inventory_complete"])
            self.assertFalse(artifact["summary"]["reconciliation_complete"])
            self.assertEqual(artifact["summary"]["incomplete_member_slots"], 1)
            slot = artifact["incomplete_members"][0]
            self.assertNotIn("key", slot)
            self.assertEqual(slot["index"], 2)
            self.assertEqual(slot["error"]["message"], "descriptor denied")
            self.assertIn(slot, artifact["inventory_diagnostics"])
            self.assertIn("COVERAGE_SUMMARY ", stdout.getvalue())

    def test_registered_constructor_remains_a_candidate(self):
        snapshot = self.snapshot()
        for entry in snapshot["entries"]:
            if entry["entry_kind"] == "member":
                entry["reachability"] = {
                    "status": "metadata_reachable", "operationally_validated": False,
                    "path": [{"kind": "registered_coclass"}]}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "coverage.json"
            self.run_fixture(snapshot, output)
            artifact = json.loads(output.read_text(encoding="utf-8"))
            for row in artifact["members"]:
                if row["status"] == "metadata_admitted":
                    self.assertFalse(row["operationally_validated"])
                    self.assertIn("constructor_evidence", row)
