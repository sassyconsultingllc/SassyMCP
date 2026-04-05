"""Audit - Tool call logging with automatic rotation.

Logs every MCP tool invocation with timestamp, tool name, and sanitized
arguments. Logs are stored in ~/.sassymcp/audit.log and rotated at 10MB.
"""

import json
import time
from pathlib import Path

_LOG_DIR = Path.home() / ".sassymcp"
_LOG_FILE = _LOG_DIR / "audit.log"
_JSONL_FILE = _LOG_DIR / "audit.jsonl"
_MAX_LOG_SIZE = 10 * 1024 * 1024  # 10 MB
_MAX_ROTATIONS = 5

# In-memory counters for get_stats()
_stats = {
    "total_calls": 0,
    "successful_calls": 0,
    "failed_calls": 0,
    "tool_counts": {},
    "session_start": time.time(),
}


def get_stats() -> dict:
    """Return session usage statistics."""
    return {
        "total_calls": _stats["total_calls"],
        "successful_calls": _stats["successful_calls"],
        "failed_calls": _stats["failed_calls"],
        "tool_counts": dict(sorted(
            _stats["tool_counts"].items(),
            key=lambda x: x[1], reverse=True
        )),
        "session_uptime_seconds": int(time.time() - _stats["session_start"]),
    }


def _rotate_if_needed():
    """Rotate log file if it exceeds max size."""
    if not _LOG_FILE.exists():
        return
    try:
        if _LOG_FILE.stat().st_size < _MAX_LOG_SIZE:
            return
    except OSError:
        return

    # Rotate: audit.log -> audit.log.1 -> audit.log.2 -> ...
    for i in range(_MAX_ROTATIONS - 1, 0, -1):
        src = _LOG_DIR / f"audit.log.{i}"
        dst = _LOG_DIR / f"audit.log.{i + 1}"
        if src.exists():
            try:
                src.rename(dst)
            except OSError:
                pass

    try:
        _LOG_FILE.rename(_LOG_DIR / "audit.log.1")
    except OSError:
        pass


def _rotate_jsonl_if_needed():
    """Rotate JSONL file if it exceeds max size."""
    if not _JSONL_FILE.exists():
        return
    try:
        if _JSONL_FILE.stat().st_size < _MAX_LOG_SIZE:
            return
    except OSError:
        return
    for i in range(_MAX_ROTATIONS - 1, 0, -1):
        src = _LOG_DIR / f"audit.jsonl.{i}"
        dst = _LOG_DIR / f"audit.jsonl.{i + 1}"
        if src.exists():
            try:
                src.rename(dst)
            except OSError:
                pass
    try:
        _JSONL_FILE.rename(_LOG_DIR / "audit.jsonl.1")
    except OSError:
        pass


def log_tool_call(tool_name: str, args: dict, elapsed_ms: int = 0, error: str = None):
    """Log a tool invocation. Called by the audit wrapper in server.py.

    Args:
        tool_name: Name of the tool that was called.
        args: Sanitized arguments dict.
        elapsed_ms: Execution time in milliseconds.
        error: Error message if the tool raised an exception.
    """
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed()

        # Sanitize: truncate long values, redact potential secrets
        sanitized = {}
        for k, v in args.items():
            s = str(v)
            if len(s) > 200:
                s = s[:200] + "...(truncated)"
            sanitized[k] = s

        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "timestamp": time.time(),
            "tool": tool_name,
            "args": sanitized,
            "ms": elapsed_ms,
        }
        if error:
            entry["error"] = error[:500]

        # Update in-memory stats
        _stats["total_calls"] += 1
        if error:
            _stats["failed_calls"] += 1
        else:
            _stats["successful_calls"] += 1
        _stats["tool_counts"][tool_name] = _stats["tool_counts"].get(tool_name, 0) + 1

        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

        # Also write JSONL for structured queries (rotate same as log)
        _rotate_jsonl_if_needed()
        with _JSONL_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # Don't let logging failures break tools


def register(server):
    @server.tool()
    async def sassy_audit_log(count: int = 50) -> str:
        """Read recent audit log entries."""
        if not _LOG_FILE.exists():
            return "No audit log entries yet"

        try:
            lines = _LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            return f"Error reading audit log: {e}"

        recent = lines[-count:] if len(lines) > count else lines
        return "\n".join(recent)

    @server.tool()
    async def sassy_audit_search(keyword: str, count: int = 50) -> str:
        """Search audit log for a keyword."""
        if not _LOG_FILE.exists():
            return "No audit log entries yet"

        try:
            lines = _LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            return f"Error reading audit log: {e}"

        needle = keyword.lower()
        matches = [line for line in lines if needle in line.lower()]
        recent = matches[-count:] if len(matches) > count else matches
        return "\n".join(recent) if recent else f"No entries matching '{keyword}'"

    @server.tool()
    async def sassy_audit_clear() -> str:
        """Clear the audit log."""
        try:
            if _LOG_FILE.exists():
                _LOG_FILE.unlink()
            # Also clear rotated files
            for i in range(1, _MAX_ROTATIONS + 1):
                rotated = _LOG_DIR / f"audit.log.{i}"
                if rotated.exists():
                    rotated.unlink()
            return "Audit log cleared"
        except OSError as e:
            return f"Error clearing audit log: {e}"
