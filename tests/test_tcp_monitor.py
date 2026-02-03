"""
Tests for TCP/IP Connection Monitor.

Tests cover:
- TCPConnection dataclass
- TCPMonitor connection tracking
- NetworkScanner device discovery
- Connection metrics and statistics
- RTT measurement
"""

import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import socket
import threading
import time
import sys

# Add src to path for imports
_src_dir = Path(__file__).parent.parent / "src"
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

from monitoring.tcp_monitor import (
    TCPState,
    TCPConnection,
    NetworkDevice,
    TCPMonitor,
    NetworkScanner,
    measure_connection_rtt,
    TCP_STATE_MAP,
)


class TestTCPState:
    """Tests for TCPState enum."""

    def test_all_states_exist(self):
        """Test that all expected TCP states are defined."""
        expected_states = [
            "ESTABLISHED", "SYN_SENT", "SYN_RECV", "FIN_WAIT1",
            "FIN_WAIT2", "TIME_WAIT", "CLOSE", "CLOSE_WAIT",
            "LAST_ACK", "LISTEN", "CLOSING", "UNKNOWN"
        ]
        for state_name in expected_states:
            assert hasattr(TCPState, state_name)
            assert TCPState[state_name].value == state_name

    def test_state_map_coverage(self):
        """Test that TCP state map covers Linux kernel states."""
        # Linux kernel uses 1-11 for TCP states
        for i in range(1, 12):
            assert i in TCP_STATE_MAP
            assert isinstance(TCP_STATE_MAP[i], TCPState)


class TestTCPConnection:
    """Tests for TCPConnection dataclass."""

    def test_create_connection(self):
        """Test creating a TCP connection."""
        conn = TCPConnection(
            local_addr="192.168.1.100",
            local_port=4403,
            remote_addr="192.168.1.50",
            remote_port=54321,
            state=TCPState.ESTABLISHED,
        )
        assert conn.local_addr == "192.168.1.100"
        assert conn.local_port == 4403
        assert conn.remote_addr == "192.168.1.50"
        assert conn.remote_port == 54321
        assert conn.state == TCPState.ESTABLISHED

    def test_connection_id(self):
        """Test connection_id property."""
        conn = TCPConnection(
            local_addr="127.0.0.1",
            local_port=4403,
            remote_addr="127.0.0.1",
            remote_port=12345,
            state=TCPState.ESTABLISHED,
        )
        assert conn.connection_id == "127.0.0.1:4403->127.0.0.1:12345"

    def test_is_meshtasticd_local(self):
        """Test is_meshtasticd for local port 4403."""
        conn = TCPConnection(
            local_addr="0.0.0.0",
            local_port=4403,
            remote_addr="192.168.1.50",
            remote_port=54321,
            state=TCPState.LISTEN,
        )
        assert conn.is_meshtasticd is True

    def test_is_meshtasticd_remote(self):
        """Test is_meshtasticd for remote port 4403."""
        conn = TCPConnection(
            local_addr="192.168.1.100",
            local_port=54321,
            remote_addr="192.168.1.1",
            remote_port=4403,
            state=TCPState.ESTABLISHED,
        )
        assert conn.is_meshtasticd is True

    def test_is_not_meshtasticd(self):
        """Test is_meshtasticd for non-4403 ports."""
        conn = TCPConnection(
            local_addr="192.168.1.100",
            local_port=54321,
            remote_addr="192.168.1.1",
            remote_port=80,
            state=TCPState.ESTABLISHED,
        )
        assert conn.is_meshtasticd is False

    def test_is_web_interface(self):
        """Test is_web_interface property."""
        for port in [80, 443]:
            conn = TCPConnection(
                local_addr="192.168.1.100",
                local_port=54321,
                remote_addr="192.168.1.1",
                remote_port=port,
                state=TCPState.ESTABLISHED,
            )
            assert conn.is_web_interface is True

    def test_duration_seconds(self):
        """Test duration_seconds calculation."""
        now = datetime.now()
        conn = TCPConnection(
            local_addr="127.0.0.1",
            local_port=4403,
            remote_addr="127.0.0.1",
            remote_port=12345,
            state=TCPState.ESTABLISHED,
            first_seen=now - timedelta(seconds=60),
            last_seen=now,
        )
        assert conn.duration_seconds == pytest.approx(60.0, abs=1)

    def test_to_dict(self):
        """Test to_dict serialization."""
        conn = TCPConnection(
            local_addr="192.168.1.100",
            local_port=4403,
            remote_addr="192.168.1.50",
            remote_port=54321,
            state=TCPState.ESTABLISHED,
            pid=1234,
            process_name="meshtasticd",
            rtt_ms=5.5,
        )
        data = conn.to_dict()

        assert data["local_addr"] == "192.168.1.100"
        assert data["local_port"] == 4403
        assert data["remote_addr"] == "192.168.1.50"
        assert data["remote_port"] == 54321
        assert data["state"] == "ESTABLISHED"
        assert data["pid"] == 1234
        assert data["process_name"] == "meshtasticd"
        assert data["rtt_ms"] == 5.5
        assert data["is_meshtasticd"] is True
        assert "connection_id" in data
        assert "duration_seconds" in data


