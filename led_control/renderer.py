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

# Fixed ceiling for temperature bars (°C → 100 %)
_TEMP_MAX = 100.0

# Alert thresholds — bar shows a stripe pattern when the raw value meets/exceeds this.
# For "battery" the alert is triggered when BELOW the threshold (low battery).
_ALERT_THRESHOLDS: dict[str, float] = {
    "cpu":        90.0,   # %
    "ram":        90.0,   # %
    "gpu":        90.0,   # %
    "gpu_vram":   90.0,   # %
    "disk":       90.0,   # I/O busy %
    "disk_read":  200.0,  # MB/s
    "disk_write": 200.0,  # MB/s
    "net_rx":     100.0,  # Mbit/s
    "net_tx":     100.0,  # Mbit/s
    "cpu_temp":   85.0,   # °C
    "gpu_temp":   85.0,   # °C
    "temp_ddr":   70.0,   # °C
    "temp_local": 60.0,   # °C
    "battery":    20.0,   # % (alert when BELOW this — low battery)
}


# ---------------------------------------------------------------------------
# Per-column rendering helpers
# ---------------------------------------------------------------------------

def _empty_frame() -> Frame:
    return [[0] * ROWS for _ in range(COLS)]


def _bar_column(value_pct: float, brightness: int = 200, alert: bool = False) -> list[int]:
    """Vertical bar filling from bottom; value_pct 0–100.
    When alert=True, every other lit row is turned off (stripe from bottom up)."""
    lit = round(max(0.0, min(100.0, value_pct)) / 100.0 * ROWS)
    col = [0] * ROWS
    # Enumerate from bottom so i=0 (bottom) is always ON
    for i, row in enumerate(range(ROWS - 1, ROWS - lit - 1, -1)):
        col[row] = brightness if (not alert or i % 2 == 0) else 0
    return col


def _temp_bar_column(temp_c: float, alert: bool = False) -> list[int]:
    """Bar height and brightness both scale with temperature."""
    pct = min(100.0, temp_c / _TEMP_MAX * 100.0)
    brightness = max(30, int(pct / 100.0 * 255))
    return _bar_column(pct, brightness, alert=alert)


# ---------------------------------------------------------------------------
# Rolling maximum tracker — used for dynamic scaling of rate metrics
# ---------------------------------------------------------------------------

class RollingMax:
    """
    Tracks a smoothly-decaying peak value.

    When a new observation exceeds the current max it jumps immediately.
    Otherwise the max decays toward `floor` by `decay` fraction per second
    (default 1.5 % / s → half-life ≈ 45 s).  This means a burst of disk or
    network activity raises the bar ceiling quickly, then the ceiling slowly
    retreats to the observed minimum once traffic dies down — bars always
    use most of their vertical range.
    """

    def __init__(self, floor: float = 1.0, decay: float = 0.015) -> None:
        self._value  = floor
        self._floor  = floor
        self._decay  = decay          # fraction lost per second
        self._last   = time.monotonic()

    def update(self, value: float) -> float:
        now = time.monotonic()
        dt  = min(now - self._last, 10.0)   # cap gap (e.g. after sleep)
        self._last = now
        self._value = max(self._floor, self._value * ((1.0 - self._decay) ** dt))
        if value > self._value:
            self._value = value
        return self._value

    def ceiling(self, headroom: float = 1.1) -> float:
        """Current peak × headroom — use as the bar's 100 % point."""
        return max(self._floor, self._value * headroom)


def _rate_bar_column(value: float, ceiling: float, alert: bool = False) -> list[int]:
    """Bar for a rate metric scaled against a dynamic ceiling."""
    pct = min(100.0, value / max(ceiling, 1e-9) * 100.0)
    return _bar_column(pct, 180, alert=alert)


