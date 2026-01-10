"""
Tests for Meshtastic TCP Connection Manager

Tests resilient connection handling for meshtasticd which only supports
one TCP client connection at a time.
"""

import pytest
import threading
import time
from unittest.mock import Mock, patch, MagicMock
import socket
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))


class TestSafeCloseInterface:
    """Tests for safe_close_interface function"""

    def test_safe_close_handles_none(self):
        """safe_close_interface handles None gracefully"""
        from utils.meshtastic_connection import safe_close_interface
        # Should not raise
        safe_close_interface(None)

    def test_safe_close_handles_broken_pipe(self):
        """safe_close_interface handles BrokenPipeError"""
        from utils.meshtastic_connection import safe_close_interface

        mock_interface = MagicMock()
        mock_interface.close.side_effect = BrokenPipeError("Broken pipe")

        # Should not raise
        safe_close_interface(mock_interface)
        mock_interface.close.assert_called_once()

    def test_safe_close_handles_connection_reset(self):
        """safe_close_interface handles ConnectionResetError"""
        from utils.meshtastic_connection import safe_close_interface

        mock_interface = MagicMock()
        mock_interface.close.side_effect = ConnectionResetError("Connection reset")

        # Should not raise
        safe_close_interface(mock_interface)
        mock_interface.close.assert_called_once()

    def test_safe_close_handles_os_error(self):
        """safe_close_interface handles OSError"""
        from utils.meshtastic_connection import safe_close_interface

        mock_interface = MagicMock()
        mock_interface.close.side_effect = OSError("Connection refused")

        # Should not raise
        safe_close_interface(mock_interface)


class TestMeshtasticConnectionManager:
    """Tests for the connection manager"""

    def test_import_connection_manager(self):
        """Connection manager module can be imported"""
        from utils.meshtastic_connection import MeshtasticConnectionManager
        assert MeshtasticConnectionManager is not None

    def test_singleton_pattern(self):
        """Connection manager uses singleton pattern"""
        from utils.meshtastic_connection import MeshtasticConnectionManager, get_connection_manager
        mgr1 = get_connection_manager()
        mgr2 = get_connection_manager()
        assert mgr1 is mgr2

    def test_default_host_port(self):
        """Connection manager has correct default host/port"""
        from utils.meshtastic_connection import MeshtasticConnectionManager
        mgr = MeshtasticConnectionManager()
        assert mgr.host == 'localhost'
        assert mgr.port == 4403

    def test_custom_host_port(self):
        """Connection manager accepts custom host/port"""
        from utils.meshtastic_connection import MeshtasticConnectionManager
        mgr = MeshtasticConnectionManager(host='192.168.1.100', port=4404)
        assert mgr.host == '192.168.1.100'
        assert mgr.port == 4404


class TestConnectionStatus:
    """Tests for connection status checking"""

    def test_is_available_when_port_open(self):
        """is_available returns True when port is reachable"""
        from utils.meshtastic_connection import MeshtasticConnectionManager

        with patch('socket.socket') as mock_socket:
            mock_sock = MagicMock()
            mock_socket.return_value.__enter__ = Mock(return_value=mock_sock)
            mock_socket.return_value.__exit__ = Mock(return_value=False)
            mock_sock.connect.return_value = None

            mgr = MeshtasticConnectionManager()
            assert mgr.is_available() is True

    def test_is_available_when_port_closed(self):
        """is_available returns False when port is not reachable"""
        from utils.meshtastic_connection import MeshtasticConnectionManager

        with patch('socket.socket') as mock_socket:
            mock_sock = MagicMock()
            mock_socket.return_value.__enter__ = Mock(return_value=mock_sock)
            mock_socket.return_value.__exit__ = Mock(return_value=False)
            mock_sock.connect.side_effect = socket.error("Connection refused")

            mgr = MeshtasticConnectionManager()
            assert mgr.is_available() is False

    def test_is_available_timeout(self):
        """is_available returns False on timeout"""
        from utils.meshtastic_connection import MeshtasticConnectionManager

        with patch('socket.socket') as mock_socket:
            mock_sock = MagicMock()
            mock_socket.return_value.__enter__ = Mock(return_value=mock_sock)
            mock_socket.return_value.__exit__ = Mock(return_value=False)
            mock_sock.connect.side_effect = socket.timeout("Connection timed out")

            mgr = MeshtasticConnectionManager()
            assert mgr.is_available() is False


class TestConnectionLocking:
    """Tests for connection locking to prevent concurrent access"""

    def test_acquire_lock(self):
        """Can acquire connection lock"""
        from utils.meshtastic_connection import MeshtasticConnectionManager
        mgr = MeshtasticConnectionManager()

        assert mgr.acquire_lock(timeout=1.0) is True
        mgr.release_lock()

    def test_lock_prevents_concurrent_access(self):
        """Lock prevents multiple concurrent acquisitions"""
        from utils.meshtastic_connection import MeshtasticConnectionManager
        mgr = MeshtasticConnectionManager()

        # First acquisition should succeed
        assert mgr.acquire_lock(timeout=1.0) is True

        # Second acquisition should fail (non-blocking)
        assert mgr.acquire_lock(timeout=0.1) is False

        mgr.release_lock()

    def test_lock_released_after_use(self):
        """Lock can be reacquired after release"""
        from utils.meshtastic_connection import MeshtasticConnectionManager
        mgr = MeshtasticConnectionManager()

        assert mgr.acquire_lock(timeout=1.0) is True
        mgr.release_lock()

        # Should be able to acquire again
        assert mgr.acquire_lock(timeout=1.0) is True
        mgr.release_lock()


