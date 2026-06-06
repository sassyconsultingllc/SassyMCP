"""SassyMCP desktop overlay — tray icon + global hotkey + a frameless launcher
that surfaces the live multi-AI coordination mesh and quick actions when VS Code
is closed. Runs in the SassyMCP Python env, so it imports the coordination layer
directly (no subprocess for reads) and spawns hermes_node.py for the second head.

Run:  python -m sassymcp.overlay        (tray + Ctrl+Alt+S launcher)
Check: python -m sassymcp.overlay --check
"""
