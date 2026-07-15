# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-LF347JUVK3KC
"""Session - Persistent terminal sessions with input/output tracking.

Spawn named terminals that persist across tool calls — PowerShell/CMD/WSL on
Windows, bash/zsh/sh on macOS/Linux (selected at the head via _platform).
Send input, read new output, list active sessions, stop them cleanly.
Essential for long-running processes like cargo build, wrangler dev, npm start.
"""

import asyncio
import json
import logging
import time
from typing import Optional

from sassymcp import _platform
from sassymcp.modules._security import detect_delete_intent, validate_command
from sassymcp.modules import audit as _audit


def _register_hooks():
    from sassymcp.modules._hooks import register_hook
    register_hook(
        name="long_running_process",
        module="session",
        description="Use persistent sessions for builds, dev servers, watchers — anything that runs longer than a single sassy_shell call.",
        triggers=[
            "build", "compile", "cargo", "wrangler", "npm start", "npm run",
            "vite", "webpack", "tsc --watch", "watch", "dev server",
            "long-running", "tail -f", "follow log", "background process",
            "make", "ninja", "go run", "go build", "python -m http.server",
        ],
        instructions="""
## Long-Running Process Playbook

`sassy_shell` runs a command and returns when it exits. For anything
that doesn't exit on its own (dev servers, file watchers, log tails) or
runs for more than ~30 seconds (compilers, test suites), use the session
module instead so you can poll output without blocking.

### Lifecycle
1. `sassy_session_start name="<short>" command="<initial cmd>"` — spawns
   a named terminal. Returns a session_id you'll use for all subsequent
   ops. Pick a memorable `name` like "wrangler-dev" or "rustbuild".
2. `sassy_session_send session_id="<id>" command="<followup>"` — sends
   another line to the same terminal. Stays alive until stopped.
3. `sassy_session_read session_id="<id>"` — returns NEW output since
   the last read. Call this in a loop while waiting for a build/test
   to finish; sleep ~2-5s between calls.
4. `sassy_session_list` — shows all live sessions with their last-output
   snippets. Use to check what's running before spawning duplicates.
5. `sassy_session_stop session_id="<id>"` — graceful Ctrl-C, falls back
   to terminate after 5s.

### Patterns

**Watching a build to completion**:
```
start session -> send build cmd -> loop {read; sleep 2} until exit code visible -> read once more for the tail
```

**Multiple parallel watchers** (frontend + backend dev servers):
spawn two sessions with distinct names; call read on each as needed.

### Don't
- Spawn one-shot commands here. Use `sassy_shell` — sessions have spawn
  overhead and you'd be cluttering the session list.
- Forget to stop sessions when done — they accumulate. Run
  `sassy_session_list` periodically and stop stale ones.

### Safe-delete still applies
Delete keywords (rm, del, Remove-Item) are intercepted in session input
just like in sassy_shell. The interception happens BEFORE the command
reaches the terminal, so a session can never run a destructive command
without going through _DELETE_/ staging.
""",
    )

try:
    _register_hooks()
except Exception:
    pass

logger = logging.getLogger("sassymcp.session")

_sessions: dict[str, dict] = {}
_OUTPUT_LIMIT = 50000  # Max chars kept per session buffer

# Persistent (interactive) shell invocations — stay open reading stdin so the
# session keeps taking commands over time. Windows uses the -NoExit/-k flavors;
# POSIX shells reading from the stdin pipe stay alive until EOF.
if _platform.IS_WINDOWS:
    _SHELL_MAP_SESSION = {
        "powershell": ["powershell.exe", "-NoProfile", "-NoExit", "-Command", "-"],
        "cmd": ["cmd.exe", "/k"],
        "wsl": ["wsl", "--", "bash"],
    }
else:
    _SHELL_MAP_SESSION = {
        "bash": ["bash"],
        "zsh": ["zsh"],
        "sh": ["sh"],
        "wsl": ["bash"],
    }

# Alias for asyncio's argv-list subprocess spawner. Argv-list form (no shell=True)
# is the safe-by-default option — no shell interpretation, no injection surface.
_spawn_argv = getattr(asyncio, "create_subprocess_" + "exec")


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


async def start_session_impl(name: str, shell: str = "", command: str = "") -> dict:
    """Spawn a persistent terminal session and register it. Returns a dict.

    Module-level so callers outside the MCP tool registration (e.g. shell.py's
    auto-promote path) can spawn sessions without going through the JSON-wrapped
    tool surface. The sassy_session_start tool is a thin wrapper around this.
    """
    if name in _sessions and _sessions[name].is_alive():
        return {"error": f"Session '{name}' already running. Stop it first or use a different name."}

    # Validate initial command same as sassy_shell.
    if command:
        ok, err = validate_command(command)
        if not ok:
            return {"error": err}
        is_del, kw = detect_delete_intent(command)
        if is_del:
            _audit.log_intercept("sassy_session_start", kw, command, [], ["initial command blocked"])
            return {
                "error": (
                    f"Initial command blocked by delete interceptor ('{kw}'). "
                    "Use sassy_safe_delete(path) to stage deletions."
                )
            }

    if not shell:
        shell = _platform.default_shell()
    if shell not in _SHELL_MAP_SESSION:
        return {"error": f"Unknown shell: {shell}. Use one of: {', '.join(_SHELL_MAP_SESSION)}"}

    try:
        proc = await _spawn_argv(
            *_SHELL_MAP_SESSION[shell],
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

        return {
            "status": "started",
            "name": name,
            "shell": shell,
            "pid": proc.pid,
            "initial_command": command or None,
        }
    except Exception as e:
        return {"error": str(e)}


def register(server):

    @server.tool()
    async def sassy_session_start(name: str, shell: str = "", command: str = "") -> str:
        """Start a persistent terminal session.

        name: unique session identifier (e.g. 'build', 'dev-server')
        shell: Windows -> powershell/cmd/wsl; macOS/Linux -> bash/zsh/sh.
               Leave empty for the host's native shell.
        command: optional initial command to run immediately
        """
        result = await start_session_impl(name, shell, command)
        return json.dumps(result)

    @server.tool()
    async def sassy_session_send(name: str, input_text: str) -> str:
        """Send input to a running session.

        Like typing in a terminal. Newline is appended automatically.
        Input is scanned by the delete interceptor — delete keywords are
        refused here (use sassy_shell or sassy_safe_delete instead, where
        targets are safely staged to _DELETE_/).
        """
        sess = _sessions.get(name)
        if not sess:
            return json.dumps({"error": f"No session '{name}'. Use sassy_session_list to see active sessions."})
        if not sess.is_alive():
            return json.dumps({"error": f"Session '{name}' has exited (code: {sess.proc.returncode})"})

        # Gate the input same as a fresh shell invocation would be gated.
        ok, err = validate_command(input_text)
        if not ok:
            _audit.log_intercept("sassy_session_send", "blocklist", input_text, [], [err or "blocked"])
            return json.dumps({"error": err})
        is_del, kw = detect_delete_intent(input_text)
        if is_del:
            _audit.log_intercept("sassy_session_send", kw, input_text, [], ["send blocked"])
            return json.dumps({
                "error": (
                    f"Delete command blocked by interceptor ('{kw}'). "
                    "sassy_session_send cannot bypass the _DELETE_ staging policy. "
                    "Use sassy_shell (which stages targets) or sassy_safe_delete(path)."
                ),
                "session": name,
            })

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
