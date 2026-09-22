<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
   CodeMark: SCLLC1-SassyMCP-ZDDE3OPVRGHM
-->
# Upgrading to SassyMCP 1.16.0

v1.16.0 is mostly a metadata release — all 278 tool descriptions were
rewritten and every tool now carries explicit MCP `ToolAnnotations`
(`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`).
Nothing about the tool set itself changes, but the pre-release security
audit did change several runtime behaviors. Each item below is verified
against the v1.16.0 source: **what changed**, **why**, and **what you must
do**.

> **Not to be confused with `MIGRATION.md`.** That file is a
> *competitor-switching* guide — it helps you replace Windows-MCP, Desktop
> Commander, and/or Filesystem MCP with SassyMCP (last updated for v1.4.1).
> It is **not** a version-upgrade guide. This document is the one you want
> when moving an existing SassyMCP install from 1.15.x to 1.16.0.

---

## 1. `sassy_permission` privilege mutations now need `confirm='YES'`

**What changed.** `sassy_permission` actions `add_rule`, `add_root`, and
`clear_rules` now refuse to run unless called with `confirm='YES'` (exact,
case-sensitive). The same gate applies to `set_mode` with `mode=bypass`.
Previously these mutations ran with no confirmation. (`remove_root` is
unaffected — removing a sandbox root only tightens the jail.)

**Why.** Any MCP client that can call `sassy_permission` could previously
widen the sandbox jail (`add_root`) or silently grant destructive tools a
free pass (`add_rule` / `clear_rules`) without any acknowledgment, and
switch the whole engine to bypass mode. The gate makes the caller
explicitly acknowledge the privilege escalation.

**What you must do.** Re-run the call with the parameter added:

```python
sassy_permission(action="add_root", path="D:/work", confirm="YES")
```

If you have automation that manages permission rules, add
`confirm="YES"` to those calls. There is no way to disable the gate.

---

## 2. Control Panel `POST /api/settings` with `mode=bypass` needs `confirm:"YES"`

**What changed.** Setting `permission.mode` to `bypass` through the
Control Panel HTTP API (`POST /api/settings`) now requires
`"confirm": "YES"` in the request body. Without it the request returns
**400** instead of 200 — previously it succeeded silently.

**Why.** The panel must not be a confirm-free backdoor to bypass mode,
which disables destructive-pattern gating for shell and file tools. The
panel API now enforces the same consent the `sassy_permission` tool does.

**What you must do.** If you script the panel (e.g. `curl` against
`/api/settings`), include the field:

```bash
curl -s -H "X-Panel-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"mode":"bypass","confirm":"YES"}' \
  http://127.0.0.1:8765/api/settings
```

Other settings changes are unaffected.

---

## 3. `sassy_panel` no longer reveals the bearer token on `status`/`start` — new `rotate` action

**What changed.**
- `sassy_panel(action="status")` and `action="start"` now return a
  **tokenless** URL. The bearer token is no longer handed to every caller
  that merely checks panel health.
- The token is revealed only by the explicit `action="url"`.
- New `action="rotate"`: generates a fresh panel bearer token, persists it,
  invalidates the old one immediately (no restart needed), and writes an
  audit event.

**Why.** Handing the bearer token to every health check meant any tool
caller — including the model itself — could silently acquire full panel
access. The token now requires an explicit, audit-visible request.

**What you must do.**
- If you scripted token capture from `status`/`start`, switch to
  `sassy_panel(action="url")`.
- If you ever pasted a tokenized URL somewhere it doesn't belong, run
  `sassy_panel(action="rotate")` — the old token stops working instantly.

---

## 4. `?token=` panel query-string auth is deprecated

**What changed.** Passing the panel token as `?token=...` in the URL still
works, but the server logs a deprecation warning on every use. The
preferred form is the `X-Panel-Token` request header.

**Why.** Tokens in URLs leak into browser history, shell history, and any
log aggregator that captures request lines. Header-based auth doesn't.

**What you must do.** Switch your HTTP clients to the header:

```bash
curl -H "X-Panel-Token: $TOKEN" http://127.0.0.1:8765/api/settings
```

Existing bookmarks and the panel UI's first-load flow keep working for
now, but plan to move them.

---

## 5. `sassy_copy` / `sassy_move` now refuse sensitive-denylist sources

**What changed.** Copying or moving a file whose source matches the
sensitive-read denylist (SSH keys under `~/.ssh`, credential/token stores,
SAM/SECURITY hives, `/etc/shadow`, and similar) is now **refused**, in all
permission modes, before any other checks run.

**Why.** Copying bytes out of their controlled location is
read-equivalent exfiltration — the same material the content-read tools
already refuse to show. Letting copy/move bypass that would make the read
denylist decorative.

**What you must do.** Nothing, if you never moved secrets around. If you
have a legitimate flow that archives credential material (e.g. a dotfiles
backup), switch it to `sassy_zip` / `sassy_tar` (see next item) or
restructure it — there is no override flag for the copy/move refusal.

---

## 6. `sassy_zip` / `sassy_tar` warn instead of refusing on sensitive members

**What changed.** Archiving a directory that contains sensitive-denylist
files still succeeds — the archive is created — but the result now carries
a **`warning`** field naming the sensitive members, and the inclusion is
written to the audit log.

**Why.** Refusing outright would break legitimate full-directory backups;
proceeding silently would hide credential material inside archives nobody
noticed. Warn-and-log is the middle path: backups keep working, and the
risk is visible.

