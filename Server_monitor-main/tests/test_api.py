"""Unit tests for Flask API endpoints."""

from __future__ import annotations

import time
from unittest.mock import patch
import pytest

from app import create_app
from db import init_db, insert_alert, insert_metric


@pytest.fixture
def app_client(tmp_path):
    """Create test client with isolated database and without running background collector."""
    db_file = str(tmp_path / "api_test.db")
    test_config = {
        "database": {"path": db_file},
        "collector": {
            "sample_interval_seconds": 5,
            "disk_path": "/",
            "retention_days": 7,
            "retention_interval_seconds": 3600,
        },
        "alerts": {
            "debounce_samples": 3,
            "hysteresis_points": 5.0,
            "thresholds": {
                "cpu": {"warning": 70.0, "critical": 90.0},
                "memory": {"warning": 75.0, "critical": 90.0},
                "disk": {"warning": 80.0, "critical": 95.0},
            },
        },
    }

    flask_app = create_app(config_override=test_config, start_bg_collector=False)
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as client:
        yield client, db_file


def test_index_endpoint(app_client):
    """GET / renders HTML page."""
    client, _ = app_client
    res = client.get("/")
    assert res.status_code == 200
    assert b"Server Health Dashboard" in res.data


def test_health_endpoint(app_client):
    """GET /health returns liveness status."""
    client, _ = app_client
    res = client.get("/health")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "ok"
    assert "timestamp" in data
    assert "uptime" in data


def test_api_metrics_endpoint(app_client):
    """GET /api/metrics returns latest sample, uptime, and active alerts."""
    client, db_file = app_client

    # When database is empty
    res0 = client.get("/api/metrics")
    assert res0.status_code == 200
    data0 = res0.get_json()
    assert data0["latest"] is None
    assert "uptime" in data0
    assert data0["active_alerts"] == []

    # Insert sample & active alert
    t = time.time()
    insert_metric(db_file, t, 33.3, 55.5, 77.7)
    alert_id = insert_alert(db_file, "cpu", "warning", 72.0, t)

    res = client.get("/api/metrics")
    assert res.status_code == 200
    data = res.get_json()
    assert data["latest"] is not None
    assert data["latest"]["cpu_percent"] == 33.3
    assert data["latest"]["memory_percent"] == 55.5
    assert data["latest"]["disk_percent"] == 77.7
    assert len(data["active_alerts"]) == 1
    assert data["active_alerts"][0]["id"] == alert_id
    assert data["active_alerts"][0]["metric"] == "cpu"


def test_api_history_endpoint(app_client):
    """GET /api/history returns time-series samples."""
    client, db_file = app_client
    now = time.time()

    # Insert samples
    insert_metric(db_file, now - 3600, 10.0, 20.0, 30.0)
    insert_metric(db_file, now - 300, 25.0, 35.0, 45.0)

    # 15 minutes query: should only include sample from 300s ago
    res15 = client.get("/api/history?minutes=15")
    assert res15.status_code == 200
    data15 = res15.get_json()
    assert data15["minutes"] == 15
    assert data15["count"] == 1
    assert data15["samples"][0]["cpu_percent"] == 25.0

    # 60 minutes query: should include sample within 1 hour
    res60 = client.get("/api/history?minutes=60")
    assert res60.status_code == 200
    data60 = res60.get_json()
    assert data60["minutes"] == 60
    assert data60["count"] >= 1


def test_api_alerts_endpoint(app_client):
    """GET /api/alerts returns alert history."""
    client, db_file = app_client
    t = time.time()

    insert_alert(db_file, "cpu", "warning", 71.0, t - 100)
    insert_alert(db_file, "memory", "critical", 92.0, t - 50)

    res = client.get("/api/alerts?limit=10")
    assert res.status_code == 200
    data = res.get_json()
    assert data["count"] == 2
    assert len(data["alerts"]) == 2
    assert data["alerts"][0]["metric"] == "memory"  # Newest first


def test_api_config_endpoint(app_client):
    """GET /api/config returns read-only configuration and thresholds."""
    client, _ = app_client
    res = client.get("/api/config")
    assert res.status_code == 200
    data = res.get_json()
    assert data["sample_interval_seconds"] == 5
    assert data["debounce_samples"] == 3
    assert data["hysteresis_points"] == 5.0
    assert "cpu" in data["thresholds"]
    assert data["thresholds"]["cpu"]["warning"] == 70.0
    assert data["thresholds"]["cpu"]["critical"] == 90.0
