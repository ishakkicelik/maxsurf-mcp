from __future__ import annotations

from contextlib import asynccontextmanager
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import anyio

from scripts import smoke_native_modeler as smoke


class FakeResult:
    isError = False

    def __init__(self, payload):
        self.content = [SimpleNamespace(type="text", text=json.dumps(payload))]

    def model_dump(self, **kwargs):
        return {"payload": json.loads(self.content[0].text)}


class FakeSession:
    """Only implement the MCP calls the smoke actually makes; never import COM."""

    def __init__(self, fixture, fail=None, cleanup_fail=False, drift=None,
                 populated=False):
        self.fixture = fixture
        self.path = str(fixture)
        self.fail = fail
        self.cleanup_fail = cleanup_fail
        self.drift = drift
        self.calls = []
        self.created = False
        self.restored = False
        self.populated = populated
        self.mutation_paths = []
        self.markers = {"truncated": False, "markers": [{"name": "keep", "offset": 3}]}
        self.grids = {"grids": {"sections": {
            "truncated": False, "lines": [{"label": "keep", "position": 7}],
        }}}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def initialize(self):
        return SimpleNamespace(serverInfo=SimpleNamespace(
            model_dump=lambda **kwargs: {"name": "fake"}))

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == self.fail:
            raise RuntimeError("injected failure")
        payload = {}
        if name == "connect_maxsurf":
            payload = {"session": {"design_path": self.path, "module": "modeler",
                                   "progid": "fixture", "clsid": "fixture", "com_host_pid": 17}}
        elif name == "list_surfaces":
            payload = {"surfaces": (
                [{"index": 1, "name": "User test ship"}] if self.populated else []
            ) + ([{"index": 2 if self.populated else 1}] if self.created else [])}
        elif name == "list_meshes":
            payload = {"count": int(self.populated) + int(self.created)}
        elif name == "list_markers":
            payload = copy.deepcopy(self.markers)
            if self.restored and self.drift == "markers":
                payload["markers"][0]["offset"] = 4
        elif name == "list_modeler_grid":
            payload = copy.deepcopy(self.grids)
            if self.restored and self.drift == "grids":
                payload["grids"]["sections"]["lines"][0]["position"] = 8
        elif name == "get_frame_of_reference":
            payload = {"baseline": 2 if self.restored and self.drift == "frame" else 1}
        elif name == "get_mesh_enums":
            payload = {"msMeshDiagonalLayoutType": {"diagonal": 1}}
        elif name == "add_quadrilateral_surface":
            self.mutation_paths.append(self.path)
            self.created = True
            payload = {"created_index": 2 if self.populated else 1}
        elif name == "get_control_net":
            longitudinal = 12.5 if self.populated and arguments["index"] == 1 else 0
            if self.restored and self.drift == "geometry":
                longitudinal += 1
            payload = {"points": [{"row": 1, "column": 1, "longitudinal": longitudinal,
                                   "offset": 0, "vertical": 0}]}
        elif name == "mesh_surface_parametric":
            payload = {"mesh": {"index": 2 if self.populated else 1}}
        elif name == "get_mesh_statistics":
            payload = {"mesh": {"index": arguments["index"]}, "statistics": {"Area": 14.5}}
        elif name == "save_design":
            self.path = arguments["path"]
        elif name == "open_design":
            if arguments["path"] == str(self.fixture):
                if self.cleanup_fail:
                    raise RuntimeError("injected cleanup failure")
                self.created = False
                self.restored = True
            self.path = arguments["path"]
        return FakeResult(payload)


class NativeSmokeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = self.root / "fixture.msd"
        self.fixture.write_bytes(b"saved disposable fixture")
        self.session = FakeSession(self.fixture)
        self.parameters = None

    def run_smoke(self, fixture=True, confirmed=True):
        @asynccontextmanager
        async def transport(parameters):
            self.parameters = parameters
            yield object(), object()

        with mock.patch.object(smoke, "stdio_client", transport), \
                mock.patch.object(smoke, "ClientSession", return_value=self.session):
            anyio.run(smoke.run, "python", "server.py", self.root / "runs",
                      self.fixture if fixture else None, confirmed)

    def evidence(self):
        paths = list((self.root / "runs").glob("native-smoke-*/evidence.json"))
        self.assertEqual(len(paths), 1)
        return json.loads(paths[0].read_text(encoding="utf-8"))

    def test_unsaved_empty_document_refused_before_connection(self):
        with self.assertRaisesRegex(RuntimeError, "Restoration cannot be guaranteed"):
            self.run_smoke(fixture=False)
        self.assertEqual(self.session.calls, [])
        self.assertEqual(self.evidence()["cleanup"]["status"], "not_needed")

    def test_fixture_requires_explicit_saved_state_attestation(self):
        with self.assertRaisesRegex(RuntimeError, "Restoration cannot be guaranteed"):
            self.run_smoke(confirmed=False)
        self.assertEqual(self.session.calls, [])

    def test_other_document_refused_without_writes(self):
        self.session.path = str(self.root / "user.msd")
        with self.assertRaisesRegex(RuntimeError, "identity"):
            self.run_smoke()
        self.assertFalse(self.session.created)
        self.assertFalse(any(name in ("open_design", "save_design",
                                      "add_quadrilateral_surface")
                             for name, _ in self.session.calls))

    def test_success_restores_markers_grids_frame_identity_and_isolates_roots(self):
        self.run_smoke()
        report = self.evidence()
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["cleanup"]["status"], "restored")
        self.assertEqual(report["initial_state"], report["final_state"])
        self.assertTrue(report["final_state"]["list_markers"]["markers"])
        target = Path(report["workspace"])
        for name in ("MAXSURF_MCP_RUNTIME_DIR", "MAXSURF_MCP_AUDIT_DIR",
                     "MAXSURF_MCP_GEN_PY_DIR"):
            self.assertTrue(Path(self.parameters.env[name]).is_relative_to(target))
        self.assertTrue(Path(report["fixture"]["recovery_copy"]).is_file())
        for name, arguments in self.session.calls:
            if name == "open_design":
                self.assertIs(arguments["save_current"], False)
            if name == "save_design":
                self.assertNotEqual(arguments["path"], str(self.fixture))

    def test_failure_immediately_after_creation_still_restores(self):
        self.session.fail = "get_control_net"
        with self.assertRaisesRegex(RuntimeError, "injected failure"):
            self.run_smoke()
        report = self.evidence()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["cleanup"]["status"], "restored")
        self.assertEqual(report["initial_state"], report["final_state"])

    def test_cleanup_failure_retains_actionable_evidence(self):
        self.session.fail = "get_control_net"
        self.session.cleanup_fail = True
        with self.assertRaisesRegex(RuntimeError, "cleanup failure"):
            self.run_smoke()
        report = self.evidence()
        self.assertEqual(report["cleanup"]["status"], "manual_recovery_required")
        self.assertIn(str(self.fixture), report["cleanup"]["manual_recovery"])
        self.assertIsNotNone(report["final_state"])
        self.assertTrue(Path(report["fixture"]["recovery_copy"]).is_file())

    def test_non_geometry_restoration_mismatch_is_failure(self):
        for drift in ("markers", "grids", "frame"):
            with self.subTest(drift=drift):
                self.session = FakeSession(self.fixture, drift=drift)
                with self.assertRaisesRegex(RuntimeError, "differ"):
                    self.run_smoke()
        reports = [json.loads(path.read_text(encoding="utf-8"))
                   for path in (self.root / "runs").glob("*/evidence.json")]
        self.assertEqual(len(reports), 3)
        self.assertTrue(all(report["cleanup"]["status"] == "manual_recovery_required"
                            for report in reports))

    def test_truncated_snapshot_refuses_before_mutation(self):
        self.session.markers["truncated"] = True
        with self.assertRaisesRegex(RuntimeError, "Incomplete"):
            self.run_smoke()
        self.assertFalse(self.session.created)

    def test_failed_marker_getter_refuses_before_mutation(self):
        self.session.markers["markers"][0]["offset_status"] = "failed"
        with self.assertRaisesRegex(RuntimeError, "Incomplete"):
            self.run_smoke()
        self.assertFalse(self.session.created)

    def test_user_document_swap_is_not_discarded_during_cleanup(self):
        original_call = self.session.call_tool

        async def swap_after_creation(name, arguments):
            if name == "get_control_net":
                self.session.path = str(self.root / "user.msd")
                raise RuntimeError("user changed document")
            return await original_call(name, arguments)

        with mock.patch.object(self.session, "call_tool", swap_after_creation):
            with self.assertRaisesRegex(RuntimeError, "refusing to discard"):
                self.run_smoke()
        opens = [arguments["path"] for name, arguments in self.session.calls
                 if name == "open_design"]
        self.assertEqual(len(opens), 1)
        self.assertEqual(Path(opens[0]).name, "working-fixture.msd")
        self.assertEqual(self.evidence()["cleanup"]["status"], "manual_recovery_required")

    def test_populated_ship_is_copied_and_original_geometry_restored(self):
        self.session.populated = True
        self.run_smoke()
        report = self.evidence()
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["initial_state"], report["final_state"])
        self.assertEqual(report["initial_state"]["control_nets"][0]["points"][0][
            "longitudinal"], 12.5)
        self.assertTrue(report["initial_state"]["mesh_statistics"])
        working = report["fixture"]["working_copy"]
        self.assertEqual(self.session.mutation_paths, [working])
        self.assertEqual(Path(working).read_bytes(), self.fixture.read_bytes())
        self.assertEqual(self.fixture.read_bytes(), b"saved disposable fixture")
        self.assertEqual(self.session.path, str(self.fixture))
        control_writes = [arguments["index"] for name, arguments in self.session.calls
                          if name == "set_control_point"]
        self.assertEqual(control_writes, [2])

    def test_populated_geometry_restore_mismatch_fails(self):
        self.session.populated = True
        self.session.drift = "geometry"
        with self.assertRaisesRegex(RuntimeError, "differ"):
            self.run_smoke()
        self.assertEqual(self.evidence()["cleanup"]["status"], "manual_recovery_required")

    def test_populated_failure_after_creation_restores_original_ship(self):
        self.session.populated = True
        self.session.fail = "set_control_point"
        with self.assertRaisesRegex(RuntimeError, "injected failure"):
            self.run_smoke()
        report = self.evidence()
        self.assertEqual(report["cleanup"]["status"], "restored")
        self.assertEqual(report["initial_state"], report["final_state"])
        self.assertEqual(self.session.mutation_paths, [report["fixture"]["working_copy"]])

    def test_working_copy_mismatch_refuses_geometry_edits_and_restores(self):
        original_call = self.session.call_tool

        async def changed_copy(name, arguments):
            if name == "get_frame_of_reference" and Path(self.session.path).name == "working-fixture.msd":
                return FakeResult({"baseline": 99})
            return await original_call(name, arguments)

        with mock.patch.object(self.session, "call_tool", changed_copy):
            with self.assertRaisesRegex(RuntimeError, "copy differs"):
                self.run_smoke()
        self.assertFalse(self.session.created)
        self.assertEqual(self.evidence()["cleanup"]["status"], "restored")

    def test_working_copy_open_failure_still_preserves_original(self):
        original_call = self.session.call_tool

        async def fail_copy_open(name, arguments):
            if name == "open_design" and Path(arguments["path"]).name == "working-fixture.msd":
                raise RuntimeError("copy open failed")
            return await original_call(name, arguments)

        with mock.patch.object(self.session, "call_tool", fail_copy_open):
            with self.assertRaisesRegex(RuntimeError, "copy open failed"):
                self.run_smoke()
        report = self.evidence()
        self.assertEqual(report["cleanup"]["status"], "restored")
        self.assertEqual(report["initial_state"], report["final_state"])


if __name__ == "__main__":
    unittest.main()
