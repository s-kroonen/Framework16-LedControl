"""
Background service loop.

Runs in a dedicated daemon thread.  On each tick it:
  1. Collects system stats
  2. (Optionally) reads screen brightness and scales matrix brightness
  3. Renders a frame for the current display mode
  4. Pushes the frame to the LED matrix

Supported command strings (placed in the queue by tray.py):
  "quit"                      — stop the loop and exit
  "mode:bars"                 — switch display mode
  "mode:cpu_cores"
  "mode:clock"
  "mode:breathe"
  "brightness:180"            — set brightness ceiling (0–255)
  "set_slot:3:gpu"            — assign stat key to column slot (0-based index)
  "set_slot:3:None"           — clear a slot (dark column)
  "sleep"                     — put matrix to sleep (clears force-on override)
  "wake"                      — wake matrix and override battery-saver auto-off
  "link_brightness:1"         — enable/disable screen-brightness linking (0/1)
  "auto_battery_saver:1"      — enable/disable auto-off when Battery Saver is on
  "reload_config"             — re-read config from disk
"""

from __future__ import annotations

import logging
import queue
import threading
import time

from .config import Config, ALL_STAT_KEYS
from .led_driver import LedDriver
from .renderer import Renderer
from .stats import StatsCollector, get_battery_saver, get_screen_brightness, init_wmi_brightness

log = logging.getLogger(__name__)


