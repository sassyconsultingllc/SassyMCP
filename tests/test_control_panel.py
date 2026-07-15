# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-MNCIYS6KFX7V
"""Tests for the Control Panel API core (sassymcp.control_panel).

Exercise the pure router (handle_api) and the token logic without binding
a socket. Config reads/writes are redirected to an in-memory dict so the
real ~/.sassymcp/config.json is never touched.

Run: pytest tests/test_control_panel.py
"""
import json
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


def test_coerce_port():
    assert cp.coerce_port(9000) == 9000
    assert cp.coerce_port("9000") == 9000
    assert cp.coerce_port(None) == cp.DEFAULT_PORT
    assert cp.coerce_port("not-a-port") == cp.DEFAULT_PORT
    assert cp.coerce_port(0) == cp.DEFAULT_PORT
    assert cp.coerce_port(99999) == cp.DEFAULT_PORT


def test_panel_info_bad_config_port():
    cp.stop_panel()
    # a junk configured port still yields a valid URL via coercion
    assert cp.panel_info(port="garbage")["port"] == cp.DEFAULT_PORT


def test_panel_info_reports_actual_bound_port():
    cp.stop_panel()
    # not running -> reports the requested port
    assert cp.panel_info(port=12345)["port"] == 12345
    info = cp.start_panel(port=8801)
    try:
        bound = cp.current_port()
        assert bound is not None
        # reported port matches the real bound port, and bind is loopback
        assert cp.panel_info()["port"] == bound
        assert "127.0.0.1" in info["url"]
        assert cp._server.server_address[0] == "127.0.0.1"
    finally:
        cp.stop_panel()


def test_audit_events_bounded_tail(monkeypatch, tmp_path):
    import sassymcp._paths as paths
    monkeypatch.setattr(paths, "HOME", tmp_path)
    jsonl = tmp_path / "audit.jsonl"
    lines = [json.dumps({"event": f"e{i}", "timestamp": i}) for i in range(500)]
    lines.append("this is not json")  # newest line is junk -> must be skipped
    jsonl.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ev = cp._audit_events(limit=10)
    assert len(ev) == 10
    assert ev[0]["event"] == "e499"  # newest valid event first


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


# ── Cockpit (read-only tool visualizers) ─────────────────────────────

def test_classify_text():
    assert cp._classify_result("Proto  Local  State\nTCP 0.0.0.0:445 LISTEN")["kind"] == "text"


def test_classify_table_array():
    out = cp._classify_result(json.dumps([{"pid": 1, "name": "init"}]))
    assert out["kind"] == "table"
    assert out["rows"][0]["pid"] == 1


def test_classify_table_wrapped_list():
    out = cp._classify_result(json.dumps({"count": 1, "top_10": [{"tool": "x", "score": 9}]}))
    assert out["kind"] == "table"
    assert out["label"] == "top_10"
    assert out["meta"]["count"] == 1  # scalar siblings preserved as metadata


def test_classify_keyvals():
    out = cp._classify_result(json.dumps({"hostname": "PC", "cpu_percent": 9.0}))
    assert out["kind"] == "keyvals"
    assert out["pairs"]["hostname"] == "PC"


def test_classify_raw_dict():
    # observability tools return a dict, not a json string
    assert cp._classify_result({"status": "healthy", "uptime_seconds": 12})["kind"] == "keyvals"


def test_classify_image():
    out = cp._classify_result(json.dumps({"image_base64": "A" * 200, "format": "jpeg", "bytes": 99}))
    assert out["kind"] == "image"
    assert out["image"] == "A" * 200
    assert out["meta"]["bytes"] == 99  # scalar metadata kept, image key stripped


def test_classify_error_envelope():
    assert cp._classify_result(json.dumps({"error": "not installed"}))["kind"] == "error"


def test_cockpit_allowlist_blocks_mutating_tools():
    # the panel must never be able to invoke a mutating/dangerous tool
    for danger in ("sassy_shell", "sassy_write_file", "sassy_selfmod_write", "sassy_safe_delete"):
        assert danger not in cp._COCKPIT_TOOLS
        _, err = cp._run_tool(danger, {})
        assert err and "not permitted" in err


def test_cockpit_catalog_shape():
    s, o = cp.handle_api("GET", "/api/cockpit", {}, None)
    assert s == 200
    assert "netstat" in o["views"] and "metrics" in o["views"]
    # every view names a concrete tool and reports availability
    for v in o["views"].values():
        assert v["tool"].startswith("sassy_")
        assert isinstance(v["available"], bool)


def test_cockpit_unknown_view():
    s, o = cp.handle_api("GET", "/api/cockpit", {"view": ["does-not-exist"]}, None)
    assert s == 404


def test_cockpit_view_unloaded_tool_is_graceful():
    # with no server assembled, the tool isn't loaded -> error kind, not a crash
    s, o = cp.handle_api("GET", "/api/cockpit", {"view": ["netstat"]}, None)
    assert s == 200
    assert o["kind"] == "error"
    assert "not loaded" in o["error"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
