# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-I34DFDJS4VI3
"""Offline module — graceful degradation and local-model fallback.

When the link drops, three things have to happen and none of them should
require the operator to guess:

1. **Detect it fast and honestly.** `sassy_offline_status` gives the probed
   link state (including the link-up-but-DNS-dead case), not a guess.
2. **Say what still works.** `sassy_offline_commands` emits the offline-safe
   command listing straight from the live tool registry, so it can never
   drift from reality the way a hand-written "offline mode" doc does. That
   listing does double duty: it is the degraded-capability report for a human
   AND the small, curated tool menu a 14B local model can actually hold in
   context — the full ~250-tool surface is not something Hermes should see.
3. **Hand the work to a local model.** `sassy_offline_handoff` writes the
   handoff to the sassy brain, mirrors it onto the crosslink channel, and
   (optionally) starts hermes_node.py against the loopback inference server.
   Hermes then drives the SAME SassyMCP functions — shell gate, audit,
   memory — so behavior offline is identical to online by construction.

The gate that produces the fast refusals lives in `sassymcp._netstate` and is
applied once in server.audit_tool, so it covers every tool without any module
having to opt in.
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from sassymcp import _netstate
from sassymcp.modules._tool_loader import TOOL_GROUPS, get_group_for_tool

logger = logging.getLogger("sassymcp.offline")

# Groups worth loading when a local model is driving: everything that works
# with no egress and earns its context cost. Fed to SASSYMCP_GROUPS.
OFFLINE_GROUPS = ("meta", "core", "infrastructure", "memory", "persona", "system")


def _register_hooks():
    from sassymcp.modules._hooks import register_hook
    register_hook(
        name="offline_fallback",
        module="offline",
        description="Internet is down — degrade gracefully and hand off to a local model.",
        triggers=[
            "offline", "no internet", "internet is down", "internet's down",
            "lost connection", "connection lost", "wifi is down", "wifi down",
            "no wifi", "no network", "network is down", "airplane mode",
            "dns is broken", "captive portal", "local model", "local llm",
            "ollama", "hermes", "work without internet", "disconnected",
        ],
        instructions="""
## Offline Fallback Playbook

Triggered when the link is down, degraded (DNS dead), or the operator wants to
keep working without egress.

### Order of operations
1. `sassy_offline_status` — one call gives the probed link state, which
   loopback inference servers are up and what models they have pulled, and the
   full split of tools that still work vs. tools that cannot.
2. `sassy_offline_commands` — the compact offline-safe command listing. Feed
   THIS to a local model, not the full catalog; it is sized for a 7-14B
   context window.
3. `sassy_offline_handoff task="<what you were doing>"` — writes the handoff
   to memory + crosslink and returns the exact hermes_node launch line. Pass
   `start_node=true` to start it in a persistent session immediately.

### What still works with no egress
Everything file, shell, editor, session, memory, persona, audit, clipboard,
process, eventlog, registry/forensics, ADB over USB, screen/vision, and state.
That is the large majority of the tool surface — offline is a degraded mode,
not a stopped one.

### What does not
GitHub (`sassy_gh_*`, `sassy_ghq_*`), web inspection (`sassy_url_*`), remote
HTTP (`sassy_http*`), updates (`sassy_update_*`), DNS/traceroute/cert checks,
and the GitHub leg of `sassy_combo_pr_review`. Each returns a structured
`{"error":"offline", ...}` immediately with a named substitute instead of
hanging on a timeout.

### Don't
- Don't tell the operator their license is at risk. License checks are
  fail-open on network error by design; an offline license stays valid.
- Don't retry a gated tool in a loop. The gate re-probes on its own cadence
  (8s while offline); one `sassy_offline_status` tells you when it's back.
