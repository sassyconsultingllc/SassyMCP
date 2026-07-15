# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-VGQGQ4JW7GW5
"""SassyMCP Nuclear Persistent State Manager.

Every tool can now remember its own state across calls and even across server restarts.
Uses the same SQLite backend as Crosslink for zero extra deps.
"""

import json
import logging
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Optional

from sassymcp._db import open_db
from sassymcp._paths import HOME as _SASSY_HOME

logger = logging.getLogger("sassymcp.state")

STATE_DB = _SASSY_HOME / "tool_state.db"

class ToolStateManager:
    """Persistent per-tool key/value store.

    Opens a fresh WAL connection per operation rather than holding one shared
    connection. The tool handlers below run inside asyncio.to_thread (the
    audit wrapper offloads sync tools to a thread pool), so concurrent calls
    land on *different* threads — and a single sqlite3.Connection is not safe
    to use from multiple threads at once. A per-call connection is cheap under
    WAL and keeps each call fully isolated; WAL + busy_timeout handle the
    cross-call write serialization without blocking the event loop.
    """

    def __init__(self):
        with closing(open_db(STATE_DB)) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS state (tool TEXT, key TEXT, value TEXT, PRIMARY KEY(tool, key))")
            conn.commit()

    def set(self, tool: str, key: str, value: Any):
        with closing(open_db(STATE_DB)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO state (tool, key, value) VALUES (?, ?, ?)",
                (tool, key, json.dumps(value))
            )
            conn.commit()

    def get(self, tool: str, key: str, default: Any = None) -> Any:
        with closing(open_db(STATE_DB)) as conn:
            row = conn.execute(
                "SELECT value FROM state WHERE tool=? AND key=?", (tool, key)
            ).fetchone()
        if row:
            return json.loads(row[0])
        return default

    def clear(self, tool: str = None):
        with closing(open_db(STATE_DB)) as conn:
            if tool:
                conn.execute("DELETE FROM state WHERE tool=?", (tool,))
            else:
                conn.execute("DELETE FROM state")
            conn.commit()


_state_manager = ToolStateManager()

def register(server):
    server.state = _state_manager

    # NOTE: these are plain `def`, not `async def`. The audit wrapper offloads
    # sync tools to a worker thread (see server._wrap_all_tools), so the
    # blocking SQLite work never stalls the event loop and never wedges a
    # concurrent client's call.
    @server.tool()
    def sassy_state_set(tool_name: str, key: str, value: str) -> str:
        """Persist any value for any tool across sessions."""
        _state_manager.set(tool_name, key, value)
        return f"State saved: {tool_name}.{key}"

    @server.tool()
    def sassy_state_get(tool_name: str, key: str) -> str:
        """Retrieve persistent state for a tool. Returns JSON-encoded value."""
        value = _state_manager.get(tool_name, key)
        return json.dumps(value)

    @server.tool()
    def sassy_state_clear(tool_name: str = "") -> str:
        """Clear state for a specific tool or all tools."""
        _state_manager.clear(tool_name if tool_name else None)
        return "State cleared"

    logger.info("tools now have memory")