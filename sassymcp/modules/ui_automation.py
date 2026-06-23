"""UIAutomation - UI control with lean output (cross-platform).

IMPORTANT: All text input operations use ctrl-a + backspace to clear
the field before typing, ensuring clean output every time. Mouse/keyboard
and screenshots go through pyautogui, which works on Windows, macOS, and
Linux (macOS requires Accessibility + Screen Recording permission for the
app running SassyMCP).

Multi-monitor and DPI-aware: sassy_screen_info reports all monitors with
resolution, position, scaling, and primary status — via win32 on Windows,
AppKit (NSScreen) on macOS, with a pyautogui single-monitor fallback.
"""

import asyncio
import json

from sassymcp import _platform
from sassymcp.modules._security import validate_path as _validate_path, is_protected_path as _is_protected_path


def _get_monitors():
    """Get all monitors with position, size, and DPI scaling, host-appropriate.
    Returns a list of dicts, or None to signal the pyautogui fallback."""
    if _platform.IS_WINDOWS:
        return _win_monitors()
    if _platform.IS_MACOS:
        return _mac_monitors()
    return None


def _mac_monitors():
    """macOS monitors via AppKit NSScreen (needs pyobjc). None if unavailable."""
    try:
        from AppKit import NSScreen
        mons = []
        for i, s in enumerate(NSScreen.screens()):
            f = s.frame()
            try:
                scale = round(float(s.backingScaleFactor()) * 100)
            except Exception:
                scale = 100
            left, top = int(f.origin.x), int(f.origin.y)
            w, h = int(f.size.width), int(f.size.height)
            mons.append({
                "left": left, "top": top, "right": left + w, "bottom": top + h,
                "width": w, "height": h, "scale_percent": scale, "primary": i == 0,
            })
        return mons or None
    except Exception:
        return None


