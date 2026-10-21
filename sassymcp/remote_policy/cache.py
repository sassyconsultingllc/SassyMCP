# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-MR5WF18EAIBK
"""HMAC-signed cache of remote policy decisions.

Rationale: the worker is consulted per tool call; without a cache, a
worker outage (or an airplane) would wedge every tool. Cached decisions
are signed with the installation's license secret, so editing the cache
file cannot forge an "allow" — and anyone who holds the secret already
owns the box, so there is nothing left to protect.

Cache key: sha256(tool | group | destructive | profile). We deliberately
do NOT key on argument summaries: policy is about the *kind* of call,
and keying on args would both explode the cache and smear argument
content across cache keys.

Corruption is self-healing: an unreadable or tampered file is treated
as empty (a miss), never as an error.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time

logger = logging.getLogger("sassymcp.remote_policy")

_CACHE_NAME = "remote_policy_cache.json"


def _path():
    from sassymcp._paths import HOME

    return HOME / _CACHE_NAME


def _cache_key(tool: str, group: str, destructive: bool, profile: str) -> str:
    raw = "|".join([tool, group or "", "1" if destructive else "0", profile or ""])
    return hashlib.sha256(raw.encode()).hexdigest()


def _load() -> dict:
    try:
        data = json.loads(_path().read_text())
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save(data: dict) -> None:
    try:
        _path().parent.mkdir(parents=True, exist_ok=True)
        _path().write_text(json.dumps(data))
    except Exception as e:
        logger.warning(f"remote policy cache write failed (non-fatal): {e}")


def _sign(entry: dict) -> str:
    from sassymcp.license import _sign_payload  # first-party internal; one secret

    return _sign_payload(entry)


def _verify(entry: dict, signature: str) -> bool:
    from sassymcp.license import _verify_signature  # first-party internal

    return _verify_signature(entry, signature)


def get(tool: str, group: str, destructive: bool, profile: str) -> dict | None:
    """Return the cached decision dict, or None on miss/expiry/tamper."""
    key = _cache_key(tool, group, destructive, profile)
    record = _load().get(key)
    if not isinstance(record, dict):
        return None
    entry = record.get("entry")
    sig = record.get("sig")
    if not isinstance(entry, dict) or not isinstance(sig, str):
        return None
    if not _verify(entry, sig):
        logger.warning("remote policy cache entry failed signature check (ignored)")
        return None
    if float(entry.get("expires_at", 0)) <= time.time():
        return None
    return entry


def put(
    tool: str,
    group: str,
    destructive: bool,
    profile: str,
    decision: dict,
    ttl_seconds: int,
) -> None:
    """Cache a validated worker decision for ttl_seconds."""
    if ttl_seconds <= 0:
        return
    entry = {
        "decision": decision.get("decision"),
        "reason": str(decision.get("reason", ""))[:500],
        "policy_version": str(decision.get("policy_version", ""))[:64],
        "expires_at": time.time() + ttl_seconds,
    }
    try:
        sig = _sign(entry)
    except Exception as e:
        logger.warning(f"remote policy cache signing failed (non-fatal): {e}")
        return
    data = _load()
    data[_cache_key(tool, group, destructive, profile)] = {"entry": entry, "sig": sig}
    _save(data)


def clear() -> None:
    """Drop all cached decisions."""
    try:
        _path().unlink(missing_ok=True)
    except Exception:
        pass


def entry_count() -> int:
    """Best-effort count of cache records (0 when unreadable)."""
    data = _load()
    return sum(1 for v in data.values() if isinstance(v, dict))
