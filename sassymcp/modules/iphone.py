# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-NVCVTM2H6LOR
# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
"""iPhone (iOS) integration via libimobiledevice -- EXPERIMENTAL.

Tools talk to a USB-paired iPhone using the libimobiledevice CLI suite
(idevice_id, ideviceinfo, idevicescreenshot, idevicesyslog, ideviceinstaller,
idevicepair). No jailbreak required, but on iOS 17+ the device must be
paired first: run `idevicepair pair` on this host, unlock the iPhone, and
tap Trust. Unpaired/untrusted devices report "No device found".

Security:
- UDIDs validated against a strict hex/dash pattern, never shell-interpolated
- All subprocesses use argv lists (no shell=True); timeouts on every call
- ideviceinstaller -i (install) is destructive: requires confirm='YES'
"""

import asyncio
import os
import re
import shutil

from sassymcp import _platform

# iOS device UDIDs are 40-char hex (older: 24-char); allow a small margin
# rather than rejecting odd-but-real identifiers.
_UDID_RE = re.compile(r"^[0-9A-Za-z-]{8,64}$")

_PAIRING_HINT = (
    "iOS 17+ pairing: run `idevicepair pair` on this host, unlock the iPhone "
    "and tap Trust when prompted, then retry."
)


def _install_hint() -> str:
    """Per-host-OS hint for installing the libimobiledevice suite."""
    return _platform.pick(
        macos="Install it with: brew install libimobiledevice",
        linux="Install it with: sudo apt install libimobiledevice-utils",
        windows="libimobiledevice is primarily a macOS/Linux tool and has limited "
                "Windows support -- use WSL2 or a macOS/Linux host with the "
                "iPhone plugged in there.",
        default="Install the libimobiledevice suite for this platform.",
    )


_EXTRA_PATH_DIRS = _platform.pick(
    macos=["/opt/homebrew/bin", "/usr/local/bin"],
    linux=["/usr/bin", "/usr/local/bin"],
    default=[],
)


def _find_binary(name: str) -> str | None:
    path = shutil.which(name)
    if path:
        return path
    for d in _EXTRA_PATH_DIRS:
        c = os.path.join(d, name)
        if os.path.isfile(c):
            return c
    return None


def _need(name: str) -> str | None:
    """Return None when the binary exists, else an 'Error: ...' install hint."""
    if _find_binary(name):
        return None
    return (
        f"Error: '{name}' not found -- the libimobiledevice suite is required "
        f"for iPhone tools. {_install_hint()}"
    )


