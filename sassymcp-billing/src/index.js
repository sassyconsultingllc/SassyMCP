// SassyMCP Billing Worker — LemonSqueezy webhook receiver + revocation oracle.
//
// SassyMCP is sold as a one-time perpetual license. LS calls
// POST /lemonsqueezy/webhook on order_created, order_refunded,
// license_key_created, license_key_updated. We verify the X-Signature
// HMAC, classify the event, and either set or clear a revocation
// record in KV keyed by the SHA-256 of the license key string. The
// raw license key never lands in KV or logs.
//
// The classifier still recognizes subscription_* events generically
// (driven by env lists) so the same Worker can be repurposed if the
// pricing model ever changes — but the deployed env defaults only
// subscribe to the four one-time events listed above.
//
// SassyMCP installs call GET /lemonsqueezy/check/:hash to learn whether their
// own key has been revoked. The lookup is cached at the edge (60s) so a million
// installs polling hourly cost roughly one origin hit per minute per unique key.
//
// Secrets (set via `wrangler secret put`):
//   LS_WEBHOOK_SECRET — matches the value configured in the LS webhook UI
//
// Vars (in wrangler.toml):
//   UNREVOKE_EVENTS — comma-separated event_name list that clears revocations
//   REVOKE_EVENTS   — comma-separated event_name list that sets revocations
//
// KV (binding BILLING_KV):
//   revoke:<sha256hex> — JSON { status, reason, revoked_at, license_key_id }
//
// Public surface:
//   GET  /                              — health check
//   POST /lemonsqueezy/webhook          — LS-only, signature-verified
//   GET  /lemonsqueezy/check/:hash      — public, edge-cached

const JSON_HEADERS = { "Content-Type": "application/json" };
const CACHE_HEADERS = { "Cache-Control": "public, max-age=60" };

function jsonResponse(obj, status = 200, extra = {}) {
    return new Response(JSON.stringify(obj), {
        status,
        headers: { ...JSON_HEADERS, ...extra },
    });
}

function errorJson(error, description, status = 400) {
    return jsonResponse({ error, error_description: description }, status);
}

// ── Crypto helpers ───────────────────────────────────────────────────

function hexFromBuf(buf) {
    return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function hmacSha256Hex(secret, message) {
    const key = await crypto.subtle.importKey(
        "raw",
        new TextEncoder().encode(secret),
        { name: "HMAC", hash: "SHA-256" },
        false,
        ["sign"],
    );
    const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message));
    return hexFromBuf(sig);
}

async function sha256Hex(s) {
    const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
    return hexFromBuf(buf);
}

function timingSafeEqualHex(a, b) {
    // Inputs are hex strings of identical length when both come from
    // SHA-256 HMAC, but we still defend against length mismatch first
    // so an attacker can't time-distinguish "wrong length" from
    // "right length, wrong bits".
    if (typeof a !== "string" || typeof b !== "string") return false;
    if (a.length !== b.length) return false;
    let diff = 0;
    for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
    return diff === 0;
}

// ── LS event classification ──────────────────────────────────────────

// Returns "revoke" | "unrevoke" | "ignore" for a parsed LS event body.
//
// `license_key_updated` is special: the action depends on the inner
// `status` attribute LS sends with the new state. Everything else
// matches against the configured REVOKE_EVENTS / UNREVOKE_EVENTS lists,
// which keeps deployment-time tuning possible without code changes.
function classifyEvent(event, env) {
    const name = event?.meta?.event_name;
    if (!name) return "ignore";

    if (name === "license_key_updated") {
        const status = event?.data?.attributes?.status;
        // LS license statuses: active | inactive | expired | disabled
        if (status === "active") return "unrevoke";
        if (status) return "revoke";
        return "ignore";
    }

    const revokeSet = new Set((env.REVOKE_EVENTS || "").split(",").map((s) => s.trim()).filter(Boolean));
    const unrevokeSet = new Set((env.UNREVOKE_EVENTS || "").split(",").map((s) => s.trim()).filter(Boolean));
    if (revokeSet.has(name)) return "revoke";
    if (unrevokeSet.has(name)) return "unrevoke";
    return "ignore";
}

