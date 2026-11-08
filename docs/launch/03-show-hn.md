<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
   CodeMark: SCLLC1-Projects-PZYCUVJ2ILGT
-->
# Show HN — paste-ready + comment battle plan

HN is the highest-variance channel: a hit is your biggest traffic day ever; a miss sinks quietly.
You win or lose **in the comments**. Post in the morning ET, then camp the thread for hours.
Be humble, technical, specific, and invite criticism. HN smells marketing instantly — no adjectives, no hype.

**The license will be the #1 thread.** Someone will open `LICENSE` within minutes. The post states it
plainly up front so that thread starts from candor, not a "gotcha."

---

## Title (80 char max — pick one)

- `Show HN: SassyMCP – one MCP server that replaces 75+ (278 tools, smart-loaded)`
- `Show HN: I collapsed 8 MCP servers into one with safe-delete interception`

First is clearer; second leads with the most novel feature. Go with the first unless you want to bet on the safe-delete angle.

---

## Body

> I build with agentic coding tools all day and kept ending up with 8+ MCP servers — filesystem, shell, GitHub, mobile, memory, OCR. Three problems compounded: (1) tool definitions ate ~25K tokens of context before I did anything, (2) eight configs to keep in sync across Claude Desktop / Cursor / Windsurf, and (3) a plain shell server will happily run a hallucinated `rm -rf`.
>
> SassyMCP is my fix for all three: one server, 278 tools across 39 modules, replacing 75+ individual servers. The parts that differ from just bundling servers:
>
> - **Smart loading:** all 278 tools exist, but only the groups you use load. Default footprint is ~5K tokens, not ~25K. A `context_estimate` tool measures what any server costs you.
> - **Safe-delete interception:** every delete-family command across every shell is intercepted, and its targets are moved to `_DELETE_/` instead of destroyed. It unwraps `cmd /c del`, decodes base64 PowerShell, and catches `[System.IO.File]::Delete`, `robocopy /MIR`, and truncate-by-redirect.
> - **Phone control with sensitive-context detection:** it drives Android through the UI accessibility tree, and it refuses to act on login, payment, or 2FA screens, handing control back to you.
>
> The GitHub module was a side quest. The official GitHub MCP server has SHA-handling bugs (github/github-mcp-server#2133), so I reimplemented it with correct blob SHA lookups, atomic multi-file commits through the Git Data API, and retry/backoff.
>
> It's **free for personal use, with every tool included and no crippled tier.** Commercial use needs a license. It's not open source: the source is public on GitHub to read, but the license is proprietary, and I'm happy to argue about that here. Windows gets a standalone exe, macOS a universal2 binary, and Linux is `pip install sassymcp`.
>
> Repo + download: https://github.com/sassyconsultingllc/SassyMCP
>
> I'd genuinely like feedback on the architecture, the safety model, and whether "one big server" is the wrong call.

---

## Comment battle plan (pre-written honest answers)

**"Paid + closed source? Hard pass."**
> Fair. To be precise about what it is: free for personal use with every tool included, and the source is public so you can read and audit what it does on your machine, but it's not OSI open source. Companies using it commercially pay for a license, and that's how I keep building it. If closed source is a dealbreaker for you, I get it. If it's the audit question, the code is right there.

**"What's the catch with free?"**
> No catch for individuals: all 278 tools, no gated groups, no telemetry, and it runs offline. Commercial use needs a license (there's a 30-day free evaluation for work use). There's also an optional supporter license if you just want to chip in. It unlocks nothing; it's a tip.

**"How do you make money then?"**
> Commercial licenses for people using it for work and companies running it across teams. Individuals stay free. Plain-English terms are in LICENSING.md in the repo.

**"What counts as commercial? I use it on my own laptop for my day job."**
> Then it's commercial. The rule of thumb is: if you're paid for the work it helps with, or an organization is using it, that's commercial use. Side projects, learning, and hobby stuff are free. You get 30 days to evaluate at work before you need a license.

**"Why one giant server instead of composable small ones? Isn't that the opposite of the Unix philosophy?"**
> Fair challenge. The composability is still there; it lives in the tool *groups*, loaded on demand, so you don't pay for what you don't use. What you gain by colocating: one config, one audit log, one safety interceptor that covers every shell entry point, and cross-module combos (e.g. "review this PR" touches GitHub + files + shell in one call). I'd argue the Unix-y win is the smart loader, not N processes.

**"Windows-only?"**
> No. Windows gets a standalone exe, macOS a universal2 binary, and on Linux it's `pip install sassymcp`. It's the same source on all three. A handful of things are Windows-native by nature (raw Registry, Defender); those report a clear message on other OSes and point to the native equivalent.

**"278 tools is absurd, that's context poison."**
> It would be if they all loaded, but they don't. The default is ~5K tokens. The `context_estimate` tool exists precisely because I got tired of guessing. Load `core` and you've got ~30 tools; opt into `android`/`vision`/`github_full` only when you need them.

**"One server with shell + delete + registry + phone is a huge attack surface."**
> Agreed that consolidation raises the stakes, which is why the safety model is the headline, not an afterthought: delete interception, protected paths (the source tree, config dir, and staging folder are refused by every guarded tool, with `resolve()` so `..` / symlinks / 8.3 names normalize), sensitive-context auto-block on phone actions, and a full audit log of every interception. Threat-model feedback genuinely welcome. What would you try first?

**"How is this different from Desktop Commander / Windows-MCP?"**
> It includes their surface (file/shell/desktop) and adds GitHub (80 tools), Android with sensitive-context detection, dynamic vision, memory, and the cross-cutting safe-delete layer, all under one config with smart loading. It's less "a better X" and more "you stop running X, Y, and Z separately."

**"Show me the context savings number, don't just claim it."**
> `sassy_context_estimate` prints it. Screenshot in the README; happy to paste a before/after in this thread for your specific group selection.

**"Is the GitHub-bug callout fair / still true?"**
> Link is github/github-mcp-server#2133. If it's been fixed since I wrote that, tell me and I'll correct the README. I don't want to misrepresent another project.

---

## Rules of engagement
- Reply to **everything**, especially the critical comments. HN respects a founder who engages the hard questions, not the easy ones.
- Never get defensive. "Fair," "you're right," and "here's the tradeoff" win the room.
- Never let a license question go unanswered or answer it vaguely. Precise and candid, every time.
- If someone finds a real bug, thank them and say when you'll fix it. That thread *is* your marketing.
- Don't ask for upvotes anywhere. Don't post the HN link to Reddit/X asking for votes; HN penalizes voting rings hard.
