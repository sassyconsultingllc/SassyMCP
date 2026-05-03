"""Multi-process-safe SQLite connection helper for SassyMCP.

Every SassyMCP SQLite database lives in ~/.sassymcp/ (or $SASSYMCP_HOME).
Multiple sassymcp.exe processes — one per connected MCP client — write to
these DBs concurrently. WAL mode + a generous busy_timeout makes that
safe; without them you get 'database is locked' errors and partial writes.

Pragmas applied:
  journal_mode = WAL          concurrent readers + one writer, no blocking
  synchronous  = NORMAL       fsync on checkpoint only — recommended pair for WAL
  busy_timeout = 5000 ms      writer waits up to 5s for the lock instead of erroring

WAL leaves '<db>-wal' and '<db>-shm' sidecar files alongside the main DB.
This is expected. Webview readers (sub-project #5) must keep both visible.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def open_db(path: Path, *, check_same_thread: bool = False) -> sqlite3.Connection:
    """Open a SassyMCP SQLite DB with multi-process-safe pragmas."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=check_same_thread, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn
