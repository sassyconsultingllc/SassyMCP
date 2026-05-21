"""SassyMCP Combo Tools — multi-step workflows compiled into single calls.

The audit log shows certain tool sequences repeat dozens of times in normal
use:
  - PR review = show + diff + comments + checks (4 calls)
  - Phone observe = state + glance + watch (3 calls)
  - Codebase grep = search_files + read top N hits (N+1 calls)

Each combo here folds those into one invocation. That cuts conversational
round-trips 3-5x for these workflows — the model issues one tool call
instead of N, and gets a single coherent result instead of having to stitch
N intermediates back together.

Combos are intentionally NOT in `core` group — they layer on github_quick,
phone_screen, fileops respectively. The usage-score-boost loader will pull
this group in for users who actually use them; everyone else keeps the
context window cleaner.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("sassymcp.combos")


def _err(msg: str) -> str:
    return json.dumps({"error": msg})


def _ok(data: Any) -> str:
    if isinstance(data, str):
        return data
    return json.dumps(data, indent=2, default=str)


def _register_hooks():
    from sassymcp.modules._hooks import register_hook
    register_hook(
        name="combo_workflows",
        module="combos",
        description="Multi-step combo tools — pr_review, phone_observe, codebase_grep — when the user wants the whole workflow in one call.",
        triggers=[
            "review the PR", "review pull request", "look at PR",
            "observe the phone", "phone status full", "what's on the phone right now",
            "find references to", "where is", "find in the codebase",
            "grep the codebase", "search the project for",
        ],
        instructions="""
## Combo Tools — when one call beats N

These combos exist because three workflows show up over and over:

### PR Review (sassy_combo_pr_review)
Replaces: sassy_ghq_get(...) + raw diff fetch + raw comments fetch + raw
checks fetch (≥4 round-trips today).
Produces: a single JSON blob with PR metadata, diff (head SHA only — first
~5KB of patch), comments (top 20), check runs (status + conclusion).
Use this whenever the user says "review PR #X", "look at the latest PR",
"is PR X ready to merge". Don't use sassy_ghq_get for review tasks.

### Phone Observe (sassy_combo_phone_observe)
Replaces: phone_state + phone_glance + phone_ui (3 calls today).
Produces: foreground app, screen on/off, battery, WiFi, notification count,
a low-res grayscale screenshot, AND the parsed UI accessibility tree —
all in one shot. Use this on every "what's on the phone right now" or as
the FIRST call before any tap/swipe (so you have coords from the UI tree).

