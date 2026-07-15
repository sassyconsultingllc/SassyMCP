<!--
   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
   CodeMark: SCLLC1-SassyMCP-NZ4XLWNGDXJA
-->
# Cloudflare Tunnel setup for SassyMCP

This guide takes a fresh install of SassyMCP from "localhost only" to
"reachable from claude.ai (or any other remote MCP client) over a
Cloudflare Tunnel." It assumes nothing vendor-specific — substitute your
own domain wherever a placeholder like `<your-domain>.tld` appears.

The shipped `start-tunnel.bat` is intentionally generic: it accepts the
tunnel name as `%1` or `%SASSYMCP_TUNNEL_NAME%`, and it does not embed
any hostname. The only thing you must configure outside the script is
the Cloudflare side — DNS route + named tunnel — and the SassyMCP-side
`SASSYMCP_ALLOWED_HOSTS` env var so the bridge stops rejecting your
hostname with HTTP 421.

## Prerequisites

1. A Cloudflare account with a domain you own (the apex zone must be on
   Cloudflare's nameservers).
2. `cloudflared` on PATH. Windows: `winget install Cloudflare.cloudflared`.
3. `sassymcp.exe` extracted from the portable zip, or installed via the
   DXT / VSIX / `sassymcp-install` paths. The tunnel launcher expects
   the exe next to it (`%~dp0sassymcp.exe`).
4. A SassyMCP auth token. Generate one with:
   ```powershell
   $b = New-Object byte[] 32
   (New-Object System.Security.Cryptography.RNGCryptoServiceProvider).GetBytes($b)
   $token = [Convert]::ToBase64String($b).Replace('+','-').Replace('/','_').TrimEnd('=')
   [Environment]::SetEnvironmentVariable("SASSYMCP_AUTH_TOKEN", $token, "User")
   ```
   Open a new shell so `SASSYMCP_AUTH_TOKEN` is in scope.

## Step 1 — Authenticate cloudflared

```powershell
cloudflared tunnel login
```

This opens a browser; pick the zone (domain) you want to host the MCP
hostname under. cloudflared drops a cert at `%USERPROFILE%\.cloudflared\cert.pem`.

## Step 2 — Create a named tunnel

Pick a short name. The default `start-tunnel.bat` looks for one called
`sassymcp`; use any name you prefer and pass it as the first argument.

```powershell
cloudflared tunnel create sassymcp
```

cloudflared writes a credentials file at
`%USERPROFILE%\.cloudflared\<UUID>.json` and prints the tunnel UUID.

## Step 3 — Route a hostname to the tunnel

```powershell
cloudflared tunnel route dns sassymcp mcp.<your-domain>.tld
```

This creates a `CNAME mcp` record on the chosen zone pointing at the
tunnel. Propagation is immediate on Cloudflare's network.

## Step 4 — Write the cloudflared config

Create `%USERPROFILE%\.cloudflared\config.yml`:

```yaml
tunnel: sassymcp
credentials-file: C:\Users\<you>\.cloudflared\<UUID>.json

ingress:
  - hostname: mcp.<your-domain>.tld
    service: http://127.0.0.1:21001
  - service: http_status:404
```

The `service` URL is the local SassyMCP HTTP bridge. `start-tunnel.bat`
launches the bridge at `127.0.0.1:21001` before invoking cloudflared, so
keep them in sync (override the bridge port by editing `set PORT=` near
the top of `start-tunnel.bat` if you need a different one).

## Step 5 — Tell SassyMCP your hostname is allowed

The bridge ships with DNS-rebinding protection on by default. The
default allowlist is loopback only (`localhost,127.0.0.1`), which means
any request arriving with `Host: mcp.<your-domain>.tld` is rejected
with HTTP 421 — exactly what you want before you've configured a real
hostname, exactly *not* what you want now.

Set the env var at User scope so it persists across reboots:

```powershell
[Environment]::SetEnvironmentVariable(
    "SASSYMCP_ALLOWED_HOSTS",
    "mcp.<your-domain>.tld,localhost,127.0.0.1",
    "User"
)
```

Comma-separated. Order doesn't matter. Open a new shell so the bridge
inherits the new value.

## Step 6 — Run it

```powershell
.\start-tunnel.bat sassymcp
```

(Or set `SASSYMCP_TUNNEL_NAME=sassymcp` and run it with no args.)

`start-tunnel.bat` will:
1. Kill anything already listening on :21001.
2. Launch `sassymcp.exe --http --host 127.0.0.1 --port 21001` in the
   background.
3. Run `cloudflared tunnel run sassymcp` in the foreground (Ctrl-C to
   stop both).

## Step 7 — Verify

From any machine with internet access:

```powershell
$token = "<your SASSYMCP_AUTH_TOKEN value>"
$body = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoketest","version":"1"}}}'
curl.exe -sS -X POST `
    -H "Authorization: Bearer $token" `
    -H "Content-Type: application/json" `
    -H "Accept: application/json, text/event-stream" `
    --data $body `
    "https://mcp.<your-domain>.tld/mcp"
```

You should get the MCP `initialize` response in JSON or SSE form, not a
`421 Misdirected Request` or `403 Forbidden`. If you get 421, your
`SASSYMCP_ALLOWED_HOSTS` is wrong (or the bridge process predates your
last env-var change — restart it). If you get 403, your bearer token
doesn't match what the bridge expects — re-read it from
`$env:SASSYMCP_AUTH_TOKEN` and try again.

`scripts/smoke-test.ps1 -RemoteUrl https://mcp.<your-domain>.tld/mcp`
does the same call wrapped in a one-shot script.

## Optional — OAuth proxy in front of the tunnel

The bare-bones setup above issues a static bearer token that *you* paste
into each client. That works for personal use; it doesn't work for
clients (like claude.ai's hosted Connectors UI) that require an OAuth
2.1 dynamic-client-registration flow.

For the hosted-Claude case, deploy `sassymcp-oauth/` — a Cloudflare
Worker that handles DCR, consent, PKCE, and token issuance, then
proxies authenticated `/mcp` calls to your tunnel hostname with the
static `UPSTREAM_BEARER` injected.

1. `cp sassymcp-oauth/wrangler.toml.example sassymcp-oauth/wrangler.toml`
2. Edit `wrangler.toml` — set the `pattern` to your OAuth hostname
   (e.g. `mcp-oauth.<your-domain>.tld`), set `UPSTREAM_URL` to your
   tunnel hostname (e.g. `https://mcp.<your-domain>.tld/mcp`), and fill
   in the KV namespace id from
   `wrangler kv:namespace create OAUTH_KV`.
3. Set secrets:
   ```powershell
   wrangler secret put PRE_AUTH_SECRET   # the consent-screen password
   wrangler secret put UPSTREAM_BEARER   # same token as SASSYMCP_AUTH_TOKEN
   ```
4. `wrangler deploy`.
5. In claude.ai (or any OAuth-aware MCP client), point the connector at
   `https://mcp-oauth.<your-domain>.tld/mcp` — the Worker handles the
   handshake and forwards to your bridge.

The `mcp-oauth.<your-domain>.tld` host is a Worker route on Cloudflare,
so you do *not* run a second tunnel for it.

## Quick troubleshooting

- **`421 Misdirected Request`** — `SASSYMCP_ALLOWED_HOSTS` doesn't list
  the `Host:` header value the client sent. Add it and restart the
  bridge.
- **`403 Forbidden`** — bearer token mismatch. The bridge reads
  `SASSYMCP_AUTH_TOKEN` from its own env at startup; the client must
  send the same string in the `Authorization: Bearer` header.
- **`502 Bad Gateway`** — cloudflared is up but the bridge isn't.
  Check `netstat -ano | findstr :21001`; if it's empty, the
  `start "sassymcp-bridge"` step failed (usually
  `SASSYMCP_AUTH_TOKEN` was missing).
- **`Connection refused` from the public hostname** — cloudflared
  didn't pick up the config. `cloudflared tunnel info sassymcp` and
  `cloudflared tunnel list` will tell you whether the tunnel is
  registered and what hostnames are routed to it.
- **Tunnel works locally but not from another network** — DNS hasn't
  propagated, or you're hitting your ISP's resolver cache. Try
  `nslookup mcp.<your-domain>.tld 1.1.1.1` to bypass.
