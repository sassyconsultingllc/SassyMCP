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
    validate_tool,
    enable_live_reload,
    compute_schema_version,
    TOOL_GROUPS,
)


# ── Self-Signed Cert Generation ──────────────────────────────────────

def _generate_self_signed_cert():
    """Generate a self-signed SSL cert for HTTPS mode. Zero external deps."""
    from pathlib import Path as _P
    cert_dir = _P.home() / ".sassymcp"
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_path = cert_dir / "server.crt"
    key_path = cert_dir / "server.key"

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


# ── Server Construction ───────────────────────────────────────────────

def _build_server() -> FastMCP:
    """Construct FastMCP with optional auth."""
    kwargs = {
        "name": "sassymcp",
        "transport_security": TransportSecuritySettings(enable_dns_rebinding_protection=False),
    }

    # Opt-in auth: only if SASSYMCP_AUTH_TOKEN or ~/.sassymcp/tokens.json exists
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
    """Determine which modules to load based on env vars.
    Priority:
    1. SASSYMCP_LOAD_ALL=1 -> load everything
    2. SASSYMCP_GROUPS=core,github_quick -> load specific groups
    3. Default: load always_load=True groups only
    """
    if os.environ.get("SASSYMCP_LOAD_ALL", "").strip() == "1":
        logger.info("SASSYMCP_LOAD_ALL=1 — loading all modules")
        return get_all_modules()

    groups_env = os.environ.get("SASSYMCP_GROUPS", "").strip()
    if groups_env:
        requested = [g.strip() for g in groups_env.split(",") if g.strip()]
        if "all" in requested:
            return get_all_modules()
        modules = []
        for g in requested:
            if g in TOOL_GROUPS:
                modules.extend(TOOL_GROUPS[g]["modules"])
            else:
                logger.warning(f"Unknown group: {g}")
        logger.info(f"SASSYMCP_GROUPS={groups_env} — loading: {modules}")
        return modules

    defaults = get_default_modules()
    logger.info(f"Default load: {defaults}")
    return defaults


def _import_module(name: str):
    """Import a SassyMCP module by name."""
    return __import__(f"sassymcp.modules.{name}", fromlist=[name])


# ── Rate Limiter Setup ────────────────────────────────────────────────

def _setup_rate_limiter():
    """Configure per-group rate limits from TOOL_GROUPS."""
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
        logger.warning(f"Rate limiter setup failed (non-fatal): {e}")
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
                result = fn(*args, **kwargs)
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


# ── Entry Point ────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="SassyMCP Server v1.0")
    parser.add_argument(
        "--http", "--serve", action="store_true",
        help="Run as HTTP server (default — for cloudflared, Claude Desktop, etc.)",
    )
    parser.add_argument("--stdio", action="store_true",
                        help="Force stdio mode (not recommended)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=21001)
    parser.add_argument("--sse", action="store_true",
                        help="Use legacy SSE transport instead of streamable-http")
    parser.add_argument("--setup", action="store_true",
                        help="Force first-run setup wizard (regenerate persona.md)")
    parser.add_argument("--ssl", action="store_true",
                        help="Enable HTTPS with self-signed cert (~/.sassymcp/server.crt/key)")
    parser.add_argument("--ssl-cert", default="",
                        help="Path to SSL certificate file (default: ~/.sassymcp/server.crt)")
    parser.add_argument("--ssl-key", default="",
                        help="Path to SSL key file (default: ~/.sassymcp/server.key)")
    args = parser.parse_args()

    # Load everything
    _load_modules()
    _register_shutdown_handlers()

    # First-run detection
    from pathlib import Path as _P
    _persona = _P.home() / ".sassymcp" / "persona.md"
    if args.setup or not _persona.exists():
        if args.setup:
            logger.info("--setup flag: setup wizard will be available for reconfiguration")
        else:
            logger.info("FIRST RUN DETECTED: no ~/.sassymcp/persona.md found")
            logger.info("Call sassy_setup_wizard to complete initial setup")

    tool_count = len(mcp._tool_manager._tools) if hasattr(mcp, "_tool_manager") else "?"
    logger.info(f"SassyMCP v1.0 started | {tool_count} tools | groups: {list(TOOL_GROUPS.keys())}")

    if args.stdio:
        logger.info("Starting SassyMCP (stdio)")
        mcp.run()
    else:
        import uvicorn
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
            ssl_cert = args.ssl_cert or str(_P2.home() / ".sassymcp" / "server.crt")
            ssl_key = args.ssl_key or str(_P2.home() / ".sassymcp" / "server.key")
            if not _P2(ssl_cert).exists() or not _P2(ssl_key).exists():
                logger.info("SSL cert/key not found — generating self-signed certificate...")
                _generate_self_signed_cert()
                ssl_cert = str(_P2.home() / ".sassymcp" / "server.crt")
                ssl_key = str(_P2.home() / ".sassymcp" / "server.key")
            uvicorn_kwargs["ssl_certfile"] = ssl_cert
            uvicorn_kwargs["ssl_keyfile"] = ssl_key
            logger.info(f"SSL enabled: cert={ssl_cert}")

        uvicorn.run(app, **uvicorn_kwargs)


if __name__ == "__main__":
    main()
