@echo off
REM =============================================================
REM  SassyMCP - Cloudflare Tunnel Mode (generic, ships in portable zip)
REM =============================================================
REM  Prereqs:
REM    1. cloudflared installed and on PATH
REM       (winget install Cloudflare.cloudflared)
REM    2. SASSYMCP_AUTH_TOKEN set in environment (User scope persists)
REM    3. sassymcp.exe in this folder (true for the portable bundle)
REM
REM  This script launches sassymcp.exe in HTTP mode bound to localhost,
REM  then runs `cloudflared tunnel run <NAME>` to expose it. cloudflared
REM  must already be configured for the named tunnel via:
REM    cloudflared tunnel login
REM    cloudflared tunnel create <NAME>
REM    cloudflared tunnel route dns <NAME> <hostname>
REM  Personal/production launchers that hard-code one tunnel name and
REM  hostname live under personal/ in the source tree (gitignored) and
REM  do NOT ship in the portable zip.
REM =============================================================

setlocal

set PORT=21001
set HOST=127.0.0.1
set SASSYMCP_LOAD_ALL=1

REM --- Resolve sassymcp.exe alongside this script ---------------
set EXE=%~dp0sassymcp.exe
if not exist "%EXE%" (
    echo [ERROR] sassymcp.exe not found next to this script.
    echo         Expected: %EXE%
    echo         Are you running this from inside the extracted portable bundle?
    exit /b 1
)

REM --- Preflight ------------------------------------------------
if not defined SASSYMCP_AUTH_TOKEN (
    echo [ERROR] SASSYMCP_AUTH_TOKEN not set in environment.
    echo         Set it at User scope, then relaunch:
    echo.
    echo           [Environment]::SetEnvironmentVariable("SASSYMCP_AUTH_TOKEN", "your-token", "User")
    exit /b 1
)

where cloudflared >nul 2>&1
if errorlevel 1 (
    echo [ERROR] cloudflared not found on PATH.
    echo         Install with: winget install Cloudflare.cloudflared
    echo         Then run this script again.
    exit /b 1
)

REM --- Tunnel name (default: sassymcp; override with arg or env) ---
set TUNNEL_NAME=%~1
if "%TUNNEL_NAME%"=="" set TUNNEL_NAME=%SASSYMCP_TUNNEL_NAME%
if "%TUNNEL_NAME%"=="" set TUNNEL_NAME=sassymcp

REM --- Kill any stale bridge on :PORT ---------------------------
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:"LISTENING.*:%PORT% "') do (
    echo [INFO] Killing stale process on :%PORT% (PID %%P)
    taskkill /f /pid %%P >nul 2>&1
)

REM --- Launch HTTP bridge in background --------------------------
echo ==============================================================
echo  SassyMCP HTTP Bridge + Cloudflare Tunnel
echo   Bind:    %HOST%:%PORT%
echo   Tunnel:  %TUNNEL_NAME%
echo   Auth:    Bearer token (from SASSYMCP_AUTH_TOKEN)
echo ==============================================================
echo.
start "sassymcp-bridge" /MIN "%EXE%" --http --host %HOST% --port %PORT%

REM --- Run the named tunnel (foreground, exits on Ctrl-C) ------
cloudflared tunnel run %TUNNEL_NAME%

echo [INFO] cloudflared exited. Stopping HTTP bridge...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:"LISTENING.*:%PORT% "') do (
    taskkill /f /pid %%P >nul 2>&1
)

endlocal
