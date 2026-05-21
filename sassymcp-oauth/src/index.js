// SassyMCP OAuth Proxy Worker
//
// Sits between claude.ai (OAuth 2.1 client) and the SassyMCP bridge
// (static bearer token). Handles DCR, consent, PKCE, token issuance,
// and proxies /mcp calls with the upstream bearer injected.
//
// Secrets (set via `wrangler secret put`):
//   PRE_AUTH_SECRET      — pasted by user on consent screen
//   UPSTREAM_BEARER      — forwarded as the upstream Authorization header
//
// Vars (in wrangler.toml):
//   UPSTREAM_URL         — your SassyMCP bridge, e.g. https://mcp.<your-domain>.tld/mcp
//
// KV (binding OAUTH_KV):
//   client:<client_id>   — {client_id, client_secret?, redirect_uris[]}
//   code:<auth_code>     — {client_id, redirect_uri, code_challenge, expires_at}
//   token:<access_token> — {client_id, issued_at, expires_at}

const JSON_HEADERS = { "Content-Type": "application/json" };
const CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, mcp-protocol-version",
    "Access-Control-Max-Age": "86400",
};

// --- Crypto helpers ----------------------------------------------
function b64url(bytes) {
    return btoa(String.fromCharCode(...new Uint8Array(bytes)))
        .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function randomToken(n = 32) {
    const b = new Uint8Array(n);
    crypto.getRandomValues(b);
    return b64url(b);
}
async function sha256(s) {
    const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
    return b64url(buf);
}

// --- JSON responses ----------------------------------------------
function jsonResponse(obj, status = 200, extraHeaders = {}) {
    return new Response(JSON.stringify(obj), {
        status,
        headers: { ...JSON_HEADERS, ...CORS_HEADERS, ...extraHeaders },
    });
}
function errorJson(error, description, status = 400) {
    return jsonResponse({ error, error_description: description }, status);
}

// --- Handlers ----------------------------------------------------

// GET /.well-known/oauth-authorization-server
function wellKnownAuthServer(origin) {
    return jsonResponse({
        issuer: origin,
        authorization_endpoint: `${origin}/authorize`,
        token_endpoint: `${origin}/token`,
        registration_endpoint: `${origin}/register`,
        response_types_supported: ["code"],
        grant_types_supported: ["authorization_code", "refresh_token"],
        code_challenge_methods_supported: ["S256"],
        token_endpoint_auth_methods_supported: ["none", "client_secret_post"],
        scopes_supported: ["mcp"],
    });
}

// GET /.well-known/oauth-protected-resource
function wellKnownProtectedResource(origin) {
    return jsonResponse({
        resource: `${origin}/mcp`,
        authorization_servers: [origin],
        bearer_methods_supported: ["header"],
        scopes_supported: ["mcp"],
    });
}

// POST /register  — Dynamic Client Registration (RFC 7591)
async function handleRegister(request, env) {
    if (request.method !== "POST") return errorJson("invalid_request", "POST required", 405);
    let body;
    try { body = await request.json(); }
    catch { return errorJson("invalid_request", "Invalid JSON", 400); }

    const redirect_uris = Array.isArray(body.redirect_uris) ? body.redirect_uris : [];
    if (redirect_uris.length === 0) {
        return errorJson("invalid_redirect_uri", "redirect_uris required", 400);
    }
    for (const uri of redirect_uris) {
        try {
            const u = new URL(uri);
            if (u.protocol !== "https:" && u.hostname !== "localhost" && u.hostname !== "127.0.0.1") {
                return errorJson("invalid_redirect_uri", `non-https redirect: ${uri}`, 400);
            }
        } catch {
            return errorJson("invalid_redirect_uri", `malformed: ${uri}`, 400);
        }
    }

    const client_id = `c_${randomToken(16)}`;
    const record = {
        client_id,
        client_name: body.client_name || "unknown",
        redirect_uris,
        token_endpoint_auth_method: body.token_endpoint_auth_method || "none",
        grant_types: body.grant_types || ["authorization_code", "refresh_token"],
        response_types: body.response_types || ["code"],
        created_at: Date.now(),
    };
    await env.OAUTH_KV.put(`client:${client_id}`, JSON.stringify(record));

    return jsonResponse({
        client_id,
        client_id_issued_at: Math.floor(record.created_at / 1000),
        redirect_uris,
        token_endpoint_auth_method: record.token_endpoint_auth_method,
        grant_types: record.grant_types,
        response_types: record.response_types,
    }, 201);
}

// GET /authorize — render consent; POST /authorize — complete flow
async function handleAuthorize(request, env, origin) {
    const url = new URL(request.url);
    const params = request.method === "POST"
        ? await request.formData()
        : url.searchParams;

    const client_id = params.get("client_id");
    const redirect_uri = params.get("redirect_uri");
    const response_type = params.get("response_type");
    const state = params.get("state") || "";
    const code_challenge = params.get("code_challenge");
    const code_challenge_method = params.get("code_challenge_method");
    const scope = params.get("scope") || "mcp";

    if (!client_id || !redirect_uri || response_type !== "code") {
        return errorJson("invalid_request", "Missing client_id, redirect_uri, or response_type=code", 400);
    }
    if (code_challenge_method !== "S256" || !code_challenge) {
        return errorJson("invalid_request", "PKCE S256 required", 400);
    }

    const client = await env.OAUTH_KV.get(`client:${client_id}`, "json");
    if (!client) return errorJson("invalid_client", "Unknown client_id", 400);
    if (!client.redirect_uris.includes(redirect_uri)) {
        return errorJson("invalid_redirect_uri", "redirect_uri not registered for this client", 400);
    }

    if (request.method === "GET") {
        return renderConsent({ client, redirect_uri, state, code_challenge, scope, origin });
    }

    // POST = submission of consent form
    const submitted = params.get("pre_auth_secret") || "";
    const expected = env.PRE_AUTH_SECRET || "";
    // SHA-256 constant-time compare. Don't short-circuit on length —
    // that's the leak the new timingSafeEqual is designed to remove.
    // The only fast-path is "expected is empty", which is a server
    // misconfig rather than a credential test.
    const ok = expected.length > 0 && submitted.length > 0
        && await timingSafeEqual(submitted, expected);
    if (!ok) {
        return renderConsent({ client, redirect_uri, state, code_challenge, scope, origin,
            error: "Incorrect pre-auth secret." });
    }

    const code = randomToken(32);
    const codeRecord = {
        client_id,
        redirect_uri,
        code_challenge,
        scope,
        expires_at: Date.now() + 60_000, // 60s
    };
    await env.OAUTH_KV.put(`code:${code}`, JSON.stringify(codeRecord), { expirationTtl: 120 });

    const redirect = new URL(redirect_uri);
    redirect.searchParams.set("code", code);
    if (state) redirect.searchParams.set("state", state);
    return Response.redirect(redirect.toString(), 302);
}

// SHA-256 hash compare — defeats both length-leak and JIT timing leaks
// the original constant-time-style JS loop was vulnerable to.
//
// The old implementation had two issues:
//   1. The early-return on length-mismatch revealed the expected length
//      (an attacker could measure response time across guesses).
//   2. A byte-by-byte JS loop with String.charCodeAt is constant-time
//      at the source level only; V8 can add early exits during JIT,
//      and charCodeAt in a hot loop is not a primitive the engine
//      treats as constant-time.
//
// crypto.subtle.digest runs in native runtime code over the full input
// regardless of content; comparing equal-length SHA-256 digests with a
// constant-time byte loop on Uint8Array removes both leaks.
async function timingSafeEqual(a, b) {
    const enc = new TextEncoder();
    const [ah, bh] = await Promise.all([
        crypto.subtle.digest("SHA-256", enc.encode(a)),
        crypto.subtle.digest("SHA-256", enc.encode(b)),
    ]);
    const av = new Uint8Array(ah);
    const bv = new Uint8Array(bh);
    let diff = 0;
    for (let i = 0; i < av.length; i++) diff |= av[i] ^ bv[i];
    return diff === 0;
}

function renderConsent({ client, redirect_uri, state, code_challenge, scope, origin, error }) {
    const safeClient = escapeHtml(client.client_name);
    const safeRedirect = escapeHtml(redirect_uri);
    const errorHtml = error ? `<p class="err">${escapeHtml(error)}</p>` : "";
    const body = `<!doctype html>
<html><head><meta charset="utf-8">
<title>Authorize ${safeClient} — SassyMCP</title>
<style>
  body { font-family: system-ui, -apple-system, Segoe UI, sans-serif; background: #0b0d10; color: #e6e6e6; max-width: 520px; margin: 60px auto; padding: 0 20px; }
  h1 { font-size: 22px; margin-bottom: 4px; }
  .sub { color: #888; margin-bottom: 28px; }
  .card { background: #15181c; border: 1px solid #2a2f36; border-radius: 8px; padding: 22px; }
  label { display: block; font-size: 13px; color: #a9b1bb; margin-bottom: 6px; }
  input[type=password] { width: 100%; padding: 10px 12px; border: 1px solid #2a2f36; border-radius: 6px; background: #0b0d10; color: #e6e6e6; font-size: 14px; box-sizing: border-box; }
  button { margin-top: 16px; padding: 10px 18px; background: #3b82f6; color: white; border: 0; border-radius: 6px; font-size: 14px; cursor: pointer; }
  button:hover { background: #2563eb; }
  .err { color: #f87171; font-size: 13px; margin: 10px 0; }
  .meta { font-size: 12px; color: #666; margin-top: 14px; word-break: break-all; }
  code { background: #1f242b; padding: 2px 6px; border-radius: 3px; font-size: 12px; }
</style>
</head><body>
<h1>Authorize connection</h1>
<p class="sub">Client <strong>${safeClient}</strong> wants to access SassyMCP.</p>
<div class="card">
  <form method="post" action="/authorize">
    <input type="hidden" name="client_id" value="${escapeHtml(client.client_id)}">
    <input type="hidden" name="redirect_uri" value="${safeRedirect}">
    <input type="hidden" name="response_type" value="code">
    <input type="hidden" name="state" value="${escapeHtml(state)}">
    <input type="hidden" name="code_challenge" value="${escapeHtml(code_challenge)}">
    <input type="hidden" name="code_challenge_method" value="S256">
    <input type="hidden" name="scope" value="${escapeHtml(scope)}">
    ${errorHtml}
    <label for="pre_auth_secret">Pre-auth secret</label>
    <input id="pre_auth_secret" name="pre_auth_secret" type="password" autofocus autocomplete="off" required>
    <button type="submit">Authorize</button>
  </form>
  <div class="meta">Redirect: <code>${safeRedirect}</code></div>
</div>
</body></html>`;
    return new Response(body, {
        status: error ? 400 : 200,
        headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
    });
}

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
        "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
    }[c]));
}

