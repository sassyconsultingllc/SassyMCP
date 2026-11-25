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

REM Resolve sassymcp.exe relative to this script (same pattern as
REM start-local.bat) instead of a hard-coded personal path.
set EXE=%~dp0dist\sassymcp.exe
if not exist "%EXE%" (
    echo [%DATE% %TIME%] FAIL: %EXE% not found next to this script >> "%LOGFILE%"
    exit /b 1
)

REM Wait for the drive holding the exe (a VeraCrypt mount can take a
REM moment after logon)
set /a WAITED=0
:wait_exe
if exist "%EXE%" goto exe_ready
if %WAITED% geq 60 (
    echo [%DATE% %TIME%] FAIL: %EXE% not present after 60s >> "%LOGFILE%"
    exit /b 1
)
timeout /t 2 /nobreak >nul
set /a WAITED+=2
goto wait_exe

:exe_ready
echo [%DATE% %TIME%] %EXE% ready after %WAITED%s >> "%LOGFILE%"

if not defined SASSYMCP_AUTH_TOKEN (
    echo [%DATE% %TIME%] FAIL: SASSYMCP_AUTH_TOKEN not in env >> "%LOGFILE%"
    exit /b 1
)

REM Kill any stale bridge already on :21001 (idempotent re-runs).
REM Ownership-checked: only a SassyMCP/python process is killed.
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:"LISTENING.*:21001 "') do (
    call :KillStale %%P
)

set SASSYMCP_LOAD_ALL=1

echo [%DATE% %TIME%] Launching bridge >> "%LOGFILE%"
start "sassymcp-bridge" /MIN "%EXE%" --http --host 127.0.0.1 --port 21001

echo [%DATE% %TIME%] Bridge launched (detached) >> "%LOGFILE%"
endlocal
goto :eof

:KillStale
REM Best-effort: kill PID %~1 only if its image is a SassyMCP/python process.
set "STALE_PID=%~1"
set "OWNER="
for /f "delims=" %%I in ('tasklist /FI "PID eq %STALE_PID%" /NH /FO CSV 2^>nul ^| findstr /I /V "^INFO:"') do set "OWNER=%%I"
if not defined OWNER (
    echo [%DATE% %TIME%] WARN: could not identify owner of PID %STALE_PID% on :21001 - leaving it alone >> "%LOGFILE%"
    goto :eof
)
echo "%OWNER%" | findstr /I "sassymcp python" >nul
if errorlevel 1 (
    echo [%DATE% %TIME%] WARN: :21001 held by PID %STALE_PID% ^(%OWNER%^) - not a SassyMCP/python process, leaving it alone >> "%LOGFILE%"
) else (
    echo [%DATE% %TIME%] Killing stale SassyMCP PID %STALE_PID% on :21001 >> "%LOGFILE%"
    taskkill /f /pid %STALE_PID% >nul 2>&1
)
goto :eof
