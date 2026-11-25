// Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
// Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
// CodeMark: SCLLC1-SassyMCP-U4VZOU5M4AUR
//
// NOTE ON HEADER STYLE: `#` is not a valid TypeScript comment (top-level `#`
// lines are a syntax error), so the repo's three header lines are kept here
// as `//` comments with identical text.

/**
 * SassyMCP Policy Worker — the remote half of the split architecture.
 *
 * The local SassyMCP agent keeps executing primitives on the user's machine
 * (it must — shell control is inherently local), but before running a
 * tool call it asks THIS worker for a policy decision. The clever half —
 * license validation, tier→group entitlements, metering — lives here, on
 * vendor infrastructure, where customers never receive the source.
 *
 * Protocol v1:
 *   GET  /v1/policy/status    → { ok, policy_version }              (no auth)
 *   POST /v1/policy/evaluate → { decision, reason, ttl_seconds,
 *                                policy_version, usage }            (Bearer <LS license key>)
 *
 * Privacy contract with the local agent: it sends REDACTED SUMMARIES
 * (cmd_preview truncated, path_scope, flags) — never full arguments, never
 * file contents. This worker logs only the SHA-256 prefix of the license
 * key, never the key itself.
 *
 * Trust posture: this worker is the vendor trust anchor. Uncertainty fails
 * CLOSED (deny) — a license we cannot confirm as active is not honored.
 * The local agent remains responsible for its own confirmation gates and
 * offline behavior; this worker only answers "what does the license allow".
 */

import {
  validateLicense,
  type LsLicenseStatus,
  type LsValidation,
} from "./lemonsqueezy";
import { checkRateLimit } from "./ratelimit";
import { recordDecision, type Decision, type UsageCounts } from "./metering";

export const POLICY_VERSION = "2026-09-22";
const DECISION_TTL_SECONDS = 300;
const LICENSE_CACHE_TTL_SECONDS = 15 * 60; // 15 minutes, per spec

export interface Env {
  POLICY_KV: KVNamespace;
}

// ═══════════════════════════════════════════════════════════════════════
//  OWNER-EDITABLE POLICY TABLES — this is the section you maintain.
//  Everything below the tables is machinery; everything policy lives here.
// ═══════════════════════════════════════════════════════════════════════

/**
 * The 18 tool groups shipped by SassyMCP v1.16.0
 * (sassymcp/modules/_tool_loader.py :: TOOL_GROUPS). A group name arriving
 * in an evaluate request that is NOT in this list is treated as "unknown"
 * and yields `ask`, never `deny` — the scaffold fails toward usability.
 * Production may want to flip that to deny; see decidePolicy().
 */
const KNOWN_GROUPS: readonly string[] = [
  "meta",
  "core",
  "infrastructure",
  "android",
  "iphone",
  "system",
  "forensics",
  "linux",
  "github_quick",
  "github_full",
  "v020",
  "persona",
  "utility",
  "setup",
  "memory",
  "updater",
  "combos",
  "prompts",
];

/**
 * LemonSqueezy variant_id → SassyMCP tier.
 *
 * [TODO: OWNER] Fill in your real LS variant IDs. The "1753676" entry is
 * real — it mirrors DEFAULT_VARIANT_MAP in sassymcp/_lemonsqueezy.py
 * (SassyMCP Supporter → pro). The others are placeholders; replace them
 * with the variant IDs from your LemonSqueezy store dashboard.
 *
 * Unknown variant IDs resolve to "free" — the same safe default as
 * variant_to_entitlement() in the Python client. A buyer with a key for
 * an unmapped variant is never silently upgraded.
 */
const VARIANT_TIER: Record<string, string> = {
  "1753676": "pro",
  // [TODO: OWNER] e.g. "2345678": "free",
  // [TODO: OWNER] e.g. "2345679": "enterprise",
};

/**
 * Tier → tool groups the tier may use. `"*"` means all known groups.
 *
 * [TODO: OWNER] THIS TABLE IS THE PRODUCT. Edit it to match what each
 * tier is actually sold as. The values below are a starting scaffold:
 * free gets the safe local basics, pro gets everything, enterprise is
 * explicit-wildcard for future groups added without a worker redeploy.
 */
const TIER_GROUPS: Record<string, string[]> = {
  free: ["meta", "core", "infrastructure", "utility", "prompts", "persona"],
  pro: [
    "meta",
    "core",
    "infrastructure",
    "android",
    "iphone",
    "system",
    "forensics",
    "linux",
    "github_quick",
    "github_full",
    "v020",
    "persona",
    "utility",
    "setup",
    "memory",
    "updater",
    "combos",
    "prompts",
  ],
  enterprise: ["*"],
};