### Codebase Grep (sassy_combo_codebase_grep)
Replaces: sassy_search_files + N x sassy_read_file (N+1 round-trips).
Produces: the top K matching files, each with the matching line numbers AND
a small (10-line) context window around each hit. Use this when the user
asks "where is X defined", "find references to Y", "search the project
for Z". For full-file reads use sassy_read_file directly; this is for
ranked search with context.
""",
    )


try:
    _register_hooks()
except Exception:
    pass


def register(server):
    """Register combo workflow tools."""

    # ---------- sassy_combo_pr_review ----------

    @server.tool()
    async def sassy_combo_pr_review(owner: str, repo: str, pr: int) -> str:
        """Fetch PR metadata, diff, comments, and CI check status in one call.

        Replaces 4 separate sassy_ghq_* round-trips with a single combined
        result. Diff is truncated to the first 5000 chars (full diffs are
        usually wasteful in a review prompt — the LLM can ask for specific
        files later if needed).

        Returns JSON with shape:
          {
            "pr": {... metadata ...},
            "diff": "<first 5000 chars>",
            "diff_truncated": bool,
            "comments": [...],
            "check_runs": [...]
          }
        """
        try:
            from sassymcp.modules._github_client import get_client, GitHubAPIError
        except ImportError as e:
            return _err(f"github client unavailable: {e}")

        gh = get_client()

        async def _fetch(path: str, raw: bool = False, params: dict | None = None) -> Any:
            try:
                if raw:
                    # Raw diff endpoint returns text/plain
                    client = await gh._get_client()
                    headers = dict(gh._headers)
                    headers["Accept"] = "application/vnd.github.diff"
                    resp = await client.get(f"{gh.BASE}/{path}", headers=headers, params=params or {})
                    if resp.status_code >= 400:
                        return f"<<error fetching diff: {resp.status_code}>>"
                    return resp.text
                resp = await gh.get(path, params=params or {})
                return gh._check(resp, f"fetch {path}")
            except GitHubAPIError as e:
                return {"_fetch_error": str(e)}

        # Run the four fetches concurrently
        pr_data, diff_text, comments, checks = await asyncio.gather(
            _fetch(f"repos/{owner}/{repo}/pulls/{pr}"),
            _fetch(f"repos/{owner}/{repo}/pulls/{pr}", raw=True),
            _fetch(f"repos/{owner}/{repo}/issues/{pr}/comments", params={"per_page": 20}),
            _fetch(
                f"repos/{owner}/{repo}/commits/"
                f"{pr_data.get('head', {}).get('sha', 'HEAD') if isinstance(pr_data, dict) else 'HEAD'}/check-runs"
            ) if isinstance(pr_data, dict) and pr_data.get("head", {}).get("sha")
            else _fetch(f"repos/{owner}/{repo}/pulls/{pr}/commits"),
            return_exceptions=False,
        )

        diff_truncated = False
        if isinstance(diff_text, str) and len(diff_text) > 5000:
            diff_text = diff_text[:5000] + "\n\n<<truncated — full diff is " + str(len(diff_text)) + " chars>>"
            diff_truncated = True

        # Strip the bulky URL fields GitHub embeds in every response
        try:
            from sassymcp.modules._tool_loader import minify_github_response
            pr_data = minify_github_response(pr_data) if isinstance(pr_data, (dict, list)) else pr_data
            comments = minify_github_response(comments) if isinstance(comments, (dict, list)) else comments
            checks = minify_github_response(checks) if isinstance(checks, (dict, list)) else checks
        except Exception:
            pass

        return _ok({
            "pr": pr_data,
            "diff": diff_text,
            "diff_truncated": diff_truncated,
            "comments": comments,
            "check_runs": checks,
        })

    # ---------- sassy_combo_phone_observe ----------

    @server.tool()
    async def sassy_combo_phone_observe(device: str = "", include_glance: bool = True) -> str:
        """Phone state + UI tree + (optional) low-res screenshot in one call.

        Replaces sassy_phone_state + sassy_phone_glance + sassy_phone_ui
        (three round-trips) with one. Use as the first call when the user
        wants to know "what's on the phone right now" or BEFORE any tap/
        swipe/type interaction (you need the UI tree for coords anyway).

        Returns JSON with shape:
          {
            "state": {... foreground app, battery, WiFi, etc ...},
            "ui_tree": [... clickable elements with coords ...],
            "glance_b64": "<low-res grayscale jpeg>" (omitted if include_glance=False)
          }

        Set include_glance=False to skip the screenshot — saves bandwidth
        when you only need the structured UI tree.
        """
        try:
            from sassymcp.modules import phone_screen as _ps
        except ImportError as e:
            return _err(f"phone_screen module unavailable: {e}")

        # phone_screen exposes its tools via @server.tool decorator inside
        # register(), but the underlying logic lives in module-level helpers
        # named _phone_state(), _phone_ui(), _phone_glance() — call those
        # directly to avoid going through the MCP layer.
        async def _safe_call(fn_name: str, **kwargs):
            fn = getattr(_ps, fn_name, None)
            if fn is None:
                return {"_error": f"phone_screen.{fn_name} not found"}
            try:
                return await fn(**kwargs)
            except Exception as e:
                return {"_error": str(e)}

        # Run state + ui in parallel; glance is optional
        coros = [
            _safe_call("_phone_state", device=device),
            _safe_call("_phone_ui", device=device),
        ]
        if include_glance:
            coros.append(_safe_call("_phone_glance", device=device))

        results = await asyncio.gather(*coros, return_exceptions=False)

        state = results[0]
        ui_tree = results[1]
        glance_b64 = results[2] if include_glance else None

        return _ok({
            "state": state,
            "ui_tree": ui_tree,
            "glance_b64": glance_b64,
        })

    # ---------- sassy_combo_codebase_grep ----------

    @server.tool()
    async def sassy_combo_codebase_grep(
        pattern: str,
        path: str = ".",
        max_files: int = 5,
        context_lines: int = 5,
    ) -> str:
        """Ranked codebase search with context windows — one call replaces
        sassy_search_files + N x sassy_read_file.

        Returns JSON with shape:
          {
            "pattern": "...",
            "matches": [
              {"path": "...", "line": 42, "context": "<10 lines around line 42>"},
              ...
            ],
            "total_files_with_matches": <int>
          }

        max_files: how many files to actually read for context (default 5).
        context_lines: lines of context BEFORE and AFTER the matching line.

        For paths-only listings use sassy_search_files directly. For full
        file reads use sassy_read_file. This is for the common case:
        'where is X used'.
        """
        # fileops doesn't expose its search at module level; the tool is
        # registered as sassy_search_files inside its register(). Call
        # through the registered tool on the server instance instead.
        try:
            tool = None
            if hasattr(server, "_tool_manager"):
                tool_obj = server._tool_manager._tools.get("sassy_search_files")
                tool = getattr(tool_obj, "fn", None) if tool_obj else None
            if tool is None:
                return _err("sassy_search_files is not registered; combo grep needs the core fileops group.")
            search_result = await tool(
                path=path,
                pattern=pattern,
                search_type="content",
                max_results=200,
                context_lines=0,
            )
        except Exception as e:
            return _err(f"search failed: {e}")

        # sassy_search_files returns a newline-joined string ("path:line:
        # match"); convert to the dict shape the rest of this combo
        # expects.
        if isinstance(search_result, str) and not search_result.startswith("{"):
            matches_list = []
            for ln in search_result.splitlines():
                if not ln or ln.startswith("No matches"):
                    continue
                # "<path>:<line>: <text>"
                parts = ln.split(":", 2)
                if len(parts) >= 2:
                    file_path = parts[0]
                    try:
                        line_no = int(parts[1])
                    except ValueError:
                        continue
                    matches_list.append({"path": file_path, "line": line_no})
            search_result = json.dumps({"matches": matches_list})

        # search_result is a JSON string from the underlying tool; parse it.
        try:
            parsed = json.loads(search_result) if isinstance(search_result, str) else search_result
        except json.JSONDecodeError:
            return _err(f"search returned non-JSON: {search_result[:200]}")

        matches_raw = parsed.get("matches") if isinstance(parsed, dict) else parsed
        if not isinstance(matches_raw, list):
            matches_raw = []

        # Group matches by file, take top max_files
        per_file: dict[str, list[int]] = {}
        for m in matches_raw:
            if not isinstance(m, dict):
                continue
            file_path = m.get("path") or m.get("file") or ""
            line_no = m.get("line") or m.get("line_number")
            if not file_path:
                continue
            per_file.setdefault(file_path, [])
            if line_no:
                per_file[file_path].append(int(line_no))

        top_files = list(per_file.keys())[:max_files]

        # For each top file, read the lines around each match
        out_matches = []
        for file_path in top_files:
            try:
                content = Path(file_path).read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                out_matches.append({
                    "path": file_path,
                    "error": f"could not read: {e}",
                })
                continue

            lines = content.splitlines()
            for line_no in per_file[file_path][:5]:  # cap per-file
                lo = max(0, line_no - context_lines - 1)
                hi = min(len(lines), line_no + context_lines)
                snippet = "\n".join(
                    f"{i+1:5d}{'>' if (i+1) == line_no else ':'} {lines[i]}"
                    for i in range(lo, hi)
                )
                out_matches.append({
                    "path": file_path,
                    "line": line_no,
                    "context": snippet,
                })

        return _ok({
            "pattern": pattern,
            "matches": out_matches,
            "total_files_with_matches": len(per_file),
        })

    logger.info("combos: 3 combo tools registered (pr_review, phone_observe, codebase_grep)")
