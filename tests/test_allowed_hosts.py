# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
"""Tests for the hot-swappable Host allowlist (sassymcp/_allowed_hosts.py).

Covers: entry validation/normalization, file round-trips (add/list/deny),
env seeding, mtime-cached sync into the MCP SDK settings object, the
pure-ASGI sync wrapper, and — the proof that matters — the real SDK
enforcement path: a real FastMCP app whose middleware 421s an unknown
Host, accepts a host added to the file afterwards with NO restart, and
still 421s a never-added Host.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from sassymcp import _allowed_hosts as ah


@pytest.fixture
def isolated_hosts(tmp_path, monkeypatch):
    """Point the allowlist file at a temp file with a clean env + fresh cache."""
    # NOTE: resolve _paths lazily, NOT from the module-level import.
    # test_lemonsqueezy / test_license_gating delete sys.modules["sassymcp._paths"]
    # and re-import it under a fresh SASSYMCP_HOME, so the object bound at
    # import time can be stale; the live sys.modules entry is the only
    # correct patch target (see _allowed_hosts._hosts_file()).
    import sassymcp._paths as live_paths

    fake = tmp_path / "allowed_hosts"
    monkeypatch.setattr(live_paths, "ALLOWED_HOSTS_FILE", fake)
    monkeypatch.delenv("SASSYMCP_ALLOWED_HOSTS", raising=False)
    ah._cache_key = None
    ah._cached = []
    ah._provisioned.clear()
    yield fake
    ah._cache_key = None
    ah._cached = []
    ah._provisioned.clear()


# ── validation / normalization ────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("abc.trycloudflare.com", "abc.trycloudflare.com"),
    ("ABC.TryCloudflare.COM", "abc.trycloudflare.com"),
    ("  spaced.example.com  ", "spaced.example.com"),
    ("host.example.com.", "host.example.com"),
    ("host.example.com:8443", "host.example.com:8443"),
    ("host.example.com:*", "host.example.com:*"),
    ("localhost", "localhost"),
    ("*.trycloudflare.com", "*.trycloudflare.com"),
    ("*.trycloudflare.com:*", "*.trycloudflare.com:*"),
    ("*.EXAMPLE.com", "*.example.com"),
])
def test_normalize_valid(raw, expected):
    assert ah.normalize_host(raw) == expected


@pytest.mark.parametrize("raw", [
    "",
    "   ",
    "https://host.example.com",
    "host.example.com/path",
    "host.example.com?x=1",
    "host.example.com#frag",
    "has space.com",
    "*",
    ":*",
    "host.example.com:notaport",
    ":8080",
    "*example.com",
    "a.*.com",
    "*.",
    "*. ",
])
def test_normalize_invalid(raw):
    with pytest.raises(ValueError):
        ah.normalize_host(raw)


# ── file round-trips ──────────────────────────────────────────────────

def test_loopback_always_allowed(isolated_hosts):
    assert ah.get_effective_hosts() == list(ah.LOOPBACK_HOSTS)
    assert ah.get_file_hosts() == []


def test_env_seeds_effective_list(isolated_hosts, monkeypatch):
    monkeypatch.setenv("SASSYMCP_ALLOWED_HOSTS", "mcp.example.com,localhost,127.0.0.1")
    assert ah.get_effective_hosts() == [
        "localhost:*", "127.0.0.1:*", "localhost", "127.0.0.1",
        "mcp.example.com",
    ]


def test_add_list_deny_round_trip(isolated_hosts):
    assert ah.add_host("aaa.trycloudflare.com") == "aaa.trycloudflare.com"
    assert ah.add_host("bbb.trycloudflare.com:8443") == "bbb.trycloudflare.com:8443"
    # idempotent: adding again is a no-op
    assert ah.add_host("AAA.trycloudflare.com") == "aaa.trycloudflare.com"
    assert ah.get_file_hosts() == [
        "aaa.trycloudflare.com", "bbb.trycloudflare.com:8443",
    ]
    assert "aaa.trycloudflare.com" in ah.get_effective_hosts()
    assert ah.remove_host("aaa.trycloudflare.com") == "aaa.trycloudflare.com"
    assert ah.get_file_hosts() == ["bbb.trycloudflare.com:8443"]
    assert "aaa.trycloudflare.com" not in ah.get_effective_hosts()


def test_add_preserves_env_seed(isolated_hosts, monkeypatch):
    monkeypatch.setenv("SASSYMCP_ALLOWED_HOSTS", "mcp.example.com")
    ah.add_host("new.trycloudflare.com")
    # The first write carries the env seed into the file.
    assert ah.get_file_hosts() == ["mcp.example.com", "new.trycloudflare.com"]


def test_loopback_cannot_be_removed(isolated_hosts):
    ah.add_host("x.trycloudflare.com")
    assert ah.remove_host("localhost") == "localhost"
    assert "localhost" in ah.get_effective_hosts()
    assert "localhost" not in ah.get_file_hosts()


def test_comments_and_blanks_ignored(isolated_hosts):
    isolated_hosts.write_text(
        "# a comment\n\n  spaced.example.com  # trailing comment\n"
        "second.example.com\n",
        encoding="utf-8",
    )
    assert ah.get_file_hosts() == ["spaced.example.com", "second.example.com"]


def test_corrupt_file_fails_safe(isolated_hosts):
    isolated_hosts.write_text("not a host://bad\n", encoding="utf-8")
    # Reading the file raises (CLI surfaces it); the sync path must not.
    with pytest.raises(ValueError):
        ah.get_file_hosts()
    settings = SimpleNamespace(allowed_hosts=list(ah.LOOPBACK_HOSTS))
    assert ah.sync_to_settings(settings) is False
    assert settings.allowed_hosts == list(ah.LOOPBACK_HOSTS)


# ── sync into SDK settings ────────────────────────────────────────────

def test_sync_mutates_list_in_place(isolated_hosts):
    settings = SimpleNamespace(allowed_hosts=["localhost:*", "127.0.0.1:*"])
    before = settings.allowed_hosts
    ah.add_host("new.trycloudflare.com")
    assert ah.sync_to_settings(settings) is True
    # Same list object the middleware reads — not a rebinding.
    assert settings.allowed_hosts is before
    assert "new.trycloudflare.com" in settings.allowed_hosts
    # Second sync is a no-op.
    assert ah.sync_to_settings(settings) is False


def test_sync_picks_up_external_edits(isolated_hosts):
    settings = SimpleNamespace(allowed_hosts=list(ah.LOOPBACK_HOSTS))
    # Simulate another process (or an editor) rewriting the file.
    isolated_hosts.write_text("edited.trycloudflare.com\n", encoding="utf-8")
    assert ah.sync_to_settings(settings) is True
    assert "edited.trycloudflare.com" in settings.allowed_hosts


# ── ASGI wrapper ──────────────────────────────────────────────────────

def test_wrapper_syncs_then_passes_through(isolated_hosts):
    # Repo convention (tests/test_discovery.py): drive async code with
    # asyncio.run() in a sync test — no pytest-asyncio in dev deps.
    import asyncio

    seen: list = []

    async def inner_app(scope, receive, send):
        seen.append(scope.get("type"))

    settings = SimpleNamespace(allowed_hosts=list(ah.LOOPBACK_HOSTS))
    app = ah.wrap_allowed_hosts_sync(inner_app, settings)
    ah.add_host("wrapped.trycloudflare.com")

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    asyncio.run(app({"type": "http", "method": "GET", "headers": []}, receive, send))
    assert seen == ["http"]
    assert "wrapped.trycloudflare.com" in settings.allowed_hosts


# ── suffix wildcards ──────────────────────────────────────────────────

@pytest.mark.parametrize("host,entry,expected", [
    ("abc.trycloudflare.com", "*.trycloudflare.com", True),
    ("a.b.trycloudflare.com", "*.trycloudflare.com", True),
    ("trycloudflare.com", "*.trycloudflare.com", False),  # needs a label below
    ("abc.evil.com", "*.trycloudflare.com", False),
    ("abc.trycloudflare.com:443", "*.trycloudflare.com", True),
    ("abc.trycloudflare.com", "*.trycloudflare.com:*", True),
    ("abc.trycloudflare.com", "abc.trycloudflare.com", False),  # not a wildcard
    ("abc.trycloudflare.com", "*.evil.com", False),
])
def test_matches_suffix_wildcard(host, entry, expected):
    assert ah.matches_suffix_wildcard(host, entry) is expected


def test_provision_for_host(isolated_hosts):
    ah.add_host("*.trycloudflare.com")
    settings = SimpleNamespace(allowed_hosts=list(ah.LOOPBACK_HOSTS))
    assert ah.sync_to_settings(settings) is True  # wildcard rides along
    # A host under the suffix is provisioned (bare + :* forms).
    assert ah.provision_for_host("zz-1.trycloudflare.com", settings) is True
    assert "zz-1.trycloudflare.com" in settings.allowed_hosts
    assert "zz-1.trycloudflare.com:*" in settings.allowed_hosts
    # Already-provisioned: no-op.
    assert ah.provision_for_host("zz-1.trycloudflare.com", settings) is False
    # A host under another suffix is not provisioned.
    assert ah.provision_for_host("zz-1.evil.com", settings) is False
    # A later sync never wipes a provisioned host.
    assert ah.sync_to_settings(settings) is False
    assert "zz-1.trycloudflare.com" in settings.allowed_hosts


def test_wildcard_end_to_end(isolated_hosts):
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.testclient import TestClient

    ah.add_host("*.trycloudflare.com")
    settings = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=ah.initial_allowed_hosts(),
    )
    mcp_server = FastMCP(name="t", transport_security=settings)
    live_settings = mcp_server.settings.transport_security
    app = ah.wrap_allowed_hosts_sync(
        mcp_server.streamable_http_app(), live_settings
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        # Any host under the suffix passes with no restart, no allow-host.
        r = _post(client, "zz-999.trycloudflare.com")
        assert r.status_code != 421, r.status_code
        # The bare suffix itself does not match the wildcard.
        r = _post(client, "trycloudflare.com")
        assert r.status_code == 421
        # A host under a different suffix is still rejected.
        r = _post(client, "zz-999.evil.com")
        assert r.status_code == 421


# ── real SDK end-to-end ───────────────────────────────────────────────
# Proves the actual enforcement path: a real FastMCP app + the SDK's
# TransportSecurityMiddleware, driven over httpx's ASGI transport.

mcp = pytest.importorskip("mcp")
httpx = pytest.importorskip("httpx")


def _post(client, host):
    # NB: "/mcp" not "/mcp/" — Starlette 307-redirects the trailing-slash
    # form before the transport's Host check ever runs.
    return client.post(
        "/mcp",
        headers={
            "Host": host,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        content=b'{"jsonrpc":"2.0","id":1,"method":"initialize",'
                b'"params":{"protocolVersion":"2024-11-05","capabilities":{},'
                b'"clientInfo":{"name":"t","version":"1"}}}',
    )


def test_hot_swap_end_to_end(isolated_hosts):
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.testclient import TestClient

    settings = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=ah.initial_allowed_hosts(),
    )
    mcp_server = FastMCP(name="t", transport_security=settings)
    # NB: FastMCP's pydantic Settings COPIES the nested settings object at
    # construction, so the wrapper must sync the FastMCP-held copy
    # (mcp_server.settings.transport_security), NOT the object passed in —
    # mutating the original changes nothing the middleware reads.
    live_settings = mcp_server.settings.transport_security
    assert live_settings is not settings
    app = ah.wrap_allowed_hosts_sync(
        mcp_server.streamable_http_app(), live_settings
    )
    # TestClient runs the app lifespan, which starts the session
    # manager's task group (httpx ASGITransport does not).
    with TestClient(app, raise_server_exceptions=False) as client:
        # Unknown tunnel host -> 421 before it is added.
        r = _post(client, "aaa-111.trycloudflare.com")
        assert r.status_code == 421, r.status_code

        # Hot-swap: add the host to the file, no restart.
        ah.add_host("aaa-111.trycloudflare.com")

        # Same request now passes the Host check (it reaches the session
        # layer, which answers something other than 421).
        r = _post(client, "aaa-111.trycloudflare.com")
        assert r.status_code != 421, r.status_code

        # A never-added host is still rejected.
        r = _post(client, "evil.example.com")
        assert r.status_code == 421

        # Loopback still works throughout.
        r = _post(client, "localhost:21001")
        assert r.status_code != 421
