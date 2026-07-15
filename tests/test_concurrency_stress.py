# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-4XGBIYSNXGRX
"""Long-running multi-process stress test for sassymcp shared state.

Marked slow — run with:
    python -m pytest tests/test_concurrency_stress.py -v -s -m slow
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
    # Add project root to PYTHONPATH so workers can import sassymcp
    project_root = Path(__file__).parent.parent
    env = {**os.environ, "SASSYMCP_HOME": str(sassy_home), "PYTHONPATH": str(project_root)}
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
        try:
            json.loads(raw)
        except json.JSONDecodeError as e:
            raise AssertionError(f"audit line {ln} is not valid JSON ({e}): {raw!r}") from e

    conn = sqlite3.connect(str(sassy_home / "memory.db"))
    try:
        n = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    finally:
        conn.close()
    assert n >= sum(iterations), f"memory rows {n} < expected {sum(iterations)}"
