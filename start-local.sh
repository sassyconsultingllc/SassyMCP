#!/bin/sh
# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# ══════════════════════════════════════════════════════════════
#  SassyMCP — Local Stdio Mode (Claude Desktop direct pipe)
#  No network exposure. Safest mode. POSIX twin of start-local.bat.
# ══════════════════════════════════════════════════════════════
set -eu
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/sassymcp-common.sh"

SASSYMCP_LOAD_ALL=1; export SASSYMCP_LOAD_ALL

# stdout is the MCP pipe in stdio mode: banner goes to stderr only.
say "═══════════════════════════════════════════" >&2
say " SassyMCP — Local Mode (stdio)" >&2
say "═══════════════════════════════════════════" >&2

exec_sassymcp "$@"
