<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
   CodeMark: SCLLC1-Projects-LAM7SF3HKCIV
-->
# SassyMCP Positioning & Messaging

The source of truth for every piece of launch copy. Pull lines from here; don't reinvent per channel.
Every fact below matches the README and `LICENSE` as of v1.18.0 (2026-11-08). If either changes, update this file first.

---

## Facts to quote (and only these)

| Fact | Value |
|---|---|
| Tool count | **278 tools, 39 modules, 18 groups** |
| Replaces | **75+ MCP servers** |
| Platforms | Windows (standalone ~35 MB exe), macOS (universal2 binary), Linux (`pip install sassymcp`) |
| Clients auto-configured | Claude Desktop, Cursor, Windsurf, VS Code Copilot, Cline, Continue, Zed, Grok Desktop |
| License | **Free for personal use, every tool included. Commercial use needs a license.** 30-day free evaluation at work. |
| Source | **Source-available, not open source.** Public on GitHub to read and audit; proprietary license. |
| Supporter license | Optional tip for individuals. Unlocks nothing; does not cover commercial use. |
| Links | Repo: `github.com/sassyconsultingllc/SassyMCP` · Site: `sassyconsultingllc.com/sassymcp` · Terms: `LICENSING.md` in the repo |

**Never say:** "MIT", "open source", "Windows-only", "$49", "Pro tier", "free tier", "unlocks", or any tool count other than 278.

---

## One-liners (by length)

- **6 words:** One MCP server to replace them all.
- **Tagline (PH, ≤60 char):** 278 tools, one server — replaces 75+ MCP servers.
- **One sentence:** SassyMCP replaces 75+ separate MCP servers with one 278-tool server that auto-configures every MCP client on your machine. It's free for personal use with nothing held back.
- **Elevator:** The MCP ecosystem is death by a thousand servers: filesystem here, shell there, GitHub, Android, screenshots, each eating context and config. SassyMCP collapses all of it into one install, with a smart loader that keeps tool definitions to ~5K tokens of context and safety features no individual server has. It's free for personal use, and companies pay for a commercial license.

---

## The problem (why anyone cares)

Anyone running agentic coding tools hits this fast:
- You install 6–10 MCP servers (Filesystem, Desktop Commander, GitHub, mobile-mcp, a memory server, an OCR server…).
- Each one **eats context window**: tool definitions can consume 20–25K tokens before you've done anything.
- Each has its **own config**, its own bugs, its own update cycle.
- Some are **dangerous**: an agent can hallucinate `rm -rf`, and a plain shell server will happily run it.

## The product (what it is)

One server, 278 tools across 39 modules in 18 groups. Install once and it auto-detects and patches Claude Desktop, Cursor, Windsurf, VS Code Copilot, Cline, Continue, Zed, and Grok Desktop. A smart loader only loads the groups you use, so context cost stays around 5K tokens instead of 25K.

## Differentiators (all real; verify against the README before quoting)

1. **Replaces 75+ servers**, including the GitHub MCP server (28.6k★), Desktop Commander (5.9k★), Windows-MCP (5k★), mobile-mcp (4.4k★), and Anthropic's official Filesystem and Memory servers.
2. **Smart loading** cuts context overhead from ~25K to ~5K tokens by loading only the groups you use.
3. **Safe-delete interception.** Every delete-family command across every shell is intercepted and staged to `_DELETE_/` for review instead of destroying files. It catches wrappers, base64-encoded payloads, `.NET` calls, `robocopy /MIR`, and truncate-by-redirect. **No other MCP server does this.** It's the antidote to agent hallucination.
4. **Phone control with a brain.** Full Android control via the UI accessibility tree (structured data, not pixels), with **automatic sensitive-context detection**: it refuses to act on login, payment, or 2FA screens and hands control back to you.
5. **Dynamic vision.** `glance`/`watch`/`diff` return only changed frames in compressed grayscale (~2KB vs ~14KB), instead of screenshot-and-pray.
6. **An orphan-proof supervisor.** A self-healing process tree that restarts crashed or hung children and leaves no zombie processes behind on a hard kill.
7. **Nothing held back.** Every tool is free for personal use. No crippled tier and no feature unlocks. Runs offline, no telemetry.
8. **A better GitHub server.** The official one has [SHA-handling bugs](https://github.com/github/github-mcp-server/issues/2133). SassyMCP's uses correct blob SHA lookups, atomic multi-file commits, retry/backoff, and rate-limit awareness.

## Target personas

- **The agentic-coding power user** (Cursor/Claude Desktop/Windsurf daily driver) wants fewer servers, less context bloat, one config. *Hook: smart loading + one install for all clients.*
- **The cautious builder** has been burned, or fears being burned, by an agent deleting files. *Hook: safe-delete interception.*
- **The mobile/automation tinkerer** wants the AI to drive their phone. *Hook: phone control + sensitive-context auto-block.*
- **The team lead / engineering manager** wants one vetted, auditable server for the whole team instead of 8 unvetted ones. *Hook: one config, one audit log, source you can audit, and a commercial license that makes it legit to deploy at work.* **This is the persona that pays.**

## Objection handling

| Objection | Answer |
|---|---|
| "Windows only?" | No. Windows gets a standalone exe, macOS a universal2 binary, and Linux installs with `pip install sassymcp`. A few concepts are Windows-native by nature (raw Registry, Defender); those report a clear message elsewhere. |
| "Why one big server instead of small composable ones?" | The composability lives in the tool groups, loaded on demand. What you gain by colocating: one config, one audit log, one safety layer across every shell entry point, and cross-module workflows. |
| "Is it a security risk, one server with everything?" | It's the opposite: delete interception, protected paths, sensitive-context auto-block, and a full audit log are built in. And the source is public, so you can audit it. |
| "Closed source / proprietary? Hard pass." | Fair. It's free for personal use, and the source is public to read and audit, but it's not OSI open source. Companies pay for a commercial license, which is how development gets funded. |
| "What's the catch with free?" | None for individuals: every tool, no telemetry, runs offline. Using it for work needs a commercial license, with a 30-day free evaluation. |
| "278 tools will blow my context." | That's what smart loading is for. The default load is ~5K tokens, and you opt into heavy groups only when needed. |

## Proof points to collect (for social proof)

- GitHub stars on the public repo
- Total tool count vs the servers it replaces (already have: 278 vs 75+)
- Context-token comparison screenshot (`sassy_context_estimate` before/after)
- A short testimonial from any early user
- Download count from GitHub releases and PyPI

## Voice

Direct, technical, no hype, no emoji. The audience is engineers who can smell marketing. Lead with specifics (numbers, the GitHub bug, the safe-delete mechanics), not adjectives. Be upfront about the license; candor survives HN and spin doesn't.
