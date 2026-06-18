"""Lifecycle + orphan-proof tests for the SassyMCP process supervisor.

Every test isolates state under tmp paths (no real ~/.sassymcp) and reaps
its own children so a failing test cannot wedge a later one. Tunables are
module globals so we monkeypatch them down to keep timing tests fast.
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from sassymcp import supervisor as sup_mod
from sassymcp._jobctl import ProcessJob
from sassymcp.supervisor import ChildSpec, Supervisor

PY = sys.executable
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)


def _sleeper(secs: float) -> list[str]:
    return [PY, "-c", f"import time; time.sleep({secs})"]


def _flapper() -> list[str]:
    # alive briefly, then non-zero exit — a crash loop
    return [PY, "-c", "import sys, time; time.sleep(0.1); sys.exit(7)"]


def _wait(pred, timeout=15.0, interval=0.05) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(interval)
    return False


def _paths(tmp_path: Path) -> dict:
    return {
        "pidfile": tmp_path / "supervisor.pid",
        "registry": tmp_path / "supervisor-children.json",
        "cmdfile": tmp_path / "supervisor.cmd",
    }


def _run_bg(sup: Supervisor) -> threading.Thread:
    t = threading.Thread(target=sup.run, daemon=True)
    t.start()
    return t


def _stop(sup: Supervisor, t: threading.Thread) -> None:
    sup._stop.set()
    t.join(timeout=20)
    try:
        sup.shutdown()  # idempotent safety net
    except Exception:
        pass


# ── lifecycle ───────────────────────────────────────────────────────────
def test_start_running_then_graceful_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(sup_mod, "HEALTH_INTERVAL", 0.1)
    p = _paths(tmp_path)
    sup = Supervisor([ChildSpec(role="fake", cmd=_sleeper(120))], **p)
    t = _run_bg(sup)
    try:
        assert _wait(lambda: sup.children["fake"].state == "running"
                     and sup.children["fake"].proc is not None), "child never started"
        pid = sup.children["fake"].proc.pid
        assert p["pidfile"].exists()
        assert _wait(lambda: p["registry"].exists())
        reg = json.loads(p["registry"].read_text())
        assert reg["children"][0]["role"] == "fake"
        assert reg["children"][0]["state"] == "running"
    finally:
        _stop(sup, t)
    # graceful stop removed the pidfile and reaped the child
    assert not p["pidfile"].exists()
    assert _wait(lambda: not psutil.pid_exists(pid), timeout=10), "child orphaned after stop"


def test_command_channel_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(sup_mod, "HEALTH_INTERVAL", 0.1)
    p = _paths(tmp_path)
    sup = Supervisor([ChildSpec(role="fake", cmd=_sleeper(120))], **p)
    t = _run_bg(sup)
    try:
        assert _wait(lambda: sup.children["fake"].proc is not None)
        # what `sassymcp supervise stop` writes:
        p["cmdfile"].write_text(json.dumps({"action": "stop"}), encoding="utf-8")
        assert _wait(lambda: not t.is_alive(), timeout=15), "supervisor ignored stop command"
    finally:
        _stop(sup, t)
    assert not p["pidfile"].exists()


def test_command_channel_restart_recycles_child(tmp_path, monkeypatch):
    monkeypatch.setattr(sup_mod, "HEALTH_INTERVAL", 0.05)
    p = _paths(tmp_path)
    sup = Supervisor([ChildSpec(role="fake", cmd=_sleeper(120))], **p)
    t = _run_bg(sup)
    try:
        assert _wait(lambda: sup.children["fake"].proc is not None)
        pid1 = sup.children["fake"].proc.pid
        p["cmdfile"].write_text(json.dumps({"action": "restart", "role": "fake"}), encoding="utf-8")
        assert _wait(lambda: sup.children["fake"].proc is not None
                     and sup.children["fake"].proc.pid != pid1, timeout=15), "child not recycled"
        assert _wait(lambda: not psutil.pid_exists(pid1), timeout=10), "old child not reaped"
    finally:
        _stop(sup, t)


# ── restart / backoff ─────────────────────────────────────────────────
def test_crash_loop_backs_off_and_gives_up(tmp_path, monkeypatch):
    monkeypatch.setattr(sup_mod, "HEALTH_INTERVAL", 0.05)
    monkeypatch.setattr(sup_mod, "BACKOFF_BASE", 0.05)
    monkeypatch.setattr(sup_mod, "BACKOFF_CAP", 0.2)
    monkeypatch.setattr(sup_mod, "MAX_RESTARTS", 3)
    monkeypatch.setattr(sup_mod, "STABLE_UPTIME", 999)  # never reset mid-test
    p = _paths(tmp_path)
    sup = Supervisor([ChildSpec(role="flap", cmd=_flapper())], **p)
    t = _run_bg(sup)
    try:
        assert _wait(lambda: sup.children["flap"].state == "failed", timeout=25), \
            "crash-looping child never marked failed"
        assert sup.children["flap"].restart_count >= 3
        assert sup.children["flap"].last_exit_code == 7
    finally:
        _stop(sup, t)


def test_no_restart_flag_leaves_dead_child_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(sup_mod, "HEALTH_INTERVAL", 0.05)
    p = _paths(tmp_path)
    sup = Supervisor([ChildSpec(role="flap", cmd=_flapper())], restart=False, **p)
    t = _run_bg(sup)
    try:
        assert _wait(lambda: sup.children["flap"].state == "stopped"
                     and sup.children["flap"].proc is None, timeout=15)
        assert sup.children["flap"].restart_count == 0
    finally:
        _stop(sup, t)


# ── pidfile single-instance ───────────────────────────────────────────
def test_pidfile_single_instance(tmp_path):
    # A real, live, FOREIGN process stands in for an already-running
    # supervisor (two supervisors are always two distinct processes; the
    # same-process case is handled by the recycled-pid guard separately).
    other = subprocess.Popen(_sleeper(120), stdin=subprocess.DEVNULL)
    try:
        p = _paths(tmp_path)
        p["pidfile"].parent.mkdir(parents=True, exist_ok=True)
        p["pidfile"].write_text(json.dumps({
            "pid": other.pid,
            "create_time": psutil.Process(other.pid).create_time(),
        }), encoding="utf-8")
        sup = Supervisor([], **p)
        with pytest.raises(SystemExit):
            sup._acquire_pidfile()
        # the live foreign pidfile must be left intact, not clobbered
        assert json.loads(p["pidfile"].read_text())["pid"] == other.pid
    finally:
        try:
            other.terminate()
            other.wait(timeout=10)
        except Exception:
            other.kill()


def test_pidfile_stale_reclaimed(tmp_path):
    p = _paths(tmp_path)
    p["pidfile"].parent.mkdir(parents=True, exist_ok=True)
    # a pid that cannot be alive (or whose create_time can't match)
    p["pidfile"].write_text(json.dumps({"pid": 2_000_000_000, "create_time": 1.0}),
                            encoding="utf-8")
    sup = Supervisor([], **p)
    sup._acquire_pidfile()  # must reclaim the stale file, not refuse
    try:
        assert json.loads(p["pidfile"].read_text())["pid"] == os.getpid()
    finally:
        sup._release_pidfile()


# ── scoping: never kill processes we don't own ────────────────────────
def test_shutdown_does_not_kill_unmanaged_neighbor(tmp_path, monkeypatch):
    monkeypatch.setattr(sup_mod, "HEALTH_INTERVAL", 0.1)
    neighbor = subprocess.Popen(_sleeper(120), stdin=subprocess.DEVNULL)
    try:
        p = _paths(tmp_path)
        sup = Supervisor([ChildSpec(role="fake", cmd=_sleeper(120))], **p)
        t = _run_bg(sup)
        assert _wait(lambda: sup.children["fake"].proc is not None)
        _stop(sup, t)
        # our own child is gone; the unrelated neighbor is untouched
        assert psutil.pid_exists(neighbor.pid), "supervisor killed an unmanaged process"
    finally:
        try:
            neighbor.terminate()
            neighbor.wait(timeout=10)
        except Exception:
            neighbor.kill()


# ── the headline guarantee: hard-kill the parent, no orphans ──────────
@pytest.mark.slow
def test_orphan_proof_on_parent_hard_kill(tmp_path):
    """Kill -9 / TerminateProcess the supervisor-like parent → its child dies.

    On Windows this proves the Job Object KILL_ON_JOB_CLOSE path; on Linux,
    PR_SET_PDEATHSIG. On macOS (no portable kill-on-death) the test skips."""
    harness = tmp_path / "harness.py"
    harness.write_text(
        "import os, sys, time, subprocess\n"
        "from sassymcp._jobctl import ProcessJob\n"
        "job = ProcessJob()\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'],\n"
        "                     stdin=subprocess.DEVNULL, **job.spawn_kwargs())\n"
        "job.assign(p)\n"
        "print(p.pid, flush=True)\n"
        "time.sleep(300)\n",
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": PROJECT_ROOT}
    h = subprocess.Popen([PY, str(harness)], stdout=subprocess.PIPE, text=True, env=env)
    try:
        line = h.stdout.readline().strip()
        child_pid = int(line)
        assert psutil.pid_exists(child_pid)
        # hard kill the parent — no signal handler / atexit runs
        h.kill()
        h.wait(timeout=10)
        died = _wait(lambda: not psutil.pid_exists(child_pid), timeout=12)
        if not died and os.name != "nt" and not sys.platform.startswith("linux"):
            try:
                psutil.Process(child_pid).kill()
            except Exception:
                pass
            pytest.skip("kill-on-parent-death not guaranteed on this POSIX platform")
        assert died, "child survived a hard parent kill — orphan-proofing failed"
    finally:
        try:
            if h.poll() is None:
                h.kill()
        except Exception:
            pass


@pytest.mark.slow
def test_orphan_proof_via_self_adopt_inheritance(tmp_path):
    """The PyInstaller-onefile path: a parent that adopts ITSELF into the job
    must take down children it never explicitly assigned, AND their
    grandchildren (the onefile worker is an unassigned grandchild). Hard-kill
    the adopting parent → the whole inherited tree dies."""
    out = tmp_path / "pids.txt"
    harness = tmp_path / "harness.py"
    harness.write_text(
        "import os, sys, subprocess, time\n"
        "from sassymcp._jobctl import ProcessJob\n"
        "job = ProcessJob()\n"
        "job.adopt_current_process()\n"  # self-adopt → descendants inherit
        "child_code = (\n"
        "  'import os,sys,subprocess,time;'\n"
        "  'g=subprocess.Popen([sys.executable,\"-c\",\"import time;time.sleep(300)\"]);'\n"
        "  'open(sys.argv[1],\"w\").write(str(os.getpid())+chr(32)+str(g.pid));'\n"
        "  'time.sleep(300)'\n"
        ")\n"
        # deliberately NO job.assign(c) — rely purely on inheritance
        "c = subprocess.Popen([sys.executable,'-c',child_code,sys.argv[1]], **job.spawn_kwargs())\n"
        "time.sleep(300)\n",
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": PROJECT_ROOT}
    h = subprocess.Popen([PY, str(harness), str(out)], env=env)
    try:
        assert _wait(lambda: out.exists() and len(out.read_text().split()) == 2, timeout=20), \
            "child/grandchild never reported pids"
        child_pid, grand_pid = (int(x) for x in out.read_text().split())
        assert psutil.pid_exists(child_pid) and psutil.pid_exists(grand_pid)
        h.kill()
        h.wait(timeout=10)
        both_dead = _wait(
            lambda: not psutil.pid_exists(child_pid) and not psutil.pid_exists(grand_pid),
            timeout=12,
        )
        if not both_dead and os.name != "nt" and not sys.platform.startswith("linux"):
            for pid in (child_pid, grand_pid):
                try:
                    psutil.Process(pid).kill()
                except Exception:
                    pass
            pytest.skip("kill-on-parent-death not guaranteed on this POSIX platform")
        assert both_dead, "inherited child/grandchild survived parent hard-kill"
    finally:
        for pid_getter in (lambda: child_pid, lambda: grand_pid):
            try:
                psutil.Process(pid_getter()).kill()
            except Exception:
                pass
        if h.poll() is None:
            try:
                h.kill()
            except Exception:
                pass


def test_job_object_constructs_on_this_platform():
    """ProcessJob must construct without error on the host OS (Windows: the
    ctypes Job Object actually allocates; POSIX: process-group mode)."""
    job = ProcessJob()
    try:
        assert job.active
        assert job.kind in ("job_object", "process_group")
        if os.name == "nt":
            assert job.kind == "job_object"
    finally:
        job.close()
