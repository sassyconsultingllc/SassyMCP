#!/bin/sh
# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# ══════════════════════════════════════════════════════════════
#  SassyMCP — HTTP Mode (localhost or LAN). POSIX twin of start-lan.bat.
#  For: Claude Desktop HTTP, Cursor, Windsurf, Grok Desktop
#
#  Non-interactive use: set BIND_ADDR / PORT (and SASSYMCP_AUTH_TOKEN for
#  LAN) in the environment, or pass --host/--port; prompts are skipped
#  when stdin is not a terminal.
# ══════════════════════════════════════════════════════════════
set -eu
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/sassymcp-common.sh"

SASSYMCP_LOAD_ALL=1; export SASSYMCP_LOAD_ALL

say "=============================================================="
say " SassyMCP - HTTP Mode"
say "=============================================================="

interactive=0; [ -t 0 ] && interactive=1

if [ -z "${BIND_ADDR:-}" ]; then
    BIND_ADDR=127.0.0.1
    if [ $interactive = 1 ]; then
        say " Choose bind address:"
        say "   1. 127.0.0.1 (localhost only - safest)"
        say "   2. 0.0.0.0   (LAN accessible - requires auth token)"
        printf '  Select [1]: '; read -r choice || choice=
        [ "$choice" = 2 ] && BIND_ADDR=0.0.0.0
    fi
fi

if [ -z "${PORT:-}" ]; then
    PORT=21001
    if [ $interactive = 1 ]; then
        printf '  Port [21001]: '; read -r p || p=
        [ -n "$p" ] && PORT=$p
    fi
fi
case "$PORT" in *[!0-9]*|"") die "invalid port: $PORT" ;; esac

if [ "$BIND_ADDR" != 127.0.0.1 ] && [ "$BIND_ADDR" != localhost ] && [ "$BIND_ADDR" != ::1 ]; then
    say ""
    say " [!] LAN mode requires an auth token for security."
    if [ -n "${SASSYMCP_AUTH_TOKEN:-}" ]; then
        token_ok "$SASSYMCP_AUTH_TOKEN" || die "SASSYMCP_AUTH_TOKEN must be 16+ chars of letters, digits, - and _ only."
        say " [OK] SASSYMCP_AUTH_TOKEN already set."
    else
        tok=
        if [ $interactive = 1 ]; then
            say " Enter a token or press Enter to auto-generate one"
            say " (letters, digits, - and _ only, at least 16 chars):"
            printf '  Token: '; read -r tok || tok=
        fi
        if [ -z "$tok" ]; then
            tok=$(gen_token)
            say " [OK] Generated a URL-safe token."
        fi
        token_ok "$tok" || die "Token rejected: use 16+ chars of letters, digits, - and _ only."
        SASSYMCP_AUTH_TOKEN=$tok; export SASSYMCP_AUTH_TOKEN
        say " [OK] Auth token set. Clients must send:"
        say "      Authorization: Bearer $SASSYMCP_AUTH_TOKEN"
    fi
elif [ -n "${SASSYMCP_AUTH_TOKEN:-}" ]; then
    say " [OK] Auth token active."
else
    say " [INFO] No auth token set. Localhost-only access."
fi

say ""
say " Starting on $BIND_ADDR:$PORT..."
say "=============================================================="

exec_sassymcp --http --host "$BIND_ADDR" --port "$PORT" "$@"
