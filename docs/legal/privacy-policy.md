<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
   CodeMark: SCLLC1-SassyMCP-TCK2NFUDLGKC
-->

# SassyMCP — Privacy Policy

> **DRAFT — TEMPLATE ONLY. THIS IS NOT LEGAL ADVICE.** This document is a
> plain-language template. It **requires review and approval by a licensed
> attorney** in each relevant jurisdiction before use or publication. Do not
> post, ship, or rely on it as-is.

**Effective date:** [TODO: OWNER]
**Contact:** [TODO: OWNER — privacy contact email]
**Controller:** [TODO: OWNER — company legal name and address]

---

## A note on how SassyMCP is built

SassyMCP is a **local-first** desktop/server application (Windows-first; also
macOS/Linux). The software runs on *your* machine. Processing of tool calls,
memories, session data, and logs happens locally. This policy describes the
few cases where data leaves your machine.

---

## 1. Data that stays on your machine

By default, everything SassyMCP knows lives in a single per-user folder on
your own computer: `~/.sassymcp/` (or the folder set by `SASSYMCP_HOME`). It
includes:

- **Audit log** (`audit.log`, `audit.jsonl`): every tool invocation with a
  timestamp, tool name, sanitized arguments, elapsed time, and error text.
  Credential-shaped values are redacted before writing.
- **Auth tokens** (`tokens.json`): bearer tokens for MCP clients; stored with
  owner-only permissions.
- **License state** (`license.json`, `.license_secret`): license key, email,
  tier label, activation records.
- **Settings & persona** (`config.json`, `persona.md`): your configuration.
- **Memories** (`memory.db`): things you explicitly ask the assistant to
  remember (`sassy_memory_remember`).
- **Tool usage stats** (`tool_usage.json`): which tools get used, kept for
  90 days.
- **Crosslink queue** (`crosslink.db`): cross-session messages on your LAN.
- **TLS material** (`server.crt`, `server.key`): self-signed certificate for
  HTTPS mode.

None of this is sent to us automatically. You can delete the folder to remove
it (the software will recreate defaults on next start).

**No telemetry.** SassyMCP contains no analytics, tracking pixels, crash
reporters, or "phone-home" telemetry. A source-level inventory of the v1.16.0
codebase (confirmed by an independent audit) found no outbound analytics or
tracking endpoints.

## 2. Network activity that does leave your machine

The following are the *only* outbound connections the software makes, and
only in the circumstances listed:

| When | What is sent | To where |
|---|---|---|
| **Startup update check** (every server start; disable with `SASSYMCP_NO_UPDATE_CHECK=1`) | Version string + User-Agent (`sassymcp/<version>`) | GitHub Releases API (`api.github.com/repos/sassyconsultingllc/SassyMCP/releases`) |
| **Update tools** (`sassy_update_check` / `_list` / `_apply`, called on demand by you or your agent) | Version string + User-Agent; on download, release asset request | GitHub Releases API and release download hosts |
| **Offline-probe** (background connectivity check, daemon thread) | TCP probes only, no data payload | `1.1.1.1`, `8.8.8.8`, `9.9.9.9:53`, `api.github.com:443` |
| **License activation** (only if you buy and activate a supporter key) | License key, machine instance ID, email, order/variant IDs | LemonSqueezy API (`api.lemonsqueezy.com`) |
| **Fast revocation check** (at startup, only if a paid license is present) | SHA-256 *hash* of the license key (not the key itself) | Operator's billing Worker at `SASSYMCP_BILLING_BASE` [TODO: OWNER — confirm production hostname] |
| **Weekly license re-validation** (only if a paid license is present and ≥7 days since last check) | License key + instance ID | LemonSqueezy API |
| **Cloudflare tunnel** (only if *you* run `start-tunnel.bat`) | Your traffic, TLS-terminated at the Cloudflare edge | Your own configured Cloudflare tunnel |
| **Crosslink LAN queue** (only if you start it) | Messages between your own sessions | Your local network (default binds `0.0.0.0:9377` when a token is set) |

Network errors during license checks are **non-decisive**: your local license
stays intact and the check is retried later.

## 3. MCP transports

SassyMCP speaks MCP over stdio (local pipes), HTTP, or legacy SSE. The HTTP
bridge binds to loopback (`127.0.0.1`) by default. If *you* bind it to a
non-loopback address (`--host 0.0.0.0`) or expose it through the tunnel, any
client with your bearer token can reach your full tool surface — that is the
product working as designed, and the choice and its risks are yours.

## 4. Data we collect directly (support, purchases)

If you email us or buy a license through LemonSqueezy, we receive the contact
and order details you provide (name, email, payment metadata handled by the
payment processor — we do not store card numbers). We use this only to provide
support and fulfill the license.

## 5. Your rights

We hold almost nothing about you on our side. For data on *your* machine,
you are the controller — delete files or folders directly. For data we hold
(support emails, purchase records), you may ask us to access, correct, or
delete it at the contact above. Jurisdiction-specific rights follow in the
addenda; where they conflict with this baseline, the addendum controls for
residents of that jurisdiction.

## 6. Children

SassyMCP is a professional tool, not directed at children. We do not knowingly
collect children's data.

---

# Jurisdiction addenda (severable)

Each addendum below is **severable**: it may be updated or removed
independently without affecting the rest of this policy. Where an addendum
grants residents broader rights, it controls for those residents.

