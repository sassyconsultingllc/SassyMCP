# Show HN — paste-ready + comment battle plan

HN is the highest-variance channel: a hit is your biggest traffic day ever; a miss sinks quietly.
You win or lose **in the comments**. Post in the morning ET, then camp the thread for hours.
Be humble, technical, specific, and invite criticism. HN smells marketing instantly — no adjectives, no hype.

---

## Title (80 char max — pick one)

- `Show HN: SassyMCP – one MCP server that replaces 75+ (270 tools, one exe)`
- `Show HN: I collapsed 8 MCP servers into one exe with safe-delete interception`

First is clearer; second leads with the most novel feature. A/B in your head — go with the first unless you want to bet on the safe-delete angle.

---

## Body

> I build with agentic coding tools all day and kept ending up with 8+ MCP servers installed — filesystem, shell, GitHub, mobile, memory, OCR, etc. Three problems compounded: (1) tool definitions ate ~25K tokens of context before I did anything, (2) eight separate configs to keep in sync across Claude Desktop / Cursor / Windsurf, and (3) a plain shell server will happily run a hallucinated `rm -rf`.
>
> SassyMCP is my attempt to fix all three. It's one Windows exe with 270 tools across 35 modules, and it replaces 75+ individual servers. Three things that are actually different from just bundling servers:
>
> - **Smart loading:** all 270 tools exist but only the groups you use load. Default footprint is ~5K tokens, not ~25K. There's a `context_estimate` tool so you can measure what any server costs you.
> - **Safe-delete interception:** every delete-family command across every shell is intercepted and the targets moved to a `_DELETE_/` folder instead of being destroyed. It unwraps `cmd /c del`, decodes base64 PowerShell, catches `[System.IO.File]::Delete`, `robocopy /MIR`, and truncate-by-redirect (`> file`). An agent can't nuke your repo by accident.
> - **Phone control with sensitive-context detection:** it drives Android through the UI accessibility tree, but reads the tree first and refuses to act on login/payment/2FA screens, handing control back to you.
>
> The GitHub module was a side-quest: the official GitHub MCP server has SHA-handling bugs (github/github-mcp-server#2133), so I reimplemented it with correct blob SHA lookups, atomic multi-file commits via the Git Data API, and retry/backoff.
>
> It's a one-time purchase ($49 for Pro), not a subscription — the free tier is a complete daily driver. The core is MIT on GitHub. It's Windows-only today, which I'm happy to defend or be talked out of.
>
> Repo + free download: https://github.com/sassyconsultingllc/SassyMCP — site: https://sassyconsultingllc.com/pricing.html
>
> Would genuinely like feedback on the architecture, the safety model, and whether the "one big server" approach is wrong. Fire away.

---

## Comment battle plan (pre-written honest answers)

**"Why one giant server instead of composable small ones? Isn't that the opposite of the Unix philosophy?"**
> Fair challenge. The composability is still there — it's the tool *groups*, loaded on demand, so you're not paying for what you don't use. What you gain by colocating: one config, one audit log, one safety interceptor that covers every shell entry point, and cross-module combos (e.g. "review this PR" touches GitHub + files + shell in one call). I'd argue the Unix-y win is the smart loader, not N processes.

**"Windows-only is a dealbreaker."**
> Understood, and it's the most common ask. The Windows-specific surface (registry, Defender, event logs, autoruns) is genuinely Windows-native. The portable core — files, shell, GitHub, memory, editor — runs anywhere an MCP client runs; you can run it from source via `uv` on macOS/Linux for those groups today. A first-class mac/Linux build is the top of the roadmap if there's demand. Is the core enough for your use, or do you need the Windows surface specifically?

**"Paid + closed source? Hard pass."**
> The core is MIT on GitHub — you can read and audit it. The paid part is a license that unlocks heavy tool groups; the free tier is a real daily driver, not a crippled demo. I went one-time, not subscription, on purpose. If you'd never pay for a dev tool, the free tier is yours; if it saves you the eight-config headache, $49 once felt fair to me — open to being told otherwise.

**"270 tools is absurd, that's context poison."**
> It would be if they all loaded — they don't. Default is ~5K tokens. The `context_estimate` tool exists precisely because I got tired of guessing. Load `core` and you've got ~30 tools; opt into `android`/`vision`/`github_full` only when you need them.

**"One server with shell + delete + registry + phone is a huge attack surface."**
> Agreed that consolidation raises the stakes, which is why the safety model is the headline, not an afterthought: delete interception, protected paths (the source tree, config dir, and staging folder are refused by every guarded tool, with `resolve()` so `..` / symlinks / 8.3 names normalize), sensitive-context auto-block on phone actions, and a full audit log of every interception. Threat model feedback genuinely welcome — what would you try first?

**"How is this different from Desktop Commander / Windows-MCP?"**
> It includes their surface (file/shell/desktop) and adds GitHub (80 tools), Android with sensitive-context detection, dynamic vision, memory, self-mod, and the cross-cutting safe-delete layer — under one config with smart loading. It's less "a better X" and more "you stop running X, Y, and Z separately."

**"Show me the context savings number, don't just claim it."**
> `sassy_context_estimate` prints it. Screenshot in the gallery; happy to paste a before/after in this thread for your specific group selection.

**"Is the GitHub-bug callout fair / still true?"**
> Link is github/github-mcp-server#2133. If it's been fixed since I wrote that, tell me and I'll correct the README — I don't want to misrepresent another project.

---

## Rules of engagement
- Reply to **everything**, especially the critical comments — HN respects a founder who engages the hard questions, not the easy ones.
- Never get defensive. "Fair," "you're right," "here's the tradeoff" win the room.
- If someone finds a real bug, thank them and say when you'll fix it. That thread *is* your marketing.
- Don't ask for upvotes anywhere. Don't post the HN link to Reddit/X asking for votes — HN penalizes voting rings hard.
