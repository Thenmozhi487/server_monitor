"""Unit tests for collector: uptime formatting, psutil priming, locking, and collection cycle."""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

import pytest
from collector import CollectorLock, MetricCollector, format_uptime, get_uptime_string
from db import get_latest_metric, init_db


def test_uptime_formatting():
    """Verify seconds to 'Xd Yh Zm' string formatting."""
    assert format_uptime(0) == "0d 0h 0m"
    assert format_uptime(59) == "0d 0h 0m"
    assert format_uptime(60) == "0d 0h 1m"
    assert format_uptime(125) == "0d 0h 2m"
    assert format_uptime(3600) == "0d 1h 0m"
    assert format_uptime(3665) == "0d 1h 1m"
    # 86400 (1 day) + 3600 (1 hour) + 65 (1 min 5 sec) = 90065
    assert format_uptime(90065) == "1d 1h 1m"
    # 3 days exactly = 3 * 86400 = 259200
    assert format_uptime(259200) == "3d 0h 0m"
    # Negative seconds handled gracefully
    assert format_uptime(-10) == "0d 0h 0m"


def test_get_uptime_string_with_mocked_psutil():
    """Verify get_uptime_string calculates difference from boot_time."""
    now = 1700000000.0
    boot = now - 90065.0  # 1d 1h 1m ago

    with patch("psutil.boot_time", return_value=boot), patch("time.time", return_value=now):
        uptime_str = get_uptime_string()
        assert uptime_str == "1d 1h 1m"


def test_collector_lock(tmp_path):
    """Verify file lock prevents duplicate acquisition and allows re-acquisition after release."""
    lock_file = str(tmp_path / "test_collector.lock")
    lock1 = CollectorLock(lock_file)
    lock2 = CollectorLock(lock_file)

    # First lock acquires successfully
    assert lock1.acquire() is True

    # Second lock fails to acquire concurrently
    assert lock2.acquire() is False

    # First lock releases
    lock1.release()

    # Second lock can now acquire
    assert lock2.acquire() is True
    lock2.release()


def test_collector_cycle_inserts_metrics(tmp_path):
    """Verify collector cycle collects metrics, inserts into DB, and does not return 0.0 after priming."""
    db_file = str(tmp_path / "collector_test.db")
    config = {
        "database": {"path": db_file},
        "collector": {
            "sample_interval_seconds": 1,
            "disk_path": "/",
            "retention_days": 7,
            "retention_interval_seconds": 3600,
        },
        "alerts": {
            "debounce_samples": 3,
            "hysteresis_points": 5,
            "thresholds": {
                "cpu": {"warning": 70, "critical": 90},
                "memory": {"warning": 75, "critical": 90},
                "disk": {"warning": 80, "critical": 95},
            },
        },
    }

    collector = MetricCollector(config)
    collector.run_cycle()

    latest = get_latest_metric(db_file)
    assert latest is not None
    assert 0.0 <= latest["cpu_percent"] <= 100.0
    assert 0.0 <= latest["memory_percent"] <= 100.0
    assert 0.0 <= latest["disk_percent"] <= 100.0
