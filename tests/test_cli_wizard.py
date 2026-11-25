# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-WIZARDEOFTEST
"""Tests for the CLI wizard EOF handling (sassymcp._cli_wizard).

Audit C F8: _prompt used to swallow EOFError and return "", which sent the
menu loop into an infinite banner-print spin on closed stdin. It now returns
None and every consumer exits cleanly.

Run: pytest tests/test_cli_wizard.py
"""
import builtins
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sassymcp import _cli_wizard as wiz


def test_prompt_returns_none_on_eof(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a: (_ for _ in ()).throw(EOFError()))
    assert wiz._prompt("msg") is None


def test_prompt_returns_none_on_keyboard_interrupt(monkeypatch):
    def _raise(*a):
        raise KeyboardInterrupt()
    monkeypatch.setattr(builtins, "input", _raise)
    assert wiz._prompt("msg") is None


def test_prompt_default_still_works(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a: "")
    assert wiz._prompt("msg", default="dflt") == "dflt"
    monkeypatch.setattr(builtins, "input", lambda *a: "  hi  ")
    assert wiz._prompt("msg") == "hi"


def test_confirm_returns_none_on_eof(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a: (_ for _ in ()).throw(EOFError()))
    assert wiz._confirm("sure?") is None
    # and the call sites treat that as "don't proceed" (falsy), not "yes"
    assert not wiz._confirm("sure?", default=True)


def test_run_wizard_exits_on_eof(monkeypatch):
    calls = {"n": 0}

    def _eof(*a):
        calls["n"] += 1
        if calls["n"] > 3:
            # old behavior: the menu looped forever on EOF. Fail loudly
            # instead of hanging the suite.
            raise AssertionError("menu looped on EOF instead of exiting")
        raise EOFError()

    monkeypatch.setattr(builtins, "input", _eof)
    assert wiz.run_wizard() is None
    assert calls["n"] == 1  # one prompt, then a clean exit


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
