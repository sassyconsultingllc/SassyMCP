"""Coordination — peer discovery and targeted handoff for SassyMCP.

A thin layer on top of crosslink that turns the free-form message bus into a
named, queryable mesh of cooperating agents. Crosslink moves bytes; coordination
answers "who is here, what can they do, and hand this specific task to that one."

Rides the SAME crosslink.db (no new tables, no schema migration, no new port) —
peers are announced as heartbeats on the `peer-announce` channel and registered
in the existing `sessions` table; delegations are targeted messages on
`device-handoff` that the receiver filters by `to`. This is the data layer the
Sassy Brain cockpit's coordination view reads.

Two entry points share the same module-level helpers (single source of truth):
  - MCP tools (register): sassy_peer_announce / peer_list / peer_delegate / coordination_board
  - CLI: `python -m sassymcp.modules.coordination [board|peers|announce|delegate]`
    used by the VS Code cockpit (WAL-aware reads/writes without native deps).

Peers in practice: Claude Desktop, Cursor, Windsurf, the Hermes Ollama node
(hermes_node.py), the VS Code cockpit itself, or a remote SassyMCP instance.

Pro tier (registered in the `v020` group alongside crosslink).
"""

import json
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone

from sassymcp._db import open_db
from sassymcp._paths import HOME as _HOME
from sassymcp.modules.crosslink import (
    DB_PATH,
    _ensure_db,
    _list_sessions,
    _post_message,
    _register_session,
)

PEER_CHANNEL = "peer-announce"
HANDOFF_CHANNEL = "device-handoff"
DEFAULT_STALE_SECONDS = 300


def _register_hooks():
    from sassymcp.modules._hooks import register_hook
    register_hook(
        name="coordination",
        module="coordination",
        description="Multi-AI peer mesh — discover other agents, see who is live, delegate a task to a specific peer.",
        triggers=[
            "who is online", "which agents", "available peers", "delegate to",
            "hand this to", "ask hermes", "ask the other agent", "coordinate with",
            "split this between", "have another model", "peer", "swarm",
        ],
        instructions="""
## Coordination Playbook — multi-AI peer mesh

Crosslink moves messages; coordination names the participants and routes work.

### Announce yourself (do this once when joining a coordinated task)
`sassy_peer_announce peer_id="<stable-id>" name="claude-desktop" platform="windows" capabilities="shell,github,phone"` —
re-call periodically to stay "alive" (heartbeat). `endpoint` is optional (a LAN/Tunnel URL for a remote node).

### See who is live
`sassy_peer_list` — active peers with their capabilities and last-seen age. Stale peers
(no heartbeat within stale_seconds) are flagged `alive=false`.

### Hand a task to ONE peer
`sassy_peer_delegate peer_id="hermes-node" task="<what>" context="<state>" next_steps="<ordered>"` —
posts a targeted handoff. The receiver picks it up with
`sassy_crosslink_recv channel="device-handoff"` and acts on messages addressed to its peer_id.

### One-call status for a dashboard/UI
`sassy_coordination_board` — peers + channel activity + recent handoff timeline + sessions
in a single payload.

### When NOT to use
- Broadcasting to everyone → `sassy_crosslink_broadcast`.
- Plain cross-client signal with no specific recipient → `sassy_crosslink_send`.
- Long-term knowledge → `sassy_memory_remember`.
""",
    )


try:
    _register_hooks()
except Exception:
    pass


def _now():
    return datetime.now(timezone.utc)


