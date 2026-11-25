# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-4DAPHZDYDMVP
"""Remote policy enforcement — the local half of the split architecture.

SassyMCP's split architecture keeps the *mechanism* local (this process,
executing tools on the user's machine) and moves the *vendor policy*
(license enforcement, revocation, per-customer tier gating, usage
metering) to a remote policy worker the vendor operates
(see workers/policy-worker/).

This package is deliberately boring and auditable. It:

  * asks the remote worker for an allow/ask/deny decision per tool call,
  * sends only redacted argument *summaries* — never full arguments,
  * caches HMAC-signed decisions so a worker outage doesn't wedge the user,
  * fails closed by default when the worker is unreachable and no fresh
    cache entry exists (configurable to fail open),
  * audit-logs every remote decision, cache hit, and fail-open/closed event.

It is OPT-IN and default-off: unless ``remote_policy.enabled`` is set
(and an endpoint plus a LemonSqueezy license key are present),
``wrap_all_tools()`` is a no-op and tool behavior is byte-for-byte
unchanged.

Relationship to the local permission policy (``sassymcp/policy.py``):
that module remains the *user-safety* floor — the shell and file tools
consult it inside their bodies, and nothing the remote worker says can
override it. The remote worker can only *further restrict* (deny, or
require confirmation). A remote "allow" never permits something the
local policy denies, because the local check still runs when the tool
body executes.

Public API:
    is_enabled()      -> bool
    get_status()      -> dict (safe to surface; contains no secrets)
    wrap_fn(fn, ...)  -> wrapped coroutine
    wrap_all_tools(tool_manager, *, audit_log=None, client_factory=None)
    RemoteDecision    (dataclass)
"""
from __future__ import annotations

from .enforcement import (
    RemoteDecision,
    get_status,
    is_enabled,
    wrap_all_tools,
    wrap_fn,
)

__all__ = [
    "RemoteDecision",
    "get_status",
    "is_enabled",
    "wrap_all_tools",
    "wrap_fn",
]
