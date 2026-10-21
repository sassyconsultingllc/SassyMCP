# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-JZ4KBZCJTIZF
"""Tests for the remote policy gate (split-architecture local half).

Covers sassymcp.remote_policy: the worker client (mocked transport),
the HMAC-signed decision cache, argument redaction, and the enforcement
wrapper (allow/ask/deny, cache hits, fail-open vs fail-closed, and the
auth-failure-always-denies invariant).

Run: ~/workspace/sassymcp-analysis/venv/bin/python -m pytest tests/test_remote_policy.py
"""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sassymcp.remote_policy import client as pclient
from sassymcp.remote_policy import cache as pcache
from sassymcp.remote_policy import enforcement as penf


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _decision_payload(**over):
    body = {
        "decision": "allow",
        "reason": "ok",
        "ttl_seconds": 300,
        "policy_version": "2026-09-22",
    }
    body.update(over)
    return body


def _mock_client(payload=None, status=200, exc=None):
    """PolicyClient whose transport is fully mocked."""
    def handler(request: httpx.Request) -> httpx.Response:
        if exc is not None:
            raise exc
        assert request.headers["X-SassyMCP-Protocol"] == "1"
        assert request.headers["Authorization"].startswith("Bearer ")
        return httpx.Response(status, json=payload)

    transport = httpx.MockTransport(handler)
    return pclient.PolicyClient(
        "https://policy.example.test",
        license_key="KEY",
        instance_id="inst",
        transport=transport,
    )


@pytest.fixture
def policy_env(monkeypatch, tmp_path):
    """Enable the gate with an isolated HOME (license file + cache)."""
    monkeypatch.setenv("SASSYMCP_POLICY_ENABLED", "1")
    monkeypatch.setenv("SASSYMCP_POLICY_ENDPOINT", "https://policy.example.test")
    monkeypatch.setattr(pcache, "HOME", tmp_path, raising=False)
    # license identity: bypass the real license file
    monkeypatch.setattr(penf, "_license_identity", lambda: ("LS-KEY", "inst-1"))
    # cache module reads HOME at call time via _path(); patch the module attr
    import sassymcp._paths as _paths

    monkeypatch.setattr(_paths, "HOME", tmp_path, raising=False)
    # pcache imported HOME at module import? No — _path() imports lazily.
    # But patch the name anyway in case of future refactors.
    return tmp_path


# ---------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------


def test_client_allow():
    c = _mock_client(_decision_payload(decision="allow"))
    out = asyncio.run(
        c.evaluate(tool="sassy_shell", group="system", destructive=True,
                   arg_summary={}, profile="full", license_tier="pro")
    )
    assert out["decision"] == "allow"
    assert out["ttl_seconds"] == 300


def test_client_deny_and_ask():
    for d in ("deny", "ask"):
        c = _mock_client(_decision_payload(decision=d, reason="nope"))
        out = asyncio.run(
            c.evaluate(tool="t", group="g", destructive=False,
                       arg_summary={}, profile="full", license_tier="free")
        )
        assert out["decision"] == d
        assert out["reason"] == "nope"


def test_client_auth_failure():
    c = _mock_client(status=401)
    with pytest.raises(pclient.PolicyAuthError):
        asyncio.run(
            c.evaluate(tool="t", group="g", destructive=False,
                       arg_summary={}, profile="full", license_tier="free")
        )


def test_client_bad_decision_rejected():
    c = _mock_client(_decision_payload(decision="maybe"))
    with pytest.raises(pclient.PolicyProtocolError):
        asyncio.run(
            c.evaluate(tool="t", group="g", destructive=False,
                       arg_summary={}, profile="full", license_tier="free")
        )


def test_client_transport_error():
    c = _mock_client(exc=httpx.ConnectError("down"))
    with pytest.raises(pclient.PolicyError):
        asyncio.run(
            c.evaluate(tool="t", group="g", destructive=False,
                       arg_summary={}, profile="full", license_tier="free")
        )


def test_client_health():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "policy_version": "x"})

    c = pclient.PolicyClient(
        "https://policy.example.test", license_key="K",
        transport=httpx.MockTransport(handler),
    )
    assert asyncio.run(c.health())["ok"] is True


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------


def test_cache_roundtrip(policy_env):
    pcache.put("sassy_shell", "system", True, "full",
               _decision_payload(decision="deny", reason="tier"), 300)
    hit = pcache.get("sassy_shell", "system", True, "full")
    assert hit is not None
    assert hit["decision"] == "deny"
    assert hit["reason"] == "tier"


def test_cache_miss_and_profile_isolation(policy_env):
    pcache.put("sassy_shell", "system", True, "full",
               _decision_payload(decision="allow"), 300)
    assert pcache.get("sassy_shell", "system", True, "readonly") is None
    assert pcache.get("sassy_read_file", "system", False, "full") is None


def test_cache_expiry(policy_env):
    pcache.put("sassy_shell", "system", True, "full",
               _decision_payload(decision="allow"), -1)  # already expired
    assert pcache.get("sassy_shell", "system", True, "full") is None


