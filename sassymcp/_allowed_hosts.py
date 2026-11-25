# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
"""File-backed Host allowlist with hot-swap (no-restart) updates.

Problem: SASSYMCP_ALLOWED_HOSTS is read once at server startup and handed
to the MCP SDK's DNS-rebinding middleware as a frozen list. A Cloudflare
*quick* tunnel mints a random hostname that is only known AFTER the tunnel
starts, so the operator faces a catch-22: the hostname must be in the
allowlist before startup, but it doesn't exist until after startup.

This module makes the allowlist live:

* The runtime source of truth is a plain-text file,
  ``~/.sassymcp/allowed_hosts`` (one host per line, ``#`` comments).
  ``SASSYMCP_ALLOWED_HOSTS`` remains the *initial seed*: on first use the
  file is seeded from it (or from the loopback defaults when unset), and
  once the file exists it wins over the env var — so ``deny-host`` can
  actually remove a host the env var once seeded.
* The loopback defaults (``localhost:*``, ``127.0.0.1:*``, ``localhost``,
  ``127.0.0.1``) are ALWAYS in the effective list, even if the file
  doesn't mention them. The file can only *add* hosts, never lock the
  operator out of loopback.
* ``sync_to_settings(settings)`` reconciles the MCP SDK's
  ``TransportSecuritySettings.allowed_hosts`` with the effective list
  *in place* (slice assignment keeps the same list object the SDK
  middleware reads on every request — verified against the SDK: its
  ``_validate_host`` iterates ``settings.allowed_hosts`` per request, so
  a mutated list takes effect immediately with no restart and no
  re-implementation of the check). A tiny pure-ASGI wrapper in
  ``server.py`` calls it on every HTTP request; an mtime/size cache
  means an unchanged file costs one ``stat()`` per request and nothing
  else. If the file is unreadable the sync fails safe: the last-known
  list stays in force and the error is logged, never raised.
* ``sassymcp allow-host <hostname>`` appends to the file (atomic write);
  the running server picks it up on the very next request. No restart,
  no re-exec, no supervisor involvement.
* Opt-in suffix wildcards (v1.21.0): an entry like ``*.trycloudflare.com``
  admits ANY hostname under that suffix. The SDK only exact-matches, so
  enforcement for wildcards happens in the per-request wrapper: a request
  Host falling under a configured wildcard gets its concrete host
  provisioned into the live allowlist (in-memory only) before the SDK
  checks it. Tradeoff, stated plainly: with a wildcard, any hostname
  under the suffix passes and the bearer token is the only gate — the
  DNS-rebinding protection is effectively off for that suffix. Never
  combine a wildcard with auth disabled.

Threading: ``sync_to_settings`` does a single slice assignment, which is
the smallest window CPython offers; a request racing the swap can at
worst see the old list for one request (fail-closed 421) and passes on
retry. The file itself is written atomically (temp file + os.replace)
via ``sassymcp._atomic``.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("sassymcp.allowed_hosts")

# Always allowed, no matter what the file or env var says. Matches the
# loopback default _build_server() shipped before this module existed.
LOOPBACK_HOSTS: tuple[str, ...] = (
    "localhost:*",
    "127.0.0.1:*",
    "localhost",
    "127.0.0.1",
)

# Env var that seeds the file on first use (and the list when no file
# exists yet). Kept for backwards compatibility with the pre-hot-swap
# setup in docs/TUNNEL.md.
_ENV_VAR = "SASSYMCP_ALLOWED_HOSTS"


def _hosts_file() -> Path:
    """Resolve the allowlist file lazily so tests can monkeypatch _paths."""
    from sassymcp import _paths

    return _paths.ALLOWED_HOSTS_FILE


def normalize_host(entry: str) -> str:
    """Validate and normalize one allowlist entry.

    Accepted forms (case-insensitive, stored lowercase):
      ``host``           — exact Host match, e.g. ``abc.trycloudflare.com``
      ``host:port``      — exact Host match incl. port
      ``host:*``         — any port on that host (SDK wildcard syntax)
      ``*.suffix``       — OPT-IN suffix wildcard (v1.21.0): any hostname
                           under the suffix, e.g. ``*.trycloudflare.com``

    Suffix wildcards are EXPLICIT opt-in only: they are never added by
    default, and only ever match because the operator deliberately wrote
    a ``*.suffix`` entry. Tradeoff, stated plainly: with a suffix
    wildcard, ANY hostname under that suffix passes the Host check —
    including a tunnel somebody else minted. The bearer token becomes
    the only gate for those hosts, and the DNS-rebinding protection the
    Host check provides is effectively off for that suffix. Do NOT use
    a suffix wildcard with auth disabled (``SASSYMCP_NO_AUTH=1``) — the
    server logs a loud warning if you do. For a single quick tunnel,
    prefer ``allow-host <exact-host>``; use ``*.trycloudflare.com`` only
    when the hostname genuinely changes every run and you accept that.

    Rejected: empty entries, anything with a scheme (``://``), a path,
    query or fragment (``/``, ``?``, ``#``), whitespace, or a ``*``
    anywhere except as a leading ``*.`` suffix wildcard or a trailing
    ``:*`` port wildcard (the SDK has no subdomain wildcards, so a bare
    ``*`` is meaningless and stays rejected).
    """
    host = entry.strip().lower().rstrip(".")
    if not host:
        raise ValueError("empty host entry")
    if "://" in host or "/" in host or "?" in host or "#" in host:
        raise ValueError(
            f"invalid host {entry!r}: no scheme, path, query, or fragment allowed"
        )
    if any(ch.isspace() for ch in host):
        raise ValueError(f"invalid host {entry!r}: whitespace not allowed")
    if "*" in host:
        # The only legal '*' positions: a leading "*." (suffix wildcard)
        # and/or a trailing ":*" (any port). Anything else is rejected.
        core = host[2:] if host.startswith("*.") else host
        if core.endswith(":*"):
            core = core[:-2]
        if "*" in core or not core or core.startswith((".", ":")):
            raise ValueError(
                f"invalid host {entry!r}: '*' is only allowed as a leading "
                "'*.' suffix wildcard and/or a trailing ':*' port wildcard"
            )
    if host in ("*", ":*"):
        raise ValueError(f"invalid host {entry!r}: a bare wildcard is not allowed")
    if ":" in host:
        _name, _, port = host.rpartition(":")
        if not _name:
            raise ValueError(f"invalid host {entry!r}: missing hostname before ':'")
        if port != "*" and not port.isdigit():
            raise ValueError(f"invalid host {entry!r}: port must be digits or '*'")
    return host


def is_suffix_wildcard(entry: str) -> bool:
    """True for an opt-in `*.suffix` entry (with optional :port/:* suffix)."""
    return entry.startswith("*.")


def _wildcard_suffix(entry: str) -> str | None:
    """Bare suffix for a `*.suffix` entry (port stripped), else None."""
    if not entry.startswith("*."):
        return None
    suffix = entry[2:]
    if ":" in suffix:
        suffix = suffix.split(":", 1)[0]
    return suffix or None


def matches_suffix_wildcard(host: str, entry: str) -> bool:
    """Does `host` (bare hostname, no port) fall under `*.suffix`?

    Requires at least one label below the suffix: ``a.suffix`` matches,
    the bare ``suffix`` itself does not, and ``a.b.suffix`` does (any
    depth below the suffix).
    """
    suffix = _wildcard_suffix(entry)
    if suffix is None:
        return False
    host = host.strip().lower().rstrip(".")
    if ":" in host:  # tolerate a Host header that still carries a port
        host = host.split(":", 1)[0]
    return host.endswith("." + suffix) and host != suffix


# Concrete hosts admitted at runtime by a suffix-wildcard match.
# In-memory only (gone on restart; re-provisioned on the next request).
# Kept separate from the file so a wildcard rule never silently writes
# concrete hosts to disk.
_provisioned: set[str] = set()


def provision_for_host(host_header: str | None, settings) -> bool:
    """Admit a request Host that matches an opt-in `*.suffix` entry.

    The SDK only exact-matches, so a wildcard rule cannot be enforced by
    the SDK itself. Instead, when a request's Host falls under a
    configured suffix wildcard, this appends the CONCRETE host (both the
    bare form and the `host:*` form, mirroring the loopback defaults) to
    the live SDK allowlist in place — the SDK then validates the concrete
    host exactly as it validates any other entry. Returns True when a
    host was provisioned. Never raises.
    """
    if not host_header:
        return False
    try:
        host = host_header.strip().lower().rstrip(".")
        if ":" in host:
            host = host.split(":", 1)[0]
        if not host or host.startswith("*."):
            return False
        current = set(settings.allowed_hosts)
        if host in current or f"{host}:*" in current:
            return False  # already allowed; nothing to provision
        for entry in _read_effective_hosts():
            if matches_suffix_wildcard(host, entry):
                for form in (host, f"{host}:*"):
                    if form not in current:
                        settings.allowed_hosts.append(form)
                        _provisioned.add(form)
                logger.info(
                    f"allowed_hosts: provisioned {host} via wildcard {entry}"
                )
                return True
        return False
    except Exception as e:
        logger.warning(f"allowed_hosts provisioning failed: {e}")
        return False


def _parse_entries(text: str) -> list[str]:
    """Parse allowlist file text: one host per line, `#` comments, blanks ignored."""
    entries: list[str] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            entries.append(normalize_host(line))
    return entries


def _seed_entries() -> list[str]:
    """Entries used when no allowlist file exists yet: env var or defaults."""
    raw = os.environ.get(_ENV_VAR, "")
    if raw.strip():
        return [normalize_host(h) for h in raw.split(",") if h.strip()]
    return list(LOOPBACK_HOSTS)


# ── mtime cache ───────────────────────────────────────────────────────
# _cache_key is (mtime_ns, size) of the file at last read, the sentinel
# _MISSING when the file did not exist, or None when nothing has been
# read yet. _cached holds the effective list for that key. An unchanged
# file costs one stat() per sync call.
_MISSING: tuple = ("__missing__", -1)
_cache_key: tuple | None = None
_cached: list[str] = []


def _read_effective_hosts() -> list[str]:
    """Read the effective list, using the mtime cache when nothing changed."""
    global _cache_key, _cached
    path = _hosts_file()
    try:
        st = path.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = _MISSING
    if key == _cache_key:
        return list(_cached)
    if key is _MISSING:
        entries = _seed_entries()
    else:
        try:
            entries = _parse_entries(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            # Corrupt/unreadable file: fail safe on the last-known list
            # (or the seed if we never had one) instead of locking out
            # every host or crashing the request path. Cache the fallback
            # under the current key so a corrupt file warns once per
            # change, not once per request.
            logger.warning(f"allowed_hosts file unreadable ({e}); keeping last-known list")
            fallback = list(_cached) if _cached else _seed_entries()
            _cache_key, _cached = key, fallback
            return list(fallback)
    # Loopback is always in, even if the file omits it.
    effective = list(LOOPBACK_HOSTS)
    for h in entries:
        if h not in effective:
            effective.append(h)
    _cache_key, _cached = key, effective
    return list(effective)


def get_effective_hosts() -> list[str]:
    """Current effective allowlist (loopback + file, or loopback + env seed)."""
    return _read_effective_hosts()


def initial_allowed_hosts() -> list[str]:
    """Initial allowlist for server startup (passed to the SDK settings).

    Same resolution as the runtime list — file if it exists, else the
    ``SASSYMCP_ALLOWED_HOSTS`` env seed, else loopback defaults — so a
    server started after ``allow-host`` ran (server down) boots with the
    file's hosts already in force.
    """
    return _read_effective_hosts()


def get_file_hosts() -> list[str]:
    """Hosts actually stored in the allowlist file (empty if no file yet)."""
    path = _hosts_file()
    try:
        return _parse_entries(path.read_text(encoding="utf-8"))
    except OSError:
        return []
    except ValueError as e:
        raise ValueError(f"{path} is corrupt: {e}")


def _write_file_hosts(entries: list[str]) -> None:
    """Atomically rewrite the allowlist file; invalidates the read cache."""
    global _cache_key
    from sassymcp._atomic import atomic_write_text

    content = "".join(h + "\n" for h in entries)
    atomic_write_text(_hosts_file(), content)
    _cache_key = None  # force a re-read on the next sync


def add_host(entry: str) -> str:
    """Add one host to the allowlist file. Returns the normalized entry.

    Seeds the file from the current effective list first, so env-var
    hosts are preserved rather than silently dropped. Idempotent:
    adding a host that is already allowed is a no-op returning it.
    """
    host = normalize_host(entry)
    entries = get_file_hosts()
    if host in entries or host in LOOPBACK_HOSTS:
        return host  # already allowed (loopback is implicit, not stored)
    if not _hosts_file().exists():
        # First write: carry the env seed into the file so nothing that
        # was allowed at boot stops being allowed.
        entries = [h for h in _seed_entries() if h not in LOOPBACK_HOSTS]
    if host not in entries:
        entries.append(host)
    _write_file_hosts(entries)
    logger.info(f"allow-host: added {host} (takes effect on the next request, no restart)")
    return host


def remove_host(entry: str) -> str:
    """Remove one host from the allowlist file. Returns the normalized entry.

    Loopback hosts are implicit and cannot be removed (they are never
    stored in the file); removing one is a no-op returning it.
    """
    host = normalize_host(entry)
    if host in LOOPBACK_HOSTS:
        return host
    entries = get_file_hosts()
    if host in entries:
        entries.remove(host)
        _write_file_hosts(entries)
        logger.info(f"deny-host: removed {host} (takes effect on the next request)")
    return host


def sync_to_settings(settings) -> bool:
    """Reconcile an SDK TransportSecuritySettings' allowlist with the file.

    Mutates ``settings.allowed_hosts`` IN PLACE (slice assignment keeps
    the same list object the SDK middleware reads per request). The
    target is the effective list PLUS any concrete hosts previously
    provisioned via a suffix wildcard (so a sync never wipes a host a
    live request just provisioned). Suffix-wildcard entries themselves
    ride along in the list — the SDK simply never exact-matches them;
    provisioning (see ``provision_for_host``) is what admits concrete
    hosts under them. Returns True when the list changed. Never raises
    — a sync failure leaves the last-known list in force and logs a
    warning.
    """
    try:
        effective = _read_effective_hosts()
    except Exception as e:  # seed parsing (bad env var) must not break requests
        logger.warning(f"allowed_hosts sync skipped ({e}); keeping last-known list")
        return False
    try:
        current = list(settings.allowed_hosts)
    except Exception:
        return False
    target = list(effective)
    for h in sorted(_provisioned):
        if h not in target:
            target.append(h)
    if current == target:
        return False
    settings.allowed_hosts[:] = target
    logger.info(f"allowed_hosts hot-swapped: {current} -> {target}")
    return True


class AllowedHostsSyncMiddleware:
    """Pure-ASGI wrapper: sync the SDK allowlist from the file per request.

    Wraps the Starlette app returned by ``mcp.streamable_http_app()`` /
    ``mcp.sse_app()`` in ``server.main()``. The sync is mtime-cached, so
    an unchanged file costs one stat() and the request then flows through
    untouched. Enforcement itself stays 100% the SDK's
    TransportSecurityMiddleware (Host 421 / Origin 403 / Content-Type 400
    semantics unchanged) — this wrapper only keeps the list it checks
    current.
    """

    def __init__(self, app, settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and self.settings is not None:
            try:
                sync_to_settings(self.settings)
            except Exception as e:  # never break a request on a sync failure
                logger.warning(f"allowed_hosts sync failed: {e}")
            # Opt-in suffix wildcards (e.g. *.trycloudflare.com) are
            # enforced here, not by the SDK: a request Host falling under
            # a configured wildcard gets its concrete host provisioned
            # into the live allowlist before the request is delegated,
            # so the SDK's exact-match check then passes it.
            try:
                headers = dict(scope.get("headers") or [])
                host = headers.get(b"host")
                if host:
                    provision_for_host(host.decode("latin-1"), self.settings)
            except Exception as e:  # never break a request on provisioning
                logger.warning(f"allowed_hosts provisioning failed: {e}")
        await self.app(scope, receive, send)


def wrap_allowed_hosts_sync(app, settings):
    """Wrap an ASGI app so the SDK allowlist is re-synced on every HTTP request."""
    return AllowedHostsSyncMiddleware(app, settings)
