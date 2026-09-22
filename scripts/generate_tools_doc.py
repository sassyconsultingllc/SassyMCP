#!/usr/bin/env python3
# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-F5TX2HZG23M3
"""Generate the human-readable tool reference: docs/TOOLS.md.

The reference is generated, not hand-written: it registers every module
listed in sassymcp/modules/_tool_loader.py TOOL_GROUPS (the same set the
server loads with SASSYMCP_LOAD_ALL=1), maps each tool to its group via the
module it was defined in, and emits the curated descriptions from
sassymcp/tool_descriptions.py. Counts are verified to sum to 278 before the
file is written; a mismatch aborts loudly so a stale doc can never ship.

Usage (run from the repo root):

    python scripts/generate_tools_doc.py
    python scripts/generate_tools_doc.py --out /tmp/tools.md
    python scripts/generate_tools_doc.py --check   # exit 1 if docs/TOOLS.md is stale

Re-run after adding/removing tools or regenerating tool_descriptions.py.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

EXPECTED_TOOL_COUNT = 278

logging.disable(logging.CRITICAL)


def load_tool_index():
    """Register all modules; return (tools dict, tool->group mapping)."""
    from mcp.server.fastmcp import FastMCP
    from sassymcp import tool_descriptions as _td
    from sassymcp.modules._tool_loader import TOOL_GROUPS, get_group_for_module

    modules = []
    for group_info in TOOL_GROUPS.values():
        modules.extend(group_info["modules"])
    infra = ["state_manager", "observability", "runtime_config"]
    ordered = [m for m in infra if m in modules]
    if "meta" in modules:
        ordered.append("meta")
    ordered += [m for m in modules if m not in infra and m != "meta"]

    server = FastMCP("gen-tools-doc")
    tool_to_group: dict[str, str | None] = {}
    for mod_name in ordered:
        before = set(server._tool_manager._tools.keys())
        module = __import__(f"sassymcp.modules.{mod_name}", fromlist=[mod_name])
        module.register(server)
        after = set(server._tool_manager._tools.keys())
        group = get_group_for_module(mod_name)
        for tool_name in after - before:
            tool_to_group[tool_name] = group

    # Apply curated descriptions (mirror of server._apply_tool_metadata).
    for tool_name, tool in server._tool_manager._tools.items():
        desc = _td.TOOL_DESCRIPTIONS.get(tool_name)
        if desc:
            tool.description = desc
    return server._tool_manager._tools, tool_to_group, TOOL_GROUPS


def render(tools, tool_to_group, tool_groups) -> str:
    total = len(tools)
    if total != EXPECTED_TOOL_COUNT:
        raise SystemExit(
            f"tool count mismatch: registered {total}, "
            f"expected {EXPECTED_TOOL_COUNT}; regenerate tool_descriptions.py "
            "and update EXPECTED_TOOL_COUNT if the change is intentional"
        )
    ungrouped = [n for n in tools if not tool_to_group.get(n)]
    if ungrouped:
        raise SystemExit(f"tools with no group mapping: {ungrouped}")

    lines = [
        "<!--",
        "   Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.",
        "   Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.",
        "   CodeMark: SCLLC1-SassyMCP-RUTQQK5VPCVE",
        "-->",
        "# SassyMCP Tool Reference",
        "",
        "> **Generated — do not edit by hand.** Produced by",
        "> `scripts/generate_tools_doc.py` from the registered tool set and the",
        "> curated descriptions in `sassymcp/tool_descriptions.py`. Re-run the",
        "> script after any tool or description change.",
        "",
        f"**{total} tools** across **{len(tool_groups)} tool groups** (v1.16.0).",
        "Every description below is the curated text clients actually receive",
        "via `tools/list`.",
        "",
        "## Group index",
        "",
        "| Group | Tools | What it covers |",
        "|-------|-------|----------------|",
    ]
    grouped: dict[str, list[str]] = {g: [] for g in tool_groups}
    for name, group in tool_to_group.items():
        grouped[group].append(name)
    for group, info in tool_groups.items():
        lines.append(
            f"| `{group}` | {len(grouped[group])} "
            f"| {info.get('description', '')} |"
        )
    lines.append("")

    for group, info in tool_groups.items():
        names = sorted(grouped[group])
        lines.append(f"## {group} — {len(names)} tools")
        lines.append("")
        if info.get("description"):
            lines.append(f"*{info['description']}*")
            lines.append("")
        for name in names:
            desc = (tools[name].description or "").strip()
            lines.append(f"### {name}")
            lines.append("")
            lines.append(desc if desc else "_No description._")
            lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(REPO_ROOT / "docs" / "TOOLS.md"),
                        help="output path (default: docs/TOOLS.md)")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if the output file differs from generated")
    args = parser.parse_args()

    tools, tool_to_group, tool_groups = load_tool_index()
    content = render(tools, tool_to_group, tool_groups)

    if args.check:
        out = Path(args.out)
        if not out.exists() or out.read_text(encoding="utf-8") != content:
            print(f"{args.out} is stale — run scripts/generate_tools_doc.py",
                  file=sys.stderr)
            return 1
        print(f"{args.out} is up to date ({len(tools)} tools)")
        return 0

    Path(args.out).write_text(content, encoding="utf-8")
    print(f"wrote {args.out} ({len(tools)} tools, "
          f"{len(tool_groups)} groups)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