def test_cache_tamper_rejected(policy_env, tmp_path):
    pcache.put("sassy_shell", "system", True, "full",
               _decision_payload(decision="deny", reason="tier"), 300)
    path = tmp_path / "remote_policy_cache.json"
    data = json.loads(path.read_text())
    key = next(iter(data))
    data[key]["entry"]["decision"] = "allow"  # attacker flips deny->allow
    path.write_text(json.dumps(data))
    # signature no longer matches -> treated as miss
    assert pcache.get("sassy_shell", "system", True, "full") is None


def test_cache_zero_ttl_not_stored(policy_env):
    pcache.put("sassy_shell", "system", True, "full",
               _decision_payload(decision="allow"), 0)
    assert pcache.entry_count() == 0


# ---------------------------------------------------------------------------
# redaction
# ---------------------------------------------------------------------------


def test_redact_secret_names():
    assert penf.redact_preview("api_key", "whatever") == "[redacted]"
    assert penf.redact_preview("password", "hunter2") == "[redacted]"
    assert penf.redact_preview("Authorization", "Bearer abc") == "[redacted]"


def test_redact_secret_values():
    assert penf.redact_preview("blob", "a" * 40) == "[redacted]"
    assert penf.redact_preview("path", "C:\\Users\\shane\\file.txt") != "[redacted]"


def test_summarize_truncates():
    out = penf.summarize_args((), {"command": "x" * 500})
    assert len(out["command"]) <= 121  # 120 + ellipsis


def test_summarize_no_preview_mode():
    out = penf.summarize_args((), {"command": "rm -rf /"}, send_preview=False)
    assert out["command"] == "<str>"
    assert "rm -rf" not in out["command"]


# ---------------------------------------------------------------------------
# enforcement
# ---------------------------------------------------------------------------


def _tool_fn(**kwargs):
    return json.dumps({"ran": True})


def _wrap_with(payload=None, status=200, exc=None, audit=None):
    client = _mock_client(payload, status=status, exc=exc)
    fn = penf.wrap_fn(_tool_fn, audit_log=audit,
                      client_factory=lambda: client)
    return fn


def test_disabled_is_passthrough(monkeypatch):
    monkeypatch.delenv("SASSYMCP_POLICY_ENABLED", raising=False)
    monkeypatch.delenv("SASSYMCP_POLICY_ENDPOINT", raising=False)
    assert penf.is_enabled() is False
    fn = penf.wrap_fn(_tool_fn)
    out = json.loads(asyncio.run(fn(confirm="YES")))
    assert out == {"ran": True}


def test_allow_runs_tool(policy_env):
    fn = _wrap_with(_decision_payload(decision="allow"))
    out = json.loads(asyncio.run(fn(command="echo hi")))
    assert out == {"ran": True}


def test_deny_blocks_tool(policy_env):
    fn = _wrap_with(_decision_payload(decision="deny", reason="revoked"))
    with pytest.raises(ToolError) as ei:
        asyncio.run(fn(command="echo hi"))
    # Raised (not returned) so the MCP layer serves isError=true; the
    # envelope rides along as the ToolError message.
    out = json.loads(ei.value.args[0])
    assert "Denied by remote policy" in out["error"]
    assert "revoked" in out["error"]
    assert out["retryable"] is False


def test_ask_returns_confirm_envelope(policy_env):
    fn = _wrap_with(_decision_payload(decision="ask", reason="sensitive"))
    with pytest.raises(ToolError) as ei:
        asyncio.run(fn())
    out = json.loads(ei.value.args[0])
    assert out["confirm_required"] is True
    assert "sensitive" in out["error"]


def test_fail_closed_on_transport_error(policy_env):
    fn = _wrap_with(exc=httpx.ConnectError("down"))
    with pytest.raises(ToolError) as ei:
        asyncio.run(fn())
    assert "Denied by remote policy (fail-closed)" in json.loads(
        ei.value.args[0])["error"]


def test_fail_open_when_configured(policy_env, monkeypatch):
    from sassymcp.modules import runtime_config

    monkeypatch.setattr(runtime_config, "get",
                        lambda k, d=None: True if k == "remote_policy.fail_open" else d)
    fn = _wrap_with(exc=httpx.ConnectError("down"))
    out = json.loads(asyncio.run(fn()))
    assert out == {"ran": True}


def test_auth_failure_always_denies_even_fail_open(policy_env, monkeypatch):
    from sassymcp.modules import runtime_config

    monkeypatch.setattr(runtime_config, "get",
                        lambda k, d=None: True if k == "remote_policy.fail_open" else d)
    fn = _wrap_with(status=401)
    with pytest.raises(ToolError) as ei:
        asyncio.run(fn())
    assert "Denied by remote policy (fail-closed)" in json.loads(
        ei.value.args[0])["error"]


def test_cache_hit_avoids_remote(policy_env):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=_decision_payload(decision="allow"))

    client = pclient.PolicyClient(
        "https://policy.example.test", license_key="K",
        transport=httpx.MockTransport(handler),
    )
    fn = penf.wrap_fn(_tool_fn, client_factory=lambda: client)
    asyncio.run(fn())
    asyncio.run(fn())
    assert len(calls) == 1  # second call served from cache


