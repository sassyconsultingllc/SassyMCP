"""JS<->Python bridge for the standalone cockpit. Exposed to the webview as
`window.pywebview.api`. The React app speaks the same message protocol it uses
under VS Code; `request()` answers it from the in-process coordination layer.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


class Bridge:
    def log(self, text: str) -> None:
        """JS-side error/diagnostic sink — prints to the app's stdout."""
        try:
            print(f"[webview] {text}", flush=True)
        except Exception:
            pass

    def request(self, msg_json: str) -> str:
        """Handle one outbound webview message; return a JSON list of inbound
        messages ({type:'board'|'brain'|'phone'}) for the shim to dispatch."""
        try:
            msg = json.loads(msg_json) if isinstance(msg_json, str) else (msg_json or {})
        except Exception:
            msg = {}
        t = msg.get("type")
        out: list[dict] = []
        try:
            if t in ("ready", "refresh"):
                out.append({"type": "board", "data": self._board()})
                if t == "ready":
                    out.append({"type": "brain", "data": self._brain()})
                    out.append({"type": "phone", "data": self._phone()})
            elif t == "refreshBrain":
                out.append({"type": "brain", "data": self._brain()})
            elif t == "refreshPhone":
                out.append({"type": "phone", "data": self._phone()})
            elif t == "announce":
                self._announce("sassy-brain", "Sassy Brain", "desktop", "cockpit,observer")
                out.append({"type": "board", "data": self._board()})
            elif t == "action":
                self._action(msg)
                out.append({"type": "board", "data": self._board()})
                if msg.get("action") in ("observePhone", "mirrorPhone"):
                    out.append({"type": "phone", "data": self._phone()})
        except Exception as e:
            out.append({"type": "board",
                        "data": {"peers": [], "channels": [], "handoffs": [], "sessions": [], "error": str(e)}})
        return json.dumps(out)

    # ── data ──────────────────────────────────────────────────────────
    def _board(self) -> dict:
        from sassymcp.modules.coordination import board_snapshot
        return board_snapshot()

    def _brain(self) -> dict:
        from sassymcp import _brain_status
        return _brain_status.snapshot()

    def _phone(self) -> dict:
        from sassymcp import _phone_status
        return _phone_status.snapshot()

    def _announce(self, pid, name, platform, caps) -> None:
        try:
            from sassymcp.modules.coordination import announce_peer
            announce_peer(pid, name, platform, caps, ttl_seconds=600)
        except Exception:
            pass

    # ── actions (best effort) ─────────────────────────────────────────
    def _action(self, msg: dict) -> None:
        action = msg.get("action")
        serial = msg.get("serial")
        if action == "observePhone":
            # Surface the phone as a coordinated node in the mesh.
            self._announce(f"phone-{serial or 'device'}", serial or "phone", "android", "screen,ui,tap,swipe")
        elif action == "mirrorPhone":
            self._spawn(["scrcpy"] + (["-s", serial] if serial else []))
        elif action == "openHome":
            self._open(self._home())
        elif action == "openAudit":
            self._open(self._home() / "audit.log")
        elif action == "runWizard":
            exe = Path(sys.executable).with_name("sassymcp.exe")
            self._spawn([str(exe) if exe.exists() else sys.executable,
                         *([] if exe.exists() else ["-m", "sassymcp"]), "setup-wizard"])

    def _home(self) -> Path:
        try:
            from sassymcp._paths import HOME
            return Path(HOME)
        except Exception:
            return Path(os.path.expanduser("~/.sassymcp"))

    def _open(self, path: Path) -> None:
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            pass

    def _spawn(self, args: list) -> None:
        try:
            subprocess.Popen(args, shell=False)
        except Exception:
            pass
