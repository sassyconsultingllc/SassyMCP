"""Coordination — peer discovery and targeted handoff for SassyMCP.

A thin layer on top of crosslink that turns the free-form message bus into a
named, queryable mesh of cooperating agents. Crosslink moves bytes; coordination
answers "who is here, what can they do, and hand this specific task to that one."

Rides the SAME crosslink.db (no new tables, no schema migration, no new port) —
peers are announced as heartbeats on the `peer-announce` channel and registered
in the existing `sessions` table; delegations are targeted messages on
`device-handoff` that the receiver filters by `to`. This is the data layer the
Sassy Brain cockpit's coordination view reads via `sassy_coordination_board`.

Peers in practice: Claude Desktop, Cursor, Windsurf, the Hermes Ollama node
(hermes_node.py), or a remote SassyMCP instance reachable over LAN/Tunnel.

Pro tier (registered in the `v020` group alongside crosslink).
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from sassymcp._db import open_db
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
    # newest first
    return sorted(peers.values(), key=lambda p: p["age_seconds"])


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
        if not peer_id:
            peer_id = f"peer-{uuid.uuid4().hex[:8]}"
        caps = _split_caps(capabilities)
        payload = {
            "peer_id": peer_id,
            "name": name,
            "platform": platform,
            "capabilities": caps,
            "endpoint": endpoint,
            "ts": _now().isoformat(),
        }
        _register_session(peer_id, name=name, platform=platform)
        msg = _post_message(peer_id, PEER_CHANNEL, json.dumps(payload), ttl_seconds)
        return json.dumps({"announced": payload, "message_id": msg.get("id")})

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
        return json.dumps(
            {"delegated_to": peer_id, "channel": channel, "message_id": msg.get("id"),
             "payload": payload}
        )

    @server.tool()
    async def sassy_coordination_board(
        stale_seconds: int = DEFAULT_STALE_SECONDS, handoff_limit: int = 20
    ) -> str:
        """One-call coordination snapshot for the Sassy Brain cockpit view.

        Returns live peers, channel activity (message counts), the recent handoff
        timeline (peer-delegate + task-handoff), and registered sessions — without
        marking any messages read.
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

        return json.dumps({
            "peers": _recent_peers(stale_seconds),
            "channels": channels,
            "handoffs": handoffs,
            "sessions": _list_sessions(),
            "db": str(DB_PATH),
            "generated_at": _now().isoformat(),
        }, indent=2)
