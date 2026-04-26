═══════════════════════════════════════════════════════════
 SassyMCP — Setup Guide
═══════════════════════════════════════════════════════════

 QUICK START:

 1. Run sassymcp.exe --setup
    This starts the server and flags the setup wizard.
    The first AI session will guide you through configuration.

 TRANSPORT MODES:

 Local (stdio — Claude Desktop / Cursor / Windsurf / Cline pipe):
   Run: start-local.bat
   Or:  sassymcp.exe
   Config: copy the template that matches your client and edit the path:
     Claude Desktop  -> %APPDATA%\Claude\claude_desktop_config.json
     Cursor          -> ~\.cursor\mcp.json
     Windsurf        -> ~\.codeium\windsurf\mcp_config.json
     Continue.dev    -> ~\.continue\config.json (merge under experimental)
     Grok Desktop    -> uses HTTP, see grok_desktop_config.template.json

 HTTP (localhost or LAN):
   Run: start-lan.bat
   Interactive: choose bind address, port, and auth token.
   For LAN access, an auth token is required.

 Cloudflare Tunnel (remote access):
   Requires: cloudflared installed and configured
   Run: start-tunnel.bat
   Interactive: sets up auth token and tunnel name.

 AUTH TOKENS:

 For HTTP/tunnel modes, set SASSYMCP_AUTH_TOKEN env var or
 use the sassy_setup_generate_token tool to create scoped
 tokens saved to ~/.sassymcp/tokens.json.

 GUIDED SETUP:

 After first launch, the AI will guide you through:
   sassy_setup_wizard      — Create your user profile
   sassy_setup_github      — Connect GitHub (opens browser for token)
   sassy_setup_ssh         — Connect remote Linux (host/user/pass)
   sassy_setup_check_tools — Scan for optional tools (nmap, adb, etc.)

 ENVIRONMENT VARIABLES:

 SASSYMCP_LOAD_ALL=1          Load all tool modules
 SASSYMCP_GROUPS=core,github  Load specific groups only
 SASSYMCP_AUTH_TOKEN=xxx      Bearer token for HTTP auth
 SASSYMCP_DEV=1               Enable live reload (dev mode)
 GITHUB_TOKEN=xxx             GitHub API access
 SSH_HOST=xxx                 Remote Linux hostname/IP
 SSH_USER=xxx                 Remote Linux username
 SSH_PASS=xxx                 Remote Linux password

 FILES:

 ~/.sassymcp/persona.md       Your user profile
 ~/.sassymcp/config.json      Server configuration
 ~/.sassymcp/tokens.json      Auth tokens
 ~/.sassymcp/tool_usage.json  Tool analytics data
 ~/.sassymcp/audit.log        Audit trail
═══════════════════════════════════════════════════════════
