"""Brain status snapshot for the Sassy Brain cockpit dashboard.

One JSON payload of real ~/.sassymcp state — license tier, memory count, audit
volume, persona, and tool-group availability. Used by the VS Code cockpit via
`python -m sassymcp._brain_status` (WAL-aware reads, no native deps).
"""

import json
import sys

from sassymcp._paths import AUDIT_LOG, HOME, PERSONA_FILE


def _license() -> dict:
    try:
        from sassymcp.license import validate_license
        return validate_license()
    except Exception as e:
        return {"valid": False, "tier": "free", "addons": [], "reason": str(e)}


def _memory_stats() -> dict:
    try:
        from sassymcp.modules.memory import MemoryStore
        return MemoryStore().stats()
    except Exception:
        return {"total_memories": 0, "milestones": 0, "projects": []}


def _audit_count() -> int:
    try:
        if AUDIT_LOG.exists():
            with open(AUDIT_LOG, "rb") as f:
                return sum(1 for _ in f)
    except Exception:
        pass
    return 0


def _groups() -> list[dict]:
    try:
        from sassymcp.license import get_allowed_groups
        from sassymcp.modules._tool_loader import TOOL_GROUPS
        allowed = get_allowed_groups()
        return [
            {
                "name": name,
                "module_count": len(g["modules"]),
                "always_load": bool(g["always_load"]),
                "allowed": name in allowed,
                "description": g.get("description", ""),
            }
            for name, g in TOOL_GROUPS.items()
        ]
    except Exception:
        return []


def snapshot() -> dict:
    lic = _license()
    mem = _memory_stats()
    groups = _groups()
    allowed = [g for g in groups if g["allowed"]]
    module_total = sum(g["module_count"] for g in groups)
    module_allowed = sum(g["module_count"] for g in allowed)
    try:
        persona_ok = PERSONA_FILE.exists() and PERSONA_FILE.stat().st_size > 50
    except Exception:
        persona_ok = False
    try:
        from sassymcp import __version__
        version = __version__
    except Exception:
        version = "?"
    return {
        "version": version,
        "tier": lic.get("tier", "free"),
        "addons": lic.get("addons", []),
        "license_valid": bool(lic.get("valid", False)),
        "email": lic.get("email", ""),
        "memory_count": mem.get("total_memories", 0),
        "milestones": mem.get("milestones", 0),
        "projects": mem.get("projects", []),
        "audit_count": _audit_count(),
        "persona": persona_ok,
        "groups": groups,
        "group_count": len(groups),
        "allowed_group_count": len(allowed),
        "module_total": module_total,
        "module_allowed": module_allowed,
        "home": str(HOME),
    }


if __name__ == "__main__":
    try:
        sys.stdout.write(json.dumps(snapshot()))
    except Exception as e:
        sys.stdout.write(json.dumps({"error": str(e), "tier": "free", "groups": []}))
        sys.exit(1)
