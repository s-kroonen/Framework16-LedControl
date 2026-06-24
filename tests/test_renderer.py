"""Tests for the renderer module — no hardware required."""

import pytest
from led_control.renderer import (
    COLS, ROWS,
    RollingMax,
    render_bars, render_cpu_cores, render_breathe, render_clock,
    Renderer, MODES,
)
from led_control.stats import SystemStats
from led_control.config import ALL_STAT_KEYS, NUM_SLOTS


def _dummy_stats(**kwargs) -> SystemStats:
    defaults = dict(
        cpu_percent=50.0, cpu_cores=[50.0] * 4,
        ram_percent=40.0, ram_used_gb=6.4, ram_total_gb=16.0,
        disk_percent=60.0,
        disk_read_mbps=50.0,
        disk_write_mbps=20.0,
        gpu_percent=30.0, gpu_vram_percent=20.0, gpu_temp_c=55.0,
        cpu_temp_c=65.0,
        temp_ddr_c=45.0,
        temp_local_c=38.0,
        net_sent_mbps=1.0, net_recv_mbps=5.0,
        battery_percent=80.0,
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
# RollingMax
# ---------------------------------------------------------------------------

class TestRollingMax:
    def test_peak_jumps_immediately(self):
        r = RollingMax(floor=1.0)
        r.update(10.0)
        assert r.ceiling() >= 10.0

    def test_floor_respected(self):
        r = RollingMax(floor=5.0)
        assert r.ceiling() >= 5.0
        r.update(0.0)
        assert r.ceiling() >= 5.0

    def test_decays_toward_floor(self):
        import time as _time
        r = RollingMax(floor=1.0, decay=0.5)   # very fast decay for test
        r.update(100.0)
        # Force a large dt by monkey-patching _last
        r._last = _time.monotonic() - 10.0     # pretend 10 s have passed
        val_after = r.update(0.0)
        assert val_after < 100.0, "max should have decayed"

    def test_headroom_applied(self):
        r = RollingMax(floor=1.0)
        r.update(50.0)
        assert r.ceiling(headroom=1.2) > 50.0

    def test_dynamic_scaling_in_renderer(self):
        """Net/disk bars use rolling max so at peak they fill to ~100%."""
        stats = _dummy_stats(net_recv_mbps=100.0)
        rend = Renderer(mode="bars", brightness=255,
                        bar_slots=["net_rx"] + [None] * 8)
        # Prime the roller with the same value several times
        for _ in range(5):
            rend.render(stats)
        frame = rend.render(stats)
        _validate_frame(frame)
        # With rolling max ≈ 100, bar should be near full
        assert frame[0][ROWS - 1] > 0


# Default slot list
_ALL_SLOTS = list(ALL_STAT_KEYS)
# One stat, rest empty
_SINGLE_CPU = ["cpu"] + [None] * 8


# ---------------------------------------------------------------------------
# render_bars — 9 fixed slots, 1:1 column mapping
# ---------------------------------------------------------------------------

class TestRenderBars:
    def test_shape_all_slots(self):
        _validate_frame(render_bars(_dummy_stats(), _ALL_SLOTS))

    def test_single_slot_only_lights_col_0(self):
        stats = _dummy_stats(cpu_percent=100)
        frame = render_bars(stats, _SINGLE_CPU)
        assert frame[0][ROWS - 1] > 0    # col 0 lit
        assert frame[1][ROWS - 1] == 0   # col 1 dark (None slot)

    def test_none_slot_is_dark(self):
        slots = [None] * NUM_SLOTS
        frame = render_bars(_dummy_stats(), slots)
        assert all(v == 0 for col in frame for v in col)

    def test_zero_stats_is_dark(self):
        stats = _dummy_stats(
            cpu_percent=0, ram_percent=0, gpu_percent=0, gpu_vram_percent=0,
            disk_percent=0, disk_read_mbps=0, disk_write_mbps=0,
            net_sent_mbps=0, net_recv_mbps=0,
            cpu_temp_c=0, gpu_temp_c=0, temp_ddr_c=0, temp_local_c=0,
            battery_percent=0,
        )
        frame = render_bars(stats, _ALL_SLOTS)
        assert all(v == 0 for col in frame for v in col)

    def test_full_stats_lights_cols(self):
        stats = _dummy_stats(cpu_percent=100, ram_percent=100)
        slots = ["cpu", "ram"] + [None] * 7
        frame = render_bars(stats, slots)
        assert frame[0][ROWS - 1] > 0
        assert frame[1][ROWS - 1] > 0

    def test_bar_grows_from_bottom(self):
        stats = _dummy_stats(cpu_percent=50)
        frame = render_bars(stats, _SINGLE_CPU)
        assert frame[0][ROWS - 1] > 0    # bottom lit
        assert frame[0][0] == 0           # top dark

    def test_each_slot_independently_rendered(self):
        """Each column reflects only its assigned stat."""
        stats = _dummy_stats(cpu_percent=100, ram_percent=0)
        slots = ["cpu", "ram"] + [None] * 7
        frame = render_bars(stats, slots)
        assert frame[0][ROWS - 1] > 0    # cpu full
        assert all(v == 0 for v in frame[1])   # ram zero

    def test_extra_slots_ignored(self):
        """Slots beyond COLS are silently ignored."""
        stats = _dummy_stats(cpu_percent=100)
        slots = _ALL_SLOTS + ["cpu", "ram"]   # 11 items
        frame = render_bars(stats, slots)
        _validate_frame(frame)

    def test_disk_activity_bar(self):
        """disk_percent is I/O activity %; 100% triggers alert stripe — bottom always lit."""
        stats = _dummy_stats(disk_percent=100)
        frame = render_bars(stats, ["disk"] + [None] * 8)
        assert frame[0][ROWS - 1] > 0, "Full disk activity should light bottom row"
        # At 100% the alert stripe is active; top row may be dark (stripe pattern)

    def test_disk_read_bar(self):
        stats = _dummy_stats(disk_read_mbps=50)  # nonzero — should light bottom
        frame = render_bars(stats, ["disk_read"] + [None] * 8)
        assert frame[0][ROWS - 1] > 0

    def test_disk_write_bar(self):
        stats = _dummy_stats(disk_write_mbps=0)
        frame = render_bars(stats, ["disk_write"] + [None] * 8)
        assert all(v == 0 for v in frame[0])

    def test_all_stat_keys_render_without_error(self):
        stats = _dummy_stats()
        for key in ALL_STAT_KEYS:
            slots = [key] + [None] * 8
            _validate_frame(render_bars(stats, slots))

    def test_temp_ddr_bar(self):
        stats = _dummy_stats(temp_ddr_c=80)
        frame = render_bars(stats, ["temp_ddr"] + [None] * 8)
        assert frame[0][ROWS - 1] > 0

    def test_temp_local_bar(self):
        stats = _dummy_stats(temp_local_c=50)
        frame = render_bars(stats, ["temp_local"] + [None] * 8)
        assert frame[0][ROWS - 1] > 0

    def test_battery_bar(self):
        stats = _dummy_stats(battery_percent=50)
        frame = render_bars(stats, ["battery"] + [None] * 8)
        assert frame[0][ROWS - 1] > 0
        assert frame[0][0] == 0    # 50% should not reach top

    def test_battery_full_lights_top(self):
        stats = _dummy_stats(battery_percent=100)
        frame = render_bars(stats, ["battery"] + [None] * 8)
        assert frame[0][0] > 0

    def test_alert_stripe_on_high_cpu(self):
        """Bar at alert level (>=90%) should have alternating off rows."""
        stats = _dummy_stats(cpu_percent=95)
        frame = render_bars(stats, ["cpu"] + [None] * 8)
        col = frame[0]
        lit = [v for v in col if v > 0]
        dark_in_bar = [v for v in col[col.index(next(v for v in col if v > 0)):] if v == 0]
        assert len(dark_in_bar) > 0, "Alert bar should have dark rows within lit region"

    def test_no_alert_stripe_below_threshold(self):
        """Bar below alert threshold should have no dark rows within lit region."""
        stats = _dummy_stats(cpu_percent=50)
        frame = render_bars(stats, ["cpu"] + [None] * 8)
        col = frame[0]
        first_lit = next((i for i, v in enumerate(col) if v > 0), None)
        if first_lit is not None:
            assert all(v > 0 for v in col[first_lit:]), "No stripes below threshold"

    def test_battery_alert_on_low(self):
        """Battery below 20% should show alert stripe."""
        stats = _dummy_stats(battery_percent=15)
        frame = render_bars(stats, ["battery"] + [None] * 8)
        col = frame[0]
        first_lit = next((i for i, v in enumerate(col) if v > 0), None)
        assert first_lit is not None
        has_dark_in_bar = any(v == 0 for v in col[first_lit:])
        assert has_dark_in_bar, "Low battery should show stripe pattern"


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
        assert sum(v for col in render_clock(200) for v in col) > 0

    def test_hours_region_has_pixels(self):
        frame = render_clock(200)
        hours_lit = sum(frame[c][r] for c in range(COLS) for r in range(4, 11))
        assert hours_lit > 0

    def test_minutes_region_has_pixels(self):
        frame = render_clock(200)
        mins_lit = sum(frame[c][r] for c in range(COLS) for r in range(17, 24))
        assert mins_lit > 0

    def test_top_and_bottom_rows_dark(self):
        frame = render_clock(200)
        assert sum(frame[c][0] for c in range(COLS)) == 0
        assert sum(frame[c][ROWS - 1] for c in range(COLS)) == 0


# ---------------------------------------------------------------------------
# render_breathe
# ---------------------------------------------------------------------------

class TestRenderBreathe:
    def test_shape(self):
        _validate_frame(render_breathe())

    def test_uniform_columns(self):
        frame = render_breathe()
        for col in frame:
            assert col == frame[0]


# ---------------------------------------------------------------------------
# Renderer class
# ---------------------------------------------------------------------------

class TestRenderer:
    def test_all_modes_valid(self):
        stats = _dummy_stats()
        for mode in MODES:
            _validate_frame(Renderer(mode=mode).render(stats))

    def test_brightness_scaling_reduces_output(self):
        stats = _dummy_stats(cpu_percent=100)
        r_full = Renderer(mode="bars", brightness=255, bar_slots=_SINGLE_CPU)
        r_half = Renderer(mode="bars", brightness=128, bar_slots=_SINGLE_CPU)
        full = r_full.render(stats)
        half = r_half.render(stats)
        for c in range(COLS):
            for row in range(ROWS):
                assert half[c][row] <= full[c][row]

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            Renderer(mode="invalid_mode")

    def test_default_bar_slots_is_all_stats(self):
        r = Renderer(mode="bars")
        assert r.bar_slots == list(ALL_STAT_KEYS)
        assert "disk" in r.bar_slots
        assert "disk_read" in r.bar_slots
        assert "disk_write" in r.bar_slots
        assert "battery" in r.bar_slots

    def test_custom_bar_slots(self):
        slots = ["cpu", None, "ram"] + [None] * 6
        r = Renderer(mode="bars", bar_slots=slots)
        _validate_frame(r.render(_dummy_stats()))
