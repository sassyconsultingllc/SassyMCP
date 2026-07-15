<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
   CodeMark: SCLLC1-SassyMCP-4TMBKYZGMQTB
-->
# Changelog

All notable changes to SassyMCP. Newest first. Versions follow semver:
`MAJOR.MINOR.PATCH` — MAJOR for breaking config / API changes, MINOR
for new tier-visible features, PATCH for fixes that don't move buyer-
facing surfaces.

## [1.13.0] — 2026-07-15 — All or nothing: tier gating removed, everything unlocked

The pro/forensics gate locked 5 tool groups (`github_full`, `android`,
`v020`, `linux`, `system`) plus `forensics` behind a license that could
not actually be purchased (checkout unwired, entitlement map empty). That
subtracted value from every user and produced revenue from none. The gate
is gone: the release model is now all-or-nothing — every tool group ships
unlocked, for everyone, with no key.

### Changed

- **`get_allowed_groups()` returns every known group unconditionally.**
  License state (missing, expired, tampered, corrupt, revoked) affects
  only the displayed tier label, never which groups load. The function
  survives as the single reintroduction point should a real buy→own loop
  ever ship.
- **`_resolve_modules()` no longer consults the license.** `SASSYMCP_GROUPS`
  loads any known group; the "requires Pro license — skipped" path is gone.
- **Licenses are now supporter keys.** `sassy_setup_license` still
  activates/validates/deactivates against LemonSqueezy and the tier label
  still shows in the startup banner, control panel, CLI wizard, and VS Code
  cockpit — as supporter recognition. Refunds still revoke the label via
  the weekly LS check and the fast billing-oracle check.
- **`SASSYMCP_LICENSE_BYPASS` is accepted and ignored** — there is nothing
  left to bypass. Old dev/CI environments that set it keep working.

### Removed

- `FREE_GROUPS` / `PRO_ONLY_GROUPS` / `ADDON_GROUPS` / `TIER_GROUPS` /
  `ALWAYS_ALLOWED` from `sassymcp.license` (nothing outside the module
  imported them). Tier vocabulary lives on as informational
  `KNOWN_TIERS` / `KNOWN_ADDONS`.
- The "BYPASS" startup-banner label and the upsell block in
  `sassy_setup_license action=status` (replaced by a supporter note).

### Tests

- `test_license_gating.py` rewritten to pin the new contract: every
  license state → all groups, exactly; failure modes never crash; the
  supporter-label machinery (generate/parse/validate) still round-trips.
  LemonSqueezy activation tests unchanged and passing.

## [1.12.0] — 2026-07-15 — The board becomes truthful: server-side auto-record + every continuity plane visible

The cockpit's feasibility problem was that its data plane was voluntary: an
LLM had to *choose* to call `sassy_peer_announce`, so the board showed
memories of demos. This release moves the recording into the server itself
and widens the board from one plane (crosslink) to all of them.

### Added

- **Server-side client auto-record.** The audit wrapper reads the MCP
  `initialize` handshake's `clientInfo` from the request context and upserts
  the calling client as a peer on every tool call (throttled to one SQLite
  touch per 15s per client). Claude Desktop, Cursor, Windsurf — any client
  that does work appears on the board as `client-<name>`, alive, with zero
  LLM cooperation. Clean shutdown flips this process's auto-recorded peers
  offline immediately (atexit backdates their session rows).
