"""Tests for how the guard hands tool bodies to the COM worker.

Two properties matter and neither is visible from a tool's return value: COM
work must leave the thread that delivered the request, and the event loop must
stay answerable while it runs.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
import unittest

import server
from maxsurf_mcp import audit, com, comthread, config, guard, session
from maxsurf_mcp.audit import Channel, Classification
from maxsurf_mcp.errors import MaxsurfError, MaxsurfTimeoutError

from .support import ComFake, FakeComModule, IsolatedEnvTestCase, ToolCollector


class RoutingTests(IsolatedEnvTestCase):
    def test_a_com_tool_body_is_submitted_to_the_worker(self):
        @guard.guarded(classification=Classification.READ, module="test",
                       channel=Channel.MCP_COM)
        def com_tool() -> str:
            return "done"

        self.assertEqual(com_tool(), "done")
        self.assertEqual(self.worker.calls, ["com_tool"])

    def test_a_local_tool_stays_on_the_calling_thread(self):
        # Keeping local tools off the worker is what leaves session_status and
        # list_progids answerable while a COM call is stuck.
        @guard.guarded(classification=Classification.READ, module="test",
                       channel=Channel.LOCAL)
        def local_tool() -> str:
            return "done"

        self.assertEqual(local_tool(), "done")
        self.assertEqual(self.worker.calls, [])

    def test_an_analysis_gets_the_analysis_timeout_budget(self):
        self.set_env(config.COM_TIMEOUT_VAR, "11")
        self.set_env(config.ANALYSIS_TIMEOUT_VAR, "999")

        @guard.guarded(classification=Classification.EXECUTE_ANALYSIS,
                       module="test", channel=Channel.MCP_COM)
        def analysis_tool() -> str:
            return "done"

        @guard.guarded(classification=Classification.READ, module="test",
                       channel=Channel.MCP_COM)
        def read_tool() -> str:
            return "done"

        analysis_tool()
        read_tool()
        self.assertEqual(self.worker.timeouts, [999.0, 11.0])

    def test_the_timeout_budget_is_read_per_call_not_at_import(self):
        @guard.guarded(classification=Classification.READ, module="test",
                       channel=Channel.MCP_COM)
        def read_tool() -> str:
            return "done"

        read_tool()
        self.set_env(config.COM_TIMEOUT_VAR, "7")
        read_tool()
        self.assertEqual(self.worker.timeouts, [120.0, 7.0])

    def test_com_tools_run_on_the_worker_thread_in_practice(self):
        worker = self.use_real_worker(FakeComModule())
        worker.start()
        observed: list[int] = []

        @guard.guarded(classification=Classification.READ, module="test",
                       channel=Channel.MCP_COM)
        def com_tool() -> str:
            observed.append(threading.get_ident())
            return "done"

        com_tool()
        self.assertEqual(observed, [worker.thread_ident()])
        self.assertNotEqual(observed[0], threading.get_ident())

    def test_concurrent_tool_calls_are_serialised_through_the_worker(self):
        worker = self.use_real_worker(FakeComModule())
        worker.start()
        active = 0
        overlaps = []
        lock = threading.Lock()

        @guard.guarded(classification=Classification.READ, module="test",
                       channel=Channel.MCP_COM)
        def com_tool() -> str:
            nonlocal active
            with lock:
                active += 1
                if active > 1:
                    overlaps.append(active)
            time.sleep(0.005)
            with lock:
                active -= 1
            return "done"

        threads = [threading.Thread(target=com_tool) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertEqual(overlaps, [])


class TimeoutReportingTests(IsolatedEnvTestCase):
    def test_a_tool_that_exceeds_its_budget_fails_with_a_typed_timeout(self):
        worker = self.use_real_worker(FakeComModule())
        release = threading.Event()
        self.addCleanup(release.set)
        worker.start()
        self.set_env(config.COM_TIMEOUT_VAR, "0.05")

        @guard.guarded(classification=Classification.READ, module="test",
                       channel=Channel.MCP_COM)
        def slow_tool() -> str:
            release.wait(timeout=30)
            return "done"

        with self.assertRaises(MaxsurfTimeoutError) as caught:
            slow_tool()

        self.assertIsInstance(caught.exception, MaxsurfError)
        records = self.records_for("slow_tool")
        self.assertEqual(records[-1]["outcome"], audit.Outcome.FAILURE)
        self.assertEqual(records[-1]["error"]["code"], "timeout_error")


class CallScopeTests(IsolatedEnvTestCase):
    def test_the_session_layer_sees_the_calling_tools_classification(self):
        observed: list[str | None] = []

        @guard.guarded(classification=Classification.WRITE_GEOMETRY,
                       module="test", channel=Channel.MCP_COM)
        def writing_tool() -> str:
            observed.append(audit.current_classification())
            return "done"

        writing_tool()
        self.assertEqual(observed, [Classification.WRITE_GEOMETRY])

    def test_session_records_are_correlated_with_the_tool_that_caused_them(self):
        self.install_com_environment(
            running={"BentleyModeler.Application": ComFake(Version="25")})

        @guard.guarded(classification=Classification.READ, module="test",
                       channel=Channel.MCP_COM)
        def reading_tool() -> str:
            com.connect("modeler")
            return "done"

        reading_tool()
        tool_record = self.records_for("reading_tool")[-1]
        connect_record = self.records_for("session_connect")[0]

        self.assertEqual(connect_record["request_id"], tool_record["audit_id"])

    def test_the_scope_is_closed_even_when_a_tool_fails(self):
        @guard.guarded(classification=Classification.READ, module="test",
                       channel=Channel.MCP_COM)
        def failing_tool() -> str:
            raise ValueError("boom")

        with self.assertRaises(MaxsurfError):
            failing_tool()
        self.assertIsNone(audit.current_classification())
        self.assertIsNone(audit.current_request_id())


class AdapterTests(IsolatedEnvTestCase):
    """FastMCP is handed a coroutine so COM cannot freeze the event loop."""

    def test_registered_tools_are_coroutine_functions(self):
        collector = ToolCollector()
        com.register_tools(collector)

        for name, function in collector.functions.items():
            with self.subTest(tool=name):
                self.assertTrue(inspect.iscoroutinefunction(function))

    def test_the_adapter_preserves_the_signature_and_documentation(self):
        adapter = guard.make_adapter(com.com_describe)

        self.assertEqual(inspect.signature(adapter),
                         inspect.signature(com.com_describe))
        self.assertEqual(adapter.__doc__, com.com_describe.__doc__)
        self.assertEqual(adapter.__name__, "com_describe")
        self.assertIs(adapter.maxsurf_tool, com.com_describe)
        self.assertEqual(adapter.maxsurf_classification,
                         com.com_describe.maxsurf_classification)

    def test_the_event_loop_keeps_running_while_com_is_busy(self):
        worker = self.use_real_worker(FakeComModule())
        worker.start()
        release = threading.Event()
        self.addCleanup(release.set)

        @guard.guarded(classification=Classification.READ, module="test",
                       channel=Channel.MCP_COM)
        def slow_tool() -> str:
            release.wait(timeout=10)
            return "done"

        async def scenario() -> tuple[str, int]:
            ticks = 0

            async def heartbeat() -> None:
                nonlocal ticks
                while True:
                    await asyncio.sleep(0.005)
                    ticks += 1
                    if ticks >= 5:
                        release.set()

            beat = asyncio.create_task(heartbeat())
            try:
                result = await guard.make_adapter(slow_tool)()
            finally:
                beat.cancel()
            return result, ticks

        result, ticks = asyncio.run(scenario())

        self.assertEqual(result, "done")
        self.assertGreaterEqual(ticks, 5,
                                "the event loop was blocked by the COM call")

    def test_an_adapter_propagates_the_typed_error(self):
        @guard.guarded(classification=Classification.READ, module="test",
                       channel=Channel.MCP_COM)
        def failing_tool() -> str:
            raise ValueError("boom")

        with self.assertRaises(MaxsurfError):
            asyncio.run(guard.make_adapter(failing_tool)())


class ServerShutdownTests(IsolatedEnvTestCase):
    def test_shutdown_releases_sessions_before_stopping_the_worker(self):
        worker = self.use_real_worker(FakeComModule())
        worker.start()
        self.install_com_environment(
            running={"BentleyModeler.Application": ComFake(Version="25")})
        com.connect("modeler")
        self.assertIsNotNone(session.get_manager().session_for("modeler"))

        released: list[int] = []
        original = session.get_manager().reset

        def recording_reset(module=None):
            released.append(threading.get_ident())
            return original(module)

        session.get_manager().reset = recording_reset  # type: ignore[method-assign]
        worker_ident = worker.thread_ident()
        summary = server.shutdown()

        # The final Release must happen inside the apartment that created the
        # pointers, not on the thread the interpreter is exiting from.
        self.assertEqual(released, [worker_ident])
        self.assertNotEqual(worker_ident, threading.get_ident())
        self.assertIsNone(session.get_manager().session_for("modeler"))
        self.assertTrue(summary["joined"])
        self.assertTrue(summary["apartment_uninitialised"])
        self.assertIsNone(comthread.peek_worker())

    def test_shutdown_without_a_worker_is_harmless(self):
        comthread.set_worker(None)
        self.assertIsNone(server.shutdown())


if __name__ == "__main__":
    unittest.main()
