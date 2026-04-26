@echo off
REM =============================================================
REM  SassyMCP - Cloudflare Tunnel Mode (production, non-interactive)
REM =============================================================
REM  Prereqs (all one-time):
REM    1. cloudflared installed as Windows service pointing at
REM       C:\Users\Admin\.cloudflared\config.yml
REM    2. Named tunnel 'sassymcp' (2b14170b-ea06-4c42-aa11-97bb1e26aea7)
REM       ingress: mcp.sassyconsultingllc.com -> http://127.0.0.1:21001
REM    3. DNS A/AAAA for mcp.sassyconsultingllc.com proxied to tunnel
REM    4. SASSYMCP_AUTH_TOKEN set at User scope (persistent)
REM    5. V: drive mounted (VeraCrypt) before this runs
REM
REM  This script only starts the HTTP bridge. Cloudflared runs as
REM  a service already - do not launch a second instance.
REM =============================================================

setlocal

set PORT=21001
set HOST=127.0.0.1
set SASSYMCP_LOAD_ALL=1

REM --- Preflight ----------------------------------------------
if not defined SASSYMCP_AUTH_TOKEN (
    echo [ERROR] SASSYMCP_AUTH_TOKEN not set in environment.
    echo         Set it at User scope, then relaunch.
    echo.
    echo         [Environment]::SetEnvironmentVariable(
    echo           "SASSYMCP_AUTH_TOKEN", "your-token", "User")
    exit /b 1
)

if not exist "V:\Projects\SassyMCP\.venv\Scripts\python.exe" (
    echo [ERROR] V:\Projects\SassyMCP\.venv missing.
    echo         Is V: mounted? VeraCrypt must unlock before this runs.
    exit /b 1
)

REM --- Kill any stale bridge on :21001 ------------------------
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:"LISTENING.*:%PORT% "') do (
    echo [INFO] Killing stale process on :%PORT% (PID %%P)
    taskkill /f /pid %%P >nul 2>&1
)

REM --- Cloudflared service health -----------------------------
sc query Cloudflared | findstr /C:"RUNNING" >nul
if %ERRORLEVEL% neq 0 (
    echo [WARN] Cloudflared service is not RUNNING.
    echo        Start it with: sc start Cloudflared
    echo        Continuing anyway - bridge will bind locally.
)

REM --- Launch bridge ------------------------------------------
echo ==============================================================
echo  SassyMCP HTTP Bridge
echo   Bind:   %HOST%:%PORT%
echo   Tunnel: mcp.sassyconsultingllc.com
echo   Auth:   Bearer token (from SASSYMCP_AUTH_TOKEN)
echo ==============================================================
echo.

"V:\Projects\SassyMCP\.venv\Scripts\python.exe" -m sassymcp.server --http --host %HOST% --port %PORT%

endlocal
