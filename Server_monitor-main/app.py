"""Flask application for Real-Time Server Health Dashboard.

Exposes REST API endpoints for current metrics, historical telemetry, active alerts,
configuration metadata, and health liveness check.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Dict, Optional

from flask import Flask, jsonify, render_template, request
import yaml

from collector import get_uptime_string, start_collector, stop_collector
from db import (
    get_active_alerts,
    get_alerts_history,
    get_latest_metric,
    get_metrics_history,
    init_db,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
)
logger = logging.getLogger("server_health")


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load configuration from YAML file with defaults."""
    if config_path is None:
        config_path = os.getenv("CONFIG_PATH", "config.yaml")

    # If relative path, check current working dir or app dir
    if not os.path.isabs(config_path) and not os.path.exists(config_path):
        app_dir = os.path.dirname(os.path.abspath(__file__))
        alt_path = os.path.join(app_dir, config_path)
        if os.path.exists(alt_path):
            config_path = alt_path

    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    else:
        logger.warning("Config file not found at '%s'. Using defaults.", config_path)
        cfg = {}

    # Set guaranteed defaults
    cfg.setdefault("server", {"host": "0.0.0.0", "port": 5000, "debug": False})
    cfg.setdefault("database", {"path": "server_health.db"})
    cfg.setdefault(
        "collector",
        {
            "sample_interval_seconds": 5,
            "disk_path": "/",
            "retention_days": 7,
            "retention_interval_seconds": 3600,
        },
    )
    cfg.setdefault(
        "alerts",
        {
            "debounce_samples": 3,
            "hysteresis_points": 5.0,
            "thresholds": {
                "cpu": {"warning": 70.0, "critical": 90.0},
                "memory": {"warning": 75.0, "critical": 90.0},
                "disk": {"warning": 80.0, "critical": 95.0},
            },
        },
    )
    return cfg


def create_app(
    config_override: Optional[Dict[str, Any]] = None,
    config_path: Optional[str] = None,
    start_bg_collector: bool = True,
) -> Flask:
    """Application factory for Flask dashboard."""
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    if config_override is not None:
        app.config["APP_CONFIG"] = config_override
    else:
        app.config["APP_CONFIG"] = load_config(config_path)

    cfg = app.config["APP_CONFIG"]
    db_path = cfg.get("database", {}).get("path", "server_health.db")

    # Ensure DB tables exist
    init_db(db_path)

    # Start background collector thread if requested
    if start_bg_collector:
        start_collector(cfg)

    # -------------------------------------------------------------
    # ROUTES
    # -------------------------------------------------------------
    @app.route("/")
    def index():
        """Render main dashboard view."""
        return render_template("index.html")

    @app.route("/health", methods=["GET"])
    def health():
        """Liveness probe endpoint."""
        import time

        return (
            jsonify(
                {
                    "status": "ok",
                    "timestamp": time.time(),
                    "uptime": get_uptime_string(),
                }
            ),
            200,
        )

    @app.route("/api/metrics", methods=["GET"])
    def get_metrics():
        """Return the latest sample, formatted uptime, and active alerts."""
        current_db = app.config["APP_CONFIG"].get("database", {}).get(
            "path", "server_health.db"
        )
        latest = get_latest_metric(current_db)
        active_alerts = get_active_alerts(current_db)
        uptime = get_uptime_string()

        return (
            jsonify(
                {
                    "latest": latest,
                    "uptime": uptime,
                    "active_alerts": active_alerts,
                }
            ),
            200,
        )

    @app.route("/api/history", methods=["GET"])
    def get_history():
        """Return time-series metrics filtered by minutes parameter (15, 60, 1440)."""
        current_db = app.config["APP_CONFIG"].get("database", {}).get(
            "path", "server_health.db"
        )
        raw_minutes = request.args.get("minutes", default="60")
        try:
            minutes = int(raw_minutes)
            if minutes <= 0:
                minutes = 60
        except ValueError:
            minutes = 60

        samples = get_metrics_history(current_db, minutes=minutes)
        return (
            jsonify(
                {
                    "minutes": minutes,
                    "count": len(samples),
                    "samples": samples,
                }
            ),
            200,
        )

    @app.route("/api/alerts", methods=["GET"])
    def get_alerts():
        """Return historical alerts log (default 50)."""
        current_db = app.config["APP_CONFIG"].get("database", {}).get(
            "path", "server_health.db"
        )
        raw_limit = request.args.get("limit", default="50")
        try:
            limit = int(raw_limit)
            if limit <= 0 or limit > 500:
                limit = 50
        except ValueError:
            limit = 50

        alerts = get_alerts_history(current_db, limit=limit)
        return (
            jsonify(
                {
                    "limit": limit,
                    "count": len(alerts),
                    "alerts": alerts,
                }
            ),
            200,
        )

    @app.route("/api/config", methods=["GET"])
    def get_config():
        """Return read-only alert thresholds and sampling parameters."""
        app_cfg = app.config["APP_CONFIG"]
        collector_cfg = app_cfg.get("collector", {})
        alert_cfg = app_cfg.get("alerts", {})

        return (
            jsonify(
                {
                    "sample_interval_seconds": collector_cfg.get(
                        "sample_interval_seconds", 5
                    ),
                    "disk_path": collector_cfg.get("disk_path", "/"),
                    "debounce_samples": alert_cfg.get("debounce_samples", 3),
                    "hysteresis_points": alert_cfg.get("hysteresis_points", 5.0),
                    "thresholds": alert_cfg.get("thresholds", {}),
                }
            ),
            200,
        )

    return app


# Default app instance for WSGI / Gunicorn
app = create_app(start_bg_collector=True)

if __name__ == "__main__":
    cfg = app.config.get("APP_CONFIG", {})
    srv = cfg.get("server", {})
    app.run(
        host=srv.get("host", "0.0.0.0"),
        port=int(srv.get("port", 5000)),
        debug=srv.get("debug", False),
    )
