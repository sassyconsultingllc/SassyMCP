# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-CGTDL5PMGAPM
"""Tests for the tool-discovery surface (meta / persona / prompts).

Self-modification tools were removed; these tests no longer register selfmod.
"""
import asyncio
import json

from mcp.server.fastmcp import FastMCP

import sassymcp.modules.meta as meta
import sassymcp.modules.persona as persona
import sassymcp.modules.prompts as prompts
from sassymcp.modules._tool_loader import TOOL_GROUPS


def _call(tool):
    return asyncio.run(tool.fn())


def test_self_check_reports_whole_and_runtime():
    s = FastMCP("t-selfcheck")
    meta.register(s)
    assert "sassy_self_check" in s._tool_manager._tools
    out = json.loads(_call(s._tool_manager._tools["sassy_self_check"]))
    assert out["verdict"] == "whole", out.get("broken")
    assert out["runtime"] == "source"
    # selfmod group removed — one fewer module than the old 36
    expected = sum(len(g["modules"]) for g in TOOL_GROUPS.values())
    assert out["modules_total"] == expected
    assert "selfmod" not in TOOL_GROUPS
    assert isinstance(out["pid"], int)
    assert all(m["import"] == "ok" for m in out["modules"].values())


def test_selfmod_group_removed():
    assert "selfmod" not in TOOL_GROUPS
    from sassymcp.modules import selfmod
    s = FastMCP("t-noselfmod")
    selfmod.register(s)
    assert not any(n.startswith("sassy_selfmod_") for n in s._tool_manager._tools)


def test_tool_catalog_enumerates_registry():
    s = FastMCP("t-catalog")
    meta.register(s)
    out = json.loads(_call(s._tool_manager._tools["sassy_tool_catalog"]))
    assert out["total"] >= 1
    for rows in out["tools"].values():
        for row in rows:
            assert row["name"].startswith("sassy_")


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
    test_selfmod_group_removed()
    test_tool_catalog_enumerates_registry()
    test_persona_bakes_discovery_protocol()
    test_discover_prompt_registered_and_renders()
    print("all discovery tests passed")
