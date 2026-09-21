<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
   CodeMark: SCLLC1-Projects-GRH6AIHESXCD
-->
# MCP Directory Submissions — the always-on engine

This is the durable revenue engine, not the launch spike. Each directory reaches an audience the
others don't — list on **all** of them. Do this BEFORE the social launch so the spike has somewhere
to land and the listings start accruing search traffic immediately.

**Prerequisite (gates everything here):** the GitHub repo must be **public**. Glama, the official
registry, and the awesome-list index from GitHub. If `github.com/sassyconsultingllc/SassyMCP` is
private, publish at least a public free-tier repo with the README. Verify before submitting.

**The free tier is what you list everywhere.** Directories are discovery for the free server; the
paid unlock happens in-product via license key. Don't try to list "Pro" — list SassyMCP (free),
and let the upgrade path live inside the tool.

---

## Reusable listing metadata (paste into each form)

```
Name:           SassyMCP
One-liner:      One MCP server that replaces 75+ — 274 tools in a single exe, smart-loaded.
Description:    SassyMCP collapses 75+ individual MCP servers into one 34 MB exe with 274 tools
                across files, shell, full GitHub API, Android control, dynamic vision, memory,
                and security audit. A smart loader keeps tool-definition context under ~5K tokens.
                Includes safe-delete interception (agents can't destroy files) and phone control
                with sensitive-context auto-block. Free tier is a full daily driver. Works with
                any MCP client. Install via the SHA256-pinned .mcpb from GitHub Releases (preferred)
                or `pip install sassymcp` — do not use unverified curl|exe install commands.
Categories:     Developer Tools, Automation, GitHub, Security, Productivity
Transport:      stdio (primary), HTTP/SSE, HTTPS
Tool count:     274 (36 modules, 18 groups)
Platform:       Windows 10/11
License:        Proprietary (all rights reserved)
Repo:           https://github.com/sassyconsultingllc/SassyMCP
Homepage:       https://sassyconsultingllc.com/store   (NOT /pricing.html — that 404s as of 2026-07-15)
Icon:           (use the bolt logo — see 08-assets-to-capture.md)
Clients tested: Claude Desktop, Cursor, Windsurf, VS Code Copilot, Cline, Continue, Zed, Grok Desktop
```

---

## The list (submit to all)

### 1. Official MCP Registry (modelcontextprotocol.io) — highest credibility
- **What:** the canonical registry; many clients/directories pull from it.
- **How:** install the `mcp-publisher` CLI → authenticate via GitHub device login → publish a `server.json`. It hosts *metadata*, not artifacts, so you must publish the package first.
  - SassyMCP has `pyproject.toml` → publish the free tier to **PyPI** (`pip`/`uv` installable), or use the **MCPB/DXT** package type (you already ship a `.dxt`). Either satisfies the artifact requirement.
  - `mcpName` must start with `io.github.sassyconsultingllc/` for GitHub-based ownership.
- **Docs:** https://modelcontextprotocol.io/registry/quickstart and https://github.com/modelcontextprotocol/registry/blob/main/docs/guides/publishing/publish-server.md
- **Effort:** medium (needs a published package). Highest payoff — do it.

### 2. Smithery (smithery.ai) — the "Docker Hub of MCP", 7,000+ servers
- **How:** `smithery mcp publish "<server-url-or-npm>" -n sassyconsultingllc/sassymcp` via the Smithery CLI, or submit through the web dashboard. Needs a publisher account + a manifest (name, description, tools, auth method) and a working server URL or package.
- **Note:** Smithery leans toward remotely-runnable servers. You can list with the GitHub repo + free install; if you want hosted, point it at an HTTP endpoint (you already support HTTP/tunnel).
- **Docs:** https://smithery.ai/docs/build
- **Effort:** medium. High traffic — do it.

### 3. Glama (glama.ai/mcp) — 21k+ servers, auto-indexed daily
- **How:** Glama auto-indexes public GitHub repos. Make sure the repo is public with a strong README (it is), then **claim** your listing on Glama to control the metadata.
- **Effort:** low (mostly automatic). Do it — just verify/claim.

### 4. mcp.so — 19k+ community servers
- **How:** click **Submit** on mcp.so, or open an issue on their GitHub. Paste the metadata block.
- **Effort:** low. Do it.

### 5. PulseMCP (pulsemcp.com) — tracks weekly visitors, official-vs-community
- **How:** use their submit form. Mark as community; they track popularity, so good reviews/usage compound your ranking.
- **Effort:** low. Do it.

### 6. MCP Market (mcpmarket.com) — 10k+ servers, 23+ categories
- **How:** submit via their site form; choose Developer Tools + Security categories.
- **Effort:** low. Do it.

