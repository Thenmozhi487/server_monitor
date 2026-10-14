"""Unit tests for SQLite database operations and WAL mode."""

from __future__ import annotations

import os
import time
import pytest
from db import (
    cleanup_retention,
    get_active_alerts,
    get_alerts_history,
    get_connection,
    get_latest_metric,
    get_metrics_history,
    init_db,
    insert_alert,
    insert_metric,
    resolve_alert,
)


@pytest.fixture
def temp_db(tmp_path):
    db_file = str(tmp_path / "test_health.db")
    init_db(db_file)
    return db_file


def test_wal_mode_enabled(temp_db):
    """Verify database initializes in WAL mode."""
    with get_connection(temp_db) as conn:
        cursor = conn.execute("PRAGMA journal_mode;")
        mode = cursor.fetchone()[0]
        assert mode.lower() == "wal"


def test_insert_and_get_latest_metric(temp_db):
    """Verify inserting and querying latest metric sample."""
    assert get_latest_metric(temp_db) is None

    t1 = time.time()
    insert_metric(temp_db, t1, 45.2, 60.1, 75.0)

    t2 = t1 + 5.0
    insert_metric(temp_db, t2, 55.8, 62.3, 75.0)

    latest = get_latest_metric(temp_db)
    assert latest is not None
    assert latest["timestamp"] == pytest.approx(t2, 0.001)
    assert latest["cpu_percent"] == 55.8
    assert latest["memory_percent"] == 62.3
    assert latest["disk_percent"] == 75.0


def test_metrics_history_window(temp_db):
    """Verify history query filters correctly by time window."""
    now = 1000000.0

    # Insert sample 90 minutes ago
    insert_metric(temp_db, now - (90 * 60), 20.0, 30.0, 40.0)
    # Insert sample 30 minutes ago
    insert_metric(temp_db, now - (30 * 60), 35.0, 45.0, 50.0)
    # Insert sample 5 minutes ago
    insert_metric(temp_db, now - (5 * 60), 50.0, 55.0, 50.0)

    # 15 minutes window: should only contain sample from 5m ago
    history_15m = get_metrics_history(temp_db, minutes=15, end_time=now)
    assert len(history_15m) == 1
    assert history_15m[0]["cpu_percent"] == 50.0

    # 60 minutes window: should contain 30m and 5m samples
    history_60m = get_metrics_history(temp_db, minutes=60, end_time=now)
    assert len(history_60m) == 2

    # 120 minutes window: should contain all 3
    history_120m = get_metrics_history(temp_db, minutes=120, end_time=now)
    assert len(history_120m) == 3


def test_alerts_crud_and_resolution(temp_db):
    """Verify alert creation, active status query, resolution, and history."""
    t_start = 1000000.0
    alert_id = insert_alert(temp_db, "cpu", "warning", 78.5, t_start)
    assert alert_id > 0

    active = get_active_alerts(temp_db)
    assert len(active) == 1
    assert active[0]["id"] == alert_id
    assert active[0]["metric"] == "cpu"
    assert active[0]["level"] == "warning"
    assert active[0]["resolved_at"] is None

    # Resolve alert
    t_resolve = t_start + 60.0
    resolve_alert(temp_db, alert_id, t_resolve)

    assert len(get_active_alerts(temp_db)) == 0

    history = get_alerts_history(temp_db, limit=10)
    assert len(history) == 1
    assert history[0]["id"] == alert_id
    assert history[0]["resolved_at"] == t_resolve


def test_retention_cleanup(temp_db):
    """Verify retention cleanup deletes records older than configured days."""
    now = time.time()
    day_seconds = 86400

    # Sample from 10 days ago (older than 7 days)
    insert_metric(temp_db, now - (10 * day_seconds), 30.0, 40.0, 50.0)
    # Sample from 8 days ago (older than 7 days)
    insert_metric(temp_db, now - (8 * day_seconds), 35.0, 45.0, 50.0)
    # Sample from 5 days ago (within 7 days)
    insert_metric(temp_db, now - (5 * day_seconds), 40.0, 50.0, 50.0)
    # Sample from today (within 7 days)
    insert_metric(temp_db, now - 100, 45.0, 55.0, 50.0)

    deleted = cleanup_retention(temp_db, retention_days=7)
    assert deleted == 2

    remaining = get_metrics_history(temp_db, minutes=100000, end_time=now)
    assert len(remaining) == 2
    assert all(m["timestamp"] >= now - (7 * day_seconds) for m in remaining)