## Addendum 1 — United States (federal)

No federal omnibus privacy law applies as of this draft. This policy is
written to satisfy FTC Act §5 expectations: it describes actual practices,
and we will honor it. If you believe a practice misled you, contact us at
[TODO: OWNER] or the FTC.

## Addendum 2 — California (CCPA / CPRA)

- **We do not sell or share personal information**, and we have no knowledge
  of selling or sharing data of consumers under 16.
- **Categories we may hold:** identifiers (email from purchases/support),
  commercial information (order records). All software data stays on your
  device and is not "collected" by us.
- **Rights:** to know, delete, correct, opt out of sale/sharing (not
  applicable), and limit use of sensitive personal information (we do not use
  any). Submit requests to [TODO: OWNER]. We will verify you via the email on
  record and respond within 45 days. No discrimination for exercising rights.
- **Service providers:** LemonSqueezy (payments) and GitHub (update checks)
  process limited data as service providers under contract.

## Addendum 3 — European Union (GDPR)

- **Controller:** [TODO: OWNER — name/address].
- **Lawful bases:** contract (license fulfillment), legitimate interest
  (update/security checks, fraud prevention), consent (optional tunnel use —
  you opt in by running it).
- **Rights:** access, rectification, erasure, restriction, portability,
  objection, and not to be subject to solely automated decision-making
  (we make none). Contact [TODO: OWNER]; response within one month.
- **Transfers:** license/purchase data may be processed in the US by
  LemonSqueezy under their DPA/SCCs; review their terms before purchase.
- **Complaints:** to your national supervisory authority.
- **Retention:** support records [TODO: OWNER — e.g., 24 months]; purchase
  records per tax law.

## Addendum 4 — United Kingdom (UK GDPR)

The UK GDPR is a **separate regime from EU GDPR post-Brexit**. The substance
of Addendum 3 applies in the UK with these changes: complaints go to the
**Information Commissioner's Office (ICO)**; "controller" details above;
international transfers follow the UK Extension / UK IDTA. Contact
[TODO: OWNER].

## Addendum 5 — India (DPDP Act, 2023)

- We act as **Data Fiduciary** only for data you send us directly (support,
  purchases). Data on your device is processed by you, not us.
- **Consent:** activating a paid license or contacting support is consent to
  process that data for that purpose; withdraw by requesting deletion.
- **Rights:** access, correction, erasure, grievance redressal
  ([TODO: OWNER — grievance officer contact]), and nomination. We will
  respond within statutory timelines.
- Breach notification will follow the Act's requirements to the Data
  Protection Board of India.

## Addendum 6 — Australia (Privacy Act 1988 + Notifiable Data Breaches)

- We aim to comply with the Australian Privacy Principles for the limited
  data we hold (support/purchase records).
- **Notifiable Data Breaches:** if a breach of data we hold is likely to
  cause serious harm, we will notify you and the OAIC as required.
- Access/correction requests: [TODO: OWNER]; complaints to the OAIC if
  unresolved.

## Addendum 7 — Canada (PIPEDA + Québec Law 25)

- **PIPEDA:** we collect only with knowledge and consent (purchase/support),
  limited to identified purposes; you may withdraw consent subject to legal
  limits; access requests to [TODO: OWNER].
- **Québec Law 25:** our privacy officer is [TODO: OWNER — name/contact].
  We conduct privacy impact assessments for new processing; confidentiality
  incidents presenting a risk of serious injury will be notified to you and
  the Commission d'accès à l'information. You have rights of access,
  rectification, and to request de-indexing/destruction where applicable.

## Addendum 8 — Brazil (LGPD)

- **Legal bases:** consent and contract performance (license/support).
- **Rights:** confirmation, access, correction, anonymization/blocking/
  deletion, portability, information on sharing, and revocation of consent —
  via [TODO: OWNER].
- Complaints to the **ANPD**. Data on your device is not transferred to us;
  purchase data may be processed abroad by our payment provider with
  appropriate safeguards.

## Addendum 9 — Japan (APPI)

- We use personal information only within the purposes stated above
  (support, license fulfillment).
- **Cross-border transfers:** data you provide (e.g., purchase email) may be
  processed outside Japan by our service providers; we take equivalent
  protective measures via contract.
- Disclosure/correction/cessation requests: [TODO: OWNER].
- [TODO: OWNER — confirm whether a Japan-local representative is required at
  your scale.]

## Addendum 10 — Singapore (PDPA)

- **Consent:** providing data for purchases/support is deemed consent for
  those purposes; withdraw via [TODO: OWNER].
- **Do Not Call:** we do not send marketing messages; no DNC registration
  needed.
- Access/correction requests: [TODO: OWNER]; complaints to the PDPC if
  unresolved.

## Addendum 11 — South Korea (PIPA)

- We collect minimum necessary data (purchase/support) and do not process
  sensitive or resident-registration data.
- **Rights:** access, correction, deletion, and suspension of processing —
  via [TODO: OWNER].
- **Breach notification:** we will notify you and the authorities without
  undue delay if required.
- [TODO: OWNER — confirm whether a domestic agent (Article 32-5) is required
  at your scale.]

---

*End of draft. Owner action: fill every [TODO: OWNER], then licensed-attorney
review before publication.*
