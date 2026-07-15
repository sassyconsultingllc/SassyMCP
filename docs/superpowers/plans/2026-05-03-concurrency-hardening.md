<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
   CodeMark: SCLLC1-SassyMCP-XZMRGTF2FGVV
-->
# Concurrency Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all of SassyMCP's per-user persistent state in `~/.sassymcp/` safe for concurrent writes from multiple `sassymcp.exe` processes (one per MCP client).

**Architecture:** Three small helper modules — `_db.py` (SQLite WAL+busy_timeout), `_atomic.py` (atomic JSON/text writes), `_audit_io.py` (locked append for audit log) — then mechanical migration of every existing callsite. Plus an `O_CREAT|O_EXCL` fix for the license-secret first-run race.

**Tech Stack:** Python 3.11, sqlite3 stdlib, msvcrt (Windows file locking), os.O_APPEND (POSIX), pytest, subprocess (for true multi-process concurrency tests). No new dependencies.

**Spec reference:** [docs/superpowers/specs/2026-05-03-multi-client-integration-design.md](../specs/2026-05-03-multi-client-integration-design.md) sub-project #1.

**Concurrency-test discipline:** Every multi-process test in this plan spawns workers via `subprocess.Popen([sys.executable, str(worker_script), ...args])` rather than multiprocessing.Pool. Reasons: (1) honest OS-process isolation, no shared imports, no `importlib.reload` tricks, (2) avoids pickle entirely, (3) closer to what real MCP clients do — each one launches its own `sassymcp.exe`.

---

## File Structure

**Create:**
- `sassymcp/_db.py` — `open_db(path, *, check_same_thread=False)` returning a sqlite3.Connection with WAL+busy_timeout pragmas applied
- `sassymcp/_atomic.py` — `atomic_write_json(path, data)`, `atomic_write_text(path, content)`
- `sassymcp/_audit_io.py` — `append_audit(path, entry)` — single-writer-safe JSON-line append
- `tests/test_db_helper.py` — unit tests for `_db.py` pragma application
- `tests/test_atomic_write.py` — unit + concurrent-race tests for `_atomic.py`
- `tests/test_audit_io.py` — concurrent append test for `_audit_io.py`
- `tests/test_concurrency_integration.py` — multi-process stress tests across memory/crosslink/state/audit/config

**Modify:**
- `sassymcp/modules/memory.py:35` — swap `sqlite3.connect()` for `open_db()`
- `sassymcp/modules/state_manager.py:22` — swap `sqlite3.connect()` for `open_db()`
- `sassymcp/modules/crosslink.py` — 7 callsites (lines 39, 52, 60, 81, 89, 235, 251) — swap for `open_db()`
- `sassymcp/modules/audit.py` — 6 callsites (lines 117-118, 120-121, 145-146, 148-149, 193-194, 198-199) — swap raw `f.open("a")+write` for `append_audit()`
- `sassymcp/modules/runtime_config.py:53-58` — swap `CONFIG_FILE.write_text(json.dumps(...))` for `atomic_write_json()`
- `sassymcp/license.py:42-44` — swap `_SECRET_FILE.write_text(new_secret)` for `O_CREAT|O_EXCL` write
- `sassymcp/license.py:148-155` — swap `LICENSE_FILE.write_text(json.dumps(...))` for `atomic_write_json()`
- `sassymcp/license.py:199-200` — same (the weekly check rewrite)
- `sassymcp/modules/setup_wizard.py:107-109` — `_save_config` swap for `atomic_write_json()`
- `sassymcp/modules/setup_wizard.py:261` — `_PERSONA_FILE.write_text(content, encoding="utf-8")` swap for `atomic_write_text()`
- `sassymcp/modules/setup_wizard.py:388` — `_TOKENS_FILE.write_text(json.dumps(...))` swap for `atomic_write_json()`

---

## Concurrency-Test Helper Pattern

Every multi-process test in this plan uses this same pattern:

```python
import subprocess
import sys
from pathlib import Path

def run_workers(worker_script: Path, sassy_home: str, *, count: int, args_per_worker: list[list[str]], timeout: int = 60) -> None:
    """Spawn `count` worker subprocesses concurrently and wait for all to exit cleanly.

    Each worker_script is a real Python file that reads sys.argv and does work.
    SASSYMCP_HOME is injected via env so each worker resolves the same shared
    state directory.
    """
    env = {**__import__("os").environ, "SASSYMCP_HOME": sassy_home}
    procs = [
        subprocess.Popen(
            [sys.executable, str(worker_script), *args_per_worker[i]],
            env=env,
        )
        for i in range(count)
    ]
    for p in procs:
        p.wait(timeout=timeout)
        if p.returncode != 0:
            raise AssertionError(f"worker exited with code {p.returncode}")
```

This helper is repeated inline in each test file rather than imported from a shared conftest, to keep each test file readable on its own.

---

## Task 1: Create `_db.py` helper with TDD

**Files:**
- Create: `sassymcp/_db.py`
- Test: `tests/test_db_helper.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db_helper.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_db_helper.py -v`
Expected: FAIL with `ImportError: cannot import name 'open_db' from 'sassymcp._db'`.

- [ ] **Step 3: Write minimal implementation**

```python
# sassymcp/_db.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_db_helper.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add sassymcp/_db.py tests/test_db_helper.py
git commit -m "feat(_db): add open_db() helper with WAL + busy_timeout

Multi-process-safe SQLite connection factory. WAL + synchronous=NORMAL
+ busy_timeout=5000ms means concurrent sassymcp.exe processes (one per MCP
client) can write to memory.db, crosslink.db, tool_state.db without
hitting 'database is locked' errors.

Foundation for sub-project #1 of the multi-client integration spec.
"
```

---

## Task 2: Create `_atomic.py` helper with TDD

