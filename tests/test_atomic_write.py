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

    # Get the project root
    project_root = Path(__file__).parent.parent
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root)

    procs = [
        subprocess.Popen(
            [sys.executable, str(worker_script), str(target), str(i), "50"],
            env=env,
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


def test_atomic_write_text_cleans_up_tmp_on_exception(tmp_path: Path):
    """If f.write raises, the temp file must be unlinked, not left behind."""
    p = tmp_path / "out.txt"

    with pytest.raises(TypeError):
        atomic_write_text(p, 12345)  # type: ignore[arg-type]

    leftovers = list(tmp_path.iterdir())
    assert leftovers == [], f"tmp file leaked on exception: {leftovers}"
