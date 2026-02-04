"""
TUI Smoke Tests - Verify all menu paths are accessible.

These tests catch import errors, missing methods, and wiring problems
without needing to mock every external service. Run on Pis after updates.
"""

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path


class TestTUIImports:
    """Verify all TUI components can be imported."""

    def test_main_module_imports(self):
        """Main TUI module should import without errors."""
        from launcher_tui.main import MeshForgeLauncher
        assert MeshForgeLauncher is not None

    def test_backend_imports(self):
        """Dialog backend should import."""
        from launcher_tui.backend import DialogBackend
        assert DialogBackend is not None

    def test_version_accessible(self):
        """Version should be accessible."""
        from __version__ import __version__
        assert __version__
        assert 'beta' in __version__ or '.' in __version__


class TestMainMenuMethods:
    """Verify all main menu options map to callable methods."""

    @pytest.fixture
    def tui_class(self):
        """Get the TUI class without instantiating."""
        from launcher_tui.main import MeshForgeLauncher
        return MeshForgeLauncher

    def test_primary_menu_methods_exist(self, tui_class):
        """All primary menu choices should have handler methods."""
        required_methods = [
            '_dashboard_menu',
            '_mesh_networks_menu',
            '_rf_menu',
            '_maps_menu',
            '_config_menu',
            '_system_menu',
        ]
        for method in required_methods:
            assert hasattr(tui_class, method), f"Missing primary menu method: {method}"

    def test_quick_actions_method_exists(self, tui_class):
        """Quick actions menu should exist."""
        assert hasattr(tui_class, '_quick_actions_menu') or hasattr(tui_class, '_quick_actions')

    def test_emergency_mode_method_exists(self, tui_class):
        """Emergency mode menu should exist."""
        assert hasattr(tui_class, '_emergency_mode_menu') or hasattr(tui_class, '_emergency_mode')


class TestMixinMethods:
    """Verify all mixins contribute their methods to the TUI class."""

    @pytest.fixture
    def tui_class(self):
        from launcher_tui.main import MeshForgeLauncher
        return MeshForgeLauncher

    def test_service_menu_mixin_methods(self, tui_class):
        """ServiceMenuMixin methods should be available."""
        service_methods = [
            '_service_menu',
            '_run_bridge',
            '_is_bridge_running',
            '_manage_service',
        ]
        for method in service_methods:
            assert hasattr(tui_class, method), f"Missing service method: {method}"

    def test_rf_tools_mixin_methods(self, tui_class):
        """RF tools methods should be available."""
        # Check for either the mixin method or alternative names
        rf_methods = ['_rf_tools_menu', '_rf_menu', '_rf_calculators']
        found = any(hasattr(tui_class, m) for m in rf_methods)
        assert found, f"Missing RF tools menu method. Checked: {rf_methods}"

    def test_rns_menu_mixin_methods(self, tui_class):
        """RNS menu methods should be available."""
        rns_methods = ['_rns_menu', '_rns_status', '_rns_services_menu']
        found = any(hasattr(tui_class, m) for m in rns_methods)
        assert found, f"Missing RNS menu method. Checked: {rns_methods}"

    def test_mqtt_mixin_methods(self, tui_class):
        """MQTT monitoring methods should be available."""
        mqtt_methods = ['_mqtt_menu', '_mqtt_monitor', '_mqtt_subscribe']
        found = any(hasattr(tui_class, m) for m in mqtt_methods)
        assert found, f"Missing MQTT method. Checked: {mqtt_methods}"

    def test_hardware_menu_mixin_methods(self, tui_class):
        """Hardware detection methods should be available."""
        hw_methods = ['_hardware_menu', '_detect_hardware', '_spi_setup']
        found = any(hasattr(tui_class, m) for m in hw_methods)
        assert found, f"Missing hardware method. Checked: {hw_methods}"

    def test_network_tools_mixin_methods(self, tui_class):
        """Network tools methods should be available."""
        net_methods = ['_network_menu', '_run_terminal_network', '_ping_test']
        found = any(hasattr(tui_class, m) for m in net_methods)
        assert found, f"Missing network tools method. Checked: {net_methods}"


class TestMixinFiles:
    """Verify all expected mixin files exist."""

    MIXIN_DIR = Path(__file__).parent.parent / 'src' / 'launcher_tui'

    EXPECTED_MIXINS = [
        'service_menu_mixin.py',
        'rf_tools_mixin.py',
        'rns_menu_mixin.py',
        'mqtt_mixin.py',
        'hardware_menu_mixin.py',
        'quick_actions_mixin.py',
        'emergency_mode_mixin.py',
        'network_tools_mixin.py',
        'system_tools_mixin.py',
        'meshtasticd_config_mixin.py',
    ]

    def test_critical_mixins_exist(self):
        """Critical mixin files should exist on disk."""
        for mixin in self.EXPECTED_MIXINS:
            mixin_path = self.MIXIN_DIR / mixin
            assert mixin_path.exists(), f"Missing mixin file: {mixin}"

    def test_mixin_files_have_class(self):
        """Each mixin file should define a Mixin class."""
        for mixin in self.EXPECTED_MIXINS:
            mixin_path = self.MIXIN_DIR / mixin
            if mixin_path.exists():
                content = mixin_path.read_text()
                # Check for class definition (any class with Mixin in name)
                assert 'class ' in content and 'Mixin' in content, \
                    f"Mixin file {mixin} should define a Mixin class"


class TestTUIInstantiation:
    """Test that TUI can be instantiated with mocked backend."""

    @patch('launcher_tui.main.DialogBackend')
    @patch('launcher_tui.main.HAS_STARTUP_CHECKS', False)
    def test_tui_instantiates(self, mock_backend):
        """TUI should instantiate without crashing."""
        mock_backend.return_value = MagicMock()

        from launcher_tui.main import MeshForgeLauncher

        # Patch the run method to prevent actual execution
        with patch.object(MeshForgeLauncher, 'run', return_value=None):
            tui = MeshForgeLauncher()
            assert tui is not None
            assert hasattr(tui, 'dialog')


class TestCriticalPaths:
    """Test critical user workflow paths exist."""

    @pytest.fixture
    def tui_class(self):
        from launcher_tui.main import MeshForgeLauncher
        return MeshForgeLauncher

    def test_service_status_path(self, tui_class):
        """User should be able to reach service status."""
        # Path: Dashboard -> Service Status OR Quick Actions -> Service Status
        assert hasattr(tui_class, '_service_menu') or hasattr(tui_class, '_show_service_status')

    def test_bridge_control_path(self, tui_class):
        """User should be able to start/stop bridge."""
        assert hasattr(tui_class, '_run_bridge')
        assert hasattr(tui_class, '_is_bridge_running')

    def test_rf_calculator_path(self, tui_class):
        """User should be able to access RF calculators."""
        rf_attrs = ['_rf_tools_menu', '_rf_menu', '_fspl_calculator', '_rf_calculators']
        found = any(hasattr(tui_class, attr) for attr in rf_attrs)
        assert found, "RF calculator path not found"

    def test_logs_access_path(self, tui_class):
        """User should be able to view logs."""
        log_attrs = ['_logs_menu', '_view_logs', '_show_logs']
        found = any(hasattr(tui_class, attr) for attr in log_attrs)
        assert found, "Logs access path not found"
