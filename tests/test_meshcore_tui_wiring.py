"""
Tests for MeshCore TUI-bridge API wiring (B1).

Tests the new gateway_cli functions and MeshCore handler dispatch.
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


# ── gateway_cli: test_meshcore_connection ──

class TestTestMeshcoreConnection:

    @patch('gateway.config.GatewayConfig')
    def test_serial_device_present(self, mock_gw):
        mc = MagicMock()
        mc.enabled = True
        mc.connection_type = "serial"
        mc.device_path = "/dev/ttyUSB1"
        config = MagicMock()
        config.meshcore = mc
        mock_gw.load.return_value = config

        with patch('os.path.exists', return_value=True):
            from gateway.gateway_cli import test_meshcore_connection
            result = test_meshcore_connection()
            assert result['reachable'] is True
            assert result['connection_type'] == "serial"

    @patch('gateway.config.GatewayConfig')
    def test_serial_device_missing(self, mock_gw):
        mc = MagicMock()
        mc.enabled = True
        mc.connection_type = "serial"
        mc.device_path = "/dev/ttyUSB99"
        config = MagicMock()
        config.meshcore = mc
        mock_gw.load.return_value = config

        with patch('os.path.exists', return_value=False):
            from gateway.gateway_cli import test_meshcore_connection
            result = test_meshcore_connection()
            assert result['reachable'] is False
            assert 'not found' in result['error']

    @patch('gateway.config.GatewayConfig')
    @patch('socket.socket')
    def test_tcp_reachable(self, mock_sock_cls, mock_gw):
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

        from gateway.gateway_cli import test_meshcore_connection
        result = test_meshcore_connection()
        assert result['reachable'] is True
        assert result['connection_type'] == "tcp"

    @patch('gateway.config.GatewayConfig')
    @patch('socket.socket')
    def test_tcp_refused(self, mock_sock_cls, mock_gw):
        mc = MagicMock()
        mc.enabled = True
        mc.connection_type = "tcp"
        mc.tcp_host = "192.168.1.10"
        mc.tcp_port = 4000
        config = MagicMock()
        config.meshcore = mc
        mock_gw.load.return_value = config

        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 111
        mock_sock_cls.return_value = mock_sock

        from gateway.gateway_cli import test_meshcore_connection
        result = test_meshcore_connection()
        assert result['reachable'] is False

    @patch('gateway.config.GatewayConfig')
    def test_disabled_meshcore(self, mock_gw):
        mc = MagicMock()
        mc.enabled = False
        config = MagicMock()
        config.meshcore = mc
        mock_gw.load.return_value = config

        from gateway.gateway_cli import test_meshcore_connection
        result = test_meshcore_connection()
        assert result['reachable'] is False
        assert 'disabled' in result['error'].lower() or 'not configured' in result['error'].lower()

    @patch('gateway.config.GatewayConfig')
    def test_config_load_fails(self, mock_gw):
        mock_gw.load.side_effect = Exception("File not found")
        from gateway.gateway_cli import test_meshcore_connection
        result = test_meshcore_connection()
        assert result['reachable'] is False
        assert 'Config load failed' in result['error']

    @patch('gateway.config.GatewayConfig')
    def test_ble_unsupported(self, mock_gw):
        mc = MagicMock()
        mc.enabled = True
        mc.connection_type = "ble"
        config = MagicMock()
        config.meshcore = mc
        mock_gw.load.return_value = config

        from gateway.gateway_cli import test_meshcore_connection
        result = test_meshcore_connection()
        assert result['reachable'] is False
        assert 'BLE' in result['error']


# ── gateway_cli: send_meshcore_message ──

class TestSendMeshcoreMessage:

    def test_bridge_not_running(self):
        import gateway.gateway_cli as gcli
        original = gcli._active_bridge
        try:
            gcli._active_bridge = None
            result = gcli.send_meshcore_message("hello")
            assert result['sent'] is False
            assert 'not running' in result['error'].lower()
        finally:
            gcli._active_bridge = original

    def test_no_meshcore_handler(self):
        import gateway.gateway_cli as gcli
        original = gcli._active_bridge
        try:
            mock_bridge = MagicMock()
            mock_bridge._running = True
            mock_bridge._meshcore_handler = None
            gcli._active_bridge = mock_bridge
            result = gcli.send_meshcore_message("hello")
            assert result['sent'] is False
            assert 'not active' in result['error'].lower()
        finally:
            gcli._active_bridge = original

    def test_successful_send(self):
        import gateway.gateway_cli as gcli
        original = gcli._active_bridge
        try:
            mock_handler = MagicMock()
            mock_handler.send_text.return_value = True
            mock_bridge = MagicMock()
            mock_bridge._running = True
            mock_bridge._meshcore_handler = mock_handler
            gcli._active_bridge = mock_bridge

            result = gcli.send_meshcore_message("hello", destination="node1")
            assert result['sent'] is True
            mock_handler.send_text.assert_called_once_with("hello", destination="node1")
        finally:
            gcli._active_bridge = original

    def test_send_failure(self):
        import gateway.gateway_cli as gcli
        original = gcli._active_bridge
        try:
            mock_handler = MagicMock()
            mock_handler.send_text.return_value = False
            mock_bridge = MagicMock()
            mock_bridge._running = True
            mock_bridge._meshcore_handler = mock_handler
            gcli._active_bridge = mock_bridge

            result = gcli.send_meshcore_message("hello")
            assert result['sent'] is False
        finally:
            gcli._active_bridge = original

    def test_send_exception(self):
        import gateway.gateway_cli as gcli
        original = gcli._active_bridge
        try:
            mock_handler = MagicMock()
            mock_handler.send_text.side_effect = Exception("radio timeout")
            mock_bridge = MagicMock()
            mock_bridge._running = True
            mock_bridge._meshcore_handler = mock_handler
            gcli._active_bridge = mock_bridge

            result = gcli.send_meshcore_message("hello")
            assert result['sent'] is False
            assert 'radio timeout' in result['error']
        finally:
            gcli._active_bridge = original


# ── gateway_cli: get_meshcore_status ──

class TestGetMeshcoreStatus:

    def test_no_bridge(self):
        import gateway.gateway_cli as gcli
        original = gcli._active_bridge
        try:
            gcli._active_bridge = None
            result = gcli.get_meshcore_status()
            assert result['active'] is False
        finally:
            gcli._active_bridge = original

    def test_no_meshcore_handler(self):
        import gateway.gateway_cli as gcli
        original = gcli._active_bridge
        try:
            mock_bridge = MagicMock()
            mock_bridge._running = True
            mock_bridge._meshcore_handler = None
            gcli._active_bridge = mock_bridge
            result = gcli.get_meshcore_status()
            assert result['active'] is False
        finally:
            gcli._active_bridge = original

    def test_active_handler(self):
        import gateway.gateway_cli as gcli
        original = gcli._active_bridge
        try:
            mock_handler = MagicMock()
            mock_handler.is_connected = True
            mock_handler._device_path = "/dev/ttyUSB1"
            mock_bridge = MagicMock()
            mock_bridge._running = True
            mock_bridge._meshcore_handler = mock_handler
            mock_bridge.get_status.return_value = {
                'statistics': {
                    'meshcore_rx': 42,
                    'meshcore_tx': 17,
                    'meshcore_nodes': 5,
                }
            }
            gcli._active_bridge = mock_bridge
            result = gcli.get_meshcore_status()
            assert result['active'] is True
            assert result['connected'] is True
            assert result['device'] == "/dev/ttyUSB1"
        finally:
            gcli._active_bridge = original


# ── MeshCore handler dispatch ──

class TestMeshCoreHandlerDispatch:
    """Verify new menu items are registered in the dispatch table."""

    def test_handler_has_new_actions(self):
        """Check that the handler has the 4 new methods."""
        from launcher_tui.handlers.meshcore import MeshCoreHandler
        handler = MeshCoreHandler.__new__(MeshCoreHandler)
        assert hasattr(handler, '_meshcore_test_connection')
        assert hasattr(handler, '_meshcore_send_message')
        assert hasattr(handler, '_meshcore_live_monitor')
        assert hasattr(handler, '_meshcore_run_diagnostics')
