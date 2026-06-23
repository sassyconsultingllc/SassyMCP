"""Tests for the Control Panel API core (sassymcp.control_panel).

Exercise the pure router (handle_api) and the token logic without binding
a socket. Config reads/writes are redirected to an in-memory dict so the
real ~/.sassymcp/config.json is never touched.

Run: pytest tests/test_control_panel.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sassymcp import control_panel as cp  # noqa: E402


def _mem_cfg(monkeypatch, store):
    import sassymcp.modules.runtime_config as rc
    monkeypatch.setattr(rc, "get", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(rc, "set_val", lambda k, v: store.__setitem__(k, v))


def test_status_route(monkeypatch):
    _mem_cfg(monkeypatch, {"permission.mode": "sandbox", "permission.sandboxRoots": []})
    s, o = cp.handle_api("GET", "/api/status", {}, None)
    assert s == 200
    assert o["effective_mode"] == "sandbox"
    assert "strict" in o["valid_modes"]


def test_events_route(monkeypatch):
    monkeypatch.setattr(cp, "_audit_events", lambda limit=100: [{"event": "policy_bypass"}])
    s, o = cp.handle_api("GET", "/api/events", {"limit": ["5"]}, None)
    assert s == 200
    assert o["events"] == [{"event": "policy_bypass"}]


def test_settings_get(monkeypatch):
    _mem_cfg(monkeypatch, {"permission.mode": "", "permission.sandboxRoots": ["/x"]})
    s, o = cp.handle_api("GET", "/api/settings", {}, None)
    assert s == 200
    assert o["sandboxRoots"] == ["/x"]


def test_settings_post_valid(monkeypatch):
    store = {}
    _mem_cfg(monkeypatch, store)
    s, o = cp.handle_api("POST", "/api/settings", {}, {"mode": "bypass"})
    assert s == 200
    assert store["permission.mode"] == "bypass"


def test_settings_post_invalid_mode(monkeypatch):
    store = {}
    _mem_cfg(monkeypatch, store)
    s, o = cp.handle_api("POST", "/api/settings", {}, {"mode": "wideopen"})
    assert s == 400
    assert "permission.mode" not in store


def test_settings_post_bad_roots_type(monkeypatch):
    store = {}
    _mem_cfg(monkeypatch, store)
    s, o = cp.handle_api("POST", "/api/settings", {}, {"sandboxRoots": "not-a-list"})
    assert s == 400


def test_rules_post_valid(monkeypatch):
    store = {}
    _mem_cfg(monkeypatch, store)
    s, o = cp.handle_api("POST", "/api/rules", {}, {"rules": [{"action": "deny", "command": "rm"}]})
    assert s == 200
    assert len(store["permission.rules"]) == 1


def test_rules_post_invalid_action(monkeypatch):
    store = {}
    _mem_cfg(monkeypatch, store)
    s, o = cp.handle_api("POST", "/api/rules", {}, {"rules": [{"action": "nope"}]})
    assert s == 400
    assert "permission.rules" not in store


def test_classifiers_route():
    s, o = cp.handle_api("GET", "/api/classifiers", {}, None)
    assert s == 200
    # the real _security keyword set should surface
    assert "rm" in o["delete_keywords"]
    assert isinstance(o["pattern_tiers"], dict)


def test_unknown_route():
    s, o = cp.handle_api("GET", "/api/nope", {}, None)
    assert s == 404


def test_token_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(cp, "_token", None)
    monkeypatch.delenv("SASSYMCP_PANEL_TOKEN", raising=False)
    monkeypatch.setattr(cp, "_token_file", lambda: tmp_path / "panel.token")
    t1 = cp.panel_token()
    t2 = cp.panel_token()
    assert t1 == t2 and len(t1) >= 16
    assert cp._token_ok(t1)
    assert not cp._token_ok("wrong")
    assert not cp._token_ok(None)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
