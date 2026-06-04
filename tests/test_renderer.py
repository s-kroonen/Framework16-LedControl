"""Tests for the renderer module — no hardware required."""

import pytest
from led_control.renderer import (
    COLS, ROWS, render_bars, render_cpu_cores, render_breathe, render_clock,
    Renderer, MODES,
)
from led_control.stats import SystemStats


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


class TestRenderBars:
    def test_shape(self):
        _validate_frame(render_bars(_dummy_stats()))

    def test_zero_stats_is_dark(self):
        stats = _dummy_stats(cpu_percent=0, ram_percent=0, gpu_percent=0,
                             gpu_vram_percent=0, disk_percent=0,
                             net_sent_mbps=0, net_recv_mbps=0,
                             cpu_temp_c=0, gpu_temp_c=0)
        frame = render_bars(stats)
        assert all(v == 0 for col in frame for v in col)

    def test_full_stats_lights_bars(self):
        stats = _dummy_stats(cpu_percent=100, ram_percent=100)
        frame = render_bars(stats)
        # Top row of CPU (col 0) and RAM (col 1) should be lit
        assert frame[0][0] > 0
        assert frame[1][0] > 0

    def test_bar_grows_from_bottom(self):
        stats = _dummy_stats(cpu_percent=50)
        frame = render_bars(stats)
        # Bottom half of col 0 lit, top half dark
        mid = ROWS // 2
        assert frame[0][ROWS - 1] > 0   # bottom row lit
        assert frame[0][0] == 0          # top row dark


class TestRenderCpuCores:
    def test_shape(self):
        _validate_frame(render_cpu_cores(_dummy_stats()))

    def test_many_cores_merged(self):
        stats = _dummy_stats(cpu_cores=[50.0] * 32)
        frame = render_cpu_cores(stats)
        _validate_frame(frame)

    def test_fewer_cores_than_cols(self):
        stats = _dummy_stats(cpu_cores=[100.0, 0.0])
        frame = render_cpu_cores(stats)
        # First column fully lit, second fully dark
        assert frame[0][ROWS - 1] > 0
        assert all(v == 0 for v in frame[1])


class TestRenderClock:
    def test_shape(self):
        _validate_frame(render_clock())

    def test_some_pixels_lit(self):
        frame = render_clock(brightness=200)
        total = sum(v for col in frame for v in col)
        assert total > 0


class TestRenderBreathe:
    def test_shape(self):
        _validate_frame(render_breathe())

    def test_uniform(self):
        frame = render_breathe()
        # All columns identical
        for col in frame:
            assert col == frame[0]


class TestRenderer:
    def test_all_modes_produce_valid_frames(self):
        stats = _dummy_stats()
        for mode in MODES:
            r = Renderer(mode=mode)
            frame = r.render(stats)
            _validate_frame(frame)

    def test_brightness_scaling(self):
        stats = _dummy_stats(cpu_percent=100)
        r_full = Renderer(mode="bars", brightness=255)
        r_half = Renderer(mode="bars", brightness=128)
        full = r_full.render(stats)
        half = r_half.render(stats)
        # Every pixel in half should be ≤ corresponding pixel in full
        for c in range(COLS):
            for row in range(ROWS):
                assert half[c][row] <= full[c][row]

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            Renderer(mode="invalid_mode")
