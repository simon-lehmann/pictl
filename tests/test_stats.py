"""Stats: CPU caching, sane fallbacks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib import stats, storage


def test_collect_returns_expected_keys(pictl_home: Path):
    out = stats.collect()
    expected = {
        "cpu_percent", "ram_used_gb", "ram_total_gb",
        "disk_used_gb", "disk_total_gb", "temp_celsius",
        "uptime_seconds", "active_sessions",
    }
    assert expected.issubset(out.keys())


def test_cpu_percent_writes_cache(pictl_home: Path):
    if not Path("/proc/stat").exists():
        pytest.skip("/proc/stat not available on this platform")
    stats.cpu_percent()
    assert stats.CPU_CACHE_PATH.exists()
    cached = json.loads(stats.CPU_CACHE_PATH.read_text())
    assert {"idle", "total", "mono"}.issubset(cached.keys())


def test_cpu_percent_uses_cache_for_fast_second_call(pictl_home: Path, monkeypatch):
    if not Path("/proc/stat").exists():
        pytest.skip("/proc/stat not available on this platform")
    stats.cpu_percent()  # warm cache

    # If the cache path is taken, no sleep should be invoked.
    sleeps: list[float] = []
    monkeypatch.setattr(stats.time, "sleep", lambda s: sleeps.append(s))
    stats.cpu_percent()
    assert sleeps == [], f"unexpected sleep calls: {sleeps}"


def test_active_session_count_uses_passed_data(pictl_home: Path, monkeypatch):
    """Passing sessions_data should avoid re-reading from disk."""
    reads = {"n": 0}
    real_read = storage.read_sessions

    def counting_read():
        reads["n"] += 1
        return real_read()

    monkeypatch.setattr(storage, "read_sessions", counting_read)
    data = {"sessions": []}
    stats.active_session_count(data)
    assert reads["n"] == 0


def test_active_session_count_only_counts_alive_running(pictl_home: Path):
    import os
    data = {"sessions": [
        {"id": "a", "status": "running", "pid": os.getpid()},
        {"id": "b", "status": "running", "pid": 99999999},  # likely dead
        {"id": "c", "status": "stopped", "pid": os.getpid()},
    ]}
    assert stats.active_session_count(data) == 1


def test_ram_usage_returns_sane_pair(pictl_home: Path):
    used, total = stats.ram_usage()
    assert used >= 0
    assert total >= used
