"""Maxsurf Modeler frame-of-reference (datum) boundary.

Every COM member used here was read from live ``ITypeInfo`` and is recorded in
``private discovery notes (not distributed)`` under "Lines-plan and reference-frame discovery". Nothing on
``IFrameOfReference`` is invented: it exposes no native method, seven read/write
``R8`` positions, two read/write datum-selector enums, and five read-only
hull-derived ``Find*`` values plus the indexed ``InternalZero``.

Two things make this a distinct boundary rather than part of
:mod:`maxsurf_mcp.modeler`:

* **It is the datum, not the geometry.** Writing it moves the origin every other
  coordinate is reported against. A control-point edit changes one point; a
  datum edit silently changes the meaning of every reading taken before and
  after it.
* **The zero point is measured, not assumed.** ``ITypeInfo`` proves that
  ``LongDatum`` and ``VertDatum`` select a datum and that ``InternalZero``
  reports a zero position per axis. It does *not* prove that the zero point is
  bound to the selected element. Rather than assert that relationship, this
  module reads ``InternalZero`` on all three axes before and after the write and
  reports the shift it actually observed.

Units: the Modeler automation interface is metres and kilograms regardless of
the unit preference set in the Maxsurf Modeler user interface, which changes
only what that interface displays. Every value here is therefore metres. That
guarantee is documented for BentleyModeler specifically and must not be carried
across to Stability, where no equivalent statement has been found.
"""

from __future__ import annotations

import math
from typing import Any

from . import audit, com
from . import constants as comconstants
from .audit import Channel, Classification
from .errors import MaxsurfCOMError, MaxsurfSafetyError, MaxsurfValidationError
from .guard import guarded, register

#: The proven object path. Recorded so the payload names its own source.
FRAME_PATH = "Design.FrameOfReference"

#: Writable ``R8`` datum positions: (payload field, proven COM member).
POSITION_FIELDS: tuple[tuple[str, str], ...] = (
    ("aft_perp", "AftPerp"),
    ("fwd_perp", "FwdPerp"),
    ("midships", "Midships"),
    ("datum_wl", "DatumWL"),
    ("baseline", "Baseline"),
    ("other_long_datum", "OtherLongDatum"),
    ("other_vert_datum", "OtherVertDatum"),
)
POSITION_MEMBERS: dict[str, str] = dict(POSITION_FIELDS)

#: Read-only ``R8`` values Maxsurf derives from the hull. These are the only
#: proven sources for auto_from_hull. There is no FindDWL member, which is why
#: DatumWL can never be derived and must always come from the caller.
HULL_DERIVED_FIELDS: tuple[tuple[str, str], ...] = (
    ("find_aft_dwl", "FindAftDWL"),
    ("find_fwd_dwl", "FindFwdDWL"),
    ("find_aft_ext", "FindAftExt"),
    ("find_fwd_ext", "FindFwdExt"),
    ("find_base", "FindBase"),
)

#: ``InternalZero(direction)`` takes the previously proven msDirectionType.
INTERNAL_ZERO_DIRECTIONS: tuple[tuple[str, str], ...] = (
    ("longitudinal", "msDTLongitudinal"),
    ("transverse", "msDTTransverse"),
    ("vertical", "msDTVertical"),
)

#: The two datum selectors, and which writable position each enum member picks.
#: The enum *numbers* are never hard-coded; only member names appear here, and
#: their values are resolved from the generated type library at runtime.
DATUM_SELECTORS: dict[str, dict[str, Any]] = {
    "long_datum": {
        "member": "LongDatum",
        "prefix": "msLDT",
        "axis": "longitudinal",
        "targets": {
            "msLDTAftPerp": "aft_perp",
            "msLDTFwdPerp": "fwd_perp",
            "msLDTMidships": "midships",
            "msLDTOther": "other_long_datum",
        },
    },
    "vert_datum": {
        "member": "VertDatum",
        "prefix": "msVDT",
        "axis": "vertical",
        "targets": {
            "msVDTDatumWL": "datum_wl",
            "msVDTBaseline": "baseline",
            "msVDTOther": "other_vert_datum",
        },
    },
}

#: auto_from_hull modes. ``dwl`` is deliberately two-staged; see _plan_changes.
AUTO_MODES = ("baseline", "dwl", "extents")

#: Modes proven correct by the M0 live acceptance run. ``baseline`` writes one
#: field, and single-field writes stored exactly what they were given in step 3
#: and in both 1 mm probes.
VERIFIED_AUTO_MODES = ("baseline",)

#: Modes withdrawn from the tool surface. They are multi-field, and a
#: multi-field call can rebase the coordinate frame between one write and the
#: next while the remaining values are still the ones computed against the old
#: frame. Live, ``dwl`` asked for a 4.5 m waterline and produced a 10.08 m one.
#: The planning code is kept, and unit-tested, because the two-stage ordering it
#: encodes is correct and is not what broke.
QUARANTINED_AUTO_MODES = ("dwl", "extents")

