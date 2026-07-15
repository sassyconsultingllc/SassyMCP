# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-6N6ND2LDNM4T
"""PhoneScreen - Android live vision, UI awareness, interaction, and screen mirroring.

Dynamic phone observation: reads the UI accessibility tree (uiautomator),
monitors for screen changes, captures low-res frames, and provides
touch/swipe/type/key input injection — all via ADB.

Scrcpy support retained for live mirroring when available.
"""

import asyncio
import base64
import io
import json
import os
import shutil
import time
import xml.etree.ElementTree as ET

from sassymcp import _platform

# ── Autonomous Pause/Resume State ────────────────────────────
# When paused, all interaction tools (tap/swipe/type) refuse to execute.
# Observation tools (ui/state/glance/watch) still work so the AI can see
# when the user is done. The user says "resume" and the AI calls
# sassy_phone_resume to unpause.
_phone_paused = False
_pause_reason = ""


def _adb_path() -> str:
    path = shutil.which("adb")
    if path:
        return path
    for c in _platform.adb_candidates():
        if os.path.isfile(c):
            return c
    return "adb"


# Bound concurrent ADB invocations. A LOOP of phone-state / phone-glance
# can fork an adb subprocess on every call; enough concurrent calls runs
# the device service out of slots and wedges every phone tool at once.
# Cap at 4 in flight — plenty for real use, refuses a flood.
_ADB_SEMAPHORE = asyncio.Semaphore(4)


async def _adb(*args, device="", timeout=15):
    """Run an ADB call, return stdout string.

    Bounded by _ADB_SEMAPHORE (4 in flight). When a caller passes a
    multi-token shell pipeline via ('shell', 'dumpsys ...', '|', 'grep ...'),
    we glue the pipeline onto ONE argument so `adb shell` invokes the
    device's `sh -c` — passing `|` as a separate argv element ships it
    as a literal token, which adb does not interpret as a pipeline.
    """
    raw_args = list(args)
    if raw_args and raw_args[0] == "shell" and "|" in raw_args[1:]:
        raw_args = ["shell", " ".join(raw_args[1:])]
    cmd = [_adb_path()]
    if device:
        cmd.extend(["-s", device])
    cmd.extend(raw_args)
    async with _ADB_SEMAPHORE:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            out = stdout.decode("utf-8", errors="replace").strip()
            if proc.returncode != 0 and not out:
                return stderr.decode("utf-8", errors="replace").strip()
            return out
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return ""
        except FileNotFoundError:
            return ""


def _parse_ui_xml(xml_text: str) -> list[dict]:
    """Parse uiautomator XML dump into a flat list of UI elements."""
    elements = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return elements

    for node in root.iter("node"):
        text = node.get("text", "")
        desc = node.get("content-desc", "")
        cls = node.get("class", "")
        pkg = node.get("package", "")
        bounds = node.get("bounds", "")
        clickable = node.get("clickable") == "true"
        focused = node.get("focused") == "true"
        checked = node.get("checked") == "true"
        enabled = node.get("enabled") == "true"
        resource_id = node.get("resource-id", "")

        # Parse bounds "[x1,y1][x2,y2]"
        cx, cy = 0, 0
        if bounds:
            try:
                parts = bounds.replace("][", ",").strip("[]").split(",")
                x1, y1, x2, y2 = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            except (ValueError, IndexError):
                pass

        # Only include elements that have text, description, or are interactive
        if text or desc or clickable or resource_id:
            el = {"class": cls.split(".")[-1] if cls else ""}
            if text:
                el["text"] = text
            if desc:
                el["desc"] = desc
            if resource_id:
                el["id"] = resource_id.split("/")[-1] if "/" in resource_id else resource_id
            if bounds:
                el["center"] = [cx, cy]
            if clickable:
                el["clickable"] = True
            if focused:
                el["focused"] = True
            if checked:
                el["checked"] = True
            if not enabled:
                el["disabled"] = True
            elements.append(el)

    return elements


_scrcpy_proc = None


