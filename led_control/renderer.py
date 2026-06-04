"""
Renderer: converts SystemStats → a 9×34 LED frame buffer.

Frame format: frame[col][row]  (col 0–8, row 0–33)
Each value is an integer 0–255 (LED brightness).
Row 0 = TOP of the matrix; row 33 = BOTTOM.

Display modes
─────────────
bars
    Variable-width vertical bar graphs.  The 9 columns are divided
    evenly among the active bar_slots list.  Fewer slots → wider bars
    that fill the full matrix.

    Slot distribution examples:
      1 stat  → 1 bar × 9 cols wide
      3 stats → 3 bars × 3 cols wide
      5 stats → bars: 2,2,2,2,1 cols  (extras assigned left-to-right)
      9 stats → 9 bars × 1 col wide

cpu_cores
    One bar per logical CPU core, merged into 9 columns if there are
    more than 9 cores.

clock
    HH stacked above MM using a 4×7 pixel font.
    Two digits fit exactly in 9 columns (4+1+4 with 1-col gap).
    Layout on the 34-row matrix:
      rows  4–10 : hours (HH)
      rows 13–14 : separator dots (center column)
      rows 17–23 : minutes (MM)

breathe
    Full-matrix sine-wave pulse for idle/standby.
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
# 4×7 pixel bitmap font
# Each digit is defined as 7 rows × 4 columns.
# Row 0 = top; col 0 = leftmost.
# Stored as a list of 7 integers; each integer is a 4-bit column mask
# where bit 3 = leftmost column, bit 0 = rightmost column.
# ---------------------------------------------------------------------------
#  bit positions:   3 2 1 0   (left → right)
#                   col: 0 1 2 3

def _glyph(rows: list[str]) -> list[int]:
    """Convert a list of 4-char strings ('#' / '.') to column bitmasks."""
    assert len(rows) == 7
    cols = []
    for c in range(4):
        mask = 0
        for r, row in enumerate(rows):
            if row[c] == "#":
                mask |= (1 << r)
        cols.append(mask)
    return cols


_FONT_4X7: dict[str, list[int]] = {
    "0": _glyph([
        ".##.",
        "#..#",
        "#..#",
        "#..#",
        "#..#",
        "#..#",
        ".##.",
    ]),
    "1": _glyph([
        ".##.",
        ".##.",
        ".##.",
        ".##.",
        ".##.",
        ".##.",
        ".##.",
    ]),
    "2": _glyph([
        ".##.",
        "#..#",
        "...#",
        "..#.",
        ".#..",
        "#...",
        "####",
    ]),
    "3": _glyph([
        "####",
        "...#",
        "...#",
        ".###",
        "...#",
        "...#",
        "####",
    ]),
    "4": _glyph([
        "#..#",
        "#..#",
        "#..#",
        "####",
        "...#",
        "...#",
        "...#",
    ]),
    "5": _glyph([
        "####",
        "#...",
        "#...",
        "####",
        "...#",
        "...#",
        "####",
    ]),
    "6": _glyph([
        ".###",
        "#...",
        "#...",
        "####",
        "#..#",
        "#..#",
        ".##.",
    ]),
    "7": _glyph([
        "####",
        "...#",
        "..#.",
        ".#..",
        ".#..",
        ".#..",
        ".#..",
    ]),
    "8": _glyph([
        ".##.",
        "#..#",
        "#..#",
        ".##.",
        "#..#",
        "#..#",
        ".##.",
    ]),
    "9": _glyph([
        ".##.",
        "#..#",
        "#..#",
        ".###",
        "...#",
        "...#",
        ".##.",
    ]),
}

# Temperature ceiling (°C → 100%)
_TEMP_MAX = 100.0
# Network throughput ceiling (Mbit/s → 100%)
_NET_MAX_MBPS = 100.0


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _empty_frame() -> Frame:
    return [[0] * ROWS for _ in range(COLS)]


def _bar_column(value_pct: float, brightness: int = 200) -> list[int]:
    """
    Single 34-row vertical bar for a 0–100 % value.
    LEDs light from the BOTTOM up (row 33 = bottom).
    """
    lit = round(max(0.0, min(100.0, value_pct)) / 100.0 * ROWS)
    col: list[int] = [0] * ROWS
    for row in range(ROWS - lit, ROWS):
        col[row] = brightness
    return col


def _temp_bar_column(temp_c: float) -> list[int]:
    """Bar whose brightness scales with temperature (cool=dim, hot=bright)."""
    pct = min(100.0, temp_c / _TEMP_MAX * 100.0)
    brightness = max(30, int(pct / 100.0 * 255))
    return _bar_column(pct, brightness)


def _net_bar_column(mbps: float) -> list[int]:
    """Network throughput bar; saturates at _NET_MAX_MBPS."""
    pct = min(100.0, mbps / _NET_MAX_MBPS * 100.0)
    return _bar_column(pct, 180)


def _stat_to_column(key: str, stats: SystemStats) -> list[int]:
    """Render a single stat key to a 34-row brightness column."""
    if key == "cpu":
        return _bar_column(stats.cpu_percent)
    if key == "ram":
        return _bar_column(stats.ram_percent)
    if key == "gpu":
        return _bar_column(stats.gpu_percent)
    if key == "gpu_vram":
        return _bar_column(stats.gpu_vram_percent)
    if key == "disk":
        return _bar_column(stats.disk_percent)
    if key == "net_rx":
        return _net_bar_column(stats.net_recv_mbps)
    if key == "net_tx":
        return _net_bar_column(stats.net_sent_mbps)
    if key == "cpu_temp":
        return _temp_bar_column(stats.cpu_temp_c)
    if key == "gpu_temp":
        return _temp_bar_column(stats.gpu_temp_c)
    return [0] * ROWS


# ---------------------------------------------------------------------------
# Column distribution
# ---------------------------------------------------------------------------

def distribute_columns(n_slots: int, total: int = COLS) -> list[int]:
    """
    Split `total` columns among `n_slots` as evenly as possible.
    Extra columns are given to the leftmost slots.

    Examples (total=9):
      n=1 → [9]
      n=2 → [5, 4]
      n=3 → [3, 3, 3]
      n=4 → [3, 2, 2, 2]
      n=5 → [2, 2, 2, 2, 1]
      n=9 → [1, 1, 1, 1, 1, 1, 1, 1, 1]
    """
    if n_slots <= 0:
        return []
    n_slots = min(n_slots, total)
    base, extra = divmod(total, n_slots)
    return [base + (1 if i < extra else 0) for i in range(n_slots)]


# ---------------------------------------------------------------------------
# Render functions
# ---------------------------------------------------------------------------

def render_bars(stats: SystemStats, slots: list[str]) -> Frame:
    """
    Render vertical bar graphs for the given ordered slot list.
    The 9 matrix columns are divided evenly; fewer slots → wider bars.
    """
    if not slots:
        return _empty_frame()

    frame = _empty_frame()
    widths = distribute_columns(len(slots))
    col_offset = 0

    for slot_idx, key in enumerate(slots):
        bar = _stat_to_column(key, stats)
        width = widths[slot_idx]
        for w in range(width):
            if col_offset + w < COLS:
                frame[col_offset + w] = list(bar)
        col_offset += width

    return frame


def render_cpu_cores(stats: SystemStats) -> Frame:
    """
    One bar per logical CPU core, merged into 9 buckets when there are
    more than 9 cores.
    """
    cores = stats.cpu_cores or [stats.cpu_percent]
    frame = _empty_frame()
    if len(cores) <= COLS:
        for i, pct in enumerate(cores[:COLS]):
            frame[i] = _bar_column(pct)
    else:
        bucket = len(cores) / COLS
        for col in range(COLS):
            start = int(col * bucket)
            end = int((col + 1) * bucket)
            group = cores[start:end] or [0.0]
            frame[col] = _bar_column(sum(group) / len(group))
    return frame


def render_clock(brightness: int = 200) -> Frame:
    """
    Render current time as HH stacked above MM using the 4×7 font.

    Layout on the 34-row matrix:
      rows  4–10 : hours tens + units (4+1+4 = 9 cols wide)
      rows 14–15 : separator dots at center column (col 4)
      rows 17–23 : minutes tens + units
    """
    import datetime
    now = datetime.datetime.now()
    hh = f"{now.hour:02d}"
    mm = f"{now.minute:02d}"

    frame = _empty_frame()

    def _paint_two_digits(chars: str, row_offset: int) -> None:
        """Paint a two-character string starting at row_offset."""
        # Layout: digit0 → cols 0-3, gap → col 4, digit1 → cols 5-8
        positions = [(0, chars[0]), (5, chars[1])]
        for col_start, ch in positions:
            glyph = _FONT_4X7.get(ch, _FONT_4X7["0"])
            for gc, col_bits in enumerate(glyph):       # gc = 0..3
                col = col_start + gc
                for r in range(7):
                    if col_bits & (1 << r):
                        row = row_offset + r
                        if 0 <= col < COLS and 0 <= row < ROWS:
                            frame[col][row] = brightness

    # Hours
    _paint_two_digits(hh, row_offset=4)

    # Separator dots (blink every other second)
    if now.second % 2 == 0:
        for dot_row in (14, 15):
            if 0 <= dot_row < ROWS:
                frame[4][dot_row] = brightness

    # Minutes
    _paint_two_digits(mm, row_offset=17)

    return frame


def render_breathe(brightness_scale: float = 1.0) -> Frame:
    """Full-matrix gentle sine-wave breathing effect."""
    t = time.monotonic()
    pulse = (math.sin(2 * math.pi * t / 4.0) + 1) / 2   # 0→1, 4 s cycle
    b = int(pulse * 60 * brightness_scale)               # max ~60 — subtle
    col = [b] * ROWS
    return [list(col) for _ in range(COLS)]


# ---------------------------------------------------------------------------
# Main Renderer class
# ---------------------------------------------------------------------------

MODES = ("bars", "cpu_cores", "clock", "breathe")


class Renderer:
    """Converts a SystemStats snapshot into a LED frame for the current mode."""

    def __init__(
        self,
        mode: str = "bars",
        brightness: int = 180,
        bar_slots: list[str] | None = None,
    ):
        if mode not in MODES:
            raise ValueError(f"Unknown mode '{mode}'. Choose from {MODES}")
        self.mode = mode
        self.brightness = brightness
        from .config import ALL_STAT_KEYS
        self.bar_slots: list[str] = bar_slots if bar_slots is not None else list(ALL_STAT_KEYS)

    def render(self, stats: SystemStats) -> Frame:
        if self.mode == "bars":
            frame = render_bars(stats, self.bar_slots)
        elif self.mode == "cpu_cores":
            frame = render_cpu_cores(stats)
        elif self.mode == "clock":
            frame = render_clock(self.brightness)
        elif self.mode == "breathe":
            frame = render_breathe()
        else:
            frame = _empty_frame()

        # Apply global brightness scaling (not applied to clock — it uses
        # brightness directly; apply to everything else)
        if self.mode not in ("clock", "breathe"):
            scale = self.brightness / 255.0
            frame = [
                [int(v * scale) for v in col]
                for col in frame
            ]

        return frame
