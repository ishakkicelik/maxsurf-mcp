"""Verified thin native roots must retain process and interface boundaries."""
from __future__ import annotations

from maxsurf_mcp import config, session
from maxsurf_mcp.errors import MaxsurfConnectionError

from .support import ComFake, IsolatedEnvTestCase


class AdditionalSessionTests(IsolatedEnvTestCase):
    def test_vpp_uses_verified_interface_without_inventing_design_identity(self):
        # Given: a running VPP root with its installed interface identity.
        spec = session.MODULES["vpp"]
        app = ComFake(declared_iid=spec.application_iid)
        self.install_com_environment(running={spec.progids[0]: app})
        self.set_env(config.ATTACH_POLICY_VAR, config.ATTACH_ONLY)

        # When: the existing root is connected.
        connected = self.sessions.get("vpp")

        # Then: no nonexistent vessel Design contract is inferred.
        self.assertIs(connected, app)
        self.assertEqual(spec.design_path_members, ())
        self.assertEqual(spec.design_object_member, "")

    def test_shape_editor_grid_cannot_activate_an_absent_application(self):
        # Given: its XMLGrid is registered but Shape Editor is not running.
        spec = session.MODULES["shape_editor"]
        client = self.install_com_environment(
            running={},
            creatable={spec.progids[0]: ComFake(declared_iid=spec.application_iid)},
        )
        self.set_env(config.ATTACH_POLICY_VAR, config.ACTIVATE_EXISTING)

        # When: the process-gated policy is used.
        with self.assertRaises(MaxsurfConnectionError):
            self.sessions.get("shape_editor")

        # Then: registration is not mistaken for a running application.
        self.assertEqual(client.dispatch_calls, [])

    def test_vpp_grid_requires_parent_session_before_activation(self):
        # Given: the helper is registered without a connected VPP parent.
        spec = session.MODULES["vpp_xmlgrid"]
        client = self.install_com_environment(
            running={},
            creatable={spec.progids[0]: ComFake(declared_iid=spec.application_iid)},
        )

        # When: the helper is requested.
        with self.assertRaises(MaxsurfConnectionError) as caught:
            self.sessions.get("vpp_xmlgrid")

        # Then: no helper activation can start the parent implicitly.
        self.assertEqual(caught.exception.details["requires_session"], "vpp")
        self.assertEqual(client.dispatch_calls, [])
