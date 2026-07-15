# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-66AHMHCOLKTF
"""LemonSqueezy License API client.

SassyMCP sells through LemonSqueezy. LS issues the customer-facing
license keys (format: `XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX`) and is
the authoritative source for activation state, refund-driven
deactivation, and per-instance concurrency limits.

The flow is:

  1. Buyer purchases on LS → LS emails them the license key.
  2. Buyer activates via `sassy_setup_license action=activate key=...`.
  3. `activate()` here calls LS's POST /v1/licenses/activate with the
     key and an instance_name (defaults to `${user}@${hostname}`).
  4. LS responds with the activation record including the variant_id
     of the product they bought; we map that to a SassyMCP
     {tier, addons} entitlement.
  5. The caller wraps the result in our existing local HMAC payload
     so offline operation keeps working via `validate_license()`.
  6. The weekly online check (`weekly_validation_check()` in license.py)
     re-pings `validate()` here to catch refunds.

LS's license endpoints don't require a bearer API key — they're
designed for client-side activation flows. We rate-limit at the
SassyMCP edge and accept failures gracefully (network errors leave
the local file untouched so the buyer doesn't get bricked by an
internet hiccup).
"""
from __future__ import annotations

import json
import logging
import os
import socket
from typing import Any

logger = logging.getLogger("sassymcp.lemonsqueezy")

LS_BASE = os.environ.get("SASSYMCP_LS_BASE", "https://api.lemonsqueezy.com")
LS_TIMEOUT_SECONDS = 15

# SassyMCP's own billing Worker (Chunk B). Empty string disables the
# fast revocation check; the weekly LS validate stays as the backstop.
# Override for staging or self-hosting via env. The URL is just the
# origin — the worker exposes /lemonsqueezy/check/<sha256hex>.
SASSYMCP_BILLING_BASE = os.environ.get("SASSYMCP_BILLING_BASE", "")
BILLING_TIMEOUT_SECONDS = 4

# ── Variant → entitlement mapping ─────────────────────────────────────
# LS sells "variants" — each product can have multiple SKUs (monthly,
# annual, etc.) that all map to the same SassyMCP entitlement. The
# customer-visible variant_id comes back on every activate/validate
# response so we look it up here.
#
# Defaults are placeholders. SaS fills these in once the LS store is
# live, either by editing this file or by setting SASSYMCP_LS_VARIANT_MAP
# to a JSON string mapping variant_id (string or int) → {tier, addons}.
DEFAULT_VARIANT_MAP: dict[str, dict[str, Any]] = {
    # Example shape — replace with real LS variant IDs:
    # "123456": {"tier": "pro", "addons": []},
    # "234567": {"tier": "pro", "addons": ["forensics"]},
    # "345678": {"tier": "free", "addons": ["forensics"]},
}


def _load_variant_map() -> dict[str, dict[str, Any]]:
    """Resolve the variant → entitlement map.

    Precedence: env override > on-disk override > module default. The
    env override is intended for testing and CI; production deployments
    should bake the real LS variant IDs into DEFAULT_VARIANT_MAP above.
    """
    env_raw = os.environ.get("SASSYMCP_LS_VARIANT_MAP", "").strip()
    if env_raw:
        try:
            parsed = json.loads(env_raw)
            # Normalize keys to strings so callers can pass int variant_ids
            # without worrying about lookup misses.
            return {str(k): v for k, v in parsed.items()}
        except json.JSONDecodeError as e:
            logger.warning(f"SASSYMCP_LS_VARIANT_MAP is not valid JSON ({e}); falling back")

    # On-disk override at ~/.sassymcp/lemonsqueezy.json for ops who want
    # to update the mapping without redeploying. Optional — absence is
    # the common case.
    try:
        from sassymcp._paths import HOME
        path = HOME / "lemonsqueezy.json"
        if path.exists():
            data = json.loads(path.read_text())
            return {str(k): v for k, v in data.get("variants", {}).items()}
    except Exception as e:
        logger.debug(f"lemonsqueezy.json read failed (non-fatal): {e}")

    return {str(k): v for k, v in DEFAULT_VARIANT_MAP.items()}


