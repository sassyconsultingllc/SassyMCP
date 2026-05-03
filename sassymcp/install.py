"""sassymcp install — detect every installed MCP client on the box and
patch its config to register sassymcp.exe.

Public CLI:
    sassymcp install                  # detect all, patch all
    sassymcp install --client claude  # only one client
    sassymcp install --dry-run        # show what would change
    sassymcp install --uninstall      # remove sassymcp from every config
    sassymcp install --exe-path PATH  # override the exe path
    sassymcp install --json           # machine-readable output
    sassymcp install --auto-other     # internal: skip claude, used by DXT first-run

Used by:
    - the standalone CLI (project.scripts entry sassymcp-install)
    - the DXT first-run hook (auto-other mode)
    - the VS Code extension (calls the CLI as a subprocess)

All config writes go through sassymcp._atomic.atomic_write_json so concurrent
invocations (e.g., DXT first-run firing while user runs CLI manually) cannot
corrupt the targets.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from sassymcp._atomic import atomic_write_json


@dataclass
class ClientInfo:
    """Where a client's MCP config lives and how to patch it."""
    name: str                   # human display name e.g. "Claude Desktop"
    short_name: str             # CLI selector e.g. "claude"
    config_path: Path           # absolute resolved path to the config json
    schema: str                 # "mcpServers" | "experimental.modelContextProtocolServers" | "context_servers"
    detected: bool              # True if config_path exists OR the parent dir exists
    notes: str = ""             # extra info (e.g. "uses HTTP transport")


@dataclass
class PatchResult:
    client: str
    config_path: Path
    action: str                 # "added" | "updated" | "noop" | "uninstalled" | "skipped" | "error"
    backup_path: Path | None = None
    detail: str = ""


# --- Detection ------------------------------------------------------------

def _appdata() -> Path:
    """Windows %APPDATA% or POSIX ~/.config equivalent."""
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))


def _claude_desktop_config() -> Path:
    if os.name == "nt":
        return _appdata() / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def _vscode_user_mcp() -> Path:
    if os.name == "nt":
        return _appdata() / "Code" / "User" / "mcp.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Code" / "User" / "mcp.json"
    return Path.home() / ".config" / "Code" / "User" / "mcp.json"


def _cursor_mcp() -> Path:
    return Path.home() / ".cursor" / "mcp.json"


def _windsurf_mcp() -> Path:
    return Path.home() / ".codeium" / "windsurf" / "mcp_config.json"


def _continue_config() -> Path:
    return Path.home() / ".continue" / "config.json"


def _cline_mcp() -> Path:
    if os.name == "nt":
        return _appdata() / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json"
    return Path.home() / ".config" / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json"


def _zed_settings() -> Path:
    return Path.home() / ".config" / "zed" / "settings.json"


def _grok_desktop_config() -> Path:
    if os.name == "nt":
        return _appdata() / "GrokDesktop" / "config.json"
    return Path.home() / ".config" / "GrokDesktop" / "config.json"


# Registry of all known clients. Order = display order in CLI output.
# path_fn is stored as the function name string so monkeypatching the module
# attribute (e.g. in tests) is honoured at detect-time.
_CLIENT_REGISTRY: list[tuple[str, str, str, str, str]] = [
    # (name, short_name, path_fn_name, schema, notes)
    ("Claude Desktop", "claude", "_claude_desktop_config", "mcpServers", ""),
    ("VS Code (Copilot)", "vscode", "_vscode_user_mcp", "servers", ""),
    ("Cursor", "cursor", "_cursor_mcp", "mcpServers", ""),
    ("Windsurf", "windsurf", "_windsurf_mcp", "mcpServers", ""),
    ("Continue", "continue", "_continue_config", "experimental.modelContextProtocolServers", "merges into experimental section"),
    ("Cline (VS Code)", "cline", "_cline_mcp", "mcpServers", "uses VS Code globalStorage"),
    ("Zed", "zed", "_zed_settings", "context_servers", "merges into top-level settings"),
    ("Grok Desktop", "grok", "_grok_desktop_config", "mcpServers", "uses HTTP transport"),
]


def detect_clients() -> list[ClientInfo]:
    """Return ClientInfo for each known MCP client. detected=True iff its
    config file or parent dir exists.
    """
    import sys as _sys
    _mod = _sys.modules[__name__]
    out: list[ClientInfo] = []
    for name, short, path_fn_name, schema, notes in _CLIENT_REGISTRY:
        try:
            path_fn = getattr(_mod, path_fn_name)
            p = path_fn()
        except Exception:
            continue
        detected = p.exists() or p.parent.exists()
        out.append(ClientInfo(
            name=name, short_name=short, config_path=p,
            schema=schema, detected=detected, notes=notes,
        ))
    return out


