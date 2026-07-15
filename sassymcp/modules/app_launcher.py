# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-GC4IDUQDMCXT
"""AppLauncher - Application control and window management (cross-platform).

Launch apps by name, focus/close/resize/snap windows, and manage the desktop
workspace programmatically. The mechanism is routed at the head (see
_platform):

  - Windows: pywinauto (UIA backend) + win32 MoveWindow.
  - macOS:   AppleScript via `osascript` driving System Events. Window
             control requires the host to grant Accessibility permission to
             the app running SassyMCP (System Settings -> Privacy & Security
             -> Accessibility); denied calls return a clear hint.
  - Linux:   best-effort via wmctrl/xdotool when installed.

Dependencies: pywinauto/psutil/pyautogui (Windows); osascript (macOS, built
in); wmctrl/xdotool (Linux, optional).
"""

import asyncio
import json
import subprocess
import time

from sassymcp import _platform
from sassymcp.modules._security import validate_path as _validate_path


# ══════════════════════════════════════════════════════════════════════════
# macOS — AppleScript / System Events
# ══════════════════════════════════════════════════════════════════════════
_AX_HINT = ("If nothing happened, grant Accessibility permission to the app "
            "running SassyMCP: System Settings -> Privacy & Security -> Accessibility.")


async def _osa(script: str, *args: str, timeout: int = 15) -> str:
    """Run an AppleScript via osascript; positional args land in `argv`."""
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script, *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return "error: osascript timed out"
    out = stdout.decode("utf-8", errors="replace").strip()
    err = stderr.decode("utf-8", errors="replace").strip()
    return out or err


# Shared System Events traversal: find the first non-background process window
# whose name contains the query, then run `<ACTION>` with `w`/`proc` in scope.
def _mac_window_script(action: str) -> str:
    return (
        "on run argv\n"
        "  set q to item 1 of argv\n"
        "  tell application \"System Events\"\n"
        "    repeat with proc in (every process whose background only is false)\n"
        "      repeat with w in (every window of proc)\n"
        "        if name of w contains q then\n"
        f"          {action}\n"
        "        end if\n"
        "      end repeat\n"
        "    end repeat\n"
        "  end tell\n"
        "  return \"error: window not found\"\n"
        "end run\n"
    )


async def _mac_launch_app(name: str, wait_seconds: float) -> str:
    proc = await asyncio.create_subprocess_exec(
        "open", "-a", name,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        return json.dumps({"error": stderr.decode("utf-8", errors="replace").strip()
                           or f"could not open app: {name}"})
    await asyncio.sleep(max(0.0, wait_seconds))
    info = await _osa(
        "on run argv\n"
        "  tell application \"System Events\"\n"
        "    set p to first process whose frontmost is true\n"
        "    set wn to \"\"\n"
        "    try\n      set wn to name of front window of p\n    end try\n"
        "    return (name of p) & \"|\" & wn\n"
        "  end tell\nend run\n", name)
    pname, _, wtitle = info.partition("|")
    return json.dumps({"launched": name, "front_process": pname,
                       "window_title": wtitle or None})


async def _mac_focus(title: str) -> str:
    out = await _osa(_mac_window_script(
        "set frontmost of proc to true\n"
        "          try\n            perform action \"AXRaise\" of w\n          end try\n"
        "          return \"focused: \" & (name of w)"), title)
    if out.startswith("focused:"):
        return json.dumps({"focused": out.split("focused:", 1)[1].strip()})
    return json.dumps({"error": out or f"Window not found: {title}", "hint": _AX_HINT})


async def _mac_close(title: str, force: bool) -> str:
    if force:
        out = await _osa(_mac_window_script(
            "set pid to unix id of proc\n"
            "          do shell script \"kill -9 \" & pid\n"
            "          return \"killed: \" & (name of w)"), title)
        key = "killed"
    else:
        out = await _osa(_mac_window_script(
            "try\n            click (first button of w whose subrole is \"AXCloseButton\")\n"
            "          on error\n            keystroke \"w\" using command down\n          end try\n"
            "          return \"closed: \" & (name of w)"), title)
        key = "closed"
    if out.startswith(key):
        return json.dumps({key: out.split(":", 1)[1].strip()})
    return json.dumps({"error": out or f"Window not found: {title}", "hint": _AX_HINT})


async def _mac_resize(title, x, y, width, height, maximize, minimize, restore) -> str:
    if minimize:
        out = await _osa(_mac_window_script(
            "set value of attribute \"AXMinimized\" of w to true\n"
            "          return \"minimized: \" & (name of w)"), title)
        return json.dumps({"minimized": out.split(":", 1)[-1].strip()} if ":" in out
                          else {"error": out, "hint": _AX_HINT})
    if maximize:
        # Fill the desktop (Finder reports {0,0,w,h} for the main display).
        out = await _osa(
            "on run argv\n"
            "  set q to item 1 of argv\n"
            "  tell application \"Finder\" to set b to bounds of window of desktop\n"
            "  tell application \"System Events\"\n"
            "    repeat with proc in (every process whose background only is false)\n"
            "      repeat with w in (every window of proc)\n"
            "        if name of w contains q then\n"
            "          set position of w to {item 1 of b, item 2 of b}\n"
            "          set size of w to {item 3 of b, item 4 of b}\n"
            "          return \"maximized: \" & (name of w)\n"
            "        end if\n      end repeat\n    end repeat\n  end tell\n"
            "  return \"error: window not found\"\nend run\n", title)
        return json.dumps({"maximized": out.split(":", 1)[-1].strip()} if out.startswith("maximized")
                          else {"error": out, "hint": _AX_HINT})
    # Move/resize, honoring -1 = keep current.
    out = await _osa(
        "on run argv\n"
        "  set q to item 1 of argv\n"
        "  set nx to (item 2 of argv) as integer\n"
        "  set ny to (item 3 of argv) as integer\n"
        "  set nw to (item 4 of argv) as integer\n"
        "  set nh to (item 5 of argv) as integer\n"
        "  tell application \"System Events\"\n"
        "    repeat with proc in (every process whose background only is false)\n"
        "      repeat with w in (every window of proc)\n"
        "        if name of w contains q then\n"
        "          if value of attribute \"AXMinimized\" of w is true then set value of attribute \"AXMinimized\" of w to false\n"
        "          set p to position of w\n          set s to size of w\n"
        "          if nx >= 0 then set item 1 of p to nx\n"
        "          if ny >= 0 then set item 2 of p to ny\n"
        "          if nw >= 0 then set item 1 of s to nw\n"
        "          if nh >= 0 then set item 2 of s to nh\n"
        "          set position of w to p\n          set size of w to s\n"
        "          return \"resized: \" & (name of w) & \" @\" & (item 1 of p) & \",\" & (item 2 of p) & \" \" & (item 1 of s) & \"x\" & (item 2 of s)\n"
        "        end if\n      end repeat\n    end repeat\n  end tell\n"
        "  return \"error: window not found\"\nend run\n",
        title, str(x), str(y), str(width), str(height))
    if out.startswith("resized"):
        return json.dumps({"resized": out.split(":", 1)[1].strip()})
    return json.dumps({"error": out or f"Window not found: {title}", "hint": _AX_HINT})


async def _mac_snap(title: str, position: str) -> str:
    valid = {"left", "right", "top-left", "top-right",
             "bottom-left", "bottom-right", "center"}
    if position not in valid:
        return json.dumps({"error": f"Invalid position. Use: {sorted(valid)}"})
    # Compute the target rect inside AppleScript from the desktop bounds so it
    # is correct on whatever display the user runs.
    geom = {
        "left":         "{x0, y0, hw, fh}",
        "right":        "{x0 + hw, y0, hw, fh}",
        "top-left":     "{x0, y0, hw, hh}",
        "top-right":    "{x0 + hw, y0, hw, hh}",
        "bottom-left":  "{x0, y0 + hh, hw, hh}",
        "bottom-right": "{x0 + hw, y0 + hh, hw, hh}",
        "center":       "{x0 + (hw / 2), y0 + (hh / 2), hw, hh}",
    }[position]
    out = await _osa(
        "on run argv\n"
        "  set q to item 1 of argv\n"
        "  tell application \"Finder\" to set b to bounds of window of desktop\n"
        "  set x0 to item 1 of b\n  set y0 to item 2 of b\n"
        "  set fw to (item 3 of b) - x0\n  set fh to (item 4 of b) - y0\n"
        "  set hw to fw div 2\n  set hh to fh div 2\n"
        f"  set r to {geom}\n"
        "  tell application \"System Events\"\n"
        "    repeat with proc in (every process whose background only is false)\n"
        "      repeat with w in (every window of proc)\n"
        "        if name of w contains q then\n"
        "          set position of w to {item 1 of r, item 2 of r}\n"
        "          set size of w to {item 3 of r, item 4 of r}\n"
        "          return \"snapped: \" & (name of w)\n"
        "        end if\n      end repeat\n    end repeat\n  end tell\n"
        "  return \"error: window not found\"\nend run\n", title)
    if out.startswith("snapped"):
        return json.dumps({"snapped": out.split(":", 1)[1].strip(), "position": position})
    return json.dumps({"error": out or f"Window not found: {title}", "hint": _AX_HINT})


# ══════════════════════════════════════════════════════════════════════════
# Linux — wmctrl / xdotool best-effort
# ══════════════════════════════════════════════════════════════════════════
async def _linux_exec(*argv, timeout: int = 10) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, (out + err).decode("utf-8", errors="replace").strip()
    except FileNotFoundError:
        return 127, f"{argv[0]} not found"
    except asyncio.TimeoutError:
        return 124, "timed out"


def _linux_unsupported(action: str) -> str:
    return json.dumps({"error": _platform.unsupported(
        f"{action} on Linux requires wmctrl (apt install wmctrl) or xdotool")})


# ══════════════════════════════════════════════════════════════════════════
# Windows — pywinauto / win32 (behavior preserved verbatim)
# ══════════════════════════════════════════════════════════════════════════
async def _win_launch_app(name: str, wait_seconds: float) -> str:
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


async def _win_focus(title: str) -> str:
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


async def _win_close(title: str, force: bool) -> str:
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


async def _win_resize(title, x, y, width, height, maximize, minimize, restore) -> str:
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


async def _win_snap(title: str, position: str, monitor: int) -> str:
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


def register(server):

    @server.tool()
    async def sassy_launch_app(name: str, wait_seconds: float = 2.0) -> str:
        """Launch an application by name.

        Windows: Start-menu search. macOS: `open -a <name>`. Linux: exec by
        name. name = the app name (e.g. "notepad"/"Notes", "chrome"/"Google
        Chrome", "code"). Waits wait_seconds, then reports the window if found.
        """
        if _platform.IS_WINDOWS:
            return await _win_launch_app(name, wait_seconds)
        if _platform.IS_MACOS:
            return await _mac_launch_app(name, wait_seconds)
        rc, out = await _linux_exec("/bin/sh", "-c", f"nohup {name} >/dev/null 2>&1 &")
        return json.dumps({"launched": name} if rc == 0 else {"error": out})

    @server.tool()
    async def sassy_launch_exe(path: str, args: str = "") -> str:
        """Launch an executable directly by path.

        Windows: .exe/.msi. macOS: a .app bundle (via `open`) or any executable
        file. Linux: any executable file. Only launches existing files.
        """
        import shlex
        from pathlib import Path as P
        ok, err = _validate_path(path)
        if not ok:
            return json.dumps({"error": err})
        p = P(path)
        extra = shlex.split(args) if args else []

        if _platform.IS_WINDOWS:
            if not p.is_file():
                return json.dumps({"error": f"File not found: {path}"})
            if p.suffix.lower() not in (".exe", ".msi"):
                return json.dumps({"error": f"Only .exe and .msi files allowed, got: {p.suffix}"})
            try:
                proc = subprocess.Popen([str(p)] + extra, creationflags=subprocess.DETACHED_PROCESS)
                time.sleep(1)
                return json.dumps({"launched": path, "pid": proc.pid, "args": args or None})
            except Exception as e:
                return json.dumps({"error": str(e)})

        if _platform.IS_MACOS and p.suffix.lower() == ".app":
            argv = ["open", str(p)] + (["--args", *extra] if extra else [])
            try:
                proc = subprocess.Popen(argv)
                return json.dumps({"launched": path, "pid": proc.pid, "method": "open", "args": args or None})
            except Exception as e:
                return json.dumps({"error": str(e)})

        # macOS non-.app and Linux: direct exec of an existing executable.
        if not p.is_file():
            return json.dumps({"error": f"File not found: {path}"})
        try:
            proc = subprocess.Popen([str(p)] + extra, start_new_session=True)
            time.sleep(0.5)
            return json.dumps({"launched": path, "pid": proc.pid, "args": args or None})
        except Exception as e:
            return json.dumps({"error": str(e)})

    @server.tool()
    async def sassy_focus_window(title: str) -> str:
        """Bring a window to the foreground by title substring."""
        if _platform.IS_WINDOWS:
            return await _win_focus(title)
        if _platform.IS_MACOS:
            return await _mac_focus(title)
        if _platform.which("wmctrl"):
            rc, out = await _linux_exec("wmctrl", "-a", title)
            return json.dumps({"focused": title} if rc == 0 else {"error": out or f"Window not found: {title}"})
        return _linux_unsupported("window focus")

    @server.tool()
    async def sassy_close_window(title: str, force: bool = False) -> str:
        """Close a window by title. Graceful unless force=True (kills the process)."""
        if _platform.IS_WINDOWS:
            return await _win_close(title, force)
        if _platform.IS_MACOS:
            return await _mac_close(title, force)
        if _platform.which("wmctrl"):
            rc, out = await _linux_exec("wmctrl", "-c", title)
            return json.dumps({"closed": title} if rc == 0 else {"error": out or f"Window not found: {title}"})
        return _linux_unsupported("window close")

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
        if _platform.IS_WINDOWS:
            return await _win_resize(title, x, y, width, height, maximize, minimize, restore)
        if _platform.IS_MACOS:
            return await _mac_resize(title, x, y, width, height, maximize, minimize, restore)
        if _platform.which("wmctrl"):
            if minimize:
                rc, out = await _linux_exec("xdotool", "search", "--name", title, "windowminimize")
            else:
                g = f"0,{max(x,0)},{max(y,0)},{max(width,-1)},{max(height,-1)}"
                rc, out = await _linux_exec("wmctrl", "-r", title, "-e", g)
            return json.dumps({"resized": title} if rc == 0 else {"error": out})
        return _linux_unsupported("window resize")

    @server.tool()
    async def sassy_snap_window(title: str, position: str = "left", monitor: int = 0) -> str:
        """Snap a window to a screen edge (like Win+Arrow).

        position: left, right, top-left, top-right, bottom-left, bottom-right,
                  center. monitor: 0 = primary (Windows multi-monitor aware).
        """
        if _platform.IS_WINDOWS:
            return await _win_snap(title, position, monitor)
        if _platform.IS_MACOS:
            return await _mac_snap(title, position)
        return _linux_unsupported("window snap")
