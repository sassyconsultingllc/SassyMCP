# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-4UN4H7B4CT62
"""Tests for usage-score-driven default module loading.

The promise of `get_default_modules()` is: static `always_load=True` groups
are the floor, and any tool with score >= USAGE_BOOST_THRESHOLD pulls its
containing group into the default load set even if that group was
on-demand only.

These tests verify the contract under three conditions:
  - cold start (no usage data) → only the static always_load groups
  - hot path (high-scoring tool exists) → its group joins the default set
  - explicit registry vs heuristic fallback both work
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest


def _seed_tool_usage(home: Path, tool_invocations: dict[str, int]) -> None:
    """Write a tool_usage.json with `count` recent invocations per tool, all
    timestamped within the last hour so they fully count toward the score.
    """
    now = time.time()
    payload = {
        "tools": {
            tool: [now - i * 60 for i in range(count)]
            for tool, count in tool_invocations.items()
        },
        "updated": now,
    }
    home.mkdir(parents=True, exist_ok=True)
    (home / "tool_usage.json").write_text(json.dumps(payload))


def _fresh_loader(monkeypatch, sassy_home: Path):
    """Reload the loader module under a fresh SASSYMCP_HOME so the tracker
    singleton picks up our seeded usage data.
    """
    monkeypatch.setenv("SASSYMCP_HOME", str(sassy_home))
    import importlib
    import sassymcp._paths
    importlib.reload(sassymcp._paths)
    import sassymcp.modules._tool_loader as loader
    importlib.reload(loader)
    # Reset the tracker singleton so it re-reads the new HOME
    loader._tracker = None
    return loader


def test_cold_start_returns_only_always_load_groups(tmp_path: Path, monkeypatch):
    """No usage data → only modules from groups marked always_load=True."""
    sassy_home = tmp_path / "sassy_home"
    sassy_home.mkdir()
    loader = _fresh_loader(monkeypatch, sassy_home)

    defaults = loader.get_default_modules()
    # Sanity checks: core / always_load modules ARE present
    assert "fileops" in defaults
    assert "shell" in defaults
    assert "memory" in defaults
    # On-demand modules ARE NOT present
    assert "adb" not in defaults
    assert "phone_screen" not in defaults
    assert "registry" not in defaults
    assert "github_ops" not in defaults  # the heavy github_full module
    # Selfmod is opt-in / never always_load (marketplace fixed-version rule)
    assert "selfmod" not in defaults


def test_high_score_adb_tool_pulls_android_group(tmp_path: Path, monkeypatch):
    """If sassy_adb_shell scores high, the android group should join defaults."""
    sassy_home = tmp_path / "sassy_home"
    sassy_home.mkdir()
    # 50+ recent invocations easily crosses the 0.5 threshold
    _seed_tool_usage(sassy_home, {"sassy_adb_shell": 50, "sassy_phone_ui": 30})
    loader = _fresh_loader(monkeypatch, sassy_home)

    defaults = loader.get_default_modules()
    assert "adb" in defaults, f"adb missing from boosted defaults: {defaults}"
    # phone_screen should also boost (high score on sassy_phone_ui)
    assert "phone_screen" in defaults, (
        f"phone_screen missing from boosted defaults: {defaults}"
    )


def test_heuristic_fallback_when_registry_is_empty(tmp_path: Path, monkeypatch):
    """At cold start the _TOOL_TO_GROUP registry may be empty (it is populated
    during module registration). The boost logic must fall back to a
    name-based heuristic so the first session after upgrade still benefits.
    """
    sassy_home = tmp_path / "sassy_home"
    sassy_home.mkdir()
    _seed_tool_usage(sassy_home, {"sassy_adb_shell": 60})
    loader = _fresh_loader(monkeypatch, sassy_home)

    # Force the registry to be empty (simulate cold start before any
    # module registration has happened)
    loader._TOOL_TO_GROUP.clear()

    boosted = loader.get_score_boosted_modules()
    # The heuristic should pull `adb` out of `sassy_adb_shell`
    assert "adb" in boosted, f"heuristic did not catch adb: {boosted}"


def test_low_score_tools_do_not_boost(tmp_path: Path, monkeypatch):
    """A handful of invocations does not cross the 0.5 threshold."""
    sassy_home = tmp_path / "sassy_home"
    sassy_home.mkdir()
    # 2 invocations is below the threshold (score ~0.2)
    _seed_tool_usage(sassy_home, {"sassy_adb_shell": 2})
    loader = _fresh_loader(monkeypatch, sassy_home)

    defaults = loader.get_default_modules()
    assert "adb" not in defaults, (
        "low-score tool should not pull its group into defaults"
    )


def test_already_loaded_groups_are_not_duplicated(tmp_path: Path, monkeypatch):
    """A boosted module that is ALREADY in always_load shouldn't appear twice."""
    sassy_home = tmp_path / "sassy_home"
    sassy_home.mkdir()
    # sassy_read_file is in core (always_load=True). Seeding high usage
    # should not cause a duplicate entry.
    _seed_tool_usage(sassy_home, {"sassy_read_file": 100})
    loader = _fresh_loader(monkeypatch, sassy_home)

    defaults = loader.get_default_modules()
    assert defaults.count("fileops") == 1


def test_pruning_only_affects_on_demand_groups(tmp_path: Path, monkeypatch):
    """get_pruned_tools should never prune a tool from an always_load group,
    even if the tool's score is below the prune threshold. Users should
    not lose visibility on their core toolkit because of a slow week.
    """
    sassy_home = tmp_path / "sassy_home"
    sassy_home.mkdir()
    loader = _fresh_loader(monkeypatch, sassy_home)

    # Manually populate the registry so pruning has data to inspect.
    loader._TOOL_TO_GROUP["sassy_read_file"] = "core"      # always_load=True
    loader._TOOL_TO_GROUP["sassy_phone_ui"] = "android"     # always_load=False

    # Seed both as never-used-in-90-days (very low scores)
    _seed_tool_usage(sassy_home, {})  # empty file; no usage data
    loader = _fresh_loader(monkeypatch, sassy_home)
    loader._TOOL_TO_GROUP["sassy_read_file"] = "core"
    loader._TOOL_TO_GROUP["sassy_phone_ui"] = "android"

    pruned = loader.get_pruned_tools()
    # Even at zero score, core tools survive
    assert "sassy_read_file" not in pruned, (
        "always_load groups must never lose tools to pruning"
    )