### 7. awesome-mcp-servers (GitHub, punkpeye/awesome-mcp-servers) — the canonical awesome list
- **How:** open a **PR** adding SassyMCP under the right category (likely "Developer Tools" / "Command Line" / a Windows section). Follow their contribution format exactly (name, link, one-line desc, language/scope badges). Make sure the repo has clean docs first.
- **Effort:** low-medium (PR review). High SEO value — this list ranks for "MCP servers" searches.

### 8. Client-specific directories (bonus reach)
- **Cline MCP Marketplace** — submit via the Cline marketplace repo/process (their docs).
- **Cursor / Windsurf community MCP lists** — add where they accept submissions.
- **VS Code / Continue** community indexes if present.
- **Effort:** low each; do the ones that take 5 minutes.

---

## Submission order (one sitting)

1. Verify repo public + README clean. → 2. Glama (claim) → 3. mcp.so → 4. PulseMCP → 5. MCP Market
→ 6. Smithery → 7. Official registry (publish package first) → 8. awesome-list PR → 9. client lists.

Then record where each listing lives so you can update them on each release.

## VS Code Marketplace (not a directory — arming notes)

The extension publisher id is `sassyconsultingllc`. `vsce package` already
works (CI `build-vsix` + local dry-run). Live `vsce publish` is gated in
`.github/workflows/release.yml` job `publish-vsix`:

1. Azure DevOps / Marketplace: create publisher **sassyconsultingllc** if it
   does not exist.
2. GitHub repo **secret** `VSCE_PAT` — a Marketplace PAT with Acquire +
   Publish. Never commit the token.
3. GitHub repo **variable** `PUBLISH_VSIX` = `true`.
4. Push a **new** version tag (do not re-tag `v1.15.1`). The job only runs on
   `refs/tags/v*.*.*` **and** `vars.PUBLISH_VSIX == 'true'`.
   `workflow_dispatch` builds artifacts but does **not** publish PyPI, the
   MCP registry, or the Marketplace.

Until that listing exists, point users at
https://github.com/sassyconsultingllc/SassyMCP/releases/latest
(`sassymcp-1.15.1.vsix`). Do not advertise
`marketplace.visualstudio.com/items?itemName=sassyconsultingllc.sassymcp`
as live — gallery search returns 0 hits.

## Submitted so far

| Directory | Date | Status | Where |
|---|---|---|---|
| Official MCP Registry | 2026-09-14 | Live 1.15.1 | https://registry.modelcontextprotocol.io/v0.1/servers/io.github.sassyconsultingllc%2Fsassymcp/versions/latest |
| PyPI `sassymcp` | 2026-09-14 | Live 1.15.1 (`info.version`) | https://pypi.org/project/sassymcp/1.15.1/ — local `pip show` may still report 1.14.3 until upgraded |
| GitHub Release | 2026-09-14 | Live v1.15.1 | exe, mcpb, dxt, vsix, macos |
| Glama | auto-index | Listed, README was stale at 1.14.4 until sidecar sync | https://glama.ai/mcp/servers/sassyconsultingllc/SassyMCP — claim in Glama UI (login) |
| CuratedMCP (curatedmcp.com) | 2026-07-29 | Stale at v1.14.2; human resubmit 1.15.1 mcpb | SHA256 `06366a12…62780` on `sassymcp-v1.15.1.mcpb`; tip → https://sassyconsultingllc.com/store#sassymcp |
| Smithery / mcp.so / PulseMCP / awesome-mcp-servers / Open VSX / VS Marketplace | 2026-09-16 | Not listed; need human login or PAT | See Marketplace arming above; awesome-list is a PR to punkpeye/awesome-mcp-servers |

## Maintenance (what directories reward)
Per the 2026 directory feedback: they surface **last-commit date, production-readiness, client
compatibility, and cold-start performance.** So: keep commits recent, keep the README's "tested
clients" matrix current, and note the single-exe (no cold-start pip install) as an advantage.

---

**Sources:**
- [Official MCP Registry quickstart](https://modelcontextprotocol.io/registry/quickstart)
- [Registry publishing guide](https://github.com/modelcontextprotocol/registry/blob/main/docs/guides/publishing/publish-server.md)
- [Smithery docs](https://smithery.ai/docs/build) · [Smithery CLI](https://github.com/smithery-ai/cli)
- [MCP server directories list — DynoMapper](https://dynomapper.com/blog/ai/mcp-server-directories/)
- [MCP registries 2026 — RoxyAPI](https://roxyapi.com/blogs/mcp-registries-where-to-list-your-server-2026)
- [MCP Market](https://mcpmarket.com/) · [Best MCP registries — TrueFoundry](https://www.truefoundry.com/blog/best-mcp-registries)
