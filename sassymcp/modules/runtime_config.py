"""Runtime Config — Full DC-parity dynamic configuration and system info.

Provides get_config (rich system snapshot), set_config_value,
and get_recent_tool_calls (session history from audit log).
Persisted to $SASSYMCP_HOME/config.json (default ~/.sassymcp/config.json).
"""

import json
import logging
import os
import platform
import sys
import time
from pathlib import Path

from sassymcp import __version__
from sassymcp._atomic import atomic_write_json
from sassymcp._paths import HOME as CONFIG_DIR, CONFIG_FILE

logger = logging.getLogger("sassymcp.config")

_DEFAULTS = {
    "defaultShell": "powershell",
    "fileReadLineLimit": 1000,
    "fileWriteLineLimit": 500,
    "allowedDirectories": [],
    "blockedCommands": [],
    # interceptor.destructiveAction: "block" (default — return error string)
    #                                "confirm" (return confirm-token JSON;
    #                                client calls sassy_shell_confirm to run)
    "interceptor.destructiveAction": "block",
    # interceptor.scanStringLiterals: when False (default), block-list words
    # found ONLY inside quoted strings are reported as low-tier and allowed
    # by sassy_shell after a log entry. Set True to restore strict scanning.
    "interceptor.scanStringLiterals": False,
    # ── Permission engine (sassymcp.policy) ──────────────────────────
    # permission.mode: "" (default — derive from interceptor.destructiveAction
    #   for back-compat: block->strict, confirm->confirm) or one of:
    #   "strict"  — block destructive patterns everywhere (today's behavior)
    #   "confirm" — destructive patterns return a confirm token
    #   "sandbox" — relaxed gating INSIDE sandboxRoots; any path resolving
    #               OUTSIDE every root is denied (ungated-LLM-but-jailed mode)
    #   "bypass"  — allow everything except protected paths (explicit, audited)
    "permission.mode": "",
    # permission.sandboxRoots: absolute paths that bound sandbox mode. Empty
    # = use the process CWD (the "project folder") as the single root.
    "permission.sandboxRoots": [],
    # permission.rules: Claude-Code-style allow/ask/deny rules evaluated
    # before the mode default; first match wins. Each rule is a dict:
    #   {"action": "allow"|"ask"|"deny", "tool": <glob>, "path": <glob>,
    #    "command": <regex>}  (omitted fields match anything)
    "permission.rules": [],
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
        atomic_write_json(CONFIG_FILE, _config)
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
                "version": __version__,
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
    async def sassy_permission(
        action: str = "status",
        mode: str = "",
        path: str = "",
        rule: str = "",
    ) -> str:
        """View and control the permission engine (sassymcp.policy).

        Single front door for the four-mode safety system that gates the
        shell and file tools. The future Control Panel UI writes the same
        config keys this tool does.

        action:
          status        (default) show effective mode, how it was derived,
                        the sandbox roots, and the active allow/ask/deny rules.
          set_mode      set permission.mode. mode= one of:
                          strict  — block destructive patterns everywhere
                          confirm — destructive patterns need a confirm token
                          sandbox — relaxed gating INSIDE the sandbox roots;
                                    anything resolving outside is refused
                          bypass  — allow all except protected paths
                          ""      — clear the override; derive from the legacy
                                    interceptor.destructiveAction setting
          add_root      add path= to permission.sandboxRoots (the jail)
          remove_root   remove path= from permission.sandboxRoots
          add_rule      append rule= (JSON: {"action","tool","path","command"})
                        to permission.rules; first match wins, evaluated before
                        the mode default
          clear_rules   remove all rules
        """
        from sassymcp import policy

        def _status() -> dict:
            return {
                "effective_mode": policy.current_mode(),
                "permission.mode": get("permission.mode", "") or "(unset — derived)",
                "derived_from": ("interceptor.destructiveAction="
                                 f"{get('interceptor.destructiveAction', 'block')}"
                                 if not (get('permission.mode', '') or '').strip()
                                 else "explicit permission.mode"),
                "sandbox_roots": [str(r) for r in policy.sandbox_roots()],
                "sandbox_roots_configured": get("permission.sandboxRoots", []),
                "rules": get("permission.rules", []),
                "valid_modes": list(policy.VALID_MODES),
            }

        action = (action or "status").strip().lower()

        if action == "status":
            return json.dumps(_status(), indent=2)

        if action == "set_mode":
            m = (mode or "").strip().lower()
            if m and m not in policy.VALID_MODES:
                return f"Invalid mode {mode!r}. Valid: {list(policy.VALID_MODES)} (or '' to clear)"
            set_val("permission.mode", m)
            return json.dumps({"set": "permission.mode", "value": m or "(cleared)",
                               "effective_mode": policy.current_mode()}, indent=2)

        if action in ("add_root", "remove_root"):
            if not path.strip():
                return "Error: path= is required for add_root/remove_root"
            roots = list(get("permission.sandboxRoots", []) or [])
            if action == "add_root":
                if path not in roots:
                    roots.append(path)
            else:
                roots = [r for r in roots if r != path]
            set_val("permission.sandboxRoots", roots)
            return json.dumps({"sandboxRoots": roots,
                               "resolved": [str(r) for r in policy.sandbox_roots()]}, indent=2)

        if action == "add_rule":
            try:
                parsed = json.loads(rule)
            except (json.JSONDecodeError, TypeError):
                return ('Error: rule= must be JSON, e.g. '
                        '{"action":"deny","tool":"sassy_shell","command":"rm"}')
            if not isinstance(parsed, dict) or str(parsed.get("action", "")).lower() not in ("allow", "ask", "deny"):
                return 'Error: rule must be a dict with action in allow|ask|deny'
            rules = list(get("permission.rules", []) or [])
            rules.append(parsed)
            set_val("permission.rules", rules)
            return json.dumps({"added": parsed, "rules": rules}, indent=2)

        if action == "clear_rules":
            set_val("permission.rules", [])
            return json.dumps({"cleared": True, "rules": []})

        return (f"Unknown action {action!r}. Use: status, set_mode, add_root, "
                "remove_root, add_rule, clear_rules")

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
