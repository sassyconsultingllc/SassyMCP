#!/usr/bin/env python
"""hermes_node.py - Hermes (local LLM) peer node for a joined session over SassyMCP crosslink.

    [ Claude / lead ]  --ch:"joint"--+
                                      +-- crosslink.db (shared SQLite) + sassy_shell gate + memory
    [ Hermes / Ollama ] -ch:"joint"--+

Rides the SAME on-disk state SassyMCP and Claude already use: no HTTP server, no
open port, no second MCP transport. Reuses SassyMCP's own functions for crosslink
I/O, the security gate, shell execution, audit, and memory -- so behavior is
identical to the running server by construction, not re-implemented.

Run with the SassyMCP venv interpreter so `import sassymcp` resolves:
    V:\\Projects\\SassyMCP\\.venv\\Scripts\\python.exe V:\\Projects\\SassyMCP\\hermes_node.py

Env: HERMES_MODEL, OLLAMA_URL, JOINT_CHANNEL (default joint), MAX_TURNS (12),
     POLL_SECONDS (2), HERMES_AUTORUN (0 = propose-only; 1 = auto-run clean cmds).
Stdlib + sassymcp only -- no third-party deps.
"""
import asyncio
import os
import re
import sys
import time
import sqlite3

# Repo root on path so `import sassymcp` works regardless of CWD.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import urllib.error
import urllib.request

from sassymcp._paths import CROSSLINK_DB
from sassymcp._db import open_db
from sassymcp.modules.crosslink import _post_message, _read_messages, _register_session
from sassymcp.modules._security import validate_command_tiered, detect_delete_intent
from sassymcp.modules.shell import _run_subprocess
from sassymcp.modules import audit as _audit
from sassymcp.modules.memory import MemoryStore

SELF_ID = "hermes-node"
CHANNEL = os.environ.get("JOINT_CHANNEL", "joint")
MODEL = os.environ.get("HERMES_MODEL", "hf.co/NousResearch/Hermes-4-14B-GGUF:Q4_K_M")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/v1/chat/completions")
MAX_TURNS = int(os.environ.get("MAX_TURNS", "12"))
POLL = float(os.environ.get("POLL_SECONDS", "2"))
# Safe by default: Hermes PROPOSES commands; SaS or Claude approves them
# (sassy_shell + sassy_shell_confirm) before anything runs. HERMES_AUTORUN=1 lets
# clean (non-destructive) commands auto-execute -- a true autonomous shell loop,
# so opt in deliberately, on your own gear.
AUTORUN = os.environ.get("HERMES_AUTORUN", "0") == "1"
MEM_KEY = "task_jointsession_ops_state"

# Hermes asks to run a command by emitting a fenced block:
#   ```run            (powershell, default)
#   ```run:wsl        (or cmd / powershell)
RUN_FENCE = re.compile(r"```run(?::(\w+))?\s*\n(.*?)```", re.DOTALL)
TURN_TAG = re.compile(r"turn=(\d+)", re.IGNORECASE)


def audit(event, pattern, command, meta=None):
    try:
        _audit.log_pattern_event(event, SELF_ID, pattern, command, meta or {})
    except Exception:
        pass  # audit shape drift must never kill the node


def harden_ssh(cmd):
    """SassyMCP edge: sassy_shell wedges on interactive SSH. Force non-interactive."""
    if cmd.strip().lower().startswith("ssh ") and "batchmode" not in cmd.lower():
        return cmd.replace("ssh ", "ssh -o BatchMode=yes -o ConnectTimeout=5 ", 1)
    return cmd


def classify(cmd):
    """Mirror sassy_shell's gate via SassyMCP's own _security module.

    'run'    -> clean, eligible to execute (auto only if AUTORUN)
    'hold'   -> destructive/delete intent: surface for confirm, never auto-run
    'refuse' -> hard blocklist hit (rm -rf, mkfs, the 'format' substring, ...)
    """
    cmd = (cmd or "").strip()
    if not cmd:
        return "refuse", "empty command"
    ok, btier, err = validate_command_tiered(cmd)
    if not ok and btier != "low":
        return "refuse", f"blocklist[{btier}]: {err}"
    is_del, kw = detect_delete_intent(cmd)
    if is_del:
        return "hold", f"destructive intent: {kw}"
    return "run", "ok"


