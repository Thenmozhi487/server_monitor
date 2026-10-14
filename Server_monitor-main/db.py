"""Database access layer for Server Health Dashboard.

Uses SQLite with WAL mode for safe concurrent access between the background
collector thread and Flask web request handlers.
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Any, Dict, List, Optional


def get_connection(db_path: str) -> sqlite3.Connection:
    """Create and configure a SQLite connection with WAL mode and row factory."""
    # Ensure directory exists if path contains directories
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(db_path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    # Configure WAL mode and busy timeout for high concurrency
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def init_db(db_path: str) -> None:
    """Initialize database tables and indexes."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                cpu_percent REAL NOT NULL,
                memory_percent REAL NOT NULL,
                disk_percent REAL NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                metric TEXT NOT NULL,
                level TEXT NOT NULL,
                value REAL NOT NULL,
                started_at REAL NOT NULL,
                resolved_at REAL
            );
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics(timestamp);"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts(metric, resolved_at);"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alerts_started ON alerts(started_at DESC);"
        )
        conn.commit()


def insert_metric(
    db_path: str,
    timestamp: float,
    cpu_percent: float,
    memory_percent: float,
    disk_percent: float,
) -> int:
    """Insert a single metric sample and return row ID."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO metrics (timestamp, cpu_percent, memory_percent, disk_percent)
            VALUES (?, ?, ?, ?);
            """,
            (
                float(timestamp),
                round(float(cpu_percent), 2),
                round(float(memory_percent), 2),
                round(float(disk_percent), 2),
            ),
        )
        conn.commit()
        return cursor.lastrowid


def get_latest_metric(db_path: str) -> Optional[Dict[str, Any]]:
    """Retrieve the most recent metric entry."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT id, timestamp, cpu_percent, memory_percent, disk_percent
            FROM metrics
            ORDER BY timestamp DESC
            LIMIT 1;
            """
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None


def get_metrics_history(
    db_path: str,
    minutes: int = 60,
    end_time: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Retrieve metric samples within the specified past minutes window."""
    if end_time is None:
        end_time = time.time()
    start_time = end_time - (minutes * 60)

    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT id, timestamp, cpu_percent, memory_percent, disk_percent
            FROM metrics
            WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC;
            """,
            (start_time, end_time),
        )
        return [dict(row) for row in cursor.fetchall()]


def insert_alert(
    db_path: str,
    metric: str,
    level: str,
    value: float,
    started_at: float,
) -> int:
    """Create a new active alert entry (resolved_at = NULL)."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO alerts (metric, level, value, started_at, resolved_at)
            VALUES (?, ?, ?, ?, NULL);
            """,
            (metric, level, round(float(value), 2), float(started_at)),
        )
        conn.commit()
        return cursor.lastrowid


def resolve_alert(db_path: str, alert_id: int, resolved_at: float) -> None:
    """Mark an active alert as resolved."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE alerts
            SET resolved_at = ?
            WHERE id = ? AND resolved_at IS NULL;
            """,
            (float(resolved_at), int(alert_id)),
        )
        conn.commit()


def get_active_alerts(db_path: str) -> List[Dict[str, Any]]:
    """Retrieve all currently active (unresolved) alerts."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT id, metric, level, value, started_at, resolved_at
            FROM alerts
            WHERE resolved_at IS NULL
            ORDER BY started_at DESC;
            """
        )
        return [dict(row) for row in cursor.fetchall()]


def get_alerts_history(db_path: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Retrieve recent alerts sorted newest first."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT id, metric, level, value, started_at, resolved_at
            FROM alerts
            ORDER BY started_at DESC
            LIMIT ?;
            """,
            (int(limit),),
        )
        return [dict(row) for row in cursor.fetchall()]


def cleanup_retention(db_path: str, retention_days: int) -> int:
    """Delete metrics older than retention_days.

    Returns the number of deleted rows.
    """
    cutoff = time.time() - (retention_days * 86400)
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            DELETE FROM metrics
            WHERE timestamp < ?;
            """,
            (cutoff,),
        )
        deleted_count = cursor.rowcount
        conn.commit()
        return deleted_count
