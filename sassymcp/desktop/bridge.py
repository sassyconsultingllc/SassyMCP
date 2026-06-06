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
        if t not in ("ready", "refresh", "refreshBrain", "refreshPhone"):
            self.log(f"request: {t} action={msg.get('action')}")
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
        self.log(f"action: {action} serial={serial}")
        if action == "observePhone":
            # Surface the phone as a coordinated node in the mesh.
            self._announce(f"phone-{serial or 'device'}", serial or "phone", "android", "screen,ui,tap,swipe")
            self.log("observePhone -> announced phone peer")
        elif action == "mirrorPhone":
            self.log(f"mirrorPhone -> scrcpy (serial={serial})")
            self._spawn(["scrcpy"] + (["-s", serial] if serial else []))
        elif action == "openHome":
            self.log(f"openHome -> explorer {self._home()}")
            self._open_folder(self._home())
        elif action == "openAudit":
            p = self._home() / "audit.log"
            self.log(f"openAudit -> {p} exists={p.exists()}")
            self._open_text(p)
        elif action == "runWizard":
            self.log("runWizard -> persona.md")
            self._open_text(self._home() / "persona.md")
        else:
            self.log(f"unknown action: {action}")

    def _home(self) -> Path:
        try:
            from sassymcp._paths import HOME
            return Path(HOME)
        except Exception:
            return Path(os.path.expanduser("~/.sassymcp"))

    def _open_folder(self, path: Path) -> None:
        """Open a folder in the OS file manager (never a browser)."""
        try:
            path = Path(path)
            if os.name == "nt":
                subprocess.Popen(["explorer", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            self.log(f"open_folder fail: {e}")

    def _open_text(self, path: Path) -> None:
        """Open a text file in a known text editor — bypasses the file
        association so it never lands in the user's default browser."""
        path = Path(path)
        if not path.exists():
            self.log(f"open_text: {path} not found")
            return
        try:
            if os.name == "nt":
                subprocess.Popen(["notepad.exe", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-t", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            self.log(f"open_text fail: {e}")

    def _spawn(self, args: list) -> None:
        try:
            subprocess.Popen(args, shell=False)
        except Exception:
            pass
