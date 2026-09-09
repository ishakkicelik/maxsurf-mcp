"""Tests for the design file lifecycle tools and their sandbox guards."""

from __future__ import annotations

import json
import types
import unittest
from unittest import mock

import server
from maxsurf_mcp.audit import Classification, Outcome
from maxsurf_mcp.errors import MaxsurfSafetyError, MaxsurfValidationError

from .support import IsolatedEnvTestCase


class _DesignRecorder:
    def __init__(self):
        self.calls: list[tuple[object, ...]] = []

    def Open(self, path: str, merge: bool, save_current: bool) -> None:
        self.calls.append(("Open", path, merge, save_current))

    def Save(self) -> None:
        self.calls.append(("Save",))

    def SaveAs(self, path: str, overwrite: bool) -> None:
        self.calls.append(("SaveAs", path, overwrite))


class _ManagedSession:
    def __init__(self, design: _DesignRecorder):
        self.design = design
        self.design_path = None
        self.design_path_status = "empty"
        self.design_swap_detection = "unavailable"
        self.stale_detail = None

    def refresh_design_identity(self) -> None:
        call = self.design.calls[-1]
        self.design_path = str(call[1])
        self.design_path_status = "read"
        self.design_swap_detection = "active"

    def mark_stale(self, detail: str) -> None:
        self.stale_detail = detail


