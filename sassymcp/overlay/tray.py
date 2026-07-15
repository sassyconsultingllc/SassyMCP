"""System-tray icon for the overlay. Menu items enqueue commands (thread-safe);
the Tk main thread drains the queue. pystray runs in a daemon thread."""

from typing import Callable

import pystray
from PIL import Image, ImageDraw


def icon_image() -> Image.Image:
    """A small magenta 'brain' disc — the Sassy mark."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((6, 6, size - 6, size - 6), fill=(214, 64, 159, 255))
    d.ellipse((22, 18, 34, 30), fill=(255, 255, 255, 230))
    d.ellipse((34, 26, 46, 38), fill=(255, 255, 255, 180))
    d.ellipse((24, 36, 36, 48), fill=(255, 255, 255, 150))
    return img


def build_tray(enqueue: Callable[[str], None]) -> "pystray.Icon":
    menu = pystray.Menu(
        pystray.MenuItem("Show Sassy Brain", lambda icon, item: enqueue("show"), default=True),
        pystray.MenuItem("Start Hermes (2nd head)", lambda icon, item: enqueue("start_hermes")),
        pystray.MenuItem("Stop Hermes", lambda icon, item: enqueue("stop_hermes")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", lambda icon, item: enqueue("quit")),
    )
    return pystray.Icon("sassymcp", icon_image(), "Sassy Brain", menu)
