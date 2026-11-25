# SassyMCP Split Architecture — Remote Policy Worker

**Status:** scaffold (local gate + worker + protocol v1). **Not yet production.**
**Owner decisions required before any customer sees this:** see "Owner TODOs".

## The idea in one paragraph

SassyMCP's value is local execution — shell, files, devices on the user's
own machine. That stays local and auditable. What moves remote is the
*vendor policy*: license enforcement, revocation, per-customer tier gating,
and usage metering. The local agent asks the remote policy worker for an
allow/ask/deny decision before each tool call; the worker answers from
LemonSqueezy license state. Nobody can decompile what they never receive,
and a refunded or revoked license stops working without anyone
reinstalling.

## What lives where

| Concern | Location | Visibility |
|---|---|---|
| Tool execution (the 278 tools) | `sassymcp/` (local) | Open, auditable — deliberately boring |
| User-safety policy (destructive classifier, protected paths, allow/ask/deny rules) | `sassymcp/policy.py` (local) | Open — the floor no remote decision can override |
| Remote gate (ask worker, cache, fail-open/closed, audit) | `sassymcp/remote_policy/` (local) | Open — the enforcement mechanics are not the secret |
| License validation, tier→group mapping, metering, rate limits | `workers/policy-worker/` (Cloudflare) | Private — **this is the part competitors can't see** |
| The actual policy table (which tier may use which groups) | `TIER_GROUPS` in `workers/policy-worker/src/index.ts` | Private, owner-edited |

The secret is not the *mechanism* (anyone can read the gate code) but the
*policy data and license state* — which live only on the worker.

## Protocol v1

```
POST {endpoint}/v1/policy/evaluate
  Authorization: Bearer <customer LemonSqueezy license key>
  X-SassyMCP-Instance: <instance id>   X-SassyMCP-Version: <server version>
  X-SassyMCP-Protocol: 1
  Body: {"tool", "group", "destructive", "arg_summary", "profile", "license_tier"}
  → {"decision": "allow"|"ask"|"deny", "reason", "ttl_seconds",
      "policy_version", "usage": {...}}

GET {endpoint}/v1/policy/status          (no auth — liveness probe)
  → {"ok": true, "policy_version": "..."}
```

`arg_summary` carries **redacted previews only** (120 chars, secret names
and secret-looking values replaced with `[redacted]`). Full tool arguments
never leave the machine — this is load-bearing for the privacy story below.

## Local behavior

Config keys (`remote_policy.*` in config.json; `SASSYMCP_POLICY_ENABLED` /
`SASSYMCP_POLICY_ENDPOINT` env overrides for the two bootstrap keys):

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Master switch. Default-off: v1.16.0 behavior is unchanged unless the owner opts in. |
| `endpoint` | `""` | Policy worker URL. |
| `timeout_seconds` | `3.0` | Per-evaluate timeout. |
| `cache_ttl_seconds` | `300` | Cap on worker-advised TTL. |
| `fail_open` | `false` | Worker unreachable + no fresh cache → allow (true) or deny (false). |
| `send_arg_preview` | `true` | Send redacted previews; false sends only type names. |

Gate activation requires all three: `enabled`, `endpoint`, and a locally
activated LemonSqueezy license key. Enabled-but-misconfigured logs a warning
and stays inactive (scaffold-safe; the owner may flip this to hard-fail).

Decision flow per call: signed cache (fresh) → worker → on failure, cache,
else fail-open/closed. **Auth failures (401/403 — key rejected) always fail
closed**, even with `fail_open: true`; failing open there would defeat the
vendor half. Cache entries are HMAC-signed with the install's license
secret; tampering is treated as a miss, never an error.

`ask` currently surfaces as a refusal envelope with
`"confirm_required": true` — there is no generic confirm-token flow at the
middleware layer yet. Tools with their own `confirm="YES"` mechanism keep
working independently.

Every remote decision, cache hit, and fail-open/closed event is audit-logged
(`<tool>[remote_policy]` entries via the standard `log_tool_call`
convention), so the audit trail shows exactly what the vendor half decided.

## Client contract for ask/deny decisions

Both `deny` and `ask` raise an MCP tool error (`isError=true` on the
`tools/call` result), so clients never see them as successful results.

**Deny envelope** (JSON in the error text):

```json
{
  "error": "Denied by remote policy (<source>): <reason>",
  "tool": "<tool_name>",
  "policy_version": <int>,
  "retryable": false
}
```

**Ask envelope** (JSON in the error text):

```json
{
  "error": "Remote policy requires explicit confirmation: <reason>",
  "confirm_required": true,
  "tool": "<tool_name>",
  "policy_version": <int>,
  "retryable": false
}
```

Semantics:

- `retryable: false` -- retrying the identical call without changed
  circumstances will hit the same decision (modulo signed-cache TTL).
- `confirm_required: true` is advisory only in v1.16.0: there is no
  generic confirm-token handshake at the middleware layer, so the client
  cannot "accept" this prompt to proceed. Treat it as a refusal that
  names the missing human confirmation. Tools with their own explicit
  `confirm="YES"` parameter keep their independent confirmation path --
  pass the confirmation through the tool's own mechanism, not the
  middleware.
- Roadmap: a future protocol revision may add a confirm-token flow
  (worker-issued token echoed back with the retried call). Until then,
  policy authors who need hard stops should use `deny`, not `ask`.

## Privacy — read before shipping

This architecture **changes SassyMCP's privacy posture**, and the legal pack
in `docs/legal/` was written for a local-first product. Before any customer
build enables the gate:

1. The privacy policy must disclose: what is sent (tool names, groups,
   redacted arg previews, instance id, license tier), to where (the
   operator's Cloudflare worker), and why (license enforcement, metering).
2. Decide data retention on the worker (metering counters currently live in
   KV per-day; set a TTL and document it).
3. The `send_arg_preview: false` mode exists for customers who want
   type-only summaries — consider making it the default for the most
   privacy-sensitive tiers.
4. Jurisdiction review: the worker processes EU/UK customer metadata the
   moment it evaluates — the GDPR/UK-GDPR sections need a processor
   analysis. This needs the attorney, not just an engineer.

Do not enable the gate in a customer build until 1–4 are done.

## Deploying the worker

```bash
cd workers/policy-worker
npm install
npm test                       # 15 vitest tests, mocked LemonSqueezy + KV
npx tsc --noEmit
wrangler kv:namespace create POLICY_KV   # paste the id into wrangler.toml
wrangler deploy
```

No `wrangler secret put` is needed — LemonSqueezy's license endpoints are
designed for client-side flows and take no API key (same as
`sassymcp/_lemonsqueezy.py`).

## Threat model (scaffold)

* **Customer shares their wheel:** license key is per-customer; the worker
  validates against LemonSqueezy and meters per key. Sharing the key shares
  the seat count — same as any license.
* **Customer blocks the worker URL:** fail-closed default denies tool calls
  (fail-open is a documented, auditable choice, not silent).
* **Customer edits the cache:** HMAC signature check fails → miss → re-ask
  the worker.
* **Customer reads the local gate code:** fine — it's the policy *data*
  that's private, and it never ships.
* **Attacker compromises the Cloudflare account:** total policy bypass.
  Protect it like production: hardware 2FA, minimal collaborators, no API
  tokens in chat/email. This is the single trust anchor of the split.
* **Worker outage:** customers fail closed (default) — which is also a
  business-continuity risk. Status page + on-call before launch.

## Owner TODOs

- [ ] Fill `VARIANT_TIER` with real LemonSqueezy variant IDs; review
      `TIER_GROUPS` against what's actually sold (free-tier list is a guess).
- [ ] Decide unknown-group policy for production (scaffold: `ask`; the flip
      point is marked in `decidePolicy()`).
- [ ] KV namespace id in `wrangler.toml` (copy from
      `wrangler.toml.example`; the working file is gitignored); deploy;
      set the endpoint in customer builds.
- [ ] Privacy policy + DPA updates (see above) with attorney review.
- [ ] Decide fail-open vs fail-closed default for production tiers.
- [ ] Metering is approximate under concurrency (KV has no atomic
      increment) — fine for abuse detection, not for money math.
- [ ] Mint unique CodeMarks for the new files (local package, worker,
      tests, this doc).
- [ ] `ask` → real confirm-token flow if the product needs it.
