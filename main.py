"""
Framework 16 LED Matrix Control — entry point.

Starts the background service loop on a daemon thread, then hands
control to the system-tray icon on the main thread (required by
pystray / Windows message pump).

Usage:
    python main.py [--no-tray] [--mode bars|cpu_cores|clock|breathe]
                   [--brightness 0-255] [--port COM5] [-v]
"""

from __future__ import annotations

import argparse
import logging
import queue
import sys


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Framework 16 LED Matrix background service")
    p.add_argument("--no-tray",    action="store_true", help="Run without system tray icon")
    p.add_argument("--mode",       default=None,
                   help="Display mode: bars|cpu_cores|clock|breathe")
    p.add_argument("--brightness", type=int, default=None, help="Brightness 0-255")
    p.add_argument("--port",       default=None,
                   help="Serial port, e.g. COM5 (default: auto-detect)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    _setup_logging(args.verbose)

    log = logging.getLogger(__name__)
    log.info("Framework 16 LED Matrix Control starting")

    from led_control.config import Config
    from led_control.service import ServiceLoop
    from led_control.tray import TrayIcon

    # Load config, apply CLI overrides
    config = Config()
    if args.mode:
        config.mode = args.mode
    if args.brightness is not None:
        config.brightness = args.brightness
    if args.port:
        config.serial_port = args.port

    # Shared command queue: tray → service
    cmd_q: queue.Queue[str] = queue.Queue()

    # Start background service loop
    service = ServiceLoop(config=config, command_queue=cmd_q)
    service.start()

    def on_quit() -> None:
        log.info("Quit requested")
        service.stop()
        sys.exit(0)

    if args.no_tray:
        log.info("Running without tray icon. Press Ctrl+C to stop.")
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            on_quit()
    else:
        tray = TrayIcon(cmd_queue=cmd_q, on_quit=on_quit)
        tray.run()  # blocks on main thread


if __name__ == "__main__":
    main()
