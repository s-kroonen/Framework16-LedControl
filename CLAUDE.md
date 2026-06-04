# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Windows 11 background service/tray application that drives the Framework 16 LED matrix with system stats (CPU, RAM, GPU, etc.) and other visual data. The app installs as a persistent background task (Windows Service or startup entry), exposes a tray icon for control, and is distributed as a Windows installer.

## Development Environment

- **Python** with virtual environment at `.venv/`
- **IDE**: PyCharm (run configurations live in `.idea/`)
- Activate venv: `.venv\Scripts\activate`
- Install deps: `pip install -r requirements.txt`
- Run the app: `python main.py`
- Run tests: `python -m pytest`

## Build & Distribution

The project uses **PyInstaller** to produce a single-file or one-folder Windows executable, then **NSIS** (or Inno Setup) to wrap it into an installer.

- Build executable: `pyinstaller led_control.spec`
- Build installer: run the NSIS/Inno Setup script in `installer/`
- Output lands in `dist/`

## Architecture

```
main.py              — entry point; starts tray icon + background loop
led_control/
  service.py         — Windows background service wrapper (pywin32 / winservice)
  tray.py            — system tray icon + context menu (pystray)
  led_driver.py      — low-level HID/USB comms with Framework 16 LED matrix
  stats.py           — system metric collectors (psutil: CPU, RAM, GPU via GPUtil/WMI)
  renderer.py        — maps metric values → LED frame buffers
  config.py          — persistent user config (JSON/TOML in %APPDATA%)
installer/
  setup.nsi          — NSIS installer script  (or setup.iss for Inno Setup)
tests/
```

### Key design decisions

- The background loop in `service.py` ticks on a configurable interval, collects stats via `stats.py`, renders a frame via `renderer.py`, and pushes it to the LED matrix via `led_driver.py`.
- `config.py` reads/writes settings from `%APPDATA%\LedControl\config.json` so preferences survive reinstalls.
- The tray icon (`tray.py`) communicates with the service loop via a shared thread-safe queue or named pipe — not by importing the service directly — so the UI and I/O loop are decoupled.
- Framework 16 LED matrix is accessed over USB HID; see the [framework-system](https://github.com/FrameworkComputer/framework-system) repo for protocol details.

## Key Dependencies

| Package | Purpose |
|---------|---------|
| `psutil` | CPU / RAM / disk metrics |
| `pystray` | System tray icon (cross-platform, works on Windows) |
| `pywin32` | Windows Service registration & WMI GPU stats |
| `hidapi` / `pyusb` | USB HID communication with LED matrix |
| `pyinstaller` | Freeze to `.exe` |
| `pillow` | Icon image manipulation for tray |
