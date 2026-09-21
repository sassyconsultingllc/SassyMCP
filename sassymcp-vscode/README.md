<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
   CodeMark: SCLLC1-SassyMCP-BGV4VTL4G62D
-->
# SassyMCP for VS Code

*Last updated: 2026-09-16 — extension v1.15.1, server v1.15.1*

One MCP server replacing 75+. Auto-configures every detected MCP client on your machine — Claude Desktop, Cursor, Windsurf, Continue, Cline, Zed, Grok, and VS Code's Copilot agent — so all your AI tools share the same brain.

## What this extension does

On activation, this extension:

1. Locates `sassymcp.exe` (PATH lookup, or override via `sassymcp.exePath` setting)
2. Runs `sassymcp install` to patch every detected MCP client's config (idempotent — re-running is a noop)
3. Adds a status bar item showing supporter-tier label (optional) and brain health
4. Provides command palette commands for the Sassy Brain Cockpit, setup, audit log access, `_DELETE_` folder review, and brain status

## Prerequisites

You need `sassymcp.exe` on your PATH OR set `sassymcp.exePath` in VS Code settings. Get it from:

- GitHub Release: [latest](https://github.com/sassyconsultingllc/SassyMCP/releases/latest) (`sassymcp.exe`, or install the `.vsix` from the same release)
- PyPI: `pip install sassymcp` (current index latest is 1.15.1)
- One-click Claude Desktop: download `sassymcp.dxt` / `.mcpb` from the same release and double-click

There is no Pro/free tool split as of v1.13.0. Every tool group ships unlocked. A LemonSqueezy key is an optional supporter purchase (seat + label), not a feature unlock. Buy at [sassyconsultingllc.com/store](https://sassyconsultingllc.com/store) if you want to support development.

The Visual Studio Marketplace listing is not live yet. Sideload `sassymcp-1.15.1.vsix` from GitHub Releases until it is.

## Commands

- `SassyMCP: Open Sassy Brain Cockpit`
- `SassyMCP: Run Setup Wizard` — first-time persona configuration
- `SassyMCP: Reinstall Client Configs` — re-runs `sassymcp install`
- `SassyMCP: Open Audit Log` — opens `~/.sassymcp/audit.log`
- `SassyMCP: Open _DELETE_ Folder` — review intercepted deletes
- `SassyMCP: Show Brain Status` — supporter label, memory, recent audit tail

## Settings

- `sassymcp.exePath` — override path to sassymcp.exe
- `sassymcp.runInstallOnActivation` — run `sassymcp install` on activation (default: true)
- `sassymcp.installRunOnce` — only run install on first activation per workspace (default: true)
- `sassymcp.repoPath` / `sassymcp.pythonPath` / `sassymcp.hermesNodePath` — cockpit + Hermes peer
- `sassymcp.hermesAutorun` — off by default (Hermes proposes; you approve)

## License

This VS Code extension is MIT licensed (`sassymcp-vscode/LICENSE`).

The SassyMCP Python package and repository root (`LICENSE`) are **Proprietary** — Copyright (c) 2026 Sassy Consulting LLC. The extension wrapping the installer/cockpit is MIT; the server it launches is not.

## Source

github.com/sassyconsultingllc/SassyMCP
