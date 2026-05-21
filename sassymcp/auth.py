"""SassyMCP Auth — Opt-in bearer token verification.

Supports two modes:
1. Static bearer token via SASSYMCP_AUTH_TOKEN env var
2. Scoped tokens from ~/.sassymcp/tokens.json

If neither is configured, auth is disabled entirely (default).
When auth IS configured but fails to initialize, the server refuses to start.

tokens.json format:
{
  "tokens": [
    {"token": "abc123", "client_id": "claude-desktop", "scopes": ["read", "write"]},
    {"token": "xyz789", "client_id": "grok-desktop", "scopes": ["read"]}
  ]
}

Security:
- Tokens compared with hmac.compare_digest (timing-safe)
- Token file must be owner-readable only (0o600 on Unix)
- Raw tokens never included in AccessToken return objects
- Token length/format validated before comparison
"""

import hashlib
import hmac
import json
import logging
import os
import re
import stat
import time
from pathlib import Path
from typing import Optional

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings

logger = logging.getLogger("sassymcp.auth")

from sassymcp._paths import TOKENS_FILE as _TOKENS_FILE
_MIN_TOKEN_LENGTH = 16
_MAX_TOKEN_LENGTH = 512

# Tokens are restricted to the URL-safe base64 alphabet (RFC 4648 §5):
# A-Z a-z 0-9 plus '-' and '_'. secrets.token_urlsafe() already emits
# this set, so existing generators stay compatible. The restriction
# protects callers that may interpolate the token into shell or SQL
# contexts later — keeping it free of metacharacters means a leaked
# token still can't be weaponized into a command-injection primitive.
_TOKEN_ALPHABET = re.compile(r"^[A-Za-z0-9_\-]+$")


def _check_file_permissions(path: Path) -> bool:
    """Verify token file is owner-only readable. Returns True if safe.

    POSIX: rejects mode bits that grant group/world read access.
    Windows: enumerates the DACL via icacls and rejects the file if
    BUILTIN\\Users, Authenticated Users, Everyone, or NT AUTHORITY\\INTERACTIVE
    have any access to it. If icacls isn't available we fall back to a
    permissive return (logged at warning level) — the file is still
    protected by the Bearer auth itself; ACL is defense-in-depth.
    """
    if os.name == "nt":
        return _check_windows_acl(path)
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
        if mode & (stat.S_IRGRP | stat.S_IROTH):
            logger.error(
                f"Token file {path} has unsafe permissions ({oct(mode)}). "
                "Must be 0600 or stricter. Run: chmod 600 " + str(path)
            )
            return False
    except OSError:
        pass
    return True


# Principals whose presence in a token file's DACL means "more than the
# owner can read this". Case-insensitive match against icacls output.
_UNSAFE_WIN_PRINCIPALS = (
    "BUILTIN\\Users",
    "Authenticated Users",
    "Everyone",
    "NT AUTHORITY\\INTERACTIVE",
    "NT AUTHORITY\\Authenticated Users",
)


