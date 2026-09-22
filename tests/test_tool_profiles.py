# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-KRQ5JGPQFHRA
"""Tests for per-session tool profiles.

Covers sassymcp.modules.tool_profiles (profile definitions, activation,
list/call enforcement, confirm gate) and the Control Panel /api/profile
routes (dashboard-only switching, audit logging).

The enforcement chokepoint is verified against the REAL FastMCP
ToolManager from the installed `mcp` package: both list_tools() and
call_tool() resolve through `ToolManager._tools`, so swapping that dict
gates both paths. Dummy tools are mapped to real loader modules so group
resolution is exercised end to end without importing the full server.

Run: ~/workspace/sassymcp-analysis/venv/bin/python -m pytest tests/test_tool_profiles.py
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.tools.tool_manager import ToolManager

from sassymcp import tool_annotations as ta
from sassymcp import tool_descriptions as td
from sassymcp.modules import _tool_loader as tl
from sassymcp.modules import tool_profiles as tp


# name -> real loader module (=> real group via _MODULE_TO_GROUP)
_DUMMY_TOOLS = [
    ("sassy_tp_shell", "shell"),            # core
    ("sassy_tp_read", "fileops"),           # core
    ("sassy_tp_gh", "github_ops"),          # github_full
    ("sassy_tp_audit", "security_audit"),   # forensics
    ("sassy_tp_mem", "memory"),             # memory
    ("sassy_tp_meta", "meta"),              # meta
    ("sassy_tp_linux", "linux"),            # linux
    ("sassy_tp_adb", "adb"),                # android
    ("sassy_tp_iphone", "iphone"),          # iphone
    ("sassy_tp_eventlog", "eventlog"),      # system
    ("sassy_tp_updater", "updater"),        # updater
]


def _make_manager() -> ToolManager:
    tm = ToolManager()
    for name, _module in _DUMMY_TOOLS:
        def _fn(name=name):
            return {"tool": name}
        _fn.__name__ = name
        tm.add_tool(_fn, name=name)
    return tm


@pytest.fixture
def tm():
    """Fresh ToolManager with dummy tools mapped to real groups."""
    tp.reset()
    for name, module in _DUMMY_TOOLS:
        tl.register_tool_group(name, module)
    manager = _make_manager()
    yield manager
    for name, _module in _DUMMY_TOOLS:
        tl._TOOL_TO_GROUP.pop(name, None)
    tp.reset()


def _listed(tm: ToolManager) -> set[str]:
    return {t.name for t in tm.list_tools()}


def _call(tm: ToolManager, name: str):
    return asyncio.run(tm.call_tool(name, {}))


# ── definitions ────────────────────────────────────────────────────────

def test_profiles_reference_real_groups():
    for name, prof in tp.PROFILES.items():
        groups = prof.get("groups")
        if groups is None:
            continue
        unknown = [g for g in groups if g not in tl.TOOL_GROUPS]
        assert not unknown, f"profile {name}: unknown groups {unknown}"


def test_expected_profile_names():
    assert sorted(tp.PROFILES) == [
        "custom", "developer", "devices", "forensics", "full",
        "readonly", "sysadmin",
    ]


def test_forensics_group_is_security_audit_plus_registry():
    assert tl.TOOL_GROUPS["forensics"]["modules"] == ["security_audit", "registry"]
    assert "forensics" in tp.PROFILES["forensics"]["groups"]
    # eventlog rides along via the system group
    assert "system" in tp.PROFILES["forensics"]["groups"]
    assert "eventlog" in tl.TOOL_GROUPS["system"]["modules"]


def test_module_registers_no_mcp_tools():
    # The privilege boundary: profiles are human-surface only. This module
    # must not expose any MCP tool that could switch profiles.
    assert not hasattr(tp, "register")
    assert [n for n in dir(tp) if n.startswith("sassy_")] == []
    src = Path(tp.__file__).read_text()
    assert "@server.tool" not in src
    assert "@mcp.tool" not in src


def test_metadata_coverage_unchanged():
    assert len(td.TOOL_DESCRIPTIONS) == 278
    assert len(ta.TOOL_ANNOTATIONS) == 278


def test_readonly_membership_matches_annotations():
    ros = tp._read_only_tools()
    assert len(ros) == 154  # pinned to the curated 1.16.0 count
    assert all(ta.TOOL_ANNOTATIONS[n].get("readOnlyHint") for n in ros)
    # destructive / mutating tools must not be in the safe set
    assert "sassy_shell" not in ros
    assert "sassy_write_file" not in ros
    assert "sassy_batch" not in ros


# ── activation & enforcement ───────────────────────────────────────────

def test_default_is_full(tm):
    assert tp.get_active() == "full"
    tp.install(tm)
    assert _listed(tm) == {n for n, _ in _DUMMY_TOOLS}


def test_activation_filters_listing(tm):
    tp.install(tm)
    tp.activate_profile("forensics", tm)
    assert _listed(tm) == {
        "sassy_tp_shell", "sassy_tp_read",      # core
        "sassy_tp_audit",                        # forensics
        "sassy_tp_eventlog",                     # system
        "sassy_tp_mem",                          # memory
        "sassy_tp_meta",                         # meta (always-on)
    }
    assert "sassy_tp_gh" not in _listed(tm)
    assert "sassy_tp_linux" not in _listed(tm)


def test_call_hidden_tool_fails(tm):
    tp.install(tm)
    tp.activate_profile("devices", tm)
    assert "sassy_tp_gh" not in _listed(tm)
    with pytest.raises(ToolError):
        _call(tm, "sassy_tp_gh")


def test_call_visible_tool_succeeds(tm):
    tp.install(tm)
    tp.activate_profile("devices", tm)
    result = _call(tm, "sassy_tp_adb")
    assert result == {"tool": "sassy_tp_adb"}


def test_reactivation_restores_full(tm):
    tp.install(tm)
    tp.activate_profile("devices", tm)
    tp.activate_profile("full", tm, confirm=True)
    assert _listed(tm) == {n for n, _ in _DUMMY_TOOLS}


def test_meta_forced_on_custom(tm):
    tp.install(tm)
    tp.activate_profile("custom", tm, groups=["linux"])
    assert _listed(tm) == {"sassy_tp_linux", "sassy_tp_meta"}


def test_readonly_resolves_from_annotations(tm, monkeypatch):
    tp.install(tm)
    monkeypatch.setattr(
        tp, "_read_only_tools", lambda: {"sassy_tp_read", "sassy_tp_meta"})
    tp.activate_profile("readonly", tm)
    assert _listed(tm) == {"sassy_tp_read", "sassy_tp_meta"}
    with pytest.raises(ToolError):
        _call(tm, "sassy_tp_shell")


# ── confirm gate ───────────────────────────────────────────────────────

def test_narrowing_needs_no_confirm(tm):
    tp.install(tm)
    tp.activate_profile("devices", tm)  # full -> devices hides tools only
    assert tp.get_active() == "devices"


def test_escalation_needs_confirm(tm):
    tp.install(tm)
    tp.activate_profile("devices", tm)
    with pytest.raises(tp.ConfirmRequired):
        tp.activate_profile("full", tm)  # would re-expose hidden tools
    tp.activate_profile("full", tm, confirm=True)
    assert tp.get_active() == "full"


def test_lateral_move_with_new_tools_needs_confirm(tm):
    tp.install(tm)
    tp.activate_profile("devices", tm)
    # sysadmin exposes linux/system/updater tools hidden under devices
    with pytest.raises(tp.ConfirmRequired):
        tp.activate_profile("sysadmin", tm)
    tp.activate_profile("sysadmin", tm, confirm=True)
    assert "sassy_tp_linux" in _listed(tm)


def test_unknown_profile_rejected(tm):
    tp.install(tm)
    with pytest.raises(ValueError):
        tp.activate_profile("root", tm)


def test_custom_validates_groups(tm):
    tp.install(tm)
    with pytest.raises(ValueError):
        tp.activate_profile("custom", tm, groups=["nope"])
    with pytest.raises(ValueError):
        tp.activate_profile("custom", tm, groups=[])


# ── control panel API (dashboard-only boundary) ────────────────────────

def _panel_with_manager(monkeypatch, tm):
    from sassymcp import control_panel as cp
    fake_server = SimpleNamespace(mcp=SimpleNamespace(_tool_manager=tm))
    monkeypatch.setitem(sys.modules, "sassymcp.server", fake_server)
    audit = []
    monkeypatch.setattr(
        cp, "_audit_panel_mutation",
        lambda actor, api, changes: audit.append(
            {"actor": actor, "api": api, "changes": changes}))
    return cp, audit


def test_panel_get_profile(monkeypatch, tm):
    cp, _audit = _panel_with_manager(monkeypatch, tm)
    tp.install(tm)
    status, obj = cp.handle_api("GET", "/api/profile", {}, None)
    assert status == 200
    assert obj["active"] == "full"
    assert obj["session_scoped"] is True
    assert len(obj["profiles"]) == 7
    assert len(obj["groups"]) == 18
    by_name = {p["name"]: p for p in obj["profiles"]}
    assert by_name["devices"]["tool_count"] is not None
    assert "android" in by_name["devices"]["groups"]


def test_panel_post_switch_audited(monkeypatch, tm):
    cp, audit = _panel_with_manager(monkeypatch, tm)
    tp.install(tm)
    status, obj = cp.handle_api(
        "POST", "/api/profile", {},
        {"profile": "devices", "confirm": "YES"})
    assert status == 200
    assert obj["active"] == "devices"
    assert tp.get_active() == "devices"
    assert len(audit) == 1
    entry = audit[0]
    assert entry["api"] == "/api/profile"
    assert entry["changes"]["profile"] == {"old": "full", "new": "devices"}


def test_panel_escalation_requires_confirm(monkeypatch, tm):
    cp, audit = _panel_with_manager(monkeypatch, tm)
    tp.install(tm)
    s, _ = cp.handle_api(
        "POST", "/api/profile", {}, {"profile": "devices", "confirm": "YES"})
    assert s == 200
    s, obj = cp.handle_api("POST", "/api/profile", {}, {"profile": "full"})
    assert s == 400
    assert obj.get("confirm_required") is True
    assert not audit or audit[-1]["changes"]["profile"]["new"] != "full"
    s, obj = cp.handle_api(
        "POST", "/api/profile", {}, {"profile": "full", "confirm": "YES"})
    assert s == 200
    assert obj["active"] == "full"


def test_panel_custom_groups(monkeypatch, tm):
    cp, audit = _panel_with_manager(monkeypatch, tm)
    tp.install(tm)
    s, obj = cp.handle_api("POST", "/api/profile", {},
                           {"profile": "custom", "groups": ["linux"]})
    assert s == 200
    assert obj["active"] == "custom"
    assert obj["custom_groups"] == ["linux"]
    assert _listed(tm) == {"sassy_tp_linux", "sassy_tp_meta"}
    assert audit[0]["changes"]["custom_groups"] == {
        "old": None, "new": ["linux"]}


def test_panel_rejects_bad_input(monkeypatch, tm):
    cp, _audit = _panel_with_manager(monkeypatch, tm)
    tp.install(tm)
    s, _ = cp.handle_api("POST", "/api/profile", {}, {"profile": "root"})
    assert s == 400
    s, _ = cp.handle_api(
        "POST", "/api/profile", {}, {"profile": "custom", "groups": ["nope"]})
    assert s == 400
    s, _ = cp.handle_api("POST", "/api/profile", {}, {})
    assert s == 400


def test_panel_503_when_unbound(monkeypatch, tm):
    cp, _audit = _panel_with_manager(monkeypatch, tm)
    tp.reset()  # not installed -> not bound
    s, obj = cp.handle_api(
        "POST", "/api/profile", {}, {"profile": "devices"})
    assert s == 503
