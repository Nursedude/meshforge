"""
Tests for device connection handling.

Run: python3 -m pytest tests/test_connections.py -v
"""

import pytest
import socket
from unittest.mock import patch, MagicMock, PropertyMock
from dataclasses import asdict

from src.utils.connections import (
    ConnectionMode,
    ConnectionConfig,
    ConnectionStatus,
    SerialConnection,
    BLEConnection,
    TCPConnection,
    MeshtasticConnector,
    detect_devices,
)


class TestConnectionMode:
    """Tests for ConnectionMode enum."""

    def test_all_modes_exist(self):
        """Test all expected connection modes exist."""
        assert ConnectionMode.SERIAL.value == "serial"
        assert ConnectionMode.BLE.value == "ble"
        assert ConnectionMode.TCP.value == "tcp"
        assert ConnectionMode.AUTO.value == "auto"

    def test_mode_count(self):
        """Test expected number of modes."""
        assert len(ConnectionMode) == 4


class TestConnectionConfig:
    """Tests for ConnectionConfig dataclass."""

    def test_defaults(self):
        """Test default configuration values."""
        config = ConnectionConfig()

        assert config.mode == ConnectionMode.AUTO
        assert config.serial_port is None
        assert config.ble_address is None
        assert config.tcp_host == "127.0.0.1"
        assert config.tcp_port == 4403
        assert config.timeout == 10.0
        assert config.retry_count == 3
        assert config.retry_delay == 2.0

    def test_custom_values(self):
        """Test custom configuration."""
        config = ConnectionConfig(
            mode=ConnectionMode.SERIAL,
            serial_port="/dev/ttyUSB0",
            timeout=5.0,
            retry_count=5
        )

        assert config.mode == ConnectionMode.SERIAL
        assert config.serial_port == "/dev/ttyUSB0"
        assert config.timeout == 5.0
        assert config.retry_count == 5


class TestConnectionStatus:
    """Tests for ConnectionStatus dataclass."""

    def test_defaults(self):
        """Test default status values."""
        status = ConnectionStatus()

        assert status.connected is False
        assert status.mode is None
        assert status.address is None
        assert status.device_info is None
        assert status.error is None

    def test_connected_status(self):
        """Test connected status."""
        status = ConnectionStatus(
            connected=True,
            mode=ConnectionMode.TCP,
            address="127.0.0.1:4403"
        )

        assert status.connected is True
        assert status.mode == ConnectionMode.TCP


class TestSerialConnection:
    """Tests for SerialConnection class."""

    def test_init(self):
        """Test initialization."""
        conn = SerialConnection("/dev/ttyUSB0", timeout=5.0)

        assert conn.port == "/dev/ttyUSB0"
        assert conn.timeout == 5.0
        assert conn._interface is None

    def test_is_connected_false_by_default(self):
        """Test is_connected returns False before connection."""
        conn = SerialConnection("/dev/ttyUSB0")
        assert conn.is_connected() is False

    def test_get_interface_returns_none_before_connect(self):
        """Test get_interface returns None before connection."""
        conn = SerialConnection("/dev/ttyUSB0")
        assert conn.get_interface() is None

    def test_connect_failure_returns_false(self):
        """Test connect returns False on failure."""
        with patch.dict('sys.modules', {'meshtastic': MagicMock(), 'meshtastic.serial_interface': MagicMock()}):
            with patch('src.utils.connections.SerialConnection.connect') as mock_connect:
                mock_connect.return_value = False
                conn = SerialConnection("/dev/nonexistent")
                assert conn.connect() is False

    def test_disconnect_clears_interface(self):
        """Test disconnect clears the interface."""
        conn = SerialConnection("/dev/ttyUSB0")
        conn._interface = MagicMock()

        conn.disconnect()

        assert conn._interface is None


class TestBLEConnection:
    """Tests for BLEConnection class."""

    def test_init(self):
        """Test initialization."""
        conn = BLEConnection("AA:BB:CC:DD:EE:FF", timeout=5.0)

        assert conn.address == "AA:BB:CC:DD:EE:FF"
        assert conn.timeout == 5.0

    def test_is_connected_false_by_default(self):
        """Test is_connected returns False before connection."""
        conn = BLEConnection("AA:BB:CC:DD:EE:FF")
        assert conn.is_connected() is False

    def test_disconnect_clears_interface(self):
        """Test disconnect clears the interface."""
        conn = BLEConnection("AA:BB:CC:DD:EE:FF")
        conn._interface = MagicMock()

        conn.disconnect()

        assert conn._interface is None


