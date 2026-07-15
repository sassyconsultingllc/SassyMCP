# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-IIDQLSPM6WR2
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

On Windows, os.replace() can race against any process that briefly opens
the destination file (AV scanners, search indexers, another concurrent
sassymcp atomic_write call) and raise PermissionError [WinError 5]. The
_replace_with_retry() helper retries up to 50 times at 10ms intervals (500ms
budget total) to ride that out. POSIX is unaffected — its os.replace
is genuinely atomic w.r.t. concurrent writers.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


# Windows-only retry budget. On POSIX os.replace is genuinely atomic w.r.t.
# concurrent writers and never raises EACCES; on Windows it can race against
# any process that briefly opens dst (AV scanners, file indexers, another
# sassymcp.exe doing its own atomic_write at the same instant), surfacing as
# PermissionError [WinError 5]. Empirically this race triggers on every run
# of the 8-subprocess concurrent stress test without a retry. 50×10ms = 500ms
# is enough budget to ride out an AV scan tick or a competing writer; longer
# budgets just hide deadlocks behind silent waits.
_REPLACE_MAX_RETRIES = 50
_REPLACE_RETRY_DELAY = 0.01


def _replace_with_retry(src: str, dst: Path) -> None:
    """Retry-aware wrapper around os.replace(src, dst).

    See module-level comment above for justification of the retry budget.
    Catches ONLY PermissionError, not every OSError — a real ENOENT or
    cross-device-link failure should surface immediately, not be retried.
    """
    for attempt in range(_REPLACE_MAX_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt < _REPLACE_MAX_RETRIES - 1:
                time.sleep(_REPLACE_RETRY_DELAY)
                continue
            raise


def atomic_write_json(path: Path, data: Any, *, indent: int = 2) -> None:
    """Write `data` as JSON to `path` atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent)
        _replace_with_retry(tmp, path)
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
        _replace_with_retry(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
