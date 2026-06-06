@echo off
REM =============================================================
REM  SassyMCP Autostart Bridge (scheduled task at logon)
REM
REM  Launches the dist exe in HTTP mode on 127.0.0.1:21001 so the
REM  Cloudflare tunnel service (which runs as a Windows service and
REM  starts at boot) has an origin to forward mcp.sassyconsultingllc.com
REM  to. Inherits SASSYMCP_AUTH_TOKEN and SASSYMCP_ALLOWED_HOSTS from
REM  the user environment.
REM =============================================================

setlocal

set LOGDIR=%LOCALAPPDATA%\SassyMCP
set LOGFILE=%LOGDIR%\bridge.log
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set EXE=V:\Projects\SassyMCP\dist\sassymcp.exe

REM Wait for V: drive (VeraCrypt mount can take a moment after logon)
set /a WAITED=0
:wait_v
if exist "%EXE%" goto v_ready
if %WAITED% geq 60 (
    echo [%DATE% %TIME%] FAIL: %EXE% not present after 60s >> "%LOGFILE%"
    exit /b 1
)
timeout /t 2 /nobreak >nul
set /a WAITED+=2
goto wait_v

:v_ready
echo [%DATE% %TIME%] %EXE% ready after %WAITED%s >> "%LOGFILE%"

if not defined SASSYMCP_AUTH_TOKEN (
    echo [%DATE% %TIME%] FAIL: SASSYMCP_AUTH_TOKEN not in env >> "%LOGFILE%"
    exit /b 1
)

REM Kill any stale bridge already on :21001 (idempotent re-runs)
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:"LISTENING.*:21001 "') do (
    echo [%DATE% %TIME%] Killing stale PID %%P on :21001 >> "%LOGFILE%"
    taskkill /f /pid %%P >nul 2>&1
)

set SASSYMCP_LOAD_ALL=1

echo [%DATE% %TIME%] Launching bridge >> "%LOGFILE%"
start "sassymcp-bridge" /MIN "%EXE%" --http --host 127.0.0.1 --port 21001

echo [%DATE% %TIME%] Bridge launched (detached) >> "%LOGFILE%"
endlocal