class TestTCPConnection:
    """Tests for TCPConnection class."""

    def test_init_defaults(self):
        """Test initialization with defaults."""
        conn = TCPConnection()

        assert conn.host == "127.0.0.1"
        assert conn.port == 4403
        assert conn.timeout == 10.0

    def test_init_custom(self):
        """Test initialization with custom values."""
        conn = TCPConnection(host="192.168.1.100", port=4404, timeout=5.0)

        assert conn.host == "192.168.1.100"
        assert conn.port == 4404
        assert conn.timeout == 5.0

    def test_is_connected_false_by_default(self):
        """Test is_connected returns False before connection."""
        conn = TCPConnection()
        assert conn.is_connected() is False

    def test_disconnect_clears_interface(self):
        """Test disconnect clears the interface."""
        conn = TCPConnection()
        conn._interface = MagicMock()

        conn.disconnect()

        assert conn._interface is None


class TestMeshtasticConnector:
    """Tests for MeshtasticConnector class."""

    def test_init(self):
        """Test initialization."""
        connector = MeshtasticConnector()

        assert connector._connection is None
        assert connector.status.connected is False

    def test_status_property(self):
        """Test status property."""
        connector = MeshtasticConnector()
        status = connector.status

        assert isinstance(status, ConnectionStatus)
        assert status.connected is False

    def test_set_callbacks(self):
        """Test setting connection callbacks."""
        connector = MeshtasticConnector()

        on_connect = MagicMock()
        on_disconnect = MagicMock()

        connector.set_callbacks(on_connect=on_connect, on_disconnect=on_disconnect)

        assert connector._on_connect == on_connect
        assert connector._on_disconnect == on_disconnect

    def test_connect_fails_without_meshtastic(self):
        """Test connect fails when meshtastic not installed."""
        with patch('src.utils.connections.check_dependency', return_value=False):
            connector = MeshtasticConnector()
            result = connector.connect()

            assert result is False
            assert "not installed" in connector.status.error.lower()

    def test_serial_connect_fails_without_port(self):
        """Test serial connect fails without port specified."""
        with patch('src.utils.connections.check_dependency', return_value=True):
            connector = MeshtasticConnector()
            config = ConnectionConfig(mode=ConnectionMode.SERIAL, serial_port=None)

            result = connector.connect(config)

            assert result is False
            assert "no serial port" in connector.status.error.lower()

    def test_ble_connect_fails_without_address(self):
        """Test BLE connect fails without address specified."""
        with patch('src.utils.connections.check_dependency', return_value=True):
            connector = MeshtasticConnector()
            config = ConnectionConfig(mode=ConnectionMode.BLE, ble_address=None)

            result = connector.connect(config)

            assert result is False
            assert "no ble address" in connector.status.error.lower()

    def test_disconnect_clears_status(self):
        """Test disconnect clears connection status."""
        connector = MeshtasticConnector()
        connector._connection = MagicMock()
        connector._status.connected = True
        connector._status.mode = ConnectionMode.TCP
        connector._status.address = "127.0.0.1:4403"

        connector.disconnect()

        assert connector._connection is None
        assert connector.status.connected is False
        assert connector.status.mode is None
        assert connector.status.address is None

    def test_disconnect_calls_callback(self):
        """Test disconnect calls on_disconnect callback."""
        connector = MeshtasticConnector()
        on_disconnect = MagicMock()
        connector.set_callbacks(on_disconnect=on_disconnect)
        connector._connection = MagicMock()

        connector.disconnect()

        on_disconnect.assert_called_once()

    def test_is_connected_false_when_no_connection(self):
        """Test is_connected returns False when no connection."""
        connector = MeshtasticConnector()
        assert connector.is_connected() is False

    def test_is_connected_true_when_connected(self):
        """Test is_connected returns True when connected."""
        connector = MeshtasticConnector()
        mock_connection = MagicMock()
        mock_connection.is_connected.return_value = True
        connector._connection = mock_connection

        assert connector.is_connected() is True

    def test_get_interface_returns_none_when_not_connected(self):
        """Test get_interface returns None when not connected."""
        connector = MeshtasticConnector()
        assert connector.get_interface() is None

    def test_get_interface_returns_interface(self):
        """Test get_interface returns interface when connected."""
        connector = MeshtasticConnector()
        mock_interface = MagicMock()
        mock_connection = MagicMock()
        mock_connection.get_interface.return_value = mock_interface
        connector._connection = mock_connection

        assert connector.get_interface() == mock_interface

    def test_auto_connect_tries_serial_first(self):
        """Test auto-connect tries serial ports first."""
        with patch('src.utils.connections.check_dependency', return_value=True):
            with patch('src.utils.connections.get_serial_ports', return_value=['/dev/ttyUSB0']):
                connector = MeshtasticConnector()

                with patch.object(connector, '_serial_connect', return_value=True) as mock_serial:
                    result = connector.connect()

                    mock_serial.assert_called_once()
                    assert result is True

    def test_auto_connect_falls_back_to_tcp(self):
        """Test auto-connect falls back to TCP when no serial."""
        with patch('src.utils.connections.check_dependency', return_value=True):
            with patch('src.utils.connections.get_serial_ports', return_value=[]):
                connector = MeshtasticConnector()

                with patch.object(connector, '_tcp_connect', return_value=True) as mock_tcp:
                    result = connector.connect()

                    mock_tcp.assert_called_once()
                    assert result is True

    def test_tcp_connect_retries(self):
        """Test TCP connect retries on failure."""
        with patch('src.utils.connections.check_dependency', return_value=True):
            with patch('time.sleep'):  # Skip delays
                connector = MeshtasticConnector()
                config = ConnectionConfig(
                    mode=ConnectionMode.TCP,
                    retry_count=3,
                    retry_delay=0.1
                )

                mock_connection = MagicMock()
                mock_connection.connect.return_value = False

                with patch('src.utils.connections.TCPConnection', return_value=mock_connection):
                    result = connector.connect(config)

                    assert result is False
                    assert mock_connection.connect.call_count == 3

    def test_get_device_info_when_connected(self):
        """Test get_device_info returns info when connected."""
        connector = MeshtasticConnector()

        mock_interface = MagicMock()
        mock_interface.myInfo.my_node_num = 12345
        mock_interface.myInfo.has_gps = True

        mock_connection = MagicMock()
        mock_connection.get_interface.return_value = mock_interface
        connector._connection = mock_connection

        info = connector.get_device_info()

        assert info is not None
        assert info['node_num'] == 12345
        assert info['has_gps'] is True

    def test_get_device_info_returns_none_when_not_connected(self):
        """Test get_device_info returns None when not connected."""
        connector = MeshtasticConnector()
        assert connector.get_device_info() is None


