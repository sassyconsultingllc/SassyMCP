# SassyMCP Positioning & Messaging

The source of truth for every piece of launch copy. Pull lines from here; don't reinvent per channel.

---

## One-liners (by length)

- **6 words:** One MCP server to replace them all.
- **Tagline (PH, ~60 char):** 270 tools, one exe — replaces 75+ MCP servers.
- **One sentence:** SassyMCP is a single 34 MB Windows exe that replaces 75+ separate MCP servers with 270 tools, auto-configures every MCP client on your machine, and is a one-time purchase — no subscription.
- **Elevator:** The MCP ecosystem is death by a thousand servers — filesystem here, shell there, GitHub, Android, screenshots, each eating context and config. SassyMCP collapses all of it into one install with a smart loader that keeps tool definitions under 5% of your context window, plus safety features no individual server has.

---

## The problem (why anyone cares)

Anyone running agentic coding tools hits this fast:
- You install 6–10 MCP servers (Filesystem, Desktop Commander, GitHub, mobile-mcp, a memory server, an OCR server…).
- Each one **eats context window** — tool definitions can consume 20–25K tokens before you've done anything.
- Each has its **own config**, its own bugs, its own update cycle.
- Some are **dangerous** — an agent can hallucinate `rm -rf` and a plain shell server will happily run it.

## The product (what it is)

One Windows exe. 270 tools across 35 modules in 18 groups. Install once → it auto-detects and
patches Claude Desktop, Cursor, Windsurf, VS Code Copilot, Cline, Continue, Zed, and Grok Desktop.
A smart loader only loads the groups you use, so context cost stays ~5K tokens instead of ~25K.

## Differentiators (all real — verify against README before quoting)

1. **Replaces 75+ servers** — including the GitHub MCP server (28.6k★), Desktop Commander (5.9k★), Windows-MCP (5k★), mobile-mcp (4.4k★), and Anthropic's official Filesystem + Memory.
2. **Smart loading** — context overhead ~25K → ~5K tokens by loading only used groups.
3. **Safe-delete interception** — every delete-family command across every shell is intercepted and staged to `_DELETE_/` for review instead of destroying files. Catches wrappers, base64-encoded payloads, `.NET` calls, `robocopy /MIR`, truncate-by-redirect. **No other MCP server does this** — it's the antidote to agent hallucination.
4. **Phone control with a brain** — full Android control via the UI accessibility tree (structured data, not pixels), with **automatic sensitive-context detection**: it refuses to act on login/payment/2FA screens and hands back to you.
5. **Dynamic vision** — `glance`/`watch`/`diff` return only changed frames in compressed grayscale (~2KB vs ~14KB), instead of screenshot-and-pray.
6. **Self-modification** — hot-reload modules, git-backed rollback on syntax errors.
7. **One-time perpetual** — buy once, own forever, runs fully offline after activation. No subscription, no telemetry.
8. **A better GitHub server** — the official one has [SHA-handling bugs](https://github.com/github/github-mcp-server/issues/2133); SassyMCP's uses correct blob SHA lookups, atomic multi-file commits, retry/backoff, rate-limit awareness.

## Target personas

- **The agentic-coding power user** (Cursor/Claude Desktop/Windsurf daily driver) — wants fewer servers, less context bloat, one config. *Hook: smart loading + one install for all clients.*
- **The cautious builder** — has been burned (or fears being burned) by an agent deleting files. *Hook: safe-delete interception.*
- **The mobile/automation tinkerer** — wants the AI to drive their phone. *Hook: phone control + sensitive-context auto-block.*
- **The security/forensics user** — *Hook: the Forensics add-on (registry, APK, certs, Defender/firewall, autoruns).*

## Objection handling

| Objection | Answer |
|---|---|
| "Windows only?" | Yes today — built Windows-native for the Windows automation surface (registry, Defender, event logs). The core MCP tools (files, shell, GitHub, memory) work anywhere an MCP client runs; from-source via `uv` is cross-platform for those. |
| "Why pay when individual servers are free?" | You're paying $49 once to delete 6–10 configs, recover ~20K tokens of context, and get safety + phone + vision features none of the free servers have. Free tier is genuinely useful if you'd rather not. |
| "Is it a security risk — one server with everything?" | It's the opposite: safe-delete interception, protected paths, sensitive-context auto-block, and a full audit log are built in. The MIT core is on GitHub to audit. |
| "270 tools will blow my context." | That's the point of smart loading — default load is ~5K tokens. You opt into heavy groups only when needed. |
| "One-time? What's the catch?" | None. Activation lets it run offline forever. Free updates. The catch other products have — the subscription — is the thing we removed. |

## Proof points to collect (for social proof)

- GitHub stars on the public repo
- Total tool count vs the servers it replaces (already have: 270 vs 75+)
- Context-token comparison screenshot (`sassy_context_estimate` before/after)
- A short testimonial from any early user
- Download count from GitHub releases

## Voice

Direct, technical, no hype, no emoji. The audience is engineers who can smell marketing. Lead with
specifics (numbers, the GitHub bug, the safe-delete mechanics), not adjectives.
