"""
Persistent configuration stored in %APPDATA%\\LedControl\\config.json.

bar_slots
─────────
Always exactly 9 items — one per physical matrix column.
Each item is either a stat key string or null/None (column stays dark).

Valid stat keys:
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
from typing import Any, Optional

log = logging.getLogger(__name__)

_APP_NAME = "LedControl"
NUM_SLOTS = 9   # matches physical matrix column count

# Canonical ordered list of all stat keys
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

# Default slots: all 9 stats, one per column
_DEFAULT_SLOTS: list[Optional[str]] = list(ALL_STAT_KEYS)  # exactly 9

_DEFAULTS: dict[str, Any] = {
    "mode": "bars",
    "bar_slots": list(_DEFAULT_SLOTS),
    "brightness": 180,
    "link_screen_brightness": False,
    "tick_interval": 1.0,
    "start_on_boot": False,
    "serial_port": None,
    "sleep_when_idle": False,
    "idle_timeout_s": 300,
}


def _config_dir() -> Path:
    appdata = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
    return Path(appdata) / _APP_NAME


def _config_path() -> Path:
    return _config_dir() / "config.json"


def _normalise_slots(raw: Any) -> list[Optional[str]]:
    """
    Coerce a raw value from JSON into a valid 9-item slot list.
    Unknown keys become None; the list is padded/trimmed to NUM_SLOTS.
    """
    if not isinstance(raw, list):
        return list(_DEFAULT_SLOTS)
    slots: list[Optional[str]] = []
    for item in raw:
        if item is None or item in ALL_STAT_KEYS:
            slots.append(item)
        else:
            slots.append(None)   # unknown key → empty slot
    # Pad to NUM_SLOTS
    while len(slots) < NUM_SLOTS:
        slots.append(None)
    return slots[:NUM_SLOTS]


class Config:
    """Read/write the application configuration."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = dict(_DEFAULTS)
        self._data["bar_slots"] = list(_DEFAULT_SLOTS)
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
                    if key == "bar_slots":
                        value = _normalise_slots(value)
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
    # Slot helpers
    # ------------------------------------------------------------------

    def set_slot(self, index: int, key: Optional[str]) -> None:
        """Assign a stat key (or None) to a specific column slot (0-based)."""
        if not (0 <= index < NUM_SLOTS):
            raise IndexError(f"Slot index {index} out of range (0–{NUM_SLOTS - 1})")
        if key is not None and key not in ALL_STAT_KEYS:
            raise ValueError(f"Unknown stat key: {key!r}")
        slots = list(self._data["bar_slots"])
        slots[index] = key
        self._data["bar_slots"] = slots
