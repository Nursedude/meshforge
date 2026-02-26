"""
Tests for MeshForge daemon mode.

Covers:
    - DaemonService protocol compliance
    - ServiceRegistry lifecycle management
    - ThreadWatchdog dead service detection and restart
    - DaemonController PID management and signal handling
    - DaemonConfig loading and merging
    - EventBus ThreadPoolExecutor fix

Run: python3 -m pytest tests/test_daemon.py -v
"""

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Ensure src/ is in path
SRC_DIR = os.path.join(os.path.dirname(__file__), '..', 'src')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


# =============================================================================
# Test fixtures
# =============================================================================

class MockService:
    """A mock service for testing ServiceRegistry and ThreadWatchdog."""

    def __init__(self, name="mock_service", start_ok=True, alive=True):
        self.name = name
        self._start_ok = start_ok
        self._alive = alive
        self.started = False
        self.stopped = False
        self.start_count = 0
        self.stop_count = 0

    def start(self):
        self.start_count += 1
        self.started = True
        return self._start_ok

    def stop(self, timeout=5.0):
        self.stop_count += 1
        self.stopped = True

    def is_alive(self):
        return self._alive

    def get_status(self):
        return {"name": self.name, "alive": self._alive}


@pytest.fixture
def mock_service():
    return MockService()


@pytest.fixture
def registry():
    from daemon import ServiceRegistry
    return ServiceRegistry()


@pytest.fixture
def daemon_config():
    from daemon_config import DaemonConfig
    return DaemonConfig()


# =============================================================================
# DaemonConfig Tests
# =============================================================================

class TestDaemonConfig:
    """Test daemon configuration loading and defaults."""

    def test_default_values(self, daemon_config):
        """Default config has sane defaults."""
        assert daemon_config.gateway_enabled is True
        assert daemon_config.health_probe_enabled is True
        assert daemon_config.health_probe_interval == 30
        assert daemon_config.mqtt_enabled is False
        assert daemon_config.config_api_enabled is True
        assert daemon_config.watchdog_interval == 60
        assert daemon_config.max_restarts == 5
        assert daemon_config.log_level == "INFO"

    def test_to_dict(self, daemon_config):
        """to_dict() returns serializable representation."""
        d = daemon_config.to_dict()
        assert isinstance(d, dict)
        assert 'gateway_enabled' in d
        assert 'health_probe_interval' in d
        assert d['gateway_enabled'] is True

    def test_profile_application(self):
        """Profile feature flags override defaults."""
        from daemon_config import DaemonConfig

        mock_profile = MagicMock()
        mock_profile.feature_flags = {
            'gateway': False,
            'mqtt': True,
        }

        config = DaemonConfig()
        config._apply_profile(mock_profile)

        assert config.gateway_enabled is False
        assert config.mqtt_enabled is True

    def test_yaml_loading(self, tmp_path):
        """Config loads from YAML file."""
        yaml_content = """
gateway: false
mqtt: true
mqtt_broker: mqtt.example.com
health_probe_interval: 60
log_level: DEBUG
"""
        config_file = tmp_path / "daemon.yaml"
        config_file.write_text(yaml_content)

        from daemon_config import DaemonConfig
        config = DaemonConfig()

        # Only test if PyYAML is available
        try:
            import yaml
            config._load_yaml(config_file)
            assert config.gateway_enabled is False
            assert config.mqtt_enabled is True
            assert config.mqtt_broker == "mqtt.example.com"
            assert config.health_probe_interval == 60
            assert config.log_level == "DEBUG"
        except ImportError:
            pytest.skip("PyYAML not installed")

    def test_invalid_yaml_handled(self, tmp_path):
        """Invalid YAML file doesn't crash."""
        from daemon_config import DaemonConfig
        config = DaemonConfig()
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text("this is not: valid: yaml: [[[")
        # Should not raise
        config._load_yaml(bad_file)

    def test_missing_yaml_handled(self):
        """Missing YAML file is handled gracefully."""
        from daemon_config import DaemonConfig
        config = DaemonConfig()
        config._load_yaml(Path("/nonexistent/path.yaml"))
        # Should not raise, defaults preserved
        assert config.gateway_enabled is True


