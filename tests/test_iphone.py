# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-ODDWKJJ5XGAO
# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
"""Tests for the experimental iPhone (libimobiledevice) module.

These tests never need a real iPhone or libimobiledevice installed:
binaries are monkeypatched away, so they pass on any host (including
CI machines with no iOS tooling at all).
"""
from __future__ import annotations

import asyncio

import pytest

import sassymcp.modules.iphone as iphone


@pytest.fixture()
def server():
    class _FakeServer:
        def __init__(self):
            self.tools = {}

        def tool(self):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn

            return deco

    s = _FakeServer()
    iphone.register(s)
    return s


def _no_binaries(monkeypatch):
    """Simulate a host with no libimobiledevice binaries installed."""
    monkeypatch.setattr(iphone.shutil, "which", lambda name, *a, **k: None)
    monkeypatch.setattr(iphone, "_EXTRA_PATH_DIRS", [])


def test_module_imports_cleanly():
    assert iphone.__name__ == "sassymcp.modules.iphone"
    assert callable(iphone.register)


def test_register_exposes_exactly_six_tools(server):
    assert sorted(server.tools) == sorted(
        [
            "sassy_iphone_list",
            "sassy_iphone_info",
            "sassy_iphone_screenshot",
            "sassy_iphone_syslog",
            "sassy_iphone_apps",
            "sassy_iphone_install",
        ]
    )


def test_missing_binary_returns_install_hint_not_traceback(server, monkeypatch):
    """Availability gating: missing binaries -> Error + per-OS install hint."""
    _no_binaries(monkeypatch)
    for name, fn in server.tools.items():
        args = {}
        if "ipa_path" in fn.__code__.co_varnames:
            args["ipa_path"] = "/tmp/app.ipa"
        if name == "sassy_iphone_install":
            args["confirm"] = "YES"  # pass the gate; fail on the binary
        try:
            result = asyncio.run(fn(**args))
        except Exception as e:  # pragma: no cover - must never happen
            pytest.fail(f"{name} raised {type(e).__name__}: {e}")
        assert result.startswith("Error:"), f"{name}: {result!r}"
        assert "libimobiledevice" in result, f"{name}: {result!r}"
        assert "brew install libimobiledevice" in result or "apt install" in result, (
            f"{name}: missing per-OS install hint: {result!r}"
        )


def test_install_refused_without_confirm(server):
    """confirm gate: anything but confirm='YES' is refused before any work."""
    fn = server.tools["sassy_iphone_install"]
    for bad in ("", "yes", "Yes", "NO", "CONFIRM"):
        result = asyncio.run(fn("/tmp/app.ipa", confirm=bad))
        assert result.startswith("Refused:"), result
        assert "confirm='YES'" in result
    # Gate fires even when binaries are absent and the ipa is missing:
    # nothing is executed, no traceback, no device touched.


def test_install_gate_precedes_ipa_validation(server):
    fn = server.tools["sassy_iphone_install"]
    result = asyncio.run(fn("/does/not/exist.ipa", confirm=""))
    assert result.startswith("Refused:")


def test_install_rejects_non_ipa(server, monkeypatch, tmp_path):
    """With binaries faked present, a non-.ipa path is rejected as an Error."""
    monkeypatch.setattr(iphone, "_find_binary", lambda name: f"/usr/bin/{name}")
    fn = server.tools["sassy_iphone_install"]
    result = asyncio.run(fn(str(tmp_path / "app.zip"), confirm="YES"))
    assert result.startswith("Error:") and ".ipa" in result


def test_invalid_udid_rejected(server, monkeypatch):
    """Bad UDIDs fail validation with an Error string, never a traceback."""
    monkeypatch.setattr(iphone, "_find_binary", lambda name: f"/usr/bin/{name}")
    fn = server.tools["sassy_iphone_info"]
    result = asyncio.run(fn(udid="not a udid!!"))
    assert result.startswith("Error: invalid UDID"), result


def test_multiple_devices_require_udid(server, monkeypatch):
    """Two connected iPhones and no udid -> clear pick-one error."""
    monkeypatch.setattr(iphone, "_find_binary", lambda name: f"/usr/bin/{name}")

    async def fake_run(binary, *args, timeout=30):
        return "AAAABBBBCCCCDDDDEEEEFFFF0000111122223333\n9999888877776666555544443333222211110000\n"

    monkeypatch.setattr(iphone, "_run", fake_run)
    fn = server.tools["sassy_iphone_info"]
    result = asyncio.run(fn())
    assert result.startswith("Error: multiple iPhones connected"), result
    assert "AAAABBBBCCCCDDDDEEEEFFFF0000111122223333" in result
    assert "udid=" in result