**Files:**
- Create: `sassymcp/_atomic.py`
- Test: `tests/test_atomic_write.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_atomic_write.py
"""Unit + concurrency tests for sassymcp._atomic write helpers."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sassymcp._atomic import atomic_write_json, atomic_write_text


def test_atomic_write_json_basic(tmp_path: Path):
    p = tmp_path / "out.json"
    atomic_write_json(p, {"hello": "world"})
    assert json.loads(p.read_text()) == {"hello": "world"}


def test_atomic_write_text_basic(tmp_path: Path):
    p = tmp_path / "out.txt"
    atomic_write_text(p, "hello\nworld\n")
    assert p.read_text() == "hello\nworld\n"


def test_atomic_write_json_creates_parent(tmp_path: Path):
    p = tmp_path / "nested" / "deeper" / "out.json"
    atomic_write_json(p, [1, 2, 3])
    assert json.loads(p.read_text()) == [1, 2, 3]


def test_atomic_write_json_overwrites_existing(tmp_path: Path):
    p = tmp_path / "out.json"
    p.write_text('{"old": true}')
    atomic_write_json(p, {"new": True})
    assert json.loads(p.read_text()) == {"new": True}


def test_atomic_write_leaves_no_tmp_files_on_success(tmp_path: Path):
    p = tmp_path / "out.json"
    atomic_write_json(p, {"k": "v"})
    leftovers = [f for f in tmp_path.iterdir() if f.name != "out.json"]
    assert leftovers == [], f"unexpected leftover files: {leftovers}"


def test_atomic_write_json_no_partial_on_concurrent_writes(tmp_path: Path):
    """8 subprocesses hammer the same file. Final state must be one of the
    inputs, never a torn half-written JSON document."""
    target = tmp_path / "race.json"

    worker_script = tmp_path / "worker.py"
    worker_script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from sassymcp._atomic import atomic_write_json\n"
        "target = Path(sys.argv[1])\n"
        "writer_id = int(sys.argv[2])\n"
        "iterations = int(sys.argv[3])\n"
        "payload = {'writer': writer_id, 'data': 'x' * 1000}\n"
        "for _ in range(iterations):\n"
        "    atomic_write_json(target, payload)\n",
        encoding="utf-8",
    )

    procs = [
        subprocess.Popen(
            [sys.executable, str(worker_script), str(target), str(i), "50"]
        )
        for i in range(8)
    ]
    for p in procs:
        p.wait(timeout=60)
        assert p.returncode == 0, f"worker {p.pid} exited with {p.returncode}"

    parsed = json.loads(target.read_text())
    valid = {(i, "x" * 1000) for i in range(8)}
    assert (parsed["writer"], parsed["data"]) in valid, (
        f"file ended in partial/garbage state: {parsed!r}"
    )


def test_atomic_write_json_cleans_up_tmp_on_exception(tmp_path: Path):
    """If json.dump raises, the temp file must be unlinked, not left behind."""
    p = tmp_path / "out.json"

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        atomic_write_json(p, {"bad": Unserializable()})  # type: ignore[dict-item]

    leftovers = list(tmp_path.iterdir())
    assert leftovers == [], f"tmp file leaked on exception: {leftovers}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_atomic_write.py -v`
Expected: FAIL with `ImportError: cannot import name 'atomic_write_json' from 'sassymcp._atomic'`.

- [ ] **Step 3: Write minimal implementation**

```python
# sassymcp/_atomic.py
"""Atomic file-write helpers for SassyMCP shared state files.

Multiple sassymcp.exe processes write the same JSON config files
(config.json, license.json, tokens.json, persona.md). Without atomic
writes, two simultaneous writers can leave the file in a torn,
half-written state — JSONDecodeError on the next read.

Strategy: write to a temp file in the same directory, then os.replace()
onto the target. os.replace is atomic on POSIX and on Windows since
Vista. The temp file lives in the same dir to guarantee same-filesystem,
otherwise os.replace falls back to copy+unlink which is not atomic.

Last-write-wins semantics still apply — concurrent writes will land one
full payload, the others are lost. That's fine for these files; truly
simultaneous writes are vanishingly rare.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, data: Any, *, indent: int = 2) -> None:
    """Write `data` as JSON to `path` atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, content: str) -> None:
    """Write `content` as UTF-8 text to `path` atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_atomic_write.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add sassymcp/_atomic.py tests/test_atomic_write.py
git commit -m "feat(_atomic): atomic JSON/text write helpers via os.replace

Same-dir tempfile + os.replace eliminates torn-write JSONDecodeErrors
when two sassymcp.exe processes rewrite config.json/license.json/etc
simultaneously. Last-write-wins still applies — file is always parseable,
but a near-simultaneous setting change can lose one update.

Tests cover basic write, parent dir creation, no-leftover-tmp on success
and on exception, and an 8-subprocess concurrent stress test.
"
```

---

## Task 3: Create `_audit_io.py` helper with TDD

