# Article: "I replaced 75 MCP servers with one exe"

Evergreen, SEO-compounding. Publish on dev.to (tags: #ai #mcp #devtools #productivity), mirror to
the Sassy Consulting blog, and ~1 week after launch submit the blog URL to HN as a normal link
(not Show HN). This is the content engine — it keeps pulling search traffic long after the spike.

Voice: technical, honest, includes the tradeoffs. No marketing adjectives. Edit the bracketed bits
once you have real numbers/links.

---

# I replaced 75 MCP servers with one exe — here's what I learned

## The death-by-a-thousand-servers problem

If you build with agentic coding tools — Claude Desktop, Cursor, Windsurf, whatever — you've
probably lived this. You want the AI to actually *do* things, so you reach for MCP servers:

- Filesystem server for reading/writing files
- Desktop Commander for shell
- The GitHub server for repos and PRs
- mobile-mcp for your phone
- A memory server for persistence
- An OCR/vision server for screenshots

Before long you're running eight of them. And three problems compound:

1. **Context bloat.** Tool definitions load into your context window whether you use them or not.
   In my setup that was ~25K tokens gone before I typed a single word.
2. **Config sprawl.** Each server is configured separately, and then configured *again* in every
   client. I had the same servers wired into Claude Desktop, Cursor, and Windsurf independently.
3. **The destructive-command problem.** A plain shell server will cheerfully run whatever the model
   emits — including a hallucinated `rm -rf` or a `Remove-Item` pointed at the wrong directory.

Each problem is individually annoying. Together they made me stop and rethink the whole stack.

## The realization: it's not the tools, it's the packaging

The tools themselves were fine. The problem was that "one server per capability" — which sounds
nicely Unix-y — actually means N processes, N configs, N context costs, and N separate places where
nothing coordinates. There's no shared audit log, no shared safety layer, no cross-tool workflow.

So I built [SassyMCP]: one Windows exe, 270 tools across 35 modules, replacing 75+ individual
servers. Here's the part that makes "one big server" not insane.

## Smart loading: 270 tools, ~5K tokens

The objection writes itself: *270 tools will obliterate your context window.* It would — if they all
loaded. They don't.

Tools are grouped (core, github_full, android, vision, system, …), and only the groups you use load.
The default footprint is ~5K tokens, not ~25K. There's a `context_estimate` tool whose entire job is
to tell you what your current server selection costs — because I was tired of guessing.

```
# default: ~5K tokens of tool definitions
sassymcp.exe

# everything, when you actually need it: ~22K tokens
SASSYMCP_LOAD_ALL=1 sassymcp.exe

# just what you want
SASSYMCP_GROUPS=core,github_full,android sassymcp.exe
```

[Insert the context_estimate before/after screenshot here.]

## One install, every client

Install once and it auto-detects and patches every MCP client on the machine — Claude Desktop,
Cursor, Windsurf, VS Code Copilot, Cline, Continue, Zed, Grok Desktop — so they all share one
server. No per-client JSON editing. One config, one audit log, one update cycle.

## The safety model: agents can't delete your files by accident

This is the feature I'm proudest of, and the one I think the "many small servers" model can't easily
replicate. Every delete-family command, across every shell and every tool entry point, is
**intercepted** — the targets are moved to a `_DELETE_/` staging folder for human review instead of
being destroyed.

It's not a naive keyword match. It catches:

- Bare commands: `rm`, `del`, `Remove-Item`, `unlink`, `rmdir`, and PowerShell aliases (`ri`, `rni`).
- Shell wrappers: `cmd /c del foo`, `powershell -c "del foo"`, `bash -c "rm foo"` — the payload is unwrapped and re-scanned.
- Base64 payloads: `powershell -EncodedCommand <base64>` is decoded (UTF-16-LE) and scanned.
- `.NET` calls: `[System.IO.File]::Delete(...)`.
- `robocopy /MIR` and `/PURGE` (mirror/purge delete destination files).
- Truncate-by-redirect: `> file.txt`, `type foo > bar.txt`.

Protected roots — the source tree, the config dir, the staging folder — are refused outright, with
path normalization (`resolve()`) so `..\`, symlinks, and Windows 8.3 short names can't sneak past the
check. `rm -rf /` is hard-blocked with no move attempted.

```
rm important.txt      → blocked, moved to ./_DELETE_/important.txt
del /q *.log          → blocked, all .log files staged
cmd /c del foo        → unwrapped and intercepted
rm -rf /              → hard-blocked, no move
ls -la                → runs normally (not a delete)
```

The point: consolidation is usually framed as *more* risk ("one server with everything"). I think
the opposite is true when the consolidation lets you put a single safety layer across every entry
point. Eight separate shell servers means eight places to get that wrong.

## Phone control that reads the screen first

It can drive an Android phone, but not by guessing at pixels — it reads the UI accessibility tree
(structured elements with coordinates and state). Before any tap/swipe/type, it scans that tree, and
if it detects a login, payment, 2FA, or permission screen, it **refuses** and hands control back to
you. You log in manually, it watches, you say "go", it resumes — now aware of what you did.

## A side-quest: the GitHub server

While building the GitHub module I hit the fact that the official GitHub MCP server has
[SHA-handling bugs][gh-bug]. So I reimplemented it: correct blob SHA lookups, proper path encoding,
atomic multi-file commits via the Git Data API, retry with backoff, rate-limit awareness. It's 80
tools and a drop-in replacement that, for my workflows, just works.

## What I'd push back on (if I were you)

- **"One big server is the wrong abstraction."** Maybe. The composability lives in the loadable
  groups rather than in separate processes. The wins — one config, one audit log, one safety layer,
  cross-module combos — felt worth it to me, but I'd genuinely debate it.
- **"Windows-only."** True today. The Windows-specific surface (registry, Defender, event logs,
  autoruns) is native; the portable core (files, shell, GitHub, memory) runs from source via `uv`
  anywhere. A first-class mac/Linux build is roadmap-if-there's-demand.

## On pricing: I made it a one-time purchase

I'm as tired of subscriptions as you are, so SassyMCP is buy-once-own-forever. The free tier is a
complete daily driver — not a crippled demo — and Pro is a single $49 payment that unlocks the heavy
automation groups. It runs fully offline after activation, with no telemetry. The core is MIT on
GitHub if you want to read it.

## Try it / tear it apart

Free download and source: [github.com/sassyconsultingllc/SassyMCP][repo]. I'd love feedback on the
architecture and the safety model specifically — including whether the whole "one server" premise is
a mistake.

[SassyMCP]: https://sassyconsultingllc.com/pricing.html
[repo]: https://github.com/sassyconsultingllc/SassyMCP
[gh-bug]: https://github.com/github/github-mcp-server/issues/2133
