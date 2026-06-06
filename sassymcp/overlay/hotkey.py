"""Global hotkey registration. `keyboard` may need admin on some Win11 configs;
if it fails we degrade gracefully — the tray icon still opens the launcher."""

from typing import Callable


def register_hotkey(callback: Callable[[], None], combo: str = "ctrl+alt+s") -> bool:
    """Register a global hotkey. Returns True on success, False if unavailable."""
    try:
        import keyboard
        keyboard.add_hotkey(combo, callback)
        return True
    except Exception:
        return False


def unregister_hotkeys() -> None:
    """Remove all global hotkeys (best effort, called on quit)."""
    try:
        import keyboard
        keyboard.unhook_all()
    except Exception:
        pass