def run_command(cmd, shell):
    out = asyncio.run(_run_subprocess(shell, harden_ssh(cmd), 60))
    return out if isinstance(out, str) else str(out)


def recent_history(limit=12):
    """Read-only channel history for model context (does NOT touch read_by)."""
    try:
        conn = open_db(CROSSLINK_DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT session_id, payload FROM messages WHERE channel=? ORDER BY id DESC LIMIT ?",
            (CHANNEL, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in reversed(rows)]
    except Exception:
        return []


def build_system(mem):
    ctx = {}
    try:
        ctx = mem.context_load()
    except Exception:
        pass

    def fold(items, n=6):
        return "\n".join(
            f"- [{m.get('key', '?')}] {str(m.get('value', ''))[:220]}" for m in (items or [])[:n]
        )

    brain = "\n".join(
        s for s in [
            "CRITICAL:\n" + fold(ctx.get("critical")) if ctx.get("critical") else "",
            "HIGH:\n" + fold(ctx.get("high_priority")) if ctx.get("high_priority") else "",
            "PATTERNS:\n" + fold(ctx.get("patterns")) if ctx.get("patterns") else "",
            "ACTIVE TASKS:\n" + fold(ctx.get("active_tasks")) if ctx.get("active_tasks") else "",
        ] if s
    ) or "(memory empty)"

    if AUTORUN:
        exec_line = "- Clean (non-destructive) commands auto-execute and their output returns to you.\n"
    else:
        exec_line = (
            "- Commands you propose are NOT auto-run; SaS or Claude approves each one before it "
            "executes. Propose clearly; do not assume execution.\n"
        )

    return (
        "You are Hermes, a local peer in a joined session with Claude (the lead) over "
        "the SassyMCP crosslink. You are talking to SaS (Shane Smith), a senior engineer.\n"
        "Protocol:\n"
        f"- You and Claude alternate on channel '{CHANNEL}'. Tag every reply: 'turn=N from=hermes'.\n"
        "- To run a shell command, emit a fenced block:\n"
        "    ```run\n    <powershell command>\n    ```\n"
        "  Use ```run:cmd or ```run:wsl for other shells. One command per block.\n"
        + exec_line +
        "- Destructive commands (delete, rm -rf, mkfs, format, dd, ...) are ALWAYS held for "
        "human confirm and will NOT run, regardless of mode.\n"
        "- Be terse. No emoji. Senior peer, not an assistant. Send ONE reply, then stop.\n\n"
        "Shared memory (sassy brain):\n" + brain
    )


def hermes_reply(system, history, incoming):
    messages = [{"role": "system", "content": system}]
    for h in history[-10:]:
        role = "assistant" if h["session_id"] == SELF_ID else "user"
        messages.append({"role": role, "content": h["payload"]})
    messages.append({"role": "user", "content": incoming})
    body = json.dumps(
        {"model": MODEL, "messages": messages, "stream": False, "temperature": 0.7}
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
    if key:  # hosted OpenAI-compatible endpoints (OpenRouter, etc.) need a bearer token
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(OLLAMA_URL, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(
            f"Ollama HTTP {e.code} at {OLLAMA_URL}: {detail} | model={MODEL!r} "
            f"-- check `ollama list` / `ollama pull {MODEL}`, or set HERMES_MODEL to a pulled tag"
        )
    return data["choices"][0]["message"]["content"].strip()


def process_commands(text):
    """Extract, gate, and (optionally) run any ```run blocks. Returns a transcript."""
    results = []
    for shell_kind, body in RUN_FENCE.findall(text):
        cmd = body.strip()
        shell = (shell_kind or "powershell").lower()
        if shell not in ("powershell", "cmd", "wsl"):
            shell = "powershell"
        verdict, detail = classify(cmd)
        if verdict == "run" and AUTORUN:
            print(f"[hermes-node] AUTORUN executing ({shell}): {cmd}", flush=True)
            audit("hermes_node_exec", "clean", cmd, {"shell": shell})
            results.append(f"[RAN {shell}] {cmd}\n{run_command(cmd, shell)}")
        elif verdict == "run":
            audit("hermes_node_propose", "clean", cmd, {"shell": shell})
            results.append(
                f"[PROPOSED {shell}] {cmd}\n"
                "  Not executed (HERMES_AUTORUN=0). Approve to run, or set HERMES_AUTORUN=1 to auto-run clean commands."
            )
        elif verdict == "hold":
            audit("hermes_node_hold", detail, cmd, {"shell": shell})
            results.append(
                f"[CONFIRM-REQUIRED] {detail}\n  {shell}: {cmd}\n"
                "  Not executed. Approve out of band (sassy_shell + sassy_shell_confirm) to run."
            )
        else:
            audit("hermes_node_refuse", detail, cmd, {"shell": shell})
            results.append(f"[REFUSED] {detail}\n  {cmd}")
    return "\n\n".join(results)


def preflight():
    """Surface endpoint/model at startup so a misconfig is obvious before turn 1."""
    if os.environ.get("LLM_API_KEY") or os.environ.get("OPENROUTER_API_KEY"):
        print(f"[hermes-node] endpoint={OLLAMA_URL} model={MODEL} (hosted, API key set)")
        return
    base = OLLAMA_URL.split("/v1/")[0]
    try:
        with urllib.request.urlopen(base + "/api/tags", timeout=10) as r:
            tags = [m.get("name", "") for m in json.loads(r.read().decode("utf-8")).get("models", [])]
        print(f"[hermes-node] ollama models: {tags or '(none pulled)'}")
        if not any(MODEL == t or MODEL.split(':')[0] in t for t in tags):
            print(f"[hermes-node] WARNING: {MODEL!r} not pulled -- run `ollama pull {MODEL}` or set HERMES_MODEL to one above")
    except Exception as e:
        print(f"[hermes-node] WARNING: Ollama unreachable at {base} ({e}); is `ollama serve` up?")


def main():
    _register_session(SELF_ID, name="hermes", platform="ollama")
    preflight()
    mem = MemoryStore()
    system = build_system(mem)
    history = recent_history()
    turns = 0
    print(f"[hermes-node] live on '{CHANNEL}' as {SELF_ID}; model={MODEL}; cap={MAX_TURNS}; autorun={AUTORUN}")

    while turns < MAX_TURNS:
        try:
            msgs = _read_messages(SELF_ID, CHANNEL, limit=20, unread_only=True)
        except Exception as e:
            print(f"[hermes-node] recv error: {e}")
            time.sleep(POLL)
            continue

        for m in sorted((x for x in msgs if x.get("session_id") != SELF_ID), key=lambda x: x["id"]):
            payload = m.get("payload", "")
            if "[CONTROL] stop" in payload:
                print("[hermes-node] stop control received; exiting.")
                return
            turns += 1
            n = TURN_TAG.search(payload)
            reply_turn = (int(n.group(1)) + 1) if n else turns + 1
            print(f"[hermes-node] turn {turns}: replying to msg id {m['id']} ({m.get('session_id')})")

            try:
                reply = hermes_reply(system, history, payload)
            except Exception as e:
                reply = f"(hermes error: {e})"

            cmd_results = process_commands(reply)
            out = f"turn={reply_turn} from=hermes\n{reply}"
            if cmd_results:
                out += f"\n\n--- command results ---\n{cmd_results}"

            _post_message(SELF_ID, CHANNEL, out[:256_000])  # crosslink 256KiB cap
            history.append({"session_id": m["session_id"], "payload": payload})
            history.append({"session_id": SELF_ID, "payload": out})

            try:
                mem.remember(
                    MEM_KEY,
                    f"joint session active on '{CHANNEL}'; last hermes turn={reply_turn}: {reply[:160]}",
                    tags=["task-active"], priority="high", project="ops",
                )
            except Exception:
                pass

            if turns >= MAX_TURNS:
                break

        time.sleep(POLL)

    print(f"[hermes-node] turn cap ({MAX_TURNS}) reached; exiting. Bump MAX_TURNS to continue.")


if __name__ == "__main__":
    main()
