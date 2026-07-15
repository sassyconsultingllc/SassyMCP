# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-WZQWYCYTMYXW
"""Tier-based group gating enforced by license.get_allowed_groups().

These tests pin down what `get_allowed_groups()` must return under each
license state. They were written before the beta-mode bypass was removed
and assume the post-bypass semantics:

  - no license file        → free tier groups only
  - valid pro license      → free baseline + pro-only groups
  - valid forensics addon  → tier groups + forensics-only group
  - tampered signature     → silently downgrades to free (no crash)
  - expired                → silently downgrades to free
  - corrupt JSON           → silently downgrades to free
  - SASSYMCP_LICENSE_BYPASS=1 → all known groups (dev escape hatch)

Tests build license payloads with the in-process signing secret rather
than calling generate_license_key directly so they exercise the on-disk
file path (LICENSE_FILE) the way a real activation would.
"""
from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path

import pytest


def _fresh_license_module(monkeypatch, sassy_home: Path):
    """Reload license.py and _paths.py under a fresh SASSYMCP_HOME so the
    module-level HOME/LICENSE_FILE/_SIGNING_SECRET are re-resolved.

    Without this, the tests would all share the production ~/.sassymcp/
    license file and signing secret, which both pollutes results and
    risks corrupting the developer's real license during a test run.
    """
    sassy_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SASSYMCP_HOME", str(sassy_home))
    monkeypatch.delenv("SASSYMCP_LICENSE_BYPASS", raising=False)
    monkeypatch.delenv("SASSYMCP_LICENSE_SECRET", raising=False)
    for mod in ("sassymcp._paths", "sassymcp.license"):
        if mod in sys.modules:
            del sys.modules[mod]
    paths = importlib.import_module("sassymcp._paths")
    lic = importlib.import_module("sassymcp.license")
    assert paths.HOME == sassy_home, "SASSYMCP_HOME did not take effect"
    return lic


def _write_license_file(lic_module, payload: dict):
    """Write LICENSE_FILE with a key whose signature matches the freshly
    loaded module's _SIGNING_SECRET. Uses the module's own generator so
    the signature is always consistent with what validate_license expects.
    """
    tier = payload.get("tier", "free")
    email = payload.get("email", "test@example.com")
    days = max(1, int((payload.get("expires", time.time() + 86400) - time.time()) / 86400))
    addons = payload.get("addons")
    key = lic_module.generate_license_key(email=email, tier=tier, days_valid=days, addons=addons)
    lic_module.LICENSE_FILE.write_text(json.dumps({
        "key": key["key"],
        "email": email,
        "tier": tier,
        "expires": key["raw"]["expires"],
        "activated_at": time.time(),
    }))


# ── Baseline (no license) ──────────────────────────────────────────────

def test_no_license_file_returns_free_groups_only(tmp_path, monkeypatch):
    lic = _fresh_license_module(monkeypatch, tmp_path / "h")
    allowed = lic.get_allowed_groups()

    assert "core" in allowed
    assert "github_quick" in allowed
    assert "utility" in allowed
    assert "android" not in allowed, "android is pro-only"
    assert "system" not in allowed, "system is pro-only"
    assert "github_full" not in allowed, "github_full is pro-only"
    assert "forensics" not in allowed, "forensics is add-on only"


def test_free_tier_includes_always_load_infra_groups(tmp_path, monkeypatch):
    """Updater, prompts, combos are infra that ship to every tier."""
    lic = _fresh_license_module(monkeypatch, tmp_path / "h")
    allowed = lic.get_allowed_groups()

    for grp in ("updater", "prompts", "combos", "meta", "infrastructure"):
        assert grp in allowed, f"{grp} must be in free tier"


# ── Pro tier ───────────────────────────────────────────────────────────

def test_valid_pro_license_unlocks_pro_groups(tmp_path, monkeypatch):
    lic = _fresh_license_module(monkeypatch, tmp_path / "h")
    _write_license_file(lic, {"tier": "pro", "expires": time.time() + 86400 * 365})

    allowed = lic.get_allowed_groups()
    assert "android" in allowed
    assert "system" in allowed
    assert "github_full" in allowed
    assert "linux" in allowed
    assert "v020" in allowed
    assert "core" in allowed, "pro must still include free baseline"


def test_pro_license_does_not_grant_forensics(tmp_path, monkeypatch):
    """Forensics is an add-on, not bundled with pro."""
    lic = _fresh_license_module(monkeypatch, tmp_path / "h")
    _write_license_file(lic, {"tier": "pro", "expires": time.time() + 86400 * 365})

    allowed = lic.get_allowed_groups()
    assert "forensics" not in allowed


# ── Forensics add-on ───────────────────────────────────────────────────