// LS puts the license key string in different places depending on the
// event family. license_key_* events have it at data.attributes.key;
// subscription_* events have a license_keys array hanging off the
// subscription, and order_* events have it nested under the order's
// first_subscription_item or directly on the order's license keys.
// This helper checks every known location and returns the first hit.
function extractLicenseKey(event) {
    const a = event?.data?.attributes || {};
    if (a.key && typeof a.key === "string") return a.key;
    if (a.license_key && typeof a.license_key === "string") return a.license_key;
    const lks = a.license_keys;
    if (Array.isArray(lks) && lks[0]?.attributes?.key) return lks[0].attributes.key;
    const custom = event?.meta?.custom_data || {};
    if (custom.license_key && typeof custom.license_key === "string") return custom.license_key;
    return null;
}

function extractLicenseKeyId(event) {
    const a = event?.data?.attributes || {};
    if (a.license_key_id) return a.license_key_id;
    if (event?.data?.type === "license-keys") return event?.data?.id;
    return null;
}

// ── Handlers ─────────────────────────────────────────────────────────

async function handleWebhook(request, env, ctx) {
    if (request.method !== "POST") {
        return errorJson("invalid_request", "POST required", 405);
    }
    if (!env.LS_WEBHOOK_SECRET) {
        // Misconfiguration: refuse loud rather than silently accepting
        // unauthenticated webhooks.
        return errorJson("server_misconfigured", "LS_WEBHOOK_SECRET not set", 500);
    }

    // Read the raw body once for signature verification AND parsing —
    // re-reading via request.json() after request.text() throws.
    const raw = await request.text();
    const sig = request.headers.get("X-Signature") || "";
    const expected = await hmacSha256Hex(env.LS_WEBHOOK_SECRET, raw);
    if (!timingSafeEqualHex(sig, expected)) {
        return errorJson("invalid_signature", "X-Signature mismatch", 401);
    }

    let event;
    try {
        event = JSON.parse(raw);
    } catch {
        return errorJson("invalid_body", "body is not JSON", 400);
    }

    const action = classifyEvent(event, env);
    const licenseKey = extractLicenseKey(event);
    const licenseKeyId = extractLicenseKeyId(event);
    const eventName = event?.meta?.event_name || "unknown";

    if (action === "ignore" || !licenseKey) {
        return jsonResponse({
            received: true,
            action,
            event: eventName,
            note: action === "ignore" ? "event not in revoke/unrevoke set" : "no license_key found in payload",
        });
    }

    const hash = await sha256Hex(licenseKey);
    const kvKey = `revoke:${hash}`;

    if (action === "revoke") {
        await env.BILLING_KV.put(
            kvKey,
            JSON.stringify({
                status: "revoked",
                reason: eventName,
                revoked_at: Date.now(),
                license_key_id: licenseKeyId,
            }),
        );
    } else if (action === "unrevoke") {
        await env.BILLING_KV.delete(kvKey);
    }

    return jsonResponse({ received: true, action, event: eventName });
}

async function handleCheck(request, env, hash) {
    if (request.method !== "GET") {
        return errorJson("invalid_request", "GET required", 405);
    }
    if (!/^[a-f0-9]{64}$/.test(hash)) {
        return errorJson("invalid_hash", "hash must be SHA-256 hex (64 lowercase hex chars)", 400);
    }
    const record = await env.BILLING_KV.get(`revoke:${hash}`, "json");
    if (record) {
        return jsonResponse({ status: "revoked", ...record }, 200, CACHE_HEADERS);
    }
    // Default-trust: unknown hash means we never received a revocation
    // for this key. The local SassyMCP install still does a weekly
    // authoritative LS validate, so an attacker can't bypass policy by
    // submitting an unminted key — they can only avoid the fast-path
    // hint, which delays revocation by at most one week.
    return jsonResponse({ status: "active" }, 200, CACHE_HEADERS);
}

function handleHealth() {
    return jsonResponse({
        service: "sassymcp-billing",
        endpoints: [
            "POST /lemonsqueezy/webhook",
            "GET /lemonsqueezy/check/{sha256hex}",
        ],
    });
}

// ── Entrypoint ───────────────────────────────────────────────────────

export default {
    async fetch(request, env, ctx) {
        const url = new URL(request.url);
        const path = url.pathname.replace(/\/+$/, "") || "/";

        if (path === "/") return handleHealth();
        if (path === "/lemonsqueezy/webhook") return handleWebhook(request, env, ctx);
        if (path.startsWith("/lemonsqueezy/check/")) {
            const hash = path.substring("/lemonsqueezy/check/".length);
            return handleCheck(request, env, hash);
        }
        return errorJson("not_found", path, 404);
    },
};
