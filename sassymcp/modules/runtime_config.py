"""Runtime Config — Full DC-parity dynamic configuration and system info.

Provides get_config (rich system snapshot), set_config_value,
and get_recent_tool_calls (session history from audit log).
Persisted to ~/.sassymcp/config.json.
"""

import json
import logging
import os
import platform
import sys
import time
from pathlib import Path

logger = logging.getLogger("sassymcp.config")

CONFIG_DIR = Path.home() / ".sassymcp"
CONFIG_FILE = CONFIG_DIR / "config.json"

_DEFAULTS = {
    "defaultShell": "powershell",
    "fileReadLineLimit": 1000,
    "fileWriteLineLimit": 500,
    "allowedDirectories": [],
    "blockedCommands": [],
}

_config: dict = {}
_start_time = time.time()


def _load():
    global _config
    try:
        if CONFIG_FILE.exists():
            raw = json.loads(CONFIG_FILE.read_text())
            _config.update(raw)
    except Exception as e:
        logger.warning(f"Failed to load config: {e}")
    for k, v in _DEFAULTS.items():
        _config.setdefault(k, v)


def _save():
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(_config, indent=2))
    except Exception as e:
        logger.warning(f"Failed to save config: {e}")


def get(key: str, default=None):
    if not _config:
        _load()
    return _config.get(key, default)


def set_val(key: str, value):
    if not _config:
        _load()
    _config[key] = value
    _save()


_load()


def _get_system_info() -> dict:
    """Gather rich system info matching DC's get_config output."""
    import psutil

    proc = psutil.Process(os.getpid())
    mem = proc.memory_info()

    # Python info
    py_info = {
        "version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "executable": sys.executable,
    }

    # OS info
    os_info = {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "node": platform.node(),
    }

    # Memory
    vm = psutil.virtual_memory()
    mem_info = {
        "process_rss_mb": round(mem.rss / 1048576, 2),
        "process_vms_mb": round(mem.vms / 1048576, 2),
        "system_total_gb": round(vm.total / 1073741824, 2),
        "system_available_gb": round(vm.available / 1073741824, 2),
        "system_percent": vm.percent,
    }

    # Disk for common drives
    disks = {}
    for letter in ("C", "V"):
        try:
            usage = psutil.disk_usage(f"{letter}:\\")
            disks[f"{letter}:"] = {
                "total_gb": round(usage.total / 1073741824, 2),
                "free_gb": round(usage.free / 1073741824, 2),
                "percent_used": usage.percent,
            }
        except Exception:
            pass

    return {
        "platform": os_info,
        "python": py_info,
        "memory": mem_info,
        "disks": disks,
        "pid": os.getpid(),
        "uptime_seconds": int(time.time() - _start_time),
        "cpu_count": psutil.cpu_count(),
    }


def _get_tool_usage_stats() -> dict:
    """Pull stats from audit log if available."""
    try:
        from sassymcp.modules.audit import get_stats
        return get_stats()
    except Exception:
        return {"note": "audit module not loaded"}


def register(server):

    @server.tool()
    async def sassy_get_config() -> str:
        """Get full SassyMCP configuration and system info.

        Returns: config settings, system info (OS, Python, memory, disk),
        uptime, loaded modules, and tool usage stats. DC get_config equivalent.
        """
        # Determine loaded modules from env
        groups_env = os.environ.get("SASSYMCP_GROUPS", "")
        load_all = os.environ.get("SASSYMCP_LOAD_ALL", "")

        result = {
            "config": _config,
            "systemInfo": _get_system_info(),
            "server": {
                "version": "1.0.0",
                "name": "sassymcp",
                "transport": "http" if "--http" in sys.argv else "stdio",
                "groups_env": groups_env or "(default)",
                "load_all": load_all == "1",
            },
            "toolUsage": _get_tool_usage_stats(),
        }
        return json.dumps(result, indent=2)

    @server.tool()
    async def sassy_set_config(key: str, value: str) -> str:
        """Set a runtime config value.

        Supported keys: defaultShell, fileReadLineLimit,
        fileWriteLineLimit, allowedDirectories, blockedCommands.
        value: JSON-encoded value (e.g. '"powershell"', '1000', '[]')
        """
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = value
        if key not in _DEFAULTS:
            return f"Unknown config key: {key}. Valid: {list(_DEFAULTS.keys())}"
        old = _config.get(key)
        set_val(key, parsed)
        return json.dumps({"key": key, "old": old, "new": parsed})

    @server.tool()
    async def sassy_recent_tool_calls(
        max_results: int = 50,
        tool_name: str = "",
        since_minutes: int = 0,
    ) -> str:
        """Get recent tool call history from audit log.

        max_results: how many to return (1-1000)
        tool_name: filter to specific tool (optional)
        since_minutes: only calls within last N minutes (0=all)
        """
        audit_file = CONFIG_DIR / "audit.jsonl"
        if not audit_file.exists():
            return json.dumps({"calls": [], "note": "No audit log found"})

        cutoff = 0.0
        if since_minutes > 0:
            cutoff = time.time() - (since_minutes * 60)

        calls = []
        total_lines = 0
        try:
            raw_lines = audit_file.read_text().strip().split("\n")
            total_lines = len(raw_lines)
            for line in raw_lines:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if cutoff and entry.get("timestamp", 0) < cutoff:
                    continue
                if tool_name and entry.get("tool") != tool_name:
                    continue
                calls.append(entry)
        except Exception as e:
            return json.dumps({"error": str(e)})

        # Return most recent N
        calls = calls[-max_results:]
        return json.dumps({
            "calls": calls,
            "count": len(calls),
            "total_in_log": total_lines,
        }, indent=2)
