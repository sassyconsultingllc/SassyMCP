<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
   CodeMark: SCLLC1-SassyMCP-EQ35FSWTDCYT
-->
# SassyMCP License System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate SassyMCP tool groups by license tier (free/pro/forensics) with offline-first HMAC key validation, Stripe-generated keys, and a setup wizard tool for activation.

**Architecture:** `license.py` validates keys offline via HMAC-SHA256. `server.py` calls it before module resolution to determine allowed groups. `setup_wizard.py` gets a `sassy_setup_license` tool for key activation. `_tool_loader.py` gets a function that filters modules by tier. Weekly background validation check against a remote endpoint handles cancellations.

**Tech Stack:** Python 3.11+, HMAC-SHA256 (stdlib), httpx (for weekly validation), existing SassyMCP module system.

**Spec:** `docs/superpowers/specs/2026-04-05-sassymcp-product-design.md`

---

### Task 1: Create license.py — Key Validation and Tier Mapping

**Files:**
- Create: `sassymcp/license.py`

- [ ] **Step 1: Create the license module with tier definitions and key validation**

```python
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
import time
from pathlib import Path

logger = logging.getLogger("sassymcp.license")

LICENSE_FILE = Path.home() / ".sassymcp" / "license.json"
VALIDATE_URL = "https://sassyconsultingllc.com/api/license/validate"

# HMAC signing secret — same key used by the Cloudflare Worker to generate keys.
# In production, this would be injected via env var for the Worker and baked into the exe.
# For now, using env var with a fallback.
_SIGNING_SECRET = os.environ.get("SASSYMCP_LICENSE_SECRET", "sassy-mcp-v1-signing-key-change-me")

# Tier → allowed tool groups
TIER_GROUPS = {
    "free": [
        "core", "meta", "github_quick", "persona", "setup",
    ],
    "pro": [
        "core", "meta", "github_quick", "persona", "setup",
        "infrastructure", "utility", "selfmod", "memory",
        "github_full", "android", "v020", "linux", "system",
    ],
    "forensics": [
        # Additive — these get ADDED to whatever tier the user has
        "security_audit_full", "registry_full",
    ],
}

# Groups that are always allowed regardless of tier
ALWAYS_ALLOWED = {"core", "meta", "github_quick", "persona", "setup"}


def _sign_payload(payload: dict) -> str:
    """Generate HMAC-SHA256 signature for a license payload."""
    # Sort keys for deterministic signing
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hmac.new(
        _SIGNING_SECRET.encode(), canonical.encode(), hashlib.sha256
    ).hexdigest()


def _verify_signature(payload: dict, signature: str) -> bool:
    """Verify HMAC-SHA256 signature. Timing-safe comparison."""
    expected = _sign_payload(payload)
    return hmac.compare_digest(expected, signature)


def generate_license_key(email: str, tier: str, days_valid: int = 30) -> dict:
    """Generate a signed license key. Used by the Cloudflare Worker (and for testing)."""
    payload = {
        "email": email,
        "tier": tier,
        "created": time.time(),
        "expires": time.time() + (days_valid * 86400),
    }
    signature = _sign_payload(payload)
    key_data = {
        **payload,
        "signature": signature,
    }
    # Encode as base64 for easy copy-paste
    key_b64 = base64.urlsafe_b64encode(json.dumps(key_data).encode()).decode()
    return {
        "key": f"sassy_{tier}_{key_b64}",
        "raw": key_data,
    }


def parse_license_key(key_string: str) -> dict | None:
    """Parse a license key string back into its components."""
    try:
        # Strip prefix: sassy_pro_<base64> or sassy_free_<base64>
        parts = key_string.split("_", 2)
        if len(parts) != 3 or parts[0] != "sassy":
            return None
        b64_data = parts[2]
        raw = json.loads(base64.urlsafe_b64decode(b64_data + "=="))
        return raw
    except Exception:
        return None


def validate_license(key_string: str = None) -> dict:
    """Validate a license key. Returns tier info.

    If key_string is None, reads from LICENSE_FILE.
    Returns: {"valid": bool, "tier": str, "email": str, "expires": float, "reason": str}
    """
    # Load from file if no key provided
    if key_string is None:
        if not LICENSE_FILE.exists():
            return {"valid": False, "tier": "free", "reason": "no_license_file"}
        try:
            data = json.loads(LICENSE_FILE.read_text())
            key_string = data.get("key", "")
        except Exception:
            return {"valid": False, "tier": "free", "reason": "corrupt_license_file"}

    # Parse the key
    parsed = parse_license_key(key_string)
    if not parsed:
        return {"valid": False, "tier": "free", "reason": "invalid_key_format"}

    # Extract fields
    signature = parsed.pop("signature", "")
    email = parsed.get("email", "")
    tier = parsed.get("tier", "free")
    expires = parsed.get("expires", 0)

    # Verify signature
    if not _verify_signature(parsed, signature):
        return {"valid": False, "tier": "free", "reason": "invalid_signature"}

    # Check expiry
    if expires < time.time():
        return {"valid": False, "tier": "free", "email": email, "reason": "expired",
                "expired_at": expires}

    return {"valid": True, "tier": tier, "email": email, "expires": expires}


def save_license(key_string: str) -> dict:
    """Save a license key to disk after validation."""
    result = validate_license(key_string)
    if not result["valid"]:
        return result

    LICENSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    LICENSE_FILE.write_text(json.dumps({
        "key": key_string,
        "email": result.get("email", ""),
        "tier": result["tier"],
        "expires": result["expires"],
        "activated_at": time.time(),
    }, indent=2))

    logger.info(f"License activated: tier={result['tier']}, email={result.get('email')}")
    return result


def remove_license():
    """Remove license file. Downgrades to free tier."""
    if LICENSE_FILE.exists():
        LICENSE_FILE.unlink()
        logger.info("License removed — downgraded to free tier")


def get_allowed_groups() -> set[str]:
    """Get the set of tool group names allowed by the current license."""
    result = validate_license()
    tier = result.get("tier", "free")

    allowed = set(TIER_GROUPS.get("free", []))

    if result.get("valid") and tier in TIER_GROUPS:
        allowed.update(TIER_GROUPS[tier])

    # Check for forensics addon
    if LICENSE_FILE.exists():
        try:
            data = json.loads(LICENSE_FILE.read_text())
            if data.get("forensics_key"):
                forensics_result = validate_license(data["forensics_key"])
                if forensics_result.get("valid"):
                    allowed.update(TIER_GROUPS.get("forensics", []))
        except Exception:
            pass

    logger.info(f"License tier: {tier} ({'valid' if result.get('valid') else 'free'}) — {len(allowed)} groups allowed")
    return allowed


async def weekly_validation_check():
    """Background check against remote endpoint. Non-blocking, best-effort."""
    if not LICENSE_FILE.exists():
        return

    try:
        data = json.loads(LICENSE_FILE.read_text())
        key = data.get("key", "")
        last_check = data.get("last_online_check", 0)

        # Only check once per week
        if time.time() - last_check < 604800:
            return

        import httpx
        resp = await httpx.AsyncClient(timeout=10).get(
            VALIDATE_URL, params={"key": key}
        )
        if resp.status_code == 200:
            remote = resp.json()
            if not remote.get("valid"):
                logger.warning(f"Remote license check failed: {remote.get('reason')}")
                # Don't immediately revoke — just log. Key expiry handles it.
                if remote.get("reason") == "revoked":
                    remove_license()
                    logger.warning("License revoked by server — downgraded to free tier")

        # Update last check timestamp
        data["last_online_check"] = time.time()
        LICENSE_FILE.write_text(json.dumps(data, indent=2))

    except Exception as e:
        # Network failure is fine — offline-first
        logger.debug(f"Weekly license check failed (non-fatal): {e}")
```

