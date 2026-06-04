"""
System metric collectors.

All public functions return values in the range 0.0–100.0 (percentage)
unless otherwise documented.  GPU support falls back gracefully when
no supported GPU is present.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import psutil

log = logging.getLogger(__name__)

# Optional GPU support via GPUtil (NVIDIA) or WMI (fallback)
try:
    import GPUtil  # type: ignore
    _GPUTIL_AVAILABLE = True
except ImportError:
    _GPUTIL_AVAILABLE = False

try:
    import wmi  # type: ignore  (pywin32)
    _WMI_AVAILABLE = True
except Exception:
    _WMI_AVAILABLE = False


def get_screen_brightness() -> Optional[int]:
    """
    Return the current Windows display brightness (0–100), or None if
    unavailable (desktop PC with no supported backlight, or pywin32 missing).

    Uses the WMI root\\wmi namespace which works for most laptop displays.
    """
    if not _WMI_AVAILABLE:
        return None
    try:
        w = wmi.WMI(namespace="root\\wmi")
        monitors = w.WmiMonitorBrightness()
        if monitors:
            return int(monitors[0].CurrentBrightness)
    except Exception:
        pass
    return None


@dataclass
class SystemStats:
    """Snapshot of system metrics at a point in time."""
    cpu_percent: float = 0.0          # overall CPU %
    cpu_cores: List[float] = field(default_factory=list)  # per-core %
    ram_percent: float = 0.0
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    disk_percent: float = 0.0         # root disk usage %
    gpu_percent: float = 0.0          # GPU load %
    gpu_vram_percent: float = 0.0     # GPU VRAM %
    gpu_temp_c: float = 0.0           # GPU temperature °C
    cpu_temp_c: float = 0.0           # CPU package temperature °C (if available)
    net_sent_mbps: float = 0.0        # current TX throughput Mbit/s
    net_recv_mbps: float = 0.0        # current RX throughput Mbit/s


class StatsCollector:
    """Collects system statistics.  Call .collect() on each tick."""

    def __init__(self) -> None:
        # Seed the psutil CPU and network counters so the first real
        # call returns a meaningful delta rather than 0.
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(percpu=True, interval=None)
        net = psutil.net_io_counters()
        self._prev_net_sent = net.bytes_sent
        self._prev_net_recv = net.bytes_recv
        self._prev_net_ts = time.monotonic()

        self._wmi_obj = None
        if _WMI_AVAILABLE:
            try:
                self._wmi_obj = wmi.WMI(namespace="root\\OpenHardwareMonitor")
            except Exception:
                try:
                    self._wmi_obj = wmi.WMI()
                except Exception:
                    self._wmi_obj = None

    # ------------------------------------------------------------------
    # CPU
    # ------------------------------------------------------------------

    def _cpu(self) -> tuple[float, List[float]]:
        overall = psutil.cpu_percent(interval=None)
        cores = psutil.cpu_percent(percpu=True, interval=None)
        return overall, cores

    def _cpu_temp(self) -> float:
        """Try to get CPU package temperature via psutil sensors or WMI."""
        try:
            temps = psutil.sensors_temperatures()
            for key in ("coretemp", "k10temp", "cpu_thermal", "acpitz"):
                if key in temps:
                    entries = [e for e in temps[key] if "package" in e.label.lower() or e.label == ""]
                    if entries:
                        return entries[0].current
        except (AttributeError, Exception):
            pass
        return 0.0

    # ------------------------------------------------------------------
    # RAM
    # ------------------------------------------------------------------

    def _ram(self) -> tuple[float, float, float]:
        vm = psutil.virtual_memory()
        return vm.percent, vm.used / 1e9, vm.total / 1e9

    # ------------------------------------------------------------------
    # Disk
    # ------------------------------------------------------------------

    def _disk(self) -> float:
        try:
            return psutil.disk_usage("/").percent
        except Exception:
            try:
                return psutil.disk_usage("C:\\").percent
            except Exception:
                return 0.0

    # ------------------------------------------------------------------
    # GPU
    # ------------------------------------------------------------------

    def _gpu(self) -> tuple[float, float, float]:
        """Returns (load_pct, vram_pct, temp_c). Falls back to 0 on error."""
        if _GPUTIL_AVAILABLE:
            try:
                gpus = GPUtil.getGPUs()
                if gpus:
                    g = gpus[0]
                    load = (g.load or 0) * 100
                    vram = (g.memoryUsed / g.memoryTotal * 100) if g.memoryTotal else 0
                    temp = g.temperature or 0
                    return load, vram, temp
            except Exception:
                pass
        return 0.0, 0.0, 0.0

    # ------------------------------------------------------------------
    # Network
    # ------------------------------------------------------------------

    def _net(self) -> tuple[float, float]:
        """Returns (sent_mbps, recv_mbps) since last call."""
        net = psutil.net_io_counters()
        now = time.monotonic()
        dt = now - self._prev_net_ts
        if dt <= 0:
            return 0.0, 0.0
        sent_mbps = (net.bytes_sent - self._prev_net_sent) * 8 / 1e6 / dt
        recv_mbps = (net.bytes_recv - self._prev_net_recv) * 8 / 1e6 / dt
        self._prev_net_sent = net.bytes_sent
        self._prev_net_recv = net.bytes_recv
        self._prev_net_ts = now
        return max(0.0, sent_mbps), max(0.0, recv_mbps)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def collect(self) -> SystemStats:
        """Collect and return a fresh SystemStats snapshot."""
        cpu, cores = self._cpu()
        ram_pct, ram_used, ram_total = self._ram()
        gpu_load, gpu_vram, gpu_temp = self._gpu()
        sent, recv = self._net()
        return SystemStats(
            cpu_percent=cpu,
            cpu_cores=cores,
            ram_percent=ram_pct,
            ram_used_gb=ram_used,
            ram_total_gb=ram_total,
            disk_percent=self._disk(),
            gpu_percent=gpu_load,
            gpu_vram_percent=gpu_vram,
            gpu_temp_c=gpu_temp,
            cpu_temp_c=self._cpu_temp(),
            net_sent_mbps=sent,
            net_recv_mbps=recv,
        )
