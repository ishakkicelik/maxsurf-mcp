# Installation guide

This guide covers Maxsurf MCP **0.2.0-rc.5** on Windows. Choose one installation method per client: a packaged extension/plugin, or a source-based MCP connection.

Original project code and documentation are [MIT licensed](../LICENSE), Copyright (c) 2026 Ismail Hakki Celik. Your Bentley entitlement is separate.

## Before installation

Prepare the following:

| Item | Requirement |
| --- | --- |
| Operating system | Windows x64, using a local desktop session |
| Python | CPython 3.12, 64-bit |
| Bentley software | Separately installed applications with the automation entitlement needed for your task |
| Client | Claude Desktop with local extensions, or Codex on Windows |
| Design workspace | A dedicated writable folder containing your working designs and exports |

Use the same Windows user and privilege level for the client and Bentley application. The accepted native test build is Maxsurf 2025 **25.00.02.339**; other builds and license editions require their own checks.

The packaged installations include Python dependencies, but do not include Python itself or Bentley software. They do not require a source checkout or virtual environment. Source installation needs access to a Python package index.

### Find your Python executable

Open PowerShell and run:

```powershell
py -3.12 -c "import sys,struct; print(sys.executable); print(sys.version); print(struct.calcsize('P')*8)"
```

The first line is the path to select when asked for Python. The remaining output must show `3.12.x` and `64`.

If `py` is unavailable, install or repair CPython 3.12 x64 and the Windows Python launcher through your approved software installation process. If you already know the Python path, check it directly:

```powershell
& 'C:\Path\To\Python312\python.exe' -c "import sys,struct; print(sys.version); print(struct.calcsize('P')*8)"
```

Replace that example path with your real executable. Do not select a `.mcpb`, ZIP, directory or Windows Store shortcut as the Python executable.

### Choose a design workspace

For example:

```powershell
New-Item -ItemType Directory -Path 'C:\MaxsurfDesigns' -Force | Out-Null
```

Put your working design copies and exports inside that folder. Existing users can instead select `C:\MaxsurfMCP\runtime` if their designs are already there. Keep the Bentley installation directory separate.

## Choose a download

| Installation | Release asset |
| --- | --- |
| Claude Desktop extension | `maxsurf-mcp-0.2.0-rc.5-win-x64-py312.mcpb` |
| Codex plugin | `maxsurf-mcp-0.2.0-rc.5-codex-win-x64-py312.zip` |
| PowerShell/source | `maxsurf-mcp-0.2.0-rc.5-source.zip` |

