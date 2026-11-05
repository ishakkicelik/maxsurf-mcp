"""Reconcile every installed native member with a route or an explicit boundary."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maxsurf_mcp import native_automation, native_inventory


TRANSPORT = frozenset({
    "QueryInterface", "AddRef", "Release", "GetTypeInfoCount", "GetTypeInfo",
    "GetIDsOfNames", "Invoke",
})


def run(output: Path) -> dict:
    inventory = native_inventory.get_inventory("all")
    owners = {guid: module for module, guid in native_inventory.LIBRARIES.items()}
    rows = []
    incomplete = []
    seen = set()
    for entry in inventory["entries"]:
        if entry["entry_kind"] != "member":
            continue
        if (entry.get("contract_status") == "metadata_error"
                or entry.get("error") or entry.get("errors") or not entry.get("key")):
            incomplete.append(entry)
            continue
        if entry["key"] in seen:
            continue  # Shared external interfaces retain one canonical contract.
        seen.add(entry["key"])
        module = owners.get(entry["library_guid"])
        if entry["name"] in TRANSPORT:
            blocker = "COM transport plumbing, not a Maxsurf engineering operation"
        elif module is None:
            blocker = "external interface without a managed Maxsurf application owner"
        elif entry["reachability"]["status"] != "metadata_reachable":
            blocker = "no installed-metadata candidate root or typed object-graph route"
        else:
            blocker = native_automation.contract_blocker(
                module, entry, allow_unmapped_effects=True)
        record = {
            "key": entry["key"], "module": module, "interface": entry["interface"],
            "member": entry["name"], "invocation_kind": entry["invocation_kind"],
            "status": "unsupported" if blocker else "metadata_admitted",
            "classification": "DIAGNOSTIC", "evidence": [
                "installed_metadata_only", "metadata_route_and_effect_policy_assessment",
            ],
            "reachability": entry["reachability"],
            "runtime_verification_required": True,
            "live_signature_verified": False,
            "operationally_validated": False,
            "engineering_acceptance": False,
        }
        if blocker:
            record["blocker"] = blocker
        else:
            record["route"] = (
                "native_call -> native_enumerate (IEnumVARIANT)"
                if native_automation._enum_contract(entry) else "native_call")
            record["constructor_evidence"] = "registered roots are candidates, not operational proof"
            record["effect_mode"] = (
                "explicit_unmapped_diagnostic"
                if native_automation.contract_blocker(module, entry)
                else "closed_policy"
            )
        rows.append(record)
    reconciliation = native_inventory.reconcile_coverage(inventory, rows)
    summary = {
        "inventory_digest": inventory["inventory_digest"],
        "inventory_complete": inventory["complete"],
        "reconciliation_complete": reconciliation["complete"] and not incomplete,
        "inventory_totals": inventory["totals"],
        "unique_member_contracts": len(rows),
        "route_totals": dict(Counter(row["status"] for row in rows)),
        "effect_modes": dict(Counter(row.get("effect_mode") for row in rows
                                     if row["status"] == "metadata_admitted")),
        "unsupported_reasons": dict(Counter(row["blocker"] for row in rows
                                            if row["status"] == "unsupported")),
        "unknown_keys": reconciliation["unknown_keys"],
        "unresolved_keys": reconciliation["unresolved_keys"],
        "engineering_acceptance": False,
        "incomplete_member_slots": len(incomplete),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as artifact:
        json.dump({"summary": summary, "members": rows, "incomplete_members": incomplete,
                   "inventory_diagnostics": [
                       entry for entry in inventory["entries"]
                       if entry.get("error") or entry.get("errors")]}, artifact,
                  indent=2, allow_nan=False)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="New local evidence file; existing files are never overwritten.")
    args = parser.parse_args()
    summary = run(args.output)
    print("COVERAGE_SUMMARY " + json.dumps(summary, allow_nan=False))
    if not summary["inventory_complete"] or not summary["reconciliation_complete"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
