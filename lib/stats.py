"""Hardware stats pulled from /proc, shutil, and vcgencmd.

Returns a flat dict suitable for `pictl stats`. Every field has a
sensible fallback so a missing file or missing vcgencmd never takes the
whole command down.

CPU sampling: a naive implementation sleeps 0.5 s on every call to
diff /proc/stat against itself. For a UI polling once a second that's
half the wall time. Instead we cache the last (idle, total, mono_ts)
sample to a file under PICTL_HOME and diff against it. If the cache is
missing or > CPU_CACHE_MAX_AGE_S old, we fall back to the slow path.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from . import storage


CPU_CACHE_PATH = storage.DATA_DIR / ".cpu-sample.json"
CPU_CACHE_MAX_AGE_S = 30.0
CPU_SLOW_SAMPLE_S = 0.5


def _read_proc_stat() -> tuple[int, int]:
    """Return (idle, total) jiffies from /proc/stat's first line."""
    with open("/proc/stat", "r", encoding="utf-8") as f:
        parts = f.readline().split()
    # parts[0] == "cpu"; fields: user, nice, system, idle, iowait, irq, softirq, steal
    values = [int(x) for x in parts[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)  # idle + iowait
    total = sum(values)
    return idle, total


def _read_cpu_cache() -> tuple[int, int, float] | None:
    try:
        with open(CPU_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return int(data["idle"]), int(data["total"]), float(data["mono"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None


def _write_cpu_cache(idle: int, total: int, mono: float) -> None:
    storage.ensure_dirs()
    try:
        with open(CPU_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({"idle": idle, "total": total, "mono": mono}, f)
    except OSError:
        pass


def cpu_percent() -> float:
    """Diff against a cached prior sample for sub-millisecond reads.

    Falls back to the legacy 0.5 s sleep if no usable cache exists.
    """
    try:
        idle_now, total_now = _read_proc_stat()
        mono_now = time.monotonic()
        cache = _read_cpu_cache()
        try:
            if cache:
                idle_prev, total_prev, mono_prev = cache
                age = mono_now - mono_prev
                if 0 < age <= CPU_CACHE_MAX_AGE_S:
                    dt = total_now - total_prev
                    di = idle_now - idle_prev
                    if dt > 0:
                        return round(100.0 * (1.0 - di / dt), 1)
            # Cache missing/stale/zero-delta: take a fresh slow sample.
            time.sleep(CPU_SLOW_SAMPLE_S)
            idle2, total2 = _read_proc_stat()
            dt = total2 - total_now
            di = idle2 - idle_now
            idle_now, total_now, mono_now = idle2, total2, time.monotonic()
            if dt <= 0:
                return 0.0
            return round(100.0 * (1.0 - di / dt), 1)
        finally:
            _write_cpu_cache(idle_now, total_now, mono_now)
    except (OSError, ValueError):
        return 0.0


def _parse_meminfo() -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                key, _, rest = line.partition(":")
                value = rest.strip().split()
                if value:
                    try:
                        out[key.strip()] = int(value[0])  # kB
                    except ValueError:
                        pass
    except OSError:
        pass
    return out


def ram_usage() -> tuple[float, float]:
    """Return (used_gb, total_gb). Used = Total - MemAvailable."""
    mem = _parse_meminfo()
    total_kb = mem.get("MemTotal", 0)
    avail_kb = mem.get("MemAvailable", mem.get("MemFree", 0))
    used_kb = max(total_kb - avail_kb, 0)
    gb = 1024 * 1024  # kB -> GB
    return round(used_kb / gb, 2), round(total_kb / gb, 2)


def disk_usage(path: str = "/") -> tuple[float, float]:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return 0.0, 0.0
    gb = 1024 ** 3
    return round(usage.used / gb, 1), round(usage.total / gb, 1)


def temperature_celsius() -> float | None:
    """Try vcgencmd first, then /sys/class/thermal."""
    try:
        out = subprocess.run(
            ["vcgencmd", "measure_temp"],
            capture_output=True, text=True, timeout=2, check=True,
        ).stdout.strip()
        # e.g. "temp=52.3'C"
        if "=" in out:
            val = out.split("=", 1)[1].split("'", 1)[0]
            return round(float(val), 1)
    except (FileNotFoundError, subprocess.SubprocessError, ValueError):
        pass

    thermal = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        millis = int(thermal.read_text(encoding="utf-8").strip())
        return round(millis / 1000.0, 1)
    except (OSError, ValueError):
        return None


def uptime_seconds() -> int:
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as f:
            return int(float(f.read().split()[0]))
    except (OSError, ValueError, IndexError):
        return 0


def active_session_count(sessions_data: dict[str, Any] | None = None) -> int:
    """Count sessions whose status is 'running' AND whose PID is alive.

    Pass `sessions_data` (the result of `storage.read_sessions()`) to
    avoid an extra read+lock when called from `collect()`.
    """
    if sessions_data is None:
        sessions_data = storage.read_sessions()
    n = 0
    for s in sessions_data.get("sessions", []):
        if s.get("status") == "running" and storage.pid_alive(s.get("pid") or 0):
            n += 1
    return n


def collect() -> dict[str, Any]:
    ram_used, ram_total = ram_usage()
    disk_used, disk_total = disk_usage("/")
    sessions_data = storage.read_sessions()
    return {
        "cpu_percent": cpu_percent(),
        "ram_used_gb": ram_used,
        "ram_total_gb": ram_total,
        "disk_used_gb": disk_used,
        "disk_total_gb": disk_total,
        "temp_celsius": temperature_celsius(),
        "uptime_seconds": uptime_seconds(),
        "active_sessions": active_session_count(sessions_data),
    }
