// Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
// Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
// CodeMark: SCLLC1-SassyMCP-LTM44OL75A5Z
//
// NOTE ON HEADER STYLE: `#` is not a valid TypeScript comment (top-level `#`
// lines are a syntax error), so the repo's three header lines are kept here
// as `//` comments with identical text.

/**
 * Per-license-key request throttling.
 *
 * Two-bucket sliding-window approximation over Workers KV:
 *   estimate = current_minute_count + previous_minute_count * fraction_of_minute_remaining
 * KV has no transactions, so under heavy concurrency the count is
 * approximate — fine for abuse throttling, not for billing.
 *
 * Keys are namespaced by the SHA-256 of the license key; the raw key is
 * never written to KV.
 */

export const RATE_LIMIT_PER_MINUTE = 600;

export interface RateLimitResult {
  allowed: boolean;
  /** Seconds the caller should wait before retrying. 0 when allowed. */
  retryAfterSeconds: number;
}

function parseCount(raw: string | null): number {
  const n = raw === null ? 0 : parseInt(raw, 10);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

export async function checkRateLimit(
  kv: KVNamespace,
  keyHash: string,
  nowMs: number = Date.now(),
): Promise<RateLimitResult> {
  const minute = Math.floor(nowMs / 60_000);
  const curKey = `rl:${keyHash}:${minute}`;
  const prevKey = `rl:${keyHash}:${minute - 1}`;

  const [curRaw, prevRaw] = await Promise.all([
    kv.get(curKey),
    kv.get(prevKey),
  ]);
  const cur = parseCount(curRaw);
  const prev = parseCount(prevRaw);

  const elapsedMs = nowMs - minute * 60_000;
  const estimated = cur + prev * ((60_000 - elapsedMs) / 60_000);

  if (estimated >= RATE_LIMIT_PER_MINUTE) {
    return {
      allowed: false,
      retryAfterSeconds: Math.max(1, Math.ceil((60_000 - elapsedMs) / 1000)),
    };
  }

  // Count this request. 180s TTL comfortably covers the two buckets we read.
  await kv.put(curKey, String(cur + 1), { expirationTtl: 180 });
  return { allowed: true, retryAfterSeconds: 0 };
}
