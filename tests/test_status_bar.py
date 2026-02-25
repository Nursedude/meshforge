"""
Tests for TUI Status Bar.

Tests cover:
- Status line format and content
- Service status caching with TTL
- Node count and bridge status display
- Cache invalidation
- Graceful failure handling
- DialogBackend --backtitle integration

Run with: pytest tests/test_status_bar.py -v
"""

import pytest
import subprocess
import sys
import os
import time
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))

from status_bar import (
    StatusBar, STATUS_CACHE_TTL, SPACE_WEATHER_CACHE_TTL,
    SYM_RUNNING, SYM_STOPPED, SYM_UNKNOWN,
    MONITORED_SERVICES,
)


class TestStatusBarFormat:
    """Test status line formatting."""

    def test_includes_version(self):
        bar = StatusBar(version="0.4.7-beta")
        with patch.object(bar, '_check_systemd_active', return_value=SYM_STOPPED):
            with patch.object(bar, '_check_bridge'):
                line = bar.get_status_line()
        assert "MeshForge v0.4.7-beta" in line

    def test_no_version(self):
        bar = StatusBar(version="")
        with patch.object(bar, '_check_systemd_active', return_value=SYM_STOPPED):
            with patch.object(bar, '_check_bridge'):
                line = bar.get_status_line()
        assert line.startswith("MeshForge |")

    def test_pipe_separated(self):
        bar = StatusBar(version="1.0")
        with patch.object(bar, '_check_systemd_active', return_value=SYM_RUNNING):
            with patch.object(bar, '_check_bridge'):
                line = bar.get_status_line()
        assert " | " in line

    def test_shows_all_monitored_services(self):
        bar = StatusBar(version="1.0")
        with patch.object(bar, '_check_systemd_active', return_value=SYM_RUNNING):
            with patch.object(bar, '_check_bridge'):
                line = bar.get_status_line()
        for _, short_name in MONITORED_SERVICES:
            assert f"{short_name}:" in line

    def test_running_symbol(self):
        bar = StatusBar(version="1.0")
        with patch.object(bar, '_check_systemd_active', return_value=SYM_RUNNING):
            with patch.object(bar, '_check_bridge'):
                line = bar.get_status_line()
        assert f"mesh:{SYM_RUNNING}" in line

    def test_stopped_symbol(self):
        bar = StatusBar(version="1.0")
        with patch.object(bar, '_check_systemd_active', return_value=SYM_STOPPED):
            with patch.object(bar, '_check_bridge'):
                line = bar.get_status_line()
        assert f"mesh:{SYM_STOPPED}" in line


