# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-4KQFE3TJHI3K
"""Sandbox tests for the sassy_shell timeout auto-promote behaviour.

Run with the project's Python interpreter:
    V:\\tools\\python\\python.exe V:\\Projects\\SassyMCP\\tests\\test_shell_auto_promote.py

Verifies that _run_subprocess promotes to a background session whenever the
requested timeout_seconds exceeds _MCP_SAFE_TIMEOUT (120s), so the MCP
client's ~240s response wall never wedges the connection. Short timeouts
should still run synchronously and return the captured output.
"""
import asyncio
import json
import sys

sys.path.insert(0, r"V:\Projects\SassyMCP")

from sassymcp.modules import shell as shell_mod
from sassymcp.modules import session as session_mod

PASS = 0
FAIL = 0


def check(label, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"OK   {label}")
    else:
        FAIL += 1
        print(f"FAIL {label}  {extra}")


async def _cleanup_session(name: str | None):
    if not name:
        return
    sess = session_mod._sessions.pop(name, None)
    if sess:
        try:
            await sess.stop()
        except Exception:
            pass


async def main():
    threshold = shell_mod._MCP_SAFE_TIMEOUT
    check(f"_MCP_SAFE_TIMEOUT defined at <=120s (got {threshold})", threshold <= 120)

    # Case 1: timeout above the safe ceiling -> auto-detach
    result = await shell_mod._run_subprocess("powershell", "Write-Host hi", threshold + 1)
    parsed = json.loads(result)
    check(
        f"timeout={threshold+1} -> auto_detached:true",
        parsed.get("auto_detached") is True,
        extra=f"got {result!r}",
    )
    check("auto-detach response includes session_name", bool(parsed.get("session_name")))
    check("auto-detach response includes hint", "session_read" in (parsed.get("hint") or ""))
    await _cleanup_session(parsed.get("session_name"))

    # Case 2: timeout at the ceiling -> still synchronous
    result = await shell_mod._run_subprocess("powershell", "Write-Host hi", threshold)
    check(
        f"timeout={threshold} -> still synchronous",
        not result.startswith("{") or '"auto_detached": true' not in result,
        extra=f"got {result!r}",
    )
    check("synchronous run captured stdout", "hi" in result)

    # Case 3: default 30s timeout -> still synchronous
    result = await shell_mod._run_subprocess("powershell", "Write-Host default", 30)
    check("timeout=30 -> synchronous", "default" in result and "auto_detached" not in result)

    # Case 4: a much larger value (e.g. 600) still cleanly detaches, doesn't hang
    result = await shell_mod._run_subprocess("powershell", "Start-Sleep -Seconds 1", 600)
    parsed = json.loads(result)
    check(
        "timeout=600 -> auto-detach (no hang)",
        parsed.get("auto_detached") is True,
        extra=f"got {result!r}",
    )
    await _cleanup_session(parsed.get("session_name"))

    # Case 5: failure surface — auto-detach with an invalid shell should report error
    result = await shell_mod._run_subprocess("notashell", "echo x", 300)
    parsed = json.loads(result)
    check(
        "invalid shell during auto-detach surfaces error",
        parsed.get("auto_detached") is False and "error" in parsed,
        extra=f"got {result!r}",
    )


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(0 if FAIL == 0 else 1)