def find_self_exe() -> Path:
    """Locate sassymcp.exe.

    Frozen (PyInstaller): sys.executable is the exe.
    Non-frozen (wheel/dev): look for sassymcp on PATH; fall back to the
    sassymcp module's parent dir.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    import shutil
    found = shutil.which("sassymcp") or shutil.which("sassymcp.exe")
    if found:
        return Path(found)
    # Dev fallback: assume sibling to the package
    pkg_dir = Path(__file__).resolve().parent.parent
    candidates = [pkg_dir / "sassymcp.exe", pkg_dir / "dist" / "sassymcp.exe"]
    for c in candidates:
        if c.exists():
            return c
    raise RuntimeError("Could not locate sassymcp executable. Pass --exe-path explicitly.")


# --- Patching -------------------------------------------------------------

_BACKUP_SUFFIX_FORMAT = ".sassymcp-backup-%Y%m%d-%H%M%S"


def _server_entry_for(client: ClientInfo, exe_path: Path) -> dict:
    """Build the JSON object that goes into the client's server table."""
    entry = {
        "command": exe_path.as_posix(),
        "args": [],
        "env": {"SASSYMCP_LOAD_ALL": "1"},
    }
    if client.short_name == "grok":
        entry["args"] = ["--http", "--host", "127.0.0.1", "--port", "21001"]
    return entry


def _backup_path_for(config_path: Path) -> Path:
    return config_path.with_suffix(config_path.suffix + time.strftime(_BACKUP_SUFFIX_FORMAT))


def _ensure_backup(config_path: Path, *, dry_run: bool) -> Path | None:
    """If config exists and no .sassymcp-backup-* sibling exists, take a
    timestamped backup. Idempotent: a re-run after a backup exists is a noop.
    """
    if not config_path.exists():
        return None
    siblings = list(config_path.parent.glob(config_path.name + ".sassymcp-backup-*"))
    if siblings:
        return siblings[0]  # already backed up
    backup = _backup_path_for(config_path)
    if not dry_run:
        backup.write_bytes(config_path.read_bytes())
    return backup


def _load_existing_config(config_path: Path) -> dict:
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        # Refuse to clobber a corrupt config; surface to the caller.
        raise


def patch_client(client: ClientInfo, exe_path: Path, *, dry_run: bool = False) -> PatchResult:
    """Add or update sassymcp in the client's config. Idempotent."""
    if not client.detected:
        return PatchResult(client=client.name, config_path=client.config_path,
                           action="skipped", detail="client not detected")

    try:
        cfg = _load_existing_config(client.config_path)
    except json.JSONDecodeError as e:
        return PatchResult(client=client.name, config_path=client.config_path,
                           action="error", detail=f"existing config is corrupt JSON: {e}")

    entry = _server_entry_for(client, exe_path)
    action = _apply_server_entry(cfg, client, entry)

    if action == "noop":
        return PatchResult(client=client.name, config_path=client.config_path, action="noop")

    backup = _ensure_backup(client.config_path, dry_run=dry_run)
    if not dry_run:
        atomic_write_json(client.config_path, cfg)

    return PatchResult(client=client.name, config_path=client.config_path,
                       action=action, backup_path=backup)


def unpatch_client(client: ClientInfo, *, dry_run: bool = False) -> PatchResult:
    """Remove sassymcp from the client's config, leaving other servers alone."""
    if not client.config_path.exists():
        return PatchResult(client=client.name, config_path=client.config_path,
                           action="skipped", detail="config file does not exist")

    try:
        cfg = _load_existing_config(client.config_path)
    except json.JSONDecodeError as e:
        return PatchResult(client=client.name, config_path=client.config_path,
                           action="error", detail=f"existing config is corrupt JSON: {e}")

    removed = _remove_server_entry(cfg, client)
    if not removed:
        return PatchResult(client=client.name, config_path=client.config_path, action="noop")

    backup = _ensure_backup(client.config_path, dry_run=dry_run)
    if not dry_run:
        atomic_write_json(client.config_path, cfg)

    return PatchResult(client=client.name, config_path=client.config_path,
                       action="uninstalled", backup_path=backup)


