@echo off
REM =============================================================
REM  SassyMCP - HTTP Mode (localhost or LAN)
REM  For: Claude Desktop HTTP, Cursor, Windsurf, Grok Desktop
REM =============================================================

setlocal enabledelayedexpansion

set SASSYMCP_LOAD_ALL=1

echo ==============================================================
echo  SassyMCP - HTTP Mode
echo ==============================================================
echo.

REM --- Bind Address -------------------------------------------
echo  Choose bind address:
echo    1. 127.0.0.1 (localhost only - safest)
echo    2. 0.0.0.0   (LAN accessible - requires auth token)
echo.
set /p BIND_CHOICE="  Select [1]: "
if "%BIND_CHOICE%"=="2" (
    set BIND_ADDR=0.0.0.0
) else (
    set BIND_ADDR=127.0.0.1
)

REM --- Port ---------------------------------------------------
set /p PORT="  Port [21001]: "
if "%PORT%"=="" set PORT=21001

REM --- Auth Token (required for LAN, optional for localhost) --
if "!BIND_ADDR!"=="0.0.0.0" (
    echo.
    echo  [!] LAN mode requires an auth token for security.
    if defined SASSYMCP_AUTH_TOKEN (
        echo  [OK] SASSYMCP_AUTH_TOKEN already set.
    ) else (
        echo  Enter a token or press Enter to auto-generate one:
        set /p USER_TOKEN="  Token: "
        if "!USER_TOKEN!"=="" (
            for /f "delims=" %%T in ('powershell -NoProfile -Command "$b=New-Object byte[] 32;(New-Object System.Security.Cryptography.RNGCryptoServiceProvider).GetBytes($b);[Convert]::ToBase64String($b).Replace('+','-').Replace('/','_').TrimEnd('=')"') do set SASSYMCP_AUTH_TOKEN=%%T
        ) else (
            set SASSYMCP_AUTH_TOKEN=!USER_TOKEN!
        )
        echo  [OK] Auth token set. Clients must send:
        echo       Authorization: Bearer !SASSYMCP_AUTH_TOKEN!
    )
) else (
    if defined SASSYMCP_AUTH_TOKEN (
        echo  [OK] Auth token active.
    ) else (
        echo  [INFO] No auth token set. Localhost-only access.
    )
)

echo.
echo  Starting on !BIND_ADDR!:!PORT!...
echo ==============================================================

if exist "%~dp0dist\sassymcp.exe" (
    "%~dp0dist\sassymcp.exe" --http --host !BIND_ADDR! --port !PORT!
) else if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -m sassymcp.server --http --host !BIND_ADDR! --port !PORT!
) else (
    echo [ERROR] No sassymcp.exe or .venv found. Run build.bat first.
    pause
    exit /b 1
)

endlocal
