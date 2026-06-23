"""Tests for the permission policy engine (sassymcp.policy).

Run: pytest tests/test_policy.py   (or: python tests/test_policy.py)

These patch policy._cfg with an in-memory config dict so the real
~/.sassymcp/config.json is never touched.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sassymcp import policy  # noqa: E402


def _patch_cfg(monkeypatch, **cfg):
    defaults = {
        "permission.mode": "",
        "permission.sandboxRoots": [],
        "permission.rules": [],
        "interceptor.destructiveAction": "block",
    }
    defaults.update(cfg)
    monkeypatch.setattr(policy, "_cfg", lambda key, default: defaults.get(key, default))


# ── mode resolution ───────────────────────────────────────────────────

def test_mode_defaults_to_strict(monkeypatch):
    _patch_cfg(monkeypatch)
    assert policy.current_mode() == "strict"


def test_mode_derives_confirm_from_legacy(monkeypatch):
    _patch_cfg(monkeypatch, **{"interceptor.destructiveAction": "confirm"})
    assert policy.current_mode() == "confirm"


def test_explicit_mode_wins_over_legacy(monkeypatch):
    _patch_cfg(monkeypatch, **{"permission.mode": "sandbox",
                               "interceptor.destructiveAction": "confirm"})
    assert policy.current_mode() == "sandbox"


# ── strict / confirm ──────────────────────────────────────────────────

def test_strict_denies_destructive(monkeypatch):
    _patch_cfg(monkeypatch, **{"permission.mode": "strict"})
    d = policy.evaluate(tool="sassy_shell", command="rm -rf build")
    assert d.action == "deny"


def test_strict_allows_safe(monkeypatch):
    _patch_cfg(monkeypatch, **{"permission.mode": "strict"})
    d = policy.evaluate(tool="sassy_shell", command="Get-ChildItem")
    assert d.action == "allow"


def test_confirm_asks_on_destructive(monkeypatch):
    _patch_cfg(monkeypatch, **{"permission.mode": "confirm"})
    d = policy.evaluate(tool="sassy_shell", command="rm -rf build")
    assert d.action == "ask"


# ── sandbox jail ──────────────────────────────────────────────────────

def test_sandbox_allows_inside_root(tmp_path, monkeypatch):
    _patch_cfg(monkeypatch, **{"permission.mode": "sandbox",
                               "permission.sandboxRoots": [str(tmp_path)]})
    inside = tmp_path / "src" / "app.py"
    d = policy.evaluate(tool="sassy_write_file", path=str(inside))
    assert d.action == "allow"


def test_sandbox_denies_outside_root(tmp_path, monkeypatch):
    _patch_cfg(monkeypatch, **{"permission.mode": "sandbox",
                               "permission.sandboxRoots": [str(tmp_path)]})
    outside = tmp_path.parent / "elsewhere.txt"
    d = policy.evaluate(tool="sassy_write_file", path=str(outside))
    assert d.action == "deny"


def test_sandbox_blocks_dotdot_escape(tmp_path, monkeypatch):
    _patch_cfg(monkeypatch, **{"permission.mode": "sandbox",
                               "permission.sandboxRoots": [str(tmp_path)]})
    escape = tmp_path / ".." / "secret.txt"
    assert policy.is_within_sandbox(escape, [tmp_path.resolve()]) is False
    d = policy.evaluate(tool="sassy_write_file", path=str(escape))
    assert d.action == "deny"


def test_sandbox_relaxes_destructive_inside(tmp_path, monkeypatch):
    # A destructive command with a path INSIDE the jail is allowed in sandbox.
    _patch_cfg(monkeypatch, **{"permission.mode": "sandbox",
                               "permission.sandboxRoots": [str(tmp_path)]})
    d = policy.evaluate(tool="sassy_shell", path=str(tmp_path / "x"),
                        command="rm -rf x")
    assert d.action == "allow"


# ── bypass + the protected-path invariant ─────────────────────────────

def test_bypass_allows_destructive(monkeypatch):
    _patch_cfg(monkeypatch, **{"permission.mode": "bypass"})
    d = policy.evaluate(tool="sassy_shell", command="rm -rf build")
    assert d.action == "allow"


def test_protected_path_denied_even_in_bypass(monkeypatch):
    _patch_cfg(monkeypatch, **{"permission.mode": "bypass"})
    # The SassyMCP source tree is a protected root.
    src = Path(policy.__file__).resolve().parent  # sassymcp/
    d = policy.evaluate(tool="sassy_write_file", path=str(src / "policy.py"))
    assert d.action == "deny"
    assert "protected" in d.reason


# ── rules layer ───────────────────────────────────────────────────────

def test_deny_rule_overrides_bypass(monkeypatch):
    _patch_cfg(monkeypatch, **{
        "permission.mode": "bypass",
        "permission.rules": [{"action": "deny", "tool": "sassy_shell", "command": r"\brm\b"}],
    })
    d = policy.evaluate(tool="sassy_shell", command="rm -rf build")
    assert d.action == "deny"


def test_allow_rule_overrides_strict(monkeypatch):
    _patch_cfg(monkeypatch, **{
        "permission.mode": "strict",
        "permission.rules": [{"action": "allow", "command": r"rm -rf build"}],
    })
    d = policy.evaluate(tool="sassy_shell", command="rm -rf build")
    assert d.action == "allow"


def test_rule_path_glob(tmp_path, monkeypatch):
    _patch_cfg(monkeypatch, **{
        "permission.mode": "strict",
        "permission.rules": [{"action": "deny", "path": "*/secrets/*"}],
    })
    d = policy.evaluate(tool="sassy_write_file", path="/app/secrets/key.pem")
    assert d.action == "deny"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
