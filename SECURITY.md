<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
   CodeMark: SCLLC1-SassyMCP-FJGD4K4VPY7Z
-->
# Security Policy

## Supported versions

| Version | Support |
|---------|---------|
| 1.16.x  | ✅ Fully supported — security fixes issued promptly |
| < 1.16  | ⚠️ Best-effort only — upgrade to 1.16.x for fixes |

Older lines receive fixes only when the issue is trivially backportable
and severe. In practice: **upgrade**; the 1.16.x line is where fixes land.

## How to report

Email **`security@sassyconsultingllc.com`** with:

- the SassyMCP version (`sassy_self_check` → version, or the installer name)
- how the issue was found and what was tested
- a minimal reproduction (commands, tool calls, config) — no live
  credentials, tokens, or customer data
- your assessment of impact

> **TODO (owner):** confirm `security@sassyconsultingllc.com` actually
> routes to a monitored mailbox, and consider adding a PGP key or an
> encrypted-report channel.

We will acknowledge receipt and keep you posted on triage. Please do not
disclose the issue publicly until we've had a chance to ship a fix.

## Response-time expectations

- **Acknowledgment:** within 3 business days of your report.
- **Triage decision** (valid / invalid / needs-more-info): within 10
  business days.
- **Fix:** critical issues (remote code execution by an unauthenticated
  party, auth bypass, token exfiltration) are patched as fast as we can
  safely ship — target days, not weeks. Lower-severity issues ride the
  next scheduled release.

These are targets, not SLAs. Sassy Consulting LLC is a small shop; if a
target slips we will tell you rather than go quiet.

## Scope — read this before reporting

SassyMCP is, by design, a **privileged automation bridge**: it grants a
connected LLM (or any MCP client holding your token) arbitrary command
execution — local shell, remote SSH, Windows registry reads/writes,
process control, clipboard, screen/keyboard automation, Android devices
over ADB, iPhones over USB, and file-system access across the configured
sandbox. **The ability to run commands is the product, not a bug.**

That has direct consequences for what counts as a vulnerability here:

- **In scope:** ways to gain that power *without* the credentials that are
  supposed to gate it. Examples: authentication bypass on the MCP or
  Control Panel endpoints; token disclosure through logs, error messages,
  or tool outputs (the `sassy_panel` token-hygiene work in 1.16.0 is this
  class of fix); permission-denylist bypasses (e.g. reading or exfiltrating
  sensitive paths like `~/.ssh` through a tool that should refuse them);
  sandbox escapes; audit-log tampering or suppression; a panel or API
  endpoint that lets an unauthenticated caller escalate to bypass mode.
- **Out of scope (by design):** an *authenticated* client using its
  legitimate powers destructively. If you hold the token and ask
  `sassy_shell` to `rm -rf` something, or `sassy_reg_write` to change a
  key — that is the tool working as documented, gated by the permission
  engine you configured (strict / confirm / sandbox / bypass). Reports
  that amount to "an authenticated user can do dangerous things" will be
  closed as intended behavior, possibly with a pointer to the permission
  modes in `UPGRADING-1.16.md`.

**Threat model in one paragraph:** the adversary is someone *without* your
token or your host access. The token, the host account SassyMCP runs as,
and the pairing/trust steps for devices (ADB authorization, iPhone
pairing) are the trust boundary. Anything that crosses that boundary
without them is a bug; anything inside it is configuration.

## What NOT to report

- Missing rate limits on a default install (rate limiting is
  defense-in-depth; see `SASSYMCP_STRICT_RATE_LIMIT` — and a missing cap is
  a hardening suggestion, not a vulnerability).
- The Control Panel binding to loopback only being "bypassable" by someone
  who already has code execution on the host (they're already inside the
  boundary).
- Tool descriptions or annotations you disagree with stylistically —
  file those as regular issues, not security reports.
- Vulnerabilities in third-party dependencies *without* a demonstration
  that they are reachable through SassyMCP's actual use of the library
  (a CVE in a transitive dep we never exercise is noise; show the path).
- Social-engineering or phishing scenarios, physical-access attacks, or
  "the installer could be replaced by a malicious download mirror"
  (use the release-provenance notes in `docs/RELEASE-PROVENANCE.md` and
  verify checksums).
- Automated scanner output with no manual verification.

## Safe harbor

If you follow this policy — report privately to the address above, give us
a reasonable window to fix, and don't exfiltrate, destroy, or degrade data
beyond what a minimal proof-of-concept requires — we will not pursue legal
action against you for the research, and we will credit you in the release
notes (or keep you anonymous, your choice).

## Hardening pointers

New in 1.16.0 and relevant to anyone deploying beyond localhost:

- Keep `tokens.json` at `0600` — the server now refuses to start
  otherwise (fail-closed).
- Keep the Control Panel on loopback; if you tunnel it, use the
  `X-Panel-Token` header, never `?token=` in URLs.
- Prefer `strict` or `confirm` permission modes over `bypass`; if you
  must use `bypass`, it now requires explicit confirmation in both the
  tool and the panel API.
- Consider `SASSYMCP_STRICT_RATE_LIMIT=1` for LAN/tunnel deployments.
- Review the audit log (`sassy_audit_log` / `sassy_recent_tool_calls`)
  the way you'd review sudo logs — it is the record of what the model's
  hands did.
