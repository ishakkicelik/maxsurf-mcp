"""Manifest-only constructors retain application and reference ownership."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import TestCase, mock

from maxsurf_mcp import native_objects as objects
from maxsurf_mcp.errors import MaxsurfCOMError, MaxsurfSafetyError, MaxsurfValidationError


class NativeObjectTests(TestCase):
    def setUp(self):
        self.identity = {
            "guid": "{HELPER}", "library_guid": "{LIB}", "library_version": [1, 1],
        }
        self.definition = {
            "typekind": 5, "name": "Helper", "guid": "{CLASS}",
            "registration": {"registered": True},
            "implementations": [{"flags": 1, "reference": self.identity}],
        }
        self.snapshot = {"libraries": [{"types": [self.definition]}],
                         "inventory_digest": "digest"}
        self.session = SimpleNamespace(
            session_id="owned", reconnect_count=0, app=object(),
            design_path="model.msd", design_name="model",
            refresh_design_identity=mock.Mock(),
        )
        self.patchers = [
            mock.patch.object(objects.inventory, "get_inventory", return_value=self.snapshot),
            mock.patch.object(objects.com, "connect"),
            mock.patch.object(objects.com, "session_for", return_value=self.session),
            mock.patch.object(objects.pythoncom, "CoCreateInstance", return_value=object()),
            mock.patch.object(objects.automation, "_wrap_object", side_effect=lambda value: value),
            mock.patch.object(objects.automation, "_identity", return_value=(None, self.identity)),
            mock.patch.object(objects.automation, "_encode",
                              return_value={"native_handle": "owned"}),
        ]
        self.mocks = [patcher.start() for patcher in self.patchers]
        for patcher in self.patchers:
            self.addCleanup(patcher.stop)

    def call(self, **arguments):
        return objects.create_native_object.__wrapped__(
            "modeler", "Helper", acknowledge_native_effects=True, **arguments)

    def test_manifest_constructor_returns_session_owned_reference(self):
        # Given: a registered class with a declared default native interface.
        # When: it is constructed through the policy-owned application session.
        result = json.loads(self.call())
        # Then: callers receive an owned reference, not an arbitrary Python object.
        self.assertEqual(result["result"], {"native_handle": "owned"})
        self.mocks[1].assert_called_once_with("modeler")
        self.mocks[3].assert_called_once()
        self.session.refresh_design_identity.assert_called_once()

    def test_unknown_or_unregistered_class_never_activates_com(self):
        for definition in ([], [dict(self.definition, registration={"registered": False})]):
            with self.subTest(definition=definition):
                self.snapshot["libraries"][0]["types"] = definition
                with self.assertRaises(MaxsurfValidationError):
                    self.call()
                self.mocks[3].assert_not_called()

    def test_constructor_refuses_a_different_live_interface(self):
        self.mocks[5].return_value = (None, dict(self.identity, guid="{FOREIGN}"))
        with self.assertRaises(MaxsurfSafetyError):
            self.call()
        self.mocks[6].assert_not_called()

    def test_application_roots_reuse_managed_session_without_reactivation(self):
        self.identity["guid"] = objects.inventory.session.MODULES["modeler"].application_iid
        result = json.loads(self.call())
        self.assertFalse(result["created"])
        self.assertEqual(result["result"], {"native_handle": "owned"})
        self.mocks[3].assert_not_called()

    def test_constructor_requires_consent_and_matching_manifest_digest(self):
        with self.assertRaises(MaxsurfSafetyError):
            objects.create_native_object.__wrapped__("modeler", "Helper")
        with self.assertRaises(MaxsurfSafetyError):
            self.call(inventory_digest="stale")
        self.mocks[3].assert_not_called()

    def test_native_activation_failure_is_a_meaningful_error(self):
        self.mocks[3].side_effect = RuntimeError("native activation failed")
        with self.assertRaises(MaxsurfCOMError):
            self.call()
