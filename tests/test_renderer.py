"""Tests for the renderer module — no hardware required."""

import pytest
from led_control.renderer import (
    COLS, ROWS,
    distribute_columns,
    render_bars, render_cpu_cores, render_breathe, render_clock,
    Renderer, MODES,
)
from led_control.stats import SystemStats
from led_control.config import ALL_STAT_KEYS


def _dummy_stats(**kwargs) -> SystemStats:
    defaults = dict(
        cpu_percent=50.0, cpu_cores=[50.0] * 4,
        ram_percent=40.0, ram_used_gb=6.4, ram_total_gb=16.0,
        disk_percent=60.0,
        gpu_percent=30.0, gpu_vram_percent=20.0, gpu_temp_c=55.0,
        cpu_temp_c=65.0,
        net_sent_mbps=1.0, net_recv_mbps=5.0,
    )
    defaults.update(kwargs)
    return SystemStats(**defaults)


def _validate_frame(frame):
    assert len(frame) == COLS, f"Expected {COLS} cols, got {len(frame)}"
    for col in frame:
        assert len(col) == ROWS, f"Expected {ROWS} rows, got {len(col)}"
        for v in col:
            assert 0 <= v <= 255, f"Brightness {v} out of range"


# ---------------------------------------------------------------------------
# distribute_columns
# ---------------------------------------------------------------------------

class TestDistributeColumns:
    def test_single(self):
        assert distribute_columns(1) == [9]

    def test_three(self):
        assert distribute_columns(3) == [3, 3, 3]

    def test_five(self):
        result = distribute_columns(5)
        assert sum(result) == 9
        assert len(result) == 5

    def test_nine(self):
        assert distribute_columns(9) == [1] * 9

    def test_sum_always_equals_total(self):
        for n in range(1, 10):
            assert sum(distribute_columns(n)) == COLS

    def test_all_widths_positive(self):
        for n in range(1, 10):
            assert all(w > 0 for w in distribute_columns(n))


# ---------------------------------------------------------------------------
# render_bars
# ---------------------------------------------------------------------------

class TestRenderBars:
    def test_shape(self):
        _validate_frame(render_bars(_dummy_stats(), ALL_STAT_KEYS))

    def test_single_slot_fills_all_cols(self):
        stats = _dummy_stats(cpu_percent=100)
        frame = render_bars(stats, ["cpu"])
        # All 9 columns should be lit (same bar, 9 wide)
        for col in frame:
            assert col[ROWS - 1] > 0, "Bottom row of full bar should be lit"

    def test_zero_stats_is_dark(self):
        stats = _dummy_stats(
            cpu_percent=0, ram_percent=0, gpu_percent=0, gpu_vram_percent=0,
            disk_percent=0, net_sent_mbps=0, net_recv_mbps=0,
            cpu_temp_c=0, gpu_temp_c=0,
        )
        frame = render_bars(stats, ALL_STAT_KEYS)
        assert all(v == 0 for col in frame for v in col)

    def test_full_stats_lights_bars(self):
        stats = _dummy_stats(cpu_percent=100, ram_percent=100)
        frame = render_bars(stats, ["cpu", "ram"])
        assert frame[0][ROWS - 1] > 0
        # With 2 slots, each bar is ~4-5 cols wide
        assert frame[4][ROWS - 1] > 0

    def test_bar_grows_from_bottom(self):
        stats = _dummy_stats(cpu_percent=50)
        frame = render_bars(stats, ["cpu"])
        # With 1 slot, all 9 cols are same value
        assert frame[0][ROWS - 1] > 0     # bottom lit
        assert frame[0][0] == 0           # top dark

    def test_empty_slots_returns_empty_frame(self):
        frame = render_bars(_dummy_stats(), [])
        _validate_frame(frame)
        assert all(v == 0 for col in frame for v in col)

    def test_three_slots_column_widths(self):
        stats = _dummy_stats(cpu_percent=100, ram_percent=100, gpu_percent=100)
        frame = render_bars(stats, ["cpu", "ram", "gpu"])
        # Each slot is 3 cols wide → all 9 cols lit at bottom
        for col in frame:
            assert col[ROWS - 1] > 0

    def test_width_fills_exactly_9_cols(self):
        """No matter how many slots, all 9 physical columns should be used."""
        stats = _dummy_stats(cpu_percent=100)
        for n in range(1, 10):
            slots = ALL_STAT_KEYS[:n]
            frame = render_bars(stats, slots)
            _validate_frame(frame)


