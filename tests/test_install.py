# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-4GXMQA4Q3PSR
"""Tests for sassymcp.install — auto-detection and config patching."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sassymcp.install import (
    ClientInfo,
    PatchResult,
    detect_clients,
    patch_client,
    unpatch_client,
    find_self_exe,
    main,
    _CLIENT_REGISTRY,
    _apply_server_entry,
    _load_existing_config,
)


# --- detect_clients ---

def test_detect_clients_returns_all_registered_clients():
    clients = detect_clients()
    short_names = {c.short_name for c in clients}
    assert short_names == {"claude", "vscode", "cursor", "windsurf",
                            "continue", "cline", "zed", "grok"}


def test_detect_clients_returns_clientinfo_objects():
    clients = detect_clients()
    assert all(isinstance(c, ClientInfo) for c in clients)
    assert all(c.config_path.is_absolute() for c in clients)


# --- patch_client (mcpServers schema — Claude Desktop, Cursor, Windsurf, Cline, Grok) ---

def _make_client(tmp_path: Path, schema: str = "mcpServers", short_name: str = "claude") -> ClientInfo:
    cfg = tmp_path / "test_config.json"
    return ClientInfo(
        name="Test Client", short_name=short_name,
        config_path=cfg, schema=schema, detected=True,
    )


def test_patch_adds_to_empty_mcpservers_config(tmp_path: Path):
    client = _make_client(tmp_path)
    exe = Path("C:/sassy/sassymcp.exe")
    result = patch_client(client, exe)
    assert result.action == "added"
    cfg = json.loads(client.config_path.read_text())
    assert cfg["mcpServers"]["sassymcp"]["command"] == exe.as_posix()
    assert cfg["mcpServers"]["sassymcp"]["env"]["SASSYMCP_LOAD_ALL"] == "1"


def test_patch_is_idempotent(tmp_path: Path):
    client = _make_client(tmp_path)
    exe = Path("C:/sassy/sassymcp.exe")
    r1 = patch_client(client, exe)
    r2 = patch_client(client, exe)
    assert r1.action == "added"
    assert r2.action == "noop"


def test_patch_updates_when_exe_path_changes(tmp_path: Path):
    client = _make_client(tmp_path)
    patch_client(client, Path("C:/old/sassymcp.exe"))
    result = patch_client(client, Path("C:/new/sassymcp.exe"))
    assert result.action == "updated"
    cfg = json.loads(client.config_path.read_text())
    assert cfg["mcpServers"]["sassymcp"]["command"] == "C:/new/sassymcp.exe"


def test_patch_preserves_other_servers(tmp_path: Path):
    client = _make_client(tmp_path)
    client.config_path.write_text(json.dumps({
        "mcpServers": {
            "filesystem": {"command": "fs.exe", "args": []},
            "github": {"command": "gh.exe", "args": []},
        }
    }))
    patch_client(client, Path("C:/sassy/sassymcp.exe"))
    cfg = json.loads(client.config_path.read_text())
    assert "filesystem" in cfg["mcpServers"]
    assert "github" in cfg["mcpServers"]
    assert "sassymcp" in cfg["mcpServers"]


def test_patch_dry_run_writes_nothing(tmp_path: Path):
    client = _make_client(tmp_path)
    exe = Path("C:/sassy/sassymcp.exe")
    result = patch_client(client, exe, dry_run=True)
    assert result.action == "added"
    assert not client.config_path.exists()


# --- backup behavior ---

def test_patch_takes_backup_on_first_write(tmp_path: Path):
    client = _make_client(tmp_path)
    client.config_path.write_text(json.dumps({"mcpServers": {"existing": {"command": "x"}}}))
    result = patch_client(client, Path("C:/sassy/sassymcp.exe"))
    assert result.backup_path is not None
    assert result.backup_path.exists()
    backup = json.loads(result.backup_path.read_text())
    assert "existing" in backup["mcpServers"]
    assert "sassymcp" not in backup["mcpServers"]


def test_patch_does_not_take_second_backup(tmp_path: Path):
    client = _make_client(tmp_path)
    client.config_path.write_text(json.dumps({"mcpServers": {"existing": {"command": "x"}}}))
    r1 = patch_client(client, Path("C:/sassy/sassymcp.exe"))
    r2 = patch_client(client, Path("C:/different/sassymcp.exe"))
    backups = list(tmp_path.glob("test_config.json.sassymcp-backup-*"))
    assert len(backups) == 1


# --- schema handling ---

def test_patch_handles_servers_schema_vscode(tmp_path: Path):
    client = _make_client(tmp_path, schema="servers", short_name="vscode")
    patch_client(client, Path("C:/sassy/sassymcp.exe"))
    cfg = json.loads(client.config_path.read_text())
    assert "servers" in cfg
    assert cfg["servers"]["sassymcp"]["command"] == "C:/sassy/sassymcp.exe"


def test_patch_handles_experimental_schema_continue(tmp_path: Path):
    client = _make_client(tmp_path, schema="experimental.modelContextProtocolServers", short_name="continue")
    patch_client(client, Path("C:/sassy/sassymcp.exe"))
    cfg = json.loads(client.config_path.read_text())
    servers = cfg["experimental"]["modelContextProtocolServers"]
    assert len(servers) == 1
    assert servers[0]["transport"]["type"] == "stdio"
    assert servers[0]["transport"]["command"] == "C:/sassy/sassymcp.exe"


def test_patch_handles_context_servers_schema_zed(tmp_path: Path):
    client = _make_client(tmp_path, schema="context_servers", short_name="zed")
    patch_client(client, Path("C:/sassy/sassymcp.exe"))
    cfg = json.loads(client.config_path.read_text())
    assert cfg["context_servers"]["sassymcp"]["command"]["path"] == "C:/sassy/sassymcp.exe"


def test_patch_grok_uses_http_args(tmp_path: Path):
    client = _make_client(tmp_path, schema="mcpServers", short_name="grok")
    patch_client(client, Path("C:/sassy/sassymcp.exe"))
    cfg = json.loads(client.config_path.read_text())
    args = cfg["mcpServers"]["sassymcp"]["args"]
    assert "--http" in args


# --- unpatch ---

def test_unpatch_removes_only_sassymcp(tmp_path: Path):
    client = _make_client(tmp_path)
    client.config_path.write_text(json.dumps({
        "mcpServers": {
            "sassymcp": {"command": "old"},
            "filesystem": {"command": "fs.exe"},
        }
    }))
    result = unpatch_client(client)
    assert result.action == "uninstalled"
    cfg = json.loads(client.config_path.read_text())
    assert "sassymcp" not in cfg["mcpServers"]
    assert "filesystem" in cfg["mcpServers"]


def test_unpatch_noop_when_sassymcp_absent(tmp_path: Path):
    client = _make_client(tmp_path)
    client.config_path.write_text(json.dumps({"mcpServers": {"filesystem": {"command": "fs.exe"}}}))
    result = unpatch_client(client)
    assert result.action == "noop"


def test_unpatch_skipped_when_config_missing(tmp_path: Path):
    client = _make_client(tmp_path)
    result = unpatch_client(client)
    assert result.action == "skipped"


# --- error handling ---

def test_load_existing_config_raises_on_corrupt_json(tmp_path: Path):
    p = tmp_path / "corrupt.json"
    p.write_text("{not valid json{{")
    with pytest.raises(json.JSONDecodeError):
        _load_existing_config(p)


def test_patch_returns_error_on_corrupt_existing_config(tmp_path: Path):
    client = _make_client(tmp_path)
    client.config_path.write_text("{not valid json{{")
    result = patch_client(client, Path("C:/sassy/sassymcp.exe"))
    assert result.action == "error"
    assert "corrupt" in result.detail.lower()


# --- main() CLI ---

def test_main_dry_run_writes_no_files(tmp_path: Path, monkeypatch):
    # Point all client paths under tmp_path so detect_clients finds nothing
    # OR mark them as not-detected. We'll use --client to force a specific one.
    cfg = tmp_path / "fake_claude" / "claude_desktop_config.json"
    cfg.parent.mkdir()

    # Patch the registry's path function for claude
    from sassymcp import install as inst_mod
    monkeypatch.setattr(inst_mod, "_claude_desktop_config", lambda: cfg)

    rc = main(["--client", "claude", "--exe-path", "C:/sassy/sassymcp.exe", "--dry-run"])
    assert rc == 0
    assert not cfg.exists()


def test_main_json_output_is_valid_json(tmp_path: Path, monkeypatch, capsys):
    cfg = tmp_path / "claude" / "claude_desktop_config.json"
    cfg.parent.mkdir()

    from sassymcp import install as inst_mod
    monkeypatch.setattr(inst_mod, "_claude_desktop_config", lambda: cfg)

    # Pass --no-skills so the test isn't dependent on the canonical playbook
    # being present at sassymcp/skills/sassymcp-tools.md (it is, but tests
    # should isolate what they check).
    rc = main(["--client", "claude", "--exe-path", "C:/sassy/sassymcp.exe",
               "--json", "--dry-run", "--no-skills"])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    # The shape is {"config_patches": [...], "skill_deployments": [...]}
    assert isinstance(parsed, dict)
    assert "config_patches" in parsed
    assert "skill_deployments" in parsed
    assert isinstance(parsed["config_patches"], list)
    assert parsed["config_patches"][0]["client"] == "Claude Desktop"
    # --no-skills suppresses the deploy step
    assert parsed["skill_deployments"] == []


def test_deploy_skill_writes_skill_md_for_claude(tmp_path: Path, monkeypatch):
    """Smoke-check that deploy_skill renders the canonical playbook to a
    Claude-Skills-style path when given a fake home directory."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    from sassymcp.install import ClientInfo, deploy_skill
    client = ClientInfo(
        name="Claude Desktop", short_name="claude",
        config_path=tmp_path / "ignored.json",
        schema="mcpServers", detected=True,
    )

    result = deploy_skill(client, dry_run=False)
    assert result["action"] in ("deployed", "noop")
    target = fake_home / ".claude" / "skills" / "sassymcp-tools" / "SKILL.md"
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    # Frontmatter preserved for Claude
    assert content.startswith("---\nname: sassymcp-tools")
    # Has the actual playbook content
    assert "sassy_screenshot" in content
    assert "sassy_phone_ui" in content


