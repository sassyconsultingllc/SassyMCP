# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-KX5WCW45DFEF
"""Regression tests for the curated tool-description + MCP annotation metadata.

Every registered tool must carry the generated metadata in
sassymcp/tool_descriptions.py and sassymcp/tool_annotations.py (278/278 as
of v1.16.0). These tests are the tripwire: adding a tool without adding its
curated description and all four MCP annotation booleans fails the suite.

Loading mirrors tests/test_discovery.py: every module listed in
TOOL_GROUPS registers onto a bare FastMCP instance (no full-server import,
no side effects). The curated metadata is then applied exactly the way
server._apply_tool_metadata() applies it, so what the tests see is what
tools/list serves.
"""
import logging

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from sassymcp import tool_annotations as _ta
from sassymcp import tool_descriptions as _td
from sassymcp.modules._tool_loader import TOOL_GROUPS, get_group_for_module

logging.disable(logging.CRITICAL)

# Lenient tripwire vocabulary for "destructiveHint=True implies the
# description discloses the destructive nature". Deliberately broad: this is
# a smoke alarm, not a prover.
_DISCLOSURE_WORDS = (
    "destruct", "delet", "irrevers", "permanent", "destroy", "kill",
    "terminat", "wipe", "remov", "mutat", "overwrit", "uninstall",
    "clear ", "clears",
)

# Hand-picked tools that are observably side-effect free; all must be
# readOnlyHint=True (the list itself is verified against
# tool_annotations.py at test time).
_KNOWN_READ_ONLY = (
    "sassy_read_file",
    "sassy_list_dir",
    "sassy_iphone_list",
    "sassy_gh_get_me",
    "sassy_memory_recall",
    "sassy_tool_catalog",
    "sassy_desktop_state",
)


def _load_all():
    """Register every module in TOOL_GROUPS and apply curated metadata."""
    modules = []
    for group_info in TOOL_GROUPS.values():
        modules.extend(group_info["modules"])
    # Same ordering the server uses: infrastructure first, then meta, then
    # everything else in group order.
    infra = ["state_manager", "observability", "runtime_config"]
    ordered = [m for m in infra if m in modules]
    if "meta" in modules:
        ordered.append("meta")
    ordered += [m for m in modules if m not in infra and m != "meta"]

    server = FastMCP("t-tool-metadata")
    tool_to_group = {}
    for mod_name in ordered:
        before = set(server._tool_manager._tools.keys())
        module = __import__(f"sassymcp.modules.{mod_name}", fromlist=[mod_name])
        module.register(server)
        after = set(server._tool_manager._tools.keys())
        group = get_group_for_module(mod_name)
        for tool_name in after - before:
            tool_to_group[tool_name] = group

    # Apply curated metadata the way server._apply_tool_metadata() does.
    for tool_name, tool in server._tool_manager._tools.items():
        desc = _td.TOOL_DESCRIPTIONS.get(tool_name)
        if desc:
            tool.description = desc
        ann = _ta.TOOL_ANNOTATIONS.get(tool_name)
        if ann:
            tool.annotations = ToolAnnotations(
                readOnlyHint=bool(ann.get("readOnlyHint", False)),
                destructiveHint=bool(ann.get("destructiveHint", False)),
                idempotentHint=bool(ann.get("idempotentHint", False)),
                openWorldHint=bool(ann.get("openWorldHint", False)),
            )
    return server, tool_to_group


_SERVER, _TOOL_TO_GROUP = _load_all()
_TOOLS = _SERVER._tool_manager._tools


def test_registered_tool_count():
    # 278 tools as of v1.16.0 (272 + 6 iPhone scaffold tools). If this
    # moves, the curated metadata modules must move with it.
    assert len(_TOOLS) == 278, f"expected 278 tools, got {len(_TOOLS)}"


def test_metadata_modules_have_full_coverage():
    desc_names = set(_td.TOOL_DESCRIPTIONS)
    ann_names = set(_ta.TOOL_ANNOTATIONS)
    tool_names = set(_TOOLS)
    assert desc_names == tool_names, (
        "description drift: missing=%s extra=%s"
        % (sorted(tool_names - desc_names), sorted(desc_names - tool_names))
    )
    assert ann_names == tool_names, (
        "annotation drift: missing=%s extra=%s"
        % (sorted(tool_names - ann_names), sorted(ann_names - tool_names))
    )


def test_every_tool_has_nonempty_description():
    empty = [n for n, t in _TOOLS.items() if not (t.description or "").strip()]
    assert not empty, f"tools without a description: {empty}"


def test_every_tool_has_all_four_annotation_booleans():
    for name, tool in _TOOLS.items():
        ann = tool.annotations
        assert ann is not None, f"{name}: no annotations applied"
        for field in ("readOnlyHint", "destructiveHint",
                      "idempotentHint", "openWorldHint"):
            value = getattr(ann, field, None)
            assert isinstance(value, bool), (
                f"{name}: {field} is not a bool ({value!r})"
            )


def test_annotation_entries_all_have_four_keys():
    # Guards the generated module itself: every entry must spell out all
    # four MCP booleans explicitly (no relying on spec defaults).
    for name, ann in _ta.TOOL_ANNOTATIONS.items():
        missing = {"readOnlyHint", "destructiveHint",
                   "idempotentHint", "openWorldHint"} - set(ann)
        assert not missing, f"{name}: annotation keys missing: {missing}"
        non_bool = [k for k in ("readOnlyHint", "destructiveHint",
                                "idempotentHint", "openWorldHint")
                    if not isinstance(ann.get(k), bool)]
        assert not non_bool, f"{name}: non-bool annotation values: {non_bool}"


def test_policy_at_least_one_destructive_tool():
    destructive = [n for n, t in _TOOLS.items()
                   if t.annotations and t.annotations.destructiveHint]
    assert destructive, "no tool is marked destructiveHint=True"


def test_known_read_only_tools():
    for name in _KNOWN_READ_ONLY:
        assert name in _TOOLS, f"known tool missing from registry: {name}"
        assert _TOOLS[name].annotations.readOnlyHint is True, (
            f"{name}: expected readOnlyHint=True"
        )


def test_destructive_tools_disclose_in_description():
    # Tripwire, not a prover: every destructiveHint=True tool must use at
    # least one disclosure word in its curated description.
    misses = []
    for name, tool in _TOOLS.items():
        if tool.annotations and tool.annotations.destructiveHint:
            desc = (tool.description or "").lower()
            if not any(w in desc for w in _DISCLOSURE_WORDS):
                misses.append(name)
    assert not misses, f"destructive tools without disclosure wording: {misses}"


def test_every_tool_maps_to_a_tool_group():
    ungrouped = [n for n in _TOOLS if n not in _TOOL_TO_GROUP]
    assert not ungrouped, f"tools with no group mapping: {ungrouped}"
    unknown = {g for g in _TOOL_TO_GROUP.values() if g not in TOOL_GROUPS}
    assert not unknown, f"tools mapped to unknown groups: {unknown}"
    total = sum(
        1 for g in _TOOL_TO_GROUP.values() if g in TOOL_GROUPS
    )
    assert total == len(_TOOLS) == 278


if __name__ == "__main__":
    test_registered_tool_count()
    test_metadata_modules_have_full_coverage()
    test_every_tool_has_nonempty_description()
    test_every_tool_has_all_four_annotation_booleans()
    test_annotation_entries_all_have_four_keys()
    test_policy_at_least_one_destructive_tool()
    test_known_read_only_tools()
    test_destructive_tools_disclose_in_description()
    test_every_tool_maps_to_a_tool_group()
    print("all tool-metadata tests passed")
