"""Tests for the hardened COM path resolver.

The audit demonstrated that the previous resolver let a caller-supplied path
step out of the COM object graph into the interpreter and reach ``os.system``.
These tests pin that door shut and prove that legitimate Maxsurf collection
access still works.
"""

from __future__ import annotations

import unittest

from maxsurf_mcp import objectpath
from maxsurf_mcp.errors import (
    MaxsurfCOMError,
    MaxsurfError,
    MaxsurfSafetyError,
    MaxsurfValidationError,
)

from .support import (
    ComCollectionFake,
    ComFake,
    SubscriptOnlyCollectionFake,
)


def _design() -> ComFake:
    """A small COM graph: Application -> Design -> Surfaces -> Surface."""
    hull = ComFake(Name="Hull", Type=1, Visible=True)
    deck = ComFake(Name="Deck", Type=2, Visible=False)
    surfaces = ComCollectionFake([hull, deck], by_name={"Deck": deck})
    design = ComFake(Surfaces=surfaces, Name="demo")
    return ComFake(Design=design, Version="25.00.02.339")


class ExploitRejectionTests(unittest.TestCase):
    """Every one of these paths resolved successfully before Phase 0."""

    def test_rejects_the_exact_audited_exploit_chain(self):
        app = _design()
        exploit = "__class__.__init__.__globals__[sys].modules[os].system"
        with self.assertRaises(MaxsurfSafetyError) as caught:
            objectpath.resolve(app, exploit)
        self.assertIn("__class__", str(caught.exception))

        # Every prefix of the chain is refused too, so the exploit cannot be
        # rebuilt one hop at a time across several calls.
        for hops in range(1, len(exploit.split(".")) + 1):
            prefix = ".".join(exploit.split(".")[:hops])
            with self.subTest(prefix=prefix):
                with self.assertRaises(MaxsurfSafetyError):
                    objectpath.resolve(app, prefix)

    def test_rejects_dunder_components(self):
        app = _design()
        for path in (
            "__class__",
            "__dict__",
            "__globals__",
            "__class__.__init__",
            "__class__.__init__.__globals__",
            "Design.__class__",
            "Design.Surfaces.__class__.__mro__",
        ):
            with self.subTest(path=path):
                with self.assertRaises(MaxsurfSafetyError):
                    objectpath.resolve(app, path)

    def test_rejects_single_underscore_components(self):
        app = _design()
        for path in ("_oleobj_", "Design._oleobj_", "_NewEnum"):
            with self.subTest(path=path):
                with self.assertRaises(MaxsurfSafetyError):
                    objectpath.resolve(app, path)

    def test_rejects_dunder_inside_a_collection_index(self):
        app = _design()
        with self.assertRaises(MaxsurfSafetyError):
            objectpath.resolve(app, "Design.Surfaces[__class__]")

    def test_rejects_pywin32_infrastructure_members(self):
        app = _design()
        for member in ("QueryInterface", "Invoke", "InvokeTypes",
                       "SetProperty", "GetIDsOfNames", "Release"):
            with self.subTest(member=member):
                with self.assertRaises(MaxsurfSafetyError):
                    objectpath.resolve(app, member)

    def test_rejects_traversal_into_a_python_object(self):
        """A member that returns a non-COM Python object terminates traversal."""
        class Escape:
            payload = "reachable"

        app = ComFake(Helper=Escape())
        with self.assertRaises(MaxsurfSafetyError) as caught:
            objectpath.resolve(app, "Helper")
        self.assertIn("outside the COM object graph", str(caught.exception))

    def test_rejects_traversal_into_a_module_or_dict(self):
        import os as os_module

        app = ComFake(Mod=os_module, Map={"a": 1})
        for path in ("Mod", "Map"):
            with self.subTest(path=path):
                with self.assertRaises(MaxsurfSafetyError):
                    objectpath.resolve(app, path)

    def test_rejects_a_non_com_root(self):
        with self.assertRaises(MaxsurfSafetyError):
            objectpath.resolve(object(), "Design")

    def test_rejects_a_value_in_a_non_final_position(self):
        app = _design()
        with self.assertRaises(MaxsurfSafetyError) as caught:
            objectpath.resolve(app, "Version.Anything")
        self.assertIn("final component", str(caught.exception))

    def test_rejects_indexing_a_scalar(self):
        app = _design()
        with self.assertRaises(MaxsurfSafetyError):
            objectpath.resolve(app, "Version[1]")

    def test_rejects_nested_and_unbalanced_indexing(self):
        app = _design()
        for path in ("Design.Surfaces[1[2]]", "Design.Surfaces(1", "Design.Surfaces[1"):
            with self.subTest(path=path):
                with self.assertRaises(MaxsurfSafetyError):
                    objectpath.resolve(app, path)

    def test_rejects_empty_index(self):
        app = _design()
        with self.assertRaises(MaxsurfValidationError):
            objectpath.resolve(app, "Design.Surfaces()")