def test_deploy_skill_strips_frontmatter_for_cursor(tmp_path: Path, monkeypatch):
    """Cursor's .cursor/rules files are plain markdown — no frontmatter."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    from sassymcp.install import ClientInfo, deploy_skill
    client = ClientInfo(
        name="Cursor", short_name="cursor",
        config_path=tmp_path / "ignored.json",
        schema="mcpServers", detected=True,
    )

    result = deploy_skill(client, dry_run=False)
    assert result["action"] in ("deployed", "noop")
    target = fake_home / ".cursor" / "rules" / "sassymcp.md"
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    # Frontmatter STRIPPED for non-Skills clients
    assert not content.startswith("---")
    assert content.startswith("# SassyMCP Tool Playbook") or "Tool Playbook" in content[:200]


def test_deploy_skill_idempotent(tmp_path: Path, monkeypatch):
    """Re-running deploy_skill on an up-to-date target should noop, not
    rewrite the file (preserves mtime, plays nice with file watchers)."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    from sassymcp.install import ClientInfo, deploy_skill
    client = ClientInfo(
        name="Claude Desktop", short_name="claude",
        config_path=tmp_path / "ignored.json",
        schema="mcpServers", detected=True,
    )
    first = deploy_skill(client, dry_run=False)
    assert first["action"] == "deployed"
    second = deploy_skill(client, dry_run=False)
    assert second["action"] == "noop"


