# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Windows 11 background service/tray application that drives the Framework 16 LED matrix with system stats (CPU, RAM, GPU, battery, temperatures, network, disk) and other visual data. The app registers as a startup entry, exposes a tray icon for control, and is distributed as a Windows installer.

## Development Environment

- **Python** with virtual environment at `.venv/`
- **IDE**: PyCharm (run configurations live in `.idea/`)
- Activate venv: `.venv\Scripts\activate`
- Install deps: `pip install -r requirements.txt`
- Run the app: `python main.py`
- Run tests: `python -m pytest`

## Git

**Never attempt `git commit`.** The SSH signing key requires an interactive passphrase. Stage files and tell the user to commit manually.

## Build & Distribution

The project uses **PyInstaller** to produce a single-file or one-folder Windows executable, then **NSIS** (or Inno Setup) to wrap it into an installer.

- Build executable: `pyinstaller led_control.spec`
- Build installer: run the NSIS/Inno Setup script in `installer/`
- Output lands in `dist/`

## Architecture

```
main.py                   — entry point; starts tray icon + background loop
                            also calls startup.create_start_menu_shortcut()
led_control/
  service.py              — background service loop (daemon thread)
  tray.py                 — system tray icon + context menu (pystray)
  led_driver.py           — USB CDC-ACM serial comms with Framework 16 LED matrix
  stats.py                — system metric collectors (psutil, WMI/LHM, winreg)
  renderer.py             — maps SystemStats → 9×34 LED frame buffers
  config.py               — persistent user config (JSON in %APPDATA%\LedControl\)
  startup.py              — Windows startup registration and Start Menu shortcut
installer/
  setup.nsi               — NSIS installer script  (or setup.iss for Inno Setup)
tests/
  test_renderer.py        — unit tests for rendering logic (58 tests)
```

### Key design decisions