def variant_to_entitlement(variant_id: int | str) -> dict[str, Any]:
    """Map an LS variant_id to {tier, addons}.

    Unknown variant_ids resolve to free tier with no addons. That's the
    safe default — a buyer with a key for an unmapped variant should
    not be silently upgraded just because we can't classify their
    purchase. Mapping coverage is a deployment correctness concern.
    """
    table = _load_variant_map()
    entitlement = table.get(str(variant_id))
    if entitlement is None:
        logger.warning(
            f"LemonSqueezy variant_id={variant_id} is not in the entitlement "
            f"map; defaulting to free tier. Update DEFAULT_VARIANT_MAP or "
            f"SASSYMCP_LS_VARIANT_MAP."
        )
        return {"tier": "free", "addons": []}
    return {
        "tier": entitlement.get("tier", "free"),
        "addons": list(entitlement.get("addons", [])),
    }


# ── HTTP calls ─────────────────────────────────────────────────────────

def default_instance_name() -> str:
    """Stable per-machine instance label so re-activations from the
    same host don't burn through the buyer's seat limit.

    LS treats instance_name as a free-form string; they de-dupe on
    instance_id (which they assign), so two activates with the same
    instance_name produce two distinct instances. SassyMCP avoids that
    by reusing the cached instance_id on subsequent calls.
    """
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"
    try:
        host = socket.gethostname()
    except Exception:
        host = "unknown-host"
    return f"sassymcp:{user}@{host}"


