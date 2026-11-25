# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-Projects-UNYJWFHDOPMI
"""Phone (Android/ADB) device snapshot for the Sassy Brain cockpit.

Lists connected ADB devices so the cockpit can show the phone as a coordinated
node. Stdlib only — shells out to `adb devices -l`. Degrades cleanly when adb
isn't installed/on PATH. Used by both the standalone app and the VS Code host
via `python -m sassymcp._phone_status`.
"""

import json
import os
import subprocess
import sys

from sassymcp import _platform


def _adb_path() -> str | None:
    # Single shared resolution (audit F-5): honors SASSYMCP_ADB, then PATH,
    # then per-OS candidates. Returns None when nothing resolves so the
    # cockpit degrades to its clean "adb not found" message.
    resolved = _platform.resolve_adb()
    if resolved == "adb":
        return None
    return resolved if os.path.isfile(resolved) else None


def snapshot() -> dict:
    adb = _adb_path()
    if not adb:
        return {"devices": [], "adb": False,
                "error": "adb not found — install platform-tools or set SASSYMCP_ADB"}
    try:
        # check=False: the exit code is ignored on purpose — stdout is parsed below.
        out = subprocess.run([adb, "devices", "-l"], capture_output=True, text=True,
                             timeout=10, check=False)
    except Exception as e:
        return {"devices": [], "adb": True, "error": f"adb error: {e}"}

    devices = []
    for line in out.stdout.splitlines()[1:]:  # skip "List of devices attached"
        line = line.strip()
        if not line or "\t" not in line and " " not in line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        model = ""
        for tok in parts[2:]:
            if tok.startswith("model:"):
                model = tok.split(":", 1)[1].replace("_", " ")
        devices.append({"serial": serial, "state": state, "model": model or serial})
    return {"devices": devices, "adb": True}


if __name__ == "__main__":
    try:
        sys.stdout.write(json.dumps(snapshot()))
    except Exception as e:
        sys.stdout.write(json.dumps({"devices": [], "adb": False, "error": str(e)}))
        sys.exit(1)
