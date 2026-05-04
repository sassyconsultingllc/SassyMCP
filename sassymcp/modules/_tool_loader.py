"""SassyMCP Smart Tool Loader v1.0 — Production + Nuclear features.

Tracks which tools are actually called, scores them, and enables
dynamic tool set management via MCP notifications/tools/list_changed.

Features:
- Usage frequency tracking with exponential decay (persisted to JSON)
- Tool group enable/disable at runtime
- Context window estimation
- Response minification for GitHub API payloads
- Proactive pruning suggestions
- Tool dependency graph
- Pre-registration schema validation
- Live reload in dev mode (watchdog)
- Schema versioning for efficient tool discovery
- Per-group rate limiting

Storage: ~/.sassymcp/tool_usage.json
"""

import asyncio
import hashlib
import importlib
import inspect
import json
import logging
import math
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("sassymcp.loader")

# Optional live reload (pip install watchdog)
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False

from sassymcp._paths import HOME as USAGE_DIR
USAGE_FILE = USAGE_DIR / "tool_usage.json"
DECAY_HALF_LIFE = 7 * 86400  # 7 days in seconds — older usage decays


class ToolUsageTracker:
    """Tracks tool invocations with timestamps for ML-lite scoring."""

    def __init__(self):
        self._data: dict[str, list[float]] = {}
        self._load()

    def _load(self):
        try:
            if USAGE_FILE.exists():
                raw = json.loads(USAGE_FILE.read_text())
                self._data = raw.get("tools", {})
        except Exception as e:
            logger.warning(f"Failed to load tool usage: {e}")
            self._data = {}

    def _save(self):
        try:
            USAGE_DIR.mkdir(parents=True, exist_ok=True)
            # Prune: keep only last 500 invocations per tool, last 90 days
            cutoff = time.time() - 90 * 86400
            pruned = {}
            for tool, timestamps in self._data.items():
                recent = [t for t in timestamps if t > cutoff][-500:]
                if recent:
                    pruned[tool] = recent
            # Update in-memory data with pruned version (prevent unbounded growth)
            self._data = pruned
            # Atomic write: temp file then rename
            tmp = USAGE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps({"tools": pruned, "updated": time.time()}, indent=1))
            tmp.replace(USAGE_FILE)
        except Exception as e:
            logger.warning(f"Failed to save tool usage: {e}")

    def record(self, tool_name: str):
        """Record a tool invocation with debounced saves (every 30s max)."""
        if tool_name not in self._data:
            self._data[tool_name] = []
        self._data[tool_name].append(time.time())
        # Debounce: save at most once per 30 seconds
        if not hasattr(self, "_last_save"):
            self._last_save = 0.0
        now = time.time()
        if now - self._last_save > 30.0:
            self._save()
            self._last_save = now

    def score(self, tool_name: str) -> float:
        """Score a tool 0.0-1.0 based on recency-weighted usage frequency.

        Uses exponential decay: recent invocations count more.
        Score formula: sum(e^(-lambda * age)) where lambda = ln(2) / half_life
        """
        timestamps = self._data.get(tool_name, [])
        if not timestamps:
            return 0.0
        now = time.time()
        lam = math.log(2) / DECAY_HALF_LIFE
        raw = sum(math.exp(-lam * (now - t)) for t in timestamps)
        # Normalize: 10+ weighted calls in last week = score 1.0
        return min(raw / 10.0, 1.0)

    def top_tools(self, n: int = 20) -> list[tuple[str, float]]:
        """Return top N tools by score."""
        scores = [(name, self.score(name)) for name in self._data]
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:n]

    def get_stats(self) -> dict:
        """Return usage statistics."""
        now = time.time()
        day_cutoff = now - 86400
        week_cutoff = now - 7 * 86400

        total_calls = sum(len(v) for v in self._data.values())
        today_calls = sum(
            sum(1 for t in v if t > day_cutoff)
            for v in self._data.values()
        )
        week_calls = sum(
            sum(1 for t in v if t > week_cutoff)
            for v in self._data.values()
        )
        unique_tools = len(self._data)

        return {
            "unique_tools_ever": unique_tools,
            "total_invocations": total_calls,
            "invocations_today": today_calls,
            "invocations_this_week": week_calls,
            "top_10": self.top_tools(10),
        }

    def suggest_pruning(self, threshold: float = 0.05) -> list[str]:
        """Return tools with score below threshold — candidates for disabling."""
        try:
            return [name for name, sc in self.top_tools(50) if sc < threshold]
        except Exception:
            return []


