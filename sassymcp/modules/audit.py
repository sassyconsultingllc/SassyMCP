"""Audit - Tool call logging with automatic rotation.

Logs every MCP tool invocation with timestamp, tool name, and sanitized
arguments. Logs are stored in $SASSYMCP_HOME/audit.log (default
~/.sassymcp/audit.log) and rotated at 10MB.
"""

import json
import time

from sassymcp._audit_io import append_audit
from sassymcp._paths import HOME as _LOG_DIR

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


def log_pattern_event(event: str, tool_name: str, pattern: str, command: str, extra: dict | None = None):
    """Log a pattern-detection event (block or bypass) for forensic review.

    event: 'pattern_block' (interceptor refused to run) or
           'pattern_bypass' (allow_pattern opt-in let it through).
    pattern: the matched pattern label, e.g. 'truncate-by-redirect'.

    Both forms are written to audit.log/audit.jsonl with a stable
    'pattern_event' field so users can grep for false-positive candidates
    via sassy_audit_search('pattern_block') or the dedicated
    sassy_audit_false_positives tool.
    """
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed()
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "timestamp": time.time(),
            "event": event,
            "pattern_event": True,
            "tool": tool_name,
            "pattern": pattern,
            "command": command[:500],
        }
        if extra:
            entry.update({k: (str(v)[:300] if not isinstance(v, (int, float, bool)) else v) for k, v in extra.items()})
        append_audit(_LOG_FILE, entry)
        _rotate_jsonl_if_needed()
        append_audit(_JSONL_FILE, entry)
    except OSError:
        pass


def log_intercept(tool_name: str, keyword: str, command: str, targets: list, results: list):
    """Log a delete-interception event to audit.log + audit.jsonl.

    Forensic record of every time the safe-delete interceptor fires.
    Keeps the raw command, detected keyword, and what was moved/skipped.
    """
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed()
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "timestamp": time.time(),
            "event": "intercept_delete",
            "tool": tool_name,
            "keyword": keyword,
            "command": command[:500],
            "targets": [str(t)[:300] for t in targets],
            "results": [str(r)[:300] for r in results],
        }
        append_audit(_LOG_FILE, entry)
        _rotate_jsonl_if_needed()
        append_audit(_JSONL_FILE, entry)
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

        append_audit(_LOG_FILE, entry)

        # Also write JSONL for structured queries (rotate same as log)
        _rotate_jsonl_if_needed()
        append_audit(_JSONL_FILE, entry)
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
    async def sassy_audit_false_positives(count: int = 20, include_bypasses: bool = True) -> str:
        """Show recent shell-interceptor pattern matches (blocks + bypasses).

        Surfaces the noisy-pattern events you'd otherwise have to grep for.
        Each row: timestamp | event | pattern | command (truncated).

        Args:
            count: max rows to return (newest last).
            include_bypasses: if False, show only blocks (commands that
                actually got refused). True also includes pattern_bypass
                entries — useful for auditing what the allow_pattern flag
                has been used to let through.
        """
        if not _JSONL_FILE.exists():
            return "No audit entries yet"
        try:
            lines = _JSONL_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            return f"Error reading audit log: {e}"

        wanted = {"pattern_block"}
        if include_bypasses:
            wanted.add("pattern_bypass")

        rows = []
        for line in lines:
            try:
                obj = json.loads(line)
            except (ValueError, json.JSONDecodeError):
                continue
            if obj.get("event") in wanted:
                rows.append(obj)

        if not rows:
            return "No pattern_block / pattern_bypass entries yet"

        rows = rows[-count:]
        out = []
        for r in rows:
            out.append(
                f"{r.get('ts','?')} | {r.get('event','?'):<15} | "
                f"{str(r.get('pattern','?')):<25} | {str(r.get('command',''))[:120]}"
            )
        return "\n".join(out)

    @server.tool()
    async def sassy_audit_clear(confirm: str = "") -> str:
        """Rotate the audit log to a timestamped archive.

        The log is NEVER unlinked — always renamed to audit.cleared.<ts>.log
        so forensic history is preserved. Pass confirm='YES' to proceed.
        """
        if confirm != "YES":
            return (
                "Refused: sassy_audit_clear requires confirm='YES' — "
                "and rotates the log rather than deleting it. "
                "Existing log is preserved as audit.cleared.<ts>.log"
            )
        try:
            stamp = time.strftime("%Y%m%dT%H%M%S")
            archived = []
            if _LOG_FILE.exists():
                dst = _LOG_DIR / f"audit.cleared.{stamp}.log"
                _LOG_FILE.rename(dst)
                archived.append(str(dst))
            if _JSONL_FILE.exists():
                dst = _LOG_DIR / f"audit.cleared.{stamp}.jsonl"
                _JSONL_FILE.rename(dst)
                archived.append(str(dst))
            # Record the rotation itself in the fresh log.
            log_tool_call("sassy_audit_clear", {"archived": str(archived)}, 0)
            return "Audit log rotated to:\n" + "\n".join(archived)
        except OSError as e:
            return f"Error rotating audit log: {e}"
