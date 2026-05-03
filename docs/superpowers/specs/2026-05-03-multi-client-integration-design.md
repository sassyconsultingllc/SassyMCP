# Multi-Client Integration: DXT + VS Code Extension + Centralized Brain

**Date:** 2026-05-03
**Status:** Draft — sub-project #1 designed in detail; sub-projects #2–#6 outlined
**Owner:** SaS

## Problem

A coding peer wants to vouch for SassyMCP but can't get it running. Today's install path is a portable zip + per-client JSON edits across Claude Desktop, Cursor, Windsurf, Continue, Cline, Grok, VS Code Copilot. Every client is its own island — shared state on disk works (persona.md, memory.db) but multiple stdio processes hit those files concurrently with no locking, and there is no one-click install.

## Goal

A new user runs **two artifacts** and is done:

1. Double-click `sassymcp.dxt` — Claude Desktop installs SassyMCP, post-install hook detects every other MCP client on the box and patches their configs.
2. (Or) Install the SassyMCP VS Code extension from the marketplace — same auto-detect/patch behavior, plus a status bar icon and webview UI for persona / memory / crosslink / audit.

Architecture: **per-client stdio process + centralized brain.** Each MCP client spawns its own thin `sassymcp.exe` over stdio. All persistent state lives in `~/.sassymcp/` (SQLite DBs + JSON files), shared by every process. No always-on daemon, no tray app.

## Architecture

```
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ Claude Desktop  │  │ VS Code Copilot │  │     Cursor      │  │   Windsurf      │
└────────┬────────┘  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘
         │ stdio              │ stdio              │ stdio              │ stdio
         ▼                    ▼                    ▼                    ▼
   sassymcp.exe         sassymcp.exe         sassymcp.exe         sassymcp.exe
   (per-client)         (per-client)         (per-client)         (per-client)
         │                    │                    │                    │
         └────────────────────┴──────┬─────────────┴────────────────────┘
                                     ▼
                  ┌─────────────────────────────────────┐
                  │ ~/.sassymcp/  (the centralized brain)│
                  │   memory.db     crosslink.db        │
                  │   tool_state.db audit.log           │
                  │   persona.md    config.json         │
                  │   license.json  tokens.json         │
                  └─────────────────────────────────────┘
                                     ▲
                  ┌──────────────────┴──────────────────┐
                  │  VS Code extension webviews          │
                  │  (read-only live viewers via better- │
                  │  sqlite3 + chokidar file watching)   │
                  └─────────────────────────────────────┘
```

Webviews never write — they read SQLite directly and watch JSON files. All mutation flows through `sassymcp.exe` processes invoked via MCP. This preserves the per-client model and avoids a daemon.

## Sub-projects

Six specs, six plans, six implementation cycles. Order is fixed because later items depend on earlier.

| # | Title | Output | Why this order |
|---|---|---|---|
| **1** | **Concurrency hardening** | WAL + busy_timeout on every SQLite, atomic-write helper for JSON, audit.log line-buffered append helper, regression tests | Foundation. Ship anything else first and concurrent users hit `database is locked` and clobbered configs. **Detailed design below.** |
| **2** | `sassymcp install` CLI | Single command that detects every installed MCP client (Claude Desktop, VS Code, Cursor, Windsurf, Continue, Cline, Grok, Zed, Roo) and writes/patches its config to register sassymcp.exe. Idempotent. Used by DXT post-install hook AND VS Code extension AND standalone shell users. | Single source of truth for client patching. Don't reimplement in three places. |
| **3** | DXT package | `manifest.json` + bundled exe + post-install hook calls `sassymcp install --from-dxt`. Builds via existing PyInstaller pipeline + new DXT zipper. | Friend's primary entry point: double-click → all his LLMs configured. |
| **4** | VS Code extension — installer + status bar | TS extension that ships sassymcp.exe (or PATH-detects), runs `sassymcp install`, registers SassyMCP in VS Code's `mcp.json`, status bar icon (tier, brain health), command palette: Run Setup Wizard / Reinstall Configs / Open Audit / Open _DELETE_ / Toggle Tool Group. | Marketplace install path. Same auto-config behavior as DXT, different surface. |
| **5** | VS Code extension — webview brain UI | Persona editor (loads/saves persona.md), Memory browser (live SQLite reader of memory.db, search/filter/tag), Crosslink stream (live tail of crosslink.db), Audit log viewer, Observability dashboard. Reads `~/.sassymcp/` directly via better-sqlite3 + chokidar. | The "active brain UI." Independent of #4 — webviews are isolated panels. |
| **6** | Unified release pipeline | One CI flow producing `sassymcp.exe`, `sassymcp.dxt`, `sassymcp-vscode-x.y.z.vsix` from one tag. Version-locked. | Cleanup — keeps three release surfaces from drifting. Not blocking #1–#5. |

