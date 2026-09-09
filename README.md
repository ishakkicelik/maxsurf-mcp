# Maxsurf MCP

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Connect Codex or Claude Desktop to locally installed **Maxsurf Modeler, Stability, Resistance, Motions and Multiframe** through the Model Context Protocol.

The MCP lets an assistant inspect models, set supported analysis inputs and request operations from Bentley's native applications. It runs on your Windows computer and uses your installed Maxsurf software.

**Current build: 0.2.0-rc.5 · 90 default tools · technical preview.** This is preparation for the project's first public release. Supported operations and outstanding validation work are documented in [Capabilities](docs/CAPABILITIES.md) and [Release status](docs/RELEASE_STATUS.md).

[Installation guide](docs/INSTALL.md) · [Tool reference](docs/TOOLS.md) · [Verification](docs/VERIFICATION.md)

## Before you start

You need:

- Windows x64 and **CPython 3.12, 64-bit**.
- The Bentley applications you intend to use, installed separately with the appropriate COM automation entitlement.
- Codex on Windows or Claude Desktop with local extensions enabled.
- A dedicated folder for your designs and exports, such as `C:\MaxsurfDesigns`.

Open only the Bentley application required for the task. For a first hull-model check, open **Maxsurf Modeler**. The default MCP connection policy requires the application to be running.

Maxsurf, Python and client subscriptions/licenses are separate from this package. Review the [project license](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md).

## Choose your installation

| You want to use | Download | Setup |
| --- | --- | --- |
| Claude Desktop extension | `maxsurf-mcp-0.2.0-rc.5-win-x64-py312.mcpb` | [Install the extension](docs/INSTALL.md#claude-desktop-extension) |
| Codex plugin | `maxsurf-mcp-0.2.0-rc.5-codex-win-x64-py312.zip` | [Install the Codex plugin](docs/INSTALL.md#codex-plugin) |
| Source code through PowerShell | `maxsurf-mcp-0.2.0-rc.5-source.zip` | [Install from source](docs/INSTALL.md#powershell-source-installation) |

The Claude and Codex bundles include the server's Python dependencies. They still require Python itself. The source installation creates a virtual environment and downloads dependencies.

These are the asset names to use when the first GitHub release is published. Uploading source to GitHub does not automatically list an extension in a client's public catalog.

## Connect to Claude Desktop

1. In Claude Desktop, open **Settings → Extensions → Advanced settings → Install extension**.
2. Select the downloaded **`.mcpb` file**.
3. In **Configure Maxsurf MCP**, choose the Python executable and design workspace:

| Configuration field | What to select |
| --- | --- |
| Python 3.12 x64 executable | The actual `python.exe` from a 64-bit Python 3.12 installation |
| Design workspace | Your design/export folder, for example `C:\MaxsurfDesigns` |

To find the correct Python file, run this in PowerShell:

```powershell
py -3.12 -c "import sys,struct; print(sys.executable); print(sys.version); print(struct.calcsize('P')*8)"
```

Use the executable path printed on the first line. The output must show Python `3.12.x` and `64`. The `.mcpb` belongs in the installation picker; `python.exe` belongs in the configuration field.

Click **Save**, enable the extension and start a new conversation. [Detailed Claude instructions](docs/INSTALL.md#claude-desktop-extension).

## Connect to Codex

Extract the **Codex ZIP** to a permanent folder, for example `C:\MaxsurfBridge`. Keep all files, including the `.agents` folder. Then run:

```powershell
codex plugin marketplace add "C:/MaxsurfBridge"
codex plugin add maxsurf-mcp@maxsurf-local
```

Restart Codex, confirm **Maxsurf MCP** is enabled, and start a new conversation. The bundled plugin requires `py -3.12` to resolve to 64-bit Python 3.12.

The default design workspace is `%LOCALAPPDATA%\MaxsurfMCP\runtime`. [Codex setup and workspace selection](docs/INSTALL.md#codex-plugin) explains how to use another folder.

## Install through PowerShell

Extract the **source ZIP** to `C:\MaxsurfSource`. In PowerShell:

```powershell
Set-Location 'C:\MaxsurfSource'
$maxsurfPython = (py -3.12 -c "import sys; print(sys.executable)").Trim()
.\scripts\install.ps1 -Python $maxsurfPython
```

The installer creates `.venv` and installs locked dependencies. It does not register the server in a client. Next, follow [Codex source configuration](docs/INSTALL.md#connect-the-source-installation-to-codex) or [Claude source configuration](docs/INSTALL.md#connect-the-source-installation-to-claude-desktop).

## Check the connection

Open **Maxsurf Modeler** manually, then send this in a new Codex or Claude conversation:

```text
Use Maxsurf MCP to report session status and the configured workspace.
Connect only to the already-running Maxsurf Modeler.
Read its current design identity and surface list.
Do not edit, save, run analysis, or use desktop/display-control tools.
```

A populated tool list confirms the MCP connection. A successful Modeler connection confirms that application's automation access. Neither result certifies an engineering calculation.

## License

[MIT](LICENSE). Copyright (c) 2026 Ismail Hakki Celik.

The MIT license covers original project code, documentation and assets. Dependency licenses and Bentley product entitlements remain separate; see [Third-party notices](THIRD_PARTY_NOTICES.md).

## Documentation and scope

- [Installation and troubleshooting](docs/INSTALL.md)
- [Supported capabilities](docs/CAPABILITIES.md) and [all tools](docs/TOOLS.md)
- [Recorded verification](docs/VERIFICATION.md) and [remaining release gates](docs/RELEASE_STATUS.md)
- [Security](SECURITY.md), [license](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md)

The current build has incomplete native-analysis, persistence and clean-machine installation acceptance. It is not a class-compliance authority or a completed ship-design system. Bentley software, manuals, samples, private training material and vessel-design recipes are excluded from the distribution.
