"""Raw MCP inputs must be rejected before SDK coercion or native dispatch."""
from __future__ import annotations

from collections import OrderedDict
from itertools import islice
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest import TestCase, mock

import anyio
import jsonschema
import mcp.types as types

import server
from maxsurf_mcp import config, native_automation


class ProtocolValidationTests(TestCase):
    def test_diagnostic_handlers_in_a_fresh_enabled_process(self):
        environment = dict(os.environ)
        environment[config.ENABLE_DIAGNOSTICS_VAR] = "1"
        environment[config.ENABLE_LEGACY_VAR] = "0"
        process = subprocess.run(
            [sys.executable, "-m", "unittest",
             "tests.diagnostic_protocol_contract.DiagnosticProtocolTests", "-q"],
            cwd=Path(__file__).resolve().parents[1],
            env=environment, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)

    def test_boolean_index_is_rejected_before_tool_dispatch(self):
        # Given: an integer schema and a caller-supplied JSON Boolean.
        request = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(
                name="surface_summary", arguments={"index": True},
            ),
        )
        handler = server.mcp._mcp_server.request_handlers[types.CallToolRequest]

        # When: the real low-level MCP handler receives that request.
        with mock.patch.object(server.mcp._tool_manager, "call_tool",
                               new_callable=mock.AsyncMock) as dispatch:
            result = anyio.run(handler, request)

        # Then: validation, not native tool execution, produces the error.
        self.assertTrue(result.root.isError)
        dispatch.assert_not_awaited()

    def test_unknown_argument_is_rejected_before_tool_dispatch(self):
        # Given: an otherwise valid read request with an unknown argument.
        request = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(
                name="session_status", arguments={"unknown_option": True},
            ),
        )
        handler = server.mcp._mcp_server.request_handlers[types.CallToolRequest]

        # When: it crosses the real MCP request boundary.
        with mock.patch.object(server.mcp._tool_manager, "call_tool",
                               new_callable=mock.AsyncMock) as dispatch:
            result = anyio.run(handler, request)

        # Then: an ignored option cannot be reported as a successful operation.
        self.assertTrue(result.root.isError)
        dispatch.assert_not_awaited()

    def test_valid_request_preserves_sdk_dispatch_and_structured_output(self):
        # Given: a valid schema-conforming request and an SDK result.
        request = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(
                name="surface_summary", arguments={"index": 1},
            ),
        )
        response = ([types.TextContent(type="text", text="native-result")],
                    {"result": "native-result"})
        handler = server.mcp._mcp_server.request_handlers[types.CallToolRequest]

        # When: the real handler dispatches through the SDK.
        with mock.patch.object(server.mcp._tool_manager, "call_tool",
                               new_callable=mock.AsyncMock,
                               return_value=response) as dispatch:
            result = anyio.run(handler, request)

        # Then: valid functionality and output validation remain operational.
        dispatch.assert_awaited_once()
        self.assertFalse(result.root.isError)
        self.assertEqual(result.root.structuredContent, {"result": "native-result"})


class DiagnosticProtocolContract:
    def call(self, name, arguments):
        request = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(name=name, arguments=arguments),
        )
        handler = server.mcp._mcp_server.request_handlers[types.CallToolRequest]
        return anyio.run(handler, request).root

    def test_unknown_arguments_never_reach_diagnostic_dispatch(self):
        arguments = {
            "com_get": {"path": "Application"},
            "com_set": {"path": "Application", "value": "1"},
            "com_invoke": {"path": "Application"},
            "native_call": {"interface": "ISurfaces", "member": "_NewEnum"},
            "native_enumerate": {"enum_handle": "owned-enum"},
            "release_native_handles": {},
            "create_native_object": {"module": "modeler", "coclass": "Surface"},
        }
        for name, valid in arguments.items():
            with self.subTest(tool=name):
                tool = server.mcp._tool_manager.get_tool(name)
                # Ensure the unknown field is the only schema violation.
                jsonschema.validate(valid, tool.parameters)
                with mock.patch.object(
                    server.mcp._tool_manager, "call_tool",
                    new_callable=mock.AsyncMock,
                ) as dispatch, mock.patch.object(
                    native_automation.com, "connect",
                ) as connect:
                    response = self.call(name, {**valid, "unknown_option": True})
                self.assertTrue(response.isError)
                dispatch.assert_not_awaited()
                connect.assert_not_called()

    def test_enumeration_schema_rejects_raw_types_before_dispatch(self):
        invalid_arguments = (
            {},
            {"enum_handle": 1},
            {"enum_handle": "owned-enum", "count": True},
            {"enum_handle": "owned-enum", "count": "2"},
            {"enum_handle": "owned-enum", "operation": False},
            {"enum_handle": "owned-enum", "module": False},
            {"enum_handle": "owned-enum", "acknowledge_native_effects": "true"},
        )
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments), mock.patch.object(
                server.mcp._tool_manager, "call_tool",
                new_callable=mock.AsyncMock,
            ) as dispatch:
                self.assertTrue(self.call("native_enumerate", arguments).isError)
                dispatch.assert_not_awaited()

    def test_enumeration_handler_awaits_adapter_and_preserves_result(self):
        main_thread = threading.get_ident()
        native_threads = []
        interface = mock.Mock()
        items = iter(("hull",))

        def next_batch(count):
            native_threads.append(threading.get_ident())
            return tuple(islice(items, count))

        interface.Next.side_effect = next_batch
        managed = SimpleNamespace(
            session_id="protocol-test", reconnect_count=0, app=object(),
            design_path="", design_name="",
        )
        stamp = native_automation._session_stamp(managed)
        handles = OrderedDict({
            "owned-enum": ("modeler", stamp,
                           native_automation._NativeEnumerator(interface)),
        })
        expected = {
            "module": "modeler", "operation": "next", "result": ["hull"],
            "engineering_acceptance": False, "fetched": 1, "exhausted": True,
        }
        with mock.patch.object(native_automation, "_handles", handles), \
                mock.patch.object(native_automation.com, "connect") as connect, \
                mock.patch.object(native_automation.com, "session_for",
                                  return_value=managed):
            response = self.call("native_enumerate", {
                "enum_handle": "owned-enum", "count": 2,
                "acknowledge_native_effects": True,
            })
            self.assertFalse(response.isError)
            self.assertEqual(json.loads(response.structuredContent["result"]), expected)
            self.assertEqual(json.loads(response.content[0].text), expected)
            connect.assert_called_once_with("modeler")
            self.assertTrue(native_threads)
            self.assertTrue(all(thread != main_thread for thread in native_threads))
            released = self.call("release_native_handles", {
                "handles_json": '["owned-enum"]',
                "acknowledge_native_effects": True,
            })
            self.assertFalse(released.isError)
            self.assertEqual(json.loads(released.structuredContent["result"]),
                             {"released": 1, "remaining": 0})
            self.assertFalse(handles)

    def test_enumeration_guards_refuse_before_native_connection(self):
        invalid_arguments = (
            {},
            {"acknowledge_native_effects": True, "count": 0},
            {"acknowledge_native_effects": True, "count": 257},
            {"acknowledge_native_effects": True, "operation": "Next"},
            {"acknowledge_native_effects": True, "operation": "reset", "count": 2},
            {"acknowledge_native_effects": True, "operation": "clone", "count": 2},
            {"acknowledge_native_effects": True, "module": "unknown"},
        )
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments), mock.patch.object(
                native_automation.com, "connect",
            ) as connect:
                response = self.call("native_enumerate", {
                    "enum_handle": "owned-enum", **arguments,
                })
                self.assertTrue(response.isError)
                connect.assert_not_called()