---

# Sub-project #1 — Concurrency Hardening (DETAILED)

## What needs to happen

Multiple `sassymcp.exe` processes will run simultaneously (one per MCP client). They will all write to:

- `~/.sassymcp/memory.db`
- `~/.sassymcp/crosslink.db`
- `~/.sassymcp/tool_state.db`
- `~/.sassymcp/audit.log`
- `~/.sassymcp/config.json`
- `~/.sassymcp/license.json`
- `~/.sassymcp/tokens.json`

Today none of these are concurrency-safe.

## Design

### 1.1 SQLite databases — WAL mode + busy_timeout

A single helper module `sassymcp/_db.py`:

```python
# sassymcp/_db.py
import sqlite3
from pathlib import Path

def open_db(path: Path, *, check_same_thread: bool = False) -> sqlite3.Connection:
    """Open a sassymcp SQLite DB with multi-process-safe pragmas.

    WAL allows concurrent readers + one writer without blocking.
    busy_timeout=5000 makes the writer wait up to 5s for the lock instead
    of immediately raising 'database is locked'.
    synchronous=NORMAL is the recommended pairing for WAL — full fsync on
    checkpoint only, not every commit, big throughput win with no real
    durability cost on local-state DBs.
    """
    conn = sqlite3.connect(str(path), check_same_thread=check_same_thread, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn
```

All three callers swap to this:

- `modules/memory.py:35` — `self.conn = sqlite3.connect(str(MEMORY_DB))` → `self.conn = open_db(MEMORY_DB)`
- `modules/state_manager.py:22` — `self.conn = sqlite3.connect(STATE_DB)` → `self.conn = open_db(STATE_DB)`
- `modules/crosslink.py` (7 callsites) — `sqlite3.connect(str(DB_PATH), check_same_thread=False)` → `open_db(DB_PATH)`