# Module-level singleton
_tracker: Optional[ToolUsageTracker] = None


def get_tracker() -> ToolUsageTracker:
    global _tracker
    if _tracker is None:
        _tracker = ToolUsageTracker()
    return _tracker


# ── Tool Groups ────────────────────────────────────────────────────────

TOOL_GROUPS = {
    "meta": {
        "modules": ["meta"],
        "description": "Context estimation, tool usage stats, group management (always loaded)",
        "always_load": True,
        "max_concurrent": 10,
        "calls_per_minute": 120,
    },
    "core": {
        "modules": ["fileops", "shell", "ui_automation", "editor", "audit", "session"],
        "description": "File operations, shell commands, desktop automation, surgical editing, audit logging, persistent terminal sessions",
        "always_load": True,
        "max_concurrent": 10,
        "calls_per_minute": 120,
    },
    "infrastructure": {
        "modules": ["observability", "state_manager", "runtime_config"],
        "description": "Metrics, health, persistent state, runtime config (always loaded)",
        "always_load": True,
        "max_concurrent": 10,
        "calls_per_minute": 120,
    },
    "android": {
        "modules": ["adb", "phone_screen"],
        "description": "ADB device control, screen mirroring",
        "always_load": False,
        "max_concurrent": 3,
        "calls_per_minute": 30,
    },
    "system": {
        "modules": ["network_audit", "process_manager", "security_audit",
                     "registry", "bluetooth", "eventlog", "clipboard"],
        "description": "System monitoring, security, networking",
        "always_load": False,
        "max_concurrent": 5,
        "calls_per_minute": 60,
    },
    "linux": {
        "modules": ["linux"],
        "description": "Remote Linux SSH commands via plink (streaming)",
        "always_load": False,
        "max_concurrent": 3,
        "calls_per_minute": 30,
    },
    "github_quick": {
        "modules": ["github_quick"],
        "description": "Daily-driver GitHub tools (6 tools)",
        "always_load": True,
        "max_concurrent": 5,
        "calls_per_minute": 30,
    },
    "github_full": {
        "modules": ["github_ops"],
        "description": "Full GitHub API (80 tools) — heavy context cost",
        "always_load": False,
        "max_concurrent": 5,
        "calls_per_minute": 30,
    },
    "v020": {
        "modules": ["vision", "app_launcher", "web_inspector", "crosslink"],
        "description": "Vision, app launcher, web inspector, crosslink",
        "always_load": False,
        "max_concurrent": 5,
        "calls_per_minute": 60,
    },
    "persona": {
        "modules": ["persona"],
        "description": "Expert-mode persona, decision framework, engineering standards",
        "always_load": True,
        "max_concurrent": 10,
        "calls_per_minute": 120,
    },
    "utility": {
        "modules": ["utility"],
        "description": "Env vars, toast notifications, zip/tar archives, file diff, HTTP requests",
        "always_load": True,
        "max_concurrent": 10,
        "calls_per_minute": 120,
    },
    "selfmod": {
        "modules": ["selfmod"],
        "description": "Self-modification: edit MCP source, hot-reload modules, git-backed rollback",
        "always_load": True,
        "max_concurrent": 3,
        "calls_per_minute": 30,
    },
    "setup": {
        "modules": ["setup_wizard", "tools_manager"],
        "description": "First-run setup wizard, external tool bootstrap, auth token generation",
        "always_load": True,
        "max_concurrent": 3,
        "calls_per_minute": 30,
    },
    "memory": {
        "modules": ["memory"],
        "description": "Persistent cross-session memory, task handoffs, milestones, pattern learning",
        "always_load": True,
        "max_concurrent": 10,
        "calls_per_minute": 120,
    },
    "updater": {
        "modules": ["updater"],
        "description": "Kali-style version checks and self-update (check, list, changelog, apply)",
        "always_load": True,
        "max_concurrent": 2,
        "calls_per_minute": 12,
    },
}


# ── Tool Dependency Graph ─────────────────────────────────────────────

TOOL_DEPENDENCIES = {
    "vision": {"ui_automation", "utility"},
    "linux": {"utility", "session"},
    "web_inspector": {"utility"},
    "github_ops": {"utility"},
    "github_quick": {"utility"},
    "app_launcher": {"ui_automation"},
    "phone_screen": {"adb"},
}

