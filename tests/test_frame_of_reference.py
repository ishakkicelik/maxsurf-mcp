"""Offline tests for the Modeler frame-of-reference boundary.

Nothing here touches Bentley COM. The fake ``IFrameOfReference`` reproduces the
one behaviour that makes the datum edit order-sensitive: ``FindAftDWL`` and
``FindFwdDWL`` are computed from ``DatumWL``, so reading them before ``DatumWL``
has been written yields the old waterline.
"""

from __future__ import annotations

import json
import math
import types
import unittest
from typing import Any

from unittest import mock

import server
from maxsurf_mcp import audit
from maxsurf_mcp import frame as frame_module
from maxsurf_mcp.audit import Classification, Outcome
from maxsurf_mcp.errors import (
    MaxsurfCOMError,
    MaxsurfSafetyError,
    MaxsurfValidationError,
)

from .support import IsolatedEnvTestCase

#: Enum families the tools resolve at runtime. Values match private discovery notes (not distributed), but
#: the production code never hard-codes them, which is what these tests pin.
_CONSTANTS = {
    "constants": {
        "msLDTAftPerp": 1,
        "msLDTFwdPerp": 2,
        "msLDTMidships": 3,
        "msLDTOther": 4,
        "msVDTDatumWL": 1,
        "msVDTBaseline": 2,
        "msVDTOther": 3,
        "msDTLongitudinal": 1,
        "msDTTransverse": 2,
        "msDTVertical": 3,
    }
}

_WRITABLE = (
    "AftPerp", "FwdPerp", "Midships", "DatumWL", "Baseline",
    "OtherLongDatum", "OtherVertDatum", "LongDatum", "VertDatum",
)
_DERIVED = ("FindAftDWL", "FindFwdDWL", "FindAftExt", "FindFwdExt", "FindBase")

#: The live Phase 3C.2 geometry: the hull bottom sits 0.7292696 m below the
#: unconfigured datum, which is what made KB and DraftAP come back negative.
HULL_BASE = -0.7292696
HULL_AFT_EXT = -0.05
HULL_FWD_EXT = 2.35

#: How far the waterline intersections move as the waterline rises. Arbitrary
#: but non-zero, so a stale read is distinguishable from a fresh one.
AFT_DWL_SLOPE = 0.10
FWD_DWL_SLOPE = -0.25


class _FakeFrame:
    """A stand-in for IFrameOfReference that records every read and write."""

    def __init__(self, journal: list[Any] | None = None, **overrides: Any):
        object.__setattr__(self, "journal", [] if journal is None else journal)
        object.__setattr__(self, "writes", [])
        object.__setattr__(self, "failing_reads", set())
        object.__setattr__(self, "failing_writes", set())
        values = {
            "AftPerp": 0.0, "FwdPerp": 0.0, "Midships": 0.0,
            "DatumWL": 0.0, "Baseline": 0.0,
            "OtherLongDatum": 0.0, "OtherVertDatum": 0.0,
            # LongDatum -> AftPerp, VertDatum -> Baseline: the default binding.
            "LongDatum": 1, "VertDatum": 2,
        }
        values.update(overrides)
        object.__setattr__(self, "values", values)

    # -- COM surface ------------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        if name in _WRITABLE:
            self._read(name)
            return self.values[name]
        if name in _DERIVED:
            self._read(name)
            return self._derived(name)
        if name == "InternalZero":
            return self._internal_zero
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name not in _WRITABLE:
            object.__setattr__(self, name, value)
            return
        self.journal.append(("write", name))
        if name in self.failing_writes:
            raise OSError(f"Maxsurf refused {name}")
        self.writes.append((name, value))
        self.values[name] = value

    def _read(self, name: str) -> None:
        self.journal.append(("read", name))
        if name in self.failing_reads:
            raise OSError(f"{name} is unavailable")

    def _derived(self, name: str) -> float:
        if name == "FindBase":
            return HULL_BASE
        if name == "FindAftExt":
            return HULL_AFT_EXT
        if name == "FindFwdExt":
            return HULL_FWD_EXT
        # The waterline intersections move with DatumWL. This is the whole
        # reason the dwl mode cannot resolve them in one pass.
        rise = self.values["DatumWL"] - HULL_BASE
        if name == "FindAftDWL":
            return HULL_AFT_EXT + AFT_DWL_SLOPE * rise
        return HULL_FWD_EXT + FWD_DWL_SLOPE * rise

    def _internal_zero(self, direction: int) -> float:
        """The zero point, bound to whichever element each selector picks."""
        if direction == 2:  # msDTTransverse
            return 0.0
        if direction == 1:  # msDTLongitudinal
            return self.values[
                {1: "AftPerp", 2: "FwdPerp", 3: "Midships",
                 4: "OtherLongDatum"}[self.values["LongDatum"]]
            ]
        return self.values[
            {1: "DatumWL", 2: "Baseline", 3: "OtherVertDatum"}[
                self.values["VertDatum"]]
        ]

    # -- assertions helpers ----------------------------------------------
    def written(self, member: str) -> float:
        return next(v for name, v in reversed(self.writes) if name == member)


