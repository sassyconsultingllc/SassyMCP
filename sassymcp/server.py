# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-EZ25ZZUKOK6V
"""SassyMCP Server v1.0 — Production entry point.

Unified MCP server combining Windows desktop automation, Android device
control (ADB/scrcpy), security auditing, forensics tools, desktop vision,
cross-session communication, web inspection, GitHub operations, and workflow persona.

Features:
- Smart group loading with exponential decay usage tracking
- Per-group rate limiting and concurrency guards
- Audit middleware with structured error recovery
- Persistent tool state across sessions
- Observability (metrics, health, tool stats)
- Live reload in dev mode (SASSYMCP_DEV=1)
- OAuth2 bearer token auth (opt-in via SASSYMCP_AUTH_TOKEN)
- HTTP/SSE mode default (works with Claude Desktop, Grok Desktop, Cursor, Windsurf)
- Graceful shutdown with crosslink notification

Compatible with Claude Desktop, Grok Desktop, Cursor, Windsurf, and any MCP client.
"""

import asyncio
import functools
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

from sassymcp import __version__

# Logging configured early — before any module can log
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("sassymcp")

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from sassymcp.modules._tool_loader import (
    get_tracker,
    get_default_modules,
    get_all_modules,
    get_group_for_tool,
    get_group_for_module,
    register_tool_group,
    resolve_dependencies,
    validate_tool,
    enable_live_reload,
    compute_schema_version,
    TOOL_GROUPS,
)

from sassymcp.license import (
    fast_revocation_check, get_allowed_groups, validate_license, weekly_validation_check,
)


# ── Self-Signed Cert Generation ──────────────────────────────────────

def _generate_self_signed_cert():
    """Generate a self-signed SSL cert for HTTPS mode. Zero external deps."""
    from sassymcp._paths import HOME as cert_dir, SSL_CERT as cert_path, SSL_KEY as key_path
    cert_dir.mkdir(parents=True, exist_ok=True)

    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        import datetime

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "SassyMCP"),
        ])
        import ipaddress as _ip
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
            .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365 * 5))
            .add_extension(
                x509.SubjectAlternativeName([
                    x509.DNSName("localhost"),
                    x509.IPAddress(_ip.IPv4Address("127.0.0.1")),
                ]),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        key_path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        logger.info(f"Self-signed cert generated: {cert_path}")
    except ImportError:
        logger.error("cryptography package not installed. Install with: uv pip install cryptography")
        raise SystemExit(1)


# ── Auth Bootstrap ────────────────────────────────────────────────────
#
# Holds the active bearer token (env-supplied or freshly minted) so the
# startup banner can paste it into the copy-paste config snippet. None
# means auth is off (SASSYMCP_NO_AUTH=1, or token bootstrap failed and
# the user has not configured anything).
_ACTIVE_AUTH_TOKEN: str | None = None


def _ensure_default_token() -> str | None:
    """First-run bootstrap so copy-paste configs work without manual setup.

    Resolution order:
      1. SASSYMCP_NO_AUTH=1                  -> return None (auth stays off)
      2. SASSYMCP_AUTH_TOKEN env var         -> use as-is (no disk write)
      3. tokens.json has client_id=default   -> reuse it
      4. otherwise                           -> mint one and persist it

    Returns the active token, or None if the user opted out / writing
    tokens.json failed (in which case auth simply stays off and the
    banner reflects that).
    """
    if os.environ.get("SASSYMCP_NO_AUTH") == "1":
        return None

    env_token = os.environ.get("SASSYMCP_AUTH_TOKEN")
    if env_token:
        return env_token

    from sassymcp._paths import TOKENS_FILE
    import secrets
    from sassymcp._atomic import atomic_write_json

    tokens_data: dict = {"tokens": []}
    if TOKENS_FILE.exists():
        try:
            tokens_data = json.loads(TOKENS_FILE.read_text(encoding="utf-8"))
            if not isinstance(tokens_data, dict) or "tokens" not in tokens_data:
                tokens_data = {"tokens": []}
        except Exception as e:
            logger.warning(f"tokens.json unreadable ({e}); leaving it alone, skipping bootstrap")
            return None

    for entry in tokens_data.get("tokens", []):
        if entry.get("client_id") == "default":
            tok = entry.get("token")
            if isinstance(tok, str) and len(tok) >= 16:
                return tok

    try:
        new_token = secrets.token_urlsafe(32)
        tokens_data.setdefault("tokens", []).append({
            "token": new_token,
            "client_id": "default",
            "scopes": ["read", "write", "admin"],
        })
        TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(TOKENS_FILE, tokens_data)
        if os.name == "nt":
            from sassymcp.auth import _lockdown_windows_acl
            _lockdown_windows_acl(TOKENS_FILE)
        else:
            try:
                os.chmod(TOKENS_FILE, 0o600)
            except OSError:
                pass
        logger.info(f"Bootstrapped default auth token -> {TOKENS_FILE}")
        return new_token
    except Exception as e:
        logger.warning(f"Token bootstrap failed, auth will be disabled: {e}")
        return None


# ── Server Construction ───────────────────────────────────────────────

def _build_server() -> FastMCP:
    """Construct FastMCP with optional auth."""
    global _ACTIVE_AUTH_TOKEN

    # DNS-rebinding protection requires an explicit Host allowlist; without
    # one, every non-loopback Host (including a Cloudflare-tunnelled
    # hostname like mcp.example.com) returns 421. The shipped default is
    # loopback-only, since the product runs locally on first boot.
    #
    # The MCP SDK's TransportSecurityMiddleware does **exact-match** on the
    # full Host header (including port). Clients send `Host: localhost:21001`,
    # so a bare `localhost` entry never matches. We use the SDK's `:*`
    # wildcard-port syntax to accept any port on each loopback host, while
    # still keeping the entries port-less as a defense-in-depth fallback.
    #
    # To expose this server over a tunnel or LAN, add your hostname via the
    # SASSYMCP_ALLOWED_HOSTS env var (comma-separated). Use `host:*` to
    # allow any port, or `host:port` for an exact port. E.g.:
    #     setx SASSYMCP_ALLOWED_HOSTS "mcp.your-domain.tld,localhost:*,127.0.0.1:*"
    # See docs/TUNNEL.md for the full Cloudflare Tunnel walk-through.
    default_hosts = "localhost:*,127.0.0.1:*,localhost,127.0.0.1"
    allowed_hosts = [
        h.strip()
        for h in os.environ.get("SASSYMCP_ALLOWED_HOSTS", default_hosts).split(",")
        if h.strip()
    ]
    kwargs = {
        "name": "sassymcp",
        "transport_security": TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
        ),
    }

    # First-run bootstrap: ensure a default bearer token exists so the
    # snippet printed by the startup banner is paste-and-go for Claude
    # Desktop, Claude Code, VS Code, Cursor, etc. Sets the module-level
    # _ACTIVE_AUTH_TOKEN so _print_banner can render the Authorization
    # header without re-reading tokens.json.
    _ACTIVE_AUTH_TOKEN = _ensure_default_token()

    # FAIL CLOSED: if auth is configured but broken, refuse to start.
    from sassymcp.auth import get_auth_config
    auth_config = get_auth_config()
    if auth_config:
        kwargs.update(auth_config)
        logger.info("Auth enabled (bearer token verification)")
    else:
        logger.info("Auth disabled (no token configured)")

    return FastMCP(**kwargs)


