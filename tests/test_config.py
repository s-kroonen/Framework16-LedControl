"""Tests for Config — reads/writes into a temp directory."""

import json
import os
from pathlib import Path
from unittest.mock import patch

from led_control.config import ALL_STAT_KEYS, NUM_SLOTS


def _cfg(tmp_path):
    """Return a fresh Config backed by tmp_path."""
    from led_control.config import Config
    with patch.dict(os.environ, {"APPDATA": str(tmp_path)}):
        return Config()


def _cfg_ctx(tmp_path):
    """Context-manager helper that keeps the APPDATA patch alive."""
    from led_control.config import Config
    return patch.dict(os.environ, {"APPDATA": str(tmp_path)}), Config


# ---------------------------------------------------------------------------

def test_defaults(tmp_path):
    cfg = _cfg(tmp_path)
    assert cfg.mode == "bars"
    assert 0 < cfg.brightness <= 255
    assert cfg.tick_interval > 0
    assert cfg.link_screen_brightness is False
    assert cfg.start_on_boot is False


def test_bar_slots_default_is_9_items(tmp_path):
    cfg = _cfg(tmp_path)
    assert len(cfg.bar_slots) == NUM_SLOTS
    # Default slots are a curated 9 (ALL_STAT_KEYS has 11 — disk_read and
    # disk_write are available but not shown by default)
    assert "cpu" in cfg.bar_slots
    assert "disk" in cfg.bar_slots
    assert all(k in ALL_STAT_KEYS for k in cfg.bar_slots if k is not None)


def test_save_and_reload(tmp_path):
    from led_control.config import Config
    with patch.dict(os.environ, {"APPDATA": str(tmp_path)}):
        cfg = Config()
        cfg.mode = "clock"
        cfg.brightness = 42
        cfg.bar_slots[0] = None
        cfg.bar_slots[1] = "gpu"
        cfg.save()

        cfg2 = Config()
        assert cfg2.mode == "clock"
        assert cfg2.brightness == 42
        assert cfg2.bar_slots[0] is None
        assert cfg2.bar_slots[1] == "gpu"


def test_unknown_keys_in_file_are_ignored(tmp_path):
    import pytest
    from led_control import config as cfg_mod
    with patch.dict(os.environ, {"APPDATA": str(tmp_path)}):
        cfg_dir = Path(tmp_path) / "LedControl"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.json").write_text(
            json.dumps({"mode": "breathe", "unknown_key": "garbage"})
        )
        cfg = cfg_mod.Config()
        assert cfg.mode == "breathe"
        with pytest.raises(AttributeError):
            _ = cfg.unknown_key


def test_invalid_bar_slot_keys_become_none(tmp_path):
    from led_control.config import Config
    with patch.dict(os.environ, {"APPDATA": str(tmp_path)}):
        cfg_dir = Path(tmp_path) / "LedControl"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.json").write_text(
            json.dumps({"bar_slots": ["cpu", "invalid_key", "ram",
                                       None, None, None, None, None, None]})
        )
        cfg = Config()
        assert cfg.bar_slots[0] == "cpu"
        assert cfg.bar_slots[1] is None   # invalid key → None
        assert cfg.bar_slots[2] == "ram"


def test_short_slot_list_padded_to_9(tmp_path):
    from led_control.config import Config
    with patch.dict(os.environ, {"APPDATA": str(tmp_path)}):
        cfg_dir = Path(tmp_path) / "LedControl"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.json").write_text(
            json.dumps({"bar_slots": ["cpu", "ram"]})
        )
        cfg = Config()
        assert len(cfg.bar_slots) == NUM_SLOTS
        assert cfg.bar_slots[0] == "cpu"
        assert cfg.bar_slots[1] == "ram"
        assert all(v is None for v in cfg.bar_slots[2:])


def test_set_slot_assigns_key(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.set_slot(0, "gpu")
    assert cfg.bar_slots[0] == "gpu"


def test_set_slot_clears_with_none(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.set_slot(3, None)
    assert cfg.bar_slots[3] is None


def test_set_slot_out_of_range_raises(tmp_path):
    import pytest
    cfg = _cfg(tmp_path)
    with pytest.raises(IndexError):
        cfg.set_slot(9, "cpu")
    with pytest.raises(IndexError):
        cfg.set_slot(-1, "cpu")


def test_set_slot_unknown_key_raises(tmp_path):
    import pytest
    cfg = _cfg(tmp_path)
    with pytest.raises(ValueError):
        cfg.set_slot(0, "not_a_stat")


def test_setattr_updates_data(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.brightness = 99
    assert cfg.brightness == 99