def _post_form(path: str, fields: dict[str, str]) -> dict[str, Any]:
    """POST x-www-form-urlencoded to LS and return the parsed JSON.

    LS endpoints reject application/json on the license routes — they
    require form encoding. Errors return non-2xx with a JSON body
    containing {"error": "..."}, which we surface verbatim so the
    caller can show LS's exact message to the buyer.
    """
    import httpx
    url = f"{LS_BASE.rstrip('/')}{path}"
    try:
        with httpx.Client(timeout=LS_TIMEOUT_SECONDS) as client:
            resp = client.post(
                url,
                data=fields,
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as e:
        # Network failure — never bricks the local install. Caller
        # interprets `network_error` as "leave the local license alone".
        logger.warning(f"LS request to {path} failed: {e}")
        return {"_network_error": str(e)}

    try:
        body = resp.json()
    except Exception:
        body = {"error": f"LS returned non-JSON ({resp.status_code}): {resp.text[:200]}"}
    body["_http_status"] = resp.status_code
    return body


def activate(license_key: str, instance_name: str | None = None) -> dict[str, Any]:
    """POST /v1/licenses/activate. Returns LS's response dict augmented
    with `_http_status`; on network failure includes `_network_error`.

    On success (LS returns activated=true), the response includes the
    new `instance` block with the activation-specific `id` we need to
    cache for future validate/deactivate calls.
    """
    name = instance_name or default_instance_name()
    return _post_form("/v1/licenses/activate", {
        "license_key": license_key,
        "instance_name": name,
    })


def validate(license_key: str, instance_id: str) -> dict[str, Any]:
    """POST /v1/licenses/validate. Used by the weekly online check.

    The response includes `license_key.status` which is one of
    `active`, `inactive`, `expired`, `disabled`. Anything other than
    `active` means we should treat the local install as downgraded.
    """
    return _post_form("/v1/licenses/validate", {
        "license_key": license_key,
        "instance_id": instance_id,
    })


def deactivate(license_key: str, instance_id: str) -> dict[str, Any]:
    """POST /v1/licenses/deactivate. Frees one of the buyer's seats
    on LS so they can activate elsewhere. Called by the deactivate
    action of sassy_setup_license.
    """
    return _post_form("/v1/licenses/deactivate", {
        "license_key": license_key,
        "instance_id": instance_id,
    })


# ── Response interpretation ───────────────────────────────────────────

def is_activation_success(resp: dict[str, Any]) -> bool:
    """Tight check: only true on a 2xx with activated=true. Network
    errors, HTTP failures, and LS-side rejections all return false."""
    if "_network_error" in resp:
        return False
    if not (200 <= int(resp.get("_http_status", 0)) < 300):
        return False
    return bool(resp.get("activated"))


def is_validation_active(resp: dict[str, Any]) -> tuple[bool, str]:
    """Returns (is_active, status_label).

    is_active is True only when LS reports the license as active AND
    we got a clean 2xx. Any other state — including network errors —
    returns (False, status_label) so the caller can decide whether to
    remove the local file or leave it (typically: only remove on
    explicit `inactive`/`expired`/`disabled`, never on network errors).
    """
    if "_network_error" in resp:
        return False, "network_error"
    if not (200 <= int(resp.get("_http_status", 0)) < 300):
        return False, f"http_{resp.get('_http_status')}"
    if not resp.get("valid"):
        return False, resp.get("error") or "ls_returned_invalid"
    status = (resp.get("license_key") or {}).get("status", "unknown")
    return (status == "active"), status


def _license_key_hash(license_key: str) -> str:
    """SHA-256 hex of the LS license key — the lookup key for our
    billing Worker's revocation oracle. Computed locally so we never
    transmit the raw key when polling our own revocation cache.
    """
    import hashlib
    return hashlib.sha256(license_key.encode("utf-8")).hexdigest()


def quick_revocation_check(license_key: str) -> dict[str, Any]:
    """Hit the SassyMCP billing Worker's revocation oracle.

    Returns one of:
      {"status": "active"}          — Worker has no revocation record
      {"status": "revoked", ...}    — Worker has a revocation; payload
                                       includes reason + revoked_at +
                                       license_key_id from the webhook
      {"status": "unknown", reason} — Worker disabled, unreachable, or
                                       returned a non-2xx
    `unknown` is the safe non-action signal — callers should defer to
    the authoritative LS validate when they see it.

    The Worker edge-caches responses for 60s and the URL is opaque
    (SHA-256 of the key, not the key itself), so high-frequency polling
    from many installs costs at most one origin hit per minute per key.
    """
    if not SASSYMCP_BILLING_BASE:
        return {"status": "unknown", "reason": "billing_base_unset"}
    import httpx
    hash_hex = _license_key_hash(license_key)
    url = f"{SASSYMCP_BILLING_BASE.rstrip('/')}/lemonsqueezy/check/{hash_hex}"
    try:
        with httpx.Client(timeout=BILLING_TIMEOUT_SECONDS) as client:
            resp = client.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError as e:
        return {"status": "unknown", "reason": f"network_error:{e}"}
    if resp.status_code != 200:
        return {"status": "unknown", "reason": f"http_{resp.status_code}"}
    try:
        return resp.json()
    except Exception:
        return {"status": "unknown", "reason": "non_json_response"}


def extract_meta(resp: dict[str, Any]) -> dict[str, Any]:
    """Pull the fields we care about from an activate/validate response.

    LS nests buyer info under `meta` (customer_email, product_id,
    variant_id, ...) and license metadata under `license_key`
    (status, expires_at, ...). This flattens them for storage.
    """
    meta = resp.get("meta") or {}
    lk = resp.get("license_key") or {}
    instance = resp.get("instance") or {}
    return {
        "email": meta.get("customer_email"),
        "variant_id": meta.get("variant_id"),
        "product_id": meta.get("product_id"),
        "store_id": meta.get("store_id"),
        "order_id": meta.get("order_id"),
        "license_key_id": lk.get("id"),
        "license_status": lk.get("status"),
        "expires_at": lk.get("expires_at"),
        "instance_id": instance.get("id"),
        "instance_name": instance.get("name"),
    }
