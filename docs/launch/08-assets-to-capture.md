# Launch assets to capture

These gate conversion. The single demo GIF is worth more than any copy in this kit — every channel
(PH gallery thumbnail, X tweet 1, Reddit, the article header) uses it. Capture these before launch day.

---

## 1. Demo GIF — THE money shot (highest priority)

**Goal:** in ~10–15 seconds, show one prompt in a real client triggering a multi-tool action that
would normally need 3 separate servers.

**Suggested scene:** in Claude Desktop or Cursor, prompt something like:
> "Find the 5 largest files under this project, then create a GitHub issue listing them."

…and show SassyMCP doing files + GitHub in one server, with the tool calls visible.

**Even better second scene (the safety hook):** show an agent issued `rm important.txt`, and the
response "intercepted — moved to ./_DELETE_/important.txt". This is the most novel thing you have.

**How to capture:** ScreenToGif or ShareX on Windows. Keep it tight, crop to the relevant pane,
loop cleanly, target < 5 MB so it inlines everywhere. Export an MP4 too (X/PH prefer MP4; Reddit/GitHub like GIF).

## 2. Context-savings screenshot
Run `sassy_context_estimate` with default groups vs `SASSYMCP_LOAD_ALL=1`, side by side, showing
the ~5K vs ~25K token difference. This backs the headline claim with a number — critical for HN/Reddit credibility.

## 3. "Replaces 75+" comparison graphic
The 8 marquee servers (GitHub 28.6k★, Desktop Commander 5.9k★, Windows-MCP 5k★, mobile-mcp 4.4k★,
Filesystem, Memory, + "70 more") collapsing into one SassyMCP. The pricing page already has a text
version of this strip — a clean graphic version is the PH gallery item #2.

## 4. Safe-delete terminal screenshot
A terminal showing several delete attempts (`rm`, `del /q *.log`, a base64 PowerShell payload) all
intercepted and staged, with the audit-log line. Proves the feature is real, not marketing.

## 5. Phone-control screenshot
The AI operating Android via the UI tree, then the auto-block on a login screen ("sensitive context
detected — handing back to you"). Shows the "control with a brain" differentiator.

## 6. Multi-client auto-config screenshot
The installer/CLI output detecting and patching Claude Desktop + Cursor + Windsurf + VS Code in one
run. Sells "one install for everything."

## 7. Icon / logo
The emerald bolt already used on the site (`fa-bolt`). Export a clean 512×512 PNG + a square icon for
directory listings (most directories want a square icon ≥ 128px).

---

## Where each asset goes

| Asset | PH gallery | X | Reddit | dev.to | pricing.html | directories |
|---|---|---|---|---|---|---|
| Demo GIF | #1 (thumbnail) | tweet 1 | top of post | header | hero | — |
| Context savings | #3 | tweet 4 | in comments | body | — | — |
| Replaces 75+ | #2 | — | — | body | already there (text) | — |
| Safe-delete | #4 | tweet 5 | in comments | body | — | — |
| Phone control | #5 | tweet 6 | — | body | — | — |
| Multi-client | #6 | tweet 3 | — | body | maybe | — |
| Icon | — | — | — | — | — | every listing |

**Fastest path:** capture the demo GIF + context-savings screenshot first. Those two unblock the PH
thumbnail, X tweet 1, and HN/Reddit credibility — i.e. ~80% of the launch's conversion lift.
