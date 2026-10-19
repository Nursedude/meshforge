"""
Tests for GatewayHeartbeat MQTT alerting.

Covers:
- Alert publishing to MQTT with correct topic and payload
- Alert deduplication (same service:event within 60s suppressed)
- Severity classification (critical, warning, info)
- Alert when MQTT disconnected (no-op, no crash)
"""

import json
import time
from unittest.mock import MagicMock

import pytest

from gateway.gateway_heartbeat import (
    GatewayHeartbeat,
    HeartbeatConfig,
)


@pytest.fixture
def alert_config():
    """Config for alert testing."""
    return HeartbeatConfig(
        enabled=True,
        mqtt_broker="localhost",
        mqtt_port=1883,
        gateway_id="gw-test",
        role="primary",
    )


@pytest.fixture
def connected_hb(alert_config):
    """Heartbeat instance with mocked MQTT connection."""
    hb = GatewayHeartbeat(config=alert_config)
    hb._mqtt_client = MagicMock()
    hb._mqtt_connected = True
    return hb


class TestAlertPublishing:
    """Tests for publish_alert method."""

    def test_alert_published_to_mqtt(self, connected_hb):
        """Alert should be published to correct topic with expected payload."""
        connected_hb.publish_alert(
            severity="critical",
            service="radio_failover",
            event="secondary_active",
            reason="Primary crashed",
        )

        connected_hb._mqtt_client.publish.assert_called_once()
        call_args = connected_hb._mqtt_client.publish.call_args
        topic = call_args[0][0]
        payload = json.loads(call_args[0][1])

        assert topic == "meshforge/gateway/gw-test/alerts"
        assert payload['severity'] == "critical"
        assert payload['service'] == "radio_failover"
        assert payload['event'] == "secondary_active"
        assert payload['reason'] == "Primary crashed"
        assert payload['gateway_id'] == "gw-test"
        assert 'timestamp' in payload

    def test_alert_not_published_when_disconnected(self, alert_config):
        """Alert should be silently dropped when MQTT is disconnected."""
        hb = GatewayHeartbeat(config=alert_config)
        hb._mqtt_client = MagicMock()
        hb._mqtt_connected = False

        hb.publish_alert("critical", "test", "down", "reason")

        hb._mqtt_client.publish.assert_not_called()

    def test_alert_not_published_without_client(self, alert_config):
        """Alert should be silently dropped when no MQTT client."""
        hb = GatewayHeartbeat(config=alert_config)
        hb._mqtt_client = None
        hb._mqtt_connected = True

        # Should not raise
        hb.publish_alert("critical", "test", "down", "reason")


class TestAlertDeduplication:
    """Tests for alert deduplication."""

    def test_duplicate_alert_suppressed(self, connected_hb):
        """Same (service, event) within dedup window should be suppressed."""
        connected_hb.publish_alert("critical", "radio_failover", "down", "crashed")
        connected_hb.publish_alert("critical", "radio_failover", "down", "crashed again")

        # Only first alert should be published
        assert connected_hb._mqtt_client.publish.call_count == 1

    def test_different_events_not_suppressed(self, connected_hb):
        """Different events for same service should not be suppressed."""
        connected_hb.publish_alert("critical", "radio_failover", "down", "crashed")
        connected_hb.publish_alert("info", "radio_failover", "up", "recovered")

        assert connected_hb._mqtt_client.publish.call_count == 2

    def test_dedup_expires_after_window(self, connected_hb):
        """After dedup window expires, same alert should be published again."""
        connected_hb.publish_alert("critical", "radio_failover", "down", "crashed")

        # Simulate time passing beyond dedup window
        connected_hb._alert_dedup["radio_failover:down"] = time.monotonic() - 120

        connected_hb.publish_alert("critical", "radio_failover", "down", "crashed again")

        assert connected_hb._mqtt_client.publish.call_count == 2


class TestAlertSeverityClassification:
    """Tests for _classify_alert_severity."""

    def test_unavailable_is_critical(self, connected_hb):
        """Service down should be classified as critical."""
        assert connected_hb._classify_alert_severity(False, "Service down") == "critical"

    def test_recovery_is_warning(self, connected_hb):
        """Recovery pending should be classified as warning."""
        assert connected_hb._classify_alert_severity(True, "Recovery pending") == "warning"

    def test_rate_limit_is_warning(self, connected_hb):
        """Rate limit reached should be classified as warning."""
        assert connected_hb._classify_alert_severity(True, "Rate limit reached") == "warning"

    def test_normal_up_is_info(self, connected_hb):
        """Normal service up should be classified as info."""
        assert connected_hb._classify_alert_severity(True, "Service started") == "info"
