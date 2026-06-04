"""Tests for LedDriver in dry-run mode (no hardware)."""

from led_control.led_driver import LedDriver, COLS, ROWS


class TestLedDriverDryRun:
    """When no port is found / no device connected, driver runs in dry-run mode."""

    def setup_method(self):
        # Force dry-run by providing a non-existent port
        self.driver = LedDriver(port="INVALID_PORT")
        # connect() will fail silently → dry-run
        self.driver.connect()

    def teardown_method(self):
        self.driver.disconnect()

    def test_connect_dry_run(self):
        # Should not raise even with bad port
        assert not self.driver.connected

    def test_set_brightness_no_crash(self):
        self.driver.set_brightness(128)

    def test_set_brightness_clamps(self):
        # Should not raise; values clamped internally
        self.driver.set_brightness(-1)
        self.driver.set_brightness(999)

    def test_stage_column_wrong_length(self):
        import pytest
        with pytest.raises(ValueError):
            self.driver.stage_column(0, [0] * (ROWS - 1))

    def test_draw_frame_wrong_cols(self):
        import pytest
        with pytest.raises(ValueError):
            self.driver.draw_frame([[0] * ROWS] * (COLS - 1))

    def test_draw_full_frame_no_crash(self):
        frame = [[128] * ROWS for _ in range(COLS)]
        self.driver.draw_frame(frame)

    def test_clear_no_crash(self):
        self.driver.clear()

    def test_sleep_no_crash(self):
        self.driver.sleep(True)
        self.driver.sleep(False)