def test_pro_plus_forensics_addon_grants_both(tmp_path, monkeypatch):
    lic = _fresh_license_module(monkeypatch, tmp_path / "h")
    _write_license_file(lic, {
        "tier": "pro",
        "addons": ["forensics"],
        "expires": time.time() + 86400 * 365,
    })

    allowed = lic.get_allowed_groups()
    assert "android" in allowed, "pro groups still there"
    assert "forensics" in allowed, "forensics add-on must unlock forensics group"


def test_free_plus_forensics_addon_grants_forensics_only(tmp_path, monkeypatch):
    lic = _fresh_license_module(monkeypatch, tmp_path / "h")
    _write_license_file(lic, {
        "tier": "free",
        "addons": ["forensics"],
        "expires": time.time() + 86400 * 365,
    })

    allowed = lic.get_allowed_groups()
    assert "forensics" in allowed
    assert "android" not in allowed, "free + forensics does not grant pro groups"


# ── Failure modes (must downgrade to free, never crash) ────────────────

def test_expired_license_downgrades_to_free(tmp_path, monkeypatch):
    lic = _fresh_license_module(monkeypatch, tmp_path / "h")
    # Build a key that expires in 1 day, then rewrite the on-disk expires
    # field to be in the past. Signature still verifies (signed payload
    # has expires-in-past too).
    key = lic.generate_license_key(
        email="x@y", tier="pro", days_valid=1, addons=None,
        _override_created=time.time() - 86400 * 365,
    )
    lic.LICENSE_FILE.write_text(json.dumps({"key": key["key"]}))

    allowed = lic.get_allowed_groups()
    assert "android" not in allowed
    assert "core" in allowed


def test_tampered_signature_downgrades_to_free(tmp_path, monkeypatch):
    lic = _fresh_license_module(monkeypatch, tmp_path / "h")
    _write_license_file(lic, {"tier": "pro", "expires": time.time() + 86400})
    raw = json.loads(lic.LICENSE_FILE.read_text())
    # Flip one character in the b64-encoded key payload
    bad = raw["key"][:-3] + ("A" if raw["key"][-3] != "A" else "B") + raw["key"][-2:]
    lic.LICENSE_FILE.write_text(json.dumps({"key": bad}))

    allowed = lic.get_allowed_groups()
    assert "android" not in allowed
    assert "core" in allowed


def test_corrupt_license_file_downgrades_to_free(tmp_path, monkeypatch):
    lic = _fresh_license_module(monkeypatch, tmp_path / "h")
    lic.LICENSE_FILE.write_text("this is not json {{{")

    allowed = lic.get_allowed_groups()
    assert "android" not in allowed
    assert "core" in allowed


# ── Dev escape hatch ───────────────────────────────────────────────────

def test_bypass_env_var_unlocks_everything(tmp_path, monkeypatch):
    lic = _fresh_license_module(monkeypatch, tmp_path / "h")
    monkeypatch.setenv("SASSYMCP_LICENSE_BYPASS", "1")

    allowed = lic.get_allowed_groups()
    # Must include groups from every tier
    assert "android" in allowed
    assert "github_full" in allowed
    assert "forensics" in allowed


# ── Intersection guarantee (no phantom groups) ────────────────────────

def test_returned_groups_are_subset_of_known_groups(tmp_path, monkeypatch):
    """get_allowed_groups must never return a group name that doesn't
    exist in TOOL_GROUPS, even if TIER_GROUPS drifts and references one.
    Without this guarantee, server.py:268 would silently skip groups and
    a buyer would pay for nothing.
    """
    lic = _fresh_license_module(monkeypatch, tmp_path / "h")
    from sassymcp.modules._tool_loader import TOOL_GROUPS
    _write_license_file(lic, {
        "tier": "pro",
        "addons": ["forensics"],
        "expires": time.time() + 86400,
    })

    allowed = lic.get_allowed_groups()
    unknown = allowed - set(TOOL_GROUPS.keys())
    assert not unknown, f"phantom groups in allowed set: {unknown}"


# ── Round-trip of addons through generate/parse ───────────────────────

def test_addons_survive_round_trip(tmp_path, monkeypatch):
    lic = _fresh_license_module(monkeypatch, tmp_path / "h")
    key = lic.generate_license_key(
        email="buyer@example.com",
        tier="pro",
        days_valid=30,
        addons=["forensics"],
    )
    parsed = lic.parse_license_key(key["key"])
    assert parsed is not None
    assert parsed["tier"] == "pro"
    assert parsed["addons"] == ["forensics"]
    # And the signature must still verify after the round trip
    result = lic.validate_license(key["key"])
    assert result["valid"] is True
    assert result["tier"] == "pro"
    assert result["addons"] == ["forensics"]