// POST /token
async function handleToken(request, env) {
    if (request.method !== "POST") return errorJson("invalid_request", "POST required", 405);
    const form = await request.formData();
    const grant_type = form.get("grant_type");

    if (grant_type === "authorization_code") {
        return await exchangeAuthCode(form, env);
    }
    if (grant_type === "refresh_token") {
        return await refreshAccessToken(form, env);
    }
    return errorJson("unsupported_grant_type", `grant_type=${grant_type}`, 400);
}

async function exchangeAuthCode(form, env) {
    const code = form.get("code");
    const client_id = form.get("client_id");
    const redirect_uri = form.get("redirect_uri");
    const code_verifier = form.get("code_verifier");

    if (!code || !client_id || !redirect_uri || !code_verifier) {
        return errorJson("invalid_request", "Missing required params");
    }

    const record = await env.OAUTH_KV.get(`code:${code}`, "json");
    if (!record) return errorJson("invalid_grant", "Unknown or expired code");
    await env.OAUTH_KV.delete(`code:${code}`); // single-use

    if (record.expires_at < Date.now()) return errorJson("invalid_grant", "Code expired");
    if (record.client_id !== client_id) return errorJson("invalid_grant", "client_id mismatch");
    if (record.redirect_uri !== redirect_uri) return errorJson("invalid_grant", "redirect_uri mismatch");

    const verifierHash = await sha256(code_verifier);
    if (verifierHash !== record.code_challenge) {
        return errorJson("invalid_grant", "PKCE verifier mismatch");
    }

    return await issueTokenPair(client_id, record.scope, env);
}

