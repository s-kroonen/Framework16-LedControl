"""
Low-level driver for the Framework 16 LED Matrix Input Module.

Protocol: USB CDC-ACM serial @ 115200 baud
  Packet format:  [0x32, 0xAC, <CMD>, <params...>]

Matrix dimensions: 9 columns × 34 rows (9×34 = 306 LEDs)
Each LED brightness: 0–255

Key commands
  0x00  Brightness   params: [value 0-255]
  0x03  Sleep        params: [0=wake, 1=sleep]
  0x04  Animate      params: [0=stop, 1=start]
  0x06  DrawBW       params: 39 bytes (bit-packed black/white image)
  0x07  StageCol     params: [col_index 0-8] + 34 brightness bytes
  0x08  FlushCols    params: none  (renders all staged columns)
  0x20  Version      params: none  → returns 3 bytes

Auto-discovery scans all COM ports for a device with VID=0x32AC / PID=0x0020.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

import serial
import serial.tools.list_ports

log = logging.getLogger(__name__)

# Framework 16 LED Matrix USB identifiers
_VID = 0x32AC
_PID = 0x0020

# Protocol constants
_MAGIC = bytes([0x32, 0xAC])

# Command IDs
CMD_BRIGHTNESS = 0x00
CMD_SLEEP      = 0x03
CMD_ANIMATE    = 0x04
CMD_DRAW_BW    = 0x06
CMD_STAGE_COL  = 0x07
CMD_FLUSH_COLS = 0x08
CMD_VERSION    = 0x20

# Matrix geometry
COLS = 9
ROWS = 34


def find_port() -> Optional[str]:
    """Return the first COM port matching the LED Matrix VID/PID, or None."""
    for port in serial.tools.list_ports.comports():
        if port.vid == _VID and port.pid == _PID:
            log.debug("Found LED matrix on %s (%s)", port.device, port.description)
            return port.device
    return None


class LedDriver:
    """Thread-safe driver for the Framework 16 LED Matrix."""

    def __init__(self, port: Optional[str] = None, baud: int = 115200):
        self._port_name = port or find_port()
        self._baud = baud
        self._serial: Optional[serial.Serial] = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Open the serial port.  Returns True on success."""
        if self._port_name is None:
            log.warning("LED matrix port not found; running in dry-run mode")
            return False
        try:
            self._serial = serial.Serial(
                self._port_name,
                baudrate=self._baud,
                timeout=1,
            )
            log.info("Connected to LED matrix on %s", self._port_name)
            # Normalise hardware brightness to maximum so that all brightness
            # control goes through software pixel scaling only (no double-multiply).
            self._send(CMD_BRIGHTNESS, bytes([255]))
            return True
        except serial.SerialException as exc:
            log.error("Cannot open %s: %s", self._port_name, exc)
            self._serial = None
            return False

    def disconnect(self) -> None:
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._serial = None

    @property
    def connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def reconnect(self) -> bool:
        """Attempt to rediscover port and reconnect."""
        self.disconnect()
        self._port_name = find_port()
        return self.connect()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _send(self, cmd: int, params: bytes = b"") -> None:
        """Send one packet to the device."""
        packet = _MAGIC + bytes([cmd]) + params
        if self._serial is None or not self._serial.is_open:
            log.debug("DRY-RUN send: %s", packet.hex())
            return
        try:
            self._serial.write(packet)
        except serial.SerialException as exc:
            log.error("Write error: %s – attempting reconnect", exc)
            self.reconnect()

    # ------------------------------------------------------------------
    # Public commands
    # ------------------------------------------------------------------

    def set_brightness(self, value: int) -> None:
        """Set global brightness (0–255)."""
        value = max(0, min(255, value))
        self._send(CMD_BRIGHTNESS, bytes([value]))

    def sleep(self, asleep: bool = True) -> None:
        """Put the matrix to sleep (True) or wake it (False)."""
        self._send(CMD_SLEEP, bytes([1 if asleep else 0]))

    def set_animate(self, enabled: bool) -> None:
        """Enable (True) or disable (False) the built-in scrolling animation."""
        self._send(CMD_ANIMATE, bytes([1 if enabled else 0]))

    def stage_column(self, col: int, brightnesses: List[int]) -> None:
        """
        Stage one column of grayscale brightness values.
        col:          0–8 (left to right)
        brightnesses: list of 34 ints, each 0–255 (top to bottom)
        """
        if len(brightnesses) != ROWS:
            raise ValueError(f"Expected {ROWS} brightness values, got {len(brightnesses)}")
        col = max(0, min(COLS - 1, col))
        params = bytes([col]) + bytes(max(0, min(255, b)) for b in brightnesses)
        self._send(CMD_STAGE_COL, params)

    def flush(self) -> None:
        """Commit all staged columns to the display."""
        self._send(CMD_FLUSH_COLS)

    def draw_frame(self, frame: List[List[int]]) -> None:
        """
        Push a full 9×34 grayscale frame to the matrix.
        frame[col][row]  – each value 0–255.
        Stages all 9 columns then flushes.
        """
        if len(frame) != COLS:
            raise ValueError(f"Frame must have {COLS} columns, got {len(frame)}")
        for col, column in enumerate(frame):
            self.stage_column(col, column)
        self.flush()

    def clear(self) -> None:
        """Blank the entire matrix."""
        empty_col = [0] * ROWS
        self.draw_frame([empty_col] * COLS)

    def get_version(self) -> Optional[bytes]:
        """Request firmware version bytes (3 bytes response)."""
        self._send(CMD_VERSION)
        if self._serial and self._serial.is_open:
            time.sleep(0.05)
            try:
                return self._serial.read(3)
            except serial.SerialException:
                pass
        return None