- **Brain board carries every continuity plane.** `board_snapshot()` (and so
  `sassy_coordination_board`, `sassymcp mesh board`, and the cockpit poll)
  now returns, alongside peers/channels/handoffs/sessions:
  - `memory` — entry/milestone counts, recent keys, active `task_*_state` /
    `task-active` / `blocker_*` rows with value snippets, recent milestones
    (read directly from memory.db; works even when the memory tool group
    isn't loaded).
  - `recent_calls` — tail of audit.jsonl (last 128KB, newest first): the
    MCP's live pulse.
  - `hooks` — every registered playbook with triggers and active state,
    continuity playbooks (session_startup, session_handoff, coordination,
    crosslink) sorted first. Planes degrade to an error marker; the core
    board never breaks.
- **Continuity playbooks always active.** The server activates
  session_startup / session_handoff / coordination / crosslink hooks at
  boot, so `sassy_hooks_list` shows any connecting agent the startup and
  handoff protocol without the agent knowing to activate anything.
- **Cockpit webview**: Memory card (active task state + milestones),
  Continuity playbooks card (Coordination tab), and a Live activity feed
  (Dashboard tab) rendering the audit tail.
- **Control Panel "Brain" tab**: agents-on-the-mesh, memory stats,
  milestones, and live tool calls as generic-renderer views
  (`sassy_peer_list`, `sassy_memory_stats`, `sassy_memory_milestones` join
  the read-only cockpit allowlist).

### Changed

- **Announce heartbeats no longer flood the channel.** `sassy_peer_announce`
  refreshes the sessions row on every call, but posts a peer-announce
  MESSAGE only when the peer is new, its identity/capabilities changed, or
  the newest announce went stale. Liveness now reads the sessions row
  (which heartbeats and auto-record refresh) instead of message age, so
  channel counts mean something again.

## [1.11.0] — 2026-07-14 — Sassy Brain cockpit: the tabbed coordination UI

The `feat/sassy-brain-cockpit` branch (all four phases of the 2026-06-06
design spec) lands on main. One feature, three surfaces, one data plane.

### Added

- **Sassy Brain Cockpit (VS Code).** `SassyMCP: Open Sassy Brain Cockpit`
  (also a click on the status-bar item) opens a branded React webview with
  three tabs:
  - **Coordination** — the hero: live peer board (Claude / Cursor / Hermes /
    remote nodes), crosslink channels with message counts, handoff timeline,
    Start-Hermes button, Android tile.
  - **Dashboard** — brain status: license tier, memory/audit/persona
    counts, per-group tool availability, version.
  - **Actions** — card launcher with fuzzy filter (announce, observe/mirror
    phone, wizard, audit log, brain folder).
  Built from `sassymcp-vscode/webview/` (Vite + React, self-contained IIFE —
  no CDN, CSP-nonced; `media/cockpit/` is gitignored build output). The
  webview never touches the filesystem: it posts intents to the extension
  host, which talks to the already-running server.
- **Coordination module** (`sassy_peer_announce`, `sassy_peer_list`,
  `sassy_peer_delegate`, `sassy_coordination_board`) — multi-AI peer mesh
  riding the existing `crosslink.db`; joins the `v020` (Pro) group. 274
  tools across 36 modules.
- **Standalone desktop app** (`python -m sassymcp.desktop`) — the same
  cockpit outside VS Code (pywebview host + JS bridge).
- **Overlay quick-launcher** (`python -m sassymcp.overlay`) — tray icon +
  global hotkey for reach when no editor is open.
- **`sassymcp mesh` subcommand** — `board | brain | phone | peers |
  announce | delegate` as one JSON line, for external UIs shelling out to a
  bundled exe.

### Fixed

- `sassymcp install` skill deployment strips the repo's leading CodeMark/CMI
  HTML comment, so deployed SKILL.md / rules files start at the YAML
  frontmatter or H1 as each client requires.

## [1.10.2] — 2026-06-30 — Control Panel cockpit: read-only tool visualizers

### Added

- **Full-stack visibility in the server-served Control Panel.** Five new tabs
  turn the operational tools an LLM rarely calls into a live dashboard:
  - **Server** — health, live metrics, tool usage/stats, recent tool calls
    (visible on every tier, no license needed).
  - **Network** — `sassy_netstat`, `sassy_open_ports`, `sassy_arp_table`.
  - **Processes** — `sassy_system_info`, `sassy_processes`, `sassy_autorun_entries`.
  - **Security** — `sassy_defender_status`, `sassy_firewall_status`, `sassy_eventlog`.
  - **Screen** — `sassy_screen_glance` (image), `sassy_list_windows`, `sassy_screen_ocr`.
- **Generic result renderer.** One endpoint runs a view's tool and the backend
  auto-detects how to draw it — table (process list), image (screenshot),
  key/value card (system info), or text (netstat) — so new read-only tools are a
  one-line `_COCKPIT_VIEWS` entry with no bespoke UI.
- The catalog reports per-view availability, so tools gated behind the
  `system` / `v020` / `forensics` groups show "not in this tier" instead of
  failing when they aren't loaded.

### Security

- The cockpit can invoke only a hard allowlist of **read-only** tools
  (`_COCKPIT_TOOLS`). There is no code path from a panel request to `sassy_shell`,
  fileops writes, selfmod, or any mutating tool. Calls reuse the registered
  tools, so each one is still recorded in the audit trail.

## [1.10.1] — 2026-06-25 — Fix: concurrent clients no longer wedge each other

### Fixed

- **Two MCP clients calling tools at the same time no longer freeze each
  other.** Claude Desktop multiplexes every chat onto a *single* stdio server
  process (one asyncio event loop) — it does not spawn a process per chat. Yet
  nearly every tool body did synchronous blocking work (SQLite, file I/O,
  screenshots/OCR) directly on that loop. With one chat this was invisible
  (calls are sequential anyway); with two, any in-flight blocking call starved
  the loop and froze the other chat until it timed out.
  - The audit wrapper now runs synchronous tools via `asyncio.to_thread`, and
    `_wrap_all_tools` forces `tool.is_async = True` so FastMCP awaits the
    (always-async) wrapper even for `def`-declared tools. Net effect: **a
    blocking tool written as a plain `def` is automatically offloaded to a
    worker thread and can never block the event loop.**
  - Converted the confirmed loop-blockers to plain `def`: `state_manager`,
    `memory`, `fileops`, `vision` (single-shot capture/OCR), `crosslink`,
    `editor`, `audit`. SQLite-backed modules (`state_manager`, `memory`) now
    open a fresh per-call connection (`with closing(open_db(...))`) instead of
    sharing one connection, which is required once calls run on worker threads.
  - Registry / eventlog / web_inspector were already non-blocking (they offload
    via `create_subprocess_exec` / `httpx.AsyncClient`) and were left as-is.

## [1.10.0] — 2026-06-25 — Cross-platform (macOS / Linux) + Control Panel + tool discovery

### Added — Cross-platform support (one source, routed at the head)

- SassyMCP now runs natively on macOS and Linux as well as Windows, from the
  *same* source (no per-OS forks). A new routing head, `sassymcp._platform`,
  resolves the host OS once at import and hands every module the correct
  command for that host. The workhorse is
  `_platform.pick(windows=…, macos=…, linux=…)`, which keeps each command's
  platform variants together at the call site, plus named helpers
  (`default_shell`, `clipboard_get/set_argv`, `adb_candidates`,
  `open_path_argv`, …) for the routings that recur.
  - **Shell** (`sassy_shell`) — adds `bash`/`zsh`/`sh`; the default shell is
    now the host's native shell (PowerShell on Windows, the login shell on
    macOS/Linux). PowerShell `&&`→`;` normalization runs only for PowerShell;
    POSIX shells run verbatim.
  - **System info** — clipboard (`pbcopy`/`pbpaste`), event log
    (`log show` / `journalctl`), firewall (`socketfilterfw` / `ufw`),
    endpoint protection (Gatekeeper + SIP + XProtect in place of Defender),
    Wi-Fi scan (`airport`/`system_profiler` / `nmcli`), ACLs (`ls -le` /
    `getfacl`), Bluetooth (`blueutil`/`system_profiler` / `bluetoothctl`),
    and notifications (`osascript` / `notify-send`) all route per host.
  - **Window control** (`sassy_focus_window`, `sassy_resize_window`,
    `sassy_snap_window`, `sassy_close_window`, `sassy_list_windows`,
    `sassy_desktop_state`) — macOS uses AppleScript via System Events (needs
    Accessibility permission); Linux is best-effort via wmctrl/xdotool.
    Multi-monitor + DPI via AppKit (NSScreen) on macOS.
  - **Port scan** (`sassy_port_scan`) — the PowerShell fallback is replaced by
    a portable async TCP scanner that runs on every OS when nmap is absent.
  - **Remote SSH** (`sassy_linux_exec`) — native OpenSSH `ssh` on macOS/Linux
    (with `sshpass`/agent/key support) alongside PuTTY `plink` on Windows.
  - **Tooling** (`sassy_setup_tools`) — installs via the host package manager
    (winget / Homebrew / apt) and resolves adb/scrcpy/tesseract/nmap/ssh from
    host-appropriate locations.
  - **Hardening** — the sensitive-read denylist now covers macOS Keychain and
    browser-credential stores.
  - **Build/packaging** — `sassymcp.spec` is platform-aware (pyobjc on macOS,
    pywinauto excluded off-Windows); `pywinauto` is a Windows-only dependency
    and pyobjc macOS-only (environment markers); `build.sh` mirrors
    `build.bat` for macOS/Linux. PyInstaller can't cross-compile — build each
    artifact on its own OS.

