"""Offline tests of installed linked and indexed area dispatch contracts."""

import json
import types
from unittest import mock

from maxsurf_mcp import surface_extras as extras
from maxsurf_mcp.errors import MaxsurfCOMError, MaxsurfValidationError

from .test_surfaces import SurfaceTestCase, _FakeSurface, _FakeSurfaces


class ExtrasTests(SurfaceTestCase):
    def test_linked_reader_preserves_native_matrix_and_basis(self):
        _, surface = self.one()
        surface.IsLinkedGeom = True
        surface.LinkedGeomBasisIndex = 2
        surface.LinkedGeomTransformMatrix = ((1, 0), (0, 1))
        payload = json.loads(extras.get_surface_linked_geometry(1))
        self.assertTrue(payload["is_linked"])
        self.assertEqual(payload["basis_index"], 2)
        self.assertEqual(payload["native_transform_matrix"], [[1, 0], [0, 1]])

    def test_area_resolves_both_enum_arguments_and_preserves_variant(self):
        _, surface = self.one()
        surface.AreaProperties = mock.Mock(return_value=(12.5, (1.0, 2.0, 3.0)))
        constants = {"constants": {
            "msATTotal": 1, "msATBelowDWL": 2,
            "msAPT3DTrueSurfaceArea": 1, "msAPT2DLateralProjectedArea": 2,
        }}
        with mock.patch.object(extras.com, "connect", return_value=types.SimpleNamespace(
            Design=types.SimpleNamespace(Surfaces=_FakeSurfaces([surface]))
        )), mock.patch("maxsurf_mcp.surfaces.comconstants.discover",
                      return_value=constants):
            payload = json.loads(extras.get_surface_area_properties(
                1, "msATBelowDWL", "msAPT2DLateralProjectedArea"))
            surface.AreaProperties.assert_called_once_with(2, 2)
            self.assertEqual(payload["native_area_properties"],
                             [12.5, [1.0, 2.0, 3.0]])
            with self.assertRaises(MaxsurfValidationError):
                extras.get_surface_area_properties(1, "msATUnknown")

    def parameters(self, definition):
        values = dict.fromkeys(extras.LINKED_ARGUMENTS[definition], 0.0)
        values.update(scaleLong=1.0, scaleTrans=2.0, scaleVert=3.0)
        if definition == "coords":
            values.update(basisAxisDir1Long=1.0, basisAxisDir2Trans=1.0,
                          cloneAxisDir1Long=1.0, cloneAxisDir2Trans=1.0)
        return values

    def test_both_linked_definitions_dispatch_exact_native_order(self):
        for definition in extras.LINKED_ARGUMENTS:
            with self.subTest(definition=definition):
                collection, basis = self.one()
                values = self.parameters(definition)
                def create(*args):
                    clone = _FakeSurface("linked")
                    clone.IsLinkedGeom = True
                    clone.LinkedGeomBasisIndex = 1
                    clone.LinkedGeomTransformMatrix = ((1.0, 0.0), (0.0, 1.0))
                    collection.items.append(clone)
                native = mock.Mock(side_effect=create)
                setattr(basis, extras.LINKED_MEMBERS[definition], native)
                payload = json.loads(extras.create_linked_geometry_surface(
                    1, definition, json.dumps(values)))
                native.assert_called_once_with(
                    *(values[name] for name in extras.LINKED_ARGUMENTS[definition]))
                self.assertEqual(payload["created_index"], 2)
                self.assertTrue(payload["link_readback_verified"])

    def test_invalid_linked_parameters_do_not_mutate(self):
        collection, basis = self.one()
        basis.CreateLinkedGeomSurfaceCoords = mock.Mock()
        for values in ({}, dict.fromkeys(extras.LINKED_ARGUMENTS["coords"], 1),
                       dict(self.parameters("coords"), scaleLong=0),
                       dict(self.parameters("coords"), scaleLong=float("nan"))):
            with self.subTest(values=values):
                with self.assertRaises(MaxsurfValidationError):
                    extras.create_linked_geometry_surface(
                        1, "coords", json.dumps(values))
        basis.CreateLinkedGeomSurfaceCoords.assert_not_called()
        self.assertEqual(collection.Count, 1)

    def test_link_creation_rejects_noop_and_wrong_basis_readback(self):
        collection, basis = self.one()
        basis.CreateLinkedGeomSurfaceRotations = mock.Mock()
        params = json.dumps(self.parameters("rotations"))
        with self.assertRaises(MaxsurfCOMError):
            extras.create_linked_geometry_surface(1, "rotations", params)
        def wrong_basis(*args):
            clone = _FakeSurface("linked")
            clone.IsLinkedGeom = True
            clone.LinkedGeomBasisIndex = 99
            clone.LinkedGeomTransformMatrix = ()
            collection.items.append(clone)
        basis.CreateLinkedGeomSurfaceRotations = wrong_basis
        with self.assertRaises(MaxsurfCOMError):
            extras.create_linked_geometry_surface(1, "rotations", params)

    def test_registration_exports_all_three_adapters(self):
        captured = []
        class Registry:
            def tool(self, **kwargs):
                def add(fn):
                    captured.append(fn.__name__)
                    return fn
                return add
        names = extras.register_tools(Registry())
        self.assertEqual(names, [fn.__name__ for fn in extras.TOOLS])
        self.assertEqual(captured, names)