- Don't dump the full tool catalog into a local model's context.
""",
    )


try:
    _register_hooks()
except Exception:
    pass


def _repo_root() -> Path:
    """Directory holding hermes_node.py. Absent in a frozen build."""
    return Path(__file__).resolve().parents[2]


def _hermes_paths() -> dict[str, Any]:
    root = _repo_root()
    node = root / "hermes_node.py"
    venv_py = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    interpreter = venv_py if venv_py.exists() else Path(sys.executable)
    return {
        "node_script": str(node),
        "node_exists": node.exists(),
        "interpreter": str(interpreter),
        "frozen": bool(getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS")),
    }


# ── In-process tool surface for a local model ─────────────────────────
#
# The offline driver (hermes_node.py) is a SEPARATE process from the MCP
# server — it has no MCP transport and no client. It gets the real tool
# surface by building the same registry in-process: `_load_modules()` fills
# the FastMCP instance, `_wrap_all_tools()` puts the audit + security + rate
# limit + offline gate wrapper back on every fn. Same code path the served
# tools take, so a local model cannot reach a less-guarded version of a tool
# than Claude can.

_registry_built = False


def build_local_registry(groups: tuple[str, ...] = OFFLINE_GROUPS) -> dict[str, Any]:
    """Populate and return the in-process tool registry {name: Tool}.

    Sets SASSYMCP_GROUPS to the offline set before loading so a 7-14B model
    gets a menu it can hold, not the full surface. Idempotent.
    """
    global _registry_built
    from sassymcp import server as _srv

    if not _registry_built:
        os.environ.setdefault("SASSYMCP_GROUPS", ",".join(groups))
        _srv._load_modules()
        _srv._wrap_all_tools()
        _registry_built = True
    return dict(_srv.mcp._tool_manager._tools)


def openai_tool_specs(groups: tuple[str, ...] = OFFLINE_GROUPS,
                      include_lan: bool = True) -> list[dict[str, Any]]:
    """OpenAI-compatible `tools` array for every offline-usable tool.

    Ollama, llama.cpp, LM Studio and vLLM all accept this shape on
    /v1/chat/completions, which is why the local driver can use one schema
    for whichever backend happens to be up.
    """
    tools = build_local_registry(groups)
    specs: list[dict[str, Any]] = []
    allowed = {"none", "lan"} if include_lan else {"none"}
    for name, tool in tools.items():
        req = _netstate.classify_tool(name, get_group_for_tool(name))
        if req not in allowed:
            continue
        desc = (getattr(tool, "description", "") or "").strip()
        specs.append({
            "type": "function",
            "function": {
                "name": name,
                # One line only: full docstrings blow a small model's budget.
                "description": (desc.splitlines()[0] if desc else name)[:300],
                "parameters": getattr(tool, "parameters", None) or {
                    "type": "object", "properties": {},
                },
            },
        })
    specs.sort(key=lambda s: s["function"]["name"])
    return specs


async def dispatch(name: str, arguments: dict[str, Any]) -> str:
    """Invoke a tool by name for the local driver. Returns its string result.

    Goes through the wrapped fn, so the security gate, delete interceptor,
    audit log, rate limiter and offline gate all apply exactly as they do for
    a remote client.
    """
    tools = build_local_registry()
    tool = tools.get(name)
    if tool is None:
        return json.dumps({
            "error": f"Unknown tool '{name}'",
            "hint": "Only tools in the offline listing exist in this process.",
        })
    req = _netstate.classify_tool(name, get_group_for_tool(name))
    if req == "internet" and _netstate.confirmed_offline():
        return json.dumps(_netstate.gate(name, get_group_for_tool(name), arguments)
                          or {"error": "offline", "tool": name})
    try:
        result = tool.fn(**(arguments or {}))
        if hasattr(result, "__await__"):
            result = await result
        return result if isinstance(result, str) else json.dumps(result, default=str)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}", "tool": name})


def _classified_tools(server) -> dict[str, list[dict[str, str]]]:
    """Split every live tool into none / lan / internet buckets."""
    buckets: dict[str, list[dict[str, str]]] = {"none": [], "lan": [], "internet": []}
    if not hasattr(server, "_tool_manager"):
        return buckets
    for name, tool in server._tool_manager._tools.items():
        group = get_group_for_tool(name) or "?"
        req = _netstate.classify_tool(name, group)
        desc = (getattr(tool, "description", "") or "").strip()
        buckets[req].append({
            "name": name,
            "group": group,
            "purpose": desc.splitlines()[0] if desc else "",
        })
    for v in buckets.values():
        v.sort(key=lambda r: (r["group"], r["name"]))
    return buckets


def register(server):
    """Register offline/local-fallback tools."""

    @server.tool()
    async def sassy_offline_status(probe: bool = True) -> str:
        """Link state, local-model readiness, and the full offline capability split.

        One call answers: am I online, is DNS actually resolving, which
        loopback inference servers are up and what have they pulled, is a
        fallback model ready, and exactly which tools still work.

        probe: True runs a fresh ~1-2s reachability probe. False reads the
               cached verdict instantly (what the gate itself uses).
        """
        st = _netstate.refresh_now() if probe else _netstate.snapshot()
        fb = _netstate.fallback_ready()
        backends = _netstate.local_models()
        buckets = _classified_tools(server)
        hermes = _hermes_paths()

        degraded = st["link"] in ("offline", "dns_only")
        return json.dumps({
            "link": {
                "state": st["link"],
                "online": st["online"],
                "dns_resolving": st["dns"],
                "anchors": st["anchors"],
                "checked_age_seconds": st["age_seconds"],
                "probes_run": st.get("probe_count", 0),
                "last_tool_failure": st.get("last_tool_failure"),
            },
            "gate": {
                "mode": _netstate.gate_mode(),
                "active": degraded and _netstate.gate_mode() == "auto",
                "effect": (
                    "Internet-only tools return {'error':'offline'} immediately "
                    "with a substitute instead of timing out."
                ),
                "disable_with": "SASSYMCP_OFFLINE_GATE=off",
            },
            "local_models": {
                "backends": backends,
                "fallback_ready": fb["ready"],
                "chosen": fb["chosen"],
                "reason": fb["reason"],
            },
            "hermes_node": hermes,
            "capability": {
                "offline_safe": len(buckets["none"]),
                "lan_only": len(buckets["lan"]),
                "needs_internet": len(buckets["internet"]),
                "unavailable_now": [
                    {"tool": t["name"],
                     "alternative": _netstate.offline_alternative(t["name"])}
                    for t in buckets["internet"]
                ] if degraded else [],
            },
            "recommended_env_for_local_model": (
                "SASSYMCP_GROUPS=" + ",".join(OFFLINE_GROUPS)
            ),
            "next": (
                "sassy_offline_commands for the command listing to hand a local "
                "model; sassy_offline_handoff to move the task to Hermes."
                if degraded else "Link is healthy — nothing to degrade."
            ),
        }, indent=2, default=str)

    @server.tool()
    async def sassy_offline_commands(group: str = "", verbose: bool = False) -> str:
        """The offline-safe command listing — every tool that works with no egress.

        Derived live from the tool registry, so it never drifts. Sized to be
        pasted into a local model's system prompt: names + one-line purposes,
        grouped, with the network-dependent tools listed separately as
        explicitly unavailable so the model doesn't invent calls to them.

        group: filter to one tool group (see sassy_tool_groups). Empty = all.
        verbose: include one-line purposes. False returns names only —
                 roughly a quarter of the tokens, which matters on a 14B.
        """
        buckets = _classified_tools(server)
        safe = buckets["none"] + buckets["lan"]
        if group:
            safe = [t for t in safe if t["group"] == group]

        by_group: dict[str, list[Any]] = {}
        for t in safe:
            entry = {"name": t["name"], "purpose": t["purpose"]} if verbose else t["name"]
            by_group.setdefault(t["group"], []).append(entry)

        return json.dumps({
            "usable_offline": sum(len(v) for v in by_group.values()),
            "note": (
                "LAN tools (linux SSH, wifi, port scan, adb wifi) are included: "
                "they need a network, not the internet. If the LAN is also down "
                "they will fail on their own timeout — they are not gated."
            ),
            "commands": by_group,
            "unavailable_offline": sorted(t["name"] for t in buckets["internet"]),
            "system_prompt_snippet": (
                "You are running with no internet. Only the tools listed under "
                "`commands` exist. Do not call anything under "
                "`unavailable_offline` — those return an offline error. "
                "Shell, files, editor, sessions, memory, and audit all work "
                "normally; commit locally and push when the link returns."
            ),
        }, indent=2, default=str)

    @server.tool()
    async def sassy_offline_handoff(
        task: str,
        channel: str = "joint",
        next_steps: str = "",
        start_node: bool = False,
    ) -> str:
        """Hand the current task to the local model and keep working.

        Writes a structured handoff to the sassy brain (key
        `task_offline_<channel>_state`), mirrors it onto the crosslink channel
        Hermes polls, and returns the exact launch line. With start_node=true
        it starts hermes_node.py in a persistent SassyMCP session so the
        handoff is live, not just written.

        task: what was being worked on.
        channel: crosslink channel Hermes listens on (default 'joint').
        next_steps: newline- or semicolon-separated ordered steps.
        start_node: True to actually start the local node.
        """
        fb = _netstate.fallback_ready()
        hermes = _hermes_paths()
        st = _netstate.snapshot()
        steps = [s.strip() for s in next_steps.replace("\n", ";").split(";") if s.strip()]

        payload = {
            "task": task,
            "status": "handed-off-offline",
            "link_state": st["link"],
            "next_steps": steps,
            "model": (fb["chosen"] or {}).get("model"),
            "backend": (fb["chosen"] or {}).get("backend"),
        }

        wrote_memory = False
        try:
            from sassymcp.modules.memory import MemoryStore
            MemoryStore().remember(
                f"task_offline_{channel}_state",
                json.dumps(payload),
                tags=["task-active", "offline"],
                priority="high",
                project="ops",
            )
            wrote_memory = True
        except Exception as e:
            logger.warning(f"offline handoff: memory write failed: {e}")

        posted = False
        try:
            from sassymcp.modules.crosslink import _post_message
            _post_message(
                "offline-handoff", channel,
                "turn=1 from=sassymcp [OFFLINE HANDOFF]\n" + json.dumps(payload, indent=2),
            )
            posted = True
        except Exception as e:
            logger.warning(f"offline handoff: crosslink post failed: {e}")

        env_line = " ".join([
            f'JOINT_CHANNEL={channel}',
            f'HERMES_MODEL={(fb["chosen"] or {}).get("model", "<pulled-model-tag>")}',
            "SASSYMCP_GROUPS=" + ",".join(OFFLINE_GROUPS),
        ])
        launch = f'{hermes["interpreter"]} {hermes["node_script"]}'

        started: Any = None
        if start_node:
            if not hermes["node_exists"]:
                started = {"error": f'hermes_node.py not found at {hermes["node_script"]} '
                                    "(frozen build — run from a source checkout)"}
            elif not fb["ready"]:
                started = {"error": fb["reason"]}
            else:
                try:
                    from sassymcp.modules.session import start_session_impl
                    setenv = "; ".join(
                        f'$env:{kv.split("=", 1)[0]}="{kv.split("=", 1)[1]}"'
                        for kv in env_line.split(" ")
                    )
                    started = await start_session_impl(
                        name=f"hermes-{channel}",
                        shell="powershell" if os.name == "nt" else "",
                        command=f"{setenv}; & {launch}",
                    )
                except Exception as e:
                    started = {"error": f"session start failed: {type(e).__name__}: {e}"}

        return json.dumps({
            "handoff": payload,
            "memory_written": wrote_memory,
            "crosslink_posted": posted,
            "local_model": fb,
            "launch": {
                "env": env_line,
                "command": launch,
                "session_name": f"hermes-{channel}",
                "note": (
                    "Hermes proposes commands by default (HERMES_AUTORUN=0) and every "
                    "one still passes SassyMCP's own security gate and audit log. Set "
                    "HERMES_AUTORUN=1 to let clean commands execute unattended."
                ),
            },
            "started": started,
            "next": (
                f"sassy_crosslink_recv channel=\"{channel}\" to read Hermes' replies; "
                f"sassy_crosslink_send to reply. sassy_session_read name=\"hermes-{channel}\" "
                "for node stdout."
            ),
        }, indent=2, default=str)
