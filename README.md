# SassyMCP

**One MCP server to replace them all.**

**270 tools | 35 modules | 18 tool groups | Replaces 75+ MCP servers | 34MB standalone exe**

*Last updated: 2026-06-17 — v1.7.2 | one-time perpetual licensing (free / pro / forensics)*

Compatible with Claude Desktop, Grok Desktop, Cursor, Windsurf, and any MCP client.

> **The official GitHub MCP server has [critical SHA-handling bugs](https://github.com/github/github-mcp-server/issues/2133).** SassyMCP's GitHub module uses correct blob SHA lookups, proper path encoding, atomic multi-file commits via Git Data API, retry logic with exponential backoff, and rate-limit awareness. It's a drop-in replacement that actually works.

## Why SassyMCP?

The MCP ecosystem is fragmented. Need file operations? Install Filesystem server. Need terminal? Desktop Commander. GitHub? Another server. Android? Another. Screenshots? Another. You end up with 6-10 separate MCP servers, each consuming context window, each with its own config, bugs, and update cycle.

SassyMCP replaces **75+ individual MCP servers** — including [Desktop Commander](https://github.com/wonderwhy-er/DesktopCommanderMCP) (5.9k stars), [Windows-MCP](https://github.com/CursorTouch/Windows-MCP) (5k stars), [GitHub MCP Server](https://github.com/github/github-mcp-server) (28.6k stars), Anthropic's official [Filesystem](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem) and [Memory](https://github.com/modelcontextprotocol/servers/tree/main/src/memory) servers, [mobile-mcp](https://github.com/mobile-next/mobile-mcp) (4.4k stars), and dozens more — with a single 34MB exe.

**Key differentiators:**
- **Smart Tool Loading** — Only loads tool groups you use. Reduces context window overhead from ~25K tokens to ~5K tokens by default.
- **Dynamic Vision** — Real-time screen monitoring with change detection for both desktop and Android. No more screenshot-and-pray.
- **Android Interaction** — Full phone control via UI accessibility tree: tap, swipe, type, with automatic sensitive context detection (auth/payment screens auto-block).
- **Pause/Resume** — User takes over the phone for manual steps (login, 2FA, account selection), AI watches and learns, then resumes autonomously.
- **Usage Tracking** — ML-lite scoring of tool invocations with exponential decay. Your most-used tools load first.
- **Context Estimation** — Built-in tool to measure how much of your 200K context window tool definitions consume.
- **Response Minification** — GitHub API responses stripped of URL metadata bloat (40-70% smaller).
- **Safe Delete** — Delete commands (`rm`, `del`, `Remove-Item`, etc.) are intercepted across all shells. Instead of destroying files, targets are moved to a `_DELETE_/` staging folder in the same directory for human review — protecting against AI hallucinations.
- **Self-Modification** — Hot-reload modules without restart, git-backed rollback on syntax errors.
- **Guided Setup** — Wizard walks through persona, GitHub token, SSH credentials, and optional tool discovery.

## What It Replaces

| Domain | SassyMCP Module | Replaces | Top Alternative |
|--------|----------------|----------|----------------|
| File operations | FileOps, Editor | 11 filesystem/editor MCP servers | [Filesystem](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem) (Anthropic official) |
| Shell / terminal | Shell, Session | 5 shell MCP servers | [Desktop Commander](https://github.com/wonderwhy-er/DesktopCommanderMCP) (5.9k stars) |
| Desktop automation | UIAutomation, Vision | 9 desktop MCP servers | [Windows-MCP](https://github.com/CursorTouch/Windows-MCP) (5k stars) |
| GitHub / Git | GitHub Quick, GitHub Full | 5 GitHub/Git MCP servers | [GitHub MCP Server](https://github.com/github/github-mcp-server) (28.6k stars) |
| Android / phone | ADB, PhoneScreen | 9 mobile MCP servers | [mobile-mcp](https://github.com/mobile-next/mobile-mcp) (4.4k stars) |
| Network scanning | NetworkAudit | 8 nmap/security MCP servers | [mcp-for-security](https://github.com/cyproxio/mcp-for-security) (601 stars) |
| Security auditing | SecurityAudit | 8 security MCP servers | [mcp-security-hub](https://github.com/FuzzingLabs/mcp-security-hub) (509 stars) |
| SSH / remote Linux | Linux | 7 SSH MCP servers | [ssh-mcp](https://github.com/tufantunc/ssh-mcp) (365 stars) |
| Memory / state | Memory, StateManager | 7 memory MCP servers | [mcp-memory-service](https://github.com/doobidoo/mcp-memory-service) (1.6k stars) |
| OCR / screen reading | Vision | 7 OCR/vision MCP servers | [PaddleOCR MCP](https://paddlepaddle.github.io/PaddleOCR/) |
| Web inspection | WebInspector, Utility | 7 web/fetch MCP servers | [Fetch](https://github.com/modelcontextprotocol/servers/tree/main/src/fetch) (Anthropic official) |
| Windows system | Registry, ProcessManager, Clipboard, EventLog, Bluetooth | 13 Windows MCP servers | [Windows-MCP](https://github.com/CursorTouch/Windows-MCP) (5k stars) |
| Hot reload | SelfMod | 3 reload MCP servers | [mcp-reloader](https://github.com/mizchi/mcp-reloader) |

**Plus features with no MCP server equivalent:** phone pause/resume with sensitive context detection (auto-blocks on login/payment screens), operational hooks (14 expert playbooks), safe delete interception, Windows autorun forensics, Android+Windows clipboard sync, usage-weighted smart loading.

## Licensing & Tiers

SassyMCP is sold through [LemonSqueezy](https://sassyconsultingllc.com/sassymcp) as a **one-time perpetual license** (no subscriptions). Free tier runs out of the box with no key required — paid tiers unlock additional tool groups. Buy once, own forever; refunds revoke automatically.

| Tier | What unlocks | What you get |
|---|---|---|
| **Free** | core, meta, github_quick, persona, setup, infrastructure, utility, selfmod, memory, updater, prompts, combos | File ops, shell, desktop automation, daily-driver GitHub, persistent memory, surgical edit, audit log, multi-client install — enough to use SassyMCP as a daily driver, just without the heavy automation surfaces. |
| **Pro** | Free + `github_full`, `android`, `v020`, `linux`, `system` | Full GitHub API (80 tools), Android phone control + dynamic vision, OCR, app launcher, web inspector, crosslink, remote Linux SSH, system monitoring, clipboard sync, event logs. |
| **Forensics** *(add-on)* | `forensics` group: `security_audit`, `registry` | Stacks additively on top of Free or Pro. APK inspection, certificate validation, hash + permission audit, Defender / firewall status, registry read/write/export, autorun forensics. |

**Activation flow:**
1. Purchase at `https://sassyconsultingllc.com/sassymcp` — LemonSqueezy emails you a key (`XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX`).
2. Activate from your AI agent: `sassy_setup_license action=activate key=...`
3. Or from a terminal: `sassymcp.exe setup` opens the interactive menu.
4. SassyMCP calls LS to register this machine as an instance, mints a local HMAC-signed payload, and unlocks the paid groups on the next server restart.

**Offline operation:** Once activated, the local HMAC payload lets SassyMCP run fully offline. A weekly authoritative re-check against LS catches refunds and cancellations; a faster startup check against the SassyMCP billing oracle cuts refund-to-revocation latency to seconds.

**Safe failure modes:** Missing, expired, tampered, or corrupt license files silently downgrade to free tier — the product never bricks. Network errors during activation or revalidation leave the local license intact.

**Dev escape hatch:** Set `SASSYMCP_LICENSE_BYPASS=1` to unlock all groups regardless of license state. Intended for development on the upstream codebase, CI, and air-gapped support cases. Logged at WARNING level.

## Modules

| Module | Tools | Group | Description |
|--------|-------|-------|-------------|
| **Meta** | 9 | meta | Context estimation, tool usage analytics, group management |
| **FileOps** | 10 | core | Read, write, search, move, copy, edit, mkdir, file info, safe delete |
| **Shell** | 2 | core | PowerShell, CMD, WSL execution with syntax normalization and delete interception |
| **UIAutomation** | 6 | core | Desktop state, click, type, hotkeys, screenshots, screen info |
| **Editor** | 2 | core | Surgical find/replace, multi-edit |
| **Audit** | 4 | core | Audit log read, search, clear, false-positive tracking |
| **Session** | 6 | core | Persistent terminal sessions (start, read, send, stop) |
| **GitHub Quick** | 6 | github_quick | Daily-driver: push_files, get_file, issue, PR, protect |
| **Persona** | 7 | persona | Expert-mode directives, decision framework, engineering standards |
| **Utility** | 11 | utility | Env vars, toast, zip/tar/unzip/untar, HTTP requests, file diff |
| **SelfMod** | 8 | selfmod | Self-edit, hot-reload, restart, rollback, status |
| **Setup** | 8 | setup | Setup wizard, GitHub token guide, SSH setup, tool checker, license activation |
| **ToolsManager** | 1 | setup | External tool bootstrap and detection |
| **Observability** | 3 | infrastructure | Health, metrics, tool stats |
| **StateManager** | 3 | infrastructure | Persistent key-value state across sessions |
| **RuntimeConfig** | 3 | infrastructure | Runtime config, recent tool calls |
| **Memory** | 9 | memory | Persistent cross-session memory, milestones, task handoffs, pattern learning |
| **Updater** | 4 | updater | Version checks, changelog, self-update (Kali-style) |
| **GitHub Full** | 80 | github_full | Complete GitHub API: repos, issues, PRs, actions, security, gists |
| **ADB** | 10 | android | Android shell, packages, file transfer, logcat, screencap |
| **PhoneScreen** | 14 | android | UI tree reader, phone glance/watch, tap/swipe/type/key, pause/resume, scrcpy |
| **NetworkAudit** | 7 | system | netstat, ARP, WiFi scan, port scan, DNS, traceroute |
| **ProcessManager** | 5 | system | Windows + Android process list/kill, system info |
| **SecurityAudit** | 7 | forensics | Hash, permissions, certs, APK, firewall, Defender — *requires Forensics add-on* |
| **Registry** | 4 | forensics | Read, write, export, autorun forensics — *requires Forensics add-on* |
| **Bluetooth** | 3 | system | Windows + Android BT enumeration |
| **EventLog** | 3 | system | Windows Event Log + Android logcat |
| **Clipboard** | 4 | system | Windows + Android clipboard sync |
| **Vision** | 8 | v020 | Screen capture, OCR, dynamic glance/watch/diff |
| **AppLauncher** | 6 | v020 | Launch apps, focus/close/resize/snap windows |
| **WebInspector** | 5 | v020 | Security headers, URL screenshots, tech stack detection |
| **Crosslink** | 7 | v020 | Cross-session messaging via HTTP API + SQLite |
| **Linux** | 1 | linux | Remote SSH execution via plink |
| **Combos** | 4 | combos | Multi-step workflows in one call: PR review, phone observe, codebase grep |
| **Prompts** | 0 | prompts | MCP slash-menu shortcuts (pr-review, phone-status, resume, brain-status, setup-sassy) |

## Dynamic Vision

### Desktop (Vision module)

Traditional MCP screenshots are blind — you capture one frame and hope it's the right one. SassyMCP's dynamic vision changes this:

| Tool | Purpose |
|------|---------|
| `sassy_screen_glance` | Fast grayscale capture at ~3-6KB. Call repeatedly to "watch" the screen. |
| `sassy_screen_watch` | Monitor for N seconds, returns **only frames where content changed** (pixel diff threshold). |
| `sassy_screen_diff` | Before/after comparison — takes frame, waits, takes another, returns both + a diff image highlighting changes. |

All three use grayscale + heavy JPEG compression to keep context cost minimal. A glance is ~2KB vs ~14KB for a full-color capture.

### Android (PhoneScreen module)

The phone isn't just a camera target — SassyMCP reads its UI accessibility tree:

| Tool | Purpose |
|------|---------|
| `sassy_phone_ui` | Reads **every visible UI element** — text, description, coordinates, clickable/focused/checked state. Structured data, not pixels. |
| `sassy_phone_state` | Foreground app, screen on/off, battery, WiFi, notification count. |
| `sassy_phone_glance` | Low-res grayscale phone screenshot via direct pipe (~4-8KB). |
| `sassy_phone_watch` | Monitors UI tree changes over duration. Returns snapshots only when elements change. |

## Phone Interaction

Full touch input via ADB — the AI can operate the phone:

| Tool | Purpose |
|------|---------|
| `sassy_phone_tap` | Tap screen coordinates |
| `sassy_phone_swipe` | Swipe between two points |
| `sassy_phone_type` | Type text into focused field |
| `sassy_phone_key` | Send key events (HOME, BACK, ENTER, VOLUME, etc.) |
| `sassy_phone_open` | Launch an app by package name |

### Sensitive Context Detection

All interaction tools (tap, swipe, type) automatically scan the UI tree before executing. If they detect **login screens, payment forms, account selectors, 2FA prompts, or permission dialogs**, the tool **refuses to execute** and returns what it sees instead. The AI then describes the screen to you and asks what to do. Pass `confirmed=True` after explicit user approval.

## Safe Delete (Delete Interception)

AI agents can hallucinate destructive commands. SassyMCP intercepts **all** delete-family commands across every shell and every tool entry point, then moves targets to a `_DELETE_/` staging folder instead of destroying them. Every interception is written to the audit log with the raw command, parsed targets, and move results.

**Coverage — every destructive path is gated:**

| Tool | Guard |
|------|-------|
| `sassy_shell` | Intercepts delete commands, stages targets to `_DELETE_/` |
| `sassy_session_send` / `sassy_session_start` | Same interceptor — persistent terminals can't bypass |
| `sassy_linux_exec` | Refuses destructive commands on the remote host |
| `sassy_adb_shell` | Refuses destructive commands on Android device (override with `allow_destructive=True`) |
| `sassy_safe_delete` | Explicit staging tool — moves symlinks as symlinks (no `resolve()` in the move path) |
| `sassy_write_file` (rewrite mode) | Snapshots existing file into `_DELETE_/` before overwriting |
| `sassy_edit_block` / `sassy_edit_multi` | Refuses protected paths, snapshots existing content to `_DELETE_/<name>.pre-edit.<ts><ext>` before applying |
| `sassy_copy` | Refuses existing destination (no silent overwrite), refuses protected src/dst |
| `sassy_move` | Refuses silent destination overwrite, refuses protected src/dst |
| `sassy_selfmod_edit` / `sassy_selfmod_write` | Bad-syntax writes rename to `<name>.bad.<ts>` (never unlink) |
| `sassy_selfmod_rollback` | Requires `confirm='YES'` — discards uncommitted changes |
| `sassy_audit_clear` | Rotates the audit log instead of deleting it; requires `confirm='YES'` |

**Intercepted command keywords:** `rm`, `rmdir`, `unlink` (Unix/WSL), `del`, `erase`, `rd` (CMD), `Remove-Item`, `ri`, `rni` (PowerShell aliases), `sdelete` / `sdelete64` (Sysinternals).

**Also caught (beyond bare keywords):**
- Shell wrappers — `powershell -c "del foo"`, `cmd /c del foo`, `bash -c "rm foo"`, `wsl -- rm foo` (payload is recursively scanned)
- Base64 payloads — `powershell -EncodedCommand <base64>` is decoded (UTF-16-LE) and recursively scanned
- `.NET` calls — `[System.IO.File]::Delete(...)`, `[System.IO.Directory]::Delete(...)`
- `Clear-Content`, `Set-Content -Value ''` (literal empty only — normal `-Value "foo"` is allowed)
- `Out-File -Force`, `New-Item -Force` (overwrite-style)
- `copy /y`, `xcopy /y` — CMD silent-overwrite flags
- **`robocopy /MIR` and `robocopy /PURGE`** — mirror/purge modes delete destination files
- Truncate-by-redirect — `> file.txt`, `type foo > bar.txt`, `cmd; > file.txt` (append `>>` and stream `2>` / `&>` correctly ignored)
- `Move-Item foo $null`
- Assignment prefixes — `$null = ri foo` is correctly unwrapped

**Protected roots** (refused by every guarded tool, not just the interceptor): the SassyMCP source tree itself, `~/.sassymcp/` (audit + config), and any `_DELETE_/` staging folder (no staging recursion). Protection uses `resolve()` so path traversal (`..\`), symlinks, and Windows 8.3 short names all normalize correctly before the check.

| Scenario | Result |
|----------|--------|
| `rm -rf /` | **Hard-blocked** by the always-on blocklist — no move attempted |
| `rm important.txt` | Blocked, file moved to `./_DELETE_/important.txt` |
| `del /q *.log` | Blocked, all `.log` files moved to `./_DELETE_/` |
| `Remove-Item -Path C:\data\old` | Blocked, `old` moved to `C:\data\_DELETE_\old` |
| `cmd /c del foo` (wrapper) | Blocked — payload is unwrapped and intercepted |
| `gci *.tmp \| ri` (PS alias) | Blocked — `ri` alias is matched |
| `sassy_write_file("doc.txt", ..., "rewrite")` on existing file | Prior content snapshotted to `_DELETE_/doc.overwrite.<ts>.txt` first |
| `ls -la` | Executes normally — not a delete command |

Name collisions in `_DELETE_/` are handled automatically with counter suffixes (`file.txt`, `file_1.txt`, `file_2.txt`). On Windows, paths with backslashes (`C:\Users\foo\bar`) are preserved correctly by the parser — no `shlex` mangling.

### Pause / Resume

For complex flows where the user needs to take over:

| Tool | Purpose |
|------|---------|
| `sassy_phone_pause` | Blocks all interaction tools. Observation tools (ui, glance, watch) still work. |
| `sassy_phone_resume` | Unblocks interaction. AI picks up where it left off, informed by everything it observed during pause. |

**Workflow:**
1. AI operates phone autonomously for routine tasks
2. AI hits a login screen → sensitive context auto-blocks → AI tells the user
3. User says "hold on" → AI calls `sassy_phone_pause`
4. User logs in manually. AI watches via `sassy_phone_ui` / `sassy_phone_glance`
5. User says "done" → AI calls `sassy_phone_resume`
6. AI continues, now aware the user logged into a specific account

## Guided Setup

On first launch (no `~/.sassymcp/persona.md`), the wizard tools are prominently available and the AI is given an onboarding playbook via the registered hook. The flow is conversational — the AI asks, you answer, it calls the tools.

### Onboarding procedure (recommended order)

Each step is independent and skippable. Just tell the AI "set up SassyMCP" or "let's get started" and it'll walk this:

| # | Step | Tool | What happens |
|---|------|------|--------------|
| 0 | **License** | `sassy_setup_license action="status"` then `action="activate" key=...` | Reports current tier. If you have a Pro/Forensics key, activates it and unlocks the gated tool groups. |
| 1 | **Persona** | `sassy_setup_wizard` | Asks the questionnaire below, generates `~/.sassymcp/persona.md`, hot-reloads the persona module. |
| 2 | **GitHub** | `sassy_setup_github action="check"` → `action="open_browser"` → `action="save_token" token=...` | Validates an existing `GITHUB_TOKEN`, or opens [github.com/settings/tokens](https://github.com/settings/tokens?type=beta), walks you through scope selection, then saves and re-validates. |
| 3 | **SSH / Linux** | `sassy_setup_ssh action="check"` → `action="save" host=... user=... password=...` → `action="test"` | Locates `plink`, stores creds in process env, runs an `echo` round-trip to verify. |
| 4 | **Optional tools** | `sassy_setup_check_tools` | Scans for `nmap`, `tesseract`, `adb`, `scrcpy`, `plink`, Chrome; reports install URLs for what's missing. |
| ✓ | **Status check** | `sassy_setup_status` | Shows what's configured, what's still missing, and the action_required hint. Run this any time. |
| ⚙️ | **Auth tokens** | `sassy_setup_generate_token client_id="claude-desktop"` | Generates a 32-byte URL-safe token for HTTP/tunnel mode and writes `~/.sassymcp/tokens.json`. |

Skip any step with `action="skip"` (where supported) — config records the skip so the AI doesn't re-prompt.

### The persona questionnaire

`sassy_setup_wizard` accepts these fields. All optional — defaults shown.

| Field | Values / format | Default |
|---|---|---|
| `role` | `developer` \| `sysadmin` \| `security` \| `devops` \| `data` \| `designer` \| `manager` \| `other` | `developer` |
| `expertise_level` | `junior` \| `mid` \| `senior` \| `principal` \| `staff` | `senior` |
| `specializations` | Comma-separated areas — e.g. `"web security, cloud infra, mobile"` | empty |
| `languages` | Comma-separated — e.g. `"Python, Rust, TypeScript, Go"` | empty |
| `frameworks` | Comma-separated — e.g. `"React, FastAPI, Cloudflare Workers"` | empty |
| `systems` | Newline-separated `hostname — OS — role` entries | empty |
| `projects` | Newline-separated `name — status — description` entries | empty |
| `communication_style` | `terse` (code only) \| `balanced` (brief explanations) \| `verbose` (detailed rationale) | `terse` |
| `security_posture` | `standard` (OWASP) \| `hardened` (+ CSP/HSTS/rate-limit) \| `paranoid` (+ air-gap, cert pinning, zero trust) | `standard` |
| `mcp_clients` | Which AI tools connect — e.g. `"Claude Desktop, Cursor, Grok Desktop"` | empty |
| `notes` | Free-form text — anything else the AI should know about how you work | empty |

Output goes to `~/.sassymcp/persona.md` (the persona module reads it on every session) and the run is recorded in `~/.sassymcp/config.json` (`setup_complete`, `setup_timestamp`, `setup_version`). Re-run `sassy_setup_wizard` any time to regenerate — the persona module hot-reloads with the new profile.

### Triggering the flow from your client

The onboarding hook fires on phrases like `"setup"`, `"first time"`, `"get started"`, `"onboard"`, `"new user"`, `"set up sassymcp"`. Anything close to those will pull the playbook into the AI's context. If you want to drive it manually, just call `sassy_setup_status` first to see where you are, then walk the table above.

## Smart Loading

By default, SassyMCP only loads frequently-used tool groups. This keeps tool definitions under 5% of your context window.

```bash
# Default: loads core, github_quick, persona, meta, utility, selfmod, setup, infrastructure
uv run sassymcp

# Load everything (270 tools, ~22K tokens of context)
SASSYMCP_LOAD_ALL=1 uv run sassymcp

# Load specific groups
SASSYMCP_GROUPS=core,github_quick,android,v020 uv run sassymcp
```

### Available Groups

| Group | Modules | Default |
|-------|---------|---------|
| `core` | fileops, shell, ui_automation, editor, audit, session | Yes |
| `meta` | meta | Yes |
| `infrastructure` | observability, state_manager, runtime_config | Yes |
| `github_quick` | github_quick (6 lean tools) | Yes |
| `persona` | persona | Yes |
| `utility` | utility | Yes |
| `selfmod` | selfmod | Yes |
| `setup` | setup_wizard, tools_manager | Yes |
| `memory` | memory | Yes |
| `updater` | updater | Yes |
| `combos` | combos (4 tools) | No |
| `prompts` | prompts (slash-menu shortcuts) | Yes |
| `github_full` | github_ops (80 tools) | No |
| `android` | adb, phone_screen | No |
| `system` | network_audit, process_manager, security_audit, registry, bluetooth, eventlog, clipboard | No |
| `v020` | vision, app_launcher, web_inspector, crosslink | No |
| `linux` | linux | No |

## Install

Pick whichever entry point matches how you already work. All four converge on the same shared brain at `~/.sassymcp/` — your persona, memory, license, and audit log are visible to every connected MCP client.

### One-click via DXT (Claude Desktop)

Download `sassymcp.dxt` from the [latest release](https://github.com/sassyconsultingllc/SassyMCP/releases/latest), double-click — Claude Desktop installs it. On first launch, sassymcp auto-detects every other MCP client on your machine (Cursor, VS Code Copilot, Windsurf, Continue, Cline, Zed, Grok Desktop) and patches each one's config so they all see SassyMCP without you editing any JSON.

### VS Code extension

Install **[SassyMCP](https://marketplace.visualstudio.com/items?itemName=sassyconsultingllc.sassymcp)** from the VS Code marketplace. The extension locates `sassymcp.exe` (PATH or the `sassymcp.exePath` setting), runs the same auto-config CLI, and adds a status bar item showing your license tier and brain health. Five commands cover Setup Wizard, Reinstall Configs, Open Audit Log, Open `_DELETE_` Folder, Show Brain Status.

### Manual auto-config CLI

If you have `sassymcp.exe` already (from the portable zip or pip install) and want to register it with every MCP client without per-client JSON editing:

```
sassymcp-install
```

That detects Claude Desktop, VS Code Copilot, Cursor, Windsurf, Continue, Cline, Zed, and Grok Desktop and patches each one's config atomically. Re-running is a noop. Take a look first with `sassymcp-install --dry-run`. Remove with `sassymcp-install --uninstall`. The CLI takes a timestamped backup of any existing config before its first edit.

### Portable bundle (manual config — older path)

**No installer required**, but you'll edit each client's JSON yourself unless you also run `sassymcp-install` after.

1. Download `sassymcp-v1.3.1-portable.zip` from the [latest release](https://github.com/sassyconsultingllc/SassyMCP/releases/latest) (~123 MB — includes `sassymcp.exe`, `adb`, `nmap`, `plink`, `scrcpy`, `tesseract`, `cloudflared`, and the `start-*.bat` launchers).
2. Extract anywhere — `D:\Tools\SassyMCP`, a thumb drive, your home folder, whatever.
3. Run `start-local.bat` (Claude Desktop), `start-lan.bat` (LAN HTTP), or `start-tunnel.bat` (Cloudflare Tunnel).

To uninstall: delete the folder. To upgrade: extract the new zip over the old folder, or to a new folder and delete the old one.

### Standalone executable (no tools bundled)

If you don't need the bundled `nmap` / `adb` / `cloudflared` (or you have them on PATH already), grab just `sassymcp.exe` (~35 MB) from the [latest release](https://github.com/sassyconsultingllc/SassyMCP/releases/latest). Drop it anywhere and point your MCP client at it.

**First-run wizard:** Double-click `sassymcp.exe` (or run it from a terminal with no flags) on a fresh machine and you'll get an interactive menu — auto-detect AI agents and register SassyMCP, activate a LemonSqueezy license key, generate / list bearer tokens, or start the HTTP server. Run `sassymcp.exe setup` anytime to re-open the menu. Once a persona is configured, bare invocation falls back to starting the HTTP server (the v1.5 behavior) so existing setups are unchanged.

### One-line PowerShell (license-gated)

For licensed users — pulls the standalone exe through the gated mirror:

```powershell
$key = "SASSY-XXXX-XXXX-XXXX"   # from https://sassyconsultingllc.com/pricing.html
$dst = "$env:LOCALAPPDATA\SassyMCP"
New-Item -Force -ItemType Directory $dst | Out-Null
Invoke-WebRequest "https://sassyconsultingllc.com/download/sassymcp/windows/sassymcp.exe?key=$key" -OutFile "$dst\sassymcp.exe"
Write-Host "Installed: $dst\sassymcp.exe"
```

### License-gated downloads

**[Get a license →](https://sassyconsultingllc.com/pricing.html)**. After checkout you receive a `SASSY-...` key; paste it into the download URL:

- `https://sassyconsultingllc.com/download/sassymcp/windows/sassymcp.exe?key=SASSY-...` — standalone exe, ~35 MB
- `https://sassyconsultingllc.com/download/sassymcp/windows/sassymcp-v1.3.1-portable.zip?key=SASSY-...` — full portable bundle, ~123 MB

No Python required for either.

### From source

```bash
git clone https://github.com/sassyconsultingllc/SassyMCP.git
cd SassyMCP
uv sync

# Optional dependencies:
uv pip install pytesseract playwright
playwright install chromium
```

### Cloudflare Tunnel (remote access)

Want to drive SassyMCP from a remote MCP client (Claude Web, another machine)? The portable bundle ships a turnkey launcher. Full step-by-step is in [**docs/TUNNEL.md**](docs/TUNNEL.md); the short version:

```powershell
winget install Cloudflare.cloudflared          # one-time
cloudflared tunnel login                       # authenticate against your CF account
cloudflared tunnel create sassymcp             # create a named tunnel
cloudflared tunnel route dns sassymcp mcp.<your-domain>.tld
# Write ~/.cloudflared/config.yml with the ingress block (see TUNNEL.md)

[Environment]::SetEnvironmentVariable("SASSYMCP_AUTH_TOKEN", "<your token>", "User")
[Environment]::SetEnvironmentVariable(
    "SASSYMCP_ALLOWED_HOSTS",
    "mcp.<your-domain>.tld,localhost,127.0.0.1", "User")

cd D:\Tools\SassyMCP                           # wherever you extracted
.\start-tunnel.bat sassymcp                    # tunnel name as arg, or set SASSYMCP_TUNNEL_NAME
```

`start-tunnel.bat` launches the HTTP bridge on `127.0.0.1:21001` and runs `cloudflared tunnel run <name>` in the foreground. Nothing in the script is vendor-specific — you supply the tunnel name and the hostname. Clients send `Authorization: Bearer <SASSYMCP_AUTH_TOKEN>` against `https://mcp.<your-domain>.tld/mcp`.

For hosted-Claude clients that require OAuth 2.1 DCR/PKCE instead of a static bearer, deploy the optional Worker under `sassymcp-oauth/` — copy `wrangler.toml.example` to `wrangler.toml`, fill in your hostname and KV id, and `wrangler deploy`. See `docs/TUNNEL.md` for the full OAuth section.

## MCP Client Config

SassyMCP speaks standard MCP. Anything that connects works — **no client-side modifications required**. The portable bundle ships ready-to-edit templates under `deploy/*_config.template.json`. Pick the row for your client, copy the template, replace `REPLACE_WITH_PATH` with the absolute path to `sassymcp.exe`, and save it where the client expects.

| Client | Transport | Config file location | Template |
|--------|-----------|----------------------|----------|
| **Claude Desktop** | stdio | `%APPDATA%\Claude\claude_desktop_config.json` (Win) / `~/Library/Application Support/Claude/claude_desktop_config.json` (mac) | `claude_desktop_config.template.json` |
| **Cursor** | stdio | `~/.cursor/mcp.json` (global) or `<project>/.cursor/mcp.json` (per-project) | `cursor_mcp_config.template.json` |
| **Windsurf** | stdio | `~/.codeium/windsurf/mcp_config.json` | `windsurf_mcp_config.template.json` |
| **Cline (VS Code)** | stdio | VS Code settings → `cline.mcpServers` (same `mcpServers` shape) | use `claude_desktop_config.template.json` |
| **Continue.dev** | stdio | `~/.continue/config.json` (merge under `experimental.modelContextProtocolServers`) | `continue_mcp_config.template.json` |
| **Grok Desktop** | HTTP | Grok Desktop MCP settings | `grok_desktop_config.template.json` |
| **Any other MCP client** | stdio or HTTP | client's MCP config | use the closest template; the `mcpServers` shape is conventional |

### Standard `mcpServers` shape (Claude Desktop, Cursor, Windsurf, Cline)

Using the exe:
```json
{
  "mcpServers": {
    "sassymcp": {
      "command": "C:\\path\\to\\sassymcp.exe",
      "env": {
        "SASSYMCP_LOAD_ALL": "1",
        "GITHUB_TOKEN": "ghp_your_token_here"
      }
    }
  }
}
```

From source:
```json
{
  "mcpServers": {
    "sassymcp": {
      "command": "uv",
      "args": ["--directory", "C:\\path\\to\\SassyMCP", "run", "sassymcp"],
      "env": {
        "SASSYMCP_LOAD_ALL": "1",
        "GITHUB_TOKEN": "ghp_your_token_here"
      }
    }
  }
}
```

### Continue.dev shape (different schema)

```json
{
  "experimental": {
    "modelContextProtocolServers": [
      {
        "transport": {
          "type": "stdio",
          "command": "C:\\path\\to\\sassymcp.exe"
        }
      }
    ]
  }
}
```

### HTTP / Grok Desktop / custom HTTP clients

Run the server in HTTP mode (`sassymcp.exe --http`, default `127.0.0.1:21001`) and point your client at `http://127.0.0.1:21001/mcp/`. Set `SASSYMCP_AUTH_TOKEN` if the bind is non-loopback.

```json
{
  "mcpServers": {
    "sassymcp": {
      "url": "http://127.0.0.1:21001/mcp/"
    }
  }
}
```

### What's actually Claude-flavored (cosmetic only)

A few docstrings and the legacy `.claude/skills/sassymcp-update.md` slash-command target Claude Code specifically. Other clients ignore them and use `sassy_update_*` tools directly. No tool, transport, or auth path requires Claude — the server doesn't know which LLM is on the other end.

## Transport Modes

| Mode | Command | Use Case |
|------|---------|----------|
| Stdio | `sassymcp.exe` | Claude Desktop, Cursor (direct pipe) |
| HTTP | `sassymcp.exe --http` | Grok Desktop, Windsurf (localhost:21001) |
| HTTP LAN | `sassymcp.exe --http --host 0.0.0.0` | Multi-device (requires auth token) |
| HTTPS | `sassymcp.exe --http --ssl` | Encrypted (auto-generates self-signed cert) |
| SSE | `sassymcp.exe --http --sse` | Legacy transport |

## Running Multiple Instances (Dual Session)

Two SassyMCP processes on the same machine — for example, a **local stdio** instance for Claude Desktop and a **remote HTTPS** instance behind a Cloudflare Tunnel for Claude Web — can clobber each other's state if they share `~/.sassymcp/`. The fix is one env var per instance.

### Conflicts to resolve per-instance

| Resource | Default | How to give each instance its own |
|----------|---------|------------------------------------|
| HTTP port | `21001` | `--port 21002` (HTTP-mode instances only) |
| Crosslink HTTP port | `9377` | `sassy_crosslink_register port=9378` |
| Auth token | env `SASSYMCP_AUTH_TOKEN` | Set per-process in launcher's env |
| Per-user state dir | `~/.sassymcp` | **`SASSYMCP_HOME=/path/to/dir`** ← the new env var (v1.3.4+) |
| SSL cert/key | `$SASSYMCP_HOME/server.{crt,key}` | Auto-isolated when `SASSYMCP_HOME` is set; or `--ssl-cert` / `--ssl-key` |

### Example: local stdio + remote tunnel side-by-side

**Instance A — local stdio for Claude Desktop** (claude_desktop_config.json):

```json
{
  "mcpServers": {
    "sassymcp-local": {
      "command": "C:\\Tools\\SassyMCP\\sassymcp.exe",
      "env": {
        "SASSYMCP_LOAD_ALL": "1",
        "SASSYMCP_HOME": "C:\\Users\\<you>\\.sassymcp-local"
      }
    }
  }
}
```

**Instance B — remote HTTPS via Cloudflare Tunnel** (`start-tunnel.bat` + a wrapper that sets the env):

```batch
set SASSYMCP_HOME=C:\Users\<you>\.sassymcp-remote
set SASSYMCP_AUTH_TOKEN=<token-for-remote>
set PORT=21002
"%~dp0sassymcp.exe" --http --host 127.0.0.1 --port %PORT%
```

Then `cloudflared tunnel run <name>` forwards `https://<your-tunnel>/mcp` to `127.0.0.1:21002`.

### What's isolated when SASSYMCP_HOME differs

Each instance gets its own:

- `persona.md` — different profiles per session
- `config.json` — different runtime config (allowed dirs, blocked commands, etc.)
- `tokens.json` — different scoped auth tokens
- `license.json` — separate license activation
- `audit.log` / `audit.jsonl` — no interleaved writes
- `crosslink.db` — separate cross-session message queues
- `memory.db` — separate persistent memories
- `tool_state.db` / `tool_usage.json` — separate per-tool state and usage analytics
- `server.crt` / `server.key` — separate self-signed certs
- The `_security` protected-paths check honors `SASSYMCP_HOME` too — neither instance can `sassy_safe_delete` into the other's home

### What's still shared between instances

- The repo source tree (always protected from delete/overwrite by `_security`)
- `%LOCALAPPDATA%\SassyMCP\updates\` — the updater download stage (harmless; tagged by version under it)
- The bundled tools in the portable zip (`adb`, `nmap`, `cloudflared`, etc.) — read-only from both instances

### Putting it in the OS (so both start at boot)

The legacy `personal/autostart-bridge.bat` + `personal/register-autostart.ps1` template is gitignored — copy it, tweak the paths and the `SASSYMCP_HOME` for each instance, then `Register-ScheduledTask` once per instance.

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `SASSYMCP_LOAD_ALL=1` | Load all 270 tools |
| `SASSYMCP_GROUPS=core,android` | Load specific groups |
| `SASSYMCP_AUTH_TOKEN=xxx` | Bearer token for HTTP auth |
| `SASSYMCP_DEV=1` | Enable live reload (dev mode) |
| `SASSYMCP_NO_UPDATE_CHECK=1` | Disable the startup update check (no GitHub API call) |
| `SASSYMCP_HOME=/path/to/dir` | Override the per-user state dir (default `~/.sassymcp`). Required when running multiple instances on one machine. |
| `SASSYMCP_REPO=/path/to/repo` | Override the auto-detected repo root in `tools/mercury_audit_sassymcp.py` (dev tool only) |
| `GITHUB_TOKEN=xxx` | GitHub API access |
| `SSH_HOST=xxx` | Remote Linux hostname/IP |
| `SSH_USER=xxx` | Remote Linux username |
| `SSH_PASS=xxx` | Remote Linux password |

## External Tools

All bundled in the [beta zip package](https://github.com/sassyconsultingllc/SassyMCP/releases). Install separately only if using the standalone exe.

| Tool | Used By | Bundled | Install (if needed) |
|------|---------|---------|---------------------|
| ADB | All `sassy_adb_*` + `sassy_phone_*` tools | Yes | [Android Platform Tools](https://developer.android.com/tools/releases/platform-tools) |
| nmap | `sassy_port_scan` | Yes | [nmap.org](https://nmap.org/download.html) |
| plink | `sassy_linux_exec` | Yes | [PuTTY](https://www.chiark.greenend.org.uk/~sgtatham/putty/latest.html) |
| scrcpy | `sassy_scrcpy_*` tools | Yes | [scrcpy releases](https://github.com/Genymobile/scrcpy/releases) |
| Tesseract | `sassy_screen_ocr`, `sassy_find_text_on_screen` | Yes | [tesseract-ocr](https://github.com/tesseract-ocr/tesseract) |
| Chrome | `sassy_url_screenshot` | No | [google.com/chrome](https://www.google.com/chrome/) |

Run `sassy_setup_check_tools` to verify all tools are detected.

## Requirements

- Windows 10/11
- Python 3.11+ (only if running from source; exe is self-contained)

## License

MIT License - Sassy Consulting LLC
