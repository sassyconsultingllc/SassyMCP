"""LemonSqueezy activation, validation, and deactivation flow.

These tests cover the integration boundary between sassymcp.license
and the LS API client in sassymcp._lemonsqueezy. The LS HTTP layer is
mocked at the `_post_form` seam so tests run without network and so we
can exercise every failure mode (network errors, HTTP 4xx, LS-side
`activated=false`, mid-life invalidation) without needing a real LS
account.

Coverage targets:
  - Happy path: LS returns activated=true → local file written with
    correct tier+addons+ls_* identifiers, internal HMAC validates.
  - Variant mapping: variant_id → tier+addons via DEFAULT_VARIANT_MAP
    and the SASSYMCP_LS_VARIANT_MAP env override.
  - LS rejection: activated=false → no file written, error surfaced.
  - Network error during activation: no file written, distinct reason.
  - Weekly revalidate: status=inactive removes local file.
  - Weekly revalidate: status=active keeps local file.
  - Weekly revalidate: network error keeps local file.
  - Deactivate happy path: LS deactivated=true → local file removed.
  - Deactivate network error: local file kept (don't burn the seat).
"""
from __future__ import annotations

import asyncio
import importlib
import json
import sys
import time
from pathlib import Path

import pytest


def _fresh_modules(monkeypatch, sassy_home: Path):
    """Reload license + _lemonsqueezy + _paths under a fresh SASSYMCP_HOME.

    Also clears the variant-map env override so each test starts with
    a known mapping (the test sets its own override per-case).
    """
    sassy_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SASSYMCP_HOME", str(sassy_home))
    monkeypatch.delenv("SASSYMCP_LICENSE_BYPASS", raising=False)
    monkeypatch.delenv("SASSYMCP_LICENSE_SECRET", raising=False)
    monkeypatch.delenv("SASSYMCP_LS_VARIANT_MAP", raising=False)
    for mod in ("sassymcp._paths", "sassymcp.license", "sassymcp._lemonsqueezy"):
        if mod in sys.modules:
            del sys.modules[mod]
    paths = importlib.import_module("sassymcp._paths")
    ls = importlib.import_module("sassymcp._lemonsqueezy")
    lic = importlib.import_module("sassymcp.license")
    assert paths.HOME == sassy_home
    return lic, ls


def _mock_ls_response(monkeypatch, ls_module, response: dict):
    """Patch _post_form to return a fixed response. Tests use this
    instead of trying to mock httpx itself because the seam is cleaner
    and avoids re-validating the form-encoding inside every test.
    """
    captured = {}

    def fake_post(path, fields):
        captured["path"] = path
        captured["fields"] = fields
        return dict(response)  # copy so test response dict isn't mutated

    monkeypatch.setattr(ls_module, "_post_form", fake_post)
    return captured


# ── Activation: happy path ────────────────────────────────────────────

def test_activate_happy_path_writes_local_file_with_correct_entitlement(tmp_path, monkeypatch):
    lic, ls = _fresh_modules(monkeypatch, tmp_path / "h")
    # Map variant 999 → pro tier
    monkeypatch.setenv("SASSYMCP_LS_VARIANT_MAP", json.dumps({
        "999": {"tier": "pro", "addons": []},
    }))

    _mock_ls_response(monkeypatch, ls, {
        "_http_status": 200,
        "activated": True,
        "license_key": {"id": 555, "status": "active", "expires_at": None},
        "instance": {"id": "inst-uuid-abc", "name": "sassymcp:user@host"},
        "meta": {
            "customer_email": "buyer@example.com",
            "variant_id": 999,
            "product_id": 100,
            "store_id": 50,
            "order_id": 12345,
        },
    })

    result = lic.activate_via_lemonsqueezy("ABCD-EFGH-IJKL-MNOP")
    assert result["valid"] is True
    assert result["tier"] == "pro"
    assert result["addons"] == []
    assert result["email"] == "buyer@example.com"
    assert result["ls_instance_id"] == "inst-uuid-abc"

    # File on disk has full LS-side identifiers
    on_disk = json.loads(lic.LICENSE_FILE.read_text())
    assert on_disk["ls_license_key"] == "ABCD-EFGH-IJKL-MNOP"
    assert on_disk["ls_instance_id"] == "inst-uuid-abc"
    assert on_disk["ls_variant_id"] == 999
    assert on_disk["ls_order_id"] == 12345

    # Internal HMAC key round-trips through validate_license unchanged
    after = lic.validate_license()
    assert after["valid"] is True
    assert after["tier"] == "pro"
    assert after["email"] == "buyer@example.com"


