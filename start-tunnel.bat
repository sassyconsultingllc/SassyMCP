@echo off
setlocal enabledelayedexpansion
REM ══════════════════════════════════════════════════════════════
REM  SassyMCP — Cloudflare Tunnel Mode
REM  HTTP server on localhost + cloudflared tunnel for remote access
REM ══════════════════════════════════════════════════════════════

set SASSYMCP_LOAD_ALL=1
set PORT=21001

echo ═══════════════════════════════════════════
echo  SassyMCP — Cloudflare Tunnel Mode
echo ═══════════════════════════════════════════
echo.

REM ── Check cloudflared ───────────────────────────────────────
where cloudflared >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] cloudflared not found in PATH.
    echo         Install: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
    echo         Or: winget install Cloudflare.cloudflared
    pause
    exit /b 1
)

REM ── Auth Token ──────────────────────────────────────────────
if not defined SASSYMCP_AUTH_TOKEN (
    echo  Tunnel mode requires an auth token.
    echo  Enter a token or press Enter to auto-generate:
    set /p USER_TOKEN="  Token: "
    if "!USER_TOKEN!"=="" (
        for /f "delims=" %%T in ('powershell -NoProfile -Command "[Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).Replace('+','-').Replace('/','_').TrimEnd('=')"') do set SASSYMCP_AUTH_TOKEN=%%T
    ) else (
        set SASSYMCP_AUTH_TOKEN=!USER_TOKEN!
    )
    echo  [OK] Auth token: !SASSYMCP_AUTH_TOKEN!
    echo       Clients must send: Authorization: Bearer !SASSYMCP_AUTH_TOKEN!
)
echo.

REM ── Tunnel Name ─────────────────────────────────────────────
set /p TUNNEL_NAME="  Cloudflare tunnel name [sassymcp]: "
if "!TUNNEL_NAME!"=="" set TUNNEL_NAME=sassymcp

REM ── Check for existing instance ─────────────────────────────
tasklist /FI "IMAGENAME eq sassymcp.exe" 2>nul | find "sassymcp.exe" >nul
if %ERRORLEVEL%==0 (
    echo [WARN] sassymcp.exe already running. Kill it first:
    echo        taskkill /f /im sassymcp.exe
    pause
    exit /b 1
)

REM ── Launch Server ───────────────────────────────────────────
echo [1/2] Starting HTTP server on 127.0.0.1:%PORT%...
if exist "%~dp0dist\sassymcp.exe" (
    start "SassyMCP HTTP" /MIN "%~dp0dist\sassymcp.exe" --http --host 127.0.0.1 --port %PORT%
) else if exist "%~dp0.venv\Scripts\python.exe" (
    start "SassyMCP HTTP" /MIN "%~dp0.venv\Scripts\python.exe" -m sassymcp.server --http --host 127.0.0.1 --port %PORT%
) else (
    echo [ERROR] No sassymcp.exe or .venv found. Run build.bat first.
    pause
    exit /b 1
)

REM Wait for server to bind
timeout /t 3 /nobreak >nul

REM ── Launch Tunnel ───────────────────────────────────────────
echo [2/2] Starting Cloudflare Tunnel '%TUNNEL_NAME%'...
start "Cloudflared Tunnel" /MIN cloudflared tunnel run %TUNNEL_NAME%

echo.
echo ═══════════════════════════════════════════
echo  Server:  http://127.0.0.1:%PORT%
echo  Tunnel:  %TUNNEL_NAME% (check CF dashboard for URL)
echo  Auth:    Bearer token required
echo.
echo  To stop: taskkill /f /im sassymcp.exe ^& taskkill /f /im cloudflared.exe
echo ═══════════════════════════════════════════
pause
