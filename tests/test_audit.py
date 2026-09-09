"""Tests for the append-only audit log and the guard that feeds it."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path

from maxsurf_mcp import audit, config, guard
from maxsurf_mcp.audit import Channel, Classification, Outcome
from maxsurf_mcp.errors import MaxsurfCOMError, MaxsurfSafetyError

from .support import IsolatedEnvTestCase, ToolCollector


@guard.guarded(classification=Classification.READ, module="probe")
def _read_tool(value: int = 1) -> str:
    return json.dumps({"value": value})


@guard.guarded(classification=Classification.WRITE_GEOMETRY, module="probe")
def _write_tool(index: int = 1) -> str:
    audit.set_design_path(f"C:/designs/{index}.msd")
    return json.dumps({"written": index})


@guard.guarded(classification=Classification.WRITE_GEOMETRY, module="probe")
def _failing_write_tool() -> str:
    raise MaxsurfCOMError("Maxsurf refused the write", com_hresult="0x80004005")


@guard.guarded(classification=Classification.READ, module="probe")
def _unexpected_error_tool() -> str:
    raise ZeroDivisionError("not a Maxsurf error")


@guard.guarded(classification=Classification.DIAGNOSTIC, module="probe",
               tier=guard.TIER_DIAGNOSTICS)
def _diagnostic_tool() -> str:
    return json.dumps({"ok": True})


@guard.guarded(classification=Classification.READ, module="probe")
def _secret_tool(design: str = "a.msd", api_key: str = "hunter2") -> str:
    return json.dumps({"ok": True})


class SuccessRecordTests(IsolatedEnvTestCase):
    def test_a_read_writes_one_complete_record(self):
        _read_tool(7)
        records = self.records_for("_read_tool")
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["stage"], "complete")
        self.assertEqual(record["outcome"], Outcome.SUCCESS)
        self.assertEqual(record["classification"], Classification.READ)
        self.assertEqual(record["channel"], Channel.MCP_COM)
        self.assertEqual(record["module"], "probe")
        self.assertEqual(record["arguments"], {"value": 7})
        self.assertIsNotNone(record["timestamp_utc"])
        self.assertIsNotNone(record["duration_ms"])
        self.assertIn("payload_sha256", record["result_summary"])

    def test_a_write_records_intent_before_completion(self):
        _write_tool(3)
        records = self.records_for("_write_tool")
        self.assertEqual([r["stage"] for r in records], ["intent", "complete"])
        self.assertEqual(records[0]["audit_id"], records[1]["audit_id"])
        self.assertIsNone(records[0]["outcome"])
        self.assertEqual(records[1]["outcome"], Outcome.SUCCESS)

    def test_design_path_is_captured_when_a_tool_sets_it(self):
        _write_tool(5)
        complete = self.records_for("_write_tool")[-1]
        self.assertEqual(complete["design_path"], "C:/designs/5.msd")

    def test_design_path_does_not_leak_between_calls(self):
        _write_tool(5)
        _read_tool(1)
        self.assertIsNone(self.records_for("_read_tool")[0]["design_path"])


class FailureRecordTests(IsolatedEnvTestCase):
    def test_a_failed_write_is_recorded_with_the_typed_error(self):
        with self.assertRaises(MaxsurfCOMError):
            _failing_write_tool()
        records = self.records_for("_failing_write_tool")
        self.assertEqual([r["stage"] for r in records], ["intent", "complete"])
        complete = records[-1]
        self.assertEqual(complete["outcome"], Outcome.FAILURE)
        self.assertEqual(complete["error"]["code"], "com_error")
        self.assertEqual(complete["error"]["type"], "MaxsurfCOMError")

    def test_an_unexpected_exception_is_normalised_and_recorded(self):
        with self.assertRaises(MaxsurfCOMError) as caught:
            _unexpected_error_tool()
        self.assertTrue(caught.exception.details.get("unexpected"))
        complete = self.records_for("_unexpected_error_tool")[-1]
        self.assertEqual(complete["outcome"], Outcome.FAILURE)
        self.assertEqual(complete["error"]["details"]["unexpected"], True)


class BlockedRecordTests(IsolatedEnvTestCase):
    def test_a_disabled_tier_is_recorded_as_blocked(self):
        with self.assertRaises(MaxsurfSafetyError):
            _diagnostic_tool()
        records = self.records_for("_diagnostic_tool")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["outcome"], Outcome.BLOCKED)
        self.assertEqual(records[0]["error"]["code"], "safety_error")
        self.assertEqual(
            records[0]["classification"], Classification.DIAGNOSTIC
        )

    def test_a_blocked_tool_never_reaches_its_body(self):
        calls = []

        @guard.guarded(classification=Classification.DIAGNOSTIC,
                       module="probe", tier=guard.TIER_DIAGNOSTICS)
        def _tracked() -> str:
            calls.append(1)
            return "{}"

        with self.assertRaises(MaxsurfSafetyError):
            _tracked()
        self.assertEqual(calls, [])


class RedactionTests(IsolatedEnvTestCase):
    def test_secret_looking_arguments_are_redacted(self):
        _secret_tool("hull.msd", "s3cret")
        record = self.records_for("_secret_tool")[0]
        self.assertEqual(record["arguments"]["design"], "hull.msd")
        self.assertEqual(record["arguments"]["api_key"], "[redacted]")

    def test_long_arguments_are_digested_not_copied(self):
        payload = "x" * 5000
        summary = audit.redact_arguments({"points_json": payload})
        entry = summary["points_json"]
        self.assertTrue(entry["truncated"])
        self.assertEqual(entry["length"], 5000)
        self.assertEqual(len(entry["sha256"]), 64)
        self.assertLess(len(entry["head"]), 5000)


class HashChainTests(IsolatedEnvTestCase):
    def test_chain_links_records_and_verifies(self):
        _read_tool(1)
        _read_tool(2)
        _write_tool(3)
        path = self.audit_files()[0]
        result = audit.verify_file(path)
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["records"], 4)

        records = audit.read_records(path)
        self.assertEqual(records[0]["prev_hash"], audit.GENESIS_HASH)
        for previous, current in zip(records, records[1:]):
            self.assertEqual(current["prev_hash"], previous["record_hash"])

    def test_editing_a_record_breaks_verification(self):
        _read_tool(1)
        _read_tool(2)
        path = self.audit_files()[0]
        lines = path.read_text(encoding="utf-8").splitlines()
        tampered = json.loads(lines[0])
        tampered["arguments"]["value"] = 999
        lines[0] = json.dumps(tampered, ensure_ascii=False)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = audit.verify_file(path)
        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "record_hash_mismatch")
        self.assertEqual(result["index"], 0)

    def test_deleting_a_record_breaks_verification(self):
        _read_tool(1)
        _read_tool(2)
        _read_tool(3)
        path = self.audit_files()[0]
        lines = path.read_text(encoding="utf-8").splitlines()
        del lines[1]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = audit.verify_file(path)
        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "prev_hash_mismatch")

    def test_chain_resumes_across_log_instances(self):
        _read_tool(1)
        path = self.audit_files()[0]
        first_head = audit.read_records(path)[-1]["record_hash"]

        # A restarted process must pick the chain back up from the file.
        audit.set_log(audit.AuditLog(directory=self.audit_dir))
        _read_tool(2)
        records = audit.read_records(path)
        self.assertEqual(records[-1]["prev_hash"], first_head)
        self.assertTrue(audit.verify_file(path)["valid"])


class ConcurrentWriterTests(IsolatedEnvTestCase):
    """The bug: independent writers each trusting their own chain head.

    Before interprocess locking, two MCP server processes appended to the same
    daily file while each held a process-local cached head. Their records
    interleaved on disk, so every record written by the second process pointed at
    a predecessor that was no longer the previous line, and verification failed.
    """

    def test_two_log_instances_sharing_a_file_produce_one_valid_chain(self):
        first = audit.AuditLog(directory=self.audit_dir, enabled=True)
        second = audit.AuditLog(directory=self.audit_dir, enabled=True)
        for index in range(8):
            writer = first if index % 2 == 0 else second
            writer.record(stage="complete", tool=f"tool{index}", module="probe",
                          classification=Classification.READ,
                          channel=Channel.INTERNAL, outcome=Outcome.SUCCESS)

        path = self.audit_files()[0]
        result = audit.verify_file(path)
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["records"], 8)

    def test_the_head_is_reread_from_disk_on_every_append(self):
        """A stale cached head must never be used, even when one exists."""
        log = audit.AuditLog(directory=self.audit_dir, enabled=True)
        log.record(stage="complete", tool="first", module="probe",
                   classification=Classification.READ,
                   channel=Channel.INTERNAL, outcome=Outcome.SUCCESS)

        # Another process appends behind this instance's back.
        other = audit.AuditLog(directory=self.audit_dir, enabled=True)
        interloper = other.record(
            stage="complete", tool="interloper", module="probe",
            classification=Classification.READ,
            channel=Channel.INTERNAL, outcome=Outcome.SUCCESS)

        log.record(stage="complete", tool="second", module="probe",
                   classification=Classification.READ,
                   channel=Channel.INTERNAL, outcome=Outcome.SUCCESS)

        records = audit.read_records(self.audit_files()[0])
        self.assertEqual([r["tool"] for r in records],
                         ["first", "interloper", "second"])
        self.assertEqual(records[-1]["prev_hash"], interloper["record_hash"])
        self.assertTrue(audit.verify_file(self.audit_files()[0])["valid"])

    def test_threads_of_one_process_produce_one_valid_chain(self):
        log = audit.AuditLog(directory=self.audit_dir, enabled=True)
        start = threading.Barrier(4)

        def write(label: str) -> None:
            start.wait(timeout=10)
            for index in range(5):
                log.record(stage="complete", tool=f"{label}{index}",
                           module="probe", classification=Classification.READ,
                           channel=Channel.INTERNAL, outcome=Outcome.SUCCESS)

        threads = [threading.Thread(target=write, args=(f"t{n}-",))
                   for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        result = audit.verify_file(self.audit_files()[0])
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["records"], 20)

    def test_a_corrupt_tail_is_refused_rather_than_chained_over(self):
        log = audit.AuditLog(directory=self.audit_dir, enabled=True)
        log.record(stage="complete", tool="first", module="probe",
                   classification=Classification.READ,
                   channel=Channel.INTERNAL, outcome=Outcome.SUCCESS)
        path = self.audit_files()[0]
        with path.open("a", encoding="utf-8") as handle:
            handle.write("{not json\n")

        with self.assertRaises(MaxsurfSafetyError) as caught:
            log.record(stage="intent", tool="second", module="probe",
                       classification=Classification.WRITE_GEOMETRY,
                       channel=Channel.INTERNAL, outcome=Outcome.SUCCESS)
        self.assertIn("hash chain", str(caught.exception))

    def test_a_record_larger_than_the_tail_window_is_still_chained(self):
        """The backwards read must widen until it finds a whole line."""
        log = audit.AuditLog(directory=self.audit_dir, enabled=True)
        big = log.record(stage="complete", tool="big", module="probe",
                         classification=Classification.READ,
                         channel=Channel.INTERNAL, outcome=Outcome.SUCCESS,
                         result_summary={"blob": "x" * (audit._TAIL_CHUNK * 3)})
        log.record(stage="complete", tool="after", module="probe",
                   classification=Classification.READ,
                   channel=Channel.INTERNAL, outcome=Outcome.SUCCESS)

        records = audit.read_records(self.audit_files()[0])
        self.assertEqual(records[-1]["prev_hash"], big["record_hash"])
        self.assertTrue(audit.verify_file(self.audit_files()[0])["valid"])


class MultiProcessAuditTests(IsolatedEnvTestCase):
    """The same failure, reproduced with genuinely separate OS processes."""

    WRITERS = 3
    PER_WRITER = 12

    def test_separate_processes_appending_concurrently_keep_the_chain_valid(self):
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ)
        env["MAXSURF_MCP_AUDIT"] = "1"
        env["MAXSURF_MCP_AUDIT_DIR"] = str(self.audit_dir)
        env["PYTHONPATH"] = str(root)

        children = [
            subprocess.Popen(
                [sys.executable, "-m", "tests.audit_writer",
                 str(self.audit_dir), f"writer{index}", str(self.PER_WRITER)],
                cwd=str(root), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for index in range(self.WRITERS)
        ]
        for child in children:
            _out, err = child.communicate(timeout=180)
            self.assertEqual(child.returncode, 0, err)

        files = self.audit_files()
        self.assertTrue(files, "the writers produced no audit file")
        total = 0
        for path in files:
            result = audit.verify_file(path)
            self.assertTrue(result["valid"], f"{path.name}: {result}")
            total += result["records"]
        self.assertEqual(total, self.WRITERS * self.PER_WRITER)

        records = [record for path in files
                   for record in audit.read_records(path)]
        self.assertEqual(len({r["audit_id"] for r in records}), total)
        # Proof the processes really were independent and really did interleave.
        self.assertGreater(len({r["session_id"] for r in records}), 1)


class AuditFailClosedTests(IsolatedEnvTestCase):
    def test_a_mutating_tool_is_refused_when_auditing_is_disabled(self):
        os.environ[config.AUDIT_ENABLED_VAR] = "0"
        with self.assertRaises(MaxsurfSafetyError) as caught:
            _write_tool(1)
        self.assertIn("cannot be proven", str(caught.exception))

    def test_a_read_still_works_when_auditing_is_disabled(self):
        os.environ[config.AUDIT_ENABLED_VAR] = "0"
        payload = json.loads(_read_tool(4))
        self.assertEqual(payload["value"], 4)
        self.assertEqual(self.audit_records(), [])

    def test_a_mutating_tool_is_refused_when_the_record_cannot_be_written(self):
        class BrokenLog(audit.AuditLog):
            def _append(self, record):
                raise OSError("disk is full")

        audit.set_log(BrokenLog(directory=self.audit_dir))
        with self.assertRaises(MaxsurfSafetyError) as caught:
            _write_tool(1)
        self.assertIn("refusing the operation", str(caught.exception))


class RecordValidationTests(IsolatedEnvTestCase):
    def test_unknown_classification_is_refused(self):
        with self.assertRaises(MaxsurfSafetyError):
            self.audit_log.record(
                stage="complete", tool="x", module="m",
                classification="NOT_A_CLASSIFICATION",
            )

    def test_unknown_channel_is_refused(self):
        with self.assertRaises(MaxsurfSafetyError):
            self.audit_log.record(
                stage="complete", tool="x", module="m",
                classification=Classification.READ, channel="carrier_pigeon",
            )

    def test_all_documented_channels_and_classifications_are_accepted(self):
        for channel in sorted(Channel.ALL):
            self.audit_log.record(
                stage="complete", tool="channel_probe", module="m",
                classification=Classification.READ, channel=channel,
                outcome=Outcome.SUCCESS,
            )
        for classification in sorted(Classification.ALL):
            self.audit_log.record(
                stage="complete", tool="class_probe", module="m",
                classification=classification, outcome=Outcome.SUCCESS,
            )
        self.assertTrue(audit.verify_file(self.audit_files()[0])["valid"])


class TierRegistrationTests(IsolatedEnvTestCase):
    def test_register_skips_disabled_tiers(self):
        collector = ToolCollector()
        guard.register(collector, (_read_tool, _diagnostic_tool))
        self.assertEqual(collector.registered, ["_read_tool"])

    def test_register_includes_a_tier_once_enabled(self):
        os.environ[config.ENABLE_DIAGNOSTICS_VAR] = "1"
        collector = ToolCollector()
        guard.register(collector, (_read_tool, _diagnostic_tool))
        self.assertEqual(
            collector.registered, ["_read_tool", "_diagnostic_tool"]
        )

    def test_unknown_tier_is_refused(self):
        with self.assertRaises(MaxsurfSafetyError):
            guard.tier_enabled("nonexistent_tier")


class GuardMetadataTests(unittest.TestCase):
    def test_the_decorator_preserves_the_wrapped_signature(self):
        import inspect

        self.assertEqual(
            list(inspect.signature(_write_tool).parameters), ["index"]
        )
        self.assertEqual(_write_tool.__name__, "_write_tool")
        self.assertEqual(_write_tool.__module__, __name__)

    def test_classification_is_exposed_on_the_tool(self):
        self.assertEqual(
            _write_tool.maxsurf_classification, Classification.WRITE_GEOMETRY
        )
        self.assertEqual(_diagnostic_tool.maxsurf_tier, guard.TIER_DIAGNOSTICS)
        self.assertIsNone(_read_tool.maxsurf_tier)

    def test_unknown_classification_cannot_be_declared(self):
        with self.assertRaises(MaxsurfSafetyError):
            guard.guarded(classification="WRITE_EVERYTHING", module="probe")


if __name__ == "__main__":
    unittest.main()
