"""Linux Module — Streaming SSH execution via plink.

Long-running commands stream partial output in real time via MCP streaming.

Authentication priority (first that matches wins, all from live env at call time):
  1. SSH_SESSION   — use a saved PuTTY session ("plink -load <name>"), which
                     carries its own key, host, user, and authentication mode.
  2. SSH_KEY       — path to a .ppk private key; uses "plink -i <path>".
  3. Pageant       — if pageant.exe is running and an SSH_AGENT_PID-equivalent
                     is detectable, plink uses it automatically (no extra args).
  4. SSH_PASS      — password fed to plink via stdin (`-pw -` is unreliable on
                     some plink builds; stdin-feed sidesteps the issue and
                     also keeps the password out of the process command line).

The legacy `plink -pw <password>` invocation is gone because it leaks the
password to every other user on the box via the process list (Task Manager,
wmic process get commandline, ETW). Set SSH_PASS and it now goes through
stdin; nothing sensitive lands in argv.
"""

import asyncio
import logging
import os
import shutil
from pathlib import Path

from sassymcp.modules._security import detect_delete_intent, validate_command
from sassymcp.modules import audit as _audit

logger = logging.getLogger("sassymcp.linux")


def _env_ssh_host() -> str:
    return os.environ.get("SSH_HOST", "")


def _env_ssh_user() -> str:
    return os.environ.get("SSH_USER", "")


def _env_ssh_pass() -> str:
    return os.environ.get("SSH_PASS", "")


def _env_ssh_key() -> str:
    return os.environ.get("SSH_KEY", "")


def _env_ssh_session() -> str:
    return os.environ.get("SSH_SESSION", "")