def test_activate_resolves_forensics_addon_from_variant(tmp_path, monkeypatch):
    lic, ls = _fresh_modules(monkeypatch, tmp_path / "h")
    # Variant 777 maps to pro+forensics
    monkeypatch.setenv("SASSYMCP_LS_VARIANT_MAP", json.dumps({
        "777": {"tier": "pro", "addons": ["forensics"]},
    }))
    _mock_ls_response(monkeypatch, ls, {
        "_http_status": 200,
        "activated": True,
        "license_key": {"id": 1, "status": "active"},
        "instance": {"id": "i-1", "name": "x"},
        "meta": {"customer_email": "x@y", "variant_id": 777},
    })

    result = lic.activate_via_lemonsqueezy("KEY")
    assert result["addons"] == ["forensics"]
    # get_allowed_groups must include the forensics group
    assert "forensics" in lic.get_allowed_groups()


def test_unmapped_variant_defaults_to_free(tmp_path, monkeypatch):
    """If LS sends a variant_id we don't know about, fail safe to free
    rather than silently upgrading the buyer to an arbitrary tier."""
    lic, ls = _fresh_modules(monkeypatch, tmp_path / "h")
    # Empty map — no variants resolve
    monkeypatch.setenv("SASSYMCP_LS_VARIANT_MAP", json.dumps({}))
    _mock_ls_response(monkeypatch, ls, {
        "_http_status": 200,
        "activated": True,
        "license_key": {"id": 1, "status": "active"},
        "instance": {"id": "i-1", "name": "x"},
        "meta": {"customer_email": "x@y", "variant_id": 999999},
    })

    result = lic.activate_via_lemonsqueezy("KEY")
    assert result["valid"] is True
    assert result["tier"] == "free"
    assert result["addons"] == []


# ── Activation: failure modes ─────────────────────────────────────────

def test_activate_ls_rejection_writes_no_file(tmp_path, monkeypatch):
    lic, ls = _fresh_modules(monkeypatch, tmp_path / "h")
    _mock_ls_response(monkeypatch, ls, {
        "_http_status": 400,
        "activated": False,
        "error": "license_key has reached the activation limit",
    })

    result = lic.activate_via_lemonsqueezy("BAD-KEY")
    assert result["valid"] is False
    assert result["reason"] == "ls_rejected"
    assert "activation limit" in result["detail"]
    assert not lic.LICENSE_FILE.exists()


def test_activate_network_error_writes_no_file(tmp_path, monkeypatch):
    lic, ls = _fresh_modules(monkeypatch, tmp_path / "h")
    _mock_ls_response(monkeypatch, ls, {
        "_network_error": "Connection refused",
    })

    result = lic.activate_via_lemonsqueezy("KEY")
    assert result["valid"] is False
    assert result["reason"] == "network_error"
    assert not lic.LICENSE_FILE.exists()


# ── Weekly revalidate ─────────────────────────────────────────────────

def _seed_ls_license(lic_module, ls_module, monkeypatch, tier="pro", addons=None):
    """Plant an LS-activated license on disk so revalidate has something
    to check. Uses a synchronous activate against a mocked response.
    """
    monkeypatch.setenv("SASSYMCP_LS_VARIANT_MAP", json.dumps({
        "1": {"tier": tier, "addons": list(addons or [])},
    }))
    _mock_ls_response(monkeypatch, ls_module, {
        "_http_status": 200,
        "activated": True,
        "license_key": {"id": 1, "status": "active"},
        "instance": {"id": "inst-seed", "name": "n"},
        "meta": {"customer_email": "e@x", "variant_id": 1},
    })
    lic_module.activate_via_lemonsqueezy("SEED-KEY")


def test_revalidate_active_keeps_local_file(tmp_path, monkeypatch):
    lic, ls = _fresh_modules(monkeypatch, tmp_path / "h")
    _seed_ls_license(lic, ls, monkeypatch)
    # Force next check to run regardless of last_online_check
    data = json.loads(lic.LICENSE_FILE.read_text())
    data["last_online_check"] = 0
    lic.LICENSE_FILE.write_text(json.dumps(data))

    _mock_ls_response(monkeypatch, ls, {
        "_http_status": 200,
        "valid": True,
        "license_key": {"status": "active"},
    })

    asyncio.run(lic.weekly_validation_check())
    assert lic.LICENSE_FILE.exists(), "Active license must not be removed"


