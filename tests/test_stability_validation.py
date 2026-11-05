"""Creation readbacks preserve native indexed getters and SI mass fields."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from maxsurf_mcp import com, stability
from maxsurf_mcp.errors import MaxsurfCOMError, MaxsurfSafetyError, MaxsurfValidationError
from tests.test_stability_controls import Points


class Rooms(Points):
    def __init__(self, kind, corrupt=None):
        super().__init__()
        self.kind = kind
        self.corrupt = corrupt
        self.AddNBV = Mock(side_effect=self.add_nbv)

    def add_nbv(self, *args):
        self.add(*args)
        self.rows[-1].Type = 3

    def ItemByName(self, name):
        return next(row for row in self.rows if row.Name == name)

    def add(self, name, aft, fwd, port, stbd, top, bottom, *perms):
        item = NS(Name=name, Type=1, aft=aft, fwd=fwd, aftPort=port, fwdPort=port,
                  AftStbd=stbd, fwdStbd=stbd, aftTop=top, fwdTop=top,
                  aftBottom=bottom, fwdBottom=bottom)
        if self.kind == "tank":
            item.intactPermeability, item.damagedPermeability = perms
        else:
            item.Permeability = perms[0]
        if self.corrupt:
            setattr(item, self.corrupt, 999)
        self.rows.append(item)


class ReadbackTests(unittest.TestCase):
    def test_room_replacement_preserves_dependents_before_backup_or_mutation(self):
        class Cases(Points):
            __call__ = Points.Item
        class Unreadable:
            @property
            def Count(self):
                raise RuntimeError("unreadable keypoints")
        for dependent in ("keypoint", "unreadable", "invalid", "missing",
                          "loadcase", "undefined_status", "damage", "empty"):
            with self.subTest(dependent=dependent), tempfile.TemporaryDirectory() as tmp:
                design = self.design()
                design.LoadCases = Cases([NS(IsDefined=False) for _ in range(8)]
                                         if dependent == "empty" else [])
                design.KeyPoints = Points()
                design.DamageCases = Points([NS(Name="Intact")])
                if dependent == "keypoint":
                    design.KeyPoints.rows.append(NS(Name="opening"))
                elif dependent == "unreadable":
                    design.KeyPoints = Unreadable()
                elif dependent == "invalid":
                    design.KeyPoints = NS(Count=None)
                elif dependent == "missing":
                    del design.KeyPoints
                elif dependent == "loadcase":
                    design.LoadCases.rows.append(NS(IsDefined=True, Name="loaded"))
                elif dependent == "undefined_status":
                    design.LoadCases.rows.append(NS(IsDefined=None))
                elif dependent == "damage":
                    design.DamageCases.rows.append(NS(Name="damaged"))
                design.Tanks.DeleteAll = Mock()
                design.Compartments.DeleteAll = Mock()
                design.SaveAsRooms = Mock(side_effect=lambda p, overwrite: Path(p).write_bytes(b"rooms"))
                owner = Mock()
                with patch.dict(os.environ, {"MAXSURF_MCP_WORKSPACE_ROOTS": tmp}), \
                        patch.object(com, "connect", return_value=NS(Design=design)), \
                        patch.object(com, "session_for", return_value=owner), \
                        patch.object(stability, "_constant", return_value=1):
                    call = lambda: stability.define_subdivision.__wrapped__(
                        json.dumps([self.room()]), clear_first=True, confirm_replace=True,
                        backup_path=str(Path(tmp) / "backup.htk"))
                    if dependent == "empty":
                        self.assertTrue(json.loads(call())["verified"])
                        design.SaveAsRooms.assert_called_once()
                        design.Compartments.DeleteAll.assert_called_once()
                        owner.refresh_design_identity.assert_called_once()
                    else:
                        with self.assertRaises(MaxsurfSafetyError):
                            call()
                        design.SaveAsRooms.assert_not_called()
                        design.Tanks.DeleteAll.assert_not_called()
                        design.Compartments.DeleteAll.assert_not_called()
                        design.Tanks.Add.assert_not_called()
                        design.Compartments.Add.assert_not_called()
                        self.assertEqual(list(Path(tmp).iterdir()), [])

    def room(self, kind="compartment"):
        row = dict(name="room", kind=kind, aft=0, fwd=5, port=-1, stbd=1,
                   top=2, bottom=0)
        return dict(row, **(dict(intact_perm=.9, damaged_perm=.8) if kind == "tank"
                          else dict(permeability=.95)))

    def design(self, kind="compartment", corrupt=None):
        return NS(Tanks=Rooms("tank", corrupt if kind == "tank" else None),
                  Compartments=Rooms("compartment", corrupt if kind != "tank" else None))

    def run_room(self, design, kind):
        with patch.object(com, "connect", return_value=NS(Design=design)), \
                patch.object(stability, "_constant", side_effect=lambda app, name:
                             3 if name == "hmCTNBV" else 1):
            return json.loads(stability.define_subdivision.__wrapped__(json.dumps([self.room(kind)])))

    def test_prismatic_room_checks_both_ends_and_permeabilities(self):
        for kind in ("compartment", "non_buoyant", "tank"):
            with self.subTest(kind=kind):
                self.assertTrue(self.run_room(self.design(kind), kind)["verified"])

    def test_each_native_boundary_and_permeability_refusal_fails(self):
        for member in ("Type", "aft", "fwd", "aftPort", "fwdPort", "AftStbd", "fwdStbd",
                       "aftTop", "fwdTop", "aftBottom", "fwdBottom", "Permeability"):
            with self.subTest(member=member), self.assertRaises(MaxsurfCOMError):
                self.run_room(self.design(corrupt=member), "compartment")
        for member in ("intactPermeability", "damagedPermeability"):
            with self.subTest(member=member), self.assertRaises(MaxsurfCOMError):
                self.run_room(self.design("tank", member), "tank")

    def test_noop_requested_readback_is_valid(self):
        self.assertTrue(self.run_room(self.design(), "compartment")["verified"])

    def mass_design(self, corrupt=None):
        rows = []
        case = NS(Name="case", IsDefined=True, Mass=2500, MassItem=lambda i: rows[i - 1])
        def add_mass(*args):
            row = NS(**dict(zip(("Name", "Quantity", "Mass", "LCG", "TCG", "VCG",
                                 "AftExtent", "FwdExtent", "FSM"), args)))
            if corrupt:
                setattr(row, corrupt, "wrong" if corrupt == "Name" else 999)
            rows.append(row)
            case.MassItemCount = len(rows)
        case.AddMassItem = Mock(side_effect=add_mass)
        cases = Points()
        cases.Add = Mock(side_effect=lambda *args: cases.rows.append(case))
        cases.__class__ = type("Cases", (Points,), {"__call__": Points.Item})
        return NS(Tanks=Points(), LoadCases=cases, ActiveLoadCase=1), case

    def run_mass(self, design):
        row = dict(name="weight", mass_t=2.5, lcg=2, tcg=0, vcg=1, aft=0, fwd=4, fsm=.7)
        with patch.object(com, "connect", return_value=NS(Design=design)), \
                patch.object(stability, "_constant", return_value=1):
            return json.loads(stability.define_loadcase.__wrapped__("case", "{}", json.dumps([row])))

    def test_mass_and_fsm_conversion_read_back_si_values(self):
        design, case = self.mass_design()
        result = self.run_mass(design)
        case.AddMassItem.assert_called_once_with("weight", 1, 2500, 2, 0, 1, 0, 4, 700)
        self.assertEqual(result["mass_items_native"][0]["FSM"], 700)
        self.assertTrue(result["verified"])

    def test_every_mass_field_is_verified_not_just_count_and_total(self):
        for member in ("Name", "Quantity", "Mass", "LCG", "TCG", "VCG",
                       "AftExtent", "FwdExtent", "FSM"):
            with self.subTest(member=member), self.assertRaises(MaxsurfCOMError):
                self.run_mass(self.mass_design(member)[0])

    def test_existing_keypoint_collision_precedes_add(self):
        points = Points([NS(Name="opening")])
        with patch.object(com, "connect", return_value=NS(Design=NS(KeyPoints=points))), \
                patch.object(stability, "_constant", return_value=3), self.assertRaises(MaxsurfSafetyError):
            stability.add_downflooding_points.__wrapped__(
                json.dumps([dict(name="opening", x=0, y=0, z=1, kind="downflooding")]))
        points.Add.assert_not_called()

    def test_keypoint_wrong_identity_coordinate_or_type_is_not_verified(self):
        for member in ("Name", "Position", "Offset", "Height", "Type"):
            points = Points()
            def add(name, x, y, z, kind):
                row = NS(Name=name, Position=x, Offset=y, Height=z, Type=kind)
                setattr(row, member, "wrong" if member == "Name" else 999)
                points.rows.append(row)
            points.Add.side_effect = add
            with self.subTest(member=member), patch.object(com, "connect", return_value=NS(Design=NS(KeyPoints=points))), \
                    patch.object(stability, "_constant", return_value=3), self.assertRaises(MaxsurfCOMError):
                stability.add_downflooding_points.__wrapped__(
                    json.dumps([dict(name="opening", x=0, y=0, z=1, kind="downflooding")]))


class CriteriaDiscoveryTests(unittest.TestCase):
    def test_override_preserved_without_native_lookup(self):
        with patch.dict(os.environ, {stability.CRITERIA_LIBRARY_ENV: "override"}), \
                patch("pythoncom.QueryPathOfRegTypeLib") as lookup:
            self.assertEqual(stability._criteria_library_dir(), "override")
        lookup.assert_not_called()

    def test_registered_install_not_2025_or_newest_directory(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch("pythoncom.QueryPathOfRegTypeLib",
                      return_value=r"C:\Registered\Maxsurf 2026\bin\win64\MaxsurfStability.exe"):
            self.assertEqual(Path(stability._criteria_library_dir()).parent,
                             Path(r"C:\Registered\Maxsurf 2026\bin\win64"))

    def test_missing_registration_fails_typed_without_stale_fallback(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch("pythoncom.QueryPathOfRegTypeLib", side_effect=RuntimeError("absent")), \
                self.assertRaises(MaxsurfCOMError):
            stability._criteria_library_dir()

    def test_only_hcr_files_are_standards(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "IMO.hcr").write_bytes(b"hcr")
            Path(tmp, "notes.txt").write_bytes(b"text")
            self.assertEqual(stability._available_standards(tmp), ["IMO"])
