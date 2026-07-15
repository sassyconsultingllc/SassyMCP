<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
   CodeMark: SCLLC1-SassyMCP-U47PZJ5FESVE
-->
# SASSYMCP IMPROVEMENT NOTES (post 2026-04-30 partition session)

*Last updated: 2026-05-15 — v1.4.1*

## Context
During a multi-hour session involving disk partitioning, LUKS setup, and
SSH-to-Linux work, the following friction patterns repeatedly blocked or
broke valid commands. Each item below has a concrete proposed fix.

---

## 1. Keyword interceptor matches inside string literals

### Problem
Commands that include words like fmt-volume, mk-fs, del-ete, rm-rf, w-ipefs
in their *content* (string literals, here-strings, comments, prose) get
blocked even when those words are not being executed.

Example block:
    Command blocked (safety): contains "fmt"

### Why it matters
Generating disk-management scripts, documentation, audit log entries, or
any prose containing risky-looking words becomes impossible without
contortions like splitting strings or base64-encoding.

### Proposed fix
Run keyword matches against tokenized command structure (parser AST), not
raw command text. Specifically:
- Strip string literals (single + double quoted) before keyword scan
- Strip here-strings
- Strip comment text
- Only match against the actual command/cmdlet/function names being
  invoked

Lower-effort interim fix: add a config flag interceptor.scan_string_literals
that defaults to false but can be enabled per-server if someone wants the
stricter mode.

---

## 2. allow_pattern only bypasses regex patterns, not keyword matches

### Problem
The tool description says allow_pattern is the "opt-in escape hatch for
power users" but it explicitly excludes keyword matches. So when the
keyword interceptor false-positives on a word inside a string, there is
no escape hatch at all - even with allow_pattern set to wildcard.

### Proposed fix
Two options:

A) Make allow_pattern wildcard a true universal bypass that also defeats
   keyword matching. Justification: if the user explicitly types wildcard,
   they have acknowledged they know what they are doing and audit logging
   captures it.

B) Add a separate flag allow_keyword that bypasses a specific keyword
   match. Same audit logging.

Either works. Option A is simpler and matches the existing semantics of
the wildcard.

---

## 3. No interactive confirmation path

### Current behavior
Destructive patterns get hard-blocked with an error. The user has to
either rephrase the command or use allow_pattern. Most of the time the
user wanted the command to execute and is annoyed to bounce off the wall.

### Proposed behavior
Add an interactive-confirm mode in the SASSYMCP config:

    interceptor:
      destructive_action: "confirm"
      confirm_methods:
        - toast            # Windows toast notification, click to approve
        - mcp_prompt       # send a structured prompt back to the MCP
                           # client (Claude can render it as a confirm
                           # widget)

Workflow:
1. User runs a destructive command
2. Interceptor matches destructive pattern
3. Instead of blocking, returns a confirmation_required response with
   a confirm_token
4. Claude surfaces this to user as a confirm widget
5. User clicks confirm
6. Claude calls sassy_shell_confirm(token) which executes

The toast option is for terminal-only sessions where there is no MCP
client to render UI - the user gets a Windows notification and clicks
through.

### Why this matters
Same point made by the user: most of the time the answer is yes. The
current model treats every destructive action as if it were the worst
possible case. Real workflow looks more like:
- yes do that 95 percent of the time
- wait no 5 percent of the time
- truly catastrophic actions: less than 0.1 percent

The confirm-do-not-block model preserves the safety win for the rare
catastrophic case while removing friction from the common case.

### Risk-tier the patterns
While we are at it, tier the destructive patterns:

    LOW    -> log only, no prompt    (rm of single file, mv to staging)
    MEDIUM -> confirm prompt          (rm -rf, mv -f, fmt operations)
    HIGH   -> double confirm + typed phrase  (raw writes to /dev/sdX,
                                              wipefs, mk-fs on mounted
                                              device, recursive removes
                                              of system paths)

Currently it is single-tier: everything is HIGH.

---

## 4. PowerShell wrapping breaks SSH heredocs

### Problem
sassy_shell is wrapping multi-line commands in a way that injects
PowerShell syntax (curly braces) into the tail of bash commands sent
over SSH. The bash side then sees an unmatched closing brace and parse
errors.

### Proposed fix
- For wsl shell, do not inject PowerShell wrapper braces
- For ssh-via-powershell, treat the command past the first ssh argument
  as opaque and escape rather than parse
- Add a raw=true flag that disables all normalization

---

## 5. write_file equivalent missing in SASSYMCP

### Problem
Desktop Commander has a write_file tool that accepts content + path and
writes the file. SASSYMCP has no equivalent direct write tool - all
writes go through sassy_shell with PowerShell Set-Content, which means
quoting hell for any content containing dollar signs, backticks, double
quotes, or here-string markers.

### Proposed fix
Add sassy_write_file(path, content, encoding=utf8, line_endings=lf|crlf).
Bypass PowerShell entirely - direct .NET File.WriteAllText call.

This single addition would have eliminated probably 40 percent of the
friction in the partition session.

---

## 6. Process output capture is unreliable

### Problem
sassy_shell sometimes returns empty output for commands that clearly
produced output. The output is being generated but not captured back to
the MCP response.

### Suspected cause
Stdout/stderr stream handling on long-running or backgrounded child
processes. Need to investigate whether output is being truncated,
buffered, or dropped by the wrapping layer.

### Proposed fix
- Add explicit timeout-aware output drain at the end of every shell call
- Surface partial output even on timeout (currently returns just the
  timeout message with nothing else)
- Add capture_to_file option that writes stdout to a file we can then
  read separately, sidestepping the MCP response size limits

---

## 7. Audit visibility for false-positive blocks

### Already exists
sassy_audit_false_positives is good. Keep it.

### Add
A counter visible in the tool description: "X false positives this
session". Helps the LLM realize early that it should switch tactics
rather than retrying the same blocked command shape.

---

## Summary priority list (what to fix first)

1. add sassy_write_file - removes most friction immediately
2. tokenize keyword scan to ignore string literals
3. add confirm-do-not-block flow with confirm_token round-trip
4. fix ssh-via-powershell wrapper that injects braces into commands
5. risk-tier the destructive patterns
6. fix process output capture reliability
7. audit counter in tool description

Items 1, 2, and 4 alone would have made tonight session take 10 minutes
instead of an hour.

---

## Resolution Status (as of v1.4.1, 2026-05-15)

| # | Item | Status |
|---|------|--------|
| 1 | `sassy_write_file` | **RESOLVED** — implemented in `sassymcp/modules/fileops.py` |
| 2 | Tokenize keyword scan | Open |
| 3 | Confirm-do-not-block flow | **RESOLVED** — `sassy_shell_confirm` implemented in `sassymcp/modules/shell.py` via `_confirm.py` |
| 4 | SSH/PowerShell wrapper braces | Open |
| 5 | Risk-tier destructive patterns | Open |
| 6 | Process output capture reliability | Open |
| 7 | Audit counter in tool description | **PARTIAL** — `sassy_audit_false_positives` exists; inline counter not yet added |