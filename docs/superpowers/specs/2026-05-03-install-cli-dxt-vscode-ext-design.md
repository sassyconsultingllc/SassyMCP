# Install CLI + DXT + VS Code Extension Design

**Date:** 2026-05-03
**Status:** Combined spec for sub-projects #2 through #6 of the multi-client integration plan
**Owner:** SaS

## Scope

Sub-project #1 (concurrency hardening) is shipped. This document covers the remaining 5 sub-projects in one design pass to keep momentum:

- #2 `sassymcp install` CLI
- #3 DXT package
- #4 VS Code extension — installer + status bar
- #5 VS Code extension — webview MVP (commands only; deep brain UI deferred)
- #6 Unified release pipeline

Reference: [2026-05-03-multi-client-integration-design.md](2026-05-03-multi-client-integration-design.md).

## Sub-project #2 — `sassymcp install` CLI

**Goal:** A single Python CLI command that detects every installed MCP client on the box and writes/patches each one's config to register sassymcp.exe.

**Invocation:**
```
sassymcp install                    # auto-detect all, patch all
sassymcp install --client claude    # only patch one client
sassymcp install --dry-run          # show what would change, write nothing
sassymcp install --uninstall        # remove sassymcp from every detected config
sassymcp install --exe-path PATH    # override which exe to register
```

**Architecture:**

A new module `sassymcp/install.py` exposes:
- `detect_clients() -> list[ClientInfo]` — returns a list of detected clients with name, config path, and current state (installed/not-installed)
- `patch_client(client: ClientInfo, exe_path: Path, *, dry_run: bool) -> PatchResult` — patches one client's config to add the sassymcp server entry. Idempotent: re-running with the same exe path is a no-op
- `unpatch_client(client: ClientInfo, *, dry_run: bool) -> PatchResult` — removes sassymcp from the config, leaves other servers alone
- `find_self_exe() -> Path` — locates the running sassymcp.exe (next to script if frozen, else PATH lookup)

**Supported clients** (ordered by user prevalence):

