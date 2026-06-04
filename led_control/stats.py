"""
System metric collectors.

All public functions return values in the range 0.0–100.0 (percentage)
unless otherwise documented.  GPU support falls back gracefully when
no supported GPU is present.

Disk metrics
────────────
  disk_percent    I/O activity % (0–100).  Calculated from the
                  read_time + write_time deltas reported by
                  psutil.disk_io_counters() over each tick interval.
                  This matches what Windows Task Manager shows in the
                  "Disk" column — how busy the disk was, not how full it is.

  disk_read_mbps  Read throughput in MB/s since last tick.
  disk_write_mbps Write throughput in MB/s since last tick.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional

import psutil

log = logging.getLogger(__name__)

# Optional GPU support
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


def get_screen_brightness() -> Optional[int]:
    """
    Return the current Windows display brightness (0–100), or None if
    unavailable (desktop PC / missing backlight driver / WMI not installed).
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
    cpu_cores: List[float] = field(default_factory=list)
    ram_percent: float = 0.0
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    disk_percent: float = 0.0         # disk I/O activity % (NOT space used)
    disk_read_mbps: float = 0.0       # disk read throughput MB/s
    disk_write_mbps: float = 0.0      # disk write throughput MB/s
    gpu_percent: float = 0.0
    gpu_vram_percent: float = 0.0
    gpu_temp_c: float = 0.0
    cpu_temp_c: float = 0.0
    net_sent_mbps: float = 0.0        # TX Mbit/s
    net_recv_mbps: float = 0.0        # RX Mbit/s


class StatsCollector:
    """Collects system statistics.  Call .collect() on each tick."""

    def __init__(self) -> None:
        # Seed CPU counters
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(percpu=True, interval=None)

        now = time.monotonic()

        # Seed network counters
        net = psutil.net_io_counters()
        self._prev_net_sent = net.bytes_sent
        self._prev_net_recv = net.bytes_recv

        # Seed disk counters
        try:
            disk = psutil.disk_io_counters()
            self._prev_disk_read_bytes = disk.read_bytes
            self._prev_disk_write_bytes = disk.write_bytes
            self._prev_disk_read_ms = disk.read_time
            self._prev_disk_write_ms = disk.write_time
            self._disk_available = True
        except Exception:
            self._prev_disk_read_bytes = 0
            self._prev_disk_write_bytes = 0
            self._prev_disk_read_ms = 0
            self._prev_disk_write_ms = 0
            self._disk_available = False

        self._prev_io_ts = now

    # ------------------------------------------------------------------
    # CPU
    # ------------------------------------------------------------------

    def _cpu(self) -> tuple[float, List[float]]:
        overall = psutil.cpu_percent(interval=None)
        cores = psutil.cpu_percent(percpu=True, interval=None)
        return overall, cores

    def _cpu_temp(self) -> float:
        try:
            temps = psutil.sensors_temperatures()
            for key in ("coretemp", "k10temp", "cpu_thermal", "acpitz"):
                if key in temps:
                    entries = [
                        e for e in temps[key]
                        if "package" in e.label.lower() or e.label == ""
                    ]
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
    # Disk  (I/O activity + throughput)
    # ------------------------------------------------------------------

    def _disk(self, dt: float) -> tuple[float, float, float]:
        """
        Returns (activity_pct, read_mbps, write_mbps).

        activity_pct — fraction of the tick interval the disk was busy
                       (read_time + write_time delta) / (dt × 1000 ms), × 100.
                       Capped at 100 %.  Mirrors Task Manager's Disk column.

        read_mbps / write_mbps — throughput in MB/s (not Mbit/s; disk
                       transfer rates are conventionally in MB/s).
        """
        if not self._disk_available or dt <= 0:
            return 0.0, 0.0, 0.0
        try:
            disk = psutil.disk_io_counters()

            # Throughput
            read_mb = (disk.read_bytes - self._prev_disk_read_bytes) / 1e6 / dt
            write_mb = (disk.write_bytes - self._prev_disk_write_bytes) / 1e6 / dt

            # Activity %: time spent doing I/O vs elapsed time
            dt_ms = dt * 1000.0
            busy_ms = (
                (disk.read_time - self._prev_disk_read_ms)
                + (disk.write_time - self._prev_disk_write_ms)
            )
            activity = min(100.0, max(0.0, busy_ms / dt_ms * 100.0))

            # Update counters
            self._prev_disk_read_bytes = disk.read_bytes
            self._prev_disk_write_bytes = disk.write_bytes
            self._prev_disk_read_ms = disk.read_time
            self._prev_disk_write_ms = disk.write_time

            return activity, max(0.0, read_mb), max(0.0, write_mb)
        except Exception as exc:
            log.debug("disk_io_counters error: %s", exc)
            return 0.0, 0.0, 0.0

    # ------------------------------------------------------------------
    # GPU
    # ------------------------------------------------------------------

    def _gpu(self) -> tuple[float, float, float]:
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

    def _net(self, dt: float) -> tuple[float, float]:
        """Returns (sent_mbps, recv_mbps) in Mbit/s."""
        if dt <= 0:
            return 0.0, 0.0
        net = psutil.net_io_counters()
        sent = (net.bytes_sent - self._prev_net_sent) * 8 / 1e6 / dt
        recv = (net.bytes_recv - self._prev_net_recv) * 8 / 1e6 / dt
        self._prev_net_sent = net.bytes_sent
        self._prev_net_recv = net.bytes_recv
        return max(0.0, sent), max(0.0, recv)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def collect(self) -> SystemStats:
        """Collect and return a fresh SystemStats snapshot."""
        now = time.monotonic()
        dt = now - self._prev_io_ts
        self._prev_io_ts = now

        cpu, cores = self._cpu()
        ram_pct, ram_used, ram_total = self._ram()
        disk_act, disk_read, disk_write = self._disk(dt)
        gpu_load, gpu_vram, gpu_temp = self._gpu()
        sent, recv = self._net(dt)

        return SystemStats(
            cpu_percent=cpu,
            cpu_cores=cores,
            ram_percent=ram_pct,
            ram_used_gb=ram_used,
            ram_total_gb=ram_total,
            disk_percent=disk_act,
            disk_read_mbps=disk_read,
            disk_write_mbps=disk_write,
            gpu_percent=gpu_load,
            gpu_vram_percent=gpu_vram,
            gpu_temp_c=gpu_temp,
            cpu_temp_c=self._cpu_temp(),
            net_sent_mbps=sent,
            net_recv_mbps=recv,
        )
