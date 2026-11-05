"""Offline policies and conditional flooding regressions; no native activation."""
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from maxsurf_mcp import com, stability_controls as controls
from maxsurf_mcp import guard
from maxsurf_mcp.errors import MaxsurfCOMError, MaxsurfSafetyError, MaxsurfValidationError


class Points:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.Add = Mock(side_effect=self.add)

    @property
    def Count(self):
        return len(self.rows)

    def Item(self, index):
        return self.rows[index - 1]

    def add(self, name, x, y, z, kind):
        self.rows.append(NS(Name=name, Position=x, Offset=y, Height=z, Type=kind))


class PolicyTests(unittest.TestCase):
    def test_trim_modes_select_exact_members(self):
        for mode, member in (("fixed", "FixedTrimValue"), ("initial", "InitialTrimValue"),
                             ("lcg", "TrimToLCGValue"), ("loadcase", None)):
            trim = NS()
            with self.subTest(mode=mode), patch.object(com, "connect", return_value=NS(Design=NS(Trim=trim))), \
                    patch.object(controls, "_constant", return_value=9) as constant:
                result = json.loads(controls.set_stability_trim.__wrapped__(
                    mode, -1.25 if member else None))
            self.assertEqual(trim.TrimMode, 9)
            if member:
                self.assertEqual(getattr(trim, member), -1.25)
            self.assertTrue(result["verified"])
            self.assertEqual(constant.call_args.args[1], controls.TRIM_MODES[mode][0])

    def test_trim_invalid_inputs_precede_connection(self):
        for mode, value in (("fixed", None), ("loadcase", 1), ("fixed", True),
                            ("lcg", float("inf")), ("other", 0), ([], 0)):
            with self.subTest(mode=mode), patch.object(com, "connect") as connect, \
                    self.assertRaises(MaxsurfValidationError):
                controls.set_stability_trim.__wrapped__(mode, value)
            connect.assert_not_called()

    def test_fluid_policy_resolves_constant(self):
        fluids = NS()
        with patch.object(com, "connect", return_value=NS(Design=NS(Fluids=fluids))), \
                patch.object(controls, "_constant", return_value=21) as constant:
            controls.set_stability_fluid_mode.__wrapped__("simulated_movement")
        self.assertEqual(fluids.FluidMode, 21)
        self.assertEqual(constant.call_args.args[1], "hmFMSimulatedFluidMovement")

    def test_fluid_invalid_policy_precedes_connection(self):
        for mode in ("unknown", "", None, []):
            with patch.object(com, "connect") as connect, self.assertRaises(MaxsurfValidationError):
                controls.set_stability_fluid_mode.__wrapped__(mode)
            connect.assert_not_called()

    def test_density_uses_specific_gravity_not_unproven_native_density_units(self):
        values = {}
        native = NS(SetSpecificGravity=Mock(side_effect=lambda k, v: values.update({k: v})),
                    SpecificGravity=lambda k: values[k])
        with patch.object(com, "connect", return_value=NS(Design=NS(Density=native))), \
                patch.object(controls, "_constant", return_value=1):
            result = json.loads(controls.set_stability_density.__wrapped__("hmFSeaWater", 1025))
        native.SetSpecificGravity.assert_called_once_with(1, 1.025)
        self.assertEqual(result["density_kg_m3"], 1025)

    def test_density_refused_write_is_not_verified(self):
        native = NS(SetSpecificGravity=Mock(), SpecificGravity=lambda k: 1)
        with patch.object(com, "connect", return_value=NS(Design=NS(Density=native))), \
                patch.object(controls, "_constant", return_value=1), self.assertRaises(MaxsurfCOMError):
            controls.set_stability_density.__wrapped__("hmFSeaWater", 1025)

    def test_density_invalid_inputs_precede_connection(self):
        for fluid, value in (("hmFTankSpecific", 1000), ("hmFSeaWater", 0),
                             ("hmFSeaWater", True), ("hmFSeaWater", float("nan"))):
            with patch.object(com, "connect") as connect, self.assertRaises(MaxsurfValidationError):
                controls.set_stability_density.__wrapped__(fluid, value)
            connect.assert_not_called()

    def test_trim_refused_write_is_detected(self):
        class Trim:
            TrimMode = 1
            @property
            def FixedTrimValue(self):
                return 0
            @FixedTrimValue.setter
            def FixedTrimValue(self, value):
                pass
        with patch.object(com, "connect", return_value=NS(Design=NS(Trim=Trim()))), \
                patch.object(controls, "_constant", return_value=1), self.assertRaises(MaxsurfCOMError):
            controls.set_stability_trim.__wrapped__("fixed", 2)

    def test_guard_normalizes_native_connection_errors_for_every_control(self):
        rows = [dict(name="opening", x=0, y=0, z=1, linked_to="room",
                     use_intact=True, use_damage=False, use_intermediate_damage=False)]
        calls = (
            (controls.set_stability_trim, ("fixed", 0)),
            (controls.set_stability_fluid_mode, ("corrected_vcg",)),
            (controls.set_stability_density, ("hmFSeaWater", 1000)),
            (controls.define_conditional_keypoints, (json.dumps(rows),)),
        )
        for tool, args in calls:
            with self.subTest(tool=tool.__name__), \
                    patch.object(guard.audit, "get_log", return_value=Mock()), \
                    patch.object(guard.comthread, "run", side_effect=lambda fn, **kwargs: fn()), \
                    patch.object(com, "connect", side_effect=RuntimeError("native unavailable")), \
                    self.assertRaises(MaxsurfCOMError):
                tool(*args)


