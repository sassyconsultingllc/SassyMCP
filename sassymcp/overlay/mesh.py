"""Overlay <-> SassyMCP bridge: coordination reads + action side-effects.

Reads the coordination board directly (same process), and runs the second head
(hermes_node.py) / utility actions as side-effects. No new ports, no MCP client.
"""

import os
import subprocess
import sys
from pathlib import Path

_hermes_proc: "subprocess.Popen | None" = None


def repo_root() -> Path:
    """Repo root that holds hermes_node.py (parent of the sassymcp package)."""
    env = os.environ.get("SASSYMCP_REPO")
    if env and Path(env).exists():
        return Path(env)
    import sassymcp
    return Path(sassymcp.__file__).resolve().parent.parent


def hermes_path() -> Path:
    override = os.environ.get("HERMES_NODE_PATH")
    if override:
        return Path(override)
    return repo_root() / "hermes_node.py"


def hermes_running() -> bool:
    return _hermes_proc is not None and _hermes_proc.poll() is None


def start_hermes() -> dict:
    global _hermes_proc
    if hermes_running():
        return {"status": "already_running", "pid": _hermes_proc.pid}  # type: ignore[union-attr]
    hp = hermes_path()
    if not hp.exists():
        return {"error": f"hermes_node.py not found at {hp}. Set HERMES_NODE_PATH or SASSYMCP_REPO."}
    env = dict(os.environ)
    env.setdefault("HERMES_AUTORUN", "0")
    try:
        _hermes_proc = subprocess.Popen([sys.executable, str(hp)], cwd=str(repo_root()), env=env)
    except Exception as e:  # pragma: no cover - spawn failure is environment-specific
        return {"error": str(e)}
    return {"status": "started", "pid": _hermes_proc.pid}


def stop_hermes() -> dict:
    global _hermes_proc
    if hermes_running():
        try:
            _hermes_proc.terminate()  # type: ignore[union-attr]
        except Exception:
            pass
        _hermes_proc = None
        return {"status": "stopped"}
    return {"status": "not_running"}


def status() -> dict:
    """Live mesh summary for the launcher header."""
    try:
        from sassymcp.modules.coordination import board_snapshot
        b = board_snapshot()
    except Exception as e:
        return {"heads": 1 if hermes_running() else 0, "peers": [], "channels": [],
                "hermes": hermes_running(), "error": str(e)}
    alive = [p for p in b.get("peers", []) if p.get("alive")]
    heads = len(alive) + (0 if any(p.get("name", "").lower().startswith("hermes") for p in alive) else (1 if hermes_running() else 0))
    return {
        "heads": heads,
        "peers": b.get("peers", []),
        "channels": b.get("channels", []),
        "hermes": hermes_running(),
    }


def announce_self() -> None:
    try:
        from sassymcp.modules.coordination import announce_peer
        announce_peer("sassy-overlay", "Sassy Overlay", "desktop", "overlay,launcher", ttl_seconds=600)
    except Exception:
        pass


def open_vscode() -> None:
    try:
        subprocess.Popen(["code", str(repo_root())], shell=True)
    except Exception:
        pass


def open_home() -> None:
    try:
        from sassymcp._paths import HOME
        if hasattr(os, "startfile"):
            os.startfile(str(HOME))  # type: ignore[attr-defined]  # Windows only
        else:
            subprocess.Popen(["xdg-open", str(HOME)])
    except Exception:
        pass
