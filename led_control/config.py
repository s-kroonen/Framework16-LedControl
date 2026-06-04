"""
Persistent configuration stored in %APPDATA%\\LedControl\\config.json.

Defaults are used on first run; the file is created automatically.
Call config.save() to persist changes.

Bar slot keys (used in bar_slots list)
───────────────────────────────────────
  "cpu"      – CPU overall %
  "ram"      – RAM %
  "gpu"      – GPU load %
  "gpu_vram" – GPU VRAM %
  "disk"     – Disk usage %
  "net_rx"   – Network receive Mbit/s
  "net_tx"   – Network transmit Mbit/s
  "cpu_temp" – CPU package temperature °C
  "gpu_temp" – GPU temperature °C
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_APP_NAME = "LedControl"

# All valid stat slot keys in canonical display order
ALL_STAT_KEYS: list[str] = [
    "cpu", "ram", "gpu", "gpu_vram", "disk",
    "net_rx", "net_tx", "cpu_temp", "gpu_temp",
]

# Human-readable labels for tray menus
STAT_LABELS: dict[str, str] = {
    "cpu":      "CPU %",
    "ram":      "RAM %",
    "gpu":      "GPU load %",
    "gpu_vram": "GPU VRAM %",
    "disk":     "Disk %",
    "net_rx":   "Net ↓ Mbit/s",
    "net_tx":   "Net ↑ Mbit/s",
    "cpu_temp": "CPU Temp °C",
    "gpu_temp": "GPU Temp °C",
}

_DEFAULTS: dict[str, Any] = {
    # Display mode
    "mode": "bars",               # bars | cpu_cores | clock | breathe

    # Bar layout — ordered list of stat keys to display.
    # Length = number of bars (1–9). All 9 matrix columns are divided
    # evenly among the active slots; fewer slots = wider bars.
    "bar_slots": list(ALL_STAT_KEYS),   # default: all 9 stats

    # Global brightness (0–255). Acts as the ceiling when
    # link_screen_brightness is enabled.
    "brightness": 180,

    # When True, matrix brightness tracks the Windows screen brightness
    # (scaled relative to the brightness ceiling above).
    "link_screen_brightness": False,

    # Tick interval in seconds
    "tick_interval": 1.0,

    # Windows startup registration
    "start_on_boot": False,

    # Serial port override (None = auto-detect)
    "serial_port": None,

    # Idle sleep
    "sleep_when_idle": False,
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
        # bar_slots must be a new list instance (not shared with _DEFAULTS)
        self._data["bar_slots"] = list(_DEFAULTS["bar_slots"])
        self.load()

    # ------------------------------------------------------------------

    def load(self) -> None:
        path = _config_path()
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as fh:
                    stored = json.load(fh)
                for key, default in _DEFAULTS.items():
                    value = stored.get(key, default)
                    # Validate bar_slots: keep only recognised keys
                    if key == "bar_slots":
                        value = [k for k in value if k in ALL_STAT_KEYS]
                        if not value:
                            value = list(_DEFAULTS["bar_slots"])
                    self._data[key] = value
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

    # ------------------------------------------------------------------
    # Bar-slot helpers
    # ------------------------------------------------------------------

    def toggle_stat(self, key: str) -> None:
        """Add key to bar_slots if absent; remove if present. Preserves order."""
        slots: list[str] = list(self._data["bar_slots"])
        if key in slots:
            slots.remove(key)
            if not slots:          # never leave zero bars
                return
        else:
            # Insert at canonical position
            canonical_pos = ALL_STAT_KEYS.index(key) if key in ALL_STAT_KEYS else len(slots)
            inserted = False
            for i, existing in enumerate(slots):
                if ALL_STAT_KEYS.index(existing) > canonical_pos:
                    slots.insert(i, key)
                    inserted = True
                    break
            if not inserted:
                slots.append(key)
        self._data["bar_slots"] = slots
