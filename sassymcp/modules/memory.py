# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-DIWQZTX2DVLX
"""SassyMCP Memory — Persistent cross-session memory with concept-based organization.

This IS the MadameClaude memory layer, built into SassyMCP. No separate server needed.
Stores memories in SQLite ($SASSYMCP_HOME/memory.db, default ~/.sassymcp/memory.db) with tags, priorities, and timestamps.
Survives server restarts. Searchable by concept, tag, project, or free text.

Memory types:
  task_<concept>_<project>_state  — Current state of a task
  pattern_<concept>               — Reusable solution learned (cross-project)
  blocker_<concept>_<project>     — Known blocker, stays until resolved
  decision_<concept>              — Architectural decision (cross-project)

The AI calls sassy_memory_context on session start to load everything it needs.
It calls sassy_memory_remember to store what it learns.
It calls sassy_memory_handoff before ending a session to save continuation state.

This replaces the aspirational madame_* tools from SassyWorks.md with real implementations.
"""

import json
import logging
import sqlite3
import time
from contextlib import closing

from sassymcp._db import open_db
from sassymcp._paths import HOME as _SASSY_HOME

logger = logging.getLogger("sassymcp.memory")

MEMORY_DB = _SASSY_HOME / "memory.db"


def _open() -> sqlite3.Connection:
    """Fresh WAL connection with Row factory. See MemoryStore docstring."""
    conn = open_db(MEMORY_DB)
    conn.row_factory = sqlite3.Row
    return conn


class MemoryStore:
    """Persistent cross-session memory.

    Opens a fresh WAL connection per operation rather than holding one shared
    connection. The memory tool handlers run inside asyncio.to_thread (the
    audit wrapper offloads sync tools to a thread pool), so concurrent calls
    land on *different* threads, and a single sqlite3.Connection is not safe
    to share across threads. Per-call connections are cheap under WAL, keep
    each call isolated, and — crucially — keep the blocking SQLite work off
    the event loop so two chats never wedge each other.
    """

    def __init__(self):
        with closing(_open()) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS memories (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                tags TEXT DEFAULT '',
                priority TEXT DEFAULT 'normal',
                project TEXT DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                access_count INTEGER DEFAULT 0
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS milestones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event TEXT NOT NULL,
                project TEXT DEFAULT '',
                tags TEXT DEFAULT '',
                timestamp REAL NOT NULL
            )""")
            conn.commit()

    def remember(self, key: str, value: str, tags: list[str] = None,
                 priority: str = "normal", project: str = "") -> dict:
        now = time.time()
        tag_str = ",".join(tags) if tags else ""
        with closing(_open()) as conn:
            existing = conn.execute("SELECT key FROM memories WHERE key=?", (key,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE memories SET value=?, tags=?, priority=?, project=?, updated_at=? WHERE key=?",
                    (value, tag_str, priority, project, now, key))
            else:
                conn.execute(
                    "INSERT INTO memories (key, value, tags, priority, project, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                    (key, value, tag_str, priority, project, now, now))
            conn.commit()
        return {"key": key, "action": "updated" if existing else "created"}

    def recall(self, key: str) -> dict | None:
        with closing(_open()) as conn:
            row = conn.execute("SELECT * FROM memories WHERE key=?", (key,)).fetchone()
            if not row:
                return None
            conn.execute("UPDATE memories SET access_count = access_count + 1 WHERE key=?", (key,))
            conn.commit()
            return dict(row)

    def search(self, query: str = "", tags: list[str] = None, project: str = "",
               priority: str = "", limit: int = 20) -> list[dict]:
        conditions = []
        params = []
        if query:
            conditions.append("(key LIKE ? OR value LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])
        if tags:
            for tag in tags:
                conditions.append("tags LIKE ?")
                params.append(f"%{tag}%")
        if project:
            conditions.append("project LIKE ?")
            params.append(f"%{project}%")
        if priority:
            conditions.append("priority=?")
            params.append(priority)

        where = " AND ".join(conditions) if conditions else "1=1"
        with closing(_open()) as conn:
            rows = conn.execute(
                f"SELECT * FROM memories WHERE {where} ORDER BY updated_at DESC LIMIT ?",
                params + [limit]).fetchall()
        return [dict(r) for r in rows]

    def forget(self, key: str) -> bool:
        with closing(_open()) as conn:
            cursor = conn.execute("DELETE FROM memories WHERE key=?", (key,))
            conn.commit()
            return cursor.rowcount > 0

    def log_milestone(self, event: str, project: str = "", tags: list[str] = None):
        tag_str = ",".join(tags) if tags else ""
        with closing(_open()) as conn:
            conn.execute(
                "INSERT INTO milestones (event, project, tags, timestamp) VALUES (?,?,?,?)",
                (event, project, tag_str, time.time()))
            conn.commit()

    def get_milestones(self, project: str = "", limit: int = 20) -> list[dict]:
        with closing(_open()) as conn:
            if project:
                rows = conn.execute(
                    "SELECT * FROM milestones WHERE project LIKE ? ORDER BY timestamp DESC LIMIT ?",
                    (f"%{project}%", limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM milestones ORDER BY timestamp DESC LIMIT ?",
                    (limit,)).fetchall()
        return [dict(r) for r in rows]

    def context_load(self, project: str = "") -> dict:
        """Load everything the AI needs to resume work."""
        critical = self.search(priority="critical", limit=10)
        high = self.search(priority="high", limit=10)
        recent = self.search(limit=10)
        active_tasks = self.search(tags=["task-active"], limit=10)
        blockers = self.search(tags=["blocker"], limit=10)
        milestones = self.get_milestones(project=project, limit=5)

        if project:
            project_memories = self.search(project=project, limit=15)
        else:
            project_memories = []

        patterns = self.search(tags=["pattern"], limit=10)

        return {
            "critical": critical,
            "high_priority": high,
            "active_tasks": active_tasks,
            "blockers": blockers,
            "recent_memories": recent,
            "project_memories": project_memories,
            "patterns": patterns,
            "milestones": milestones,
        }

    def stats(self) -> dict:
        with closing(_open()) as conn:
            total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            by_priority = {}
            for row in conn.execute("SELECT priority, COUNT(*) as cnt FROM memories GROUP BY priority"):
                by_priority[row[0]] = row[1]
            milestone_count = conn.execute("SELECT COUNT(*) FROM milestones").fetchone()[0]
            projects = [r[0] for r in conn.execute(
                "SELECT DISTINCT project FROM memories WHERE project != '' ORDER BY project")]
        return {
            "total_memories": total,
            "by_priority": by_priority,
            "milestones": milestone_count,
            "projects": projects,
        }


_memory = MemoryStore()


def _register_hooks():
    from sassymcp.modules._hooks import register_hook

    register_hook(
        name="session_startup",
        module="memory",
        description="Session startup protocol — load context, check handoffs, resume work",
        triggers=["start session", "new session", "what were we doing", "continue", "pick up where",
                  "resume work", "what's the status"],
        instructions="""
