"""
Renderer: converts SystemStats → a 9×34 LED frame buffer.

Frame format: frame[col][row]  (col 0-8, row 0-33)
Each value is an integer 0–255 (LED brightness).
Row 0 is the TOP of the matrix; row 33 is the BOTTOM.

Display modes
─────────────
  bars        Nine vertical bar-graph columns representing metrics:
              0:CPU  1:RAM  2:GPU  3:GPU-VRAM  4:Disk
              5:Net-RX  6:Net-TX  7:CPU-temp  8:GPU-temp

  cpu_cores   One bar per logical CPU core (up to 9, merged if more).

  clock       Current time as HH:MM in a 3×5 pixel font (two digits
              per half = 6px + separator = 9px wide total).

  breathe     Gentle sine-wave pulsing pattern to indicate idle/standby.
"""

from __future__ import annotations

import math
import time
from typing import List

from .stats import SystemStats

COLS = 9
ROWS = 34

Frame = List[List[int]]  # frame[col][row]


# ---------------------------------------------------------------------------
# Tiny 3×5 pixel font for digits 0–9 and colon ":"
# Each glyph is 3 columns × 5 rows stored as a list of 3 column bitmasks
# (bit 0 = top row).
# ---------------------------------------------------------------------------
_FONT_3X5: dict[str, List[int]] = {
    "0": [0b11111, 0b10001, 0b11111],
    "1": [0b00000, 0b11111, 0b00000],
    "2": [0b11101, 0b10101, 0b10111],
    "3": [0b10101, 0b10101, 0b11111],
    "4": [0b00111, 0b00100, 0b11111],
    "5": [0b10111, 0b10101, 0b11101],
    "6": [0b11111, 0b10101, 0b11101],
    "7": [0b00001, 0b00001, 0b11111],
    "8": [0b11111, 0b10101, 0b11111],
    "9": [0b10111, 0b10101, 0b11111],
    ":": [0b00000, 0b01010, 0b00000],
}

# Temperature thresholds for colour-mapped bar (mapped to brightness)
_TEMP_MAX = 100.0   # °C mapped to full brightness
_NET_MAX_MBPS = 100.0  # Mbit/s ceiling for bar scale


def _empty_frame() -> Frame:
    return [[0] * ROWS for _ in range(COLS)]


def _bar_column(value_pct: float, brightness: int = 200) -> List[int]:
    """
    Build a single 34-row vertical bar for a 0–100 % value.
    LEDs light from the BOTTOM up.
    """
    lit = round(value_pct / 100.0 * ROWS)
    col: List[int] = [0] * ROWS
    for row in range(ROWS):
        # row 0 = top, row ROWS-1 = bottom
        if row >= (ROWS - lit):
            col[row] = brightness
    return col


def _temp_bar_column(temp_c: float) -> List[int]:
    """
    Bar whose brightness encodes temperature (cool=dim, hot=bright).
    Also fills proportionally to temp / _TEMP_MAX.
    """
    pct = min(100.0, temp_c / _TEMP_MAX * 100.0)
    brightness = max(30, int(pct / 100.0 * 255))
    return _bar_column(pct, brightness)


def _net_bar_column(mbps: float) -> List[int]:
    """Network throughput bar; saturates at _NET_MAX_MBPS."""
    pct = min(100.0, mbps / _NET_MAX_MBPS * 100.0)
    return _bar_column(pct, 180)


# ---------------------------------------------------------------------------
# Render functions
# ---------------------------------------------------------------------------

def render_bars(stats: SystemStats) -> Frame:
    """
    Nine-column bar-graph view.
    Columns: CPU | RAM | GPU | GPU-VRAM | Disk | Net-RX | Net-TX | CPU-T | GPU-T
    """
    frame = _empty_frame()
    frame[0] = _bar_column(stats.cpu_percent)
    frame[1] = _bar_column(stats.ram_percent)
    frame[2] = _bar_column(stats.gpu_percent)
    frame[3] = _bar_column(stats.gpu_vram_percent)
    frame[4] = _bar_column(stats.disk_percent)
    frame[5] = _net_bar_column(stats.net_recv_mbps)
    frame[6] = _net_bar_column(stats.net_sent_mbps)
    frame[7] = _temp_bar_column(stats.cpu_temp_c)
    frame[8] = _temp_bar_column(stats.gpu_temp_c)
    return frame