class TestNetworkDevice:
    """Tests for NetworkDevice dataclass."""

    def test_create_device(self):
        """Test creating a network device."""
        device = NetworkDevice(
            ip_address="192.168.1.100",
            hostname="meshtastic-node",
            ports={4403: "meshtasticd", 80: "http"},
            response_time_ms=5.2,
            is_meshtasticd=True,
            is_web_enabled=True,
        )
        assert device.ip_address == "192.168.1.100"
        assert device.hostname == "meshtastic-node"
        assert 4403 in device.ports
        assert device.is_meshtasticd is True

    def test_to_dict(self):
        """Test to_dict serialization."""
        device = NetworkDevice(
            ip_address="192.168.1.100",
            ports={4403: "meshtasticd"},
            is_meshtasticd=True,
        )
        data = device.to_dict()

        assert data["ip_address"] == "192.168.1.100"
        assert data["ports"] == {4403: "meshtasticd"}
        assert data["is_meshtasticd"] is True


class TestTCPMonitor:
    """Tests for TCPMonitor class."""

    def test_init_defaults(self):
        """Test default initialization."""
        monitor = TCPMonitor()
        assert monitor.poll_interval == 1.0
        assert 4403 in monitor.filter_ports
        assert monitor._running is False

    def test_init_custom_ports(self):
        """Test custom port filtering."""
        monitor = TCPMonitor(filter_ports={4403, 8080})
        assert monitor.filter_ports == {4403, 8080}

    def test_start_stop(self):
        """Test starting and stopping the monitor."""
        monitor = TCPMonitor(poll_interval=0.1)

        # Mock the polling to avoid actual system calls
        monitor._get_tcp_connections = Mock(return_value=[])

        monitor.start()
        assert monitor._running is True
        assert monitor._monitor_thread is not None
        assert monitor._monitor_thread.is_alive()

        time.sleep(0.2)  # Let it poll once

        monitor.stop()
        assert monitor._running is False

        # Wait for thread to stop
        time.sleep(0.2)
        assert not monitor._monitor_thread.is_alive()

    def test_get_connections_empty(self):
        """Test get_connections when no connections exist."""
        monitor = TCPMonitor()
        connections = monitor.get_connections()
        assert connections == []

    def test_get_connections_filtered_by_state(self):
        """Test filtering connections by state."""
        monitor = TCPMonitor()

        # Add some test connections
        monitor._connections = {
            "conn1": TCPConnection(
                local_addr="127.0.0.1", local_port=4403,
                remote_addr="127.0.0.1", remote_port=12345,
                state=TCPState.ESTABLISHED
            ),
            "conn2": TCPConnection(
                local_addr="127.0.0.1", local_port=4403,
                remote_addr="127.0.0.1", remote_port=12346,
                state=TCPState.LISTEN
            ),
        }

        established = monitor.get_connections(filter_state=TCPState.ESTABLISHED)
        assert len(established) == 1
        assert established[0].remote_port == 12345

        listening = monitor.get_connections(filter_state=TCPState.LISTEN)
        assert len(listening) == 1
        assert listening[0].remote_port == 12346

    def test_get_meshtasticd_connections(self):
        """Test getting only meshtasticd connections."""
        monitor = TCPMonitor()

        monitor._connections = {
            "mesh1": TCPConnection(
                local_addr="127.0.0.1", local_port=4403,
                remote_addr="192.168.1.50", remote_port=54321,
                state=TCPState.ESTABLISHED
            ),
            "web1": TCPConnection(
                local_addr="127.0.0.1", local_port=80,
                remote_addr="192.168.1.50", remote_port=12345,
                state=TCPState.ESTABLISHED
            ),
        }

        meshtasticd = monitor.get_meshtasticd_connections()
        assert len(meshtasticd) == 1
        assert meshtasticd[0].local_port == 4403

    def test_get_stats(self):
        """Test statistics collection."""
        monitor = TCPMonitor()

        monitor._connections = {
            "mesh1": TCPConnection(
                local_addr="127.0.0.1", local_port=4403,
                remote_addr="192.168.1.50", remote_port=54321,
                state=TCPState.ESTABLISHED
            ),
            "web1": TCPConnection(
                local_addr="127.0.0.1", local_port=80,
                remote_addr="192.168.1.50", remote_port=12345,
                state=TCPState.ESTABLISHED
            ),
        }
        monitor._stats["total_connections_seen"] = 10

        stats = monitor.get_stats()
        assert stats["active_connections"] == 2
        assert stats["meshtasticd_connections"] == 1
        assert stats["web_connections"] == 1
        assert stats["total_connections_seen"] == 10

    def test_callback_on_connection_added(self):
        """Test callback is called when connection is added."""
        monitor = TCPMonitor()
        added_connections = []

        def on_added(conn):
            added_connections.append(conn)

        monitor.on_connection_added = on_added

        # Mock system call to return a new connection
        mock_connections = [{
            "connection_id": "127.0.0.1:4403->192.168.1.50:54321",
            "local_addr": "127.0.0.1",
            "local_port": 4403,
            "remote_addr": "192.168.1.50",
            "remote_port": 54321,
            "state": TCPState.ESTABLISHED,
            "pid": None,
            "process_name": None,
        }]
        monitor._get_tcp_connections = Mock(return_value=mock_connections)

        monitor._poll_connections()

        assert len(added_connections) == 1
        assert added_connections[0].remote_addr == "192.168.1.50"

    def test_callback_on_connection_removed(self):
        """Test callback is called when connection is removed."""
        monitor = TCPMonitor()
        removed_connections = []

        def on_removed(conn):
            removed_connections.append(conn)

        monitor.on_connection_removed = on_removed

        # Add a connection first
        monitor._connections = {
            "conn1": TCPConnection(
                local_addr="127.0.0.1", local_port=4403,
                remote_addr="192.168.1.50", remote_port=54321,
                state=TCPState.ESTABLISHED
            ),
        }

        # Mock system call to return empty (connection closed)
        monitor._get_tcp_connections = Mock(return_value=[])

        monitor._poll_connections()

        assert len(removed_connections) == 1
        assert removed_connections[0].remote_addr == "192.168.1.50"


