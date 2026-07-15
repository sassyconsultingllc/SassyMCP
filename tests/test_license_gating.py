# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-WZQWYCYTMYXW
"""Tier gating is GONE — license.get_allowed_groups() unlocks everything.

v1.13.0 removed the pro/forensics gate: the release model is all-or-nothing,
every tool group ships unlocked for everyone. These tests pin that contract
so gating can't silently creep back in:

  - every license state (none, free, pro, expired, tampered, corrupt)
    → ALL known groups, exactly
  - failure modes never raise, never crash a startup
  - SASSYMCP_LICENSE_BYPASS is accepted-and-ignored (already everything)

The supporter-key machinery (generate / parse / validate) still works and
still reports tier labels honestly — that's pinned here too, because the
banner, control panel, and cockpit display it.
"""
from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path

import pytest


def _all_known_groups() -> set[str]:
    from sassymcp.modules._tool_loader import TOOL_GROUPS
    return set(TOOL_GROUPS.keys())


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


# ── Everything unlocked, in every license state ────────────────────────

def test_no_license_file_unlocks_all_groups(tmp_path, monkeypatch):
    lic = _fresh_license_module(monkeypatch, tmp_path / "h")
    allowed = lic.get_allowed_groups()

    assert allowed == _all_known_groups()
    # Spot-check the groups the old gate used to lock:
    for grp in ("android", "system", "github_full", "linux", "v020", "forensics"):
        assert grp in allowed, f"{grp} must be unlocked without a license"


def test_valid_pro_license_changes_nothing(tmp_path, monkeypatch):
    lic = _fresh_license_module(monkeypatch, tmp_path / "h")
    _write_license_file(lic, {"tier": "pro", "expires": time.time() + 86400 * 365})

    assert lic.get_allowed_groups() == _all_known_groups()


def test_forensics_addon_changes_nothing(tmp_path, monkeypatch):
    lic = _fresh_license_module(monkeypatch, tmp_path / "h")
    _write_license_file(lic, {
        "tier": "free",
        "addons": ["forensics"],
        "expires": time.time() + 86400 * 365,
    })

    assert lic.get_allowed_groups() == _all_known_groups()


# ── Failure modes (must never crash, must never lock anything) ─────────

def test_expired_license_still_unlocks_everything(tmp_path, monkeypatch):
    lic = _fresh_license_module(monkeypatch, tmp_path / "h")
    key = lic.generate_license_key(
        email="x@y", tier="pro", days_valid=1, addons=None,
        _override_created=time.time() - 86400 * 365,
    )
    lic.LICENSE_FILE.write_text(json.dumps({"key": key["key"]}))

    assert lic.get_allowed_groups() == _all_known_groups()


def test_tampered_signature_still_unlocks_everything(tmp_path, monkeypatch):
    lic = _fresh_license_module(monkeypatch, tmp_path / "h")
    _write_license_file(lic, {"tier": "pro", "expires": time.time() + 86400})
    raw = json.loads(lic.LICENSE_FILE.read_text())
    # Flip one character in the b64-encoded key payload
    bad = raw["key"][:-3] + ("A" if raw["key"][-3] != "A" else "B") + raw["key"][-2:]
    lic.LICENSE_FILE.write_text(json.dumps({"key": bad}))

    assert lic.get_allowed_groups() == _all_known_groups()


def test_corrupt_license_file_still_unlocks_everything(tmp_path, monkeypatch):
    lic = _fresh_license_module(monkeypatch, tmp_path / "h")
    lic.LICENSE_FILE.write_text("this is not json {{{")

    assert lic.get_allowed_groups() == _all_known_groups()


def test_bypass_env_var_is_harmless_noop(tmp_path, monkeypatch):
    """SASSYMCP_LICENSE_BYPASS used to be the dev escape hatch. It's kept
    accepted-and-ignored so old dev environments and CI configs don't
    break — and it must not error or change the result."""
    lic = _fresh_license_module(monkeypatch, tmp_path / "h")
    monkeypatch.setenv("SASSYMCP_LICENSE_BYPASS", "1")

    assert lic.get_allowed_groups() == _all_known_groups()


# ── No phantom groups ──────────────────────────────────────────────────

def test_returned_groups_exactly_match_known_groups(tmp_path, monkeypatch):
    """get_allowed_groups must return exactly TOOL_GROUPS' keys — never a
    phantom name the server module resolver can't load, never a subset
    (that would mean gating crept back in)."""
    lic = _fresh_license_module(monkeypatch, tmp_path / "h")
    allowed = lic.get_allowed_groups()
    known = _all_known_groups()
    assert allowed == known, (
        f"drift between allowed and known groups: "
        f"missing={known - allowed} phantom={allowed - known}"
    )


# ── Supporter-key machinery still works (label integrity) ─────────────

def test_tier_label_still_validates_and_displays(tmp_path, monkeypatch):
    """Gating is gone but the banner/control panel/cockpit still show the
    supporter tier — validate_license must keep reporting it honestly."""
    lic = _fresh_license_module(monkeypatch, tmp_path / "h")
    _write_license_file(lic, {
        "tier": "pro",
        "addons": ["forensics"],
        "expires": time.time() + 86400 * 365,
    })

    result = lic.validate_license()
    assert result["valid"] is True
    assert result["tier"] == "pro"
    assert result["addons"] == ["forensics"]


def test_expired_license_label_downgrades_to_free(tmp_path, monkeypatch):
    lic = _fresh_license_module(monkeypatch, tmp_path / "h")
    key = lic.generate_license_key(
        email="x@y", tier="pro", days_valid=1, addons=None,
        _override_created=time.time() - 86400 * 365,
    )
    lic.LICENSE_FILE.write_text(json.dumps({"key": key["key"]}))

    result = lic.validate_license()
    assert result["valid"] is False
    assert result["tier"] == "free"
    assert result["reason"] == "expired"


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