def render_cpu_cores(stats: SystemStats) -> Frame:
    """
    One bar per logical CPU core.  If there are more than 9 cores the
    cores are averaged into groups of equal size to fit in 9 columns.
    """
    cores = stats.cpu_cores or [stats.cpu_percent]
    frame = _empty_frame()
    if len(cores) <= COLS:
        for i, pct in enumerate(cores):
            frame[i] = _bar_column(pct)
    else:
        # Average groups of cores into 9 buckets
        bucket = len(cores) / COLS
        for col in range(COLS):
            start = int(col * bucket)
            end = int((col + 1) * bucket)
            group = cores[start:end] or [0]
            frame[col] = _bar_column(sum(group) / len(group))
    return frame


def render_clock(brightness: int = 200) -> Frame:
    """
    Render HH:MM using a 3×5 font, centred vertically in the 34-row matrix.
    Layout: [H][H][ ][:][ ][M][M]  →  3+3+1+1+1+3+3 = 15px … padded to 9 cols?

    Because the matrix is only 9 cols wide we render a compact layout:
      col 0-2: tens-of-hours digit
      col 3:   colon
      col 4-6: tens-of-minutes digit
      (hours units and minutes units are shown as a 2nd pass scrolled into
       a sub-frame; we alternate two 3-character sub-frames every 3 s)

    Actually, since 9 cols and a 3×5 font with 1-col spacing:
      Two chars need 3+1+3 = 7 cols, leaving 1 col each side for padding.
    We therefore render two alternating pairs:
      Phase A:  HH  (hours tens + units)
      Phase B:  MM  (minutes tens + units)
    switching every 3 seconds.
    """
    import datetime
    now = datetime.datetime.now()
    phase_a = (int(time.time()) // 3) % 2 == 0
    if phase_a:
        chars = f"{now.hour:02d}"
    else:
        chars = f"{now.minute:02d}"

    frame = _empty_frame()
    # Place two digits in cols 1-3 and 5-7 (1-col gap)
    offsets = [1, 5]
    row_offset = (ROWS - 5) // 2  # vertical centre

    for char_idx, ch in enumerate(chars[:2]):
        glyph = _FONT_3X5.get(ch, _FONT_3X5["0"])
        for gc, col_bits in enumerate(glyph):
            col = offsets[char_idx] + gc
            if col >= COLS:
                break
            for r in range(5):
                if col_bits & (1 << r):
                    row = row_offset + r
                    if 0 <= row < ROWS:
                        frame[col][row] = brightness

    # Colon blink in col 4 (every other second)
    if now.second % 2 == 0:
        mid = ROWS // 2
        frame[4][mid - 1] = brightness
        frame[4][mid + 1] = brightness

    return frame


def render_breathe(brightness_scale: float = 1.0) -> Frame:
    """
    Full-matrix gentle sine-wave breathing effect.
    brightness_scale 0.0–1.0 controls peak brightness.
    """
    t = time.monotonic()
    # 4-second breathing cycle
    pulse = (math.sin(2 * math.pi * t / 4.0) + 1) / 2  # 0→1
    b = int(pulse * 60 * brightness_scale)  # max 60/255 — subtle glow
    col = [b] * ROWS
    return [list(col) for _ in range(COLS)]


# ---------------------------------------------------------------------------
# Main renderer class
# ---------------------------------------------------------------------------

MODES = ("bars", "cpu_cores", "clock", "breathe")


class Renderer:
    """Converts a SystemStats snapshot into a LED frame for the current mode."""

    def __init__(self, mode: str = "bars", brightness: int = 180):
        if mode not in MODES:
            raise ValueError(f"Unknown mode '{mode}'. Choose from {MODES}")
        self.mode = mode
        self.brightness = brightness

    def render(self, stats: SystemStats) -> Frame:
        if self.mode == "bars":
            frame = render_bars(stats)
        elif self.mode == "cpu_cores":
            frame = render_cpu_cores(stats)
        elif self.mode == "clock":
            frame = render_clock(self.brightness)
        elif self.mode == "breathe":
            frame = render_breathe()
        else:
            frame = _empty_frame()

        # Apply global brightness scaling
        scale = self.brightness / 255.0
        return [
            [int(v * scale) for v in col]
            for col in frame
        ]
