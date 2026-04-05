"""Linux Module — Streaming SSH execution via plink.

Long-running commands stream partial output in real time via MCP streaming.
"""

import asyncio
import logging
import os
import shutil
from pathlib import Path

from sassymcp.modules._security import validate_command

logger = logging.getLogger("sassymcp.linux")

SSH_HOST = os.environ.get("SSH_HOST", "")
SSH_USER = os.environ.get("SSH_USER", "")
SSH_PASS = os.environ.get("SSH_PASS", "")

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
    """Streaming generator for real-time SSH output via plink."""
    if not SSH_HOST or not SSH_USER or not SSH_PASS:
        yield "ERROR: SSH credentials not configured. Set SSH_HOST, SSH_USER, SSH_PASS env vars.\n"
        yield "  SSH_HOST: hostname or IP of the remote Linux machine\n"
        yield "  SSH_USER: SSH username\n"
        yield "  SSH_PASS: SSH password (for plink -pw authentication)\n"
        return

    if not PLINK_PATH:
        yield "ERROR: plink.exe not found. Set PLINK_PATH env var or install PuTTY.\n"
        yield f"Searched: PLINK_PATH env, PATH, {', '.join(str(p) for p in _PLINK_SEARCH_PATHS)}\n"
        return

    full_cmd = [PLINK_PATH, "-ssh", "-pw", SSH_PASS, "-batch", f"{SSH_USER}@{SSH_HOST}", cmd]

    try:
        proc = await asyncio.create_subprocess_exec(
            *full_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except PermissionError:
        yield f"ERROR: Permission denied running {PLINK_PATH}. Windows Defender or AV may be blocking it.\n"
        return
    except FileNotFoundError:
        yield f"ERROR: plink.exe not found at {PLINK_PATH}\n"
        return

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

    logger.info(f"Linux module loaded (plink: {PLINK_PATH or 'NOT FOUND'})")