async function refreshAccessToken(form, env) {
    const refresh_token = form.get("refresh_token");
    const client_id = form.get("client_id");
    if (!refresh_token) return errorJson("invalid_request", "refresh_token required");

    const record = await env.OAUTH_KV.get(`refresh:${refresh_token}`, "json");
    if (!record) return errorJson("invalid_grant", "Unknown refresh_token");
    if (client_id && record.client_id !== client_id) {
        return errorJson("invalid_grant", "client_id mismatch");
    }

    // Rotate: invalidate old refresh token, issue new pair
    await env.OAUTH_KV.delete(`refresh:${refresh_token}`);
    return await issueTokenPair(record.client_id, record.scope, env);
}

async function issueTokenPair(client_id, scope, env) {
    const access_token = randomToken(32);
    const refresh_token = randomToken(32);
    const expiresInSec = 3600;
    const now = Date.now();

    await env.OAUTH_KV.put(
        `token:${access_token}`,
        JSON.stringify({ client_id, scope, issued_at: now, expires_at: now + expiresInSec * 1000 }),
        { expirationTtl: expiresInSec + 60 }
    );
    await env.OAUTH_KV.put(
        `refresh:${refresh_token}`,
        JSON.stringify({ client_id, scope, issued_at: now }),
        { expirationTtl: 30 * 24 * 3600 } // 30 days
    );

    return jsonResponse({
        access_token,
        token_type: "Bearer",
        expires_in: expiresInSec,
        refresh_token,
        scope,
    }, 200, { "Cache-Control": "no-store", "Pragma": "no-cache" });
}