class TestNetworkScanner:
    """Tests for NetworkScanner class."""

    def test_init_defaults(self):
        """Test default initialization."""
        scanner = NetworkScanner()
        assert scanner.timeout == 1.0
        assert scanner.max_threads == 50
        assert 4403 in scanner.ports
        assert 80 in scanner.ports

    def test_init_custom_ports(self):
        """Test custom port configuration."""
        scanner = NetworkScanner(ports={4403: "meshtasticd"})
        assert scanner.ports == {4403: "meshtasticd"}

    @patch('socket.socket')
    def test_check_port_open(self, mock_socket_class):
        """Test port checking for open port."""
        mock_socket = MagicMock()
        mock_socket.connect_ex.return_value = 0  # Success
        mock_socket_class.return_value = mock_socket

        scanner = NetworkScanner()
        is_open, response_time = scanner._check_port("192.168.1.100", 4403)

        assert is_open is True
        assert response_time is not None
        assert response_time >= 0

    @patch('socket.socket')
    def test_check_port_closed(self, mock_socket_class):
        """Test port checking for closed port."""
        mock_socket = MagicMock()
        mock_socket.connect_ex.return_value = 111  # Connection refused
        mock_socket_class.return_value = mock_socket

        scanner = NetworkScanner()
        is_open, response_time = scanner._check_port("192.168.1.100", 4403)

        assert is_open is False
        assert response_time is None

    @patch('socket.socket')
    def test_scan_host_with_open_ports(self, mock_socket_class):
        """Test scanning host with open ports."""
        mock_socket = MagicMock()
        # 4403 open, others closed
        mock_socket.connect_ex.side_effect = lambda addr: 0 if addr[1] == 4403 else 111
        mock_socket_class.return_value = mock_socket

        scanner = NetworkScanner(ports={4403: "meshtasticd"})
        device = scanner.scan_host("192.168.1.100")

        assert device is not None
        assert device.ip_address == "192.168.1.100"
        assert device.is_meshtasticd is True
        assert 4403 in device.ports

    @patch('socket.socket')
    def test_scan_host_all_closed(self, mock_socket_class):
        """Test scanning host with all ports closed."""
        mock_socket = MagicMock()
        mock_socket.connect_ex.return_value = 111  # All closed
        mock_socket_class.return_value = mock_socket

        scanner = NetworkScanner()
        device = scanner.scan_host("192.168.1.100")

        assert device is None

    def test_scan_hosts_empty(self):
        """Test scanning empty host list."""
        scanner = NetworkScanner()
        scanner._check_port = Mock(return_value=(False, None))

        devices = scanner.scan_hosts([])
        assert devices == []

    @patch('socket.socket')
    def test_scan_hosts_parallel(self, mock_socket_class):
        """Test parallel host scanning."""
        mock_socket = MagicMock()
        # All ports open (for simplicity)
        mock_socket.connect_ex.return_value = 0
        mock_socket_class.return_value = mock_socket

        scanner = NetworkScanner(ports={4403: "meshtasticd"}, max_threads=10)

        hosts = [f"192.168.1.{i}" for i in range(1, 6)]
        devices = scanner.scan_hosts(hosts)

        assert len(devices) == 5
        assert all(d.is_meshtasticd for d in devices)

    def test_scan_subnet_invalid_cidr(self):
        """Test scanning with invalid CIDR notation."""
        scanner = NetworkScanner()
        devices = scanner.scan_subnet("invalid-cidr")
        assert devices == []

    def test_get_meshtasticd_devices(self):
        """Test filtering for meshtasticd devices."""
        scanner = NetworkScanner()
        scanner._devices = {
            "192.168.1.100": NetworkDevice(
                ip_address="192.168.1.100",
                ports={4403: "meshtasticd"},
                is_meshtasticd=True,
            ),
            "192.168.1.101": NetworkDevice(
                ip_address="192.168.1.101",
                ports={80: "http"},
                is_meshtasticd=False,
            ),
        }

        meshtasticd = scanner.get_meshtasticd_devices()
        assert len(meshtasticd) == 1
        assert meshtasticd[0].ip_address == "192.168.1.100"

    def test_stop_scan(self):
        """Test stopping an ongoing scan."""
        scanner = NetworkScanner()
        scanner.stop()
        assert scanner._stop_event.is_set()


