"""Crosslink - Cross-session communication for SassyMCP.

Enables different MCP client sessions (Claude Desktop, Cursor, Windsurf,
Cline, Grok, mobile, web — anything that connects to a SassyMCP server)
to communicate through a shared local message queue backed by SQLite.

Architecture:
  - SQLite DB at $SASSYMCP_HOME/crosslink.db (default ~/.sassymcp/crosslink.db)
  - HTTP API on configurable bind address (default 0.0.0.0:9377 for LAN access)
  - Optional token auth via SASSYMCP_CROSSLINK_TOKEN env var or token param
  - Messages have sender, channel, payload, timestamp
  - Sessions register with ID/name/platform
  - Read tracking per-session

For dual-instance setups (local + remote on the same machine), give each
instance its own SASSYMCP_HOME so they get separate DBs, AND override
DEFAULT_PORT via the sassy_crosslink_register port arg.
"""

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from sassymcp._db import open_db
from sassymcp._paths import CROSSLINK_DB as DB_PATH
DEFAULT_PORT = 9377


def _register_hooks():
    from sassymcp.modules._hooks import register_hook
    register_hook(
        name="crosslink",
        module="crosslink",
        description="Cross-session/cross-client messaging — hand off work between Claude Desktop, Cursor, Windsurf, mobile, etc.",
        triggers=[
            "hand off", "handoff", "continue in", "switch to", "pick up where",
            "send to my other", "tell my", "leave a message for", "task-handoff",
            "from my desktop", "from my phone", "across sessions", "between clients",
        ],
        instructions="""
## Crosslink Playbook — when to bridge sessions

Use this when the user wants their work to follow them across MCP clients
(Claude Desktop ↔ Cursor ↔ Windsurf ↔ Continue ↔ mobile) or when they
explicitly say "continue this on my [other client]".

### Send a handoff
1. `sassy_crosslink_register session_id="<id>" name="claude-desktop" platform="windows"` — register the SOURCE session if not already registered.
2. `sassy_crosslink_send payload="<json with task, status, next_steps>" channel="task-handoff"` — channel name is conventionally `task-handoff` for resume-on-other-client; use `default` for chat-style messages.

### Pick up a handoff (THIS IS THE FIRST THING TO DO ON SESSION START if SaS workflow is loaded)
1. `sassy_crosslink_recv channel="task-handoff" unread_only=True limit=5`
2. If a handoff exists, parse the JSON payload and execute its `next_steps` immediately. Don't ask the user "what were we working on" — the handoff IS the answer.

### Discover other live sessions
`sassy_crosslink_status` — shows registered sessions, message counts per channel, server status.

### When NOT to use
- For state that only matters within ONE session — use sassy_state_set instead.
- For long-term knowledge — use sassy_memory_remember (memory module).
- For pure logging — use audit (sassy_audit_log).

Crosslink is for inter-session SIGNALS, not bulk data transfer.
""",
    )

try:
    _register_hooks()
except Exception:
    pass
_server_thread = None
_server_instance = None
_auth_token = None  # Set when server starts


def _ensure_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = open_db(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, channel TEXT DEFAULT 'default', payload TEXT NOT NULL, created_at TEXT NOT NULL, read_by TEXT DEFAULT '', ttl_seconds INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, name TEXT, platform TEXT, last_seen TEXT, created_at TEXT)")
    # Expire old messages with TTL > 0
    conn.execute("DELETE FROM messages WHERE ttl_seconds > 0 AND datetime(created_at, '+' || ttl_seconds || ' seconds') < datetime('now')")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_chan ON messages(channel)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(created_at)")
    conn.commit(); conn.close()


def _post_message(sid, channel, payload, ttl_seconds=0):
    _ensure_db()
    now = datetime.now(timezone.utc).isoformat()
    conn = open_db(DB_PATH)
    cur = conn.execute("INSERT INTO messages (session_id,channel,payload,created_at,ttl_seconds) VALUES (?,?,?,?,?)", (sid, channel, payload, now, ttl_seconds))
    mid = cur.lastrowid; conn.commit(); conn.close()
    return {"id": mid, "session_id": sid, "channel": channel, "created_at": now, "ttl_seconds": ttl_seconds}


