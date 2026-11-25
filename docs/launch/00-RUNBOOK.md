<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
   CodeMark: SCLLC1-Projects-KX7PBEJFI4TG
-->
# SassyMCP Launch + Sales Runbook

**Updated:** 2026-11-08, rewritten for the license change (LICENSE v2.0).
**Goal:** drive SassyMCP toward **$3,000/month**.
**Model:** **free for personal use, commercial license for work use.** Every tool ships unlocked for
everyone; a license changes the *right to use it for work*, never the features. Terms: `LICENSING.md`.
**Author:** SaS + Claude · **Created:** 2026-06-05

> **History:** the June plan sold feature-gated Pro ($49) / Forensics ($29) / Team ($199). Gating was
> removed in v1.13.0 (2026-07-15) and the product went all-free with an optional $25 supporter tip,
> which made $0. The license change on 2026-11-08 restores a revenue line *without* taking any tool
> away from individuals, so nothing is ever clawed back after the launch.

---

## Revenue model

| Line | Who | What they get | Role |
|---|---|---|---|
| Personal use | Individuals, students | Every tool, free forever | **Adoption engine.** Stars, directory rank, word of mouth. |
| Commercial license | Anyone using it for work; teams | The right to use it at work, per seat | **The revenue.** |
| Supporter license | Individuals who want to chip in | A badge (unlocks nothing; no commercial rights) | Tips. Don't plan around it. |

## The honest math (commercial price is decided in Move 1)

| Commercial price per seat | Seats/month for $3k |
|---|---|
| $49 | ~61 |
| $99 | ~31 |
| $199 | ~16 |

**Implication:** commercial seats come from *people who already use it personally and bring it to work*,
and from team leads deploying it. That means adoption first: the free install base is the funnel. A
one-day spike won't get there alone; durable numbers come from **directories (always-on discovery)
+ content (always-on) + a launch spike to seed them.**

---

## Critical path (in order)

1. **[DONE 2026-11-08] License changed.** `LICENSE` v2.0 + `LICENSING.md` + README; free personal / paid commercial.
2. **[DONE 2026-11-08] Dead links fixed and deployed.** `/sassymcp` and `/pricing.html` now 302 to `/store#sassymcp` (sassyconsultingllc-cloudflare PR #8, live).
3. **[Move 1 — BLOCKS LAUNCH] Make the commercial license buyable.**
   - Pick the price (see the table above) and seat terms.
   - Create the **SassyMCP Commercial** product in the LS dashboard (license keys ON, activation limit = seats).
   - Wire it in the worker (`PRODUCTS` + `LS_VARIANT_*`), add a store card next to the supporter SKU, and map the variant in `sassymcp/_lemonsqueezy.py` `DEFAULT_VARIANT_MAP` so the activated tier displays.
   - Interim fallback until then: `info@sassyconsultingllc.com` invoices directly (`LICENSING.md` already says so).
   - **Do not launch while "commercial use needs a license" has no buy button.** You'd be telling every work user to pay with no way to do it.
4. **[Capture assets]** 1 demo GIF + 4–6 screenshots. See `08-assets-to-capture.md`. Every channel converts 2–3× better with a real demo GIF.
5. **[Seed directories]** Submit to the rest. Status and steps are in `07-directories.md`. This is the engine; do it before the social spike.
6. **[Launch spike]** ProductHunt + Show HN + Reddit + X, same week. `02`–`05`.
7. **[Content engine]** Publish the dev.to/blog article, then keep publishing. `06`.
8. **[Measure + iterate]** Watch LS orders (commercial vs supporter), directory referrers, and which channel converts.

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

**Pre-launch:** commercial SKU live (step 3), capture assets, seed ALL directories, line up the ProductHunt listing (draft, don't publish), pre-write every post from the docs here.

**Launch day (a Tuesday–Thursday):**
1. 00:01 PT — ProductHunt goes live (schedule it).
2. Morning — Show HN post. Then sit in the thread answering for hours; HN rewards founder presence. Expect the license thread first; the answers are pre-written in `03`.
3. Same morning — Reddit posts (stagger by ~1–2h across subreddits, tailor each per `04`).
4. Throughout — X thread, reply to everyone.
5. Update the ProductHunt first comment + reply to every commenter.

**Week after:** publish the dev.to article, post it to the smaller subreddits and Hacker News (as a blog, not Show HN), follow up on directory listings that need claiming.

**Ongoing engine (monthly):** one new piece of content, refresh directory listings, re-post to relevant new threads ("what MCP servers do you use?" posts appear constantly; answer them honestly with SassyMCP as one option).

---

## Sales-truth guardrails

- **The license line is non-negotiable in every post:** free for personal use, commercial use needs a license, source-available (not open source). Never say "MIT" or "open source."
- **Never gate an existing tool behind payment.** Individuals keep everything forever. New paid things (team features, hosted) get added as *new*, never as removals.
- **No metering claims.** It's a local server. Never advertise "N calls/month."
- **No expired promos.** If you run a sale, use a real future date and remove it when it ends.
- **Quote only facts in `01-positioning.md`.** 278 tools, 39 modules, Windows/macOS/Linux. If the README changes, update `01` first.
