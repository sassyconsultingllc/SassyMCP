"""Frameless, always-on-top launcher window (Spotlight-style). Type to filter,
arrow keys to move, Enter to run, Esc to dismiss. Shows the live heads-count and
a fuzzy action list. Pure tkinter (stdlib) — no heavy GUI dependency."""

import tkinter as tk
from tkinter import font as tkfont

from . import mesh

BG = "#1b1b1d"
CARD = "#232327"
FG = "#e6e6e6"
DIM = "#9a9aa2"
ACCENT = "#d6409f"
ACCENT2 = "#9d4edd"


class Launcher:
    def __init__(self, root: tk.Tk, app):
        self.app = app
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.configure(bg=ACCENT)
        except tk.TclError:
            pass

        # (label, callback, keep_open)
        self.actions = [
            ("⚡  Start Hermes  —  bring the 2nd head online", lambda: self.app.enqueue("start_hermes"), False),
            ("■  Stop Hermes", lambda: self.app.enqueue("stop_hermes"), False),
            ("↻  Refresh mesh status", self.refresh, True),
            ("\U0001f4e3  Announce overlay to the mesh", mesh.announce_self, True),
            ("\U0001f9e0  Open repo in VS Code (full cockpit)", mesh.open_vscode, False),
            ("\U0001f4c2  Open ~/.sassymcp folder", mesh.open_home, False),
            ("✕  Quit overlay", lambda: self.app.enqueue("quit"), False),
        ]
        self.filtered = list(self.actions)

        outer = tk.Frame(self.win, bg=BG, bd=0)
        outer.pack(fill="both", expand=True, padx=2, pady=2)

        head = tk.Frame(outer, bg=BG)
        head.pack(fill="x", padx=14, pady=(12, 6))
        bold = tkfont.Font(family="Segoe UI", size=14, weight="bold")
        tk.Label(head, text="\U0001f9e0 Sassy Brain", bg=BG, fg=FG, font=bold).pack(side="left")
        self.heads = tk.Label(head, text="", bg=BG, fg=ACCENT, font=("Segoe UI", 11, "bold"))
        self.heads.pack(side="right")

        self.entry = tk.Entry(
            outer, bg=CARD, fg=FG, insertbackground=FG, relief="flat",
            font=("Segoe UI", 12), highlightthickness=1, highlightbackground=ACCENT2,
            highlightcolor=ACCENT,
        )
        self.entry.pack(fill="x", padx=14, pady=6, ipady=6)

        self.listbox = tk.Listbox(
            outer, bg=BG, fg=FG, selectbackground=ACCENT, selectforeground="#ffffff",
            relief="flat", font=("Segoe UI", 11), height=len(self.actions),
            activestyle="none", highlightthickness=0,
        )
        self.listbox.pack(fill="both", expand=True, padx=10, pady=(2, 6))

        self.statusline = tk.Label(outer, text="", bg=BG, fg=DIM, font=("Segoe UI", 9), anchor="w")
        self.statusline.pack(fill="x", padx=14, pady=(0, 10))

        self.entry.bind("<KeyRelease>", self._on_key)
        self.entry.bind("<Down>", lambda e: (self._select(0), "break"))
        self.entry.bind("<Return>", lambda e: self._run(self._current()))
        self.entry.bind("<Escape>", lambda e: self.hide())
        self.listbox.bind("<Return>", lambda e: self._run(self.listbox.curselection() and self.listbox.curselection()[0] or 0))
        self.listbox.bind("<Double-Button-1>", lambda e: self._run(self.listbox.nearest(e.y)))
        self.listbox.bind("<Escape>", lambda e: self.hide())
        self.win.bind("<FocusOut>", self._on_focus_out)

        self._render()

    # ── data ──────────────────────────────────────────────────────────
    def refresh(self):
        st = mesh.status()
        n = st.get("heads", 0)
        self.heads.config(text=f"⚡ {n} head{'s' if n != 1 else ''} live" if n else "idle")
        peers = st.get("peers", [])
        alive = [p for p in peers if p.get("alive")]
        chans = len(st.get("channels", []))
        names = ", ".join(p.get("name") or p.get("peer_id") for p in alive[:4]) or "no live peers"
        self.statusline.config(text=f"{names}   ·   {chans} channels   ·   hermes: {'on' if st.get('hermes') else 'off'}")

    # ── filtering / rendering ─────────────────────────────────────────
    def _on_key(self, event):
        if event.keysym in ("Down", "Up", "Return", "Escape"):
            return
        q = self.entry.get().strip().lower()
        self.filtered = [a for a in self.actions if q in a[0].lower()] if q else list(self.actions)
        self._render()

    def _render(self):
        self.listbox.delete(0, tk.END)
        for label, _cb, _keep in self.filtered:
            self.listbox.insert(tk.END, "  " + label)
        if self.filtered:
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(0)

    def _current(self) -> int:
        sel = self.listbox.curselection()
        return sel[0] if sel else 0

    def _select(self, i: int):
        if not self.filtered:
            return
        self.listbox.focus_set()
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(i)
        self.listbox.activate(i)

    def _run(self, i: int):
        if not self.filtered or i < 0 or i >= len(self.filtered):
            return "break"
        _label, cb, keep = self.filtered[i]
        try:
            cb()
        except Exception:
            pass
        if keep:
            self.refresh()
        else:
            self.hide()
        return "break"

    # ── window ────────────────────────────────────────────────────────
    def _on_focus_out(self, _event):
        # Click-away to dismiss — but NOT when focus merely moved to a child
        # widget (Entry/Listbox). Tk fires <FocusOut> on the toplevel when
        # focus shifts to a child, so we defer and re-check: focus_get()
        # returns a widget if focus is still inside this app, None if it left.
        self.win.after(120, self._maybe_hide)

    def _maybe_hide(self):
        try:
            focused = self.win.focus_get()
        except Exception:
            focused = None
        if focused is None:
            self.hide()

    def visible(self) -> bool:
        return self.win.state() != "withdrawn"

    def show(self):
        w, h = 460, 320
        sw = self.win.winfo_screenwidth()
        x = (sw - w) // 2
        y = max(80, self.win.winfo_screenheight() // 5)
        self.win.geometry(f"{w}x{h}+{x}+{y}")
        self.entry.delete(0, tk.END)
        self.filtered = list(self.actions)
        self._render()
        self.refresh()
        self.win.deiconify()
        self.win.lift()
        self.win.attributes("-topmost", True)
        self.entry.focus_force()

    def hide(self):
        self.win.withdraw()

    def toggle(self):
        if self.visible():
            self.hide()
        else:
            self.show()