### Added — Control Panel (loopback web UI)

- **SassyMCP Control Panel** (`sassymcp.control_panel`) — a localhost-only,
  token-gated web UI served from its own daemon-thread HTTP server,
  independent of the MCP transport (works under stdio and HTTP alike).
  Three panes:
  - **Event log** — tails `audit.jsonl` (tool calls, intercepts, policy
    decisions), with optional 5s auto-refresh.
  - **Settings** — permission mode, sandbox roots, legacy
    `interceptor.destructiveAction`, live tier display.
  - **Classifiers & rules** — read-only view of the built-in destructive
    classifiers (delete keywords, catastrophic always-blocks, tiered regex
    patterns) plus an editor for the allow/ask/deny rules layer.
- **`sassy_panel` tool** — `status` / `start` / `stop` / `url`. Opt-in:
  the panel starts at server boot only when `panel.enabled` is set (or
  `SASSYMCP_PANEL=1`), so stdio installs never open a port unasked. Binds
  `127.0.0.1` and requires the per-install token in
  `~/.sassymcp/control_panel.token`.

### Added — tool discovery (see docs/releases/v1.10.0.md)

- `sassy_self_check` (manifest vs live-registry reconciliation, BROKEN-module
  detection) and `sassy_tool_catalog` (live name/purpose/group map), plus
  frozen-safe selfmod stubs and the `/sassymcp:discover` prompt.

