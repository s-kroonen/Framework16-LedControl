"""
System tray icon and context menu.

Uses pystray.  The icon is a simple 64×64 image generated with Pillow.
All user actions are forwarded to the service loop via the command queue.
"""

from __future__ import annotations

import logging
import queue
import sys
from typing import Callable

from PIL import Image, ImageDraw

try:
    import pystray
    from pystray import MenuItem as Item
except ImportError:
    pystray = None  # type: ignore

log = logging.getLogger(__name__)


def _make_icon_image(size: int = 64) -> Image.Image:
    """Draw a simple 3×3 LED-grid icon."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    dot = size // 5
    gap = dot
    colors = [
        (255, 120, 0),  (200, 100, 0), (255, 120, 0),
        (200, 100, 0),  (255, 200, 50),(200, 100, 0),
        (255, 120, 0),  (200, 100, 0), (255, 120, 0),
    ]
    for i, color in enumerate(colors):
        row, col = divmod(i, 3)
        x = gap + col * (dot + gap)
        y = gap + row * (dot + gap)
        draw.ellipse([x, y, x + dot, y + dot], fill=color)
    return img


class TrayIcon:
    """
    Manages the system-tray icon.  Call .run() to enter the pystray
    event loop (blocks the calling thread; run on the main thread).
    """

    def __init__(
        self,
        cmd_queue: "queue.Queue[str]",
        on_quit: Callable[[], None],
    ):
        self._q = cmd_queue
        self._on_quit = on_quit
        self._icon: pystray.Icon | None = None

    # ------------------------------------------------------------------

    def _send(self, cmd: str) -> None:
        self._q.put(cmd)

    # ------------------------------------------------------------------
    # Menu callbacks
    # ------------------------------------------------------------------

    def _set_mode(self, mode: str):
        def _cb(icon, item):  # noqa: ANN001
            self._send(f"mode:{mode}")
        return _cb

    def _set_brightness(self, value: int):
        def _cb(icon, item):  # noqa: ANN001
            self._send(f"brightness:{value}")
        return _cb

    def _quit_cb(self, icon, item) -> None:  # noqa: ANN001
        self._send("quit")
        icon.stop()
        self._on_quit()

    def _sleep_cb(self, icon, item) -> None:
        self._send("sleep")

    def _wake_cb(self, icon, item) -> None:
        self._send("wake")

    # ------------------------------------------------------------------

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            Item("LED Matrix Control", None, enabled=False),
            pystray.Menu.SEPARATOR,
            Item(
                "Display mode",
                pystray.Menu(
                    Item("System bars",  self._set_mode("bars")),
                    Item("CPU cores",    self._set_mode("cpu_cores")),
                    Item("Clock",        self._set_mode("clock")),
                    Item("Breathe",      self._set_mode("breathe")),
                ),
            ),
            Item(
                "Brightness",
                pystray.Menu(
                    Item("25%",   self._set_brightness(64)),
                    Item("50%",   self._set_brightness(128)),
                    Item("75%",   self._set_brightness(192)),
                    Item("100%",  self._set_brightness(255)),
                ),
            ),
            pystray.Menu.SEPARATOR,
            Item("Sleep matrix",  self._sleep_cb),
            Item("Wake matrix",   self._wake_cb),
            pystray.Menu.SEPARATOR,
            Item("Quit",          self._quit_cb),
        )

    # ------------------------------------------------------------------

    def run(self) -> None:
        """Enter the pystray event loop (blocks)."""
        if pystray is None:
            log.error("pystray is not installed; tray icon unavailable")
            # Without a tray we just block forever so the service keeps running
            import time
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                self._on_quit()
            return

        self._icon = pystray.Icon(
            name="LedControl",
            icon=_make_icon_image(),
            title="Framework LED Matrix",
            menu=self._build_menu(),
        )
        log.info("Tray icon starting")
        self._icon.run()
