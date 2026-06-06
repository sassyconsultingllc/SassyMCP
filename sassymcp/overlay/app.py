"""Overlay entry point. Tk runs on the main thread; the tray (daemon thread) and
the global hotkey marshal commands through a queue the Tk loop drains — tkinter
is not thread-safe, so nothing else touches it directly."""

import queue
import sys
import threading


class OverlayApp:
    def __init__(self):
        import tkinter as tk
        from .launcher import Launcher
        from .tray import build_tray
        from .hotkey import register_hotkey
        from . import mesh

        self.mesh = mesh
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Sassy Brain Overlay")
        self.q: "queue.Queue[str]" = queue.Queue()
        self.launcher = Launcher(self.root, self)
        self.tray = build_tray(self.enqueue)
        self.hotkey_ok = register_hotkey(lambda: self.enqueue("toggle"))
        self._poll()

    def enqueue(self, cmd: str) -> None:
        self.q.put(cmd)

    def _poll(self) -> None:
        try:
            while True:
                self._dispatch(self.q.get_nowait())
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    def _dispatch(self, cmd: str) -> None:
        if cmd == "toggle":
            self.launcher.toggle()
        elif cmd == "show":
            self.launcher.show()
        elif cmd == "hide":
            self.launcher.hide()
        elif cmd == "start_hermes":
            self.mesh.start_hermes()
        elif cmd == "stop_hermes":
            self.mesh.stop_hermes()
        elif cmd == "quit":
            self.quit()

    def run(self) -> None:
        threading.Thread(target=self.tray.run, daemon=True).start()
        self.mesh.announce_self()
        print(f"[sassy-overlay] live | tray + Ctrl+Alt+S launcher | global hotkey: {'on' if self.hotkey_ok else 'unavailable (use tray)'}")
        self.root.mainloop()

    def quit(self) -> None:
        try:
            from .hotkey import unregister_hotkeys
            unregister_hotkeys()
        except Exception:
            pass
        try:
            self.tray.stop()
        except Exception:
            pass
        try:
            self.mesh.stop_hermes()
        except Exception:
            pass
        self.root.quit()


def _check() -> int:
    """Headless smoke test: import everything, build the icon, read the mesh."""
    from . import mesh, tray
    img = tray.icon_image()
    st = mesh.status()
    print(f"overlay check OK | icon={img.size} | heads={st.get('heads')} "
          f"peers={len(st.get('peers', []))} channels={len(st.get('channels', []))} "
          f"repo={mesh.repo_root()} hermes_node={'found' if mesh.hermes_path().exists() else 'missing'}")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--check" in argv:
        return _check()
    OverlayApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
