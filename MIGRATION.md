<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
   CodeMark: SCLLC1-SassyMCP-CTPPSSXQUS7R
-->
# Migrating to SassyMCP

*Last updated: 2026-05-15 — v1.4.1*

**Replace 3+ MCP servers with one.** This guide helps you switch from Windows-MCP, Desktop Commander, and/or Filesystem MCP to SassyMCP.

## Why Switch?

| Before | After |
|--------|-------|
| Windows-MCP (~8K tokens) | **SassyMCP (~10K tokens total)** |
| Desktop Commander (~20K tokens) | replaces all three + adds |
| Filesystem MCP (~varies) | Android, security, forensics |
| **~35K+ tokens overhead** | **~10K tokens overhead** |

- **~25K fewer tokens** consumed by tool definitions
- **274 tools** across 36 modules in 18 groups (more than the 3 servers combined)
- **Syntax normalization** — no more PowerShell `&&` crashes
- **Android integration** — ADB, scrcpy, logcat built in
- **Security tools** — hash, certs, firewall, Defender, APK analysis
- **One process** instead of three

## Quick Start

### 1. Prerequisites

```
Python 3.11+
uv (pip install uv, or: curl -LsSf https://astral.sh/uv/install.sh | sh)
```

Optional (for extended features):
```
ADB (Android SDK Platform Tools) — for Android device control
scrcpy — for live Android screen mirroring
nmap — for advanced port scanning
```

### 2. Clone

```bash
git clone https://github.com/sassyconsultingllc/SassyMCP.git
cd SassyMCP
uv sync
```

### 3. Test It Works

```bash
uv run sassymcp
```

You should see it start and wait for MCP stdio input. Ctrl+C to exit.


### 4. Add to Claude Desktop

Edit `claude_desktop_config.json`:

**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

Add this to the `mcpServers` section:

```json
"sassymcp": {
  "command": "uv",
  "args": ["--directory", "C:\\path\\to\\SassyMCP", "run", "sassymcp"]
}
```

Replace `C:\\path\\to\\SassyMCP` with your actual clone path.

### 5. Remove Old Servers

Remove or disable these from your config (if present):
- `Windows-MCP`
- `desktop-commander`
- `filesystem` (the Filesystem MCP server)

### 6. Restart Claude Desktop

Close and reopen Claude Desktop. SassyMCP should appear in your MCP tools.

## Tool Name Mapping

If you're used to the old tool names, here's what replaced what:

### From Windows-MCP
| Old Tool | SassyMCP Replacement |
|----------|---------------------|
| `State-Tool` | `sassy_desktop_state` (leaner output, no taskbar bloat) |
| `Click-Tool` | `sassy_click` |
| `Type-Tool` | `sassy_type_text` (auto ctrl-a+backspace clear) |
| `Scroll-Tool` | Use `sassy_hotkey` with Page Up/Down |
| `Shortcut-Tool` | `sassy_hotkey` |
| `Powershell-Tool` | `sassy_shell` (auto-normalizes && syntax) |
| `Scrape-Tool` | Use Claude's built-in web_fetch |

### From Desktop Commander
| Old Tool | SassyMCP Replacement |
|----------|---------------------|
| `read_file` | `sassy_read_file` |
| `write_file` | `sassy_write_file` |
| `list_directory` | `sassy_list_dir` |
| `start_search` | `sassy_search_files` |
| `start_process` | `sassy_shell` |
| `edit_block` | `sassy_edit_block` (fuzzy match + diff reporting) |
| `get_file_info` | `sassy_file_info` |

### From Filesystem MCP
| Old Tool | SassyMCP Replacement |
|----------|---------------------|
| `read_file` | `sassy_read_file` |
| `write_file` | `sassy_write_file` |
| `list_directory` | `sassy_list_dir` |
| `search_files` | `sassy_search_files` |
| `move_file` | `sassy_move` |
| `create_directory` | `sassy_mkdir` |
| `get_file_info` | `sassy_file_info` |

### New Tools (not in any predecessor)
- `sassy_edit_block`, `sassy_edit_multi` — surgical diff-based editing with fuzzy match
- `sassy_mkdir` — directory creation with parent auto-create
- `sassy_adb_*` — 10 Android device tools
- `sassy_scrcpy_*` — 3 screen mirroring tools
- `sassy_netstat`, `sassy_port_scan`, `sassy_arp_table` — network audit
- `sassy_hash_file`, `sassy_cert_check`, `sassy_apk_info` — security
- `sassy_reg_*`, `sassy_autorun_entries` — registry forensics
- `sassy_bt_*` — Bluetooth enumeration
- `sassy_eventlog*` — Windows Event Log
- `sassy_clipboard_*` — cross-device clipboard
- `sassy_crosslink_*` — cross-session communication (SQLite + HTTP API)
- `sassy_persona_*` — workflow persona and dev practices
- `sassy_gh_*` — 80-tool full GitHub API replacement
- `sassy_ghq_*` — 6-tool lean GitHub daily-driver
- `sassy_url_*` — web inspection (headers, tech stack, performance, links)
- `sassy_screen_*`, `sassy_find_text_on_screen` — desktop vision + OCR
- `sassy_launch_*`, `sassy_focus_window`, `sassy_snap_window` — app/window management
- `sassy_context_estimate`, `sassy_tool_usage`, `sassy_tool_groups` — meta/introspection

## Publishing to npm/PyPI (Optional)

SassyMCP can be published for `uvx` one-liner install:

```bash
# Build
uv build

# Publish to PyPI
uv publish
```

Then users can run:
```bash
uvx sassymcp
```

And config becomes:
```json
"sassymcp": {
  "command": "uvx",
  "args": ["sassymcp"]
}
```

## Contributing

PRs welcome. Each module is self-contained in `sassymcp/modules/`.
To add a new module:

1. Create `sassymcp/modules/your_module.py`
2. Implement `def register(server: Server):`
3. Add `@server.tool()` decorated async functions
4. Add your module to the appropriate group in `TOOL_GROUPS` in `sassymcp/modules/_tool_loader.py`

### New Tools (since v1.3.x)
- `sassy_memory_*` — 9 persistent cross-session memory tools (remember, recall, search, context, milestones, handoff)
- `sassy_update_*` — 4 self-update tools (check, list, changelog, apply)
- `sassy_combo_*` — 4 multi-step workflow tools (pr_review, phone_observe, codebase_grep)
- `sassy_shell_confirm` — confirmation flow for intercepted destructive commands
- MCP prompts — slash-menu shortcuts for common workflows

## License

MIT License (c) 2026