class DesignLifecycleTestCase(IsolatedEnvTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.design = _DesignRecorder()
        patcher = mock.patch.object(
            server.com, "connect",
            return_value=types.SimpleNamespace(Design=self.design),
        )
        self.connect = patcher.start()
        self.addCleanup(patcher.stop)
        self.session = _ManagedSession(self.design)
        session_patcher = mock.patch.object(
            server.com, "session_for", return_value=self.session,
        )
        session_patcher.start()
        self.addCleanup(session_patcher.stop)


class OpenDesignTests(DesignLifecycleTestCase):
    def test_opens_a_design_inside_the_sandbox(self):
        existing = self.workspace_file("hull.msd")
        payload = json.loads(
            server.open_design(str(existing), merge=True, save_current=True)
        )
        self.assertEqual(
            self.design.calls, [("Open", str(existing.resolve()), True, True)]
        )
        self.assertEqual(payload["via"], "Design.Open")
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(payload["session_identity"]["status"], "accepted")
        self.assertEqual(self.session.design_path, str(existing.resolve()))

    def test_warns_when_unsaved_changes_are_discarded(self):
        existing = self.workspace_file("hull.msd")
        payload = json.loads(server.open_design(str(existing), save_current=False, confirm_discard=True))
        self.assertEqual(len(payload["warnings"]), 1)
        self.assertIn("discarded", payload["warnings"][0])

    def test_refuses_a_path_outside_the_sandbox(self):
        outside = self.tmp / "outside.msd"
        outside.write_text("x", encoding="utf-8")
        with self.assertRaises(MaxsurfSafetyError):
            server.open_design(str(outside))
        self.assertEqual(self.design.calls, [])

    def test_refuses_parent_directory_traversal(self):
        with self.assertRaises(MaxsurfSafetyError):
            server.open_design(str(self.workspace_root / ".." / "outside.msd"))
        self.assertEqual(self.design.calls, [])

    def test_refuses_a_relative_path(self):
        with self.assertRaises(MaxsurfSafetyError):
            server.open_design("hull.msd")
        self.assertEqual(self.design.calls, [])

    def test_refuses_a_non_design_extension(self):
        other = self.workspace_file("hull.txt")
        with self.assertRaises(MaxsurfSafetyError):
            server.open_design(str(other))
        self.assertEqual(self.design.calls, [])

    def test_refuses_a_missing_file(self):
        with self.assertRaises(MaxsurfValidationError):
            server.open_design(str(self.workspace_path("absent.msd")))
        self.assertEqual(self.design.calls, [])

    def test_records_the_design_path_in_the_audit_log(self):
        existing = self.workspace_file("hull.msd")
        server.open_design(str(existing))
        records = self.records_for("open_design")
        self.assertEqual([r["stage"] for r in records], ["intent", "complete"])
        self.assertEqual(
            records[-1]["design_path"], str(existing.resolve())
        )
        self.assertEqual(
            records[-1]["classification"], Classification.WRITE_STATE
        )

    def test_a_refused_open_is_audited_as_a_failure(self):
        with self.assertRaises(MaxsurfSafetyError):
            server.open_design("relative.msd")
        self.assertEqual(
            self.records_for("open_design")[-1]["outcome"], Outcome.FAILURE
        )


class SaveDesignTests(DesignLifecycleTestCase):
    def test_in_place_save_is_disabled(self):
        for empty in ("", "   "):
            with self.subTest(path=empty):
                with self.assertRaises(MaxsurfSafetyError) as caught:
                    server.save_design(empty)
                self.assertIn("in-place save is disabled", str(caught.exception))
        self.assertEqual(self.design.calls, [])

    def test_design_save_is_never_invoked(self):
        with self.assertRaises(MaxsurfSafetyError):
            server.save_design()
        self.assertNotIn(("Save",), self.design.calls)

    def test_saves_to_an_explicit_path_inside_the_sandbox(self):
        target = self.workspace_path("out.msd")
        payload = json.loads(server.save_design(str(target)))
        self.assertEqual(
            self.design.calls, [("SaveAs", str(target), False)]
        )
        self.assertEqual(payload["via"], "Design.SaveAs")
        self.assertFalse(payload["replaced_existing_file"])
        self.assertEqual(payload["session_identity"]["status"], "accepted")
        self.assertEqual(self.session.design_path, str(target))

    def test_an_unexpected_identity_after_save_is_refused(self):
        target = self.workspace_path("out.msd")

        def wrong_identity() -> None:
            self.session.design_path = str(self.workspace_path("other.msd"))
            self.session.design_path_status = "read"

        self.session.refresh_design_identity = wrong_identity
        with self.assertRaises(MaxsurfSafetyError):
            server.save_design(str(target))
        self.assertIsNotNone(self.session.stale_detail)

    def test_an_existing_file_requires_explicit_overwrite(self):
        existing = self.workspace_file("out.msd", "old")
        with self.assertRaises(MaxsurfSafetyError) as caught:
            server.save_design(str(existing))
        self.assertIn("overwrite=True", str(caught.exception))
        self.assertEqual(self.design.calls, [])

    def test_overwrite_true_is_allowed_and_reported(self):
        existing = self.workspace_file("out.msd", "old")
        payload = json.loads(server.save_design(str(existing), overwrite=True))
        self.assertEqual(
            self.design.calls, [("SaveAs", str(existing.resolve()), True)]
        )
        self.assertTrue(payload["replaced_existing_file"])

    def test_refuses_a_target_outside_the_sandbox(self):
        with self.assertRaises(MaxsurfSafetyError):
            server.save_design(str(self.tmp / "escape.msd"), overwrite=True)
        self.assertEqual(self.design.calls, [])

    def test_refuses_a_non_design_extension(self):
        with self.assertRaises(MaxsurfSafetyError):
            server.save_design(str(self.workspace_path("out.txt")))
        self.assertEqual(self.design.calls, [])

    def test_creates_missing_sandbox_subdirectories(self):
        target = self.workspace_path("variants/v1/hull.msd")
        server.save_design(str(target))
        self.assertTrue(target.parent.is_dir())

    def test_the_save_is_audited_with_intent_and_path(self):
        target = self.workspace_path("out.msd")
        server.save_design(str(target))
        records = self.records_for("save_design")
        self.assertEqual([r["stage"] for r in records], ["intent", "complete"])
        self.assertEqual(
            records[0]["classification"], Classification.WRITE_FILE
        )
        self.assertEqual(records[-1]["design_path"], str(target))


if __name__ == "__main__":
    unittest.main()
