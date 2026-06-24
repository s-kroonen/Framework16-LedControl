"""
System metric collectors.

Temperature sensors
───────────────────
Temperature reading requires LibreHardwareMonitor or Open Hardware Monitor
to be running.  The service connects to their WMI namespace at startup and
maps sensor names to stat keys:

  cpu_temp   ← F75303_CPU  (APU die, most accurate)
  temp_ddr   ← F75303_DDR  (memory temperature)
  temp_local ← F75303_Local (ambient near F75303 chip)
  gpu_temp   ← dGPU AMB / GPU Core (zero if no dGPU installed)

iGPU load is also read from LibreHardwareMonitor / OHM when available.
Without a hardware monitor running, temperature and iGPU stats return 0.

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
  get_battery_saver()  Returns True when Windows Battery Saver is active.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import psutil

log = logging.getLogger(__name__)

try:
    import GPUtil  # type: ignore
    _GPUTIL_AVAILABLE = True
except ImportError:
    _GPUTIL_AVAILABLE = False

try:
    import wmi  # type: ignore
    _WMI_AVAILABLE = True
except Exception:
    _WMI_AVAILABLE = False


# ---------------------------------------------------------------------------
# Battery saver detection  (Windows only, zero-dependency)
# ---------------------------------------------------------------------------

class _SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus",       ctypes.c_byte),
        ("BatteryFlag",        ctypes.c_byte),
        ("BatteryLifePercent", ctypes.c_byte),
        ("SystemStatusFlag",   ctypes.c_byte),   # bit 0 = Battery Saver on
        ("BatteryLifeTime",    ctypes.c_ulong),
        ("BatteryFullLifeTime", ctypes.c_ulong),
    ]


def get_battery_saver() -> bool:
    """Return True if Windows Battery Saver mode is currently active."""
    try:
        status = _SYSTEM_POWER_STATUS()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return bool(status.SystemStatusFlag & 1)
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Screen brightness  (WMI root\wmi — must call init_wmi_brightness() first)
# ---------------------------------------------------------------------------

_brightness_wmi = None


def init_wmi_brightness() -> None:
    """
    Create the per-thread WMI brightness connection.
    Call once at thread start after pythoncom.CoInitialize().
    """
    global _brightness_wmi
    if not _WMI_AVAILABLE:
        return
    try:
        _brightness_wmi = wmi.WMI(namespace="root\\wmi")
        log.debug("WMI brightness connection ready")
    except Exception as exc:
        log.warning("init_wmi_brightness failed: %s: %s", type(exc).__name__, exc)
        _brightness_wmi = None


def get_screen_brightness() -> Optional[int]:
    """Return current display brightness 0–100, or None."""
    if _brightness_wmi is None:
        return None
    try:
        monitors = _brightness_wmi.WmiMonitorBrightness()
        if monitors:
            return int(monitors[0].CurrentBrightness)
    except Exception as exc:
        log.debug("get_screen_brightness: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Hardware monitor temperature sensors
# ---------------------------------------------------------------------------

class TempSensorReader:
    """
    Reads named temperature sensors from LibreHardwareMonitor or
    Open Hardware Monitor via their WMI namespace.

    Must call init() on the service thread after CoInitialize().

    Sensor-to-stat-key mapping (first matching name wins):
      cpu_temp   → F75303_CPU, CPU Package, CPU Tdie, Tctl/Tdie
      temp_ddr   → F75303_DDR, T_MEM, DDR
      temp_local → F75303_Local, Local, Ambient
      gpu_temp   → dGPU AMB, GPU Core, GPU
    """

    _NAMESPACES = [
        "root\\LibreHardwareMonitor",
        "root\\OpenHardwareMonitor",
    ]

    # Ordered candidate names for each Temperature sensor key
    _CANDIDATES: Dict[str, List[str]] = {
        "cpu_temp":   ["F75303_CPU", "CPU Package", "CPU Tdie", "Tctl/Tdie"],
        "temp_ddr":   ["F75303_DDR", "T_MEM", "DDR"],
        "temp_local": ["F75303_Local", "Local", "Ambient"],
        "gpu_temp":   ["dGPU AMB", "GPU Core", "GPU"],
    }

    # Ordered candidate names for Load-type sensor keys (iGPU, etc.)
    _LOAD_CANDIDATES: Dict[str, List[str]] = {
        "gpu_percent": ["GPU Core", "GPU D3D 3D", "GPU", "D3D 3D"],
    }

    def __init__(self) -> None:
        self._wmi: Any = None
        # stat_key -> exact sensor name in WMI (Temperature sensors)
        self._key_to_sensor: Dict[str, str] = {}
        # stat_key -> exact sensor name in WMI (Load sensors)
        self._key_to_load_sensor: Dict[str, str] = {}

    def init(self) -> None:
        """Connect to hardware monitor WMI namespace. Call on service thread."""
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

                # Build temperature key → sensor-name map
                temp_mapping: Dict[str, str] = {}
                for key, candidates in self._CANDIDATES.items():
                    for name in candidates:
                        if name in temp_avail:
                            temp_mapping[key] = name
                            break

                # Build load key → sensor-name map
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
                        ns,
                        list(temp_mapping.keys()),
                        list(load_mapping.keys()),
                    )
                    return
                else:
                    log.debug("%s: sensors present but none matched our keys", ns)

            except Exception as exc:
                log.debug("TempSensorReader: cannot connect to %s: %s", ns, exc)

        log.info(
            "No hardware monitor WMI namespace available.  "
            "Install LibreHardwareMonitor or Open Hardware Monitor and "
            "run it once to expose sensors."
        )

    def read(self) -> Dict[str, float]:
        """Return {stat_key: value} for all mapped sensors (temps in °C, loads in %)."""
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
            log.debug("TempSensorReader.read error: %s", exc)
            return {}

    @property
    def mapped_keys(self) -> List[str]:
        return list(self._key_to_sensor.keys()) + list(self._key_to_load_sensor.keys())


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
    disk_percent: float = 0.0       # I/O busy %
    disk_read_mbps: float = 0.0
    disk_write_mbps: float = 0.0
    gpu_percent: float = 0.0        # iGPU load % (from LHM) or dGPU (GPUtil)
    gpu_vram_percent: float = 0.0
    gpu_temp_c: float = 0.0         # dGPU AMB or GPU Core
    cpu_temp_c: float = 0.0         # F75303_CPU / APU die
    temp_ddr_c: float = 0.0         # F75303_DDR / memory
    temp_local_c: float = 0.0       # F75303_Local / ambient
    net_sent_mbps: float = 0.0
    net_recv_mbps: float = 0.0
    battery_percent: float = 100.0  # battery charge % (100 when on AC)


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

class StatsCollector:
    """Collects system statistics each tick.  Call .collect()."""

    def __init__(self) -> None:
        # Seed CPU counters (first call always returns 0.0)
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(percpu=True, interval=None)

        now = time.monotonic()

        # Network baseline
        net = psutil.net_io_counters()
        self._prev_net_sent = net.bytes_sent
        self._prev_net_recv = net.bytes_recv

        # Disk baseline
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

        # Hardware temperature sensors (LibreHardwareMonitor / OHM)
        self._temp_reader = TempSensorReader()
        self._temp_reader.init()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _cpu(self) -> tuple[float, List[float]]:
        return psutil.cpu_percent(interval=None), \
               psutil.cpu_percent(percpu=True, interval=None)

    def _ram(self) -> tuple[float, float, float]:
        vm = psutil.virtual_memory()
        return vm.percent, vm.used / 1e9, vm.total / 1e9

    def _disk(self, dt: float) -> tuple[float, float, float]:
        """Returns (busy_pct, read_mbps, write_mbps)."""
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

    # ------------------------------------------------------------------

    def _battery(self) -> float:
        """Return battery charge % (0–100), or 100.0 when on AC without battery."""
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

        cpu, cores              = self._cpu()
        ram_pct, ram_used, ram_total = self._ram()
        disk_act, disk_r, disk_w     = self._disk(dt)
        gpu_load, gpu_vram, gpu_temp = self._gpu()
        sent, recv               = self._net(dt)
        battery                  = self._battery()

        # Hardware monitor sensors (temps + iGPU load if LHM/OHM running)
        hw = self._temp_reader.read()
        cpu_temp   = hw.get("cpu_temp",    0.0)
        temp_ddr   = hw.get("temp_ddr",    0.0)
        temp_local = hw.get("temp_local",  0.0)
        _gpu_temp  = hw.get("gpu_temp",    gpu_temp)   # prefer HW monitor
        _gpu_load  = hw.get("gpu_percent", gpu_load)   # iGPU load from LHM

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