**What you must do.** If you consume these tools' results in automation,
check for the `warning` key. Periodically review audit-log entries for
archive operations that swept up sensitive files, and decide whether those
archives should exist.

---

## 7. `sassy_state_clear` requires `confirm='YES'`

**What changed.** Clearing persisted tool state — for one tool or for all
tools — now refuses to run without `confirm='YES'` (exact, case-sensitive).
Previously the clear was unconditional.

**Why.** State deletion is irreversible; an accidental or prompt-injected
clear wipes cross-session memory with no recovery. This matches the
convention already used by `sassy_permission` privilege mutations and
`sassy_audit_clear`.

**What you must do.** Add the parameter:

```python
sassy_state_clear(tool_name="my_tool", confirm="YES")  # one tool
sassy_state_clear(confirm="YES")                       # all tools
```

---

## 8. `sassy_screenshot` errors on a malformed `region`

**What changed.** A `region` that isn't four integers as `x,y,w,h` (wrong
part count, non-numeric values) now returns an **error**. Previously it
fell through and silently captured the full screen.

**Why.** Silently capturing the whole screen when the caller asked for a
region captures more than intended — a privacy footgun hiding behind a
typo.

**What you must do.** Pass exactly four integers, e.g.
`region="100,200,800,600"`, and check for the new error return in
automation that builds region strings dynamically.

---

## 9. `tokens.json` with group/world access is refused at startup (fail-closed)

**What changed.** If `~/.sassymcp/tokens.json` is readable **or** writable
by group or anyone else on POSIX (mode bits beyond `0600`), or grants
access to broad principals (`Everyone`, `BUILTIN\Users`, …) on Windows,
the server **refuses to start** with a `PermissionError` instead of
loading the tokens.

**Why.** A token file any local user can read or replace lets that user
impersonate your auth and take the server. Auth misconfiguration is fatal
by design — a loud refusal beats a silently compromised server.

**What you must do.** Lock the file down:

```bash
chmod 600 ~/.sassymcp/tokens.json
```

On Windows, remove non-owner entries from the file's ACL. If you were
intentionally sharing the file between users: stop — issue per-user
tokens instead.

---

## 10. Strict rate limiting is now an opt-in (`SASSYMCP_STRICT_RATE_LIMIT=1`)

**What changed.** A new environment variable, `SASSYMCP_STRICT_RATE_LIMIT`,
switches the per-group rate limiter from fail-open to fail-closed:
- a rate-limiter setup failure **aborts startup** instead of running
  unthrottled, and
- a limiter error during `acquire()` **refuses the call** instead of
  letting it through.

**The default is unchanged**: without the variable, a broken limiter logs
loudly but lets calls through (rate limiting is defense-in-depth, not the
auth boundary).

**Why.** Operators exposing an instance over a tunnel or LAN may want a
hard guarantee that a limiter failure can never silently downgrade them to
unthrottled. Opt-in keeps the safe default for everyone else.

**What you must do.** Nothing, unless you want the guarantee — then set
`SASSYMCP_STRICT_RATE_LIMIT=1` in the server's environment before
starting. Note that with it set, a limiter misconfiguration becomes a
startup failure, so test it once before deploying.

---

## Tool profiles (new in 1.16.0)

The Control Panel has a new **Profiles** tab (`GET`/`POST /api/profile`) that
gates which tools an MCP session can see (`tools/list`) and call
(`tools/call`). Seven profiles: `full` (default), `developer`, `forensics`,
`devices`, `sysadmin`, `readonly` (every `readOnlyHint=true` tool), and
`custom` (a group set you tick in the dashboard).

**What you must do.** Nothing — this is purely additive. Every server starts
on `full`, profiles are session-scoped and never persisted (a restart always
comes back up on `full`), and there is deliberately **no MCP tool** that can
switch or widen a profile, so agents cannot escalate themselves. Two things
to know if you use it: switching to a *broader* profile is an audited
escalation and needs `confirm="YES"` (the panel UI sends it for you);
narrowing never does. A tool hidden from `tools/list` is also uncallable via
`tools/call`. See README "Tool profiles" for the profile→group mapping.

---

## Summary of required actions

| # | Change | Action |
|---|--------|--------|
| 1 | `sassy_permission` privilege mutations gated | Add `confirm="YES"` to `add_rule`/`add_root`/`clear_rules`/`set_mode=bypass` calls |
| 2 | Panel `POST /api/settings` bypass gated | Add `"confirm":"YES"` to bypass-mode API calls |
| 3 | `sassy_panel` token hygiene | Use `action="url"` for the token; `rotate` if exposed |
| 4 | `?token=` deprecated | Switch to `X-Panel-Token` header |
| 5 | copy/move refuse sensitive sources | Move secret backups to zip/tar flows |
| 6 | zip/tar warn on sensitive members | Check the `warning` field; review audit log |
| 7 | `sassy_state_clear` gated | Add `confirm="YES"` |
| 8 | screenshot region validated | Pass four integers; handle the new error |
| 9 | `tokens.json` permissions enforced | `chmod 600 ~/.sassymcp/tokens.json` |
| 10 | Strict rate limiting opt-in | Set `SASSYMCP_STRICT_RATE_LIMIT=1` only if you want fail-closed |
| 11 | Tool profiles (new surface) | Nothing required — additive; starts on `full`, session-scoped |