def _pageant_running() -> bool:
    """Best-effort detection of a live Pageant agent on Windows.

    plink talks to pageant via a named window ("Pageant"), so a process
    listing is a reasonable proxy. We only check on Windows — POSIX uses
    ssh-agent, which OpenSSH consults transparently via SSH_AUTH_SOCK.
    """
    if os.name != "nt":
        return False
    try:
        import subprocess
        out = subprocess.run(
            ["tasklist.exe", "/FI", "IMAGENAME eq pageant.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=2,
        )
        return "pageant.exe" in (out.stdout or "").lower()
    except Exception:
        return False


# Kept as module-level convenience reads (used by the diagnostic banner
# below). The actual auth flow re-reads from os.environ on every call so
# `sassy_env_set SSH_PASS=...` takes effect immediately without a restart.
SSH_HOST = _env_ssh_host()
SSH_USER = _env_ssh_user()
SSH_PASS = _env_ssh_pass()

# Search order for plink.exe
_PLINK_SEARCH_PATHS = [
    Path.home() / "AppData" / "Local" / "Temp" / "plink.exe",
    Path("C:/Program Files/PuTTY/plink.exe"),
    Path("C:/Program Files (x86)/PuTTY/plink.exe"),
    Path("C:/ProgramData/chocolatey/bin/plink.exe"),
]


def _find_plink() -> str:
    """Resolve plink.exe path: env var > PATH lookup > common locations."""
    env_path = os.environ.get("PLINK_PATH")
    if env_path and Path(env_path).is_file():
        return env_path

    which_result = shutil.which("plink")
    if which_result:
        return which_result

    for candidate in _PLINK_SEARCH_PATHS:
        if candidate.is_file():
            return str(candidate)

    return ""  # empty = not found


PLINK_PATH = _find_plink()


async def _ssh_exec_stream(cmd: str, timeout: int = 60):
    """Streaming generator for real-time SSH output via plink.

    Reads SSH config from os.environ on every call so `sassy_env_set
    SSH_PASS=...` takes effect immediately. Authentication mode is
    resolved in priority order: SSH_SESSION, SSH_KEY, Pageant,
    SSH_PASS-via-stdin.
    """
    host = _env_ssh_host()
    user = _env_ssh_user()
    session = _env_ssh_session()
    key = _env_ssh_key()
    password = _env_ssh_pass()
    pageant = _pageant_running()

    # A saved PuTTY session carries its own host/user/key — host+user
    # become optional in that mode.
    if not session and (not host or not user):
        yield "ERROR: SSH not configured. Set EITHER:\n"
        yield "  SSH_SESSION=<putty-session-name>     (saved session carries everything)\n"
        yield "  OR  SSH_HOST + SSH_USER + ONE OF:\n"
        yield "        SSH_KEY=<path-to-.ppk>         (recommended; key auth)\n"
        yield "        Pageant running                (auto-detected)\n"
        yield "        SSH_PASS=<password>            (fed via stdin, not argv)\n"
        return

    # If host+user is set but NO auth source matches, fail fast with a
    # clear diagnostic instead of letting plink hit the network and
    # produce 'No supported authentication methods available'.
    if not session and not key and not password and not pageant:
        yield "ERROR: No SSH auth source configured.\n"
        yield "  Tried: SSH_SESSION (unset), SSH_KEY (unset), Pageant (not running), SSH_PASS (empty)\n"
        yield "  Pick one of:\n"
        yield "    SSH_KEY=<path-to-.ppk>           (recommended; key auth)\n"
        yield "    SSH_SESSION=<putty-session-name> (use a saved PuTTY session)\n"
        yield "    SSH_PASS=<password>              (fed to plink via stdin)\n"
        return

    if not PLINK_PATH:
        yield "ERROR: plink.exe not found. Set PLINK_PATH env var or install PuTTY.\n"
        yield f"Searched: PLINK_PATH env, PATH, {', '.join(str(p) for p in _PLINK_SEARCH_PATHS)}\n"
        return

    # Build the argv. Password (when used) goes via stdin, NOT -pw,
    # so it doesn't appear in the process command line. Argv-list form
    # is preserved end-to-end; no shell interpolation.
    full_cmd: list[str] = [PLINK_PATH, "-ssh", "-batch"]
    stdin_payload: bytes | None = None
    auth_mode = "unknown"

    if session:
        full_cmd += ["-load", session]
        if host and user:
            full_cmd.append(f"{user}@{host}")
        elif host:
            full_cmd.append(host)
        auth_mode = f"session:{session}"
    else:
        if key:
            full_cmd += ["-i", key]
            auth_mode = "key"
        elif pageant:
            auth_mode = "pageant"
        elif password:
            stdin_payload = (password + "\n").encode("utf-8")
            auth_mode = "password-stdin"
        full_cmd.append(f"{user}@{host}")

    full_cmd.append(cmd)

    try:
        proc = await asyncio.create_subprocess_exec(
            *full_cmd,
            stdin=asyncio.subprocess.PIPE if stdin_payload is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except PermissionError:
        yield f"ERROR: Permission denied running {PLINK_PATH}. Windows Defender or AV may be blocking it.\n"
        return
    except FileNotFoundError:
        yield f"ERROR: plink.exe not found at {PLINK_PATH}\n"
        return

    logger.debug(f"SSH connect via {auth_mode} to {user or '?'}@{host or '?'}")

    if stdin_payload is not None:
        try:
            proc.stdin.write(stdin_payload)
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass

    async def stream_lines(stream):
        while True:
            line = await stream.readline()
            if not line:
                break
            yield line.decode("utf-8", errors="replace").strip() + "\n"

    async for line in stream_lines(proc.stdout):
        yield line
    async for line in stream_lines(proc.stderr):
        yield f"STDERR: {line}"

    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        yield f"ERROR: Command timed out after {timeout}s\n"


def register(server):

    @server.tool()
    async def sassy_linux_exec(command: str, timeout_seconds: int = 60) -> str:
        """Execute command on remote Linux via SSH (plink).

        Returns combined stdout/stderr output.
        Set SSH_HOST, SSH_USER, SSH_PASS env vars for connection.
        """
        ok, err = validate_command(command)
        if not ok:
            return f"Error: {err}"
        is_del, kw = detect_delete_intent(command)
        if is_del:
            _audit.log_intercept("sassy_linux_exec", kw, command, [], ["remote delete blocked"])
            return (
                f"Error: Delete command blocked by interceptor ('{kw}'). "
                "sassy_linux_exec cannot run destructive file operations on the remote host. "
                "SSH in manually if you need to remove files."
            )
        timeout_seconds = min(max(timeout_seconds, 1), 300)
        output = []
        async for chunk in _ssh_exec_stream(command, timeout_seconds):
            output.append(chunk)
        return "".join(output)

    # TODO: sassy_linux_gpu_status, sassy_linux_docker, sassy_linux_apt, sassy_linux_scp
    # These need real implementations — not registering stubs.

    # Persist last working directory if state_manager is available
    state = getattr(server, "state", None)
    if state:
        try:
            state.set("linux", "last_cwd", "/root")
        except Exception:
            pass

    auth_summary = (
        f"session={_env_ssh_session() or '-'}, "
        f"key={'set' if _env_ssh_key() else '-'}, "
        f"pageant={'yes' if _pageant_running() else 'no'}, "
        f"password={'set' if _env_ssh_pass() else '-'}"
    )
    logger.info(f"Linux module loaded (plink: {PLINK_PATH or 'NOT FOUND'}; auth: {auth_summary})")
