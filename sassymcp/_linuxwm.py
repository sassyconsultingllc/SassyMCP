# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
"""Window management for Linux/BSD desktops (X11 and XWayland).

Uses only argv-form subprocess calls to widely packaged tools:

- ``wmctrl``  : list / focus / close / move-resize / state (EWMH WMs:
                XFCE, GNOME-on-X11, KDE, Cinnamon, MATE, i3 partially ...)
- ``xdotool`` : fallback for focus / close / minimize / geometry
- ``xrandr``  : monitor layout

Native Wayland compositors do not let clients enumerate or move other
clients' windows; there the helpers return a clear error naming the
limitation. Apps running under XWayland are still visible to wmctrl.

Every public function is synchronous and safe to call via
``asyncio.to_thread``. Errors are returned, never raised.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess

__all__ = [
    "available",
    "close",
    "focus",
    "list_windows",
    "missing_hint",
    "monitors",
    "move_resize",
    "set_state",
    "snap",
]

_TIMEOUT = 10


def _run(*argv: str) -> tuple[int, str]:
    try:
        p = subprocess.run(list(argv), capture_output=True, text=True,
                           timeout=_TIMEOUT, check=False)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, f"{argv[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, f"{argv[0]} timed out"


def _is_pure_wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY")) and not os.environ.get("DISPLAY")


def available() -> bool:
    """True when some window tool can talk to an X server."""
    if not os.environ.get("DISPLAY"):
        return False
    return bool(shutil.which("wmctrl") or shutil.which("xdotool"))


def missing_hint() -> str:
    if _is_pure_wayland():
        return ("native Wayland sessions do not allow controlling other apps' "
                "windows; log in to an X11/Xorg session (or run the app under XWayland)")
    if not os.environ.get("DISPLAY"):
        return "no graphical display ($DISPLAY is unset)"
    from sassymcp._platform import install_hint
    return f"install wmctrl or xdotool ({install_hint('wmctrl')})"


# ── Enumeration ───────────────────────────────────────────────────────────
_WMCTRL_LINE = re.compile(
    r"^(0x[0-9a-fA-F]+)\s+(-?\d+)\s+(\d+)\s+(-?\d+)\s+(-?\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s?(.*)$")


def _proc_name(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        import psutil
        return psutil.Process(pid).name()
    except Exception:
        pass
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def list_windows() -> list[dict] | str:
    """Top-level windows as dicts, or an error string."""
    if not os.environ.get("DISPLAY"):
        return missing_hint()
    if shutil.which("wmctrl"):
        rc, out = _run("wmctrl", "-l", "-p", "-G")
        if rc != 0:
            return out.strip() or "wmctrl failed"
        wins = []
        for line in out.splitlines():
            m = _WMCTRL_LINE.match(line)
            if not m:
                continue
            wid, desk, pid, x, y, w, h, _host, title = m.groups()
            if (int(desk) < 0 and not title) or int(w) <= 10 or int(h) <= 10:
                continue  # untitled sticky panels, 1x1/5x5 WM helper windows
            wins.append({
                "id": wid, "title": title, "pid": int(pid),
                "process": _proc_name(int(pid)), "desktop": int(desk),
                "left": int(x), "top": int(y), "width": int(w), "height": int(h),
            })
        return wins
    if shutil.which("xdotool"):
        rc, out = _run("xdotool", "search", "--onlyvisible", "--name", ".")
        if rc != 0 and not out.strip():
            return []
        wins = []
        for wid in out.split():
            if not wid.isdigit():
                continue
            _, title = _run("xdotool", "getwindowname", wid)
            _, geo = _run("xdotool", "getwindowgeometry", "--shell", wid)
            _, pid = _run("xdotool", "getwindowpid", wid)
            g = dict(kv.split("=", 1) for kv in geo.split() if "=" in kv)
            p = int(pid.strip()) if pid.strip().isdigit() else 0
            wins.append({
                "id": hex(int(wid)), "title": title.strip(), "pid": p,
                "process": _proc_name(p),
                "left": int(g.get("X", 0)), "top": int(g.get("Y", 0)),
                "width": int(g.get("WIDTH", 0)), "height": int(g.get("HEIGHT", 0)),
            })
        return [w for w in wins if w["title"] and w["width"] > 10 and w["height"] > 10]
    return missing_hint()


def _find(title: str) -> dict | str:
    wins = list_windows()
    if isinstance(wins, str):
        return wins
    t = title.lower()
    for w in wins:
        if t in w["title"].lower():
            return w
    return f"Window not found: {title}"


# ── Actions ───────────────────────────────────────────────────────────────
def focus(title: str) -> dict:
    w = _find(title)
    if isinstance(w, str):
        return {"error": w}
    if shutil.which("wmctrl"):
        rc, out = _run("wmctrl", "-i", "-a", w["id"])
    else:
        rc, out = _run("xdotool", "windowactivate", str(int(w["id"], 16)))
    return {"focused": w["title"]} if rc == 0 else {"error": out.strip()}


def close(title: str, force: bool = False) -> dict:
    w = _find(title)
    if isinstance(w, str):
        return {"error": w}
    if force and w.get("pid"):
        try:
            os.kill(w["pid"], 9)
            return {"closed": w["title"], "killed_pid": w["pid"]}
        except OSError as e:
            return {"error": str(e)}
    if shutil.which("wmctrl"):
        rc, out = _run("wmctrl", "-i", "-c", w["id"])
    else:
        rc, out = _run("xdotool", "windowclose", str(int(w["id"], 16)))
    return {"closed": w["title"]} if rc == 0 else {"error": out.strip()}


def set_state(title: str, state: str) -> dict:
    """state: maximize | minimize | restore."""
    w = _find(title)
    if isinstance(w, str):
        return {"error": w}
    if state == "minimize":
        if shutil.which("xdotool"):
            rc, out = _run("xdotool", "windowminimize", str(int(w["id"], 16)))
        else:
            rc, out = _run("wmctrl", "-i", "-r", w["id"], "-b", "add,hidden")
    elif state == "maximize":
        rc, out = _run("wmctrl", "-i", "-r", w["id"], "-b",
                       "add,maximized_vert,maximized_horz")
    else:  # restore
        rc, out = _run("wmctrl", "-i", "-r", w["id"], "-b",
                       "remove,maximized_vert,maximized_horz,hidden")
        if rc == 0:
            _run("wmctrl", "-i", "-a", w["id"])
    return {"window": w["title"], "state": state} if rc == 0 else {"error": out.strip()}


def move_resize(title: str, x: int = -1, y: int = -1,
                width: int = -1, height: int = -1) -> dict:
    """-1 keeps the current value (wmctrl semantics)."""
    w = _find(title)
    if isinstance(w, str):
        return {"error": w}
    if shutil.which("wmctrl"):
        # A maximized window ignores -e on most WMs; un-maximize first.
        _run("wmctrl", "-i", "-r", w["id"], "-b", "remove,maximized_vert,maximized_horz")
        g = f"0,{x},{y},{width},{height}"
        rc, out = _run("wmctrl", "-i", "-r", w["id"], "-e", g)
    else:
        wid = str(int(w["id"], 16))
        rc, out = 0, ""
        if x >= 0 or y >= 0:
            rc, out = _run("xdotool", "windowmove", wid,
                           str(x if x >= 0 else w["left"]), str(y if y >= 0 else w["top"]))
        if rc == 0 and (width >= 0 or height >= 0):
            rc, out = _run("xdotool", "windowsize", wid,
                           str(width if width >= 0 else w["width"]),
                           str(height if height >= 0 else w["height"]))
    return {"resized": w["title"], "x": x, "y": y, "width": width, "height": height} \
        if rc == 0 else {"error": out.strip()}


# ── Monitors ──────────────────────────────────────────────────────────────
_XRANDR_MON = re.compile(r"^\s*\d+:\s+\+?(\*?)(\S+)\s+(\d+)/\d+x(\d+)/\d+\+(-?\d+)\+(-?\d+)")


def monitors() -> list[dict] | None:
    """Monitor layout via `xrandr --listmonitors`, or None if unavailable."""
    if not os.environ.get("DISPLAY") or not shutil.which("xrandr"):
        return None
    rc, out = _run("xrandr", "--listmonitors")
    if rc != 0:
        return None
    mons = []
    for line in out.splitlines():
        m = _XRANDR_MON.match(line)
        if not m:
            continue
        star, name, w, h, x, y = m.groups()
        w, h, x, y = int(w), int(h), int(x), int(y)
        mons.append({"name": name, "left": x, "top": y, "right": x + w, "bottom": y + h,
                     "width": w, "height": h, "scale_percent": 100, "primary": bool(star)})
    if mons and not any(m["primary"] for m in mons):
        mons[0]["primary"] = True
    return mons or None


def snap(title: str, position: str = "left", monitor: int = 0) -> dict:
    mons = monitors() or []
    if not mons:
        return {"error": "cannot read monitor layout (needs xrandr)"}
    ordered = sorted(mons, key=lambda m: (not m["primary"], m["left"], m["top"]))
    mon = ordered[monitor] if 0 <= monitor < len(ordered) else ordered[0]
    L, T, W, H = mon["left"], mon["top"], mon["width"], mon["height"]
    hw, hh = W // 2, H // 2
    rects = {
        "left": (L, T, hw, H), "right": (L + hw, T, W - hw, H),
        "top": (L, T, W, hh), "bottom": (L, T + hh, W, H - hh),
        "top-left": (L, T, hw, hh), "top-right": (L + hw, T, W - hw, hh),
        "bottom-left": (L, T + hh, hw, H - hh), "bottom-right": (L + hw, T + hh, W - hw, H - hh),
        "center": (L + W // 6, T + H // 6, W * 2 // 3, H * 2 // 3),
    }
    if position == "maximize":
        return set_state(title, "maximize")
    r = rects.get(position)
    if not r:
        return {"error": f"unknown position {position!r}; use one of {sorted(rects)}"}
    res = move_resize(title, *r)
    if "error" not in res:
        res = {"snapped": res["resized"], "position": position, "monitor": mon.get("name")}
    return res