class TestWithConnection:
    """Tests for the with_connection context manager"""

    def test_with_connection_success(self):
        """with_connection yields interface on success"""
        from utils.meshtastic_connection import MeshtasticConnectionManager

        mock_interface = MagicMock()

        with patch('utils.meshtastic_connection.MeshtasticConnectionManager._create_interface') as mock_create:
            mock_create.return_value = mock_interface

            mgr = MeshtasticConnectionManager()
            with mgr.with_connection() as iface:
                assert iface is mock_interface

    def test_with_connection_closes_interface(self):
        """with_connection closes interface after use"""
        from utils.meshtastic_connection import MeshtasticConnectionManager

        mock_interface = MagicMock()

        with patch('utils.meshtastic_connection.MeshtasticConnectionManager._create_interface') as mock_create:
            mock_create.return_value = mock_interface

            mgr = MeshtasticConnectionManager()
            with mgr.with_connection() as iface:
                pass

            # Interface should be closed
            mock_interface.close.assert_called_once()

    def test_with_connection_releases_lock_on_exception(self):
        """with_connection releases lock even on exception"""
        from utils.meshtastic_connection import MeshtasticConnectionManager

        mock_interface = MagicMock()

        with patch('utils.meshtastic_connection.MeshtasticConnectionManager._create_interface') as mock_create:
            mock_create.return_value = mock_interface

            mgr = MeshtasticConnectionManager()

            try:
                with mgr.with_connection() as iface:
                    raise ValueError("Test error")
            except ValueError:
                pass

            # Lock should be released - we can acquire it again
            assert mgr.acquire_lock(timeout=0.1) is True
            mgr.release_lock()


class TestRetryLogic:
    """Tests for connection retry logic"""

    def test_retry_on_connection_reset(self):
        """Retries connection on ConnectionResetError"""
        from utils.meshtastic_connection import MeshtasticConnectionManager

        mock_interface = MagicMock()
        call_count = 0

        def create_with_failure(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionResetError("Connection reset by peer")
            return mock_interface

        with patch('utils.meshtastic_connection.MeshtasticConnectionManager._create_interface') as mock_create:
            mock_create.side_effect = create_with_failure

            mgr = MeshtasticConnectionManager()
            with mgr.with_connection(max_retries=3) as iface:
                assert iface is mock_interface

            assert call_count == 2  # Failed once, succeeded on retry

    def test_max_retries_exceeded(self):
        """Raises exception when max retries exceeded"""
        from utils.meshtastic_connection import MeshtasticConnectionManager, ConnectionError

        with patch('utils.meshtastic_connection.MeshtasticConnectionManager._create_interface') as mock_create:
            mock_create.side_effect = ConnectionResetError("Connection reset by peer")

            mgr = MeshtasticConnectionManager()

            with pytest.raises(ConnectionError) as exc_info:
                with mgr.with_connection(max_retries=2) as iface:
                    pass

            assert "max retries" in str(exc_info.value).lower()


class TestGetNodes:
    """Tests for the get_nodes convenience method"""

    def test_get_nodes_returns_list(self):
        """get_nodes returns a list of node dictionaries"""
        from utils.meshtastic_connection import MeshtasticConnectionManager

        mock_interface = MagicMock()
        mock_node = MagicMock()
        mock_node.user = MagicMock()
        mock_node.user.id = '!12345678'
        mock_node.user.longName = 'Test Node'
        mock_node.user.shortName = 'TST'
        mock_interface.nodes = {'!12345678': mock_node}

        with patch('utils.meshtastic_connection.MeshtasticConnectionManager._create_interface') as mock_create:
            mock_create.return_value = mock_interface

            mgr = MeshtasticConnectionManager()
            nodes = mgr.get_nodes()

            assert isinstance(nodes, list)
            assert len(nodes) == 1
            assert nodes[0]['id'] == '!12345678'

    def test_get_nodes_returns_empty_on_error(self):
        """get_nodes returns empty list on connection error"""
        from utils.meshtastic_connection import MeshtasticConnectionManager

        with patch('utils.meshtastic_connection.MeshtasticConnectionManager._create_interface') as mock_create:
            mock_create.side_effect = ConnectionResetError("Connection failed")

            mgr = MeshtasticConnectionManager()
            nodes = mgr.get_nodes()

            assert nodes == []


class TestGetChannels:
    """Tests for the get_channels convenience method"""

    def test_get_channels_returns_list(self):
        """get_channels returns a list of channel dictionaries"""
        from utils.meshtastic_connection import MeshtasticConnectionManager

        mock_interface = MagicMock()
        mock_channel = MagicMock()
        mock_channel.role = 1  # PRIMARY
        mock_channel.settings = MagicMock()
        mock_channel.settings.name = 'TestChannel'
        mock_channel.settings.psk = b'test'
        mock_interface.localNode = MagicMock()
        mock_interface.localNode.channels = [mock_channel]

        with patch('utils.meshtastic_connection.MeshtasticConnectionManager._create_interface') as mock_create:
            mock_create.return_value = mock_interface

            mgr = MeshtasticConnectionManager()
            channels = mgr.get_channels()

            assert isinstance(channels, list)
            assert len(channels) == 1
            assert channels[0]['name'] == 'TestChannel'

    def test_get_channels_returns_empty_on_error(self):
        """get_channels returns empty list on connection error"""
        from utils.meshtastic_connection import MeshtasticConnectionManager

        with patch('utils.meshtastic_connection.MeshtasticConnectionManager._create_interface') as mock_create:
            mock_create.side_effect = ConnectionResetError("Connection failed")

            mgr = MeshtasticConnectionManager()
            channels = mgr.get_channels()

            assert channels == []
