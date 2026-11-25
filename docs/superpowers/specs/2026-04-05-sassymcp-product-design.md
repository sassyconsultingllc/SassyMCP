<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
   CodeMark: SCLLC1-SassyMCP-BHJ7NDMSCXJV
-->
# SassyMCP Product Design — Software-Leads-Consulting Model

## Problem

SassyMCP is a 255-tool MCP server with persistent memory, dynamic vision, phone control, operational hooks, and GitHub API fixes. It's fully functional but has zero paying customers. The infrastructure exists (Stripe configured, exe built, GitHub releases live) but nothing connects the product to a purchase flow.

## Solution

Two-tier product with a consulting upsell:

1. **Free tier** — self-serve download from GitHub, 22 tools, enough to get hooked
2. **Pro tier ($29/mo)** — all 255 productivity/automation tools, unlocked via license key
3. **Managed setup ($99/hr consulting)** — you install and configure Pro for business owners
4. **WinForensics (separate product)** — security/forensics tools gated by their own license

Revenue model: recurring software subscriptions that scale without your time + high-margin consulting for hands-on customers.

## Architecture

### License Key System

**File:** `sassymcp/license.py` (new, ~100 lines)

Startup flow:
- Read `~/.sassymcp/license.json`
- Validate signature offline (HMAC-SHA256 with baked-in public key)
- Map tier to allowed tool groups
- Weekly online validation against Cloudflare Worker (handles cancellations)

License key format — signed JSON payload:
```json
{
  "key": "sassy_pro_<base64_payload>",
  "email": "user@example.com",
  "tier": "pro",
  "expires": "2026-05-04T00:00:00Z",
  "signature": "<hmac_sha256>"
}
```

Validation: offline-first. Signature checked locally. No phone-home on every startup. Optional weekly check to handle Stripe cancellations — if check fails (network down), key stays valid until expiry.

### Tier Gating

```
FREE (22 tools):
  core: fileops, shell, ui_automation, editor, audit, session
  meta: context_estimate, tool_usage, tool_groups, minify_test, hooks_*
  github_quick: 6 daily-driver tools
  persona: style, decisions, practices, observability, capabilities, context
  setup: wizard, github, ssh, check_tools, status, generate_token, license

PRO — SassyMCP ($29/mo):
  Everything in Free, plus:
  infrastructure: observability, state_manager, runtime_config
  utility: env, toast, zip/tar, http, diff
  selfmod: edit, reload, restart, rollback
  memory: remember, recall, search, context, handoff, milestones
  github_full: 80 tools
  android: adb (10) + phone_screen (14)
  v020: vision (8), app_launcher (6), web_inspector (5), crosslink (7)
  linux: ssh remote exec
  system basics: process_manager, clipboard, bluetooth, eventlog, network_audit
  Hooks: all except security_scan, forensics

WINFORENSICS (separate license):
  security_audit: hash, certs, firewall, defender, APK, permissions
  registry: read, write, export, autorun forensics
  Hooks: security_scan, forensics
```

**Implementation:** `_resolve_modules()` in `server.py` checks `license.py` for allowed groups before loading. `SASSYMCP_LOAD_ALL=1` is overridden by tier — free tier can't force-load pro groups even with the env var.

### Stripe Purchase Flow

```
Customer → sassyconsultingllc.com/sassymcp → "Get Pro" button
  → Stripe Checkout (hosted) → pays $29/mo
  → Stripe fires webhook → Cloudflare Worker at /api/license/webhook
  → Worker generates HMAC-signed license key
  → Worker stores key in Cloudflare KV (key → {email, tier, stripe_sub_id, created})
  → Stripe receipt includes the key (or follow-up email via Stripe)
  → Customer pastes key in AI session
  → AI calls sassy_setup_license(key="sassy_pro_...")
  → Saves to ~/.sassymcp/license.json → tools unlock immediately
```

Cancellation flow:
```
Stripe fires customer.subscription.deleted webhook
  → Worker marks key as revoked in KV
  → Next weekly online check from exe sees revocation
  → Downgrades to free tier (tools still work, fewer of them)
  → User sees: "Pro license expired. Renew at sassyconsultingllc.com/sassymcp"
```

### Cloudflare Worker Endpoints

Added to existing sassyconsultingllc-cloudflare repo:

**`POST /api/license/webhook`**
- Receives Stripe webhook events
- Validates Stripe signature
- On `checkout.session.completed`: generate key, store in KV, attach to customer metadata
- On `customer.subscription.deleted`: mark key as revoked in KV

**`GET /api/license/validate?key=<key>`**
- Returns `{"valid": true, "tier": "pro", "expires": "..."}` or `{"valid": false, "reason": "revoked"}`
- Called weekly by the exe (best-effort, non-blocking)

**`GET /sassymcp`**
- Static product page with pricing, features, download links, consulting CTA

### Product Page Structure

Route: `sassyconsultingllc.com/sassymcp`

```
Hero: "Your AI's operating system"
Subhead: "255 tools. Persistent memory. Dynamic vision. One exe."

Feature grid (3 columns, 2 rows):
  Desktop Automation | Phone Control      | Persistent Memory
  GitHub Operations  | Dynamic Vision     | Self-Modification

Pricing (2 cards):
  FREE                          PRO ($29/mo)
  22 tools                      255 tools
  Core file/shell/UI            Full GitHub API (80 tools)
  Basic GitHub (6 tools)        Dynamic vision (desktop + phone)
  Persona system                Phone interaction + pause/resume
                                Persistent memory across sessions
                                14 operational hooks
                                Android device control
                                Self-modification + hot reload
                                SSH remote Linux
                                Web inspection + tech recon
                                Cross-session messaging

  [Download Free]               [Get Pro — $29/mo]

Consulting CTA:
  "Need it installed and configured for your business?"
  Managed Setup — $99/hr
  [Book a Call]

Footer: Veteran-owned. Madison, WI. Sassy Consulting LLC.
```

### Setup Wizard Addition

**New tool:** `sassy_setup_license(key, action="activate")`
- `action="activate"` — validates key signature, saves to license.json, triggers module reload
- `action="status"` — shows current tier, expiry, key prefix
- `action="deactivate"` — removes license.json, downgrades to free

Added to existing `setup_wizard.py`. The onboarding hook updated to include license activation as Step 0 (before persona setup).

### Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `sassymcp/license.py` | CREATE | Key validation, tier mapping, signature verification |
| `sassymcp/server.py` | MODIFY | Call license check before `_resolve_modules()` |
| `sassymcp/modules/setup_wizard.py` | MODIFY | Add `sassy_setup_license` tool |
| `sassymcp/modules/_tool_loader.py` | MODIFY | `_resolve_modules` respects tier gating |
| `sassyconsultingllc-cloudflare/` | MODIFY | Add `/sassymcp` page, `/api/license/*` endpoints |
| Cloudflare KV namespace | CREATE | `sassymcp-licenses` for key storage |
| Stripe | CREATE | Payment link for Pro ($29/mo) |

### What We Don't Build

- No user accounts or login system
- No web dashboard or SaaS platform
- No custom payment UI (Stripe hosted checkout)
- No email system (Stripe receipts handle delivery)
- No cloud-hosted SassyMCP instances
- No mobile app

### Success Criteria

1. A developer can download the free exe, use 22 tools, and upgrade to Pro by paying $29/mo
2. License key arrives via Stripe receipt, activates in one tool call, unlocks all 255 tools
3. Cancellation gracefully downgrades without breaking the user's workflow
4. Product page clearly communicates free vs pro vs consulting
5. The entire purchase-to-activation flow works without human intervention
