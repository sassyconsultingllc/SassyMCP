# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-TESTCOORD001
"""Coordination module: server-side auto-record, announce dedup, brain board.

All DB paths are patched into tmp_path so nothing touches the real
~/.sassymcp. The module-level throttle map is cleared per test.
"""
from __future__ import annotations

import json
import sqlite3
import time

import pytest

import sassymcp.modules.coordination as coord
import sassymcp.modules.crosslink as crosslink
from sassymcp._db import open_db


@pytest.fixture()
def iso_home(tmp_path, monkeypatch):
    """Isolate crosslink.db, memory.db, and audit.jsonl under tmp_path."""
    db = tmp_path / "crosslink.db"
    monkeypatch.setattr(crosslink, "DB_PATH", db)
    monkeypatch.setattr(coord, "DB_PATH", db)
    monkeypatch.setattr(coord, "_HOME", tmp_path)
    monkeypatch.setattr(coord, "_touched", {})
    return tmp_path


def _messages(db, channel):
    conn = open_db(db)
    rows = conn.execute(
        "SELECT session_id, payload FROM messages WHERE channel=? ORDER BY id",
        (channel,)).fetchall()
    conn.close()
    return rows


def test_touch_registers_client_as_live_peer(iso_home):
    coord.touch_client_peer("Claude Desktop", "1.0")
    peers = coord._recent_peers()
    assert len(peers) == 1
    p = peers[0]
    assert p["peer_id"] == "client-claude-desktop"
    assert p["alive"] is True
    assert "auto-recorded" in p["capabilities"]
    # exactly one announce message was posted
    assert len(_messages(iso_home / "crosslink.db", coord.PEER_CHANNEL)) == 1


def test_touch_is_throttled_and_never_floods(iso_home):
    for _ in range(20):
        coord.touch_client_peer("Cursor", "2.1")
    # one throttle window -> one announce message, one session row
    assert len(_messages(iso_home / "crosslink.db", coord.PEER_CHANNEL)) == 1
    assert len(coord._recent_peers()) == 1


def test_announce_heartbeat_updates_row_without_new_message(iso_home):
    first = coord.announce_peer("p1", name="hermes", platform="linux", capabilities="ollama")
    assert first["message_id"] is not None
    second = coord.announce_peer("p1", name="hermes", platform="linux", capabilities="ollama")
    assert second.get("heartbeat") is True
    assert second["message_id"] is None
    assert len(_messages(iso_home / "crosslink.db", coord.PEER_CHANNEL)) == 1
    # identity change posts a fresh announce
    third = coord.announce_peer("p1", name="hermes", platform="linux", capabilities="ollama,vision")
    assert third["message_id"] is not None
    assert len(_messages(iso_home / "crosslink.db", coord.PEER_CHANNEL)) == 2


def test_liveness_prefers_fresh_session_row(iso_home):
    coord.announce_peer("p2", name="node", platform="win")
    db = iso_home / "crosslink.db"
    # Backdate the announce MESSAGE far past staleness; keep the session fresh.
    conn = open_db(db)
    conn.execute("UPDATE messages SET created_at='2020-01-01T00:00:00+00:00'")
    conn.commit()
    conn.close()
    crosslink._register_session("p2", name="node", platform="win")
    peers = coord._recent_peers()
    assert peers[0]["peer_id"] == "p2"
    assert peers[0]["alive"] is True  # session row won over the stale message


def test_mark_client_peers_offline(iso_home):
    coord.touch_client_peer("Windsurf", "3.0")
    assert coord._recent_peers()[0]["alive"] is True
    coord.mark_client_peers_offline()
    assert coord._recent_peers()[0]["alive"] is False


def test_board_carries_all_planes(iso_home):
    coord.touch_client_peer("Claude Desktop", "1.0")
    board = coord.board_snapshot()
    for key in ("peers", "channels", "handoffs", "sessions",
                "memory", "recent_calls", "hooks"):
        assert key in board, f"board missing plane: {key}"
    # no memory.db yet -> explicitly unavailable, not an error
    assert board["memory"]["available"] is False
    assert board["recent_calls"] == []
    hook_names = [h["name"] for h in board["hooks"]["hooks"]]
    # continuity playbooks exist and sort first
    for name in ("session_startup", "session_handoff", "coordination"):
        assert name in hook_names
    assert set(hook_names[:3]) <= {"session_startup", "session_handoff",
                                   "coordination", "crosslink"}


def test_memory_summary_surfaces_task_state(iso_home):
    db = iso_home / "memory.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE memories (
        key TEXT PRIMARY KEY, value TEXT NOT NULL, tags TEXT DEFAULT '',
        priority TEXT DEFAULT 'normal', project TEXT DEFAULT '',
        created_at REAL NOT NULL, updated_at REAL NOT NULL,
        access_count INTEGER DEFAULT 0)""")
    conn.execute("""CREATE TABLE milestones (
        id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL,
        project TEXT DEFAULT '', tags TEXT DEFAULT '', timestamp REAL NOT NULL)""")
    now = time.time()
    conn.execute("INSERT INTO memories VALUES (?,?,?,?,?,?,?,0)",
                 ("task_tls_racrust_state", "x" * 300, "task-active", "high",
                  "racrust", now, now))
    conn.execute("INSERT INTO memories VALUES (?,?,?,?,?,?,?,0)",
                 ("pattern_tls", "use rustls", "", "normal", "", now, now))
    conn.execute("INSERT INTO milestones (event, project, timestamp) VALUES (?,?,?)",
                 ("shipped v1", "racrust", now))
    conn.commit()
    conn.close()

    m = coord._memory_summary()
    assert m["available"] is True
    assert m["memory_count"] == 2 and m["milestone_count"] == 1
    tasks = m["active_tasks"]
    assert len(tasks) == 1 and tasks[0]["key"] == "task_tls_racrust_state"
    assert tasks[0]["value"].endswith("…") and len(tasks[0]["value"]) <= 161
    assert m["milestones"][0]["event"] == "shipped v1"


def test_recent_calls_tails_audit_jsonl(iso_home):
    f = iso_home / "audit.jsonl"
    now = time.time()
    lines = [json.dumps({"tool": f"sassy_tool_{i}", "elapsed_ms": i * 10,
                         "timestamp": now - i}) for i in range(30)]
    lines.insert(5, "{broken json")  # must be skipped, not fatal
    f.write_text("\n".join(lines), encoding="utf-8")

    calls = coord._recent_calls(limit=10)
    assert len(calls) == 10
    # Tail semantics: entries come from the END of the file (production appends
    # chronologically, so that is newest-first). The broken line is skipped.
    assert calls[0]["tool"] == "sassy_tool_29"
    assert all(c["tool"].startswith("sassy_tool_") for c in calls)
    assert all(c["error"] is None for c in calls)