def _read_messages(sid, channel="default", limit=20, unread_only=True, since=""):
    _ensure_db()
    conn = open_db(DB_PATH); conn.row_factory = sqlite3.Row
    q, p = "SELECT * FROM messages WHERE channel=?", [channel]
    if unread_only:
        # Escape SQL LIKE wildcards in session_id
        escaped_sid = sid.replace("%", "\\%").replace("_", "\\_")
        q += " AND read_by NOT LIKE ? ESCAPE '\\'"
        p.append(f"%{escaped_sid}%")
    if since: q += " AND created_at>?"; p.append(since)
    q += " ORDER BY created_at DESC LIMIT ?"; p.append(limit)
    rows = conn.execute(q, p).fetchall(); msgs = [dict(r) for r in rows]
    for m in msgs:
        rb = m.get("read_by", "")
        if sid not in rb:
            conn.execute("UPDATE messages SET read_by=? WHERE id=?", (f"{rb},{sid}" if rb else sid, m["id"]))
    conn.commit(); conn.close()
    return msgs


def _register_session(sid, name="", platform=""):
    _ensure_db()
    now = datetime.now(timezone.utc).isoformat()
    conn = open_db(DB_PATH)
    conn.execute("INSERT INTO sessions (session_id,name,platform,last_seen,created_at) VALUES (?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET last_seen=?,name=COALESCE(?,name)", (sid, name, platform, now, now, now, name or None))
    conn.commit(); conn.close()
    return {"session_id": sid, "name": name, "platform": platform, "last_seen": now}


def _list_sessions():
    _ensure_db()
    conn = open_db(DB_PATH); conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM sessions ORDER BY last_seen DESC").fetchall()
    conn.close(); return [dict(r) for r in rows]


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _check_auth(self) -> bool:
        if not _auth_token:
            return True
        auth = self.headers.get("Authorization", "")
        if auth == f"Bearer {_auth_token}":
            return True
        qs = parse_qs(urlparse(self.path).query)
        if qs.get("token", [None])[0] == _auth_token:
            return True
        return False

    def _json(self, data, status=200):
        origin = self.headers.get("Origin", "")
        allowed_origins = {"http://localhost:9377", "http://127.0.0.1:9377"}
        cors_origin = origin if origin in allowed_origins else "null"
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", cors_origin)
        self.end_headers(); self.wfile.write(json.dumps(data).encode())

    def _unauthorized(self):
        self._json({"error": "Unauthorized. Use Authorization: Bearer <token> header or ?token= query param."}, 401)

    def do_OPTIONS(self):
        origin = self.headers.get("Origin", "")
        allowed_origins = {"http://localhost:9377", "http://127.0.0.1:9377"}
        cors_origin = origin if origin in allowed_origins else "null"
        self.send_response(200)
        for h, v in [("Access-Control-Allow-Origin", cors_origin), ("Access-Control-Allow-Methods","GET,POST,OPTIONS"),("Access-Control-Allow-Headers","Content-Type,Authorization")]: self.send_header(h, v)
        self.end_headers()

    def do_GET(self):
        if not self._check_auth(): self._unauthorized(); return
        p = urlparse(self.path); qs = parse_qs(p.query)
        if p.path == "/health": self._json({"status": "ok", "service": "sassymcp-crosslink", "auth_enabled": _auth_token is not None})
        elif p.path == "/sessions": self._json({"sessions": _list_sessions()})
        elif p.path == "/messages":
            self._json({"messages": _read_messages(qs.get("session_id",["anon"])[0], qs.get("channel",["default"])[0], int(qs.get("limit",["20"])[0]), qs.get("unread",["true"])[0]=="true", qs.get("since",[""])[0])})
        else: self._json({"error": "Not found"}, 404)

    def do_POST(self):
        if not self._check_auth(): self._unauthorized(); return
        p = urlparse(self.path)
        content_len = int(self.headers.get("Content-Length", 0) or 0)
        if content_len > 1_048_576:  # 1MB max
            self._json({"error": "payload too large"}, 413)
            return
        try:
            body = json.loads(self.rfile.read(content_len)) if content_len > 0 else {}
        except (json.JSONDecodeError, ValueError):
            body = {}
        if p.path == "/messages":
            if not body.get("payload"): self._json({"error": "payload required"}, 400); return
            self._json(_post_message(body.get("session_id","anon"), body.get("channel","default"), body["payload"]), 201)
        elif p.path == "/sessions": self._json(_register_session(body.get("session_id",uuid.uuid4().hex[:8]), body.get("name",""), body.get("platform","")), 201)
        else: self._json({"error": "Not found"}, 404)


