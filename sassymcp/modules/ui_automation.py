"""UIAutomation - Windows UI control with lean output.

IMPORTANT: All text input operations use ctrl-a + backspace to clear
the field before typing, ensuring clean output every time.

Multi-monitor and DPI-aware: sassy_screen_info reports all monitors
with resolution, position, scaling, and primary status.
"""

import json

from sassymcp.modules._security import validate_path as _validate_path, is_protected_path as _is_protected_path


def _get_monitors():
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
        Coordinates are absolute across all monitors."""
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