## [1.9.0] — 2026-06-23 — Permission engine (modes + sandbox jail + rules)

### Added

- **Permission policy engine (`sassymcp.policy`)** — one `evaluate()` is now
  the single decision point for "may this operation proceed?", folding the
  destructive-command classifier, the protected-path guard, and runtime
  config into four modes:
  - `strict` — block destructive patterns everywhere (the prior default)
  - `confirm` — destructive patterns return a confirm token
  - `sandbox` — relaxed gating *inside* the configured project roots; any
    path resolving *outside* the jail is refused (incl. `..`/symlink escapes).
    The "run an ungated LLM, but confined to the project folder" mode.
  - `bypass` — allow everything except protected paths (explicit, audited)
  - A Claude-style allow/ask/deny **rules layer** (tool-glob + path-glob +
    command-regex; first match wins) overrides the mode default.
  - The catastrophic block-list (format/mkfs/etc.) and the protected-path
    invariant (the SassyMCP source tree + `~/.sassymcp`) hold in **every**
    mode, including bypass.
- **`sassy_permission` tool** — view/set the mode, manage sandbox roots, and
  add/clear rules from chat (`status` / `set_mode` / `add_root` /
  `remove_root` / `add_rule` / `clear_rules`).
- Back-compat: with `permission.mode` unset, the mode is derived from the
  existing `interceptor.destructiveAction`, so installs are byte-for-byte
  unchanged until they opt in.

### Fixed

- **Shell interceptor treated piped/compound deletes' command names as files.**
  `_parse_delete_targets` ignored pipeline/statement boundaries, so
  `Get-ChildItem … | Remove-Item`, `dir | del`, and `cd x; rm y` scooped up
  the command name, operators (`|`, `&&`), and script-block fragments as
  "files to move to `_DELETE_`" — and sometimes moved the wrong files. Now
  only a bare leading delete invocation auto-stages (parsing targets from its
  own statement); embedded deletes route to the normal block/confirm path.

### Build