def test_single_device_auto_selected(server, monkeypatch):
    """One connected iPhone -> -u is passed automatically."""
    monkeypatch.setattr(iphone, "_find_binary", lambda name: f"/usr/bin/{name}")
    seen = {}

    async def fake_run(binary, *args, timeout=30):
        if binary == "idevice_id":
            return "AAAABBBBCCCCDDDDEEEEFFFF0000111122223333\n"
        seen["binary"] = binary
        seen["args"] = args
        return "DeviceName: Test iPhone"

    monkeypatch.setattr(iphone, "_run", fake_run)
    result = asyncio.run(server.tools["sassy_iphone_info"]())
    assert "DeviceName" in result
    assert seen["args"][:2] == ("-u", "AAAABBBBCCCCDDDDEEEEFFFF0000111122223333")


def test_no_device_mentions_pairing(server, monkeypatch):
    """Zero devices -> Error with the iOS 17+ pairing requirement."""
    monkeypatch.setattr(iphone, "_find_binary", lambda name: f"/usr/bin/{name}")

    async def fake_run(binary, *args, timeout=30):
        return ""

    monkeypatch.setattr(iphone, "_run", fake_run)
    result = asyncio.run(server.tools["sassy_iphone_list"]())
    assert result.startswith("Error: no iPhone found"), result
    assert "idevicepair pair" in result
    assert "iOS 17" in result


def test_pairing_hint_appended_on_no_device_output(server, monkeypatch):
    """A 'No device found' failure from the binary gains the pairing hint."""
    monkeypatch.setattr(iphone, "_find_binary", lambda name: f"/usr/bin/{name}")
    calls = {"n": 0}

    async def fake_run(binary, *args, timeout=30):
        if binary == "idevice_id":
            calls["n"] += 1
            return "AAAABBBBCCCCDDDDEEEEFFFF0000111122223333\n"
        return "Error (exit 1): No device found, is it plugged in?"

    monkeypatch.setattr(iphone, "_run", fake_run)
    result = asyncio.run(server.tools["sassy_iphone_apps"]())
    assert "No device found" in result
    assert "idevicepair pair" in result


def test_syslog_bounds_respected(server, monkeypatch):
    """syslog: lines clamped to 1..5000, never unbounded."""
    monkeypatch.setattr(iphone, "_find_binary", lambda name: f"/usr/bin/{name}")

    captured = {}

    class _FakeProc:
        def __init__(self, payload: bytes):
            self._payload = payload
            self.killed = False

        async def wait(self):
            return 0

        async def communicate(self):
            return self._payload, b""

        def kill(self):
            self.killed = True

    async def fake_spawn(path, *args, **kwargs):
        captured["args"] = args
        return _FakeProc(b"\n".join(f"line {i}".encode() for i in range(7000)))

    monkeypatch.setattr(iphone.asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setattr(iphone.asyncio, "wait_for", _real_wait_for(iphone.asyncio))

    async def fake_udid_args(udid):
        return ["-u", "SOMEUDID01"], None

    monkeypatch.setattr(iphone, "_udid_args", fake_udid_args)

    # lines=100000 must clamp to 5000
    result = asyncio.run(server.tools["sassy_iphone_syslog"](lines=100000, timeout=1))
    out_lines = [ln for ln in result.splitlines() if ln.startswith("line ")]
    assert len(out_lines) == 5000, f"got {len(out_lines)} lines"
    assert out_lines[0] == "line 2000"  # tail of the 7000-line capture
    assert "showing last 5000" in result


def _real_wait_for(asyncio_mod):
    real = asyncio_mod.wait_for

    async def _wrap(awaitable, timeout=None):
        return await real(awaitable, timeout=timeout)

    return _wrap


def test_syslog_empty_capture_errors_gracefully(server, monkeypatch):
    monkeypatch.setattr(iphone, "_find_binary", lambda name: f"/usr/bin/{name}")

    class _FakeProc:
        async def wait(self):
            return 0

        async def communicate(self):
            return b"", b"No device found"

        def kill(self):
            pass

    async def fake_spawn(path, *args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(iphone.asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setattr(iphone.asyncio, "wait_for", _real_wait_for(iphone.asyncio))

    async def fake_udid_args(udid):
        return ["-u", "SOMEUDID01"], None

    monkeypatch.setattr(iphone, "_udid_args", fake_udid_args)
    result = asyncio.run(server.tools["sassy_iphone_syslog"](timeout=1))
    assert result.startswith("Error:"), result
    assert "idevicepair pair" in result
