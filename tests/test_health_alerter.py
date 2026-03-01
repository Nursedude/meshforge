"""Tests for monitoring.health_alerter — predictive node health alerts."""

import sys
import os
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from monitoring.health_alerter import (
    HealthAlert, HealthAlerter,
    BATTERY_WARNING_PCT, BATTERY_CRITICAL_PCT,
    SNR_DEGRADING_RATE_THRESHOLD, STALE_HOURS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def alerter():
    """Fresh HealthAlerter with no suppression history."""
    return HealthAlerter()


def _make_node(node_id="!abc123", name="TestNode", battery=None, snr=None,
               last_heard=None, trend_direction=None, trend_rate=0):
    """Helper to create a plain dict node."""
    return {
        "id": node_id,
        "name": name,
        "battery": battery,
        "snr": snr,
        "last_heard": last_heard,
        "trend_direction": trend_direction,
        "trend_rate": trend_rate,
    }


# ---------------------------------------------------------------------------
# Battery alert tests
# ---------------------------------------------------------------------------

class TestBatteryAlerts:
    def test_battery_critical(self, alerter):
        """Battery at or below critical threshold triggers critical alert."""
        node = _make_node(battery=5)
        alerts = alerter.check_nodes([node])
        assert len(alerts) == 1
        assert alerts[0].alert_type == "battery_critical"
        assert alerts[0].severity == "critical"
        assert alerts[0].value == 5

    def test_battery_at_critical_boundary(self, alerter):
        """Battery exactly at critical threshold triggers critical."""
        node = _make_node(battery=BATTERY_CRITICAL_PCT)
        alerts = alerter.check_nodes([node])
        assert len(alerts) == 1
        assert alerts[0].alert_type == "battery_critical"

    def test_battery_warning(self, alerter):
        """Battery between warning and critical triggers warning alert."""
        node = _make_node(battery=15)
        alerts = alerter.check_nodes([node])
        assert len(alerts) == 1
        assert alerts[0].alert_type == "battery_low"
        assert alerts[0].severity == "warning"

    def test_battery_at_warning_boundary(self, alerter):
        """Battery exactly at warning threshold triggers warning."""
        node = _make_node(battery=BATTERY_WARNING_PCT)
        alerts = alerter.check_nodes([node])
        assert len(alerts) == 1
        assert alerts[0].alert_type == "battery_low"

    def test_battery_healthy_no_alert(self, alerter):
        """Battery above warning threshold generates no alert."""
        node = _make_node(battery=80)
        alerts = alerter.check_nodes([node])
        assert len(alerts) == 0

    def test_battery_none_no_alert(self, alerter):
        """Missing battery data generates no alert."""
        node = _make_node(battery=None)
        alerts = alerter.check_nodes([node])
        assert len(alerts) == 0


# ---------------------------------------------------------------------------
# Signal degradation tests
# ---------------------------------------------------------------------------

class TestSignalAlerts:
    def test_signal_degrading_above_threshold(self, alerter):
        """Degrading signal faster than threshold triggers alert."""
        node = _make_node(
            snr=5.0,
            trend_direction="degrading",
            trend_rate=-2.0,  # 2 dB/hr degradation
        )
        alerts = alerter.check_nodes([node])
        assert len(alerts) == 1
        assert alerts[0].alert_type == "signal_degrading"
        assert alerts[0].severity == "warning"

    def test_signal_degrading_with_prediction(self, alerter):
        """Degrading signal includes predicted hours to unusable."""
        node = _make_node(
            snr=5.0,
            trend_direction="degrading",
            trend_rate=-2.5,
        )
        alerts = alerter.check_nodes([node])
        assert len(alerts) == 1
        # SNR 5.0, target -20, rate 2.5 → (5 - (-20)) / 2.5 = 10.0h
        assert alerts[0].predicted_hours == 10.0

    def test_signal_degrading_below_threshold_no_alert(self, alerter):
        """Slow degradation below threshold generates no alert."""
        node = _make_node(
            snr=5.0,
            trend_direction="degrading",
            trend_rate=-0.5,  # Below threshold
        )
        alerts = alerter.check_nodes([node])
        assert len(alerts) == 0

    def test_signal_stable_no_alert(self, alerter):
        """Stable signal generates no alert."""
        node = _make_node(snr=10.0, trend_direction="stable", trend_rate=0)
        alerts = alerter.check_nodes([node])
        assert len(alerts) == 0

    def test_signal_improving_no_alert(self, alerter):
        """Improving signal generates no alert."""
        node = _make_node(snr=10.0, trend_direction="improving", trend_rate=1.5)
        alerts = alerter.check_nodes([node])
        assert len(alerts) == 0


# ---------------------------------------------------------------------------
# Stale node tests
# ---------------------------------------------------------------------------

class TestStaleNodeAlerts:
    def test_stale_node(self, alerter):
        """Node not heard for >STALE_HOURS triggers alert."""
        old_time = time.time() - (STALE_HOURS + 1) * 3600
        node = _make_node(last_heard=old_time)
        alerts = alerter.check_nodes([node])
        assert len(alerts) == 1
        assert alerts[0].alert_type == "node_stale"
        assert alerts[0].severity == "warning"

    def test_recent_node_no_alert(self, alerter):
        """Recently heard node generates no stale alert."""
        node = _make_node(last_heard=time.time() - 60)  # 1 minute ago
        alerts = alerter.check_nodes([node])
        assert len(alerts) == 0

    def test_no_last_heard_no_alert(self, alerter):
        """Missing last_heard generates no alert."""
        node = _make_node(last_heard=None)
        alerts = alerter.check_nodes([node])
        assert len(alerts) == 0


# ---------------------------------------------------------------------------
# Deduplication tests
# ---------------------------------------------------------------------------

class TestDeduplication:
    def test_duplicate_alerts_suppressed(self, alerter):
        """Same node+type alert is suppressed on second call."""
        node = _make_node(battery=5)
        alerts1 = alerter.check_nodes([node])
        assert len(alerts1) == 1

        alerts2 = alerter.check_nodes([node])
        assert len(alerts2) == 0  # Suppressed

    def test_different_nodes_not_suppressed(self, alerter):
        """Alerts for different nodes are not suppressed."""
        node1 = _make_node(node_id="!aaa", name="Node1", battery=5)
        node2 = _make_node(node_id="!bbb", name="Node2", battery=5)

        alerts1 = alerter.check_nodes([node1])
        assert len(alerts1) == 1

        alerts2 = alerter.check_nodes([node2])
        assert len(alerts2) == 1

    def test_different_alert_types_not_suppressed(self, alerter):
        """Different alert types for same node are not suppressed."""
        old_time = time.time() - (STALE_HOURS + 1) * 3600
        node = _make_node(battery=5, last_heard=old_time)
        alerts = alerter.check_nodes([node])
        # Should get both battery_critical and node_stale
        assert len(alerts) == 2
        types = {a.alert_type for a in alerts}
        assert "battery_critical" in types
        assert "node_stale" in types

    def test_clear_suppressions(self, alerter):
        """clear_suppressions() allows alerts to fire again."""
        node = _make_node(battery=5)
        alerts1 = alerter.check_nodes([node])
        assert len(alerts1) == 1

        alerter.clear_suppressions()

        alerts2 = alerter.check_nodes([node])
        assert len(alerts2) == 1  # No longer suppressed


# ---------------------------------------------------------------------------
# Severity ordering tests
# ---------------------------------------------------------------------------

class TestSeverityOrdering:
    def test_critical_before_warning(self, alerter):
        """Critical alerts appear before warning alerts."""
        old_time = time.time() - (STALE_HOURS + 1) * 3600
        nodes = [
            _make_node(node_id="!stale", name="Stale", last_heard=old_time),
            _make_node(node_id="!crit", name="Critical", battery=5),
        ]
        alerts = alerter.check_nodes(nodes)
        assert len(alerts) == 2
        assert alerts[0].severity == "critical"
        assert alerts[1].severity == "warning"


# ---------------------------------------------------------------------------
# Input normalization tests
# ---------------------------------------------------------------------------

class TestNormalization:
    def test_geojson_feature_input(self, alerter):
        """GeoJSON feature dict is normalized correctly."""
        feature = {
            "type": "Feature",
            "properties": {
                "id": "!geo1",
                "name": "GeoNode",
                "battery": 8,
            },
            "geometry": {"type": "Point", "coordinates": [0, 0]},
        }
        alerts = alerter.check_nodes([feature])
        assert len(alerts) == 1
        assert alerts[0].node_id == "!geo1"
        assert alerts[0].node_name == "GeoNode"

    def test_plain_dict_with_battery_level_key(self, alerter):
        """Plain dict with battery_level key (not battery) is handled."""
        node = {"node_id": "!alt1", "long_name": "AltNode", "battery_level": 7}
        alerts = alerter.check_nodes([node])
        assert len(alerts) == 1
        assert alerts[0].node_name == "AltNode"

    def test_none_node_skipped(self, alerter):
        """None in node list is skipped gracefully."""
        alerts = alerter.check_nodes([None, _make_node(battery=5)])
        assert len(alerts) == 1

    def test_empty_list_no_alerts(self, alerter):
        """Empty node list produces no alerts."""
        alerts = alerter.check_nodes([])
        assert len(alerts) == 0


# ---------------------------------------------------------------------------
# HealthAlert dataclass tests
# ---------------------------------------------------------------------------

class TestHealthAlertDataclass:
    def test_to_dict(self):
        """to_dict() returns all fields."""
        alert = HealthAlert(
            node_id="!abc",
            node_name="Test",
            alert_type="battery_low",
            severity="warning",
            message="Battery low",
            predicted_hours=4.5,
            value=15.0,
        )
        d = alert.to_dict()
        assert d["node_id"] == "!abc"
        assert d["node_name"] == "Test"
        assert d["alert_type"] == "battery_low"
        assert d["severity"] == "warning"
        assert d["predicted_hours"] == 4.5
        assert d["value"] == 15.0
        assert "timestamp" in d

    def test_default_timestamp(self):
        """HealthAlert gets a default timestamp on creation."""
        before = time.time()
        alert = HealthAlert(
            node_id="!x", node_name="X",
            alert_type="test", severity="info", message="test",
        )
        after = time.time()
        assert before <= alert.timestamp <= after