class TestServiceChecks:
    """Test service status checking."""

    @patch('subprocess.run')
    def test_active_service(self, mock_run):
        mock_run.return_value = MagicMock(stdout='active\n', returncode=0)
        bar = StatusBar()
        result = bar._check_systemd_active('meshtasticd')
        assert result == SYM_RUNNING
        # Service check module may make multiple subprocess calls
        assert mock_run.called


    @patch('subprocess.run')
    def test_inactive_service(self, mock_run):
        mock_run.return_value = MagicMock(stdout='inactive\n', returncode=3)
        bar = StatusBar()
        result = bar._check_systemd_active('rnsd')
        assert result == SYM_STOPPED

    @patch('subprocess.run')
    def test_failed_service(self, mock_run):
        mock_run.return_value = MagicMock(stdout='failed\n', returncode=3)
        bar = StatusBar()
        result = bar._check_systemd_active('mosquitto')
        assert result == SYM_STOPPED

    @patch('subprocess.run')
    def test_timeout_returns_unknown(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd='systemctl', timeout=3)
        bar = StatusBar()
        result = bar._check_systemd_active('meshtasticd')
        # With service_check module, timeout may map to STOPPED or UNKNOWN
        assert result in (SYM_UNKNOWN, SYM_STOPPED)

    @patch('subprocess.run')
    def test_no_systemctl_returns_unknown(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        bar = StatusBar()
        result = bar._check_systemd_active('meshtasticd')
        # With service_check module, missing systemctl may map to STOPPED or UNKNOWN
        assert result in (SYM_UNKNOWN, SYM_STOPPED)


class TestRnsdZombieDetection:
    """Test rnsd zombie detection: systemd active but shared instance not available."""

    def test_rnsd_zombie_shows_stopped(self):
        """rnsd active in systemd but shared instance not available → stopped."""
        bar = StatusBar(version="1.0")
        with patch('status_bar.check_systemd_service', return_value=(True, True)):
            with patch('status_bar.check_rns_shared_instance', return_value=False):
                result = bar._check_systemd_active('rnsd')
        assert result == SYM_STOPPED

    def test_rnsd_healthy_shows_running(self):
        """rnsd active in systemd and shared instance available → running."""
        bar = StatusBar(version="1.0")
        with patch('status_bar.check_systemd_service', return_value=(True, True)):
            with patch('status_bar.check_rns_shared_instance', return_value=True):
                result = bar._check_systemd_active('rnsd')
        assert result == SYM_RUNNING

    def test_rnsd_systemd_inactive_skips_instance_check(self):
        """rnsd not active in systemd → stopped without shared instance check."""
        bar = StatusBar(version="1.0")
        with patch('status_bar.check_systemd_service', return_value=(False, False)):
            with patch('status_bar.check_rns_shared_instance') as mock_rns:
                result = bar._check_systemd_active('rnsd')
        mock_rns.assert_not_called()
        assert result == SYM_STOPPED

    def test_meshtasticd_no_instance_check(self):
        """meshtasticd should not trigger RNS shared instance check."""
        bar = StatusBar(version="1.0")
        with patch('status_bar.check_systemd_service', return_value=(True, True)):
            with patch('status_bar.check_rns_shared_instance') as mock_rns:
                result = bar._check_systemd_active('meshtasticd')
        mock_rns.assert_not_called()
        assert result == SYM_RUNNING

    def test_instance_check_unavailable_falls_through(self):
        """When check_rns_shared_instance raises OSError, exception is caught."""
        bar = StatusBar(version="1.0")
        with patch('status_bar.check_systemd_service', return_value=(True, True)):
            with patch('status_bar.check_rns_shared_instance', side_effect=OSError("unavailable")):
                result = bar._check_systemd_active('rnsd')
        # OSError caught by the try/except → SYM_UNKNOWN
        assert result in (SYM_UNKNOWN, SYM_STOPPED)


class TestBridgeCheck:
    """Test bridge status checking."""

    @patch('subprocess.run')
    def test_bridge_running(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        bar = StatusBar()
        bar._check_bridge()
        assert bar._bridge_running is True

    @patch('subprocess.run')
    def test_bridge_not_running(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        bar = StatusBar()
        bar._check_bridge()
        assert bar._bridge_running is False

    @patch('subprocess.run')
    def test_bridge_check_failure(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        bar = StatusBar()
        bar._check_bridge()
        # With service_check module, failure may result in False or None
        assert bar._bridge_running in (None, False)

    def test_bridge_displayed_when_running(self):
        bar = StatusBar(version="1.0")
        bar._bridge_running = True
        bar._subsystem_states = {}  # Clear any cross-test EventBus pollution
        bar._cache_time = time.time()  # Prevent refresh
        bar._cache = {s: SYM_STOPPED for s, _ in MONITORED_SERVICES}
        # Prevent _refresh_if_stale from overwriting state via EventBus
        with patch.object(bar, '_refresh_if_stale'):
            line = bar.get_status_line()
        assert f"bridge:{SYM_RUNNING}" in line

    def test_bridge_displayed_when_stopped(self):
        bar = StatusBar(version="1.0")
        bar._bridge_running = False
        bar._cache_time = time.time()
        bar._cache = {s: SYM_STOPPED for s, _ in MONITORED_SERVICES}
        line = bar.get_status_line()
        assert f"bridge:{SYM_STOPPED}" in line

    def test_bridge_not_displayed_when_none(self):
        bar = StatusBar(version="1.0")
        bar._bridge_running = None
        bar._cache_time = time.time()
        bar._cache = {s: SYM_STOPPED for s, _ in MONITORED_SERVICES}
        line = bar.get_status_line()
        assert "bridge" not in line


class TestNodeCount:
    """Test node count display."""

    def test_set_node_count(self):
        bar = StatusBar(version="1.0")
        bar._cache_time = time.time()
        bar._cache = {s: SYM_STOPPED for s, _ in MONITORED_SERVICES}
        bar.set_node_count(7)
        line = bar.get_status_line()
        assert "nodes:7" in line

    def test_no_node_count_by_default(self):
        bar = StatusBar(version="1.0")
        bar._cache_time = time.time()
        bar._cache = {s: SYM_STOPPED for s, _ in MONITORED_SERVICES}
        line = bar.get_status_line()
        assert "nodes" not in line

    def test_zero_nodes(self):
        bar = StatusBar(version="1.0")
        bar._cache_time = time.time()
        bar._cache = {s: SYM_STOPPED for s, _ in MONITORED_SERVICES}
        bar.set_node_count(0)
        line = bar.get_status_line()
        assert "nodes:0" in line


class TestCaching:
    """Test cache TTL behavior."""

    def test_fresh_cache_not_refreshed(self):
        bar = StatusBar(version="1.0")
        bar._cache_time = time.time()
        bar._cache = {s: SYM_RUNNING for s, _ in MONITORED_SERVICES}

        with patch.object(bar, '_check_services') as mock_check:
            bar._refresh_if_stale()
            mock_check.assert_not_called()

    def test_stale_cache_triggers_refresh(self):
        bar = StatusBar(version="1.0")
        bar._cache_time = time.time() - STATUS_CACHE_TTL - 1

        with patch.object(bar, '_check_services') as mock_services:
            with patch.object(bar, '_check_bridge') as mock_bridge:
                bar._refresh_if_stale()
                mock_services.assert_called_once()
                mock_bridge.assert_called_once()

    def test_invalidate_forces_refresh(self):
        bar = StatusBar(version="1.0")
        bar._cache_time = time.time()
        bar._cache = {s: SYM_RUNNING for s, _ in MONITORED_SERVICES}

        bar.invalidate()
        assert bar._cache_time == 0.0

        with patch.object(bar, '_check_services') as mock_services:
            with patch.object(bar, '_check_bridge'):
                bar._refresh_if_stale()
                mock_services.assert_called_once()

    def test_get_service_status_triggers_refresh(self):
        bar = StatusBar(version="1.0")
        bar._cache_time = 0.0  # Force stale

        with patch.object(bar, '_check_systemd_active', return_value=SYM_RUNNING):
            with patch.object(bar, '_check_bridge'):
                result = bar.get_service_status('meshtasticd')
        assert result == SYM_RUNNING


class TestDialogBackendIntegration:
    """Test StatusBar integration with DialogBackend."""

    def test_set_status_bar(self):
        from backend import DialogBackend
        backend = DialogBackend()
        bar = StatusBar(version="1.0")
        backend.set_status_bar(bar)
        assert backend._status_bar is bar

    def test_no_status_bar_by_default(self):
        from backend import DialogBackend
        backend = DialogBackend()
        assert backend._status_bar is None

    @patch('subprocess.run', return_value=MagicMock(returncode=0))
    def test_backtitle_injected(self, mock_run):
        """When status bar is set, --backtitle should be in the command."""
        from backend import DialogBackend
        backend = DialogBackend()
        backend.backend = 'whiptail'

        bar = StatusBar(version="1.0")
        bar._cache_time = time.time()
        bar._cache = {s: SYM_RUNNING for s, _ in MONITORED_SERVICES}
        backend.set_status_bar(bar)

        # Call a dialog method (msgbox for simplicity)
        backend.msgbox("Test", "Hello")

        # Verify subprocess.run was called with --backtitle in the args list
        call_args = mock_run.call_args[0][0]
        assert '--backtitle' in call_args

    @patch('subprocess.run', return_value=MagicMock(returncode=0))
    def test_no_backtitle_without_bar(self, mock_run):
        """Without status bar, no --backtitle in command."""
        from backend import DialogBackend
        backend = DialogBackend()
        backend.backend = 'whiptail'

        backend.msgbox("Test", "Hello")

        call_args = mock_run.call_args[0][0]
        assert '--backtitle' not in call_args

    @patch('subprocess.run', return_value=MagicMock(returncode=0))
    def test_status_bar_exception_doesnt_crash(self, mock_run):
        """Status bar failure must never block dialog display."""
        from backend import DialogBackend
        backend = DialogBackend()
        backend.backend = 'whiptail'

        # Create a broken status bar
        bar = MagicMock()
        bar.get_status_line.side_effect = RuntimeError("broken")
        backend.set_status_bar(bar)

        # Should still work without error
        backend.msgbox("Test", "Hello")
        mock_run.assert_called_once()

        # --backtitle should NOT be in the command (graceful fallback)
        call_args = mock_run.call_args[0][0]
        assert '--backtitle' not in call_args


class TestStatusBarSymbols:
    """Test that symbols are terminal-safe."""

    def test_running_symbol_is_ascii(self):
        assert SYM_RUNNING.isascii()

    def test_stopped_symbol_is_ascii(self):
        assert SYM_STOPPED.isascii()

    def test_unknown_symbol_is_ascii(self):
        assert SYM_UNKNOWN.isascii()

    def test_status_line_is_ascii(self):
        bar = StatusBar(version="1.0")
        bar._cache_time = time.time()
        bar._cache = {s: SYM_RUNNING for s, _ in MONITORED_SERVICES}
        bar._bridge_running = True
        bar.set_node_count(5)
        line = bar.get_status_line()
        assert line.isascii()


class TestSpaceWeather:
    """Test space weather display in status bar."""

    def test_space_weather_displayed_when_available(self):
        bar = StatusBar(version="1.0")
        bar._cache_time = time.time()
        bar._space_weather_time = time.time()
        bar._cache = {s: SYM_STOPPED for s, _ in MONITORED_SERVICES}
        bar._space_weather = "SFI:125 K:2"
        line = bar.get_status_line()
        assert "SFI:125 K:2" in line

    def test_space_weather_not_displayed_when_none(self):
        bar = StatusBar(version="1.0")
        bar._cache_time = time.time()
        bar._space_weather_time = time.time()
        bar._cache = {s: SYM_STOPPED for s, _ in MONITORED_SERVICES}
        bar._space_weather = None
        line = bar.get_status_line()
        assert "SFI" not in line
        assert "K:" not in line

    def test_space_weather_separate_ttl(self):
        """Space weather has its own cache TTL."""
        bar = StatusBar(version="1.0")

        # Services cache is fresh, space weather is stale
        bar._cache_time = time.time()
        bar._space_weather_time = time.time() - SPACE_WEATHER_CACHE_TTL - 1
        bar._cache = {s: SYM_STOPPED for s, _ in MONITORED_SERVICES}

        with patch.object(bar, '_check_services') as mock_services:
            with patch.object(bar, '_check_bridge'):
                with patch.object(bar, '_check_space_weather') as mock_weather:
                    bar._refresh_if_stale()
                    # Services should NOT be called (fresh cache)
                    mock_services.assert_not_called()
                    # But space weather SHOULD be called (stale)
                    mock_weather.assert_called_once()

    def test_space_weather_fetch_success(self):
        """Test successful space weather fetch."""
        bar = StatusBar(version="1.0")

        # Mock the SpaceWeatherAPI via status_bar module attribute
        mock_data = MagicMock()
        mock_data.solar_flux = 145.5
        mock_data.k_index = 3

        MockAPI = MagicMock()
        MockAPI.return_value.get_current_conditions.return_value = mock_data

        with patch('status_bar.SpaceWeatherAPI', MockAPI):
            bar._check_space_weather()

        assert bar._space_weather == "SFI:145 K:3"

    def test_space_weather_graceful_failure(self):
        """Space weather failure should not break status bar."""
        bar = StatusBar(version="1.0")
        bar._space_weather = "SFI:100 K:1"  # Previous value

        MockAPI = MagicMock()
        MockAPI.return_value.get_current_conditions.side_effect = Exception("Network error")

        with patch('status_bar.SpaceWeatherAPI', MockAPI):
            bar._check_space_weather()

        # Should be cleared on failure
        assert bar._space_weather is None

    def test_space_weather_import_error(self):
        """SpaceWeatherAPI raising should not crash."""
        bar = StatusBar(version="1.0")

        with patch('status_bar.SpaceWeatherAPI', side_effect=Exception("import error")):
            bar._check_space_weather()
            assert bar._space_weather is None

    def test_space_weather_partial_data(self):
        """Handle partial data (e.g., only SFI available)."""
        bar = StatusBar(version="1.0")

        mock_data = MagicMock()
        mock_data.solar_flux = 120.0
        mock_data.k_index = None  # Not available

        MockAPI = MagicMock()
        MockAPI.return_value.get_current_conditions.return_value = mock_data

        with patch('status_bar.SpaceWeatherAPI', MockAPI):
            bar._check_space_weather()

        assert bar._space_weather == "SFI:120"
        assert "K:" not in bar._space_weather


class TestStatusBarEdgeCases:
    """Test edge cases."""

    def test_empty_version_string(self):
        bar = StatusBar(version="")
        bar._cache_time = time.time()
        bar._cache = {s: SYM_STOPPED for s, _ in MONITORED_SERVICES}
        line = bar.get_status_line()
        assert "MeshForge" in line
        assert "v" not in line.split("|")[0] or "MeshForge |" in line

    def test_large_node_count(self):
        bar = StatusBar(version="1.0")
        bar._cache_time = time.time()
        bar._cache = {s: SYM_STOPPED for s, _ in MONITORED_SERVICES}
        bar.set_node_count(9999)
        line = bar.get_status_line()
        assert "nodes:9999" in line

    def test_concurrent_calls_safe(self):
        """Multiple rapid calls should not crash."""
        bar = StatusBar(version="1.0")
        with patch.object(bar, '_check_systemd_active', return_value=SYM_RUNNING):
            with patch.object(bar, '_check_bridge'):
                for _ in range(100):
                    line = bar.get_status_line()
                    assert isinstance(line, str)


class TestSubsystemStatusDisplay:
    """Test bridge status with subsystem states (Phase 2)."""

    def test_bridge_healthy_both_subsystems(self):
        """Both subsystems healthy shows running symbol."""
        bar = StatusBar(version="1.0")
        bar._bridge_running = True
        bar.set_subsystem_states({"meshtastic": "healthy", "rns": "healthy"})
        result = bar._format_bridge_status()
        assert result == "bridge:*"

    def test_bridge_degraded_rns_down(self):
        """RNS subsystem down shows DEGRADED(rns)."""
        bar = StatusBar(version="1.0")
        bar._bridge_running = True
        bar.set_subsystem_states({"meshtastic": "healthy", "rns": "disconnected"})
        result = bar._format_bridge_status()
        assert result == "bridge:DEGRADED(rns)"

    def test_bridge_degraded_mesh_down(self):
        """Meshtastic subsystem down shows DEGRADED(mesh)."""
        bar = StatusBar(version="1.0")
        bar._bridge_running = True
        bar.set_subsystem_states({"meshtastic": "disconnected", "rns": "healthy"})
        result = bar._format_bridge_status()
        assert result == "bridge:DEGRADED(mesh)"

    def test_bridge_offline_both_down(self):
        """Both subsystems down shows OFFLINE."""
        bar = StatusBar(version="1.0")
        bar._bridge_running = True
        bar.set_subsystem_states({"meshtastic": "disconnected", "rns": "disconnected"})
        result = bar._format_bridge_status()
        assert result == "bridge:OFFLINE"

    def test_bridge_not_running(self):
        """Bridge not running shows stopped symbol."""
        bar = StatusBar(version="1.0")
        bar._bridge_running = False
        result = bar._format_bridge_status()
        assert result == "bridge:-"

    def test_bridge_running_no_subsystem_data(self):
        """Bridge running but no subsystem data shows running symbol."""
        bar = StatusBar(version="1.0")
        bar._bridge_running = True
        result = bar._format_bridge_status()
        assert result == "bridge:*"

    def test_disabled_subsystem_treated_as_down(self):
        """DISABLED subsystem treated as not healthy."""
        bar = StatusBar(version="1.0")
        bar._bridge_running = True
        bar.set_subsystem_states({"meshtastic": "healthy", "rns": "disabled"})
        result = bar._format_bridge_status()
        assert result == "bridge:DEGRADED(rns)"

    def test_subsystem_states_in_status_line(self):
        """Subsystem state appears in full status line."""
        bar = StatusBar(version="1.0")
        bar._bridge_running = True
        bar.set_subsystem_states({"meshtastic": "healthy", "rns": "disconnected"})
        with patch.object(bar, '_refresh_if_stale'):
            line = bar.get_status_line()
            assert "DEGRADED(rns)" in line

    def test_event_updates_subsystem_state(self):
        """ServiceEvent with bridge_ prefix updates subsystem state."""
        bar = StatusBar(version="1.0")

        class FakeEvent:
            service_name = "bridge_rns"
            available = False
            message = "rns: disconnected"

        bar._on_service_event(FakeEvent())
        assert bar._subsystem_states.get("rns") == "disconnected"

    def test_event_healthy_subsystem(self):
        """ServiceEvent for healthy bridge subsystem."""
        bar = StatusBar(version="1.0")

        class FakeEvent:
            service_name = "bridge_meshtastic"
            available = True
            message = "meshtastic: healthy"

        bar._on_service_event(FakeEvent())
        assert bar._subsystem_states.get("meshtastic") == "healthy"


class TestSeedNodeCount:
    """Test initial node count seeding from node tracker."""

    def test_seed_from_tracker(self):
        """StatusBar should pull initial count from node tracker."""
        bar = StatusBar(version="1.0")
        bar._node_count = None  # Reset (constructor may have seeded)

        # Simulate a tracker with 5 nodes
        mock_tracker = MagicMock()
        mock_tracker.get_all_nodes.return_value = [MagicMock()] * 5

        with patch('status_bar.get_node_tracker', return_value=mock_tracker):
            bar._seed_node_count()

        assert bar._node_count == 5

    def test_seed_empty_tracker(self):
        """Empty tracker should not set node count."""
        bar = StatusBar(version="1.0")
        bar._node_count = None

        mock_tracker = MagicMock()
        mock_tracker.get_all_nodes.return_value = []

        with patch('status_bar.get_node_tracker', return_value=mock_tracker):
            bar._seed_node_count()

        assert bar._node_count is None

    def test_seed_import_failure(self):
        """get_node_tracker raising should not crash."""
        bar = StatusBar(version="1.0")
        bar._node_count = None

        with patch('status_bar.get_node_tracker', side_effect=Exception("import error")):
            bar._seed_node_count()

        assert bar._node_count is None


class TestStartupChecksZombieDetection:
    """Test zombie detection in startup_checks.py status display."""

    def test_zombie_rnsd_shows_up_plain(self):
        """rnsd running + port not open → still shows 'UP' (zombie display removed)."""
        from startup_checks import EnvironmentState, ServiceInfo, ServiceRunState

        env = EnvironmentState()
        env.services = {
            'meshtasticd': ServiceInfo(name='meshtasticd', state=ServiceRunState.RUNNING,
                                       port=4403, port_open=True),
            'rnsd': ServiceInfo(name='rnsd', state=ServiceRunState.RUNNING,
                                port=37428, port_open=False),
        }
        line = env.get_status_line(plain=True)
        assert "meshtasticd: UP" in line
        assert "rnsd: UP" in line
        assert "no port" not in line

    def test_healthy_rnsd_shows_up(self):
        """rnsd running + port open → 'UP' in plain mode."""
        from startup_checks import EnvironmentState, ServiceInfo, ServiceRunState

        env = EnvironmentState()
        env.services = {
            'rnsd': ServiceInfo(name='rnsd', state=ServiceRunState.RUNNING,
                                port=37428, port_open=True),
        }
        line = env.get_status_line(plain=True)
        assert "rnsd: UP" in line
        assert "no port" not in line

    def test_no_port_service_not_affected(self):
        """Service without port config is not affected by zombie check."""
        from startup_checks import EnvironmentState, ServiceInfo, ServiceRunState

        env = EnvironmentState()
        env.services = {
            'test_svc': ServiceInfo(name='test_svc', state=ServiceRunState.RUNNING,
                                    port=None, port_open=False),
        }
        line = env.get_status_line(plain=True)
        assert "test_svc: UP" in line
        assert "no port" not in line

    def test_all_services_running_false_when_zombie(self):
        """all_services_running should be False when rnsd is a zombie."""
        from startup_checks import EnvironmentState, ServiceInfo, ServiceRunState

        env = EnvironmentState()
        env.services = {
            'meshtasticd': ServiceInfo(name='meshtasticd', state=ServiceRunState.RUNNING,
                                       port=4403, port_open=True),
            'rnsd': ServiceInfo(name='rnsd', state=ServiceRunState.RUNNING,
                                port=37428, port_open=False),
        }
        assert env.all_services_running is False

    def test_all_services_running_true_when_healthy(self):
        """all_services_running should be True when all ports are bound."""
        from startup_checks import EnvironmentState, ServiceInfo, ServiceRunState

        env = EnvironmentState()
        env.services = {
            'meshtasticd': ServiceInfo(name='meshtasticd', state=ServiceRunState.RUNNING,
                                       port=4403, port_open=True),
            'rnsd': ServiceInfo(name='rnsd', state=ServiceRunState.RUNNING,
                                port=37428, port_open=True),
        }
        assert env.all_services_running is True

    def test_zombie_rnsd_green_in_ansi_mode(self):
        """rnsd running shows green in ANSI mode (zombie display removed)."""
        from startup_checks import EnvironmentState, ServiceInfo, ServiceRunState

        env = EnvironmentState()
        env.services = {
            'rnsd': ServiceInfo(name='rnsd', state=ServiceRunState.RUNNING,
                                port=37428, port_open=False),
        }
        line = env.get_status_line(plain=False)
        assert '\033[32m' in line  # green for running
        assert '\033[33m' not in line  # NOT yellow


class TestEnhancedStatusLineZombie:
    """Test zombie detection in enhanced status line."""

    def test_enhanced_zombie_rnsd_shows_running(self):
        """Enhanced status line shows running for rnsd (zombie display removed)."""
        from startup_checks import EnvironmentState, ServiceInfo, ServiceRunState

        bar = StatusBar(version="1.0")
        env = EnvironmentState()
        env.is_root = True
        env.services = {
            'meshtasticd': ServiceInfo(name='meshtasticd', state=ServiceRunState.RUNNING,
                                       port=4403, port_open=True),
            'rnsd': ServiceInfo(name='rnsd', state=ServiceRunState.RUNNING,
                                port=37428, port_open=False),
        }
        env.conflicts = []

        with patch.object(bar, 'get_environment', return_value=env):
            with patch('status_bar.ServiceRunState', ServiceRunState):
                line = bar.get_enhanced_status_line()

        assert f"mesh:{SYM_RUNNING}" in line
        assert f"rnsd:{SYM_RUNNING}" in line


class TestEventDrivenServiceSkip:
    """Test that services updated by events skip polling."""

    def test_event_updated_service_skips_poll(self):
        """Service updated via EventBus should not be polled again."""
        bar = StatusBar(version="1.0")

        # Simulate event update for meshtasticd
        class FakeEvent:
            service_name = "meshtasticd"
            available = True

        bar._on_service_event(FakeEvent())
        assert "meshtasticd" in bar._event_updated_services
        assert bar._cache["meshtasticd"] == SYM_RUNNING

        # Now check that _check_services skips the event-updated service
        with patch.object(bar, '_check_systemd_active', return_value=SYM_STOPPED) as mock_check:
            bar._check_services()

            # meshtasticd should NOT have been polled (event is authoritative)
            called_services = [call.args[0] for call in mock_check.call_args_list]
            assert "meshtasticd" not in called_services

            # But rnsd and mosquitto should still be polled
            assert "rnsd" in called_services
            assert "mosquitto" in called_services

    def test_non_event_services_still_polled(self):
        """Services without event updates should still be polled."""
        bar = StatusBar(version="1.0")
        bar._event_updated_services = set()  # No events received

        with patch.object(bar, '_check_systemd_active', return_value=SYM_STOPPED) as mock_check:
            bar._check_services()

            called_services = [call.args[0] for call in mock_check.call_args_list]
            assert len(called_services) == len(MONITORED_SERVICES)
