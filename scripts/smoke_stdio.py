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
            assert not status.isError
            assert refusal.isError
            assert init.serverInfo.version=='0.2.0-rc.5'
            assert len(tools.tools)==90
            print(json.dumps({'protocol':init.protocolVersion,'server':init.serverInfo.model_dump(),
                              'tools':len(tools.tools),'session_status':'pass','consent_refusal':'pass'}))


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