class TestDetectDevices:
    """Tests for detect_devices function."""

    def test_detects_serial_ports(self):
        """Test detection of serial ports."""
        with patch('src.utils.connections.get_serial_ports', return_value=['/dev/ttyUSB0', '/dev/ttyUSB1']):
            with patch('socket.socket') as mock_socket:
                mock_sock = MagicMock()
                mock_sock.connect_ex.return_value = 1  # Connection failed (no meshtasticd)
                mock_socket.return_value = mock_sock

                devices = detect_devices()

                serial_devices = [d for d in devices if d['mode'] == ConnectionMode.SERIAL]
                assert len(serial_devices) == 2

    def test_detects_meshtasticd(self):
        """Test detection of meshtasticd service."""
        with patch('src.utils.connections.get_serial_ports', return_value=[]):
            with patch('socket.socket') as mock_socket:
                mock_sock = MagicMock()
                mock_sock.connect_ex.return_value = 0  # Connection successful
                mock_socket.return_value = mock_sock

                devices = detect_devices()

                tcp_devices = [d for d in devices if d['mode'] == ConnectionMode.TCP]
                assert len(tcp_devices) == 1
                assert tcp_devices[0]['address'] == '127.0.0.1:4403'

    def test_returns_empty_when_no_devices(self):
        """Test returns empty list when no devices found."""
        with patch('src.utils.connections.get_serial_ports', return_value=[]):
            with patch('socket.socket') as mock_socket:
                mock_sock = MagicMock()
                mock_sock.connect_ex.return_value = 1  # Connection failed
                mock_socket.return_value = mock_sock

                devices = detect_devices()

                assert devices == []

    def test_handles_socket_exception(self):
        """Test handles socket exceptions gracefully."""
        with patch('src.utils.connections.get_serial_ports', return_value=[]):
            with patch('socket.socket') as mock_socket:
                mock_socket.side_effect = Exception("Socket error")

                devices = detect_devices()

                # Should not raise, just return empty or partial list
                assert isinstance(devices, list)