def test_deploy_skill_skipped_when_client_has_no_rules_format(tmp_path: Path, monkeypatch):
    """VS Code Copilot, Continue, Zed, Grok don't have a stable rules-file
    format that we can reliably write to. deploy_skill should report
    skipped rather than guess."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    from sassymcp.install import ClientInfo, deploy_skill
    for short, name in [("vscode", "VS Code"), ("continue", "Continue"),
                         ("zed", "Zed"), ("grok", "Grok Desktop")]:
        client = ClientInfo(
            name=name, short_name=short,
            config_path=tmp_path / "ignored.json",
            schema="mcpServers", detected=True,
        )
        result = deploy_skill(client, dry_run=False)
        assert result["action"] == "skipped", (
            f"{short} should be skipped for skill deployment, got {result}"
        )


def test_main_uninstall_removes_entry(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "claude" / "claude_desktop_config.json"
    cfg.parent.mkdir()
    cfg.write_text(json.dumps({"mcpServers": {"sassymcp": {"command": "x"}, "other": {"command": "y"}}}))

    from sassymcp import install as inst_mod
    monkeypatch.setattr(inst_mod, "_claude_desktop_config", lambda: cfg)

    rc = main(["--client", "claude", "--uninstall"])
    assert rc == 0
    parsed = json.loads(cfg.read_text())
    assert "sassymcp" not in parsed["mcpServers"]
    assert "other" in parsed["mcpServers"]


def test_main_unknown_client_returns_2(capsys):
    rc = main(["--client", "nonexistent-client", "--exe-path", "C:/sassy.exe"])
    assert rc == 2


def test_main_client_auto_patches_all_detected(tmp_path: Path, monkeypatch):
    """Regression: `--client auto` / `all` is a sentinel for "every client",
    not a short_name. It used to fall into the filter branch, match nothing,
    and exit 2 with "Unknown client: 'auto'" — which broke both the TTY
    wizard's quick-install and the documented `install --client auto`."""
    cursor_cfg = tmp_path / "cursor" / "mcp.json"
    cursor_cfg.parent.mkdir()

    from sassymcp import install as inst_mod
    monkeypatch.setattr(inst_mod, "_cursor_mcp", lambda: cursor_cfg)
    # Constrain the registry to one client so the test is hermetic and never
    # touches the real machine's other client configs.
    monkeypatch.setattr(inst_mod, "_CLIENT_REGISTRY", [
        ("Cursor", "cursor", "_cursor_mcp", "mcpServers", ""),
    ])

    for sentinel in ("auto", "all", "AUTO"):
        cursor_cfg.write_text("{}")
        rc = main(["--client", sentinel, "--exe-path",
                   "C:/sassy/sassymcp.exe", "--no-skills"])
        assert rc == 0, f"--client {sentinel} should succeed, got {rc}"
        parsed = json.loads(cursor_cfg.read_text())
        assert "sassymcp" in parsed["mcpServers"]


