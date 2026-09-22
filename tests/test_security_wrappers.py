# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-WLMPZ6KD4V7T
# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
"""Regression tests for the extended _WRAPPER_CMDS (audit hardening, 2026-09-21).

detect_delete_intent() only recurses into shells listed in _WRAPPER_CMDS.
Before the fix, dash/ksh/fish/ash/iex payloads were invisible to the
delete interceptor AND to _sandbox_check_shell() (shell.py), which uses
detect_delete_intent() to confine identifiable delete targets to the jail —
so `dash -c "rm -rf /tmp/x"` ran unstaged and unjailed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from sassymcp.modules._security import _WRAPPER_CMDS, detect_delete_intent

WRAPPER_HITS = [
    # (command, expected keyword root)
    ('dash -c "rm -rf /tmp/x"', "rm"),
    ('fish -c "rm -rf /tmp/x"', "rm"),
    ('ksh -c "rm -rf /tmp/x"', "rm"),
    ('ash -c "rm -rf /tmp/x"', "rm"),
    ('iex "Remove-Item /tmp/x"', "remove-item"),
    ('Invoke-Expression "Remove-Item /tmp/x"', "remove-item"),
    # Nested: Invoke-Expression inside a PowerShell -c payload.
    ("powershell -c \"iex 'Remove-Item /tmp/x'\"", "remove-item"),
    ("pwsh -c \"iex 'Remove-Item /tmp/x'\"", "remove-item"),
]

WRAPPER_MISSES = [
    "dash --version",
    "fish -c 'echo hello'",
    "ksh -l",
    "iex",                      # bare iex recurses into nothing
    "ash -c 'echo hi > /tmp/x.log'",
]


@pytest.mark.parametrize("command,keyword", WRAPPER_HITS)
def test_wrapper_shells_recurse_into_delete_payloads(command, keyword):
    is_del, kw = detect_delete_intent(command)
    assert is_del, f"missed delete intent: {command!r}"
    assert kw == keyword, f"{command!r}: expected {keyword!r}, got {kw!r}"


@pytest.mark.parametrize("command", WRAPPER_MISSES)
def test_wrapper_shells_do_not_false_positive(command):
    is_del, kw = detect_delete_intent(command)
    assert not is_del, f"false positive ({kw!r}): {command!r}"


def test_new_wrappers_are_registered():
    for name in ("dash", "ksh", "fish", "ash"):
        assert _WRAPPER_CMDS[name] == {"-c"}
    # Invoke-Expression takes its payload positionally — empty flag set
    # means _scan_segment's "first positional token" path handles it.
    assert _WRAPPER_CMDS["iex"] == set()
    assert _WRAPPER_CMDS["invoke-expression"] == set()
