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

  POSIX: O_APPEND on the file descriptor. The kernel guarantees that
         every write() to an O_APPEND fd does the seek-to-end + the
         actual write atomically against other O_APPEND writers.

  Windows: msvcrt.locking acquires a byte-range lock on a fixed byte
           (position 0) so every concurrent writer contends on the same
           lock. Locking at "current EOF" after an "ab" open does NOT
           serialize across processes: each process has its own view of
           EOF when opened, so the locks land on different bytes and
           never collide. Position 0 is fixed across processes — the
           lock IS the serialization point. The seek-to-end and write
           happen inside the critical section. msvcrt allows locking
           ranges beyond EOF, so this works on empty files.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def append_audit(path: Path, entry: dict[str, Any]) -> None:
    """Append `entry` as a single JSON line to `path` atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = (json.dumps(entry) + "\n").encode("utf-8")

    if os.name == "nt":
        import msvcrt

        with open(path, "ab") as f:
            # Lock a single byte at a FIXED position (0) so every concurrent
            # writer contends on the same lock. Locking at the current EOF
            # would NOT serialize correctly: each process's "ab" open positions
            # the cursor at its own view of EOF, which can drift between
            # processes when writes are mid-flight, so locks at "EOF" can land
            # on different bytes and never collide. Position 0 is fixed across
            # processes — the lock IS the serialization point.
            #
            # msvcrt.locking can lock ranges beyond EOF as virtual byte-range
            # locks, so this works on a brand-new empty file.
            locked = False
            try:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                locked = True
            except OSError:
                # Lock acquisition failed under heavy contention. Proceed
                # unlocked rather than dropping the audit entry — losing
                # one entry is worse than one possibly-interleaved entry.
                pass

            try:
                f.seek(0, 2)  # seek to end inside the critical section
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            finally:
                if locked:
                    try:
                        f.seek(0)
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
    else:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, blob)
        finally:
            os.close(fd)