def _win_monitors():
    """Get all monitors with position, size, DPI scaling via ctypes.
    Returns list of dicts. Falls back to pyautogui single-monitor if ctypes fails."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        shcore = ctypes.windll.shcore

        # Enable DPI awareness so we get real coordinates
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        except Exception:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass

        monitors = []

        def _callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
            info = wintypes.RECT()
            ctypes.memmove(ctypes.byref(info), lprcMonitor, ctypes.sizeof(wintypes.RECT))
            # Get DPI
            dpi_x = ctypes.c_uint()
            dpi_y = ctypes.c_uint()
            try:
                shcore.GetDpiForMonitor(hMonitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
                scale = round(dpi_x.value / 96.0 * 100)
            except Exception:
                scale = 100

            monitors.append({
                "left": info.left,
                "top": info.top,
                "right": info.right,
                "bottom": info.bottom,
                "width": info.right - info.left,
                "height": info.bottom - info.top,
                "scale_percent": scale,
                "primary": info.left == 0 and info.top == 0,
            })
            return True

        MONITORENUMPROC = ctypes.CFUNCTYPE(
            ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.POINTER(wintypes.RECT), ctypes.c_double)
        user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_callback), 0)

        return monitors if monitors else None
    except Exception:
        return None


async def _mac_desktop_state():
    """Enumerate visible app windows on macOS via System Events (osascript)."""
    script = (
        "set out to \"\"\n"
        "tell application \"System Events\"\n"
        "  repeat with proc in (every process whose background only is false)\n"
        "    repeat with w in (every window of proc)\n"
        "      try\n"
        "        set p to position of w\n        set s to size of w\n"
        "        set out to out & (name of w) & \"\\t\" & (item 1 of p) & \"\\t\" & (item 2 of p) & \"\\t\" & (item 1 of s) & \"\\t\" & (item 2 of s) & linefeed\n"
        "      end try\n"
        "    end repeat\n"
        "  end repeat\n"
        "end tell\n"
        "return out\n"
    )
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
    raw = stdout.decode("utf-8", errors="replace").strip()
    if not raw:
        err = stderr.decode("utf-8", errors="replace").strip()
        return json.dumps({"error": err or "no windows",
                           "hint": "Grant Accessibility permission to the app running SassyMCP."})
    windows = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) != 5 or not parts[0].strip():
            continue
        try:
            windows.append({"title": parts[0], "left": int(parts[1]), "top": int(parts[2]),
                            "width": int(parts[3]), "height": int(parts[4])})
        except ValueError:
            continue
    return json.dumps(windows, indent=2)


def register(server):
    @server.tool()
    async def sassy_screen_info() -> str:
        """Get display configuration: all monitors with resolution, position,
        DPI scaling, and which is primary. Essential for multi-monitor setups."""
        monitors = _get_monitors()
        if monitors:
            return json.dumps({"monitors": monitors, "count": len(monitors)}, indent=2)
        # Fallback
        import pyautogui
        w, h = pyautogui.size()
        return json.dumps({"monitors": [{"left": 0, "top": 0, "width": w, "height": h,
            "scale_percent": 100, "primary": True, "note": "single-monitor fallback"}],
            "count": 1}, indent=2)

    @server.tool()
    async def sassy_desktop_state(include_taskbar: bool = False) -> str:
        """Get desktop state: open windows and positions. Lean output.
        Coordinates are absolute across all monitors. Windows: pywinauto.
        macOS: System Events (needs Accessibility permission)."""
        if _platform.IS_MACOS:
            return await _mac_desktop_state()
        if not _platform.IS_WINDOWS:
            return json.dumps({"error": _platform.unsupported(
                "desktop window enumeration on Linux (needs wmctrl)")})
        try:
            from pywinauto import Desktop
        except ImportError:
            return "Error: pywinauto not installed"
        desktop = Desktop(backend="uia")
        windows = []
        for w in desktop.windows():
            try:
                if not w.is_visible(): continue
                title = w.window_text()
                if not title: continue
                if not include_taskbar and "taskbar" in title.lower(): continue
                rect = w.rectangle()
                windows.append({"title": title, "left": rect.left, "top": rect.top,
                                "width": rect.width(), "height": rect.height()})
            except Exception: continue
        return json.dumps(windows, indent=2)

    @server.tool()
    async def sassy_click(x: int, y: int, button: str = "left", clicks: int = 1) -> str:
        """Click at absolute screen coordinates (works across multiple monitors).
        Use sassy_screen_info to find monitor positions first."""
        import pyautogui
        pyautogui.click(x, y, clicks=clicks, button=button)
        return f"Clicked ({x}, {y}) {button} x{clicks}"

    @server.tool()
    async def sassy_type_text(text: str, target_x: int = 0, target_y: int = 0, interval: float = 0.02) -> str:
        """Type text into a field. Always clears field first with ctrl-a + backspace.
        If target_x/target_y provided, clicks the field first."""
        import pyautogui
        import time
        if target_x and target_y:
            pyautogui.click(target_x, target_y)
            time.sleep(0.1)
        # Always clear field before typing - ensures clean output
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.05)
        pyautogui.press("backspace")
        time.sleep(0.05)
        pyautogui.typewrite(text, interval=interval)
        return f"Typed {len(text)} chars (field cleared first)"

    @server.tool()
    async def sassy_hotkey(keys: str) -> str:
        """Press keyboard shortcut. Keys separated by +, e.g. ctrl+c."""
        import pyautogui
        key_list = [k.strip() for k in keys.split("+")]
        pyautogui.hotkey(*key_list)
        return f"Pressed {keys}"

    @server.tool()
    async def sassy_screenshot(path: str = "", region: str = "", monitor: int = -1) -> str:
        """Take screenshot. Optional region as x,y,w,h. monitor=-1 for all, 0 for primary, 1+ for others."""
        import pyautogui
        from pathlib import Path
        if not path:
            path = str(Path.home() / "sassymcp_screenshot.png")
        ok, err = _validate_path(path)
        if not ok:
            return f"Error: {err}"
        prot, reason = _is_protected_path(Path(path).absolute())
        if prot:
            return f"Refused: path is protected ({reason})"
        kwargs = {}
        if region:
            parts = [int(x) for x in region.split(",")]
            if len(parts) == 4: kwargs["region"] = tuple(parts)
        elif monitor >= 0:
            monitors = _get_monitors()
            if monitors and monitor < len(monitors):
                m = monitors[monitor]
                kwargs["region"] = (m["left"], m["top"], m["width"], m["height"])
        img = pyautogui.screenshot(**kwargs)
        img.save(path)
        return f"Screenshot saved to {path} ({img.size[0]}x{img.size[1]})"
