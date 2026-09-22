# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-DZ3ZHFE4DIO4
"""Per-session tool profiles — dashboard-controlled gating of the MCP tool set.

A *profile* is a named subset of the tool catalog that the current MCP
session may see and call. Profiles are a HUMAN-surface control: they are
switched only from the Control Panel web UI (`GET`/`POST /api/profile`).
No MCP tool can switch, widen, or inspect the profile machinery, so a
session can never escalate itself out of a restrictive profile.

How it works
------------
At server startup (`install()`, called from `server._load_modules()` after
tool registration, audit wrapping, and metadata application) the module
stashes the full tool dict and binds the FastMCP `ToolManager`. Activating
a profile rebinds `tool_manager._tools` to a filtered dict holding the same
`Tool` objects (audit wrappers, descriptions, and annotations stay intact).

Enforcement chokepoint: both MCP paths resolve through the tool manager's
`_tools` dict (verified against the installed `mcp` package):

* `FastMCP.list_tools()` -> `ToolManager.list_tools()` -> `list(self._tools.values())`
* `FastMCP.call_tool(name, ...)` -> `ToolManager.call_tool()` ->
  `self.get_tool(name)` -> `self._tools.get(name)`, raising
  `ToolError("Unknown tool")` when the tool is hidden.

So a tool hidden from `tools/list` is also uncallable via `tools/call` —
including fan-out paths like `sassy_batch`, which dispatch through the same
manager. There is no second registry to keep in sync.

Scope and limits
----------------
* Session-scoped, runtime-only: state lives in module globals, is never
  written to disk, and resets to `full` on every server restart.
* Profiles filter by tool GROUP for the presets, except `readonly`, which
  is computed per-tool from `tool_annotations.py` (`readOnlyHint=True`).
* `meta` is forced on in every group-based profile: its tools are the
  session's introspection layer (`sassy_tool_groups`, `sassy_self_check`,
  ...). Without them a session under a restrictive profile would have no
  window into what it can and cannot see. `sassy_tool_group_toggle` only
  flips `always_load` flags for the NEXT startup — it cannot add tools to
  the live registry, so it is not an escalation path (and profiles reset
  to full on restart anyway).
* Profile changes take effect for the CURRENT process only. Clients that
  cache the tool list should re-list; the panel cannot push a
  `notifications/tools/list_changed` because it has no MCP session handle
  (same degraded-but-safe posture as `sassy_tool_group_toggle`).

This module registers NO MCP tools: it defines no `register()` and no
`sassy_*` callables, so the tool count (278) is unchanged.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger("sassymcp.tool_profiles")

from sassymcp.modules._tool_loader import TOOL_GROUPS, get_group_for_tool

# Groups every preset resolves against — kept as an explicit list (rather
# than `list(TOOL_GROUPS)`) so a typo'd profile definition fails loudly at
# import time via the _validate_profiles() check below.
_ALL_GROUP_NAMES = (
    "meta", "core", "infrastructure", "android", "iphone", "system",
    "forensics", "linux", "github_quick", "github_full", "v020", "persona",
    "utility", "setup", "memory", "updater", "combos", "prompts",
)

# Forced into every group-based profile (see module docstring).
ALWAYS_ON_GROUPS = ("meta",)

PROFILES: dict[str, dict] = {
    "full": {
        "title": "Full Access",
        "description": "All 18 tool groups. The default on every server start.",
        "groups": list(_ALL_GROUP_NAMES),
    },
    "developer": {
        "title": "Developer",
        "description": "Code, GitHub, vision/web, memory, persona, setup, updater. "
                       "No device control, forensics, or remote-Linux tools.",
        "groups": ["meta", "core", "infrastructure", "utility", "github_quick",
                   "github_full", "v020", "memory", "persona", "setup", "updater"],
    },
    "forensics": {
        "title": "Forensics",
        "description": "Security audit + registry inspection (forensics group: "
                       "security_audit, registry), plus event logs via the system "
                       "group. No device control or GitHub write surface.",
        "groups": ["meta", "core", "infrastructure", "forensics", "utility",
                   "system", "memory"],
    },
    "devices": {
        "title": "Device Farm",
        "description": "Android (adb) + iPhone (libimobiledevice) control with "
                       "core file/shell tooling. No GitHub, forensics, or "
                       "remote-Linux surface.",
        "groups": ["meta", "core", "infrastructure", "android", "iphone",
                   "utility", "memory"],
    },
    "sysadmin": {
        "title": "Sysadmin",
        "description": "Local system monitoring, remote Linux over SSH, "
                       "updater. No phone control, GitHub, or forensics.",
        "groups": ["meta", "core", "infrastructure", "system", "linux",
                   "utility", "memory", "updater"],
    },
    "readonly": {
        "title": "Safe / Read-Only",
        "description": "Every tool whose curated MCP annotation says "
                       "readOnlyHint=True — computed per-tool from "
                       "tool_annotations.py, not from groups. No state "
                       "mutation of any kind is possible.",
        "groups": None,  # computed, see _read_only_tools()
    },
    "custom": {
        "title": "Custom",
        "description": "An explicit group set chosen in the Control Panel. "
                       "Stored for the session only.",
        "groups": None,  # supplied at activation time
    },
}


def _validate_profiles() -> None:
    for name, prof in PROFILES.items():
        groups = prof.get("groups")
        if groups is None:
            continue
        unknown = [g for g in groups if g not in TOOL_GROUPS]
        if unknown:
            raise ValueError(f"profile {name!r} references unknown groups: {unknown}")


_validate_profiles()


def _read_only_tools() -> set[str]:
    """Tool names whose curated annotation is readOnlyHint=True."""
    try:
        from sassymcp import tool_annotations as _ta
    except Exception as e:  # fail closed: unknown membership -> empty
        logger.warning(f"tool_annotations unavailable, readonly profile empty: {e}")
        return set()
    return {
        name for name, ann in _ta.TOOL_ANNOTATIONS.items()
        if ann.get("readOnlyHint")
    }


class ConfirmRequired(ValueError):
    """Raised when an escalation needs explicit confirm='YES'."""


# ── Session-scoped runtime state (never persisted) ─────────────────────

# RLock: install()/activate_profile() hold the lock while calling
# profile_status(), which also takes it.
_LOCK = threading.RLock()
_ACTIVE_PROFILE = "full"
_CUSTOM_GROUPS: list[str] | None = None
_TOOL_MANAGER = None          # bound FastMCP ToolManager
_FULL_TOOLS: dict | None = None  # stashed full registry {name: Tool}


def is_bound() -> bool:
    return _TOOL_MANAGER is not None


def get_active() -> str:
    return _ACTIVE_PROFILE


def get_custom_groups() -> list[str] | None:
    return list(_CUSTOM_GROUPS) if _CUSTOM_GROUPS is not None else None


def resolve_groups(profile: str, custom_groups: list[str] | None = None) -> set[str]:
    """Group set for a profile (with ALWAYS_ON_GROUPS forced in).

    Raises ValueError on unknown profile / unknown group names.
    """
    if profile not in PROFILES:
        raise ValueError(
            f"unknown profile {profile!r}; valid: {sorted(PROFILES)}")
    prof = PROFILES[profile]
    if profile == "custom":
        if not custom_groups:
            raise ValueError(
                "custom profile needs an explicit non-empty 'groups' list")
        unknown = [g for g in custom_groups if g not in TOOL_GROUPS]
        if unknown:
            raise ValueError(f"unknown groups: {unknown}")
        return set(custom_groups) | set(ALWAYS_ON_GROUPS)
    groups = prof.get("groups")
    if groups is None:  # readonly — group resolution is not applicable
        raise ValueError(f"profile {profile!r} is tool-computed, not group-based")
    return set(groups) | set(ALWAYS_ON_GROUPS)


def _allowed_tool_names(profile: str,
                        custom_groups: list[str] | None = None) -> set[str]:
    """Tool names visible under a profile, intersected with the live registry."""
    if _FULL_TOOLS is None:
        return set()
    if profile == "readonly":
        return set(_read_only_tools()) & set(_FULL_TOOLS)
    if profile == "custom" and not custom_groups:
        return set()  # no custom set chosen yet — activation validates
    allowed_groups = resolve_groups(profile, custom_groups)
    return {
        name for name in _FULL_TOOLS
        if get_group_for_tool(name) in allowed_groups
    }


def _needs_confirm(new_tools: set[str]) -> bool:
    """True when activation would EXPOSE any tool not currently visible.

    Rule: moving to a broader tool set requires confirm='YES'. Pure
    de-escalations (hiding tools only) and no-op re-applies never do.
    """
    if _TOOL_MANAGER is None:
        return False
    current = set(_TOOL_MANAGER._tools)
    return bool(new_tools - current)


def install(tool_manager) -> dict:
    """Bind the tool manager at server startup; stash the full registry.

    Idempotent: re-installing (e.g. dev live-reload) re-stashes and
    re-applies the currently active profile so the session's gating
    survives re-registration.
    """
    global _TOOL_MANAGER, _FULL_TOOLS
    with _LOCK:
        _TOOL_MANAGER = tool_manager
        _FULL_TOOLS = dict(tool_manager._tools)
        logger.info(f"tool profiles bound: {len(_FULL_TOOLS)} tools stashed")
        _apply_locked(_ACTIVE_PROFILE, _CUSTOM_GROUPS)
        return profile_status()


def activate_profile(profile: str, tool_manager=None, *,
                     groups: list[str] | None = None,
                     confirm: bool = False) -> dict:
    """Switch the active profile and swap the visible tool set.

    `tool_manager` defaults to the manager bound by `install()`.
    Raises ConfirmRequired when the switch would expose tools that are
    currently hidden and confirm is not True; ValueError on bad input.
    """
    global _FULL_TOOLS, _TOOL_MANAGER, _ACTIVE_PROFILE, _CUSTOM_GROUPS
    with _LOCK:
        tm = tool_manager if tool_manager is not None else _TOOL_MANAGER
        if tm is None:
            raise ValueError("tool profiles not initialized (server still starting?)")
        if profile not in PROFILES:
            raise ValueError(
                f"unknown profile {profile!r}; valid: {sorted(PROFILES)}")
        new_groups = list(groups) if profile == "custom" else None
        if profile == "custom":
            if not new_groups:
                raise ValueError(
                    "custom profile needs an explicit non-empty 'groups' list")
            unknown = [g for g in new_groups if g not in TOOL_GROUPS]
            if unknown:
                raise ValueError(f"unknown groups: {unknown}")
        # Establish the full-registry stash on first use: whatever the
        # manager holds now is the full set unless install() already
        # stashed it. The stash is never the currently filtered view.
        if _FULL_TOOLS is None:
            _FULL_TOOLS = dict(tm._tools)
        _TOOL_MANAGER = tm
        new_tools = _allowed_tool_names(profile, new_groups)
        if _needs_confirm(new_tools) and not confirm:
            hidden_now = sorted(new_tools - set(tm._tools))
            raise ConfirmRequired(
                f"switching to {profile!r} would expose "
                f"{len(hidden_now)} currently-hidden tool(s) "
                f"(e.g. {', '.join(hidden_now[:3])}); "
                f"re-post with confirm='YES' to proceed")
        _ACTIVE_PROFILE = profile
        _CUSTOM_GROUPS = new_groups
        _apply_locked(profile, new_groups)
        return profile_status()


def _apply_locked(profile: str, custom_groups: list[str] | None) -> None:
    """Rebind tool_manager._tools to the profile's allowed subset.

    Caller must hold _LOCK. Swaps the dict OBJECT (atomic under the GIL)
    rather than mutating in place, so in-flight list/call readers always
    see a consistent registry. Tool objects are shared, not copied — audit
    wrappers, descriptions, and annotations stay intact.
    """
    global _FULL_TOOLS
    if _TOOL_MANAGER is None or _FULL_TOOLS is None:
        return
    allowed = _allowed_tool_names(profile, custom_groups)
    _TOOL_MANAGER._tools = {n: _FULL_TOOLS[n] for n in allowed if n in _FULL_TOOLS}
    logger.info(f"tool profile -> {profile!r}: {len(_TOOL_MANAGER._tools)} "
                f"of {len(_FULL_TOOLS)} tools visible")


def profile_status() -> dict:
    """Snapshot for the panel: active profile, definitions, group catalog."""
    with _LOCK:
        active = _ACTIVE_PROFILE
        custom = list(_CUSTOM_GROUPS) if _CUSTOM_GROUPS is not None else None
        bound = _TOOL_MANAGER is not None
    profiles = []
    for name, prof in PROFILES.items():
        if name == "custom":
            groups = custom
        elif name == "readonly":
            groups = None
        else:
            groups = sorted(set(prof["groups"]) | set(ALWAYS_ON_GROUPS))
        entry = {
            "name": name,
            "title": prof["title"],
            "description": prof["description"],
            "groups": groups,
            "tool_count": (len(_allowed_tool_names(name, custom))
                           if bound else None),
        }
        profiles.append(entry)
    return {
        "active": active,
        "custom_groups": custom,
        "bound": bound,
        "session_scoped": True,
        "profiles": profiles,
        "groups": [
            {"name": g, "description": TOOL_GROUPS[g]["description"]}
            for g in _ALL_GROUP_NAMES
        ],
    }


def reset() -> None:
    """Test hook: drop all session state (default is full, unbound)."""
    global _ACTIVE_PROFILE, _CUSTOM_GROUPS, _TOOL_MANAGER, _FULL_TOOLS
    with _LOCK:
        _ACTIVE_PROFILE = "full"
        _CUSTOM_GROUPS = None
        _TOOL_MANAGER = None
        _FULL_TOOLS = None
