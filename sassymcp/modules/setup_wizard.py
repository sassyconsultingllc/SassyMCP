"""SassyMCP Setup Wizard — First-run questionnaire that generates persona.md.

On first launch (no ~/.sassymcp/persona.md), the wizard tool is prominently
available. The AI calls sassy_setup_wizard with answers to generate a
tailored persona file. Subsequent sessions use the generated persona
automatically via the persona module.

For monetization: this is the onboarding flow. Every new user gets a
personalized experience from their first session.
"""

import json
import logging
import os
import secrets
import time
from pathlib import Path

from sassymcp._atomic import atomic_write_json, atomic_write_text

logger = logging.getLogger("sassymcp.setup")


def _register_hooks():
    from sassymcp.modules._hooks import register_hook

    register_hook(
        name="onboarding",
        module="setup_wizard",
        description="New user onboarding — guided setup flow for first-time users",
        triggers=["setup", "first time", "configure", "get started", "onboard", "new user",
                  "initial setup", "set up sassymcp"],
        instructions="""
## Onboarding Playbook

Guide new users through setup in THIS order. Each step can be skipped.

### Step 0: License (sassy_setup_license)
1. action="status" — check current tier
2. If free: mention upgrade at sassyconsultingllc.com/sassymcp ($29/mo)
3. If they have a key: action="activate" with their key
4. Don't push — just inform what Pro unlocks and move on

### Step 1: Persona (sassy_setup_wizard)
Ask about: role, expertise level, languages, frameworks, communication style.
Also ask two device questions:
- "Do you have an Android phone you want to control?" → has_android=True
- "Do you have a Linux server or WSL you work with?" → has_linux=True
Keep it conversational — don't dump all parameters at once.

### Step 2: Tools (sassy_setup_tools)
Run action="check" first. ALWAYS install tesseract if missing — it is required
for OCR/vision regardless of other choices:
  sassy_setup_tools(action="install_required")
If has_android is True: also install adb and scrcpy.
If has_linux is True: also install plink.
Present install results clearly.

### Step 3: GitHub (sassy_setup_github)
1. action="check" — is a token already set?
2. If not: action="open_browser" — opens the token creation page
3. Walk them through scope selection (Contents, Issues, PRs, Metadata)
4. action="save_token" with their token — validates and saves
5. If they don't use GitHub: action="skip"

### Step 4: SSH / Linux (sassy_setup_ssh)
Only if has_linux is True (or user expressed interest).
1. action="check" — plink installed? Credentials set?
2. If they have a Linux server: collect host, user, password
3. action="save" then action="test" to verify
4. If no Linux: action="skip"

### Tone:
- First-time users: patient, explain what each thing does
- Returning users: fast, just confirm what changed
- Call sassy_setup_status to check what already configured
""",
    )

try:
    _register_hooks()
except Exception:
    pass

from sassymcp._paths import (
    HOME as _SASSYMCP_DIR,
    PERSONA_FILE as _PERSONA_FILE,
    CONFIG_FILE as _CONFIG_FILE,
    TOKENS_FILE as _TOKENS_FILE,
)


def _is_setup_complete() -> bool:
    """Check if initial setup has been completed."""
    return _PERSONA_FILE.exists() and _PERSONA_FILE.stat().st_size > 50


