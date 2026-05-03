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

  Windows: msvcrt.locking acquires a byte-range lock on the file. We
           hold it for the duration of the write+flush+fsync.
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
            # Lock at position 0 for the entire file to serialize all appends
            locked = False
            try:
                f.seek(0)
                # Lock a reasonably large range (1GB fits in signed 32-bit)
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, (1 << 30) - 1)
                locked = True
            except OSError:
                # Lock acquisition failed under heavy contention. Proceed
                # unlocked rather than dropping the audit entry — losing
                # one entry is worse than one possibly-interleaved entry.
                pass

            try:
                f.seek(0, 2)  # Seek to end after locking
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            finally:
                if locked:
                    try:
                        f.seek(0)
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, (1 << 30) - 1)
                    except OSError:
                        pass
    else:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, blob)
        finally:
            os.close(fd)