- [ ] **Step 2: Verify syntax**

Run: `.venv/Scripts/python.exe -c "import py_compile; py_compile.compile('sassymcp/license.py', doraise=True); print('PASS')"`

Expected: `PASS`

- [ ] **Step 3: Commit**

```bash
git add sassymcp/license.py
git commit -m "feat: add license.py — offline-first HMAC key validation and tier gating"
```

---

### Task 2: Wire License Check into Server Startup

**Files:**
- Modify: `sassymcp/server.py` (lines 135-162, the `_resolve_modules` function)

- [ ] **Step 1: Add license import and modify _resolve_modules**

In `server.py`, add after the existing imports from `_tool_loader` (line 53):

```python
from sassymcp.license import get_allowed_groups, weekly_validation_check
```

Replace the `_resolve_modules` function (lines 135-162) with:

```python
def _resolve_modules() -> list[str]:
    """Determine which modules to load based on license tier + env vars.
    Priority:
    1. License tier gates which groups are available
    2. SASSYMCP_LOAD_ALL=1 -> load all ALLOWED modules
    3. SASSYMCP_GROUPS=core,github_quick -> load specific ALLOWED groups
    4. Default: load always_load=True groups (intersected with allowed)
    """
    allowed_groups = get_allowed_groups()

    if os.environ.get("SASSYMCP_LOAD_ALL", "").strip() == "1":
        # Load all modules, but only from allowed groups
        modules = []
        for group_name, group_info in TOOL_GROUPS.items():
            if group_name in allowed_groups:
                modules.extend(group_info["modules"])
        if modules:
            logger.info(f"SASSYMCP_LOAD_ALL=1 — loading allowed modules: {modules}")
            return resolve_dependencies(modules)
        return get_default_modules()

    groups_env = os.environ.get("SASSYMCP_GROUPS", "").strip()
    if groups_env:
        requested = [g.strip() for g in groups_env.split(",") if g.strip()]
        modules = []
        for g in requested:
            if g in TOOL_GROUPS and g in allowed_groups:
                modules.extend(TOOL_GROUPS[g]["modules"])
            elif g in TOOL_GROUPS and g not in allowed_groups:
                logger.warning(f"Group '{g}' requires Pro license — skipped")
            else:
                logger.warning(f"Unknown group: {g}")
        logger.info(f"SASSYMCP_GROUPS={groups_env} — loading: {modules}")
        return resolve_dependencies(modules)

    defaults = get_default_modules()
    logger.info(f"Default load: {defaults}")
    return defaults
```

