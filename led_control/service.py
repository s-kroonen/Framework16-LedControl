"""
Background service loop.

Runs in a dedicated daemon thread.  On each tick it:
  1. Collects system stats
  2. Renders a frame for the current display mode
  3. Pushes the frame to the LED matrix

Communication with the tray icon happens via a thread-safe command queue.

Supported commands (strings placed in the queue by tray.py):
  "quit"            — stop the loop and exit
  "mode:bars"       — switch display mode
  "mode:cpu_cores"
  "mode:clock"
  "mode:breathe"
  "brightness:180"  — set brightness (0–255)
  "sleep"           — put matrix to sleep
  "wake"            — wake matrix
  "reload_config"   — re-read config from disk
"""

from __future__ import annotations

import logging
import queue
import threading
import time

from .config import Config
from .led_driver import LedDriver
from .renderer import Renderer
from .stats import StatsCollector

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
        cfg = self._config
        driver = LedDriver(port=cfg.serial_port)
        driver.connect()
        driver.wake = lambda: driver.sleep(False)

        stats_collector = StatsCollector()
        renderer = Renderer(mode=cfg.mode, brightness=cfg.brightness)

        # Wake the matrix
        driver.sleep(False)

        last_tick = 0.0

        while not self._stop_event.is_set():
            # --- Process pending commands ---
            try:
                while True:
                    cmd = self._cmd_q.get_nowait()
                    self._handle_command(cmd, driver, renderer)
            except queue.Empty:
                pass

            if self._stop_event.is_set():
                break

            # --- Tick ---
            now = time.monotonic()
            if now - last_tick >= cfg.tick_interval:
                last_tick = now
                try:
                    stats = stats_collector.collect()
                    frame = renderer.render(stats)
                    driver.draw_frame(frame)
                except Exception as exc:
                    log.error("Tick error: %s", exc)

            time.sleep(0.05)  # yield; tight loop avoided

        driver.clear()
        driver.sleep(True)
        driver.disconnect()
        log.info("Service loop exited cleanly")

    def _handle_command(
        self,
        cmd: str,
        driver: LedDriver,
        renderer: Renderer,
    ) -> None:
        log.debug("Command: %s", cmd)
        if cmd == "quit":
            self._stop_event.set()
        elif cmd.startswith("mode:"):
            new_mode = cmd.split(":", 1)[1]
            renderer.mode = new_mode
            self._config.mode = new_mode
            self._config.save()
        elif cmd.startswith("brightness:"):
            try:
                value = int(cmd.split(":", 1)[1])
                renderer.brightness = value
                driver.set_brightness(value)
                self._config.brightness = value
                self._config.save()
            except ValueError:
                log.warning("Invalid brightness value in command: %s", cmd)
        elif cmd == "sleep":
            driver.sleep(True)
        elif cmd == "wake":
            driver.sleep(False)
        elif cmd == "reload_config":
            self._config.load()
            renderer.mode = self._config.mode
            renderer.brightness = self._config.brightness
        else:
            log.warning("Unknown command: %s", cmd)
