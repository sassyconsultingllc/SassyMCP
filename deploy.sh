#!/bin/sh
# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# ══════════════════════════════════════════════════════════════
#  SassyMCP Deploy (macOS / Linux / BSD) — portable package from dist/sassymcp.
#  POSIX twin of deploy.bat. Run ./build.sh first.
# ══════════════════════════════════════════════════════════════
set -eu
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/sassymcp-common.sh"

EXE="$SCRIPT_DIR/dist/sassymcp"
OUT="$SCRIPT_DIR/deploy-posix"
[ -x "$EXE" ] || die "dist/sassymcp not found. Run ./build.sh first."

say "[DEPLOY] Creating package at $OUT ..."
mkdir -p "$OUT"
cp -f "$EXE" "$OUT/sassymcp"
for f in sassymcp-common.sh start-local.sh start-lan.sh start-tunnel.sh \
         start-supervised.sh autostart-bridge.sh; do
    cp -f "$SCRIPT_DIR/$f" "$OUT/$f"
done
cp -f "$SCRIPT_DIR/deploy/start-beta.sh" "$OUT/start-beta.sh"
chmod 755 "$OUT/sassymcp" "$OUT"/*.sh
chmod 644 "$OUT/sassymcp-common.sh"

for t in claude_desktop cursor_mcp windsurf_mcp continue_mcp grok_desktop; do
    src="$SCRIPT_DIR/deploy/${t}_config.template.json"
    [ -f "$src" ] || src="$SCRIPT_DIR/deploy/${t}.template.json"
    [ -f "$src" ] && sed 's#REPLACE_WITH_PATH\\\\sassymcp.exe#REPLACE_WITH_PATH/sassymcp#g' "$src" >"$OUT/$(basename "$src")"
done

if [ "$(uname -s)" = Darwin ]; then
    CLAUDE_CFG="~/Library/Application Support/Claude/claude_desktop_config.json"
else
    CLAUDE_CFG="\${XDG_CONFIG_HOME:-~/.config}/Claude/claude_desktop_config.json"
fi

cat >"$OUT/README.txt" <<EOF
SassyMCP — Setup Guide (macOS / Linux / BSD)

QUICK START
  ./sassymcp --setup            start + flag the setup wizard
  ./sassymcp install            auto-register with detected MCP clients

TRANSPORT MODES
  Local (stdio):      ./start-local.sh      (or point your client at ./sassymcp)
                      Claude Desktop config: $CLAUDE_CFG
  HTTP (local/LAN):   ./start-lan.sh        (LAN requires an auth token)
  Supervised HTTP:    ./start-supervised.sh (auto-restart; 'sassymcp supervise stop')
  Cloudflare Tunnel:  ./start-tunnel.sh [TUNNEL_NAME]
  Start at login:     ./autostart-bridge.sh install   (systemd user unit / LaunchAgent)

OPTIONAL TOOLS (install with your package manager)
  OCR: tesseract   Android: adb, scrcpy   iOS: libimobiledevice
  Network: nmap    Window control (X11): wmctrl or xdotool, xrandr
  Clipboard: wl-clipboard (Wayland) or xclip/xsel (X11)
  Notifications: notify-send (libnotify)
  Or ask the AI to run: sassy_setup_tools(action="check")

ENVIRONMENT
  SASSYMCP_LOAD_ALL=1          load all tool modules
  SASSYMCP_GROUPS=core,github  load specific groups only
  SASSYMCP_AUTH_TOKEN=xxx      bearer token for HTTP auth (16+ chars [A-Za-z0-9_-])
  GITHUB_TOKEN=xxx             GitHub API access

FILES
  ~/.sassymcp/config.json  ~/.sassymcp/tokens.json  ~/.sassymcp/audit.log
EOF

say "[DEPLOY] Package ready:"
ls -la "$OUT"