def _stat_to_column(
    key: str,
    stats: SystemStats,
    rolls: dict[str, RollingMax] | None = None,
    alert_mode: str = "stripe",
    blink_on: bool = True,
) -> list[int]:
    """
    Render a stat key to a 34-row brightness column.

    Alert mode behaviour when threshold is exceeded:
      "none"         — normal solid bar
      "stripe"       — alternating rows off within lit region
      "stripe_blink" — alternates between striped bar and solid bar each frame
      "bar_blink"    — alternates between solid bar and dark (all off) each frame
    """
    def _is_alert(raw: float) -> bool:
        th = _ALERT_THRESHOLDS.get(key)
        if th is None:
            return False
        return raw < th if key == "battery" else raw >= th

    def _pct(value: float) -> list[int]:
        if not _is_alert(value) or alert_mode == "none":
            return _bar_column(value)
        if alert_mode == "stripe":
            return _bar_column(value, alert=True)
        if alert_mode == "stripe_blink":
            # blink between striped and solid
            return _bar_column(value, alert=True) if blink_on else _bar_column(value)
        if alert_mode == "bar_blink":
            # blink between solid and dark
            return _bar_column(value) if blink_on else [0] * ROWS
        return _bar_column(value)

    def _rate(value: float, roll_key: str) -> list[int]:
        ceil = rolls[roll_key].ceiling() if (rolls and roll_key in rolls) else max(value, 1.0)
        if not _is_alert(value) or alert_mode == "none":
            return _rate_bar_column(value, ceil)
        if alert_mode == "stripe":
            return _rate_bar_column(value, ceil, alert=True)
        if alert_mode == "stripe_blink":
            return _rate_bar_column(value, ceil, alert=True) if blink_on else _rate_bar_column(value, ceil)
        if alert_mode == "bar_blink":
            return _rate_bar_column(value, ceil) if blink_on else [0] * ROWS
        return _rate_bar_column(value, ceil)

    def _temp(temp_c: float) -> list[int]:
        if not _is_alert(temp_c) or alert_mode == "none":
            return _temp_bar_column(temp_c)
        if alert_mode == "stripe":
            return _temp_bar_column(temp_c, alert=True)
        if alert_mode == "stripe_blink":
            return _temp_bar_column(temp_c, alert=True) if blink_on else _temp_bar_column(temp_c)
        if alert_mode == "bar_blink":
            return _temp_bar_column(temp_c) if blink_on else [0] * ROWS
        return _temp_bar_column(temp_c)

    if key == "cpu":        return _pct(stats.cpu_percent)
    if key == "ram":        return _pct(stats.ram_percent)
    if key == "gpu":        return _pct(stats.gpu_percent)
    if key == "gpu_vram":   return _pct(stats.gpu_vram_percent)
    if key == "disk":       return _pct(stats.disk_percent)
    if key == "disk_read":  return _rate(stats.disk_read_mbps,  "disk_read")
    if key == "disk_write": return _rate(stats.disk_write_mbps, "disk_write")
    if key == "net_rx":     return _rate(stats.net_recv_mbps,   "net_rx")
    if key == "net_tx":     return _rate(stats.net_sent_mbps,   "net_tx")
    if key == "battery":    return _pct(stats.battery_percent)
    if key == "cpu_temp":   return _temp(stats.cpu_temp_c)
    if key == "temp_ddr":   return _temp(stats.temp_ddr_c)
    if key == "temp_local": return _temp(stats.temp_local_c)
    if key == "gpu_temp":   return _temp(stats.gpu_temp_c)
    return [0] * ROWS


# ---------------------------------------------------------------------------
# Render functions
# ---------------------------------------------------------------------------

def render_bars(
    stats: SystemStats,
    slots: list[Optional[str]],
    rolls: dict[str, RollingMax] | None = None,
    alert_mode: str = "stripe",
    blink_on: bool = True,
) -> Frame:
    """
    Render 9 bar-graph columns from the slot assignment list.
    slots[i] is the stat key for column i, or None for a dark column.
    Exactly 9 slots are expected (matching COLS).
    """
    frame = _empty_frame()
    for col, key in enumerate(slots[:COLS]):
        if key is not None:
            frame[col] = _stat_to_column(key, stats, rolls, alert_mode=alert_mode, blink_on=blink_on)
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
        alert_mode: str = "stripe",
    ):
        if mode not in MODES:
            raise ValueError(f"Unknown mode '{mode}'. Choose from {MODES}")
        self.mode = mode
        self.brightness = brightness
        self.alert_mode = alert_mode
        from .config import ALL_STAT_KEYS
        self.bar_slots: list[Optional[str]] = (
            bar_slots if bar_slots is not None else list(ALL_STAT_KEYS)
        )
        # One rolling-max tracker per rate-based metric.
        # Each updates on every tick so the ceiling adapts to recent peaks.
        self._rolls: dict[str, RollingMax] = {
            "net_rx":     RollingMax(floor=0.5),   # Mbit/s
            "net_tx":     RollingMax(floor=0.5),
            "disk_read":  RollingMax(floor=0.5),   # MB/s
            "disk_write": RollingMax(floor=0.5),
        }
        self._render_count = 0   # flipped each call; drives blink toggle

    def _update_rolls(self, stats: SystemStats) -> None:
        self._rolls["net_rx"].update(stats.net_recv_mbps)
        self._rolls["net_tx"].update(stats.net_sent_mbps)
        self._rolls["disk_read"].update(stats.disk_read_mbps)
        self._rolls["disk_write"].update(stats.disk_write_mbps)

    def render(self, stats: SystemStats) -> Frame:
        self._update_rolls(stats)
        self._render_count += 1
        blink_on = self._render_count % 2 == 0   # alternates every frame

        if self.mode == "bars":
            frame = render_bars(stats, self.bar_slots, self._rolls,
                                alert_mode=self.alert_mode, blink_on=blink_on)
        elif self.mode == "cpu_cores":
            frame = render_cpu_cores(stats)
        elif self.mode == "clock":
            return render_clock(self.brightness)
        elif self.mode == "breathe":
            return render_breathe()
        else:
            frame = _empty_frame()

        scale = self.brightness / 255.0
        return [[int(v * scale) for v in col] for col in frame]