# ---------------------------------------------------------------------------
# render_cpu_cores
# ---------------------------------------------------------------------------

class TestRenderCpuCores:
    def test_shape(self):
        _validate_frame(render_cpu_cores(_dummy_stats()))

    def test_many_cores_merged(self):
        stats = _dummy_stats(cpu_cores=[50.0] * 32)
        _validate_frame(render_cpu_cores(stats))

    def test_fewer_cores_than_cols(self):
        stats = _dummy_stats(cpu_cores=[100.0, 0.0])
        frame = render_cpu_cores(stats)
        assert frame[0][ROWS - 1] > 0
        assert all(v == 0 for v in frame[1])


# ---------------------------------------------------------------------------
# render_clock
# ---------------------------------------------------------------------------

class TestRenderClock:
    def test_shape(self):
        _validate_frame(render_clock())

    def test_some_pixels_lit(self):
        frame = render_clock(brightness=200)
        total = sum(v for col in frame for v in col)
        assert total > 0

    def test_hours_and_minutes_in_separate_regions(self):
        """Hours pixels should appear in rows 4-10, minutes in 17-23."""
        frame = render_clock(brightness=200)
        # Check hours region (rows 4-10) has some lit pixels
        hours_lit = sum(frame[c][r] for c in range(COLS) for r in range(4, 11))
        # Check minutes region (rows 17-23) has some lit pixels
        mins_lit = sum(frame[c][r] for c in range(COLS) for r in range(17, 24))
        assert hours_lit > 0, "Hours region should have lit pixels"
        assert mins_lit > 0, "Minutes region should have lit pixels"

    def test_no_pixels_outside_expected_rows(self):
        """Pixels should not appear in the very top or very bottom rows."""
        frame = render_clock(brightness=200)
        top_lit = sum(frame[c][0] for c in range(COLS))
        bottom_lit = sum(frame[c][ROWS - 1] for c in range(COLS))
        assert top_lit == 0,    "Row 0 should be dark"
        assert bottom_lit == 0, "Row 33 should be dark"


# ---------------------------------------------------------------------------
# render_breathe
# ---------------------------------------------------------------------------

class TestRenderBreathe:
    def test_shape(self):
        _validate_frame(render_breathe())

    def test_uniform(self):
        frame = render_breathe()
        for col in frame:
            assert col == frame[0]


# ---------------------------------------------------------------------------
# Renderer class
# ---------------------------------------------------------------------------

class TestRenderer:
    def test_all_modes_produce_valid_frames(self):
        stats = _dummy_stats()
        for mode in MODES:
            r = Renderer(mode=mode)
            _validate_frame(r.render(stats))

    def test_brightness_scaling(self):
        stats = _dummy_stats(cpu_percent=100)
        r_full = Renderer(mode="bars", brightness=255, bar_slots=["cpu"])
        r_half = Renderer(mode="bars", brightness=128, bar_slots=["cpu"])
        full = r_full.render(stats)
        half = r_half.render(stats)
        for c in range(COLS):
            for row in range(ROWS):
                assert half[c][row] <= full[c][row]

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            Renderer(mode="invalid_mode")

    def test_custom_bar_slots(self):
        stats = _dummy_stats(cpu_percent=100)
        r = Renderer(mode="bars", brightness=255, bar_slots=["cpu", "ram"])
        frame = r.render(stats)
        _validate_frame(frame)

    def test_bar_slots_synced_from_config(self):
        """Renderer.bar_slots default comes from ALL_STAT_KEYS."""
        r = Renderer(mode="bars")
        assert r.bar_slots == list(ALL_STAT_KEYS)
