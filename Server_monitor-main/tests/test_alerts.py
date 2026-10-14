"""Unit tests for AlertEvaluator: debounce, hysteresis, escalation, and persistence."""

from __future__ import annotations

import pytest
from alerts import AlertEvaluator
from db import get_active_alerts, get_alerts_history, init_db, insert_alert


@pytest.fixture
def alert_config():
    return {
        "debounce_samples": 3,
        "hysteresis_points": 5.0,
        "thresholds": {
            "cpu": {"warning": 70.0, "critical": 90.0},
            "memory": {"warning": 75.0, "critical": 90.0},
            "disk": {"warning": 80.0, "critical": 95.0},
        },
    }


@pytest.fixture
def test_db(tmp_path):
    db_file = str(tmp_path / "alerts_test.db")
    init_db(db_file)
    return db_file


def test_alert_debounce_warning_requires_n_samples(test_db, alert_config):
    """Verify warning alert only fires after N consecutive samples above threshold."""
    evaluator = AlertEvaluator(test_db, alert_config)
    t = 1000.0

    # Sample 1: 72% (above warning 70%) -> debounce count 1/3, no alert
    evt1 = evaluator.evaluate_metric("cpu", 72.0, t)
    assert evt1 is None
    assert len(evaluator.get_active_alerts_list()) == 0
    assert len(get_active_alerts(test_db)) == 0

    # Sample 2: 74% -> debounce count 2/3, no alert
    evt2 = evaluator.evaluate_metric("cpu", 74.0, t + 5)
    assert evt2 is None
    assert len(evaluator.get_active_alerts_list()) == 0

    # Sample 3: 71% -> debounce count 3/3, FIRES warning alert!
    evt3 = evaluator.evaluate_metric("cpu", 71.0, t + 10)
    assert evt3 is not None
    assert evt3["action"] == "fired"
    assert evt3["level"] == "warning"
    assert evt3["metric"] == "cpu"

    active = get_active_alerts(test_db)
    assert len(active) == 1
    assert active[0]["level"] == "warning"
    assert active[0]["value"] == 71.0


def test_alert_debounce_resets_on_dip(test_db, alert_config):
    """Verify that a dip below threshold resets debounce counter (no false alarm on transient spikes)."""
    evaluator = AlertEvaluator(test_db, alert_config)
    t = 1000.0

    evaluator.evaluate_metric("cpu", 75.0, t)      # 1st high
    evaluator.evaluate_metric("cpu", 78.0, t + 5)  # 2nd high
    # 3rd sample dips to 68% (< 70%) -> counter resets!
    evaluator.evaluate_metric("cpu", 68.0, t + 10)

    # 4th sample is high again -> counter is now only 1/3, must NOT fire
    evt4 = evaluator.evaluate_metric("cpu", 76.0, t + 15)
    assert evt4 is None
    assert len(get_active_alerts(test_db)) == 0


def test_alert_hysteresis_resolution(test_db, alert_config):
    """Verify warning alert resolves only after dropping below (threshold - hysteresis)."""
    evaluator = AlertEvaluator(test_db, alert_config)
    t = 1000.0

    # Trigger warning (threshold 70, hysteresis 5 -> clear at < 65)
    evaluator.evaluate_metric("cpu", 72.0, t)
    evaluator.evaluate_metric("cpu", 73.0, t + 5)
    evt = evaluator.evaluate_metric("cpu", 75.0, t + 10)
    assert evt is not None and evt["action"] == "fired"

    # Value drops to 68% (below 70%, but above 65%) -> MUST STAY ACTIVE due to hysteresis
    evt_stay = evaluator.evaluate_metric("cpu", 68.0, t + 15)
    assert evt_stay is None
    assert len(evaluator.get_active_alerts_list()) == 1

    # Value drops to 65.5% -> still above 65.0 -> MUST STAY ACTIVE
    evt_stay2 = evaluator.evaluate_metric("cpu", 65.5, t + 20)
    assert evt_stay2 is None
    assert len(evaluator.get_active_alerts_list()) == 1

    # Value drops to 64.0% (< 65.0) -> RESOLVES!
    evt_res = evaluator.evaluate_metric("cpu", 64.0, t + 25)
    assert evt_res is not None
    assert evt_res["action"] == "resolved"
    assert evt_res["level"] == "warning"

    # DB verify
    assert len(get_active_alerts(test_db)) == 0
    history = get_alerts_history(test_db)
    assert len(history) == 1
    assert history[0]["resolved_at"] == t + 25


def test_escalation_to_critical_and_deescalation(test_db, alert_config):
    """Verify warning escalates to critical, and critical de-escalates with hysteresis."""
    evaluator = AlertEvaluator(test_db, alert_config)
    t = 1000.0

    # Fire warning (3 samples >= 70%)
    for i in range(3):
        evaluator.evaluate_metric("cpu", 75.0, t + i * 5)
    assert evaluator.active_alerts["cpu"]["level"] == "warning"

    # Escalate to critical (requires 3 samples >= 90%)
    evaluator.evaluate_metric("cpu", 92.0, t + 20)
    evaluator.evaluate_metric("cpu", 94.0, t + 25)
    evt_esc = evaluator.evaluate_metric("cpu", 93.0, t + 30)

    assert evt_esc is not None
    assert evt_esc["action"] == "escalated"
    assert evt_esc["level"] == "critical"
    assert evaluator.active_alerts["cpu"]["level"] == "critical"

    # Critical threshold 90, hysteresis 5 -> clears below 85
    # Drops to 88% -> stays critical
    assert evaluator.evaluate_metric("cpu", 88.0, t + 35) is None

    # Drops to 80% (< 85, but >= 65) -> de-escalates to warning
    evt_deesc = evaluator.evaluate_metric("cpu", 80.0, t + 40)
    assert evt_deesc is not None
    assert evt_deesc["action"] == "de-escalated"
    assert evt_deesc["level"] == "warning"
    assert evaluator.active_alerts["cpu"]["level"] == "warning"

    # Drops to 55% (< 65) -> fully resolves
    evt_full_res = evaluator.evaluate_metric("cpu", 55.0, t + 45)
    assert evt_full_res is not None
    assert evt_full_res["action"] == "resolved"
    assert evaluator.active_alerts["cpu"] is None


def test_restart_hydration_preserves_active_alert(test_db, alert_config):
    """Verify active alerts are re-hydrated from database on startup."""
    # Simulate an active alert already recorded in DB
    t_start = 5000.0
    alert_id = insert_alert(test_db, "memory", "warning", 78.0, t_start)

    # Initialize a new evaluator (simulating app restart)
    new_evaluator = AlertEvaluator(test_db, alert_config)
    assert new_evaluator.active_alerts["memory"] is not None
    assert new_evaluator.active_alerts["memory"]["id"] == alert_id
    assert new_evaluator.active_alerts["memory"]["level"] == "warning"

    # Now drop memory below hysteresis (threshold 75 - 5 = 70)
    evt = new_evaluator.evaluate_metric("memory", 68.0, t_start + 60)
    assert evt is not None
    assert evt["action"] == "resolved"
    assert evt["id"] == alert_id
    assert len(get_active_alerts(test_db)) == 0
