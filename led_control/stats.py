"""
System metric collectors.

Temperature sensors
───────────────────
Windows:
  Requires LibreHardwareMonitor or Open Hardware Monitor running.
  Connects to their WMI namespace and maps sensor names to stat keys.

Linux:
  Reads directly from psutil.sensors_temperatures() (kernel hwmon).
  iGPU busy % is read from /sys/class/drm/card*/device/gpu_busy_percent
  (AMD only; returns 0 on Intel/NVIDIA without extra tooling).

Disk metrics
────────────
  disk_percent    I/O busy % (read_time + write_time delta / elapsed).
  disk_read_mbps  Read throughput MB/s.
  disk_write_mbps Write throughput MB/s.

Battery
───────
  battery_percent  Battery charge % (0–100).  100 when AC-only device.

Battery saver
─────────────
  get_battery_saver()  Returns True when the system's power-saving mode is active.
  Windows: reads HKLM EnergySaverState registry key.
  Linux:   reads power-profiles-daemon active profile ("power-saver") via D-Bus,
           falling back to upower's "PowerSaveMode" property.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import psutil

log = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

try:
    import GPUtil  # type: ignore
    _GPUTIL_AVAILABLE = True
except ImportError:
    _GPUTIL_AVAILABLE = False

if _IS_WINDOWS:
    try:
        import wmi  # type: ignore
        _WMI_AVAILABLE = True
    except Exception:
        _WMI_AVAILABLE = False
else:
    _WMI_AVAILABLE = False


# ---------------------------------------------------------------------------
# Battery saver / power-save mode detection
# ---------------------------------------------------------------------------

def get_battery_saver() -> bool:
    """Return True if the system's power-saving mode is currently active.

    Windows: reads EnergySaverState registry key under HKLM Control\\Power.
             Value 1 = Battery Saver active.  GetSystemPowerStatus fallback.
    Linux:   queries power-profiles-daemon active profile via D-Bus.
             Falls back to reading UPower PowerSaveMode property.
    """
    if _IS_WINDOWS:
        return _get_battery_saver_windows()
    return _get_battery_saver_linux()


def _get_battery_saver_windows() -> bool:
    import ctypes
    # Primary: registry (GetSystemPowerStatus.SystemStatusFlag is unreliable on Win11)
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Power",
        ) as key:
            val, _ = winreg.QueryValueEx(key, "EnergySaverState")
            return val == 1   # 1=active, 2=configured but off, 0=disabled
    except FileNotFoundError:
        pass
    except Exception as exc:
        log.debug("get_battery_saver registry: %s", exc)

    # Fallback: GetSystemPowerStatus
    try:
        import ctypes.wintypes

        class _SPS(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus",        ctypes.c_ubyte),
                ("BatteryFlag",         ctypes.c_ubyte),
                ("BatteryLifePercent",  ctypes.c_ubyte),
                ("SystemStatusFlag",    ctypes.c_ubyte),
                ("BatteryLifeTime",     ctypes.c_ulong),
                ("BatteryFullLifeTime", ctypes.c_ulong),
            ]
        s = _SPS()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
            return bool(s.SystemStatusFlag & 1)
    except Exception as exc:
        log.debug("get_battery_saver API: %s", exc)

    return False


def _get_battery_saver_linux() -> bool:
    # Primary: power-profiles-daemon via D-Bus
    try:
        import subprocess
        result = subprocess.run(
            ["gdbus", "call", "--system",
             "--dest", "net.hadess.PowerProfiles",
             "--object-path", "/net/hadess/PowerProfiles",
             "--method", "org.freedesktop.DBus.Properties.Get",
             "net.hadess.PowerProfiles", "ActiveProfile"],
            capture_output=True, text=True, timeout=1,
        )
        if result.returncode == 0 and "power-saver" in result.stdout:
            return True
        if result.returncode == 0:
            return False   # daemon responded — not in power-saver
    except Exception as exc:
        log.debug("power-profiles-daemon query: %s", exc)

    # Fallback: UPower PowerSaveMode
    try:
        import subprocess
        result = subprocess.run(
            ["gdbus", "call", "--system",
             "--dest", "org.freedesktop.UPower",
             "--object-path", "/org/freedesktop/UPower",
             "--method", "org.freedesktop.DBus.Properties.Get",
             "org.freedesktop.UPower", "PowerSaveMode"],
            capture_output=True, text=True, timeout=1,
        )
        if result.returncode == 0:
            return "true" in result.stdout.lower()
    except Exception as exc:
        log.debug("upower PowerSaveMode query: %s", exc)

    return False


# ---------------------------------------------------------------------------
# Screen brightness
# ---------------------------------------------------------------------------

_brightness_wmi = None


def init_wmi_brightness() -> None:
    """Windows only: create the per-thread WMI brightness connection."""
    global _brightness_wmi
    if not _IS_WINDOWS or not _WMI_AVAILABLE:
        return
    try:
        _brightness_wmi = wmi.WMI(namespace="root\\wmi")
        log.debug("WMI brightness connection ready")
    except Exception as exc:
        log.warning("init_wmi_brightness failed: %s: %s", type(exc).__name__, exc)
        _brightness_wmi = None


def get_screen_brightness() -> Optional[int]:
    """Return current display brightness 0–100, or None."""
    if _IS_WINDOWS:
        return _get_brightness_windows()
    return _get_brightness_linux()


def _get_brightness_windows() -> Optional[int]:
    if _brightness_wmi is None:
        return None
    try:
        monitors = _brightness_wmi.WmiMonitorBrightness()
        if monitors:
            return int(monitors[0].CurrentBrightness)
    except Exception as exc:
        log.debug("get_screen_brightness: %s", exc)
    return None


def _get_brightness_linux() -> Optional[int]:
    """Read brightness from /sys/class/backlight/*/brightness."""
    import glob
    try:
        paths = glob.glob("/sys/class/backlight/*/brightness")
        if not paths:
            return None
        bl_dir = paths[0].rsplit("/", 1)[0]
        with open(f"{bl_dir}/brightness") as f:
            current = int(f.read().strip())
        with open(f"{bl_dir}/max_brightness") as f:
            maximum = int(f.read().strip())
        if maximum <= 0:
            return None
        return round(current / maximum * 100)
    except Exception as exc:
        log.debug("_get_brightness_linux: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Temperature sensor reader
# ---------------------------------------------------------------------------

class TempSensorReader:
    """
    Reads named temperature sensors and iGPU load.

    Windows:
      Connects to LibreHardwareMonitor / Open Hardware Monitor WMI namespace.
      Must call init() on the service thread after CoInitialize().

    Linux:
      Uses psutil.sensors_temperatures() which reads kernel hwmon entries.
      iGPU busy % is read from /sys/class/drm/card*/device/gpu_busy_percent
      (AMD only; 0 on other hardware without extra tooling).
    """

    # Windows WMI sensor name → stat key mappings
    _NAMESPACES = [
        "root\\LibreHardwareMonitor",
        "root\\OpenHardwareMonitor",
    ]

    _CANDIDATES: Dict[str, List[str]] = {
        "cpu_temp":   ["F75303_CPU", "CPU Package", "CPU Tdie", "Tctl/Tdie"],
        "temp_ddr":   ["F75303_DDR", "T_MEM", "DDR"],
        "temp_local": ["F75303_Local", "Local", "Ambient"],
        "gpu_temp":   ["dGPU AMB", "GPU Core", "GPU"],
    }

    _LOAD_CANDIDATES: Dict[str, List[str]] = {
        "gpu_percent": ["GPU Core", "GPU D3D 3D", "GPU", "D3D 3D"],
    }

    # Linux psutil chip name → stat key (first matching chip label wins)
    _LINUX_CHIP_CANDIDATES: Dict[str, List[str]] = {
        "cpu_temp":   ["k10temp", "coretemp", "acpitz", "cpu_thermal"],
        "temp_ddr":   ["ddr_thermal", "dimm"],
        "temp_local": ["f75303", "nct6775", "it8"],
        "gpu_temp":   ["amdgpu", "radeon", "nouveau"],
    }

    # Linux psutil sensor label → stat key within a chip
    _LINUX_LABEL_CANDIDATES: Dict[str, List[str]] = {
        "cpu_temp":   ["Tctl", "Tdie", "Package id 0", "CPU", "temp1"],
        "temp_ddr":   ["temp1", "DDR"],
        "temp_local": ["temp1", "Local", "Ambient"],
        "gpu_temp":   ["edge", "junction", "temp1"],
    }

    def __init__(self) -> None:
        self._wmi: Any = None
        self._key_to_sensor: Dict[str, str] = {}
        self._key_to_load_sensor: Dict[str, str] = {}
        self._linux_ready = False

    def init(self) -> None:
        """Connect to hardware sensors. Call on service thread (Windows: after CoInitialize)."""
        if _IS_WINDOWS:
            self._init_windows()
        else:
            self._init_linux()

    def _init_windows(self) -> None:
        if not _WMI_AVAILABLE:
            log.debug("TempSensorReader: wmi module not available")
            return
        for ns in self._NAMESPACES:
            try:
                w = wmi.WMI(namespace=ns)
                temp_avail: Dict[str, float] = {}
                load_avail: Dict[str, float] = {}
                for s in w.Sensor():
                    if s.Value is None:
                        continue
                    if s.SensorType == "Temperature":
                        temp_avail[s.Name] = float(s.Value)
                    elif s.SensorType == "Load":
                        load_avail[s.Name] = float(s.Value)

                if not temp_avail and not load_avail:
                    log.debug("%s: no sensors found", ns)
                    continue

                temp_mapping: Dict[str, str] = {}
                for key, candidates in self._CANDIDATES.items():
                    for name in candidates:
                        if name in temp_avail:
                            temp_mapping[key] = name
                            break

                load_mapping: Dict[str, str] = {}
                for key, candidates in self._LOAD_CANDIDATES.items():
                    for name in candidates:
                        if name in load_avail:
                            load_mapping[key] = name
                            break

                if temp_mapping or load_mapping:
                    self._wmi = w
                    self._key_to_sensor = temp_mapping
                    self._key_to_load_sensor = load_mapping
                    log.info(
                        "Hardware sensors: namespace=%s  temps=%s  loads=%s",
                        ns, list(temp_mapping.keys()), list(load_mapping.keys()),
                    )
                    return
                else:
                    log.debug("%s: sensors present but none matched our keys", ns)

            except Exception as exc:
                log.debug("TempSensorReader: cannot connect to %s: %s", ns, exc)

        log.info(
            "No hardware monitor WMI namespace available. "
            "Install LibreHardwareMonitor or Open Hardware Monitor and run it once."
        )

    def _init_linux(self) -> None:
        temps = psutil.sensors_temperatures()
        if not temps:
            log.info(
                "No temperature sensors found via psutil. "
                "Ensure lm-sensors is installed and 'sensors-detect' has been run."
            )
            return
        self._linux_ready = True
        available_chips = list(temps.keys())
        log.info("Linux temperature sensors available: %s", available_chips)

    def read(self) -> Dict[str, float]:
        """Return {stat_key: value} for all mapped sensors (temps °C, loads %)."""
        if _IS_WINDOWS:
            return self._read_windows()
        return self._read_linux()

    def _read_windows(self) -> Dict[str, float]:
        if self._wmi is None:
            return {}
        try:
            temp_live: Dict[str, float] = {}
            load_live: Dict[str, float] = {}
            for s in self._wmi.Sensor():
                if s.Value is None:
                    continue
                if s.SensorType == "Temperature":
                    temp_live[s.Name] = float(s.Value)
                elif s.SensorType == "Load":
                    load_live[s.Name] = float(s.Value)

            result: Dict[str, float] = {}
            for key, name in self._key_to_sensor.items():
                if name in temp_live:
                    result[key] = temp_live[name]
            for key, name in self._key_to_load_sensor.items():
                if name in load_live:
                    result[key] = load_live[name]
            return result
        except Exception as exc:
            log.debug("TempSensorReader._read_windows error: %s", exc)
            return {}

    def _read_linux(self) -> Dict[str, float]:
        result: Dict[str, float] = {}
        try:
            temps = psutil.sensors_temperatures()
            if not temps:
                return result

            for stat_key, chip_candidates in self._LINUX_CHIP_CANDIDATES.items():
                label_candidates = self._LINUX_LABEL_CANDIDATES.get(stat_key, ["temp1"])
                for chip in chip_candidates:
                    if chip not in temps:
                        continue
                    entries = {e.label: e.current for e in temps[chip] if e.current}
                    # try preferred labels first
                    for lbl in label_candidates:
                        if lbl in entries:
                            result[stat_key] = entries[lbl]
                            break
                    else:
                        # fall back to first sensor on this chip
                        first = next(iter(entries.values()), None)
                        if first is not None:
                            result[stat_key] = first
                    if stat_key in result:
                        break

            # iGPU busy % — AMD exposes via DRM sysfs
            gpu_pct = _read_amd_igpu_busy()
            if gpu_pct is not None:
                result["gpu_percent"] = gpu_pct

        except Exception as exc:
            log.debug("TempSensorReader._read_linux error: %s", exc)
        return result

    @property
    def mapped_keys(self) -> List[str]:
        if _IS_WINDOWS:
            return list(self._key_to_sensor.keys()) + list(self._key_to_load_sensor.keys())
        temps = psutil.sensors_temperatures() if self._linux_ready else {}
        keys = []
        for stat_key, chip_candidates in self._LINUX_CHIP_CANDIDATES.items():
            if any(c in temps for c in chip_candidates):
                keys.append(stat_key)
        if _read_amd_igpu_busy() is not None:
            keys.append("gpu_percent")
        return keys


def _read_amd_igpu_busy() -> Optional[float]:
    """Read AMD iGPU busy % from DRM sysfs. Returns None if unavailable."""
    import glob
    try:
        paths = glob.glob("/sys/class/drm/card*/device/gpu_busy_percent")
        if paths:
            with open(paths[0]) as f:
                return float(f.read().strip())
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# System stats dataclass
# ---------------------------------------------------------------------------

@dataclass
class SystemStats:
    """Snapshot of all system metrics."""
    cpu_percent: float = 0.0
    cpu_cores: List[float] = field(default_factory=list)
    ram_percent: float = 0.0
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    disk_percent: float = 0.0
    disk_read_mbps: float = 0.0
    disk_write_mbps: float = 0.0
    gpu_percent: float = 0.0
    gpu_vram_percent: float = 0.0
    gpu_temp_c: float = 0.0
    cpu_temp_c: float = 0.0
    temp_ddr_c: float = 0.0
    temp_local_c: float = 0.0
    net_sent_mbps: float = 0.0
    net_recv_mbps: float = 0.0
    battery_percent: float = 100.0


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

class StatsCollector:
    """Collects system statistics each tick. Call .collect()."""

    def __init__(self) -> None:
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(percpu=True, interval=None)

        now = time.monotonic()

        net = psutil.net_io_counters()
        self._prev_net_sent = net.bytes_sent
        self._prev_net_recv = net.bytes_recv

        try:
            disk = psutil.disk_io_counters()
            self._prev_disk_read_bytes  = disk.read_bytes
            self._prev_disk_write_bytes = disk.write_bytes
            self._prev_disk_read_ms     = disk.read_time
            self._prev_disk_write_ms    = disk.write_time
            self._disk_available = True
        except Exception:
            self._prev_disk_read_bytes  = 0
            self._prev_disk_write_bytes = 0
            self._prev_disk_read_ms     = 0
            self._prev_disk_write_ms    = 0
            self._disk_available = False

        self._prev_io_ts = now
        self._temp_reader = TempSensorReader()
        self._temp_reader.init()

    def _cpu(self) -> tuple[float, List[float]]:
        return (psutil.cpu_percent(interval=None),
                psutil.cpu_percent(percpu=True, interval=None))

    def _ram(self) -> tuple[float, float, float]:
        vm = psutil.virtual_memory()
        return vm.percent, vm.used / 1e9, vm.total / 1e9

    def _disk(self, dt: float) -> tuple[float, float, float]:
        if not self._disk_available or dt <= 0:
            return 0.0, 0.0, 0.0
        try:
            d = psutil.disk_io_counters()
            read_mb  = (d.read_bytes  - self._prev_disk_read_bytes)  / 1e6 / dt
            write_mb = (d.write_bytes - self._prev_disk_write_bytes) / 1e6 / dt
            dt_ms    = dt * 1000.0
            busy_ms  = ((d.read_time  - self._prev_disk_read_ms)
                      + (d.write_time - self._prev_disk_write_ms))
            activity = min(100.0, max(0.0, busy_ms / dt_ms * 100.0))
            self._prev_disk_read_bytes  = d.read_bytes
            self._prev_disk_write_bytes = d.write_bytes
            self._prev_disk_read_ms     = d.read_time
            self._prev_disk_write_ms    = d.write_time
            return activity, max(0.0, read_mb), max(0.0, write_mb)
        except Exception as exc:
            log.debug("disk_io_counters: %s", exc)
            return 0.0, 0.0, 0.0

    def _gpu(self) -> tuple[float, float, float]:
        if _GPUTIL_AVAILABLE:
            try:
                gpus = GPUtil.getGPUs()
                if gpus:
                    g = gpus[0]
                    load = (g.load or 0) * 100
                    vram = (g.memoryUsed / g.memoryTotal * 100) if g.memoryTotal else 0
                    return load, vram, g.temperature or 0
            except Exception:
                pass
        return 0.0, 0.0, 0.0

    def _net(self, dt: float) -> tuple[float, float]:
        if dt <= 0:
            return 0.0, 0.0
        net  = psutil.net_io_counters()
        sent = (net.bytes_sent - self._prev_net_sent) * 8 / 1e6 / dt
        recv = (net.bytes_recv - self._prev_net_recv) * 8 / 1e6 / dt
        self._prev_net_sent = net.bytes_sent
        self._prev_net_recv = net.bytes_recv
        return max(0.0, sent), max(0.0, recv)

    def _battery(self) -> float:
        try:
            b = psutil.sensors_battery()
            if b is not None:
                return max(0.0, min(100.0, b.percent))
        except Exception:
            pass
        return 100.0

    def collect(self) -> SystemStats:
        now = time.monotonic()
        dt  = max(0.001, now - self._prev_io_ts)
        self._prev_io_ts = now

        cpu, cores               = self._cpu()
        ram_pct, ram_used, ram_total = self._ram()
        disk_act, disk_r, disk_w = self._disk(dt)
        gpu_load, gpu_vram, gpu_temp = self._gpu()
        sent, recv               = self._net(dt)
        battery                  = self._battery()

        hw = self._temp_reader.read()
        cpu_temp   = hw.get("cpu_temp",    0.0)
        temp_ddr   = hw.get("temp_ddr",    0.0)
        temp_local = hw.get("temp_local",  0.0)
        _gpu_temp  = hw.get("gpu_temp",    gpu_temp)
        _gpu_load  = hw.get("gpu_percent", gpu_load)

        return SystemStats(
            cpu_percent     = cpu,
            cpu_cores       = cores,
            ram_percent     = ram_pct,
            ram_used_gb     = ram_used,
            ram_total_gb    = ram_total,
            disk_percent    = disk_act,
            disk_read_mbps  = disk_r,
            disk_write_mbps = disk_w,
            gpu_percent     = _gpu_load,
            gpu_vram_percent= gpu_vram,
            gpu_temp_c      = _gpu_temp,
            cpu_temp_c      = cpu_temp,
            temp_ddr_c      = temp_ddr,
            temp_local_c    = temp_local,
            net_sent_mbps   = sent,
            net_recv_mbps   = recv,
            battery_percent = battery,
        )