Download the matching checksum JSON from the same release. Check a downloaded file with:

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\Downloads\maxsurf-mcp-0.2.0-rc.5-win-x64-py312.mcpb'
```

Use the actual download path and compare the hash with that filename's entry in `SHA256SUMS-*.json`. These packages are unsigned; a checksum verifies file integrity, not publisher identity.

## Claude Desktop extension

1. Open **Claude Desktop → Settings → Extensions → Advanced settings**.
2. Under **Extension developer**, select **Install extension**.
3. Choose the downloaded **`.mcpb` file** and complete the installation prompt.
4. Open **Configure Maxsurf MCP** and fill in both required fields:

| Field | Value |
| --- | --- |
| **Python 3.12 x64 executable** | Select the `python.exe` printed by the Python check above |
| **Design workspace** | Select `C:\MaxsurfDesigns`, or your existing dedicated workspace |

For the established development setup, the values can be:

```text
Python:    C:\MaxsurfMCP\.venv\Scripts\python.exe
Workspace: C:\MaxsurfMCP\runtime
```

Those paths are examples for that existing setup, not requirements for another user's computer.

5. Click **Save** and enable **Maxsurf MCP**.
6. Start a new conversation. Restart Claude Desktop if the tool list has not refreshed.
7. Perform the [first connection check](#first-connection-check).

The installation picker accepts the MCPB. The later configuration picker accepts Python. This is the distinction between the two dialogs shown during setup.

These steps follow [Anthropic's local-extension instructions](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop). Organization policies may restrict custom extensions.

## Codex plugin

The Codex ZIP is a separate package from the Claude MCPB.

1. Extract the Codex ZIP to a permanent folder such as `C:\MaxsurfBridge`. With the file in your Downloads folder:

```powershell
$maxsurfCodexZip = Join-Path $env:USERPROFILE 'Downloads\maxsurf-mcp-0.2.0-rc.5-codex-win-x64-py312.zip'
Expand-Archive -LiteralPath $maxsurfCodexZip -DestinationPath 'C:\MaxsurfBridge'
```

Use a fresh destination. Preserve the complete archive, including dotfolders. The extracted root must contain:

```text
C:\MaxsurfBridge\.agents\plugins\marketplace.json
C:\MaxsurfBridge\plugins\maxsurf-mcp\.codex-plugin\plugin.json
C:\MaxsurfBridge\plugins\maxsurf-mcp\.mcp.json
C:\MaxsurfBridge\plugins\maxsurf-mcp\server\launch.py
C:\MaxsurfBridge\plugins\maxsurf-mcp\server\lib\
```

2. Check that `py -3.12` selects 64-bit Python 3.12, as shown earlier.
3. Add the local marketplace and install the plugin:

```powershell
codex plugin marketplace add "C:/MaxsurfBridge"
codex plugin add maxsurf-mcp@maxsurf-local
codex plugin marketplace list
```

4. Restart Codex and confirm **Maxsurf MCP** is installed and enabled in the plugin manager.
5. Start a new conversation and perform the [first connection check](#first-connection-check).

The plugin is named `maxsurf-mcp` and its marketplace is `maxsurf-local`. These command names were checked against the installed Codex CLI. If your version lacks the plugin commands, use the [source MCP configuration](#connect-the-source-installation-to-codex).

The default workspace is `%LOCALAPPDATA%\MaxsurfMCP\runtime`. To choose another folder for Codex CLI, set the variable before launching it from the same PowerShell session:

```powershell
$env:MAXSURF_MCP_WORKSPACE_ROOTS = 'C:\MaxsurfDesigns'
codex
```

For Codex Desktop, set `MAXSURF_MCP_WORKSPACE_ROOTS` through Windows' **Edit environment variables for your account** dialog, then fully exit and reopen Codex. A variable set in an unrelated terminal does not update an already-running desktop app. The bundle also retains its runtime folder for logs/cache.

OpenAI documents [local plugin marketplaces](https://developers.openai.com/plugins/build/plugins) and [local MCP configuration](https://developers.openai.com/codex/mcp/). GitHub publication and public plugin-directory listing are separate actions.

## PowerShell source installation

Choose this method if you want to run the source directly. It installs the Python environment, then you connect that installation to your chosen client.

### 1. Extract the source

The example assumes the source ZIP is in Downloads:

```powershell
$maxsurfSourceZip = Join-Path $env:USERPROFILE 'Downloads\maxsurf-mcp-0.2.0-rc.5-source.zip'
Expand-Archive -LiteralPath $maxsurfSourceZip -DestinationPath 'C:\MaxsurfSource'
Set-Location 'C:\MaxsurfSource'
```

Confirm that `server.py`, `requirements.lock` and `scripts\install.ps1` are directly inside that extracted tree.

### 2. Install dependencies

```powershell
$maxsurfPython = (py -3.12 -c "import sys; print(sys.executable)").Trim()
.\scripts\install.ps1 -Python $maxsurfPython
```

The script validates Windows, Python 3.12 and 64-bit architecture, creates `.venv`, and installs `requirements.lock`. It refuses to overwrite an existing `.venv`. If one exists, inspect that environment or use a fresh source directory.

If PowerShell blocks the script under your execution policy, follow your organization's approved process. The equivalent source setup can also be run as individual commands:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\requirements.lock
```

Use this alternative only in a fresh source directory. No environment activation is required because the commands address the virtual environment's Python directly.

### 3. Verify the server

```powershell
.\.venv\Scripts\python.exe .\scripts\smoke_stdio.py --python .\.venv\Scripts\python.exe --entry .\server.py
```

Expected output includes version `0.2.0-rc.5`, `90` tools, `session_status: pass` and `consent_refusal: pass`. This check exercises the actual MCP protocol and a refusal before COM connection; **no Bentley application needs to be opened for it**.

Then choose one of the two client configurations below. Do not run `server.py` in a separate terminal for normal use: the MCP client starts the process and owns its input/output connection.

## Connect the source installation to Codex

Edit `%USERPROFILE%\.codex\config.toml`. Add the following block, replacing example paths with your actual source and workspace paths. If `mcp_servers.maxsurf` already exists, update that block rather than adding a duplicate; preserve other settings.