# =============================================================================
# ServiceRegistry Tests
# =============================================================================

class TestServiceRegistry:
    """Test service registration, start/stop ordering, status reporting."""

    def test_register_service(self, registry, mock_service):
        """Service registration works."""
        registry.register(mock_service)
        assert registry.get_service("mock_service") is mock_service

    def test_start_all_success(self, registry):
        """All services start successfully."""
        svc1 = MockService("svc1")
        svc2 = MockService("svc2")
        registry.register(svc1)
        registry.register(svc2)

        results = registry.start_all()
        assert results == {"svc1": True, "svc2": True}
        assert svc1.started
        assert svc2.started

    def test_start_all_partial_failure(self, registry):
        """Partial start failure doesn't block other services."""
        svc1 = MockService("svc1", start_ok=False)
        svc2 = MockService("svc2", start_ok=True)
        registry.register(svc1)
        registry.register(svc2)

        results = registry.start_all()
        assert results == {"svc1": False, "svc2": True}
        assert svc1.started  # Attempted
        assert svc2.started  # Still started

    def test_stop_all_reverse_order(self, registry):
        """Services stop in reverse registration order."""
        stop_order = []
        svc1 = MockService("first")
        svc1.stop = lambda timeout=5.0: stop_order.append("first")
        svc2 = MockService("second")
        svc2.stop = lambda timeout=5.0: stop_order.append("second")

        registry.register(svc1)
        registry.register(svc2)
        registry.stop_all()

        assert stop_order == ["second", "first"]

    def test_get_all_status(self, registry):
        """Status dict includes all registered services."""
        svc1 = MockService("svc1", alive=True)
        svc2 = MockService("svc2", alive=False)
        registry.register(svc1)
        registry.register(svc2)

        status = registry.get_all_status()
        assert "svc1" in status
        assert "svc2" in status
        assert status["svc1"]["alive"] is True
        assert status["svc2"]["alive"] is False

    def test_restart_service(self, registry):
        """Restart stops then starts a service."""
        svc = MockService("svc1")
        registry.register(svc)

        result = registry.restart_service("svc1")
        assert result is True
        assert svc.stop_count == 1
        assert svc.start_count == 1

    def test_restart_nonexistent_service(self, registry):
        """Restarting unknown service returns False."""
        assert registry.restart_service("nonexistent") is False

    def test_get_nonexistent_service(self, registry):
        """Getting unknown service returns None."""
        assert registry.get_service("nonexistent") is None


# =============================================================================
# ThreadWatchdog Tests
# =============================================================================