## Session Startup Playbook

On EVERY new session, do this BEFORE anything else:

### 1. Load Context
Call sassy_memory_context — returns critical memories, active tasks, blockers,
recent milestones, and patterns. This is your situational awareness.

### 2. Check Handoffs
Call sassy_crosslink_recv channel="task-handoff" — check for continuation payloads
from the previous session. If one exists, resume from its next_steps immediately.

### 3. Infer Intent
If the user's first message is a greeting ("hey", "what's up"), check active tasks
and recent milestones to suggest what to work on.
If the user's first message is a task, check memory for relevant patterns and prior work.

### NEVER:
- Ask "what were we working on?" — figure it out from memory + crosslink
- Start from scratch on a task that has prior state
- Ignore patterns learned from previous sessions
""",
    )

    register_hook(
        name="session_handoff",
        module="memory",
        description="Session handoff — save state for the next session to pick up",
        triggers=["continue later", "save progress", "hand off", "pick this up later",
                  "context getting long", "running out of context"],
        instructions="""
## Session Handoff Playbook

When ending a session or running low on context:

### 1. Write Handoff
Call sassy_crosslink_send with channel="task-handoff" and a JSON payload:
{
  "task": "what you were doing",
  "status": "in-progress | blocked | needs-review",
  "completed": ["list of done items"],
  "next_steps": ["ordered list of what to do next"],
  "blockers": ["anything blocking progress"],
  "files_touched": ["files modified this session"],
  "context_notes": "critical context the next session needs"
}

### 2. Update Memory
Call sassy_memory_remember for the task state:
- key: task_<concept>_<project>_state
- priority: high
- tags: task-active, handoff

### 3. Log Milestone
Call sassy_memory_log with what was accomplished.