Also add the `resolve_dependencies` import at the top with the other `_tool_loader` imports:

```python
from sassymcp.modules._tool_loader import (
    get_tracker,
    get_default_modules,
    get_all_modules,
    get_group_for_tool,
    get_group_for_module,
    register_tool_group,
    validate_tool,
    enable_live_reload,
    compute_schema_version,
    resolve_dependencies,
    TOOL_GROUPS,
)
```

- [ ] **Step 2: Schedule weekly validation in _load_modules**

In `server.py`, at the end of `_load_modules()` (after the live reload block, around line 458), add:

```python
    # Schedule weekly license validation (non-blocking background task)
    try:
        import asyncio
        asyncio.get_event_loop().create_task(weekly_validation_check())
    except RuntimeError:
        pass  # No event loop yet — will run on first request
```

- [ ] **Step 3: Verify syntax**

Run: `.venv/Scripts/python.exe -c "import py_compile; py_compile.compile('sassymcp/server.py', doraise=True); print('PASS')"`

Expected: `PASS`

- [ ] **Step 4: Commit**

```bash
git add sassymcp/server.py
git commit -m "feat: wire license tier gating into module resolution"
```

---

### Task 3: Add sassy_setup_license Tool

**Files:**
- Modify: `sassymcp/modules/setup_wizard.py`

- [ ] **Step 1: Add the license tool to setup_wizard.py**

Add this tool inside the `register(server)` function, after the `sassy_setup_check_tools` tool (before the onboarding hook section):

```python
    @server.tool()
    async def sassy_setup_license(key: str = "", action: str = "status") -> str:
        """Manage your SassyMCP license. Activate a Pro key, check status, or deactivate.

        action: status | activate | deactivate
        key: license key string (required for activate action)
        """
        from sassymcp.license import validate_license, save_license, remove_license, LICENSE_FILE

        if action == "status":
            result = validate_license()
            tier = result.get("tier", "free")
            info = {
                "tier": tier,
                "valid": result.get("valid", False),
                "email": result.get("email"),
                "expires": result.get("expires"),
                "license_file": str(LICENSE_FILE),
                "license_exists": LICENSE_FILE.exists(),
            }
            if tier == "free" and not result.get("valid"):
                info["upgrade"] = {
                    "url": "https://sassyconsultingllc.com/sassymcp",
                    "price": "$29/mo",
                    "what_you_get": "255 tools, persistent memory, dynamic vision, phone control, "
                                    "GitHub full API, operational hooks, self-modification, and more.",
                }
            return json.dumps(info, indent=2)

        elif action == "activate":
            if not key:
                return json.dumps({"error": "Provide the key parameter with your license key.",
                                   "get_key": "https://sassyconsultingllc.com/sassymcp"})
            result = save_license(key)
            if result.get("valid"):
                return json.dumps({
                    "status": "activated",
                    "tier": result["tier"],
                    "email": result.get("email"),
                    "expires": result.get("expires"),
                    "note": "Restart the server to load all Pro tools, or call sassy_selfmod_restart().",
                }, indent=2)
            return json.dumps({
                "status": "failed",
                "reason": result.get("reason"),
                "hint": "Check the key and try again. Keys start with sassy_pro_ or sassy_forensics_.",
            })

        elif action == "deactivate":
            remove_license()
            return json.dumps({
                "status": "deactivated",
                "tier": "free",
                "note": "Downgraded to free tier. Restart to apply.",
            })

        return json.dumps({"error": f"Unknown action: {action}. Use: status, activate, deactivate"})
```

