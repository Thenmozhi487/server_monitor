"""Alert evaluator for Server Health Dashboard.

Implements N-consecutive sample debouncing and hysteresis-based resolution
for CPU, memory, and disk health metrics.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from db import get_active_alerts, insert_alert, resolve_alert

logger = logging.getLogger(__name__)


class AlertEvaluator:
    """Manages alert state transitions with debounce and hysteresis."""

    def __init__(self, db_path: str, alert_config: Dict[str, Any]) -> None:
        self.db_path = db_path
        self.debounce_samples: int = int(alert_config.get("debounce_samples", 3))
        self.hysteresis_points: float = float(alert_config.get("hysteresis_points", 5.0))
        self.thresholds: Dict[str, Dict[str, float]] = {
            metric: {
                "warning": float(cfg.get("warning", 70.0)),
                "critical": float(cfg.get("critical", 90.0)),
            }
            for metric, cfg in alert_config.get("thresholds", {}).items()
        }

        # Track consecutive breach counts per metric
        # Structure: {metric: {"warning": int, "critical": int}}
        self._consecutive_breaches: Dict[str, Dict[str, int]] = {
            metric: {"warning": 0, "critical": 0}
            for metric in ("cpu", "memory", "disk")
        }

        # Active alert state per metric:
        # Structure: {metric: Optional[{"id": int, "level": str, "value": float, "started_at": float}]}
        self.active_alerts: Dict[str, Optional[Dict[str, Any]]] = {
            metric: None for metric in ("cpu", "memory", "disk")
        }

        # Hydrate active alerts from SQLite to survive application restarts
        self._hydrate_active_alerts()

    def _hydrate_active_alerts(self) -> None:
        """Load currently active (unresolved) alerts from database on startup."""
        try:
            records = get_active_alerts(self.db_path)
            for rec in records:
                metric = rec["metric"]
                if metric in self.active_alerts and self.active_alerts[metric] is None:
                    self.active_alerts[metric] = {
                        "id": rec["id"],
                        "level": rec["level"],
                        "value": rec["value"],
                        "started_at": rec["started_at"],
                    }
                    logger.info(
                        "Restored active %s alert for %s (id=%d)",
                        rec["level"],
                        metric,
                        rec["id"],
                    )
        except Exception as e:
            logger.warning("Could not hydrate active alerts on startup: %s", e)

    def evaluate_metric(
        self,
        metric: str,
        value: float,
        timestamp: float,
    ) -> Optional[Dict[str, Any]]:
        """Evaluate a new metric sample against thresholds, debounce, and hysteresis.

        Returns an event dict if an alert state changed (fired, escalated, or resolved),
        or None if state remained unchanged.
        """
        metric_cfg = self.thresholds.get(metric)
        if not metric_cfg:
            return None

        warn_th = metric_cfg["warning"]
        crit_th = metric_cfg["critical"]
        hysteresis = self.hysteresis_points
        debounce = self.debounce_samples

        current_active = self.active_alerts.get(metric)
        current_level = current_active["level"] if current_active else None

        # Update consecutive breach counters
        if value >= crit_th:
            self._consecutive_breaches[metric]["critical"] += 1
            self._consecutive_breaches[metric]["warning"] += 1
        elif value >= warn_th:
            self._consecutive_breaches[metric]["warning"] += 1
            self._consecutive_breaches[metric]["critical"] = 0
        else:
            self._consecutive_breaches[metric]["warning"] = 0
            self._consecutive_breaches[metric]["critical"] = 0

        crit_count = self._consecutive_breaches[metric]["critical"]
        warn_count = self._consecutive_breaches[metric]["warning"]

        event: Optional[Dict[str, Any]] = None

        # -------------------------------------------------------------
        # STATE: NORMAL (No active alert)
        # -------------------------------------------------------------
        if current_level is None:
            if crit_count >= debounce:
                # Direct critical breach
                alert_id = insert_alert(self.db_path, metric, "critical", value, timestamp)
                self.active_alerts[metric] = {
                    "id": alert_id,
                    "level": "critical",
                    "value": value,
                    "started_at": timestamp,
                }
                event = {
                    "action": "fired",
                    "metric": metric,
                    "level": "critical",
                    "id": alert_id,
                    "value": value,
                }
            elif warn_count >= debounce:
                # Warning breach
                alert_id = insert_alert(self.db_path, metric, "warning", value, timestamp)
                self.active_alerts[metric] = {
                    "id": alert_id,
                    "level": "warning",
                    "value": value,
                    "started_at": timestamp,
                }
                event = {
                    "action": "fired",
                    "metric": metric,
                    "level": "warning",
                    "id": alert_id,
                    "value": value,
                }

        # -------------------------------------------------------------
        # STATE: WARNING
        # -------------------------------------------------------------
        elif current_level == "warning":
            # Check escalation to critical (requires N consecutive critical breaches)
            if crit_count >= debounce:
                # Resolve warning alert and trigger critical alert
                assert current_active is not None
                resolve_alert(self.db_path, current_active["id"], timestamp)
                new_id = insert_alert(self.db_path, metric, "critical", value, timestamp)
                self.active_alerts[metric] = {
                    "id": new_id,
                    "level": "critical",
                    "value": value,
                    "started_at": timestamp,
                }
                event = {
                    "action": "escalated",
                    "metric": metric,
                    "level": "critical",
                    "previous_id": current_active["id"],
                    "id": new_id,
                    "value": value,
                }
            # Check resolution using hysteresis: clear when value drops below (warning - hysteresis)
            elif value < (warn_th - hysteresis):
                assert current_active is not None
                resolve_alert(self.db_path, current_active["id"], timestamp)
                prev_id = current_active["id"]
                self.active_alerts[metric] = None
                self._consecutive_breaches[metric]["warning"] = 0
                self._consecutive_breaches[metric]["critical"] = 0
                event = {
                    "action": "resolved",
                    "metric": metric,
                    "level": "warning",
                    "id": prev_id,
                    "value": value,
                }

        # -------------------------------------------------------------
        # STATE: CRITICAL
        # -------------------------------------------------------------
        elif current_level == "critical":
            # Check if critical condition is cleared (value < critical - hysteresis)
            crit_clear_th = crit_th - hysteresis
            if value < crit_clear_th:
                assert current_active is not None
                resolve_alert(self.db_path, current_active["id"], timestamp)
                prev_id = current_active["id"]

                # Does it drop down to warning or completely to normal?
                warn_clear_th = warn_th - hysteresis
                if value >= warn_clear_th:
                    # De-escalate to warning
                    new_id = insert_alert(self.db_path, metric, "warning", value, timestamp)
                    self.active_alerts[metric] = {
                        "id": new_id,
                        "level": "warning",
                        "value": value,
                        "started_at": timestamp,
                    }
                    # We maintain warning breach state
                    self._consecutive_breaches[metric]["critical"] = 0
                    event = {
                        "action": "de-escalated",
                        "metric": metric,
                        "level": "warning",
                        "previous_id": prev_id,
                        "id": new_id,
                        "value": value,
                    }
                else:
                    # Fully resolved to normal
                    self.active_alerts[metric] = None
                    self._consecutive_breaches[metric]["warning"] = 0
                    self._consecutive_breaches[metric]["critical"] = 0
                    event = {
                        "action": "resolved",
                        "metric": metric,
                        "level": "critical",
                        "id": prev_id,
                        "value": value,
                    }

        return event

    def get_active_alerts_list(self) -> list[Dict[str, Any]]:
        """Return a list of currently active alerts."""
        return [alert for alert in self.active_alerts.values() if alert is not None]