### 4. Tell the User
"Handoff written. Next session picks up at [specific point]."
""",
    )

try:
    _register_hooks()
except Exception:
    pass


def register(server):
    """Register persistent memory tools."""

    @server.tool()
    def sassy_memory_remember(
        key: str,
        value: str,
        tags: str = "",
        priority: str = "normal",
        project: str = "",
    ) -> str:
        """Store a memory that persists across sessions.

        key: unique identifier (use naming convention: task_<concept>_<project>_state,
             pattern_<concept>, blocker_<concept>_<project>, decision_<concept>)
        value: the memory content (what happened, what was learned, current state)
        tags: comma-separated (e.g. "task-active,tls,security")
        priority: critical | high | normal | low
        project: project name (e.g. "sassymcp", "sassy-browser")
        """
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        result = _memory.remember(key, value, tag_list, priority, project)
        return json.dumps(result)

    @server.tool()
    def sassy_memory_recall(key: str) -> str:
        """Recall a specific memory by key."""
        mem = _memory.recall(key)
        if not mem:
            return json.dumps({"error": f"No memory found for key: {key}"})
        return json.dumps(mem, default=str)

    @server.tool()
    def sassy_memory_search(
        query: str = "",
        tags: str = "",
        project: str = "",
        priority: str = "",
        limit: int = 20,
    ) -> str:
        """Search memories by text, tags, project, or priority.

        query: free text search across keys and values
        tags: comma-separated tag filter (e.g. "pattern,tls")
        project: filter by project name
        priority: filter by priority level
        """
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
        results = _memory.search(query, tag_list, project, priority, min(limit, 50))
        return json.dumps({"results": results, "count": len(results)}, default=str)

    @server.tool()
    def sassy_memory_forget(key: str) -> str:
        """Delete a memory. Use when information is no longer relevant."""
        if _memory.forget(key):
            return json.dumps({"forgotten": key})
        return json.dumps({"error": f"No memory found for key: {key}"})

    @server.tool()
    def sassy_memory_context(project: str = "") -> str:
        """Load full context for session startup. Returns critical memories,
        active tasks, blockers, recent milestones, and learned patterns.

        Call this at the START of every session. This is how you know
        what's been happening and what needs to continue.

        project: optional filter to focus on a specific project
        """
        ctx = _memory.context_load(project)
        return json.dumps(ctx, default=str, indent=2)

    @server.tool()
    def sassy_memory_log(event: str, project: str = "", tags: str = "") -> str:
        """Log a milestone event. Use for significant completions, decisions, or changes.

        event: what happened (e.g. "deployed v1.0", "fixed TLS cert chain", "merged PR #42")
        project: which project
        tags: comma-separated (e.g. "deployment,milestone")
        """
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        _memory.log_milestone(event, project, tag_list)
        return json.dumps({"logged": event, "project": project})

    @server.tool()
    def sassy_memory_milestones(project: str = "", limit: int = 20) -> str:
        """View recent milestones, optionally filtered by project."""
        milestones = _memory.get_milestones(project, min(limit, 100))
        return json.dumps({"milestones": milestones, "count": len(milestones)}, default=str)

    @server.tool()
    def sassy_memory_handoff(
        task: str,
        status: str = "in-progress",
        completed: str = "",
        next_steps: str = "",
        blockers: str = "",
        files_touched: str = "",
        project: str = "",
        context_notes: str = "",
    ) -> str:
        """Write a session handoff — saves state to BOTH memory and crosslink.

        The next session calls sassy_memory_context and sassy_crosslink_recv
        to pick up exactly where you left off.

        task: what you were working on
        status: in-progress | blocked | needs-review | paused | completed
        completed: comma-separated list of completed items
        next_steps: comma-separated ordered list of what to do next
        blockers: comma-separated list of blockers
        files_touched: comma-separated list of files modified
        project: project name
        context_notes: critical context the next session needs
        """
        handoff = {
            "task": task,
            "status": status,
            "completed": [s.strip() for s in completed.split(",") if s.strip()] if completed else [],
            "next_steps": [s.strip() for s in next_steps.split(",") if s.strip()] if next_steps else [],
            "blockers": [s.strip() for s in blockers.split(",") if s.strip()] if blockers else [],
            "files_touched": [s.strip() for s in files_touched.split(",") if s.strip()] if files_touched else [],
            "project": project,
            "context_notes": context_notes,
            "timestamp": time.time(),
        }

        # Save to memory
        key = f"task_{task.lower().replace(' ', '_')[:40]}_{project}_state" if project else f"task_{task.lower().replace(' ', '_')[:40]}_state"
        _memory.remember(key, json.dumps(handoff), tags=["task-active", "handoff"],
                         priority="high", project=project)

        # Save to crosslink for immediate pickup
        try:
            from sassymcp.modules.crosslink import _post_message
            _post_message("memory", "task-handoff", json.dumps(handoff))
        except Exception:
            pass

        # Log milestone
        _memory.log_milestone(f"Handoff: {task} ({status})", project, ["handoff"])

        return json.dumps({
            "handoff_saved": True,
            "memory_key": key,
            "crosslink_channel": "task-handoff",
            "next_session": "Call sassy_memory_context to resume.",
        }, indent=2)

    @server.tool()
    def sassy_memory_stats() -> str:
        """Memory system stats: total memories, priorities, projects, milestones."""
        return json.dumps(_memory.stats())

    logger.info("Memory system loaded (persistent cross-session)")