// ANY /mcp — verify token, proxy to upstream with injected bearer
async function handleMcpProxy(request, env, origin) {
    const auth = request.headers.get("Authorization") || "";
    const match = auth.match(/^Bearer\s+(.+)$/i);
    if (!match) {
        return new Response(JSON.stringify({ error: "unauthorized" }), {
            status: 401,
            headers: {
                ...JSON_HEADERS, ...CORS_HEADERS,
                "WWW-Authenticate": `Bearer resource_metadata="${origin}/.well-known/oauth-protected-resource"`,
            },
        });
    }
    const token = match[1];
    const record = await env.OAUTH_KV.get(`token:${token}`, "json");
    if (!record) {
        return new Response(JSON.stringify({ error: "invalid_token" }), {
            status: 401,
            headers: {
                ...JSON_HEADERS, ...CORS_HEADERS,
                "WWW-Authenticate": `Bearer error="invalid_token", resource_metadata="${origin}/.well-known/oauth-protected-resource"`,
            },
        });
    }
    if (record.expires_at < Date.now()) {
        await env.OAUTH_KV.delete(`token:${token}`);
        return new Response(JSON.stringify({ error: "invalid_token", error_description: "expired" }), {
            status: 401,
            headers: {
                ...JSON_HEADERS, ...CORS_HEADERS,
                "WWW-Authenticate": `Bearer error="invalid_token", error_description="expired"`,
            },
        });
    }

    // Build upstream request
    const upstreamHeaders = new Headers(request.headers);
    upstreamHeaders.set("Authorization", `Bearer ${env.UPSTREAM_BEARER}`);
    upstreamHeaders.delete("host");
    upstreamHeaders.delete("cf-connecting-ip");
    upstreamHeaders.delete("cf-ipcountry");
    upstreamHeaders.delete("cf-ray");
    upstreamHeaders.delete("cf-visitor");

    const upstream = await fetch(env.UPSTREAM_URL, {
        method: request.method,
        headers: upstreamHeaders,
        body: ["GET","HEAD"].includes(request.method) ? undefined : request.body,
    });

    const respHeaders = new Headers(upstream.headers);
    for (const [k, v] of Object.entries(CORS_HEADERS)) respHeaders.set(k, v);

    return new Response(upstream.body, {
        status: upstream.status,
        statusText: upstream.statusText,
        headers: respHeaders,
    });
}

// --- Router ------------------------------------------------------
export default {
    async fetch(request, env, ctx) {
        const url = new URL(request.url);
        const origin = `${url.protocol}//${url.host}`;

        if (request.method === "OPTIONS") {
            return new Response(null, { status: 204, headers: CORS_HEADERS });
        }

        // Well-known metadata
        if (url.pathname === "/.well-known/oauth-authorization-server") {
            return wellKnownAuthServer(origin);
        }
        if (url.pathname === "/.well-known/oauth-protected-resource" ||
            url.pathname === "/.well-known/oauth-protected-resource/mcp") {
            return wellKnownProtectedResource(origin);
        }

        // OAuth endpoints
        if (url.pathname === "/register")   return handleRegister(request, env);
        if (url.pathname === "/authorize")  return handleAuthorize(request, env, origin);
        if (url.pathname === "/token")      return handleToken(request, env);

        // MCP proxy (both /mcp and /mcp/ and /mcp/anything)
        if (url.pathname === "/mcp" || url.pathname.startsWith("/mcp/")) {
            return handleMcpProxy(request, env, origin);
        }

        // Health
        if (url.pathname === "/" || url.pathname === "/health") {
            return new Response("sassymcp-oauth ok", {
                status: 200, headers: { "Content-Type": "text/plain" },
            });
        }

        return new Response("Not Found", { status: 404 });
    }
};