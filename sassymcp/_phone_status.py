"""Phone (Android/ADB) device snapshot for the Sassy Brain cockpit.

Lists connected ADB devices so the cockpit can show the phone as a coordinated
node. Stdlib only — shells out to `adb devices -l`. Degrades cleanly when adb
isn't installed/on PATH. Used by both the standalone app and the VS Code host
via `python -m sassymcp._phone_status`.
"""

import json
import os
import shutil
import subprocess
import sys


def _adb_path() -> str | None:
    # Honor an explicit override, else PATH, else a couple of common spots.
    for cand in (os.environ.get("SASSYMCP_ADB"), "adb", shutil.which("adb")):
        if cand and (cand in ("adb",) or os.path.exists(cand)):
            return cand
    local = os.path.expanduser(r"~\AppData\Local\Android\Sdk\platform-tools\adb.exe")
    return local if os.path.exists(local) else None


def snapshot() -> dict:
    adb = _adb_path()
    if not adb:
        return {"devices": [], "adb": False,
                "error": "adb not found — install platform-tools or set SASSYMCP_ADB"}
    try:
        out = subprocess.run([adb, "devices", "-l"], capture_output=True, text=True, timeout=10)
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
