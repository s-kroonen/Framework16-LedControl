"""Tests for Config — reads/writes a temp directory."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch


def _make_config(tmp_dir: str):
    from led_control.config import Config
    appdata_fake = str(tmp_dir)
    with patch.dict(os.environ, {"APPDATA": appdata_fake}):
        return Config()


def test_defaults(tmp_path):
    cfg = _make_config(tmp_path)
    assert cfg.mode == "bars"
    assert 0 < cfg.brightness <= 255
    assert cfg.tick_interval > 0


def test_save_and_reload(tmp_path):
    # Keep the APPDATA patch active for both save() and reload
    from led_control.config import Config
    with patch.dict(os.environ, {"APPDATA": str(tmp_path)}):
        cfg = Config()
        cfg.mode = "clock"
        cfg.brightness = 42
        cfg.save()

        cfg2 = Config()
        assert cfg2.mode == "clock"
        assert cfg2.brightness == 42


def test_unknown_keys_in_file_are_ignored(tmp_path):
    from led_control import config as cfg_mod
    with patch.dict(os.environ, {"APPDATA": str(tmp_path)}):
        cfg_dir = Path(tmp_path) / "LedControl"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_file = cfg_dir / "config.json"
        cfg_file.write_text(json.dumps({"mode": "breathe", "unknown_key": "garbage"}))

        cfg = cfg_mod.Config()
        assert cfg.mode == "breathe"
        # unknown key should not be exposed
        import pytest
        with pytest.raises(AttributeError):
            _ = cfg.unknown_key


def test_setattr_updates_data(tmp_path):
    cfg = _make_config(tmp_path)
    cfg.brightness = 99
    assert cfg.brightness == 99
