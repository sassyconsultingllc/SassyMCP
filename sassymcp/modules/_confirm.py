"""Confirm-token machinery for tier-based destructive-action confirmation.

Used by sassy_shell when a destructive pattern matches at MEDIUM or HIGH
tier and `interceptor.destructiveAction` is set to "confirm" — instead of
hard-blocking the call, sassy_shell returns a confirmation_required
response carrying a single-use, short-TTL token. The caller (the MCP
client / Claude) then calls `sassy_shell_confirm(token)` to actually
execute the command.

Tokens are bound to the EXACT command string, shell, and cwd at issue
time — replaying a token against a different command is rejected.

In-memory only: tokens vanish on server restart (deliberate; restart =
fresh authorization context).
"""

from __future__ import annotations

import os
import secrets
import time
from typing import Optional

# token -> {command, shell, cwd, tier, pattern, phrase_required, expires}
_PENDING: dict[str, dict] = {}

# 60s default TTL — long enough for a one-click confirm, short enough that
# stale tokens never accumulate in memory or get misused.
_DEFAULT_TTL = 60

# Hard cap on outstanding tokens to bound memory under abuse.
_MAX_PENDING = 64


def _purge_expired() -> None:
    now = time.time()
    stale = [t for t, e in _PENDING.items() if e["expires"] <= now]
    for t in stale:
        _PENDING.pop(t, None)


def make_token(
    command: str,
    shell: str,
    tier: str,
    pattern: str,
    phrase_required: Optional[str] = None,
    timeout_seconds: int = 30,
    cwd: Optional[str] = None,
    ttl_seconds: int = _DEFAULT_TTL,
) -> tuple[str, dict]:
    """Issue a fresh confirm token. Returns (token, entry_dict).

    The entry_dict mirrors what consume_token will hand back, useful for
    callers that want to embed the same context in their response.
    """
    _purge_expired()
    if len(_PENDING) >= _MAX_PENDING:
        # Drop the oldest to keep memory bounded.
        oldest = min(_PENDING.items(), key=lambda kv: kv[1]["expires"])
        _PENDING.pop(oldest[0], None)

    token = secrets.token_urlsafe(16)
    entry = {
        "command": command,
        "shell": shell,
        "cwd": cwd or os.getcwd(),
        "tier": tier,
        "pattern": pattern,
        "phrase_required": phrase_required,
        "timeout_seconds": timeout_seconds,
        "expires": time.time() + ttl_seconds,
        "issued": time.time(),
    }
    _PENDING[token] = entry
    return token, entry


def consume_token(
    token: str,
    confirm_phrase: str = "",
    cwd: Optional[str] = None,
) -> tuple[bool, Optional[dict], Optional[str]]:
    """Single-use redemption. Returns (ok, entry, error_message).

    Validates:
      - token exists and has not expired
      - if phrase_required is set, confirm_phrase matches it exactly
      - cwd at consume time matches cwd at issue time (best-effort safety)

    On success the token is removed from the pending table — replay yields
    'token not found'.
    """
    _purge_expired()
    entry = _PENDING.pop(token, None)
    if entry is None:
        return False, None, "token not found or expired"

    if entry["expires"] <= time.time():
        return False, None, "token expired"

    phrase_required = entry.get("phrase_required")
    if phrase_required and confirm_phrase.strip() != phrase_required:
        # Re-insert so a typo doesn't burn the token. Caller can retry once.
        # We DO bound retries by leaving expiry alone — a stale token still
        # ages out at the original deadline.
        _PENDING[token] = entry
        return False, None, (
            f"phrase mismatch — call again with confirm_phrase exactly "
            f"matching: {phrase_required!r}"
        )

    here = cwd or os.getcwd()
    if entry.get("cwd") and entry["cwd"] != here:
        return False, None, (
            f"cwd mismatch: token issued from {entry['cwd']!r}, "
            f"called from {here!r}"
        )

    return True, entry, None


def pending_count() -> int:
    _purge_expired()
    return len(_PENDING)
