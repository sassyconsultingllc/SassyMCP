#!/bin/sh
# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# ══════════════════════════════════════════════════════════════
#  SassyMCP — Cloudflare Tunnel Mode. POSIX twin of start-tunnel.bat.
#
#  Prereqs:
#    1. cloudflared on PATH, tunnel already configured:
#         cloudflared tunnel login
#         cloudflared tunnel create <NAME>
#         cloudflared tunnel route dns <NAME> <hostname>
#    2. SASSYMCP_AUTH_TOKEN exported (16+ chars, [A-Za-z0-9_-])
#
#  Usage: ./start-tunnel.sh [TUNNEL_NAME]
#  (default: $SASSYMCP_TUNNEL_NAME, else "sassymcp")
#
#  Optional, for OAuth-proxy fronted deployments:
#    export SASSYMCP_RESOURCE_URL=https://your-oauth-proxy.example.com/mcp
#    export SASSYMCP_OAUTH_ISSUER=https://your-oauth-proxy.example.com
# ══════════════════════════════════════════════════════════════
set -eu
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/sassymcp-common.sh"

PORT=${PORT:-21001}
HOST=${HOST:-127.0.0.1}
SASSYMCP_LOAD_ALL=1; export SASSYMCP_LOAD_ALL

[ -n "${SASSYMCP_AUTH_TOKEN:-}" ] || die "SASSYMCP_AUTH_TOKEN not set. Add 'export SASSYMCP_AUTH_TOKEN=...' to your shell profile (~/.profile, ~/.zshrc, ...)."
token_ok "$SASSYMCP_AUTH_TOKEN" || die "SASSYMCP_AUTH_TOKEN must be 16+ chars of letters, digits, - and _ only."
have cloudflared || die "cloudflared not found on PATH. Install: $(install_hint cloudflared)  (or see https://pkg.cloudflare.com)"

TUNNEL_NAME=${1:-${SASSYMCP_TUNNEL_NAME:-sassymcp}}

kill_stale_on_port "$PORT"

LOGDIR=$(state_dir); mkdir -p "$LOGDIR"
SASSY_LOG="$LOGDIR/bridge.log"

say "=============================================================="
say " SassyMCP HTTP Bridge + Cloudflare Tunnel"
say "  Bind:    $HOST:$PORT"
say "  Tunnel:  $TUNNEL_NAME"
say "  Auth:    Bearer token (from SASSYMCP_AUTH_TOKEN)"
say "  Log:     $SASSY_LOG"
say "=============================================================="

BRIDGE_PID=$(spawn_sassymcp --http --host "$HOST" --port "$PORT")

cleanup() {
    say "[INFO] Stopping HTTP bridge (PID $BRIDGE_PID)..."
    kill "$BRIDGE_PID" 2>/dev/null || true
    kill_stale_on_port "$PORT"
}
trap cleanup EXIT INT TERM

# Foreground; Ctrl-C stops both.
cloudflared tunnel run "$TUNNEL_NAME"