def _apply_server_entry(cfg: dict, client: ClientInfo, entry: dict) -> str:
    """In-place mutate cfg to add/update sassymcp's entry. Return action."""
    if client.schema == "mcpServers":
        servers = cfg.setdefault("mcpServers", {})
        if servers.get("sassymcp") == entry:
            return "noop"
        action = "updated" if "sassymcp" in servers else "added"
        servers["sassymcp"] = entry
        return action

    if client.schema == "servers":  # VS Code mcp.json
        servers = cfg.setdefault("servers", {})
        if servers.get("sassymcp") == entry:
            return "noop"
        action = "updated" if "sassymcp" in servers else "added"
        servers["sassymcp"] = entry
        return action

    if client.schema == "experimental.modelContextProtocolServers":
        experimental = cfg.setdefault("experimental", {})
        servers = experimental.setdefault("modelContextProtocolServers", [])
        new_entry = {"transport": {"type": "stdio", "command": entry["command"], "args": entry["args"]}}
        for i, s in enumerate(servers):
            if (isinstance(s, dict) and isinstance(s.get("transport"), dict)
                    and "sassymcp" in str(s["transport"].get("command", "")).lower()):
                if s == new_entry:
                    return "noop"
                servers[i] = new_entry
                return "updated"
        servers.append(new_entry)
        return "added"

    if client.schema == "context_servers":  # Zed
        servers = cfg.setdefault("context_servers", {})
        zed_entry = {"command": {"path": entry["command"], "args": entry["args"], "env": entry["env"]}}
        if servers.get("sassymcp") == zed_entry:
            return "noop"
        action = "updated" if "sassymcp" in servers else "added"
        servers["sassymcp"] = zed_entry
        return action

    raise ValueError(f"Unknown schema: {client.schema!r}")


def _remove_server_entry(cfg: dict, client: ClientInfo) -> bool:
    """In-place mutate cfg to remove sassymcp. Return True if anything was removed."""
    if client.schema in ("mcpServers", "servers"):
        servers = cfg.get("mcpServers" if client.schema == "mcpServers" else "servers", {})
        return servers.pop("sassymcp", None) is not None

    if client.schema == "experimental.modelContextProtocolServers":
        experimental = cfg.get("experimental", {})
        servers = experimental.get("modelContextProtocolServers", [])
        before = len(servers)
        experimental["modelContextProtocolServers"] = [
            s for s in servers
            if not (isinstance(s, dict) and isinstance(s.get("transport"), dict)
                    and "sassymcp" in str(s["transport"].get("command", "")).lower())
        ]
        return len(experimental["modelContextProtocolServers"]) < before

    if client.schema == "context_servers":
        servers = cfg.get("context_servers", {})
        return servers.pop("sassymcp", None) is not None

    return False


# --- CLI ------------------------------------------------------------------

def _print_table(results: list[PatchResult]) -> None:
    if not results:
        print("No clients detected.")
        return
    name_w = max(len(r.client) for r in results)
    path_w = min(60, max(len(str(r.config_path)) for r in results))
    print(f"{'Client'.ljust(name_w)} | {'Config'.ljust(path_w)} | Result")
    print("-" * (name_w + path_w + 14))
    for r in results:
        path_str = str(r.config_path)
        if len(path_str) > path_w:
            path_str = "..." + path_str[-(path_w - 3):]
        detail = f" — {r.detail}" if r.detail else ""
        print(f"{r.client.ljust(name_w)} | {path_str.ljust(path_w)} | {r.action}{detail}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sassymcp install",
                                description="Patch every installed MCP client to register sassymcp.")
    p.add_argument("--client", help="only patch this client (short_name)")
    p.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    p.add_argument("--uninstall", action="store_true", help="remove sassymcp from every config")
    p.add_argument("--exe-path", help="override the exe path (default: locate self)")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p.add_argument("--auto-other", action="store_true",
                   help="internal: skip Claude Desktop (used by DXT first-run hook)")

    args = p.parse_args(argv)

    try:
        exe_path = Path(args.exe_path) if args.exe_path else find_self_exe()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    clients = detect_clients()
    if args.client:
        clients = [c for c in clients if c.short_name == args.client]
        if not clients:
            print(f"Unknown client: {args.client!r}", file=sys.stderr)
            return 2
    if args.auto_other:
        clients = [c for c in clients if c.short_name != "claude"]

    results: list[PatchResult] = []
    op = unpatch_client if args.uninstall else patch_client
    for c in clients:
        if args.uninstall:
            results.append(unpatch_client(c, dry_run=args.dry_run))
        else:
            results.append(patch_client(c, exe_path, dry_run=args.dry_run))

    if args.json:
        out = [{
            "client": r.client, "config_path": str(r.config_path),
            "action": r.action, "backup": str(r.backup_path) if r.backup_path else None,
            "detail": r.detail,
        } for r in results]
        print(json.dumps(out, indent=2))
    else:
        _print_table(results)

    return 0 if all(r.action != "error" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
