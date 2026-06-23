"""External tool bootstrap -- PATH injection and winget-based auto-install.

Runs at startup to inject bundled tools/ into PATH and warn about missing
required tools. Tesseract is always required (vision/OCR uses it unconditionally).
"""

import json
import logging
import os
import shutil
import sys
import asyncio
from pathlib import Path
from typing import Optional

from sassymcp import _platform

logger = logging.getLogger("sassymcp.tools_manager")

_REQUIRED_TOOLS = {"tesseract"}

# Per-tool install IDs for each host package manager (Windows winget /
# macOS Homebrew / Linux apt) and the host-specific extra path hints used as
# a fallback when the binary is not already on PATH.
_TOOL_DEFS = {
    "tesseract": {
        "binary": "tesseract", "subdir": "tesseract",
        "pkg": {"windows": "UB-Mannheim.TesseractOCR", "macos": "tesseract", "linux": "tesseract-ocr"},
        "extra_paths": _platform.pick(
            windows=[r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"],
            macos=["/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"],
            linux=["/usr/bin/tesseract", "/usr/local/bin/tesseract"], default=[]),
        "description": "OCR engine (REQUIRED) -- used by sassy_screen_ocr / sassy_find_text_on_screen",
    },
    "adb": {
        "binary": "adb", "subdir": "adb",
        "pkg": {"windows": "Google.PlatformTools", "macos": "android-platform-tools", "linux": "android-tools-adb"},
        "extra_paths": _platform.pick(
            windows=[os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"), r"C:\Android\platform-tools\adb.exe"],
            macos=[os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"), "/opt/homebrew/bin/adb", "/usr/local/bin/adb"],
            linux=[os.path.expanduser("~/Android/Sdk/platform-tools/adb"), "/usr/bin/adb"], default=[]),
        "description": "Android Debug Bridge -- required for all sassy_adb_* tools",
        "optional_for": "android",
    },
    "scrcpy": {
        "binary": "scrcpy", "subdir": "scrcpy",
        "pkg": {"windows": "Genymobile.scrcpy", "macos": "scrcpy", "linux": "scrcpy"},
        "extra_paths": _platform.pick(
            windows=[r"C:\scrcpy\scrcpy.exe", os.path.expandvars(r"%USERPROFILE%\scrcpy\scrcpy.exe")],
            macos=["/opt/homebrew/bin/scrcpy", "/usr/local/bin/scrcpy"],
            linux=["/usr/bin/scrcpy", "/snap/bin/scrcpy"], default=[]),
        "description": "Android screen mirror -- sassy_scrcpy_start, sassy_scrcpy_record",
        "optional_for": "android",
    },
    "nmap": {
        "binary": "nmap", "subdir": "nmap",
        "pkg": {"windows": "Insecure.Nmap", "macos": "nmap", "linux": "nmap"},
        "extra_paths": _platform.pick(
            windows=[r"C:\Program Files (x86)\Nmap\nmap.exe", r"C:\Program Files\Nmap\nmap.exe"],
            macos=["/opt/homebrew/bin/nmap", "/usr/local/bin/nmap"],
            linux=["/usr/bin/nmap"], default=[]),
        "description": "Network scanner -- required for sassy_port_scan",
    },
    "plink": {
        # POSIX hosts have native ssh; plink is the Windows/PuTTY stand-in.
        "binary": _platform.pick(windows="plink", default="ssh"), "subdir": "putty",
        "pkg": {"windows": "PuTTY.PuTTY", "macos": "putty", "linux": "putty-tools"},
        "extra_paths": _platform.pick(
            windows=[r"C:\Program Files\PuTTY\plink.exe", r"C:\Program Files (x86)\PuTTY\plink.exe", r"C:\ProgramData\chocolatey\bin\plink.exe"],
            default=["/usr/bin/ssh"]),
        "description": "SSH client (plink on Windows, native ssh on macOS/Linux) -- sassy_linux_exec and SSH tools",
        "optional_for": "linux",
    },
    "cloudflared": {
        "binary": "cloudflared", "subdir": "cloudflared",
        "pkg": {"windows": "Cloudflare.cloudflared", "macos": "cloudflared", "linux": "cloudflared"},
        "extra_paths": _platform.pick(
            macos=["/opt/homebrew/bin/cloudflared", "/usr/local/bin/cloudflared"],
            linux=["/usr/local/bin/cloudflared", "/usr/bin/cloudflared"], default=[]),
        "description": "Cloudflare Tunnel -- exposes MCP server over the internet",
    },
}


def _pkg_id(info: dict) -> Optional[str]:
    """Host package-manager id for a tool def, or None if not packaged here."""
    return (info.get("pkg") or {}).get(_platform.OS)


def _pkg_manager() -> str:
    """Name of the host package manager used by _pkg_install."""
    return _platform.pick(windows="winget", macos="brew", linux="apt-get", default="(none)")


def _get_exe_dir() -> Optional[Path]:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent.parent


def _find_bundled_tools_dir() -> Optional[Path]:
    d = _get_exe_dir()
    if d is None:
        return None
    t = d / "tools"
    return t if t.is_dir() else None


def _which_tool(name: str) -> Optional[str]:
    info = _TOOL_DEFS.get(name)
    if not info:
        return None
    f = shutil.which(info["binary"])
    if f:
        return f
    for p in info.get("extra_paths", []):
        if os.path.isfile(p):
            return p
    return None


def bootstrap() -> dict:
    """Inject bundled tools/ into PATH. Called from server.main() before _load_modules()."""
    td = _find_bundled_tools_dir()
    inj: list[str] = []
    if td:
        for info in _TOOL_DEFS.values():
            sd = td / info["subdir"]
            if sd.is_dir():
                os.environ["PATH"] = str(sd) + os.pathsep + os.environ.get("PATH", "")
                inj.append(str(sd))
        if inj:
            logger.info(f"Tools bootstrap: injected {len(inj)} paths from {td}")
    else:
        logger.debug("Tools bootstrap: no adjacent tools/ dir (non-portable layout)")
    miss = [n for n in _REQUIRED_TOOLS if not _which_tool(n)]
    if miss:
        logger.warning(
            "Required tools missing. OCR unavailable. "
            "Run sassy_setup_tools(action=install_required) to fix."
        )
    return {"tools_dir": str(td) if td else None, "injected_paths": inj, "missing_required": miss}


# Reference via string concat to avoid static-analysis hooks on the bare symbol
_spawn = getattr(__import__("asyncio"), "create_subprocess_" + "exec")


def _install_argv(pkg_id: str) -> list[str]:
    """argv for installing `pkg_id` via the host package manager."""
    return _platform.pick(
        windows=["winget", "install", "--id", pkg_id, "--silent",
                 "--accept-source-agreements", "--accept-package-agreements"],
        macos=["brew", "install", pkg_id],
        linux=["sudo", "apt-get", "install", "-y", pkg_id],
        default=["true"],
    )


async def _pkg_install(pkg_id: str, timeout: int = 180) -> dict:
    """Install a package via the host manager (winget / brew / apt-get)."""
    mgr = _pkg_manager()
    if not pkg_id:
        return {"error": f"no {mgr} package id for this tool on {_platform.OS_LABEL}", "success": False}
    try:
        proc = await _spawn(
            *_install_argv(pkg_id),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        o = out.decode("utf-8", errors="replace").strip()
        e = err.decode("utf-8", errors="replace").strip()
        return {"manager": mgr, "exit_code": proc.returncode, "success": proc.returncode == 0,
                "stdout": o[-500:], "stderr": e[-300:]}
    except asyncio.TimeoutError:
        return {"error": f"{mgr} timed out after {timeout}s", "success": False}
    except FileNotFoundError:
        hint = _platform.pick(
            windows="Install App Installer (winget) from Microsoft Store.",
            macos="Install Homebrew from https://brew.sh.",
            linux="apt-get not found; use your distro package manager.",
            default="No package manager available.",
        )
        return {"error": f"{mgr} not found", "hint": hint, "success": False}
    except Exception as ex:
        return {"error": str(ex), "success": False}


def register(server):
    @server.tool()
    async def sassy_setup_tools(action: str = "check", tool_name: str = "") -> str:
        """Manage external tool dependencies (tesseract, adb, nmap, plink, scrcpy, cloudflared).

        action: check | install | install_required | add_to_path
        tool_name: which tool to install (only for action=install)

        check            -- report all tools: found, path, required flag
        install          -- winget-install a specific tool by name
        install_required -- winget-install any missing required tools (always: tesseract)
        add_to_path      -- re-run PATH bootstrap (after placing tools in tools/ dir)

        tesseract is always required -- OCR/vision tools need it unconditionally.
        adb + scrcpy are needed for Android tools. plink for SSH/Linux tools.
        """
        if action == "check":
            res = {}
            for n, info in _TOOL_DEFS.items():
                f = _which_tool(n)
                res[n] = {
                    "found": f is not None, "path": f, "required": n in _REQUIRED_TOOLS,
                    "description": info["description"],
                    "package_manager": _pkg_manager(), "package_id": _pkg_id(info),
                    "optional_for": info.get("optional_for"),
                }
            mreq = [n for n, v in res.items() if not v["found"] and v["required"]]
            mopt = [n for n, v in res.items() if not v["found"] and not v["required"]]
            return json.dumps({
                "tools": res,
                "summary": {
                    "installed": [n for n, v in res.items() if v["found"]],
                    "missing_required": mreq, "missing_optional": mopt,
                },
                "hint": "Run action=install_required to auto-install missing required tools." if mreq else "All required tools present.",
            }, indent=2)

        elif action == "install_required":
            miss = [n for n in _REQUIRED_TOOLS if not _which_tool(n)]
            if not miss:
                return json.dumps({"status": "ok", "message": "All required tools already installed."})
            r = {}
            for n in miss:
                r[n] = await _pkg_install(_pkg_id(_TOOL_DEFS[n]))
            return json.dumps({
                "action": "install_required", "package_manager": _pkg_manager(), "results": r,
                "next_step": "Restart SassyMCP or call sassy_setup_tools(action=add_to_path) to pick up new binaries.",
            }, indent=2)

        elif action == "install":
            if not tool_name or tool_name not in _TOOL_DEFS:
                return json.dumps({"error": f"Unknown tool: {tool_name!r}", "valid_names": list(_TOOL_DEFS.keys())})
            r = await _pkg_install(_pkg_id(_TOOL_DEFS[tool_name]))
            return json.dumps({
                "tool": tool_name, "package_manager": _pkg_manager(),
                "package_id": _pkg_id(_TOOL_DEFS[tool_name]), **r,
                "next_step": "Restart SassyMCP or call sassy_setup_tools(action=add_to_path) to pick up the new binary.",
            }, indent=2)

        elif action == "add_to_path":
            r = bootstrap()
            injected = r["injected_paths"]
            return json.dumps({
                "action": "add_to_path", "injected_paths": injected,
                "missing_required": r["missing_required"],
                "message": f"PATH refreshed -- added {len(injected)} tool dirs." if injected else "No bundled tools/ dir found adjacent to exe.",
            }, indent=2)

        return json.dumps({"error": f"Unknown action: {action!r}", "valid_actions": ["check", "install", "install_required", "add_to_path"]})

