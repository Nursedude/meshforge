"""
Tests for the MeshAlertEngine.

Tests alert evaluation logic with mock data (no MQTT or hardware needed),
cooldown behavior, emergency keyword matching, battery threshold checks,
config load/save via SettingsManager, and graceful degradation.
"""

import os
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

# Ensure src is on path
SRC_DIR = os.path.join(os.path.dirname(__file__), '..', 'src')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from utils.event_bus import AlertEvent, event_bus
from utils.mesh_alert_engine import MeshAlertEngine, get_alert_engine


@pytest.fixture(autouse=True)
def _alert_config_in_tmp(tmp_path, monkeypatch):
    """Every engine here writes settings through SettingsManager, which
    resolves CONFIG_DIR at construction — the operator's REAL
    ~/.config/meshforge. `update_config("battery_threshold", 15)` and the
    fixture's cooldown/noisy values were landing in the live
    mesh_alerts.json on every suite run, and `test_singleton_accessor` left
    a real-home singleton behind for later tests (found 2026-09-22 by the
    truth sweep's real-home witness). CONFIG_DIR is read at call time, so
    patching it redirects every engine built in this file."""
    import utils.common as common
    import utils.mesh_alert_engine as mae
    monkeypatch.setattr(common, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(mae, "_engine", None)


@pytest.fixture
def engine():
    """Create a fresh MeshAlertEngine for each test."""
    e = MeshAlertEngine()
    # Override settings to avoid file I/O in tests
    e._settings._settings = {
        "enabled": True,
        "battery_threshold": 20,
        "disconnect_timeout_minutes": 30,
        "cooldown_seconds": 1,  # Short cooldown for testing
        "emergency_keywords": ["help", "emergency", "sos", "mayday"],
        "noisy_node_threshold": 5,
        "snr_threshold": -10.0,
        "enabled_types": [
            "battery", "emergency", "new_node", "disconnect",
            "noisy_node", "snr",
        ],
    }
    e._started = True
    e._start_time = time.time() - 60  # Pretend started 60s ago
    yield e
    e.stop()


class _MockNode:
    """Minimal mock node for testing."""
    def __init__(self, node_id="!test1234", long_name="TestNode",
                 short_name="TN", battery_level=None, snr=None):
        self.node_id = node_id
        self.long_name = long_name
        self.short_name = short_name
        self.battery_level = battery_level
        self.snr = snr


class _MockMessage:
    """Minimal mock message for testing."""
    def __init__(self, text="", sender_id="!sender01", sender_name="Sender"):
        self.text = text
        self.sender_id = sender_id
        self.sender_name = sender_name
        self.node_id = sender_id


class TestBatteryAlert:
    """Test battery level alert evaluation."""

    def test_low_battery_triggers_alert(self, engine):
        node = _MockNode(battery_level=15)
        engine._evaluate_battery(node)
        alerts = engine.get_active_alerts()
        assert len(alerts) == 1
        assert alerts[0].alert_type == "battery"
        assert alerts[0].severity == 2

    def test_very_low_battery_is_high_severity(self, engine):
        node = _MockNode(battery_level=5)
        engine._evaluate_battery(node)
        alerts = engine.get_active_alerts()
        assert len(alerts) == 1
        assert alerts[0].severity == 3

    def test_normal_battery_no_alert(self, engine):
        node = _MockNode(battery_level=80)
        engine._evaluate_battery(node)
        assert len(engine.get_active_alerts()) == 0

    def test_none_battery_no_alert(self, engine):
        node = _MockNode(battery_level=None)
        engine._evaluate_battery(node)
        assert len(engine.get_active_alerts()) == 0

    def test_battery_disabled_no_alert(self, engine):
        engine._settings._settings["enabled_types"] = ["emergency"]
        node = _MockNode(battery_level=5)
        engine._evaluate_battery(node)
        assert len(engine.get_active_alerts()) == 0


class TestEmergencyAlert:
    """Test emergency keyword detection."""

    def test_keyword_triggers_alert(self, engine):
        msg = _MockMessage(text="I need help at mile 5")
        engine._evaluate_emergency(msg)
        alerts = engine.get_active_alerts()
        assert len(alerts) == 1
        assert alerts[0].alert_type == "emergency"
        assert alerts[0].severity == 4

    def test_case_insensitive(self, engine):
        msg = _MockMessage(text="HELP NEEDED")
        engine._evaluate_emergency(msg)
        assert len(engine.get_active_alerts()) == 1

    def test_no_match_no_alert(self, engine):
        msg = _MockMessage(text="Beautiful day for a hike!")
        engine._evaluate_emergency(msg)
        assert len(engine.get_active_alerts()) == 0

    def test_empty_text_no_alert(self, engine):
        msg = _MockMessage(text="")
        engine._evaluate_emergency(msg)
        assert len(engine.get_active_alerts()) == 0

    def test_custom_keywords(self, engine):
        engine._settings._settings["emergency_keywords"] = ["fire", "flood"]
        msg = _MockMessage(text="There's a fire on the trail")
        engine._evaluate_emergency(msg)
        assert len(engine.get_active_alerts()) == 1


class TestNewNodeAlert:
    """Test new node discovery alerts."""

    def test_new_node_triggers_alert(self, engine):
        node = _MockNode(node_id="!newnode01", long_name="NewHiker")
        engine._evaluate_new_node(node)
        alerts = engine.get_active_alerts()
        assert len(alerts) == 1
        assert alerts[0].alert_type == "new_node"
        assert alerts[0].severity == 1

    def test_known_node_no_alert(self, engine):
        engine._known_nodes["!known01"] = time.time()
        node = _MockNode(node_id="!known01")
        engine._evaluate_new_node(node)
        assert len(engine.get_active_alerts()) == 0


class TestSNRAlert:
    """Test SNR threshold alerts."""

    def test_low_snr_triggers_alert(self, engine):
        node = _MockNode(snr=-15.0)
        engine._evaluate_snr(node)
        alerts = engine.get_active_alerts()
        assert len(alerts) == 1
        assert alerts[0].alert_type == "snr"

    def test_good_snr_no_alert(self, engine):
        node = _MockNode(snr=5.0)
        engine._evaluate_snr(node)
        assert len(engine.get_active_alerts()) == 0


class TestNoisyNodeAlert:
    """Test message rate tracking and noisy node detection."""

    def test_rapid_messages_trigger_alert(self, engine):
        for i in range(6):
            msg = _MockMessage(text=f"spam {i}", sender_id="!spammer")
            engine._evaluate_noisy_node(msg)
        alerts = engine.get_active_alerts()
        assert len(alerts) == 1
        assert alerts[0].alert_type == "noisy_node"

    def test_normal_rate_no_alert(self, engine):
        msg = _MockMessage(text="hello", sender_id="!normal")
        engine._evaluate_noisy_node(msg)
        assert len(engine.get_active_alerts()) == 0


class TestCooldown:
    """Test per-node per-type alert cooldown."""

    def test_same_alert_suppressed_within_cooldown(self, engine):
        node = _MockNode(battery_level=10)
        engine._evaluate_battery(node)
        engine._evaluate_battery(node)
        # Second should be suppressed by cooldown
        assert len(engine.get_active_alerts()) == 1

    def test_different_types_not_suppressed(self, engine):
        node = _MockNode(battery_level=10, snr=-15.0)
        engine._evaluate_battery(node)
        engine._evaluate_snr(node)
        assert len(engine.get_active_alerts()) == 2

    def test_different_nodes_not_suppressed(self, engine):
        n1 = _MockNode(node_id="!node1", battery_level=10)
        n2 = _MockNode(node_id="!node2", battery_level=10)
        engine._evaluate_battery(n1)
        engine._evaluate_battery(n2)
        assert len(engine.get_active_alerts()) == 2

    def test_cooldown_expires(self, engine):
        engine._settings._settings["cooldown_seconds"] = 0  # Immediate expiry
        node = _MockNode(battery_level=10)
        engine._evaluate_battery(node)
        # Reset cooldown entry
        engine._cooldowns.clear()
        engine._evaluate_battery(node)
        assert len(engine.get_active_alerts()) == 2


class TestConfigPersistence:
    """Test configuration load/save."""

    def test_update_config(self, engine):
        engine.update_config("battery_threshold", 15)
        assert engine.config["battery_threshold"] == 15

    def test_disabled_engine_no_alerts(self, engine):
        engine._settings._settings["enabled"] = False
        node = _MockNode(battery_level=5)
        engine._evaluate_battery(node)
        assert len(engine.get_active_alerts()) == 0


class TestAcknowledge:
    """Test alert acknowledgement."""

    def test_acknowledge_all(self, engine):
        n1 = _MockNode(node_id="!n1", battery_level=5)
        n2 = _MockNode(node_id="!n2", battery_level=8)
        engine._evaluate_battery(n1)
        engine._evaluate_battery(n2)
        assert len(engine.get_active_alerts()) == 2
        count = engine.acknowledge_all()
        assert count == 2
        assert len(engine.get_active_alerts()) == 0

    def test_get_all_alerts_includes_acknowledged(self, engine):
        node = _MockNode(battery_level=5)
        engine._evaluate_battery(node)
        engine.acknowledge_all()
        assert len(engine.get_all_alerts()) == 1
        assert len(engine.get_active_alerts()) == 0


class TestMQTTSubscriberIntegration:
    """Test attachment to MQTT subscriber."""

    def test_attach_registers_callbacks(self, engine):
        subscriber = MagicMock()
        engine.attach_subscriber(subscriber)
        subscriber.register_node_callback.assert_called_once_with(engine._on_node_update)
        subscriber.register_message_callback.assert_called_once_with(engine._on_message)

    def test_attach_handles_none(self, engine):
        engine.attach_subscriber(None)  # Should not raise

    def test_node_callback_tracks_known_nodes(self, engine):
        node = _MockNode(node_id="!tracked")
        engine._on_node_update(node)
        assert "!tracked" in engine._known_nodes

    def test_message_callback_evaluates_emergency(self, engine):
        msg = _MockMessage(text="mayday mayday")
        engine._on_message(msg)
        alerts = engine.get_active_alerts()
        assert any(a.alert_type == "emergency" for a in alerts)


class TestAlertCountByType:
    """Test alert count grouping."""

    def test_count_by_type(self, engine):
        n1 = _MockNode(node_id="!a", battery_level=5)
        n2 = _MockNode(node_id="!b", battery_level=8, snr=-15.0)
        engine._evaluate_battery(n1)
        engine._evaluate_battery(n2)
        engine._evaluate_snr(n2)
        counts = engine.get_alert_count_by_type()
        assert counts["battery"] == 2
        assert counts["snr"] == 1


class TestGracefulDegradation:
    """Test behavior when meshing_around is not installed."""

    def test_engine_works_without_meshing_around(self):
        """Engine should function with core features even without MA."""
        e = MeshAlertEngine()
        e._started = True
        e._start_time = time.time() - 60
        node = _MockNode(battery_level=10)
        e._evaluate_battery(node)
        assert len(e.get_active_alerts()) == 1

    def test_singleton_accessor(self):
        """get_alert_engine() should return a valid engine."""
        engine = get_alert_engine()
        assert engine is not None
        assert isinstance(engine, MeshAlertEngine)


class TestStartStop:
    """Test engine lifecycle."""

    def test_start_creates_disconnect_thread(self):
        e = MeshAlertEngine()
        e.start()
        assert e._started
        assert e._disconnect_thread is not None
        assert e._disconnect_thread.is_alive()
        e.stop()
        assert not e._started

    def test_double_start_is_safe(self):
        e = MeshAlertEngine()
        e.start()
        e.start()  # Should not create second thread
        e.stop()
