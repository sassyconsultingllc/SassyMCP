# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-2MZZRNVGOE62
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
    "IS_LINUX",
    "IS_MACOS",
    "IS_POSIX",
    "IS_WINDOWS",
    "OS",
    "OS_LABEL",
    "SHELL_MAP",
    "UnsupportedPlatform",
    "adb_candidates",
    "adb_semaphore",
    "clipboard_get_argv",
    "clipboard_set_argv",
    "default_shell",
    "first_available",
    "first_existing",
    "install_argv",
    "install_hint",
    "is_root",
    "open_app_argv",
    "open_path_argv",
    "pick",
    "pkg_manager",
    "pkg_name",
    "resolve_adb",
    "shell_argv",
    "unsupported",
    "which",
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
        # BSD / illumos / other POSIX: prefer a generic POSIX arm, then the
        # Linux arm (most userland tools overlap), then the default.
        candidates = (posix, linux, default)

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


def first_available(*argvs: list[str], default: list[str] | None = None) -> list[str]:
    """First argv whose program (argv[0]) is on PATH or is an existing file.

    Lets one call site express "use ss, else netstat" or "nmcli, else iw"
    without nested ternaries, so minimal installs (Fedora/Kali/Alpine/BSD
    images often lack net-tools, bind-utils, etc.) degrade gracefully.
    Falls back to `default`, else the last candidate (so the downstream
    error names a real tool rather than an empty argv).
    """
    for argv in argvs:
        if argv and (shutil.which(argv[0]) or os.path.isfile(argv[0])):
            return list(argv)
    if default is not None:
        return list(default)
    return list(argvs[-1]) if argvs else []


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
    if login in ("zsh", "bash", "sh", "ksh", "dash") and which(login):
        return login
    # Login shell is fish/csh/tcsh/unset: pick a POSIX-compatible one that is
    # actually installed (Alpine/BSD/minimal containers often lack bash).
    for cand in (("zsh", "bash") if IS_MACOS else ("bash", "zsh")):
        if which(cand):
            return cand
    return "sh"


def _posix_shell_map() -> dict[str, list[str]]:
    m = {
        "bash": ["bash", "-lc"],
        "zsh": ["zsh", "-lc"],
        "sh": ["sh", "-c"],
    }
    for extra in ("ksh", "dash"):
        if which(extra):
            m[extra] = [extra, "-c"]
    # `wsl` only makes sense on Windows; on POSIX, alias it to bash (or sh when
    # bash is absent) so a cross-platform caller asking for "wsl" still runs.
    m["wsl"] = m["bash"] if which("bash") else m["sh"]
    # PowerShell 7 runs on Linux/macOS/BSD as `pwsh`; expose it under the
    # Windows name so scripts that request shell="powershell" keep working.
    if which("pwsh"):
        m["powershell"] = ["pwsh", "-NoProfile", "-Command"]
        m["pwsh"] = m["powershell"]
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
def _linux_clip(*, wayland: list[str], xclip: list[str], xsel: list[str]) -> list[str]:
    """Wayland (wl-clipboard) when in a Wayland session, else xclip/xsel.

    Fedora/Ubuntu default to Wayland; Kali/XFCE and most BSD desktops are
    X11. Under Wayland, xclip only sees XWayland's clipboard, so wl-clipboard
    is preferred when present.
    """
    if os.environ.get("WAYLAND_DISPLAY") and which(wayland[0]):
        return wayland
    if which("xclip"):
        return xclip
    if which("xsel"):
        return xsel
    if which(wayland[0]):
        return wayland
    return xclip  # surfaces a clean "xclip not found" downstream


