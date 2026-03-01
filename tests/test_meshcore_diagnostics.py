"""
Tests for MeshCore diagnostic checks (C3).

Tests check functions in core.diagnostics.checks.meshcore and
engine integration for the MESHCORE category.
"""

import os
import sys
import types
import pytest
from unittest.mock import patch, MagicMock, PropertyMock

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.diagnostics.models import CheckCategory, CheckStatus, CheckResult


# ── CheckCategory.MESHCORE exists ──

class TestCheckCategoryMeshCore:
    """Verify MESHCORE was added to the enum."""

    def test_meshcore_category_exists(self):
        assert hasattr(CheckCategory, 'MESHCORE')

    def test_meshcore_category_value(self):
        assert CheckCategory.MESHCORE.value == "meshcore"

    def test_meshcore_in_category_list(self):
        assert CheckCategory.MESHCORE in list(CheckCategory)


# ── check_meshcore_installed ──

class TestCheckMeshCoreInstalled:

    @patch('core.diagnostics.checks.meshcore._HAS_MESHCORE', True)
    @patch('core.diagnostics.checks.meshcore._meshcore_mod')
    def test_installed_with_version(self, mock_mod):
        mock_mod.__version__ = '1.2.3'
        from core.diagnostics.checks.meshcore import check_meshcore_installed
        result = check_meshcore_installed()
        assert result.status == CheckStatus.PASS
        assert result.category == CheckCategory.MESHCORE
        assert '1.2.3' in result.message
        assert result.details['version'] == '1.2.3'

    @patch('core.diagnostics.checks.meshcore._HAS_MESHCORE', True)
    @patch('core.diagnostics.checks.meshcore._meshcore_mod', MagicMock(spec=[]))
    def test_installed_no_version(self):
        from core.diagnostics.checks.meshcore import check_meshcore_installed
        result = check_meshcore_installed()
        assert result.status == CheckStatus.PASS
        assert 'unknown' in result.message

    @patch('core.diagnostics.checks.meshcore._HAS_MESHCORE', False)
    def test_not_installed(self):
        from core.diagnostics.checks.meshcore import check_meshcore_installed
        result = check_meshcore_installed()
        assert result.status == CheckStatus.WARN
        assert result.fix_hint == "pip install meshcore"


# ── check_meshcore_config ──

class TestCheckMeshCoreConfig:

    @patch('core.diagnostics.checks.meshcore._HAS_GW_CONFIG', False)
    def test_no_config_module(self):
        from core.diagnostics.checks.meshcore import check_meshcore_config
        result = check_meshcore_config()
        assert result.status == CheckStatus.SKIP

    @patch('core.diagnostics.checks.meshcore._HAS_GW_CONFIG', True)
    @patch('core.diagnostics.checks.meshcore._GatewayConfig')
    def test_config_load_fails(self, mock_gw):
        mock_gw.load.side_effect = Exception("File not found")
        from core.diagnostics.checks.meshcore import check_meshcore_config
        result = check_meshcore_config()
        assert result.status == CheckStatus.WARN
        assert "Could not load" in result.message

    @patch('core.diagnostics.checks.meshcore._HAS_GW_CONFIG', True)
    @patch('core.diagnostics.checks.meshcore._GatewayConfig')
    def test_no_meshcore_section(self, mock_gw):
        config = MagicMock()
        config.meshcore = None
        mock_gw.load.return_value = config
        from core.diagnostics.checks.meshcore import check_meshcore_config
        result = check_meshcore_config()
        assert result.status == CheckStatus.WARN
        assert "not configured" in result.message

    @patch('core.diagnostics.checks.meshcore._HAS_GW_CONFIG', True)
    @patch('core.diagnostics.checks.meshcore._GatewayConfig')
    def test_meshcore_disabled(self, mock_gw):
        mc = MagicMock()
        mc.enabled = False
        mc.connection_type = "serial"
        config = MagicMock()
        config.meshcore = mc
        mock_gw.load.return_value = config
        from core.diagnostics.checks.meshcore import check_meshcore_config
        result = check_meshcore_config()
        assert result.status == CheckStatus.WARN
        assert "disabled" in result.message

    @patch('core.diagnostics.checks.meshcore._HAS_GW_CONFIG', True)
    @patch('core.diagnostics.checks.meshcore._GatewayConfig')
    def test_invalid_connection_type(self, mock_gw):
        mc = MagicMock()
        mc.enabled = True
        mc.connection_type = "smoke_signals"
        config = MagicMock()
        config.meshcore = mc
        mock_gw.load.return_value = config
        from core.diagnostics.checks.meshcore import check_meshcore_config
        result = check_meshcore_config()
        assert result.status == CheckStatus.FAIL
        assert "Invalid" in result.message

    @patch('core.diagnostics.checks.meshcore._HAS_GW_CONFIG', True)
    @patch('core.diagnostics.checks.meshcore._GatewayConfig')
    def test_valid_serial_config(self, mock_gw):
        mc = MagicMock()
        mc.enabled = True
        mc.connection_type = "serial"
        mc.device_path = "/dev/ttyUSB1"
        config = MagicMock()
        config.meshcore = mc
        mock_gw.load.return_value = config
        from core.diagnostics.checks.meshcore import check_meshcore_config
        result = check_meshcore_config()
        assert result.status == CheckStatus.PASS
        assert "serial" in result.message
        assert result.details["device_path"] == "/dev/ttyUSB1"

    @patch('core.diagnostics.checks.meshcore._HAS_GW_CONFIG', True)
    @patch('core.diagnostics.checks.meshcore._GatewayConfig')
    def test_valid_tcp_config(self, mock_gw):
        mc = MagicMock()
        mc.enabled = True
        mc.connection_type = "tcp"
        mc.tcp_host = "192.168.1.10"
        mc.tcp_port = 4000
        config = MagicMock()
        config.meshcore = mc
        mock_gw.load.return_value = config
        from core.diagnostics.checks.meshcore import check_meshcore_config
        result = check_meshcore_config()
        assert result.status == CheckStatus.PASS
        assert result.details["tcp_host"] == "192.168.1.10"


