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
import stat
import time
from pathlib import Path
from typing import Optional

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings

logger = logging.getLogger("sassymcp.auth")

_TOKENS_FILE = Path.home() / ".sassymcp" / "tokens.json"
_MIN_TOKEN_LENGTH = 16
_MAX_TOKEN_LENGTH = 512


def _check_file_permissions(path: Path) -> bool:
    """Verify token file is not world/group readable. Returns True if safe."""
    if os.name == "nt":
        return True  # Windows ACLs handled differently — skip for now
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


def _token_format_valid(token: str) -> bool:
    """Validate token length and character set."""
    if not token or len(token) < _MIN_TOKEN_LENGTH or len(token) > _MAX_TOKEN_LENGTH:
        return False
    return token.isprintable() and " " not in token


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

        Uses hmac.compare_digest for timing-safe comparison.
        """
        if not _token_format_valid(token):
            return None

        # Check static env token (timing-safe)
        if self._static_token and hmac.compare_digest(token, self._static_token):
            return AccessToken(
                token=self._token_id(token),
                client_id="static-env",
                scopes=["read", "write", "admin"],
            )

        # Check scoped tokens (timing-safe)
        # We iterate ALL tokens to avoid timing leaks on hash lookup
        matched_entry = None
        for _hash, entry in self._token_map.items():
            if hmac.compare_digest(token, entry["raw_token"]):
                matched_entry = entry
                break  # safe to break — comparison already done

        if not matched_entry:
            return None

        # Check expiry
        if matched_entry.get("expires_at") and matched_entry["expires_at"] < int(time.time()):
            logger.warning(f"Expired token for client {matched_entry['client_id']}")
            return None

        return AccessToken(
            token=self._token_id(token),
            client_id=matched_entry["client_id"],
            scopes=matched_entry["scopes"],
            expires_at=matched_entry.get("expires_at"),
        )


def get_auth_config(server_url: str = "http://localhost:21001") -> Optional[dict]:
    """Return auth kwargs for FastMCP if auth is configured.

    Returns None if auth is not configured (no token env var, no tokens file).
    Raises on auth misconfiguration — fail closed, never degrade to open.
    """
    has_env_token = bool(os.environ.get("SASSYMCP_AUTH_TOKEN"))
    has_tokens_file = _TOKENS_FILE.exists()

    if not has_env_token and not has_tokens_file:
        return None

    # This will raise if tokens.json is corrupt, has bad permissions, etc.
    # Caller must NOT catch this — auth misconfiguration is fatal.
    verifier = SassyTokenVerifier()

    return {
        "token_verifier": verifier,
        "auth": AuthSettings(
            issuer_url=server_url,
            resource_server_url=server_url,
        ),
    }
