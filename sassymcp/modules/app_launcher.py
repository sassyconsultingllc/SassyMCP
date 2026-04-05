"""AppLauncher - Application control and window management.

Launch apps by name, focus/close/resize windows, and manage
the desktop workspace programmatically.

Dependencies: pywinauto, psutil, pyautogui.
"""

import json
import subprocess
import time


def register(server):

    @server.tool()
    async def sassy_launch_app(name: str, wait_seconds: float = 2.0) -> str:
        """Launch an application by name via Start menu search.

        name = app name as you'd type it in Start (e.g. "notepad", "chrome", "code").
        Waits wait_seconds for it to appear. Returns window info if found.
        """
        import pyautogui

        try:
            pyautogui.press("win")
            time.sleep(0.5)
            pyautogui.typewrite(name, interval=0.05)
            time.sleep(0.8)
            pyautogui.press("enter")
            time.sleep(wait_seconds)

            from pywinauto import Desktop
            desktop = Desktop(backend="uia")
            name_lower = name.lower()
            for w in desktop.windows():
                try:
                    if not w.is_visible():
                        continue
                    title = w.window_text()
                    if title and name_lower in title.lower():
                        r = w.rectangle()
                        return json.dumps({
                            "launched": name,
                            "window_title": title,
                            "pid": w.process_id(),
                            "position": [r.left, r.top, r.width(), r.height()],
                        })
                except Exception:
                    continue

            return json.dumps({"launched": name, "note": "App started but window not detected by title match"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    @server.tool()
    async def sassy_launch_exe(path: str, args: str = "") -> str:
        """Launch an executable directly by path.

        path = full path to .exe. args = command-line arguments.
        Only allows launching existing .exe files (not scripts or interpreters).
        """
        import shlex
        from pathlib import Path as P
        p = P(path)
        if not p.is_file():
            return json.dumps({"error": f"File not found: {path}"})
        if p.suffix.lower() not in (".exe", ".msi"):
            return json.dumps({"error": f"Only .exe and .msi files allowed, got: {p.suffix}"})
        try:
            cmd = [str(p)] + (shlex.split(args) if args else [])
            proc = subprocess.Popen(cmd, creationflags=subprocess.DETACHED_PROCESS)
            time.sleep(1)
            return json.dumps({
                "launched": path,
                "pid": proc.pid,
                "args": args or None,
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    @server.tool()
    async def sassy_focus_window(title: str) -> str:
        """Bring a window to the foreground by title substring."""
        try:
            from pywinauto import Desktop
            desktop = Desktop(backend="uia")
            title_lower = title.lower()
            for w in desktop.windows():
                try:
                    wt = w.window_text()
                    if wt and title_lower in wt.lower() and w.is_visible():
                        w.set_focus()
                        time.sleep(0.3)
                        return json.dumps({"focused": wt, "pid": w.process_id()})
                except Exception:
                    continue
            return json.dumps({"error": f"Window not found: {title}"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    @server.tool()
    async def sassy_close_window(title: str, force: bool = False) -> str:
        """Close a window by title. Sends WM_CLOSE (graceful) unless force=True.

        Apps may show save dialogs. force=True kills the process.
        """
        try:
            from pywinauto import Desktop
            import psutil

            desktop = Desktop(backend="uia")
            title_lower = title.lower()
            for w in desktop.windows():
                try:
                    wt = w.window_text()
                    if wt and title_lower in wt.lower() and w.is_visible():
                        pid = w.process_id()
                        if force:
                            psutil.Process(pid).kill()
                            return json.dumps({"killed": wt, "pid": pid})
                        else:
                            w.close()
                            return json.dumps({"closed": wt, "pid": pid, "method": "WM_CLOSE"})
                except Exception:
                    continue
            return json.dumps({"error": f"Window not found: {title}"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    @server.tool()
    async def sassy_resize_window(
        title: str,
        x: int = -1, y: int = -1,
        width: int = -1, height: int = -1,
        maximize: bool = False,
        minimize: bool = False,
        restore: bool = False,
    ) -> str:
        """Move and/or resize a window by title.

        x/y = position (-1 to keep current). width/height = size (-1 to keep).
        maximize/minimize/restore for window state changes.
        """
        try:
            from pywinauto import Desktop
            desktop = Desktop(backend="uia")
            title_lower = title.lower()
            for w in desktop.windows():
                try:
                    wt = w.window_text()
                    if wt and title_lower in wt.lower() and w.is_visible():
                        if maximize:
                            w.maximize()
                            return json.dumps({"maximized": wt})
                        if minimize:
                            w.minimize()
                            return json.dumps({"minimized": wt})
                        if restore:
                            w.restore()
                            time.sleep(0.2)

                        rect = w.rectangle()
                        new_x = x if x >= 0 else rect.left
                        new_y = y if y >= 0 else rect.top
                        new_w = width if width > 0 else rect.width()
                        new_h = height if height > 0 else rect.height()

                        # Use win32 API directly — pywinauto UIA backend lacks move_window
                        import ctypes as _ct
                        try:
                            hwnd = w.handle
                            _ct.windll.user32.MoveWindow(hwnd, new_x, new_y, new_w, new_h, True)
                        except Exception:
                            w.move_window(new_x, new_y, new_w, new_h)
                        return json.dumps({
                            "resized": wt,
                            "position": [new_x, new_y],
                            "size": [new_w, new_h],
                        })
                except Exception:
                    continue
            return json.dumps({"error": f"Window not found: {title}"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    @server.tool()
    async def sassy_snap_window(title: str, position: str = "left", monitor: int = 0) -> str:
        """Snap a window to a screen edge. Like Win+Arrow.

        position: "left", "right", "top-left", "top-right",
                  "bottom-left", "bottom-right", "center"
        monitor: 0 = primary, 1+ = additional monitors
        """
        try:
            from pywinauto import Desktop
            import ctypes

            # Get actual work area (excludes taskbar) for the target monitor
            try:
                from sassymcp.modules.ui_automation import _get_monitors
                monitors = _get_monitors()
                if monitors and monitor < len(monitors):
                    m = monitors[monitor]
                    mon_x, mon_y = m["left"], m["top"]
                    mon_w, mon_h = m["width"], m["height"]
                else:
                    import pyautogui
                    mon_x, mon_y = 0, 0
                    mon_w, mon_h = pyautogui.size()
            except Exception:
                import pyautogui
                mon_x, mon_y = 0, 0
                mon_w, mon_h = pyautogui.size()

            # Get actual work area (taskbar-aware) via SystemParametersInfo
            try:
                rect = ctypes.wintypes.RECT()
                ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
                # Only use work area for primary monitor
                if monitor == 0:
                    mon_h = rect.bottom - rect.top
            except Exception:
                pass

            half_w, half_h = mon_w // 2, mon_h // 2

            positions = {
                "left":         (mon_x, mon_y, half_w, mon_h),
                "right":        (mon_x + half_w, mon_y, half_w, mon_h),
                "top-left":     (mon_x, mon_y, half_w, half_h),
                "top-right":    (mon_x + half_w, mon_y, half_w, half_h),
                "bottom-left":  (mon_x, mon_y + half_h, half_w, half_h),
                "bottom-right": (mon_x + half_w, mon_y + half_h, half_w, half_h),
                "center":       (mon_x + mon_w // 4, mon_y + mon_h // 4, half_w, half_h),
            }

            if position not in positions:
                return json.dumps({"error": f"Invalid position. Use: {list(positions.keys())}"})

            desktop = Desktop(backend="uia")
            title_lower = title.lower()
            # Try exact-start match first, then substring (matches focus_window/resize_window behavior)
            candidates = []
            for w in desktop.windows():
                try:
                    wt = w.window_text()
                    if wt and w.is_visible():
                        if title_lower in wt.lower():
                            candidates.append(w)
                except Exception:
                    continue
            if not candidates:
                return json.dumps({"error": f"Window not found: {title}"})
            w = candidates[0]
            wt = w.window_text()
            try:
                w.restore()
            except Exception:
                pass
            time.sleep(0.1)
            px, py, pw, ph = positions[position]
            # Use win32 API directly — pywinauto UIA backend lacks move_window
            try:
                hwnd = w.handle
                ctypes.windll.user32.MoveWindow(hwnd, px, py, pw, ph, True)
            except Exception:
                # Fallback: try pywinauto method in case backend supports it
                w.move_window(px, py, pw, ph)
            return json.dumps({"snapped": wt, "position": position,
                               "rect": [px, py, pw, ph]})
        except Exception as e:
            return json.dumps({"error": str(e)})