# ── check_meshcore_device ──

class TestCheckMeshCoreDevice:

    @patch('core.diagnostics.checks.meshcore._HAS_GW_CONFIG', False)
    def test_no_config_module(self):
        from core.diagnostics.checks.meshcore import check_meshcore_device
        result = check_meshcore_device()
        assert result.status == CheckStatus.SKIP

    @patch('core.diagnostics.checks.meshcore._HAS_GW_CONFIG', True)
    @patch('core.diagnostics.checks.meshcore._GatewayConfig')
    def test_not_enabled(self, mock_gw):
        mc = MagicMock()
        mc.enabled = False
        config = MagicMock()
        config.meshcore = mc
        mock_gw.load.return_value = config
        from core.diagnostics.checks.meshcore import check_meshcore_device
        result = check_meshcore_device()
        assert result.status == CheckStatus.SKIP

    @patch('core.diagnostics.checks.meshcore._HAS_GW_CONFIG', True)
    @patch('core.diagnostics.checks.meshcore._GatewayConfig')
    @patch('os.path.exists', return_value=True)
    def test_serial_device_present(self, mock_exists, mock_gw):
        mc = MagicMock()
        mc.enabled = True
        mc.connection_type = "serial"
        mc.device_path = "/dev/ttyUSB1"
        config = MagicMock()
        config.meshcore = mc
        mock_gw.load.return_value = config
        from core.diagnostics.checks.meshcore import check_meshcore_device
        result = check_meshcore_device()
        assert result.status == CheckStatus.PASS
        assert "/dev/ttyUSB1" in result.message

    @patch('core.diagnostics.checks.meshcore._HAS_GW_CONFIG', True)
    @patch('core.diagnostics.checks.meshcore._GatewayConfig')
    @patch('os.path.exists', return_value=False)
    def test_serial_device_missing(self, mock_exists, mock_gw):
        mc = MagicMock()
        mc.enabled = True
        mc.connection_type = "serial"
        mc.device_path = "/dev/ttyUSB1"
        config = MagicMock()
        config.meshcore = mc
        mock_gw.load.return_value = config
        from core.diagnostics.checks.meshcore import check_meshcore_device
        result = check_meshcore_device()
        assert result.status == CheckStatus.FAIL
        assert "not found" in result.message

    @patch('core.diagnostics.checks.meshcore._HAS_GW_CONFIG', True)
    @patch('core.diagnostics.checks.meshcore._GatewayConfig')
    @patch('socket.socket')
    def test_tcp_device_reachable(self, mock_sock_cls, mock_gw):
        mc = MagicMock()
        mc.enabled = True
        mc.connection_type = "tcp"
        mc.tcp_host = "192.168.1.10"
        mc.tcp_port = 4000
        config = MagicMock()
        config.meshcore = mc
        mock_gw.load.return_value = config

        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0
        mock_sock_cls.return_value = mock_sock

        from core.diagnostics.checks.meshcore import check_meshcore_device
        result = check_meshcore_device()
        assert result.status == CheckStatus.PASS
        assert "192.168.1.10:4000" in result.message

    @patch('core.diagnostics.checks.meshcore._HAS_GW_CONFIG', True)
    @patch('core.diagnostics.checks.meshcore._GatewayConfig')
    @patch('socket.socket')
    def test_tcp_device_refused(self, mock_sock_cls, mock_gw):
        mc = MagicMock()
        mc.enabled = True
        mc.connection_type = "tcp"
        mc.tcp_host = "192.168.1.10"
        mc.tcp_port = 4000
        config = MagicMock()
        config.meshcore = mc
        mock_gw.load.return_value = config

        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 111  # Connection refused
        mock_sock_cls.return_value = mock_sock

        from core.diagnostics.checks.meshcore import check_meshcore_device
        result = check_meshcore_device()
        assert result.status == CheckStatus.FAIL
        assert "refused" in result.message

    @patch('core.diagnostics.checks.meshcore._HAS_GW_CONFIG', True)
    @patch('core.diagnostics.checks.meshcore._GatewayConfig')
    def test_ble_device_warns(self, mock_gw):
        mc = MagicMock()
        mc.enabled = True
        mc.connection_type = "ble"
        config = MagicMock()
        config.meshcore = mc
        mock_gw.load.return_value = config
        from core.diagnostics.checks.meshcore import check_meshcore_device
        result = check_meshcore_device()
        assert result.status == CheckStatus.WARN
        assert "BLE" in result.message


