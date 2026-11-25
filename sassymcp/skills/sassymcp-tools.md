<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
   CodeMark: SCLLC1-SassyMCP-MSFTNQ4IMH3B
-->
---
name: sassymcp-tools
description: Activate when working with SassyMCP — recognises when user asks for screenshots, phone control, audit log review, GitHub PR work, persona memory, cross-session handoffs, network/registry forensics, or persistent terminal sessions. Maps user phrasings to the right tool sequences so the model picks the efficient path instead of improvising.
---

# SassyMCP Tool Playbook

This is the canonical "when to use which tool" guide for SassyMCP. The
`sassymcp install` CLI deploys per-client renderings of this file:

- Claude Desktop / Claude Code: `~/.claude/skills/sassymcp-tools/SKILL.md`
- Cursor: `~/.cursor/rules/sassymcp.md`
- Windsurf: `~/.codeium/windsurf/global_rules.md` (or workspace `.windsurfrules`)
- Continue: merged into the system message via `~/.continue/config.json`
- Zed: prepended to `assistant.default_model.system_prompt` in `~/.config/zed/settings.json`

## When the user asks for...

| User says... | Tool sequence |
|---|---|
| "screenshot" / "what's on screen" | `sassy_screenshot` (full color) OR `sassy_screen_glance` (3-6KB grayscale, prefer for repeated polling) |
| "watch the screen for changes" | `sassy_screen_watch` — returns only frames where pixel-diff exceeded threshold |
| "before/after the change" | `sassy_screen_diff` — takes frame, waits, takes another, returns both + diff image |
| "phone status" / "is my phone connected" | `sassy_phone_state` then `sassy_phone_glance` |
| "phone UI" / "tap that button" / "click X on phone" | `sassy_phone_ui` FIRST to get coords, then `sassy_phone_tap` with the coords. NEVER tap blind. |
| "swipe up / down / scroll on phone" | `sassy_phone_swipe` with `(x1,y1) -> (x2,y2)` coords from the UI tree |
| "type on phone" | `sassy_phone_type` (needs an editable field already focused) |
| "android shell" / "adb command" | `sassy_adb_shell` — destructive commands (rm, dd) auto-block; pass `allow_destructive=True` after explicit confirmation |
| "check the audit log" / "what got blocked" | `sassy_audit_search pattern_event="pattern_block"` for the security-interception trail |
| "review the PR" / "look at PR #N" | `sassy_combo_pr_review owner=<owner> repo=<repo> pr=N` — metadata + diff + comments + CI check status in one call. Or use the `pr-review` MCP prompt. |
| "remember this for next session" | `sassy_memory_remember` with key prefix `task_<concept>_<project>_state`. The key convention: `pattern_<concept>` for cross-project solutions, `decision_<concept>` for architecture decisions, `blocker_<concept>_<project>` for known blockers. |
| "what was I working on" / session start | `sassy_memory_context` FIRST, then `sassy_crosslink_recv channel="task-handoff" unread_only=True` to pick up cross-client handoffs. Don't ask the user — figure it out. |
| "hand this off to my [other client]" | `sassy_crosslink_send channel="task-handoff"` with a JSON payload containing `task`, `status`, `next_steps`, `files_touched`, `context_notes` |
| "build / compile / dev server / wrangler" | `sassy_session_start` (NOT `sassy_shell`) — sessions persist; shell is one-shot. Then `sassy_session_read` in a loop to follow output. |
| "scan my network" / "what's listening" | `sassy_netstat` + `sassy_arp_table` for local-host posture; `sassy_port_scan target=<host>` for remote (always confirm with user before scanning third-party) |
| "what runs on boot" / "autoruns" / "persistence" | `sassy_autorun_entries` (cross-platform — Windows Run/RunOnce under HKLM+HKCU, macOS LaunchAgents/LaunchDaemons + login items, Linux enabled systemd units + crontab + XDG autostart) |
| "USB history" / "connected devices" | `sassy_reg_read key_path="HKLM\\System\\CurrentControlSet\\Enum\\USBSTOR"` |
| "edit a file in this project" | `sassy_edit_block` for surgical find/replace OR `sassy_edit_multi` for parallel edits across one file. The SassyMCP source tree is auto-protected — edit it in a checkout / PR, not via MCP tools. |
| "delete X" | NEVER use `sassy_shell rm` — the interceptor moves targets to `_DELETE_/` instead. Use `sassy_safe_delete` if you want explicit staging. |
| "add a new tool to SassyMCP" | Edit the SassyMCP source checkout and ship a new release. Self-modification tools are removed. |
| "run a command on my Linux box" / "ssh into the server" | `sassy_linux_exec` — SSH to the host in `SSH_HOST`/`SSH_USER` (plink on Windows, native ssh on macOS/Linux). Destructive commands are a hard block with no override; SSH in manually to remove files. |
| "find references to X in the codebase" | `sassy_search_files pattern=<x>` — always faster than `sassy_shell grep`. Returns ranked file list. |
| "what's the SassyMCP context cost?" | `sassy_context_estimate` — shows tool def tokens as % of 200K window. |
| "show tool usage stats" | `sassy_tool_usage` or `sassy_observability_tool_stats` (latter includes pruning suggestions) |

## Discipline

1. **Don't improvise via shell when a tool exists.** If the user says "screenshot", call `sassy_screenshot`, not `sassy_shell powershell ScreenCapture`. The shell version has more failure modes and burns more context.

2. **Read before edit.** Always `sassy_read_file` before any edit. The SassyMCP source tree is auto-protected — change it in a checkout / PR, not via MCP tools.

3. **Sensitive contexts auto-block.** Phone interaction tools (tap, swipe, type) refuse on detected login/payment/2FA screens. Pass `confirmed=True` only after asking the user.

4. **Smart loading is real.** SassyMCP loads only the tool groups whose top tools cross score >= 0.5 in the user's `~/.sassymcp/tool_usage.json` history, plus the always-on core. Override with `SASSYMCP_GROUPS=core,android,system` env var, or `SASSYMCP_LOAD_ALL=1` to load every group.

5. **Cross-session memory is the source of truth.** Use `sassy_memory_*` to persist learning across sessions. The convention is task-concept-based, not project-based — same concept across projects shares patterns automatically.

## Discover more

- `sassy_hooks_list` — see all registered operational playbooks (web audit, phone autonomous, github review, etc.)
- `sassy_hooks_activate name="<hook>"` — load a hook's full multi-step playbook into context
- `sassy_setup_status` — reports what's configured (persona, GitHub token, SSH creds, Pro license)
- `sassy_tool_groups` — list all tool groups and which ones are currently loaded
