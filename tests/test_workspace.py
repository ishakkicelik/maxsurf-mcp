"""Tests for the filesystem sandbox that guards every design file operation."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from maxsurf_mcp import config, workspace
from maxsurf_mcp.errors import MaxsurfSafetyError, MaxsurfValidationError

from .support import IsolatedEnvTestCase


class WorkspaceRootTests(IsolatedEnvTestCase):
    def test_defaults_to_the_repository_runtime_directory(self):
        os.environ.pop(config.WORKSPACE_ROOTS_VAR, None)
        roots = config.workspace_roots()
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0], config.runtime_root().resolve())

    def test_multiple_roots_are_supported(self):
        second = self.tmp / "second"
        second.mkdir()
        os.environ[config.WORKSPACE_ROOTS_VAR] = os.pathsep.join(
            [str(self.workspace_root), str(second)]
        )
        roots = config.workspace_roots()
        self.assertEqual(
            {root.name for root in roots}, {"workspace", "second"}
        )


class ResolveExistingTests(IsolatedEnvTestCase):
    def test_accepts_an_existing_file_inside_a_root(self):
        created = self.workspace_file("hull.msd")
        resolved = workspace.resolve_existing(
            str(created), purpose="test",
            allowed_suffixes=workspace.DESIGN_SUFFIXES,
        )
        self.assertEqual(resolved, created.resolve())

    def test_rejects_a_path_outside_every_root(self):
        outside = self.tmp / "outside.msd"
        outside.write_text("x", encoding="utf-8")
        with self.assertRaises(MaxsurfSafetyError) as caught:
            workspace.resolve_existing(str(outside), purpose="test")
        self.assertIn("outside every configured workspace root",
                      str(caught.exception))

    def test_rejects_parent_directory_traversal(self):
        sneaky = str(self.workspace_root / ".." / "outside.msd")
        with self.assertRaises(MaxsurfSafetyError) as caught:
            workspace.resolve_existing(sneaky, purpose="test")
        self.assertIn("parent-directory traversal", str(caught.exception))

    def test_rejects_a_relative_path(self):
        with self.assertRaises(MaxsurfSafetyError):
            workspace.resolve_existing("hull.msd", purpose="test")

    def test_rejects_an_empty_path(self):
        with self.assertRaises(MaxsurfValidationError):
            workspace.resolve_existing("   ", purpose="test")

    def test_rejects_a_null_byte(self):
        with self.assertRaises(MaxsurfSafetyError):
            workspace.resolve_existing(
                str(self.workspace_root / "a\x00b.msd"), purpose="test"
            )

    def test_rejects_a_disallowed_extension(self):
        created = self.workspace_file("notes.txt")
        with self.assertRaises(MaxsurfSafetyError) as caught:
            workspace.resolve_existing(
                str(created), purpose="test",
                allowed_suffixes=workspace.DESIGN_SUFFIXES,
            )
        self.assertIn("extension is not allowed", str(caught.exception))

    def test_rejects_a_missing_file(self):
        with self.assertRaises(MaxsurfValidationError):
            workspace.resolve_existing(
                str(self.workspace_path("absent.msd")), purpose="test"
            )

    def test_rejects_a_directory(self):
        directory = self.workspace_root / "sub"
        directory.mkdir()
        with self.assertRaises(MaxsurfValidationError):
            workspace.resolve_existing(str(directory), purpose="test")


class ResolveTargetTests(IsolatedEnvTestCase):
    def test_accepts_a_new_file_inside_a_root(self):
        target = self.workspace_path("new.msd")
        resolved = workspace.resolve_target(
            str(target), purpose="test", overwrite=False,
            allowed_suffixes=workspace.DESIGN_SUFFIXES,
        )
        self.assertEqual(resolved, Path(os.path.normpath(str(target))))

    def test_existing_file_requires_explicit_overwrite(self):
        existing = self.workspace_file("hull.msd")
        with self.assertRaises(MaxsurfSafetyError) as caught:
            workspace.resolve_target(
                str(existing), purpose="test", overwrite=False
            )
        self.assertIn("overwrite=True", str(caught.exception))

    def test_existing_file_is_accepted_with_overwrite(self):
        existing = self.workspace_file("hull.msd")
        resolved = workspace.resolve_target(
            str(existing), purpose="test", overwrite=True
        )
        self.assertEqual(resolved, existing.resolve())

    def test_creates_missing_parent_directories_inside_the_root(self):
        target = self.workspace_path("variants/v1/hull.msd")
        workspace.resolve_target(str(target), purpose="test", overwrite=False)
        self.assertTrue(target.parent.is_dir())

    def test_rejects_a_target_outside_every_root(self):
        with self.assertRaises(MaxsurfSafetyError):
            workspace.resolve_target(
                str(self.tmp / "escape.msd"), purpose="test", overwrite=True
            )

    def test_rejects_traversal_in_a_write_target(self):
        with self.assertRaises(MaxsurfSafetyError):
            workspace.resolve_target(
                str(self.workspace_root / ".." / "escape.msd"),
                purpose="test", overwrite=True,
            )

    def test_rejects_an_existing_directory_as_a_target(self):
        directory = self.workspace_root / "sub"
        directory.mkdir()
        with self.assertRaises(MaxsurfValidationError):
            workspace.resolve_target(
                str(directory), purpose="test", overwrite=True
            )

    @unittest.skipUnless(os.name == "nt", "Windows path semantics")
    def test_containment_is_case_insensitive_on_windows(self):
        created = self.workspace_file("hull.msd")
        shouted = str(created).upper()
        resolved = workspace.resolve_existing(shouted, purpose="test")
        self.assertTrue(resolved.exists())


if __name__ == "__main__":
    unittest.main()
