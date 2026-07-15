# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-JWS3NR6MCVRF
"""SassyMCP Hooks — Operational playbooks that teach the AI HOW to think.

Hooks are NOT tool documentation. They're expert-level instruction sets that
change the AI's approach to a domain. When activated, the AI gets pre-loaded
expertise — the right "lens" for the task.

Example: "audit my website" without a hook = check headers, done.
With the web_audit hook = security + performance + practicality + content +
technical analysis, each with specific tools and evaluation criteria.

Architecture:
- Modules register hooks via register_hook() during their register() call
- Multiple hooks per module (e.g., vision has "desktop_monitor" and "desktop_debug")
- Hooks are activated on demand via sassy_hooks_activate
- Active hooks persist in session state and can be stacked
- The AI calls sassy_hooks_list to discover what's available
"""

import json
import logging
import time

logger = logging.getLogger("sassymcp.hooks")

# Global hook registry: {hook_name: {module, name, description, triggers, instructions}}
_HOOKS: dict[str, dict] = {}

# Currently active hooks (ordered, most recent last)
_ACTIVE: list[str] = []


def register_hook(
    name: str,
    module: str,
    description: str,
    triggers: list[str],
    instructions: str,
):
    """Register an operational hook.

    name: unique hook ID (e.g., "web_audit", "phone_autonomous")
    module: which module owns this hook
    description: one-line summary for discovery
    triggers: phrases that suggest this hook should activate
              (e.g., ["audit website", "check my site", "evaluate site"])
    instructions: the full operational playbook — this is what gets loaded
                  into the AI's context when the hook activates
    """
    _HOOKS[name] = {
        "name": name,
        "module": module,
        "description": description,
        "triggers": triggers,
        "instructions": instructions.strip(),
        "registered_at": time.time(),
    }
    logger.debug(f"Hook registered: {name} (module: {module})")


def get_all_hooks() -> dict[str, dict]:
    """Return all registered hooks (without full instructions — just metadata)."""
    return {
        name: {
            "name": h["name"],
            "module": h["module"],
            "description": h["description"],
            "triggers": h["triggers"],
        }
        for name, h in _HOOKS.items()
    }


def get_hook(name: str) -> dict | None:
    """Get a specific hook with full instructions."""
    return _HOOKS.get(name)


def activate_hook(name: str) -> dict | None:
    """Activate a hook. Returns full hook data or None if not found."""
    hook = _HOOKS.get(name)
    if not hook:
        return None
    if name not in _ACTIVE:
        _ACTIVE.append(name)
    return hook


def deactivate_hook(name: str) -> bool:
    """Deactivate a hook. Returns True if it was active."""
    if name in _ACTIVE:
        _ACTIVE.remove(name)
        return True
    return False


def get_active_hooks() -> list[dict]:
    """Return currently active hooks with full instructions."""
    return [_HOOKS[name] for name in _ACTIVE if name in _HOOKS]


def clear_active_hooks():
    """Deactivate all hooks."""
    _ACTIVE.clear()


def suggest_hooks(user_text: str) -> list[dict]:
    """Suggest hooks based on user text. Returns matching hooks ranked by relevance."""
    text_lower = user_text.lower()
    matches = []
    for name, hook in _HOOKS.items():
        score = 0
        for trigger in hook["triggers"]:
            if trigger.lower() in text_lower:
                score += 1
        if score > 0:
            matches.append({
                "name": name,
                "module": hook["module"],
                "description": hook["description"],
                "score": score,
            })
    return sorted(matches, key=lambda x: x["score"], reverse=True)