# ── check_meshcore_bridge_status ──

class TestCheckMeshCoreBridgeStatus:

    @patch('core.diagnostics.checks.meshcore._HAS_GW_CLI', False)
    def test_no_cli_module(self):
        from core.diagnostics.checks.meshcore import check_meshcore_bridge_status
        result = check_meshcore_bridge_status()
        assert result.status == CheckStatus.SKIP

    @patch('core.diagnostics.checks.meshcore._HAS_GW_CLI', True)
    @patch('core.diagnostics.checks.meshcore._is_gateway_running', return_value=False)
    def test_bridge_not_running(self, mock_running):
        from core.diagnostics.checks.meshcore import check_meshcore_bridge_status
        result = check_meshcore_bridge_status()
        assert result.status == CheckStatus.WARN
        assert "not running" in result.message

    @patch('core.diagnostics.checks.meshcore._HAS_GW_CLI', True)
    @patch('core.diagnostics.checks.meshcore._is_gateway_running', return_value=True)
    @patch('core.diagnostics.checks.meshcore._get_gateway_stats')
    def test_bridge_connected(self, mock_stats, mock_running):
        mock_stats.return_value = {
            'meshcore_connected': True,
            'meshcore_rx': 42,
            'meshcore_tx': 17,
        }
        from core.diagnostics.checks.meshcore import check_meshcore_bridge_status
        result = check_meshcore_bridge_status()
        assert result.status == CheckStatus.PASS
        assert "RX:42" in result.message
        assert "TX:17" in result.message

    @patch('core.diagnostics.checks.meshcore._HAS_GW_CLI', True)
    @patch('core.diagnostics.checks.meshcore._is_gateway_running', return_value=True)
    @patch('core.diagnostics.checks.meshcore._get_gateway_stats')
    def test_bridge_not_connected(self, mock_stats, mock_running):
        mock_stats.return_value = {
            'meshcore_connected': False,
        }
        from core.diagnostics.checks.meshcore import check_meshcore_bridge_status
        result = check_meshcore_bridge_status()
        assert result.status == CheckStatus.WARN
        assert "not connected" in result.message

    @patch('core.diagnostics.checks.meshcore._HAS_GW_CLI', True)
    @patch('core.diagnostics.checks.meshcore._is_gateway_running', return_value=True)
    @patch('core.diagnostics.checks.meshcore._get_gateway_stats', side_effect=Exception("oops"))
    def test_bridge_stats_error(self, mock_stats, mock_running):
        from core.diagnostics.checks.meshcore import check_meshcore_bridge_status
        result = check_meshcore_bridge_status()
        assert result.status == CheckStatus.WARN


# ── get_all_checks helper ──

class TestGetAllChecks:

    def test_returns_four_functions(self):
        from core.diagnostics.checks.meshcore import get_all_checks
        checks = get_all_checks()
        assert len(checks) == 4
        assert all(callable(c) for c in checks)


# ── Engine integration ──

class TestEngineIntegration:

    def test_engine_has_meshcore_in_check_map(self):
        """Verify the engine's run_category knows about MESHCORE."""
        from core.diagnostics.engine import DiagnosticEngine
        # Reset singleton for clean test
        with patch.object(DiagnosticEngine, '_instance', None):
            engine = DiagnosticEngine()
            # run_category should not error for MESHCORE
            with patch('core.diagnostics.engine.check_meshcore_installed') as m1, \
                 patch('core.diagnostics.engine.check_meshcore_config') as m2, \
                 patch('core.diagnostics.engine.check_meshcore_device') as m3, \
                 patch('core.diagnostics.engine.check_meshcore_bridge_status') as m4:
                dummy = CheckResult(
                    name="test",
                    category=CheckCategory.MESHCORE,
                    status=CheckStatus.PASS,
                    message="ok"
                )
                m1.return_value = dummy
                m2.return_value = dummy
                m3.return_value = dummy
                m4.return_value = dummy
                results = engine.run_category(CheckCategory.MESHCORE)
                assert len(results) == 4

    def test_run_all_includes_meshcore(self):
        """Verify run_all iterates over MESHCORE (it iterates CheckCategory)."""
        # CheckCategory is used in _run_all_internal via list(CheckCategory)
        categories = list(CheckCategory)
        assert CheckCategory.MESHCORE in categories
