# Sassy Brain Cockpit — Design Spec

**Date:** 2026-06-06
**Branch:** `feat/sassy-brain-cockpit`
**Status:** shipped — all four phases merged to main (v1.11.0, 2026-07-14)

## Why

CursorTouch (Windows-MCP et al., ~7,100 GitHub stars, "2M+ users via Claude Desktop
Extensions") is the breakout in the "AI controls your computer" MCP category and is
teasing a unified hosted app. SassyMCP already matches or exceeds them on capability —
desktop **and** Android in one server, crosslink multi-AI coordination + memory/handoff,
241+ tools, and a real security posture (audit log, shell confirmation, safe-delete
staging) that CursorTouch's own README disclaims ("NO sandbox or isolation layer", full
user privileges). What SassyMCP lacks is **UX polish, a sellable surface, and viewership**.

Decision (SaS): don't build a from-scratch terminal CLI. Fold the new UX into the
**SassyMCP VS Code extension ("Sassy Brain")** — a polished, distinct, sellable UI that
lives where developers already work. Lead with the one thing CursorTouch can't match:
**cross-device + multi-AI coordination.**

## Locked choices

- **Webview stack:** Vite + React + `@vscode/webview-ui-toolkit`.
- **v1 hero:** the **Coordination view** (peers, crosslink channels, handoff timeline,
  Start-Hermes, Android tile).
- **Reach:** VS Code panel + command palette **and** a standalone tray + global-hotkey
  desktop overlay (so it works when VS Code is closed).
- **Platforms:** Windows + Android now. **iOS is out of scope** — it requires
  WebDriverAgent on macOS (a separate macOS SassyMCP build). The coordination layer is
  designed so a future macOS/iOS node plugs into the same crosslink channels. Do not
  claim Apple in marketing yet.

## Current state (what exists today)

- `sassymcp-vscode/` v1.6.0 — installer + status bar + ONE webview (Setup Wizard, plain
  HTML strings, no build step). `brain.ts` is a read-only view of `~/.sassymcp/`. Commands:
  runSetupWizard, reinstallConfigs, openAuditLog, openDeleteFolder, showBrainStatus.
- `sassymcp/modules/crosslink.py` — SQLite (`~/.sassymcp/crosslink.db`) + stdlib HTTP bus
  on port 9377. Tables `messages`, `sessions`. Helpers `_post_message`, `_read_messages`,
  `_register_session`, `_list_sessions`, `_ensure_db`. 7 MCP tools. Pro-tier (`v020` group).
- `hermes_node.py` — proven multi-AI pattern: a local Ollama peer alternates turns with
  Claude on a `joint` channel over the shared DB, security gate enforced.
- `sassymcp/_paths.py` — all state under `SASSYMCP_HOME` (default `~/.sassymcp/`).
- Tier gating: `license.py` `PRO_ONLY_GROUPS` includes `v020`; `server.py:_resolve_modules`
  loads group modules intersected with `get_allowed_groups()`.

## Architecture

```
VS Code (React webview)  ──postMessage──>  extension host (TS)  ──HTTP+bearer──>  SassyMCP server
Standalone overlay (tray+hotkey)           reads config.json/tokens.json         (MCP HTTP, existing port)
        │                                                                                │
        └────────────────────────── both are thin clients; no new server port ──────────┘
                                                                                         │
                          crosslink.db (SQLite) ── peers / channels / handoffs ──────────┘
                          ADB / scrcpy ── Android tile
```

The webview never touches the filesystem or runs tools directly. It posts intents to the
extension host, which calls the already-running MCP server over HTTP (port from
`~/.sassymcp/config.json`, bearer from `~/.sassymcp/tokens.json`) — mirroring the
`spawn`/process pattern already in `setupWizard.ts`.

## Backend: coordination module (Phase 1, this slice)

New `sassymcp/modules/coordination.py`, registered in the `v020` group (Pro), riding the
existing `crosslink.db`. Non-breaking; adds channels by convention, no schema migration.

Tools:
- `sassy_peer_announce(peer_id, name, platform, capabilities, endpoint, ttl_seconds)` —
  register/refresh a peer (Claude, Cursor, Hermes, a remote node). Updates the `sessions`
  table and posts a heartbeat to the `peer-announce` channel with capabilities/endpoint.
- `sassy_peer_list(stale_seconds)` — active peers, newest announce per peer, alive flag.
- `sassy_peer_delegate(peer_id, task, context, next_steps, channel)` — targeted handoff
  addressed to one peer on `device-handoff` (receiver filters by `to`).
- `sassy_coordination_board(...)` — one-call aggregate for the webview: peers + channels
  with counts + recent handoff timeline + registered sessions.

Channels (convention, formalized later): `peer-announce`, `device-handoff`, `task-handoff`
(existing), `phone-context`, `joint` (Hermes).

## Phased delivery

- **Phase 1 — coordination backend** (this slice): `coordination.py` + wire into `v020`.
  Verifiable in isolation. *Ships in the server, not the extension.*
- **Phase 2 — Coordination view (the hero):** Vite/React webview in `sassymcp-vscode/`;
  a `BrainCockpitPanel` with the peers/channels/handoff board reading
  `sassy_coordination_board`, a Start-Hermes button (spawns `hermes_node.py`), and an
  Android tile (`sassy_adb_devices` + `phone_glance`). Extension `v1.7.0`.
- **Phase 3 — Action Launcher + dashboard:** multi-option launcher (cards + fuzzy box +
  palette commands) over the full tool surface; real `memory.db` counts via `sql.js`.
- **Phase 4 — Standalone overlay:** tray icon + global hotkey desktop launcher (separate
  process; reuses the same HTTP client) for reach when VS Code is closed.
- **Parallel — distribution:** list in MCP directories (Glama, LobeHub, mcpservers.org,
  mcp.so, Awesome MCP, official registry, PyPI); post the two Reddit drafts.

## Risks

- Global hotkey under Win11 UAC / frozen exe (Phase 4) — fallback to tray-click-to-open.
- React build step in the extension — standard; keep the Setup Wizard's no-build webview
  as the fallback path until the cockpit is stable.
- `context_threshold` auto-handoff depends on the AI calling `sassy_context_estimate` —
  prompt-engineering concern, documented in the playbook, not a code guarantee.
