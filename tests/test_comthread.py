"""Tests for the dedicated COM STA worker.

The worker is the thing that makes every other COM guarantee possible, so these
tests assert the properties that are invisible in normal use: which thread the
work ran on, that the apartment was both entered and left, that concurrent
callers cannot interleave, and that a timeout reports an abandoned wait rather
than pretending to have cancelled anything.
"""

from __future__ import annotations

import threading
import time
import unittest

from maxsurf_mcp import audit, comthread
from maxsurf_mcp.errors import MaxsurfSafetyError, MaxsurfTimeoutError

from .support import FakeComModule, IsolatedEnvTestCase


class ApartmentTests(IsolatedEnvTestCase):
    """CoInitializeEx / CoUninitialize pairing and thread ownership."""

    def test_the_worker_enters_a_single_threaded_apartment(self):
        com = FakeComModule()
        worker = self.use_real_worker(com)
        worker.start()

        self.assertEqual(com.names()[:1], ["CoInitializeEx"])
        self.assertEqual(com.events[0][1], com.COINIT_APARTMENTTHREADED)

    def test_every_initialise_is_paired_with_an_uninitialise(self):
        com = FakeComModule()
        worker = self.use_real_worker(com)
        worker.start()
        worker.run(lambda: None, description="work")
        summary = worker.shutdown(timeout=5.0)

        self.assertTrue(summary["joined"])
        self.assertEqual(com.names(), ["CoInitializeEx", "CoUninitialize"])

    def test_the_apartment_is_owned_by_one_thread_that_is_not_the_caller(self):
        com = FakeComModule()
        worker = self.use_real_worker(com)
        worker.start()
        worker.run(lambda: None, description="work")
        worker.shutdown(timeout=5.0)

        # Both apartment calls came from the same thread, and that thread was
        # neither the test's thread nor a per-call thread.
        self.assertEqual(len(set(com.threads)), 1)
        self.assertNotIn(threading.get_ident(), com.threads)

    def test_jobs_execute_on_the_worker_thread(self):
        worker = self.use_real_worker(FakeComModule())
        worker.start()
        observed = worker.run(threading.get_ident, description="whoami")

        self.assertEqual(observed, worker.thread_ident())
        self.assertNotEqual(observed, threading.get_ident())

    def test_a_refused_apartment_is_reported_rather_than_silently_ignored(self):
        worker = self.use_real_worker(FakeComModule(fail_init=True))
        with self.assertRaises(MaxsurfSafetyError) as caught:
            worker.start()

        self.assertIn("apartment", str(caught.exception))
        self.assertFalse(worker.describe()["apartment_initialised"])

    def test_the_idle_loop_pumps_the_message_queue(self):
        com = FakeComModule()
        worker = self.use_real_worker(com)
        worker.start()
        time.sleep(comthread.PUMP_INTERVAL_S * 4)
        worker.shutdown(timeout=5.0)

        self.assertGreater(com.pumps, 0)