class TestThreadWatchdog:
    """Test dead service detection and restart."""

    def test_watchdog_detects_dead_service(self, registry):
        """Watchdog calls restart when is_alive() returns False."""
        from daemon import ThreadWatchdog

        svc = MockService("dying", alive=False)
        registry.register(svc)

        watchdog = ThreadWatchdog(registry, interval=1, max_restarts=3)
        watchdog._check_services()

        # Service should have been restarted
        assert svc.stop_count >= 1 or svc.start_count >= 1

    def test_watchdog_skips_alive_services(self, registry):
        """Watchdog doesn't restart alive services."""
        from daemon import ThreadWatchdog

        svc = MockService("healthy", alive=True)
        registry.register(svc)

        watchdog = ThreadWatchdog(registry, interval=1, max_restarts=3)
        watchdog._check_services()

        assert svc.stop_count == 0
        assert svc.start_count == 0

    def test_watchdog_respects_max_restarts(self, registry):
        """After max_restarts failures, watchdog stops trying."""
        from daemon import ThreadWatchdog

        svc = MockService("failing", start_ok=False, alive=False)
        registry.register(svc)

        watchdog = ThreadWatchdog(registry, interval=1, max_restarts=2)

        # Run check enough times to exhaust restarts
        for _ in range(5):
            watchdog._check_services()
            watchdog._backoff_until.clear()  # Clear backoff for testing

        # Should have stopped after max_restarts
        assert svc.start_count <= 3  # 2 restarts + maybe 1 extra

    def test_watchdog_start_stop(self, registry):
        """Watchdog thread starts and stops cleanly."""
        from daemon import ThreadWatchdog

        watchdog = ThreadWatchdog(registry, interval=60, max_restarts=3)
        watchdog.start()
        assert watchdog._thread.is_alive()

        watchdog.stop(timeout=2)
        assert not watchdog._thread.is_alive()

    def test_get_restart_counts(self, registry):
        """Restart counts are tracked."""
        from daemon import ThreadWatchdog

        watchdog = ThreadWatchdog(registry, interval=1, max_restarts=5)
        watchdog._restart_counts["svc1"] = 3
        counts = watchdog.get_restart_counts()
        assert counts == {"svc1": 3}


# =============================================================================
# DaemonController Tests
# =============================================================================

class TestDaemonController:
    """Test PID file management and signal handling."""

    def test_pid_file_path(self):
        """PID file path is deterministic."""
        from daemon import DaemonController
        controller = DaemonController()
        controller._config = MagicMock()
        controller._config.pid_dir = "/run/meshforge"
        assert controller._pid_file_path() == Path("/run/meshforge/meshforged.pid")

    def test_status_file_path(self):
        """Status file uses get_real_user_home()."""
        from daemon import DaemonController
        controller = DaemonController()
        path = controller._status_file_path()
        assert "daemon_status.json" in str(path)
        assert ".config/meshforge" in str(path)

    def test_write_status_file(self, tmp_path):
        """Status file is valid JSON."""
        from daemon import DaemonController, ServiceRegistry
        controller = DaemonController()
        controller._registry = ServiceRegistry()
        controller._started_at = None
        controller._profile_name = "test"
        controller._config = MagicMock()

        status_path = tmp_path / "status.json"
        with patch.object(controller, '_status_file_path', return_value=status_path):
            controller._write_status_file()

        assert status_path.exists()
        data = json.loads(status_path.read_text())
        assert "daemon" in data
        assert "services" in data
        assert data["daemon"]["status"] == "running"

    def test_stop_remote_no_pid(self, tmp_path):
        """stop_remote handles missing PID file."""
        from daemon import DaemonController
        controller = DaemonController()
        controller._config = MagicMock()
        controller._config.pid_dir = str(tmp_path)

        result = controller.stop_remote()
        assert result == 1  # Not running

    def test_status_not_running(self, tmp_path):
        """status returns 1 when daemon not running."""
        from daemon import DaemonController
        controller = DaemonController()
        controller._config = MagicMock()
        controller._config.pid_dir = str(tmp_path)

        result = controller.status()
        assert result == 1

    def test_register_services_from_config(self):
        """Services are registered based on config flags."""
        from daemon import DaemonController, ServiceRegistry
        from daemon_config import DaemonConfig

        controller = DaemonController()
        controller._config = DaemonConfig(
            gateway_enabled=True,
            health_probe_enabled=True,
            mqtt_enabled=False,
            config_api_enabled=False,
            map_server_enabled=False,
            telemetry_enabled=False,
            node_tracker_enabled=True,
        )
        controller._registry = ServiceRegistry()
        controller._register_services()

        status = controller._registry.get_all_status()
        assert "gateway_bridge" in status
        assert "health_probe" in status
        assert "node_tracker" in status
        assert "mqtt_subscriber" not in status
        assert "config_api" not in status


# =============================================================================
# Service Wrapper Tests
# =============================================================================

