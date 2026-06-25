"""SassyMCP Meta — Context estimation, tool usage stats, and tool group management.

Provides tools for introspecting the MCP server itself:
- Context window estimation (how much are tool defs eating?)
- Tool usage analytics (which tools get used, trends)
- Tool group enable/disable (dynamic tool loading)
- Response size analysis

These are always registered.
"""

import json
import logging
from typing import Any

logger = logging.getLogger("sassymcp.meta")

from sassymcp.modules._tool_loader import (
    get_tracker,
    get_group_info,
    get_default_modules,
    get_pruned_tools,
    get_group_for_tool,
    TOOL_GROUPS,
    estimate_tool_context_tokens,
    minify_github_response,
)
from sassymcp.modules._hooks import (
    get_all_hooks,
    get_hook,
    activate_hook,
    deactivate_hook,
    get_active_hooks,
    clear_active_hooks,
    suggest_hooks,
)


def register(server):
    """Register meta/introspection tools."""

    @server.tool()
    async def sassy_context_estimate() -> str:
        """Estimate current context window usage from MCP tool definitions.

        Shows: total estimated tokens, % of 200K window, heaviest tools.
        Use this to understand why your context is running low.
        """
        try:
            # Access the server's internal tool registry
            tools = []
            if hasattr(server, '_tool_manager'):
                mgr = server._tool_manager
                for name, tool in mgr._tools.items():
                    tools.append({
                        "name": name,
                        "description": tool.description or "",
                        "inputSchema": tool.parameters or {},
                    })

            if not tools:
                return json.dumps({
                    "note": "Could not access tool registry directly.",
                    "est_tool_count": "Check SASSYMCP_LOAD_ALL / SASSYMCP_GROUPS env",
                    "recommendation": "Use sassy_tool_groups to see loaded groups.",
                })

            result = estimate_tool_context_tokens(tools)
            result["recommendations"] = []

            pct = result["est_percent_of_200k"]
            if pct > 15:
                result["recommendations"].append(
                    f"Tool defs consume ~{pct}% of context. Disable unused groups."
                )
            if result["tool_count"] > 100:
                result["recommendations"].append(
                    f"{result['tool_count']} tools registered. Disable github_full if not needed."
                )

            return json.dumps(result, indent=2)

        except Exception as e:
            return json.dumps({"error": str(e)})

    @server.tool()
    async def sassy_tool_usage() -> str:
        """Show tool usage analytics: invocation counts, trends, top tools.

        Tracks which tools you actually use to inform smart loading.
        Data persists across sessions in ~/.sassymcp/tool_usage.json.
        """
        tracker = get_tracker()
        stats = tracker.get_stats()
        formatted_top = [
            {"tool": name, "score": round(score, 3)}
            for name, score in stats["top_10"]
        ]
        stats["top_10"] = formatted_top
        return json.dumps(stats, indent=2)

    @server.tool()
    async def sassy_tool_groups() -> str:
        """List available tool groups and their load status.

        Shows which groups are loaded, their tool counts, and descriptions.
        Use sassy_tool_group_toggle to enable/disable groups.
        """
        info = get_group_info()
        return json.dumps(info, indent=2)

    @server.tool()
    async def sassy_self_check() -> str:
        """Proprioceptive self-check: reconcile the declared module manifest
        against the live tool registry and surface any module that FAILED to
        import — the silent drop _load_modules()'s try/except otherwise hides.

        Answers "am I whole?" with a real readout instead of phantom limbs.
        A tool can be legitimately absent for three reasons, all reported
        separately so none is mistaken for a regression:
          * dormant   — in an on-demand group (always_load=False) not yet
                         toggled on or usage-boosted. Absent BY DESIGN; appears
                         after sassy_tool_group_toggle or a usage boost.
          * pruned    — low usage score dropped it from the default load
                         (see _tool_loader.get_pruned_tools).
          * unsupported — module import raises on THIS platform, but it was
                         never in the default load, so the failure is non-fatal.
        The one real regression is:
          * BROKEN    — an expected-loaded module whose import raises, so its
                         tools never register and the loss is silent.

        Every BROKEN module is logged at ERROR (server log / audit substrate)
        so the amputation is loud, not silent. verdict='whole' == every
        expected module imports cleanly.
        """
        import importlib
        import traceback

        live_tools: set[str] = set()
        if hasattr(server, "_tool_manager"):
            live_tools = set(server._tool_manager._tools.keys())

        try:
            default_modules = set(get_default_modules())
        except Exception:
            default_modules = set()
        try:
            pruned_tools = get_pruned_tools()
        except Exception:
            pruned_tools = set()
        try:
            from sassymcp import __version__ as _ver
        except Exception:
            _ver = "unknown"

        broken: list[dict] = []
        dormant: list[str] = []
        report: dict[str, Any] = {}

        for group_name, ginfo in TOOL_GROUPS.items():
            for mod in ginfo["modules"]:
                expected_loaded = mod in default_modules
                entry: dict[str, Any] = {
                    "group": group_name,
                    "always_load": ginfo["always_load"],
                    "expected_in_default_load": expected_loaded,
                }
                # import != register, but an import that RAISES is exactly the
                # silent drop the loader hides — this is where we catch it.
                # Re-importing an already-loaded module is a cheap no-op.
                try:
                    importlib.import_module(f"sassymcp.modules.{mod}")
                    entry["import"] = "ok"
                    if not expected_loaded:
                        dormant.append(mod)
                except Exception as e:
                    entry["import"] = "FAILED"
                    entry["error"] = f"{type(e).__name__}: {e}"
                    entry["traceback"] = traceback.format_exc().splitlines()[-3:]
                    if expected_loaded:
                        broken.append({
                            "module": mod, "group": group_name, "error": entry["error"],
                        })
                    else:
                        entry["note"] = (
                            "unsupported/optional — not in default load, "
                            "failure is non-fatal (would surface on toggle)"
                        )
                report[mod] = entry

        for b in broken:
            logger.error(
                f"self_check: module '{b['module']}' (group {b['group']}) failed "
                f"to import — its tools are NOT registered: {b['error']}"
            )

        import sys as _sys
        import os as _os
        # Runtime self-identification so ANY client can tell which instance it
        # is talking to (the opaque-UUID namespace problem solves itself if
        # each server says who it is). 'frozen' == packaged PyInstaller build;
        # 'source' == running from a checkout (self-modification works here).
        runtime = "frozen" if (getattr(_sys, "frozen", False) or hasattr(_sys, "_MEIPASS")) else "source"
        return json.dumps({
            "verdict": "whole" if not broken else "DEGRADED",
            "version": _ver,
            "runtime": runtime,
            "package_root": str(getattr(_sys, "_MEIPASS", "") or ""),
            "pid": _os.getpid(),
            "live_tool_count": len(live_tools),
            "modules_total": sum(len(g["modules"]) for g in TOOL_GROUPS.values()),
            "broken": broken,
            "dormant_by_design": sorted(set(dormant)),
            "pruned_low_usage": sorted(pruned_tools),
            "modules": report,
            "hint": (
                "verdict=whole means every expected module imports. "
                "dormant_by_design tools are absent by the loader's smart-load "
                "design, not a fault — they appear after sassy_tool_group_toggle "
                "or once usage boosts them."
            ),
        }, indent=2, default=str)

    @server.tool()
    async def sassy_tool_catalog(group: str = "", query: str = "") -> str:
        """Catalog every registered tool: name, one-line purpose, group.

        Client-agnostic capability map for ANY MCP wrapper — enumerate what
        this server can actually do without loading each tool's schema.
        Derived live from the registry, so it never drifts from reality the
        way hand-written capability prose does (the drift that makes absent
        lazy-loaded tools look like missing ones).

        group: filter to one tool group (see sassy_tool_groups). Empty = all.
        query: case-insensitive substring match on tool name or purpose.
        """
        rows = []
        if hasattr(server, "_tool_manager"):
            for name, tool in server._tool_manager._tools.items():
                desc = (getattr(tool, "description", "") or "").strip()
                purpose = desc.splitlines()[0] if desc else ""
                g = get_group_for_tool(name) or "?"
                if group and g != group:
                    continue
                if query:
                    q = query.lower()
                    if q not in name.lower() and q not in purpose.lower():
                        continue
                rows.append((g, name, purpose))
        rows.sort(key=lambda r: (r[0], r[1]))
        by_group: dict[str, Any] = {}
        for g, name, purpose in rows:
            by_group.setdefault(g, []).append({"name": name, "purpose": purpose})
        return json.dumps({
            "total": len(rows),
            "group_counts": {g: len(v) for g, v in by_group.items()},
            "filtered_by": {"group": group or None, "query": query or None},
            "tools": by_group,
        }, indent=2, default=str)

    @server.tool()
    async def sassy_tool_group_toggle(group: str, enable: bool = True) -> str:
        """Enable or disable a tool group. Emits tools/list_changed notification.

        NOTE: clients vary. MCP clients that handle tools/list_changed
        (Claude Code, Cursor, etc.) refresh automatically; clients that
        don't (Claude Desktop today) require a manual server restart.

        group: core, android, system, github_quick, github_full, v020, persona
        enable: True to load, False to unload
        """
        if group not in TOOL_GROUPS:
            return json.dumps({
                "error": f"Unknown group '{group}'",
                "valid_groups": list(TOOL_GROUPS.keys()),
            })

        TOOL_GROUPS[group]["always_load"] = enable
        action = "enabled" if enable else "disabled"

        notified = False
        try:
            if hasattr(server, '_session') and server._session:
                await server._session.send_notification(
                    "notifications/tools/list_changed", {}
                )
                notified = True
        except Exception:
            pass

        return json.dumps({
            "status": f"Group '{group}' {action}",
            "modules": TOOL_GROUPS[group]["modules"],
            "notification_sent": notified,
            "note": "Restart the server if your MCP client doesn't handle tools/list_changed (e.g. Claude Desktop today)" if not notified else "Client should re-fetch tool list",
        })

    @server.tool()
    async def sassy_minify_test(sample_json: str) -> str:
        """Test the GitHub response minifier on sample JSON.

        Paste a GitHub API response and see how much it shrinks.
        Shows before/after token estimates.
        """
        try:
            data = json.loads(sample_json)
            minified = minify_github_response(data)

            orig = json.dumps(data, separators=(",", ":"))
            mini = json.dumps(minified, separators=(",", ":"))

            savings_pct = round((1 - len(mini) / max(len(orig), 1)) * 100, 1)

            return json.dumps({
                "original_chars": len(orig),
                "minified_chars": len(mini),
                "savings_percent": savings_pct,
                "original_est_tokens": len(orig) // 4,
                "minified_est_tokens": len(mini) // 4,
                "tokens_saved": (len(orig) - len(mini)) // 4,
                "minified_data": minified,
            }, indent=2)

        except json.JSONDecodeError as e:
            return json.dumps({"error": f"Invalid JSON: {e}"})

    # ── Hook Management ──────────────────────────────────────────

    @server.tool()
    async def sassy_hooks_list() -> str:
        """List all available operational hooks.

        Hooks are expert playbooks that teach the AI HOW to approach a task.
        When activated, the AI gets pre-loaded domain expertise — the right
        "lens" for the job. Use sassy_hooks_activate to load one.
        """
        hooks = get_all_hooks()
        return json.dumps({
            "hooks": hooks,
            "count": len(hooks),
            "active": [h["name"] for h in get_active_hooks()],
            "hint": "Call sassy_hooks_activate with a hook name to load its playbook.",
        }, indent=2)

    @server.tool()
    async def sassy_hooks_activate(hook_name: str) -> str:
        """Activate an operational hook. Returns the full expert playbook.

        The playbook contains step-by-step instructions for HOW to approach
        the task — which tools to use, in what order, what to look for,
        and what NOT to do. Follow it.

        Use sassy_hooks_list to see available hooks.
        """
        hook = activate_hook(hook_name)
        if not hook:
            # Try fuzzy match
            all_hooks = get_all_hooks()
            suggestions = [name for name in all_hooks if hook_name.lower() in name.lower()]
            return json.dumps({
                "error": f"Hook '{hook_name}' not found",
                "available": list(all_hooks.keys()),
                "suggestions": suggestions,
            })

        return json.dumps({
            "activated": hook["name"],
            "module": hook["module"],
            "description": hook["description"],
            "instructions": hook["instructions"],
            "note": "Follow the playbook above. It was written by experts for this exact task.",
        }, indent=2)

    @server.tool()
    async def sassy_hooks_deactivate(hook_name: str = "") -> str:
        """Deactivate a hook or all hooks.

        hook_name: specific hook to deactivate, or empty to clear all.
        """
        if not hook_name:
            clear_active_hooks()
            return json.dumps({"status": "all hooks deactivated"})

        if deactivate_hook(hook_name):
            return json.dumps({"deactivated": hook_name})
        return json.dumps({"error": f"Hook '{hook_name}' was not active"})

    @server.tool()
    async def sassy_hooks_suggest(user_text: str) -> str:
        """Suggest hooks based on what the user is trying to do.

        Pass the user's request text. Returns matching hooks ranked by relevance.
        The AI should call this when it's unsure which hook to use, or
        proactively when the user's request matches a known domain.
        """
        matches = suggest_hooks(user_text)
        if not matches:
            return json.dumps({
                "suggestions": [],
                "note": "No hooks match this request. Proceeding without a playbook.",
            })

        return json.dumps({
            "suggestions": matches,
            "top_match": matches[0]["name"],
            "hint": f"Consider activating '{matches[0]['name']}' — {matches[0]['description']}",
        }, indent=2)