// ═══════════════════════════════════════════════════════════════════════
//  Machinery — you should not need to edit below this line.
// ═══════════════════════════════════════════════════════════════════════

interface EvaluateBody {
  tool: string;
  group: string;
  destructive: boolean;
  arg_summary?: Record<string, unknown>;
  profile?: string;
  /** Informational only — the authoritative tier comes from LS, not the client. */
  license_tier?: string;
}

interface CachedLicense {
  v: 1;
  validation: LsValidation;
  cached_at: string;
}

/** Exported for tests. The raw key never leaves the request handler. */
export async function hashLicenseKey(licenseKey: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(licenseKey),
  );
  return [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function tierForVariant(variantId: string | null): string {
  if (variantId !== null) {
    const tier = VARIANT_TIER[variantId];
    if (tier) return tier;
  }
  return "free"; // safe default, mirrors the Python client
}

function groupAllowed(tier: string, group: string): boolean {
  const allowed = TIER_GROUPS[tier] ?? [];
  return allowed.includes("*") || allowed.includes(group);
}

function decidePolicy(
  status: LsLicenseStatus,
  tier: string,
  group: string,
): { decision: Decision; reason: string } {
  // 1. License must be active. Anything else — expired, disabled,
  //    inactive, invalid, unknown, or unreachable — denies, and the reason
  //    names the status so the buyer knows what to fix.
  if (status !== "active") {
    if (status === "network_error") {
      return {
        decision: "deny",
        reason:
          "License service unreachable and no cached validation is available; " +
          "failing closed. Retry shortly — this is not a billing problem.",
      };
    }
    return {
      decision: "deny",
      reason:
        `License status is '${status}' (expected 'active'). ` +
        "Resolve billing with Sassy Consulting LLC, then retry.",
    };
  }

  // 2. Unknown groups fail toward usability in this scaffold: ask the user
  //    instead of denying. PRODUCTION NOTE: if the group list ever becomes
  //    a security boundary (rather than a packaging boundary), flip this
  //    to deny — an attacker probing group names should not get a
  //    user-mediated oracle.
  if (!KNOWN_GROUPS.includes(group)) {
    return {
      decision: "ask",
      reason:
        `Tool group '${group}' is not a known SassyMCP group. ` +
        "Escalating to the user for a one-time decision.",
    };
  }

  // 3. Tier entitlement.
  if (groupAllowed(tier, group)) {
    return {
      decision: "allow",
      reason: `Group '${group}' is permitted for tier '${tier}'.`,
    };
  }
  return {
    decision: "deny",
    reason: `Group '${group}' is not included in the '${tier}' tier.`,
  };
}

function errJson(
  status: number,
  code: string,
  message: string,
  extra: Record<string, unknown> = {},
): Response {
  return new Response(JSON.stringify({ error: message, code, ...extra }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function validateBody(
  raw: unknown,
): { ok: true; body: EvaluateBody } | { ok: false; message: string } {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    return { ok: false, message: "Request body must be a JSON object." };
  }
  const b = raw as Record<string, unknown>;
  if (typeof b.tool !== "string" || b.tool.length === 0) {
    return { ok: false, message: "'tool' must be a non-empty string." };
  }
  if (typeof b.group !== "string" || b.group.length === 0) {
    return { ok: false, message: "'group' must be a non-empty string." };
  }
  if (typeof b.destructive !== "boolean") {
    return { ok: false, message: "'destructive' must be a boolean." };
  }
  if (
    b.arg_summary !== undefined &&
    (typeof b.arg_summary !== "object" ||
      b.arg_summary === null ||
      Array.isArray(b.arg_summary))
  ) {
    return {
      ok: false,
      message: "'arg_summary' must be an object when present.",
    };
  }
  if (b.profile !== undefined && typeof b.profile !== "string") {
    return { ok: false, message: "'profile' must be a string when present." };
  }
  if (b.license_tier !== undefined && typeof b.license_tier !== "string") {
    return {
      ok: false,
      message: "'license_tier' must be a string when present.",
    };
  }
  return {
    ok: true,
    body: {
      tool: b.tool,
      group: b.group,
      destructive: b.destructive,
      arg_summary: b.arg_summary as Record<string, unknown> | undefined,
      profile: b.profile as string | undefined,
      license_tier: b.license_tier as string | undefined,
    },
  };
}

async function getCachedValidation(
  kv: KVNamespace,
  keyHash: string,
  licenseKey: string,
  instanceId: string,
): Promise<LsValidation> {
  const cacheKey = `lic:${keyHash}`;
  try {
    const cached = (await kv.get(cacheKey, "json")) as CachedLicense | null;
    if (cached && cached.v === 1 && cached.validation) {
      return cached.validation;
    }
  } catch {
    // Corrupt cache entry — fall through to a fresh LS validation.
  }

  const validation = await validateLicense(licenseKey, instanceId);

  // Cache everything EXCEPT transport failures: caching a network_error
  // would extend an LS outage into a 15-minute deny-all.
  if (validation.status !== "network_error") {
    const payload: CachedLicense = {
      v: 1,
      validation,
      cached_at: new Date().toISOString(),
    };
    await kv.put(cacheKey, JSON.stringify(payload), {
      expirationTtl: LICENSE_CACHE_TTL_SECONDS,
    });
  }
  return validation;
}

async function handleEvaluate(request: Request, env: Env): Promise<Response> {
  // ── 1. Auth: the Bearer <redacted> IS the LemonSqueezy license key. ──
  const auth = request.headers.get("Authorization") ?? "";
  const match = /^Bearer (.+)$/.exec(auth);
  const licenseKey = match ? match[1].trim() : "";
  if (!licenseKey) {
    return errJson(
      401,
      "missing_auth",
      "Missing or malformed Authorization header; expected 'Bearer <lemonsqueezy-license-key>'.",
    );
  }
  const keyHash = await hashLicenseKey(licenseKey);
  const keyPrefix = keyHash.slice(0, 12); // logging only — never the key

  // ── 2. Rate limit (before any LS call, so abuse can't hammer LS). ──
  const rl = await checkRateLimit(env.POLICY_KV, keyHash);
  if (!rl.allowed) {
    console.log(
      JSON.stringify({
        event: "rate_limited",
        key_prefix: keyPrefix,
        retry_after_seconds: rl.retryAfterSeconds,
      }),
    );
    return errJson(
      429,
      "rate_limited",
      "Rate limit exceeded for this license key.",
      { retry_after_seconds: rl.retryAfterSeconds },
    );
  }

  // ── 3. Instance id (required — LS validate needs it). ──
  const instanceId = (request.headers.get("X-SassyMCP-Instance") ?? "").trim();
  if (!instanceId) {
    return errJson(
      400,
      "missing_instance",
      "Missing X-SassyMCP-Instance header; the worker needs it to validate the license with LemonSqueezy.",
    );
  }

  // ── 4. Body parsing + validation. ──
  let raw: unknown;
  try {
    raw = await request.json();
  } catch {
    return errJson(
      400,
      "invalid_body",
      "Request body is not valid JSON.",
    );
  }
  const parsed = validateBody(raw);
  if (!parsed.ok) {
    return errJson(400, "invalid_body", parsed.message);
  }
  const body = parsed.body;

  // ── 5. License validation (KV-cached, 15 min). ──
  const validation = await getCachedValidation(
    env.POLICY_KV,
    keyHash,
    licenseKey,
    instanceId,
  );
  const tier = tierForVariant(validation.variantId);

  // The client-reported tier is informational only. A mismatch is worth a
  // log line — it can indicate a tampered or confused local agent.
  if (body.license_tier && body.license_tier !== tier) {
    console.warn(
      JSON.stringify({
        event: "tier_mismatch",
        key_prefix: keyPrefix,
        reported: body.license_tier,
        authoritative: tier,
      }),
    );
  }

  // ── 6. Decide, meter, respond. ──
  const { decision, reason } = decidePolicy(
    validation.status,
    tier,
    body.group,
  );
  const usage: UsageCounts = await recordDecision(
    env.POLICY_KV,
    keyHash,
    decision,
  );

  console.log(
    JSON.stringify({
      event: "policy_evaluate",
      key_prefix: keyPrefix,
      instance: instanceId,
      client_version: request.headers.get("X-SassyMCP-Version") ?? "unknown",
      tool: body.tool,
      group: body.group,
      destructive: body.destructive,
      profile: body.profile ?? "full",
      license_status: validation.status,
      tier,
      decision,
    }),
  );

  return new Response(
    JSON.stringify({
      decision,
      reason,
      ttl_seconds: DECISION_TTL_SECONDS,
      policy_version: POLICY_VERSION,
      usage,
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    if (request.method === "GET" && path === "/v1/policy/status") {
      return new Response(
        JSON.stringify({ ok: true, policy_version: POLICY_VERSION }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }

    if (request.method === "POST" && path === "/v1/policy/evaluate") {
      return handleEvaluate(request, env);
    }

    return errJson(
      404,
      "not_found",
      `No route for ${request.method} ${url.pathname}.`,
    );
  },
};
