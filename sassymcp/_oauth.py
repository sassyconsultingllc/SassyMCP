# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-OAUTH01
"""SassyMCP OAuth authorization server.

Makes this server its own OAuth 2.0 authorization server (RFC 6749 with
PKCE, RFC 8414 metadata, RFC 7591 dynamic client registration) so MCP
clients can obtain bearer tokens through the standard OAuth dance
instead of hand-copying a token from the startup banner.

Endpoints (mounted by the MCP SDK's ``create_auth_routes``):

    GET  /.well-known/oauth-authorization-server   authorization-server metadata
    POST /register                                dynamic client registration
    GET  /authorize                               authorization endpoint
    POST /token                                   code/refresh-token exchange
    POST /revoke                                  token revocation

Plus SassyMCP's own operator-consent page (mounted separately in
``server.run_http``):

    GET  /oauth/consent
    POST /oauth/consent

Why the consent page exists
---------------------------
The SDK's ``/authorize`` handler validates the request and then 302s to
whatever URL ``provider.authorize()`` returns. If that URL were the
client's ``redirect_uri`` directly (auto-approve), ANY client that can
reach this server's HTTP port could mint itself a bearer token with zero
operator involvement: register via DCR, drive ``/authorize`` from the
operator's browser, receive the code at its own redirect URI, exchange
it — full API access from one malicious webpage visit. The consent page
interposes the operator: the code is only minted after the person
running the server sees the client's name and exact redirect URI and
clicks Approve.

CSRF hardening: the approval POST is bound to the browser session that
loaded the consent page via a SameSite=Lax, HttpOnly cookie carrying a
per-flow secret. A cross-origin auto-submitted form cannot present that
cookie, so a malicious site cannot forge the approval.

On non-loopback binds the browser is no longer proof of operator
identity, so the consent form additionally requires an existing
admin-scoped bearer token (mirrors the ``PRE_AUTH_SECRET`` pattern of
the ``sassymcp-oauth`` Cloudflare worker).

Storage
-------
* ``oauth.json`` (``~/.sassymcp/``): registered clients + refresh tokens
  (refresh tokens stored as SHA-256 hashes, never raw). Owner-only
  permissions (0600); corrupt file → fail closed.
* ``tokens.json``: OAuth-issued *access* tokens live alongside the
  CLI-minted ones, so the existing ``SassyTokenVerifier`` validates them
  with zero changes to the ``/mcp`` auth path.
* Authorization codes + pending consent requests: in-memory only
  (10-minute TTL; a restart just means the client retries the flow).
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from sassymcp._paths import OAUTH_FILE
from sassymcp.auth import SassyTokenVerifier, _check_file_permissions

logger = logging.getLogger("sassymcp.oauth")

# Scope vocabulary shared with the rest of SassyMCP (see the setup
# wizard's default "read,write" and the bootstrap token's
# ["read","write","admin"]). Advisory — tools do not enforce scopes.
_OAUTH_SCOPES = ["read", "write", "admin"]
_DEFAULT_SCOPES = ["read", "write"]

_CODE_LIFETIME = 600  # authorization codes: 10 minutes, single-use
_ACCESS_LIFETIME = 3600  # access tokens: 1 hour
_REFRESH_LIFETIME = 30 * 86400  # refresh tokens: 30 days
_CONSENT_TTL = 600  # pending consent requests: 10 minutes
_CONSENT_COOKIE = "sassy_oauth_consent"
_MAX_REDIRECT_URIS = 10


class _OAuthStore:
    """JSON-backed store for OAuth clients and refresh tokens.

    File layout: ``{"clients": {client_id: {...}}, "refresh_tokens": {hash: {...}}}``.
    Fail closed: a missing file starts empty; an unreadable or corrupt
    file raises instead of silently dropping every client.
    """

    def __init__(self, path: Path | None = None):
        path = path if path is not None else OAUTH_FILE
        self._path = path
        self._data: dict = {"clients": {}, "refresh_tokens": {}}
        if path.exists():
            if not _check_file_permissions(path):
                raise PermissionError(f"OAuth store {path} has unsafe permissions")
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                raise ValueError(f"Failed to parse {path}: {e}") from e
            if not isinstance(raw, dict):
                raise ValueError(f"{path} is corrupt: top-level JSON must be an object")
            clients = raw.get("clients", {})
            refresh = raw.get("refresh_tokens", {})
            if not isinstance(clients, dict) or not isinstance(refresh, dict):
                raise ValueError(f"{path} is corrupt")
            self._data = {"clients": clients, "refresh_tokens": refresh}

    def save(self) -> None:
        from sassymcp._atomic import atomic_write_json

        atomic_write_json(self._path, self._data)
        if os.name == "nt":
            from sassymcp.auth import _lockdown_windows_acl

            _lockdown_windows_acl(self._path)
        else:
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass


def _validate_redirect_uri(uri: str) -> None:
    """Policy check for a client-registered redirect URI.

    Raises RegistrationError unless the URI is http(s), has no fragment
    or userinfo, and uses https unless it targets loopback.
    """
    try:
        parts = urlparse(uri)
    except Exception:
        raise RegistrationError("invalid_redirect_uri", f"Malformed redirect_uri: {uri}")
    if not parts.scheme or not parts.hostname:
        raise RegistrationError("invalid_redirect_uri", f"Malformed redirect_uri: {uri}")
    if parts.scheme not in ("http", "https"):
        raise RegistrationError(
            "invalid_redirect_uri", "redirect_uri must use http or https"
        )
    if parts.fragment:
        raise RegistrationError(
            "invalid_redirect_uri", "redirect_uri must not contain a fragment"
        )
    if parts.username or parts.password:
        raise RegistrationError(
            "invalid_redirect_uri", "redirect_uri must not contain userinfo"
        )
    host = (parts.hostname or "").lower()
    is_loopback = host in ("localhost", "127.0.0.1", "::1") or host.startswith("127.")
    if parts.scheme == "http" and not is_loopback:
        raise RegistrationError(
            "invalid_redirect_uri",
            "redirect_uri must use https except on loopback",
        )


class SassyOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """OAuth authorization-server provider backed by SassyMCP's token system.

    Access tokens minted here are written into the same ``tokens.json``
    the CLI manages, so ``SassyTokenVerifier`` (and therefore the
    ``/mcp`` endpoint) accepts them with no special-casing. Refresh
    tokens live in ``oauth.json`` as hashes.
    """

    def __init__(
        self,
        verifier: SassyTokenVerifier,
        issuer_url: str,
        store_path: Path | None = None,
    ):
        self._verifier = verifier
        self._issuer = issuer_url.rstrip("/")
        self._store = _OAuthStore(store_path or OAUTH_FILE)
        self._codes: dict[str, AuthorizationCode] = {}
        self._pending: dict[str, dict] = {}

    # -- housekeeping -------------------------------------------------
    def _purge_expired(self) -> None:
        now = time.time()
        self._codes = {c: v for c, v in self._codes.items() if v.expires_at > now}
        self._pending = {
            r: v for r, v in self._pending.items() if v["created_at"] + _CONSENT_TTL > now
        }

    def _peek_pending(self, request_id: str) -> dict | None:
        self._purge_expired()
        return self._pending.get(request_id)

    def _pop_pending(self, request_id: str) -> dict | None:
        self._purge_expired()
        return self._pending.pop(request_id, None)

    # -- OAuthAuthorizationServerProvider: clients --------------------
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        raw = self._store._data["clients"].get(client_id)
        if raw is None:
            return None
        try:
            return OAuthClientInformationFull.model_validate(raw)
        except Exception:
            logger.error(f"Stored OAuth client {client_id} failed validation; ignoring")
            return None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        uris = client_info.redirect_uris or []
        if not uris:
            raise RegistrationError(
                "invalid_redirect_uri", "At least one redirect_uri is required"
            )
        if len(uris) > _MAX_REDIRECT_URIS:
            raise RegistrationError(
                "invalid_redirect_uri",
                f"Too many redirect_uris (max {_MAX_REDIRECT_URIS})",
            )
        for uri in uris:
            _validate_redirect_uri(str(uri))
        self._store._data["clients"][client_info.client_id] = client_info.model_dump(
            mode="json"
        )
        self._store.save()
        logger.info(
            f"Registered OAuth client {client_info.client_id} "
            f"({client_info.client_name or 'unnamed'})"
        )

    # -- OAuthAuthorizationServerProvider: authorization --------------
    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Stash the validated request and redirect to the consent page.

        The SDK's AuthorizationHandler 302s to the URL returned here, so
        returning the consent-page URL interposes the operator's approval
        before any code is minted (see module docstring for why
        auto-approve would be a token-minting primitive for attackers).
        """
        request_id = secrets.token_urlsafe(24)
        # No explicit scope requested -> grant the client's registered
        # scopes (which default to ["read", "write"] at registration).
        scopes = params.scopes
        if scopes is None:
            scopes = client.scope.split(" ") if client.scope else []
        self._pending[request_id] = {
            "client_id": client.client_id,
            "redirect_uri": str(params.redirect_uri),
            "scopes": list(scopes),
            "code_challenge": params.code_challenge,
            "state": params.state,
            "resource": params.resource,
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "csrf_secret": secrets.token_urlsafe(24),
            "created_at": time.time(),
        }
        self._purge_expired()
        return f"{self._issuer}/oauth/consent?request_id={request_id}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        self._purge_expired()
        code = self._codes.get(authorization_code)
        if code is None or code.client_id != client.client_id:
            return None
        return code

    # -- OAuthAuthorizationServerProvider: tokens ---------------------
    async def _mint_token_pair(
        self, client_id: str, scopes: list[str], resource: str | None
    ) -> OAuthToken:
        grant = secrets.token_urlsafe(24)
        now = int(time.time())
        access_raw = self._verifier.issue_token(
            # NOTE: the OAuth client_id goes in raw (no "oauth:" prefix):
            # the SDK's RevocationHandler only revokes a token when
            # loaded_token.client_id == the authenticated client_id.
            client_id=client_id,
            scopes=scopes,
            expires_at=now + _ACCESS_LIFETIME,
            grant=grant,
        )
        refresh_raw = secrets.token_urlsafe(32)  # 256 bits
        key = hashlib.sha256(refresh_raw.encode("utf-8")).hexdigest()
        self._store._data["refresh_tokens"][key] = {
            "client_id": client_id,
            "scopes": list(scopes),
            "grant": grant,
            "resource": resource,
            "created_at": now,
            "expires_at": now + _REFRESH_LIFETIME,
        }
        self._store.save()
        logger.info(
            f"Minted OAuth token pair for client {client_id} "
            f"(scopes={scopes}, grant {grant[:12]}…)"
        )
        return OAuthToken(
            access_token=access_raw,
            token_type="Bearer",
            expires_in=_ACCESS_LIFETIME,
            scope=" ".join(scopes) if scopes else None,
            refresh_token=refresh_raw,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        # Single-use: pop first so a duplicate/concurrent exchange can never
        # replay. Client match, expiry, redirect_uri binding and PKCE were
        # already enforced by the SDK's TokenHandler before this call.
        code = self._codes.pop(authorization_code.code, None)
        if code is None:
            raise TokenError("invalid_grant", "Authorization code already used or unknown")
        return await self._mint_token_pair(client.client_id, code.scopes, code.resource)

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        key = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()
        raw = self._store._data["refresh_tokens"].get(key)
        if raw is None or raw["client_id"] != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=raw["client_id"],
            scopes=raw["scopes"],
            expires_at=raw["expires_at"],
            resource=raw.get("resource"),
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        # Rotate: pop first (single-use), sweep the old grant's access
        # tokens, then mint a fresh pair. Scope narrowing and expiry were
        # already enforced by the SDK's TokenHandler.
        key = hashlib.sha256(refresh_token.token.encode("utf-8")).hexdigest()
        stored = self._store._data["refresh_tokens"].pop(key, None)
        if stored is None:
            raise TokenError("invalid_grant", "Refresh token already used or unknown")
        self._verifier.revoke_grant_tokens(stored["grant"])
        self._store.save()
        return await self._mint_token_pair(client.client_id, scopes, stored.get("resource"))

    async def load_access_token(self, token: str) -> AccessToken | None:
        # OAuth-issued access tokens live in the same tokens.json as
        # CLI-minted ones, so the standard verifier handles all of them —
        # plus the static SASSYMCP_AUTH_TOKEN env token.
        return await self._verifier.verify_token(token)

    async def revoke_token(self, token: str) -> None:
        # RFC 7009 §2.2.1: revoking an unknown token is not an error.
        #
        # Note: despite the protocol typing this as `str`, the SDK's
        # RevocationHandler actually passes the *loaded token object*
        # (AccessToken | RefreshToken). Handle both shapes. AccessToken.token
        # is only a 12-char id prefix (raw tokens never go into AccessToken
        # objects), so those revoke by id prefix.
        raw: str | None = token if isinstance(token, str) else None
        if raw is None:
            maybe_raw = getattr(token, "token", None)
            if isinstance(token, RefreshToken) and isinstance(maybe_raw, str):
                raw = maybe_raw
            elif isinstance(token, AccessToken) and isinstance(maybe_raw, str):
                self._verifier.revoke_token_by_id_prefix(maybe_raw)
                return
            else:  # pragma: no cover - defensive
                return
        if self._verifier.revoke_token(raw):
            return
        key = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        stored = self._store._data["refresh_tokens"].pop(key, None)
        if stored is not None:
            self._verifier.revoke_grant_tokens(stored["grant"])
            self._store.save()
            logger.info(f"Revoked OAuth refresh token for client {stored['client_id']}")


# -- operator consent page ---------------------------------------------
_CONSENT_CSS = """
body{font-family:system-ui,-apple-system,sans-serif;background:#0f1115;color:#e8eaed;
margin:0;display:flex;justify-content:center;padding:48px 16px}
.card{max-width:560px;background:#1a1d24;border:1px solid #2c313a;border-radius:12px;padding:32px}
h1{font-size:20px;margin:0 0 16px}p{line-height:1.5}code{background:#262b34;
padding:2px 6px;border-radius:4px;word-break:break-all;font-size:13px}
.warn{background:#3a2b12;border:1px solid #8a5a17;border-radius:8px;padding:12px 14px;font-size:14px}
.err{background:#3d1414;border:1px solid #8a2323;border-radius:8px;padding:12px 14px;font-size:14px}
.row{display:flex;gap:12px;margin-top:20px}
button{flex:1;padding:12px;border-radius:8px;border:0;font-size:16px;cursor:pointer}
.approve{background:#1f7a3d;color:#fff}.deny{background:#3a3f47;color:#e8eaed}
input[type=password]{width:100%;padding:10px;border-radius:8px;border:1px solid #2c313a;
background:#262b34;color:#e8eaed;margin-top:8px;box-sizing:border-box}
label{font-size:14px;color:#b9bec7}
"""


def _consent_page(
    *,
    client_name: str,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    request_id: str,
    needs_operator_token: bool,
    error: str | None = None,
) -> str:
    scope_text = ", ".join(scopes) if scopes else "(no scopes requested)"
    token_field = ""
    if needs_operator_token:
        token_field = (
            "<p><label>This server is bound to a non-loopback address, so please "
            "confirm you are the operator by pasting an <strong>admin-scoped</strong> "
            "bearer token:<input type=\"password\" name=\"operator_token\" "
            "autocomplete=\"off\"></label></p>"
        )
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Authorize application — SassyMCP</title>
<style>{_CONSENT_CSS}</style></head>
<body><div class="card">
<h1>Authorize application</h1>
{err}
<p><strong>{html.escape(client_name)}</strong><br>
<code>{html.escape(client_id)}</code> is requesting access to your SassyMCP server.</p>
<p>Permissions: <code>{html.escape(scope_text)}</code></p>
<p>After approval, the authorization code will be sent to:<br>
<code>{html.escape(redirect_uri)}</code></p>
<p class="warn">Only approve applications you trust. If you do not recognize the
address above, click Deny — approving sends a login code to that address.</p>
{token_field}
<form method="post" action="/oauth/consent">
<input type="hidden" name="request_id" value="{html.escape(request_id)}">
<div class="row">
<button class="approve" type="submit" name="decision" value="approve">Approve</button>
<button class="deny" type="submit" name="decision" value="deny">Deny</button>
</div></form></div></body></html>"""


def _error_page(message: str) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Authorization error — SassyMCP</title>
<style>{_CONSENT_CSS}</style></head>
<body><div class="card"><h1>Authorization error</h1>
<p class="err">{html.escape(message)}</p>
<p>You can close this page and retry the authorization flow from your client.</p>
</div></body></html>"""


def create_consent_routes(
    provider: SassyOAuthProvider, *, host_is_loopback: bool
) -> list[Route]:
    """Build the ``/oauth/consent`` GET/POST routes for operator approval."""

    async def consent_get(request: Request) -> Response:
        request_id = request.query_params.get("request_id", "")
        pending = provider._peek_pending(request_id)
        if pending is None:
            return HTMLResponse(
                _error_page("Unknown or expired authorization request."), status_code=400
            )
        client = await provider.get_client(pending["client_id"])
        client_name = (client.client_name if client else None) or pending["client_id"]
        resp = HTMLResponse(
            _consent_page(
                client_name=client_name,
                client_id=pending["client_id"],
                redirect_uri=pending["redirect_uri"],
                scopes=pending["scopes"],
                request_id=request_id,
                needs_operator_token=not host_is_loopback,
            )
        )
        # Bind the approval to the browser session that loaded this page:
        # the POST must present this SameSite=Lax cookie, which a
        # cross-origin attacker's auto-submitted form cannot carry.
        resp.set_cookie(
            _CONSENT_COOKIE,
            pending["csrf_secret"],
            max_age=_CONSENT_TTL,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return resp

    async def consent_post(request: Request) -> Response:
        form = await request.form()
        request_id = str(form.get("request_id", ""))
        pending = provider._peek_pending(request_id)
        cookie = request.cookies.get(_CONSENT_COOKIE, "")
        if (
            pending is None
            or not cookie
            or not hmac.compare_digest(cookie, pending["csrf_secret"])
        ):
            # Unknown/expired flow, or a forged cross-origin POST that could
            # not present the session cookie.
            return HTMLResponse(
                _error_page("Unknown, expired, or forged authorization request."),
                status_code=400,
            )

        async def rerender(error: str, status: int = 403) -> Response:
            client = await provider.get_client(pending["client_id"])
            client_name = (client.client_name if client else None) or pending["client_id"]
            resp = HTMLResponse(
                _consent_page(
                    client_name=client_name,
                    client_id=pending["client_id"],
                    redirect_uri=pending["redirect_uri"],
                    scopes=pending["scopes"],
                    request_id=request_id,
                    needs_operator_token=not host_is_loopback,
                    error=error,
                ),
                status_code=status,
            )
            resp.set_cookie(
                _CONSENT_COOKIE,
                pending["csrf_secret"],
                max_age=_CONSENT_TTL,
                httponly=True,
                samesite="lax",
                path="/",
            )
            return resp

        if not host_is_loopback:
            op_token = str(form.get("operator_token", ""))
            access = await provider._verifier.verify_token(op_token) if op_token else None
            if access is None or "admin" not in (access.scopes or []):
                return await rerender(
                    "An admin-scoped bearer token is required to approve on a "
                    "non-loopback bind."
                )

        # Consume the flow: one decision per authorization request.
        provider._pop_pending(request_id)
        redirect_uri = pending["redirect_uri"]
        state = pending["state"]

        if str(form.get("decision", "")) != "approve":
            url = construct_redirect_uri(
                redirect_uri, error="access_denied", state=state
            )
            return RedirectResponse(
                url, status_code=302, headers={"Cache-Control": "no-store"}
            )

        code = secrets.token_urlsafe(32)  # 256 bits of entropy
        provider._codes[code] = AuthorizationCode(
            code=code,
            scopes=pending["scopes"],
            expires_at=time.time() + _CODE_LIFETIME,
            client_id=pending["client_id"],
            code_challenge=pending["code_challenge"],
            redirect_uri=pending["redirect_uri"],
            redirect_uri_provided_explicitly=pending["redirect_uri_provided_explicitly"],
            resource=pending["resource"],
        )
        logger.info(f"Operator approved OAuth client {pending['client_id']}")
        url = construct_redirect_uri(redirect_uri, code=code, state=state)
        resp = RedirectResponse(url, status_code=302, headers={"Cache-Control": "no-store"})
        resp.delete_cookie(_CONSENT_COOKIE, path="/")
        return resp

    return [
        Route("/oauth/consent", endpoint=consent_get, methods=["GET"]),
        Route("/oauth/consent", endpoint=consent_post, methods=["POST"]),
    ]


def enable_oauth_server(mcp, issuer_url: str) -> SassyOAuthProvider | None:
    """Attach a SassyOAuthProvider to an already-built FastMCP instance.

    Called from ``run_http`` after the TLS policy is final, because the
    effective issuer (http vs https) is only known then. Returns the
    provider, or None when the issuer is not a valid OAuth issuer —
    with a warning, not a crash: ``/mcp`` keeps serving with bearer-token
    auth, the OAuth endpoints just stay off.
    """
    from mcp.server.auth.routes import validate_issuer_url
    from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions

    try:
        validate_issuer_url(AnyHttpUrl(issuer_url))
    except Exception as e:
        logger.warning(
            f"OAuth authorization server disabled: issuer {issuer_url} is not "
            f"a valid OAuth issuer ({e}). /mcp bearer-token auth is unaffected."
        )
        return None

    verifier = mcp._token_verifier
    if verifier is None:  # pragma: no cover - caller checks auth is active
        return None

    provider = SassyOAuthProvider(verifier=verifier, issuer_url=issuer_url)
    base = mcp.settings.auth
    mcp.settings.auth = base.model_copy(
        update={
            "issuer_url": AnyHttpUrl(issuer_url),
            "client_registration_options": ClientRegistrationOptions(
                enabled=True,
                valid_scopes=list(_OAUTH_SCOPES),
                default_scopes=list(_DEFAULT_SCOPES),
            ),
            "revocation_options": RevocationOptions(enabled=True),
        }
    )
    # Note: FastMCP.__init__ forbids passing auth_server_provider AND
    # token_verifier together, but at app-build time the two are
    # independent flags — the verifier keeps guarding /mcp exactly as
    # before while the provider serves the OAuth routes. Setting the
    # private attr deliberately preserves the existing /mcp auth path
    # byte-for-byte instead of re-routing it through ProviderTokenVerifier.
    mcp._auth_server_provider = provider
    logger.info(f"OAuth authorization server enabled (issuer {issuer_url})")
    return provider
