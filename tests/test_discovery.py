# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-CGTDL5PMGAPM
"""Tests for the v1.10.0 tool-discovery surface + frozen-safe selfmod.

Registers the meta / selfmod / persona / prompts modules onto a throwaway
FastMCP instance and asserts the new discovery tools behave. Runnable both
under pytest and as `python tests/test_discovery.py`.
"""
import asyncio
import json

from mcp.server.fastmcp import FastMCP

import sassymcp.modules.meta as meta
import sassymcp.modules.selfmod as selfmod
import sassymcp.modules.persona as persona
import sassymcp.modules.prompts as prompts


def _call(tool):
    return asyncio.run(tool.fn())


def test_self_check_reports_whole_and_runtime():
    s = FastMCP("t-selfcheck")
    meta.register(s)
    assert "sassy_self_check" in s._tool_manager._tools
    out = json.loads(_call(s._tool_manager._tools["sassy_self_check"]))
    # Running from a source checkout, every declared module must import.
    assert out["verdict"] == "whole", out.get("broken")
    assert out["runtime"] == "source"
    assert out["modules_total"] == 36  # +coordination (multi-AI mesh)
    assert isinstance(out["pid"], int)
    # Every module entry carries an import status; none expected-loaded is BROKEN.
    assert all(m["import"] == "ok" for m in out["modules"].values())


def test_tool_catalog_enumerates_registry():
    s = FastMCP("t-catalog")
    meta.register(s)
    out = json.loads(_call(s._tool_manager._tools["sassy_tool_catalog"]))
    assert out["total"] >= 1
    # Catalog is keyed by group and every row has a name.
    for rows in out["tools"].values():
        for row in rows:
            assert row["name"].startswith("sassy_")


def test_selfmod_frozen_registers_no_tools():
    """Packaged builds must not expose selfmod tools at all (marketplace
    graders require a fixed graded version — stubs that advertise rewrite
    were rejected).
    """
    original = selfmod._FROZEN
    selfmod._FROZEN = True
    try:
        s = FastMCP("t-frozen")
        selfmod.register(s)
        names = set(s._tool_manager._tools)
        for t in ("sassy_selfmod_status", "sassy_selfmod_read", "sassy_selfmod_edit",
                  "sassy_selfmod_write", "sassy_selfmod_reload", "sassy_selfmod_restart",
                  "sassy_selfmod_rollback"):
            assert t not in names, f"frozen build must not register {t}"
    finally:
        selfmod._FROZEN = original


def test_selfmod_source_requires_opt_in(monkeypatch):
    """From source, selfmod stays off unless SASSYMCP_ENABLE_SELFMOD=1."""
    original = selfmod._FROZEN
    selfmod._FROZEN = False
    try:
        monkeypatch.delenv("SASSYMCP_ENABLE_SELFMOD", raising=False)
        s = FastMCP("t-gated")
        selfmod.register(s)
        assert "sassy_selfmod_status" not in s._tool_manager._tools

        monkeypatch.setenv("SASSYMCP_ENABLE_SELFMOD", "1")
        s2 = FastMCP("t-enabled")
        selfmod.register(s2)
        assert "sassy_selfmod_status" in s2._tool_manager._tools
    finally:
        selfmod._FROZEN = original
        monkeypatch.delenv("SASSYMCP_ENABLE_SELFMOD", raising=False)


def test_persona_bakes_discovery_protocol():
    s = FastMCP("t-persona")
    persona.register(s)
    obs = _call(s._tool_manager._tools["sassy_persona_observability"])
    assert "sassy_self_check" in obs and "sassy_tool_catalog" in obs
    assert "first-call sequence for any task" in obs
    assert "sassy_hooks_suggest" in obs


def test_discover_prompt_registered_and_renders():
    s = FastMCP("t-prompts")
    prompts.register(s)
    pm = s._prompt_manager
    assert "discover" in pm._prompts
    disc = pm._prompts["discover"]
    fn = getattr(disc, "fn", None) or getattr(disc, "func", None)
    rendered = fn(task="audit my site")
    assert "sassy_self_check" in rendered
    assert "sassy_tool_catalog" in rendered
    assert "audit my site" in rendered


if __name__ == "__main__":
    test_self_check_reports_whole_and_runtime()
    test_tool_catalog_enumerates_registry()
    test_selfmod_frozen_registers_no_tools()
    # opt-in test needs pytest monkeypatch; skip in bare python run
    test_persona_bakes_discovery_protocol()
    test_discover_prompt_registered_and_renders()
    print("all discovery tests passed")
