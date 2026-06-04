"""
System tray icon and context menu.

The entire menu is built fresh each time it is opened by passing a
generator function to pystray.Menu — this ensures every checkmark and
slot label reflects the live config state without manual rebuilding.

Menu structure
──────────────
  [header: LED Matrix Control]
  ─────
  Display mode ▶  bars / cpu_cores / clock / breathe
  ─────
  Column layout
    Slot 1: CPU %  ▶  Empty | CPU % ✓ | RAM % | ...
    Slot 2: RAM %  ▶  ...
    ...
    Slot 9: GPU Temp °C  ▶  ...
  ─────
  Brightness ▶  25 / 50 / 75 / 100 %
  Link to screen brightness  [checked]
  ─────
  Start on boot  [checked]
  ─────
  Sleep matrix
  Wake matrix
  ─────
  Quit
"""

from __future__ import annotations

import logging
import queue
from typing import Callable, Optional

from PIL import Image, ImageDraw

try:
    import pystray
    from pystray import MenuItem as Item
except ImportError:
    pystray = None  # type: ignore

from . import startup
from .config import ALL_STAT_KEYS, STAT_LABELS, NUM_SLOTS, Config

log = logging.getLogger(__name__)


def _make_icon_image(size: int = 64) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    dot = size // 5
    gap = dot
    colors = [
        (255, 120, 0), (200, 100, 0), (255, 120, 0),
        (200, 100, 0), (255, 200, 50), (200, 100, 0),
        (255, 120, 0), (200, 100, 0), (255, 120, 0),
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
        config: Config,
        on_quit: Callable[[], None],
    ):
        self._q = cmd_queue
        self._config = config
        self._on_quit = on_quit
        self._icon: "pystray.Icon | None" = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _send(self, cmd: str) -> None:
        self._q.put(cmd)

    def _slot_label(self, slot_idx: int) -> str:
        key = self._config.bar_slots[slot_idx]
        val = STAT_LABELS.get(key, "—") if key else "Empty"
        return f"Slot {slot_idx + 1}:  {val}"

    # ------------------------------------------------------------------
    # Slot submenu — one per column, rebuilt on every open
    # ------------------------------------------------------------------

    def _make_slot_submenu(self, slot_idx: int) -> "pystray.Menu":
        def items():
            # Read current assignment fresh every time the submenu opens
            current = self._config.bar_slots[slot_idx]

            yield Item(
                "Empty",
                self._set_slot_cb(slot_idx, None),
                checked=lambda item, c=current: c is None,
                radio=True,
            )
            for key in ALL_STAT_KEYS:
                label = STAT_LABELS[key]
                yield Item(
                    label,
                    self._set_slot_cb(slot_idx, key),
                    checked=lambda item, k=key, c=current: c == k,
                    radio=True,
                )

        return pystray.Menu(items)

    def _set_slot_cb(self, slot_idx: int, key: Optional[str]) -> Callable:
        def cb(icon, item):
            # Update config in-place so the next menu open shows the new value
            self._config.bar_slots[slot_idx] = key
            key_str = key if key is not None else "None"
            self._send(f"set_slot:{slot_idx}:{key_str}")

        return cb

    # ------------------------------------------------------------------
    # Main menu — generator is called every time the menu opens
    # ------------------------------------------------------------------

    def _menu_items(self):
        cfg = self._config

        yield Item("LED Matrix Control", None, enabled=False)
        yield pystray.Menu.SEPARATOR

        # --- Display mode ---
        yield Item(
            "Display mode",
            pystray.Menu(
                Item("System bars",  lambda icon, item: self._send("mode:bars")),
                Item("CPU cores",    lambda icon, item: self._send("mode:cpu_cores")),
                Item("Clock",        lambda icon, item: self._send("mode:clock")),
                Item("Breathe",      lambda icon, item: self._send("mode:breathe")),
            ),
        )
        yield pystray.Menu.SEPARATOR

        # --- Column layout (9 slots) ---
        for i in range(NUM_SLOTS):
            yield Item(
                self._slot_label(i),           # live label
                self._make_slot_submenu(i),
            )
        yield pystray.Menu.SEPARATOR

        # --- Brightness ---
        yield Item(
            "Brightness",
            pystray.Menu(
                Item("25%",   lambda icon, item: self._send("brightness:64")),
                Item("50%",   lambda icon, item: self._send("brightness:128")),
                Item("75%",   lambda icon, item: self._send("brightness:192")),
                Item("100%",  lambda icon, item: self._send("brightness:255")),
            ),
        )
        yield Item(
            "Link to screen brightness",
            self._toggle_link_brightness,
            checked=lambda item: bool(cfg.link_screen_brightness),
        )
        yield pystray.Menu.SEPARATOR

        # --- Startup ---
        yield Item(
            "Start on boot",
            self._toggle_start_on_boot,
            checked=lambda item: startup.is_enabled(),
        )
        yield pystray.Menu.SEPARATOR

        # --- Hardware control ---
        yield Item("Sleep matrix", lambda icon, item: self._send("sleep"))
        yield Item("Wake matrix",  lambda icon, item: self._send("wake"))
        yield pystray.Menu.SEPARATOR

        yield Item("Quit", self._quit_cb)

    # ------------------------------------------------------------------
    # Action callbacks
    # ------------------------------------------------------------------

    def _toggle_link_brightness(self, icon, item) -> None:
        new = not self._config.link_screen_brightness
        self._config.link_screen_brightness = new
        self._send(f"link_brightness:{'1' if new else '0'}")

    def _toggle_start_on_boot(self, icon, item) -> None:
        new = not startup.is_enabled()
        startup.sync(new)
        self._config.start_on_boot = new
        self._config.save()

    def _quit_cb(self, icon, item) -> None:
        self._send("quit")
        icon.stop()
        self._on_quit()

    # ------------------------------------------------------------------

    def run(self) -> None:
        """Enter the pystray event loop (blocks)."""
        if pystray is None:
            log.error("pystray is not installed; tray icon unavailable")
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
            menu=pystray.Menu(self._menu_items),
        )
        log.info("Tray icon starting")
        self._icon.run()
