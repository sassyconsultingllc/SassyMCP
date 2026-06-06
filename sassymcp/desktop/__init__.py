"""Sassy Brain — the standalone multi-agent cockpit (its own desktop window).

A pywebview shell that hosts the same React cockpit UI as the VS Code panel, but
as a standalone, sellable app with no editor dependency. It talks straight to the
in-process coordination / brain / phone layer (no MCP client, no subprocess for
reads).

Run:   python -m sassymcp.desktop          (opens the window)
Check: python -m sassymcp.desktop --check  (headless data-path smoke test)
"""