#: Fields ``auto_from_hull="dwl"`` can only resolve after DatumWL is written.
DWL_DEFERRED_FIELDS = ("aft_perp", "fwd_perp", "midships")

#: Tolerance for "already at the requested value" and for checking that Maxsurf
#: stored what it was given. Positions are metres, so this is a nanometre: far
#: below any meaningful hull dimension and far above float noise.
TOLERANCE = 1e-9

#: Which axis each writable position lives on. Needed to reconcile a stored
#: value against the origin shift measured on that same axis.
FIELD_AXIS: dict[str, str] = {
    "aft_perp": "longitudinal",
    "fwd_perp": "longitudinal",
    "midships": "longitudinal",
    "other_long_datum": "longitudinal",
    "datum_wl": "vertical",
    "baseline": "vertical",
    "other_vert_datum": "vertical",
}

#: How a written value ended up being reported. Replaces a bare boolean, which
#: could not tell "Maxsurf refused this" apart from "Maxsurf stored it and then
#: the origin moved underneath the reading".
STORED = "stored"
STORED_FRAME_SHIFTED = "stored_frame_shifted"
NOT_STORED = "not_stored"


# -- reading -----------------------------------------------------------------

def _read_member(obj: Any, member: str, cast: Any = float) -> dict[str, Any]:
    """Read one COM member, keeping "unreadable" distinct from "zero".

    A datum that was never configured genuinely reads 0.0, and a getter that
    raised must never be reported as the same thing.
    """
    probed = com.probe(obj, member)
    if probed["status"] == "read":
        try:
            value = cast(probed["value"])
        except (TypeError, ValueError) as exc:
            return {"value": None, "status": "uncastable", "member": member,
                    "detail": str(exc)[:200]}
        return {"value": value, "status": "read", "member": member,
                "detail": None}
    attempt = (probed["attempts"] or [{}])[0]
    return {
        "value": None,
        "status": attempt.get("status") or probed["status"],
        "member": member,
        "detail": attempt.get("error"),
    }


def _read_indexed(obj: Any, member: str, index: int) -> dict[str, Any]:
    """Read an indexed property without assuming the pywin32 projection style."""
    try:
        accessor = getattr(obj, member)
    except AttributeError:
        return {"value": None, "status": "absent", "member": member,
                "detail": None}
    except Exception as exc:  # noqa: BLE001 - COM getters can fail by design
        return {"value": None, "status": "failed", "member": member,
                "detail": str(exc)[:200]}
    try:
        raw = accessor(index) if callable(accessor) else accessor[index]
    except Exception as exc:  # noqa: BLE001
        return {"value": None, "status": "failed", "member": member,
                "detail": str(exc)[:200]}
    if raw is None:
        return {"value": None, "status": "null", "member": member,
                "detail": None}
    return {"value": float(raw), "status": "read", "member": member,
            "detail": None}


def _enum_map(app: Any, prefix: str) -> dict[str, int]:
    """Resolve an enum family from the generated type library at runtime."""
    discovered = comconstants.discover(app, name_filter=prefix)
    return {
        name: int(number)
        for name, number in discovered["constants"].items()
        if name.startswith(prefix) and not isinstance(number, bool)
    }


def _enum_name(members: dict[str, int], value: Any) -> str | None:
    for name, number in members.items():
        if number == value:
            return name
    return None


def _read_datum_selectors(app: Any, frame: Any) -> dict[str, dict[str, Any]]:
    """Read LongDatum and VertDatum, naming the enum member each one holds."""
    selectors: dict[str, dict[str, Any]] = {}
    for field, spec in DATUM_SELECTORS.items():
        members = _enum_map(app, spec["prefix"])
        entry = _read_member(frame, spec["member"], cast=int)
        entry["enum_name"] = (
            _enum_name(members, entry["value"]) if entry["status"] == "read"
            else None
        )
        entry["available"] = sorted(spec["targets"])
        selectors[field] = entry
    return selectors


def _read_internal_zero(app: Any, frame: Any) -> dict[str, dict[str, Any]]:
    """Read the zero position on all three axes. This is the shift evidence."""
    directions = _enum_map(app, "msDT")
    zeros: dict[str, dict[str, Any]] = {}
    for axis, enum_name in INTERNAL_ZERO_DIRECTIONS:
        number = directions.get(enum_name)
        if number is None:
            zeros[axis] = {
                "value": None, "status": "enum_unresolved",
                "member": "InternalZero", "direction": enum_name,
                "detail": f"{enum_name} is not in the generated type library",
            }
            continue
        entry = _read_indexed(frame, "InternalZero", number)
        entry["direction"] = enum_name
        zeros[axis] = entry
    return zeros


def _frame_object(app: Any) -> Any:
    """Resolve Design.FrameOfReference, distinguishing each failure."""
    design = com.probe(app, "Design")
    if design["status"] != "read":
        raise MaxsurfCOMError(
            "no Design object is available; open a design in Maxsurf Modeler "
            "first",
            module="modeler", path=FRAME_PATH,
        )
    frame = com.probe(design["value"], "FrameOfReference")
    if frame["status"] != "read":
        raise MaxsurfCOMError(
            "Design.FrameOfReference could not be resolved",
            module="modeler", path=FRAME_PATH,
            probe_status=frame["status"], attempts=frame["attempts"],
        )
    return frame["value"]


