# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-4D4TJ23GRX72
"""SassyMCP permission policy engine.

Single source of truth for "may this operation proceed?". It folds the
three safety mechanisms SassyMCP already has —

  - the destructive-command classifier (modules._security)
  - the protected-path guard (is_protected_path)
  - the runtime config store (modules.runtime_config)

— into one `evaluate()` call that the shell and file tools consult,
plus a Claude-Code-style allow/ask/deny rules layer on top.

This module is intentionally side-effect free: it only *decides*. The
caller maps the decision onto behaviour (run / stage to _DELETE_ /
return a confirm token / refuse). Wiring the call sites is a separate,
test-guarded step — importing this module changes nothing on its own.

Modes (config key `permission.mode`):
  strict   - destructive patterns are denied everywhere (today's default)
  confirm  - destructive patterns return "ask" (the confirm-token flow)
  sandbox  - relaxed gating INSIDE the sandbox roots; any path that
             resolves OUTSIDE every root is denied. This is the
             "ungated LLM, but jailed to the project folder" mode:
             the agent can do what it likes within the jail and nothing
             outside it.
  bypass   - allow everything EXCEPT protected paths (explicit, audited).

When `permission.mode` is unset (""), the mode is derived from the
legacy `interceptor.destructiveAction` key (block->strict, confirm->
confirm) so existing installs keep their exact current behaviour.

Protected paths (the SassyMCP source tree and ~/.sassymcp) are denied
in EVERY mode, including bypass — that invariant is never relaxed.
"""
from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("sassymcp.policy")

VALID_MODES = ("strict", "confirm", "sandbox", "bypass")


@dataclass(frozen=True)
class Decision:
    """Outcome of a permission check.

    action: "allow" | "ask" | "deny"
    reason: human-readable explanation (surfaced to the LLM/user)
    mode:   the effective mode the decision was made under
    rule:   the allow/ask/deny rule that produced this decision, or None
            when the decision came from the mode default. Callers use this
            to tell an explicit override from the baseline behaviour (e.g.
            sassy_shell only short-circuits its interceptor on a rule).
    """
    action: str
    reason: str
    mode: str
    rule: dict | None = None

    @property
    def allowed(self) -> bool:
        return self.action == "allow"


def _cfg(key: str, default):
    """Lazy runtime-config read; never raises (policy must not crash a tool)."""
    try:
        from sassymcp.modules.runtime_config import get
        return get(key, default)
    except Exception:
        return default


def current_mode() -> str:
    """Resolve the effective permission mode.

    Explicit `permission.mode` wins. Empty falls back to the legacy
    `interceptor.destructiveAction` so pre-engine installs are unchanged:
    block -> strict, confirm -> confirm.
    """
    mode = str(_cfg("permission.mode", "") or "").strip().lower()
    if mode in VALID_MODES:
        return mode
    legacy = str(_cfg("interceptor.destructiveAction", "block") or "block").strip().lower()
    return "confirm" if legacy == "confirm" else "strict"


def sandbox_roots() -> list[Path]:
    """Absolute, resolved sandbox roots. Empty config -> the process CWD.

    Resolved with strict=False so non-existent-but-intended roots still
    produce a stable comparison base.
    """
    raw = _cfg("permission.sandboxRoots", []) or []
    roots: list[Path] = []
    for r in raw:
        try:
            roots.append(Path(r).resolve(strict=False))
        except (OSError, ValueError):
            continue
    if not roots:
        try:
            roots.append(Path.cwd().resolve(strict=False))
        except (OSError, ValueError):
            pass
    return roots


def _resolve(path: str | Path) -> Path | None:
    try:
        return Path(path).resolve(strict=False)
    except (OSError, ValueError):
        return None


def is_within_sandbox(path: str | Path, roots: list[Path] | None = None) -> bool:
    """True iff `path` resolves to a location at or under any sandbox root.

    Uses resolve() so `..` traversal and symlinks can't escape the jail.
    """
    p = _resolve(path)
    if p is None:
        return False
    for root in (roots if roots is not None else sandbox_roots()):
        if p == root or root in p.parents:
            return True
    return False


