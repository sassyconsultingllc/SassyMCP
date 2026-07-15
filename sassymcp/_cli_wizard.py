# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-MIKZU6WCW3DX
"""Interactive CLI wizard for sassymcp.exe.

Two entry points:
  - `sassymcp setup`  — explicit subcommand, always opens the menu
  - `sassymcp` with TTY stdin AND no persona.md yet (first run) — auto-
    opens the menu so a buyer who double-clicked the exe gets a UI
    instead of a logging window.

Existing users with a persona.md still get the auto-HTTP-server
behavior on bare invocation — this avoids surprising anyone who was
relying on `sassymcp.exe` to start the server in one click.

The wizard is intentionally stdlib-only (no questionary / rich /
prompt_toolkit). PyInstaller-frozen exes are big enough already and
the menu is short enough that plain `input()` is plenty.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


# ── Terminal helpers ──────────────────────────────────────────────────

def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    # Windows Terminal, ConEmu, modern conhost all set this. The bare
    # cmd.exe on Win10 doesn't, but it does support ANSI when VT
    # processing is on. Be conservative — color is a nice-to-have.
    return os.environ.get("TERM") not in (None, "dumb") or sys.platform != "win32"


def _color(s: str, code: str) -> str:
    if not _supports_color():
        return s
    return f"\033[{code}m{s}\033[0m"


def _bold(s: str) -> str:
    return _color(s, "1")


def _dim(s: str) -> str:
    return _color(s, "2")


def _green(s: str) -> str:
    return _color(s, "32")


def _yellow(s: str) -> str:
    return _color(s, "33")


def _red(s: str) -> str:
    return _color(s, "31")


def _hr():
    print(_dim("─" * 60))


def _prompt(msg: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    try:
        v = input(f"{msg}{hint}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""
    return v or default


def _confirm(msg: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    v = _prompt(f"{msg} ({d})")
    if not v:
        return default
    return v.lower().startswith("y")


def _pause(msg: str = "Press Enter to continue"):
    try:
        input(_dim(msg) + "... ")
    except (EOFError, KeyboardInterrupt):
        pass


# ── Status ────────────────────────────────────────────────────────────

def _gather_status() -> dict:
    """Collect the bits the buyer cares about: version, license state,
    persona configured, token count, billing oracle URL. All reads are
    cheap and tolerate missing files.
    """
    from sassymcp import __version__
    from sassymcp._paths import (
        PERSONA_FILE, LICENSE_FILE, TOKENS_FILE, CONFIG_FILE, HOME,
    )

    info: dict = {
        "version": __version__,
        "home": str(HOME),
        "persona_exists": PERSONA_FILE.exists(),
        "license_exists": LICENSE_FILE.exists(),
        "tokens_exist": TOKENS_FILE.exists(),
    }

    try:
        from sassymcp.license import validate_license
        v = validate_license()
        info["tier"] = v.get("tier", "free")
        info["addons"] = v.get("addons", [])
        info["license_valid"] = v.get("valid", False)
        info["license_reason"] = v.get("reason")
        info["license_email"] = v.get("email")
    except Exception as e:
        info["tier"] = "free"
        info["addons"] = []
        info["license_valid"] = False
        info["license_reason"] = f"load_error:{e}"

    if TOKENS_FILE.exists():
        try:
            info["tokens_count"] = len(json.loads(TOKENS_FILE.read_text()).get("tokens", []))
        except Exception:
            info["tokens_count"] = 0
    else:
        info["tokens_count"] = 0

    info["billing_base"] = os.environ.get("SASSYMCP_BILLING_BASE", "") or _dim("(not set)")
    return info


def _print_banner(info: dict):
    print()
    print(_bold(f"  SassyMCP v{info['version']}  ") + _dim("interactive setup"))
    _hr()
    tier = info["tier"]
    addons = info["addons"]
    label = tier + ("+" + ",".join(addons) if addons else "")
    if info["license_valid"]:
        tier_disp = _green(label)
    else:
        tier_disp = _yellow(label)
    print(f"  tier         : {tier_disp}")
    if not info["license_valid"] and info.get("license_reason"):
        print(f"  license      : {_dim(info['license_reason'])}")
    if info["license_email"]:
        print(f"  registered to: {info['license_email']}")
    print(f"  persona      : "
          + (_green('configured') if info['persona_exists'] else _yellow('not yet set up')))
    print(f"  auth tokens  : {info['tokens_count']}")
    print(f"  billing URL  : {info['billing_base']}")
    print(f"  state dir    : {_dim(info['home'])}")
    _hr()


# ── Menu actions ──────────────────────────────────────────────────────

def _action_install(_info: dict):
    print()
    print(_bold("Auto-detect AI agents and register SassyMCP"))
    print(_dim("Scans Claude Desktop, Claude Code, Cursor, Cline, Continue, Windsurf,"))
    print(_dim("Zed, VS Code, Grok Desktop and adds an mcpServers entry to each."))
    print()
    if not _confirm("Run install --client auto?"):
        return
    from sassymcp.install import main as install_main
    rc = install_main(["--client", "auto"])
    print()
    if rc == 0:
        print(_green("Install complete."))
    else:
        print(_red(f"Install exited with code {rc}."))
    _pause()


def _action_install_specific(_info: dict):
    print()
    print(_bold("Configure one specific AI agent"))
    choices = ["claude-desktop", "claude-code", "cursor", "cline", "continue",
               "windsurf", "zed", "vscode", "grok"]
    for i, c in enumerate(choices, 1):
        print(f"  {i:>2}) {c}")
    print()
    sel = _prompt("Pick a number (or blank to cancel)")
    if not sel:
        return
    try:
        client = choices[int(sel) - 1]
    except (ValueError, IndexError):
        print(_red("Invalid selection."))
        _pause()
        return
    from sassymcp.install import main as install_main
    rc = install_main(["--client", client])
    print()
    print(_green("Done.") if rc == 0 else _red(f"Exit code {rc}"))
    _pause()


def _action_license(info: dict):
    print()
    print(_bold("License management"))
    print()
    print("  1) Activate a LemonSqueezy license key")
    print("  2) Show full license status (JSON)")
    print("  3) Re-validate against LemonSqueezy now")
    print("  4) Deactivate (free this machine's seat)")
    print("  5) Open purchase page in browser")
    print("  6) Back")
    sel = _prompt("Pick a number")
    if sel == "1":
        key = _prompt("Paste your LS license key")
        if not key:
            return
        from sassymcp.license import activate_via_lemonsqueezy
        result = activate_via_lemonsqueezy(key)
        print()
        if result.get("valid"):
            print(_green(f"Activated: tier={result['tier']} addons={result.get('addons')}"))
            if result.get("warning"):
                print(_yellow(result["warning"]))
        else:
            print(_red(f"Failed: {result.get('reason')} — {result.get('detail') or ''}"))
        _pause()
    elif sel == "2":
        print(json.dumps(info, indent=2, default=str))
        _pause()
    elif sel == "3":
        import asyncio
        from sassymcp.license import LICENSE_FILE, _ls_revalidate, validate_license
        if not LICENSE_FILE.exists():
            print(_yellow("No license file — nothing to validate."))
            _pause()
            return
        data = json.loads(LICENSE_FILE.read_text())
        ls_key = data.get("ls_license_key")
        ls_inst = data.get("ls_instance_id")
        if not (ls_key and ls_inst):
            print(_dim("Legacy / self-signed key — no LS to re-check against."))
            _pause()
            return
        asyncio.run(_ls_revalidate(data, ls_key, ls_inst))
        post = validate_license()
        print(_green(f"Re-checked: tier={post.get('tier')} valid={post.get('valid')}"))
        _pause()
    elif sel == "4":
        if not _confirm("Deactivate this machine's seat?", default=False):
            return
        from sassymcp.license import deactivate_via_lemonsqueezy
        r = deactivate_via_lemonsqueezy()
        print(_green(f"Deactivated: {r.get('status')}"))
        _pause()
    elif sel == "5":
        import webbrowser
        webbrowser.open("https://sassyconsultingllc.com/store")
    elif sel == "6":
        return


def _action_tokens(_info: dict):
    print()
    print(_bold("Auth tokens"))
    print()
    print("  1) List tokens")
    print("  2) Generate a new token")
    print("  3) Back")
    sel = _prompt("Pick a number")
    if sel == "1":
        from sassymcp._paths import TOKENS_FILE
        if not TOKENS_FILE.exists():
            print(_dim("No tokens file yet — generate one to get started."))
        else:
            for t in json.loads(TOKENS_FILE.read_text()).get("tokens", []):
                tok = t.get("token", "")
                disp = tok[:8] + "…" + tok[-4:] if len(tok) > 16 else tok
                print(f"  {t.get('client_id', '?'):<30} scopes={t.get('scopes', [])} {disp}")
        _pause()
    elif sel == "2":
        client_id = _prompt("client_id (e.g. claude-desktop, cursor)")
        if not client_id:
            return
        scopes = _prompt("scopes (comma-separated)", default="read,write")
        # Reuse the CLI subcommand instead of re-implementing token
        # generation — it already handles ACL lockdown + atomic write.
        from sassymcp.server import _cli_generate_token
        _cli_generate_token([
            "--client-id", client_id,
            "--scopes", scopes,
        ])
        _pause()


def _action_run_server(_info: dict):
    print()
    print(_bold("Starting HTTP server on 127.0.0.1:21001"))
    print(_dim("Press Ctrl+C to stop."))
    # Returning a sentinel lets main() know to start the server. We
    # don't start it from inside the wizard so the wizard's stdout
    # banners don't tangle with the server's startup log.
    return "run_server"


# ── Menu loop ─────────────────────────────────────────────────────────

_MENU = [
    ("Quick install — auto-detect AI agents and configure all", _action_install),
    ("Configure a specific AI agent", _action_install_specific),
    ("License (activate / status / re-check / deactivate)", _action_license),
    ("Auth tokens (list / generate)", _action_tokens),
    ("Run as HTTP server", _action_run_server),
    ("Exit", None),
]


def run_wizard() -> str | None:
    """Drive the interactive wizard. Returns one of:
      - "run_server" if the user picked option 5
      - None on any other exit (clean quit / Ctrl+C)
    The caller (server.py main) decides what to do with that signal.
    """
    while True:
        info = _gather_status()
        _print_banner(info)
        for i, (label, _action) in enumerate(_MENU, 1):
            print(f"  {i}) {label}")
        print()
        sel = _prompt("Choose an option")
        if not sel:
            continue
        try:
            idx = int(sel) - 1
            label, action = _MENU[idx]
        except (ValueError, IndexError):
            print(_red("Pick a number from the list."))
            continue
        if action is None:
            return None
        result = action(info)
        if result == "run_server":
            return "run_server"
