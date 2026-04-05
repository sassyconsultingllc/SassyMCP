"""SassyMCP Nuclear Persistent State Manager.

Every tool can now remember its own state across calls and even across server restarts.
Uses the same SQLite backend as Crosslink for zero extra deps.
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("sassymcp.state")

STATE_DB = Path.home() / ".sassymcp" / "tool_state.db"

class ToolStateManager:
    def __init__(self):
        STATE_DB.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(STATE_DB)
        self.conn.execute("CREATE TABLE IF NOT EXISTS state (tool TEXT, key TEXT, value TEXT, PRIMARY KEY(tool, key))")

    def set(self, tool: str, key: str, value: Any):
        self.conn.execute(
            "INSERT OR REPLACE INTO state (tool, key, value) VALUES (?, ?, ?)",
            (tool, key, json.dumps(value))
        )
        self.conn.commit()

    def get(self, tool: str, key: str, default: Any = None) -> Any:
        row = self.conn.execute(
            "SELECT value FROM state WHERE tool=? AND key=?", (tool, key)
        ).fetchone()
        if row:
            return json.loads(row[0])
        return default

    def clear(self, tool: str = None):
        if tool:
            self.conn.execute("DELETE FROM state WHERE tool=?", (tool,))
        else:
            self.conn.execute("DELETE FROM state")
        self.conn.commit()


_state_manager = ToolStateManager()

def register(server):
    server.state = _state_manager

    @server.tool()
    async def sassy_state_set(tool_name: str, key: str, value: str) -> str:
        """Persist any value for any tool across sessions."""
        _state_manager.set(tool_name, key, value)
        return f"State saved: {tool_name}.{key}"

    @server.tool()
    async def sassy_state_get(tool_name: str, key: str) -> str:
        """Retrieve persistent state for a tool. Returns JSON-encoded value."""
        value = _state_manager.get(tool_name, key)
        return json.dumps(value)

    @server.tool()
    async def sassy_state_clear(tool_name: str = "") -> str:
        """Clear state for a specific tool or all tools."""
        _state_manager.clear(tool_name if tool_name else None)
        return "State cleared"

    logger.info("tools now have memory")