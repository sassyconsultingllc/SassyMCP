<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
   CodeMark: SCLLC1-SassyMCP-NYNZCT5HQJB6
-->

# SassyMCP — End User License Agreement (EULA)

> **DRAFT — TEMPLATE ONLY. THIS IS NOT LEGAL ADVICE.** This document is a
> plain-language template. It **requires review and approval by a licensed
> attorney** in each relevant jurisdiction before use or publication. Do not
> ship, bundle, or rely on it as-is.

**Effective date / version:** [TODO: OWNER]
**Licensor:** [TODO: OWNER — company legal name and address]
**Contact:** [TODO: OWNER — contact email]

By installing, copying, or using SassyMCP ("the Software"), you agree to this
Agreement. If you do not agree, do not install or use it, and delete any
copies.

## 1. License grant

We grant you a personal, non-exclusive, non-transferable, revocable license
to install and use the Software on computers you own or control, for your own
lawful purposes. The Software ships with all tool groups unlocked; a paid
"supporter" license key is optional and affects only the displayed supporter
tier label, not which features work.

## 2. Restrictions

You may not:

- **Redistribute** the Software (or any part of it) to others, whether for
  payment or free, except as we expressly authorize in writing.
- **Reverse-engineer the license checks** — decompiling, bypassing, or
  tampering with license validation, revocation checks, or the HMAC-signed
  license file (`~/.sassymcp/license.json`).
- Remove or alter copyright notices or proprietary markings.
- Use the Software to violate law or the rights of others.

General interoperability reverse engineering permitted by mandatory law
(where applicable — see addenda) is not restricted beyond what the law
requires.

## 3. Dual-use nature — your responsibility

SassyMCP gives large language models broad control over your machine:
shell execution, file operations, UI automation, network tools, device
bridges (ADB), and more. **You are responsible for what your agents do
with that power.** Specifically:

- Review and supervise agent actions; destructive tools are labeled as such,
  but labels do not replace your judgment.
- Treat the bearer token as a password: anyone holding it can drive your
  full tool surface over any network path you expose (LAN bind, tunnel).
- If you expose the server beyond loopback (`--host 0.0.0.0`, `start-lan.bat`,
  `start-tunnel.bat`), you accept the resulting risk.
- We are not responsible for actions your agents take, data they access or
  delete, or third-party systems they touch at your direction.

## 4. Updates, license validation, and network behavior

To keep the Software secure, it may:

- Check for updates at startup (GitHub Releases; opt out with
  `SASSYMCP_NO_UPDATE_CHECK=1`) and on your demand via update tools.
- Validate a paid license at startup (fast revocation check) and weekly
  thereafter, as described in the Privacy Policy. Network failures never
  remove a valid local license.

## 5. Ownership

The Software is licensed, not sold. [TODO: OWNER] retains all right, title,
and interest, including intellectual property rights. No rights are granted
except as expressly stated here.

## 6. No warranty

THE SOFTWARE IS PROVIDED "AS IS," WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND
NON-INFRINGEMENT. We do not warrant that it is error-free, secure against
all attack, or suitable for any particular use — especially safety-critical
uses, for which it is not intended.

## 7. Limitation of liability

TO THE MAXIMUM EXTENT PERMITTED BY LAW, [TODO: OWNER]'S TOTAL LIABILITY
UNDER THIS AGREEMENT IS LIMITED TO **[TODO: OWNER — e.g., the amount you
paid for the Software, or $50 if none]**. IN NO EVENT ARE WE LIABLE FOR
INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES,
INCLUDING DATA LOSS OR ACTIONS TAKEN BY YOUR AGENTS.

## 8. Termination

This Agreement ends if you breach it (including license-check tampering or
redistribution) or when you delete the Software. On termination you must stop
using and delete all copies. Paid-license refunds are handled under the
Terms of Use; revocation removes the supporter tier label via the validation
system described in the Privacy Policy.

## 9. Governing law

[TODO: OWNER — governing law and venue]. Disputes: contact [TODO: OWNER]
first and allow 30 days for informal resolution.

---

# Jurisdiction addenda (severable)

Each addendum is **severable** — it may be updated or removed independently.
Where an addendum gives residents stronger protections, it controls for
those residents.

## Addendum 1 — United States (federal)

§2's reverse-engineering restriction does not prohibit conduct expressly
permitted by federal law (e.g., fair-use interoperability analysis). DMCA
§1201 concerns should be raised with counsel before enforcement action.

## Addendum 2 — California

Nothing in §§6–7 limits liability for gross negligence, willful misconduct,
fraud, or as otherwise prohibited by California law.

## Addendum 3 — European Union

- **Reverse engineering:** Article 6 of the Software Directive (2009/24/EC)
  permits decompilation for interoperability where its conditions are met;
  §2 yields to the extent the law requires.
- **Consumer sales:** EU consumers keep mandatory conformity and remedy
  rights under the Sale of Goods Directive and Digital Content Directive for
  paid licenses; §§6–7 are read subject to those rights.
- **Unfair terms:** any term found unfair under the Unfair Contract Terms
  Directive is severed; the remainder stands.

## Addendum 4 — United Kingdom (post-Brexit, separate regime)

The UK has its own software and consumer regime post-Brexit. UK users retain
interoperability decompilation rights under the Copyright, Designs and
Patents Act 1988 (s.50B) and consumer rights under the Consumer Rights Act
2015 (including digital-content conformity). §§2, 6–7 yield accordingly.

## Addendum 5 — India

§2 does not restrict acts permitted under the Copyright Act, 1957 (including
s.52 allowances). Indian consumers retain remedies under the Consumer
Protection Act, 2019; grievance officer: [TODO: OWNER].

## Addendum 6 — Australia

Australian consumers retain non-excludable guarantees under the Australian
Consumer Law (including that digital products be of acceptable quality and
fit for disclosed purpose). §§6–7 do not exclude those guarantees; liability
for breach is limited to re-supply or its cost where the law permits.

## Addendum 7 — Canada (incl. Québec)

- Provincial consumer protection statutes apply and cannot be waived.
- **Québec:** this Agreement with Québec consumers is governed by Québec
  law; a French version prevails once provided.
  [TODO: OWNER — French translation required before distribution in Québec.]

## Addendum 8 — Brazil

The Software Directive-style interoperability limits and CDC consumer
protections apply: abusive clauses are void, and the burden of proof reverses
in the consumer's favor. §§2, 6–7 yield to the CDC and Brazilian Software
Law (9.609/98) as applicable.

## Addendum 9 — Japan

Clauses unreasonably disadvantageous to consumers are void under the
Consumer Contract Act. Reverse engineering for interoperability analysis
permitted under Japanese law is not restricted.

## Addendum 10 — Singapore

Nothing here limits rights under the Consumer Protection (Fair Trading) Act
or the Unfair Contract Terms Act (to the extent applicable to negotiated
consumer terms).

## Addendum 11 — South Korea

Standard-terms regulation applies: any clause deemed unfair under the Act
on the Regulation of Terms and Conditions is void. Korean consumers retain
e-commerce protections for paid licenses.

---

*End of draft. Owner action: fill every [TODO: OWNER], then licensed-attorney
review before publication.*
