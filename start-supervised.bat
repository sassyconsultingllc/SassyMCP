@echo off
REM =============================================================
REM  SassyMCP - Supervised mode (recommended)
REM =============================================================
REM  Runs the HTTP bridge under `sassymcp supervise`, which:
REM    - keeps the bridge alive (restart with backoff on crash/hang),
REM    - guarantees no orphaned bridge survives this window closing
REM      (Windows Job Object: kill-on-close), so you never get a wedged
REM      SQLite/WAL lock the way `taskkill /f` on the old launcher did,
REM    - exposes `sassymcp.exe supervise status` / `stop` for control.
REM
REM  Tunnel:
REM    --tunnel-mode none     (default) bridge only; run cloudflared
REM                           separately (e.g. as a Windows service).
REM    --tunnel-mode managed  also run `cloudflared tunnel run <name>` as a
REM                           supervised child (set SASSYMCP_TUNNEL_NAME or
REM                           pass --tunnel-name). cloudflared must be on PATH
REM                           and the named tunnel already configured.
REM
REM  Stop cleanly from another terminal:  sassymcp.exe supervise stop
REM =============================================================

setlocal

set HOST=127.0.0.1
set PORT=21001
set TUNNEL_MODE=%SASSYMCP_TUNNEL_MODE%
if "%TUNNEL_MODE%"=="" set TUNNEL_MODE=none

set EXE=%~dp0sassymcp.exe
if not exist "%EXE%" set EXE=%~dp0dist\sassymcp.exe
if not exist "%EXE%" (
    echo [ERROR] sassymcp.exe not found next to this script or in dist\.
    exit /b 1
)

echo ==============================================================
echo  SassyMCP - Supervised
echo   Bridge:  %HOST%:%PORT%
echo   Tunnel:  %TUNNEL_MODE%
echo   Stop:    "%EXE%" supervise stop
echo ==============================================================
echo.

"%EXE%" supervise start --host %HOST% --port %PORT% --tunnel-mode %TUNNEL_MODE%

endlocal
