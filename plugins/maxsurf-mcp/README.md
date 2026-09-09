# Maxsurf MCP for Codex

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A local Windows plugin for connecting Codex to Bentley Maxsurf applications.

## Install the packaged plugin

1. Download the **Codex ZIP**, not the Claude MCPB or source ZIP.
2. Extract the complete ZIP to a permanent folder such as `C:\MaxsurfBridge`. Keep the `.agents` directory.
3. Verify that `py -3.12` selects 64-bit Python 3.12.
4. In PowerShell, run:

```powershell
codex plugin marketplace add "C:/MaxsurfBridge"
codex plugin add maxsurf-mcp@maxsurf-local
```

5. Restart Codex, enable **Maxsurf MCP** and start a new conversation.

The packaged plugin contains the server and pinned dependencies. This source plugin directory alone does not include them; build or download the complete Codex bundle.

## Workspace and first connection

The default workspace is `%LOCALAPPDATA%\MaxsurfMCP\runtime`. To use another design folder, set `MAXSURF_MCP_WORKSPACE_ROOTS` in the environment inherited by Codex before starting it.

For a first check, manually open **Maxsurf Modeler**. Ask Codex to call `session_status`, connect only to Modeler and list its current design/surfaces without edits or desktop-control actions.

## License

[MIT](LICENSE). Copyright (c) 2026 Ismail Hakki Celik. The installed plugin includes this notice; bundled dependencies retain their own licenses.

Full setup, source configuration and troubleshooting are in `docs/INSTALL.md` at the extracted release root. Review the root `LICENSE`, security guidance and release-status report before use.