mcp = _build_server()


# ── Module Resolution ─────────────────────────────────────────────────

def _resolve_modules() -> list[str]:
    """Determine which modules to load based on license tier + env vars.
    Priority:
    1. License tier gates which groups are available
    2. SASSYMCP_LOAD_ALL=1 -> load all ALLOWED modules
    3. SASSYMCP_GROUPS=core,github_quick -> load specific ALLOWED groups
    4. Default: load always_load=True groups (intersected with allowed)
    """
    allowed_groups = get_allowed_groups()

    if os.environ.get("SASSYMCP_LOAD_ALL", "").strip() == "1":
        modules = []
        for group_name, group_info in TOOL_GROUPS.items():
            if group_name in allowed_groups:
                modules.extend(group_info["modules"])
        if modules:
            logger.info(f"SASSYMCP_LOAD_ALL=1 — loading allowed modules: {modules}")
            return resolve_dependencies(modules)
        return get_default_modules()

    groups_env = os.environ.get("SASSYMCP_GROUPS", "").strip()
    if groups_env:
        requested = [g.strip() for g in groups_env.split(",") if g.strip()]
        modules = []
        for g in requested:
            if g in TOOL_GROUPS and g in allowed_groups:
                modules.extend(TOOL_GROUPS[g]["modules"])
            elif g in TOOL_GROUPS and g not in allowed_groups:
                logger.warning(f"Group '{g}' requires Pro license — skipped")
            else:
                logger.warning(f"Unknown group: {g}")
        logger.info(f"SASSYMCP_GROUPS={groups_env} — loading: {modules}")
        return resolve_dependencies(modules)

    defaults = get_default_modules()
    logger.info(f"Default load: {defaults}")
    return defaults


def _import_module(name: str):
    """Import a SassyMCP module by name."""
    return __import__(f"sassymcp.modules.{name}", fromlist=[name])


# ── Rate Limiter Setup ────────────────────────────────────────────────

def _setup_rate_limiter():
    """Configure per-group rate limits from TOOL_GROUPS.

    Logs at error level if setup fails so an operator noticing the
    miss in the logs knows there's no concurrency cap any more. The
    server still starts (rate limiting is a defense-in-depth layer,
    not the auth boundary), but the failure is loud rather than silent.
    """
    try:
        from sassymcp.modules._rate_limiter import get_limiter
        limiter = get_limiter()
        for group_name, group_info in TOOL_GROUPS.items():
            limiter.configure_group(
                group_name,
                max_concurrent=group_info.get("max_concurrent", 10),
                calls_per_minute=group_info.get("calls_per_minute", 120),
            )
        return limiter
    except Exception as e:
        logger.error(
            f"Rate limiter setup failed; tools will run UNTHROTTLED: {e}. "
            "Investigate before exposing this instance over a tunnel or LAN."
        )
        return None


# ── Audit + Error Recovery Middleware ─────────────────────────────────

def _get_audit_logger():
    """Lazy-import audit module to avoid circular deps."""
    try:
        from sassymcp.modules.audit import log_tool_call
        return log_tool_call
    except Exception:
        return None


def _is_retryable(exc: Exception) -> bool:
    """Classify whether an exception is worth retrying."""
    retryable_types = (
        TimeoutError,
        asyncio.TimeoutError,
        ConnectionError,
        ConnectionResetError,
        ConnectionRefusedError,
    )
    if isinstance(exc, retryable_types):
        return True
    # sqlite locked
    exc_str = str(exc).lower()
    if "locked" in exc_str or "busy" in exc_str:
        return True
    return False


def _get_retry_hint(exc: Exception) -> str:
    """Provide actionable guidance without leaking internal paths."""
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "Command timed out. Retry with a longer timeout or simpler command."
    if isinstance(exc, PermissionError):
        return "Permission denied. May need elevated privileges or AV whitelist."
    if isinstance(exc, FileNotFoundError):
        return "File or command not found."
    if isinstance(exc, (ConnectionError, ConnectionResetError, ConnectionRefusedError)):
        return "Connection failed. Check that the target service is running."
    if "locked" in str(exc).lower():
        return "Database locked. Retry in a moment."
    # Generic — do not expose raw exception messages to clients
    return "An internal error occurred."