def _age_seconds(iso_ts: str) -> float:
    """Seconds since an ISO-8601 UTC timestamp; large number on parse failure."""
    try:
        dt = datetime.fromisoformat(iso_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (_now() - dt).total_seconds()
    except (ValueError, TypeError):
        return 10 ** 9


def _split_caps(capabilities) -> list[str]:
    if isinstance(capabilities, (list, tuple)):
        return [str(c).strip() for c in capabilities if str(c).strip()]
    return [c.strip() for c in str(capabilities or "").split(",") if c.strip()]


def _recent_peers(stale_seconds: int = DEFAULT_STALE_SECONDS) -> list[dict]:
    """Latest announce per peer from the peer-announce channel (read-only — does
    NOT mark messages read, so the cockpit can poll without consuming handoffs).
    """
    _ensure_db()
    conn = open_db(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT session_id, payload, created_at FROM messages "
        "WHERE channel=? ORDER BY id DESC LIMIT 500",
        (PEER_CHANNEL,),
    ).fetchall()
    conn.close()

    peers: dict[str, dict] = {}
    for r in rows:
        try:
            data = json.loads(r["payload"])
        except (json.JSONDecodeError, TypeError):
            data = {}
        pid = data.get("peer_id") or r["session_id"]
        if pid in peers:
            continue  # rows are id DESC, so the first hit is the newest
        age = _age_seconds(r["created_at"])
        peers[pid] = {
            "peer_id": pid,
            "name": data.get("name", ""),
            "platform": data.get("platform", ""),
            "capabilities": data.get("capabilities", []),
            "endpoint": data.get("endpoint", ""),
            "last_seen": r["created_at"],
            "age_seconds": round(age, 1),
            "alive": age <= stale_seconds,
        }

    # When a peer has a sessions row, that row IS its liveness: announce_peer
    # heartbeats (and the server's touch_client_peer) refresh it without
    # re-posting a message, and mark_client_peers_offline backdates it on
    # shutdown. The announce message is the identity record, not the pulse.
    # Peers with no row (legacy message-only announces) keep message-age.
    for s in _list_sessions():
        p = peers.get(s.get("session_id"))
        if not p:
            continue
        row_age = _age_seconds(s.get("last_seen", ""))
        p["last_seen"] = s["last_seen"]
        p["age_seconds"] = round(row_age, 1)
        p["alive"] = row_age <= stale_seconds

    # newest first
    return sorted(peers.values(), key=lambda p: p["age_seconds"])


def _newest_announce(peer_id: str) -> tuple[dict, float] | None:
    """Newest stored announce payload for a peer and its age, or None."""
    _ensure_db()
    conn = open_db(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT payload, created_at FROM messages "
        "WHERE channel=? AND session_id=? ORDER BY id DESC LIMIT 1",
        (PEER_CHANNEL, peer_id),
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        data = json.loads(row["payload"])
    except (json.JSONDecodeError, TypeError):
        data = {}
    return data, _age_seconds(row["created_at"])


def announce_peer(peer_id: str = "", name: str = "", platform: str = "",
                  capabilities="", endpoint: str = "", ttl_seconds: int = 0) -> dict:
    """Register/refresh a peer heartbeat. Shared by the MCP tool and the CLI.

    Heartbeat semantics: the sessions row is refreshed on EVERY call (that is
    what liveness reads), but a peer-announce MESSAGE is only posted when the
    peer is new, its identity/capabilities changed, or the newest stored
    announce has gone stale. Without the dedup, periodic heartbeats (the
    cockpit polls, the server auto-records) flood the channel with identical
    rows and the message count stops meaning anything.
    """
    if not peer_id:
        peer_id = f"peer-{uuid.uuid4().hex[:8]}"
    payload = {
        "peer_id": peer_id,
        "name": name,
        "platform": platform,
        "capabilities": _split_caps(capabilities),
        "endpoint": endpoint,
        "ts": _now().isoformat(),
    }
    _register_session(peer_id, name=name, platform=platform)

    prev = _newest_announce(peer_id)
    identity = {k: payload[k] for k in ("name", "platform", "capabilities", "endpoint")}
    if prev is not None:
        prev_payload, prev_age = prev
        prev_identity = {k: prev_payload.get(k, [] if k == "capabilities" else "")
                         for k in identity}
        if prev_identity == identity and prev_age <= DEFAULT_STALE_SECONDS:
            return {"announced": payload, "message_id": None, "heartbeat": True}

    msg = _post_message(peer_id, PEER_CHANNEL, json.dumps(payload), ttl_seconds)
    return {"announced": payload, "message_id": msg.get("id")}


# ── Server-side auto-record ───────────────────────────────────────────
#
# The mesh is only truthful if every client that does work appears on it —
# and LLM clients do not call sassy_peer_announce unprompted. So the server
# records them itself: the audit wrapper calls touch_client_peer() on every
# tool call with the MCP clientInfo from the initialize handshake. First
# call registers the client as a peer; subsequent calls refresh liveness
# (throttled). No LLM cooperation involved.

_TOUCH_INTERVAL_SECONDS = 15.0
_touched: dict[str, float] = {}  # identity key -> monotonic time of last touch


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "unknown"


def touch_client_peer(client_name: str, client_version: str = "") -> None:
    """Upsert the connected MCP client as a live peer (throttled, never raises).

    peer_id is derived from the client name alone ("client-claude-ai"), so one
    row tracks each client product regardless of how many chats it multiplexes
    onto the connection — which matches how stdio actually works.
    """
    if not client_name:
        return
    key = f"{client_name}|{client_version}"
    now = time.monotonic()
    if now - _touched.get(key, 0.0) < _TOUCH_INTERVAL_SECONDS:
        return
    _touched[key] = now
    try:
        import platform as _plat
        announce_peer(
            peer_id=f"client-{_slug(client_name)}",
            name=client_name + (f" {client_version}" if client_version else ""),
            platform=f"mcp-client/{_plat.system().lower()}",
            capabilities="auto-recorded",
        )
    except Exception:
        _touched.pop(key, None)  # let the next call retry


def mark_client_peers_offline() -> None:
    """Backdate this process's auto-recorded peers so the board flips them to
    alive=false immediately on clean shutdown (sessions has no status column;
    liveness is derived from last_seen age)."""
    if not _touched:
        return
    try:
        cutoff = datetime.fromtimestamp(
            time.time() - DEFAULT_STALE_SECONDS - 1, tz=timezone.utc
        ).isoformat()
        peer_ids = [f"client-{_slug(k.split('|', 1)[0])}" for k in _touched]
        conn = open_db(DB_PATH)
        conn.executemany(
            "UPDATE sessions SET last_seen=? WHERE session_id=?",
            [(cutoff, pid) for pid in peer_ids],
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def delegate_task(peer_id: str, task: str, context: str = "", next_steps: str = "",
                  from_peer: str = "", channel: str = HANDOFF_CHANNEL) -> dict:
    """Post a targeted handoff to one peer. Shared by the MCP tool and the CLI."""
    if not from_peer:
        from_peer = f"peer-{uuid.uuid4().hex[:6]}"
    payload = {
        "to": peer_id,
        "from": from_peer,
        "task": task,
        "context": context,
        "next_steps": next_steps,
        "ts": _now().isoformat(),
    }
    msg = _post_message(from_peer, channel, json.dumps(payload))
    return {"delegated_to": peer_id, "channel": channel,
            "message_id": msg.get("id"), "payload": payload}


def _memory_summary(recent_limit: int = 8, task_limit: int = 8,
                    milestone_limit: int = 5) -> dict:
    """Memory-plane summary for the board (read-only on memory.db).

    Reads the DB directly rather than importing the memory module's store so
    the board works even when the memory tool group isn't loaded.
    """
    db = _HOME / "memory.db"
    if not db.exists():
        return {"available": False, "memory_count": 0, "milestone_count": 0,
                "recent": [], "active_tasks": [], "milestones": []}
    conn = open_db(db)
    conn.row_factory = sqlite3.Row
    try:
        mem_count = conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"]
        mile_count = conn.execute("SELECT COUNT(*) AS c FROM milestones").fetchone()["c"]

        def _mem_row(r, with_value=False):
            out = {
                "key": r["key"],
                "priority": r["priority"],
                "project": r["project"],
                "tags": [t for t in (r["tags"] or "").split(",") if t],
                "age_seconds": round(max(0.0, time.time() - r["updated_at"]), 1),
            }
            if with_value:
                v = r["value"] or ""
                out["value"] = v[:160] + ("…" if len(v) > 160 else "")
            return out

        recent = [_mem_row(r) for r in conn.execute(
            "SELECT key, value, tags, priority, project, updated_at "
            "FROM memories ORDER BY updated_at DESC LIMIT ?", (recent_limit,))]
        active_tasks = [_mem_row(r, with_value=True) for r in conn.execute(
            "SELECT key, value, tags, priority, project, updated_at FROM memories "
            "WHERE (key LIKE 'task_%' AND key LIKE '%_state') "
            "   OR tags LIKE '%task-active%' OR key LIKE 'blocker_%' "
            "ORDER BY updated_at DESC LIMIT ?", (task_limit,))]
        milestones = [{
            "event": r["event"],
            "project": r["project"],
            "age_seconds": round(max(0.0, time.time() - r["timestamp"]), 1),
        } for r in conn.execute(
            "SELECT event, project, timestamp FROM milestones "
            "ORDER BY timestamp DESC LIMIT ?", (milestone_limit,))]
    finally:
        conn.close()
    return {"available": True, "memory_count": mem_count,
            "milestone_count": mile_count, "recent": recent,
            "active_tasks": active_tasks, "milestones": milestones,
            "db": str(db)}


def _recent_calls(limit: int = 15) -> list[dict]:
    """Tail of the durable audit log (newest first) — the MCP's live pulse.

    Reads the last chunk of audit.jsonl instead of the whole file: the log
    grows without bound between rotations and the board polls frequently.
    """
    f = _HOME / "audit.jsonl"
    if not f.exists():
        return []
    try:
        size = f.stat().st_size
        with f.open("rb") as fh:
            fh.seek(max(0, size - 131072))
            chunk = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = chunk.splitlines()
    if len(lines) > 1 and size > 131072:
        lines = lines[1:]  # first line is almost certainly a partial entry
    calls = []
    for line in reversed(lines):
        if len(calls) >= limit:
            break
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        tool = e.get("tool") or e.get("tool_name")
        if not tool:
            continue
        ts = e.get("timestamp", 0)
        calls.append({
            "tool": tool,
            "elapsed_ms": e.get("elapsed_ms"),
            "error": (e.get("error") or "")[:120] or None,
            "age_seconds": round(max(0.0, time.time() - ts), 1) if ts else None,
        })
    return calls


def _hooks_summary() -> dict:
    """Continuity playbooks: which exist, their triggers, which are active.

    Imports the memory module so its session_startup / session_handoff hooks
    are registered even when this runs as a bare subprocess. `active` is
    per-process state — meaningful from the in-server tool, always [] from
    the polled CLI.
    """
    import sassymcp.modules.memory  # noqa: F401  (registers its hooks on import)
    from sassymcp.modules._hooks import get_active_hooks, get_all_hooks
    active = {h["name"] for h in get_active_hooks()}
    hooks = [{
        "name": h["name"],
        "module": h["module"],
        "description": h["description"],
        "triggers": h["triggers"][:4],
        "active": h["name"] in active,
    } for h in get_all_hooks().values()]
    continuity = ("session_startup", "session_handoff", "coordination", "crosslink")
    hooks.sort(key=lambda h: (h["name"] not in continuity, h["name"]))
    return {"hooks": hooks, "active_count": len(active)}


def board_snapshot(stale_seconds: int = DEFAULT_STALE_SECONDS, handoff_limit: int = 20) -> dict:
    """Brain-board snapshot for the Sassy Brain cockpit (read-only, WAL-aware).

    One payload, every continuity plane: crosslink (peers/channels), handoffs,
    memory (entries/tasks/milestones), the audit tail (live tool calls), and
    hook availability. Single source of truth shared by the
    sassy_coordination_board MCP tool and the `python -m
    sassymcp.modules.coordination` CLI the VS Code extension polls. Reads
    through open_db() so it sees rows still in the WAL — direct file reads
    (e.g. sql.js) would miss recent handoffs until checkpoint.
    """
    _ensure_db()
    conn = open_db(DB_PATH)
    conn.row_factory = sqlite3.Row

    channels = [
        {"channel": r["channel"], "count": r["c"]}
        for r in conn.execute(
            "SELECT channel, COUNT(*) AS c FROM messages "
            "GROUP BY channel ORDER BY c DESC"
        ).fetchall()
    ]

    handoff_rows = conn.execute(
        "SELECT id, session_id, channel, payload, created_at FROM messages "
        "WHERE channel IN (?, 'task-handoff') ORDER BY id DESC LIMIT ?",
        (HANDOFF_CHANNEL, handoff_limit),
    ).fetchall()
    conn.close()

    handoffs = []
    for r in handoff_rows:
        try:
            data = json.loads(r["payload"])
        except (json.JSONDecodeError, TypeError):
            data = {"raw": r["payload"][:200]}
        handoffs.append({
            "id": r["id"],
            "channel": r["channel"],
            "from": data.get("from", r["session_id"]),
            "to": data.get("to", ""),
            "task": data.get("task", data.get("status", "")),
            "created_at": r["created_at"],
            "age_seconds": round(_age_seconds(r["created_at"]), 1),
        })

    out = {
        "peers": _recent_peers(stale_seconds),
        "channels": channels,
        "handoffs": handoffs,
        "sessions": _list_sessions(),
        "db": str(DB_PATH),
        "generated_at": _now().isoformat(),
    }
    # The additional planes must never break the core board — each degrades
    # to an error marker the UI can render as "unavailable".
    for key, fn in (("memory", _memory_summary),
                    ("recent_calls", _recent_calls),
                    ("hooks", _hooks_summary)):
        try:
            out[key] = fn()
        except Exception as e:
            out[key] = {"error": str(e)}
    return out


def register(server):

    @server.tool()
    async def sassy_peer_announce(
        peer_id: str = "",
        name: str = "",
        platform: str = "",
        capabilities: str = "",
        endpoint: str = "",
        ttl_seconds: int = 0,
    ) -> str:
        """Announce/refresh this agent as a live peer in the coordination mesh.

        peer_id: stable identifier for this agent (auto-generated if empty).
        name: human label, e.g. 'claude-desktop', 'hermes-node'.
        platform: 'windows', 'ollama', 'remote-lan', etc.
        capabilities: comma-separated, e.g. 'shell,github,phone,vision'.
        endpoint: optional URL for a remote/cross-machine peer.
        ttl_seconds: auto-expire this heartbeat after N seconds (0 = never).

        Re-call periodically to stay 'alive' (see sassy_peer_list stale window).
        """
        return json.dumps(announce_peer(peer_id, name, platform, capabilities, endpoint, ttl_seconds))

    @server.tool()
    async def sassy_peer_list(stale_seconds: int = DEFAULT_STALE_SECONDS) -> str:
        """List peers in the coordination mesh, newest heartbeat first.

        stale_seconds: a peer with no heartbeat within this window is flagged
        alive=false (still listed, so the UI can show 'last seen Xs ago').
        """
        peers = _recent_peers(stale_seconds)
        alive = [p for p in peers if p["alive"]]
        return json.dumps(
            {"peers": peers, "count": len(peers), "alive": len(alive)}, indent=2
        )

    @server.tool()
    async def sassy_peer_delegate(
        peer_id: str,
        task: str,
        context: str = "",
        next_steps: str = "",
        from_peer: str = "",
        channel: str = HANDOFF_CHANNEL,
    ) -> str:
        """Hand a specific task to ONE peer (targeted handoff).

        The recipient reads it with sassy_crosslink_recv on the given channel and
        acts on messages whose 'to' equals its own peer_id.

        peer_id: the recipient peer (see sassy_peer_list).
        task: what to do. context: state the peer needs. next_steps: ordered plan.
        from_peer: sender id (auto-generated if empty).
        channel: handoff channel (default 'device-handoff').
        """
        return json.dumps(delegate_task(peer_id, task, context, next_steps, from_peer, channel))

    @server.tool()
    async def sassy_coordination_board(
        stale_seconds: int = DEFAULT_STALE_SECONDS, handoff_limit: int = 20
    ) -> str:
        """One-call coordination snapshot for the Sassy Brain cockpit view.

        Returns live peers, channel activity (message counts), the recent handoff
        timeline (peer-delegate + task-handoff), and registered sessions — without
        marking any messages read.
        """
        return json.dumps(board_snapshot(stale_seconds, handoff_limit), indent=2)


def _main(argv=None):
    """CLI used by the VS Code cockpit. Emits one JSON line to stdout.

    Subcommands: board (default) | peers | announce | delegate.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="python -m sassymcp.modules.coordination")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("board")
    p_peers = sub.add_parser("peers")
    p_peers.add_argument("--stale", type=int, default=DEFAULT_STALE_SECONDS)
    p_ann = sub.add_parser("announce")
    p_ann.add_argument("--id", default="")
    p_ann.add_argument("--name", default="")
    p_ann.add_argument("--platform", default="")
    p_ann.add_argument("--caps", default="")
    p_ann.add_argument("--endpoint", default="")
    p_ann.add_argument("--ttl", type=int, default=0)
    p_del = sub.add_parser("delegate")
    p_del.add_argument("--to", required=True)
    p_del.add_argument("--task", required=True)
    p_del.add_argument("--context", default="")
    p_del.add_argument("--next", default="")
    p_del.add_argument("--from", dest="from_peer", default="")
    p_del.add_argument("--channel", default=HANDOFF_CHANNEL)
    args = parser.parse_args(argv)

    if args.cmd == "announce":
        return announce_peer(args.id, args.name, args.platform, args.caps, args.endpoint, args.ttl)
    if args.cmd == "delegate":
        return delegate_task(args.to, args.task, args.context, args.next, args.from_peer, args.channel)
    if args.cmd == "peers":
        peers = _recent_peers(args.stale)
        return {"peers": peers, "count": len(peers), "alive": len([p for p in peers if p["alive"]])}
    return board_snapshot()


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.write(json.dumps(_main()))
    except SystemExit:
        raise
    except Exception as e:  # never crash the poller; hand it a usable error shape
        sys.stdout.write(json.dumps({
            "error": str(e), "peers": [], "channels": [],
            "handoffs": [], "sessions": [],
        }))
        sys.exit(1)