WAL creates `<db>-wal` and `<db>-shm` sidecar files. The webview readers (sub-project #5) must be aware of this. WAL mode persists across reopens — set once on first open, sticks.

### 1.2 JSON config files — atomic write helper

A helper `sassymcp/_atomic.py`:

```python
# sassymcp/_atomic.py
import json
import os
import tempfile
from pathlib import Path

def atomic_write_json(path: Path, data: dict | list, *, indent: int = 2) -> None:
    """Write JSON atomically: temp file in same dir + os.replace().

    os.replace() is atomic on both POSIX and Windows (since Vista). Two
    processes racing to write the same file will end up with one full
    valid JSON document, not a torn half-write of either.

    Tempfile must be on the same filesystem as the target — using the
    same directory guarantees that.
    """
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
    """Same pattern for non-JSON text (persona.md, .license_secret)."""
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

Callsites to migrate:

- `license.py:43` `_SECRET_FILE.write_text(new_secret)` → `atomic_write_text(_SECRET_FILE, new_secret)` + chmod after
- `license.py:149` `LICENSE_FILE.write_text(json.dumps(...))` → `atomic_write_json(LICENSE_FILE, {...})`
- `license.py:200` same
- `runtime_config.py:56` `CONFIG_FILE.write_text(json.dumps(_config, indent=2))` → `atomic_write_json(CONFIG_FILE, _config)`
- `setup_wizard.py` writes for `config.json`, `tokens.json`, `persona.md` — audit and migrate all
- `selfmod.py` writes — these are the user's own source files, NOT shared `~/.sassymcp/` state, leave alone

This still has a last-write-wins issue if two processes both compute new state and write it back. For `config.json` and similar, that means a near-simultaneous setting change from two clients can lose one update — but the file will never be corrupt. This is acceptable; truly concurrent setting writes are vanishingly rare. If it ever becomes a problem we add a content-hash CAS layer; not now.

### 1.3 audit.log — line-flushed append helper

Today: `with open(...) as f: f.write(json.dumps(entry) + "\n")`. Concurrent appends on Windows can interleave bytes when an entry exceeds the OS pipe buffer (~4KB). Audit entries are usually small but `sassy_shell` outputs and full-stack tracebacks routinely cross that line.

Helper `sassymcp/_audit_io.py`:

```python
# sassymcp/_audit_io.py
import json
import os
from pathlib import Path

def append_audit(path: Path, entry: dict) -> None:
    """Append one JSON line atomically.

    Strategy: serialize entry, ensure trailing \\n, write the entire blob
    in a single os.write() to a file opened in append mode. POSIX guarantees
    atomicity for write() ≤ PIPE_BUF; for larger writes we hold the file
    open with O_APPEND which serializes the seek+write at the kernel level
    on POSIX. On Windows we use a portalocker-style file lock.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = (json.dumps(entry) + "\n").encode("utf-8")

    if os.name == "nt":
        # Windows: msvcrt locking on the open file. Lock entire file
        # (locking._LK_LOCK blocks until lock acquired).
        import msvcrt
        with open(path, "ab") as f:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
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
        # POSIX: O_APPEND guarantees serialized writes for any single write().
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, blob)
        finally:
            os.close(fd)
```

Migrate the 6 callsites in `modules/audit.py` (lines 118, 121, 146, 149, 194, 199).

### 1.4 First-run race on `.license_secret`

`license.py:30` generates a random secret on first run if the file doesn't exist. Two processes starting simultaneously could both generate different secrets, one wins the write, the other has a different in-memory secret — license validation diverges across processes.

Fix: use atomic create-if-missing (`O_CREAT | O_EXCL`):

```python
def _load_signing_secret() -> str:
    if os.environ.get("SASSYMCP_LICENSE_SECRET"):
        return os.environ["SASSYMCP_LICENSE_SECRET"]
    # Try to read existing
    if _SECRET_FILE.exists():
        try:
            return _SECRET_FILE.read_text().strip()
        except Exception:
            pass
    # Atomic create-if-not-exists
    new_secret = secrets.token_hex(32)
    _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(_SECRET_FILE), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, new_secret.encode())
        finally:
            os.close(fd)
        return new_secret
    except FileExistsError:
        # Another process beat us. Read theirs.
        return _SECRET_FILE.read_text().strip()
```

### 1.5 Test coverage

Add `tests/test_concurrency.py`:

- **test_memory_concurrent_writes** — fork/spawn 8 processes, each writes 100 memories with unique keys, assert no `database is locked` errors and final row count = 800
- **test_crosslink_concurrent_send_recv** — 4 senders + 4 receivers running concurrently for 10s, assert no errors and message conservation (sent count == recv count + queued)
- **test_state_concurrent_writes** — same pattern as memory
- **test_audit_log_no_interleaving** — 8 processes each write 100 entries with multi-line tracebacks (5KB each), assert every line in resulting log is valid JSON and contains a complete entry
- **test_config_atomic_write** — 8 processes each write a different `config.json` payload, assert final file is always parseable JSON and matches one of the inputs (no partial)
- **test_license_secret_first_run_race** — 8 processes start with empty `~/.sassymcp/`, assert all 8 end up with the same secret

Tests run on Windows + Linux (CI matrix already has both for sassymcp).

## Out of scope for #1

- Persistent terminal sessions (`session.py`) — these own real PIDs/handles per-process and are intentionally not shared. Document this in the persona/persona docs but no code change.
- Rate limiters and observability counters in RAM — per-process is fine; nothing user-facing depends on them being aggregate. If we ever want global metrics, sub-project #5's webview can SUM across processes by reading SQLite, which is the right pattern anyway.
- `_DELETE_/` staging — already filesystem-atomic via `os.rename`, no change needed.

## Acceptance criteria for #1

1. Eight `sassymcp.exe` processes running in parallel, each driving a synthetic 1000-tool-call workload, complete without a single `database is locked` error or corrupt JSON file. Run for 10 minutes, assert tail of audit.log is well-formed.
2. All 6 concurrency tests pass on Windows + Linux CI.
3. No regressions in existing test suite.
4. Pyright/mypy clean.

## Estimated scope

- 2 helper modules (~200 LOC total, mostly comments)
- 3 SQLite caller migrations (~10 line touches each)
- ~6 JSON write migrations
- 6 audit log migrations
- 1 license secret race fix
- 6 concurrency tests + 1 stress test
- Total: a focused day's work, mechanical.

## Implementation order

After spec sign-off → writing-plans skill → execute-plans skill. Then ship sub-projects #2 through #6 in order.