# Reverse lookup: module name → group name
_MODULE_TO_GROUP: dict[str, str] = {}
for _gname, _ginfo in TOOL_GROUPS.items():
    for _mod in _ginfo["modules"]:
        _MODULE_TO_GROUP[_mod] = _gname


def get_group_for_module(module_name: str) -> Optional[str]:
    """Return the group name a module belongs to, or None."""
    return _MODULE_TO_GROUP.get(module_name)


_TOOL_TO_GROUP: dict[str, str] = {}


def register_tool_group(tool_name: str, module_name: str):
    """Record which group a tool belongs to. Called during module registration."""
    group = _MODULE_TO_GROUP.get(module_name)
    if group:
        _TOOL_TO_GROUP[tool_name] = group


def get_group_for_tool(tool_name: str) -> Optional[str]:
    """Reverse lookup: tool name → group name. Uses explicit registry built at load time."""
    return _TOOL_TO_GROUP.get(tool_name)


def resolve_dependencies(modules: list[str]) -> list[str]:
    """Given a list of modules, add any missing dependencies."""
    resolved = set(modules)
    changed = True
    while changed:
        changed = False
        for mod in list(resolved):
            deps = TOOL_DEPENDENCIES.get(mod, set())
            for dep in deps:
                if dep not in resolved:
                    resolved.add(dep)
                    changed = True
                    logger.info(f"Auto-loaded dependency: {dep} (required by {mod})")
    return list(resolved)


# Threshold: any tool with score >= this value force-loads its containing
# group, regardless of always_load. The README's "your most-used tools load
# first" promise actually depends on this — without it, a user whose top-5
# scorer is sassy_adb_shell never sees the android group auto-loaded.
USAGE_BOOST_THRESHOLD = 0.5

# Tools with score < this are dropped from the default load entirely (Pro
# tier still loads everything via SASSYMCP_LOAD_ALL=1 or per-group toggle).
# Saves context on the long tail of "registered but never used" tools.
USAGE_PRUNE_THRESHOLD = 0.05


def get_score_boosted_modules() -> list[str]:
    """Return modules whose tools have usage scores >= USAGE_BOOST_THRESHOLD.

    The user has demonstrably used these tools enough that the model should
    see them every session. Without this hook, a user's actual workflow can
    diverge wildly from the static `always_load` defaults — e.g., a user
    who lives in `sassy_adb_shell` (android group, always_load=False) would
    have to manually toggle the group on every session to see their #1
    most-used tool.
    """
    try:
        tracker = get_tracker()
    except Exception as e:
        logger.warning(f"Could not load usage tracker for boost: {e}")
        return []

    boosted_modules: set[str] = set()
    for tool_name, score in tracker.top_tools(50):
        if score < USAGE_BOOST_THRESHOLD:
            break  # tracker.top_tools is sorted desc; nothing below qualifies
        # Map tool -> module via the explicit registry (best effort).
        # During first-import the registry may be empty; in that case we
        # fall back to a name-prefix heuristic that catches the common
        # sassy_<verb>_<noun> pattern (e.g. sassy_adb_shell -> adb).
        group = _TOOL_TO_GROUP.get(tool_name)
        if group:
            boosted_modules.update(TOOL_GROUPS[group]["modules"])
            continue
        # Heuristic fallback for cold-start: split on underscores after the
        # `sassy_` prefix and try each segment as a module name. When a
        # match is found, boost the WHOLE GROUP's modules (not just the one
        # matched module) — that way a single high-score tool like
        # sassy_adb_shell pulls in BOTH `adb` and `phone_screen` (both
        # in the `android` group), not just `adb` alone.
        if tool_name.startswith("sassy_"):
            parts = tool_name[len("sassy_"):].split("_")
            for module_name in parts:
                module_group = _MODULE_TO_GROUP.get(module_name)
                if module_group:
                    boosted_modules.update(TOOL_GROUPS[module_group]["modules"])
                    break

    return sorted(boosted_modules)


def get_pruned_tools() -> set[str]:
    """Return the set of tool names that should be HIDDEN at registration time
    because their score is below the prune threshold AND they're not in an
    always_load group.

    A tool in an always_load group stays — pruning is opt-in for the
    on-demand groups so users keep their core toolkit visible regardless
    of usage history.
    """
    try:
        tracker = get_tracker()
    except Exception:
        return set()

    pruned: set[str] = set()
    # Only inspect tools we have data for; never-used tools get to stay
    # because they may simply be new (not unwanted).
    for tool_name, score in tracker.top_tools(500):
        if score >= USAGE_PRUNE_THRESHOLD:
            continue
        group = _TOOL_TO_GROUP.get(tool_name)
        if not group:
            continue
        if TOOL_GROUPS[group].get("always_load"):
            # Don't prune from always-loaded groups; users expect their
            # toolkit to stay coherent there.
            continue
        pruned.add(tool_name)
    return pruned


