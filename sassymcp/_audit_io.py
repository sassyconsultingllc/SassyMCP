# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-KDJHO5NKNLX6
"""Single-writer-safe JSON-line append for the SassyMCP audit log.

Today every audit-log call does:

    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\\n")

Under concurrent processes on Windows, f.write() of a blob larger than
the OS internal buffer can interleave with another process's write — you
end up with `{"a":1,"b":` from one entry followed by `{"c":2}\\n{"d":3}\\n`
from another, and json.loads chokes when reading the log later.

Fix: serialise the whole entry into one bytes blob and emit it in a
single OS-level write that is guaranteed atomic w.r.t. other processes.

  POSIX: O_APPEND on the file descriptor. Linux holds the inode lock
         across the seek-to-end + write, making the operation atomic
         against other O_APPEND writers in practice. Formal POSIX only
         guarantees this for writes ≤ PIPE_BUF on pipes; the regular-file
         case is an implementation property of Linux (and most other
         POSIX kernels). For the audit-log workload (entries up to a
         few MB) this guarantee holds reliably.

  Windows: msvcrt.locking acquires a byte-range lock on a fixed byte
           (position 0) so every concurrent writer contends on the same
           lock. Locking at "current EOF" after an "ab" open does NOT
           serialize across processes: each process has its own view of
           EOF when opened, so the locks land on different bytes and
           never collide. Position 0 is fixed across processes — the
           lock IS the serialization point. The seek-to-end and write
           happen inside the critical section. msvcrt allows locking
           ranges beyond EOF, so this works on empty files.

Windows fsync asymmetry: the Windows branch calls os.fsync after every
write to guarantee durability of the audit entry. POSIX does not — recent
entries on POSIX may be in the page cache without being flushed if the
process crashes. This is intentional: Windows file caches can hold
seconds of writes before flushing, which is unacceptable for a forensic
audit log; Linux's writeback is more aggressive (~5s default) so the
asymmetry mostly manifests on Windows.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

# Windows-only lock-acquisition retry budget. msvcrt's LK_LOCK has a 10s
# internal timeout and raises OSError("Resource deadlock avoided") on
# expiry, which under 8-process × ~66ms-per-write contention does trip
# empirically (1 in 5 runs of the concurrent stress test). Falling through
# to an unlocked write would corrupt the log — it would race with concurrent
# locked writes and produce exactly the interleaved bytes this module exists
# to prevent. Instead use LK_NBLCK (non-blocking, fails fast with EACCES on
# contention) inside our own retry loop, so we control the budget and never
# write unlocked. 100×50ms = 5s total — generous enough to ride out an
# 8-process queue (worst-case ~7×100ms wait), short enough to surface true
# deadlocks within a test timeout.
_LOCK_MAX_RETRIES = 100
_LOCK_RETRY_DELAY = 0.05


def append_audit(path: Path, entry: dict[str, Any]) -> None:
    """Append `entry` as a single JSON line to `path` atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = (json.dumps(entry) + "\n").encode("utf-8")

    if os.name == "nt":
        import msvcrt

        with open(path, "ab") as f:
            # Lock a single byte at a FIXED position (0) so every concurrent
            # writer contends on the same lock. Locking at the current EOF
            # would NOT serialize correctly: the lock position diverges from
            # the write position after the lock is acquired (write happens
            # at end-of-file, which moves; the lock stays where it was set),
            # so two locks taken at "EOF" can land on different bytes once
            # data is mid-flight. Position 0 is fixed across processes — the
            # lock IS the serialization point.
            #
            # msvcrt.locking can lock ranges beyond EOF as virtual byte-range
            # locks, so this works on a brand-new empty file.
            #
            # If acquisition still fails after the full retry budget, the
            # OSError propagates. The caller in audit.py wraps in try/except
            # OSError to drop the entry — better to drop than to write
            # unlocked (which would race with concurrent locked writes and
            # produce the interleaved-byte corruption this module exists to
            # prevent — it is not a graceful-degradation path).
            f.seek(0)
            for attempt in range(_LOCK_MAX_RETRIES):
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if attempt < _LOCK_MAX_RETRIES - 1:
                        time.sleep(_LOCK_RETRY_DELAY)
                        continue
                    raise
            try:
                f.seek(0, 2)  # seek to end inside the critical section
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, blob)
        finally:
            os.close(fd)