- `build-mcpb.ps1` now emits a `.dxt` copy alongside the `.mcpb` (identical
  format) so older Claude Desktop builds — and any client still keyed to the
  old extension — can install the same bundle. Attach both to releases.

## [1.8.1] — 2026-06-18 — Dependency maintenance

### Maintenance

- **Cleared all open Dependabot alerts** — bumps applied directly on `main`
  (the PR branches had diverged from the 1.8.0 tree), rebuilt, full suite
  green:
  - Python (`uv.lock`): `cryptography` 48.0.0 → 49.0.0, `starlette`
    1.0.1 → 1.3.1, `python-multipart` 0.0.29 → 0.0.32, `pyjwt`
    2.12.1 → 2.13.0.
  - VS Code extension (build/dev deps): `@vscode/vsce` 2.27 → 3.9.2,
    `markdown-it` 14.2.0, `undici` → 7.28.0, `form-data` → 4.0.6. Two
    high-severity advisories cleared; `npm audit` reports 0 vulnerabilities.
- Rebuilt `sassymcp.exe` / `.mcpb` against the updated dependency tree;
  frozen-exe smoke check (supervise + stdio init) passes.

## [1.8.0] — 2026-06-18 — Process supervisor (`sassymcp supervise`)

### Added

- **`sassymcp supervise` — an orphan-proof, self-healing process manager**
  for the runtime tree. It runs the HTTP bridge (and, optionally, the
  cloudflared tunnel) as managed children, health-checks them, and
  restarts crashed/hung children with exponential backoff. Subcommands:
  `start` / `stop` / `status` / `restart <role>`.
  - **Orphan-proof teardown** (`sassymcp/_jobctl.py`): on Windows a Job
    Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (via `ctypes`/
    `kernel32` — no `pywin32`, so it works in the frozen exe); on POSIX a
    new session per child plus `killpg`, and `PR_SET_PDEATHSIG` on Linux.
    A hard `kill -9` / `TerminateProcess` of the supervisor leaves **zero**
    surviving children — no orphaned bridge holding a wedged SQLite/WAL
    lock.
  - **Readiness health check** POSTs to the bridge's `/mcp` and recycles a
    *hung-but-alive* bridge — the failure Windows Task Scheduler can never
    catch.
  - **Crash-survivable control surface**: a single-instance pidfile, an
    on-disk child registry (`supervisor-children.json`), and a file
    command channel under `$SASSYMCP_HOME` — `status` works even when the
    bridge is dead, so an operator (or agent) can recover a wedged system.
- **`SASSYMCP_SUPERVISED=1`**: when the bridge runs under the supervisor,
  `sassy_selfmod_restart` now exits and lets the supervisor respawn it,
  instead of detaching its own successor (which would race for the port).