def test_denials_are_audit_logged(policy_env):
    events = []

    def audit(tool_name, args, elapsed_ms, error):
        events.append((tool_name, args, error))

    fn = _wrap_with(_decision_payload(decision="deny", reason="tier"), audit=audit)
    with pytest.raises(ToolError):
        asyncio.run(fn())
    assert events, "expected an audit entry for the denial"
    name, args, error = events[0]
    assert name == "_tool_fn[remote_policy]"
    assert args["decision"] == "deny"
    assert "tier" in error
    # redacted summary must not leak full args
    assert "hunter2" not in json.dumps(args)


def test_deny_raises_through_real_tool_manager(policy_env):
    """Regression: the wrapper must RAISE on deny so the MCP layer marks
    isError=true. Returning the envelope as a string served denials as
    successful tool results (spec gap, fixed 2026-09-23)."""
    from mcp.server.fastmcp.tools.tool_manager import ToolManager

    async def probe() -> str:
        return "ok"

    tm = ToolManager()
    tm.add_tool(probe)
    tool = tm.get_tool("probe")
    wrapped_client = _mock_client(
        _decision_payload(decision="deny", reason="tier"))
    tool.fn = penf.wrap_fn(tool.fn, client_factory=lambda: wrapped_client)
    tool.is_async = True
    with pytest.raises(ToolError):
        asyncio.run(tm.call_tool("probe", {}))


def test_wrap_all_tools_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("SASSYMCP_POLICY_ENABLED", raising=False)
    mgr = SimpleNamespace(_tools={})
    assert penf.wrap_all_tools(mgr) == {"enabled": False, "wrapped": 0}


def test_wrap_all_tools_wraps_once(policy_env):
    async def body():
        return "ok"

    tool = SimpleNamespace(fn=body, is_async=False)
    mgr = SimpleNamespace(_tools={"t": tool})
    client = _mock_client(_decision_payload(decision="allow"))
    out = penf.wrap_all_tools(mgr, client_factory=lambda: client)
    assert out == {"enabled": True, "wrapped": 1}
    assert tool.is_async is True
    assert getattr(tool.fn, "_remote_policy_wrapped", False) is True
    # second pass does not double-wrap
    out2 = penf.wrap_all_tools(mgr, client_factory=lambda: client)
    assert out2["wrapped"] == 0


def test_get_status_shape(policy_env):
    st = penf.get_status()
    assert st["enabled"] is True
    assert st["endpoint_configured"] is True
    assert st["has_license_key"] is True
    assert "LS-KEY" not in json.dumps(st)  # no secrets leak


# ---------------------------------------------------------------------------
# spec-gap nits: wrap guard, kwarg filtering, generic fail-closed reasons
# ---------------------------------------------------------------------------


def test_wrap_fn_idempotent_direct(policy_env):
    """The single-wrap guard lives in public wrap_fn, not just wrap_all_tools."""
    def body():
        return "ok"

    once = penf.wrap_fn(body)
    twice = penf.wrap_fn(once)
    assert twice is once
    assert getattr(twice, "_remote_policy_wrapped", False) is True


def test_declared_kwargs_filters_injected_objects():
    """Injected kwargs (e.g. FastMCP Context) never reach the arg summary."""

    def tool_fn(command: str = ""):
        return command

    class FakeContext:
        def __repr__(self):  # pragma: no cover
            return "<FakeContext session=secret>"

    filtered = penf._declared_kwargs(
        tool_fn, {"command": "echo hi", "ctx": FakeContext()}
    )
    assert filtered == {"command": "echo hi"}


def test_wrapper_summary_excludes_injected_kwargs(policy_env):
    """End-to-end: an injected ctx kwarg is stripped before the worker call."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content.decode()))
        return httpx.Response(200, json=_decision_payload(decision="allow"))

    client = pclient.PolicyClient(
        "https://policy.example.test", license_key="KEY", instance_id="inst",
        transport=httpx.MockTransport(handler),
    )

    async def body(command: str = "", **kwargs):
        return "ok"

    fn = penf.wrap_fn(body, client_factory=lambda: client)
    out = asyncio.run(fn(command="echo hi", ctx=object()))
    assert out == "ok"
    assert "ctx" not in seen["arg_summary"]
    assert "command" in seen["arg_summary"]


def test_fail_closed_reason_hides_raw_exception(policy_env):
    """Client-facing refusal must not embed raw transport exception text."""
    secret_detail = "proxy.internal:3128 refused TLS to 10.0.0.9"
    fn = _wrap_with(exc=httpx.ConnectError(secret_detail))
    with pytest.raises(ToolError) as ei:
        asyncio.run(fn())
    out = json.loads(ei.value.args[0])
    assert secret_detail not in out["error"]
    assert "policy worker unreachable" in out["error"]
    assert "fail-closed per config" in out["error"]