def _check_windows_acl(path: Path) -> bool:
    """Return True iff path's DACL grants access ONLY to its owner.

    Uses icacls.exe (built into Windows since Vista). Parses the textual
    output for any of the "more than owner" principals; if any appear,
    refuses to proceed and asks the user to lock the file down.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["icacls.exe", str(path)],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning(
            f"icacls unavailable ({e}); skipping Windows ACL check on {path}. "
            "Bearer auth is still enforced, but the token file's ACL was not verified."
        )
        return True
    if result.returncode != 0:
        logger.warning(
            f"icacls returned non-zero on {path}: {result.stderr.strip()[:200]}. "
            "Skipping ACL check; bearer auth remains in force."
        )
        return True
    text = result.stdout or ""
    text_lower = text.lower()
    for bad in _UNSAFE_WIN_PRINCIPALS:
        if bad.lower() in text_lower:
            logger.error(
                f"Token file {path} grants access to '{bad}'. Lock it down with:\n"
                f"  icacls \"{path}\" /inheritance:r /grant:r \"%USERNAME%:F\""
            )
            return False
    return True


def _lockdown_windows_acl(path: Path) -> bool:
    """Best-effort: strip inherited ACEs and grant only the current user
    full control. Returns True on success.

    Idempotent. Safe to call from CLI subcommands and from the server
    bootstrap. Failures are logged at warning level — the bearer auth
    itself is still in force; this is defense-in-depth.
    """
    if os.name != "nt":
        return True
    import subprocess
    try:
        username = os.environ.get("USERNAME") or os.environ.get("USER") or ""
        if not username:
            logger.warning(f"USERNAME env var empty; cannot lock down {path}")
            return False
        # /inheritance:r removes inherited ACEs; /grant:r replaces any
        # existing grant to the current user with explicit Full control.
        result = subprocess.run(
            ["icacls.exe", str(path),
             "/inheritance:r",
             "/grant:r", f"{username}:F"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            logger.warning(
                f"icacls lockdown on {path} failed: {result.stderr.strip()[:200]}"
            )
            return False
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning(f"icacls lockdown skipped ({e})")
        return False


def _token_format_valid(token: str) -> bool:
    """Validate token length and character set.

    Enforces URL-safe base64 ([A-Za-z0-9_-]) so leaked tokens cannot be
    weaponised as shell metacharacters if a downstream caller interpolates
    them. secrets.token_urlsafe() (our generator) emits exactly this set.
    """
    if not token or len(token) < _MIN_TOKEN_LENGTH or len(token) > _MAX_TOKEN_LENGTH:
        return False
    return bool(_TOKEN_ALPHABET.match(token))


class SassyTokenVerifier(TokenVerifier):
    """Implements the MCP TokenVerifier protocol for bearer token auth.

    All comparisons are timing-safe. Raw tokens are never placed into
    AccessToken objects — a truncated hash is used as the token identifier.
    """

    def __init__(self):
        self._static_token: Optional[str] = os.environ.get("SASSYMCP_AUTH_TOKEN")
        self._token_map: dict[str, dict] = {}  # keyed by sha256 hash of token
        self._load_tokens()

    def _hash_token(self, token: str) -> str:
        """One-way hash for internal token keying."""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _token_id(self, token: str) -> str:
        """Short identifier for logging/AccessToken (not the raw token)."""
        return self._hash_token(token)[:12]

    def _load_tokens(self):
        """Load scoped tokens from tokens.json."""
        if not _TOKENS_FILE.exists():
            return

        if not _check_file_permissions(_TOKENS_FILE):
            raise PermissionError(f"Token file {_TOKENS_FILE} has unsafe permissions")

        try:
            data = json.loads(_TOKENS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise ValueError(f"Failed to parse tokens.json: {e}") from e

        for entry in data.get("tokens", []):
            tok = entry.get("token", "")
            if not _token_format_valid(tok):
                logger.warning(f"Skipping invalid token for client {entry.get('client_id', '?')}")
                continue

            expires_at = entry.get("expires_at")
            if expires_at is not None:
                try:
                    expires_at = int(expires_at)
                except (ValueError, TypeError):
                    logger.warning(f"Invalid expires_at for client {entry.get('client_id', '?')}, ignoring expiry")
                    expires_at = None

            token_hash = self._hash_token(tok)
            self._token_map[token_hash] = {
                "raw_token": tok,  # needed for hmac comparison
                "client_id": entry.get("client_id", "unknown"),
                "scopes": entry.get("scopes", []),
                "expires_at": expires_at,
            }

        logger.info(f"Loaded {len(self._token_map)} scoped token(s)")

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify a bearer token and return access info if valid.

        Lookup model:
          1. Hash the presented token (SHA-256).
          2. Dict-lookup by hash (O(1), no per-entry compare).
          3. On hit, single constant-time compare against the stored raw
             token to defeat any hash-collision games (cryptographically
             irrelevant for SHA-256 but cheap, so we keep the property).
          4. On miss, do one constant-time compare against a fixed dummy
             string to keep the timing envelope independent of "did the
             hash hit?". This is what the old "iterate everything" loop
             bought us; with the hashed key it's a single op, not N.

        Result: token-table size no longer affects verify_token latency
        (relevant once a user accumulates many client_id entries).
        """
        if not _token_format_valid(token):
            return None

        # Static env token wins (it's always exactly one comparison anyway).
        if self._static_token and hmac.compare_digest(token, self._static_token):
            return AccessToken(
                token=self._token_id(token),
                client_id="static-env",
                scopes=["read", "write", "admin"],
            )

        token_hash = self._hash_token(token)
        entry = self._token_map.get(token_hash)
        if entry is None:
            # Constant-time miss path so an attacker can't time us into
            # confirming a partial hash match.
            hmac.compare_digest(token, "x" * len(token))
            return None

        if not hmac.compare_digest(token, entry["raw_token"]):
            # Hash collision (mathematically negligible for SHA-256, but
            # we still gate on the raw compare so any future hash swap
            # stays safe).
            return None

        # Check expiry
        if entry.get("expires_at") and entry["expires_at"] < int(time.time()):
            logger.warning(f"Expired token for client {entry['client_id']}")
            return None

        return AccessToken(
            token=self._token_id(token),
            client_id=entry["client_id"],
            scopes=entry["scopes"],
            expires_at=entry.get("expires_at"),
        )


def get_auth_config(server_url: str = "http://localhost:21001") -> Optional[dict]:
    """Return auth kwargs for FastMCP if auth is configured.

    Returns None if auth is not configured (no token env var, no tokens file).
    Raises on auth misconfiguration — fail closed, never degrade to open.

    OAuth discovery URLs (advertised in WWW-Authenticate / protected-resource
    metadata) are env-driven so a public deployment behind a Cloudflare tunnel
    or OAuth-proxy worker doesn't leak `localhost` to remote clients:

      SASSYMCP_RESOURCE_URL — public URL of the resource server (used as
        `resource_server_url`; drives WWW-Authenticate `resource_metadata`).
        Set this to the URL clients actually reach (e.g. the OAuth proxy's
        /mcp URL) so 401 responses redirect them somewhere reachable.

      SASSYMCP_OAUTH_ISSUER — URL of the OAuth authorization server (used as
        `issuer_url`; populates `authorization_servers` in PRM). Set this to
        the OAuth proxy host that actually serves /.well-known/oauth-authorization-server,
        /authorize, /token, /register.

    If unset, both fall back to `server_url` (legacy behaviour — fine for
    pure-localhost dev, broken for any public deployment).
    """
    has_env_token = bool(os.environ.get("SASSYMCP_AUTH_TOKEN"))
    has_tokens_file = _TOKENS_FILE.exists()

    if not has_env_token and not has_tokens_file:
        return None

    # This will raise if tokens.json is corrupt, has bad permissions, etc.
    # Caller must NOT catch this — auth misconfiguration is fatal.
    verifier = SassyTokenVerifier()

    resource_server_url = os.environ.get("SASSYMCP_RESOURCE_URL", server_url)
    issuer_url = os.environ.get("SASSYMCP_OAUTH_ISSUER", server_url)

    if resource_server_url != server_url or issuer_url != server_url:
        logger.info(
            f"OAuth discovery: resource={resource_server_url} issuer={issuer_url}"
        )

    return {
        "token_verifier": verifier,
        "auth": AuthSettings(
            issuer_url=issuer_url,
            resource_server_url=resource_server_url,
        ),
    }