- [ ] **Step 2: Update the onboarding hook to include license as Step 0**

Find the `onboarding` hook's instructions string in `setup_wizard.py` and add this before "### Step 1: Persona":

```
### Step 0: License (sassy_setup_license)
1. action="status" — check current tier
2. If free: mention upgrade at sassyconsultingllc.com/sassymcp ($29/mo)
3. If they have a key: action="activate" with their key
4. Don't push — just inform what Pro unlocks and move on
```

- [ ] **Step 3: Verify syntax**

Run: `.venv/Scripts/python.exe -c "import py_compile; py_compile.compile('sassymcp/modules/setup_wizard.py', doraise=True); print('PASS')"`

Expected: `PASS`

- [ ] **Step 4: Commit**

```bash
git add sassymcp/modules/setup_wizard.py
git commit -m "feat: add sassy_setup_license tool for key activation"
```

---

### Task 4: Update PyInstaller Spec and Rebuild

**Files:**
- Modify: `sassymcp.spec`

- [ ] **Step 1: Verify license.py is in hiddenimports**

Check that `sassymcp.spec` already has `sassymcp.modules.memory` and `sassymcp.modules._hooks` (added earlier). Add `sassymcp.license` if not present:

```python
        'sassymcp.license',
```

Add it near the other top-level sassymcp imports in the hiddenimports list.

- [ ] **Step 2: Verify full import chain**

Run:
```bash
.venv/Scripts/python.exe -c "
from sassymcp.license import validate_license, get_allowed_groups, generate_license_key
result = validate_license()
print(f'License validation: {result}')
groups = get_allowed_groups()
print(f'Allowed groups: {groups}')

# Test key generation + validation roundtrip
key_data = generate_license_key('test@example.com', 'pro', 30)
print(f'Generated key prefix: {key_data[\"key\"][:30]}...')
result = validate_license(key_data['key'])
print(f'Roundtrip validation: {result}')
assert result['valid'], 'Key validation roundtrip failed!'
assert result['tier'] == 'pro', 'Wrong tier!'
print('PASS: license roundtrip')
"
```

Expected: PASS with valid key roundtrip.

- [ ] **Step 3: Rebuild exe**

Run: `.venv/Scripts/pyinstaller.exe --clean --noconfirm sassymcp.spec`

Expected: `Building EXE from EXE-00.toc completed successfully.`

- [ ] **Step 4: Verify exe runs**

Run: `timeout 5 dist/sassymcp.exe --help`

Expected: Shows usage info without errors.

- [ ] **Step 5: Commit**

```bash
git add sassymcp.spec
git commit -m "build: add license.py to PyInstaller hiddenimports"
```

---

### Task 5: Create Stripe Payment Link for Pro Tier

- [ ] **Step 1: Verify existing Stripe product**

Use `list_products` to find the existing SassyMCP product. It already has $29/mo and $99/mo prices configured.

- [ ] **Step 2: Create payment link for $29/mo price**

Use `create_payment_link` with the $29/mo price ID and quantity 1.

- [ ] **Step 3: Record the payment link URL**

Save the URL — this goes on the product page as the "Get Pro" button href.

- [ ] **Step 4: Commit the payment link to docs**

```bash
echo "Pro payment link: <URL>" >> docs/superpowers/specs/2026-04-05-sassymcp-product-design.md
git add docs/superpowers/specs/2026-04-05-sassymcp-product-design.md
git commit -m "docs: record Stripe payment link for Pro tier"
```

---

### Task 6: Push and Update Release

- [ ] **Step 1: Push all commits**

```bash
git push origin main
```

- [ ] **Step 2: Upload new exe to release**

```bash
gh release upload v1.0.0 dist/sassymcp.exe --clobber
```

- [ ] **Step 3: Update release notes**

Update the release description to mention license/tier system.
