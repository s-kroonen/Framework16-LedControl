"""
Persistent configuration stored in %APPDATA%\\LedControl\\config.json.

Defaults are used on first run; the file is created automatically.
Call config.save() to persist changes.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_APP_NAME = "LedControl"

_DEFAULTS: dict[str, Any] = {
    "mode": "bars",          # bars | cpu_cores | clock | breathe
    "brightness": 180,       # 0–255
    "tick_interval": 1.0,    # seconds between stat collections
    "start_on_boot": False,  # register as Windows startup entry
    "serial_port": None,     # None = auto-detect; or e.g. "COM5"
    "sleep_when_idle": False, # dim matrix after idle_timeout_s seconds
    "idle_timeout_s": 300,
}


def _config_dir() -> Path:
    appdata = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
    return Path(appdata) / _APP_NAME


def _config_path() -> Path:
    return _config_dir() / "config.json"


class Config:
    """Read/write the application configuration."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = dict(_DEFAULTS)
        self.load()

    # ------------------------------------------------------------------

    def load(self) -> None:
        path = _config_path()
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as fh:
                    stored = json.load(fh)
                # Only keep known keys; fill missing ones with defaults
                for key, default in _DEFAULTS.items():
                    self._data[key] = stored.get(key, default)
                log.debug("Config loaded from %s", path)
            except Exception as exc:
                log.warning("Cannot read config (%s); using defaults", exc)
        else:
            log.info("No config file found; using defaults")

    def save(self) -> None:
        path = _config_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
            log.debug("Config saved to %s", path)
        except Exception as exc:
            log.error("Cannot save config: %s", exc)

    # ------------------------------------------------------------------
    # Attribute-style access
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(f"Unknown config key: {name!r}")

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            super().__setattr__(name, value)
        else:
            self._data[name] = value

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)
