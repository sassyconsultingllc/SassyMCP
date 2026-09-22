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

from sassymcp import control_panel as cp


def _mem_cfg(monkeypatch, store, audit_log=None):
    import sassymcp.modules.runtime_config as rc
    monkeypatch.setattr(rc, "get", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(rc, "set_val", lambda k, v: store.__setitem__(k, v))
    if audit_log is not None:
        # keep the suite hermetic: record panel-mutation audit calls instead
        # of appending to the real ~/.sassymcp/audit.jsonl
        monkeypatch.setattr(
            cp, "_audit_panel_mutation",
            lambda actor, api, changes: audit_log.append(
                {"actor": actor, "api": api, "changes": changes}),
        )


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
    audit_log = []
    _mem_cfg(monkeypatch, store, audit_log)
    # bypass mode requires confirm:"YES" (trust-tier parity with the
    # sassy_permission MCP tool) — the panel UI sends it on every save
    s, _o = cp.handle_api("POST", "/api/settings", {}, {"mode": "bypass", "confirm": "YES"},
                          actor="panel:header")
    assert s == 200
    assert store["permission.mode"] == "bypass"
    # the mutation must be audit-logged with actor, keys, and old->new
    assert len(audit_log) == 1
    ev = audit_log[0]
    assert ev["actor"] == "panel:header"
    assert ev["api"] == "/api/settings"
    assert ev["changes"]["permission.mode"] == {"old": "", "new": "bypass"}


def test_settings_post_bypass_requires_confirm(monkeypatch):
    store = {}
    audit_log = []
    _mem_cfg(monkeypatch, store, audit_log)
    # missing confirm -> 400 and NO mutation (behavior change, audit C F2)
    s, o = cp.handle_api("POST", "/api/settings", {}, {"mode": "bypass"},
                         actor="panel:header")
    assert s == 400
    assert "confirm" in o["error"]
    assert "permission.mode" not in store
    assert audit_log == []
    # wrong-case confirm is also rejected
    s, _o = cp.handle_api("POST", "/api/settings", {}, {"mode": "bypass", "confirm": "yes"})
    assert s == 400
    # non-bypass modes don't need the field
    s, _o = cp.handle_api("POST", "/api/settings", {}, {"mode": "strict"})
    assert s == 200
    assert store["permission.mode"] == "strict"


def test_settings_post_rejects_relative_roots(monkeypatch):
    store = {}
    audit_log = []
    _mem_cfg(monkeypatch, store, audit_log)
    s, o = cp.handle_api("POST", "/api/settings", {},
                         {"sandboxRoots": ["relative/path"]})
    assert s == 400
    assert o["bad"] == ["relative/path"]
    assert "permission.sandboxRoots" not in store
    # empty strings are not absolute either
    s, _o = cp.handle_api("POST", "/api/settings", {}, {"sandboxRoots": [""]})
    assert s == 400
    # absolute paths (posix or windows style on their own platform) pass
    import os
    abs_root = os.path.abspath(os.sep)
    s, _o = cp.handle_api("POST", "/api/settings", {},
                          {"sandboxRoots": [abs_root]})
    assert s == 200
    assert store["permission.sandboxRoots"] == [abs_root]


def test_settings_post_invalid_mode(monkeypatch):
    store = {}
    _mem_cfg(monkeypatch, store)
    s, _o = cp.handle_api("POST", "/api/settings", {}, {"mode": "wideopen"})
    assert s == 400
    assert "permission.mode" not in store


def test_settings_post_bad_roots_type(monkeypatch):
    store = {}
    _mem_cfg(monkeypatch, store)
    s, _o = cp.handle_api("POST", "/api/settings", {}, {"sandboxRoots": "not-a-list"})
    assert s == 400


def test_rules_post_valid(monkeypatch):
    store = {}
    audit_log = []
    _mem_cfg(monkeypatch, store, audit_log)
    s, _o = cp.handle_api("POST", "/api/rules", {},
                          {"rules": [{"action": "deny", "command": "rm"}]},
                          actor="panel:query")
    assert s == 200
    assert len(store["permission.rules"]) == 1
    assert len(audit_log) == 1
    ev = audit_log[0]
    assert ev["actor"] == "panel:query"
    assert ev["api"] == "/api/rules"
    assert ev["changes"]["permission.rules"]["old"] == []
    assert ev["changes"]["permission.rules"]["new"] == [{"action": "deny", "command": "rm"}]


def test_mutation_audit_records_old_and_new_values(monkeypatch):
    store = {"permission.mode": "strict", "interceptor.destructiveAction": "block"}
    audit_log = []
    _mem_cfg(monkeypatch, store, audit_log)
    s, _o = cp.handle_api("POST", "/api/settings", {},
                          {"mode": "sandbox", "destructiveAction": "confirm"})
    assert s == 200
    assert len(audit_log) == 1
    changes = audit_log[0]["changes"]
    assert changes["permission.mode"] == {"old": "strict", "new": "sandbox"}
    assert changes["interceptor.destructiveAction"] == {"old": "block", "new": "confirm"}


def test_failed_mutation_is_not_audited(monkeypatch):
    store = {}
    audit_log = []
    _mem_cfg(monkeypatch, store, audit_log)
    s, _o = cp.handle_api("POST", "/api/settings", {}, {"mode": "wideopen"})
    assert s == 400
    assert audit_log == []


def test_mutation_audit_redacts_secrets(monkeypatch, tmp_path):
    # exercise the REAL audit path (not the stub) against a temp HOME and
    # confirm key-named and value-shaped secrets are masked in the entry
    import sassymcp._paths as paths
    monkeypatch.setattr(paths, "HOME", tmp_path)
    import sassymcp.modules.audit as audit_mod
    monkeypatch.setattr(audit_mod, "_LOG_DIR", tmp_path)
    monkeypatch.setattr(audit_mod, "_LOG_FILE", tmp_path / "audit.log")
    monkeypatch.setattr(audit_mod, "_JSONL_FILE", tmp_path / "audit.jsonl")
    cp._audit_panel_mutation(
        "panel:header", "/api/settings",
        {"panel.token": {"old": "", "new": "ghp_" + "A" * 40}},
    )
    entry = json.loads((tmp_path / "audit.jsonl").read_text().strip())
    assert entry["tool"] == "control_panel"
    assert "ts" in entry and "timestamp" in entry
    masked = entry["args"]["panel.token"]
    assert masked == "***REDACTED***" or "ghp_" not in str(masked)


def test_rules_post_invalid_action(monkeypatch):
    store = {}
    _mem_cfg(monkeypatch, store)
    s, _o = cp.handle_api("POST", "/api/rules", {}, {"rules": [{"action": "nope"}]})
    assert s == 400
    assert "permission.rules" not in store


def test_classifiers_route():
    s, o = cp.handle_api("GET", "/api/classifiers", {}, None)
    assert s == 200
    # the real _security keyword set should surface
    assert "rm" in o["delete_keywords"]
    assert isinstance(o["pattern_tiers"], dict)


def test_unknown_route():
    s, _o = cp.handle_api("GET", "/api/nope", {}, None)
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


def test_rotate_panel_token(monkeypatch, tmp_path):
    monkeypatch.setattr(cp, "_token", None)
    monkeypatch.delenv("SASSYMCP_PANEL_TOKEN", raising=False)
    monkeypatch.setattr(cp, "_token_file", lambda: tmp_path / "panel.token")
    old = cp.panel_token()
    new = cp.rotate_panel_token()
    assert new != old and len(new) >= 16
    # the new token is what the handler compares against now (old is dead)
    assert cp._token_ok(new)
    assert not cp._token_ok(old)
    # persisted: a fresh process reading the file gets the new token
    assert (tmp_path / "panel.token").read_text().strip() == new
    monkeypatch.setattr(cp, "_token", None)
    assert cp.panel_token() == new


def test_panel_base_url_has_no_token(monkeypatch):
    cp.stop_panel()
    monkeypatch.setattr(cp, "_token", "SHOULD-NOT-APPEAR")
    url = cp.panel_base_url(port=8765)
    assert url == "http://127.0.0.1:8765/"
    assert "token" not in url and "SHOULD-NOT-APPEAR" not in url


def test_token_persist_failure_warns_stderr_once(monkeypatch, tmp_path, capsys):
    # point the token file somewhere unwritable: parent is a regular file
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    monkeypatch.setattr(cp, "_token_file", lambda: blocker / "panel.token")
    monkeypatch.setattr(cp, "_persist_stderr_warned", False)
    assert cp._persist_token_file("tok-1") is False
    assert cp._persist_token_file("tok-2") is False  # second call: no repeat
    err = capsys.readouterr().err
    assert "could not write the panel token file" in err
    assert err.count("could not write the panel token file") == 1


def test_query_token_emits_deprecation_warning(monkeypatch, caplog):
    import logging
    from unittest.mock import MagicMock
    handler = MagicMock()
    handler.headers = {}
    with caplog.at_level(logging.WARNING, logger="sassymcp.control_panel"):
        tok = cp._provided_token(handler, {"token": ["abc"]})
    assert tok == "abc"
    assert any("deprecated" in r.message for r in caplog.records)
    # the header path stays silent
    handler.headers = {"X-Panel-Token": "abc"}
    with caplog.at_level(logging.WARNING, logger="sassymcp.control_panel"):
        caplog.clear()
        assert cp._provided_token(handler, {}) == "abc"
    assert not [r for r in caplog.records if "deprecated" in r.message]


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
    for danger in ("sassy_shell", "sassy_write_file", "sassy_safe_delete"):
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
    s, _o = cp.handle_api("GET", "/api/cockpit", {"view": ["does-not-exist"]}, None)
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
