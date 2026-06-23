#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  SassyMCP Build Script (macOS / Linux) — creates dist/sassymcp
#
#  Mirrors build.bat. PyInstaller cannot cross-compile: run this on
#  the OS you want the binary for (a Mac for the macOS build, Linux for
#  the Linux build). The spec (sassymcp.spec) is platform-aware and adds
#  the right UI stack (pyobjc on macOS) automatically.
# ══════════════════════════════════════════════════════════════
set -euo pipefail

cd "$(dirname "$0")"

# Resolve a venv python if present, else fall back to python3.
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
else
    PY="$(command -v python3 || command -v python)"
fi
echo "[BUILD] Using interpreter: $PY"

echo "[BUILD] Installing build dependencies..."
if command -v uv >/dev/null 2>&1; then
    uv pip install pyinstaller >/dev/null 2>&1 || "$PY" -m pip install pyinstaller
else
    "$PY" -m pip install pyinstaller
fi

echo "[BUILD] Verifying imports..."
if ! "$PY" -c "from sassymcp.server import mcp; print('[BUILD] Core imports OK')"; then
    echo "[BUILD] FAILED — core imports broken. Fix before building."
    exit 1
fi

echo "[BUILD] Running PyInstaller..."
"$PY" -m PyInstaller --clean --noconfirm sassymcp.spec

if [ -f "dist/sassymcp" ]; then
    echo
    echo "[BUILD] Success! dist/sassymcp ready."
    echo "[BUILD] Size: $(wc -c < dist/sassymcp) bytes"
    echo
    echo "[BUILD] Launch modes:"
    echo "  dist/sassymcp                       (stdio — Claude Desktop pipe)"
    echo "  dist/sassymcp --http                (HTTP — localhost:21001)"
    echo "  dist/sassymcp --http --host 0.0.0.0 (HTTP — LAN access)"
    echo "  dist/sassymcp --http --sse          (SSE — legacy transport)"
    echo "  dist/sassymcp --setup               (force setup wizard)"
    echo
    echo "[BUILD] macOS note: window-control + screenshot tools need Accessibility"
    echo "        and Screen Recording permission for the app running SassyMCP"
    echo "        (System Settings -> Privacy & Security)."
else
    echo "[BUILD] FAILED — check output above."
    exit 1
fi
