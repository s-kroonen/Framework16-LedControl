# Framework 16 LED Matrix — Developer Reference

Technical reference for the Framework 16 LED matrix input module and this project's implementation.

---

## Hardware

- **Module**: Framework 16 LED Matrix Input Module
- **Interface**: USB CDC-ACM virtual serial port (115200 baud)
- **USB IDs**: VID `0x32AC` PID `0x0020`
- **Matrix size**: 9 columns × 34 rows
- **Brightness**: 0–255 per LED

The COM port assigned by Windows varies. The app auto-detects the matrix by VID/PID in `led_driver.py`; a fallback manual port can be set in `%APPDATA%\LedControl\config.json` under `serial_port`.

---

## LED Protocol

The matrix uses a **StageCol + FlushCols** protocol over the CDC-ACM connection.

1. **StageCol** — send the brightness values for one column (34 bytes)
2. **FlushCols** — commit all staged columns to the display in one atomic update

Full protocol details and command byte definitions are in the [framework-system](https://github.com/FrameworkComputer/framework-system) repository.

The project's wrapper is in `led_control/led_driver.py`:

```python
driver = LedDriver(port="COM3")   # or port=None to auto-detect
driver.connect()
driver.draw_frame(frame)          # frame[col][row], values 0–255
driver.sleep(True)                # blank the matrix without disconnecting
driver.clear()                    # zero all LEDs
driver.disconnect()
```

The driver retries on disconnect with exponential backoff (1 s → 60 s max) managed by the service loop.

---

## Frame Format

A frame is a Python `list[list[int]]` with shape `[COLS][ROWS]` = `[9][34]`.

- `frame[col][row]` — brightness 0–255
- `col 0` = leftmost column, `col 8` = rightmost
- `row 0` = top of matrix, `row 33` = bottom
- Bars fill from the **bottom up** (row 33 first)

```python
from led_control.renderer import Frame, COLS, ROWS

frame: Frame = [[0] * ROWS for _ in range(COLS)]
frame[0][33] = 200   # bottom pixel of leftmost column
```

---

## Project Architecture

```
main.py              — entry point; starts daemon thread + pystray loop
led_control/
  service.py         — background loop: collect → render → push (daemon thread)
  tray.py            — system tray icon + context menu (pystray, main thread)
  led_driver.py      — CDC-ACM serial driver with reconnect logic
  stats.py           — SystemStats dataclass + StatsCollector
  renderer.py        — SystemStats → Frame; display modes, alert logic
  config.py          — %APPDATA%\LedControl\config.json wrapper
  startup.py         — Windows run-on-boot registry + Start Menu shortcut
tests/
  test_renderer.py   — rendering unit tests (pytest)
```

### Thread model

| Thread | What it does |
|--------|-------------|
| Main (pystray) | Drives the tray icon event loop; puts commands into the queue |
| LedServiceLoop (daemon) | Ticks on `cfg.tick_interval`; reads queue, collects stats, renders, sends frame |

The two threads share only a `queue.Queue[str]` (commands tray → service) and the `Config` object. The service thread calls `pythoncom.CoInitialize()` at startup so WMI calls work from it.

---

## Configuration

Stored at `%APPDATA%\LedControl\config.json`. Accessed via `Config` attributes:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `mode` | str | `"bars"` | Display mode |
| `bar_slots` | list[str\|null] | 9 defaults | Stat key per column, or null |
| `brightness` | int | `180` | LED brightness ceiling 0–255 |
| `link_screen_brightness` | bool | `false` | Scale matrix brightness with screen |
| `tick_interval` | float | `1.0` | Seconds between frames |
| `start_on_boot` | bool | `false` | Register in Windows startup |
| `serial_port` | str\|null | `null` | Force a COM port (null = auto-detect) |
| `auto_off_battery_saver` | bool | `false` | Sleep matrix when Battery Saver is on |
| `alert_mode` | str | `"stripe"` | Threshold alert style |

---

## Stat Keys

All 14 keys are defined in `config.ALL_STAT_KEYS` and map to fields of `stats.SystemStats`.

| Key | Field | Notes |
|-----|-------|-------|
| `cpu` | `cpu_percent` | psutil, non-blocking |
| `ram` | `ram_percent` | psutil virtual_memory |
| `gpu` | `gpu_percent` | LHM iGPU load; falls back to GPUtil dGPU |
| `gpu_vram` | `gpu_vram_percent` | GPUtil only; 0 without dGPU |
| `disk` | `disk_percent` | I/O busy % (Task Manager style) |
| `disk_read` | `disk_read_mbps` | Dynamic ceiling via RollingMax |
| `disk_write` | `disk_write_mbps` | Dynamic ceiling via RollingMax |
| `net_rx` | `net_recv_mbps` | Mbit/s, dynamic ceiling |
| `net_tx` | `net_sent_mbps` | Mbit/s, dynamic ceiling |
| `battery` | `battery_percent` | psutil; 100 when on AC |
| `cpu_temp` | `cpu_temp_c` | LHM: F75303_CPU or CPU Package |
| `temp_ddr` | `temp_ddr_c` | LHM: F75303_DDR |
| `temp_local` | `temp_local_c` | LHM: F75303_Local |
| `gpu_temp` | `gpu_temp_c` | LHM: dGPU AMB; 0 if no dGPU |

**Temperature and iGPU load** require [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) or Open Hardware Monitor running. The sensor reader connects to their WMI namespace (`root\LibreHardwareMonitor` or `root\OpenHardwareMonitor`) at startup on the service thread.

---

## Display Modes

| Mode | Render function | Description |
|------|----------------|-------------|
| `bars` | `render_bars()` | 9 vertical bar graphs from `cfg.bar_slots` |
| `cpu_cores` | `render_cpu_cores()` | One bar per logical core, merged into 9 buckets |
| `clock` | `render_clock()` | HH:MM using 4×7 bitmap font, blinking separator |
| `breathe` | `render_breathe()` | Full-matrix sine-wave breathing pulse |

The active mode is stored in `cfg.mode` and dispatched in `Renderer.render()`.

---

## Alert System

When a metric meets or exceeds its threshold, the column shows a visual warning pattern. Thresholds are in `renderer._ALERT_THRESHOLDS`.

| Key | Threshold | Direction |
|-----|-----------|-----------|
| `cpu`, `ram`, `gpu`, `gpu_vram`, `disk` | 90 % | ≥ |
| `disk_read`, `disk_write` | 200 MB/s | ≥ |
| `net_rx`, `net_tx` | 100 Mbit/s | ≥ |
| `cpu_temp`, `gpu_temp` | 85 °C | ≥ |
| `temp_ddr` | 70 °C | ≥ |
| `temp_local` | 60 °C | ≥ |
| `battery` | 20 % | **≤** (low battery) |

| Alert mode | Visual |
|------------|--------|
| `none` | Normal solid bar |
| `stripe` | Alternating lit/dark rows within the bar |
| `stripe_blink` | Alternates striped ↔ solid each frame |
| `bar_blink` | Alternates solid ↔ dark each frame |

Blink is frame-count based (`Renderer._render_count % 2`) to avoid aliasing with the 1 s tick.

---

## Battery Saver Detection

`stats.get_battery_saver()` reads:

1. **Registry** (primary): `HKLM\SYSTEM\CurrentControlSet\Control\Power\EnergySaverState`
   - `1` = Battery Saver active → returns `True`
   - `2` = configured but inactive
   - `0` = disabled
2. **GetSystemPowerStatus** (fallback): `SystemStatusFlag & 1`

`GetSystemPowerStatus.SystemStatusFlag` returns `0` on Windows 11 Framework hardware even when Battery Saver is active, so the registry key is authoritative.

---

## Dynamic Bar Ceilings

Rate metrics (`disk_read`, `disk_write`, `net_rx`, `net_tx`) use a `RollingMax` tracker so the bar always uses most of its vertical range:

- New peak → ceiling jumps immediately
- Quiet period → ceiling decays toward the minimum floor at ~1.5 %/s (half-life ≈ 45 s)

This means a burst of disk activity stretches the bar to fill the column, then the ceiling slowly retreats as activity drops.

---

## Adding a Display Mode

See `CLAUDE.md` → *How to Add a New Display Mode* for step-by-step instructions.

Short version: write `render_mymode()` in `renderer.py`, add the name to `MODES`, add a branch in `Renderer.render()`, add a tray `Item` in `tray._menu_items()`.

---

## Adding a Stat Key

See `CLAUDE.md` → *How to Add a New Stat / Custom Screen Column* for step-by-step instructions.

Short version: add field to `SystemStats`, collect it in `StatsCollector.collect()`, register key + label in `config.py`, map it in `renderer._stat_to_column()`.

---

## Troubleshooting

**Matrix not detected**

Check Device Manager → Ports for a COM port with VID 32AC / PID 0020. If it shows as an unknown device, install the Framework CDC-ACM driver. Force the port with `"serial_port": "COM5"` in config.json.

**Temperature stats show 0**

LibreHardwareMonitor or Open Hardware Monitor must be running. Start it, let it scan sensors once, then restart the LED app. The service thread re-attempts WMI connection at startup only.

**iGPU load shows 0 despite LHM running**

The sensor name varies by driver version. Check LHM's sensor list for names containing "GPU". Add the exact name to `TempSensorReader._LOAD_CANDIDATES["gpu_percent"]` in `stats.py`.

**Battery Saver auto-off not triggering**

Run this from a Python prompt while Battery Saver is active to verify the registry value:

```python
import winreg
with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                    r"SYSTEM\CurrentControlSet\Control\Power") as k:
    print(winreg.QueryValueEx(k, "EnergySaverState"))
# Expected: (1, 4)
```

**High CPU usage from the app**

Increase `tick_interval` in config.json (e.g. `2.0`). The service thread's 50 ms idle sleep is intentional to keep command latency low; the stat collection itself is the main cost.

**pystray menu item throws ValueError**

pystray's `_assert_action` rejects any callable that has parameters beyond `(icon, item)`. Do not use lambdas with default keyword arguments (`lambda icon, item, x=val: ...`). Use a factory method instead:

```python
def _my_cb(self, val):
    def cb(icon, item):
        self._send(f"cmd:{val}")
    return cb
```