class LegitimateTraversalTests(unittest.TestCase):
    """Maxsurf access patterns that must keep working."""

    def test_resolves_the_root_when_the_path_is_empty(self):
        app = _design()
        self.assertIs(objectpath.resolve(app, ""), app)

    def test_resolves_nested_objects(self):
        app = _design()
        surfaces = objectpath.resolve(app, "Design.Surfaces")
        self.assertEqual(surfaces.Count, 2)

    def test_resolves_call_style_collection_index(self):
        app = _design()
        self.assertEqual(
            objectpath.resolve(app, "Design.Surfaces(1).Name"), "Hull"
        )

    def test_resolves_bracket_style_collection_index(self):
        app = _design()
        self.assertEqual(
            objectpath.resolve(app, "Design.Surfaces[2].Name"), "Deck"
        )

    def test_resolves_string_keyed_collection_access(self):
        app = _design()
        self.assertEqual(
            objectpath.resolve(app, 'Design.Surfaces("Deck").Name'), "Deck"
        )

    def test_resolves_subscript_only_collections(self):
        hull = ComFake(Name="Hull")
        app = ComFake(Design=ComFake(
            Surfaces=SubscriptOnlyCollectionFake([hull])
        ))
        self.assertEqual(
            objectpath.resolve(app, "Design.Surfaces[1].Name"), "Hull"
        )

    def test_resolves_scalar_leaves_and_counts(self):
        app = _design()
        self.assertEqual(objectpath.resolve(app, "Design.Surfaces.Count"), 2)
        self.assertTrue(objectpath.resolve(app, "Design.Surfaces(1).Visible"))

    def test_resolves_com_out_parameter_tuples(self):
        surface = ComFake(Limits=(4, 6))
        app = ComFake(Design=ComFake(Surface=surface))
        self.assertEqual(
            objectpath.resolve(app, "Design.Surface.Limits"), (4, 6)
        )

    def test_allows_interior_underscores_in_member_names(self):
        """Maxsurf really exposes members such as CONNECT_SetUndefinedProject."""
        app = ComFake(Design=ComFake(CONNECT_Flag=7))
        self.assertEqual(
            objectpath.resolve(app, "Design.CONNECT_Flag"), 7
        )


class MemberErrorTests(unittest.TestCase):
    def test_missing_member_is_a_validation_error(self):
        app = _design()
        with self.assertRaises(MaxsurfValidationError):
            objectpath.resolve(app, "Design.NoSuchMember")

    def test_failing_getter_is_a_com_error(self):
        class Failing(ComFake):
            @property
            def Boom(self):
                raise OSError("COM getter exploded")

        app = ComFake(Design=Failing())
        with self.assertRaises(MaxsurfCOMError):
            objectpath.resolve(app, "Design.Boom")

    def test_out_of_range_index_is_a_com_error(self):
        app = _design()
        with self.assertRaises(MaxsurfCOMError):
            objectpath.resolve(app, "Design.Surfaces(9).Name")


class ResolveParentTests(unittest.TestCase):
    def test_returns_com_parent_and_validated_leaf(self):
        app = _design()
        parent, leaf = objectpath.resolve_parent(app, "Design.Surfaces.Count")
        self.assertEqual(leaf, "Count")
        self.assertEqual(parent.Count, 2)

    def test_refuses_a_dunder_leaf(self):
        app = _design()
        with self.assertRaises(MaxsurfSafetyError):
            objectpath.resolve_parent(app, "Design.__class__")

    def test_refuses_a_blocked_infrastructure_leaf(self):
        app = _design()
        with self.assertRaises(MaxsurfSafetyError):
            objectpath.resolve_parent(app, "Design.SetProperty")

    def test_refuses_an_indexed_leaf_as_a_write_target(self):
        app = _design()
        with self.assertRaises(MaxsurfSafetyError):
            objectpath.resolve_parent(app, "Design.Surfaces(1)")

    def test_refuses_an_empty_path(self):
        app = _design()
        with self.assertRaises(MaxsurfValidationError):
            objectpath.resolve_parent(app, "")

    def test_refuses_a_scalar_parent(self):
        app = _design()
        with self.assertRaises(MaxsurfError):
            objectpath.resolve_parent(app, "Version.Length")


class ClassificationTests(unittest.TestCase):
    def test_classifies_com_scalar_sequence_and_forbidden(self):
        self.assertEqual(objectpath.classify(ComFake()), "com")
        self.assertEqual(objectpath.classify(3), "scalar")
        self.assertEqual(objectpath.classify("x"), "scalar")
        self.assertEqual(objectpath.classify(None), "scalar")
        self.assertEqual(objectpath.classify((1.0, 2.0)), "sequence")
        self.assertEqual(objectpath.classify({"a": 1}), "forbidden")
        self.assertEqual(objectpath.classify(objectpath), "forbidden")
        self.assertEqual(objectpath.classify(ComFake), "forbidden")

    def test_a_type_is_never_a_com_object(self):
        self.assertFalse(objectpath.is_com_object(ComFake))
        self.assertTrue(objectpath.is_com_object(ComFake()))

    def test_hostile_getattr_is_not_a_com_object(self):
        class Hostile:
            def __getattr__(self, name):
                raise RuntimeError("nope")

        self.assertFalse(objectpath.is_com_object(Hostile()))


if __name__ == "__main__":
    unittest.main()
