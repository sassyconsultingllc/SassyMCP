#!/bin/sh
# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# ══════════════════════════════════════════════════════════════
#  SassyMCP autostart bridge — POSIX twin of autostart-bridge.bat.
#
#  Runs the supervised HTTP bridge on 127.0.0.1:21001 at login so a
#  separately managed tunnel (cloudflared service) has an origin.
#
#    ./autostart-bridge.sh install     register at login (per-user)
#    ./autostart-bridge.sh uninstall   remove it
#    ./autostart-bridge.sh status      show service state
#    ./autostart-bridge.sh run         run in the foreground (what the
#                                      service manager calls)
#
#  Linux (systemd): ~/.config/systemd/user/sassymcp-bridge.service
#  macOS:           ~/Library/LaunchAgents/com.sassyconsulting.sassymcp.plist
#  Other (BSD, OpenRC, runit, ...): prints an @reboot crontab line.
#
#  The auth token is read from an env file, never embedded in the unit:
#    Linux/BSD: ${XDG_CONFIG_HOME:-~/.config}/sassymcp/bridge.env
#    macOS:     ~/Library/Application Support/SassyMCP/bridge.env
#  containing e.g.  SASSYMCP_AUTH_TOKEN=...   (chmod 600)
# ══════════════════════════════════════════════════════════════
set -eu
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/sassymcp-common.sh"

HOST=${HOST:-127.0.0.1}
PORT=${PORT:-21001}
SELF="$SCRIPT_DIR/autostart-bridge.sh"
OS=$(uname -s)

if [ "$OS" = Darwin ]; then
    ENV_FILE="$HOME/Library/Application Support/SassyMCP/bridge.env"
else
    ENV_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/sassymcp/bridge.env"
fi

ensure_env_file() {
    mkdir -p "$(dirname "$ENV_FILE")"
    if [ ! -f "$ENV_FILE" ]; then
        umask 077
        {
            echo "# SassyMCP bridge environment (owner-only)."
            echo "SASSYMCP_AUTH_TOKEN=$(gen_token)"
            echo "SASSYMCP_LOAD_ALL=1"
        } >"$ENV_FILE"
        say "[OK] Created $ENV_FILE with a fresh token (mode 600)."
    fi
    chmod 600 "$ENV_FILE"
}

run_fg() {
    if [ -f "$ENV_FILE" ]; then
        set -a; . "$ENV_FILE"; set +a
    fi
    [ -n "${SASSYMCP_AUTH_TOKEN:-}" ] || die "SASSYMCP_AUTH_TOKEN not set (expected in $ENV_FILE)."
    SASSYMCP_LOAD_ALL=${SASSYMCP_LOAD_ALL:-1}; export SASSYMCP_LOAD_ALL
    # The source drive may mount a little after login (LUKS/VeraCrypt/NFS).
    waited=0
    while ! resolve_sassymcp; do
        [ $waited -ge 60 ] && die "SassyMCP not available after 60s."
        sleep 2; waited=$((waited + 2))
    done
    kill_stale_on_port "$PORT"
    exec_sassymcp supervise start --host "$HOST" --port "$PORT" --tunnel-mode none
}

install_systemd() {
    unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    mkdir -p "$unit_dir"
    cat >"$unit_dir/sassymcp-bridge.service" <<EOF
[Unit]
Description=SassyMCP HTTP bridge (supervised)
After=network-online.target

[Service]
Type=simple
ExecStart=/bin/sh "$SELF" run
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now sassymcp-bridge.service
    say "[OK] systemd user unit installed and started."
    say "     Logs:  journalctl --user -u sassymcp-bridge -f"
    say "     To keep it running while logged out: loginctl enable-linger \"\$USER\""
}

install_launchd() {
    plist="$HOME/Library/LaunchAgents/com.sassyconsulting.sassymcp.plist"
    logdir=$(state_dir); mkdir -p "$logdir" "$(dirname "$plist")"
    cat >"$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.sassyconsulting.sassymcp</string>
  <key>ProgramArguments</key>
  <array><string>/bin/sh</string><string>$SELF</string><string>run</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>StandardOutPath</key><string>$logdir/bridge.log</string>
  <key>StandardErrorPath</key><string>$logdir/bridge.log</string>
</dict>
</plist>
EOF
    launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$plist"
    say "[OK] LaunchAgent installed and loaded. Log: $logdir/bridge.log"
}

case "${1:-}" in
    run) run_fg ;;
    install)
        ensure_env_file
        if [ "$OS" = Darwin ]; then
            install_launchd
        elif have systemctl && systemctl --user show-environment >/dev/null 2>&1; then
            install_systemd
        else
            logdir=$(state_dir)
            say "No systemd user session found. Add this to 'crontab -e':"
            say "  @reboot /bin/sh \"$SELF\" run >>\"$logdir/bridge.log\" 2>&1"
        fi
        ;;
    uninstall)
        if [ "$OS" = Darwin ]; then
            plist="$HOME/Library/LaunchAgents/com.sassyconsulting.sassymcp.plist"
            launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
            rm -f "$plist"
        elif have systemctl; then
            systemctl --user disable --now sassymcp-bridge.service 2>/dev/null || true
            rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/sassymcp-bridge.service"
            systemctl --user daemon-reload 2>/dev/null || true
        fi
        say "[OK] Autostart removed (env file kept: $ENV_FILE)."
        ;;
    status)
        if [ "$OS" = Darwin ]; then
            launchctl print "gui/$(id -u)/com.sassyconsulting.sassymcp" 2>&1 | head -20
        elif have systemctl; then
            systemctl --user status sassymcp-bridge.service --no-pager 2>&1 | head -20 || true
        fi
        ;;
    *) say "usage: $0 install|uninstall|status|run"; exit 2 ;;
esac
