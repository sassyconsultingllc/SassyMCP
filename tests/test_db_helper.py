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


def test_open_db_switches_existing_delete_db_to_wal(tmp_path: Path):
    """Real-world migration path: a user's existing memory.db was created with
    the default journal_mode=DELETE. After upgrade, open_db must flip it to
    WAL on first reopen.
    """
    db = tmp_path / "legacy.db"
    # Seed the file with default (DELETE) journal mode and a real schema.
    seed = sqlite3.connect(str(db))
    try:
        seed.execute("CREATE TABLE t (x INT)")
        seed.commit()
        mode = seed.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "delete", f"seed sanity check: expected delete, got {mode}"
    finally:
        seed.close()

    # open_db should switch to WAL.
    conn = open_db(db)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal", f"expected wal after upgrade, got {mode}"
        # And the existing schema is still present.
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='t'").fetchone() is not None
    finally:
        conn.close()
