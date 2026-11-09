# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-TZ81VI7INQNJ
"""Async HTTP client for the SassyMCP policy worker (protocol v1).

The client only ever sends redacted argument *summaries* — see
enforcement.summarize_args. Full tool arguments never leave the machine.

Transport failures surface as PolicyError subclasses; the enforcement
layer (not this client) decides fail-open vs fail-closed.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("sassymcp.remote_policy")

EVALUATE_PATH = "/v1/policy/evaluate"
STATUS_PATH = "/v1/policy/status"
PROTOCOL_VERSION = "1"
VALID_DECISIONS = ("allow", "ask", "deny")


class PolicyError(Exception):
    """Base: the worker could not be consulted or answered badly."""


class PolicyAuthError(PolicyError):
    """401/403 — the license key was rejected (revoked/expired/unknown).

    Handled specially by enforcement: auth failures ALWAYS fail closed,
    regardless of the fail_open setting, because failing open here would
    defeat the entire vendor half of the split.
    """


class PolicyProtocolError(PolicyError):
    """The worker answered, but the response failed validation."""


def _validate_decision(data: object) -> dict:
    """Normalize and validate an evaluate response. Raises PolicyProtocolError."""
    if not isinstance(data, dict):
        raise PolicyProtocolError(f"expected JSON object, got {type(data).__name__}")
    decision = data.get("decision")
    if decision not in VALID_DECISIONS:
        raise PolicyProtocolError(f"invalid decision {decision!r}")
    try:
        ttl = int(data.get("ttl_seconds", 0))
    except (TypeError, ValueError):
        raise PolicyProtocolError("ttl_seconds is not an integer")
    return {
        "decision": decision,
        "reason": str(data.get("reason", ""))[:500],
        "ttl_seconds": max(0, ttl),
        "policy_version": str(data.get("policy_version", ""))[:64],
        "usage": data.get("usage") if isinstance(data.get("usage"), dict) else {},
    }


class PolicyClient:
    """Thin async client. Stateless — one instance per process is fine."""

    def __init__(
        self,
        endpoint: str,
        *,
        license_key: str,
        instance_id: str | None = None,
        timeout: float = 3.0,
        server_version: str = "unknown",
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._base = endpoint.rstrip("/")
        self._license_key = license_key
        self._instance_id = instance_id or "unknown"
        self._timeout = float(timeout)
        self._server_version = server_version
        self._transport = transport  # test seam (httpx.MockTransport)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._license_key}",
            "Content-Type": "application/json",
            "X-SassyMCP-Instance": self._instance_id,
            "X-SassyMCP-Version": self._server_version,
            "X-SassyMCP-Protocol": PROTOCOL_VERSION,
        }

    async def evaluate(
        self,
        *,
        tool: str,
        group: str,
        destructive: bool,
        arg_summary: dict,
        profile: str,
        license_tier: str,
    ) -> dict:
        """Ask the worker for a decision. Returns the validated decision dict."""
        body = {
            "tool": tool,
            "group": group,
            "destructive": bool(destructive),
            "arg_summary": arg_summary,
            "profile": profile,
            "license_tier": license_tier,
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as http:
                resp = await http.post(
                    self._base + EVALUATE_PATH, headers=self._headers(), json=body
                )
        except httpx.HTTPError as e:
            raise PolicyError(f"policy worker unreachable: {e}") from e

        if resp.status_code in (401, 403):
            raise PolicyAuthError(
                f"policy worker rejected the license key (HTTP {resp.status_code})"
            )
        if resp.status_code == 429:
            retry = resp.headers.get("retry-after", "?")
            raise PolicyError(f"policy worker rate-limited (retry after {retry}s)")
        if resp.status_code != 200:
            raise PolicyError(f"policy worker returned HTTP {resp.status_code}")
        try:
            data = resp.json()
        except Exception as e:
            raise PolicyProtocolError(f"non-JSON response: {e}") from e
        return _validate_decision(data)

    async def health(self) -> dict:
        """Unauthenticated liveness check. Returns the status payload."""
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as http:
                resp = await http.get(self._base + STATUS_PATH)
        except httpx.HTTPError as e:
            raise PolicyError(f"policy worker unreachable: {e}") from e
        if resp.status_code != 200:
            raise PolicyError(f"policy worker status returned HTTP {resp.status_code}")
        try:
            return resp.json()
        except Exception as e:
            raise PolicyProtocolError(f"non-JSON status response: {e}") from e
