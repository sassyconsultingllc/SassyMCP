# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-D95BF7GPMJXI
"""Enforcement: the middleware that consults the remote policy worker.

Wiring mirrors ``server._wrap_all_tools``: walk the tool manager, wrap
each ``tool.fn`` once, force ``is_async``. Default-off — ``wrap_all_tools``
returns immediately unless :func:`is_enabled`.

Per-call flow inside the wrapper:

  1. Build the tool facts (group, destructive flag) and a *redacted*
     argument summary — full arguments never leave the machine.
  2. Check the signed decision cache (fresh hit → use it).
  3. Otherwise ask the worker. On transport/auth/protocol failure:
       - auth failure (key rejected) → ALWAYS deny, even when
         ``fail_open`` is set (failing open here would defeat the
         vendor half of the split);
       - other failures → allow iff ``remote_policy.fail_open`` is true
         (default false → deny).
  4. Map the decision: allow → run the tool; ask → ``ToolError`` with a
     refusal envelope (``confirm_required: true``); deny → ``ToolError``
     with a refusal envelope. Refusals are raised, not returned, so the
     MCP layer serves them with ``isError=true`` — a policy denial must
     never look like a successful tool result on the wire.

The local permission policy (``sassymcp/policy.py``) is NOT re-run here:
the shell and file tools already consult it inside their bodies, and it
remains the user-safety floor that no remote decision can override.

Every remote decision, cache hit, and fail-open/closed event is
audit-logged through the caller-supplied audit callable (the same
``log_tool_call(tool_name, args, elapsed_ms, error)`` convention the
audit middleware uses).
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import json
import logging
import os
import re
import time
from dataclasses import dataclass

from mcp.server.fastmcp.exceptions import ToolError

logger = logging.getLogger("sassymcp.remote_policy")

_PREVIEW_MAX = 120
_REDACTED = "[redacted]"
_SECRET_NAME_RE = re.compile(
    r"passw(or)?d|passwd|pwd|token|api[_-]?key|secret|bearer|authorization|private[_-]?key",
    re.IGNORECASE,
)
# A long, spaceless, high-entropy-looking value is almost certainly a
# credential or blob, not something the policy worker needs to see.
_SECRET_VALUE_RE = re.compile(r"^[A-Za-z0-9_\-+/=]{24,}$")


@dataclass
class RemoteDecision:
    decision: str  # allow | ask | deny
    reason: str = ""
    source: str = "remote"  # remote | cache | fail-open | fail-closed
    policy_version: str = ""
    ttl_seconds: int = 0


# ---------------------------------------------------------------------------
# Config + identity
# ---------------------------------------------------------------------------


def _cfg(key: str, default=None):
    """Runtime config with explicit env overrides for the two bootstrap keys."""
    env_map = {
        "remote_policy.enabled": "SASSYMCP_POLICY_ENABLED",
        "remote_policy.endpoint": "SASSYMCP_POLICY_ENDPOINT",
    }
    if key in env_map:
        raw = os.environ.get(env_map[key])
        if raw is not None:
            if isinstance(default, bool):
                return raw.strip().lower() in ("1", "true", "yes")
            return raw
    try:
        from sassymcp.modules.runtime_config import get as rc_get

        return rc_get(key, default)
    except Exception:
        return default


def _license_identity() -> tuple[str | None, str | None]:
    """(ls_license_key, ls_instance_id) from the local license file. Never raises."""
    try:
        from sassymcp._paths import LICENSE_FILE

        data = json.loads(LICENSE_FILE.read_text())
        if isinstance(data, dict):
            return data.get("ls_license_key"), data.get("ls_instance_id")
    except Exception:
        pass
    return None, None


_warned: set[str] = set()


def is_enabled() -> bool:
    """True only when the gate is fully configured: enabled + endpoint + key."""
    enabled = bool(_cfg("remote_policy.enabled", False))
    endpoint = str(_cfg("remote_policy.endpoint", "") or "").strip()
    if not (enabled and endpoint):
        return False
    key, _ = _license_identity()
    if not key:
        if "no-key" not in _warned:
            _warned.add("no-key")
            logger.warning(
                "remote_policy enabled but no LemonSqueezy license key found — "
                "gate inactive until a license is activated"
            )
        return False
    return True


def get_status() -> dict:
    """Operator-facing status. Safe to surface — contains no secrets."""
    from . import cache as _cache

    endpoint = str(_cfg("remote_policy.endpoint", "") or "").strip()
    key, instance_id = _license_identity()
    return {
        "enabled": is_enabled(),
        "endpoint_configured": bool(endpoint),
        "endpoint": endpoint[:80],
        "has_license_key": bool(key),
        "instance_id": instance_id or "unknown",
        "fail_open": bool(_cfg("remote_policy.fail_open", False)),
        "timeout_seconds": float(_cfg("remote_policy.timeout_seconds", 3.0)),
        "cache_ttl_seconds": int(_cfg("remote_policy.cache_ttl_seconds", 300)),
        "send_arg_preview": bool(_cfg("remote_policy.send_arg_preview", True)),
        "cache_entries": _cache.entry_count(),
    }


# ---------------------------------------------------------------------------
# Argument summarization (redacted previews only)
# ---------------------------------------------------------------------------


def redact_preview(name: str, value) -> str:
    """Render one argument as a redacted, truncated preview string."""
    text = value if isinstance(value, str) else repr(value)
    if _SECRET_NAME_RE.search(str(name)):
        return _REDACTED
    if _SECRET_VALUE_RE.match(text.strip()):
        return _REDACTED
    if len(text) > _PREVIEW_MAX:
        return text[:_PREVIEW_MAX] + "…"
    return text


def summarize_args(args, kwargs, *, send_preview: bool = True) -> dict:
    """Build the arg_summary payload. Never includes full argument values."""
    summary: dict[str, str] = {}
    items = [(f"arg{i}", v) for i, v in enumerate(args)] + list(kwargs.items())
    for name, value in items:
        name = str(name)
        if not send_preview:
            summary[name] = f"<{type(value).__name__}>"
        else:
            summary[name] = redact_preview(name, value)
    return summary


def _tool_facts(tool_name: str) -> tuple[str, bool]:
    """(group, destructive) from the loader and the annotation set."""
    group, destructive = "", False
    try:
        from sassymcp.modules._tool_loader import get_group_for_tool

        group = get_group_for_tool(tool_name) or ""
    except Exception:
        pass
    try:
        from sassymcp.tool_annotations import TOOL_ANNOTATIONS

        destructive = bool(
            TOOL_ANNOTATIONS.get(tool_name, {}).get("destructiveHint", False)
        )
    except Exception:
        pass
    return group, destructive


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


def _default_client_factory():
    from .client import PolicyClient

    endpoint = str(_cfg("remote_policy.endpoint", "") or "").strip()
    key, instance_id = _license_identity()
    try:
        from sassymcp import __version__ as _v
    except Exception:
        _v = "unknown"
    return PolicyClient(
        endpoint,
        license_key=key or "",
        instance_id=instance_id,
        timeout=float(_cfg("remote_policy.timeout_seconds", 3.0)),
        server_version=_v,
    )


async def _decide(
    tool_name: str,
    group: str,
    destructive: bool,
    arg_summary: dict,
    *,
    client_factory,
    profile: str = "full",
    license_tier: str = "unknown",
) -> RemoteDecision:
    from . import cache as _cache
    from .client import PolicyAuthError

    max_ttl = int(_cfg("remote_policy.cache_ttl_seconds", 300))

    hit = _cache.get(tool_name, group, destructive, profile)
    if hit is not None:
        return RemoteDecision(
            decision=hit["decision"],
            reason=hit.get("reason", ""),
            source="cache",
            policy_version=hit.get("policy_version", ""),
        )

    try:
        client = client_factory()
        resp = await client.evaluate(
            tool=tool_name,
            group=group,
            destructive=destructive,
            arg_summary=arg_summary,
            profile=profile,
            license_tier=license_tier,
        )
    except PolicyAuthError as e:
        # Auth failures always fail closed — failing open here would
        # defeat the vendor half of the split (revoked key ⇒ deny).
        # The raw auth detail is logged server-side; clients get a
        # generic reason so no internal error text leaks over MCP.
        logger.warning("remote policy auth failure for %s: %s", tool_name, e)
        return RemoteDecision(
            decision="deny",
            reason="policy worker rejected the license key; fail-closed per config",
            source="fail-closed",
        )
    except Exception as e:
        # Log the raw transport/DNS/proxy detail server-side; clients get
        # a generic reason — httpx exception text can carry hostnames,
        # proxy internals, or TLS details that don't belong in an MCP
        # error surfaced to a third-party model.
        logger.warning("remote policy worker unreachable for %s: %s", tool_name, e)
        if bool(_cfg("remote_policy.fail_open", False)):
            return RemoteDecision(
                decision="allow",
                reason="policy worker unreachable; fail-open per config",
                source="fail-open",
            )
        return RemoteDecision(
            decision="deny",
            reason="policy worker unreachable; fail-closed per config",
            source="fail-closed",
        )

    ttl = max(0, min(int(resp.get("ttl_seconds", 0)), max_ttl))
    if ttl > 0:
        _cache.put(tool_name, group, destructive, profile, resp, ttl)
    return RemoteDecision(
        decision=resp["decision"],
        reason=resp.get("reason", ""),
        source="remote",
        policy_version=resp.get("policy_version", ""),
        ttl_seconds=ttl,
    )


# ---------------------------------------------------------------------------
# Wrapping
# ---------------------------------------------------------------------------


async def _call(fn, args, kwargs):
    if inspect.iscoroutinefunction(fn):
        return await fn(*args, **kwargs)
    # Sync tool bodies must never run inline on the event loop.
    return await asyncio.to_thread(fn, *args, **kwargs)


def _audit(audit_log, tool_name: str, arg_summary: dict, decision: RemoteDecision) -> None:
    if audit_log is None:
        return
    try:
        audit_log(
            f"{tool_name}[remote_policy]",
            {
                "decision": decision.decision,
                "source": decision.source,
                "policy_version": decision.policy_version,
                "arg_summary": arg_summary,
            },
            0,
            None
            if decision.decision == "allow"
            else f"remote policy {decision.decision} ({decision.source}): {decision.reason}",
        )
    except Exception:
        pass  # audit must never break tools


def _declared_kwargs(fn, kwargs: dict) -> dict:
    """Filter kwargs to the wrapped function's declared parameters.

    FastMCP injects objects (e.g. Context) that are not part of the tool's
    schema; the redacted arg summary must only ever describe declared
    arguments. If the signature can't be inspected, return kwargs as-is.
    """
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return dict(kwargs)
    declared = {
        name
        for name, p in params.items()
        if p.kind
        not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    }
    return {k: v for k, v in kwargs.items() if k in declared}


def wrap_fn(fn, *, audit_log=None, client_factory=None):
    """Wrap one tool callable with the remote-policy gate.

    Idempotent: wrapping an already-wrapped callable returns it unchanged.
    """
    if getattr(fn, "_remote_policy_wrapped", False):
        logger.debug("wrap_fn: %s already wrapped, skipping", getattr(fn, "__name__", fn))
        return fn
    factory = client_factory or _default_client_factory

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        tool_name = fn.__name__
        if not is_enabled():
            return await _call(fn, args, kwargs)
        start = time.monotonic()
        send_preview = bool(_cfg("remote_policy.send_arg_preview", True))
        group, destructive = _tool_facts(tool_name)
        # Filter kwargs to the wrapped function's declared parameters before
        # summarizing: the MCP layer can inject objects like Context that are
        # not part of the tool's schema and must never reach the worker.
        summary_kwargs = _declared_kwargs(fn, kwargs)
        arg_summary = summarize_args(args, summary_kwargs, send_preview=send_preview)
        decision = await _decide(
            tool_name, group, destructive, arg_summary, client_factory=factory
        )
        _audit(audit_log, tool_name, arg_summary, decision)
        logger.debug(
            "remote policy %s/%s for %s: %s",
            decision.decision,
            decision.source,
            tool_name,
            decision.reason,
        )
        if decision.decision == "allow":
            return await _call(fn, args, kwargs)
        if decision.decision == "ask":
            # Scaffold limitation: there is no generic confirm-token flow
            # at the middleware layer, so "ask" surfaces as a refusal the
            # caller can satisfy explicitly where the tool supports it.
            # Raised (not returned) so the MCP layer marks isError=true.
            raise ToolError(
                json.dumps(
                    {
                        "error": "Remote policy requires explicit confirmation: "
                        + decision.reason,
                        "confirm_required": True,
                        "tool": tool_name,
                        "policy_version": decision.policy_version,
                        "retryable": False,
                    },
                    indent=2,
                )
            )
        raise ToolError(
            json.dumps(
                {
                    "error": f"Denied by remote policy ({decision.source}): "
                    + decision.reason,
                    "tool": tool_name,
                    "policy_version": decision.policy_version,
                    "retryable": False,
                },
                indent=2,
            )
        )

    wrapper._remote_policy_wrapped = True  # noqa: SLF001 — same pattern as audit wrap
    return wrapper


def wrap_all_tools(tool_manager, *, audit_log=None, client_factory=None) -> dict:
    """Walk the tool manager and gate every tool. No-op unless is_enabled().

    Mirrors server._wrap_all_tools (single-wrap flag, is_async forcing).
    Returns a small report dict.
    """
    if not is_enabled():
        return {"enabled": False, "wrapped": 0}
    tools = tool_manager._tools.values() if hasattr(tool_manager, "_tools") else []
    wrapped = 0
    for tool in tools:
        fn = getattr(tool, "fn", None)
        if fn is None:
            continue
        # wrap_fn is idempotent (single-wrap guard lives there, not here):
        # it returns the input unchanged when already wrapped.
        new_fn = wrap_fn(fn, audit_log=audit_log, client_factory=client_factory)
        if new_fn is not fn:
            wrapped += 1
        tool.fn = new_fn
        try:
            tool.is_async = True
        except Exception:
            pass
    logger.info(f"Remote policy gate applied to {wrapped} tools")
    return {"enabled": True, "wrapped": wrapped}
