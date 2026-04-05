@echo off
REM ══════════════════════════════════════════════════════════════
REM  SassyMCP Build Script — Creates dist\sassymcp.exe
REM ══════════════════════════════════════════════════════════════

cd /d "%~dp0"

echo [BUILD] Installing build dependencies...
uv pip install pyinstaller 2>nul
if %ERRORLEVEL% neq 0 (
    echo [BUILD] uv not available, trying pip...
    .venv\Scripts\python.exe -m pip install pyinstaller 2>nul
)

echo [BUILD] Verifying imports...
.venv\Scripts\python.exe -c "from sassymcp.server import mcp; print('[BUILD] Core imports OK')" 2>nul
if %ERRORLEVEL% neq 0 (
    echo [BUILD] FAILED — core imports broken. Fix before building.
    exit /b 1
)

echo [BUILD] Running PyInstaller...
.venv\Scripts\pyinstaller.exe --clean --noconfirm sassymcp.spec

if exist "dist\sassymcp.exe" (
    echo.
    echo [BUILD] Success! dist\sassymcp.exe ready.
    for %%A in (dist\sassymcp.exe) do echo [BUILD] Size: %%~zA bytes
    echo.
    echo [BUILD] Launch modes:
    echo   dist\sassymcp.exe                      (stdio — Claude Desktop pipe)
    echo   dist\sassymcp.exe --http                (HTTP — localhost:21001)
    echo   dist\sassymcp.exe --http --host 0.0.0.0 (HTTP — LAN access)
    echo   dist\sassymcp.exe --http --sse          (SSE — legacy transport)
    echo   dist\sassymcp.exe --setup               (force setup wizard)
) else (
    echo [BUILD] FAILED — check output above.
    exit /b 1
)
