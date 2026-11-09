#!/bin/sh
# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# ══════════════════════════════════════════════════════════════
#  SassyMCP — Supervised mode (recommended). POSIX twin of start-supervised.bat.
#
#  Runs the HTTP bridge under `sassymcp supervise`, which restarts it with
#  backoff on crash/hang and takes the whole process tree down when the
#  supervisor exits (POSIX keeper process; see sassymcp/_jobctl.py).
#
#  Tunnel:
#    SASSYMCP_TUNNEL_MODE=none     (default) bridge only
#    SASSYMCP_TUNNEL_MODE=managed  also run `cloudflared tunnel run <name>`
#                                  (set SASSYMCP_TUNNEL_NAME)
#
#  Stop cleanly from another terminal:  sassymcp supervise stop
# ══════════════════════════════════════════════════════════════
set -eu
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/sassymcp-common.sh"

HOST=${HOST:-127.0.0.1}
PORT=${PORT:-21001}
TUNNEL_MODE=${SASSYMCP_TUNNEL_MODE:-none}

say "=============================================================="
say " SassyMCP - Supervised"
say "  Bridge:  $HOST:$PORT"
say "  Tunnel:  $TUNNEL_MODE"
say "=============================================================="

exec_sassymcp supervise start --host "$HOST" --port "$PORT" --tunnel-mode "$TUNNEL_MODE" "$@"
