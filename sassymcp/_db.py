# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-2CIZVNXNEMLQ
"""Multi-process-safe SQLite connection helper for SassyMCP.

Every SassyMCP SQLite database lives in ~/.sassymcp/ (or $SASSYMCP_HOME).
Multiple sassymcp.exe processes — one per connected MCP client — write to
these DBs concurrently. WAL mode + a generous busy_timeout makes that
safe; without them you get 'database is locked' errors and partial writes.

Pragmas applied:
  journal_mode = WAL          concurrent readers + one writer, no blocking
  synchronous  = NORMAL       fsync on checkpoint only — recommended pair for WAL
  busy_timeout = 5000 ms      writer waits up to 5s for the lock instead of erroring
  timeout      = 5.0 s        connect-time wait to open the file (sqlite3.connect arg,
                              not a pragma — distinct from busy_timeout above)

WAL leaves '<db>-wal' and '<db>-shm' sidecar files alongside the main DB.
This is expected. Webview readers (sub-project #5) must keep both visible.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def open_db(path: Path, *, check_same_thread: bool = False) -> sqlite3.Connection:
    """Open a SassyMCP SQLite DB with multi-process-safe pragmas."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=check_same_thread, timeout=5.0)
    actual_mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    if actual_mode.lower() != "wal":
        conn.close()
        raise RuntimeError(
            f"open_db: WAL journal mode rejected for {path!r}; got {actual_mode!r}. "
            "WAL is required for multi-process safety; refusing to return a non-WAL connection."
        )
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn
