# Changelog

All notable changes to SassyMCP. Newest first. Versions follow semver:
`MAJOR.MINOR.PATCH` — MAJOR for breaking config / API changes, MINOR
for new tier-visible features, PATCH for fixes that don't move buyer-
facing surfaces.

## [1.6.0] — 2026-05-20 — Monetization-ready

The big one: SassyMCP is now sold through LemonSqueezy with real
tier-based gating, online revocation, and a buyer-side activation flow.
Free tier still runs out of the box with no key required — just gets
fewer groups.

### Added

- **Tier enforcement** (`sassymcp/license.py`). `get_allowed_groups()`
  now consults the active license and returns only what the buyer has
  paid for. Three sets:
  - `FREE_GROUPS` — core, meta, github_quick, persona, setup,
    infrastructure, utility, selfmod, memory, updater, prompts, combos.
  - `PRO_ONLY_GROUPS` — github_full, android, v020, linux, system.
  - `ADDON_GROUPS["forensics"]` — security_audit, registry (a new
    `forensics` group carved out of the old `system` group).
  - All failure modes (missing / corrupt / tampered / expired) silently
    downgrade to free. The product never bricks.
- **`SASSYMCP_LICENSE_BYPASS=1`** dev escape hatch. Logged at WARNING.
- **LemonSqueezy integration** (`sassymcp/_lemonsqueezy.py`):
  - `activate()`, `validate()`, `deactivate()` against LS's License API.
  - Variant-id → entitlement mapping with env override
    `SASSYMCP_LS_VARIANT_MAP` for staging.
  - `quick_revocation_check()` against SassyMCP's billing Worker.
- **`activate_via_lemonsqueezy()`** + **`deactivate_via_lemonsqueezy()`**
  in `license.py` — full activation flow, mints internal HMAC payload
  from LS response so offline use keeps working, defers deactivation
  on network errors so buyers don't burn seats.
- **Fast revocation oracle** (`sassymcp-billing/` Cloudflare Worker).
  Verifies LS `X-Signature` HMAC, classifies events, stores revocations
  in KV keyed by `sha256(license_key)` so raw keys never land in KV or
  logs. Public `/lemonsqueezy/check/:hash` is edge-cached 60s. Cuts
  refund-to-revocation latency from ~7 days to seconds.
- **Startup fast-check + weekly authoritative re-validate** scheduled
  non-blocking by `server.py`. Fast oracle short-circuits the LS round-
  trip when it already has a revocation entry.
- **`sassy_setup_license validate`** MCP action for on-demand re-check.
- **License `addons` field** for stacking add-ons additively on top of
  the base tier (`{tier: pro, addons: ["forensics"]}`).
- **Server startup log** now shows actual tier label and resolved
  allowed-groups set instead of `list(TOOL_GROUPS.keys())`.
- **Billing Worker** scaffold: `wrangler.toml.example`, `.gitignore`,
  `package.json`, `src/index.js`.
- **CLI**: new `sassymcp setup` subcommand opens the interactive wizard
  alongside `generate-token`, `show-token`, `install`.
- **TTY wizard**: double-clicking `sassymcp.exe` (or running it from a
  terminal with no other flags) now opens a menu instead of starting an
  HTTP server with no UI. Menu covers quick install, license activation,
  token management, and explicit run-as-server.
- **32 new tests** covering tier gating, LS activation, weekly check,
  and the fast revocation oracle.

### Changed

- `weekly_validation_check()` now routes LS-issued keys through LS, and
  consults the billing Worker first as a fast pre-check. Network errors
  at either layer leave the local license alone.
- `sassy_setup_license` rewired for LS — `activate` calls
  `activate_via_lemonsqueezy()`, `deactivate` calls
  `deactivate_via_lemonsqueezy()`. The legacy HMAC-only `save_license`
  path remains for self-signed dev keys.
- `_tool_loader.py`: `system` group split. Forensics modules
  (`security_audit`, `registry`) now live in their own `forensics`
  group so the add-on can gate them independently.
- DXT manifest bakes `SASSYMCP_BILLING_BASE=https://billing.sassyconsultingllc.com`
  into the spawned process env so the fast revocation oracle is
  reachable from a stock install.

### Buyer-visible behavior

| License state | What loads |
|---|---|
| No key | Free baseline only (~12 groups, ~30 tools) |
| Valid pro | Free + pro groups (~17 groups, ~140 tools) |
| Forensics add-on | + `security_audit`, `registry` modules |
| Expired / tampered / corrupt | Silently downgrades to free baseline |

### Commercial model

One-time perpetual license per machine, sold through LemonSqueezy. No
recurring subscription. The buyer pays once, receives a key, activates
on the machines they own (up to the per-license seat cap configured on
the LS variant). Refunds revoke via the `order_refunded` webhook;
license deactivation by the buyer (LS dashboard self-serve) revokes via
`license_key_updated`. The billing Worker subscribes to exactly four
events: `order_created`, `order_refunded`, `license_key_created`,
`license_key_updated`.

## [1.5.0] — 2026-05 — Frozen-exe completeness

- Frozen `sassymcp.exe` via PyInstaller, lean build, generalized for
  sale (Mercury-2 security audit pass).
- Multi-client auto-config (`sassymcp install --client auto`) detects
  Claude Desktop, Claude Code, Cursor, Cline, Continue, Windsurf, Zed,
  VS Code Copilot, Grok Desktop.
- DXT bundle ships as a drag-drop install for Claude Desktop.

## [1.4.2] — Earlier — sassy_shell timeout auto-promote

- `sassy_shell` MCP-safe timeout auto-promote: long-running commands
  automatically promoted to background sessions so the MCP client
  doesn't time-out the call.

## [1.4.1] — Earlier — Marketplace readiness

- CI: opt-in node24 build target.
- Marketplace-ready packaging touches.

## Older

Pre-1.4 history lives in `git log`. SassyMCP was developed iteratively
without a written changelog until v1.4.1; this file starts the record
going forward.
