# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-WJLDMC2K4FPQ
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
    project_root = Path(__file__).parent.parent

    worker_script = tmp_path / "worker.py"
    worker_script.write_text(
        f"import sys\n"
        f"from pathlib import Path\n"
        f"sys.path.insert(0, r'{project_root}')\n"
        f"from sassymcp._audit_io import append_audit\n"
        f"log = Path(sys.argv[1])\n"
        f"worker_id = int(sys.argv[2])\n"
        f"count = int(sys.argv[3])\n"
        f"big = 'T' * 5000\n"
        f"for i in range(count):\n"
        f"    append_audit(log, {{'worker': worker_id, 'i': i, 'trace': big}})\n",
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
        p.wait(timeout=120)
        assert p.returncode == 0

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == workers * per_worker, (
        f"expected {workers * per_worker} lines, got {len(lines)}"
    )

    seen = {(w, i): False for w in range(workers) for i in range(per_worker)}
    for ln, raw in enumerate(lines):
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError as e:
            raise AssertionError(f"line {ln} is not valid JSON ({e}): {raw!r}") from e
        assert "worker" in entry and "i" in entry, f"line {ln}: missing keys: {raw!r}"
        seen[(entry["worker"], entry["i"])] = True

    missing = [k for k, v in seen.items() if not v]
    assert not missing, f"missing entries: {missing[:10]}"