def test_revalidate_inactive_removes_local_file(tmp_path, monkeypatch):
    lic, ls = _fresh_modules(monkeypatch, tmp_path / "h")
    _seed_ls_license(lic, ls, monkeypatch)
    data = json.loads(lic.LICENSE_FILE.read_text())
    data["last_online_check"] = 0
    lic.LICENSE_FILE.write_text(json.dumps(data))

    _mock_ls_response(monkeypatch, ls, {
        "_http_status": 200,
        "valid": True,  # request itself was valid…
        "license_key": {"status": "inactive"},  # …but key has been deactivated
    })

    asyncio.run(lic.weekly_validation_check())
    assert not lic.LICENSE_FILE.exists()


def test_revalidate_disabled_removes_local_file(tmp_path, monkeypatch):
    lic, ls = _fresh_modules(monkeypatch, tmp_path / "h")
    _seed_ls_license(lic, ls, monkeypatch)
    data = json.loads(lic.LICENSE_FILE.read_text())
    data["last_online_check"] = 0
    lic.LICENSE_FILE.write_text(json.dumps(data))

    _mock_ls_response(monkeypatch, ls, {
        "_http_status": 200,
        "valid": True,
        "license_key": {"status": "disabled"},
    })

    asyncio.run(lic.weekly_validation_check())
    assert not lic.LICENSE_FILE.exists()


def test_revalidate_network_error_keeps_local_file(tmp_path, monkeypatch):
    lic, ls = _fresh_modules(monkeypatch, tmp_path / "h")
    _seed_ls_license(lic, ls, monkeypatch)
    data = json.loads(lic.LICENSE_FILE.read_text())
    data["last_online_check"] = 0
    lic.LICENSE_FILE.write_text(json.dumps(data))

    _mock_ls_response(monkeypatch, ls, {
        "_network_error": "DNS resolution failed",
    })

    asyncio.run(lic.weekly_validation_check())
    assert lic.LICENSE_FILE.exists(), \
        "Network errors must not invalidate the local license"


def test_revalidate_skips_when_under_a_week_old(tmp_path, monkeypatch):
    lic, ls = _fresh_modules(monkeypatch, tmp_path / "h")
    _seed_ls_license(lic, ls, monkeypatch)
    # Mark a recent check; should not call LS at all
    data = json.loads(lic.LICENSE_FILE.read_text())
    data["last_online_check"] = time.time() - 60  # 1 min ago
    lic.LICENSE_FILE.write_text(json.dumps(data))

    call_count = {"n": 0}
    def panic(path, fields):
        call_count["n"] += 1
        return {"_http_status": 500, "valid": False}
    monkeypatch.setattr(ls, "_post_form", panic)

    asyncio.run(lic.weekly_validation_check())
    assert call_count["n"] == 0, "Should not call LS within the weekly cooldown"


# ── Deactivation ──────────────────────────────────────────────────────

def test_deactivate_happy_path_removes_local_file(tmp_path, monkeypatch):
    lic, ls = _fresh_modules(monkeypatch, tmp_path / "h")
    _seed_ls_license(lic, ls, monkeypatch)

    captured = _mock_ls_response(monkeypatch, ls, {
        "_http_status": 200,
        "deactivated": True,
    })

    result = lic.deactivate_via_lemonsqueezy()
    assert result["status"] == "deactivated"
    assert not lic.LICENSE_FILE.exists()
    assert captured["path"] == "/v1/licenses/deactivate"
    assert captured["fields"]["instance_id"] == "inst-seed"


def test_deactivate_network_error_keeps_local_file(tmp_path, monkeypatch):
    """If LS is unreachable, don't burn the seat. The buyer can retry."""
    lic, ls = _fresh_modules(monkeypatch, tmp_path / "h")
    _seed_ls_license(lic, ls, monkeypatch)

    _mock_ls_response(monkeypatch, ls, {
        "_network_error": "Connection timeout",
    })

    result = lic.deactivate_via_lemonsqueezy()
    assert result["status"] == "deferred"
    assert lic.LICENSE_FILE.exists()


def test_deactivate_with_no_license_is_safe(tmp_path, monkeypatch):
    lic, ls = _fresh_modules(monkeypatch, tmp_path / "h")
    result = lic.deactivate_via_lemonsqueezy()
    assert result["status"] == "no_license"


# ── Fast revocation check (Chunk B: billing Worker oracle) ────────────

def _mock_quick_check(monkeypatch, ls_module, response: dict):
    """Patch quick_revocation_check at the seam. Bypasses both httpx
    and the SASSYMCP_BILLING_BASE env var so the test is hermetic
    regardless of deployment state.
    """
    captured = {}

    def fake_check(license_key):
        captured["license_key"] = license_key
        return dict(response)

    monkeypatch.setattr(ls_module, "quick_revocation_check", fake_check)
    return captured


