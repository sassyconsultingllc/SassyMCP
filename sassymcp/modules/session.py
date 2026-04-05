"""Session - Persistent terminal sessions with input/output tracking.

Spawn named terminals (PowerShell, CMD, WSL) that persist across tool calls.
Send input, read new output, list active sessions, stop them cleanly.
Essential for long-running processes like cargo build, wrangler dev, npm start.
"""

import asyncio
import json
import logging
import time
from typing import Optional

logger = logging.getLogger("sassymcp.session")

_sessions: dict[str, dict] = {}
_OUTPUT_LIMIT = 50000  # Max chars kept per session buffer


class _Session:
    """A persistent subprocess with output buffering."""

    def __init__(self, name: str, proc: asyncio.subprocess.Process, shell: str):
        self.name = name
        self.proc = proc
        self.shell = shell
        self.created = time.time()
        self.buffer = ""
        self.read_cursor = 0
        self._reader_task: Optional[asyncio.Task] = None

    async def start_reader(self):
        """Background task that continuously reads stdout into buffer."""
        try:
            while True:
                data = await self.proc.stdout.read(4096)
                if not data:
                    break
                text = data.decode("utf-8", errors="replace")
                self.buffer += text
                # Trim buffer if too large (keep tail)
                if len(self.buffer) > _OUTPUT_LIMIT:
                    trimmed = len(self.buffer) - _OUTPUT_LIMIT
                    self.buffer = self.buffer[-_OUTPUT_LIMIT:]
                    if self.read_cursor > 0:
                        self.read_cursor = max(0, self.read_cursor - trimmed)
        except (asyncio.CancelledError, Exception):
            pass

    def get_new_output(self) -> str:
        """Return output since last read, advance cursor."""
        new = self.buffer[self.read_cursor:]
        self.read_cursor = len(self.buffer)
        return new

    def is_alive(self) -> bool:
        return self.proc.returncode is None

    async def send(self, text: str):
        """Send input to the process stdin."""
        if self.proc.stdin:
            self.proc.stdin.write((text + "\n").encode("utf-8"))
            await self.proc.stdin.drain()

    async def stop(self):
        """Terminate the process."""
        if self._reader_task:
            self._reader_task.cancel()
        if self.is_alive():
            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.proc.kill()


def register(server):

    @server.tool()
    async def sassy_session_start(name: str, shell: str = "powershell", command: str = "") -> str:
        """Start a persistent terminal session.

        name: unique session identifier (e.g. 'build', 'dev-server')
        shell: powershell, cmd, or wsl
        command: optional initial command to run immediately
        """
        if name in _sessions and _sessions[name].is_alive():
            return json.dumps({"error": f"Session '{name}' already running. Stop it first or use a different name."})

        shell_map = {
            "powershell": ["powershell.exe", "-NoProfile", "-NoExit", "-Command", "-"],
            "cmd": ["cmd.exe", "/k"],
            "wsl": ["wsl", "--", "bash"],
        }
        if shell not in shell_map:
            return json.dumps({"error": f"Unknown shell: {shell}. Use: powershell, cmd, wsl"})

        try:
            proc = await asyncio.create_subprocess_exec(
                *shell_map[shell],
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,  # Merge stderr into stdout
            )
            sess = _Session(name, proc, shell)
            sess._reader_task = asyncio.create_task(sess.start_reader())
            _sessions[name] = sess

            if command:
                await asyncio.sleep(0.3)  # Let shell initialize
                await sess.send(command)
                await asyncio.sleep(0.5)  # Let initial output arrive

            return json.dumps({
                "status": "started",
                "name": name,
                "shell": shell,
                "pid": proc.pid,
                "initial_command": command or None,
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    @server.tool()
    async def sassy_session_send(name: str, input_text: str) -> str:
        """Send input to a running session.

        Like typing in a terminal. Newline is appended automatically.
        """
        sess = _sessions.get(name)
        if not sess:
            return json.dumps({"error": f"No session '{name}'. Use sassy_session_list to see active sessions."})
        if not sess.is_alive():
            return json.dumps({"error": f"Session '{name}' has exited (code: {sess.proc.returncode})"})

        try:
            await sess.send(input_text)
            await asyncio.sleep(0.3)  # Brief pause for output
            new_output = sess.get_new_output()
            return json.dumps({
                "sent": input_text,
                "new_output": new_output[-5000:] if new_output else "(no output yet)",
                "session": name,
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    @server.tool()
    async def sassy_session_read(name: str) -> str:
        """Read new output from a session without sending input.

        Returns only text that arrived since the last read.
        """
        sess = _sessions.get(name)
        if not sess:
            return json.dumps({"error": f"No session '{name}'."})

        new_output = sess.get_new_output()
        return json.dumps({
            "session": name,
            "alive": sess.is_alive(),
            "new_output": new_output[-10000:] if new_output else "(no new output)",
            "total_buffer_size": len(sess.buffer),
        })

    @server.tool()
    async def sassy_session_list() -> str:
        """List all active terminal sessions."""
        now = time.time()
        sessions = []
        for name, sess in _sessions.items():
            sessions.append({
                "name": name,
                "shell": sess.shell,
                "pid": sess.proc.pid,
                "alive": sess.is_alive(),
                "uptime_seconds": int(now - sess.created),
                "buffer_size": len(sess.buffer),
                "exit_code": sess.proc.returncode,
            })
        return json.dumps({"sessions": sessions, "count": len(sessions)}, indent=2)

    @server.tool()
    async def sassy_session_stop(name: str) -> str:
        """Stop and clean up a terminal session."""
        sess = _sessions.pop(name, None)
        if not sess:
            return json.dumps({"error": f"No session '{name}'."})

        final_output = sess.get_new_output()
        await sess.stop()
        return json.dumps({
            "stopped": name,
            "exit_code": sess.proc.returncode,
            "final_output": final_output[-3000:] if final_output else "(empty)",
        })

    @server.tool()
    async def sassy_session_stop_all() -> str:
        """Stop all active terminal sessions."""
        names = list(_sessions.keys())
        results = []
        for name in names:
            sess = _sessions.pop(name)
            await sess.stop()
            results.append({"name": name, "exit_code": sess.proc.returncode})
        return json.dumps({"stopped": results, "count": len(results)})
