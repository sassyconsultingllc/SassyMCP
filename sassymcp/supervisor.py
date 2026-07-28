# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-ISAPQFPWMCGP
"""SassyMCP process supervisor — `sassymcp supervise`.

A thin, dependency-light parent that owns the runtime tree: it spawns the
HTTP bridge (and, optionally, the cloudflared tunnel) as managed children,
keeps them alive with health checks + exponential-backoff restart, and
guarantees they die when the supervisor dies (see ``_jobctl``). It is the
fix for the two failure modes the old launcher scripts caused:

  * orphaned ``sassymcp.exe`` holding a wedged SQLite/WAL lock (the bat
    files ``taskkill /f`` the bridge by netstat-parsed PID — no graceful
    stop), and
  * a *hung-but-alive* bridge that Windows Task Scheduler can never catch
    because the process is still running.

Why managed-child (not in-process uvicorn): only a separate parent can
kill and relaunch a wedged bridge without itself dying, and can hold the
orphan-proofing Job Object across a bridge restart.

Control surface (crash-survivable, file-based — no admin port):
  * ``sassymcp supervise start``   become the supervisor (single instance)
  * ``sassymcp supervise stop``    graceful stop via the command channel
  * ``sassymcp supervise status``  print the on-disk registry; exit!=0 if unhealthy
  * ``sassymcp supervise restart <role>``  recycle one child

stdio mode is intentionally NOT supervised — that process is owned by the
MCP client's pipe. The first-run installer and user-launched GUI apps stay
detached by design and are never assigned to the job.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from sassymcp import _paths
from sassymcp._atomic import atomic_write_json
from sassymcp._jobctl import ProcessJob, terminate_tree

# Tunables (seconds). Conservative: a few-second health cadence is plenty
# for a local bridge, and a 60s backoff cap stops a crash-loop from pinning
# a core without giving up forever until the retry ceiling is hit.
HEALTH_INTERVAL = 3.0
READINESS_TIMEOUT = 3.0
READINESS_FAILS_TO_UNHEALTHY = 3
BACKOFF_BASE = 1.0
BACKOFF_CAP = 60.0
MAX_RESTARTS = 5          # consecutive restarts before a child is marked FAILED
STABLE_UPTIME = 30.0      # uptime after which restart_count resets to 0
REAP_GRACE = 5.0


@dataclass
class ChildSpec:
    role: str
    cmd: list[str]
    env: dict | None = None
    cwd: str | None = None
    readiness_url: str | None = None  # bridge: catch hung-but-alive
    enabled: bool = True


@dataclass
class _Child:
    spec: ChildSpec
    proc: subprocess.Popen | None = None
    create_time: float | None = None
    state: str = "stopped"            # running | restarting | failed | stopped
    started_epoch: float = 0.0
    restart_count: int = 0
    last_exit_code: int | None = None
    consecutive_health_fails: int = 0
    last_ok_epoch: float = 0.0
    next_attempt_epoch: float = 0.0
    readiness: str = "unknown"        # ready | unresponsive | n/a | unknown


def _proc_create_time(pid: int) -> float | None:
    try:
        import psutil
        return psutil.Process(pid).create_time()
    except Exception:
        return None


def _pid_alive(pid: int, create_time: float | None = None) -> bool:
    """True iff `pid` is a live process AND (if given) its create_time matches.

    The create_time guard defeats PID recycling — a recycled PID is a
    *different* process and must not be mistaken for our old child."""
    try:
        import psutil
        if not psutil.pid_exists(pid):
            return False
        if create_time is not None:
            try:
                return abs(psutil.Process(pid).create_time() - create_time) < 1.0
            except Exception:
                return False
        return True
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _probe_http(url: str, timeout: float) -> bool:
    """Readiness = the server returns ANY HTTP status within `timeout`.

    A 200 or a 401 both prove the bridge is responsive (not wedged); only a
    connection refusal / timeout means unhealthy. Auth is irrelevant here."""
    try:
        import httpx
        try:
            httpx.post(
                url, timeout=timeout,
                headers={"Content-Type": "application/json",
                         "Accept": "application/json, text/event-stream"},
                content=b'{"jsonrpc":"2.0","id":0,"method":"ping"}',
            )
            return True  # any response object == server answered
        except httpx.HTTPStatusError:
            return True
        except Exception:
            return False
    except Exception:
        return True  # httpx missing -> don't down a child we can't probe


class Supervisor:
    def __init__(
        self,
        specs: list[ChildSpec],
        *,
        pidfile: Path | None = None,
        registry: Path | None = None,
        cmdfile: Path | None = None,
        restart: bool = True,
        adopt_self: bool = False,
    ) -> None:
        self.specs = specs
        self.pidfile = pidfile or _paths.SUPERVISOR_PIDFILE
        self.registry = registry or _paths.SUPERVISOR_REGISTRY
        self.cmdfile = cmdfile or _paths.SUPERVISOR_CMD
        self.restart = restart
        # adopt_self: join our OWN process to the kill-on-close job so every
        # descendant (incl. PyInstaller onefile workers) auto-inherits it.
        # ONLY safe for the real CLI process — never when embedded in a test
        # thread, which would tie the host process's life to the job.
        self.adopt_self = adopt_self
        self.job = ProcessJob()
        self.children: dict[str, _Child] = {s.role: _Child(spec=s) for s in specs}
        self._stop = threading.Event()
        self._own_create_time = _proc_create_time(os.getpid())

    # ── single-instance pidfile ───────────────────────────────────────
    def _acquire_pidfile(self) -> None:
        self.pidfile.parent.mkdir(parents=True, exist_ok=True)
        # O_EXCL gives single-winner create. On a pre-existing file we decide
        # live-vs-stale; on a stale file we unlink and retry. The retry loop
        # closes the TOCTOU window where two supervisors race to reclaim the
        # same stale pidfile — the loser sees the winner's fresh file, finds
        # it live, and refuses (rather than crashing on an uncaught EEXIST).
        for _ in range(5):
            try:
                fd = os.open(str(self.pidfile), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                break
            except FileExistsError:
                if self._existing_supervisor_alive():
                    raise SystemExit(
                        f"supervisor already running for {_paths.HOME} "
                        f"(pidfile {self.pidfile}); use `supervise stop` first"
                    )
                try:
                    self.pidfile.unlink()
                except OSError:
                    pass
        else:
            raise SystemExit(f"could not acquire supervisor pidfile {self.pidfile}")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({
                "pid": os.getpid(),
                "create_time": self._own_create_time,
                "start_epoch": time.time(),
                "version": _sassy_version(),
            }, f)

    def _existing_supervisor_alive(self) -> bool:
        try:
            data = json.loads(self.pidfile.read_text(encoding="utf-8"))
        except Exception:
            return False
        pid = data.get("pid")
        if not isinstance(pid, int) or pid == os.getpid():
            return False
        return _pid_alive(pid, data.get("create_time"))

    def _release_pidfile(self) -> None:
        try:
            data = json.loads(self.pidfile.read_text(encoding="utf-8"))
            if data.get("pid") == os.getpid():
                self.pidfile.unlink()
        except Exception:
            pass

    # ── child lifecycle ────────────────────────────────────────────────
    def _start_child(self, child: _Child) -> None:
        spec = child.spec
        env = {**os.environ, **(spec.env or {})}
        try:
            proc = subprocess.Popen(
                spec.cmd, env=env, cwd=spec.cwd,
                stdin=subprocess.DEVNULL,
                **self.job.spawn_kwargs(),
            )
        except Exception as e:
            child.state = "failed"
            child.last_exit_code = None
            _log(f"[{spec.role}] spawn failed: {e}")
            return
        assigned = self.job.assign(proc)
        child.proc = proc
        child.create_time = _proc_create_time(proc.pid)
        child.started_epoch = time.time()
        child.state = "running"
        child.consecutive_health_fails = 0
        child.readiness = "unknown"
        _log(f"[{spec.role}] started pid={proc.pid} "
             f"job_assigned={assigned} cmd={' '.join(spec.cmd)}")

    def _reap_child(self, child: _Child, grace: float = REAP_GRACE) -> None:
        if child.proc is not None:
            code = terminate_tree(child.proc, grace=grace)
            child.last_exit_code = code
        child.proc = None
        child.state = "stopped"

    def _schedule_restart(self, child: _Child) -> None:
        if not self.restart:
            child.state = "stopped"
            return
        # Reset the crash-loop counter if the child had been stable a while.
        if child.started_epoch and (time.time() - child.started_epoch) > STABLE_UPTIME:
            child.restart_count = 0
        if child.restart_count >= MAX_RESTARTS:
            child.state = "failed"
            _log(f"[{child.spec.role}] FAILED — {child.restart_count} restarts, giving up")
            return
        backoff = min(BACKOFF_CAP, BACKOFF_BASE * (2 ** child.restart_count))
        child.restart_count += 1
        child.next_attempt_epoch = time.time() + backoff
        child.state = "restarting"
        _log(f"[{child.spec.role}] restart #{child.restart_count} in {backoff:.0f}s")

    # ── health tick ────────────────────────────────────────────────────
    def _tick(self) -> None:
        now = time.time()
        for child in self.children.values():
            if not child.spec.enabled:
                continue
            if child.state == "failed":
                continue
            if child.state == "restarting":
                if now >= child.next_attempt_epoch:
                    self._start_child(child)
                continue
            if child.state != "running" or child.proc is None:
                continue

            # liveness
            if child.proc.poll() is not None:
                child.last_exit_code = child.proc.returncode
                _log(f"[{child.spec.role}] exited code={child.last_exit_code}")
                child.proc = None
                self._schedule_restart(child)
                continue

            # readiness (catches the hung-but-alive bridge)
            if child.spec.readiness_url:
                if _probe_http(child.spec.readiness_url, READINESS_TIMEOUT):
                    child.readiness = "ready"
                    child.consecutive_health_fails = 0
                    child.last_ok_epoch = now
                else:
                    child.consecutive_health_fails += 1
                    child.readiness = "unresponsive"
                    if child.consecutive_health_fails >= READINESS_FAILS_TO_UNHEALTHY:
                        _log(f"[{child.spec.role}] unresponsive "
                             f"({child.consecutive_health_fails}x) — recycling")
                        self._reap_child(child)
                        self._schedule_restart(child)
            else:
                child.readiness = "n/a"
                child.last_ok_epoch = now

    # ── registry persistence ───────────────────────────────────────────
    def _write_registry(self) -> None:
        children = []
        for child in self.children.values():
            children.append({
                "role": child.spec.role,
                "pid": child.proc.pid if child.proc else None,
                "create_time": child.create_time,
                "cmd": child.spec.cmd,
                "state": child.state,
                "started_epoch": child.started_epoch,
                "restart_count": child.restart_count,
                "last_exit_code": child.last_exit_code,
                "readiness": child.readiness,
                "health": {
                    "last_ok_epoch": child.last_ok_epoch,
                    "consecutive_failures": child.consecutive_health_fails,
                },
            })
        try:
            atomic_write_json(self.registry, {
                "schema": 1,
                "supervisor_pid": os.getpid(),
                "supervisor_create_time": self._own_create_time,
                "updated_epoch": time.time(),
                "job_kind": self.job.kind,
                "children": children,
            })
        except Exception as e:
            _log(f"registry write failed (non-fatal): {e}")

    # ── command channel (stop / restart) ───────────────────────────────
    def _poll_commands(self) -> None:
        if not self.cmdfile.exists():
            return
        try:
            cmd = json.loads(self.cmdfile.read_text(encoding="utf-8"))
        except Exception:
            cmd = {}
        try:
            self.cmdfile.unlink()
        except OSError:
            pass
        action = cmd.get("action")
        if action == "stop":
            _log("stop command received")
            self._stop.set()
        elif action == "restart":
            role = cmd.get("role")
            child = self.children.get(role)
            if child:
                _log(f"[{role}] restart command received")
                self._reap_child(child)
                child.restart_count = 0
                child.next_attempt_epoch = 0.0
                child.state = "restarting"
            else:
                _log(f"restart: unknown role {role!r}")

    # ── stale-child reclaim on (re)start ───────────────────────────────
    def adopt_stale(self) -> None:
        """A previous supervisor may have died hard, leaving its children
        and a registry behind. Reap any still-alive prior children so we
        start from a clean tree (we cannot re-assign them to our new job)."""
        try:
            data = json.loads(self.registry.read_text(encoding="utf-8"))
        except Exception:
            return
        if data.get("supervisor_pid") == os.getpid():
            return
        for entry in data.get("children", []):
            pid = entry.get("pid")
            if isinstance(pid, int) and _pid_alive(pid, entry.get("create_time")):
                _log(f"reaping stale {entry.get('role')} pid={pid} from prior supervisor")
                try:
                    import psutil
                    p = psutil.Process(pid)
                    p.terminate()
                    try:
                        p.wait(timeout=REAP_GRACE)
                    except Exception:
                        p.kill()
                except Exception:
                    pass

    # ── run / shutdown ─────────────────────────────────────────────────
    def _install_signals(self) -> None:
        def _handler(signum, frame):
            _log(f"signal {signum} — shutting down")
            self._stop.set()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handler)
            except (ValueError, OSError):
                pass
        if hasattr(signal, "SIGBREAK"):
            try:
                signal.signal(signal.SIGBREAK, _handler)
            except (ValueError, OSError):
                pass

    def run(self) -> int:
        self._acquire_pidfile()
        self._install_signals()
        # clear any stale command from a prior run
        try:
            self.cmdfile.unlink()
        except OSError:
            pass
        if self.adopt_self:
            adopted = self.job.adopt_current_process()
            _log(f"self adopted into job: {adopted}")
        _log(f"supervisor up pid={os.getpid()} home={_paths.HOME} job={self.job.kind}")
        try:
            self.adopt_stale()
            for child in self.children.values():
                if child.spec.enabled:
                    self._start_child(child)
            self._write_registry()
            while not self._stop.is_set():
                self._poll_commands()
                if self._stop.is_set():
                    break
                self._tick()
                self._write_registry()
                self._stop.wait(HEALTH_INTERVAL)
        finally:
            self.shutdown()
        return 0

    def shutdown(self) -> None:
        _log("draining children")
        for child in self.children.values():
            if child.proc is not None:
                self._reap_child(child)
        self.job.close()  # belt-and-suspenders: kills anything still in the job
        self._write_registry()
        self._release_pidfile()
        _log("supervisor down")


# ── module helpers ─────────────────────────────────────────────────────
def _sassy_version() -> str:
    try:
        from sassymcp import __version__
        return __version__
    except Exception:
        return "?"


def _log(msg: str) -> None:
    print(f"[supervise] {msg}", flush=True)


def _bridge_cmd(host: str, port: int) -> list[str]:
    """Reconstruct how to launch the bridge the same way we were launched:
    frozen exe -> [exe, --http ...]; dev -> [python, -m sassymcp.server, ...]."""
    if getattr(sys, "frozen", False):
        base = [sys.executable]
    else:
        base = [sys.executable, "-m", "sassymcp.server"]
    return [*base, "--http", "--host", host, "--port", str(port)]


def _build_specs(args) -> list[ChildSpec]:
    specs = [ChildSpec(
        role="bridge",
        cmd=_bridge_cmd(args.host, args.port),
        # SASSYMCP_SUPERVISED tells the bridge to exit on supervised restart
        # handoff rather than re-exec itself (the supervisor owns respawn and
        # would race us for the port).
        env={"SASSYMCP_LOAD_ALL": "1", "SASSYMCP_SUPERVISED": "1"},
        readiness_url=f"http://{args.host}:{args.port}/mcp",
    )]
    if args.tunnel_mode == "managed":
        specs.append(ChildSpec(
            role="tunnel",
            cmd=["cloudflared", "tunnel", "run", args.tunnel_name],
        ))
    # service/none: tunnel is not a managed child (observe-only / absent)
    return specs


# ── CLI entry: `sassymcp supervise ...` ────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="sassymcp supervise",
        description="Own the SassyMCP runtime tree (bridge + tunnel) with "
                    "orphan-proof, self-healing process supervision.",
    )
    sub = p.add_subparsers(dest="action", required=True)

    p_start = sub.add_parser("start", help="become the supervisor (foreground)")
    p_start.add_argument("--host", default="127.0.0.1")
    p_start.add_argument("--port", type=int, default=21001)
    p_start.add_argument("--tunnel-mode", choices=["managed", "service", "none"],
                         default="none", help="managed: run cloudflared as a child; "
                         "service/none: don't own a tunnel")
    p_start.add_argument("--tunnel-name", default=os.environ.get("SASSYMCP_TUNNEL_NAME", "sassymcp"))
    p_start.add_argument("--no-restart", action="store_true",
                         help="don't auto-restart children (run-once / debugging)")

    sub.add_parser("stop", help="gracefully stop the running supervisor")
    sub.add_parser("status", help="print supervisor + child status (exit!=0 if unhealthy)")
    p_restart = sub.add_parser("restart", help="recycle one managed child")
    p_restart.add_argument("role", help="child role, e.g. bridge | tunnel")

    args = p.parse_args(argv)

    if args.action == "start":
        specs = _build_specs(args)
        sup = Supervisor(specs, restart=not args.no_restart, adopt_self=True)
        try:
            return sup.run()
        except SystemExit as e:
            _log(str(e))
            return 1
    if args.action == "stop":
        return _cmd_send({"action": "stop"}, wait_for_exit=True)
    if args.action == "restart":
        return _cmd_send({"action": "restart", "role": args.role})
    if args.action == "status":
        return _cmd_status()
    return 2


def _cmd_send(cmd: dict, *, wait_for_exit: bool = False) -> int:
    pidfile = _paths.SUPERVISOR_PIDFILE
    if not pidfile.exists():
        _log("no supervisor running (no pidfile)")
        return 1
    atomic_write_json(_paths.SUPERVISOR_CMD, cmd)
    if not wait_for_exit:
        _log(f"sent {cmd.get('action')} command")
        return 0
    # wait for the pidfile to disappear (clean shutdown)
    deadline = time.time() + 30.0
    while time.time() < deadline:
        if not pidfile.exists():
            _log("supervisor stopped")
            return 0
        time.sleep(0.25)
    _log("timed out waiting for supervisor to stop")
    return 1


def _cmd_status() -> int:
    reg = _paths.SUPERVISOR_REGISTRY
    pidfile = _paths.SUPERVISOR_PIDFILE
    running = pidfile.exists() and _existing_pidfile_alive(pidfile)
    if not reg.exists():
        print(json.dumps({"running": running, "children": []}, indent=2))
        return 0 if running else 1
    try:
        data = json.loads(reg.read_text(encoding="utf-8"))
    except Exception as e:
        print(json.dumps({"running": running, "error": f"unreadable registry: {e}"}))
        return 1
    data["running"] = running
    print(json.dumps(data, indent=2))
    children = data.get("children", [])
    healthy = running and all(
        c.get("state") == "running" and c.get("readiness") in ("ready", "n/a", "unknown")
        for c in children
    ) if children else running
    return 0 if healthy else 1


def _existing_pidfile_alive(pidfile: Path) -> bool:
    try:
        data = json.loads(pidfile.read_text(encoding="utf-8"))
    except Exception:
        return False
    pid = data.get("pid")
    return isinstance(pid, int) and _pid_alive(pid, data.get("create_time"))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