- **Service loop** (`service.py`) ticks at `cfg.tick_interval` seconds. On each tick it collects stats, renders a frame, and pushes it to the matrix. The loop runs in a daemon thread; the main thread runs the pystray event loop.
- **IPC**: `tray.py` puts string commands into a `queue.Queue` which `service.py` drains on each tick. There are no shared mutable globals between tray and service — only the queue and the `Config` object (which is read from tray and also mutated by tray for optimistic UI updates).
- **COM initialisation**: `service.py` calls `pythoncom.CoInitialize()` at the start of the service thread so WMI calls work from that thread.
- **Config** is stored at `%APPDATA%\LedControl\config.json` and survives reinstalls. `Config` uses `__getattr__`/`__setattr__` so fields are accessed as attributes: `cfg.brightness`, `cfg.alert_mode`, etc.
- **LED protocol**: USB CDC-ACM serial @115200 baud, VID=0x32AC PID=0x0020, 9×34 matrix. StageCol+FlushCols protocol. See [framework-system](https://github.com/FrameworkComputer/framework-system) for details.
- **pystray callbacks**: pystray's `_assert_action` rejects lambdas that have default keyword arguments. All slot/alert-mode callbacks use factory methods that return plain `(icon, item)` callables.

## Key Dependencies

| Package | Purpose |
|---------|---------|
| `psutil` | CPU / RAM / disk / network / battery metrics |
| `pystray` | System tray icon (Windows) |
| `pywin32` | WMI GPU/brightness stats; `pythoncom.CoInitialize()` |
| `pillow` | Icon image for tray |
| `pyinstaller` | Freeze to `.exe` |
| `LibreHardwareMonitor` | Temperature and iGPU load sensors (external tool, user must run it) |

## Stat Keys

All valid stat keys are defined in `config.ALL_STAT_KEYS`. Each key maps to a field in `stats.SystemStats`.

| Key | Source | Description |
|-----|--------|-------------|
| `cpu` | psutil | Overall CPU % |
| `ram` | psutil | RAM % |
| `gpu` | LHM (iGPU) / GPUtil (dGPU) | GPU load % |
| `gpu_vram` | GPUtil | GPU VRAM % |
| `disk` | psutil | Disk I/O busy % (Task-Manager style) |
| `disk_read` | psutil | Disk read MB/s (dynamic ceiling) |
| `disk_write` | psutil | Disk write MB/s (dynamic ceiling) |
| `net_rx` | psutil | Network receive Mbit/s (dynamic ceiling) |
| `net_tx` | psutil | Network transmit Mbit/s (dynamic ceiling) |
| `battery` | psutil | Battery charge % |
| `cpu_temp` | LHM: F75303_CPU / CPU Package | APU die temperature °C |
| `temp_ddr` | LHM: F75303_DDR | DDR memory temperature °C |
| `temp_local` | LHM: F75303_Local | Local/ambient temperature °C |
| `gpu_temp` | LHM: dGPU AMB | GPU temperature °C (0 if no dGPU) |

Temperature and iGPU load require **LibreHardwareMonitor** or **Open Hardware Monitor** running in the background. Without it those stats return 0.

**Battery saver detection** uses `HKLM\SYSTEM\CurrentControlSet\Control\Power\EnergySaverState` (primary) — value `1` = active, `2` = configured but inactive, `0` = disabled. `GetSystemPowerStatus.SystemStatusFlag` is unreliable on Windows 11 Framework hardware and is only used as a fallback.

## Display Modes

Defined in `renderer.MODES = ("bars", "cpu_cores", "clock", "breathe")`.

| Mode | Description |
|------|-------------|
| `bars` | Nine vertical bar graphs, one per column, driven by `cfg.bar_slots` |
| `cpu_cores` | One bar per logical CPU core, merged into 9 buckets |
| `clock` | HH above MM using a 4×7 bitmap font |
| `breathe` | Full-matrix sine-wave breathing pulse |

The `Renderer.render(stats)` method dispatches to the appropriate `render_*` function based on `self.mode`.

## Alert Modes

When a metric exceeds its threshold (`renderer._ALERT_THRESHOLDS`), the column can show a visual warning. The active mode is set via `cfg.alert_mode`.

| Mode | Behaviour |
|------|-----------|
| `none` | Normal solid bar |
| `stripe` | Alternating rows dark within the lit region (striped pattern) |
| `stripe_blink` | Alternates between striped and solid bar each frame |
| `bar_blink` | Alternates between solid bar and fully dark each frame |

Blink is driven by `Renderer._render_count % 2 == 0` (not wall-clock time) to avoid aliasing with the 1 s tick interval.

**Battery alert**: triggered when battery is *below* the threshold (low battery), unlike all other keys which alert when *at or above*.

## Service Commands

Commands are strings placed in the queue by `tray.py` and processed by `service._handle_command()`.

| Command | Effect |
|---------|--------|
| `quit` | Stop the loop and exit |
| `mode:<name>` | Switch display mode (`bars`, `cpu_cores`, `clock`, `breathe`) |
| `brightness:<0-255>` | Set brightness ceiling |
| `set_slot:<idx>:<key\|None>` | Assign stat key (or empty) to a column (0-based) |
| `sleep` | Manually sleep the matrix |
| `wake` | Wake the matrix and override battery-saver auto-off |
| `link_brightness:<0\|1>` | Link matrix brightness to screen brightness |
| `auto_battery_saver:<0\|1>` | Enable/disable auto-off when Battery Saver is active |
| `set_alert_mode:<mode>` | Set alert mode (`none`, `stripe`, `stripe_blink`, `bar_blink`) |
| `reload_config` | Re-read config from disk |

---

## How to Add a New Display Mode

Display modes render a complete `Frame` (9×34 brightness matrix) from a `SystemStats` snapshot (or independently, like `clock`).

### 1. Write the render function in `renderer.py`

```python
def render_mymode(stats: SystemStats) -> Frame:
    """One-line description."""
    frame = _empty_frame()
    # ... fill frame[col][row] with 0–255 values ...
    return frame
```

Use existing helpers:
- `_bar_column(value_pct, brightness, alert)` — vertical bar, 0–100 %
- `_temp_bar_column(temp_c, alert)` — brightness scales with temperature
- `_rate_bar_column(value, ceiling, alert)` — bar for rate metrics

### 2. Register the mode name

```python
MODES = ("bars", "cpu_cores", "clock", "breathe", "mymode")
```

### 3. Add a branch in `Renderer.render()`

```python
elif self.mode == "mymode":
    frame = render_mymode(stats)
```

Place it before the `else: frame = _empty_frame()` fallback. For modes that don't need brightness scaling (like `clock`), return early with `return render_mymode(...)` to skip the scale step.

### 4. Expose it in the tray menu (`tray.py`)

```python
Item("My mode", lambda icon, item: self._send("mode:mymode")),
```

Add the `Item` inside the `"Display mode"` submenu block in `_menu_items()`.

### 5. (Optional) Add tests in `tests/test_renderer.py`

```python
def test_mymode_basic():
    r = Renderer(mode="mymode")
    stats = _dummy_stats()
    frame = r.render(stats)
    assert len(frame) == 9
    assert len(frame[0]) == 34
```

---

## How to Add a New Stat / Custom Screen Column

A stat key is a string that maps `SystemStats` → a single 34-row brightness column in the `bars` mode. Custom screen columns are just stat keys assigned to bar slots.

### 1. Add the field to `SystemStats` (`stats.py`)

```python
@dataclass
class SystemStats:
    ...
    my_metric: float = 0.0
```

### 2. Collect the value in `StatsCollector.collect()` (`stats.py`)

Add a helper method `_my_metric()` and call it in `collect()`:

```python
def _my_metric(self) -> float:
    # read from psutil, WMI, registry, etc.
    return some_value

def collect(self) -> SystemStats:
    ...
    return SystemStats(
        ...
        my_metric = self._my_metric(),
    )
```

### 3. Register the key in `config.py`

```python
ALL_STAT_KEYS: list[str] = [
    ..., "my_metric",
]
STAT_LABELS["my_metric"] = "My Metric"
```

If the key belongs to the temperature group, also add it to `TEMP_STAT_KEYS`.

Add an alert threshold in `renderer._ALERT_THRESHOLDS` if a warning is desired:

```python
_ALERT_THRESHOLDS["my_metric"] = 90.0   # alert at ≥ 90
```

For a *low* alert (like battery), add handling in `_stat_to_column._is_alert()`:

```python
return raw < th if key in ("battery", "my_metric") else raw >= th
```

### 4. Map the key to a column in `renderer._stat_to_column()`

```python
if key == "my_metric":  return _pct(stats.my_metric)
```

Use the appropriate sub-function:
- `_pct(value)` — for percentage-based metrics (0–100 %)
- `_rate(value, "roll_key")` — for throughput metrics with a dynamic ceiling
- `_temp(temp_c)` — for temperature in °C

For a dynamic ceiling, also add a `RollingMax` entry in `Renderer.__init__()`:

```python
self._rolls["my_metric"] = RollingMax(floor=1.0)
```

And update it in `Renderer._update_rolls()`:

```python
self._rolls["my_metric"].update(stats.my_metric)
```

### 5. Add the stat to the default slot list (optional, `config.py`)

```python
_DEFAULT_SLOTS = [
    "cpu", "ram", "gpu", ..., "my_metric",
]
```

Only do this if it should be shown by default; otherwise it's opt-in via the tray.

### 6. Add tests

In `tests/test_renderer.py`, add `my_metric=X` to `_dummy_stats()` and write at least one test verifying the column renders non-zero when the metric is non-zero.
