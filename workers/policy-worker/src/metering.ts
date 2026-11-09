// Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
// Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
// CodeMark: SCLLC1-SassyMCP-38C5MQCVJGFR
//
// NOTE ON HEADER STYLE: `#` is not a valid TypeScript comment (top-level `#`
// lines are a syntax error), so the repo's three header lines are kept here
// as `//` comments with identical text.

/**
 * Per-license-key usage metering in Workers KV.
 *
 * Keys (all namespaced by the SHA-256 of the license key — the raw key is
 * never persisted):
 *   usage:<hash>:<YYYY-MM-DD>          → total evaluations today
 *   usage:<hash>:<YYYY-MM-DD>:<decision> → per-decision counts (allow/ask/deny)
 *
 * KV has no atomic increment, so these counters are read-modify-write and
 * therefore APPROXIMATE under concurrent load. They are suitable for
 * dashboards, abuse triage, and rough billing — not for money math.
 * If exact counts ever matter, stream evaluations to Workers Analytics
 * Engine or a Durable Object instead.
 *
 * Counters expire after 32 days so storage stays bounded.
 */

export type Decision = "allow" | "ask" | "deny";

export interface UsageCounts {
  /** UTC date, YYYY-MM-DD. */
  date: string;
  total: number;
  allow: number;
  ask: number;
  deny: number;
}

const COUNTER_TTL_SECONDS = 32 * 24 * 3600;

function parseCount(raw: string | null): number {
  const n = raw === null ? 0 : parseInt(raw, 10);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

function utcDate(nowMs: number): string {
  return new Date(nowMs).toISOString().slice(0, 10);
}

/**
 * Record one policy decision and return today's counts INCLUDING this
 * decision, for echoing back in the evaluate response.
 */
export async function recordDecision(
  kv: KVNamespace,
  keyHash: string,
  decision: Decision,
  nowMs: number = Date.now(),
): Promise<UsageCounts> {
  const date = utcDate(nowMs);
  const totalKey = `usage:${keyHash}:${date}`;
  const decisionKey = `usage:${keyHash}:${date}:${decision}`;

  const [totalRaw, decisionRaw] = await Promise.all([
    kv.get(totalKey),
    kv.get(decisionKey),
  ]);
  const total = parseCount(totalRaw) + 1;
  const decisionCount = parseCount(decisionRaw) + 1;

  await Promise.all([
    kv.put(totalKey, String(total), { expirationTtl: COUNTER_TTL_SECONDS }),
    kv.put(decisionKey, String(decisionCount), {
      expirationTtl: COUNTER_TTL_SECONDS,
    }),
  ]);

  const [allowRaw, askRaw, denyRaw] = await Promise.all([
    kv.get(`usage:${keyHash}:${date}:allow`),
    kv.get(`usage:${keyHash}:${date}:ask`),
    kv.get(`usage:${keyHash}:${date}:deny`),
  ]);

  return {
    date,
    total,
    allow: parseCount(allowRaw),
    ask: parseCount(askRaw),
    deny: parseCount(denyRaw),
  };
}
