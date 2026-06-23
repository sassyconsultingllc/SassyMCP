"""Single source of truth for OS detection and per-command platform routing.

This is "the head": the host OS is resolved exactly once, at import, and
every module asks this module which concrete command to run for the host
it landed on. The same SassyMCP source ships to every user; on a Mac the
commands route the macOS way, on Windows the Windows way, on Linux the
Linux way — no per-OS source forks.

Two layers:

1. Detection constants (`IS_WINDOWS`, `IS_MACOS`, `IS_LINUX`, `IS_POSIX`,
   `OS`, `OS_LABEL`). Resolved at import and treated as immutable for the
   process lifetime, matching the `_paths.HOME` convention.

2. Routing helpers. The workhorse is `pick(...)`: a module spells out the
   argv (or value) for each OS inline and `pick` returns the one that
   matches the host. This keeps each command's platform variants together
   and readable at the call site, instead of scattering `if os.name ==`
   ladders through every module. Named helpers (`default_shell`,
   `clipboard_get_argv`, `adb_candidates`, `open_path_argv`, ...) wrap the
   handful of routings that recur in more than one place so they are spelled
   once.

Design notes
------------
- Helpers return argv *lists* (`list[str]`), because every caller feeds
  `asyncio.create_subprocess_exec(*argv)` / `subprocess.run(argv)`, which
  avoids shell-injection and matches the existing module style.
- macOS and Linux frequently share a POSIX implementation; `pick` accepts a
  `posix=` arm that both fall back to when no OS-specific arm is given.
- When a capability genuinely has no host equivalent, callers raise
  `UnsupportedPlatform` (or surface `unsupported(feature)`), so the failure
  is an explicit, uniform message rather than a confusing native error.
"""
from __future__ import annotations

import os
import shutil
import sys
from typing import TypeVar

T = TypeVar("T")

__all__ = [
    "IS_WINDOWS",
    "IS_MACOS",
    "IS_LINUX",
    "IS_POSIX",
    "OS",
    "OS_LABEL",
    "UnsupportedPlatform",
    "pick",
    "unsupported",
    "which",
    "first_existing",
    "default_shell",
    "SHELL_MAP",
    "shell_argv",
    "clipboard_get_argv",
    "clipboard_set_argv",
    "open_path_argv",
    "open_app_argv",
    "adb_candidates",
]


# ── Detection (resolved once, at import) ──────────────────────────────────
IS_WINDOWS: bool = os.name == "nt"
IS_MACOS: bool = sys.platform == "darwin"
IS_LINUX: bool = sys.platform.startswith("linux")
IS_POSIX: bool = os.name == "posix"

if IS_WINDOWS:
    OS = "windows"
    OS_LABEL = "Windows"
elif IS_MACOS:
    OS = "macos"
    OS_LABEL = "macOS"
elif IS_LINUX:
    OS = "linux"
    OS_LABEL = "Linux"
else:  # other POSIX (BSD, etc.) — treated as linux-like for routing
    OS = "linux" if IS_POSIX else "unknown"
    OS_LABEL = sys.platform


class UnsupportedPlatform(RuntimeError):
    """Raised when a capability has no implementation on the host OS."""


# ── The workhorse: per-command routing ────────────────────────────────────
_UNSET = object()


def pick(
    *,
    windows: T = _UNSET,  # type: ignore[assignment]
    macos: T = _UNSET,  # type: ignore[assignment]
    linux: T = _UNSET,  # type: ignore[assignment]
    posix: T = _UNSET,  # type: ignore[assignment]
    default: T = _UNSET,  # type: ignore[assignment]
    feature: str = "",
) -> T:
    """Return the value for the host OS.

    Resolution order is most-specific first:
      Windows -> `windows`, else `default`.
      macOS   -> `macos`,   else `posix`, else `default`.
      Linux   -> `linux`,   else `posix`, else `default`.

    A missing arm with no `default` raises `UnsupportedPlatform` naming
    `feature` (or the caller's module). Use this so "no equivalent on this
    OS" is an explicit, uniform failure rather than a None slipping through.

    Typical use::

        argv = _platform.pick(
            windows=["powershell.exe", "-NoProfile", "-Command", ps],
            macos=["pbpaste"],
            linux=["xclip", "-selection", "clipboard", "-o"],
            feature="clipboard read",
        )
    """
    if IS_WINDOWS:
        candidates = (windows, default)
    elif IS_MACOS:
        candidates = (macos, posix, default)
    elif IS_LINUX:
        candidates = (linux, posix, default)
    else:
        candidates = (posix, default)

    for c in candidates:
        if c is not _UNSET:
            return c

    label = feature or "this operation"
    raise UnsupportedPlatform(f"{label} is not supported on {OS_LABEL}")


def unsupported(feature: str) -> str:
    """Uniform user-facing string for a capability missing on the host OS.

    Returned (not raised) by tool implementations so the MCP client gets a
    clean message instead of a traceback for the rare genuinely-absent
    capability.
    """
    return f"Not supported on {OS_LABEL}: {feature}"