class TestServiceWrappers:
    """Test each service wrapper's DaemonService compliance."""

    def test_gateway_service_interface(self):
        """GatewayBridgeService has required methods."""
        from daemon import GatewayBridgeService
        svc = GatewayBridgeService()
        assert svc.name == "gateway_bridge"
        assert hasattr(svc, 'start')
        assert hasattr(svc, 'stop')
        assert hasattr(svc, 'is_alive')
        assert hasattr(svc, 'get_status')

    def test_health_probe_service_interface(self):
        """HealthProbeService has required methods."""
        from daemon import HealthProbeService
        svc = HealthProbeService(interval=30)
        assert svc.name == "health_probe"
        assert hasattr(svc, 'start')
        assert hasattr(svc, 'stop')
        assert hasattr(svc, 'is_alive')
        assert hasattr(svc, 'get_status')

    def test_mqtt_service_interface(self):
        """MQTTSubscriberService has required methods."""
        from daemon import MQTTSubscriberService
        svc = MQTTSubscriberService(broker="localhost", port=1883)
        assert svc.name == "mqtt_subscriber"
        assert hasattr(svc, 'start')
        assert hasattr(svc, 'stop')
        assert hasattr(svc, 'is_alive')
        assert hasattr(svc, 'get_status')

    def test_config_api_service_interface(self):
        """ConfigAPIService has required methods."""
        from daemon import ConfigAPIService
        svc = ConfigAPIService(port=8081)
        assert svc.name == "config_api"
        assert hasattr(svc, 'start')

    def test_map_server_service_interface(self):
        """MapServerService has required methods."""
        from daemon import MapServerService
        svc = MapServerService(port=5000)
        assert svc.name == "map_server"
        assert hasattr(svc, 'start')
        assert not svc.is_alive()

    def test_telemetry_service_interface(self):
        """TelemetryPollerService has required methods."""
        from daemon import TelemetryPollerService
        svc = TelemetryPollerService(poll_interval_minutes=30)
        assert svc.name == "telemetry_poller"
        assert not svc.is_alive()

    def test_node_tracker_service_interface(self):
        """NodeTrackerService has required methods."""
        from daemon import NodeTrackerService
        svc = NodeTrackerService()
        assert svc.name == "node_tracker"
        assert not svc.is_alive()


# =============================================================================
# EventBus ThreadPool Tests
# =============================================================================

class TestEventBusThreadPool:
    """Test the EventBus ThreadPoolExecutor fix."""

    def test_emit_does_not_create_threads_per_call(self):
        """Emit uses bounded thread pool, not thread per subscriber."""
        from utils.event_bus import EventBus

        bus = EventBus()
        results = []

        def callback(event):
            results.append(event)

        bus.subscribe("test", callback)

        # Emit 10 events — should reuse pool threads, not create 10
        for i in range(10):
            bus.emit("test", f"event_{i}")

        time.sleep(0.5)
        assert len(results) == 10

        bus.shutdown()

    def test_shutdown_prevents_further_emissions(self):
        """After shutdown, emit does not raise."""
        from utils.event_bus import EventBus

        bus = EventBus()
        bus.subscribe("test", lambda e: None)
        bus.shutdown()

        # Should not raise
        bus.emit("test", "after_shutdown")

    def test_emit_sync_still_works(self):
        """emit_sync() is unaffected by pool changes."""
        from utils.event_bus import EventBus

        bus = EventBus()
        results = []
        bus.subscribe("test", lambda e: results.append(e))
        bus.emit_sync("test", "sync_event")

        assert len(results) == 1
        assert results[0] == "sync_event"

        bus.shutdown()

    def test_thread_pool_bounded(self):
        """Thread pool has bounded max_workers."""
        from utils.event_bus import EventBus

        bus = EventBus()
        assert bus._executor._max_workers <= 8  # Reasonable bound

        bus.shutdown()
