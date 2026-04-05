"""Vision - Desktop screen capture, OCR, dynamic monitoring, and visual analysis.

Provides screenshot capture with compression for MCP limits,
OCR via Tesseract, text-on-screen finding with coordinates,
per-window/per-region capture, and real-time screen monitoring
with change detection.

Dependencies: Pillow (required), pytesseract + Tesseract binary (optional for OCR).
"""

import asyncio
import base64
import io
import json
import time
from pathlib import Path


def _find_window_rect(title_substring: str):
    """Find a window by title substring, return (x, y, w, h) or None."""
    try:
        from pywinauto import Desktop
        desktop = Desktop(backend="uia")
        title_lower = title_substring.lower()
        for w in desktop.windows():
            try:
                if not w.is_visible():
                    continue
                wt = w.window_text()
                if wt and title_lower in wt.lower():
                    r = w.rectangle()
                    return (r.left, r.top, r.width(), r.height())
            except Exception:
                continue
    except ImportError:
        pass
    return None


def register(server):

    @server.tool()
    async def sassy_screen_capture(
        window_title: str = "",
        region: str = "",
        max_width: int = 1280,
        quality: int = 70,
        save_path: str = "",
    ) -> str:
        """Capture screen as compressed base64 JPEG for inline viewing.

        No args = full primary screen. window_title = capture specific window.
        region = "x,y,w,h" rectangle. max_width resizes for MCP size limits.
        quality = JPEG 1-100. save_path = also save to disk.
        Returns JSON with base64 image data and metadata.
        """
        import pyautogui
        from PIL import Image

        try:
            if window_title:
                rect = _find_window_rect(window_title)
                if rect is None:
                    return json.dumps({"error": f"Window not found: {window_title}"})
                img = pyautogui.screenshot(region=rect)
            elif region:
                parts = [int(x.strip()) for x in region.split(",")]
                if len(parts) != 4:
                    return json.dumps({"error": "Region must be x,y,w,h"})
                img = pyautogui.screenshot(region=tuple(parts))
            else:
                img = pyautogui.screenshot()

            orig_w, orig_h = img.size
            if orig_w > max_width:
                ratio = max_width / orig_w
                img = img.resize((max_width, int(orig_h * ratio)), Image.LANCZOS)

            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")

            if save_path:
                img.save(save_path)

            return json.dumps({
                "image_base64": b64,
                "format": "jpeg",
                "original_size": [orig_w, orig_h],
                "captured_size": list(img.size),
                "bytes": len(buf.getvalue()),
                "saved_to": save_path or None,
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    @server.tool()
    async def sassy_screen_ocr(
        window_title: str = "",
        region: str = "",
        language: str = "eng",
    ) -> str:
        """Screenshot + OCR in one call. Returns extracted text.

        window_title or region to scope. Auto-detects dark themes and inverts
        for better OCR accuracy. Requires pytesseract + Tesseract binary.
        """
        import pyautogui

        try:
            import pytesseract
        except ImportError:
            return json.dumps({"error": "pytesseract not installed. Install: pip install pytesseract — also requires Tesseract binary: https://github.com/tesseract-ocr/tesseract"})

        try:
            if window_title:
                rect = _find_window_rect(window_title)
                if rect is None:
                    return json.dumps({"error": f"Window not found: {window_title}"})
                img = pyautogui.screenshot(region=rect)
            elif region:
                parts = [int(x.strip()) for x in region.split(",")]
                img = pyautogui.screenshot(region=tuple(parts))
            else:
                img = pyautogui.screenshot()

            from PIL import ImageStat, ImageOps
            stat = ImageStat.Stat(img.convert("L"))
            dark = stat.mean[0] < 128
            ocr_img = ImageOps.invert(img.convert("RGB")) if dark else img

            text = pytesseract.image_to_string(ocr_img, lang=language)
            return json.dumps({
                "text": text.strip(),
                "lines": len([l for l in text.strip().split("\n") if l.strip()]),
                "dark_theme_detected": dark,
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    @server.tool()
    async def sassy_find_text_on_screen(
        search_text: str,
        window_title: str = "",
        click: bool = False,
        language: str = "eng",
    ) -> str:
        """Find text on screen via OCR and return coordinates. Optionally click it.

        search_text = case-insensitive substring. window_title scopes the search.
        click=True clicks center of found text. Returns absolute screen coordinates.
        """
        import pyautogui

        try:
            import pytesseract
        except ImportError:
            return json.dumps({"error": "pytesseract not installed. Install: pip install pytesseract — also requires Tesseract binary: https://github.com/tesseract-ocr/tesseract"})

        try:
            offset_x, offset_y = 0, 0
            if window_title:
                rect = _find_window_rect(window_title)
                if rect is None:
                    return json.dumps({"error": f"Window not found: {window_title}"})
                img = pyautogui.screenshot(region=rect)
                offset_x, offset_y = rect[0], rect[1]
            else:
                img = pyautogui.screenshot()

            from PIL import ImageStat, ImageOps
            stat = ImageStat.Stat(img.convert("L"))
            ocr_img = ImageOps.invert(img.convert("RGB")) if stat.mean[0] < 128 else img

            data = pytesseract.image_to_data(ocr_img, lang=language, output_type=pytesseract.Output.DICT)

            search_lower = search_text.lower()
            matches = []
            for i, word in enumerate(data["text"]):
                if search_lower in word.lower():
                    x = data["left"][i] + offset_x
                    y = data["top"][i] + offset_y
                    w = data["width"][i]
                    h = data["height"][i]
                    cx, cy = x + w // 2, y + h // 2
                    matches.append({"text": word, "x": x, "y": y, "w": w, "h": h,
                                    "center_x": cx, "center_y": cy})

            if not matches:
                full_text = " ".join(data["text"]).lower()
                if search_lower in full_text:
                    idx = full_text.find(search_lower)
                    char_count = 0
                    for i, word in enumerate(data["text"]):
                        char_count += len(word) + 1
                        if char_count > idx and data["width"][i] > 0:
                            x = data["left"][i] + offset_x
                            y = data["top"][i] + offset_y
                            w = data["width"][i]
                            h = data["height"][i]
                            cx, cy = x + w // 2, y + h // 2
                            matches.append({"text": search_text, "x": x, "y": y,
                                            "w": w, "h": h, "center_x": cx, "center_y": cy})
                            break

            if not matches:
                return json.dumps({"found": False, "search": search_text})

            result = {"found": True, "matches": matches, "count": len(matches)}

            if click and matches:
                m = matches[0]
                pyautogui.click(m["center_x"], m["center_y"])
                result["clicked"] = {"x": m["center_x"], "y": m["center_y"]}

            return json.dumps(result)
        except Exception as e:
            return json.dumps({"error": str(e)})

    @server.tool()
    async def sassy_screen_region(
        x: int, y: int, width: int, height: int,
        max_width: int = 1024,
        quality: int = 80,
    ) -> str:
        """Capture a specific screen region. Returns compressed base64 JPEG.

        Useful for zooming into UI elements, error dialogs, etc.
        """
        import pyautogui
        from PIL import Image

        try:
            img = pyautogui.screenshot(region=(x, y, width, height))
            orig_w, orig_h = img.size
            if orig_w > max_width:
                ratio = max_width / orig_w
                img = img.resize((max_width, int(orig_h * ratio)), Image.LANCZOS)

            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")

            return json.dumps({
                "image_base64": b64,
                "format": "jpeg",
                "region": [x, y, width, height],
                "captured_size": list(img.size),
                "bytes": len(buf.getvalue()),
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    # ── Dynamic Desktop Vision ───────────────────────────────────

    def _capture_grayscale(region=None, max_width=640, quality=20):
        """Capture a low-res grayscale JPEG. Returns (PIL.Image, bytes, b64_str)."""
        import pyautogui
        from PIL import Image

        img = pyautogui.screenshot(region=region)
        orig_w, orig_h = img.size
        if orig_w > max_width:
            ratio = max_width / orig_w
            img = img.resize((max_width, int(orig_h * ratio)), Image.LANCZOS)
        gray = img.convert("L")
        buf = io.BytesIO()
        gray.save(buf, format="JPEG", quality=quality, optimize=True)
        raw = buf.getvalue()
        return gray, raw, base64.b64encode(raw).decode("ascii")

    def _image_diff_percent(img_a, img_b):
        """Calculate percentage of pixels that differ significantly between two grayscale images."""
        from PIL import ImageChops
        if img_a.size != img_b.size:
            return 100.0
        diff = ImageChops.difference(img_a, img_b)
        pixels = list(diff.getdata())
        total = len(pixels)
        if total == 0:
            return 0.0
        # Count pixels that changed by more than 15 intensity levels (out of 255)
        changed = sum(1 for p in pixels if p > 15)
        return round(changed / total * 100, 1)

    @server.tool()
    async def sassy_screen_glance(
        window_title: str = "",
        region: str = "",
        max_width: int = 640,
        quality: int = 20,
    ) -> str:
        """Fast low-res grayscale capture optimized for AI vision. ~3-6KB per frame.

        Much smaller than sassy_screen_capture. Designed for frequent polling —
        call this repeatedly to "watch" what's happening on screen.
        """
        try:
            capture_region = None
            if window_title:
                capture_region = _find_window_rect(window_title)
                if capture_region is None:
                    return json.dumps({"error": f"Window not found: {window_title}"})
            elif region:
                parts = [int(x.strip()) for x in region.split(",")]
                if len(parts) != 4:
                    return json.dumps({"error": "Region must be x,y,w,h"})
                capture_region = tuple(parts)

            gray, raw, b64 = _capture_grayscale(capture_region, max_width, quality)
            return json.dumps({
                "image_base64": b64,
                "format": "grayscale_jpeg",
                "size": list(gray.size),
                "bytes": len(raw),
                "timestamp": time.time(),
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    @server.tool()
    async def sassy_screen_watch(
        seconds: float = 5.0,
        interval: float = 0.5,
        window_title: str = "",
        region: str = "",
        change_threshold: float = 2.0,
        max_frames: int = 10,
        max_width: int = 480,
        quality: int = 15,
    ) -> str:
        """Monitor screen for changes over a duration. Returns only frames where content changed.

        Captures grayscale frames at interval, compares consecutive frames,
        and returns only those exceeding change_threshold (% pixels changed).
        First frame is always included. Optimized for minimal context usage.

        seconds: how long to monitor (1-30)
        interval: seconds between captures (0.3-5.0)
        change_threshold: minimum % pixel change to include frame (0-100)
        max_frames: cap on returned frames to protect context budget
        max_width: resolution (lower = smaller, 320-800)
        quality: JPEG quality (5-50, lower = smaller)
        """
        seconds = max(1.0, min(seconds, 30.0))
        interval = max(0.3, min(interval, 5.0))
        max_frames = max(1, min(max_frames, 20))
        max_width = max(320, min(max_width, 800))
        quality = max(5, min(quality, 50))

        capture_region = None
        if window_title:
            capture_region = _find_window_rect(window_title)
            if capture_region is None:
                return json.dumps({"error": f"Window not found: {window_title}"})
        elif region:
            parts = [int(x.strip()) for x in region.split(",")]
            if len(parts) != 4:
                return json.dumps({"error": "Region must be x,y,w,h"})
            capture_region = tuple(parts)

        try:
            frames = []
            prev_img = None
            total_captured = 0
            start_time = time.time()

            while time.time() - start_time < seconds and len(frames) < max_frames:
                gray, raw, b64 = _capture_grayscale(capture_region, max_width, quality)
                total_captured += 1
                elapsed = round(time.time() - start_time, 2)

                if prev_img is None:
                    # Always include first frame
                    frames.append({
                        "frame": len(frames) + 1,
                        "elapsed_s": elapsed,
                        "change_pct": 0.0,
                        "bytes": len(raw),
                        "image_base64": b64,
                    })
                else:
                    diff_pct = _image_diff_percent(prev_img, gray)
                    if diff_pct >= change_threshold:
                        frames.append({
                            "frame": len(frames) + 1,
                            "elapsed_s": elapsed,
                            "change_pct": diff_pct,
                            "bytes": len(raw),
                            "image_base64": b64,
                        })

                prev_img = gray
                await asyncio.sleep(interval)

            total_bytes = sum(f["bytes"] for f in frames)
            return json.dumps({
                "frames": frames,
                "total_captured": total_captured,
                "frames_with_changes": len(frames),
                "duration_s": round(time.time() - start_time, 2),
                "total_bytes": total_bytes,
                "settings": {
                    "interval": interval,
                    "threshold": change_threshold,
                    "resolution": max_width,
                },
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    @server.tool()
    async def sassy_screen_diff(
        window_title: str = "",
        region: str = "",
        wait_seconds: float = 2.0,
        max_width: int = 640,
    ) -> str:
        """Capture before/after screenshots and show what changed.

        Takes a frame now, waits wait_seconds, takes another frame.
        Returns both frames plus a diff highlighting changed regions.
        Useful for verifying an action had the expected visual effect.
        """
        from PIL import ImageChops, ImageFilter

        capture_region = None
        if window_title:
            capture_region = _find_window_rect(window_title)
            if capture_region is None:
                return json.dumps({"error": f"Window not found: {window_title}"})
        elif region:
            parts = [int(x.strip()) for x in region.split(",")]
            if len(parts) != 4:
                return json.dumps({"error": "Region must be x,y,w,h"})
            capture_region = tuple(parts)

        try:
            # Before
            before_img, before_raw, before_b64 = _capture_grayscale(capture_region, max_width, 25)

            await asyncio.sleep(max(0.5, min(wait_seconds, 30.0)))

            # After
            after_img, after_raw, after_b64 = _capture_grayscale(capture_region, max_width, 25)

            # Diff visualization — amplify differences
            diff_img = ImageChops.difference(before_img, after_img)
            # Boost contrast on diff so changes are visible
            diff_img = diff_img.point(lambda x: min(255, x * 5))
            # Blur slightly to group nearby changes
            diff_img = diff_img.filter(ImageFilter.GaussianBlur(radius=2))

            diff_buf = io.BytesIO()
            diff_img.save(diff_buf, format="JPEG", quality=30)
            diff_b64 = base64.b64encode(diff_buf.getvalue()).decode("ascii")

            change_pct = _image_diff_percent(before_img, after_img)

            return json.dumps({
                "before": {"image_base64": before_b64, "bytes": len(before_raw)},
                "after": {"image_base64": after_b64, "bytes": len(after_raw)},
                "diff": {"image_base64": diff_b64, "bytes": len(diff_buf.getvalue())},
                "change_percent": change_pct,
                "wait_seconds": wait_seconds,
                "changed": change_pct > 1.0,
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    @server.tool()
    async def sassy_list_windows(include_hidden: bool = False) -> str:
        """List all visible windows with title, process, position, and size.

        Enhanced version of desktop_state with process info.
        """
        try:
            from pywinauto import Desktop
            import psutil
        except ImportError:
            return json.dumps({"error": "pywinauto or psutil not installed"})

        try:
            desktop = Desktop(backend="uia")
            windows = []
            for w in desktop.windows():
                try:
                    if not include_hidden and not w.is_visible():
                        continue
                    title = w.window_text()
                    if not title:
                        continue
                    rect = w.rectangle()
                    pid = w.process_id()
                    proc_name = ""
                    try:
                        proc_name = psutil.Process(pid).name()
                    except Exception:
                        pass
                    windows.append({
                        "title": title,
                        "process": proc_name,
                        "pid": pid,
                        "left": rect.left,
                        "top": rect.top,
                        "width": rect.width(),
                        "height": rect.height(),
                        "visible": w.is_visible(),
                    })
                except Exception:
                    continue
            return json.dumps(windows, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)})