class ConditionalTests(unittest.TestCase):
    def point(self, **changes):
        return dict(name="opening", x=1, y=2, z=3, linked_to="cockpit",
                    use_intact=True, use_damage=False, use_intermediate_damage=False, **changes)

    def design(self, points=None):
        return NS(KeyPoints=points or Points(), Tanks=Points(),
                  Compartments=Points([NS(Name="cockpit")]))

    def run_points(self, design, rows):
        with patch.object(com, "connect", return_value=NS(Design=design)), \
                patch.object(controls, "_constant", return_value=7):
            return json.loads(controls.define_conditional_keypoints.__wrapped__(json.dumps(rows)))

    def test_coordinates_links_and_every_policy_are_read_back(self):
        design = self.design()
        result = self.run_points(design, [self.point()])
        self.assertTrue(result["verified"])
        self.assertEqual(result["points"][0], dict(Name="opening", Position=1, Offset=2,
                         Height=3, LinkedTo="cockpit", Type=7, UseIntact=True,
                         UseDamage=False, UseIntermediateDamage=False))
        design.KeyPoints.Add.assert_called_once_with("opening", 1, 2, 3, 7)

    def test_unknown_room_and_collision_precede_creation(self):
        for design, row, error in (
            (self.design(), dict(self.point(), linked_to="missing"), MaxsurfValidationError),
            (self.design(Points([NS(Name="opening")])), self.point(), MaxsurfSafetyError),
        ):
            with self.assertRaises(error):
                self.run_points(design, [row])
            design.KeyPoints.Add.assert_not_called()

    def test_all_points_validated_before_any_mutation(self):
        design = self.design()
        for rows in ([self.point(), self.point()], [dict(self.point(), use_damage=1)],
                     [dict(self.point(), FillInto=True)], []):
            with self.assertRaises(MaxsurfValidationError):
                self.run_points(design, rows)
        design.KeyPoints.Add.assert_not_called()

    def test_refused_coordinate_write_fails_readback(self):
        points = Points()
        points.Add.side_effect = lambda *args: points.rows.append(
            NS(Name=args[0], Position=0, Offset=args[2], Height=args[3], Type=args[4]))
        with self.assertRaises(MaxsurfCOMError):
            self.run_points(self.design(points), [self.point()])
