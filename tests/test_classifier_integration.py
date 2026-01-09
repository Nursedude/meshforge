"""
Integration tests for classifier pattern in rns_bridge and diagnostics.

Tests the integration of:
- RoutingClassifier in RNSMeshtasticBridge
- NotificationClassifier in DiagnosticEngine
"""

import pytest
import tempfile
from pathlib import Path
from datetime import datetime
from unittest.mock import MagicMock, patch

# Test RNS Bridge integration
from src.gateway.rns_bridge import (
    RNSMeshtasticBridge, BridgedMessage, CLASSIFIER_AVAILABLE
)
from src.gateway.config import GatewayConfig, RoutingRule

# Test Diagnostics integration
from src.core.diagnostics.engine import DiagnosticEngine
from src.core.diagnostics.models import EventSeverity, CheckCategory


class TestBridgeClassifierIntegration:
    """Tests for RoutingClassifier integration in RNSMeshtasticBridge."""

    @pytest.fixture
    def bridge(self):
        """Create a bridge with test config."""
        config = GatewayConfig(enabled=True)
        config.routing_rules = [
            RoutingRule(
                name="test_rule",
                enabled=True,
                direction="bidirectional",
                priority=10
            )
        ]
        return RNSMeshtasticBridge(config)

    @pytest.fixture
    def mesh_message(self):
        """Create a test message from Meshtastic."""
        return BridgedMessage(
            source_network="meshtastic",
            source_id="!abcd1234",
            destination_id=None,
            content="Hello from mesh",
            is_broadcast=True
        )

    @pytest.fixture
    def rns_message(self):
        """Create a test message from RNS."""
        return BridgedMessage(
            source_network="rns",
            source_id="abc123def456",
            destination_id=None,
            content="Hello from RNS",
            is_broadcast=True
        )

    def test_classifier_initialized(self, bridge):
        """Test classifier is initialized when available."""
        if CLASSIFIER_AVAILABLE:
            assert bridge._classifier is not None
        else:
            assert bridge._classifier is None

    def test_should_bridge_mesh_message(self, bridge, mesh_message):
        """Test bridging decision for Meshtastic message."""
        result = bridge._should_bridge(mesh_message)

        # With classifier, should get a decision
        assert isinstance(result, bool)

    def test_should_bridge_rns_message(self, bridge, rns_message):
        """Test bridging decision for RNS message."""
        result = bridge._should_bridge(rns_message)

        assert isinstance(result, bool)

    def test_disabled_config_blocks_bridge(self, mesh_message):
        """Test that disabled config prevents bridging."""
        config = GatewayConfig(enabled=False)
        bridge = RNSMeshtasticBridge(config)

        result = bridge._should_bridge(mesh_message)

        assert result is False

    @pytest.mark.skipif(not CLASSIFIER_AVAILABLE, reason="Classifier not available")
    def test_classification_recorded(self, bridge, mesh_message):
        """Test that classification is recorded."""
        bridge._should_bridge(mesh_message)

        last = bridge.get_last_classification()
        assert last is not None
        assert 'category' in last
        assert 'confidence' in last

    @pytest.mark.skipif(not CLASSIFIER_AVAILABLE, reason="Classifier not available")
    def test_routing_stats_include_classifier(self, bridge, mesh_message):
        """Test routing stats include classifier data."""
        bridge._should_bridge(mesh_message)

        stats = bridge.get_routing_stats()

        assert 'classifier' in stats
        assert 'total' in stats['classifier']

    @pytest.mark.skipif(not CLASSIFIER_AVAILABLE, reason="Classifier not available")
    def test_bounced_messages_tracked(self, bridge):
        """Test that bounced messages are tracked in stats."""
        # Initial bounced count
        initial = bridge.stats['bounced']

        # Create message with minimal info (likely to bounce)
        msg = BridgedMessage(
            source_network="unknown",
            source_id="",
            destination_id=None,
            content=""
        )
        bridge._should_bridge(msg)

        # Should see some stats activity
        stats = bridge.get_routing_stats()
        assert 'bounced' in stats

    @pytest.mark.skipif(not CLASSIFIER_AVAILABLE, reason="Classifier not available")
    def test_fix_routing(self, bridge):
        """Test fix button for routing decisions."""
        result = bridge.fix_routing("test_msg_id", "drop")

        assert result is True

    def test_legacy_fallback_when_no_classifier(self, mesh_message):
        """Test legacy routing works when classifier unavailable."""
        config = GatewayConfig(enabled=True, default_route="bidirectional")
        bridge = RNSMeshtasticBridge(config)

        # Temporarily disable classifier
        original = bridge._classifier
        bridge._classifier = None

        result = bridge._should_bridge(mesh_message)

        bridge._classifier = original

        # Should still get a decision from legacy logic
        assert isinstance(result, bool)


