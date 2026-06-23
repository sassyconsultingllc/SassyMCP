"""SassyMCP License — Offline-first tier gating with HMAC-signed keys.

License keys are HMAC-SHA256 signed JSON payloads. Validated locally on startup.
Optional weekly online check handles Stripe cancellations.

Tiers:
  free      — core, meta, github_quick, persona, setup (22 tools)
  pro       — free + all productivity/automation groups (255 tools)
  forensics — security_audit, registry (additive, stacks with any tier)
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path

from sassymcp._atomic import atomic_write_json

from sassymcp._paths import LICENSE_FILE  # re-exported for back-compat

logger = logging.getLogger("sassymcp.license")
VALIDATE_URL = "https://sassyconsultingllc.com/api/license/validate"

_SECRET_FILE = LICENSE_FILE.parent / ".license_secret"


def _load_signing_secret() -> str:
    """Load or generate a persistent per-installation signing secret.

    Multi-process safe: O_CREAT|O_EXCL on first creation so that when
    multiple sassymcp.exe processes start simultaneously and all find no
    .license_secret, exactly one wins the create race; the others get
    FileExistsError and read the winner's value. Without this, every
    process would generate its own divergent secret and license validation
    would not agree across MCP clients on the same machine.
    """
    if os.environ.get("SASSYMCP_LICENSE_SECRET"):
        return os.environ["SASSYMCP_LICENSE_SECRET"]
    if _SECRET_FILE.exists():
        try:
            return _SECRET_FILE.read_text().strip()
        except Exception:
            pass
    new_secret = secrets.token_hex(32)
    try:
        _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(_SECRET_FILE), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, new_secret.encode())
        finally:
            os.close(fd)
        try:
            _SECRET_FILE.chmod(0o600)
        except OSError:
            pass
        return new_secret
    except FileExistsError:
        # Another process won the create race. Read theirs.
        try:
            return _SECRET_FILE.read_text().strip()
        except Exception as e:
            logger.warning(f"Could not read license secret after race: {e}")
            return new_secret
    except Exception as e:
        logger.warning(f"Could not persist license secret: {e}")
        return new_secret


_SIGNING_SECRET = _load_signing_secret()

# Free baseline. Every tier — and every failure mode (no license, expired,
# corrupt, tampered) — guarantees at least these. Group names must match
# TOOL_GROUPS keys in sassymcp.modules._tool_loader; the intersection in
# get_allowed_groups() will silently drop drift, but anything in this set
# that doesn't resolve is a paying-customer-facing bug.
FREE_GROUPS = {
    "core", "meta", "github_quick", "persona", "setup",
    "infrastructure", "utility", "selfmod", "memory",
    # Infra/always-load groups that aren't tier-priced:
    "updater", "prompts", "combos",
}

# Pro adds power-user automation surfaces on top of free.
PRO_ONLY_GROUPS = {
    "github_full", "android", "v020", "linux", "system",
}

# Stand-alone add-ons. Each one names a single group that unlocks when
# the buyer's license payload carries that add-on slug. Add-ons stack
# additively with the base tier.
ADDON_GROUPS = {
    "forensics": {"forensics"},
}

TIER_GROUPS = {
    "free": FREE_GROUPS,
    "pro": FREE_GROUPS | PRO_ONLY_GROUPS,
}

# Back-compat alias for the original always-allowed concept. Same set
# as FREE_GROUPS; older code importing this name still works.
ALWAYS_ALLOWED = FREE_GROUPS


def _sign_payload(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hmac.new(
        _SIGNING_SECRET.encode(), canonical.encode(), hashlib.sha256
    ).hexdigest()


def _verify_signature(payload: dict, signature: str) -> bool:
    expected = _sign_payload(payload)
    return hmac.compare_digest(expected, signature)


def generate_license_key(
    email: str,
    tier: str,
    days_valid: int = 30,
    addons: list[str] | None = None,
    _override_created: float | None = None,
) -> dict:
    """Produce a signed license key.

    addons: optional list of add-on slugs (e.g. ["forensics"]) that stack
    additively on top of the base tier. None and [] are equivalent and
    omitted from the signed payload so v1.5 keys without addons still
    round-trip identically.

    _override_created: test hook for producing keys whose `created` is
    in the past, used to build already-expired keys for the expiry test.
    Not part of the public API.
    """
    created = _override_created if _override_created is not None else time.time()
    payload = {
        "email": email,
        "tier": tier,
        "created": created,
        "expires": created + (days_valid * 86400),
    }
    if addons:
        # Sort so canonical JSON is stable regardless of caller's order.
        payload["addons"] = sorted(addons)
    signature = _sign_payload(payload)
    key_data = {
        **payload,
        "signature": signature,
    }
    key_b64 = base64.urlsafe_b64encode(json.dumps(key_data).encode()).decode()
    return {
        "key": f"sassy_{tier}_{key_b64}",
        "raw": key_data,
    }


def parse_license_key(key_string: str) -> dict | None:
    try:
        parts = key_string.split("_", 2)
        if len(parts) != 3 or parts[0] != "sassy":
            return None
        b64_data = parts[2]
        raw = json.loads(base64.urlsafe_b64decode(b64_data + "=="))
        return raw
    except Exception:
        return None


def validate_license(key_string: str = None) -> dict:
    if key_string is None:
        if not LICENSE_FILE.exists():
            return {"valid": False, "tier": "free", "addons": [], "reason": "no_license_file"}
        try:
            data = json.loads(LICENSE_FILE.read_text())
            key_string = data.get("key", "")
        except Exception:
            return {"valid": False, "tier": "free", "addons": [], "reason": "corrupt_license_file"}

    parsed = parse_license_key(key_string)
    if not parsed:
        return {"valid": False, "tier": "free", "addons": [], "reason": "invalid_key_format"}

    signature = parsed.pop("signature", "")
    email = parsed.get("email", "")
    tier = parsed.get("tier", "free")
    expires = parsed.get("expires", 0)
    addons = parsed.get("addons", []) or []

    if not _verify_signature(parsed, signature):
        return {"valid": False, "tier": "free", "addons": [], "reason": "invalid_signature"}

    if expires < time.time():
        return {"valid": False, "tier": "free", "addons": [], "email": email,
                "reason": "expired", "expired_at": expires}

    return {"valid": True, "tier": tier, "email": email, "expires": expires, "addons": addons}


def save_license(key_string: str) -> dict:
    result = validate_license(key_string)
    if not result["valid"]:
        return result

    atomic_write_json(LICENSE_FILE, {
        "key": key_string,
        "email": result.get("email", ""),
        "tier": result["tier"],
        "expires": result["expires"],
        "activated_at": time.time(),
    })

    logger.info(f"License activated: tier={result['tier']}, email={result.get('email')}")
    return result


def remove_license():
    if LICENSE_FILE.exists():
        LICENSE_FILE.unlink()
        logger.info("License removed — downgraded to free tier")


# ── LemonSqueezy activation flow ──────────────────────────────────────

def activate_via_lemonsqueezy(
    ls_license_key: str,
    instance_name: str | None = None,
    days_valid: int = 365,
) -> dict:
    """Activate an LS-issued license key against LemonSqueezy and
    materialize it as a local HMAC-signed license file.

    Flow:
      1. Call LS /v1/licenses/activate with the buyer's key.
      2. On `activated=true`, look up the LS variant_id in our
         entitlement map to determine {tier, addons}.
      3. Mint an internal HMAC payload encoding that entitlement so
         future startups validate locally without round-tripping to LS.
      4. Persist to LICENSE_FILE with the LS-side identifiers (key,
         instance_id, variant_id) so the weekly check and deactivate
         flow can call LS again.

    Returns a dict shaped like validate_license() — `valid`, `tier`,
    `addons`, `email`, `expires` — plus `ls_instance_id` on success
    and a `reason` field on failure. Never raises; network failures
    surface as `reason="network_error"`.
    """
    from sassymcp import _lemonsqueezy as ls

    resp = ls.activate(ls_license_key, instance_name)
    if not ls.is_activation_success(resp):
        if "_network_error" in resp:
            return {
                "valid": False, "tier": "free", "addons": [],
                "reason": "network_error",
                "detail": resp.get("_network_error"),
            }
        return {
            "valid": False, "tier": "free", "addons": [],
            "reason": "ls_rejected",
            "detail": resp.get("error"),
            "http_status": resp.get("_http_status"),
        }

    meta = ls.extract_meta(resp)
    entitlement = ls.variant_to_entitlement(meta.get("variant_id"))

    # A successful LS activation that resolves to the free tier almost
    # always means this variant_id isn't in the entitlement map yet (a
    # deployment gap, not a buyer error). The seat still gets registered so
    # we don't strand the purchase, but the buyer would otherwise silently
    # receive nothing — surface it loudly so it's caught immediately.
    unmapped = entitlement["tier"] == "free" and not entitlement["addons"]
    if unmapped:
        logger.error(
            f"LS activation resolved to FREE for a purchase: variant_id="
            f"{meta.get('variant_id')} is not in the entitlement map. The "
            f"buyer paid but gets no paid groups. Add this variant_id to "
            f"DEFAULT_VARIANT_MAP or SASSYMCP_LS_VARIANT_MAP."
        )

    # Mint our internal HMAC key with the resolved tier+addons. The
    # expires window here is independent of LS's own expires_at — we
    # always issue a short-lived internal key (days_valid) so that even
    # if the weekly check is somehow bypassed, the local install
    # auto-expires and forces a re-validation.
    key = generate_license_key(
        email=meta.get("email") or "",
        tier=entitlement["tier"],
        days_valid=days_valid,
        addons=entitlement["addons"],
    )

    payload = {
        "key": key["key"],
        "email": meta.get("email") or "",
        "tier": entitlement["tier"],
        "addons": entitlement["addons"],
        "expires": key["raw"]["expires"],
        "activated_at": time.time(),
        # LS-side identifiers used by validate/deactivate:
        "ls_license_key": ls_license_key,
        "ls_instance_id": meta.get("instance_id"),
        "ls_instance_name": meta.get("instance_name"),
        "ls_variant_id": meta.get("variant_id"),
        "ls_product_id": meta.get("product_id"),
        "ls_store_id": meta.get("store_id"),
        "ls_order_id": meta.get("order_id"),
    }
    atomic_write_json(LICENSE_FILE, payload)

    logger.info(
        f"License activated via LemonSqueezy: tier={entitlement['tier']} "
        f"addons={entitlement['addons']} variant_id={meta.get('variant_id')} "
        f"instance_id={meta.get('instance_id')}"
    )
    result = {
        "valid": True,
        "tier": entitlement["tier"],
        "addons": entitlement["addons"],
        "email": meta.get("email"),
        "expires": key["raw"]["expires"],
        "ls_instance_id": meta.get("instance_id"),
    }
    if unmapped:
        result["warning"] = (
            f"Purchase activated but variant_id={meta.get('variant_id')} maps to "
            f"the FREE tier — the entitlement map is missing this variant. "
            f"Contact support so your paid tier can be unlocked."
        )
    return result


def deactivate_via_lemonsqueezy() -> dict:
    """Tell LS to release this install's seat, then remove the local
    license file. Safe to call when no license is present — returns a
    no-op result rather than raising.
    """
    from sassymcp import _lemonsqueezy as ls

    if not LICENSE_FILE.exists():
        return {"status": "no_license", "tier": "free"}

    try:
        data = json.loads(LICENSE_FILE.read_text())
    except Exception:
        # Corrupt file — just delete it, the buyer can't have used it anyway.
        remove_license()
        return {"status": "removed_corrupt", "tier": "free"}

    ls_key = data.get("ls_license_key")
    ls_instance = data.get("ls_instance_id")
    ls_result: dict | None = None
    if ls_key and ls_instance:
        ls_result = ls.deactivate(ls_key, ls_instance)
        if "_network_error" in ls_result:
            # Don't remove the local file on network errors — the buyer
            # can retry. Otherwise they'd lose the seat on LS's side
            # without us being able to free it.
            return {
                "status": "deferred",
                "reason": "network_error",
                "detail": ls_result.get("_network_error"),
                "hint": "Local license intact. Try again when online.",
            }

    remove_license()
    return {
        "status": "deactivated",
        "tier": "free",
        "ls_response": ls_result,
    }


def get_allowed_groups() -> set[str]:
    """Tier-aware group allowlist used by the server module resolver.

    Any failure to parse / verify / load a license downgrades to free
    tier silently — never raises, never crashes a startup. The free
    baseline is unconditionally included so a paying customer who lets
    their key expire still has a usable product instead of a bricked one.

    SASSYMCP_LICENSE_BYPASS=1 returns every known group. Intended for
    development on the upstream codebase, CI, and air-gapped support
    cases where the buyer can't reach the validation endpoint. The
    bypass is logged at WARNING so it's visible in audit trails.
    """
    from sassymcp.modules._tool_loader import TOOL_GROUPS
    known = set(TOOL_GROUPS.keys())

    if os.environ.get("SASSYMCP_LICENSE_BYPASS", "").strip() in ("1", "true", "yes"):
        logger.warning(f"License: BYPASS active — all {len(known)} groups allowed")
        return known

    result = validate_license()
    tier = result.get("tier", "free")
    addons = result.get("addons") or []

    allowed = set(TIER_GROUPS.get(tier, FREE_GROUPS))
    for addon in addons:
        allowed.update(ADDON_GROUPS.get(addon, set()))

    # Hard guarantee: free baseline always present even if a future
    # TIER_GROUPS entry forgets to include it.
    allowed.update(FREE_GROUPS)

    # Drop any group name that doesn't actually exist in TOOL_GROUPS so
    # the server module resolver never tries to load a phantom group.
    resolved = allowed & known

    if result.get("valid"):
        logger.info(
            f"License: tier={tier} addons={addons or 'none'} — "
            f"{len(resolved)} groups allowed"
        )
    else:
        logger.info(
            f"License: free tier ({result.get('reason', 'unlicensed')}) — "
            f"{len(resolved)} groups allowed"
        )
    return resolved


async def fast_revocation_check() -> bool:
    """Hit SassyMCP's billing Worker for a quick revocation signal.

    Returns True if the local file was just removed because the
    Worker reported a revocation. False otherwise (active, unknown,
    no license, legacy key, or fast oracle disabled). Safe to call
    at startup and at higher frequency than the weekly check — the
    Worker edge-caches responses for 60s.

    The Worker only knows about revocations it has received via LS
    webhooks. A 'status=unknown' result is non-decisive — the weekly
    full LS validate remains the backstop.
    """
    if not LICENSE_FILE.exists():
        return False
    try:
        data = json.loads(LICENSE_FILE.read_text())
    except Exception:
        return False
    ls_key = data.get("ls_license_key")
    if not ls_key:
        return False  # legacy / self-signed keys never revoke via the oracle

    import asyncio
    from sassymcp import _lemonsqueezy as ls

    result = await asyncio.to_thread(ls.quick_revocation_check, ls_key)
    status = result.get("status")
    if status == "revoked":
        logger.warning(
            f"Fast revocation check: license revoked "
            f"(reason={result.get('reason')}, "
            f"revoked_at={result.get('revoked_at')}) — removing local file"
        )
        remove_license()
        return True
    if status == "active":
        logger.debug("Fast revocation check: active")
    else:
        logger.debug(f"Fast revocation check: unknown ({result.get('reason')})")
    return False


async def weekly_validation_check():
    """Re-check the active license against the issuing authority.

    For LS-issued licenses (those with ls_license_key + ls_instance_id
    in the local file), the check hits LS's /v1/licenses/validate. A
    decisive non-active status (inactive / expired / disabled) removes
    the local file so the next get_allowed_groups() call drops to free.

    Before hitting LS directly, we consult SassyMCP's billing Worker
    via fast_revocation_check(). If it reports revoked, we skip the
    expensive LS call entirely — the Worker's webhook-fed cache is
    just as authoritative for the revocation question.

    Network errors and HTTP failures at either layer are non-decisive
    — the local file stays intact and we just defer the check. A
    buyer who's offline or behind a flaky VPN doesn't lose their tools.

    For legacy self-issued keys (no ls_* fields), falls back to the
    original GET against VALIDATE_URL for back-compat. Primarily for
    SaS's self-signed dev key — production keys will all come through LS.
    """
    if not LICENSE_FILE.exists():
        return

    try:
        data = json.loads(LICENSE_FILE.read_text())
        last_check = data.get("last_online_check", 0)
        if time.time() - last_check < 604800:
            return

        ls_key = data.get("ls_license_key")
        ls_instance = data.get("ls_instance_id")

        if ls_key and ls_instance:
            # Fast path: if the billing oracle has a revocation for this
            # key, we're done — no need to round-trip to LS.
            if await fast_revocation_check():
                return
            await _ls_revalidate(data, ls_key, ls_instance)
        else:
            await _legacy_revalidate(data)

        data["last_online_check"] = time.time()
        if LICENSE_FILE.exists():  # _ls_revalidate may have removed it
            atomic_write_json(LICENSE_FILE, data)

    except Exception as e:
        logger.debug(f"Weekly license check failed (non-fatal): {e}")


async def _ls_revalidate(data: dict, ls_key: str, ls_instance: str) -> None:
    """Run an LS validate call from inside the async weekly checker.

    The synchronous httpx call in `_lemonsqueezy.validate()` would
    block the event loop, so we offload it via asyncio.to_thread.
    """
    import asyncio
    from sassymcp import _lemonsqueezy as ls

    resp = await asyncio.to_thread(ls.validate, ls_key, ls_instance)
    is_active, status = ls.is_validation_active(resp)
    if is_active:
        logger.info(f"LS weekly check: license active (status={status})")
        return
    if status == "network_error":
        logger.info("LS weekly check: network error, deferring (local license intact)")
        return
    # Any decisive non-active response = remove local file.
    logger.warning(f"LS weekly check: license is {status} — removing local file")
    remove_license()


async def _legacy_revalidate(data: dict) -> None:
    """Back-compat path for licenses minted before LS integration.

    Hits the SassyMCP-hosted validate endpoint (still a placeholder URL
    until that worker ships). Treats `reason=revoked` as the only
    decisive non-valid signal — everything else (network, parse
    errors) is non-decisive.
    """
    import httpx
    key = data.get("key", "")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(VALIDATE_URL, params={"key": key})
    except httpx.HTTPError as e:
        logger.debug(f"Legacy weekly check network error (non-fatal): {e}")
        return
    if resp.status_code != 200:
        return
    remote = resp.json()
    if not remote.get("valid"):
        logger.warning(f"Legacy weekly check failed: {remote.get('reason')}")
        if remote.get("reason") == "revoked":
            remove_license()
