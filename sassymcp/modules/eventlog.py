# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-P3B2IEWOCE4S
"""EventLog - System log (Windows Event Log / macOS unified log / Linux journald) + Android logcat.

The host log source is selected at the head (see _platform):
  - Windows: Get-WinEvent (System/Application/Security)
  - macOS:   `log show` (unified logging)
  - Linux:   `journalctl`
The tool names and arguments are identical on every OS; only the underlying
command changes. level/source/keyword map to the closest native filter.
"""

import asyncio
import re

from sassymcp import _platform

_SAFE_NAME = re.compile(r'^[A-Za-z0-9 _\-\.]+$')


def _sanitize(value: str, label: str) -> str:
    """Reject values containing shell metacharacters."""
    if not _SAFE_NAME.match(value):
        raise ValueError(f"Invalid {label}: contains disallowed characters")
    return value


# ── Per-OS argv builders ──────────────────────────────────────────────────
# Every input is _sanitize()d to [A-Za-z0-9 _-.] before reaching these, so
# embedding into predicate strings is injection-safe, and argv form (no shell)
# keeps each predicate a single token.

def _win_read_argv(log_name: str, count: int, level: str, source: str) -> list[str]:
    filters = []
    if level:
        lvl = {"error": 2, "warning": 3, "information": 4}.get(level.lower())
        if lvl:
            filters.append(f"Level={lvl}")
    if source:
        filters.append(f"ProviderName='{source}'")
    xpath = f' -FilterXPath "*[System[{" and ".join(filters)}]]"' if filters else ""
    ps = (f'Get-WinEvent -LogName {log_name}{xpath} -MaxEvents {count} '
          f'| Select TimeCreated,LevelDisplayName,ProviderName,Message | FL')
    return ["powershell.exe", "-NoProfile", "-Command", ps]


def _win_search_argv(log_name: str, keyword: str, count: int) -> list[str]:
    ps = (f"Get-WinEvent -LogName {log_name} -MaxEvents 500 "
          f"| Where {{ $_.Message -like '*{keyword}*' }} "
          f"| Select -First {count} TimeCreated,LevelDisplayName,Message | FL")
    return ["powershell.exe", "-NoProfile", "-Command", ps]


def _macos_log_argv(count: int, level: str, source: str, keyword: str = "") -> list[str]:
    preds: list[str] = []
    if level:
        m = {
            "error": 'messageType == "error" OR messageType == "fault"',
            "warning": 'messageType == "default"',
            "information": 'messageType == "info"',
        }.get(level.lower())
        if m:
            preds.append(f"({m})")
    if source:
        preds.append(f'(process == "{source}" OR subsystem == "{source}")')
    if keyword:
        preds.append(f'eventMessage CONTAINS "{keyword}"')
    argv = ["log", "show", "--style", "syslog", "--last", "6h"]
    if level and level.lower() in ("info", "information"):
        argv.append("--info")
    if preds:
        argv += ["--predicate", " AND ".join(preds)]
    return argv


def _linux_journal_argv(count: int, level: str, source: str, keyword: str = "") -> list[str]:
    argv = ["journalctl", "--no-pager", "-n", str(count)]
    if level:
        p = {"error": "3", "warning": "4", "information": "6"}.get(level.lower())
        if p:
            argv += ["-p", p]
    if source:
        argv += ["-t", source]
    if keyword:
        argv += ["-g", keyword]
    return argv


async def _run(argv: list[str], timeout: int = 30) -> str:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        out = stdout.decode("utf-8", errors="replace").strip()
        if not out:
            out = stderr.decode("utf-8", errors="replace").strip()
        return out[:5000]
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return f"Timed out after {timeout}s"


def register(server):
    @server.tool()
    async def sassy_eventlog(log_name: str = "System", count: int = 20, level: str = "", source: str = "") -> str:
        """Read the system log. Windows: log_name = System/Application/Security
        (Get-WinEvent). macOS: unified log (`log show`). Linux: journald
        (`journalctl`). level: error/warning/information. source: provider /
        process / subsystem name. log_name is Windows-only; ignored elsewhere."""
        log_name = _sanitize(log_name, "log_name")
        count = max(1, min(count, 1000))
        if source:
            source = _sanitize(source, "source")
        argv = _platform.pick(
            windows=_win_read_argv(log_name, count, level, source),
            macos=_macos_log_argv(count, level, source),
            linux=_linux_journal_argv(count, level, source),
            feature="system event log",
        )
        return await _run(argv)

    @server.tool()
    async def sassy_eventlog_search(keyword: str, log_name: str = "System", count: int = 20) -> str:
        """Search the system log for a keyword across hosts (Get-WinEvent /
        `log show` predicate / `journalctl -g`)."""
        log_name = _sanitize(log_name, "log_name")
        keyword = _sanitize(keyword, "keyword")
        count = max(1, min(count, 500))
        argv = _platform.pick(
            windows=_win_search_argv(log_name, keyword, count),
            macos=_macos_log_argv(count, "", "", keyword=keyword),
            linux=_linux_journal_argv(count, "", "", keyword=keyword),
            feature="system event log search",
        )
        return await _run(argv)

    @server.tool()
    async def sassy_android_logcat(tag: str = "", level: str = "", lines: int = 100, device: str = "") -> str:
        """Read Android logcat."""
        args = ["adb"] + (["-s", device] if device else []) + ["logcat", "-d", "-t", str(lines)]
        if tag and level:
            args.extend([f"{tag}:{level}", "*:S"])
        elif tag:
            args.extend([f"{tag}:V", "*:S"])
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            return stdout.decode("utf-8", errors="replace").strip()
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return "Timed out after 15s"
