"""Clipboard - Cross-device clipboard (Windows <-> Android).

Security:
- ADB device identifiers validated
- Android clipboard uses base64 encoding only (no shell escaping fallback)
- Windows clipboard uses stdin piping (no string interpolation)
"""

import asyncio
import base64
import re

from sassymcp.modules._security import validate_adb_device


async def _safe_wait(proc, timeout=10):
    """Wait for process with timeout; kill on timeout."""
    try:
        return await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        raise


async def _safe_wait_stdin(proc, data, timeout=10):
    """Wait for process with stdin input and timeout; kill on timeout."""
    try:
        return await asyncio.wait_for(
            proc.communicate(input=data), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        raise


def _validated_device_args(device: str) -> list[str] | str:
    """Return device args or error string."""
    if not device:
        return []
    ok, err = validate_adb_device(device)
    if not ok:
        return err
    return ["-s", device]


def register(server):
    @server.tool()
    async def sassy_clipboard_get() -> str:
        """Get Windows clipboard text."""
        proc = await asyncio.create_subprocess_exec(
            "powershell.exe", "-NoProfile", "-Command", "Get-Clipboard",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, _ = await _safe_wait(proc)
            return stdout.decode("utf-8", errors="replace").strip()
        except asyncio.TimeoutError:
            return "Timed out after 10s"

    @server.tool()
    async def sassy_clipboard_set(text: str) -> str:
        """Set Windows clipboard text."""
        ps_script = "$input | Set-Clipboard"
        proc = await asyncio.create_subprocess_exec(
            "powershell.exe", "-NoProfile", "-Command", ps_script,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            await _safe_wait_stdin(proc, text.encode("utf-8"))
            return f"Clipboard set ({len(text)} chars)"
        except asyncio.TimeoutError:
            return "Timed out after 10s"

    @server.tool()
    async def sassy_clipboard_to_android(device: str = "") -> str:
        """Send Windows clipboard to Android via base64 encoding."""
        dev = _validated_device_args(device)
        if isinstance(dev, str):
            return f"Error: {dev}"

        proc = await asyncio.create_subprocess_exec(
            "powershell.exe", "-NoProfile", "-Command", "Get-Clipboard",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, _ = await _safe_wait(proc)
        except asyncio.TimeoutError:
            return "Timed out reading clipboard"
        text = stdout.decode("utf-8", errors="replace").strip()
        if not text:
            return "Windows clipboard is empty"

        # Base64 encode — safe for shell interpolation (alphanumeric + /+=)
        b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
        if not re.match(r'^[A-Za-z0-9+/=]+$', b64):
            return "Error: unexpected base64 output"

        args = ["adb"] + dev
        args.extend(["shell", f"echo {b64} | base64 -d | am broadcast -a clipper.set -e text -"])
        proc2 = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            await _safe_wait(proc2)
        except asyncio.TimeoutError:
            return "Timed out sending to Android"
        return f"Sent to Android: {text[:50]}..."

    @server.tool()
    async def sassy_clipboard_from_android(device: str = "") -> str:
        """Get Android clipboard to Windows."""
        dev = _validated_device_args(device)
        if isinstance(dev, str):
            return f"Error: {dev}"

        args = ["adb"] + dev + ["shell", "am broadcast -a clipper.get"]
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, _ = await _safe_wait(proc)
            return stdout.decode("utf-8", errors="replace").strip()[:200]
        except asyncio.TimeoutError:
            return "Timed out after 10s"
