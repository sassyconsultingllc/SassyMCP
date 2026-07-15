# SassyMCP Launch + Sales Runbook

> **⚠️ SUPERSEDED IN PART — 2026-07-15 (v1.13.0): tier gating removed.**
> The product is now all-or-nothing: every tool group ships unlocked for
> everyone; a license is a **supporter purchase** (seat + tier label), not a
> feature unlock. Every claim below of the form "Pro unlocks X" / "Forensics
> add-on unlocks Y" is **no longer true** and must be rewritten before any
> copy in 01–08 is published. Pricing/AOV math assumed feature gating and
> needs a fresh decision (supporter model, or re-gate only after the
> LemonSqueezy buy→own loop actually works end-to-end).

**Goal:** drive SassyMCP toward **$3,000/month**.
**Model:** one-time perpetual license. Pro **$49**, Forensics add-on **+$29**, Team site license **$199**. *(see supersession note above)*
**Author:** SaS + Claude · **Created:** 2026-06-05

---

## The honest math

One-time revenue **resets every month** — there's no recurring base to coast on. So the
target is a *run rate*, fed by an always-on discovery engine plus periodic spikes.

| Scenario | Orders/month for $3k | Per day |
|---|---|---|
| Pure Pro ($49) | 61 | ~2.0 |
| Realistic mix (AOV ≈ $62*) | ~48 | ~1.6 |

\* Blended AOV assumes 50% Pro-only ($49), 30% Pro+Forensics ($78), 15% Forensics-on-free ($29), 5% Team ($199).

**Implication:** a one-day ProductHunt spike does **not** get you to $3k/mo on its own.
The durable number comes from **directories (always-on discovery) + content/SEO (always-on) +
a launch spike to seed them**. Treat the launch as ignition, the engine as the directories.

---

## Critical path (in order)

1. **[DONE — by Claude] Fix the funnel.** Site no longer contradicts the product. See "Funnel fixes shipped" below.
2. **[YOU — LemonSqueezy setup] Wire the one-time products.** Nothing sells until this is done. See "LemonSqueezy handoff" below.
3. **[YOU — verify repo is public]** Glama, the official registry, and the awesome-list index from GitHub. If `github.com/sassyconsultingllc/SassyMCP` is private, directory discovery is dead on arrival. Make it (or a free-tier mirror) public.
4. **[Capture assets]** 1 demo GIF + 4–6 screenshots. See `08-assets-to-capture.md`. Everything below converts 2–3× better with a real demo GIF.
5. **[Seed directories]** Submit to all of them — `07-directories.md`. This is the engine; do it first, before the social spike.
6. **[Launch spike]** ProductHunt + Show HN + Reddit + X, same week. `02`–`05`.
7. **[Content engine]** Publish the dev.to/blog article, then keep publishing. `06`.
8. **[Measure + iterate]** Watch LS orders, directory referrers, and which channel converts.

---

## LemonSqueezy handoff (YOU — blocks all sales)

The worker resolves a product to an LS variant via env vars; **pricing lives in the LS dashboard.**
The site now sends one-time products (`mcp-pro`, `mcp-forensics`, `mcp-team`). Make the LS side match:

