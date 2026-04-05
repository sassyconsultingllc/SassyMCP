@echo off
REM ══════════════════════════════════════════════════════════════
REM  SassyMCP Deploy Script — Portable package for distribution
REM ══════════════════════════════════════════════════════════════

set DEPLOY_DIR=%~dp0deploy
set EXE=%~dp0dist\sassymcp.exe

if not exist "%EXE%" (
    echo [ERROR] dist\sassymcp.exe not found. Run build.bat first.
    exit /b 1
)

echo [DEPLOY] Creating deploy package at %DEPLOY_DIR%...
mkdir "%DEPLOY_DIR%" 2>nul

REM Copy executable
copy /Y "%EXE%" "%DEPLOY_DIR%\sassymcp.exe"

REM Copy all launcher scripts
copy /Y "%~dp0start-local.bat" "%DEPLOY_DIR%\start-local.bat"
copy /Y "%~dp0start-lan.bat" "%DEPLOY_DIR%\start-lan.bat"
copy /Y "%~dp0start-tunnel.bat" "%DEPLOY_DIR%\start-tunnel.bat"

REM Create Claude Desktop config template
(
echo {
echo   "mcpServers": {
echo     "sassymcp": {
echo       "command": "REPLACE_WITH_PATH\\sassymcp.exe",
echo       "args": [],
echo       "env": {
echo         "SASSYMCP_LOAD_ALL": "1"
echo       }
echo     }
echo   }
echo }
) > "%DEPLOY_DIR%\claude_desktop_config.template.json"

REM Create Grok Desktop config template
(
echo {
echo   "mcpServers": {
echo     "sassymcp": {
echo       "command": "REPLACE_WITH_PATH\\sassymcp.exe",
echo       "args": ["--http", "--host", "127.0.0.1", "--port", "21001"],
echo       "env": {
echo         "SASSYMCP_LOAD_ALL": "1"
echo       }
echo     }
echo   }
echo }
) > "%DEPLOY_DIR%\grok_desktop_config.template.json"

REM Create README
(
echo ═══════════════════════════════════════════════════════════
echo  SassyMCP — Setup Guide
echo ═══════════════════════════════════════════════════════════
echo.
echo  QUICK START:
echo.
echo  1. Run sassymcp.exe --setup
echo     This starts the server and flags the setup wizard.
echo     The first AI session will guide you through configuration.
echo.
echo  TRANSPORT MODES:
echo.
echo  Local ^(stdio — Claude Desktop pipe^):
echo    Run: start-local.bat
echo    Or:  sassymcp.exe
echo    Config: Copy claude_desktop_config.template.json to
echo            %%APPDATA%%\Claude\claude_desktop_config.json
echo            Edit the path to match your install location.
echo.
echo  HTTP ^(localhost or LAN^):
echo    Run: start-lan.bat
echo    Interactive: choose bind address, port, and auth token.
echo    For LAN access, an auth token is required.
echo.
echo  Cloudflare Tunnel ^(remote access^):
echo    Requires: cloudflared installed and configured
echo    Run: start-tunnel.bat
echo    Interactive: sets up auth token and tunnel name.
echo.
echo  AUTH TOKENS:
echo.
echo  For HTTP/tunnel modes, set SASSYMCP_AUTH_TOKEN env var or
echo  use the sassy_setup_generate_token tool to create scoped
echo  tokens saved to ~/.sassymcp/tokens.json.
echo.
echo  GUIDED SETUP:
echo.
echo  After first launch, the AI will guide you through:
echo    sassy_setup_wizard      — Create your user profile
echo    sassy_setup_github      — Connect GitHub ^(opens browser for token^)
echo    sassy_setup_ssh         — Connect remote Linux ^(host/user/pass^)
echo    sassy_setup_check_tools — Scan for optional tools ^(nmap, adb, etc.^)
echo.
echo  ENVIRONMENT VARIABLES:
echo.
echo  SASSYMCP_LOAD_ALL=1          Load all tool modules
echo  SASSYMCP_GROUPS=core,github  Load specific groups only
echo  SASSYMCP_AUTH_TOKEN=xxx      Bearer token for HTTP auth
echo  SASSYMCP_DEV=1               Enable live reload ^(dev mode^)
echo  GITHUB_TOKEN=xxx             GitHub API access
echo  SSH_HOST=xxx                 Remote Linux hostname/IP
echo  SSH_USER=xxx                 Remote Linux username
echo  SSH_PASS=xxx                 Remote Linux password
echo.
echo  FILES:
echo.
echo  ~/.sassymcp/persona.md       Your user profile
echo  ~/.sassymcp/config.json      Server configuration
echo  ~/.sassymcp/tokens.json      Auth tokens
echo  ~/.sassymcp/tool_usage.json  Tool analytics data
echo  ~/.sassymcp/audit.log        Audit trail
echo ═══════════════════════════════════════════════════════════
) > "%DEPLOY_DIR%\README.txt"

echo.
echo [DEPLOY] Package ready:
dir "%DEPLOY_DIR%" /B
echo.
for %%A in ("%DEPLOY_DIR%\sassymcp.exe") do echo [DEPLOY] EXE size: %%~zA bytes
echo.
echo [DEPLOY] Contents:
echo   sassymcp.exe                        — Server executable
echo   start-local.bat                     — Stdio mode launcher
echo   start-lan.bat                       — HTTP mode launcher (localhost/LAN)
echo   start-tunnel.bat                    — Cloudflare tunnel launcher
echo   claude_desktop_config.template.json — Claude Desktop config template
echo   grok_desktop_config.template.json   — Grok Desktop config template
echo   README.txt                          — Setup guide
