"""Tests for Config — reads/writes a temp directory."""

import json
import os
from pathlib import Path
from unittest.mock import patch

from led_control.config import ALL_STAT_KEYS


def _make_config(tmp_dir):
    from led_control.config import Config
    with patch.dict(os.environ, {"APPDATA": str(tmp_dir)}):
        return Config()


def test_defaults(tmp_path):
    cfg = _make_config(tmp_path)
    assert cfg.mode == "bars"
    assert 0 < cfg.brightness <= 255
    assert cfg.tick_interval > 0
    assert cfg.bar_slots == list(ALL_STAT_KEYS)
    assert cfg.link_screen_brightness is False


def test_save_and_reload(tmp_path):
    from led_control.config import Config
    with patch.dict(os.environ, {"APPDATA": str(tmp_path)}):
        cfg = Config()
        cfg.mode = "clock"
        cfg.brightness = 42
        cfg.bar_slots = ["cpu", "ram"]
        cfg.save()

        cfg2 = Config()
        assert cfg2.mode == "clock"
        assert cfg2.brightness == 42
        assert cfg2.bar_slots == ["cpu", "ram"]


def test_unknown_keys_in_file_are_ignored(tmp_path):
    from led_control import config as cfg_mod
    import pytest
    with patch.dict(os.environ, {"APPDATA": str(tmp_path)}):
        cfg_dir = Path(tmp_path) / "LedControl"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_file = cfg_dir / "config.json"
        cfg_file.write_text(json.dumps({"mode": "breathe", "unknown_key": "garbage"}))

        cfg = cfg_mod.Config()
        assert cfg.mode == "breathe"
        with pytest.raises(AttributeError):
            _ = cfg.unknown_key


def test_setattr_updates_data(tmp_path):
    cfg = _make_config(tmp_path)
    cfg.brightness = 99
    assert cfg.brightness == 99


def test_invalid_bar_slots_replaced_with_defaults(tmp_path):
    from led_control.config import Config
    with patch.dict(os.environ, {"APPDATA": str(tmp_path)}):
        cfg_dir = Path(tmp_path) / "LedControl"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.json").write_text(
            json.dumps({"bar_slots": ["invalid_key", "also_bad"]})
        )
        cfg = Config()
        assert cfg.bar_slots == list(ALL_STAT_KEYS), "Invalid slots should fall back to defaults"


def test_toggle_stat_removes_present_key(tmp_path):
    cfg = _make_config(tmp_path)
    assert "cpu" in cfg.bar_slots
    cfg.toggle_stat("cpu")
    assert "cpu" not in cfg.bar_slots


def test_toggle_stat_adds_absent_key(tmp_path):
    cfg = _make_config(tmp_path)
    cfg.bar_slots = ["ram", "gpu"]
    cfg.toggle_stat("cpu")
    assert "cpu" in cfg.bar_slots


def test_toggle_stat_preserves_canonical_order(tmp_path):
    cfg = _make_config(tmp_path)
    cfg.bar_slots = ["ram", "gpu"]       # cpu removed
    cfg.toggle_stat("cpu")               # re-add cpu
    # cpu should come before ram and gpu (canonical order: cpu, ram, gpu, ...)
    assert cfg.bar_slots.index("cpu") < cfg.bar_slots.index("ram")


def test_toggle_stat_wont_remove_last_slot(tmp_path):
    cfg = _make_config(tmp_path)
    cfg.bar_slots = ["cpu"]
    cfg.toggle_stat("cpu")              # would leave zero slots
    assert cfg.bar_slots == ["cpu"]     # unchanged


def test_link_screen_brightness_default_false(tmp_path):
    cfg = _make_config(tmp_path)
    assert cfg.link_screen_brightness is False
