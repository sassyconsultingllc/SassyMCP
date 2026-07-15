# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-ZPTR2CPXHISB
"""SassyMCP - Unified MCP server for Windows/macOS/Linux + Android automation and security auditing.

This module is the canonical source of truth for the SassyMCP version
string. pyproject.toml reads it via hatchling's dynamic-version hook,
and every banner / log line / user-agent in the codebase imports
__version__ from here. To bump the version, edit ONLY this line.
"""

__version__ = "1.11.0"
