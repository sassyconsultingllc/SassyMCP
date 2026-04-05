# SassyMCP

**Unified MCP server for Windows desktop automation, Android device control, security auditing, GitHub operations, web inspection, cross-session communication, and AI workflow persona.**

Compatible with Claude Desktop, Grok Desktop, Cursor, Windsurf, and any MCP client.

> **The official GitHub MCP server has [critical SHA-handling bugs](https://github.com/github/github-mcp-server/issues/2133).** SassyMCP's GitHub module uses correct blob SHA lookups, proper path encoding, atomic multi-file commits via Git Data API, retry logic with exponential backoff, and rate-limit awareness. It's a drop-in replacement that actually works.

## Why SassyMCP?

SassyMCP replaces multiple fragmented MCP servers (Windows-MCP, Desktop Commander, Filesystem, GitHub, etc.) with a single modular server. One install, smaller context footprint, more tools, smarter loading.

**Key differentiators:**
- **Smart Tool Loading** — Only loads tool groups you use. Reduces context window overhead from ~25K tokens to ~5K tokens by default.
- **Usage Tracking** — ML-lite scoring of tool invocations with exponential decay. Your most-used tools load first.
- **Context Estimation** — Built-in tool to measure how much of your 200K context window tool definitions consume.
- **Response Minification** — GitHub API responses stripped of URL metadata bloat (40-70% smaller).
- **Proper GitHub SHA Handling** — Uses blob SHA from Contents API, not ETag from HEAD requests.

## Modules

| Module | Tools | Group | Description |
|--------|-------|-------|-------------|
| **Meta** | 5 | always | Context estimation, tool usage analytics, group management |
| **FileOps** | 9 | core | Read, write, search, move, copy, edit, mkdir, file info |
| **Shell** | 6 | core | PowerShell, CMD, WSL, exec (Python/Node), persistent sessions |
| **UIAutomation** | 5 | core | Desktop state, click, type, hotkeys, screenshots |
| **GitHub Quick** | 6 | github_quick | Daily-driver: push_files, get_file, issue, PR, protect |
| **Persona** | 6 | persona | Expert-mode directives, decision framework, engineering standards |
| **GitHub Full** | 80 | github_full | Complete GitHub API: repos, issues, PRs, actions, security, gists |
| **ADB** | 10 | android | Android shell, packages, file transfer, logcat, screencap |
| **PhoneScreen** | 3 | android | scrcpy start/stop, screen recording |
| **NetworkAudit** | 8 | system | netstat, ARP, WiFi scan, port scan, DNS, traceroute |
| **ProcessManager** | 4 | system | Windows + Android process list/kill, system info |
| **SecurityAudit** | 7 | system | Hash, permissions, certs, APK, firewall, Defender |
| **Registry** | 4 | system | Read, write, export, autorun forensics |
| **Bluetooth** | 3 | system | Windows + Android BT enumeration |
| **EventLog** | 3 | system | Windows Event Log + Android logcat |
| **Clipboard** | 4 | system | Windows + Android clipboard sync |
| **Vision** | 5 | v020 | Screen capture, OCR, find text on screen |
| **AppLauncher** | 7 | v020 | Launch apps, focus/close/resize/snap windows |
| **WebInspector** | 5 | v020 | Security headers, URL screenshots, tech stack detection |
| **Crosslink** | 7 | v020 | Cross-session messaging via HTTP API + SQLite |

**192 tools** across 20 modules. Default load: **22 tools** (meta + core + github_quick + persona).

## Smart Loading

By default, SassyMCP only loads frequently-used tool groups. This keeps tool definitions under 5% of your context window.

```bash
# Default: loads core, github_quick, persona, meta
uv run sassymcp

# Load everything (192 tools, ~25K tokens of context)
SASSYMCP_LOAD_ALL=1 uv run sassymcp

# Load specific groups
SASSYMCP_GROUPS=core,github_quick,system,v020 uv run sassymcp
```

### Available Groups

| Group | Modules | Default |
|-------|---------|--------|
| `core` | fileops, shell, ui_automation | Yes |
| `github_quick` | github_quick (6 lean tools) | Yes |
| `persona` | persona | Yes |
| `github_full` | github_ops (80 tools) | No |
| `android` | adb, phone_screen | No |
| `system` | network_audit, process_manager, security_audit, registry, bluetooth, eventlog, clipboard | No |
| `v020` | vision, app_launcher, web_inspector, crosslink | No |

### Context Estimation

Use the built-in `sassy_context_estimate` tool to see exactly how much context your tool definitions consume.

### Usage Tracking

SassyMCP tracks which tools you use and scores them with exponential decay.
Run `sassy_tool_usage` to see your analytics.
Data persists across sessions in `~/.sassymcp/tool_usage.json`.

## Install

```bash
git clone https://github.com/your-org/SassyMCP.git
cd SassyMCP
uv sync

# Required for GitHub module:
uv pip install httpx

# Optional dependencies:
uv pip install pytesseract playwright
playwright install chromium
```

## Claude Desktop Config

```json
{
  "mcpServers": {
    "sassymcp": {
      "command": "uv",
      "args": ["--directory", "C:\\path\\to\\SassyMCP", "run", "sassymcp"],
      "env": {
        "GITHUB_TOKEN": "ghp_your_token_here"
      }
    }
  }
}
```

### Load All Tools

```json
{
  "mcpServers": {
    "sassymcp": {
      "command": "uv",
      "args": ["--directory", "C:\\path\\to\\SassyMCP", "run", "sassymcp"],
      "env": {
        "GITHUB_TOKEN": "ghp_your_token_here",
        "SASSYMCP_LOAD_ALL": "1"
      }
    }
  }
}
```

### Custom Groups

```json
{
  "mcpServers": {
    "sassymcp": {
      "command": "uv",
      "args": ["--directory", "C:\\path\\to\\SassyMCP", "run", "sassymcp"],
      "env": {
        "GITHUB_TOKEN": "ghp_your_token_here",
        "SASSYMCP_GROUPS": "core,github_quick,github_full,system"
      }
    }
  }
}
```

## GitHub Module — Why Not Just Use the Official One?

The [official GitHub MCP server](https://github.com/github/github-mcp-server) has four critical bugs in its `create_or_update_file` implementation ([issue #2133](https://github.com/github/github-mcp-server/issues/2133)):

1. **ETag vs Blob SHA Mismatch** — Compares user-provided blob SHA against HTTP ETag (an opaque cache token), causing ~30-40% false "SHA mismatch" failures.
2. **PathEscape Breaks Multi-Segment Paths** — `url.PathEscape` encodes `/` as `%2F`, breaking paths like `src/lib/utils.py`.
3. **Blind ETag-as-SHA Injection** — When no SHA is provided, extracts ETag and uses it as SHA, which the API rejects.
4. **Deferred Body Close Stacking** — Multiple `defer resp.Body.Close()` calls can stall HTTP/2 connections.

SassyMCP's GitHub module:
- Uses `GET /repos/{owner}/{repo}/contents/{path}` to get the **real blob SHA**
- Splits into `create_file` and `update_file` (no ambiguity)
- Defaults to `push_files` via Git Data API (create tree → commit → update ref) for atomic multi-file operations
- Includes retry logic with exponential backoff on 5xx errors
- Monitors `X-RateLimit-Remaining` and auto-waits on rate limits
- Strips 40-70% of response metadata to save context tokens

## Requirements

- Python 3.11+, Windows 10/11
- Optional: ADB, scrcpy, nmap, Tesseract OCR, Chrome/Playwright

## License

MIT License © 2026
