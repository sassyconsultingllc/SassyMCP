// Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
// Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
// CodeMark: SCLLC1-SassyMCP-5H2PW98HB8EY
//
// NOTE ON HEADER STYLE: `#` is not a valid TypeScript comment (top-level `#`
// lines are a syntax error), so the repo's three header lines are kept here
// as `//` comments with identical text.

/**
 * Vitest suite for the policy worker. The LemonSqueezy HTTP call is mocked
 * via vi.stubGlobal("fetch", ...); KV is an in-memory stub implementing the
 * get/put surface the worker actually uses.
 *
 * Run:  npm test        (from workers/policy-worker/)
 *       npx vitest run  (same thing, explicit)
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import worker, { POLICY_VERSION, hashLicenseKey } from "../src/index";

async function j(res: Response): Promise<any> {
  return res.json();
}

const TEST_KEY = "LS-TEST-KEY-0000-1111-2222-333333333333";
const TEST_INSTANCE = "test-instance-1";

// ── In-memory KV stub ────────────────────────────────────────────────

function makeKv() {
  const store = new Map<string, string>();
  const kv = {
    get: async (key: string, opts?: unknown): Promise<unknown> => {
      const v = store.get(key);
      if (v === undefined) return null;
      if (opts === "json" || (opts as any)?.type === "json") {
        return JSON.parse(v);
      }
      return v;
    },
    put: async (key: string, value: string): Promise<void> => {
      store.set(key, value);
    },
    __store: store,
  };
  return kv;
}

function makeEnv() {
  return { POLICY_KV: makeKv() as unknown as KVNamespace };
}

// ── LS fetch mock ────────────────────────────────────────────────────

function mockLs(payload: unknown, status = 200) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json(payload, { status }),
    ) as unknown as typeof fetch,
  );
}

const LS_ACTIVE_PRO = {
  valid: true,
  license_key: { id: 111, status: "active", expires_at: "2027-09-22T00:00:00.000Z" },
  meta: {
    customer_email: "buyer@example.com",
    variant_id: 1753676, // the real pro variant from sassymcp/_lemonsqueezy.py
    product_id: 1,
    store_id: 1,
    order_id: 1,
  },
  instance: { id: "inst-1", name: "sassy:test@localhost" },
};

const LS_EXPIRED = {
  valid: false,
  error: "This license key has expired.",
  license_key: { id: 112, status: "expired", expires_at: "2025-01-01T00:00:00.000Z" },
  meta: { customer_email: "buyer@example.com", variant_id: 1753676 },
};

const LS_ACTIVE_UNKNOWN_VARIANT = {
  ...LS_ACTIVE_PRO,
  meta: { ...LS_ACTIVE_PRO.meta, variant_id: 9990001 }, // unmapped → free tier
};

// ── Request helpers ──────────────────────────────────────────────────

const BASE_BODY = {
  tool: "sassy_shell",
  group: "system",
  destructive: true,
  arg_summary: { cmd_preview: "rm -rf /tmp/scratch/...", path_scope: "/tmp" },
  profile: "full",
  license_tier: "pro",
};

function evalRequest(
  body: unknown,
  opts: { key?: string | null; instance?: string | null } = {},
): Request {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (opts.key !== null) {
    headers["Authorization"] = `Bearer ${opts.key ?? TEST_KEY}`;
  }
  if (opts.instance !== null) {
    headers["X-SassyMCP-Instance"] = opts.instance ?? TEST_INSTANCE;
  }
  headers["X-SassyMCP-Version"] = "1.16.0";
  return new Request("https://policy.test/v1/policy/evaluate", {
    method: "POST",
    headers,
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

beforeEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ── Tests ────────────────────────────────────────────────────────────

describe("GET /v1/policy/status", () => {
  it("answers without auth", async () => {
    const env = makeEnv();
    const res = await worker.fetch(
      new Request("https://policy.test/v1/policy/status"),
      env,
    );
    expect(res.status).toBe(200);
    expect(await j(res)).toEqual({ ok: true, policy_version: POLICY_VERSION });
  });
});

describe("auth", () => {
  it("rejects a missing Authorization header with 401 + envelope", async () => {
    const env = makeEnv();
    const res = await worker.fetch(evalRequest(BASE_BODY, { key: null }), env);
    expect(res.status).toBe(401);
    const body = await j(res);
    expect(body.code).toBe("missing_auth");
    expect(typeof body.error).toBe("string");
  });

  it("rejects a malformed Authorization scheme with 401", async () => {
    const env = makeEnv();
    const req = evalRequest(BASE_BODY);
    req.headers.set("Authorization", "Token abc123");
    const res = await worker.fetch(req, env);
    expect(res.status).toBe(401);
    expect((await j(res)).code).toBe("missing_auth");
  });
});

describe("body validation", () => {
  it("rejects non-JSON with 400 invalid_body", async () => {
    mockLs(LS_ACTIVE_PRO);
    const env = makeEnv();
    const res = await worker.fetch(evalRequest("{not json"), env);
    expect(res.status).toBe(400);
    expect((await j(res)).code).toBe("invalid_body");
  });

  it("rejects a missing 'tool' with 400 invalid_body", async () => {
    mockLs(LS_ACTIVE_PRO);
    const env = makeEnv();
    const { tool: _drop, ...rest } = BASE_BODY;
    const res = await worker.fetch(evalRequest(rest), env);
    expect(res.status).toBe(400);
    const body = await j(res);
    expect(body.code).toBe("invalid_body");
    expect(body.error).toContain("'tool'");
  });

  it("rejects a non-boolean 'destructive' with 400 invalid_body", async () => {
    mockLs(LS_ACTIVE_PRO);
    const env = makeEnv();
    const res = await worker.fetch(
      evalRequest({ ...BASE_BODY, destructive: "yes" }),
      env,
    );
    expect(res.status).toBe(400);
    expect((await j(res)).code).toBe("invalid_body");
  });

  it("rejects a missing X-SassyMCP-Instance with 400 missing_instance", async () => {
    mockLs(LS_ACTIVE_PRO);
    const env = makeEnv();
    const res = await worker.fetch(
      evalRequest(BASE_BODY, { instance: null }),
      env,
    );
    expect(res.status).toBe(400);
    expect((await j(res)).code).toBe("missing_instance");
  });
});

describe("policy decisions", () => {
  it("active pro license + permitted group → allow, with usage echo", async () => {
    mockLs(LS_ACTIVE_PRO);
    const env = makeEnv();
    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});

    const res = await worker.fetch(evalRequest(BASE_BODY), env);
    expect(res.status).toBe(200);
    const body = await j(res);
    expect(body.decision).toBe("allow");
    expect(body.ttl_seconds).toBe(300);
    expect(body.policy_version).toBe(POLICY_VERSION);
    expect(body.usage.total).toBe(1);
    expect(body.usage.allow).toBe(1);
    expect(body.usage.deny).toBe(0);

    // The raw license key must never appear in logs — only the hash prefix.
    const logged = logSpy.mock.calls.map((c) => String(c[0])).join("\n");
    expect(logged).not.toContain(TEST_KEY);
    expect(logged).toContain("policy_evaluate");

    // Second call increments metering.
    const res2 = await worker.fetch(evalRequest(BASE_BODY), env);
    expect((await j(res2)).usage.total).toBe(2);
  });

  it("expired license → deny, reason names the status", async () => {
    mockLs(LS_EXPIRED);
    const env = makeEnv();
    const res = await worker.fetch(evalRequest(BASE_BODY), env);
    expect(res.status).toBe(200);
    const body = await j(res);
    expect(body.decision).toBe("deny");
    expect(body.reason).toContain("expired");
    expect(body.usage.deny).toBe(1);
  });

  it("LS network failure → deny (fail closed), reason says unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("connection reset");
      }) as unknown as typeof fetch,
    );
    const env = makeEnv();
    const res = await worker.fetch(evalRequest(BASE_BODY), env);
    const body = await j(res);
    expect(body.decision).toBe("deny");
    expect(body.reason).toContain("unreachable");
  });

  it("unknown tool group → ask (fail toward usability)", async () => {
    mockLs(LS_ACTIVE_PRO);
    const env = makeEnv();
    const res = await worker.fetch(
      evalRequest({ ...BASE_BODY, group: "warpdrive" }),
      env,
    );
    const body = await j(res);
    expect(body.decision).toBe("ask");
    expect(body.reason).toContain("warpdrive");
  });

  it("unmapped variant → free tier: forensics denied, core allowed", async () => {
    mockLs(LS_ACTIVE_UNKNOWN_VARIANT);
    const env = makeEnv();

    const denied = await worker.fetch(
      evalRequest({ ...BASE_BODY, group: "forensics" }),
      env,
    );
    const deniedBody = await j(denied);
    expect(deniedBody.decision).toBe("deny");
    expect(deniedBody.reason).toContain("'free'");

    const allowed = await worker.fetch(
      evalRequest({ ...BASE_BODY, group: "core" }),
      env,
    );
    expect((await j(allowed)).decision).toBe("allow");
  });

  it("caches the LS validation for 15 minutes (one LS call for two evaluates)", async () => {
    const fetchMock = vi.fn(async () =>
      Response.json(LS_ACTIVE_PRO),
    ) as unknown as typeof fetch;
    vi.stubGlobal("fetch", fetchMock);
    const env = makeEnv();

    await worker.fetch(evalRequest(BASE_BODY), env);
    await worker.fetch(evalRequest(BASE_BODY), env);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("rate limiting", () => {
  it("returns 429 with retry_after_seconds when the window is exhausted", async () => {
    mockLs(LS_ACTIVE_PRO);
    const env = makeEnv();
    const keyHash = await hashLicenseKey(TEST_KEY);
    const minute = Math.floor(Date.now() / 60_000);
    // Pre-fill the current bucket at the limit.
    await env.POLICY_KV.put(`rl:${keyHash}:${minute}`, "600");

    const res = await worker.fetch(evalRequest(BASE_BODY), env);
    expect(res.status).toBe(429);
    const body = await j(res);
    expect(body.code).toBe("rate_limited");
    expect(body.retry_after_seconds).toBeGreaterThanOrEqual(1);
    expect(typeof body.error).toBe("string");
  });
});

describe("unknown routes", () => {
  it("returns 404 with an error envelope", async () => {
    const env = makeEnv();
    const res = await worker.fetch(
      new Request("https://policy.test/v1/nope"),
      env,
    );
    expect(res.status).toBe(404);
    expect((await j(res)).code).toBe("not_found");
  });
});