1. In LemonSqueezy, create **three one-time (single-payment) products/variants**:
   - **SassyMCP Pro** — $49, license keys enabled, **activation limit = 2**.
   - **SassyMCP Forensics** — $29, license keys enabled, **activation limit = 1** (or 2 to match Pro).
   - **SassyMCP Team** — $199, license keys enabled, **activation limit = 10**.
   (Make sure each is *single payment*, not subscription. License keys: enable in the variant's "License keys" section.)
2. Copy each **variant ID** from the LS dashboard.
3. Set the worker env vars (variant IDs are not secret — plain `[vars]` in `wrangler.toml` is fine, or dashboard):
   ```
   LS_VARIANT_MCP_PRO        = <pro variant id>
   LS_VARIANT_MCP_FORENSICS  = <forensics variant id>
   LS_VARIANT_MCP_TEAM       = <team variant id>
   ```
   Confirm `LEMONSQUEEZY_API_KEY` and `LEMONSQUEEZY_STORE_ID` are already set (they are, for the other products).
4. Deploy the worker: `npx wrangler deploy` from `V:\Projects\sassyconsultingllc-cloudflare`.
5. **End-to-end test:** open `/pricing.html` → Buy Pro → complete a real $49 checkout (refund yourself after) → confirm the license-key email arrives and `sassy_setup_license action=activate key=...` unlocks the Pro groups.
6. Confirm the `forensics` entitlement maps correctly. The product code expects an `addons: ["forensics"]` field; verify the variant→entitlement mapping in `sassymcp/_lemonsqueezy.py` (`SASSYMCP_LS_VARIANT_MAP`) lines up with the new variant IDs, so a Forensics purchase actually unlocks `security_audit` + `registry`.

**Until step 5 passes, do not drive launch traffic — you'd be sending buyers to a broken checkout.**

---

## Funnel fixes shipped (by Claude, 2026-06-05)

In `V:\Projects\sassyconsultingllc-cloudflare` — review the diff, then deploy.

| File | Change |
|---|---|
| `public/pricing.html` | Full rewrite to perpetual. Removed expired "75% off until June 1" banner, removed fake call-quota metering, removed monthly/annual toggle. Added "replaces 75+ servers" hook strip, Forensics add-on tier (was invisible), honest Team site license (replaced phantom Team/Enterprise subscription tiers), trust row, real FAQ. Free CTA now points to GitHub releases (direct download) instead of `/contact`. |
| `public/checkout/pro.html` | One-time $49, "buy once / own forever", removed subscription + metering language. Sends `{product:'mcp-pro'}` (no `billing`). |
| `public/checkout/forensics.html` | **New** — $29 one-time add-on checkout. |
| `public/checkout/team.html` | Converted from $99/mo subscription to $199 one-time site license (10 machines, forensics included, priority support). |
| `src/worker.js` | `PRODUCTS`: `mcp-pro` and `mcp-team` flipped `subscription`→`payment`; added `mcp-forensics`. |
| `public/index.html` | Schema.org offer price 29→49; nav button, hero CTA, product badge, and product card price all de-subscriptioned and de-expired ("$49 · buy once, own forever"). |

**Still TODO on the funnel (you or me):**
- Add the demo GIF + screenshots to `pricing.html` and `index.html` (placeholders noted in `08-assets-to-capture.md`).
- Add social proof once you have it (GitHub stars, a quote, "used by N developers").
- Optional: a dedicated `/sassymcp` landing page (currently `/pricing.html` doubles as it).

---

## Why each channel (and what it's worth)

| Channel | Type | Realistic value | Doc |
|---|---|---|---|
| Directories (Smithery, mcp.so, Glama, PulseMCP, official registry, awesome-list) | Always-on | The engine. Steady trickle that compounds. Do first. | `07-directories.md` |
| ProductHunt | Spike | A few hundred visits in a day; credibility badge afterward. | `02-product-hunt.md` |
| Show HN | Spike | High-variance; if it lands, biggest single traffic day you'll get. | `03-show-hn.md` |
| Reddit (r/mcp, r/ClaudeAI, r/cursor, r/LocalLLaMA, r/ChatGPTCoding) | Spike + residual | Best fit for the audience; posts keep pulling search traffic. | `04-reddit.md` |
| X/Twitter | Spike | Depends on your following; good for the launch-day drumbeat. | `05-x-thread.md` |
| dev.to / blog | Engine | SEO compounding; "I replaced 75 MCP servers with one" is evergreen. | `06-devto-article.md` |

---

## Launch week sequence

**Pre-launch (this week):** finish LS setup, verify repo public, capture assets, seed ALL directories, line up the ProductHunt listing (draft, don't publish), pre-write every post from the docs here.

**Launch day (a Tuesday–Thursday):**
1. 00:01 PT — ProductHunt goes live (schedule it).
2. Morning — Show HN post. Then sit in the thread answering for hours; HN rewards founder presence.
3. Same morning — Reddit posts (stagger by ~1–2h across subreddits, tailor each per `04`).
4. Throughout — X thread, reply to everyone.
5. Update the ProductHunt first comment + reply to every commenter.

**Week after:** publish the dev.to article, post it to the smaller subreddits and Hacker News (as a blog, not Show HN), follow up on directory listings that need claiming.

**Ongoing engine (monthly):** one new piece of content, refresh directory listings, re-post to relevant new threads ("what MCP servers do you use?" posts appear constantly — answer them honestly with SassyMCP as one option).

---

## Sales-truth guardrails (don't undo the funnel fix)

- **No metering claims.** It's a local exe. Never advertise "N calls/month."
- **No expired promos.** If you run a sale, use a real future date and remove it when it ends.
- **One model.** Perpetual everywhere. If you ever add recurring revenue, make it an *optional* support/hosted add-on, not a re-pricing of the software.
- **The free tier is the wedge.** It must be genuinely useful and one-click to install — it's what gets you into every directory and what converts to Pro in-product.
