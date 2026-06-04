"""
Renderer: converts SystemStats → a 9×34 LED frame buffer.

Frame format: frame[col][row]  (col 0–8, row 0–33)
Each value is an integer 0–255 (LED brightness).
Row 0 = TOP of the matrix; row 33 = BOTTOM.

Display modes
─────────────
bars
    Nine vertical bar graphs — one per physical matrix column.
    Each column is driven by the stat assigned to that slot in
    config.bar_slots.  Slots set to None display a dark column.

cpu_cores
    One bar per logical CPU core merged into 9 columns.

clock
    HH stacked above MM using a 4×7 pixel font.
    Two digits fill all 9 columns (4+1+4 with 1-col gap).
    Layout:
      rows  4–10 : hours  (HH)
      rows 14–15 : blinking separator dots at center column
      rows 17–23 : minutes (MM)

breathe
    Full-matrix sine-wave pulse for idle/standby.
"""

from __future__ import annotations

import math
import time
from typing import List, Optional

from .stats import SystemStats

COLS = 9
ROWS = 34

Frame = List[List[int]]  # frame[col][row]

# ---------------------------------------------------------------------------
# 4×7 pixel bitmap font
# Each glyph: list of 4 column bitmasks.
# Bit r of mask = whether row r (from top) is lit.
# ---------------------------------------------------------------------------

def _glyph(rows: list[str]) -> list[int]:
    """Convert 7 × 4-char strings ('#' / '.') to column bitmasks."""
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
    "0": _glyph([".##.", "#..#", "#..#", "#..#", "#..#", "#..#", ".##."]),
    "1": _glyph([".##.", ".##.", ".##.", ".##.", ".##.", ".##.", ".##."]),
    "2": _glyph([".##.", "#..#", "...#", "..#.", ".#..", "#...", "####"]),
    "3": _glyph(["####", "...#", "...#", ".###", "...#", "...#", "####"]),
    "4": _glyph(["#..#", "#..#", "#..#", "####", "...#", "...#", "...#"]),
    "5": _glyph(["####", "#...", "#...", "####", "...#", "...#", "####"]),
    "6": _glyph([".###", "#...", "#...", "####", "#..#", "#..#", ".##."]),
    "7": _glyph(["####", "...#", "..#.", ".#..", ".#..", ".#..", ".#.."]),
    "8": _glyph([".##.", "#..#", "#..#", ".##.", "#..#", "#..#", ".##."]),
    "9": _glyph([".##.", "#..#", "#..#", ".###", "...#", "...#", ".##."]),
}

# Ceiling values for rate-mapped metrics
_TEMP_MAX = 100.0        # °C
_NET_MAX_MBPS = 100.0    # Mbit/s network
_DISK_MAX_MBPS = 500.0   # MB/s disk throughput (covers SATA SSD; NVMe saturates sooner)


# ---------------------------------------------------------------------------
# Per-column rendering helpers
# ---------------------------------------------------------------------------

def _empty_frame() -> Frame:
    return [[0] * ROWS for _ in range(COLS)]


def _bar_column(value_pct: float, brightness: int = 200) -> list[int]:
    """Vertical bar filling from bottom; value_pct 0–100."""
    lit = round(max(0.0, min(100.0, value_pct)) / 100.0 * ROWS)
    col = [0] * ROWS
    for row in range(ROWS - lit, ROWS):
        col[row] = brightness
    return col


def _temp_bar_column(temp_c: float) -> list[int]:
    """Bar height and brightness both scale with temperature."""
    pct = min(100.0, temp_c / _TEMP_MAX * 100.0)
    brightness = max(30, int(pct / 100.0 * 255))
    return _bar_column(pct, brightness)


def _net_bar_column(mbps: float) -> list[int]:
    pct = min(100.0, mbps / _NET_MAX_MBPS * 100.0)
    return _bar_column(pct, 180)


def _disk_rate_bar_column(mbps: float) -> list[int]:
    """Disk read or write throughput bar (MB/s, ceiling _DISK_MAX_MBPS)."""
    pct = min(100.0, mbps / _DISK_MAX_MBPS * 100.0)
    return _bar_column(pct, 180)


