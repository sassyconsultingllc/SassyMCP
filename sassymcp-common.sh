#!/bin/sh
# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# ══════════════════════════════════════════════════════════════
#  Shared helpers for the POSIX launchers (start-*.sh, autostart-bridge.sh).
#  Sourced, not executed. Plain POSIX sh: works under bash, dash, zsh, ksh,
#  busybox ash, FreeBSD/OpenBSD sh and macOS /bin/sh. No GNU-only flags.
# ══════════════════════════════════════════════════════════════

# Directory of the script that sourced us (set by the caller as SCRIPT_DIR).
: "${SCRIPT_DIR:?SCRIPT_DIR must be set before sourcing sassymcp-common.sh}"

say()  { printf '%s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
die()  { printf '[ERROR] %s\n' "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

# Resolve how to run SassyMCP into SASSY_BIN (+ SASSY_ARGS for `-m module`).
# Returns non-zero when nothing usable is found.
#   1. ./sassymcp next to the script (portable bundle)
#   2. ./dist/sassymcp (source checkout after build.sh)
#   3. ./.venv/bin/python -m sassymcp.server (source checkout + venv)
#   4. `sassymcp` on PATH (pipx / pip --user / distro package)
#   5. python3 -m sassymcp.server if the package is importable
resolve_sassymcp() {
    SASSY_BIN=""; SASSY_ARGS=""
    if [ -x "$SCRIPT_DIR/sassymcp" ] && [ ! -d "$SCRIPT_DIR/sassymcp" ]; then
        SASSY_BIN="$SCRIPT_DIR/sassymcp"
    elif [ -x "$SCRIPT_DIR/dist/sassymcp" ]; then
        SASSY_BIN="$SCRIPT_DIR/dist/sassymcp"
    elif [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
        SASSY_BIN="$SCRIPT_DIR/.venv/bin/python"; SASSY_ARGS="-m sassymcp.server"
    elif have sassymcp; then
        SASSY_BIN="$(command -v sassymcp)"
    else
        for py in python3 python; do
            if have "$py" && "$py" -c 'import sassymcp' >/dev/null 2>&1; then
                SASSY_BIN="$(command -v "$py")"; SASSY_ARGS="-m sassymcp.server"
                break
            fi
        done
    fi
    [ -n "${SASSY_BIN:-}" ]
}

_NOT_FOUND="SassyMCP not found. Run ./build.sh, create .venv, or: pipx install sassymcp"

# Run SassyMCP with extra args (replaces the shell).
exec_sassymcp() {
    resolve_sassymcp || die "$_NOT_FOUND"
    # shellcheck disable=SC2086  # SASSY_ARGS is intentionally word-split
    exec "$SASSY_BIN" $SASSY_ARGS "$@"
}

# Start SassyMCP in the background, detached from this terminal. Prints PID.
spawn_sassymcp() {
    resolve_sassymcp || die "$_NOT_FOUND"
    _log="${SASSY_LOG:-/dev/null}"
    # shellcheck disable=SC2086
    if have setsid; then
        setsid "$SASSY_BIN" $SASSY_ARGS "$@" >>"$_log" 2>&1 </dev/null &
    else
        nohup "$SASSY_BIN" $SASSY_ARGS "$@" >>"$_log" 2>&1 </dev/null &
    fi
    echo $!
}

# URL-safe random token (letters, digits, - and _), 43 chars.
gen_token() {
    if have python3; then
        python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
    elif have openssl; then
        openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n'; echo
    else
        LC_ALL=C tr -dc 'A-Za-z0-9_-' </dev/urandom | head -c 43; echo
    fi
}

# PIDs listening on TCP port $1 (one per line). Tries lsof, ss, fuser,
# sockstat (BSD) in turn; prints nothing if none can tell.
listener_pids() {
    _port="$1"
    if have lsof; then
        lsof -nP -t -iTCP:"$_port" -sTCP:LISTEN 2>/dev/null && return
    fi
    if have ss; then
        ss -ltnpH "sport = :$_port" 2>/dev/null | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' && return
    fi
    if have fuser; then
        fuser -n tcp "$_port" 2>/dev/null | tr -s ' ' '\n' | grep -E '^[0-9]+$' && return
    fi
    if have sockstat; then
        sockstat -4 -6 -l -P tcp -p "$_port" 2>/dev/null | awk 'NR>1{print $3}'
    fi
}

# Command name for PID $1 (portable: ps -o comm= works on Linux/macOS/BSD).
pid_comm() { ps -o comm= -p "$1" 2>/dev/null; }

# Kill whatever listens on port $1, but only if it is a SassyMCP/python process.
kill_stale_on_port() {
    _me="$(id -un)"
    for _pid in $(listener_pids "$1"); do
        _owner="$(pid_comm "$_pid")"
        _user="$(ps -o user= -p "$_pid" 2>/dev/null | tr -d ' ')"
        if [ -n "$_user" ] && [ "$_user" != "$_me" ]; then
            # Never touch another user's process (e.g. a system service).
            warn "Port :$1 is held by PID $_pid ($_owner, user $_user) - not ours, leaving it alone."
            continue
        fi
        case "$_owner" in
            *sassymcp*|*python*)
                say "[INFO] Stopping stale SassyMCP process on :$1 (PID $_pid, $_owner)"
                kill "$_pid" 2>/dev/null
                sleep 1
                kill -0 "$_pid" 2>/dev/null && kill -9 "$_pid" 2>/dev/null
                ;;
            "")
                warn "Could not identify owner of PID $_pid on :$1 - leaving it alone." ;;
            *)
                warn "Port :$1 is held by PID $_pid ($_owner) - not SassyMCP/python, leaving it alone." ;;
        esac
    done
}

# Valid token per server rules: [A-Za-z0-9_-]{16,}
token_ok() {
    case "$1" in
        *[!A-Za-z0-9_-]*|"") return 1 ;;
    esac
    [ "${#1}" -ge 16 ]
}

# Per-OS user state dir for logs.
state_dir() {
    case "$(uname -s)" in
        Darwin) printf '%s\n' "$HOME/Library/Logs/SassyMCP" ;;
        *) printf '%s\n' "${XDG_STATE_HOME:-$HOME/.local/state}/sassymcp" ;;
    esac
}

# Package-manager-appropriate install hint for a binary.
install_hint() {
    _pkg="$1"
    if [ "$(uname -s)" = Darwin ]; then say "brew install $_pkg"; return; fi
    for m in apt-get dnf yum pacman zypper apk xbps-install pkg pkg_add brew; do
        if have "$m"; then
            case "$m" in
                apt-get) say "sudo apt-get install $_pkg" ;;
                dnf|yum) say "sudo $m install $_pkg" ;;
                pacman) say "sudo pacman -S $_pkg" ;;
                zypper) say "sudo zypper install $_pkg" ;;
                apk) say "sudo apk add $_pkg" ;;
                xbps-install) say "sudo xbps-install $_pkg" ;;
                pkg) say "sudo pkg install $_pkg" ;;
                pkg_add) say "doas pkg_add $_pkg" ;;
                brew) say "brew install $_pkg" ;;
            esac
            return
        fi
    done
    say "install $_pkg with your package manager"
}