- **Test hygiene** (`tests/conftest.py`): an autouse fixture reaps any
  child process a test leaks (scoped to pytest's own descendants), so a
  failed test can't wedge a later one's DB lock.

### Notes

- `start-supervised.bat` is the recommended launcher; the legacy
  `taskkill /f` port-kill in `start-tunnel.bat` was the source of wedged
  locks. stdio mode is intentionally not supervised (the MCP client owns
  that pipe).

## [1.7.2] — 2026-06-17 — `.mcpb` bundle + first built release

### Changed

- **Desktop bundle migrated from `.dxt` to `.mcpb`** — Anthropic's MCP
  Bundle format. `manifest.json` now declares `manifest_version: "0.2"`
  (the deprecated `dxt_version` field is removed), and the bundle is
  packed and validated with the official `@anthropic-ai/mcpb` CLI. The
  staging dir was renamed `dxt/` → `mcpb/` and `scripts/build-dxt.ps1`
  → `scripts/build-mcpb.ps1`.
- **Rebuilt `sassymcp.exe` from current `main`**, so the distributed
  binary now contains the v1.7.1 `install --client auto`/`all` fix.
  (v1.7.1 was a source-only tag with no published artifact; v1.7.2 is
  the first built release of the 1.7.x line.)

### Verification

- Frozen-exe QC against the rebuilt binary: `sassymcp.exe install
  --client auto --dry-run` exits 0 and lists clients; `--client bogus`
  exits 2 with the valid-name hint; the stdio `initialize` handshake
  returns `serverInfo`; `--http` boot answers `/mcp` with 200. Full
  pytest suite green. `mcpb validate` + `mcpb info` pass on the bundle.

## [1.7.1] — 2026-06-15 — Fix: `install --client auto` crash

### Fixed

- **`sassymcp install --client auto` (and `--client all`) no longer exits 2
  with "Unknown client: 'auto'".** `auto`/`all` are now recognized as
  sentinels meaning "every detected client" — identical to omitting
  `--client`. The old code treated the entire `--client` value as a single
  short_name to filter on, so the advertised `install --client auto` and the
  bundled exe's TTY wizard quick-install (which passes `auto`) both crashed
  before writing any client config. `patch_client()` already skips
  undetected clients, so fanning out over all of them is safe.
- The DXT first-run hook was never affected — it uses `--auto-other` — so
  drag-drop installs into Claude Desktop kept working. The crash only hit
  users who ran the documented command or the exe's interactive menu.
- Unknown `--client` values now print the valid short_names plus the
  `auto`/`all` sentinels instead of a bare "Unknown client".

## [1.7.0] — 2026-06-15 — Perpetual licensing + core refocus

A focused core release. The product surface is the MCP server itself —
the frozen `sassymcp.exe`, the `.dxt` bundle, the Python modules, and the
launcher `.bat` files. Experimental "Sassy Brain" cockpit/desktop work
stays out of this repo's release line.

### Changed

- **Billing pivots from subscription to one-time perpetual license.**
  LemonSqueezy is now configured for a perpetual buy-once entitlement
  instead of a recurring subscription. Activation, validation, and the
  fast revocation oracle are unchanged; only the purchase model moved.
- **LS post-purchase automation** (`tools/ls-setup`) for variant +
  webhook provisioning after the dashboard is configured.

### Removed

- **Multi-AI coordination mesh.** `sassy_peer_announce`,
  `sassy_peer_list`, `sassy_peer_delegate`, and `sassy_coordination_board`
  (the `coordination` module), the `_brain_status` / `_phone_status`
  snapshot helpers, and the `sassymcp mesh` CLI subcommand are removed.
  These were scaffolding for the separate Sassy Brain cockpit and do not
  belong in the core server. The `v020` tool group keeps vision, app
  launcher, web inspector, and crosslink.

### Maintenance

- Dependency bumps closing Dependabot alerts: `starlette` 0.52.1 → 1.0.1,
  plus dev-dependency bumps (`qs`, `tmp`) in the VS Code extension.

## [1.6.0] — 2026-05-20 — Monetization-ready

The big one: SassyMCP is now sold through LemonSqueezy with real
tier-based gating, online revocation, and a buyer-side activation flow.
Free tier still runs out of the box with no key required — just gets
fewer groups.

### Added

- **Tier enforcement** (`sassymcp/license.py`). `get_allowed_groups()`
  now consults the active license and returns only what the buyer has
  paid for. Three sets:
  - `FREE_GROUPS` — core, meta, github_quick, persona, setup,
    infrastructure, utility, selfmod, memory, updater, prompts, combos.
  - `PRO_ONLY_GROUPS` — github_full, android, v020, linux, system.
  - `ADDON_GROUPS["forensics"]` — security_audit, registry (a new
    `forensics` group carved out of the old `system` group).
  - All failure modes (missing / corrupt / tampered / expired) silently
    downgrade to free. The product never bricks.
- **`SASSYMCP_LICENSE_BYPASS=1`** dev escape hatch. Logged at WARNING.
- **LemonSqueezy integration** (`sassymcp/_lemonsqueezy.py`):
  - `activate()`, `validate()`, `deactivate()` against LS's License API.
  - Variant-id → entitlement mapping with env override
    `SASSYMCP_LS_VARIANT_MAP` for staging.
  - `quick_revocation_check()` against SassyMCP's billing Worker.
- **`activate_via_lemonsqueezy()`** + **`deactivate_via_lemonsqueezy()`**
  in `license.py` — full activation flow, mints internal HMAC payload
  from LS response so offline use keeps working, defers deactivation
  on network errors so buyers don't burn seats.
- **Fast revocation oracle** (`sassymcp-billing/` Cloudflare Worker).
  Verifies LS `X-Signature` HMAC, classifies events, stores revocations
  in KV keyed by `sha256(license_key)` so raw keys never land in KV or
  logs. Public `/lemonsqueezy/check/:hash` is edge-cached 60s. Cuts
  refund-to-revocation latency from ~7 days to seconds.
- **Startup fast-check + weekly authoritative re-validate** scheduled
  non-blocking by `server.py`. Fast oracle short-circuits the LS round-
  trip when it already has a revocation entry.
- **`sassy_setup_license validate`** MCP action for on-demand re-check.
- **License `addons` field** for stacking add-ons additively on top of
  the base tier (`{tier: pro, addons: ["forensics"]}`).
- **Server startup log** now shows actual tier label and resolved
  allowed-groups set instead of `list(TOOL_GROUPS.keys())`.
- **Billing Worker** scaffold: `wrangler.toml.example`, `.gitignore`,
  `package.json`, `src/index.js`.
- **CLI**: new `sassymcp setup` subcommand opens the interactive wizard
  alongside `generate-token`, `show-token`, `install`.
- **TTY wizard**: double-clicking `sassymcp.exe` (or running it from a
  terminal with no other flags) now opens a menu instead of starting an
  HTTP server with no UI. Menu covers quick install, license activation,
  token management, and explicit run-as-server.
- **32 new tests** covering tier gating, LS activation, weekly check,
  and the fast revocation oracle.

### Changed

- `weekly_validation_check()` now routes LS-issued keys through LS, and
  consults the billing Worker first as a fast pre-check. Network errors
  at either layer leave the local license alone.
- `sassy_setup_license` rewired for LS — `activate` calls
  `activate_via_lemonsqueezy()`, `deactivate` calls
  `deactivate_via_lemonsqueezy()`. The legacy HMAC-only `save_license`
  path remains for self-signed dev keys.
- `_tool_loader.py`: `system` group split. Forensics modules
  (`security_audit`, `registry`) now live in their own `forensics`
  group so the add-on can gate them independently.
- DXT manifest bakes `SASSYMCP_BILLING_BASE=https://billing.sassyconsultingllc.com`
  into the spawned process env so the fast revocation oracle is
  reachable from a stock install.

### Buyer-visible behavior

| License state | What loads |
|---|---|
| No key | Free baseline only (~12 groups, ~30 tools) |
| Valid pro | Free + pro groups (~17 groups, ~140 tools) |
| Forensics add-on | + `security_audit`, `registry` modules |
| Expired / tampered / corrupt | Silently downgrades to free baseline |

### Commercial model

One-time perpetual license per machine, sold through LemonSqueezy. No
recurring subscription. The buyer pays once, receives a key, activates
on the machines they own (up to the per-license seat cap configured on
the LS variant). Refunds revoke via the `order_refunded` webhook;
license deactivation by the buyer (LS dashboard self-serve) revokes via
`license_key_updated`. The billing Worker subscribes to exactly four
events: `order_created`, `order_refunded`, `license_key_created`,
`license_key_updated`.

## [1.5.0] — 2026-05 — Frozen-exe completeness

- Frozen `sassymcp.exe` via PyInstaller, lean build, generalized for
  sale (Mercury-2 security audit pass).
- Multi-client auto-config (`sassymcp install --client auto`) detects
  Claude Desktop, Claude Code, Cursor, Cline, Continue, Windsurf, Zed,
  VS Code Copilot, Grok Desktop.
- DXT bundle ships as a drag-drop install for Claude Desktop.

## [1.4.2] — Earlier — sassy_shell timeout auto-promote

- `sassy_shell` MCP-safe timeout auto-promote: long-running commands
  automatically promoted to background sessions so the MCP client
  doesn't time-out the call.

## [1.4.1] — Earlier — Marketplace readiness

- CI: opt-in node24 build target.
- Marketplace-ready packaging touches.

## Older

Pre-1.4 history lives in `git log`. SassyMCP was developed iteratively
without a written changelog until v1.4.1; this file starts the record
going forward.
