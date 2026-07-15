# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-K4PW45HCJUJA
"""Shared pytest fixtures + a safety net against leaked child processes.

Several tests spawn real OS subprocesses (terminal sessions, supervisor
children, concurrency workers). If a test fails or is interrupted before
its own cleanup runs, those children can linger and wedge a later test's
SQLite/WAL lock — the exact failure this project's supervisor exists to
prevent. The autouse fixture below force-kills any process that is still a
DESCENDANT of the pytest process after each test. It is intentionally
scoped to our own descendants (via psutil) so it can never touch an
unrelated process on the machine — a real sassymcp bridge, the user's
editor, etc.
"""
from __future__ import annotations

import pytest

try:
    import psutil
except Exception:  # pragma: no cover - psutil is a hard dep, but stay safe
    psutil = None


@pytest.fixture(autouse=True)
def _reap_leaked_children():
    yield
    if psutil is None:
        return
    try:
        survivors = psutil.Process().children(recursive=True)
    except Exception:
        return
    for child in survivors:
        try:
            child.kill()
        except Exception:
            pass
    if survivors:
        psutil.wait_procs(survivors, timeout=5)