def get_default_modules() -> list[str]:
    """Return modules that should load by default.

    Combines two sources:
      1. Static `always_load=True` groups (the floor)
      2. Usage-score-boosted modules (whose top tools cross USAGE_BOOST_THRESHOLD)

    Without #2, the static defaults stayed frozen across releases regardless
    of actual user behaviour — making the README's "smart loading" claim a
    lie. With #2, a user who actually uses Android tools has them there
    automatically; a user who only ever uses core stays minimal.
    """
    modules: list[str] = []
    for group in TOOL_GROUPS.values():
        if group["always_load"]:
            modules.extend(group["modules"])
    boosted = get_score_boosted_modules()
    if boosted:
        new = [m for m in boosted if m not in modules]
        if new:
            logger.info(
                f"usage-boost: adding {new} based on score >= {USAGE_BOOST_THRESHOLD}"
            )
            modules.extend(new)
    return resolve_dependencies(modules)


def auto_activate_hooks_for_modules(module_names: list[str]) -> list[str]:
    """Auto-activate hooks owned by the given modules.

    Called at startup AFTER all modules have registered their hooks via
    register_hook(). Activates every hook whose `module` field matches one
    of the given modules — so a user who got `adb` boosted into their
    default load also gets the `phone_autonomous`/`phone_observe` hooks
    pre-loaded into the AI's context, without an explicit
    `sassy_hooks_activate` call.

    Returns the list of activated hook names (for logging).
    """
    try:
        from sassymcp.modules._hooks import _HOOKS, activate_hook
    except ImportError:
        return []

    target_modules = set(module_names)
    activated: list[str] = []
    for hook_name, hook_data in _HOOKS.items():
        if hook_data.get("module") in target_modules:
            if activate_hook(hook_name) is not None:
                activated.append(hook_name)
    if activated:
        logger.info(f"auto-activated hooks for boosted modules: {activated}")
    return activated


def get_all_modules() -> list[str]:
    """Return all known module names."""
    modules = []
    for group in TOOL_GROUPS.values():
        modules.extend(group["modules"])
    return resolve_dependencies(modules)


def get_group_info() -> dict:
    """Return group info for display."""
    return {
        name: {
            "description": g["description"],
            "modules": g["modules"],
            "always_load": g["always_load"],
            "max_concurrent": g.get("max_concurrent", 10),
            "calls_per_minute": g.get("calls_per_minute", 120),
            "tool_count": "varies",
        }
        for name, g in TOOL_GROUPS.items()
    }


# ── Schema Versioning ────────────────────────────────────────────────

_schema_version: Optional[str] = None


def compute_schema_version(tool_definitions: list[dict]) -> str:
    """Compute a hash of tool names + descriptions for cache invalidation."""
    global _schema_version
    payload = json.dumps(
        sorted([
            (t.get("name", ""), t.get("description", ""))
            for t in tool_definitions
        ]),
        separators=(",", ":"),
    )
    _schema_version = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return _schema_version


def get_schema_version() -> Optional[str]:
    """Return the last computed schema version hash."""
    return _schema_version


# ── Context Window Estimation ────────────────────────────────────────