# ── PATH / binary discovery ───────────────────────────────────────────────
def which(*names: str) -> str | None:
    """First of `names` found on PATH, else None."""
    for n in names:
        hit = shutil.which(n)
        if hit:
            return hit
    return None


def first_existing(*paths: str) -> str | None:
    """First of `paths` (expanded for ~ and env vars) that exists on disk."""
    for p in paths:
        if not p:
            continue
        expanded = os.path.expanduser(os.path.expandvars(p))
        if os.path.exists(expanded):
            return expanded
    return None


# ── Shell routing ─────────────────────────────────────────────────────────
def default_shell() -> str:
    """The `shell=` selector sassy_shell should default to on this host.

    Windows -> "powershell". POSIX -> the user's login shell basename when
    it is one we map (zsh/bash/sh), else "zsh" on macOS (the modern default)
    and "bash" on Linux. The returned token is always a key of SHELL_MAP.
    """
    if IS_WINDOWS:
        return "powershell"
    login = os.path.basename(os.environ.get("SHELL", "")).strip()
    if login in ("zsh", "bash", "sh"):
        return login
    return "zsh" if IS_MACOS else "bash"


def _posix_shell_map() -> dict[str, list[str]]:
    m = {
        "bash": ["bash", "-lc"],
        "zsh": ["zsh", "-lc"],
        "sh": ["sh", "-c"],
    }
    # `wsl` only makes sense on Windows; on POSIX, allow it to alias bash so a
    # cross-platform caller asking for "wsl" still runs under a POSIX shell.
    m["wsl"] = m["bash"]
    return m


# Single source of truth for "which argv launches shell X". Windows keeps the
# PowerShell/CMD/WSL trio; POSIX exposes bash/zsh/sh. shell.py reads this so
# the dispatch table is defined in exactly one place.
if IS_WINDOWS:
    SHELL_MAP: dict[str, list[str]] = {
        "powershell": ["powershell.exe", "-NoProfile", "-Command"],
        "cmd": ["cmd.exe", "/c"],
        "wsl": ["wsl", "--", "bash", "-c"],
    }
else:
    SHELL_MAP = _posix_shell_map()


def shell_argv(shell: str) -> list[str] | None:
    """argv prefix for shell selector `shell`, or None if unknown on host."""
    entry = SHELL_MAP.get(shell)
    return list(entry) if entry else None


# ── Clipboard routing ─────────────────────────────────────────────────────
def clipboard_get_argv() -> list[str]:
    """argv that prints the clipboard's text contents to stdout."""
    return pick(
        windows=["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard"],
        macos=["pbpaste"],
        linux=(
            ["xclip", "-selection", "clipboard", "-o"]
            if which("xclip")
            else ["xsel", "--clipboard", "--output"]
        ),
        feature="clipboard read",
    )


def clipboard_set_argv() -> list[str]:
    """argv that reads stdin and writes it to the clipboard.

    The text is fed on stdin (not embedded in the argv) so arbitrary content
    — quotes, newlines, unicode — round-trips without escaping games.
    """
    return pick(
        windows=["powershell.exe", "-NoProfile", "-Command", "$input | Set-Clipboard"],
        macos=["pbcopy"],
        linux=(
            ["xclip", "-selection", "clipboard"]
            if which("xclip")
            else ["xsel", "--clipboard", "--input"]
        ),
        feature="clipboard write",
    )


# ── "open" routing (files, URLs, apps) ────────────────────────────────────
def open_path_argv(path: str) -> list[str]:
    """argv that opens a file/URL with the OS default handler."""
    return pick(
        windows=["cmd.exe", "/c", "start", "", path],
        macos=["open", path],
        linux=["xdg-open", path],
        feature="open path",
    )


def open_app_argv(app: str, *args: str) -> list[str]:
    """argv that launches a GUI application by name.

    Windows: `start "" <app>`. macOS: `open -a <app>` (extra args after
    `--args`). Linux: best-effort direct exec.
    """
    if IS_MACOS:
        argv = ["open", "-a", app]
        if args:
            argv += ["--args", *args]
        return argv
    if IS_WINDOWS:
        return ["cmd.exe", "/c", "start", "", app, *args]
    return [app, *args]


# ── Android platform-tools (adb) discovery ────────────────────────────────
def adb_candidates() -> list[str]:
    """Likely absolute paths to an `adb` binary on this host, best-first.

    Callers should still fall back to the bare name "adb" (PATH lookup) when
    none of these exist.
    """
    if IS_WINDOWS:
        return [
            os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
            r"C:\Android\platform-tools\adb.exe",
        ]
    if IS_MACOS:
        return [
            os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
            "/opt/homebrew/bin/adb",
            "/usr/local/bin/adb",
        ]
    # Linux
    return [
        os.path.expanduser("~/Android/Sdk/platform-tools/adb"),
        os.path.expanduser("~/.local/share/android-sdk/platform-tools/adb"),
        "/usr/lib/android-sdk/platform-tools/adb",
        "/usr/local/bin/adb",
    ]
