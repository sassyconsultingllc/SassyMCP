"""ADB - Android Debug Bridge integration.

Security:
- Device identifiers validated against strict pattern
- Package names validated against Android naming rules
- Shell commands pass through blockedCommands check
- screencap/rm use separate args (no f-string shell injection)
"""

import asyncio
import ipaddress
import shutil
import os

from sassymcp.modules._security import validate_adb_device, validate_adb_package, validate_command


def _adb_path() -> str:
    path = shutil.which("adb")
    if path: return path
    for c in [os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
              r"C:\Android\platform-tools\adb.exe"]:
        if os.path.isfile(c): return c
    return "adb"


async def _run_adb(*args, timeout=30):
    cmd = [_adb_path()] + list(args)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0 and err: return f"Error (exit {proc.returncode}): {err}"
        return out if out else err
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return f"Timed out after {timeout}s"
    except FileNotFoundError: return "Error: adb not found"


def _device_args(device: str) -> list[str]:
    """Build -s device args with validation."""
    if not device:
        return []
    ok, err = validate_adb_device(device)
    if not ok:
        raise ValueError(err)
    return ["-s", device]


def register(server):
    @server.tool()
    async def sassy_adb_devices() -> str:
        """List connected Android devices."""
        return await _run_adb("devices", "-l")

    @server.tool()
    async def sassy_adb_shell(command: str, device: str = "") -> str:
        """Run shell command on Android device."""
        ok, err = validate_command(command)
        if not ok:
            return f"Error: {err}"
        try:
            args = _device_args(device)
        except ValueError as e:
            return f"Error: {e}"
        return await _run_adb(*(args + ["shell", command]))

    @server.tool()
    async def sassy_adb_packages(filter_str: str = "") -> str:
        """List installed packages."""
        if filter_str:
            ok, err = validate_adb_package(filter_str)
            if not ok:
                return f"Error: {err}"
            cmd = f"pm list packages | grep -i {filter_str}"
        else:
            cmd = "pm list packages"
        return await _run_adb("shell", cmd)

    @server.tool()
    async def sassy_adb_pull(remote_path: str, local_path: str, device: str = "") -> str:
        """Pull file from Android to local."""
        try:
            args = _device_args(device)
        except ValueError as e:
            return f"Error: {e}"
        return await _run_adb(*(args + ["pull", remote_path, local_path]))

    @server.tool()
    async def sassy_adb_push(local_path: str, remote_path: str, device: str = "") -> str:
        """Push file from local to Android."""
        try:
            args = _device_args(device)
        except ValueError as e:
            return f"Error: {e}"
        return await _run_adb(*(args + ["push", local_path, remote_path]))

    @server.tool()
    async def sassy_adb_install(apk_path: str, device: str = "") -> str:
        """Install APK on Android device."""
        try:
            args = _device_args(device)
        except ValueError as e:
            return f"Error: {e}"
        return await _run_adb(*(args + ["install", "-r", apk_path]), timeout=120)

    @server.tool()
    async def sassy_adb_logcat(filter_str: str = "", lines: int = 100, device: str = "") -> str:
        """Get Android logcat output."""
        try:
            args = _device_args(device)
        except ValueError as e:
            return f"Error: {e}"
        args += ["logcat", "-d", "-t", str(min(max(lines, 1), 10000))]
        if filter_str:
            ok, err = validate_adb_package(filter_str)  # same safe charset
            if not ok:
                return f"Error: invalid filter: {filter_str}"
            args.append(filter_str)
        return await _run_adb(*args)

    @server.tool()
    async def sassy_adb_screencap(local_path: str = "", device: str = "") -> str:
        """Capture Android screen to local PNG."""
        from pathlib import Path
        if not local_path: local_path = str(Path.home() / "android_screen.png")
        try:
            d = _device_args(device)
        except ValueError as e:
            return f"Error: {e}"
        remote = "/sdcard/sassymcp_screen.png"
        # Use separate args — no f-string shell injection
        await _run_adb(*(d + ["shell", "screencap", "-p", remote]))
        result = await _run_adb(*(d + ["pull", remote, local_path]))
        await _run_adb(*(d + ["shell", "rm", remote]))
        return f"Screen captured to {local_path}" if "error" not in result.lower() else result

    @server.tool()
    async def sassy_adb_app_info(package: str, device: str = "") -> str:
        """Get detailed info about an installed Android app."""
        ok, err = validate_adb_package(package)
        if not ok:
            return f"Error: {err}"
        try:
            d = _device_args(device)
        except ValueError as e:
            return f"Error: {e}"
        raw = await _run_adb(*(d + ["shell", "dumpsys", "package", package]))
        # Truncate to prevent MCP response overflow (dumpsys can be 100K+)
        if len(raw) > 15000:
            # Extract key sections
            import json as _json
            sections = {}
            current_section = "header"
            lines = raw.splitlines()
            for line in lines[:500]:
                stripped = line.strip()
                if stripped.endswith(":") and not stripped.startswith("-"):
                    current_section = stripped.rstrip(":")
                    sections.setdefault(current_section, [])
                else:
                    sections.setdefault(current_section, []).append(line)
            # Keep most useful sections
            keep = ["header", "Packages", "requested permissions", "install permissions",
                    "declared permissions", "User 0", "Queries", "version"]
            summary = {}
            for key in sections:
                for k in keep:
                    if k.lower() in key.lower():
                        summary[key] = "\n".join(sections[key][:50])
                        break
            if summary:
                return _json.dumps({"package": package, "sections": summary,
                                    "note": f"Truncated from {len(raw)} chars. Use adb_shell 'dumpsys package {package}' for full output."}, indent=2)
            return raw[:15000] + f"\n\n... truncated ({len(raw)} total chars)"
        return raw

    @server.tool()
    async def sassy_adb_wifi_connect(ip: str, port: int = 5555) -> str:
        """Connect to Android device over WiFi."""
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            return f"Error: invalid IP address: {ip}"
        port = min(max(port, 1), 65535)
        return await _run_adb("connect", f"{ip}:{port}")
