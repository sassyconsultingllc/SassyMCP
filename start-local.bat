@echo off
REM ══════════════════════════════════════════════════════════════
REM  SassyMCP — Local Stdio Mode (Claude Desktop direct pipe)
REM  No network exposure. Safest mode.
REM ══════════════════════════════════════════════════════════════

set SASSYMCP_LOAD_ALL=1

echo ═══════════════════════════════════════════
echo  SassyMCP — Local Mode (stdio)
echo ═══════════════════════════════════════════
echo.

if exist "%~dp0dist\sassymcp.exe" (
    "%~dp0dist\sassymcp.exe"
) else if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -m sassymcp.server
) else (
    echo [ERROR] No sassymcp.exe or .venv found. Run build.bat first.
    pause
    exit /b 1
)
