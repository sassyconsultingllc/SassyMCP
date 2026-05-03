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
    # Add project root to PYTHONPATH for subprocess imports
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] = str(Path(__file__).parent.parent) + os.pathsep + env["PYTHONPATH"]
    else:
        env["PYTHONPATH"] = str(Path(__file__).parent.parent)
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
    # Add project root to PYTHONPATH for subprocess imports
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] = str(Path(__file__).parent.parent) + os.pathsep + env["PYTHONPATH"]
    else:
        env["PYTHONPATH"] = str(Path(__file__).parent.parent)
    procs = [
        subprocess.Popen(
            [sys.executable, str(worker_script), str(w), str(per_worker)], env=env
        )
        for w in range(workers)
    ]
    for p in procs:
        p.wait(timeout=120)
        assert p.returncode == 0, f"worker exited with {p.returncode}"

    db = sassy_home / "tool_state.db"
    assert db.exists()
    conn = sqlite3.connect(str(db))
    try:
        count = conn.execute("SELECT COUNT(*) FROM state").fetchone()[0]
    finally:
        conn.close()
    assert count == workers * per_worker, (
        f"expected {workers * per_worker} states, got {count}"
    )