async def _run(binary: str, *args, timeout: int = 30) -> str:
    """Run a libimobiledevice binary, returning trimmed stdout or an Error string."""
    path = _find_binary(binary) or binary
    try:
        proc = await asyncio.create_subprocess_exec(
            path, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            detail = err or out or "no output"
            return f"Error (exit {proc.returncode}): {detail}"
        return out if out else err
    except TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return f"Timed out after {timeout}s"
    except FileNotFoundError:
        return _need(binary) or f"Error: {binary} not found"


def _with_pairing_hint(result: str) -> str:
    """Append the iOS 17+ pairing hint when a command found no device."""
    if result.startswith("Error") and "no device" in result.lower():
        return f"{result} {_PAIRING_HINT}"
    return result


async def _udid_args(udid: str) -> "tuple[list[str], str | None]":
    """Resolve the -u selector: explicit udid, or the single connected device.

    Returns (argv_prefix, error). error is an 'Error: ...' string when the
    call cannot proceed (binary missing, invalid UDID, no devices, or
    several devices with no udid given).
    """
    if udid:
        if not _UDID_RE.match(udid):
            return [], (
                "Error: invalid UDID -- expected 8-64 letters, digits or dashes. "
                "List connected devices with sassy_iphone_list."
            )
        return ["-u", udid], None
    err = _need("idevice_id")
    if err:
        return [], err
    raw = await _run("idevice_id", "-l")
    if raw.startswith("Error") or raw.startswith("Timed out"):
        return [], _with_pairing_hint(raw)
    udids = [u for u in raw.split() if u]
    if not udids:
        return [], (
            "Error: no iPhone found. Check the USB cable and unlock the phone. "
            + _PAIRING_HINT
        )
    if len(udids) > 1:
        return [], (
            "Error: multiple iPhones connected ("
            + ", ".join(udids)
            + "); pass udid='<UDID>' to select one."
        )
    return ["-u", udids[0]], None


def register(server):
    @server.tool()
    async def sassy_iphone_list() -> str:
        """List connected iPhones (their UDIDs) via 'idevice_id -l'.

        Prerequisite: the libimobiledevice suite must be installed on this host
        (macOS: brew install libimobiledevice; Linux: sudo apt install
        libimobiledevice-utils). Returns one UDID per line, or an error with
        the iOS 17+ pairing requirement (`idevicepair pair` + tap Trust on
        the device) when none is visible. Takes no parameters; call this
        first before other iphone tools to find the udid parameter value.
        """
        err = _need("idevice_id")
        if err:
            return err
        raw = await _run("idevice_id", "-l")
        if raw.startswith("Error") or raw.startswith("Timed out"):
            return _with_pairing_hint(raw)
        udids = [u for u in raw.split() if u]
        if not udids:
            return (
                "Error: no iPhone found. Check the USB cable and unlock the phone. "
                + _PAIRING_HINT
            )
        return "\n".join(udids)

    @server.tool()
    async def sassy_iphone_info(udid: str = "") -> str:
        """Device info (name, model, iOS version, serial, etc.) via 'ideviceinfo'.

        Prerequisite: libimobiledevice on this host (macOS: brew install
        libimobiledevice; Linux: sudo apt install libimobiledevice-utils).
        udid selects the device; omit it when exactly one iPhone is connected
        (with several connected and no udid, the call errors and lists them).
        iOS 17+: the phone must be paired (`idevicepair pair`, tap Trust) or
        the device reports not found. Use sassy_iphone_list to discover UDIDs.
        """
        err = _need("ideviceinfo")
        if err:
            return err
        sel, derr = await _udid_args(udid)
        if derr:
            return derr
        return _with_pairing_hint(await _run("ideviceinfo", *sel, timeout=30))

    @server.tool()
    async def sassy_iphone_screenshot(local_path: str = "", udid: str = "") -> str:
        """Capture the iPhone screen to a local PNG via 'idevicescreenshot'.

        Prerequisite: libimobiledevice on this host (macOS: brew install
        libimobiledevice; Linux: sudo apt install libimobiledevice-utils).
        local_path is the save destination on this host (default
        ~/iphone_screen.png); udid selects the device when several are
        connected (sassy_iphone_list shows them). iOS 17+: the phone must be
        paired (`idevicepair pair`, tap Trust). Returns the saved path or an
        error string.
        """
        err = _need("idevicescreenshot")
        if err:
            return err
        sel, derr = await _udid_args(udid)
        if derr:
            return derr
        if not local_path:
            from pathlib import Path
            local_path = str(Path.home() / "iphone_screen.png")
        result = await _run("idevicescreenshot", *(sel + [local_path]), timeout=30)
        if result.startswith("Error") or result.startswith("Timed out"):
            return _with_pairing_hint(result)
        return f"Screen captured to {local_path}"

    @server.tool()
    async def sassy_iphone_syslog(udid: str = "", lines: int = 100, timeout: int = 10) -> str:
        """Recent iOS system log lines via 'idevicesyslog' (bounded capture).

        Prerequisite: libimobiledevice on this host (macOS: brew install
        libimobiledevice; Linux: sudo apt install libimobiledevice-utils).
        udid selects the device (omit with exactly one connected). idevicesyslog
        is a live stream, so this tool captures for `timeout` seconds only
        (1-60, default 10) then stops -- it can never run unbounded. lines
        (1-5000, default 100) caps how many of the captured tail lines are
        returned. iOS 17+: the phone must be paired (`idevicepair pair`, tap
        Trust). Use for crash diagnosis on the device.
        """
        n = min(max(int(lines), 1), 5000)
        t = min(max(int(timeout), 1), 60)
        err = _need("idevicesyslog")
        if err:
            return err
        sel, derr = await _udid_args(udid)
        if derr:
            return derr
        path = _find_binary("idevicesyslog") or "idevicesyslog"
        try:
            proc = await asyncio.create_subprocess_exec(
                path, *sel,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                await asyncio.wait_for(proc.wait(), timeout=t)
            except TimeoutError:
                pass  # expected: syslog streams forever; the timeout IS the bound
            try:
                out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=2)
            except TimeoutError:
                out_b, err_b = b"", b""
            try:
                proc.kill()
            except Exception:
                pass
            out = out_b.decode("utf-8", errors="replace").strip()
            err_txt = err_b.decode("utf-8", errors="replace").strip()
        except FileNotFoundError:
            return _need("idevicesyslog") or "Error: idevicesyslog not found"
        except Exception as e:
            return f"Error: syslog capture failed: {e}"
        if not out:
            if "no device" in err_txt.lower():
                return _with_pairing_hint(f"Error: {err_txt}")
            return (
                "Error: no syslog output captured in "
                f"{t}s -- is the device paired/trusted? " + _PAIRING_HINT
            )
        captured = out.splitlines()
        shown = captured[-n:]
        return (
            f"[captured {t}s of syslog, showing last {len(shown)} "
            f"of {len(captured)} captured lines]\n" + "\n".join(shown)
        )

    @server.tool()
    async def sassy_iphone_apps(udid: str = "") -> str:
        """List installed apps (bundle IDs, names, versions) via 'ideviceinstaller -l'.

        Prerequisite: libimobiledevice on this host (macOS: brew install
        libimobiledevice; Linux: sudo apt install libimobiledevice-utils).
        udid selects the device; omit it when exactly one iPhone is connected
        (with several connected and no udid, the call errors and lists them).
        iOS 17+: the phone must be paired (`idevicepair pair`, tap Trust).
        """
        err = _need("ideviceinstaller")
        if err:
            return err
        sel, derr = await _udid_args(udid)
        if derr:
            return derr
        return _with_pairing_hint(await _run("ideviceinstaller", *(sel + ["-l"]), timeout=30))

    @server.tool()
    async def sassy_iphone_install(ipa_path: str, udid: str = "", confirm: str = "") -> str:
        """Install an .ipa onto the iPhone via 'ideviceinstaller -i'.

        DESTRUCTIVE: installs onto the device (device-state mutation), so it
        requires confirm='YES' -- anything else is refused. ipa_path is a file
        on this host, required, and must end in .ipa. Prerequisite:
        libimobiledevice on this host (macOS: brew install libimobiledevice;
        Linux: sudo apt install libimobiledevice-utils). udid selects the
        device (omit with exactly one connected). iOS 17+: the phone must be
        paired (`idevicepair pair`, tap Trust). Do not install untrusted IPAs.
        """
        if confirm != "YES":
            return (
                "Refused: sassy_iphone_install requires confirm='YES' -- "
                "installing mutates the device. Pass confirm='YES' to proceed."
            )
        err = _need("ideviceinstaller")
        if err:
            return err
        if not ipa_path:
            return "Error: ipa_path is required."
        if not ipa_path.lower().endswith(".ipa"):
            return "Error: ipa_path must be an .ipa file."
        if not os.path.isfile(ipa_path):
            return f"Error: ipa_path not found: {ipa_path}"
        sel, derr = await _udid_args(udid)
        if derr:
            return derr
        return _with_pairing_hint(
            await _run("ideviceinstaller", *(sel + ["-i", ipa_path]), timeout=120)
        )