| Client | Config location | Format |
|---|---|---|
| Claude Desktop | `%APPDATA%\Claude\claude_desktop_config.json` (Win), `~/Library/Application Support/Claude/claude_desktop_config.json` (Mac), `~/.config/Claude/claude_desktop_config.json` (Linux) | `mcpServers` dict |
| VS Code (Copilot agent) | `~/.config/Code/User/mcp.json` (cross-platform) and per-workspace `.vscode/mcp.json` | `servers` dict |
| Cursor | `~/.cursor/mcp.json` | `mcpServers` dict |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` | `mcpServers` dict |
| Continue | `~/.continue/config.json` | `experimental.modelContextProtocolServers` array |
| Cline | `~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json` | `mcpServers` dict |
| Roo Code | `~/.config/Code/User/globalStorage/RooVeterinaryInc.roo-cline/settings/cline_mcp_settings.json` | same as Cline |
| Zed | `~/.config/zed/settings.json` → `context_servers` | object |
| Grok Desktop | `%APPDATA%\GrokDesktop\config.json` (Win) | `mcpServers` dict (uses HTTP, args differ) |

**Atomic writes:** patch_client uses `sassymcp._atomic.atomic_write_json` (from sub-project #1) so concurrent installs (e.g., DXT post-install firing while a user manually runs the CLI) don't corrupt configs.

**Backup:** before first write to any config, move the existing file to `<file>.sassymcp-backup-<timestamp>`. Stored once; if a backup already exists, leave it alone (don't overwrite the user's pristine state).

**Output:** human-readable table:
```
Client          | Config                                         | Result
----------------+------------------------------------------------+---------------
Claude Desktop  | C:\Users\...\Claude\claude_desktop_config.json | patched (added)
VS Code Copilot | C:\Users\...\Code\User\mcp.json                | patched (updated)
Cursor          | (not detected)                                 | skipped
Windsurf        | C:\Users\...\codeium\windsurf\mcp_config.json  | patched (already up to date)
```

**Plus** machine-readable JSON when `--json` flag is passed.

**Wiring:** add `[project.scripts]` entry `sassymcp-install = "sassymcp.install:main"` in pyproject.toml so it ships with both the wheel and the PyInstaller-frozen exe.

## Sub-project #3 — DXT package

**Goal:** A double-clickable `sassymcp.dxt` file that installs SassyMCP into Claude Desktop with one click, then runs `sassymcp install` to patch every other detected client.

**DXT format** (Anthropic Desktop Extensions, [docs](https://github.com/anthropics/dxt)):
- Zip file with `.dxt` extension
- `manifest.json` at root describing server type, entry point, and metadata
- For Python servers: bundled Python or stdlib fallback; for binary: just include the exe

**Our DXT layout:**
```
sassymcp.dxt (zip)
├── manifest.json
├── server/
│   └── sassymcp.exe        (the PyInstaller-frozen binary, ~35MB)
├── icon.png                (192x192, optional but recommended)
└── README.md               (visible in Claude Desktop's extension panel)
```

**manifest.json:**
```json
{
  "dxt_version": "0.1",
  "name": "sassymcp",
  "display_name": "SassyMCP",
  "version": "1.3.5",
  "description": "One MCP server replacing 75+ — file ops, shell, GitHub, Android, vision, security audit, persona memory.",
  "author": {"name": "SassyMCP Contributors", "url": "https://sassyconsultingllc.com"},
  "license": "MIT",
  "homepage": "https://github.com/sassyconsultingllc/SassyMCP",
  "icon": "icon.png",
  "server": {
    "type": "binary",
    "entry_point": "server/sassymcp.exe",
    "mcp_config": {
      "command": "${__dirname}/server/sassymcp.exe",
      "args": [],
      "env": {"SASSYMCP_LOAD_ALL": "1"}
    }
  }
}
```

**Post-install hook:** the DXT spec doesn't have a formal post-install hook yet. We use first-run detection in the sassymcp server itself: on startup, if `~/.sassymcp/.installed-other-clients` doesn't exist, fork-and-detach a `sassymcp install --auto-other` subprocess that patches all OTHER detected clients (not Claude Desktop, since that's where DXT installed it). On success, touch the marker file so subsequent starts skip the check.

**Build:** new `scripts/build-dxt.ps1` that:
1. Builds `sassymcp.exe` via existing PyInstaller spec
2. Creates a tempdir with `manifest.json` + `server/sassymcp.exe` + `icon.png` + `README.md`
3. Zips to `dist/sassymcp-v<version>.dxt`

## Sub-project #4 — VS Code extension (installer + status bar)

**Goal:** Extension that detects sassymcp.exe (bundled or PATH), runs `sassymcp install` to patch every detected MCP client, and surfaces a status bar item showing brain health.

**Layout:**
```
sassymcp-vscode/
├── package.json            (extension manifest)
├── tsconfig.json
├── src/
│   ├── extension.ts        (activation, command registration, status bar)
│   ├── installer.ts        (locate exe, run sassymcp install)
│   ├── status.ts           (status bar updates, polls brain health every 30s)
│   └── brain.ts            (read ~/.sassymcp/ files for status display)
├── resources/
│   └── icon.png
└── README.md
```

**Commands** registered in `package.json`:
- `SassyMCP: Run Setup Wizard` — runs `sassymcp setup-wizard` in a terminal
- `SassyMCP: Reinstall Client Configs` — re-runs `sassymcp install`
- `SassyMCP: Open Audit Log` — opens `~/.sassymcp/audit.log` in the editor
- `SassyMCP: Open _DELETE_ Folder` — reveals `~/.sassymcp/_DELETE_/` in the OS file manager
- `SassyMCP: Toggle Tool Group` — quickpick of tool groups
- `SassyMCP: Show Brain Status` — shows tier, memory count, recent audit entries in an info window

**Status bar item** (right side):
- `$(zap) SassyMCP: Pro` — license tier visible
- `$(zap) SassyMCP: Free` — for unlicensed users
- `$(warning) SassyMCP: Not Installed` — when sassymcp.exe can't be found
- Click → runs `Show Brain Status` command

**Activation:** on startup AND on `workspaceContains:**/.sassymcp/persona.md` (so per-workspace per-user setups also light it up).

**Bundled exe:** the .vsix package includes sassymcp.exe (~35MB) for zero-config install. If the bundled exe is older than what's on PATH, prefer PATH.

**Build:** `npm run package` produces `sassymcp-vscode-<version>.vsix`. Publish via `vsce publish`.

## Sub-project #5 — VS Code webview MVP

**Scope-down:** the original spec called for a full persona editor + memory browser + crosslink stream + audit viewer + observability dashboard. For shipping speed, this sub-project ships only:

- **Setup Wizard webview** — a single-page form that mirrors `sassy_setup_wizard`'s questionnaire, posts to a child process running `sassymcp setup-wizard --json`, displays the generated `persona.md` for review.

The remaining webviews (memory browser, crosslink stream, audit, observability) are documented as **future work** in the extension README. They're not blocked by anything and can be added incrementally — webviews are isolated, each is its own React/HTML panel reading `~/.sassymcp/` directly.

This keeps the first ship of the VS Code extension small and reviewable.

## Sub-project #6 — Unified release pipeline

**Goal:** One CI workflow producing all three artifacts from one tag.

**File:** `.github/workflows/release.yml`

**Trigger:** push to `v*.*.*` tag.

**Jobs (sequential, each depends on the prior):**

1. **build-exe** — Windows runner, runs `build.bat`, uploads `sassymcp.exe` and the portable zip as artifacts
2. **build-dxt** — Windows runner, downloads exe artifact, runs `scripts/build-dxt.ps1`, uploads `sassymcp.dxt`
3. **build-vsix** — Ubuntu runner, downloads exe artifact, runs `cd sassymcp-vscode && npm install && npm run package`, uploads `sassymcp-vscode-<version>.vsix`
4. **release** — Ubuntu runner, downloads all 3 artifacts, runs `gh release create $TAG --notes-from-tag` and attaches artifacts

Optional follow-on (not blocking the release):
- **publish-vsix** — `vsce publish` on tag push if `VSCE_PAT` secret is set
- **publish-dxt** — push to wherever DXTs are distributed (Anthropic registry once it exists)

## Order

Build #2 first, then #3 and #4 in parallel (they're independent), then #5 layered on #4, then #6 wraps it all up.

## Plans

Each sub-project gets its own plan file alongside this spec. They're shorter than the concurrency-hardening plan because the work is more mechanical:

- `2026-05-03-install-cli.md` (sub-project #2)
- `2026-05-03-dxt-package.md` (sub-project #3)
- `2026-05-03-vscode-extension-installer.md` (sub-projects #4 + #5 combined)
- `2026-05-03-release-pipeline.md` (sub-project #6)
