# Maxsurf MCP 0.2.0-rc.5

First public release candidate for connecting Codex and Claude Desktop to locally installed Bentley Maxsurf applications.

**Technical preview.** This package does not claim complete native-suite coverage, production engineering approval or vessel/class certification.

## Install

| Client or method | Asset | Instructions |
| --- | --- | --- |
| Claude Desktop extension | `maxsurf-mcp-0.2.0-rc.5-win-x64-py312.mcpb` | [Claude setup](INSTALL.md#claude-desktop-extension) |
| Codex plugin | `maxsurf-mcp-0.2.0-rc.5-codex-win-x64-py312.zip` | [Codex setup](INSTALL.md#codex-plugin) |
| Source through PowerShell | `maxsurf-mcp-0.2.0-rc.5-source.zip` | [Source installation](INSTALL.md#powershell-source-installation) |

Windows x64, CPython 3.12 x64 and separately licensed Bentley applications are required. The extension/plugin bundles include Python dependencies; Python and Bentley software are installed separately.

For Claude, select the MCPB during installation, then select `python.exe` and a design workspace in Configure. For Codex, extract the dedicated Codex ZIP and register its local marketplace. Open only the Bentley module required for the task.

## Verification and limitations

This build exposes 90 default tools. Recorded offline and native checks are described in [Verification](VERIFICATION.md); outstanding work is listed in [Release status](RELEASE_STATUS.md). A successful installation or connection is not an engineering validation.

Native persistence, consistent cross-program engineering inputs, complete damaged stability, broader Motions/structural acceptance and clean-user client installation still have open gates. See those reports before evaluating the package.

## License

**MIT**. Copyright (c) 2026 Ismail Hakki Celik. Third-party licenses and Bentley entitlements remain separate.

## Distribution

Attach the three assets and the generated `SHA256SUMS-*.json`. Bentley binaries, manuals, sample models, private designs and training material are excluded.

[License](../LICENSE) · [Third-party notices](../THIRD_PARTY_NOTICES.md) · [Security](../SECURITY.md)