def _load_config() -> dict:
    """Load persistent config."""
    try:
        if _CONFIG_FILE.exists():
            return json.loads(_CONFIG_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_config(config: dict):
    """Save persistent config."""
    atomic_write_json(_CONFIG_FILE, config)


def _generate_persona_md(answers: dict) -> str:
    """Generate persona.md content from questionnaire answers."""
    sections = []

    sections.append("# SassyMCP User Profile")
    sections.append(f"*Generated: {time.strftime('%Y-%m-%d %H:%M')}*\n")

    # Role & Expertise
    role = answers.get("role", "developer")
    expertise = answers.get("expertise_level", "senior")
    specializations = answers.get("specializations", "")
    sections.append("## Role & Expertise")
    sections.append(f"- **Role**: {role}")
    sections.append(f"- **Level**: {expertise}")
    if specializations:
        sections.append(f"- **Specializations**: {specializations}")
    sections.append("")

    # Languages & Frameworks
    languages = answers.get("languages", "")
    frameworks = answers.get("frameworks", "")
    if languages or frameworks:
        sections.append("## Tech Stack")
        if languages:
            sections.append(f"- **Languages**: {languages}")
        if frameworks:
            sections.append(f"- **Frameworks/Tools**: {frameworks}")
        sections.append("")

    # Systems
    systems = answers.get("systems", "")
    if systems:
        sections.append("## Systems Managed")
        for line in systems.split("\n"):
            line = line.strip()
            if line:
                sections.append(f"- {line}")
        sections.append("")

    # Projects
    projects = answers.get("projects", "")
    if projects:
        sections.append("## Active Projects")
        for line in projects.split("\n"):
            line = line.strip()
            if line:
                sections.append(f"- {line}")
        sections.append("")

    # Communication Style
    style = answers.get("communication_style", "terse")
    sections.append("## Communication Preferences")
    style_map = {
        "terse": "Minimal output. Code and results only. No explanations unless asked.",
        "balanced": "Brief explanations with code. State what changed and why.",
        "verbose": "Detailed explanations, rationale, and alternatives discussed.",
    }
    sections.append(f"- **Style**: {style} — {style_map.get(style, style)}")
    sections.append("")

    # Security Posture
    security = answers.get("security_posture", "standard")
    sections.append("## Security Posture")
    security_map = {
        "standard": "OWASP defaults. Validate inputs, escape outputs, parameterize queries.",
        "hardened": "Standard + CSP, HSTS, rate limiting, dependency auditing, principle of least privilege.",
        "paranoid": "Hardened + air-gapped secrets, cert pinning, full audit trails, zero trust networking.",
    }
    sections.append(f"- **Level**: {security} — {security_map.get(security, security)}")
    sections.append("")

    # MCP Context
    clients = answers.get("mcp_clients", "")
    if clients:
        sections.append("## MCP Environment")
        sections.append(f"- **Clients**: {clients}")
        sections.append("")

    # Custom Notes
    notes = answers.get("notes", "")
    if notes:
        sections.append("## Additional Notes")
        sections.append(notes)
        sections.append("")

    # Tool Playbook — always appended. Persona is in an always_load=True group,
    # so this section is visible to the AI on every session, regardless of
    # which client is talking to SassyMCP. It maps common user requests to
    # the right tool sequences so the model picks the efficient path instead
    # of improvising via shell/web fetches.
    sections.append("## Tool Playbook")
    sections.append("")
    sections.append("When the user asks for... use these tools (in order):")
    sections.append("")
    sections.append("| User says... | Tool sequence |")
    sections.append("|---|---|")
    sections.append("| \"screenshot\" / \"what's on screen\" | `sassy_screenshot` (full color) OR `sassy_screen_glance` (3-6KB grayscale) |")
    sections.append("| \"watch the screen for changes\" | `sassy_screen_watch` — returns only changed frames |")
    sections.append("| \"phone status\" | `sassy_phone_state` then `sassy_phone_glance` |")
    sections.append("| \"phone UI\" / \"tap that button\" | `sassy_phone_ui` FIRST (read coords) then `sassy_phone_tap` |")
    sections.append("| \"check the audit\" / \"what got blocked\" | `sassy_audit_search pattern_event=\"pattern_block\"` |")
    sections.append("| \"review the PR\" | `sassy_github_quick_pr action=\"show\"` then `action=\"diff\"` |")
    sections.append("| \"remember this for next session\" | `sassy_memory_remember` with key prefix `task_<concept>_<project>_state` |")
    sections.append("| \"what was I working on\" | `sassy_memory_context` FIRST, then `sassy_crosslink_recv channel=\"task-handoff\"` |")
    sections.append("| \"build / compile / dev server\" | `sassy_session_start` (NOT `sassy_shell` — sessions persist past a single call) |")
    sections.append("| \"scan my network\" | `sassy_netstat` + `sassy_arp` for local; `sassy_port_scan` for remote |")
    sections.append("| \"what's running on this machine\" / \"autoruns\" | `sassy_reg_autoruns` (Windows forensics) |")
    sections.append("| \"hand off to my [other client]\" | `sassy_crosslink_send channel=\"task-handoff\"` |")
    sections.append("| \"add a tool that...\" | Activate `self_modify` hook; use `sassy_selfmod_*` workflow (read → edit → hot-reload) |")
    sections.append("")
    sections.append("Discover more playbooks any time: `sassy_hooks_list` shows registered operational hooks; `sassy_hooks_activate name=\"<x>\"` loads a hook's full playbook into context.")
    sections.append("")
    sections.append("Smart loading: SassyMCP auto-loads tool groups whose top tools have a usage score >= 0.5 in your `~/.sassymcp/tool_usage.json` history. Override with `SASSYMCP_GROUPS=core,android,system` env var, or `SASSYMCP_LOAD_ALL=1` for all 257.")
    sections.append("")

    return "\n".join(sections)


def _generate_auth_token() -> str:
    """Generate a cryptographically secure auth token."""
    return secrets.token_urlsafe(32)


def register(server):
    """Register setup wizard tools."""

    @server.tool()
    async def sassy_setup_wizard(
        role: str = "developer",
        expertise_level: str = "senior",
        specializations: str = "",
        languages: str = "",
        frameworks: str = "",
        systems: str = "",
        projects: str = "",
        communication_style: str = "terse",
        security_posture: str = "standard",
        mcp_clients: str = "",
        has_android: bool = False,
        has_linux: bool = False,
        notes: str = "",
    ) -> str:
        """First-run setup wizard. Generates ~/.sassymcp/persona.md from your answers.

        Call with your profile to personalize SassyMCP. All fields optional.

        role: developer | sysadmin | security | devops | data | designer | manager | other
        expertise_level: junior | mid | senior | principal | staff
        specializations: Comma-separated areas (e.g. "web security, cloud infra, mobile")
        languages: Comma-separated (e.g. "Python, Rust, TypeScript, Go")
        frameworks: Comma-separated (e.g. "React, FastAPI, Cloudflare Workers")
        systems: Newline-separated "hostname — OS — role" entries
        projects: Newline-separated "name — status — description" entries
        communication_style: terse | balanced | verbose
        security_posture: standard | hardened | paranoid
        mcp_clients: Which AI tools connect (e.g. "Claude Desktop, Cursor, Grok Desktop")
        has_android: True if the user has an Android phone to control (installs adb + scrcpy)
        has_linux: True if the user works with a Linux server or WSL (installs plink)
        notes: Anything else the AI should know about how you work
        """
        answers = {
            "role": role,
            "expertise_level": expertise_level,
            "specializations": specializations,
            "languages": languages,
            "frameworks": frameworks,
            "systems": systems,
            "projects": projects,
            "communication_style": communication_style,
            "security_posture": security_posture,
            "mcp_clients": mcp_clients,
            "has_android": has_android,
            "has_linux": has_linux,
            "notes": notes,
        }

        # Generate persona.md
        content = _generate_persona_md(answers)
        atomic_write_text(_PERSONA_FILE, content)

        # Update config
        config = _load_config()
        config["setup_complete"] = True
        config["setup_timestamp"] = time.time()
        config["setup_version"] = "1.0.0"
        config["has_android"] = has_android
        config["has_linux"] = has_linux
        _save_config(config)

        # Reload persona module so it picks up the new file
        try:
            import importlib
            import sassymcp.modules.persona as persona_mod
            importlib.reload(persona_mod)
            persona_mod.USER_CONTEXT = persona_mod._load_user_context()
            if hasattr(server, "_tool_manager"):
                persona_mod.register(server)
            logger.info("Persona module reloaded with new profile")
        except Exception as e:
            logger.warning(f"Persona reload failed (non-fatal): {e}")

        next_steps = [
            "Your profile is now active. The persona module will use it automatically.",
            "Call sassy_persona_context to verify your profile.",
            "Call sassy_persona_full to see complete operating parameters.",
            "Re-run sassy_setup_wizard anytime to update your profile.",
            "Run sassy_setup_tools(action='check') to see tool status.",
        ]

        # Identify tools to install based on user answers
        tools_to_install = ["tesseract"]  # always required
        if has_android:
            tools_to_install.extend(["adb", "scrcpy"])
        if has_linux:
            tools_to_install.append("plink")

        return json.dumps({
            "status": "setup_complete",
            "persona_file": str(_PERSONA_FILE),
            "profile": answers,
            "tools_to_install": tools_to_install,
            "next_steps": next_steps,
            "tool_install_hint": (
                f"Call sassy_setup_tools(action='install_required') to install tesseract (required). "
                + (f"Also run sassy_setup_tools(action='install', tool_name='adb') and tool_name='scrcpy' for Android. " if has_android else "")
                + (f"Also run sassy_setup_tools(action='install', tool_name='plink') for Linux/SSH." if has_linux else "")
            ),
        }, indent=2)

    @server.tool()
    async def sassy_setup_status() -> str:
        """Check setup status: is persona configured? Auth tokens? Config state?"""
        config = _load_config()

        persona_exists = _PERSONA_FILE.exists()
        persona_size = _PERSONA_FILE.stat().st_size if persona_exists else 0
        tokens_exist = _TOKENS_FILE.exists()
        auth_token_env = bool(os.environ.get("SASSYMCP_AUTH_TOKEN"))

        # Check what's configured
        status = {
            "setup_complete": config.get("setup_complete", False),
            "persona": {
                "exists": persona_exists,
                "size_bytes": persona_size,
                "path": str(_PERSONA_FILE),
            },
            "auth": {
                "env_token_set": auth_token_env,
                "tokens_file_exists": tokens_exist,
                "auth_active": auth_token_env or tokens_exist,
            },
            "config": {
                "path": str(_CONFIG_FILE),
                "keys": list(config.keys()),
            },
            "data_dir": str(_SASSYMCP_DIR),
            "files_in_data_dir": sorted([
                f.name for f in _SASSYMCP_DIR.iterdir()
            ]) if _SASSYMCP_DIR.exists() else [],
        }

        if not config.get("setup_complete"):
            status["action_required"] = (
                "Run sassy_setup_wizard to complete initial setup. "
                "This generates your persona profile for personalized AI interaction."
            )

        return json.dumps(status, indent=2)

    @server.tool()
    async def sassy_setup_generate_token(client_id: str = "default", scopes: str = "read,write") -> str:
        """Generate a new auth token for MCP client authentication.

        Creates a secure token and saves it to ~/.sassymcp/tokens.json.
        Use this token in SASSYMCP_AUTH_TOKEN env var or in client config.

        client_id: identifier for the client (e.g. "claude-desktop", "grok", "cursor")
        scopes: comma-separated permissions (read, write, admin)
        """
        token = _generate_auth_token()
        scope_list = [s.strip() for s in scopes.split(",") if s.strip()]

        # Load or create tokens file
        tokens_data = {"tokens": []}
        if _TOKENS_FILE.exists():
            try:
                tokens_data = json.loads(_TOKENS_FILE.read_text())
            except Exception:
                pass

        # Remove existing entry for same client_id
        tokens_data["tokens"] = [
            t for t in tokens_data.get("tokens", [])
            if t.get("client_id") != client_id
        ]

        # Add new token
        tokens_data["tokens"].append({
            "token": token,
            "client_id": client_id,
            "scopes": scope_list,
        })

        atomic_write_json(_TOKENS_FILE, tokens_data)

        # Lock down DACL/permissions so the file is owner-only.
        if os.name == "nt":
            try:
                from sassymcp.auth import _lockdown_windows_acl
                _lockdown_windows_acl(_TOKENS_FILE)
            except Exception as _e:
                logger.warning(f"Windows ACL lockdown skipped: {_e}")
        else:
            try:
                os.chmod(_TOKENS_FILE, 0o600)
            except OSError as _e:
                logger.warning(f"chmod 0600 on tokens.json failed: {_e}")

        return json.dumps({
            "token": token,
            "client_id": client_id,
            "scopes": scope_list,
            "saved_to": str(_TOKENS_FILE),
            "usage": {
                "env_var": f"set SASSYMCP_AUTH_TOKEN={token}",
                "header": f"Authorization: Bearer {token}",
                "query": f"?token={token}",
            },
            "note": "Store this token securely. It won't be shown again in full.",
        }, indent=2)

    # ── GitHub Token Setup ────────────────────────────────────────

    @server.tool()
    async def sassy_setup_github(action: str = "check", token: str = "") -> str:
        """Guide GitHub token setup. Opens browser, validates, saves.

        action: check | open_browser | save_token | skip
        token: the GitHub PAT to save (only for save_token action)
        """
        import webbrowser

        config = _load_config()

        if action == "check":
            gh_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
            if gh_token:
                # Validate against GitHub API
                try:
                    import httpx
                    resp = httpx.get("https://api.github.com/user",
                                     headers={"Authorization": f"Bearer {gh_token}"},
                                     timeout=10)
                    if resp.status_code == 200:
                        user = resp.json()
                        return json.dumps({
                            "status": "configured",
                            "github_user": user.get("login"),
                            "token_prefix": gh_token[:4] + "...",
                            "scopes": resp.headers.get("x-oauth-scopes", "unknown"),
                        })
                    return json.dumps({"status": "invalid_token", "http_status": resp.status_code,
                                       "hint": "Token exists but GitHub rejected it. Re-run with action=open_browser to create a new one."})
                except Exception as e:
                    return json.dumps({"status": "error", "error": str(e)})
            return json.dumps({
                "status": "not_configured",
                "hint": "No GITHUB_TOKEN found. Use action=open_browser to create one, or action=skip to skip.",
            })

        elif action == "open_browser":
            url = "https://github.com/settings/tokens?type=beta"
            try:
                webbrowser.open(url)
            except Exception:
                pass
            return json.dumps({
                "status": "browser_opened",
                "url": url,
                "instructions": [
                    "1. Click 'Generate new token' on the page that opened.",
                    "2. Give it a name like 'SassyMCP'.",
                    "3. Set expiration (90 days recommended for security).",
                    "4. Under 'Repository access', select 'All repositories' or specific repos.",
                    "5. Under 'Permissions', enable: Contents (Read/Write), Issues (Read/Write), Pull Requests (Read/Write), Metadata (Read).",
                    "6. Click 'Generate token' and copy the token.",
                    "7. Call sassy_setup_github with action='save_token' and token='ghp_your_token_here'.",
                ],
            })

        elif action == "save_token":
            if not token:
                return json.dumps({"error": "Provide the token parameter with your GitHub PAT."})
            if not (token.startswith("ghp_") or token.startswith("github_pat_") or len(token) > 20):
                return json.dumps({"error": "Invalid token format. GitHub tokens start with ghp_ or github_pat_"})

            # Validate
            try:
                import httpx
                resp = httpx.get("https://api.github.com/user",
                                 headers={"Authorization": f"Bearer {token}"},
                                 timeout=10)
                if resp.status_code != 200:
                    return json.dumps({"error": f"GitHub rejected the token (HTTP {resp.status_code}). Check and try again."})
                user = resp.json()
            except Exception as e:
                return json.dumps({"error": f"Could not validate token: {e}"})

            # Save to process env
            os.environ["GITHUB_TOKEN"] = token

            # Update config
            config["github_configured"] = True
            config["github_user"] = user.get("login")
            config["github_token_set"] = time.strftime('%Y-%m-%d %H:%M')
            _save_config(config)

            return json.dumps({
                "status": "saved",
                "github_user": user.get("login"),
                "scopes": resp.headers.get("x-oauth-scopes", "unknown"),
                "note": "Token active for this session. To persist across restarts, set GITHUB_TOKEN in your system environment or MCP client config.",
                "persistence_hint": {
                    "claude_desktop": 'Add "GITHUB_TOKEN": "your_token" to env section in claude_desktop_config.json',
                    "system": 'Run: setx GITHUB_TOKEN "your_token" in an admin terminal',
                },
            }, indent=2)

        elif action == "skip":
            config["github_configured"] = False
            config["github_skipped"] = True
            _save_config(config)
            return json.dumps({"status": "skipped", "note": "GitHub integration skipped. Run sassy_setup_github anytime to configure later."})

        return json.dumps({"error": f"Unknown action: {action}. Use: check, open_browser, save_token, skip"})

    # ── SSH Setup ────────────────────────────────────────────────

    @server.tool()
    async def sassy_setup_ssh(
        action: str = "check",
        host: str = "",
        user: str = "",
        password: str = "",
        key: str = "",
        session: str = "",
    ) -> str:
        """Guide SSH/Linux remote setup. Checks plink, saves creds, tests connection.

        action: check | save | test | skip
        host:     SSH hostname or IP                (for save action)
        user:     SSH username                      (for save action)
        password: SSH password                      (optional; fed via stdin
                  at test time, not on the argv list)
        key:      path to a .ppk private key        (preferred over password)
        session:  saved PuTTY session name          (carries host+user+key)

        Authentication priority at test time: session > key > Pageant >
        password-via-stdin. Pass at least ONE auth source on save, otherwise
        the call returns status=incomplete (no false 'saved' on empty creds).
        """
        import shutil

        config = _load_config()

        # Find plink
        plink = (os.environ.get("PLINK_PATH")
                 or shutil.which("plink")
                 or next((p for p in [
                     os.path.expandvars(r"%LOCALAPPDATA%\Temp\plink.exe"),
                     r"C:\Program Files\PuTTY\plink.exe",
                     r"C:\Program Files (x86)\PuTTY\plink.exe",
                     r"C:\ProgramData\chocolatey\bin\plink.exe",
                 ] if os.path.isfile(p)), None))

        if action == "check":
            ssh_host = os.environ.get("SSH_HOST")
            ssh_user = os.environ.get("SSH_USER")
            ssh_pass = os.environ.get("SSH_PASS")
            ssh_key = os.environ.get("SSH_KEY")
            ssh_session = os.environ.get("SSH_SESSION")
            has_auth = bool(ssh_session or ssh_key or ssh_pass)
            return json.dumps({
                "plink_found": plink is not None,
                "plink_path": plink,
                "plink_install_url": "https://www.chiark.greenend.org.uk/~sgtatham/putty/latest.html" if not plink else None,
                "ssh_host_set": bool(ssh_host),
                "ssh_user_set": bool(ssh_user),
                "ssh_pass_set": bool(ssh_pass),
                "ssh_key_set": bool(ssh_key),
                "ssh_session_set": bool(ssh_session),
                "configured": bool((ssh_session or (ssh_host and ssh_user)) and has_auth),
                "hint": (
                    "SSH configured. Use action=test to verify."
                    if (ssh_session or (ssh_host and ssh_user and has_auth))
                    else "Use action=save with host+user and AT LEAST ONE of: key, session, password."
                ),
            })

        elif action == "save":
            # A saved PuTTY session carries host+user+key in its own config,
            # so session alone is a complete configuration. Otherwise we
            # need host+user PLUS one of (key, password, session).
            if not session and (not host or not user):
                return json.dumps({
                    "status": "incomplete",
                    "error": "Provide host+user (or set session=<putty-session-name>).",
                })
            if not session and not key and not password:
                return json.dumps({
                    "status": "incomplete",
                    "error": (
                        "No auth source provided. Pass exactly one of: "
                        "key=<path-to-.ppk>, password=<...>, session=<name>."
                    ),
                    "hint": (
                        "Key-based auth is strongly preferred — your remote "
                        "may refuse password auth (publickey-only config)."
                    ),
                })

            if host:
                os.environ["SSH_HOST"] = host
            if user:
                os.environ["SSH_USER"] = user
            if password:
                os.environ["SSH_PASS"] = password
            if key:
                os.environ["SSH_KEY"] = key
            if session:
                os.environ["SSH_SESSION"] = session

            config["ssh_configured"] = True
            config["ssh_host"] = host or config.get("ssh_host", "")
            config["ssh_user"] = user or config.get("ssh_user", "")
            config["ssh_auth_mode"] = (
                "session" if session
                else ("key" if key else "password")
            )
            config["ssh_configured_at"] = time.strftime('%Y-%m-%d %H:%M')
            _save_config(config)

            return json.dumps({
                "status": "saved",
                "host": host or config.get("ssh_host"),
                "user": user or config.get("ssh_user"),
                "auth_mode": config["ssh_auth_mode"],
                "key_set": bool(key),
                "session_set": bool(session),
                "password_set": bool(password),
                "note": (
                    "Credentials active for this session. To persist across "
                    "restarts, set the matching SSH_HOST/SSH_USER/SSH_KEY/"
                    "SSH_SESSION/SSH_PASS env vars in your system env or "
                    "MCP client config."
                ),
                "next": "Use action=test to verify the connection.",
            })

        elif action == "test":
            ssh_host = os.environ.get("SSH_HOST")
            ssh_user = os.environ.get("SSH_USER")
            ssh_pass = os.environ.get("SSH_PASS")
            ssh_key = os.environ.get("SSH_KEY")
            ssh_session = os.environ.get("SSH_SESSION")
            if not (ssh_session or (ssh_host and ssh_user)):
                return json.dumps({"error": "SSH_HOST/SSH_USER (or SSH_SESSION) not set. Use action=save first."})
            if not (ssh_session or ssh_key or ssh_pass):
                return json.dumps({"error": "No SSH auth source set (need SSH_KEY, SSH_SESSION, or SSH_PASS). Use action=save first."})
            if not plink:
                return json.dumps({"error": "plink not found. Install PuTTY: https://www.chiark.greenend.org.uk/~sgtatham/putty/latest.html"})
            try:
                import asyncio as _asyncio

                # Mirror linux._ssh_exec_stream's resolution so the test
                # validates the same path real calls use. Password goes
                # via stdin so it isn't visible in the process list.
                argv: list[str] = [plink, "-ssh", "-batch"]
                stdin_payload: bytes | None = None
                if ssh_session:
                    argv += ["-load", ssh_session]
                    if ssh_host and ssh_user:
                        argv.append(f"{ssh_user}@{ssh_host}")
                    elif ssh_host:
                        argv.append(ssh_host)
                else:
                    if ssh_key:
                        argv += ["-i", ssh_key]
                    elif ssh_pass:
                        stdin_payload = (ssh_pass + "\n").encode("utf-8")
                    argv.append(f"{ssh_user}@{ssh_host}")
                argv += ["echo", "SassyMCP_SSH_OK"]

                proc = await _asyncio.create_subprocess_exec(
                    *argv,
                    stdin=_asyncio.subprocess.PIPE if stdin_payload is not None else _asyncio.subprocess.DEVNULL,
                    stdout=_asyncio.subprocess.PIPE,
                    stderr=_asyncio.subprocess.PIPE)
                if stdin_payload is not None:
                    try:
                        proc.stdin.write(stdin_payload)
                        await proc.stdin.drain()
                        proc.stdin.close()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                stdout, stderr = await _asyncio.wait_for(proc.communicate(), timeout=15)
                out = stdout.decode("utf-8", errors="replace").strip()
                if "SassyMCP_SSH_OK" in out:
                    return json.dumps({"status": "connected", "host": ssh_host or "(session)", "user": ssh_user or "(session)", "output": out})
                return json.dumps({"status": "failed", "stdout": out, "stderr": stderr.decode("utf-8", errors="replace").strip()})
            except Exception as e:
                return json.dumps({"status": "error", "error": str(e)})

        elif action == "skip":
            config["ssh_configured"] = False
            config["ssh_skipped"] = True
            _save_config(config)
            return json.dumps({"status": "skipped", "note": "SSH integration skipped. Run sassy_setup_ssh anytime to configure later."})

        return json.dumps({"error": f"Unknown action: {action}. Use: check, save, test, skip"})

    # ── Optional Tools Check ─────────────────────────────────────

    @server.tool()
    async def sassy_setup_check_tools() -> str:
        """Scan for external tools and report availability. Tesseract is required.

        Checks: nmap, Tesseract OCR (REQUIRED), adb, scrcpy, plink (PuTTY), Chrome/Chromium.
        Also checks Python packages: pytesseract, playwright.
        Use sassy_setup_tools for winget-based auto-install.
        """
        import shutil

        tools = {}

        # System tools
        tool_checks = {
            "nmap": {
                "search": ["nmap"],
                "url": "https://nmap.org/download.html",
                "used_by": "sassy_port_scan",
            },
            "tesseract": {
                "search": ["tesseract"],
                "extra_paths": [
                    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                ],
                "url": "https://github.com/tesseract-ocr/tesseract",
                "used_by": "sassy_screen_ocr, sassy_find_text_on_screen",
                "required": True,
            },
            "adb": {
                "search": ["adb"],
                "extra_paths": [
                    os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
                    r"C:\Android\platform-tools\adb.exe",
                ],
                "url": "https://developer.android.com/tools/releases/platform-tools",
                "used_by": "all sassy_adb_* tools",
            },
            "scrcpy": {
                "search": ["scrcpy"],
                "extra_paths": [
                    r"C:\scrcpy\scrcpy.exe",
                    os.path.expandvars(r"%USERPROFILE%\scrcpy\scrcpy.exe"),
                ],
                "url": "https://github.com/Genymobile/scrcpy/releases",
                "used_by": "sassy_scrcpy_start, sassy_scrcpy_record",
            },
            "plink": {
                "search": ["plink"],
                "extra_paths": [
                    r"C:\Program Files\PuTTY\plink.exe",
                    r"C:\Program Files (x86)\PuTTY\plink.exe",
                    r"C:\ProgramData\chocolatey\bin\plink.exe",
                ],
                "url": "https://www.chiark.greenend.org.uk/~sgtatham/putty/latest.html",
                "used_by": "sassy_linux_exec",
            },
            "chrome": {
                "search": ["chrome", "chromium"],
                "extra_paths": [
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                ],
                "url": "https://www.google.com/chrome/",
                "used_by": "sassy_url_screenshot (via playwright)",
            },
        }

        for name, info in tool_checks.items():
            found = None
            for cmd in info["search"]:
                found = shutil.which(cmd)
                if found:
                    break
            if not found:
                for p in info.get("extra_paths", []):
                    if os.path.isfile(p):
                        found = p
                        break
            tools[name] = {
                "installed": found is not None,
                "path": found,
                "required": info.get("required", False),
                "install_url": info["url"] if not found else None,
                "used_by": info["used_by"],
            }

        # Python packages
        packages = {}
        for pkg in ["pytesseract", "playwright", "watchdog"]:
            try:
                __import__(pkg)
                packages[pkg] = {"installed": True}
            except ImportError:
                pip_cmd = f"pip install {pkg}"
                if pkg == "playwright":
                    pip_cmd += " && playwright install chromium"
                packages[pkg] = {"installed": False, "install": pip_cmd}

        missing_required = [k for k, v in tools.items() if not v["installed"] and v.get("required")]
        return json.dumps({
            "system_tools": tools,
            "python_packages": packages,
            "summary": {
                "installed": [k for k, v in tools.items() if v["installed"]],
                "missing_required": missing_required,
                "missing_optional": [k for k, v in tools.items() if not v["installed"] and not v.get("required")],
            },
            "hint": (
                "Run sassy_setup_tools(action='install_required') to auto-install missing required tools (tesseract)."
                if missing_required else
                "All required tools present."
            ),
        }, indent=2)

    # ── License Management ───────────────────────────────────────

    @server.tool()
    async def sassy_setup_license(key: str = "", action: str = "status") -> str:
        """Manage your SassyMCP license. Activate a Pro key, check status, or deactivate.

        action: status | activate | deactivate
        key: license key string (required for activate action)
        """
        from sassymcp.license import validate_license, save_license, remove_license, LICENSE_FILE

        if action == "status":
            result = validate_license()
            tier = result.get("tier", "free")
            info = {
                "tier": tier,
                "valid": result.get("valid", False),
                "email": result.get("email"),
                "expires": result.get("expires"),
                "license_file": str(LICENSE_FILE),
                "license_exists": LICENSE_FILE.exists(),
            }
            if tier == "free" and not result.get("valid"):
                info["upgrade"] = {
                    "url": "https://sassyconsultingllc.com/sassymcp",
                    "price": "$29/mo",
                    "what_you_get": "255 tools, persistent memory, dynamic vision, phone control, "
                                    "GitHub full API, operational hooks, self-modification, and more.",
                }
            return json.dumps(info, indent=2)

        elif action == "activate":
            if not key:
                return json.dumps({"error": "Provide the key parameter with your license key.",
                                   "get_key": "https://sassyconsultingllc.com/sassymcp"})
            result = save_license(key)
            if result.get("valid"):
                return json.dumps({
                    "status": "activated",
                    "tier": result["tier"],
                    "email": result.get("email"),
                    "expires": result.get("expires"),
                    "note": "Restart the server to load all Pro tools, or call sassy_selfmod_restart().",
                }, indent=2)
            return json.dumps({
                "status": "failed",
                "reason": result.get("reason"),
                "hint": "Check the key and try again. Keys start with sassy_pro_ or sassy_forensics_.",
            })

        elif action == "deactivate":
            remove_license()
            return json.dumps({
                "status": "deactivated",
                "tier": "free",
                "note": "Downgraded to free tier. Restart to apply.",
            })

        return json.dumps({"error": f"Unknown action: {action}. Use: status, activate, deactivate"})

    # ── Updated setup_status with integration fields ─────────────

    # Patch the existing sassy_setup_status to add integration checks
    _original_setup_status = sassy_setup_status

    @server.tool()
    async def sassy_setup_status() -> str:
        """Check setup status: is persona configured? Auth tokens? Config state?"""
        config = _load_config()

        persona_exists = _PERSONA_FILE.exists()
        persona_size = _PERSONA_FILE.stat().st_size if persona_exists else 0
        tokens_exist = _TOKENS_FILE.exists()
        auth_token_env = bool(os.environ.get("SASSYMCP_AUTH_TOKEN"))

        # Integration status
        gh_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
        ssh_host = os.environ.get("SSH_HOST")

        status = {
            "setup_complete": config.get("setup_complete", False),
            "persona": {
                "exists": persona_exists,
                "size_bytes": persona_size,
                "path": str(_PERSONA_FILE),
            },
            "auth": {
                "env_token_set": auth_token_env,
                "tokens_file_exists": tokens_exist,
                "auth_active": auth_token_env or tokens_exist,
            },
            "integrations": {
                "github_configured": bool(gh_token),
                "github_user": config.get("github_user"),
                "ssh_configured": bool(ssh_host and os.environ.get("SSH_USER")),
                "ssh_host": ssh_host,
            },
            "config": {
                "path": str(_CONFIG_FILE),
                "keys": list(config.keys()),
            },
            "data_dir": str(_SASSYMCP_DIR),
            "files_in_data_dir": sorted([
                f.name for f in _SASSYMCP_DIR.iterdir()
            ]) if _SASSYMCP_DIR.exists() else [],
        }

        if not config.get("setup_complete"):
            status["action_required"] = (
                "Run sassy_setup_wizard to complete initial setup. "
                "Then use sassy_setup_github and sassy_setup_ssh for integrations. "
                "Run sassy_setup_check_tools to see what optional tools are available."
            )

        return json.dumps(status, indent=2)

    # Log setup status on load
    if _is_setup_complete():
        logger.info("Setup complete (persona.md exists)")
    else:
        logger.info("FIRST RUN: persona.md not found. Call sassy_setup_wizard to configure.")