def _register_hooks():
    from sassymcp.modules._hooks import register_hook

    register_hook(
        name="phone_autonomous",
        module="phone_screen",
        description="Autonomous phone control — operate the phone with minimal user intervention",
        triggers=["use my phone", "open app", "do this on my phone", "phone task", "automate phone", "set up app"],
        instructions="""
## Autonomous Phone Control Playbook

You are operating the user's Android phone. Work methodically.

### Before ANY interaction:
1. sassy_phone_state — check screen on, foreground app, battery
2. sassy_phone_ui — read the full UI tree to understand what's on screen
3. Plan your taps based on element coordinates from the UI tree
4. NEVER guess coordinates — always read the UI tree first

### Interaction Loop:
1. Read UI tree → identify target element → get its center coordinates
2. Tap/swipe/type → wait 1-2 seconds for UI to update
3. Read UI tree again → verify the action worked
4. If unexpected result → read UI tree, reassess, don't retry blindly

### Sensitive Context (AUTOMATIC):
- tap/swipe/type auto-check for login/payment/auth screens
- If blocked: describe what you see, ask the user, call with confirmed=True only after approval
- NEVER set confirmed=True without the user explicitly saying yes

### When to Pause:
- User says "wait", "hold on", "let me" → sassy_phone_pause
- You detect the user needs to enter credentials → suggest pausing
- Keep watching during pause — you learn from what the user does
- Resume only when user says "done"/"continue"/"go"

### Navigation:
- sassy_phone_key("HOME") — go to home screen
- sassy_phone_key("BACK") — go back
- sassy_phone_key("APP_SWITCH") — recent apps
- sassy_phone_open("com.package.name") — launch specific app
""",
    )

    register_hook(
        name="phone_supervised",
        module="phone_screen",
        description="Supervised phone mode — user drives, AI watches and assists",
        triggers=["watch my phone", "help me with phone", "guide me", "what do i tap", "walk me through"],
        instructions="""
## Supervised Phone Playbook

The user is operating the phone. You are watching and advising.

### Your Role:
- Observe via sassy_phone_ui and sassy_phone_glance
- Describe what you see when asked
- Suggest next steps based on the UI tree
- Read text the user can't see clearly
- Watch for errors or wrong paths

### DO:
- Poll sassy_phone_ui every few seconds to stay current
- Tell the user exactly what to tap (element text + position)
- Warn if the user is about to do something destructive
- Note what app is in foreground via sassy_phone_state

### DON'T:
- Don't use tap/swipe/type — the user is driving
- Don't assume you know what the user sees — always read the UI tree
- Don't give instructions from memory — verify the current screen state first
""",
    )

    register_hook(
        name="phone_debug",
        module="phone_screen",
        description="Diagnose phone issues — crashes, slow apps, connectivity, storage",
        triggers=["phone problem", "app crashing", "phone slow", "debug phone", "phone not working", "diagnose phone"],
        instructions="""
## Phone Debugging Playbook

Diagnose Android issues systematically.

### Triage (do these first):
1. sassy_phone_state — screen on? battery level? wifi connected?
2. sassy_adb_devices — device connected and authorized?
3. sassy_phone_ui — can we read the UI? (rules out frozen/locked state)

### App Issues:
- App crashing → sassy_adb_logcat with app filter, look for FATAL/ANR
- App slow → sassy_android_processes to check CPU/memory usage
- App not opening → sassy_adb_app_info to check package status
- Force stop: sassy_adb_shell "am force-stop com.package.name"
- Clear data: sassy_adb_shell "pm clear com.package.name" (WARNING: destructive)

### System Issues:
- Storage: sassy_adb_shell "df -h" or "dumpsys diskstats"
- Battery drain: sassy_adb_shell "dumpsys batterystats"
- Network: sassy_adb_shell "ping -c 3 google.com"
- Running services: sassy_adb_shell "dumpsys activity services"

### Always:
- Check logcat BEFORE and AFTER reproducing an issue
- Use sassy_phone_watch to observe UI changes during reproduction
- Report findings with specific log lines, not summaries
""",
    )

