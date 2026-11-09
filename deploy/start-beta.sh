#!/bin/sh
# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# SassyMCP Beta — full build with bundled tools. POSIX twin of start-beta.bat.
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

for t in adb nmap scrcpy tesseract; do
    [ -d "$SCRIPT_DIR/tools/$t" ] && PATH="$SCRIPT_DIR/tools/$t:$PATH"
done
export PATH
[ -d "$SCRIPT_DIR/tools/tesseract/tessdata" ] && TESSDATA_PREFIX="$SCRIPT_DIR/tools/tesseract/tessdata" && export TESSDATA_PREFIX
SASSYMCP_LOAD_ALL=1; export SASSYMCP_LOAD_ALL

exec "$SCRIPT_DIR/sassymcp" "$@"