def _is_protected(path: str | Path) -> tuple[bool, str | None]:
    try:
        from sassymcp.modules._security import is_protected_path
        return is_protected_path(path)
    except Exception:
        return False, None


def _command_is_destructive(command: str) -> tuple[bool, str]:
    """Classify a shell command. Returns (is_destructive, label).

    Combines the keyword/regex delete detector with the block-list scan
    so the policy sees the same surface the interceptor does.
    """
    if not command:
        return False, ""
    try:
        from sassymcp.modules._security import detect_delete_intent, validate_command_tiered
    except Exception:
        return False, ""
    is_del, kw = detect_delete_intent(command)
    if is_del:
        return True, kw
    ok, tier, err = validate_command_tiered(command)
    if not ok and tier != "low":
        return True, (err or "blocklist")
    return False, ""


# ── Rules layer (allow / ask / deny) ─────────────────────────────────

def _rule_matches(rule: dict, *, tool: str | None, path: str | None, command: str | None) -> bool:
    """A rule matches when every field it specifies matches. Omitted
    fields are wildcards. `tool`/`path` use glob semantics; `command`
    uses regex search (case-insensitive)."""
    t = rule.get("tool")
    if t and not (tool and fnmatch.fnmatch(tool, t)):
        return False
    pth = rule.get("path")
    if pth:
        if not path:
            return False
        # Match against both the raw and resolved forms so a rule written
        # against a logical path still catches a `..`-obfuscated argument.
        resolved = _resolve(path)
        candidates = [str(path)]
        if resolved is not None:
            candidates.append(str(resolved))
        if not any(fnmatch.fnmatch(c, pth) for c in candidates):
            return False
    cmd = rule.get("command")
    if cmd:
        if not command:
            return False
        try:
            if not re.search(cmd, command, re.IGNORECASE):
                return False
        except re.error:
            return False
    return True


def _first_matching_rule(*, tool, path, command) -> dict | None:
    rules = _cfg("permission.rules", []) or []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        action = str(rule.get("action", "")).lower()
        if action not in ("allow", "ask", "deny"):
            continue
        if _rule_matches(rule, tool=tool, path=path, command=command):
            return rule
    return None


# ── The decision ─────────────────────────────────────────────────────

def evaluate(
    *,
    tool: str | None = None,
    path: str | None = None,
    command: str | None = None,
) -> Decision:
    """Decide whether an operation may proceed.

    Order of precedence:
      1. Protected-path invariant — always deny (every mode, incl. bypass).
      2. Explicit allow/ask/deny rule — first match wins.
      3. Mode default.
    """
    mode = current_mode()

    # 1. Protected paths are sacrosanct in every mode.
    if path:
        prot, reason = _is_protected(path)
        if prot:
            return Decision("deny", f"protected path: {reason}", mode)

    # 2. Explicit rules override the mode default.
    rule = _first_matching_rule(tool=tool, path=path, command=command)
    if rule is not None:
        return Decision(
            str(rule["action"]).lower(),
            f"matched rule {rule!r}",
            mode,
            rule=rule,
        )

    # 3. Mode default.
    if mode == "bypass":
        return Decision("allow", "bypass mode", mode)

    if mode == "sandbox":
        if path is not None and not is_within_sandbox(path):
            roots = ", ".join(str(r) for r in sandbox_roots())
            return Decision(
                "deny",
                f"sandbox mode: path is outside the project jail ({roots})",
                mode,
            )
        # Inside the jail (or a no-path command): gating is relaxed.
        return Decision("allow", "sandbox mode: within project jail", mode)

    # strict / confirm both key off destructiveness of the command.
    is_dangerous, label = _command_is_destructive(command or "")
    if is_dangerous:
        if mode == "confirm":
            return Decision("ask", f"confirm required: destructive '{label}'", mode)
        return Decision("deny", f"blocked: destructive '{label}'", mode)

    return Decision("allow", "no destructive pattern matched", mode)
