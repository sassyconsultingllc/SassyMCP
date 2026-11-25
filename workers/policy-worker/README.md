# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-49YUF0WQU1WA

# SassyMCP Policy Worker

The remote half of SassyMCP's split architecture. The local agent executes
primitives on the user's machine; **this worker** — running on vendor
infrastructure — is the only place that knows license state, tier
entitlements, and usage. Customers never receive this source.

## What it does

| Route | Auth | Purpose |
|---|---|---|
| `GET /v1/policy/status` | none | Health check → `{ ok, policy_version }` |
| `POST /v1/policy/evaluate` | `Authorization: Bearer <LS license key>` | Policy decision for one tool call |

Evaluate request (the local agent sends **redacted summaries only** —
truncated `cmd_preview`, `path_scope` — never full arguments):

```json
{
  "tool": "sassy_shell",
  "group": "system",
  "destructive": true,
  "arg_summary": { "cmd_preview": "rm -rf /tmp/scratch/...", "path_scope": "/tmp" },
  "profile": "full",
  "license_tier": "pro"
}
```

Evaluate response:

```json
{
  "decision": "allow",
  "reason": "Group 'system' is permitted for tier 'pro'.",
  "ttl_seconds": 300,
  "policy_version": "2026-09-22",
  "usage": { "date": "2026-09-22", "total": 42, "allow": 40, "ask": 1, "deny": 1 }
}
```

Decision logic: license status != `active` → `deny` (reason names the
status: expired/disabled/inactive). Active licenses map LS `variant_id` →
tier → allowed tool groups via the `TIER_GROUPS` table. Unknown tool groups
→ `ask` (scaffold fails toward usability; flip to deny in production if the
group list becomes a security boundary). LS unreachable → `deny`
(fail closed — this worker is the trust anchor).

## Prerequisites

- Node 18+ and npm
- A Cloudflare account with Workers + KV
- `wrangler` (installed as a devDependency; `npx wrangler` works too)

## Setup

```bash
cd workers/policy-worker
npm install

# 1. Create the KV namespace and paste the id into wrangler.toml
#    (copy wrangler.toml.example → wrangler.toml; never commit the
#    working file — it is gitignored so a production id cannot leak)
#    environment's real id)
wrangler kv:namespace create POLICY_KV

# 2. Fill in the OWNER tables in src/index.ts:
#      - VARIANT_TIER  (your LemonSqueezy variant IDs → free/pro/enterprise)
#      - TIER_GROUPS   (what each tier may actually do — THIS TABLE IS THE PRODUCT)

# 3. Run the tests
npm test

# 4. Typecheck (optional but recommended before deploy)
npx tsc --noEmit

# 5. Dev loop / deploy
npm run dev      # local dev server
npm run deploy   # wrangler deploy → https://sassymcp-policy-worker.<you>.workers.dev
```

### Secrets

**None are required.** LemonSqueezy's license endpoints (`/v1/licenses/validate`)
need no API key — they are designed for client-side activation flows, exactly
as `sassymcp/_lemonsqueezy.py` already does. There is deliberately no
`wrangler secret put` step. If you later add a vendor API key for a different
LS route, add it then — not now.

## The tables you must fill in (`src/index.ts`)

1. **`VARIANT_TIER`** — `variant_id → tier`. The `1753676 → pro` entry is
   real (mirrors `DEFAULT_VARIANT_MAP` in `sassymcp/_lemonsqueezy.py`).
   Find the rest in your LemonSqueezy dashboard → Products → Variants.
   Unmapped variants default to `free` (never silently upgrade).
2. **`TIER_GROUPS`** — `tier → allowed tool groups`. `"*"` means all groups.
   The scaffold gives free the safe local basics and pro everything; adjust
   to what you actually sell.

Both tables are marked `[TODO: OWNER]` in the source. The worker deploys
and runs without them being complete — it just won't gate correctly.

## Local-agent integration sketch

The local agent (future work, not in this scaffold) calls evaluate before
executing a tool call:

```python
resp = httpx.post(
    f"{POLICY_ENDPOINT}/v1/policy/evaluate",
    headers={
        "Authorization": f"Bearer {license_key}",   # the LS key, not a SassyMCP token
        "X-SassyMCP-Instance": instance_id,          # required
        "X-SassyMCP-Version": sassymcp.__version__,  # informational
    },
    json={
        "tool": tool_name, "group": group, "destructive": bool,
        "arg_summary": redacted_summary,             # NEVER full args
        "profile": active_profile,
        "license_tier": local_tier_label,            # informational only
    },
    timeout=5,
)
decision = resp.json()["decision"]  # allow | ask | deny
```

Cache `allow` decisions locally for `ttl_seconds`; on `deny`/`ask` or
worker-unreachable, fall back to the local confirmation gates (fail safe,
not fail open, for destructive tools).

## Privacy

The worker only ever sees redacted summaries. It persists keyed hashes
(SHA-256 of the license key), never the key itself, and logs only the
12-hex-char hash prefix. If you add a remote policy brain that sees more,
update `docs/legal/privacy-policy.md` — the current draft promises
local-first.

## SECURITY — read this before deploying

This worker is the **vendor trust anchor** for the split architecture. If it
is compromised, every connected local agent's licensing can be bypassed.

- **Protect the Cloudflare account** with a hardware security key (WebAuthn)
  and an alert on new logins. There is nothing to rotate client-side — the
  license keys live with LemonSqueezy, and this worker holds no secrets —
  which means account takeover is the whole attack surface. Treat it
  accordingly.
- **Wrangler access**: anyone with `wrangler deploy` rights to this worker
  can change what every customer's license allows. Limit collaborators.
- **KV data** contains only hashes, counters, and cached LS validation
  payloads (variant id, status, expiry, buyer email from LS). No license
  keys, ever.
- **Rate limiting** (600 req/min/key, KV-backed sliding window) is abuse
  throttling, not billing — counters are approximate under concurrency.
  For money math, stream to Analytics Engine or a Durable Object.
- **Fail-closed**: LS unreachable → `deny`. The one exception is the
  unauthenticated `/v1/policy/status` endpoint, which reveals nothing but
  the policy version.
