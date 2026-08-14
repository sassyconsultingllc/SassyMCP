# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-X6NYBBL72GHP
"""Offline degradation + local-model fallback.

The load-bearing property is fail-open: an un-probed or broken netstate must
never gate a tool. Everything else here is classification accuracy.
"""

import time

import pytest

from sassymcp import _netstate


@pytest.fixture(autouse=True)
def _restore_state():
    saved = dict(_netstate._state)
    yield
    _netstate._state.clear()
    _netstate._state.update(saved)


def _force(link: str):
    _netstate._state.update({
        "link": link,
        "online": link == "online",
        "dns": link == "online",
        "checked_at": time.time(),
    })


# ── classification ────────────────────────────────────────────────────

@pytest.mark.parametrize("tool,group,expected", [
    ("sassy_gh_create_pr", "github_quick", "internet"),
    ("sassy_ghq_push", "github_quick", "internet"),
    ("sassy_url_headers", "v020", "internet"),
    ("sassy_http", "utility", "internet"),
    ("sassy_http_ping", "utility", "internet"),
    ("sassy_update_apply", "updater", "internet"),
    ("sassy_dns_lookup", "system", "internet"),
    ("sassy_traceroute", "system", "internet"),
    ("sassy_linux_exec", "linux", "lan"),
    ("sassy_port_scan", "system", "lan"),
    ("sassy_wifi_networks", "system", "lan"),
    ("sassy_adb_wifi_connect", "android", "lan"),
    ("sassy_read_file", "core", "none"),
    ("sassy_shell", "core", "none"),
    ("sassy_memory_recall", "memory", "none"),
    ("sassy_screenshot", "v020", "none"),
    ("sassy_adb_shell", "android", "none"),
])
def test_classification(tool, group, expected):
    assert _netstate.classify_tool(tool, group) == expected


def test_group_default_comes_from_tool_groups():
    """A github tool with no prefix match still inherits the group's declaration."""
    assert _netstate.classify_tool("sassy_something_new", "github_full") == "internet"
    assert _netstate.classify_tool("sassy_something_new", "core") == "none"


# ── gate ──────────────────────────────────────────────────────────────

def test_gate_open_when_online():
    _force("online")
    assert _netstate.gate("sassy_gh_list_prs", "github_quick", {}) is None


def test_gate_fails_open_on_assumed_state():
    """Never-probed state must not gate anything — this is the safety property."""
    _netstate._state.update({"link": "assumed", "online": True, "checked_at": 0.0})
    assert _netstate.gate("sassy_gh_list_prs", "github_quick", {}) is None


def test_gate_blocks_internet_tool_when_offline():
    _force("offline")
    refusal = _netstate.gate("sassy_gh_list_prs", "github_quick", {})
    assert refusal is not None
    assert refusal["error"] == "offline"
    assert refusal["retryable"] is True
    assert refusal["offline_alternative"]


def test_gate_blocks_on_dns_only():
    """Link up, resolver dead — an api.github.com call still cannot work."""
    _netstate._state.update({"link": "dns_only", "online": False, "dns": False,
                             "checked_at": time.time()})
    refusal = _netstate.gate("sassy_url_headers", "v020", {})
    assert refusal is not None
    assert "DNS" in refusal["detail"]


def test_gate_never_blocks_local_or_lan_tools():
    _force("offline")
    assert _netstate.gate("sassy_read_file", "core", {}) is None
    assert _netstate.gate("sassy_shell", "core", {}) is None
    assert _netstate.gate("sassy_linux_exec", "linux", {}) is None


def test_gate_allows_loopback_targets():
    """Probing the local inference server while the WAN is down must work."""
    _force("offline")
    assert _netstate.gate("sassy_http", "utility",
                          {"url": "http://127.0.0.1:11434/api/tags"}) is None
    assert _netstate.gate("sassy_http_ping", "utility",
                          {"host": "localhost"}) is None


def test_gate_respects_off_switch(monkeypatch):
    _force("offline")
    monkeypatch.setenv("SASSYMCP_OFFLINE_GATE", "off")
    assert _netstate.gate("sassy_gh_list_prs", "github_quick", {}) is None


# ── reactive detection ────────────────────────────────────────────────

def test_network_failure_expires_cache():
    _force("online")
    _netstate.note_tool_failure(
        "sassy_gh_list_prs",
        OSError("<urlopen error [Errno 11001] getaddrinfo failed>"),
    )
    assert _netstate._state["checked_at"] == 0.0
    assert _netstate._state["last_tool_failure"]["tool"] == "sassy_gh_list_prs"


def test_non_network_failure_leaves_cache_alone():
    _force("online")
    stamp = _netstate._state["checked_at"]
    _netstate.note_tool_failure("sassy_read_file", ValueError("bad argument"))
    assert _netstate._state["checked_at"] == stamp


# ── alternatives ──────────────────────────────────────────────────────

def test_every_internet_family_has_an_alternative():
    for tool in ("sassy_gh_create_pr", "sassy_url_headers", "sassy_http",
                 "sassy_update_check", "sassy_dns_lookup", "sassy_traceroute",
                 "sassy_cert_check", "sassy_setup_github"):
        assert _netstate.offline_alternative(tool), f"{tool} has no substitute"


def test_local_tools_have_no_alternative_noise():
    assert _netstate.offline_alternative("sassy_read_file") is None


# ── local model discovery ─────────────────────────────────────────────

def test_fallback_ready_shape():
    fb = _netstate.fallback_ready()
    assert set(fb) == {"ready", "chosen", "backends_up", "reason"}
    assert isinstance(fb["ready"], bool)
    if not fb["ready"]:
        assert fb["reason"]


def test_snapshot_never_blocks():
    start = time.monotonic()
    for _ in range(200):
        _netstate.snapshot()
    assert time.monotonic() - start < 0.5
