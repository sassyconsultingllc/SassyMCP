"""Atomic file-write helpers for SassyMCP shared state files.

Multiple sassymcp.exe processes write the same JSON config files
(config.json, license.json, tokens.json, persona.md). Without atomic
writes, two simultaneous writers can leave the file in a torn,
half-written state — JSONDecodeError on the next read.

Strategy: write to a temp file in the same directory, then os.replace()
onto the target. os.replace is atomic on POSIX and on Windows since
Vista. The temp file lives in the same dir to guarantee same-filesystem,
otherwise os.replace falls back to copy+unlink which is not atomic.

Last-write-wins semantics still apply — concurrent writes will land one
full payload, the others are lost. That's fine for these files; truly
simultaneous writes are vanishingly rare.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, data: Any, *, indent: int = 2) -> None:
    """Write `data` as JSON to `path` atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent)
        _atomic_replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, content: str) -> None:
    """Write `content` as UTF-8 text to `path` atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        _atomic_replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _atomic_replace(src: str, dst: Path, max_retries: int = 200, retry_delay: float = 0.01) -> None:
    """Replace dst with src, retrying on Windows lock contention."""
    for attempt in range(max_retries):
        try:
            os.replace(src, dst)
            return
        except (PermissionError, OSError) as e:
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                raise