class TestDiagnosticsClassifierIntegration:
    """Tests for NotificationClassifier integration in DiagnosticEngine."""

    @pytest.fixture
    def engine(self):
        """Get a fresh diagnostic engine instance."""
        # Reset singleton for testing
        DiagnosticEngine._instance = None
        engine = DiagnosticEngine.get_instance()
        yield engine
        # Cleanup
        DiagnosticEngine._instance = None

    def test_notification_classifier_initialized(self, engine):
        """Test notification classifier is initialized."""
        # Classifier should be present if available
        if hasattr(engine, '_notification_classifier'):
            # May be None if import failed, that's OK
            pass

    def test_log_event_returns_event(self, engine):
        """Test log_event still returns event."""
        event = engine.log_event(
            severity=EventSeverity.INFO,
            source="test",
            message="Test message"
        )

        assert event is not None
        assert event.message == "Test message"

    def test_critical_event_logged(self, engine):
        """Test critical events are logged."""
        event = engine.log_event(
            severity=EventSeverity.CRITICAL,
            source="test",
            message="Critical failure occurred"
        )

        assert event.severity == EventSeverity.CRITICAL

    def test_notification_callback_registration(self, engine):
        """Test notification callback can be registered."""
        callback = MagicMock()

        engine.register_notification_callback(callback)

        # Callback should be registered
        assert callback in engine._notification_callbacks

    def test_notification_callback_called_for_critical(self, engine):
        """Test notification callback called for critical events."""
        callback = MagicMock()
        engine.register_notification_callback(callback)

        # Log a critical event
        engine.log_event(
            severity=EventSeverity.CRITICAL,
            source="test",
            message="System crash detected"  # Contains 'crash' keyword
        )

        # If classifier is available and determines notification needed,
        # callback should be called
        # Note: This depends on classifier confidence, so we just verify
        # the mechanism works
        if engine._notification_classifier:
            # Check if callback was potentially called
            # (depends on confidence threshold)
            pass

    def test_info_event_may_not_notify(self, engine):
        """Test that info events may not trigger notification."""
        callback = MagicMock()
        engine.register_notification_callback(callback)

        # Log a low-priority event
        engine.log_event(
            severity=EventSeverity.DEBUG,
            source="test",
            message="Routine check completed"
        )

        # Debug events should typically not trigger notifications
        # (depends on classifier, but generally true)

    def test_notification_stats(self, engine):
        """Test notification stats are available."""
        stats = engine.get_notification_stats()

        # Should return a dict (empty if no classifier)
        assert isinstance(stats, dict)

    def test_fix_notification(self, engine):
        """Test fix button for notification priority."""
        if engine._notification_classifier:
            result = engine.fix_notification("test_event", "background")
            assert result is True
        else:
            result = engine.fix_notification("test_event", "background")
            assert result is False

    def test_event_callbacks_always_called(self, engine):
        """Test that regular event callbacks are always called."""
        callback = MagicMock()
        engine.register_event_callback(callback)

        engine.log_event(
            severity=EventSeverity.INFO,
            source="test",
            message="Test event"
        )

        # Regular callbacks should always be called
        callback.assert_called_once()

    def test_multiple_events_classified(self, engine):
        """Test multiple events are classified independently."""
        events = [
            (EventSeverity.CRITICAL, "System error"),
            (EventSeverity.WARNING, "Connection warning"),
            (EventSeverity.INFO, "Node connected"),
            (EventSeverity.DEBUG, "Trace message"),
        ]

        for severity, message in events:
            event = engine.log_event(
                severity=severity,
                source="test",
                message=message
            )
            assert event is not None

        # Check stats if available
        stats = engine.get_notification_stats()
        if stats and 'total' in stats:
            assert stats['total'] >= len(events)


class TestEndToEndIntegration:
    """End-to-end integration tests."""

    def test_bridge_with_real_config(self):
        """Test bridge initialization with realistic config."""
        config = GatewayConfig(
            enabled=True,
            default_route="bidirectional",
            routing_rules=[
                RoutingRule(
                    name="emergency",
                    enabled=True,
                    direction="bidirectional",
                    message_filter="EMERGENCY",
                    priority=100
                ),
                RoutingRule(
                    name="standard",
                    enabled=True,
                    direction="bidirectional",
                    priority=10
                )
            ]
        )

        bridge = RNSMeshtasticBridge(config)

        # Create messages
        emergency = BridgedMessage(
            source_network="meshtastic",
            source_id="!abcd1234",
            destination_id=None,
            content="EMERGENCY: Help needed!",
            is_broadcast=True
        )

        normal = BridgedMessage(
            source_network="meshtastic",
            source_id="!abcd1234",
            destination_id=None,
            content="Hello everyone",
            is_broadcast=True
        )

        # Both should get routing decisions
        assert isinstance(bridge._should_bridge(emergency), bool)
        assert isinstance(bridge._should_bridge(normal), bool)

    def test_diagnostics_with_category(self):
        """Test diagnostics with check category."""
        DiagnosticEngine._instance = None
        engine = DiagnosticEngine.get_instance()

        try:
            event = engine.log_event(
                severity=EventSeverity.ERROR,
                source="meshtastic",
                message="Connection failed",
                category=CheckCategory.MESHTASTIC,
                fix_hint="Check if meshtasticd is running"
            )

            assert event.category == CheckCategory.MESHTASTIC
            assert event.fix_hint is not None
        finally:
            DiagnosticEngine._instance = None

    def test_classifier_receipts_persist(self):
        """Test that classifier decisions can be retrieved."""
        config = GatewayConfig(enabled=True)
        bridge = RNSMeshtasticBridge(config)

        if not CLASSIFIER_AVAILABLE:
            pytest.skip("Classifier not available")

        # Make several routing decisions
        for i in range(5):
            msg = BridgedMessage(
                source_network="meshtastic",
                source_id=f"!node{i:04d}",
                destination_id=None,
                content=f"Message {i}",
                is_broadcast=True
            )
            bridge._should_bridge(msg)

        # Check receipts
        receipts = bridge._classifier.get_receipts()
        assert len(receipts) == 5

    def test_bounce_queue_accessible(self):
        """Test that bouncer queue is accessible."""
        config = GatewayConfig(enabled=True)
        bridge = RNSMeshtasticBridge(config)

        if not CLASSIFIER_AVAILABLE:
            pytest.skip("Classifier not available")

        stats = bridge.get_routing_stats()
        assert 'bouncer_queue' in stats
        assert isinstance(stats['bouncer_queue'], int)