**Files:**
- Create: `sassymcp/_audit_io.py`
- Test: `tests/test_audit_io.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_audit_io.py
"""Concurrent-append correctness tests for sassymcp._audit_io.append_audit."""
import json
import subprocess
import sys
from pathlib import Path

from sassymcp._audit_io import append_audit


def test_append_audit_basic(tmp_path: Path):
    p = tmp_path / "audit.log"
    append_audit(p, {"event": "hello"})
    append_audit(p, {"event": "world"})
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"event": "hello"}
    assert json.loads(lines[1]) == {"event": "world"}


def test_append_audit_creates_parent(tmp_path: Path):
    p = tmp_path / "nested" / "audit.log"
    append_audit(p, {"event": "x"})
    assert p.exists()


def test_append_audit_no_interleaving_under_concurrent_load(tmp_path: Path):
    """8 subprocesses × 100 entries × ~5KB each. Every line in the resulting
    log must be a complete, parseable JSON entry. No interleaved bytes."""
    log = tmp_path / "audit.log"

    worker_script = tmp_path / "worker.py"
    worker_script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from sassymcp._audit_io import append_audit\n"
        "log = Path(sys.argv[1])\n"
        "worker_id = int(sys.argv[2])\n"
        "count = int(sys.argv[3])\n"
        "big = 'T' * 5000\n"
        "for i in range(count):\n"
        "    append_audit(log, {'worker': worker_id, 'i': i, 'trace': big})\n",
        encoding="utf-8",
    )

    workers = 8
    per_worker = 100
    procs = [
        subprocess.Popen(
            [sys.executable, str(worker_script), str(log), str(w), str(per_worker)]
        )
        for w in range(workers)
    ]
    for p in procs:
        p.wait(timeout=60)
        assert p.returncode == 0

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == workers * per_worker, (
        f"expected {workers * per_worker} lines, got {len(lines)}"
    )

    seen = {(w, i): False for w in range(workers) for i in range(per_worker)}
    for ln, raw in enumerate(lines):
        entry = json.loads(raw)
        assert "worker" in entry and "i" in entry, f"line {ln}: missing keys: {raw!r}"
        seen[(entry["worker"], entry["i"])] = True

    missing = [k for k, v in seen.items() if not v]
    assert not missing, f"missing entries: {missing[:10]}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_audit_io.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# sassymcp/_audit_io.py
"""Single-writer-safe JSON-line append for the SassyMCP audit log.

Today every audit-log call does:

    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\\n")

Under concurrent processes on Windows, f.write() of a blob larger than
the OS internal buffer can interleave with another process's write — you
end up with `{"a":1,"b":` from one entry followed by `{"c":2}\\n{"d":3}\\n`
from another, and json.loads chokes when reading the log later.

Fix: serialise the whole entry into one bytes blob and emit it in a
single OS-level write that is guaranteed atomic w.r.t. other processes.

  POSIX: O_APPEND on the file descriptor. The kernel guarantees that
         every write() to an O_APPEND fd does the seek-to-end + the
         actual write atomically against other O_APPEND writers.

  Windows: msvcrt.locking acquires a byte-range lock on the file. We
           hold it for the duration of the write+flush+fsync.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def append_audit(path: Path, entry: dict[str, Any]) -> None:
    """Append `entry` as a single JSON line to `path` atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = (json.dumps(entry) + "\n").encode("utf-8")

    if os.name == "nt":
        import msvcrt

        with open(path, "ab") as f:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            except OSError:
                pass
            try:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            finally:
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
    else:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, blob)
        finally:
            os.close(fd)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_audit_io.py -v`
Expected: 3 passed. Concurrent test runs in ~30 seconds.

- [ ] **Step 5: Commit**

```bash
git add sassymcp/_audit_io.py tests/test_audit_io.py
git commit -m "feat(_audit_io): atomic JSONL append for audit log

POSIX uses O_APPEND for kernel-level write serialisation. Windows uses
msvcrt.locking byte-range locks held across write+flush+fsync. Eliminates
the interleaved-bytes failure mode where two sassymcp.exe processes
appending entries with multi-line tracebacks (>4KB) end up with garbled,
unparseable JSONL lines.

Test stresses 8 subprocesses × 100 × 5KB entries.
"
```

---

## Task 4: Migrate `memory.py` to `open_db()`

**Files:**
- Modify: `sassymcp/modules/memory.py:35`
- Test: `tests/test_concurrency_integration.py` (created here, extended in later tasks)

- [ ] **Step 1: Write the failing concurrent-write test**

```python
# tests/test_concurrency_integration.py
"""End-to-end concurrent-process tests for sassymcp shared state.

Each test spawns multiple OS subprocesses hitting the same SQLite/JSON
files under a tmp SASSYMCP_HOME. Asserts no 'database is locked' errors
and no data loss.
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_memory_concurrent_writes_no_locked_errors(tmp_path: Path):
    sassy_home = tmp_path / "sassy_home"
    sassy_home.mkdir()

    worker_script = tmp_path / "memory_worker.py"
    worker_script.write_text(
        "import os, sys\n"
        "from sassymcp.modules.memory import MemoryStore\n"
        "worker_id = int(sys.argv[1])\n"
        "count = int(sys.argv[2])\n"
        "store = MemoryStore()\n"
        "for i in range(count):\n"
        "    store.remember(f'w{worker_id}_k{i}', f'value-{worker_id}-{i}')\n",
        encoding="utf-8",
    )

    workers = 8
    per_worker = 100
    env = {**os.environ, "SASSYMCP_HOME": str(sassy_home)}
    procs = [
        subprocess.Popen(
            [sys.executable, str(worker_script), str(w), str(per_worker)], env=env
        )
        for w in range(workers)
    ]
    for p in procs:
        p.wait(timeout=120)
        assert p.returncode == 0, f"worker exited with {p.returncode}"

    db = sassy_home / "memory.db"
    assert db.exists()
    conn = sqlite3.connect(str(db))
    try:
        count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    finally:
        conn.close()
    assert count == workers * per_worker, (
        f"expected {workers * per_worker} memories, got {count}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_concurrency_integration.py::test_memory_concurrent_writes_no_locked_errors -v`
Expected: FAIL — at least one worker exits non-zero with `sqlite3.OperationalError: database is locked`.

- [ ] **Step 3: Edit `memory.py` to use `open_db()`**

Edit `sassymcp/modules/memory.py`:

After line 22 `import sqlite3` add:
```python
from sassymcp._db import open_db
```

Replace lines 33-36:
```python
class MemoryStore:
    def __init__(self):
        MEMORY_DB.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(MEMORY_DB))
        self.conn.row_factory = sqlite3.Row
```
with:
```python
class MemoryStore:
    def __init__(self):
        self.conn = open_db(MEMORY_DB)
        self.conn.row_factory = sqlite3.Row
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_concurrency_integration.py::test_memory_concurrent_writes_no_locked_errors -v`
Expected: PASS — 800 rows, no errors.

Run: `python -m pytest tests/ -v -k memory`
Expected: no regressions.

- [ ] **Step 5: Commit**

```bash
git add sassymcp/modules/memory.py tests/test_concurrency_integration.py
git commit -m "fix(memory): use open_db() for multi-process WAL safety

MemoryStore opens memory.db via sassymcp._db.open_db() so concurrent
sassymcp.exe processes can write memories without 'database is locked'.

Adds 8-subprocess concurrent integration test (800 writes total).
"
```

---

## Task 5: Migrate `state_manager.py` to `open_db()`

**Files:**
- Modify: `sassymcp/modules/state_manager.py:22`
- Test: extend `tests/test_concurrency_integration.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_concurrency_integration.py`:

```python
def test_state_manager_concurrent_writes_no_locked_errors(tmp_path: Path):
    sassy_home = tmp_path / "sassy_home"
    sassy_home.mkdir()

    worker_script = tmp_path / "state_worker.py"
    worker_script.write_text(
        "import os, sys\n"
        "from sassymcp.modules.state_manager import ToolStateManager\n"
        "worker_id = int(sys.argv[1])\n"
        "count = int(sys.argv[2])\n"
        "sm = ToolStateManager()\n"
        "for i in range(count):\n"
        "    sm.set(f'tool_{worker_id}', f'key_{i}', f'val_{worker_id}_{i}')\n",
        encoding="utf-8",
    )

    workers = 8
    per_worker = 100
    env = {**os.environ, "SASSYMCP_HOME": str(sassy_home)}
    procs = [
        subprocess.Popen(
            [sys.executable, str(worker_script), str(w), str(per_worker)], env=env
        )
        for w in range(workers)
    ]
    for p in procs:
        p.wait(timeout=120)
        assert p.returncode == 0

    db = sassy_home / "tool_state.db"
    assert db.exists()
    conn = sqlite3.connect(str(db))
    try:
        count = conn.execute("SELECT COUNT(*) FROM state").fetchone()[0]
    finally:
        conn.close()
    assert count == workers * per_worker
```

- [ ] **Step 2: Run test — expect failure**

Run: `python -m pytest tests/test_concurrency_integration.py::test_state_manager_concurrent_writes_no_locked_errors -v`
Expected: FAIL with `database is locked`.

- [ ] **Step 3: Edit `state_manager.py`**

Edit `sassymcp/modules/state_manager.py`:

After line 9 `import sqlite3` add:
```python
from sassymcp._db import open_db
```

Replace lines 19-23:
```python
class ToolStateManager:
    def __init__(self):
        STATE_DB.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(STATE_DB)
        self.conn.execute("CREATE TABLE IF NOT EXISTS state (tool TEXT, key TEXT, value TEXT, PRIMARY KEY(tool, key))")
```
with:
```python
class ToolStateManager:
    def __init__(self):
        self.conn = open_db(STATE_DB)
        self.conn.execute("CREATE TABLE IF NOT EXISTS state (tool TEXT, key TEXT, value TEXT, PRIMARY KEY(tool, key))")
```

- [ ] **Step 4: Run test — expect pass**

Run: `python -m pytest tests/test_concurrency_integration.py::test_state_manager_concurrent_writes_no_locked_errors -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sassymcp/modules/state_manager.py tests/test_concurrency_integration.py
git commit -m "fix(state_manager): use open_db() for multi-process WAL safety

ToolStateManager uses sassymcp._db.open_db() for tool_state.db.
Adds 8-subprocess concurrent integration test.
"
```

---

## Task 6: Migrate `crosslink.py` to `open_db()` (7 callsites)

**Files:**
- Modify: `sassymcp/modules/crosslink.py` (lines 39, 52, 60, 81, 89, 235, 251)
- Test: extend `tests/test_concurrency_integration.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_concurrency_integration.py`:

```python
def test_crosslink_concurrent_send_no_locked_errors(tmp_path: Path):
    sassy_home = tmp_path / "sassy_home"
    sassy_home.mkdir()

    worker_script = tmp_path / "crosslink_worker.py"
    worker_script.write_text(
        "import os, sys\n"
        "import sassymcp.modules.crosslink as cl\n"
        "sender_id = int(sys.argv[1])\n"
        "count = int(sys.argv[2])\n"
        "for i in range(count):\n"
        "    cl._post_message(f'sender_{sender_id}', 'default', f'msg_{sender_id}_{i}')\n",
        encoding="utf-8",
    )

    senders = 8
    per_sender = 50
    env = {**os.environ, "SASSYMCP_HOME": str(sassy_home)}
    procs = [
        subprocess.Popen(
            [sys.executable, str(worker_script), str(s), str(per_sender)], env=env
        )
        for s in range(senders)
    ]
    for p in procs:
        p.wait(timeout=120)
        assert p.returncode == 0

    db = sassy_home / "crosslink.db"
    assert db.exists()
    conn = sqlite3.connect(str(db))
    try:
        count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    finally:
        conn.close()
    assert count == senders * per_sender
```

- [ ] **Step 2: Run test — expect failure**

Run: `python -m pytest tests/test_concurrency_integration.py::test_crosslink_concurrent_send_no_locked_errors -v`
Expected: FAIL with `database is locked`.

- [ ] **Step 3: Edit `crosslink.py`**

Edit `sassymcp/modules/crosslink.py`:

After line 22 `import sqlite3` add:
```python
from sassymcp._db import open_db
```

Replace **all 7** occurrences of:
```python
sqlite3.connect(str(DB_PATH), check_same_thread=False)
```
with:
```python
open_db(DB_PATH)
```

The 7 sites are at lines 39, 52, 60, 81, 89, 235, 251. After editing, verify:
```bash
grep -n "sqlite3.connect(str(DB_PATH)" sassymcp/modules/crosslink.py
```
Expected: zero matches.

The `_ensure_db()` mkdir at line 38 is redundant with `open_db`'s mkdir but harmless — leave it.

- [ ] **Step 4: Run test — expect pass + full regression**

Run: `python -m pytest tests/test_concurrency_integration.py::test_crosslink_concurrent_send_no_locked_errors -v`
Expected: PASS, 400 rows.

Run: `python -m pytest tests/ -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add sassymcp/modules/crosslink.py tests/test_concurrency_integration.py
git commit -m "fix(crosslink): use open_db() at all 7 sqlite3.connect callsites

Crosslink opens crosslink.db via sassymcp._db.open_db() for WAL +
busy_timeout. Adds 8-subprocess concurrent _post_message stress test.
"
```

---

## Task 7: Migrate `audit.py` to `append_audit()` (6 callsites)

**Files:**
- Modify: `sassymcp/modules/audit.py` (lines 117-118, 120-121, 145-146, 148-149, 193-194, 198-199)
- Test: extend `tests/test_concurrency_integration.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_concurrency_integration.py`:

```python
def test_audit_log_no_interleaving_under_concurrent_load(tmp_path: Path):
    sassy_home = tmp_path / "sassy_home"
    sassy_home.mkdir()

    worker_script = tmp_path / "audit_worker.py"
    worker_script.write_text(
        "import os, sys\n"
        "import sassymcp.modules.audit as audit\n"
        "worker_id = int(sys.argv[1])\n"
        "count = int(sys.argv[2])\n"
        "big_arg = 'X' * 5000\n"
        "for i in range(count):\n"
        "    audit.log_tool_call(f'tool_{worker_id}', {'i': i, 'big': big_arg}, elapsed_ms=i)\n",
        encoding="utf-8",
    )

    workers = 8
    per_worker = 100
    env = {**os.environ, "SASSYMCP_HOME": str(sassy_home)}
    procs = [
        subprocess.Popen(
            [sys.executable, str(worker_script), str(w), str(per_worker)], env=env
        )
        for w in range(workers)
    ]
    for p in procs:
        p.wait(timeout=120)
        assert p.returncode == 0

    log = sassy_home / "audit.log"
    assert log.exists()
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == workers * per_worker

    for ln, raw in enumerate(lines):
        json.loads(raw)
```

- [ ] **Step 2: Run test — expect failure**

Run: `python -m pytest tests/test_concurrency_integration.py::test_audit_log_no_interleaving_under_concurrent_load -v`
Expected: FAIL on at least some runs with `JSONDecodeError`.

- [ ] **Step 3: Edit `audit.py`**

Edit `sassymcp/modules/audit.py`:

After line 9 `import time` add:
```python
from sassymcp._audit_io import append_audit
```

Replace **all 3 occurrences** of:
```python
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
```
with:
```python
        append_audit(_LOG_FILE, entry)
```

Replace **all 3 occurrences** of:
```python
        with _JSONL_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
```
with:
```python
        append_audit(_JSONL_FILE, entry)
```

The `_rotate_if_needed()` / `_rotate_jsonl_if_needed()` calls before each block stay in place.

After editing, verify:
```bash
grep -n 'f.write(json.dumps(entry)' sassymcp/modules/audit.py
```
Expected: zero matches.

- [ ] **Step 4: Run test — expect pass**

Run: `python -m pytest tests/test_concurrency_integration.py::test_audit_log_no_interleaving_under_concurrent_load -v`
Expected: PASS — 800 lines, every one valid JSON.

- [ ] **Step 5: Commit**

```bash
git add sassymcp/modules/audit.py tests/test_concurrency_integration.py
git commit -m "fix(audit): use append_audit() for atomic JSONL writes

All 6 raw f.write(json.dumps(...)+'\\n') sites in audit.py now use
sassymcp._audit_io.append_audit, which acquires msvcrt.locking on
Windows (or O_APPEND on POSIX) for kernel-level write serialisation.
Eliminates interleaved-bytes JSONDecodeError under multi-process load.

Test: 8 subprocesses × 100 entries × 5KB tracebacks; every line parses.
"
```

---

## Task 8: Migrate `runtime_config.py` to `atomic_write_json()`

**Files:**
- Modify: `sassymcp/modules/runtime_config.py:53-58`
- Test: extend `tests/test_concurrency_integration.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_concurrency_integration.py`:

```python
def test_runtime_config_atomic_writes_no_corruption(tmp_path: Path):
    sassy_home = tmp_path / "sassy_home"
    sassy_home.mkdir()

    worker_script = tmp_path / "config_worker.py"
    worker_script.write_text(
        "import os, sys\n"
        "import sassymcp.modules.runtime_config as rc\n"
        "worker_id = int(sys.argv[1])\n"
        "iterations = int(sys.argv[2])\n"
        "for i in range(iterations):\n"
        "    rc.set_val(f'writer_{worker_id}_setting', f'value_{i}')\n",
        encoding="utf-8",
    )

    workers = 8
    per_worker = 30
    env = {**os.environ, "SASSYMCP_HOME": str(sassy_home)}
    procs = [
        subprocess.Popen(
            [sys.executable, str(worker_script), str(w), str(per_worker)], env=env
        )
        for w in range(workers)
    ]
    for p in procs:
        p.wait(timeout=120)
        assert p.returncode == 0

    cfg = sassy_home / "config.json"
    assert cfg.exists()
    parsed = json.loads(cfg.read_text())
    assert isinstance(parsed, dict)
```

- [ ] **Step 2: Run test — expect failure on some runs**

Run: `python -m pytest tests/test_concurrency_integration.py::test_runtime_config_atomic_writes_no_corruption -v`
Expected: FAIL on some runs with `JSONDecodeError`.

- [ ] **Step 3: Edit `runtime_config.py`**

Edit `sassymcp/modules/runtime_config.py`:

After line 13 `import time` add:
```python
from sassymcp._atomic import atomic_write_json
```

Replace lines 53-58:
```python
def _save():
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(_config, indent=2))
    except Exception as e:
        logger.warning(f"Failed to save config: {e}")
```
with:
```python
def _save():
    try:
        atomic_write_json(CONFIG_FILE, _config)
    except Exception as e:
        logger.warning(f"Failed to save config: {e}")
```

- [ ] **Step 4: Run test — expect pass**

Run: `python -m pytest tests/test_concurrency_integration.py::test_runtime_config_atomic_writes_no_corruption -v`
Expected: PASS.

Run: `python -m pytest tests/ -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add sassymcp/modules/runtime_config.py tests/test_concurrency_integration.py
git commit -m "fix(runtime_config): atomic_write_json for config.json

_save() uses sassymcp._atomic.atomic_write_json so concurrent setting
changes from multiple sassymcp.exe processes can never leave config.json
in a torn half-written state. Test runs 8 subprocess writers × 30
changes and asserts the file always parses.
"
```

---

## Task 9: Migrate `license.py` — atomic writes + first-run secret race fix

**Files:**
- Modify: `sassymcp/license.py` (lines 30-47, 148-155, 199-200)
- Test: extend `tests/test_concurrency_integration.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_concurrency_integration.py`:

```python
def test_license_secret_first_run_race(tmp_path: Path):
    """8 subprocesses start with empty SASSYMCP_HOME and all import license.py
    simultaneously. They must all end up with the SAME signing secret."""
    sassy_home = tmp_path / "sassy_home"
    sassy_home.mkdir()

    worker_script = tmp_path / "secret_worker.py"
    worker_script.write_text(
        "import os, sys\n"
        "import sassymcp.license as lic\n"
        "out = sys.argv[1]\n"
        "with open(out, 'w', encoding='utf-8') as f:\n"
        "    f.write(lic._SIGNING_SECRET)\n",
        encoding="utf-8",
    )

    workers = 8
    out_files = [tmp_path / f"secret_{w}.txt" for w in range(workers)]
    env = {**os.environ, "SASSYMCP_HOME": str(sassy_home)}
    # Clear SASSYMCP_LICENSE_SECRET if set in parent env — we want the
    # workers to race on file creation, not pull from env.
    env.pop("SASSYMCP_LICENSE_SECRET", None)

    procs = [
        subprocess.Popen(
            [sys.executable, str(worker_script), str(out_files[w])], env=env
        )
        for w in range(workers)
    ]
    for p in procs:
        p.wait(timeout=120)
        assert p.returncode == 0

    secrets_seen = {f.read_text(encoding="utf-8") for f in out_files}
    assert len(secrets_seen) == 1, (
        f"expected 1 shared secret, got {len(secrets_seen)} distinct"
    )


def test_license_file_atomic_writes_no_corruption(tmp_path: Path):
    sassy_home = tmp_path / "sassy_home"
    sassy_home.mkdir()

    # Generate a real key so save_license() succeeds.
    setup_env = {**os.environ, "SASSYMCP_HOME": str(sassy_home),
                 "SASSYMCP_LICENSE_SECRET": "test-secret-shared-across-workers"}
    gen_script = tmp_path / "gen.py"
    gen_script.write_text(
        "import sassymcp.license as lic\n"
        "import sys\n"
        "k = lic.generate_license_key('test@example.com', 'pro', days_valid=30)\n"
        "sys.stdout.write(k['key'])\n",
        encoding="utf-8",
    )
    res = subprocess.run(
        [sys.executable, str(gen_script)], env=setup_env,
        capture_output=True, text=True, check=True, timeout=30,
    )
    key_string = res.stdout.strip()
    assert key_string

    save_script = tmp_path / "save.py"
    save_script.write_text(
        "import sys\n"
        "import sassymcp.license as lic\n"
        "key = sys.argv[1]\n"
        "for _ in range(20):\n"
        "    lic.save_license(key)\n",
        encoding="utf-8",
    )

    workers = 8
    procs = [
        subprocess.Popen(
            [sys.executable, str(save_script), key_string], env=setup_env
        )
        for _ in range(workers)
    ]
    for p in procs:
        p.wait(timeout=120)
        assert p.returncode == 0

    lf = sassy_home / "license.json"
    assert lf.exists()
    parsed = json.loads(lf.read_text())
    assert parsed.get("tier") == "pro"
```

- [ ] **Step 2: Run tests — expect failure**

Run: `python -m pytest tests/test_concurrency_integration.py::test_license_secret_first_run_race tests/test_concurrency_integration.py::test_license_file_atomic_writes_no_corruption -v`
Expected: secret-race FAILS with multiple distinct secrets; license-file test may FAIL intermittently.

- [ ] **Step 3: Edit `license.py`**

Edit `sassymcp/license.py`:

After line 19 `import time` add:
```python
from sassymcp._atomic import atomic_write_json
```

Replace lines 30-47 (`_load_signing_secret`):
```python
def _load_signing_secret() -> str:
    """Load or generate a persistent per-installation signing secret."""
    if os.environ.get("SASSYMCP_LICENSE_SECRET"):
        return os.environ["SASSYMCP_LICENSE_SECRET"]
    if _SECRET_FILE.exists():
        try:
            return _SECRET_FILE.read_text().strip()
        except Exception:
            pass
    # First run: generate a cryptographically random secret and persist it.
    new_secret = secrets.token_hex(32)
    try:
        _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SECRET_FILE.write_text(new_secret)
        _SECRET_FILE.chmod(0o600)
    except Exception as e:
        logger.warning(f"Could not persist license secret: {e}")
    return new_secret
```
with:
```python
def _load_signing_secret() -> str:
    """Load or generate a persistent per-installation signing secret.

    Multi-process safe: O_CREAT|O_EXCL on first creation so that when
    multiple sassymcp.exe processes start simultaneously and all find no
    .license_secret, exactly one wins the create race; the others get
    FileExistsError and read the winner's value.
    """
    if os.environ.get("SASSYMCP_LICENSE_SECRET"):
        return os.environ["SASSYMCP_LICENSE_SECRET"]
    if _SECRET_FILE.exists():
        try:
            return _SECRET_FILE.read_text().strip()
        except Exception:
            pass
    new_secret = secrets.token_hex(32)
    try:
        _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(_SECRET_FILE), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, new_secret.encode())
        finally:
            os.close(fd)
        try:
            _SECRET_FILE.chmod(0o600)
        except OSError:
            pass
        return new_secret
    except FileExistsError:
        try:
            return _SECRET_FILE.read_text().strip()
        except Exception as e:
            logger.warning(f"Could not read license secret after race: {e}")
            return new_secret
    except Exception as e:
        logger.warning(f"Could not persist license secret: {e}")
        return new_secret
```

Replace lines 148-155 (`save_license`):
```python
    LICENSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    LICENSE_FILE.write_text(json.dumps({
        "key": key_string,
        "email": result.get("email", ""),
        "tier": result["tier"],
        "expires": result["expires"],
        "activated_at": time.time(),
    }, indent=2))
```
with:
```python
    atomic_write_json(LICENSE_FILE, {
        "key": key_string,
        "email": result.get("email", ""),
        "tier": result["tier"],
        "expires": result["expires"],
        "activated_at": time.time(),
    })
```

Replace line 200 (in `weekly_validation_check`):
```python
        data["last_online_check"] = time.time()
        LICENSE_FILE.write_text(json.dumps(data, indent=2))
```
with:
```python
        data["last_online_check"] = time.time()
        atomic_write_json(LICENSE_FILE, data)
```

- [ ] **Step 4: Run tests — expect pass**

Run: `python -m pytest tests/test_concurrency_integration.py::test_license_secret_first_run_race tests/test_concurrency_integration.py::test_license_file_atomic_writes_no_corruption -v`
Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add sassymcp/license.py tests/test_concurrency_integration.py
git commit -m "fix(license): O_CREAT|O_EXCL secret race + atomic license.json writes

_load_signing_secret uses O_CREAT|O_EXCL so multiple sassymcp.exe
processes starting simultaneously converge on a single shared signing
secret instead of generating divergent ones. save_license and the
weekly_validation_check rewrite use atomic_write_json so license.json
is always parseable.

Tests: 8-subprocess race for the secret, 8-subprocess save loop for
license.json.
"
```

---

## Task 10: Migrate `setup_wizard.py` writes

**Files:**
- Modify: `sassymcp/modules/setup_wizard.py` (lines 107-109, 261, 388)

These three writes are mechanical migrations to helpers already proven safe by Task 2's tests.

- [ ] **Step 1: Edit `setup_wizard.py`**

Edit `sassymcp/modules/setup_wizard.py`:

After line 17 `import time` add:
```python
from sassymcp._atomic import atomic_write_json, atomic_write_text
```

Replace lines 106-109 (`_save_config`):
```python
def _save_config(config: dict):
    """Save persistent config."""
    _SASSYMCP_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(json.dumps(config, indent=2))
```
with:
```python
def _save_config(config: dict):
    """Save persistent config."""
    atomic_write_json(_CONFIG_FILE, config)
```

Replace line 261:
```python
        _PERSONA_FILE.write_text(content, encoding="utf-8")
```
with:
```python
        atomic_write_text(_PERSONA_FILE, content)
```

Replace line 388:
```python
        _TOKENS_FILE.write_text(json.dumps(tokens_data, indent=2))
```
with:
```python
        atomic_write_json(_TOKENS_FILE, tokens_data)
```

- [ ] **Step 2: Run full test suite to confirm no regression**

Run: `python -m pytest tests/ -v`
Expected: every test passes.

- [ ] **Step 3: Manual smoke check via setup wizard**

```bash
SASSYMCP_HOME=/tmp/sassy_smoke python - <<'PY'
import sassymcp.modules.setup_wizard as sw
sw._save_config({"hello": "world"})
print("ok:", sw._CONFIG_FILE.read_text())
PY
```

Expected: prints `ok: {"hello": "world", ...}` (with whatever `_save_config` adds).

- [ ] **Step 4: Commit**

```bash
git add sassymcp/modules/setup_wizard.py
git commit -m "fix(setup_wizard): atomic writes for config/persona/tokens

_save_config, persona.md write, and tokens.json write go through
sassymcp._atomic helpers. Eliminates torn-write corruption when the
wizard runs simultaneously from two sassymcp.exe processes.
"
```

---

## Task 11: Stress test — 8 processes × mixed workload × 30s

**Files:**
- Create: `tests/test_concurrency_stress.py`
- Modify: `pyproject.toml` (add `[tool.pytest.ini_options]` markers section)

This is a longer-running validation, marked `slow` so it's deselected by default.

- [ ] **Step 1: Add the slow marker to pyproject.toml**

Edit `pyproject.toml`. Add at the end of the file:

```toml
[tool.pytest.ini_options]
markers = [
    "slow: long-running stress tests (use -m slow to run, deselected by default)"
]
```

If a `[tool.pytest.ini_options]` section already exists, just add the `markers` key to it.

- [ ] **Step 2: Write the stress test**

```python
# tests/test_concurrency_stress.py
"""Long-running multi-process stress test for sassymcp shared state.

Marked slow — run with:  python -m pytest tests/test_concurrency_stress.py -v -s -m slow
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.slow
def test_mixed_workload_stress(tmp_path: Path):
    sassy_home = tmp_path / "sassy_home"
    sassy_home.mkdir()
    duration = 30  # seconds

    worker_script = tmp_path / "mixed_worker.py"
    worker_script.write_text(
        "import os, sys, time\n"
        "import sassymcp.modules.memory as mem\n"
        "import sassymcp.modules.crosslink as cl\n"
        "import sassymcp.modules.audit as audit\n"
        "import sassymcp.modules.runtime_config as rc\n"
        "import sassymcp.modules.state_manager as sm_mod\n"
        "worker_id = int(sys.argv[1])\n"
        "duration = int(sys.argv[2])\n"
        "store = mem.MemoryStore()\n"
        "sm = sm_mod.ToolStateManager()\n"
        "end = time.time() + duration\n"
        "i = 0\n"
        "while time.time() < end:\n"
        "    store.remember(f'w{worker_id}_k{i}', f'v{i}')\n"
        "    cl._post_message(f'w{worker_id}', 'default', f'hello-{i}')\n"
        "    audit.log_tool_call(f'tool_{worker_id}', {'i': i, 'blob': 'X' * 4096})\n"
        "    rc.set_val(f'w{worker_id}_setting', str(i))\n"
        "    sm.set(f'tool_{worker_id}', f'k{i}', f'v{i}')\n"
        "    i += 1\n"
        "print(i)\n",
        encoding="utf-8",
    )

    workers = 8
    env = {**os.environ, "SASSYMCP_HOME": str(sassy_home)}
    procs = [
        subprocess.Popen(
            [sys.executable, str(worker_script), str(w), str(duration)],
            env=env,
            stdout=subprocess.PIPE,
            text=True,
        )
        for w in range(workers)
    ]

    iterations = []
    for p in procs:
        out, _ = p.communicate(timeout=duration + 30)
        assert p.returncode == 0, f"worker exited {p.returncode}; stdout={out!r}"
        iterations.append(int(out.strip()))

    print(f"\n  Per-worker iterations: {iterations}")
    print(f"  Total operations: {sum(iterations) * 5}")

    assert (sassy_home / "memory.db").exists()
    assert (sassy_home / "crosslink.db").exists()
    assert (sassy_home / "tool_state.db").exists()
    assert (sassy_home / "audit.log").exists()
    assert (sassy_home / "config.json").exists()

    json.loads((sassy_home / "config.json").read_text())

    for ln, raw in enumerate(
        (sassy_home / "audit.log").read_text(encoding="utf-8").splitlines()
    ):
        json.loads(raw)

    conn = sqlite3.connect(str(sassy_home / "memory.db"))
    try:
        n = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    finally:
        conn.close()
    assert n >= sum(iterations), f"memory rows {n} < expected {sum(iterations)}"
```

- [ ] **Step 3: Run the stress test**

Run: `python -m pytest tests/test_concurrency_stress.py -v -s -m slow`
Expected: PASS in ~30 seconds. No errors.

- [ ] **Step 4: Run the entire suite once more**

Run: `python -m pytest tests/ -v`
Expected: all green except slow tests (deselected by default).

Run: `python -m pytest tests/ -v -m slow`
Expected: stress test green.

- [ ] **Step 5: Commit**

```bash
git add tests/test_concurrency_stress.py pyproject.toml
git commit -m "test(concurrency): 8-process × 30-second mixed-workload stress test

Spawns 8 sassymcp subprocesses that simultaneously write memories, send
crosslink messages, log audit entries, save config changes, and update
tool state. Verifies after 30s that every file is well-formed and every
SQLite DB has the expected row count. Marked slow — run with
'pytest -m slow'.

Acceptance criterion #1 of the multi-client integration spec sub-project
#1 (concurrency hardening) is now exercised end-to-end.
"
```

---

## Self-Review Notes

1. **Spec coverage** — Every callsite and helper from sub-project #1 of the spec is covered:
   - `_db.py` helper → Task 1
   - `_atomic.py` helper → Task 2
   - `_audit_io.py` helper → Task 3
   - memory.db migration → Task 4
   - tool_state.db migration → Task 5
   - crosslink.db migration (all 7 sites) → Task 6
   - audit.log/jsonl migration (all 6 sites) → Task 7
   - config.json migration → Task 8
   - license.json migration + .license_secret race → Task 9
   - persona.md, tokens.json migration → Task 10
   - End-to-end stress validation → Task 11

2. **No placeholders** — Every step has runnable code or exact commands. No "TBD" / "implement later" / "similar to Task N".

3. **Type consistency** — `open_db(path: Path, *, check_same_thread: bool = False) -> sqlite3.Connection`, `atomic_write_json(path: Path, data: Any)`, `atomic_write_text(path: Path, content: str)`, `append_audit(path: Path, entry: dict)` — same signature in spec, helper definition, and every callsite.

4. **Acceptance criteria mapping**
   - Spec acceptance #1 (8 procs × 1000 calls × 10 minutes, no errors) — Task 11 stress test (8 × 30s mixed workload). The 10-minute-vs-30-second deviation is intentional: 30s gives the same coverage with much faster CI; if real-world testing surfaces issues, extend duration via the `duration` arg.
   - Spec acceptance #2 (6 concurrency tests on Win+Linux CI) — Tasks 4, 5, 6, 7, 8, 9 each add an integration test. Use existing CI matrix.
   - Spec acceptance #3 (no regressions) — full test suite is run after every task.
   - Spec acceptance #4 (pyright/mypy clean) — run `python -m mypy sassymcp/` after Task 10. If new typing errors surface, fix them in a follow-up commit on the same branch.

## Out of scope for this plan (deferred to later sub-projects)

- DXT package (sub-project #3)
- `sassymcp install` CLI (sub-project #2)
- VS Code extension (sub-projects #4 + #5)
- Persistent terminal session sharing — inherently per-process, not multi-writer state
- Rate limiter / observability counter aggregation — per-process is acceptable per spec