_rate_limiter = None


def audit_tool(fn):
    """Decorator: audit logging, rate limiting, error recovery for every tool."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        tool_name = fn.__name__
        log_tool_call = _get_audit_logger()
        obs = getattr(mcp, "observability", None)

        # Rate limiting
        group = get_group_for_tool(tool_name)
        acquired = False
        if _rate_limiter and group:
            try:
                acquired = await _rate_limiter.acquire(group)
                if not acquired:
                    return json.dumps({
                        "error": f"Rate limited (group: {group})",
                        "retryable": True,
                        "retry_after_seconds": 5,
                        "retry_hint": f"Group '{group}' is at capacity. Wait a moment.",
                    })
            except Exception:
                acquired = False  # limiter failure = allow through

        # Usage tracking
        tracker = get_tracker()
        tracker.record(tool_name)

        start = time.monotonic()
        error = None
        try:
            if asyncio.iscoroutinefunction(fn):
                result = await fn(*args, **kwargs)
            else:
                # Sync tool bodies must never run inline on the event loop —
                # one blocking call (SQLite, file I/O, screenshot/OCR) would
                # freeze every other connected client's in-flight tool call,
                # which is exactly the "two chats wedge each other" symptom.
                # Offload to a worker thread so concurrent sessions interleave.
                # _wrap_all_tools forces tool.is_async=True so FastMCP always
                # awaits this (always-async) wrapper, even for def-declared tools.
                result = await asyncio.to_thread(fn, *args, **kwargs)
            if obs:
                obs.record_call(success=True)
            return result
        except Exception as e:
            error = str(e)
            if obs:
                obs.record_call(success=False)
            # Structured error recovery
            return json.dumps({
                "error": error,
                "tool": tool_name,
                "retryable": _is_retryable(e),
                "retry_hint": _get_retry_hint(e),
                "retry_after_seconds": 5 if _is_retryable(e) else None,
            })
        finally:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            if log_tool_call:
                try:
                    _SENSITIVE_KEYS = {"password", "token", "secret", "key", "auth",
                                       "credential", "pw", "pass", "api_key", "apikey"}
                    safe_args = {}
                    for k, v in kwargs.items():
                        if any(s in k.lower() for s in _SENSITIVE_KEYS):
                            safe_args[k] = "***REDACTED***"
                        else:
                            s = str(v)
                            safe_args[k] = s[:200] if len(s) > 200 else v
                    log_tool_call(
                        tool_name=tool_name,
                        args=safe_args,
                        elapsed_ms=elapsed_ms,
                        error=error,
                    )
                except Exception:
                    pass  # never let audit break tools

            # Release rate limiter slot
            if _rate_limiter and group and acquired:
                try:
                    _rate_limiter.release(group)
                except Exception:
                    pass

    return wrapper


def _wrap_all_tools():
    """Walk mcp's registered tools and wrap each with audit_tool.
    Uses _audit_wrapped flag to prevent double-wrapping.
    """
    try:
        tools = mcp._tool_manager._tools
        for name, tool in tools.items():
            if hasattr(tool, "fn") and not getattr(tool.fn, "_audit_wrapped", False):
                # Validate before wrapping
                validate_tool(tool.fn)
                tool.fn = audit_tool(tool.fn)
                tool.fn._audit_wrapped = True
                # audit_tool's wrapper is ALWAYS a coroutine. FastMCP froze
                # tool.is_async at registration from the original fn and uses
                # it to decide whether to await (func_metadata: `if fn_is_async:
                # await fn(...) else: fn(...)`). Force it True so a sync-declared
                # tool (def, not async def) is still awaited — and therefore
                # offloaded to a thread by the wrapper — instead of returning an
                # un-awaited coroutine. Lets any blocking tool be written as a
                # plain def and stay off the event loop.
                tool.is_async = True
        logger.info(f"Audit middleware applied to {len(tools)} tools")
    except Exception as e:
        logger.warning(f"Audit middleware wiring failed (non-fatal): {e}")


# ── Graceful Shutdown ─────────────────────────────────────────────────

async def _graceful_shutdown(signum=None):
    """Clean shutdown: notify crosslink, clear state, exit."""
    logger.info(f"Shutdown triggered (signal {signum})")

    # Crosslink notification (lazy import, never fails)
    try:
        from sassymcp.modules.crosslink import _post_message
        _post_message("system", "default", "SassyMCP shutting down")
    except Exception:
        pass

    # Clear transient state
    state = getattr(mcp, "state", None)
    if state:
        try:
            state.clear()
        except Exception:
            pass

    await asyncio.sleep(0.3)
    logger.info("SassyMCP shutdown complete")


def _register_shutdown_handlers():
    """Register OS signals for clean exit. Async primary, sync fallback."""
    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(_graceful_shutdown(s)))
        logger.info("Graceful shutdown handlers registered (async)")
    except (NotImplementedError, RuntimeError):
        # Windows or no running loop — sync fallback
        def _sync_shutdown(signum, frame):
            logger.info(f"Shutdown triggered (signal {signum})")
            # Can't await in sync handler — do best-effort cleanup
            try:
                from sassymcp.modules.crosslink import _post_message
                _post_message("system", "default", "SassyMCP shutting down")
            except Exception:
                pass
            state = getattr(mcp, "state", None)
            if state:
                try:
                    state.clear()
                except Exception:
                    pass
            logger.info("SassyMCP shutdown complete")
            sys.exit(0)

        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, _sync_shutdown)
        logger.info("Graceful shutdown handlers registered (sync fallback)")


# ── Module Loading ─────────────────────────────────────────────────────

def _load_modules():
    """Load all configured modules with infrastructure-first ordering."""
    global _rate_limiter

    # Setup rate limiter before loading modules
    _rate_limiter = _setup_rate_limiter()

    # Infrastructure modules first (state_manager, observability, runtime_config)
    # so that other modules can use server.state and server.observability
    infra_modules = ["state_manager", "observability", "runtime_config"]
    target_modules = _resolve_modules()

    # Separate infra from the rest, preserving order
    ordered = []
    for mod in infra_modules:
        if mod in target_modules:
            ordered.append(mod)
    for mod in target_modules:
        if mod not in infra_modules:
            ordered.append(mod)

    # Always register meta first (after infra)
    if "meta" in ordered:
        ordered.remove("meta")
        ordered.insert(len([m for m in ordered if m in infra_modules]), "meta")

    loaded = 0
    for mod_name in ordered:
        # Snapshot tool names before registration
        before = set(mcp._tool_manager._tools.keys()) if hasattr(mcp, "_tool_manager") else set()
        try:
            module = _import_module(mod_name)
            module.register(mcp)
            loaded += 1
            # Map newly registered tools to their group
            after = set(mcp._tool_manager._tools.keys()) if hasattr(mcp, "_tool_manager") else set()
            for tool_name in after - before:
                register_tool_group(tool_name, mod_name)
            logger.info(f"Registered module: {mod_name}")
        except Exception as e:
            logger.warning(f"Failed to register {mod_name}: {e}")

    logger.info(f"SassyMCP ready: {loaded} modules loaded")

    # Auto-activate hooks for any modules that boosted into the default
    # load via usage scores. The hooks were registered during module
    # import (via the `try: _register_hooks()` pattern at module top); we
    # just need to flip their activation flag so the AI sees the playbook
    # in its context without an explicit sassy_hooks_activate call.
    try:
        from sassymcp.modules._tool_loader import (
            get_score_boosted_modules,
            auto_activate_hooks_for_modules,
        )
        boosted = get_score_boosted_modules()
        if boosted:
            auto_activate_hooks_for_modules(boosted)
    except Exception as e:
        logger.warning(f"hook auto-activation failed (non-fatal): {e}")

    # Wire audit middleware after all tools are registered
    _wrap_all_tools()

    # Compute schema version for cache invalidation
    try:
        tools_list = []
        if hasattr(mcp, "_tool_manager"):
            for name, tool in mcp._tool_manager._tools.items():
                tools_list.append({
                    "name": name,
                    "description": getattr(tool, "description", ""),
                })
        version = compute_schema_version(tools_list)
        logger.info(f"Schema version: {version}")
    except Exception:
        pass

    # Live reload in dev mode
    if os.environ.get("SASSYMCP_DEV") == "1":
        modules_dir = Path(__file__).parent / "modules"
        enable_live_reload(mcp, modules_dir)

    # License validation, two cadences:
    #   - Fast revocation check at startup: hits the billing Worker's
    #     edge-cached revocation oracle. If LS fired a refund/cancel
    #     webhook since last startup, the local license is removed
    #     within ~seconds rather than waiting a week.
    #   - Weekly full LS validate: the authoritative backstop in case
    #     the billing Worker missed a webhook or returned 'unknown'.
    # Both are non-blocking — startup never waits on network.
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(fast_revocation_check())
        loop.create_task(weekly_validation_check())
    except RuntimeError:
        pass  # No event loop yet — will run on first request


# ── Entry Point ────────────────────────────────────────────────────────

def _is_piped() -> bool:
    """Detect if stdin is connected to a pipe (MCP client) or a terminal (human)."""
    try:
        return not sys.stdin.isatty()
    except Exception:
        return False


def _check_for_updates_at_startup(timeout_seconds: float = 3.0):
    """Run a one-shot update check at startup. Returns dict or None.

    Honored env: SASSYMCP_NO_UPDATE_CHECK=1 disables the check entirely.
    Network is bounded by `timeout_seconds`; failure is silent (returns None).
    Result is cached in the Updater singleton so the first LLM-side
    `sassy_update_check` call within 5 min reuses the same data.
    """
    if os.environ.get("SASSYMCP_NO_UPDATE_CHECK") == "1":
        return None
    try:
        import threading
        from sassymcp.modules import updater as _upd_mod
        # Reuse the per-server updater instance if registered, else make one.
        upd = getattr(mcp, "updater", None) or _upd_mod.Updater()
        result = {"value": None}

        def _run():
            try:
                # Updater._http_json uses a 10s urlopen timeout; we wrap with
                # threading so we can return early if the network is slow.
                result["value"] = upd.check(force=False)
            except Exception as e:
                logger.debug(f"Startup update check raised: {e}")

        t = threading.Thread(target=_run, daemon=True, name="sassymcp-update-check")
        t.start()
        t.join(timeout=timeout_seconds)
        if t.is_alive():
            logger.info(f"Startup update check timed out after {timeout_seconds}s — skipping")
            return None
        return result["value"]
    except Exception as e:
        logger.debug(f"Startup update check failed: {e}")
        return None


def _format_update_line(update_info) -> str | None:
    """Turn a check() result into a single human-readable line, or None."""
    if not update_info or "error" in update_info:
        return None
    if update_info.get("upgradable"):
        return (
            f"Update available: {update_info['current']} -> {update_info['latest']}"
            f"  (call sassy_update_check for details)"
        )
    if update_info.get("latest"):
        return f"Up to date (v{update_info['current']})"
    return None


def _print_banner(tool_count, host, port, first_run, *, transport="http",
                  scheme="http", token: str | None = None, update_info=None):
    """Print a human-readable startup banner with paste-and-go config snippets.

    The snippet matches the current Claude Code / Claude Desktop / VS Code
    Copilot / Cursor MCP shape:

        {
          "mcpServers": {
            "sassymcp": {
              "type": "http",
              "url": "...",
              "headers": {"Authorization": "Bearer ..."}
            }
          }
        }

    When `token` is provided, the `headers` block is emitted with the
    real bearer value (NOT a placeholder), so the snippet works on the
    first paste. When `token` is None, auth is off and the headers block
    is omitted.
    """
    from sassymcp._paths import TOKENS_FILE
    url = f"{scheme}://{host}:{port}"
    endpoint = f"{url}/mcp/"
    typ = "sse" if transport == "sse" else "http"

    bar = "=" * 62
    print(flush=True)
    print(f"  {bar}", flush=True)
    print(f"   SassyMCP v{__version__}  |  {tool_count} tools  |  Ready", flush=True)
    update_line = _format_update_line(update_info)
    if update_line:
        print(f"   {update_line}", flush=True)
    print(f"  {bar}", flush=True)
    print(flush=True)
    print(f"   MCP endpoint:  {endpoint}", flush=True)
    if token:
        # For pathologically short tokens (shouldn't happen — our minimum
        # is 16 — but a future caller could pass a custom value), avoid
        # dumping the whole thing in the preview line. The full token
        # still appears in the copy-paste snippet below where it's needed.
        if len(token) >= 12:
            preview = f"{token[:6]}...{token[-4:]}"
        else:
            preview = "*" * len(token)
        print(f"   Auth:          Bearer {preview}  (full token in snippet below)", flush=True)
    else:
        print("   Auth:          disabled  (SASSYMCP_NO_AUTH=1 or token bootstrap failed)", flush=True)

    # Surface insecure-auth situation in the banner too — the warning
    # already went to the log, but the banner is what a person sees.
    _is_loopback = host in ("127.0.0.1", "localhost", "::1")
    if token and not _is_loopback and scheme == "http":
        print("   ! INSECURE: bearer auth over plain HTTP on a non-loopback host.", flush=True)
        print("     Make sure TLS is terminated upstream (Cloudflare Tunnel,", flush=True)
        print("     reverse proxy) before sharing this snippet with anyone.", flush=True)
    elif scheme == "https":
        print("   TLS:           self-signed cert (clients may need to trust it on first connect)", flush=True)
    print(flush=True)

    # ── 1. Claude Code CLI one-liner ────────────────────────────────────
    print("   Claude Code (CLI):", flush=True)
    if token:
        print(f'     claude mcp add --transport http sassymcp {endpoint} \\', flush=True)
        print(f'       --header "Authorization: Bearer {token}"', flush=True)
    else:
        print(f"     claude mcp add --transport http sassymcp {endpoint}", flush=True)
    print(flush=True)

    # ── 2. JSON snippet for Claude Desktop / VS Code / Cursor / Windsurf ─
    print("   Claude Desktop / VS Code / Cursor / Windsurf (paste into config):", flush=True)
    print("     {", flush=True)
    print('       "mcpServers": {', flush=True)
    print('         "sassymcp": {', flush=True)
    print(f'           "type": "{typ}",', flush=True)
    if token:
        print(f'           "url": "{endpoint}",', flush=True)
        print('           "headers": {', flush=True)
        print(f'             "Authorization": "Bearer {token}"', flush=True)
        print('           }', flush=True)
    else:
        print(f'           "url": "{endpoint}"', flush=True)
    print('         }', flush=True)
    print('       }', flush=True)
    print('     }', flush=True)
    print(flush=True)
    print("   (VS Code mcp.json uses the same shape under \"servers\" instead of \"mcpServers\".)", flush=True)
    print(flush=True)

    # ── 3. Token management ─────────────────────────────────────────────
    if token:
        print("   Token management:", flush=True)
        print(f"     stored in:  {TOKENS_FILE}", flush=True)
        print("     rotate:     sassymcp.exe generate-token --client-id default", flush=True)
        print("     show:       sassymcp.exe show-token", flush=True)
        print("     disable:    set SASSYMCP_NO_AUTH=1 and restart", flush=True)
        print(flush=True)

    # ── 4. Auto-patch every installed MCP client ────────────────────────
    print("   Auto-register with every installed MCP client (idempotent):", flush=True)
    print("     sassymcp.exe install", flush=True)
    print(flush=True)

    if first_run:
        print("   ** FIRST RUN: After connecting, ask the AI:", flush=True)
        print('        "Run sassy_setup_wizard to set up my profile"', flush=True)
        print(flush=True)
    print(f"  {bar}", flush=True)
    print(flush=True)


def _maybe_run_first_run_install():
    """If running under a DXT install (or any first-run scenario), patch
    every OTHER detected MCP client's config so the user's Cursor / VS Code /
    Windsurf etc. all see sassymcp without manual JSON editing.

    Idempotent: a marker file at ~/.sassymcp/.installed-other-clients
    prevents re-runs. The marker is created BEFORE we spawn the
    installer, atomically via O_EXCL, so two concurrent first-run
    invocations (e.g., DXT first-run AND a manual launch within the same
    second) can't both spawn the installer subprocess. The subprocess is
    detached; if it fails or hangs we do not block server startup.
    """
    from sassymcp._paths import HOME as _SASSY_HOME
    marker = _SASSY_HOME / ".installed-other-clients"
    if marker.exists():
        return
    try:
        _SASSY_HOME.mkdir(parents=True, exist_ok=True)
        # O_EXCL gives a single-winner semantic: only the process that
        # successfully creates the marker proceeds to spawn the
        # installer. Everyone else sees FileExistsError and returns.
        fd = os.open(str(marker), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        os.close(fd)
    except FileExistsError:
        return  # another process won the race
    except OSError:
        return  # filesystem hostile, skip silently

    import subprocess
    try:
        if os.name == "nt":
            DETACHED_PROCESS = 0x00000008
            subprocess.Popen(
                [sys.executable, "-m", "sassymcp.install", "--auto-other"],
                creationflags=DETACHED_PROCESS,
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(
                [sys.executable, "-m", "sassymcp.install", "--auto-other"],
                start_new_session=True,
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception:
        pass  # never block startup on auto-install


# ── CLI subcommands (token / install) ─────────────────────────────────
#
# The exe also doubles as a Swiss-army knife: `sassymcp.exe install`
# patches every detected MCP client, `sassymcp.exe generate-token` mints
# a fresh bearer (or rotates an existing one), `sassymcp.exe show-token`
# prints the active one. These run BEFORE the server starts and exit on
# their own — they never touch the FastMCP instance or load modules, so
# they're safe to call from a fresh box.

def _cli_generate_token(argv: list[str]) -> int:
    """`sassymcp.exe generate-token` — create or rotate a bearer token."""
    import argparse
    import secrets
    from sassymcp._paths import TOKENS_FILE
    from sassymcp._atomic import atomic_write_json

    p = argparse.ArgumentParser(
        prog="sassymcp generate-token",
        description="Create or rotate a SassyMCP bearer token.",
    )
    p.add_argument("--client-id", default="default",
                   help="client identifier (default: 'default')")
    p.add_argument("--scopes", default="read,write,admin",
                   help="comma-separated scopes (default: read,write,admin)")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON")
    args = p.parse_args(argv)

    scope_list = [s.strip() for s in args.scopes.split(",") if s.strip()]
    token = secrets.token_urlsafe(32)

    tokens_data: dict = {"tokens": []}
    if TOKENS_FILE.exists():
        try:
            tokens_data = json.loads(TOKENS_FILE.read_text(encoding="utf-8"))
            if not isinstance(tokens_data, dict) or "tokens" not in tokens_data:
                tokens_data = {"tokens": []}
        except Exception as e:
            print(f"Error: tokens.json is corrupt ({e}). Move it aside and retry.",
                  file=sys.stderr)
            return 2

    tokens_data["tokens"] = [
        t for t in tokens_data.get("tokens", [])
        if t.get("client_id") != args.client_id
    ]
    tokens_data["tokens"].append({
        "token": token,
        "client_id": args.client_id,
        "scopes": scope_list,
    })

    try:
        TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(TOKENS_FILE, tokens_data)
        if os.name == "nt":
            from sassymcp.auth import _lockdown_windows_acl
            _lockdown_windows_acl(TOKENS_FILE)
        else:
            os.chmod(TOKENS_FILE, 0o600)
    except Exception as e:
        print(f"Error writing {TOKENS_FILE}: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({
            "token": token,
            "client_id": args.client_id,
            "scopes": scope_list,
            "saved_to": str(TOKENS_FILE),
            "header": f"Authorization: Bearer {token}",
        }, indent=2))
    else:
        print(f"Token created for client_id={args.client_id!r}, scopes={scope_list}")
        print(f"Saved to: {TOKENS_FILE}")
        print()
        print(f"  Authorization: Bearer {token}")
        print()
        print("Paste this header into your MCP client config.")
        print("Restart sassymcp.exe so the running server picks it up.")
    return 0


def _cli_show_token(argv: list[str]) -> int:
    """`sassymcp.exe show-token` — print existing tokens (or just the default)."""
    import argparse
    from sassymcp._paths import TOKENS_FILE

    p = argparse.ArgumentParser(
        prog="sassymcp show-token",
        description="Print SassyMCP bearer token(s) stored on disk.",
    )
    p.add_argument("--client-id", default="default",
                   help="show only this client_id (default: 'default'). "
                        "Pass --all to dump every entry.")
    p.add_argument("--all", action="store_true",
                   help="dump every token entry (raw JSON)")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON")
    args = p.parse_args(argv)

    env_token = os.environ.get("SASSYMCP_AUTH_TOKEN")
    if env_token:
        if args.json:
            print(json.dumps({"source": "SASSYMCP_AUTH_TOKEN", "token": env_token}, indent=2))
        else:
            print("Source: SASSYMCP_AUTH_TOKEN env var (overrides tokens.json)")
            print(f"Token:  {env_token}")
        return 0

    if not TOKENS_FILE.exists():
        msg = f"No tokens.json at {TOKENS_FILE}. Run: sassymcp.exe generate-token"
        if args.json:
            print(json.dumps({"error": msg}, indent=2))
        else:
            print(msg, file=sys.stderr)
        return 1

    try:
        data = json.loads(TOKENS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Error: tokens.json is corrupt ({e})", file=sys.stderr)
        return 2

    entries = data.get("tokens", []) if isinstance(data, dict) else []
    if args.all:
        out = entries
    else:
        out = [t for t in entries if t.get("client_id") == args.client_id]
        if not out:
            msg = f"No token found for client_id={args.client_id!r}. Run: sassymcp.exe generate-token"
            if args.json:
                print(json.dumps({"error": msg}, indent=2))
            else:
                print(msg, file=sys.stderr)
            return 1

    if args.json:
        print(json.dumps(out, indent=2))
    else:
        for t in out:
            print(f"client_id: {t.get('client_id')}")
            print(f"scopes:    {t.get('scopes')}")
            print(f"token:     {t.get('token')}")
            print()
        print(f"(stored in {TOKENS_FILE})")
    return 0


def _cli_mesh(argv: list[str]) -> int:
    """Emit coordination-mesh data/actions as one JSON line, for external UIs
    (e.g. the Sassy Brain desktop app) that shell out to a bundled/installed
    sassymcp.exe instead of a python interpreter.

    Usage: sassymcp mesh [board|brain|phone|peers|announce ...|delegate ...]
    Reuses the same code paths as `python -m sassymcp.modules.coordination`,
    `python -m sassymcp._brain_status`, and `python -m sassymcp._phone_status`.
    """
    import json
    cmd = argv[0] if argv else "board"
    try:
        if cmd == "brain":
            from sassymcp import _brain_status
            out = _brain_status.snapshot()
        elif cmd == "phone":
            from sassymcp import _phone_status
            out = _phone_status.snapshot()
        else:  # board | peers | announce | delegate
            from sassymcp.modules.coordination import _main as _coord_main
            out = _coord_main(argv)
        sys.stdout.write(json.dumps(out))
        return 0
    except SystemExit:
        raise
    except Exception as e:
        sys.stdout.write(json.dumps({"error": str(e)}))
        return 1


def _dispatch_subcommand() -> int | None:
    """Sniff argv[1] for a subcommand before argparse takes over.

    Returns an exit code if a subcommand handled the invocation, otherwise
    None (meaning fall through to the server's own argparse).
    """
    if len(sys.argv) < 2:
        return None
    sub = sys.argv[1]
    if sub == "generate-token":
        return _cli_generate_token(sys.argv[2:])
    if sub == "show-token":
        return _cli_show_token(sys.argv[2:])
    if sub == "install":
        from sassymcp.install import main as _install_main
        return _install_main(sys.argv[2:])
    if sub == "supervise":
        from sassymcp.supervisor import main as _supervise_main
        return _supervise_main(sys.argv[2:])
    if sub == "mesh":
        return _cli_mesh(sys.argv[2:])
    if sub in ("setup", "wizard"):
        # Interactive menu. Returns "run_server" if the user picks the
        # "Run as HTTP server" option, in which case we fall through to
        # the server's normal argparse path with --http forced on.
        from sassymcp._cli_wizard import run_wizard
        result = run_wizard()
        if result == "run_server":
            # Re-enter main() with --http so the standard flow takes over.
            sys.argv = [sys.argv[0], "--http"]
            return None
        return 0
    return None


def main():
    # Subcommand dispatch happens before argparse so `sassymcp.exe install
    # --client cursor` reaches install.main() with its own argparse intact.
    rc = _dispatch_subcommand()
    if rc is not None:
        sys.exit(rc)

    import argparse

    parser = argparse.ArgumentParser(
        description=f"SassyMCP Server v{__version__}",
        epilog="Subcommands: setup | install | supervise | mesh | generate-token | show-token  "
               "(e.g. `sassymcp.exe setup` opens the interactive wizard)",
    )
    parser.add_argument(
        "--http", "--serve", action="store_true",
        help="Run as HTTP server (auto-detected when launched interactively)",
    )
    parser.add_argument("--stdio", action="store_true",
                        help="Force stdio mode (for MCP clients that pipe stdin/stdout)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=21001)
    parser.add_argument("--sse", action="store_true",
                        help="Use legacy SSE transport instead of streamable-http")
    parser.add_argument("--setup", action="store_true",
                        help="Force first-run setup wizard (regenerate persona.md)")
    parser.add_argument("--ssl", action="store_true",
                        help="Enable HTTPS with self-signed cert ($SASSYMCP_HOME/server.crt/key). "
                             "Auto-enabled when bearer auth is active AND --host is non-loopback "
                             "(bearer tokens MUST NOT cross the wire in plaintext).")
    parser.add_argument("--ssl-cert", default="",
                        help="Path to SSL certificate file (default: $SASSYMCP_HOME/server.crt)")
    parser.add_argument("--ssl-key", default="",
                        help="Path to SSL key file (default: $SASSYMCP_HOME/server.key)")
    parser.add_argument("--insecure-auth", action="store_true",
                        help="Allow bearer auth over plain HTTP on non-loopback hosts. "
                             "Only set this if TLS is terminated upstream (e.g. Cloudflare "
                             "Tunnel, an ingress controller, or a reverse proxy). Otherwise "
                             "your token leaks on the network.")
    args = parser.parse_args()

    _maybe_run_first_run_install()

    # First-run TTY: a human just double-clicked sassymcp.exe and we
    # have nothing configured yet. Open the wizard instead of silently
    # starting an HTTP server they can't see. Conditions:
    #   - stdin AND stdout are both TTYs (so input() works)
    #   - no explicit transport flag set
    #   - no persona.md yet (first-ever launch on this machine)
    # Existing users with a configured persona.md keep getting the
    # auto-HTTP-server behavior they're used to.
    if not args.stdio and not args.http and not _is_piped():
        try:
            from sassymcp._paths import PERSONA_FILE as _persona_check
            first_ever = not _persona_check.exists()
        except Exception:
            first_ever = False
        if first_ever and sys.stdin.isatty() and sys.stdout.isatty():
            from sassymcp._cli_wizard import run_wizard
            result = run_wizard()
            if result != "run_server":
                sys.exit(0)
            args.http = True

    # Auto-detect transport: if stdin is a pipe, an MCP client is calling us.
    # If stdin is a terminal (human double-clicked or ran from cmd), use HTTP.
    if not args.stdio and not args.http:
        if _is_piped():
            args.stdio = True
        else:
            args.http = True

    # Bootstrap external tools into PATH before any module loads
    try:
        from sassymcp.modules.tools_manager import bootstrap as _tools_bootstrap
        _tools_info = _tools_bootstrap()
        if _tools_info.get("missing_required"):
            logger.warning(
                f"Startup: required tools missing: {_tools_info['missing_required']} "
                "-- run sassy_setup_tools(action='install_required')"
            )
    except Exception as _e:
        logger.debug(f"Tools bootstrap skipped: {_e}")

    # Load everything
    _load_modules()
    _register_shutdown_handlers()

    # First-run detection
    from sassymcp._paths import HOME as _SASSY_HOME, PERSONA_FILE as _persona
    first_run = not _persona.exists()
    if args.setup or first_run:
        if args.setup:
            logger.info("--setup flag: setup wizard will be available for reconfiguration")
        else:
            logger.info(f"FIRST RUN DETECTED: no persona.md found in {_SASSY_HOME}")

    tool_count = len(mcp._tool_manager._tools) if hasattr(mcp, "_tool_manager") else "?"
    _lic = validate_license()
    _tier_label = _lic.get("tier", "free")
    if _lic.get("addons"):
        _tier_label += "+" + ",".join(_lic["addons"])
    if os.environ.get("SASSYMCP_LICENSE_BYPASS", "").strip() in ("1", "true", "yes"):
        _tier_label = "BYPASS"
    logger.info(
        f"SassyMCP v{__version__} started | tier={_tier_label} | "
        f"{tool_count} tools | groups: {sorted(get_allowed_groups())}"
    )

    # Control Panel — loopback web UI. Opt-in (config panel.enabled or
    # SASSYMCP_PANEL=1) so stdio installs don't open a port unless asked.
    # Runs in its own daemon thread, independent of the MCP transport.
    try:
        from sassymcp.modules.runtime_config import get as _cfg_get
        _panel_on = (os.environ.get("SASSYMCP_PANEL", "").strip() in ("1", "true", "yes")
                     or bool(_cfg_get("panel.enabled", False)))
        if _panel_on:
            from sassymcp import control_panel as _panel
            # Pass the raw config value — start_panel() coerces a bad/non-int
            # panel.port to the default rather than raising.
            _pinfo = _panel.start_panel(port=_cfg_get("panel.port", _panel.DEFAULT_PORT))
            if _pinfo.get("url"):
                logger.info(f"Control Panel: {_pinfo['url']}")
    except Exception as _pe:
        logger.warning(f"Control Panel did not start: {_pe}")

    # Startup update check (opt-out: SASSYMCP_NO_UPDATE_CHECK=1).
    # Logged in both modes; printed in HTTP banner only — stdio uses stdout
    # for the JSON-RPC stream so the banner cannot print to it.
    update_info = _check_for_updates_at_startup()
    update_line = _format_update_line(update_info)
    if update_line:
        logger.info(update_line)

    if args.stdio:
        logger.info("Starting SassyMCP (stdio — MCP client detected)")
        mcp.run()
    else:
        import uvicorn

        # ── TLS policy ──────────────────────────────────────────────────
        # Bearer tokens over plain HTTP are safe ONLY on the loopback
        # interface (the packets never touch the network). On any other
        # bind address — 0.0.0.0, a LAN IP, a public IP — the bearer
        # rides the wire in cleartext and any passive sniffer captures it.
        # So: when auth is on AND host is non-loopback, auto-enable TLS
        # (self-signed cert is regenerated below). If the user explicitly
        # passed --insecure-auth, we honor that (their tunnel/proxy is
        # presumably handling TLS upstream) but log a loud warning.
        _LOOPBACK = {"127.0.0.1", "localhost", "::1"}
        host_is_loopback = args.host in _LOOPBACK
        if _ACTIVE_AUTH_TOKEN and not host_is_loopback and not args.ssl:
            if args.insecure_auth:
                logger.warning(
                    f"--insecure-auth: bearer token over plain HTTP on "
                    f"{args.host}. Assuming TLS is terminated upstream "
                    f"(Cloudflare Tunnel / reverse proxy). If not, your "
                    f"token is leaking on the network."
                )
            else:
                logger.warning(
                    f"Bearer auth active on non-loopback host {args.host} — "
                    f"auto-enabling --ssl. Pass --insecure-auth to override "
                    f"(only if TLS is handled upstream)."
                )
                args.ssl = True

        if args.sse:
            logger.info(f"Starting SassyMCP (SSE) on {args.host}:{args.port}")
            app = mcp.sse_app()
        else:
            logger.info(f"Starting SassyMCP (streamable-http) on {args.host}:{args.port}")
            app = mcp.streamable_http_app()

        uvicorn_kwargs = {"host": args.host, "port": args.port, "log_level": "info"}

        # SSL support
        if args.ssl:
            from pathlib import Path as _P2
            from sassymcp._paths import SSL_CERT as _DEFAULT_CERT, SSL_KEY as _DEFAULT_KEY
            ssl_cert = args.ssl_cert or str(_DEFAULT_CERT)
            ssl_key = args.ssl_key or str(_DEFAULT_KEY)
            if not _P2(ssl_cert).exists() or not _P2(ssl_key).exists():
                logger.info("SSL cert/key not found — generating self-signed certificate...")
                _generate_self_signed_cert()
                ssl_cert = str(_DEFAULT_CERT)
                ssl_key = str(_DEFAULT_KEY)
            uvicorn_kwargs["ssl_certfile"] = ssl_cert
            uvicorn_kwargs["ssl_keyfile"] = ssl_key
            logger.info(f"SSL enabled: cert={ssl_cert}")

        # Print human-readable banner with connection instructions
        _print_banner(
            tool_count, args.host, args.port, first_run or args.setup,
            transport="sse" if args.sse else "http",
            scheme="https" if args.ssl else "http",
            token=_ACTIVE_AUTH_TOKEN,
            update_info=update_info,
        )

        uvicorn.run(app, **uvicorn_kwargs)


if __name__ == "__main__":
    main()