def clipboard_get_argv() -> list[str]:
    """argv that prints the clipboard's text contents to stdout."""
    return pick(
        windows=["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard"],
        macos=["pbpaste"],
        linux=_linux_clip(
            wayland=["wl-paste", "--no-newline"],
            xclip=["xclip", "-selection", "clipboard", "-o"],
            xsel=["xsel", "--clipboard", "--output"],
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
        linux=_linux_clip(
            wayland=["wl-copy"],
            xclip=["xclip", "-selection", "clipboard"],
            xsel=["xsel", "--clipboard", "--input"],
        ),
        feature="clipboard write",
    )


# ── "open" routing (files, URLs, apps) ────────────────────────────────────
def open_path_argv(path: str) -> list[str]:
    """argv that opens a file/URL with the OS default handler."""
    return pick(
        windows=["cmd.exe", "/c", "start", "", path],
        macos=["open", path],
        linux=first_available(["xdg-open", path], ["gio", "open", path],
                              ["kde-open", path], ["gnome-open", path]),
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


def resolve_adb() -> str:
    """Single shared adb-binary resolution (audit F-5).

    Precedence: SASSYMCP_ADB env override (when it points at an existing
    file) -> "adb" on PATH -> adb_candidates() best-first -> bare "adb"
    (which surfaces a clean "not found" error downstream).

    adb.py, phone_screen.py, _phone_status.py, and eventlog.py all resolve
    through this one function so the env override behaves identically for
    the cockpit snapshot and the real tool paths.
    """
    env = os.environ.get("SASSYMCP_ADB")
    if env and os.path.isfile(env):
        return env
    path = which("adb")
    if path:
        return path
    for c in adb_candidates():
        if os.path.isfile(c):
            return c
    return "adb"


_adb_semaphore = None


def adb_semaphore():
    """Shared in-flight cap for ADB subprocesses (audit F-6).

    Created lazily on first call — never at import — so no event loop is
    bound until the semaphore is actually used inside a running loop. On
    Python 3.10+ asyncio primitives bind to the first running loop that
    acquires them; the MCP server runs a single loop for its lifetime, so
    sharing one semaphore across adb.py and phone_screen.py is safe there.
    Cap is 4 in flight: enough for real use, refuses a flood that would
    wedge the device's adb service.
    """
    global _adb_semaphore
    if _adb_semaphore is None:
        import asyncio

        _adb_semaphore = asyncio.Semaphore(4)
    return _adb_semaphore


# ── Package manager routing (distro-aware) ────────────────────────────────
# Order matters: on hosts with several managers (e.g. Homebrew on Linux, or
# apt on a Fedora box with alien), the native system manager wins.
_PKG_MANAGERS: tuple[tuple[str, list[str]], ...] = (
    ("apt-get", ["apt-get", "install", "-y"]),          # Debian/Ubuntu/Kali/Mint/Pop
    ("dnf", ["dnf", "install", "-y"]),                  # Fedora/RHEL 8+/Rocky/Alma
    ("yum", ["yum", "install", "-y"]),                  # RHEL/CentOS 7
    ("pacman", ["pacman", "-S", "--noconfirm", "--needed"]),  # Arch/Manjaro/Endeavour
    ("zypper", ["zypper", "--non-interactive", "install"]),   # openSUSE/SLES
    ("apk", ["apk", "add"]),                            # Alpine
    ("xbps-install", ["xbps-install", "-y"]),           # Void
    ("emerge", ["emerge", "--noreplace"]),              # Gentoo
    ("pkg", ["pkg", "install", "-y"]),                  # FreeBSD
    ("pkg_add", ["pkg_add"]),                           # OpenBSD
    ("pkgin", ["pkgin", "-y", "install"]),              # NetBSD
    ("brew", ["brew", "install"]),                      # Homebrew (macOS/Linux)
)

# Logical tool -> package name per manager. Missing key = same as the tool
# name. None = not packaged there (install hint points elsewhere).
_PKG_NAMES: dict[str, dict[str, str | None]] = {
    "tesseract": {"apt-get": "tesseract-ocr", "zypper": "tesseract-ocr",
                  "apk": "tesseract-ocr", "winget": "UB-Mannheim.TesseractOCR"},
    "adb": {"apt-get": "adb", "dnf": "android-tools", "yum": "android-tools",
            "pacman": "android-tools", "zypper": "android-tools", "apk": "android-tools",
            "xbps-install": "android-tools", "emerge": "dev-util/android-tools",
            "pkg": "android-tools", "brew": "android-platform-tools",
            "winget": "Google.PlatformTools"},
    "scrcpy": {"winget": "Genymobile.scrcpy", "emerge": "media-video/scrcpy"},
    "idevice": {"apt-get": "libimobiledevice-utils", "dnf": "libimobiledevice-utils",
                "yum": "libimobiledevice-utils", "pacman": "libimobiledevice",
                "zypper": "imobiledevice-tools", "apk": "libimobiledevice-progs",
                "xbps-install": "libimobiledevice", "emerge": "app-pda/libimobiledevice",
                "pkg": "libimobiledevice", "brew": "libimobiledevice", "winget": None},
    "nmap": {"winget": "Insecure.Nmap", "emerge": "net-analyzer/nmap"},
    "ssh": {"apt-get": "openssh-client", "dnf": "openssh-clients", "yum": "openssh-clients",
            "pacman": "openssh", "zypper": "openssh-clients", "apk": "openssh-client",
            "xbps-install": "openssh", "emerge": "net-misc/openssh", "pkg": None,
            "brew": None, "winget": "PuTTY.PuTTY"},
    "cloudflared": {"apt-get": None, "dnf": None, "yum": None, "zypper": None,
                    "winget": "Cloudflare.cloudflared"},
    "wmctrl": {"winget": None, "brew": None},
    "xdotool": {"winget": None, "brew": None},
    "xclip": {"winget": None, "brew": None},
    "wl-clipboard": {"winget": None, "brew": None},
    "notify-send": {"apt-get": "libnotify-bin", "dnf": "libnotify", "yum": "libnotify",
                    "pacman": "libnotify", "zypper": "libnotify-tools", "apk": "libnotify",
                    "pkg": "libnotify", "winget": None, "brew": None},
    "bluetoothctl": {"apt-get": "bluez", "dnf": "bluez", "yum": "bluez", "pacman": "bluez-utils",
                     "zypper": "bluez", "apk": "bluez", "winget": None, "brew": "blueutil"},
    "traceroute": {"winget": None, "brew": None},
    "dig": {"apt-get": "dnsutils", "dnf": "bind-utils", "yum": "bind-utils",
            "pacman": "bind", "zypper": "bind-utils", "apk": "bind-tools",
            "pkg": "bind-tools", "winget": None, "brew": "bind"},
}


def is_root() -> bool:
    """True when running with euid 0 (POSIX). Always False on Windows."""
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)


def pkg_manager() -> str | None:
    """Name of the host's native package manager, or None."""
    if IS_WINDOWS:
        return "winget" if which("winget") else None
    if IS_MACOS:
        return "brew" if which("brew") else None
    for name, _ in _PKG_MANAGERS:
        if which(name):
            return name
    return None


def pkg_name(tool: str, manager: str | None = None) -> str | None:
    """Package that provides `tool` under `manager` (default: host's)."""
    mgr = manager or pkg_manager()
    names = _PKG_NAMES.get(tool, {})
    if mgr in names:
        return names[mgr]
    return None if mgr == "winget" else tool


def install_argv(pkg: str, manager: str | None = None) -> list[str] | None:
    """argv that installs `pkg` non-interactively, or None if no manager.

    System managers get `sudo -n` when not already root: MCP servers have no
    TTY, so an interactive sudo prompt would just hang until timeout; `-n`
    fails fast with "a password is required" instead. Homebrew must never run
    under sudo.
    """
    mgr = manager or pkg_manager()
    if not mgr:
        return None
    if mgr == "winget":
        return ["winget", "install", "--id", pkg, "--silent",
                "--accept-source-agreements", "--accept-package-agreements"]
    base = dict(_PKG_MANAGERS).get(mgr)
    if base is None:
        return None
    argv = [*base, pkg]
    if mgr != "brew" and not is_root():
        if which("sudo"):
            argv = ["sudo", "-n", *argv]
        elif which("doas"):
            argv = ["doas", "-n", *argv]
    return argv


def install_hint(tool: str) -> str:
    """Human-readable install command for `tool` on this host."""
    mgr = pkg_manager()
    pkg = pkg_name(tool, mgr)
    if not mgr:
        return f"install {tool} with your system package manager"
    if pkg is None:
        return f"{tool} is not in the {mgr} repositories; see the project's install docs"
    argv = install_argv(pkg, mgr) or []
    if argv[:2] == ["sudo", "-n"] or argv[:2] == ["doas", "-n"]:
        argv = [argv[0], *argv[2:]]
    return " ".join(argv)
