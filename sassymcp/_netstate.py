# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-QSJHVQJLYOLT
"""Network-state oracle and local-model discovery for graceful offline degradation.

Two jobs, both fail-open:

1. Answer "is the internet actually reachable?" without ever blocking a tool
   call. Probes run on a daemon thread; callers read a cached verdict in
   microseconds. A never-probed or unknown state reads as ONLINE, so a broken
   probe degrades to today's behavior (attempt, then fail on its own timeout)
   instead of locking the toolset out. Same fail-open posture license.py
   already takes on `network_error` — an offline operator never loses tools.

2. Answer "what local model can take over?" by probing loopback inference
   servers (Ollama, llama.cpp, LM Studio, vLLM) for live model tags. The
   Ollama endpoint honors OLLAMA_URL so this agrees with hermes_node.py by
   construction rather than by convention.

Link state is deliberately three-valued: a link with working TCP but dead DNS
(captive portal, hijacked resolver) is classified `dns_only` and treated as
offline for anything that resolves a hostname, which is the honest answer.

Stdlib only — this has to work when the network that would install a
dependency is exactly what's missing.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Optional

# ── Probe configuration ───────────────────────────────────────────────

# Raw IPs: reachable without a resolver, so they isolate "link is up" from
# "DNS works". Port 53 answers TCP on all three.
_IP_ANCHORS: tuple[tuple[str, int], ...] = (
    ("1.1.1.1", 53),
    ("8.8.8.8", 53),
    ("9.9.9.9", 53),
)
# Hostname anchor: requires resolver + egress on 443, i.e. what a GitHub or
# update call actually needs.
_DNS_ANCHOR: tuple[str, int] = ("api.github.com", 443)

_PROBE_TIMEOUT = float(os.environ.get("SASSYMCP_NET_PROBE_TIMEOUT", "1.2"))
# Re-probe cadence. Offline is re-checked faster so recovery is noticed
# promptly; online is checked lazily so the common case costs nothing.
_FRESH_ONLINE = 30.0
_FRESH_OFFLINE = 8.0

_OLLAMA_ENV = os.environ.get("OLLAMA_URL", "").strip()
_OLLAMA_BASE = _OLLAMA_ENV.split("/v1/")[0] if _OLLAMA_ENV else "http://127.0.0.1:11434"

# Loopback inference servers, in discovery order. `chat` is OpenAI-compatible
# on every one of these, which is why hermes_node speaks that dialect.
LOCAL_BACKENDS: tuple[dict[str, str], ...] = (
    {"name": "ollama", "base": _OLLAMA_BASE, "models": "/api/tags",
     "chat": "/v1/chat/completions"},
    {"name": "llama.cpp", "base": "http://127.0.0.1:8080", "models": "/v1/models",
     "chat": "/v1/chat/completions"},
    {"name": "lmstudio", "base": "http://127.0.0.1:1234", "models": "/v1/models",
     "chat": "/v1/chat/completions"},
    {"name": "vllm", "base": "http://127.0.0.1:8000", "models": "/v1/models",
     "chat": "/v1/chat/completions"},
)

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1", "0.0.0.0", "[::1]")


# ── Cached state ──────────────────────────────────────────────────────

_LOCK = threading.Lock()
_state: dict[str, Any] = {
    "online": True,          # fail-open until a probe says otherwise
    "link": "assumed",       # assumed | online | dns_only | offline
    "dns": True,
    "checked_at": 0.0,
    "anchors": {},
    "probe_count": 0,
    "last_tool_failure": None,
}
_refreshing = False

_models_lock = threading.Lock()
_models_cache: dict[str, Any] = {"checked_at": 0.0, "backends": []}
_MODELS_TTL = 60.0


def _tcp_ok(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _probe() -> dict[str, Any]:
    """Blocking probe. Runs on a worker thread, never on the event loop."""
    anchors: dict[str, bool] = {}
    link_up = False
    for host, port in _IP_ANCHORS:
        ok = _tcp_ok(host, port, _PROBE_TIMEOUT)
        anchors[f"{host}:{port}"] = ok
        if ok:
            link_up = True
            break  # one reachable anchor is proof enough; don't pay for three
    dns_host, dns_port = _DNS_ANCHOR
    dns_ok = _tcp_ok(dns_host, dns_port, _PROBE_TIMEOUT)
    anchors[f"{dns_host}:{dns_port}"] = dns_ok
    if dns_ok:
        link_up = True

    if dns_ok:
        link = "online"
    elif link_up:
        link = "dns_only"
    else:
        link = "offline"

    return {
        "online": link == "online",
        "link": link,
        "dns": dns_ok,
        "checked_at": time.time(),
        "anchors": anchors,
    }


def _refresh_async() -> None:
    global _refreshing
    with _LOCK:
        if _refreshing:
            return
        _refreshing = True

    def _run():
        global _refreshing
        try:
            fresh = _probe()
            with _LOCK:
                _state.update(fresh)
                _state["probe_count"] = _state.get("probe_count", 0) + 1
        except Exception:
            pass  # a broken probe must never change the verdict
        finally:
            with _LOCK:
                _refreshing = False

    threading.Thread(target=_run, daemon=True, name="sassymcp-netprobe").start()


def snapshot(refresh: bool = True) -> dict[str, Any]:
    """Return the cached link verdict immediately.

    Kicks a background refresh when the cache is stale. Never blocks, so it is
    safe to call in the hot path of every tool invocation.
    """
    with _LOCK:
        st = dict(_state)
    age = time.time() - st["checked_at"]
    ttl = _FRESH_OFFLINE if not st["online"] else _FRESH_ONLINE
    st["age_seconds"] = round(age, 1)
    st["stale"] = age > ttl
    if refresh and st["stale"]:
        _refresh_async()
    return st


def refresh_now(timeout: float = 4.0) -> dict[str, Any]:
    """Probe synchronously and return the fresh verdict. For tools that were
    asked point-blank ("are we online?") and can afford the ~1-2s."""
    try:
        fresh = _probe()
        with _LOCK:
            _state.update(fresh)
            _state["probe_count"] = _state.get("probe_count", 0) + 1
    except Exception:
        pass
    return snapshot(refresh=False)


def is_online() -> bool:
    return bool(snapshot()["online"])


def confirmed_offline() -> bool:
    """True only when a real probe (not the fail-open default) said offline.

    The gate keys off this, never off `not is_online()`, so an un-probed
    server never blocks anything.
    """
    st = snapshot()
    return st["link"] in ("offline", "dns_only")


def note_tool_failure(tool_name: str, exc: BaseException) -> None:
    """Reactive signal: a tool just died on something that smells like a
    network fault. Expire the cache so the next read re-probes instead of
    serving a stale 'online' for up to 30s."""
    text = f"{type(exc).__name__}: {exc}".lower()
    markers = ("getaddrinfo", "name or service not known", "temporary failure in name",
               "connection refused", "network is unreachable", "no route to host",
               "timed out", "urlopen error", "ssl", "connectionerror", "connection aborted")
    if not any(m in text for m in markers):
        return
    with _LOCK:
        _state["checked_at"] = 0.0  # force stale
        _state["last_tool_failure"] = {"tool": tool_name, "error": text[:200],
                                       "at": time.time()}
    _refresh_async()


# ── Local model discovery ─────────────────────────────────────────────

def _http_json(url: str, timeout: float = 1.5) -> Optional[dict]:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def _extract_models(backend: str, payload: dict) -> list[str]:
    if backend == "ollama":
        return [m.get("name", "") for m in payload.get("models", []) if m.get("name")]
    # OpenAI-compatible /v1/models
    return [m.get("id", "") for m in payload.get("data", []) if m.get("id")]


def local_models(force: bool = False) -> list[dict[str, Any]]:
    """Probe loopback inference servers. Returns only the ones that answered.

    Cached for 60s — model lists don't move fast, and this runs behind an
    operator asking "what can take over", not in a hot path.
    """
    with _models_lock:
        fresh_enough = (time.time() - _models_cache["checked_at"]) < _MODELS_TTL
        if fresh_enough and not force:
            return list(_models_cache["backends"])

    found: list[dict[str, Any]] = []
    for be in LOCAL_BACKENDS:
        host_port = be["base"].split("://", 1)[-1]
        host, _, port = host_port.partition(":")
        if not _tcp_ok(host, int(port or 80), 0.4):
            continue
        payload = _http_json(be["base"] + be["models"])
        if payload is None:
            continue
        found.append({
            "backend": be["name"],
            "base": be["base"],
            "chat_endpoint": be["base"] + be["chat"],
            "models": _extract_models(be["name"], payload),
        })

    with _models_lock:
        _models_cache["checked_at"] = time.time()
        _models_cache["backends"] = found
    return list(found)


def fallback_ready() -> dict[str, Any]:
    """Is there a local model that can carry the session, and which one?"""
    backends = local_models()
    preferred_model = os.environ.get("HERMES_MODEL", "").strip()
    chosen = None
    for be in backends:
        if not be["models"]:
            continue
        if preferred_model and any(
            preferred_model == m or preferred_model.split(":")[0] in m for m in be["models"]
        ):
            chosen = {"backend": be["backend"], "model": preferred_model,
                      "chat_endpoint": be["chat_endpoint"]}
            break
        if chosen is None:
            chosen = {"backend": be["backend"], "model": be["models"][0],
                      "chat_endpoint": be["chat_endpoint"]}
    return {
        "ready": chosen is not None,
        "chosen": chosen,
        "backends_up": [b["backend"] for b in backends],
        "reason": None if chosen else (
            "No loopback inference server answered. Start one "
            "(`ollama serve`, llama.cpp --server, LM Studio) and re-check."
        ),
    }


# ── Tool network classification ───────────────────────────────────────

# Prefix rules beat a hand-maintained per-tool table: they stay correct as
# tools are added to a family, which is exactly how this surface grows.
# Longest prefix wins, so a specific tool can override its family.
_PREFIX_RULES: tuple[tuple[str, str], ...] = (
    ("sassy_gh_", "internet"),
    ("sassy_ghq_", "internet"),
    ("sassy_url_", "internet"),
    ("sassy_http", "internet"),
    ("sassy_update_", "internet"),
    ("sassy_setup_github", "internet"),
    ("sassy_setup_license", "internet"),
    ("sassy_setup_tools", "internet"),
    ("sassy_cert_check", "internet"),
    ("sassy_dns_lookup", "internet"),
    ("sassy_traceroute", "internet"),
    ("sassy_combo_pr_review", "internet"),
    # LAN-only: needs a network, but not the internet. Never gated offline.
    ("sassy_linux_", "lan"),
    ("sassy_adb_wifi_connect", "lan"),
    ("sassy_port_scan", "lan"),
    ("sassy_wifi_", "lan"),
    ("sassy_arp_table", "lan"),
)

def _group_default(group: Optional[str]) -> str:
    """Fall back to the group's declared `network` field in TOOL_GROUPS.

    Single source of truth: the group table declares the family requirement,
    the prefix rules above refine it per tool. A group marked "mixed" carries
    no default — its internet tools are the ones a prefix rule names.
    """
    if not group:
        return "none"
    try:
        from sassymcp.modules._tool_loader import TOOL_GROUPS
        val = TOOL_GROUPS.get(group, {}).get("network", "none")
    except Exception:
        return "none"
    return val if val in ("none", "lan", "internet") else "none"


def classify_tool(tool_name: str, group: Optional[str] = None) -> str:
    """Return 'none' | 'lan' | 'internet' — what this tool needs to work."""
    best = ("", "")
    for prefix, req in _PREFIX_RULES:
        if tool_name.startswith(prefix) and len(prefix) > len(best[0]):
            best = (prefix, req)
    if best[0]:
        return best[1]
    return _group_default(group)


# Offline substitutes. Keyed by prefix, same longest-match rule. These are the
# lines the model reads when it hits the gate, so they name a concrete next
# action rather than restating that the network is down.
_ALTERNATIVES: tuple[tuple[str, str], ...] = (
    ("sassy_gh_", "Work locally: `sassy_shell` git add/commit on a branch. Stage the PR "
                  "title/body with `sassy_memory_remember key=\"task_pr_<repo>_state\"` and "
                  "push + open the PR when the link returns."),
    ("sassy_ghq_", "Same as sassy_gh_*: commit locally, queue the PR text in memory, push later."),
    ("sassy_url_", "No offline substitute for live page fetches. Use a previously saved copy "
                   "via `sassy_read_file`, or `sassy_search_files` over a local mirror."),
    ("sassy_http", "Only loopback targets work offline — point it at 127.0.0.1 (e.g. the local "
                   "Ollama endpoint). Remote hosts will fail."),
    ("sassy_update_", "Update checks require the release feed. Skip until online; "
                      "SASSYMCP_NO_UPDATE_CHECK=1 silences the startup probe."),
    ("sassy_setup_github", "Token setup needs api.github.com. Defer; the rest of the wizard "
                           "(`sassy_setup_status`, `sassy_setup_check_tools`) works offline."),
    ("sassy_setup_license", "License validation is fail-open by design — your existing license "
                            "stays valid offline. Nothing to do."),
    ("sassy_cert_check", "Remote TLS inspection needs egress. For a local cert, "
                         "`sassy_read_file` the PEM and inspect it directly."),
    ("sassy_dns_lookup", "Resolver is unreachable. Check the hosts file with `sassy_read_file`, "
                         "or use a known IP directly."),
    ("sassy_traceroute", "Needs egress. `sassy_arp_table` and `sassy_netstat` still map the "
                         "local segment."),
    ("sassy_combo_pr_review", "Its GitHub leg is down. Use `sassy_combo_codebase_grep` and "
                              "`sassy_diff` to review the working tree locally."),
)


def offline_alternative(tool_name: str) -> Optional[str]:
    best = ("", None)
    for prefix, alt in _ALTERNATIVES:
        if tool_name.startswith(prefix) and len(prefix) > len(best[0]):
            best = (prefix, alt)
    return best[1]


def targets_loopback(kwargs: dict[str, Any]) -> bool:
    """True when the call is aimed at localhost, so the gate lets it through.

    Probing the local Ollama with `sassy_http` while the WAN is down is a
    legitimate — and, offline, a very likely — thing to want.
    """
    for key in ("url", "host", "hostname", "target", "endpoint", "address", "base_url"):
        val = kwargs.get(key)
        if isinstance(val, str) and any(h in val.lower() for h in _LOOPBACK_HOSTS):
            return True
    return False


# ── Gate policy ───────────────────────────────────────────────────────

def gate_mode() -> str:
    """auto (default) — refuse internet tools only on a CONFIRMED-offline probe.
    off — never gate; every call is attempted and fails on its own timeout."""
    mode = os.environ.get("SASSYMCP_OFFLINE_GATE", "auto").strip().lower()
    return mode if mode in ("auto", "off") else "auto"


def gate(tool_name: str, group: Optional[str], kwargs: dict[str, Any]) -> Optional[dict]:
    """Return a structured refusal dict when this call cannot possibly succeed,
    else None. The refusal is the graceful part: an immediate, accurate answer
    with a named substitute, instead of a 10-30s DNS or TLS timeout."""
    if gate_mode() == "off":
        return None
    if classify_tool(tool_name, group) != "internet":
        return None
    if targets_loopback(kwargs):
        return None
    st = snapshot()
    if st["link"] not in ("offline", "dns_only"):
        return None

    fb = fallback_ready()
    return {
        "error": "offline",
        "tool": tool_name,
        "link_state": st["link"],
        "detail": (
            "No internet egress" if st["link"] == "offline"
            else "Link is up but DNS is not resolving (captive portal or dead resolver)"
        ),
        "probed_at_age_seconds": st["age_seconds"],
        "retryable": True,
        "retry_after_seconds": 30,
        "offline_alternative": offline_alternative(tool_name),
        "local_model": fb["chosen"] if fb["ready"] else None,
        "hint": (
            "Call sassy_offline_status for the full offline-capable command list, "
            "or sassy_offline_commands for just the names. Set SASSYMCP_OFFLINE_GATE=off "
            "to attempt the call anyway."
        ),
    }


__all__ = [
    "snapshot", "refresh_now", "is_online", "confirmed_offline", "note_tool_failure",
    "local_models", "fallback_ready", "classify_tool", "offline_alternative",
    "targets_loopback", "gate", "gate_mode", "LOCAL_BACKENDS",
]