class SerialisationTests(IsolatedEnvTestCase):
    """Concurrent MCP calls must not interleave inside COM."""

    def test_concurrent_callers_are_serialised(self):
        worker = self.use_real_worker(FakeComModule())
        worker.start()
        active = 0
        overlaps = []
        order = []
        lock = threading.Lock()

        def job(index: int) -> int:
            nonlocal active
            with lock:
                active += 1
                if active > 1:
                    overlaps.append(index)
            time.sleep(0.01)
            with lock:
                order.append(index)
                active -= 1
            return index

        threads = [
            threading.Thread(target=lambda i=i: worker.run(
                lambda: job(i), description=f"job{i}"))
            for i in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertEqual(overlaps, [], "COM work overlapped")
        self.assertEqual(sorted(order), list(range(8)))

    def test_a_nested_call_runs_inline_instead_of_deadlocking(self):
        worker = self.use_real_worker(FakeComModule())
        worker.start()

        def outer() -> tuple[int, bool]:
            inner = worker.run(threading.get_ident, description="inner")
            return inner, worker.is_worker_thread()

        inner_ident, on_worker = worker.run(outer, description="outer")

        self.assertTrue(on_worker)
        self.assertEqual(inner_ident, worker.thread_ident())

    def test_the_original_exception_reaches_the_caller(self):
        worker = self.use_real_worker(FakeComModule())
        worker.start()
        failure = ValueError("inside com")

        def job():
            raise failure

        with self.assertRaises(ValueError) as caught:
            worker.run(job, description="failing")
        self.assertIs(caught.exception, failure)


class TimeoutTests(IsolatedEnvTestCase):
    """A timeout abandons the wait. It never claims to have cancelled a call."""

    def setUp(self) -> None:
        super().setUp()
        self.release = threading.Event()

    def start_worker(self):
        """Start a worker whose blocked job is released before it is joined.

        Cleanups run last-registered-first, so registering the release after the
        worker means the abandoned job ends before shutdown waits for it.
        """
        worker = self.use_real_worker(FakeComModule())
        self.addCleanup(self.release.set)
        worker.start()
        return worker

    def blocking_job(self) -> str:
        self.release.wait(timeout=30)
        return "finished"

    def test_an_expired_wait_reports_that_the_call_is_still_running(self):
        worker = self.start_worker()

        with self.assertRaises(MaxsurfTimeoutError) as caught:
            worker.run(self.blocking_job, timeout=0.05, description="slow")

        details = caught.exception.details
        self.assertEqual(details["operation_state"], "still_running_in_maxsurf")
        self.assertNotIn("cancel", str(caught.exception).lower().replace(
            "not cancelled", ""))
        self.assertIn("abandoned", str(caught.exception))

    def test_further_com_work_is_refused_while_a_call_is_abandoned(self):
        worker = self.start_worker()
        with self.assertRaises(MaxsurfTimeoutError):
            worker.run(self.blocking_job, timeout=0.05, description="slow")

        with self.assertRaises(MaxsurfSafetyError) as caught:
            worker.run(lambda: "next", timeout=5.0, description="next")

        self.assertIn("abandoned", str(caught.exception))
        self.assertEqual(caught.exception.details["blocked_by"], ["slow"])
        self.assertFalse(worker.describe()["healthy"])

    def test_the_worker_recovers_once_the_abandoned_call_returns(self):
        worker = self.start_worker()
        with self.assertRaises(MaxsurfTimeoutError):
            worker.run(self.blocking_job, timeout=0.05, description="slow")

        self.release.set()
        deadline = time.monotonic() + 10
        while worker.describe()["abandoned_operations"] and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertEqual(worker.describe()["abandoned_operations"], [])
        self.assertTrue(worker.describe()["healthy"])
        self.assertEqual(worker.run(lambda: "next", description="next"), "next")

    def test_a_timeout_is_audited_with_the_operation_state(self):
        worker = self.start_worker()
        with self.assertRaises(MaxsurfTimeoutError):
            worker.run(self.blocking_job, timeout=0.05, description="slow")

        records = self.records_for("com_timeout")
        self.assertEqual(len(records), 1)
        summary = records[0]["result_summary"]
        self.assertEqual(summary["operation"], "slow")
        self.assertEqual(summary["operation_state"], "still_running_in_maxsurf")
        self.assertEqual(records[0]["channel"], audit.Channel.INTERNAL)


class LifecycleAuditTests(IsolatedEnvTestCase):
    def test_start_and_shutdown_are_audited(self):
        worker = self.use_real_worker(FakeComModule())
        worker.start()
        worker.shutdown(timeout=5.0)

        self.assertEqual(len(self.records_for("com_worker_start")), 1)
        shutdowns = self.records_for("com_worker_shutdown")
        self.assertEqual(len(shutdowns), 1)
        self.assertTrue(shutdowns[0]["result_summary"]["joined"])
        self.assertTrue(
            shutdowns[0]["result_summary"]["apartment_uninitialised"]
        )

    def test_shutting_down_a_worker_that_never_ran_is_harmless(self):
        worker = comthread.ComWorker(name="never-started",
                                     com_module=FakeComModule())
        summary = worker.shutdown(timeout=1.0)
        self.assertFalse(summary["running"])

    def test_a_stopped_worker_refuses_to_restart(self):
        worker = self.use_real_worker(FakeComModule())
        worker.start()
        worker.shutdown(timeout=5.0)
        with self.assertRaises(MaxsurfSafetyError):
            worker.start()


class ContextPropagationTests(IsolatedEnvTestCase):
    """Audit context has to survive the hand-off to the worker and back."""

    def test_the_caller_sees_a_design_path_recorded_inside_a_job(self):
        worker = self.use_real_worker(FakeComModule())
        worker.start()
        scope = audit.begin_call_scope("req-1", audit.Classification.READ)
        try:
            worker.run(lambda: audit.set_design_path("C:/ws/hull.msd"),
                       description="sets_path")
            self.assertEqual(audit.current_design_path(), "C:/ws/hull.msd")
        finally:
            audit.end_call_scope(scope)

    def test_the_job_sees_the_callers_request_scope(self):
        worker = self.use_real_worker(FakeComModule())
        worker.start()
        scope = audit.begin_call_scope("req-2", audit.Classification.WRITE_STATE)
        try:
            observed = worker.run(
                lambda: (audit.current_request_id(),
                         audit.current_classification()),
                description="reads_scope",
            )
        finally:
            audit.end_call_scope(scope)

        self.assertEqual(observed, ("req-2", audit.Classification.WRITE_STATE))

    def test_one_calls_design_path_does_not_leak_into_the_next_call(self):
        worker = self.use_real_worker(FakeComModule())
        worker.start()

        def call(request_id: str, set_path: str | None) -> str | None:
            scope = audit.begin_call_scope(request_id, audit.Classification.READ)
            try:
                worker.run(
                    lambda: (audit.set_design_path(set_path) if set_path
                             else None),
                    description=request_id,
                )
                return audit.current_design_path()
            finally:
                audit.end_call_scope(scope)

        self.assertEqual(call("first", "C:/ws/first.msd"), "C:/ws/first.msd")
        self.assertIsNone(call("second", None))

    def test_audit_state_cannot_escape_into_a_scopeless_caller(self):
        worker = self.use_real_worker(FakeComModule())
        worker.start()
        worker.run(lambda: audit.set_design_path("C:/ws/stray.msd"),
                   description="no_scope")

        self.assertIsNone(audit.current_design_path())


if __name__ == "__main__":
    unittest.main()
