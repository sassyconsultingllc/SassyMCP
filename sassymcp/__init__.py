"""SassyMCP - Unified MCP server for Windows + Android automation and security auditing.

This module is the canonical source of truth for the SassyMCP version
string. pyproject.toml reads it via hatchling's dynamic-version hook,
and every banner / log line / user-agent in the codebase imports
__version__ from here. To bump the version, edit ONLY this line.
"""

__version__ = "1.7.0"