```toml
[mcp_servers.maxsurf]
command = 'C:\MaxsurfSource\.venv\Scripts\python.exe'
args = ['C:\MaxsurfSource\server.py']
cwd = 'C:\MaxsurfSource'
startup_timeout_sec = 60
tool_timeout_sec = 1800

[mcp_servers.maxsurf.env]
MAXSURF_MCP_WORKSPACE_ROOTS = 'C:\MaxsurfDesigns'
MAXSURF_MCP_ATTACH_POLICY = 'activate_existing'
MAXSURF_MCP_ENABLE_DIAGNOSTICS = '0'
MAXSURF_MCP_ENABLE_LEGACY = '0'
```

Restart Codex and start a new conversation. For CLI status, run `codex mcp list`; inside Codex CLI, use `/mcp`.

Codex uses TOML for this configuration, not Claude's `mcpServers` JSON. The timeout allows long solver operations; it does not turn a failed or interrupted native analysis into a pass. See [OpenAI's MCP configuration reference](https://developers.openai.com/codex/mcp/).

## Connect the source installation to Claude Desktop

In Claude Desktop, open **Settings → Developer → Edit Config**. On Windows the manual configuration file is normally `%APPDATA%\Claude\claude_desktop_config.json`.

Add a `maxsurf` entry under `mcpServers`. If other servers are present, merge this entry without replacing them:

```json
{
  "mcpServers": {
    "maxsurf": {
      "command": "C:\\MaxsurfSource\\.venv\\Scripts\\python.exe",
      "args": ["C:\\MaxsurfSource\\server.py"],
      "env": {
        "MAXSURF_MCP_WORKSPACE_ROOTS": "C:\\MaxsurfDesigns",
        "MAXSURF_MCP_ATTACH_POLICY": "activate_existing",
        "MAXSURF_MCP_ENABLE_DIAGNOSTICS": "0",
        "MAXSURF_MCP_ENABLE_LEGACY": "0"
      }
    }
  }
}
```

Save the JSON, fully exit and reopen Claude Desktop, then start a new conversation. This is an alternative to the MCPB extension; avoid enabling both copies in the same client. The configuration location and Developer menu are described in the [official MCP local-server guide](https://modelcontextprotocol.io/docs/develop/connect-local-servers).

## First connection check

For a first model inspection, manually open **Maxsurf Modeler** and finish any native startup/license dialogs. Keep other modules closed unless your task requires them.

Send:

```text
Use Maxsurf MCP to report session status and the configured workspace.
Connect only to the already-running Maxsurf Modeler.
Read its current design identity and surface list.
Do not edit, save, run analysis, or use desktop/display-control tools.
```

For another task, open and connect only the relevant application:

| Task | Application | MCP module |
| --- | --- | --- |
| Hull geometry and Modeler hydrostatics | Maxsurf Modeler | `modeler` |
| Loading, intact/damaged stability | Maxsurf Stability | `stability` |
| Open-water resistance | Maxsurf Resistance | `resistance` |
| Seakeeping | Maxsurf Motions | `motions` |
| Structural frame analysis | Multiframe | `multiframe` |

Check identity, units and current inputs before requesting changes. A connection or populated tool list is not solver-result validation. For display-control tools, separate permission and a supported visible Modeler window are required; they are not part of this initial connection check.

## Troubleshooting

| Problem | What to do |
| --- | --- |
| `Server disconnected` after choosing a file | In Configure, choose `python.exe`, not the MCPB. Recheck Python 3.12 and 64-bit architecture. |
| Save is disabled in Configure | Both the Python file and workspace folder must be selected. |
| `py` or `codex` is not recognized | Install/repair the corresponding launcher or CLI, then open a new PowerShell window. |
| `.venv` already exists | The source installer stops intentionally. Inspect it or use a fresh source directory. |
| Installed extension has no tools | Check its configuration, enable it, restart the client and start a new conversation. Inspect client logs if it still fails. |
| Version or tool count is unexpected | Check for an older extension, duplicate manual entry or cached plugin. |
| Application is not running | Open the named Bentley module manually. |
| COM class is not registered | Check the Bentley installation and supported installer repair process. |
| License/automation access fails | Resolve the entitlement in Bentley's application; MCP installation does not supply a license. |
| File is outside the workspace | Use an authorized working copy inside the selected design folder. |
| A call times out | The native operation may still be running. Inspect its state before repeating a write or calculation. |

Current acceptance limits, including clean-user client installation and native persistence, remain in [Release status](RELEASE_STATUS.md). Installation instructions do not claim those gates have passed. Review [Security](../SECURITY.md) and [the project license](../LICENSE) before deployment.