class TestMeasureConnectionRTT:
    """Tests for measure_connection_rtt function."""

    @patch('socket.socket')
    def test_measure_rtt_success(self, mock_socket_class):
        """Test RTT measurement success."""
        mock_socket = MagicMock()
        mock_socket.connect.return_value = None
        mock_socket_class.return_value = mock_socket

        rtt = measure_connection_rtt("localhost", 4403, count=2)

        assert rtt is not None
        assert rtt >= 0

    @patch('socket.socket')
    def test_measure_rtt_failure(self, mock_socket_class):
        """Test RTT measurement when connection fails."""
        mock_socket = MagicMock()
        mock_socket.connect.side_effect = socket.error("Connection refused")
        mock_socket_class.return_value = mock_socket

        rtt = measure_connection_rtt("localhost", 4403, count=2)

        assert rtt is None


class TestProcNetTcpParsing:
    """Tests for /proc/net/tcp parsing (Linux only)."""

    def test_parse_proc_addr_ipv4(self):
        """Test parsing IPv4 address from /proc/net/tcp format."""
        monitor = TCPMonitor()

        # 127.0.0.1:4403 in proc format (little-endian hex)
        # 127.0.0.1 = 0x7F000001 -> little-endian = 0100007F
        addr, port = monitor._parse_proc_addr("0100007F:1133")

        assert addr == "127.0.0.1"
        assert port == 0x1133  # 4403

    def test_parse_proc_addr_zero(self):
        """Test parsing 0.0.0.0:0."""
        monitor = TCPMonitor()
        addr, port = monitor._parse_proc_addr("00000000:0000")

        assert addr == "0.0.0.0"
        assert port == 0


class TestIntegration:
    """Integration tests (require actual network access)."""

    @pytest.mark.skipif(
        not Path("/proc/net/tcp").exists(),
        reason="Linux /proc filesystem not available"
    )
    def test_get_connections_proc(self):
        """Test getting connections via /proc/net/tcp."""
        monitor = TCPMonitor()
        # Use proc method directly
        connections = monitor._get_connections_proc()

        # Should return a list (may be empty if no matching connections)
        assert isinstance(connections, list)
        for conn in connections:
            assert "local_addr" in conn
            assert "local_port" in conn
            assert "state" in conn

    def test_local_network_detection(self):
        """Test local network detection."""
        scanner = NetworkScanner()
        networks = scanner._get_local_networks()

        # Should return at least one network (or fallback)
        assert len(networks) >= 1
        # Should be valid CIDR notation
        for net in networks:
            assert "/" in net


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