def estimate_tool_context_tokens(tool_definitions: list[dict]) -> dict:
    """Estimate how many tokens the tool definitions consume.

    Each tool definition includes: name, description, parameter JSON schema.
    Rough estimate: 1 token ~ 4 chars of JSON.
    """
    total_chars = 0
    tool_sizes = []
    for tool in tool_definitions:
        tool_json = json.dumps(tool, separators=(",", ":"))
        chars = len(tool_json)
        total_chars += chars
        tool_sizes.append({
            "name": tool.get("name", "unknown"),
            "chars": chars,
            "est_tokens": chars // 4,
        })

    tool_sizes.sort(key=lambda x: x["chars"], reverse=True)

    return {
        "total_chars": total_chars,
        "est_tokens": total_chars // 4,
        "est_percent_of_200k": round((total_chars // 4) / 200000 * 100, 1),
        "tool_count": len(tool_definitions),
        "top_10_heaviest": tool_sizes[:10],
    }


# ── Response Minification ────────────────────────────────────────────

GITHUB_STRIP_KEYS = {
    "node_id", "gravatar_id", "followers_url", "following_url",
    "gists_url", "starred_url", "subscriptions_url", "organizations_url",
    "repos_url", "events_url", "received_events_url", "forks_url",
    "keys_url", "collaborators_url", "teams_url", "hooks_url",
    "issue_events_url", "assignees_url", "branches_url", "tags_url",
    "blobs_url", "git_tags_url", "git_refs_url", "trees_url",
    "statuses_url", "languages_url", "stargazers_url", "contributors_url",
    "subscribers_url", "subscription_url", "commits_url", "git_commits_url",
    "comments_url", "issue_comment_url", "contents_url", "compare_url",
    "merges_url", "archive_url", "downloads_url", "issues_url",
    "pulls_url", "milestones_url", "notifications_url", "labels_url",
    "releases_url", "deployments_url", "git_url", "ssh_url",
    "clone_url", "svn_url", "mirror_url", "review_comments_url",
    "review_comment_url", "timeline_url", "members_url",
    "public_members_url", "avatar_url", "diff_url", "patch_url",
    "_links",
}


def minify_github_response(data: Any, depth: int = 0) -> Any:
    """Strip URL-heavy metadata from GitHub API responses.

    Saves 40-70% of tokens on typical responses.
    Preserves all actionable data (ids, shas, names, states, bodies).
    """
    if depth > 15:
        return data

    if isinstance(data, dict):
        return {
            k: minify_github_response(v, depth + 1)
            for k, v in data.items()
            if k not in GITHUB_STRIP_KEYS
        }
    elif isinstance(data, list):
        return [minify_github_response(item, depth + 1) for item in data]
    else:
        return data


def minify_file_listing(data: Any) -> Any:
    """Minify directory listing responses — keep only name, path, sha, type, size."""
    if isinstance(data, list):
        return [
            {k: item[k] for k in ("name", "path", "sha", "type", "size") if k in item}
            for item in data
            if isinstance(item, dict)
        ]
    return data


# ── Pre-registration Validation ──────────────────────────────────────

def validate_tool(fn) -> bool:
    """Pre-flight check: docstring and type-hinted parameters.

    Returns True if valid, False if issues found (logs warnings).
    Never blocks registration — just warns.
    """
    valid = True
    name = getattr(fn, "__name__", "<unknown>")

    if not getattr(fn, "__doc__", None):
        logger.warning(f"Tool {name}: missing docstring")
        valid = False

    try:
        sig = inspect.signature(fn)
        for param_name, param in sig.parameters.items():
            if param_name in ("self", "return"):
                continue
            if param.annotation is inspect.Parameter.empty:
                logger.warning(f"Tool {name}: parameter '{param_name}' has no type hint")
                valid = False
    except (ValueError, TypeError):
        pass

    return valid


# ── Live Reload (dev mode only) ──────────────────────────────────────

if WATCHDOG_AVAILABLE:
    class ModuleReloader(FileSystemEventHandler):
        """Watches modules/ for changes and hot-reloads them."""

        def __init__(self, server, modules_dir: Path):
            self.server = server
            self.modules_dir = modules_dir
            self._last_reload = time.time()

        def on_modified(self, event):
            if time.time() - self._last_reload < 1.0:
                return  # debounce
            if not event.src_path.endswith(".py"):
                return
            if event.src_path.endswith("__init__.py"):
                return

            module_name = Path(event.src_path).stem
            if module_name.startswith("_"):
                return  # skip private modules

            logger.info(f"Live reload triggered for {module_name}")
            try:
                mod = importlib.import_module(f"sassymcp.modules.{module_name}")
                importlib.reload(mod)
                if hasattr(mod, "register"):
                    mod.register(self.server)
                    logger.info(f"Live reload succeeded: {module_name}")
                self._last_reload = time.time()
            except Exception as e:
                logger.error(f"Live reload failed for {module_name}: {e}")


def enable_live_reload(server, modules_dir: Path):
    """Start watchdog observer for hot module reload. Dev mode only."""
    if not WATCHDOG_AVAILABLE:
        logger.warning("watchdog not installed — live reload disabled (pip install watchdog)")
        return None
    observer = Observer()
    handler = ModuleReloader(server, modules_dir)
    observer.schedule(handler, str(modules_dir), recursive=False)
    observer.daemon = True
    observer.start()
    logger.info("Live reload ENABLED for modules/")
    return observer
