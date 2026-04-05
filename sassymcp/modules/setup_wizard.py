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

logger = logging.getLogger("sassymcp.setup")

_SASSYMCP_DIR = Path.home() / ".sassymcp"
_PERSONA_FILE = _SASSYMCP_DIR / "persona.md"
_CONFIG_FILE = _SASSYMCP_DIR / "config.json"
_TOKENS_FILE = _SASSYMCP_DIR / "tokens.json"


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
    _SASSYMCP_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(json.dumps(config, indent=2))


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
            "notes": notes,
        }

        # Generate persona.md
        content = _generate_persona_md(answers)
        _SASSYMCP_DIR.mkdir(parents=True, exist_ok=True)
        _PERSONA_FILE.write_text(content, encoding="utf-8")

        # Update config
        config = _load_config()
        config["setup_complete"] = True
        config["setup_timestamp"] = time.time()
        config["setup_version"] = "1.0.0"
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

        return json.dumps({
            "status": "setup_complete",
            "persona_file": str(_PERSONA_FILE),
            "profile": answers,
            "next_steps": [
                "Your profile is now active. The persona module will use it automatically.",
                "Call sassy_persona_context to verify your profile.",
                "Call sassy_persona_full to see complete operating parameters.",
                "Re-run sassy_setup_wizard anytime to update your profile.",
            ],
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

        _SASSYMCP_DIR.mkdir(parents=True, exist_ok=True)
        _TOKENS_FILE.write_text(json.dumps(tokens_data, indent=2))

        # Set restrictive permissions on Unix
        if os.name != "nt":
            os.chmod(_TOKENS_FILE, 0o600)

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
    ) -> str:
        """Guide SSH/Linux remote setup. Checks plink, saves creds, tests connection.

        action: check | save | test | skip
        host: SSH hostname or IP (for save action)
        user: SSH username (for save action)
        password: SSH password (for save action)
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
            return json.dumps({
                "plink_found": plink is not None,
                "plink_path": plink,
                "plink_install_url": "https://www.chiark.greenend.org.uk/~sgtatham/putty/latest.html" if not plink else None,
                "ssh_host_set": bool(ssh_host),
                "ssh_user_set": bool(ssh_user),
                "ssh_pass_set": bool(ssh_pass),
                "configured": bool(ssh_host and ssh_user and ssh_pass),
                "hint": "Use action=save with host, user, password to configure." if not (ssh_host and ssh_user) else "SSH configured. Use action=test to verify.",
            })

        elif action == "save":
            if not host or not user:
                return json.dumps({"error": "Provide host and user parameters. password is also required for plink."})
            os.environ["SSH_HOST"] = host
            os.environ["SSH_USER"] = user
            if password:
                os.environ["SSH_PASS"] = password

            config["ssh_configured"] = True
            config["ssh_host"] = host
            config["ssh_user"] = user
            config["ssh_configured_at"] = time.strftime('%Y-%m-%d %H:%M')
            _save_config(config)

            return json.dumps({
                "status": "saved",
                "host": host,
                "user": user,
                "password_set": bool(password),
                "note": "Credentials active for this session. To persist, set SSH_HOST/SSH_USER/SSH_PASS in system env or MCP client config.",
                "next": "Use action=test to verify the connection.",
            })

        elif action == "test":
            ssh_host = os.environ.get("SSH_HOST")
            ssh_user = os.environ.get("SSH_USER")
            ssh_pass = os.environ.get("SSH_PASS")
            if not all([ssh_host, ssh_user, ssh_pass]):
                return json.dumps({"error": "SSH credentials not set. Use action=save first."})
            if not plink:
                return json.dumps({"error": "plink not found. Install PuTTY: https://www.chiark.greenend.org.uk/~sgtatham/putty/latest.html"})
            try:
                import asyncio as _asyncio
                proc = await _asyncio.create_subprocess_exec(
                    plink, "-ssh", "-pw", ssh_pass, "-batch", f"{ssh_user}@{ssh_host}", "echo", "SassyMCP_SSH_OK",
                    stdout=_asyncio.subprocess.PIPE, stderr=_asyncio.subprocess.PIPE)
                stdout, stderr = await _asyncio.wait_for(proc.communicate(), timeout=15)
                out = stdout.decode("utf-8", errors="replace").strip()
                if "SassyMCP_SSH_OK" in out:
                    return json.dumps({"status": "connected", "host": ssh_host, "user": ssh_user, "output": out})
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
        """Scan for optional tools and report availability with install URLs.

        Checks: nmap, Tesseract OCR, adb, scrcpy, plink (PuTTY), Chrome/Chromium.
        Also checks Python packages: pytesseract, playwright.
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

        return json.dumps({
            "system_tools": tools,
            "python_packages": packages,
            "summary": {
                "installed": [k for k, v in tools.items() if v["installed"]],
                "missing": [k for k, v in tools.items() if not v["installed"]],
            },
        }, indent=2)

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
