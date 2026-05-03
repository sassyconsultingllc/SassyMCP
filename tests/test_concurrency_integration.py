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
    # Add project root to PYTHONPATH for subprocess imports
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] = str(Path(__file__).parent.parent) + os.pathsep + env["PYTHONPATH"]
    else:
        env["PYTHONPATH"] = str(Path(__file__).parent.parent)
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
        p.wait(timeout=180)
        assert p.returncode == 0

    log = sassy_home / "audit.log"
    assert log.exists()
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == workers * per_worker, (
        f"expected {workers * per_worker} lines, got {len(lines)}"
    )

    for ln, raw in enumerate(lines):
        try:
            json.loads(raw)
        except json.JSONDecodeError as e:
            raise AssertionError(f"line {ln} is not valid JSON ({e}): {raw!r}") from e


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
        assert p.returncode == 0

    cfg = sassy_home / "config.json"
    assert cfg.exists()
    parsed = json.loads(cfg.read_text())
    assert isinstance(parsed, dict)


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
    # Add project root to PYTHONPATH for subprocess imports
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] = str(Path(__file__).parent.parent) + os.pathsep + env["PYTHONPATH"]
    else:
        env["PYTHONPATH"] = str(Path(__file__).parent.parent)
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
    # Add project root to PYTHONPATH for subprocess imports
    if "PYTHONPATH" in setup_env:
        setup_env["PYTHONPATH"] = str(Path(__file__).parent.parent) + os.pathsep + setup_env["PYTHONPATH"]
    else:
        setup_env["PYTHONPATH"] = str(Path(__file__).parent.parent)
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
