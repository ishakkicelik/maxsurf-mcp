"""Exercise MCP initialize/list/read/error over real stdio; never touch Maxsurf."""
import argparse
import asyncio
import json
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run(python, entry, bundle, plugin_root=None):
    if plugin_root:
        root = Path(plugin_root).resolve()
        definition = json.loads((root / ".mcp.json").read_text())["mcpServers"]["maxsurf"]
        import os
        env = dict(os.environ, **definition.get("env", {}))
        parameters = StdioServerParameters(command=definition["command"],
            args=definition["args"], cwd=str(root / definition.get("cwd", ".")), env=env)
    else:
        args=(['-I','-S'] if bundle else [])+[str(Path(entry).resolve())]
        parameters = StdioServerParameters(command=python,args=args)
    async with stdio_client(parameters) as (read,write):
        async with ClientSession(read,write) as session:
            init=await session.initialize()
            tools=await session.list_tools()
            status=await session.call_tool('session_status',{})
            # This is refused BEFORE connecting to an application.
            refusal=await session.call_tool('transfer_modeler_to_stability',{'confirm_replace':False})
            invalid_index = await session.call_tool('surface_summary', {'index': True})
            unknown_option = await session.call_tool('session_status', {'unknown_option': True})
            for label, result, error in (
                    ("session_status", status, False),
                    ("consent refusal", refusal, True),
                    ("Boolean index refusal", invalid_index, True),
                    ("unknown argument refusal", unknown_option, True)):
                if result.isError != error:
                    raise RuntimeError(f"{label}: expected isError={error}")
            if init.serverInfo.version != '0.2.0-rc.5':
                raise RuntimeError(f"Unexpected server version: {init.serverInfo.version}")
            expected = json.loads(
                (Path(__file__).resolve().parents[1] / "tests" / "data"
                 / "tool_schemas.json").read_text(encoding="utf-8")
            )
            if (len(tools.tools) != len(expected)
                    or {tool.name: tool.inputSchema for tool in tools.tools} != expected):
                raise RuntimeError("Tool schema snapshot differs")
            print(json.dumps({'protocol':init.protocolVersion,'server':init.serverInfo.model_dump(),
                              'tools':len(tools.tools),'session_status':'pass','consent_refusal':'pass',
                              'raw_input_validation':'pass'}))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--python')
    parser.add_argument('--entry')
    parser.add_argument('--plugin-root',help="Test exactly the bundled Codex MCP command/cwd")
    parser.add_argument('--bundle',action='store_true')
    args=parser.parse_args()
    if not args.plugin_root and not (args.python and args.entry):
        parser.error("Provide --plugin-root, or both --python and --entry")
    asyncio.run(run(args.python,args.entry,args.bundle,args.plugin_root))