def register(server):

    @server.tool()
    async def sassy_crosslink_start(port: int = DEFAULT_PORT, bind: str = "", token: str = "") -> str:
        """Start the Crosslink HTTP API for LAN-accessible cross-device messaging.

        bind: '0.0.0.0' for LAN access (default), '127.0.0.1' for localhost only.
        token: auth token required for all requests. If empty, checks SASSYMCP_CROSSLINK_TOKEN
               env var. If both empty, runs without auth (localhost use only recommended).
        Endpoints: GET /health, GET/POST /sessions, GET/POST /messages.
        Auth: Authorization: Bearer <token> header or ?token=<token> query param.
        """
        global _server_thread, _server_instance, _auth_token
        if _server_instance is not None:
            return json.dumps({"status": "already_running", "port": port})

        _auth_token = token or os.environ.get("SASSYMCP_CROSSLINK_TOKEN", "") or None
        # Default to localhost when no auth — don't expose unauthenticated on LAN
        if not bind:
            bind = "0.0.0.0" if _auth_token else "127.0.0.1"
        _ensure_db()

        try:
            _server_instance = HTTPServer((bind, port), _Handler)
            _server_thread = threading.Thread(target=_server_instance.serve_forever, daemon=True)
            _server_thread.start()

            # Get LAN IP for convenience
            import socket
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                lan_ip = s.getsockname()[0]
                s.close()
            except Exception:
                lan_ip = bind

            return json.dumps({
                "status": "started",
                "bind": bind,
                "port": port,
                "lan_url": f"http://{lan_ip}:{port}",
                "localhost_url": f"http://127.0.0.1:{port}",
                "auth_enabled": _auth_token is not None,
                "db": str(DB_PATH),
            })
        except Exception as e:
            _server_instance = None
            return json.dumps({"error": str(e)})

    @server.tool()
    async def sassy_crosslink_stop() -> str:
        """Stop the Crosslink HTTP API server."""
        global _server_thread, _server_instance, _auth_token
        if _server_instance is None: return json.dumps({"status": "not_running"})
        _server_instance.shutdown(); _server_instance = None; _server_thread = None; _auth_token = None
        return json.dumps({"status": "stopped"})

    @server.tool()
    async def sassy_crosslink_send(payload: str, channel: str = "default", session_id: str = "", ttl_seconds: int = 0) -> str:
        """Send a message to the crosslink queue.

        payload: message content
        channel: topic/channel name
        session_id: sender ID (auto-generated if empty)
        ttl_seconds: auto-expire after N seconds (0 = never expire)
        """
        if not session_id: session_id = f"sassymcp-{uuid.uuid4().hex[:6]}"
        return json.dumps(_post_message(session_id, channel, payload, ttl_seconds))

    @server.tool()
    async def sassy_crosslink_recv(session_id: str = "sassymcp", channel: str = "default", limit: int = 20, unread_only: bool = True) -> str:
        """Read messages from the crosslink queue. Marks them as read for this session."""
        msgs = _read_messages(session_id, channel, limit, unread_only)
        return json.dumps({"messages": msgs, "count": len(msgs)})

    @server.tool()
    async def sassy_crosslink_status() -> str:
        """Check crosslink status: server running, sessions, message counts, channels."""
        _ensure_db()
        conn = open_db(DB_PATH)
        total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        channels = [r[0] for r in conn.execute("SELECT DISTINCT channel FROM messages").fetchall()]
        conn.close()
        return json.dumps({"server_running": _server_instance is not None, "port": DEFAULT_PORT if _server_instance else None, "db": str(DB_PATH), "total_messages": total, "channels": channels, "sessions": _list_sessions()}, indent=2)

    @server.tool()
    async def sassy_crosslink_register(session_id: str = "", name: str = "", platform: str = "") -> str:
        """Register a session. session_id auto-generated if empty. name/platform for identification."""
        if not session_id: session_id = f"session-{uuid.uuid4().hex[:8]}"
        return json.dumps(_register_session(session_id, name, platform))

    @server.tool()
    async def sassy_crosslink_broadcast(payload: str, session_id: str = "sassymcp") -> str:
        """Broadcast a message to ALL known channels."""
        _ensure_db()
        conn = open_db(DB_PATH)
        channels = [r[0] for r in conn.execute("SELECT DISTINCT channel FROM messages").fetchall()]
        conn.close()
        if not channels: channels = ["default"]
        results = [_post_message(session_id, ch, payload) for ch in channels]
        return json.dumps({"broadcast_to": channels, "results": results})
