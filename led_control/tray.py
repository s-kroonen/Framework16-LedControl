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
from .config import ALL_STAT_KEYS, ALERT_MODES, ALERT_MODE_LABELS, STAT_LABELS, TEMP_STAT_KEYS, NUM_SLOTS, Config

log = logging.getLogger(__name__)


def _patch_pystray_icon_suffix() -> None:
    """Give pystray's temp tray-icon file a .png suffix.

    pystray's GTK/AppIndicator backend writes the icon via
    tempfile.mktemp() with no extension. GNOME's AppIndicator extension
    identifies the icon file by its suffix, not by sniffing file
    contents — an extensionless file silently fails to load as an icon,
    leaving the indicator docked but with no working click target.
    """
    try:
        from pystray._util.gtk import GtkIcon
    except ImportError:
        return

    import tempfile as _tempfile

    def _update_fs_icon(self):
        self._icon_path = _tempfile.mktemp(suffix=".png")
        with open(self._icon_path, "wb") as f:
            self.icon.save(f, "PNG")
        self._icon_valid = True

    GtkIcon._update_fs_icon = _update_fs_icon


_patch_pystray_icon_suffix()


def _print_tray_help() -> None:
    """Print actionable fix instructions when the tray icon fails on Linux."""
    import sys
    if sys.platform == "win32":
        return
    print(
        "\n"
        "── Tray icon error ──────────────────────────────────────────────────\n"
        "The system tray icon could not be created.\n"
        "\n"
        "On GNOME (including with Dash to Panel / Arc Menu) you also need the\n"
        "AppIndicator extension:\n"
        "\n"
        "  Fedora:         sudo dnf install gnome-shell-extension-appindicator\n"
        "  Ubuntu/Debian:  sudo apt install gnome-shell-extension-appindicator\n"
        "\n"
        "Then enable it:\n"
        "  gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com\n"
        "  (or use the GNOME Extensions app)\n"
        "\n"
        "Log out and back in after enabling.\n"
        "\n"
        "KDE / XFCE: tray should work without extra extensions.\n"
        "\n"
        "Running headless for now — the LED matrix is still active.\n"
        "────────────────────────────────────────────────────────────────────\n",
        file=sys.stderr,
    )


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
        # Non-temperature stat keys (shown flat at the top of the submenu)
        _non_temp = [k for k in ALL_STAT_KEYS if k not in TEMP_STAT_KEYS]

        def items():
            current = self._config.bar_slots[slot_idx]

            # Empty option
            yield Item(
                "Empty",
                self._set_slot_cb(slot_idx, None),
                checked=lambda item, c=current: c is None,
                radio=True,
            )

            # Non-temperature stats (flat list)
            for key in _non_temp:
                yield Item(
                    STAT_LABELS[key],
                    self._set_slot_cb(slot_idx, key),
                    checked=lambda item, k=key, c=current: c == k,
                    radio=True,
                )

            # Temperature sensors — nested submenu
            def temp_items(c=current):
                for key in TEMP_STAT_KEYS:
                    yield Item(
                        STAT_LABELS[key],
                        self._set_slot_cb(slot_idx, key),
                        checked=lambda item, k=key, cur=c: cur == k,
                        radio=True,
                    )

            yield Item("Temperatures", pystray.Menu(temp_items))

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

        # --- Alert mode ---
        def _alert_items(c=cfg):
            for am in ALERT_MODES:
                yield Item(
                    ALERT_MODE_LABELS[am],
                    self._set_alert_mode_cb(am),
                    checked=lambda item, m=am, c=c: c.alert_mode == m,
                    radio=True,
                )
        yield Item("Alert mode", pystray.Menu(_alert_items))
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

        # --- Battery saver ---
        yield Item(
            "Auto-off on Battery Saver",
            self._toggle_battery_saver,
            checked=lambda item: bool(cfg.auto_off_battery_saver),
        )
        yield pystray.Menu.SEPARATOR

        # --- Hardware control ---
        yield Item("Sleep matrix", lambda icon, item: self._send("sleep"))
        yield Item("Wake matrix (force on)", lambda icon, item: self._send("wake"))
        yield pystray.Menu.SEPARATOR

        yield Item("Restart", self._restart_cb)
        yield Item("Quit", self._quit_cb)

    # ------------------------------------------------------------------
    # Action callbacks
    # ------------------------------------------------------------------

    def _toggle_link_brightness(self, icon, item) -> None:
        new = not self._config.link_screen_brightness
        self._config.link_screen_brightness = new
        self._send(f"link_brightness:{'1' if new else '0'}")

    def _toggle_battery_saver(self, icon, item) -> None:
        new = not self._config.auto_off_battery_saver
        self._config.auto_off_battery_saver = new
        self._send(f"auto_battery_saver:{'1' if new else '0'}")

    def _set_alert_mode_cb(self, mode: str) -> Callable:
        def cb(icon, item):
            self._config.alert_mode = mode
            self._send(f"set_alert_mode:{mode}")
        return cb

    def _toggle_start_on_boot(self, icon, item) -> None:
        new = not startup.is_enabled()
        startup.sync(new)
        self._config.start_on_boot = new
        self._config.save()

    def _restart_cb(self, icon, item) -> None:
        from . import startup
        startup.launch_background()
        self._send("quit")
        icon.stop()
        self._on_quit()

    def _quit_cb(self, icon, item) -> None:
        self._send("quit")
        icon.stop()
        self._on_quit()

    # ------------------------------------------------------------------

    def run(self) -> None:
        """Enter the pystray event loop (blocks)."""
        if pystray is None:
            log.error("pystray is not installed; tray icon unavailable")
            self._run_headless()
            return

        self._icon = pystray.Icon(
            name="LedControl",
            icon=_make_icon_image(),
            title="Framework LED Matrix",
            menu=pystray.Menu(self._menu_items),
        )
        log.info("Tray icon starting")
        try:
            self._icon.run()
        except Exception as exc:
            log.error("Tray icon failed to start: %s", exc)
            _print_tray_help()
            log.warning("Continuing without tray icon — use Ctrl+C or kill to stop")
            self._run_headless()

    def _run_headless(self) -> None:
        """Block the main thread with no tray; Ctrl-C exits cleanly."""
        import time
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self._on_quit()
