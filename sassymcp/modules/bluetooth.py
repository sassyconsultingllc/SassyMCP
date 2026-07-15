# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-OPYIGAVYMW57
"""Bluetooth - Device enumeration and diagnostics (cross-platform).

Host enumeration is routed at the head (see _platform):
  - Windows: Get-PnpDevice -Class Bluetooth
  - macOS:   blueutil (if installed) / system_profiler SPBluetoothDataType
  - Linux:   bluetoothctl
The Android path (sassy_bt_android) is identical on every host.
"""

import asyncio

from sassymcp import _platform


async def _run(argv, timeout=15) -> str:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    out = stdout.decode("utf-8", errors="replace").strip()
    return out or stderr.decode("utf-8", errors="replace").strip()


def register(server):
    @server.tool()
    async def sassy_bt_devices() -> str:
        """List paired Bluetooth devices."""
        argv = _platform.pick(
            windows=["powershell.exe", "-NoProfile", "-Command",
                     "Get-PnpDevice -Class Bluetooth | Where { $_.Status -eq 'OK' } "
                     "| Select FriendlyName,DeviceID,Status | FT -Auto"],
            macos=(["blueutil", "--paired"] if _platform.which("blueutil")
                   else ["system_profiler", "SPBluetoothDataType"]),
            linux=["bluetoothctl", "paired-devices"],
            feature="paired Bluetooth devices",
        )
        return await _run(argv)

    @server.tool()
    async def sassy_bt_scan() -> str:
        """List all Bluetooth devices."""
        argv = _platform.pick(
            windows=["powershell.exe", "-NoProfile", "-Command",
                     "Get-PnpDevice -Class Bluetooth "
                     "| Select FriendlyName,DeviceID,Status,InstanceId | FT -Auto"],
            macos=(["blueutil", "--inquiry"] if _platform.which("blueutil")
                   else ["system_profiler", "SPBluetoothDataType"]),
            linux=["bluetoothctl", "devices"],
            feature="Bluetooth scan",
        )
        return await _run(argv, timeout=20)

    @server.tool()
    async def sassy_bt_android(device: str = "") -> str:
        """List Bluetooth devices from Android."""
        args = ["adb"] + (["-s", device] if device else []) + ["shell", "dumpsys bluetooth_manager"]
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        output = stdout.decode("utf-8", errors="replace")
        relevant = []
        capture = False
        for line in output.splitlines():
            if "Bonded devices" in line or "Connected devices" in line: capture = True
            elif capture and line.strip() == "": capture = False
            if capture: relevant.append(line)
        return "\n".join(relevant[:50]) if relevant else "No Bluetooth info found"