def _read_state(app: Any, frame: Any) -> dict[str, Any]:
    """Read every proven member of IFrameOfReference in one pass."""
    return {
        "positions": {
            field: _read_member(frame, member)
            for field, member in POSITION_FIELDS
        },
        "hull_derived": {
            field: _read_member(frame, member)
            for field, member in HULL_DERIVED_FIELDS
        },
        "datum": _read_datum_selectors(app, frame),
        "internal_zero": _read_internal_zero(app, frame),
    }


def _zero_point_bindings(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Report which writable position each datum selector currently picks.

    ``ITypeInfo`` proves the selectors and the positions exist; it does not
    prove that moving the selected position moves the zero point. That is an
    inference from the member names, so it is labelled as one, and the measured
    ``InternalZero`` shift is what actually settles the question.
    """
    bindings: dict[str, dict[str, Any]] = {}
    for field, spec in DATUM_SELECTORS.items():
        enum_name = state["datum"][field].get("enum_name")
        bindings[spec["axis"]] = {
            "selector": field,
            "selector_member": spec["member"],
            "enum_name": enum_name,
            "bound_field": spec["targets"].get(enum_name) if enum_name else None,
            "resolved": bool(enum_name and enum_name in spec["targets"]),
            "proven": False,
            "basis": "documented: the automation help calls LongDatum/VertDatum "
                     "the zero point datum types, but ITypeInfo does not prove "
                     "it, so the measured internal_zero shift is the evidence",
        }
    return bindings


def _frame_applied(state: dict[str, Any]) -> dict[str, Any]:
    """Whether the configured frame of reference is already in effect.

    This distinction is not cosmetic. During the M0 live acceptance run, the
    *first* write against a design whose frame of reference was configured but
    not applied rebased the reported origin on **both** axes -- by ``+midships``
    longitudinally and ``-DatumWL`` vertically -- even though the field written
    (``Baseline``) was bound to neither. A second write of that same field moved
    nothing. So the same call, on the same field, shifts the origin by 28 m or
    by nothing depending on state the caller cannot see.

    The signal: in the applied state the position each selector picks reads
    approximately zero, because it *is* the origin. Live evidence, before and
    after that first write:

    ```text
    before   midships = 28.261717796   datum_wl = 5.576552629
    after    midships =  0.0           datum_wl = 0.0
    ```

    This is a falsifiable prediction from one design, not a proven rule, so it
    is reported as an observation and never used to permit a write.
    """
    bindings = _zero_point_bindings(state)
    axes: dict[str, Any] = {}
    for axis, binding in bindings.items():
        field = binding["bound_field"]
        entry = state["positions"].get(field) if field else None
        if entry is None or entry["status"] != "read":
            axes[axis] = {"bound_field": field, "value": None,
                          "applied": None, "reason": "binding unresolved"
                          if field is None else "bound position unreadable"}
            continue
        axes[axis] = {
            "bound_field": field,
            "value": entry["value"],
            "applied": abs(entry["value"]) <= TOLERANCE,
            "reason": None,
        }
    verdicts = [a["applied"] for a in axes.values()]
    if any(v is None for v in verdicts):
        applied: bool | None = None
    else:
        applied = all(verdicts)
    return {
        "applied": applied,
        "axes": axes,
        "basis": "the position each datum selector picks reads ~0 once the "
                 "frame is in effect; observed once during the M0 live "
                 "acceptance run, not proven",
    }


def _state_warnings(state: dict[str, Any]) -> list[str]:
    """Diagnostics a caller needs before trusting any hydrostatic reading."""
    warnings: list[str] = []
    positions = state["positions"]
    readable = [e for e in positions.values() if e["status"] == "read"]
    if len(readable) == len(POSITION_FIELDS) and all(
            abs(e["value"]) <= TOLERANCE for e in readable):
        warnings.append(
            "every datum position is zero, so the frame of reference has never "
            "been configured; draft, KB and the vertical centres are then "
            "measured from an arbitrary origin and can legitimately come back "
            "negative (private discovery notes, Phase 3C.2)"
        )
    base = state["hull_derived"]["find_base"]
    baseline = positions["baseline"]
    if base["status"] == "read" and baseline["status"] == "read":
        offset = base["value"] - baseline["value"]
        if abs(offset) > TOLERANCE:
            warnings.append(
                f"Baseline is {baseline['value']} but the hull's own FindBase "
                f"is {base['value']}, an offset of {offset} m; vertical "
                f"readings are measured from the Baseline, not from the hull"
            )
    unreadable = sorted(
        field for field, entry in positions.items() if entry["status"] != "read"
    )
    if unreadable:
        warnings.append(
            f"these datum positions could not be read: {', '.join(unreadable)}"
        )
    applied = _frame_applied(state)
    if applied["applied"] is False:
        pending = {
            axis: entry["value"] for axis, entry in applied["axes"].items()
            if entry["applied"] is False
        }
        warnings.append(
            f"the frame of reference appears configured but not yet applied "
            f"({', '.join(f'{a}={v}' for a, v in sorted(pending.items()))} "
            f"instead of ~0); the next write of any datum field is expected to "
            f"rebase the reported origin on both axes by about these amounts"
        )
    return warnings


def _state_payload(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "datum": state["datum"],
        "positions": state["positions"],
        "hull_derived": state["hull_derived"],
        "internal_zero": state["internal_zero"],
        "zero_point_bindings": _zero_point_bindings(state),
        "frame_applied": _frame_applied(state),
    }


def _storage_state(field: str, requested: float, stored: Any,
                   shift: dict[str, Any]) -> tuple[str, float | None]:
    """Reconcile what Maxsurf reports against what was asked for.

    A stored value read after the origin moved is reported in the new frame, so
    a direct comparison makes a successful write look like a rejected one. The
    reconciliation is exact:

    ```text
    after == requested + InternalZero_delta(that field's axis)
    ```

    verified live on three writes with errors of 0.0, 0.0 and 1.1e-16.
    """
    if stored is None:
        return NOT_STORED, None
    if abs(float(stored) - float(requested)) <= TOLERANCE:
        return STORED, 0.0
    axis = FIELD_AXIS.get(field)
    delta = (shift.get(axis) or {}).get("delta") if axis else None
    if delta is None:
        return NOT_STORED, None
    if abs(float(stored) - (float(requested) + float(delta))) <= TOLERANCE:
        return STORED_FRAME_SHIFTED, float(delta)
    return NOT_STORED, None


UNITS_NOTE = (
    "metres; the BentleyModeler automation interface is SI regardless of the "
    "unit preference shown in the Maxsurf Modeler user interface"
)


@guarded(classification=Classification.READ, module="frame",
         channel=Channel.MCP_COM)
def get_frame_of_reference() -> str:
    """READ-ONLY. Report the Modeler frame of reference and its hull-derived values.

    Returns the seven writable datum positions, both datum selectors with their
    enum names, the five read-only hull-derived Find values, and the zero
    position on all three axes. Every field is {value, status, member, detail},
    so a getter that failed is never reported as a zero.

    Values are metres. Warnings flag the two conditions that make hydrostatic
    output untrustworthy: a frame of reference that was never configured, and a
    Baseline that does not sit on the hull.
    """
    app = com.connect("modeler")
    frame = _frame_object(app)
    state = _read_state(app, frame)
    return com.to_json({
        "module": "modeler",
        "path": FRAME_PATH,
        "units": UNITS_NOTE,
        **_state_payload(state),
        "warnings": _state_warnings(state),
    })


# -- write planning ----------------------------------------------------------

def _validate_number(field: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MaxsurfValidationError(
            f"{field} must be a number", field=field, received=repr(value)
        )
    number = float(value)
    if not math.isfinite(number):
        raise MaxsurfValidationError(
            f"{field} must be a finite number", field=field,
            received=repr(value),
        )
    return number


def _resolve_enum_request(app: Any, field: str, requested: str) -> dict[str, Any]:
    """Turn a datum-selector enum name into its runtime number."""
    spec = DATUM_SELECTORS[field]
    members = _enum_map(app, spec["prefix"])
    permitted = {name: number for name, number in members.items()
                 if name in spec["targets"]}
    canonical = next(
        (name for name in permitted
         if name.casefold() == requested.strip().casefold()),
        None,
    )
    if canonical is None:
        raise MaxsurfValidationError(
            f"unknown {spec['member']} enum name",
            field=field, requested=requested,
            available=sorted(permitted) or sorted(spec["targets"]),
        )
    return {"field": field, "member": spec["member"],
            "enum_name": canonical, "value": permitted[canonical]}


def _hull_value(state: dict[str, Any], key: str, mode: str) -> float:
    """Read one hull-derived source, refusing before any write if it failed."""
    entry = state["hull_derived"][key]
    if entry["status"] != "read":
        raise MaxsurfValidationError(
            f"auto_from_hull={mode!r} needs {entry['member']}, which could not "
            f"be read; nothing was written",
            mode=mode, source=entry["member"], status=entry["status"],
            detail=entry["detail"],
            hint="open a design containing a hull surface in Maxsurf Modeler",
        )
    return float(entry["value"])


def _change(field: str, value: float, source: str, stage: int) -> dict[str, Any]:
    return {
        "field": field,
        "member": POSITION_MEMBERS[field],
        "value": value,
        "source": source,
        "stage": stage,
    }


def _plan_changes(explicit: dict[str, float], mode: str, draft_m: float | None,
                  state: dict[str, Any]) -> dict[str, Any]:
    """Split the requested datum edit into an ordered plan.

    Stage one holds every value that is known before anything is written. Stage
    two exists only for ``auto_from_hull="dwl"``, because ``FindAftDWL`` and
    ``FindFwdDWL`` locate where the *design waterline* meets the hull: read
    before ``DatumWL`` has been written they report the old waterline, so the
    perpendiculars would be set from a waterline that no longer exists. The
    manual's own procedure -- Find Base, enter the DWL height, then Set to DWL
    for each perpendicular -- encodes the same dependency, and this is where it
    is enforced.
    """
    notes: list[str] = []
    derived: dict[str, float] = {}

    if mode:
        derived["baseline"] = _hull_value(state, "find_base", mode)
    if mode == "extents":
        derived["aft_perp"] = _hull_value(state, "find_aft_ext", mode)
        derived["fwd_perp"] = _hull_value(state, "find_fwd_ext", mode)
    if mode == "dwl":
        base = explicit.get("baseline", derived.get("baseline"))
        derived["datum_wl"] = base + float(draft_m)
        notes.append(
            f"DatumWL was derived as Baseline ({base}) + draft_m ({draft_m}) "
            f"= {derived['datum_wl']}. There is no FindDWL member, so a design "
            f"waterline can only ever come from the caller."
        )

    deferred = (
        [field for field in DWL_DEFERRED_FIELDS if field not in explicit]
        if mode == "dwl" else []
    )
    # An explicitly supplied perpendicular still belongs to stage two under the
    # dwl procedure, so the whole set lands after DatumWL rather than half of it
    # before and half after.
    stage_two_fields = list(DWL_DEFERRED_FIELDS) if mode == "dwl" else []

    # Baseline first, then DatumWL, then everything else: the manual's procedure
    # establishes the vertical datum before the waterline that is measured from
    # it, and the declaration order in POSITION_FIELDS does not.
    ordered = ["baseline", "datum_wl"] + [
        field for field, _member in POSITION_FIELDS
        if field not in ("baseline", "datum_wl")
    ]

    stage_one: list[dict[str, Any]] = []
    for field in ordered:
        if field in stage_two_fields:
            continue
        if field in explicit:
            stage_one.append(_change(field, explicit[field], "argument", 1))
        elif field in derived:
            stage_one.append(_change(field, derived[field], f"auto:{mode}", 1))

    if mode == "extents":
        # Midships is the midpoint of the perpendiculars. That is the standard
        # convention, not an API fact, so it is stated as an assumption and can
        # be overridden by passing midships explicitly.
        if "midships" not in explicit:
            aft = explicit.get("aft_perp", derived["aft_perp"])
            fwd = explicit.get("fwd_perp", derived["fwd_perp"])
            stage_one.append(
                _change("midships", (aft + fwd) / 2.0, f"auto:{mode}", 1)
            )
        notes.append(
            "Midships was taken as the midpoint of the perpendiculars. This is "
            "the usual convention, not something the COM interface defines; "
            "pass midships explicitly to override it."
        )
    if mode == "dwl":
        notes.append(
            "AftPerp, FwdPerp and Midships are written after DatumWL, and "
            "FindAftDWL/FindFwdDWL are re-read in between, because they locate "
            "the waterline that DatumWL has just moved."
        )
        if "midships" not in explicit:
            notes.append(
                "Midships will be taken as the midpoint of the perpendiculars. "
                "This is the usual convention, not something the COM interface "
                "defines; pass midships explicitly to override it."
            )

    return {
        "mode": mode or None,
        "draft_m": draft_m,
        "stage_one": stage_one,
        "stage_two_fields": stage_two_fields,
        "deferred_fields": deferred,
        "notes": notes,
    }


def _resolve_stage_two(frame: Any, explicit: dict[str, float], mode: str,
                       ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Resolve the dwl perpendiculars *after* DatumWL has been written."""
    sources: dict[str, Any] = {}
    needs_reread = ("aft_perp" not in explicit) or ("fwd_perp" not in explicit)
    if needs_reread:
        for key, member in (("find_aft_dwl", "FindAftDWL"),
                            ("find_fwd_dwl", "FindFwdDWL")):
            sources[key] = _read_member(frame, member)
            if sources[key]["status"] != "read":
                raise MaxsurfCOMError(
                    f"{member} could not be re-read after DatumWL was written, "
                    f"so the perpendiculars cannot be set from the new "
                    f"waterline; Baseline and DatumWL are already written",
                    mode=mode, source=member, status=sources[key]["status"],
                    detail=sources[key]["detail"],
                )

    aft = explicit.get("aft_perp")
    aft_source = "argument"
    if aft is None:
        aft = float(sources["find_aft_dwl"]["value"])
        aft_source = f"auto:{mode}:FindAftDWL(after DatumWL)"
    fwd = explicit.get("fwd_perp")
    fwd_source = "argument"
    if fwd is None:
        fwd = float(sources["find_fwd_dwl"]["value"])
        fwd_source = f"auto:{mode}:FindFwdDWL(after DatumWL)"

    changes = [
        _change("aft_perp", aft, aft_source, 2),
        _change("fwd_perp", fwd, fwd_source, 2),
    ]
    if "midships" in explicit:
        changes.append(_change("midships", explicit["midships"], "argument", 2))
    else:
        changes.append(
            _change("midships", (aft + fwd) / 2.0, f"auto:{mode}:midpoint", 2)
        )
    return changes, sources


# -- write execution ---------------------------------------------------------

def _shifting_fields(state: dict[str, Any], position_fields: set[str],
                     selector_fields: set[str]) -> tuple[list[str], list[str]]:
    """Classify which requested changes can move the zero point.

    Fails safe: when a datum selector cannot be resolved, every requested
    position is treated as capable of shifting the zero point rather than
    assuming it is harmless.
    """
    bindings = _zero_point_bindings(state)
    unresolved = [b["selector"] for b in bindings.values() if not b["resolved"]]
    warnings: list[str] = []
    if selector_fields:
        # Rebinding the zero point to a different element moves it whenever the
        # two elements hold different values.
        shifting = set(selector_fields)
    else:
        shifting = set()
    if unresolved:
        warnings.append(
            f"these datum selectors could not be resolved ({', '.join(unresolved)}), "
            f"so every requested position change is treated as capable of "
            f"moving the zero point"
        )
        shifting |= position_fields
    else:
        bound = {b["bound_field"] for b in bindings.values() if b["bound_field"]}
        shifting |= position_fields & bound
    return sorted(shifting), warnings


def _apply(frame: Any, changes: list[dict[str, Any]],
           applied: list[dict[str, Any]], planned: list[str]) -> None:
    """Write one batch, reporting the true applied count if it fails part-way."""
    for change in changes:
        try:
            setattr(frame, change["member"], change["value"])
        except Exception as exc:  # noqa: BLE001 - normalised below
            raise MaxsurfCOMError(
                "a datum property write failed part-way through; the frame of "
                "reference is partially modified",
                cause=exc,
                failed_field=change["field"],
                failed_member=change["member"],
                requested_value=change["value"],
                applied_before_failure=[c["field"] for c in applied],
                planned_fields=planned,
            ) from exc
        applied.append(change)


def _drop_no_ops(changes: list[dict[str, Any]], state: dict[str, Any],
                 ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate changes that would alter something from ones that would not."""
    effective: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    for change in changes:
        current = state["positions"][change["field"]]
        if (current["status"] == "read"
                and abs(current["value"] - change["value"]) <= TOLERANCE):
            unchanged.append({
                **change,
                "reason": "already at the requested value",
            })
            continue
        effective.append(change)
    return effective, unchanged


def _selector_no_ops(selectors: list[dict[str, Any]], state: dict[str, Any],
                     ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    effective: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    for selector in selectors:
        current = state["datum"][selector["field"]]
        if current["status"] == "read" and current["value"] == selector["value"]:
            unchanged.append({**selector,
                              "reason": "already at the requested value"})
            continue
        effective.append(selector)
    return effective, unchanged


def _zero_point_shift(before: dict[str, Any],
                      after: dict[str, Any]) -> dict[str, Any]:
    """Measure how far the zero point actually moved on each axis."""
    shift: dict[str, Any] = {}
    for axis, _enum_name_ in INTERNAL_ZERO_DIRECTIONS:
        start, end = before["internal_zero"][axis], after["internal_zero"][axis]
        if start["status"] == "read" and end["status"] == "read":
            shift[axis] = {
                "before": start["value"],
                "after": end["value"],
                "delta": end["value"] - start["value"],
                "status": "measured",
            }
        else:
            shift[axis] = {
                "before": start["value"], "after": end["value"], "delta": None,
                "status": "unmeasurable",
                "detail": start["detail"] or end["detail"],
            }
    return shift


def _record_before_state(payload: dict[str, Any]) -> None:
    """Persist the pre-write datum, before any property is touched.

    ``stage="intent"`` on a WRITE_STATE record makes this must-audit: if it
    cannot be written the whole operation is refused. A datum write that left no
    record of what the datum used to be would be unrecoverable, because the
    original values exist nowhere else.
    """
    audit.get_log().record(
        stage="intent",
        tool="set_frame_of_reference_before_state",
        module="frame",
        classification=Classification.WRITE_STATE,
        channel=Channel.INTERNAL,
        result_summary=payload,
    )


def _collect_explicit(values: dict[str, Any]) -> dict[str, float]:
    return {
        field: _validate_number(field, value)
        for field, value in values.items() if value is not None
    }


@guarded(classification=Classification.WRITE_STATE, module="frame",
         channel=Channel.MCP_COM)
def set_frame_of_reference(
    aft_perp: float | None = None,
    fwd_perp: float | None = None,
    midships: float | None = None,
    datum_wl: float | None = None,
    baseline: float | None = None,
    other_long_datum: float | None = None,
    other_vert_datum: float | None = None,
    long_datum: str = "",
    vert_datum: str = "",
    auto_from_hull: str = "",
    draft_m: float | None = None,
    confirm: bool = False,
) -> str:
    """WRITE_STATE. Set the Modeler frame of reference. This moves the datum.

    Every value is in metres. Omitted positions are left untouched. The pre-write
    datum is written to the audit log before any property is set.

    auto_from_hull has one verified mode:
      baseline - Baseline from FindBase. One field, which is what hydrostatics
        needs: it puts the vertical datum on the hull so draft and KB are
        measured from the keel rather than from an arbitrary origin.
    The multi-field modes dwl and extents are withdrawn and raise. Live testing
    showed a multi-field call can rebase the frame mid-sequence, and dwl asked
    for a 4.5 m waterline while producing a 10.08 m one. Set those fields
    explicitly instead.

    DatumWL is never derived; no FindDWL member exists.

    confirm=True is required for every write. Which fields are bound to the zero
    point is still reported, but it cannot gate the write: a first write against
    a configured-but-unapplied frame rebases both axes whatever field it touches.
    The result reports the InternalZero shift actually measured on all three axes.
    """
    mode = (auto_from_hull or "").strip().casefold()
    if mode and mode not in AUTO_MODES:
        raise MaxsurfValidationError(
            "unknown auto_from_hull mode", requested=auto_from_hull,
            available=list(VERIFIED_AUTO_MODES),
        )
    if mode in QUARANTINED_AUTO_MODES:
        raise MaxsurfValidationError(
            "multi-field auto modes are not verified; dwl produced a 5.58 m "
            "waterline error in live testing. Use auto_from_hull='baseline' or "
            "set fields explicitly.",
            requested=mode,
            available=list(VERIFIED_AUTO_MODES),
            quarantined=list(QUARANTINED_AUTO_MODES),
            reason="a multi-field write can rebase the coordinate frame between "
                   "one write and the next, leaving the remaining values "
                   "expressed in a frame that no longer exists",
            evidence="private discovery notes, Completing the M0 acceptance run",
        )
    if mode == "dwl" and draft_m is None:
        raise MaxsurfValidationError(
            "auto_from_hull='dwl' requires draft_m, the design waterline height "
            "above the baseline in metres; there is no FindDWL member to derive "
            "it from",
            mode=mode,
        )
    if draft_m is not None and mode != "dwl":
        raise MaxsurfValidationError(
            "draft_m only applies to auto_from_hull='dwl'; it would otherwise be "
            "silently ignored",
            draft_m=draft_m, auto_from_hull=auto_from_hull or None,
        )
    if draft_m is not None:
        draft_m = _validate_number("draft_m", draft_m)

    explicit = _collect_explicit({
        "aft_perp": aft_perp,
        "fwd_perp": fwd_perp,
        "midships": midships,
        "datum_wl": datum_wl,
        "baseline": baseline,
        "other_long_datum": other_long_datum,
        "other_vert_datum": other_vert_datum,
    })
    selector_requests = {
        field: text.strip()
        for field, text in (("long_datum", long_datum),
                            ("vert_datum", vert_datum))
        if isinstance(text, str) and text.strip()
    }
    if not explicit and not selector_requests and not mode:
        raise MaxsurfValidationError(
            "nothing was requested; supply at least one datum value, a datum "
            "selector, or auto_from_hull",
            available_positions=[field for field, _m in POSITION_FIELDS],
            available_modes=list(AUTO_MODES),
        )

    app = com.connect("modeler")
    frame = _frame_object(app)
    before = _read_state(app, frame)

    selectors = [
        _resolve_enum_request(app, field, text)
        for field, text in selector_requests.items()
    ]
    plan = _plan_changes(explicit, mode, draft_m, before)

    stage_one, unchanged = _drop_no_ops(plan["stage_one"], before)
    selectors, selector_unchanged = _selector_no_ops(selectors, before)

    # Stage-two values are unknown until DatumWL has been written, so those
    # fields are always treated as capable of changing.
    position_fields = ({c["field"] for c in stage_one}
                       | set(plan["stage_two_fields"]))
    shifting, shift_warnings = _shifting_fields(
        before, position_fields, {s["field"] for s in selectors}
    )

    if not position_fields and not selectors:
        raise MaxsurfValidationError(
            "the frame of reference already holds every requested value; "
            "nothing was written",
            unchanged=[c["field"] for c in unchanged]
            + [s["field"] for s in selector_unchanged],
        )

    # confirm gates every write, not only the ones the binding model predicts
    # will shift the origin. Not because the binding model is unreliable -- it
    # held exactly under live probing, an unbound write moving nothing and a
    # bound write moving its own axis by precisely the written delta -- but
    # because it is blind to the activation event. The first write against a
    # configured-but-unapplied frame rebased both axes by 28 m and 5.6 m while
    # touching a field bound to neither, and a later write of that same field
    # moved nothing. The classification cannot see which case it is in before
    # writing, so it reports and does not decide.
    applied_state = _frame_applied(before)
    if not confirm:
        raise MaxsurfSafetyError(
            "writing the frame of reference moves the datum that every other "
            "coordinate is reported against; nothing was written. Re-issue with "
            "confirm=true if that is intended",
            planned_fields=sorted(position_fields
                                  | {s["field"] for s in selectors}),
            zero_point_shifting_fields=shifting,
            frame_applied=applied_state,
            zero_point_bindings=_zero_point_bindings(before),
            current_positions={
                field: entry["value"]
                for field, entry in before["positions"].items()
            },
            warnings=shift_warnings + ([
                "this frame of reference appears configured but not applied, so "
                "this write is expected to rebase the reported origin on both "
                "axes regardless of which field it touches"
            ] if applied_state["applied"] is False else []),
        )

    planned_fields = sorted(position_fields | {s["field"] for s in selectors})
    _record_before_state({
        "path": FRAME_PATH,
        "units": UNITS_NOTE,
        "before": _state_payload(before),
        "plan": {
            "mode": plan["mode"],
            "draft_m": plan["draft_m"],
            "stage_one": stage_one,
            "stage_two_fields": plan["stage_two_fields"],
            "deferred_fields": plan["deferred_fields"],
            "selectors": selectors,
            "notes": plan["notes"],
        },
        "planned_fields": planned_fields,
        "zero_point_shifting_fields": shifting,
        "confirm": confirm,
    })

    applied: list[dict[str, Any]] = []
    stage_two_sources: dict[str, Any] = {}
    _apply(frame, stage_one, applied, planned_fields)

    if plan["stage_two_fields"]:
        stage_two, stage_two_sources = _resolve_stage_two(frame, explicit, mode)
        stage_two, stage_two_unchanged = _drop_no_ops(stage_two, before)
        unchanged.extend(stage_two_unchanged)
        _apply(frame, stage_two, applied, planned_fields)

    # Selectors are written last so a re-bind lands on final position values.
    for selector in selectors:
        try:
            setattr(frame, selector["member"], selector["value"])
        except Exception as exc:  # noqa: BLE001
            raise MaxsurfCOMError(
                "a datum selector write failed; the positions written before it "
                "are already applied",
                cause=exc,
                failed_field=selector["field"],
                failed_member=selector["member"],
                requested_value=selector["value"],
                applied_before_failure=[c["field"] for c in applied],
                planned_fields=planned_fields,
            ) from exc
        applied.append({**selector, "stage": 3, "source": "argument"})

    after = _read_state(app, frame)

    # The shift has to be known before the readback can be judged: a value
    # stored just before the origin moved is reported in the new frame, and
    # comparing it naively makes a successful write look like a refused one.
    shift = _zero_point_shift(before, after)

    changed = []
    for change in applied:
        field = change["field"]
        current = (after["positions"].get(field)
                   or after["datum"].get(field) or {})
        stored = current.get("value")
        storage, frame_shift = _storage_state(
            field, change["value"], stored, shift
        )
        changed.append({
            "field": field,
            "member": change["member"],
            "before": before["positions"].get(
                field, before["datum"].get(field, {})).get("value"),
            "requested": change["value"],
            "after": stored,
            "storage": storage,
            "frame_shift": frame_shift,
            "accepted": storage in (STORED, STORED_FRAME_SHIFTED),
            "source": change["source"],
            "stage": change["stage"],
            "enum_name": change.get("enum_name"),
        })

    warnings = list(shift_warnings)
    rejected = [c["field"] for c in changed if c["storage"] == NOT_STORED]
    if rejected:
        warnings.append(
            f"Maxsurf did not store the requested value for: "
            f"{', '.join(rejected)}; compare requested against after"
        )
    reframed = [c["field"] for c in changed
                if c["storage"] == STORED_FRAME_SHIFTED]
    if reframed:
        warnings.append(
            f"these values were stored and are reported in a shifted frame: "
            f"{', '.join(reframed)}; after equals requested plus the measured "
            f"internal_zero delta for that axis, so the write took effect"
        )
    moved = sorted(
        axis for axis, entry in shift.items()
        if entry["status"] == "measured" and abs(entry["delta"]) > TOLERANCE
    )
    if moved:
        warnings.append(
            f"the zero point moved on: {', '.join(moved)}; every coordinate "
            f"read against this datum before this call is offset by that amount"
        )
    warnings.extend(_state_warnings(after))

    return com.to_json({
        "module": "modeler",
        "path": FRAME_PATH,
        "units": UNITS_NOTE,
        "auto_from_hull": {
            "mode": plan["mode"],
            "draft_m": plan["draft_m"],
            "deferred_fields": plan["deferred_fields"],
            "stage_two_sources": stage_two_sources,
            "notes": plan["notes"],
        },
        "confirm": confirm,
        "zero_point_shifting_fields": shifting,
        "zero_point_shifting_note": (
            "reported, not enforced; confirm is required for every write "
            "because this classification cannot see whether the frame is "
            "already applied"
        ),
        "changed": changed,
        "unchanged": unchanged + selector_unchanged,
        "internal_zero_shift": shift,
        "before": _state_payload(before),
        "after": _state_payload(after),
        "warnings": warnings,
    })


TOOLS = (get_frame_of_reference, set_frame_of_reference)


def register_tools(mcp: Any) -> list[str]:
    """Register the frame-of-reference tools."""
    return register(mcp, TOOLS)