class ServiceLoop:
    """
    The main background loop.  Instantiate, then call .start().
    Stop by putting "quit" into the command queue, or call .stop().
    """

    def __init__(self, config: Config, command_queue: "queue.Queue[str]"):
        self._config = config
        self._cmd_q = command_queue
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name="LedServiceLoop",
            daemon=True,
        )
        self._thread.start()
        log.info("Service loop started")

    def stop(self) -> None:
        self._stop_event.set()
        self._cmd_q.put("quit")
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Service loop stopped")

    # ------------------------------------------------------------------

    def _run(self) -> None:
        # Initialise COM for this thread so WMI calls work.
        # pythoncom is part of pywin32 and must be called once per thread.
        try:
            import pythoncom
            pythoncom.CoInitialize()
            _com_initialised = True
        except Exception as exc:
            log.debug("pythoncom.CoInitialize skipped: %s", exc)
            _com_initialised = False

        # Now safe to set up WMI brightness connection on this thread.
        init_wmi_brightness()

        cfg = self._config
        driver = LedDriver(port=cfg.serial_port)
        driver.connect()

        stats_collector = StatsCollector()
        renderer = Renderer(
            mode=cfg.mode,
            brightness=cfg.brightness,
            bar_slots=list(cfg.bar_slots),
        )

        if driver.connected:
            driver.sleep(False)

        # --- Reconnection state ---
        _BACKOFF_INITIAL = 1.0    # seconds
        _BACKOFF_MAX     = 60.0   # seconds
        backoff          = _BACKOFF_INITIAL
        next_reconnect_at = 0.0   # monotonic time; 0 = reconnect immediately

        # --- Brightness-link state ---
        # Track the last screen percentage we acted on so we only call
        # renderer updates (and emit logs) when the value actually changes.
        last_screen_pct: int | None = None

        # --- Battery-saver auto-off state ---
        # _batt_saver_sleeping: matrix was auto-slept due to battery saver
        # _force_on: user explicitly woke the matrix, overrides battery saver
        _batt_saver_sleeping = False
        _force_on = False

        # Shared mutable state accessed by _handle_command
        state = {"force_on": False, "batt_saver_sleeping": False}

        last_tick = 0.0

        while not self._stop_event.is_set():
            # --- Process pending commands ---
            try:
                while True:
                    cmd = self._cmd_q.get_nowait()
                    self._handle_command(cmd, driver, renderer, state)
                    if cmd.startswith(("brightness:", "link_brightness:")):
                        last_screen_pct = None
            except queue.Empty:
                pass

            if self._stop_event.is_set():
                break

            now = time.monotonic()

            # --- Reconnect with exponential backoff ---
            if not driver.connected:
                if now >= next_reconnect_at:
                    log.info(
                        "LED matrix not connected — attempting reconnect "
                        "(next backoff: %.0f s) ...", backoff
                    )
                    if driver.reconnect():
                        log.info("Reconnected to LED matrix")
                        driver.sleep(False)
                        backoff = _BACKOFF_INITIAL
                        next_reconnect_at = 0.0
                    else:
                        next_reconnect_at = now + backoff
                        log.info(
                            "Reconnect failed — next attempt in %.0f s", backoff
                        )
                        backoff = min(backoff * 2.0, _BACKOFF_MAX)
                time.sleep(0.05)
                continue   # skip frame rendering until connected

            # --- Battery-saver auto-off ---
            _force_on = state["force_on"]
            _batt_saver_sleeping = state["batt_saver_sleeping"]
            if cfg.auto_off_battery_saver:
                batt_saver_active = get_battery_saver()
                if batt_saver_active and not _force_on and not _batt_saver_sleeping:
                    log.info("Battery Saver active — sleeping LED matrix")
                    driver.sleep(True)
                    state["batt_saver_sleeping"] = True
                elif not batt_saver_active and _batt_saver_sleeping:
                    log.info("Battery Saver off — waking LED matrix")
                    driver.sleep(False)
                    state["batt_saver_sleeping"] = False
                    state["force_on"] = False

            if state["batt_saver_sleeping"]:
                time.sleep(0.5)
                continue

            # --- Tick ---
            if now - last_tick >= cfg.tick_interval:
                last_tick = now
                try:
                    stats = stats_collector.collect()

                    # Screen-brightness linking.
                    if cfg.link_screen_brightness:
                        screen_pct = get_screen_brightness()
                        if screen_pct is not None and screen_pct != last_screen_pct:
                            effective = max(5, int(screen_pct / 100.0 * cfg.brightness))
                            log.info(
                                "Screen brightness: %d%% -> matrix %d (ceiling %d)",
                                screen_pct, effective, cfg.brightness,
                            )
                            renderer.brightness = effective
                            last_screen_pct = screen_pct

                    frame = renderer.render(stats)
                    driver.draw_frame(frame)
                except Exception as exc:
                    log.error("Tick error: %s", exc)

            time.sleep(0.05)

        if driver.connected:
            driver.clear()
            driver.sleep(True)
        driver.disconnect()

        if _com_initialised:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass

        log.info("Service loop exited cleanly")

    def _handle_command(
        self,
        cmd: str,
        driver: LedDriver,
        renderer: Renderer,
        state: dict,
    ) -> None:
        log.debug("Command: %s", cmd)
        cfg = self._config

        if cmd == "quit":
            self._stop_event.set()

        elif cmd.startswith("mode:"):
            new_mode = cmd.split(":", 1)[1]
            renderer.mode = new_mode
            cfg.mode = new_mode
            cfg.save()

        elif cmd.startswith("brightness:"):
            try:
                value = max(0, min(255, int(cmd.split(":", 1)[1])))
                renderer.brightness = value   # software scaling only
                cfg.brightness = value
                cfg.save()
            except ValueError:
                log.warning("Invalid brightness value: %s", cmd)

        elif cmd.startswith("set_slot:"):
            # Format: set_slot:<index>:<key_or_None>
            parts = cmd.split(":", 2)
            if len(parts) == 3:
                try:
                    idx = int(parts[1])
                    key_raw = parts[2]
                    key = None if key_raw in ("None", "", "null") else key_raw
                    if key is not None and key not in ALL_STAT_KEYS:
                        log.warning("Unknown stat key in set_slot: %s", key)
                    else:
                        cfg.set_slot(idx, key)
                        renderer.bar_slots = list(cfg.bar_slots)
                        cfg.save()
                        log.debug("Slot %d -> %s", idx, key)
                except (ValueError, IndexError) as exc:
                    log.warning("set_slot error: %s (%s)", cmd, exc)

        elif cmd.startswith("link_brightness:"):
            enabled = cmd.split(":", 1)[1] == "1"
            cfg.link_screen_brightness = enabled
            if not enabled:
                renderer.brightness = cfg.brightness
            cfg.save()
            log.info("Screen brightness linking: %s", "on" if enabled else "off")

        elif cmd == "sleep":
            # Manual sleep — also clears force-on so battery saver can resume control
            state["force_on"] = False
            state["batt_saver_sleeping"] = False
            driver.sleep(True)

        elif cmd == "wake":
            # Wake and override any battery-saver auto-off
            state["force_on"] = True
            state["batt_saver_sleeping"] = False
            driver.sleep(False)
            log.info("Matrix woken (battery saver override: on)")

        elif cmd.startswith("auto_battery_saver:"):
            enabled = cmd.split(":", 1)[1] == "1"
            cfg.auto_off_battery_saver = enabled
            cfg.save()
            if not enabled and state["batt_saver_sleeping"]:
                # Re-enable display when feature is turned off
                state["batt_saver_sleeping"] = False
                state["force_on"] = False
                driver.sleep(False)
            log.info("Auto-off on battery saver: %s", "on" if enabled else "off")

        elif cmd == "reload_config":
            cfg.load()
            renderer.mode = cfg.mode
            renderer.brightness = cfg.brightness
            renderer.bar_slots = list(cfg.bar_slots)

        else:
            log.warning("Unknown command: %s", cmd)