class _JournallingAuditLog(audit.AuditLog):
    """An audit log that records when each record was written, in order."""

    def __init__(self, directory: Any, journal: list[Any]) -> None:
        super().__init__(directory=directory)
        self._journal = journal

    def write(self, record: dict[str, Any], *, must_audit: bool):
        self._journal.append(("audit", record["stage"], record["tool"]))
        return super().write(record, must_audit=must_audit)


class FrameTestCase(IsolatedEnvTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.journal: list[Any] = []
        audit.set_log(_JournallingAuditLog(self.audit_dir, self.journal))

    def patch(self, frame: _FakeFrame) -> _FakeFrame:
        # One journal for COM reads/writes and audit records, so their relative
        # order is directly observable.
        frame.journal = self.journal
        app = types.SimpleNamespace(
            Design=types.SimpleNamespace(FrameOfReference=frame)
        )
        connect = mock.patch.object(server.com, "connect", return_value=app)
        constants = mock.patch.object(
            server.comconstants, "discover", return_value=_CONSTANTS
        )
        connect.start()
        constants.start()
        self.addCleanup(connect.stop)
        self.addCleanup(constants.stop)
        return frame

    def index_of(self, entry: Any) -> int:
        return self.journal.index(entry)

    def first_write_index(self) -> int:
        return min(i for i, e in enumerate(self.journal) if e[0] == "write")


class ReadTests(FrameTestCase):
    def test_every_proven_member_is_reported_with_its_enum_name(self):
        self.patch(_FakeFrame(AftPerp=1.5, Baseline=HULL_BASE))
        payload = json.loads(server.get_frame_of_reference())

        self.assertEqual(payload["path"], "Design.FrameOfReference")
        self.assertEqual(sorted(payload["positions"]), sorted([
            "aft_perp", "fwd_perp", "midships", "datum_wl", "baseline",
            "other_long_datum", "other_vert_datum",
        ]))
        self.assertEqual(payload["positions"]["aft_perp"]["value"], 1.5)
        self.assertEqual(payload["positions"]["aft_perp"]["member"], "AftPerp")
        self.assertEqual(sorted(payload["hull_derived"]), sorted([
            "find_aft_dwl", "find_fwd_dwl", "find_aft_ext", "find_fwd_ext",
            "find_base",
        ]))
        self.assertEqual(payload["hull_derived"]["find_base"]["value"], HULL_BASE)
        self.assertEqual(payload["datum"]["long_datum"]["enum_name"],
                         "msLDTAftPerp")
        self.assertEqual(payload["datum"]["vert_datum"]["enum_name"],
                         "msVDTBaseline")
        self.assertEqual(sorted(payload["internal_zero"]),
                         ["longitudinal", "transverse", "vertical"])

    def test_reading_writes_nothing(self):
        frame = self.patch(_FakeFrame())
        server.get_frame_of_reference()
        self.assertEqual(frame.writes, [])

    def test_an_unconfigured_frame_of_reference_is_flagged(self):
        self.patch(_FakeFrame())
        payload = json.loads(server.get_frame_of_reference())
        self.assertTrue(any("never been configured" in w
                            for w in payload["warnings"]))

    def test_a_configured_frame_of_reference_is_not_flagged(self):
        self.patch(_FakeFrame(Baseline=HULL_BASE, AftPerp=0.0, FwdPerp=2.3,
                              Midships=1.15, DatumWL=HULL_BASE + 0.3))
        payload = json.loads(server.get_frame_of_reference())
        self.assertFalse(any("never been configured" in w
                             for w in payload["warnings"]))

    def test_a_baseline_off_the_hull_is_reported_with_its_offset(self):
        self.patch(_FakeFrame(AftPerp=1.0))
        payload = json.loads(server.get_frame_of_reference())
        offset = [w for w in payload["warnings"] if "FindBase" in w]
        self.assertEqual(len(offset), 1)
        self.assertIn(str(HULL_BASE), offset[0])

    def test_a_failing_getter_is_not_reported_as_zero(self):
        frame = _FakeFrame()
        frame.failing_reads.add("Midships")
        self.patch(frame)
        payload = json.loads(server.get_frame_of_reference())

        entry = payload["positions"]["midships"]
        self.assertIsNone(entry["value"])
        self.assertEqual(entry["status"], "failed")
        self.assertIn("unavailable", entry["detail"])
        self.assertTrue(any("could not be read" in w
                            for w in payload["warnings"]))

    def test_the_zero_point_binding_is_reported_as_inferred(self):
        self.patch(_FakeFrame())
        payload = json.loads(server.get_frame_of_reference())
        vertical = payload["zero_point_bindings"]["vertical"]
        self.assertEqual(vertical["bound_field"], "baseline")
        self.assertTrue(vertical["resolved"])
        self.assertFalse(vertical["proven"])


class ValidationTests(FrameTestCase):
    def test_an_empty_request_is_refused_without_touching_com(self):
        frame = self.patch(_FakeFrame())
        with self.assertRaises(MaxsurfValidationError) as caught:
            server.set_frame_of_reference()
        self.assertIn("nothing was requested", str(caught.exception))
        self.assertEqual(frame.writes, [])

    def test_an_unknown_auto_mode_lists_the_available_ones(self):
        frame = self.patch(_FakeFrame())
        with self.assertRaises(MaxsurfValidationError) as caught:
            server.set_frame_of_reference(auto_from_hull="perpendiculars")
        self.assertEqual(caught.exception.details["available"], ["baseline"])
        self.assertEqual(frame.writes, [])

    def test_a_draft_outside_dwl_mode_is_refused_rather_than_ignored(self):
        frame = self.patch(_FakeFrame())
        with self.assertRaises(MaxsurfValidationError) as caught:
            server.set_frame_of_reference(auto_from_hull="baseline",
                                          draft_m=0.3, confirm=True)
        self.assertIn("silently ignored", str(caught.exception))
        self.assertEqual(frame.writes, [])

    def test_non_finite_and_non_numeric_values_are_refused(self):
        frame = self.patch(_FakeFrame())
        for bad in (float("nan"), math.inf, -math.inf, True, "1.0"):
            with self.subTest(value=bad):
                with self.assertRaises(MaxsurfValidationError):
                    server.set_frame_of_reference(datum_wl=bad)
        self.assertEqual(frame.writes, [])

    def test_an_unknown_datum_enum_name_lists_the_permitted_ones(self):
        frame = self.patch(_FakeFrame())
        with self.assertRaises(MaxsurfValidationError) as caught:
            server.set_frame_of_reference(vert_datum="msVDTKeel", confirm=True)
        self.assertEqual(caught.exception.details["available"],
                         ["msVDTBaseline", "msVDTDatumWL", "msVDTOther"])
        self.assertEqual(frame.writes, [])

    def test_a_request_that_changes_nothing_is_refused(self):
        frame = self.patch(_FakeFrame(DatumWL=1.25))
        with self.assertRaises(MaxsurfValidationError) as caught:
            server.set_frame_of_reference(datum_wl=1.25)
        self.assertIn("already holds every requested value",
                      str(caught.exception))
        self.assertEqual(frame.writes, [])


class ConfirmTests(FrameTestCase):
    """confirm gates every write.

    The binding model is not unreliable -- live probing showed it holds exactly
    in the steady state. It is blind: a first write against a configured but
    unapplied frame rebases both axes whatever field it touches, and nothing
    readable before the write distinguishes that case with certainty. So the
    classification reports and does not decide.
    """

    def test_every_write_requires_confirmation(self):
        for kwargs in ({"baseline": HULL_BASE},        # vertically bound
                       {"datum_wl": 0.3},              # unbound
                       {"other_long_datum": 1.0},      # unbound
                       {"vert_datum": "msVDTDatumWL"},  # rebinds
                       {"auto_from_hull": "baseline"}):
            with self.subTest(**kwargs):
                frame = _FakeFrame()
                self.patch(frame)
                with self.assertRaises(MaxsurfSafetyError) as caught:
                    server.set_frame_of_reference(**kwargs)
                self.assertIn("moves the datum", str(caught.exception))
                self.assertEqual(frame.writes, [])

    def test_the_refusal_still_reports_which_fields_are_bound(self):
        self.patch(_FakeFrame())
        with self.assertRaises(MaxsurfSafetyError) as caught:
            server.set_frame_of_reference(baseline=HULL_BASE)
        details = caught.exception.details
        self.assertEqual(details["zero_point_shifting_fields"], ["baseline"])
        self.assertEqual(details["planned_fields"], ["baseline"])
        self.assertIn("frame_applied", details)

    def test_an_unbound_field_is_reported_as_not_shifting_but_still_gated(self):
        frame = _FakeFrame()
        self.patch(frame)
        with self.assertRaises(MaxsurfSafetyError) as caught:
            server.set_frame_of_reference(datum_wl=0.3)
        self.assertEqual(caught.exception.details["zero_point_shifting_fields"],
                         [])
        self.assertEqual(frame.writes, [])

    def test_confirmation_permits_the_write_and_reports_what_shifted(self):
        frame = self.patch(_FakeFrame())
        payload = json.loads(
            server.set_frame_of_reference(baseline=HULL_BASE, confirm=True)
        )
        self.assertEqual(frame.writes, [("Baseline", HULL_BASE)])
        self.assertEqual(payload["zero_point_shifting_fields"], ["baseline"])
        vertical = payload["internal_zero_shift"]["vertical"]
        self.assertEqual(vertical["status"], "measured")
        self.assertAlmostEqual(vertical["delta"], HULL_BASE)
        self.assertTrue(any("zero point moved on: vertical" in w
                            for w in payload["warnings"]))

    def test_an_unresolvable_selector_fails_safe_in_the_report(self):
        # VertDatum holds a value no enum member matches, so the binding cannot
        # be resolved and every position must be reported as capable of shifting.
        frame = self.patch(_FakeFrame(VertDatum=99))
        with self.assertRaises(MaxsurfSafetyError) as caught:
            server.set_frame_of_reference(other_vert_datum=1.0)
        self.assertEqual(caught.exception.details["zero_point_shifting_fields"],
                         ["other_vert_datum"])
        self.assertEqual(frame.writes, [])


class AuditTests(FrameTestCase):
    def test_the_before_state_is_recorded_before_any_property_is_written(self):
        self.patch(_FakeFrame())
        server.set_frame_of_reference(baseline=HULL_BASE, confirm=True)

        intent = self.index_of(
            ("audit", "intent", "set_frame_of_reference_before_state")
        )
        self.assertLess(intent, self.first_write_index())

    def test_the_before_state_record_carries_the_pre_write_positions(self):
        self.patch(_FakeFrame(Baseline=0.25))
        server.set_frame_of_reference(baseline=HULL_BASE, confirm=True)

        records = self.records_for("set_frame_of_reference_before_state")
        self.assertEqual(len(records), 1)
        summary = records[0]["result_summary"]
        self.assertEqual(records[0]["stage"], "intent")
        self.assertEqual(records[0]["classification"],
                         Classification.WRITE_STATE)
        self.assertEqual(summary["before"]["positions"]["baseline"]["value"],
                         0.25)
        self.assertEqual(summary["planned_fields"], ["baseline"])
        self.assertEqual(summary["zero_point_shifting_fields"], ["baseline"])

    def test_the_guard_still_brackets_the_call_with_intent_and_complete(self):
        self.patch(_FakeFrame())
        server.set_frame_of_reference(baseline=HULL_BASE, confirm=True)
        records = self.records_for("set_frame_of_reference")
        self.assertEqual([r["stage"] for r in records], ["intent", "complete"])
        self.assertEqual(records[-1]["outcome"], Outcome.SUCCESS)

    def test_a_refused_write_is_audited_as_a_failure(self):
        self.patch(_FakeFrame())
        with self.assertRaises(MaxsurfSafetyError):
            server.set_frame_of_reference(baseline=HULL_BASE)
        records = self.records_for("set_frame_of_reference")
        self.assertEqual(records[-1]["outcome"], Outcome.FAILURE)
        self.assertEqual(records[-1]["error"]["code"], "safety_error")
        self.assertEqual(
            self.records_for("set_frame_of_reference_before_state"), []
        )


class AutoFromHullTests(FrameTestCase):
    def test_baseline_mode_touches_only_the_baseline(self):
        frame = self.patch(_FakeFrame())
        payload = json.loads(
            server.set_frame_of_reference(auto_from_hull="baseline",
                                          confirm=True)
        )
        self.assertEqual(frame.writes, [("Baseline", HULL_BASE)])
        self.assertEqual([c["field"] for c in payload["changed"]], ["baseline"])
        self.assertEqual(payload["changed"][0]["source"], "auto:baseline")

    def test_an_unreadable_hull_source_refuses_before_any_write(self):
        frame = _FakeFrame()
        frame.failing_reads.add("FindBase")
        self.patch(frame)
        with self.assertRaises(MaxsurfValidationError) as caught:
            server.set_frame_of_reference(auto_from_hull="baseline",
                                          confirm=True)
        self.assertEqual(caught.exception.details["source"], "FindBase")
        self.assertEqual(frame.writes, [])
        self.assertEqual(
            self.records_for("set_frame_of_reference_before_state"), []
        )


class QuarantinedModeTests(FrameTestCase):
    """dwl and extents are withdrawn from the tool surface.

    Live, dwl asked for a 4.5 m waterline and produced a 10.08 m one: a
    multi-field call can rebase the coordinate frame between one write and the
    next, leaving the remaining values expressed in a frame that no longer
    exists. The planning code is kept and unit-tested below, because the
    two-stage ordering it encodes is correct and is not what broke.
    """

    def test_both_multi_field_modes_are_refused_without_touching_com(self):
        for mode in ("dwl", "extents"):
            with self.subTest(mode=mode):
                frame = _FakeFrame()
                self.patch(frame)
                with self.assertRaises(MaxsurfValidationError) as caught:
                    server.set_frame_of_reference(auto_from_hull=mode,
                                                  draft_m=0.3 if mode == "dwl"
                                                  else None, confirm=True)
                message = str(caught.exception)
                self.assertIn("multi-field auto modes are not verified", message)
                self.assertIn("5.58 m waterline error", message)
                self.assertEqual(caught.exception.details["available"],
                                 ["baseline"])
                self.assertEqual(frame.writes, [])

    def test_the_quarantine_precedes_the_draft_check(self):
        # dwl without draft_m must report the quarantine, not the missing draft.
        frame = self.patch(_FakeFrame())
        with self.assertRaises(MaxsurfValidationError) as caught:
            server.set_frame_of_reference(auto_from_hull="dwl", confirm=True)
        self.assertIn("multi-field auto modes", str(caught.exception))
        self.assertEqual(frame.writes, [])

    def test_the_verified_mode_still_works(self):
        frame = self.patch(_FakeFrame())
        payload = json.loads(server.set_frame_of_reference(
            auto_from_hull="baseline", confirm=True))
        self.assertEqual(frame.writes, [("Baseline", HULL_BASE)])
        self.assertEqual([c["field"] for c in payload["changed"]], ["baseline"])


def _planning_state(**hull: float) -> dict[str, Any]:
    """The slice of read state that _plan_changes actually consults."""
    return {
        "hull_derived": {
            key: {"value": value, "status": "read", "member": key,
                  "detail": None}
            for key, value in hull.items()
        }
    }


class MultiFieldPlanningTests(unittest.TestCase):
    """The withdrawn modes' planning logic, kept alive at unit level.

    These pin the ordering dependency the Modeler manual encodes -- Find Base,
    enter the DWL height, then Set to DWL for each perpendicular -- which the
    tool boundary no longer exercises. Restoring the modes must keep them
    passing.
    """

    DRAFT = 0.30

    def state(self) -> dict[str, Any]:
        return _planning_state(
            find_base=HULL_BASE, find_aft_ext=HULL_AFT_EXT,
            find_fwd_ext=HULL_FWD_EXT,
            find_aft_dwl=HULL_AFT_EXT + AFT_DWL_SLOPE * (0.0 - HULL_BASE),
            find_fwd_dwl=HULL_FWD_EXT + FWD_DWL_SLOPE * (0.0 - HULL_BASE),
        )

    def test_dwl_writes_the_baseline_before_the_waterline(self):
        plan = frame_module._plan_changes({}, "dwl", self.DRAFT, self.state())
        self.assertEqual([c["field"] for c in plan["stage_one"]],
                         ["baseline", "datum_wl"])

    def test_dwl_defers_the_perpendiculars_past_the_waterline_write(self):
        plan = frame_module._plan_changes({}, "dwl", self.DRAFT, self.state())
        self.assertEqual(plan["stage_two_fields"],
                         ["aft_perp", "fwd_perp", "midships"])
        self.assertTrue(any("re-read" in note for note in plan["notes"]))

    def test_dwl_derives_the_waterline_from_the_baseline_and_draft(self):
        plan = frame_module._plan_changes({}, "dwl", self.DRAFT, self.state())
        values = {c["field"]: c["value"] for c in plan["stage_one"]}
        self.assertAlmostEqual(values["baseline"], HULL_BASE)
        self.assertAlmostEqual(values["datum_wl"], HULL_BASE + self.DRAFT)
        self.assertTrue(any("no FindDWL member" in note
                            for note in plan["notes"]))

    def test_extents_resolves_everything_in_one_stage(self):
        plan = frame_module._plan_changes({}, "extents", None, self.state())
        values = {c["field"]: c["value"] for c in plan["stage_one"]}
        self.assertEqual(plan["stage_two_fields"], [])
        self.assertAlmostEqual(values["aft_perp"], HULL_AFT_EXT)
        self.assertAlmostEqual(values["fwd_perp"], HULL_FWD_EXT)
        self.assertAlmostEqual(values["midships"],
                               (HULL_AFT_EXT + HULL_FWD_EXT) / 2.0)
        self.assertNotIn("datum_wl", values)

    def test_an_explicit_value_overrides_the_derived_one(self):
        plan = frame_module._plan_changes({"aft_perp": -0.4}, "extents", None,
                                          self.state())
        values = {c["field"]: c["value"] for c in plan["stage_one"]}
        self.assertEqual(values["aft_perp"], -0.4)
        self.assertAlmostEqual(values["midships"],
                               (-0.4 + HULL_FWD_EXT) / 2.0)

    def test_an_unreadable_hull_source_raises_before_anything_is_planned(self):
        state = _planning_state()
        state["hull_derived"]["find_base"] = {
            "value": None, "status": "failed", "member": "FindBase",
            "detail": "unavailable",
        }
        with self.assertRaises(MaxsurfValidationError) as caught:
            frame_module._plan_changes({}, "baseline", None, state)
        self.assertEqual(caught.exception.details["source"], "FindBase")

    def test_stage_two_reads_the_waterline_after_it_has_moved(self):
        frame = _FakeFrame()
        frame.values["DatumWL"] = HULL_BASE + self.DRAFT  # as stage one leaves it
        changes, sources = frame_module._resolve_stage_two(frame, {}, "dwl")
        values = {c["field"]: c["value"] for c in changes}
        fresh = HULL_AFT_EXT + AFT_DWL_SLOPE * self.DRAFT
        stale = HULL_AFT_EXT + AFT_DWL_SLOPE * (0.0 - HULL_BASE)
        self.assertAlmostEqual(values["aft_perp"], fresh)
        self.assertNotAlmostEqual(values["aft_perp"], stale)
        self.assertAlmostEqual(values["midships"],
                               (values["aft_perp"] + values["fwd_perp"]) / 2.0)
        self.assertAlmostEqual(sources["find_aft_dwl"]["value"], fresh)

    def test_a_failed_re_read_after_the_waterline_write_is_reported(self):
        frame = _FakeFrame()
        frame.failing_reads.add("FindAftDWL")
        with self.assertRaises(MaxsurfCOMError) as caught:
            frame_module._resolve_stage_two(frame, {}, "dwl")
        self.assertIn("could not be re-read after DatumWL",
                      str(caught.exception))


class PartialFailureTests(FrameTestCase):
    def test_a_failed_write_reports_the_true_applied_count(self):
        frame = _FakeFrame()
        frame.failing_writes.add("Midships")
        self.patch(frame)
        with self.assertRaises(MaxsurfCOMError) as caught:
            server.set_frame_of_reference(
                baseline=HULL_BASE, aft_perp=HULL_AFT_EXT,
                fwd_perp=HULL_FWD_EXT, midships=1.0, confirm=True,
            )
        details = caught.exception.details
        self.assertEqual(details["failed_member"], "Midships")
        self.assertNotIn("midships", details["applied_before_failure"])
        self.assertEqual(sorted(details["applied_before_failure"]),
                         ["aft_perp", "baseline", "fwd_perp"])
        self.assertIn("partially modified", str(caught.exception))


class FrameAppliedTests(FrameTestCase):
    """The activation predicate, from the M0 live acceptance run.

    Live: before the first write midships=28.2617 and datum_wl=5.5766; that
    write rebased both axes and afterwards both read 0.0.
    """

    def test_a_frame_whose_bound_positions_read_zero_is_applied(self):
        # LongDatum -> Midships, VertDatum -> DatumWL, both at the origin.
        self.patch(_FakeFrame(LongDatum=3, VertDatum=1,
                              Midships=0.0, DatumWL=0.0, Baseline=-5.5777))
        payload = json.loads(server.get_frame_of_reference())
        applied = payload["frame_applied"]
        self.assertTrue(applied["applied"])
        self.assertEqual(applied["axes"]["longitudinal"]["bound_field"],
                         "midships")
        self.assertEqual(applied["axes"]["vertical"]["bound_field"], "datum_wl")

    def test_a_configured_but_unapplied_frame_is_detected_and_warned_about(self):
        self.patch(_FakeFrame(LongDatum=3, VertDatum=1,
                              Midships=28.261717796325684,
                              DatumWL=5.576552629470825))
        payload = json.loads(server.get_frame_of_reference())
        applied = payload["frame_applied"]
        self.assertFalse(applied["applied"])
        self.assertEqual(applied["axes"]["longitudinal"]["value"],
                         28.261717796325684)
        self.assertTrue(any("configured but not yet applied" in w
                            for w in payload["warnings"]))

    def test_an_unresolvable_binding_yields_an_unknown_verdict(self):
        self.patch(_FakeFrame(VertDatum=99))
        payload = json.loads(server.get_frame_of_reference())
        self.assertIsNone(payload["frame_applied"]["applied"])

    def test_the_refusal_warns_when_the_frame_is_not_applied(self):
        self.patch(_FakeFrame(LongDatum=3, VertDatum=1,
                              Midships=28.261717796325684,
                              DatumWL=5.576552629470825))
        with self.assertRaises(MaxsurfSafetyError) as caught:
            server.set_frame_of_reference(baseline=HULL_BASE)
        warnings = caught.exception.details["warnings"]
        self.assertTrue(any("rebase the reported origin on both axes" in w
                            for w in warnings))


class StorageStateTests(unittest.TestCase):
    """The readback reconciliation, pinned to the three live-measured writes.

    after == requested + InternalZero_delta(that field's axis), verified live
    with errors of 0.0, 0.0 and 1.1e-16.
    """

    @staticmethod
    def shift(longitudinal=0.0, vertical=0.0):
        return {
            "longitudinal": {"delta": longitudinal, "status": "measured"},
            "transverse": {"delta": 0.0, "status": "measured"},
            "vertical": {"delta": vertical, "status": "measured"},
        }

    def test_step_three_baseline_write_is_stored_in_a_shifted_frame(self):
        state, delta = frame_module._storage_state(
            "baseline", -0.0011554148590562363, -5.577708044329881,
            self.shift(longitudinal=28.261717796325684,
                       vertical=-5.576552629470825),
        )
        self.assertEqual(state, frame_module.STORED_FRAME_SHIFTED)
        self.assertAlmostEqual(delta, -5.576552629470825)

    def test_probe_a_unbound_write_is_stored_outright(self):
        state, delta = frame_module._storage_state(
            "baseline", -5.576708044329881, -5.576708044329881, self.shift(),
        )
        self.assertEqual(state, frame_module.STORED)
        self.assertEqual(delta, 0.0)

    def test_probe_b_bound_write_is_stored_in_a_shifted_frame(self):
        state, delta = frame_module._storage_state(
            "datum_wl", 0.001, 0.0,
            self.shift(vertical=-0.0009999999999998899),
        )
        self.assertEqual(state, frame_module.STORED_FRAME_SHIFTED)

    def test_a_value_explained_by_neither_is_not_stored(self):
        state, delta = frame_module._storage_state(
            "baseline", 1.0, 7.5, self.shift(vertical=-0.001),
        )
        self.assertEqual(state, frame_module.NOT_STORED)
        self.assertIsNone(delta)

    def test_a_shift_on_another_axis_does_not_excuse_the_value(self):
        # A longitudinal shift must not be used to explain a vertical field.
        state, _delta = frame_module._storage_state(
            "baseline", 1.0, 3.0, self.shift(longitudinal=2.0),
        )
        self.assertEqual(state, frame_module.NOT_STORED)

    def test_an_unmeasurable_shift_cannot_excuse_a_mismatch(self):
        shift = {"vertical": {"delta": None, "status": "unmeasurable"}}
        state, _delta = frame_module._storage_state(
            "baseline", 1.0, 2.0, shift)
        self.assertEqual(state, frame_module.NOT_STORED)


class RegistrationTests(unittest.TestCase):
    def test_the_tool_descriptions_declare_mutability(self):
        tools = {t.name: t for t in server.mcp._tool_manager.list_tools()}
        self.assertTrue(
            tools["get_frame_of_reference"].description.startswith("READ-ONLY.")
        )
        self.assertTrue(
            tools["set_frame_of_reference"].description.startswith("WRITE_STATE.")
        )

    def test_the_write_tool_exposes_its_confirmation_and_auto_inputs(self):
        tools = {t.name: t for t in server.mcp._tool_manager.list_tools()}
        properties = tools["set_frame_of_reference"].parameters["properties"]
        for name in ("confirm", "auto_from_hull", "draft_m", "baseline",
                     "long_datum", "vert_datum"):
            self.assertIn(name, properties)

    def test_neither_tool_is_behind_an_optional_tier(self):
        for name in ("get_frame_of_reference", "set_frame_of_reference"):
            self.assertIsNone(getattr(server, name).maxsurf_tier)


if __name__ == "__main__":
    unittest.main()
