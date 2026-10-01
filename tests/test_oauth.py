# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-OAUTHTEST
"""Tests for the SassyMCP OAuth authorization server (sassymcp/_oauth.py).

Covers: authorization-server metadata, dynamic client registration,
the /authorize -> /oauth/consent -> /token code flow (with PKCE),
refresh rotation, revocation, the consent CSRF binding, the
non-loopback operator-token gate, and fail-closed store handling.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse

import pytest

ISSUER = "http://127.0.0.1:21001"
REDIRECT = "http://127.0.0.1:8765/cb"


@pytest.fixture
def oauth_isolation(tmp_path, monkeypatch):
    """Isolated token + oauth stores; static env token for the verifier."""
    monkeypatch.setenv("SASSYMCP_AUTH_TOKEN", "test-static-oauth-token-0123456789ab")
    import sassymcp.auth as auth_mod

    tokens_file = tmp_path / "tokens.json"
    monkeypatch.setattr(auth_mod, "_TOKENS_FILE", tokens_file)
    return tmp_path


@pytest.fixture
def provider(oauth_isolation):
    from sassymcp._oauth import SassyOAuthProvider
    from sassymcp.auth import SassyTokenVerifier

    verifier = SassyTokenVerifier()
    prov = SassyOAuthProvider(
        verifier=verifier,
        issuer_url=ISSUER,
        store_path=oauth_isolation / "oauth.json",
    )
    return prov, verifier


def make_client(prov, host_is_loopback=True):
    from mcp.server.auth.routes import create_auth_routes
    from mcp.server.auth.settings import (
        AuthSettings,
        ClientRegistrationOptions,
        RevocationOptions,
    )
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from sassymcp._oauth import create_consent_routes

    auth = AuthSettings(
        issuer_url=ISSUER,
        resource_server_url=ISSUER,
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=["read", "write", "admin"],
            default_scopes=["read", "write"],
        ),
        revocation_options=RevocationOptions(enabled=True),
    )
    routes = create_auth_routes(
        prov,
        auth.issuer_url,
        None,
        auth.client_registration_options,
        auth.revocation_options,
    )
    routes += create_consent_routes(prov, host_is_loopback=host_is_loopback)
    return TestClient(Starlette(routes=routes))


@pytest.fixture
def http(provider):
    prov, _ = provider
    return make_client(prov)


def _pkce():
    verifier = "verifier-" + "z" * 40
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    return verifier, challenge


def _register(http, **kw):
    body = {
        "redirect_uris": [REDIRECT],
        "client_name": "test-client",
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
    }
    body.update(kw)
    r = http.post("/register", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _request_id(http, cid, challenge, redirect_uri=REDIRECT, scope=None):
    params = {
        "client_id": cid,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "xyz-state",
    }
    if scope is not None:
        params["scope"] = scope
    r = http.get("/authorize", params=params, follow_redirects=False)
    assert r.status_code == 302, r.text
    loc = r.headers["location"]
    assert "/oauth/consent" in loc
    return parse_qs(urlparse(loc).query)["request_id"][0]


def _approve(http, request_id, decision="approve", extra=None):
    r = http.get("/oauth/consent", params={"request_id": request_id})
    assert r.status_code == 200, r.text
    data = {"request_id": request_id, "decision": decision}
    data.update(extra or {})
    r = http.post("/oauth/consent", data=data, follow_redirects=False)
    assert r.status_code == 302, r.text
    return r.headers["location"]


def _exchange(http, cid, code, verifier_raw, redirect_uri=REDIRECT):
    return http.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": cid,
            "code_verifier": verifier_raw,
        },
    )


def _full_flow(http):
    """Register -> authorize -> approve -> exchange. Returns (cid, tokens)."""
    reg = _register(http)
    cid = reg["client_id"]
    verifier_raw, challenge = _pkce()
    rid = _request_id(http, cid, challenge)
    loc = _approve(http, rid)
    code = parse_qs(urlparse(loc).query)["code"][0]
    r = _exchange(http, cid, code, verifier_raw)
    assert r.status_code == 200, r.text
    return cid, r.json()


def _verify(provider, token):
    prov, _ = provider
    return asyncio.run(prov.load_access_token(token))


# -- metadata ---------------------------------------------------------
def test_metadata(http):
    r = http.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    md = r.json()
    assert md["issuer"] == ISSUER + "/"
    assert md["authorization_endpoint"] == ISSUER + "/authorize"
    assert md["token_endpoint"] == ISSUER + "/token"
    assert md["registration_endpoint"] == ISSUER + "/register"
    assert md["code_challenge_methods_supported"] == ["S256"]
    assert md["response_types_supported"] == ["code"]


# -- registration -----------------------------------------------------
def test_register_persists_client(http, oauth_isolation):
    reg = _register(http, client_name="persist-me")
    stored = json.loads((oauth_isolation / "oauth.json").read_text())
    assert reg["client_id"] in stored["clients"]
    assert stored["clients"][reg["client_id"]]["client_name"] == "persist-me"


def test_register_rejects_plain_http_redirect(http):
    r = http.post(
        "/register",
        json={
            "redirect_uris": ["http://example.com/cb"],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_redirect_uri"


def test_register_accepts_https_redirect(http):
    reg = _register(http, redirect_uris=["https://example.com/cb"])
    assert reg["client_id"]


def test_register_rejects_missing_redirect_uri(http):
    r = http.post(
        "/register",
        json={
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
        },
    )
    assert r.status_code == 400


def test_register_rejects_fragment_redirect(http):
    r = http.post(
        "/register",
        json={
            "redirect_uris": ["https://example.com/cb#frag"],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
        },
    )
    assert r.status_code == 400


# -- authorize validation ----------------------------------------------
def test_authorize_unknown_client(http):
    _, challenge = _pkce()
    r = http.get(
        "/authorize",
        params={
            "client_id": "nope",
            "response_type": "code",
            "redirect_uri": REDIRECT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    # unknown client -> direct error, NOT a redirect to an attacker URL
    assert r.status_code == 400


def test_authorize_unregistered_redirect_uri(http):
    reg = _register(http)
    _, challenge = _pkce()
    r = http.get(
        "/authorize",
        params={
            "client_id": reg["client_id"],
            "response_type": "code",
            "redirect_uri": "http://127.0.0.1:9999/evil",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_authorize_requires_pkce(http):
    reg = _register(http)
    r = http.get(
        "/authorize",
        params={
            "client_id": reg["client_id"],
            "response_type": "code",
            "redirect_uri": REDIRECT,
        },
        follow_redirects=False,
    )
    # missing PKCE -> the handler redirects back to the *registered*
    # redirect_uri with the error (RFC 6749 4.1.2.1), never to consent
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith(REDIRECT)
    assert parse_qs(urlparse(loc).query)["error"] == ["invalid_request"]
    assert "/oauth/consent" not in loc


# -- consent page ------------------------------------------------------
def test_consent_page_shows_client_and_redirect(http):
    reg = _register(http, client_name="my-cool-app")
    _, challenge = _pkce()
    rid = _request_id(http, reg["client_id"], challenge)
    r = http.get("/oauth/consent", params={"request_id": rid})
    assert r.status_code == 200
    assert "my-cool-app" in r.text
    assert REDIRECT in r.text  # exact redirect URI shown (phishing defense)


def test_consent_unknown_request(http):
    r = http.get("/oauth/consent", params={"request_id": "bogus"})
    assert r.status_code == 400


def test_consent_csrf_without_cookie_rejected(http):
    """A forged cross-origin POST cannot present the session cookie."""
    reg = _register(http)
    _, challenge = _pkce()
    rid = _request_id(http, reg["client_id"], challenge)
    http.get("/oauth/consent", params={"request_id": rid})
    http.cookies.clear()  # attacker has no cookie
    r = http.post(
        "/oauth/consent",
        data={"request_id": rid, "decision": "approve"},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_consent_deny_redirects_with_error(http):
    reg = _register(http)
    _, challenge = _pkce()
    rid = _request_id(http, reg["client_id"], challenge)
    loc = _approve(http, rid, decision="deny")
    q = parse_qs(urlparse(loc).query)
    assert q["error"] == ["access_denied"]
    assert q["state"] == ["xyz-state"]


# -- full code flow -----------------------------------------------------
def test_full_code_flow(http, provider, oauth_isolation):
    cid, tok = _full_flow(http)
    assert tok["token_type"] == "Bearer"
    assert tok["expires_in"] == 3600
    assert "access_token" in tok and "refresh_token" in tok

    access = _verify(provider, tok["access_token"])
    assert access is not None
    assert access.client_id == cid
    assert access.scopes == ["read", "write"]  # registration defaults

    # access token persisted to tokens.json alongside CLI tokens
    tokens_file = oauth_isolation / "tokens.json"
    assert tokens_file.exists()
    entries = json.loads(tokens_file.read_text())["tokens"]
    assert any(e["client_id"] == cid for e in entries)

    # refresh token stored only as a hash in oauth.json
    stored = json.loads((oauth_isolation / "oauth.json").read_text())
    assert len(stored["refresh_tokens"]) == 1
    assert tok["refresh_token"] not in json.dumps(stored["refresh_tokens"])


def test_explicit_scopes_honored(http, provider):
    reg = _register(http)
    cid = reg["client_id"]
    verifier_raw, challenge = _pkce()
    rid = _request_id(http, cid, challenge, scope="read")
    loc = _approve(http, rid)
    code = parse_qs(urlparse(loc).query)["code"][0]
    r = _exchange(http, cid, code, verifier_raw)
    assert r.status_code == 200
    access = _verify(provider, r.json()["access_token"])
    assert access.scopes == ["read"]


def test_pkce_mismatch_rejected(http):
    reg = _register(http)
    _, challenge = _pkce()
    rid = _request_id(http, reg["client_id"], challenge)
    loc = _approve(http, rid)
    code = parse_qs(urlparse(loc).query)["code"][0]
    r = _exchange(http, reg["client_id"], code, "wrong-verifier-" + "q" * 40)
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


def test_code_single_use(http):
    reg = _register(http)
    cid = reg["client_id"]
    verifier_raw, challenge = _pkce()
    rid = _request_id(http, cid, challenge)
    loc = _approve(http, rid)
    code = parse_qs(urlparse(loc).query)["code"][0]
    assert _exchange(http, cid, code, verifier_raw).status_code == 200
    r2 = _exchange(http, cid, code, verifier_raw)
    assert r2.status_code == 400
    assert r2.json()["error"] == "invalid_grant"


# -- refresh ------------------------------------------------------------
def test_refresh_rotation(http, provider):
    cid, tok = _full_flow(http)
    r = http.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": tok["refresh_token"],
            "client_id": cid,
        },
    )
    assert r.status_code == 200, r.text
    tok2 = r.json()
    assert tok2["access_token"] != tok["access_token"]
    assert tok2["refresh_token"] != tok["refresh_token"]

    # old refresh token is dead (single-use rotation)
    r = http.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": tok["refresh_token"],
            "client_id": cid,
        },
    )
    assert r.status_code == 400

    # old grant's access token was swept on rotation
    assert _verify(provider, tok["access_token"]) is None
    # new access token works
    assert _verify(provider, tok2["access_token"]) is not None


# -- revocation ----------------------------------------------------------
def _revoke(http, token, cid, hint=None):
    # NOTE: the SDK's RevocationRequest model requires the client_secret
    # field even for public ("none") clients -- send it empty.
    data = {"token": token, "client_id": cid, "client_secret": ""}
    if hint:
        data["token_type_hint"] = hint
    return http.post("/revoke", data=data)


def test_revoke_access_token(http, provider):
    cid, tok = _full_flow(http)
    r = _revoke(http, tok["access_token"], cid)
    assert r.status_code == 200, r.text
    assert _verify(provider, tok["access_token"]) is None


def test_revoke_refresh_token_sweeps_access(http, provider):
    cid, tok = _full_flow(http)
    r = _revoke(http, tok["refresh_token"], cid, hint="refresh_token")
    assert r.status_code == 200, r.text
    prov, _ = provider
    assert asyncio.run(prov.load_refresh_token({"client_id": cid}, tok["refresh_token"])) is None
    assert _verify(provider, tok["access_token"]) is None


def test_revoke_unknown_token_is_noop(http):
    cid, _ = _full_flow(http)
    r = _revoke(http, "z" * 43, cid)
    assert r.status_code == 200


def test_cross_client_revoke_denied(http, provider):
    cid, tok = _full_flow(http)
    other = _register(http)["client_id"]
    r = _revoke(http, tok["access_token"], other)
    assert r.status_code == 200  # RFC 7009: no error signal...
    # ...but the victim's token still verifies
    assert _verify(provider, tok["access_token"]) is not None


# -- non-loopback consent gate -------------------------------------------
def test_non_loopback_consent_requires_admin_token(provider):
    prov, verifier = provider
    http = make_client(prov, host_is_loopback=False)
    reg = _register(http)
    _, challenge = _pkce()
    rid = _request_id(http, reg["client_id"], challenge)

    # page asks for the operator token
    r = http.get("/oauth/consent", params={"request_id": rid})
    assert r.status_code == 200
    assert "operator_token" in r.text or "operator" in r.text

    # no token -> 403, flow stays alive
    r = http.post(
        "/oauth/consent", data={"request_id": rid, "decision": "approve"},
        follow_redirects=False,
    )
    assert r.status_code == 403

    # non-admin token -> 403
    import time as _t

    pleb = verifier.issue_token("pleb", ["read"], int(_t.time()) + 3600)
    r = http.post(
        "/oauth/consent",
        data={"request_id": rid, "decision": "approve", "operator_token": pleb},
        follow_redirects=False,
    )
    assert r.status_code == 403

    # admin token -> approved
    admin = verifier.issue_token("admin", ["read", "write", "admin"], int(_t.time()) + 3600)
    r = http.post(
        "/oauth/consent",
        data={"request_id": rid, "decision": "approve", "operator_token": admin},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "code=" in r.headers["location"]


# -- fail-closed ----------------------------------------------------------
def test_corrupt_oauth_store_fails_closed(oauth_isolation):
    import os

    from sassymcp._oauth import SassyOAuthProvider
    from sassymcp.auth import SassyTokenVerifier

    bad = oauth_isolation / "oauth.json"
    bad.write_text("{not json")
    os.chmod(bad, 0o600)  # pass the permissions gate, fail at the parse gate
    with pytest.raises(ValueError):
        SassyOAuthProvider(
            verifier=SassyTokenVerifier(),
            issuer_url=ISSUER,
            store_path=bad,
        )


def test_enable_oauth_server_rejects_non_https_lan_issuer():
    from unittest.mock import MagicMock

    from sassymcp._oauth import enable_oauth_server

    mcp = MagicMock()
    mcp._token_verifier = MagicMock()
    mcp._auth_server_provider = None
    # must not raise -- returns None with a warning instead
    assert enable_oauth_server(mcp, "http://192.168.1.5:21001") is None
    assert mcp._auth_server_provider is None


def test_enable_oauth_server_accepts_loopback(oauth_isolation, monkeypatch):
    from unittest.mock import MagicMock

    import sassymcp._oauth as oauth_mod
    from mcp.server.auth.settings import AuthSettings
    from sassymcp._oauth import enable_oauth_server
    from sassymcp.auth import SassyTokenVerifier

    monkeypatch.setattr(oauth_mod, "OAUTH_FILE", oauth_isolation / "oauth.json")
    mcp = MagicMock()
    mcp._token_verifier = SassyTokenVerifier()
    mcp.settings.auth = AuthSettings(issuer_url=ISSUER, resource_server_url=ISSUER)
    got = enable_oauth_server(mcp, ISSUER)
    assert got is not None
    assert mcp._auth_server_provider is got
    assert mcp.settings.auth.client_registration_options.enabled is True
    assert mcp.settings.auth.revocation_options.enabled is True