def _stat_to_column(key: str, stats: SystemStats) -> list[int]:
    """Render a stat key to a 34-row brightness column."""
    dispatch = {
        "cpu":        lambda: _bar_column(stats.cpu_percent),
        "ram":        lambda: _bar_column(stats.ram_percent),
        "gpu":        lambda: _bar_column(stats.gpu_percent),
        "gpu_vram":   lambda: _bar_column(stats.gpu_vram_percent),
        "disk":       lambda: _bar_column(stats.disk_percent),      # I/O activity %
        "disk_read":  lambda: _disk_rate_bar_column(stats.disk_read_mbps),
        "disk_write": lambda: _disk_rate_bar_column(stats.disk_write_mbps),
        "net_rx":     lambda: _net_bar_column(stats.net_recv_mbps),
        "net_tx":     lambda: _net_bar_column(stats.net_sent_mbps),
        "cpu_temp":   lambda: _temp_bar_column(stats.cpu_temp_c),
        "gpu_temp":   lambda: _temp_bar_column(stats.gpu_temp_c),
    }
    fn = dispatch.get(key)
    return fn() if fn else [0] * ROWS


# ---------------------------------------------------------------------------
# Render functions
# ---------------------------------------------------------------------------

def render_bars(stats: SystemStats, slots: list[Optional[str]]) -> Frame:
    """
    Render 9 bar-graph columns from the slot assignment list.
    slots[i] is the stat key for column i, or None for a dark column.
    Exactly 9 slots are expected (matching COLS).
    """
    frame = _empty_frame()
    for col, key in enumerate(slots[:COLS]):
        if key is not None:
            frame[col] = _stat_to_column(key, stats)
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

    Column layout: digit₀ → cols 0-3, gap col 4, digit₁ → cols 5-8
    Row layout:
      rows  4–10 : hours
      rows 14–15 : separator dots (col 4, blink every second)
      rows 17–23 : minutes
    """
    import datetime
    now = datetime.datetime.now()
    frame = _empty_frame()

    def _paint(chars: str, row_offset: int) -> None:
        for char_idx, ch in enumerate(chars[:2]):
            col_start = 0 if char_idx == 0 else 5
            glyph = _FONT_4X7.get(ch, _FONT_4X7["0"])
            for gc, col_bits in enumerate(glyph):
                col = col_start + gc
                for r in range(7):
                    if col_bits & (1 << r):
                        row = row_offset + r
                        if 0 <= col < COLS and 0 <= row < ROWS:
                            frame[col][row] = brightness

    _paint(f"{now.hour:02d}", row_offset=4)

    if now.second % 2 == 0:
        for dot_row in (14, 15):
            frame[4][dot_row] = brightness

    _paint(f"{now.minute:02d}", row_offset=17)

    return frame


def render_breathe(brightness_scale: float = 1.0) -> Frame:
    """Full-matrix gentle sine-wave breathing effect."""
    t = time.monotonic()
    pulse = (math.sin(2 * math.pi * t / 4.0) + 1) / 2
    b = int(pulse * 60 * brightness_scale)
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
        bar_slots: list[Optional[str]] | None = None,
    ):
        if mode not in MODES:
            raise ValueError(f"Unknown mode '{mode}'. Choose from {MODES}")
        self.mode = mode
        self.brightness = brightness
        from .config import ALL_STAT_KEYS, NUM_SLOTS
        if bar_slots is not None:
            self.bar_slots: list[Optional[str]] = bar_slots
        else:
            # Default: all stats in canonical order
            self.bar_slots = list(ALL_STAT_KEYS)

    def render(self, stats: SystemStats) -> Frame:
        if self.mode == "bars":
            frame = render_bars(stats, self.bar_slots)
        elif self.mode == "cpu_cores":
            frame = render_cpu_cores(stats)
        elif self.mode == "clock":
            # Clock uses brightness directly; skip scaling below
            return render_clock(self.brightness)
        elif self.mode == "breathe":
            return render_breathe()
        else:
            frame = _empty_frame()

        # Apply global brightness scaling
        scale = self.brightness / 255.0
        return [[int(v * scale) for v in col] for col in frame]