def test_fast_revocation_check_removes_file_on_revoked(tmp_path, monkeypatch):
    lic, ls = _fresh_modules(monkeypatch, tmp_path / "h")
    _seed_ls_license(lic, ls, monkeypatch)
    captured = _mock_quick_check(monkeypatch, ls, {
        "status": "revoked",
        "reason": "subscription_cancelled",
        "revoked_at": 1700000000000,
        "license_key_id": 555,
    })

    removed = asyncio.run(lic.fast_revocation_check())
    assert removed is True
    assert not lic.LICENSE_FILE.exists()
    assert captured["license_key"] == "SEED-KEY"


def test_fast_revocation_check_keeps_file_on_active(tmp_path, monkeypatch):
    lic, ls = _fresh_modules(monkeypatch, tmp_path / "h")
    _seed_ls_license(lic, ls, monkeypatch)
    _mock_quick_check(monkeypatch, ls, {"status": "active"})

    removed = asyncio.run(lic.fast_revocation_check())
    assert removed is False
    assert lic.LICENSE_FILE.exists()


def test_fast_revocation_check_keeps_file_on_unknown(tmp_path, monkeypatch):
    """`unknown` is non-decisive — don't act on it. The weekly LS
    validate is the backstop."""
    lic, ls = _fresh_modules(monkeypatch, tmp_path / "h")
    _seed_ls_license(lic, ls, monkeypatch)
    _mock_quick_check(monkeypatch, ls, {
        "status": "unknown",
        "reason": "network_error:DNS resolution failed",
    })

    removed = asyncio.run(lic.fast_revocation_check())
    assert removed is False
    assert lic.LICENSE_FILE.exists()


def test_fast_revocation_check_skips_legacy_keys(tmp_path, monkeypatch):
    """Self-signed keys without ls_license_key have no oracle entry —
    skip the check entirely rather than misinterpreting the absence."""
    lic, ls = _fresh_modules(monkeypatch, tmp_path / "h")
    # Plant a legacy-format license (no ls_* fields)
    key = lic.generate_license_key(email="dev@x", tier="pro", days_valid=30)
    lic.save_license(key["key"])

    called = {"n": 0}
    def panic(license_key):
        called["n"] += 1
        return {"status": "revoked"}
    monkeypatch.setattr(ls, "quick_revocation_check", panic)

    removed = asyncio.run(lic.fast_revocation_check())
    assert removed is False
    assert called["n"] == 0
    assert lic.LICENSE_FILE.exists()


def test_fast_revocation_check_no_file_is_safe(tmp_path, monkeypatch):
    lic, ls = _fresh_modules(monkeypatch, tmp_path / "h")
    # No license file at all
    removed = asyncio.run(lic.fast_revocation_check())
    assert removed is False


def test_weekly_check_short_circuits_when_oracle_reports_revoked(tmp_path, monkeypatch):
    """The fast oracle should pre-empt the slow LS validate. This test
    pins that the weekly path doesn't call LS when the oracle has
    already removed the file."""
    lic, ls = _fresh_modules(monkeypatch, tmp_path / "h")
    _seed_ls_license(lic, ls, monkeypatch)
    data = json.loads(lic.LICENSE_FILE.read_text())
    data["last_online_check"] = 0
    lic.LICENSE_FILE.write_text(json.dumps(data))

    _mock_quick_check(monkeypatch, ls, {"status": "revoked", "reason": "order_refunded"})
    # If the weekly path falls through, it'd call _post_form. We want
    # that to NOT happen — assert via a sentinel.
    ls_calls = {"n": 0}
    def panic_post(path, fields):
        ls_calls["n"] += 1
        return {"_http_status": 200, "valid": True, "license_key": {"status": "active"}}
    monkeypatch.setattr(ls, "_post_form", panic_post)

    asyncio.run(lic.weekly_validation_check())
    assert not lic.LICENSE_FILE.exists()
    assert ls_calls["n"] == 0, "Oracle revocation must short-circuit the LS validate"


def test_license_key_hash_is_stable(tmp_path, monkeypatch):
    """The hash function is the public contract between the local
    install and the billing Worker. Pin a known value so a future
    refactor can't break the lookup silently."""
    lic, ls = _fresh_modules(monkeypatch, tmp_path / "h")
    import hashlib
    sample = "ABCD-EFGH-IJKL-MNOP"
    expected = hashlib.sha256(sample.encode("utf-8")).hexdigest()
    assert ls._license_key_hash(sample) == expected
    assert len(ls._license_key_hash(sample)) == 64