def test_main_auto_other_skips_claude(tmp_path: Path, monkeypatch):
    """--auto-other (DXT first-run hook) skips Claude Desktop because DXT
    already installed itself there."""
    claude_cfg = tmp_path / "claude" / "claude_desktop_config.json"
    cursor_cfg = tmp_path / "cursor" / "mcp.json"
    claude_cfg.parent.mkdir()
    cursor_cfg.parent.mkdir()

    from sassymcp import install as inst_mod
    monkeypatch.setattr(inst_mod, "_claude_desktop_config", lambda: claude_cfg)
    monkeypatch.setattr(inst_mod, "_cursor_mcp", lambda: cursor_cfg)
    # Hermetic: constrain the registry to the two clients this test asserts on.
    # Without this, --auto-other walks the FULL registry and writes to the real
    # machine's Windsurf/Continue/Cline/etc. configs (a real config on disk with
    # bad JSON even made this test fail). --no-skills likewise stops the
    # tool-playbook deployment from writing to real rules-file paths.
    monkeypatch.setattr(inst_mod, "_CLIENT_REGISTRY", [
        ("Claude Desktop", "claude", "_claude_desktop_config", "mcpServers", ""),
        ("Cursor", "cursor", "_cursor_mcp", "mcpServers", ""),
    ])

    rc = main(["--exe-path", "C:/sassy/sassymcp.exe", "--auto-other", "--no-skills"])
    assert rc == 0
    # Claude untouched
    assert not claude_cfg.exists()
    # Cursor patched
    assert cursor_cfg.exists()
    parsed = json.loads(cursor_cfg.read_text())
    assert "sassymcp" in parsed["mcpServers"]
