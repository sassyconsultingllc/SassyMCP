"""Unit tests for sassymcp._db.open_db()."""
import sqlite3
import threading
from pathlib import Path

from sassymcp._db import open_db


def test_open_db_sets_wal_journal_mode(tmp_path: Path):
    db = tmp_path / "test.db"
    conn = open_db(db)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal", f"expected WAL, got {mode}"
    finally:
        conn.close()


def test_open_db_sets_synchronous_normal(tmp_path: Path):
    db = tmp_path / "test.db"
    conn = open_db(db)
    try:
        s = conn.execute("PRAGMA synchronous").fetchone()[0]
        assert s == 1, f"expected synchronous=NORMAL (1), got {s}"
    finally:
        conn.close()


def test_open_db_sets_busy_timeout(tmp_path: Path):
    db = tmp_path / "test.db"
    conn = open_db(db)
    try:
        ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert ms == 5000, f"expected 5000ms, got {ms}"
    finally:
        conn.close()


def test_open_db_creates_parent_dirs(tmp_path: Path):
    db = tmp_path / "nested" / "deeper" / "test.db"
    conn = open_db(db)
    try:
        assert db.exists()
        assert db.parent.is_dir()
    finally:
        conn.close()


def test_open_db_check_same_thread_default_false(tmp_path: Path):
    db = tmp_path / "test.db"
    conn = open_db(db)
    try:
        result = []

        def query():
            result.append(conn.execute("SELECT 1").fetchone()[0])

        t = threading.Thread(target=query)
        t.start()
        t.join()
        assert result == [1]
    finally:
        conn.close()