try:
    _register_hooks()
except Exception:
    pass


def _find_scrcpy():
    path = shutil.which("scrcpy")
    if path:
        return path
    candidates = _platform.pick(
        windows=[r"C:\scrcpy\scrcpy.exe", os.path.expandvars(r"%USERPROFILE%\scrcpy\scrcpy.exe")],
        macos=["/opt/homebrew/bin/scrcpy", "/usr/local/bin/scrcpy"],
        linux=["/usr/bin/scrcpy", "/usr/local/bin/scrcpy", "/snap/bin/scrcpy"],
        default=[],
    )
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def register(server):

    # ── Phone Observation (what's on screen) ─────────────────────

    @server.tool()
    async def sassy_phone_ui(device: str = "") -> str:
        """Read the phone's UI accessibility tree. Returns every visible element
        with text, description, coordinates, and interaction state.

        This is how the AI "sees" the phone — structured data, not pixels.
        Much faster than screenshots and never misses text content.
        """
        xml = await _adb("exec-out", "uiautomator", "dump", "/dev/tty", device=device, timeout=10)
        if not xml or "<hierarchy" not in xml:
            return json.dumps({"error": "Could not read UI tree. Screen may be locked or uiautomator unavailable."})

        elements = _parse_ui_xml(xml)
        result = {
            "elements": elements,
            "count": len(elements),
            "timestamp": time.time(),
        }
        # Flag sensitive contexts so the AI knows to ask the user
        sensitive = _detect_sensitive_context(elements)
        if sensitive:
            result["caution"] = sensitive
        return json.dumps(result)

    @server.tool()
    async def sassy_phone_state(device: str = "") -> str:
        """Get phone state: foreground app, screen on/off, battery, wifi, notifications.

        Quick status check — combine with sassy_phone_ui for full awareness.
        """
        results = {}

        # Foreground activity
        top = await _adb("shell", "dumpsys", "activity", "activities",
                         "|", "grep", "-E", "mResumedActivity|mFocusedApp",
                         device=device, timeout=5)
        if top:
            # Extract package/activity from the output
            for line in top.splitlines():
                if "u0" in line:
                    parts = line.strip().split()
                    for p in parts:
                        if "/" in p and "." in p:
                            pkg_act = p.strip("{").strip("}")
                            results["foreground"] = pkg_act
                            break

        # Screen state
        screen = await _adb("shell", "dumpsys", "power", "|", "grep", "mScreenOn",
                            device=device, timeout=5)
        results["screen_on"] = "true" in screen.lower() if screen else None

        # Battery
        battery = await _adb("shell", "dumpsys", "battery", "|",
                             "grep", "-E", "level|status|plugged",
                             device=device, timeout=5)
        if battery:
            for line in battery.splitlines():
                line = line.strip()
                if "level" in line.lower():
                    try:
                        results["battery_level"] = int(line.split(":")[-1].strip())
                    except ValueError:
                        pass
                elif "status" in line.lower():
                    val = line.split(":")[-1].strip()
                    status_map = {"2": "charging", "3": "discharging", "4": "not_charging", "5": "full"}
                    results["battery_status"] = status_map.get(val, val)
                elif "plugged" in line.lower():
                    val = line.split(":")[-1].strip()
                    results["plugged"] = val != "0"

        # WiFi
        wifi = await _adb("shell", "dumpsys", "wifi", "|", "grep", "mNetworkInfo",
                          device=device, timeout=5)
        if wifi and "CONNECTED" in wifi.upper():
            results["wifi"] = "connected"
        elif wifi:
            results["wifi"] = "disconnected"

        # Notification count (quick)
        notif = await _adb("shell", "dumpsys", "notification", "|",
                           "grep", "-c", "StatusBarNotification",
                           device=device, timeout=5)
        try:
            results["notification_count"] = int(notif.strip())
        except (ValueError, AttributeError):
            pass

        results["timestamp"] = time.time()
        return json.dumps(results, indent=2)

    @server.tool()
    async def sassy_phone_glance(
        device: str = "",
        max_width: int = 480,
        quality: int = 20,
    ) -> str:
        """Fast low-res grayscale phone screenshot for AI vision. ~4-8KB.

        Uses exec-out to pipe directly (no temp file on device).
        Converts to grayscale and compresses hard for minimal context cost.
        """
        from PIL import Image

        try:
            # exec-out pipes the PNG directly — no file write on device
            proc = await asyncio.create_subprocess_exec(
                _adb_path(), *(["-s", device] if device else []),
                "exec-out", "screencap", "-p",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)

            if not stdout or len(stdout) < 100:
                return json.dumps({"error": "Could not capture screen. Device connected?"})

            img = Image.open(io.BytesIO(stdout))
            orig_w, orig_h = img.size
            if orig_w > max_width:
                ratio = max_width / orig_w
                img = img.resize((max_width, int(orig_h * ratio)), Image.LANCZOS)
            gray = img.convert("L")

            buf = io.BytesIO()
            gray.save(buf, format="JPEG", quality=quality, optimize=True)
            raw = buf.getvalue()

            return json.dumps({
                "image_base64": base64.b64encode(raw).decode("ascii"),
                "format": "grayscale_jpeg",
                "original_size": [orig_w, orig_h],
                "size": list(gray.size),
                "bytes": len(raw),
                "timestamp": time.time(),
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    @server.tool()
    async def sassy_phone_watch(
        seconds: float = 5.0,
        interval: float = 1.0,
        device: str = "",
        change_threshold: float = 3.0,
        max_frames: int = 8,
    ) -> str:
        """Monitor phone screen for changes. Returns UI tree snapshots when content changes.

        Uses uiautomator (fast, structured) for change detection, not pixels.
        Includes a grayscale frame on the first and last capture for visual context.

        seconds: monitoring duration (1-30)
        interval: seconds between checks (0.5-5.0)
        change_threshold: minimum % of elements that must differ to count as change
        """
        seconds = max(1.0, min(seconds, 30.0))
        interval = max(0.5, min(interval, 5.0))
        max_frames = max(1, min(max_frames, 15))

        snapshots = []
        prev_elements = None
        start = time.time()

        while time.time() - start < seconds and len(snapshots) < max_frames:
            xml = await _adb("exec-out", "uiautomator", "dump", "/dev/tty",
                             device=device, timeout=8)
            elements = _parse_ui_xml(xml) if xml and "<hierarchy" in xml else []
            elapsed = round(time.time() - start, 2)

            if prev_elements is None:
                # First snapshot — always include
                snapshots.append({
                    "frame": 1,
                    "elapsed_s": elapsed,
                    "elements": elements,
                    "element_count": len(elements),
                    "change": "initial",
                })
            else:
                # Compare: count elements with different text
                prev_texts = {(e.get("id", ""), e.get("text", "")) for e in prev_elements}
                curr_texts = {(e.get("id", ""), e.get("text", "")) for e in elements}
                diff_count = len(prev_texts.symmetric_difference(curr_texts))
                total = max(len(prev_texts), len(curr_texts), 1)
                change_pct = round(diff_count / total * 100, 1)

                if change_pct >= change_threshold:
                    snapshots.append({
                        "frame": len(snapshots) + 1,
                        "elapsed_s": elapsed,
                        "elements": elements,
                        "element_count": len(elements),
                        "change_pct": change_pct,
                        "elements_changed": diff_count,
                    })

            prev_elements = elements
            await asyncio.sleep(interval)

        return json.dumps({
            "snapshots": snapshots,
            "snapshot_count": len(snapshots),
            "duration_s": round(time.time() - start, 2),
        })

    # ── Sensitive Context Detection ────────────────────────────────

    # Keywords/patterns that indicate auth, payments, or user-decision screens
    _SENSITIVE_KEYWORDS = {
        "password", "passcode", "pin", "sign in", "signin", "sign up", "signup",
        "log in", "login", "log out", "logout", "authenticate", "verification",
        "verify", "2fa", "two-factor", "otp", "one-time", "biometric",
        "fingerprint", "face id", "confirm", "authorize", "permission",
        "allow", "deny", "grant", "accept", "decline", "consent",
        "purchase", "buy", "pay", "checkout", "payment", "subscribe",
        "billing", "credit card", "debit", "cvv", "expir",
        "delete account", "remove account", "reset", "erase",
        "choose account", "select account", "switch account", "add account",
        "captcha", "i'm not a robot", "security check",
        "terms", "privacy policy", "agree",
        "send money", "transfer", "withdraw",
        "uninstall", "factory reset", "wipe",
    }

    _SENSITIVE_RESOURCE_IDS = {
        "password", "passwd", "pin_entry", "otp", "captcha",
        "login", "signin", "signup", "auth",
        "payment", "checkout", "purchase",
    }

    def _detect_sensitive_context(elements: list[dict]) -> dict | None:
        """Scan UI elements for auth/payment/decision contexts.
        Returns context info if sensitive, None if safe to proceed."""
        triggers = []
        for el in elements:
            text = (el.get("text", "") + " " + el.get("desc", "")).lower()
            res_id = el.get("id", "").lower()

            for kw in _SENSITIVE_KEYWORDS:
                if kw in text:
                    triggers.append({"keyword": kw, "element_text": el.get("text", ""),
                                     "element_id": el.get("id", "")})
                    break
            for rid in _SENSITIVE_RESOURCE_IDS:
                if rid in res_id:
                    triggers.append({"keyword": f"resource:{rid}", "element_text": el.get("text", ""),
                                     "element_id": el.get("id", "")})
                    break

        if triggers:
            # Deduplicate
            seen = set()
            unique = []
            for t in triggers:
                key = (t["keyword"], t.get("element_id"))
                if key not in seen:
                    seen.add(key)
                    unique.append(t)
            return {
                "sensitive": True,
                "reason": "Screen contains auth, payment, or permission elements",
                "triggers": unique[:10],
                "action": "CONFIRM_WITH_USER — describe what you see and ask the user what to do. Do NOT tap, type, or interact without explicit user instruction.",
            }
        return None

    async def _check_screen_safety(device: str = "") -> dict | None:
        """Quick UI tree scan for sensitive context. Returns warning or None."""
        xml = await _adb("exec-out", "uiautomator", "dump", "/dev/tty",
                         device=device, timeout=8)
        if not xml or "<hierarchy" not in xml:
            return None
        elements = _parse_ui_xml(xml)
        return _detect_sensitive_context(elements)

    # ── Pause/Resume Controls ────────────────────────────────────

    @server.tool()
    async def sassy_phone_pause(reason: str = "User requested pause") -> str:
        """Pause all phone interaction. Observation tools still work.

        Call when: user says "wait", "hold on", "let me handle this", or when
        sensitive context is detected and user wants to do it manually.

        While paused:
        - tap/swipe/type/key are BLOCKED
        - phone_ui/glance/watch/state STILL WORK — keep watching to learn context
        - When user says "done"/"continue"/"resume" → call sassy_phone_resume
        """
        global _phone_paused, _pause_reason
        _phone_paused = True
        _pause_reason = reason
        return json.dumps({
            "status": "paused",
            "reason": reason,
            "note": "Phone interaction paused. Observation tools (ui, glance, watch, state) still work. "
                    "Call sassy_phone_resume when the user is ready to continue.",
        })

    @server.tool()
    async def sassy_phone_resume() -> str:
        """Resume phone interaction after a pause.

        Call this when the user says they're done with manual interaction
        (e.g. "done", "continue", "resume", "go ahead").
        """
        global _phone_paused, _pause_reason
        was_paused = _phone_paused
        old_reason = _pause_reason
        _phone_paused = False
        _pause_reason = ""
        return json.dumps({
            "status": "resumed",
            "was_paused": was_paused,
            "previous_reason": old_reason,
        })

    # ── Phone Interaction (touch, type, navigate) ────────────────
    # All interaction tools:
    # 1. Check if paused — refuse if so
    # 2. Check the screen for sensitive contexts BEFORE acting
    # 3. If auth/payment/permission detected, REFUSE and describe what's on screen

    def _pause_check() -> dict | None:
        """Return pause info if paused, None if ok to proceed."""
        if _phone_paused:
            return {
                "paused": True,
                "reason": _pause_reason,
                "action": "Phone interaction is paused. Wait for the user to say 'resume' or 'done', "
                          "then call sassy_phone_resume. You can still observe with sassy_phone_ui/glance.",
            }
        return None

    @server.tool()
    async def sassy_phone_tap(x: int, y: int, device: str = "", confirmed: bool = False) -> str:
        """Tap a point on the phone screen. Use sassy_phone_ui to find coordinates.

        Safety: auto-checks screen for login/payment/permission contexts.
        If detected, returns a warning instead of tapping. Set confirmed=True
        only after the user explicitly says to proceed.
        """
        paused = _pause_check()
        if paused:
            paused["blocked_action"] = f"tap({x}, {y})"
            return json.dumps(paused)

        if not confirmed:
            warning = await _check_screen_safety(device)
            if warning:
                warning["blocked_action"] = f"tap({x}, {y})"
                warning["hint"] = "Tell the user what you see on the phone and ask if they want you to tap. Then call again with confirmed=True."
                return json.dumps(warning)

        result = await _adb("shell", "input", "tap", str(x), str(y), device=device)
        return json.dumps({"tapped": [x, y], "result": result or "ok"})

    @server.tool()
    async def sassy_phone_swipe(
        x1: int, y1: int, x2: int, y2: int,
        duration_ms: int = 300,
        device: str = "",
        confirmed: bool = False,
    ) -> str:
        """Swipe on the phone screen. duration_ms controls speed.

        Safety: auto-checks for sensitive contexts before swiping.
        """
        paused = _pause_check()
        if paused:
            paused["blocked_action"] = f"swipe({x1},{y1} -> {x2},{y2})"
            return json.dumps(paused)

        if not confirmed:
            warning = await _check_screen_safety(device)
            if warning:
                warning["blocked_action"] = f"swipe({x1},{y1} -> {x2},{y2})"
                warning["hint"] = "Describe the screen to the user and ask before swiping. Call with confirmed=True after."
                return json.dumps(warning)

        duration_ms = max(100, min(duration_ms, 5000))
        result = await _adb("shell", "input", "swipe",
                            str(x1), str(y1), str(x2), str(y2), str(duration_ms),
                            device=device)
        return json.dumps({"swiped": [[x1, y1], [x2, y2]], "duration_ms": duration_ms,
                           "result": result or "ok"})

    @server.tool()
    async def sassy_phone_type(text: str, device: str = "", confirmed: bool = False) -> str:
        """Type text on the phone. Requires a text field to be focused.

        Safety: auto-checks for login/auth contexts. Won't type into password
        fields or auth screens without confirmed=True from user approval.
        """
        paused = _pause_check()
        if paused:
            paused["blocked_action"] = f"type('{text[:20]}...')" if len(text) > 20 else f"type('{text}')"
            return json.dumps(paused)

        if not confirmed:
            warning = await _check_screen_safety(device)
            if warning:
                warning["blocked_action"] = f"type('{text[:20]}...')" if len(text) > 20 else f"type('{text}')"
                warning["hint"] = "The phone shows a sensitive screen. Describe it to the user and ask what to type. Call with confirmed=True after."
                return json.dumps(warning)

        # Escape special characters for ADB input text
        safe = text.replace("\\", "\\\\").replace(" ", "%s").replace("'", "\\'").replace('"', '\\"')
        safe = safe.replace("&", "\\&").replace("<", "\\<").replace(">", "\\>")
        safe = safe.replace("|", "\\|").replace(";", "\\;").replace("(", "\\(").replace(")", "\\)")
        result = await _adb("shell", "input", "text", safe, device=device)
        return json.dumps({"typed": text, "chars": len(text), "result": result or "ok"})

    @server.tool()
    async def sassy_phone_key(keycode: str, device: str = "") -> str:
        """Send a key event. Common keycodes: KEYCODE_HOME, KEYCODE_BACK,
        KEYCODE_ENTER, KEYCODE_VOLUME_UP, KEYCODE_VOLUME_DOWN, KEYCODE_POWER,
        KEYCODE_APP_SWITCH (recent apps), KEYCODE_MENU.
        """
        # Allow shorthand: "home" -> "KEYCODE_HOME"
        if not keycode.startswith("KEYCODE_"):
            keycode = f"KEYCODE_{keycode.upper()}"
        result = await _adb("shell", "input", "keyevent", keycode, device=device)
        return json.dumps({"key": keycode, "result": result or "ok"})

    @server.tool()
    async def sassy_phone_open(package: str, device: str = "") -> str:
        """Open an app by package name. Use sassy_adb_packages to find package names."""
        result = await _adb("shell", "monkey", "-p", package,
                            "-c", "android.intent.category.LAUNCHER", "1",
                            device=device)
        return json.dumps({"opened": package, "result": result or "ok"})

    # ── Scrcpy (retained for live mirroring) ─────────────────────

    @server.tool()
    async def sassy_scrcpy_start(device: str = "", max_size: int = 1024, no_audio: bool = True) -> str:
        """Start scrcpy screen mirroring."""
        global _scrcpy_proc
        scrcpy = _find_scrcpy()
        if not scrcpy:
            return "Error: scrcpy not found. Install from https://github.com/Genymobile/scrcpy/releases — extract and add to PATH."
        if _scrcpy_proc and _scrcpy_proc.returncode is None:
            return f"scrcpy already running (PID: {_scrcpy_proc.pid})"
        cmd = [scrcpy, "--max-size", str(max_size)]
        if no_audio:
            cmd.append("--no-audio")
        if device:
            cmd.extend(["-s", device])
        try:
            _scrcpy_proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            return f"scrcpy started (PID: {_scrcpy_proc.pid})"
        except FileNotFoundError:
            return "Error: scrcpy not found. Install from https://github.com/Genymobile/scrcpy/releases — extract and add to PATH."

    @server.tool()
    async def sassy_scrcpy_stop() -> str:
        """Stop scrcpy screen mirroring."""
        global _scrcpy_proc
        if _scrcpy_proc and _scrcpy_proc.returncode is None:
            _scrcpy_proc.terminate()
            await _scrcpy_proc.wait()
            _scrcpy_proc = None
            return "scrcpy stopped"
        return "scrcpy not running"

    # Register a shutdown hook so we don't leak a scrcpy subprocess when
    # the server exits. atexit fires after the event loop is gone, so we
    # use a sync terminate() rather than await wait() — the process is
    # already on its way out and we just need the device handle freed.
    import atexit as _atexit

    def _reap_scrcpy_on_exit():
        proc = _scrcpy_proc
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
        except Exception:
            pass
        # No await/wait here — the runtime is shutting down.

    _atexit.register(_reap_scrcpy_on_exit)

    @server.tool()
    async def sassy_scrcpy_record(output_path: str, device: str = "", time_limit: int = 30) -> str:
        """Record Android screen to file."""
        scrcpy = _find_scrcpy()
        if not scrcpy:
            return "Error: scrcpy not found. Install from https://github.com/Genymobile/scrcpy/releases — extract and add to PATH."
        cmd = [scrcpy, "--no-display", "--record", output_path,
               "--max-size", "1024", "--time-limit", str(time_limit)]
        if device:
            cmd.extend(["-s", device])
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await asyncio.wait_for(proc.communicate(), timeout=time_limit + 10)
            return f"Recording saved to {output_path}"
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return f"Recording may be at {output_path}"
        except FileNotFoundError:
            return "Error: scrcpy not found. Install from https://github.com/Genymobile/scrcpy/releases — extract and add to PATH."
