<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
   CodeMark: SCLLC1-SassyMCP-BGV4VTL4G62D
-->
# SassyMCP for VS Code

*Last updated: 2026-05-15 — extension v1.4.1, server v1.4.1*

One MCP server replacing 75+. Auto-configures every detected MCP client on your machine — Claude Desktop, Cursor, Windsurf, Continue, Cline, Zed, Grok, and VS Code's Copilot agent — so all your AI tools share the same brain.

## What this extension does

On activation, this extension:

1. Locates `sassymcp.exe` (PATH lookup, or override via `sassymcp.exePath` setting)
2. Runs `sassymcp install` to patch every detected MCP client's config (idempotent — re-running is a noop)
3. Adds a status bar item showing your license tier and brain health
4. Provides command palette commands for setup, audit log access, _DELETE_ folder review, and tool group management

## Prerequisites

You need `sassymcp.exe` on your PATH OR set `sassymcp.exePath` in VS Code settings. Get it from:

- One-click install: download `sassymcp.dxt` and double-click (Claude Desktop)
- Portable: extract `sassymcp-portable.zip` and add to PATH
- License: free tier includes everything; Pro tier (sassyconsultingllc.com/pricing) unlocks Android + Linux + advanced security

## Commands

- `SassyMCP: Run Setup Wizard` — opens a webview for first-time persona configuration
- `SassyMCP: Reinstall Client Configs` — re-runs `sassymcp install` (e.g., after moving the exe)
- `SassyMCP: Open Audit Log` — opens `~/.sassymcp/audit.log` in the editor
- `SassyMCP: Open _DELETE_ Folder` — reveals `~/.sassymcp/_DELETE_/` in the file manager (review intercepted deletes before purging)
- `SassyMCP: Show Brain Status` — info window with license tier, memory entry count, recent audit tail

## Settings

- `sassymcp.exePath` — override path to sassymcp.exe
- `sassymcp.runInstallOnActivation` — run `sassymcp install` on activation (default: true)
- `sassymcp.installRunOnce` — only run install on first activation per workspace (default: true)

## Source

The underlying SassyMCP server provides 270 tools across 35 modules in 17 groups (67 Python source files, 18,497 lines).

github.com/sassyconsultingllc/SassyMCP — MIT licensed.
