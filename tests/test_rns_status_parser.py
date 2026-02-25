"""
Tests for RNS status parser.

Tests the pure parsing logic using real rnstatus output samples.
No external dependencies or network access required.

Run: python3 -m pytest tests/test_rns_status_parser.py -v
"""

import pytest
import subprocess
from unittest.mock import patch, MagicMock
from pathlib import Path

import sys
import os

# Ensure src/ is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.rns_status_parser import (
    parse_rnstatus,
    RNSStatus,
    RNSInterface,
    InterfaceStatus,
    InterfaceMode,
    TrafficCounters,
    TransportStatus,
    _find_rnstatus_binary,
    run_rnstatus,
)


# ---------------------------------------------------------------------------
# Sample rnstatus outputs
# ---------------------------------------------------------------------------

FULL_OUTPUT = """\
 Shared Instance[rns/meshforge moc2 rns]
    Status    : Up
    Serving   : 0 programs
    Rate      : 1.00 Gbps
    Traffic   : ↑242 B  0 bps
                ↓978 B  0 bps

 AutoInterface[Default Interface]
    Status    : Up
    Mode      : Full
    Rate      : 10.00 Mbps
    Peers     : 1 reachable
    Traffic   : ↑239 B  0 bps
                ↓0 B    0 bps

 TCPInterface[HawaiiNet RNS/192.168.86.38:4242]
    Status    : Up
    Mode      : Full
    Rate      : 10.00 Mbps
    Traffic   : ↑439 B  0 bps
                ↓239 B  0 bps

 MeshtasticInterface[Meshtastic Gateway]
    Status    : Up
    Mode      : Full
    Rate      : 500.00 bps
    Traffic   : ↑239 B  0 bps
                ↓0 B    0 bps

 MeshtasticInterface[Meshtastic Short Turbo]
    Status    : Up
    Mode      : Gateway
    Rate      : 500.00 bps
    Traffic   : ↑239 B  0 bps
                ↓0 B    0 bps

 Transport Instance <1a7cc0821444f4977d6c0571141ce5f3> running
 Uptime is 58m and 33.58s
"""

ERROR_OUTPUT = "Could not get shared instance status"

DOWN_INTERFACE = """\
 TCPInterface[Dead Link]
    Status    : Down
    Mode      : Full
    Rate      : 10.00 Mbps
    Traffic   : ↑0 B  0 bps
                ↓0 B  0 bps
"""

RX_ONLY_OUTPUT = """\
 TCPInterface[Broken Link]
    Status    : Up
    Mode      : Full
    Rate      : 10.00 Mbps
    Traffic   : ↑0 B  0 bps
                ↓500 B  10 bps
"""

LARGE_TRAFFIC = """\
 TCPInterface[Busy Link]
    Status    : Up
    Mode      : Full
    Rate      : 100.00 Mbps
    Traffic   : ↑1,234 KiB  500 Kbps
                ↓5,678 MiB  2 Mbps
"""


# ---------------------------------------------------------------------------
# Tests: parse_rnstatus
# ---------------------------------------------------------------------------


class TestParseInterfaceCount:
    def test_full_output_has_five_interfaces(self):
        result = parse_rnstatus(FULL_OUTPUT)
        assert len(result.interfaces) == 5

    def test_empty_output_has_no_interfaces(self):
        result = parse_rnstatus("")
        assert len(result.interfaces) == 0

    def test_single_interface(self):
        result = parse_rnstatus(DOWN_INTERFACE)
        assert len(result.interfaces) == 1


class TestParseInterfaceTypes:
    def test_shared_instance_type(self):
        result = parse_rnstatus(FULL_OUTPUT)
        shared = result.interfaces[0]
        assert shared.type_name == "Shared Instance"

    def test_auto_interface_type(self):
        result = parse_rnstatus(FULL_OUTPUT)
        auto = result.interfaces[1]
        assert auto.type_name == "AutoInterface"

    def test_tcp_interface_type(self):
        result = parse_rnstatus(FULL_OUTPUT)
        tcp = result.interfaces[2]
        assert tcp.type_name == "TCPInterface"

    def test_meshtastic_interface_type(self):
        result = parse_rnstatus(FULL_OUTPUT)
        mesh = result.interfaces[3]
        assert mesh.type_name == "MeshtasticInterface"


class TestParseDisplayNames:
    def test_shared_instance_name(self):
        result = parse_rnstatus(FULL_OUTPUT)
        assert result.interfaces[0].display_name == "rns/meshforge moc2 rns"

    def test_auto_interface_name(self):
        result = parse_rnstatus(FULL_OUTPUT)
        assert result.interfaces[1].display_name == "Default Interface"

    def test_tcp_interface_name_with_address(self):
        result = parse_rnstatus(FULL_OUTPUT)
        assert "192.168.86.38:4242" in result.interfaces[2].display_name

    def test_full_name_property(self):
        result = parse_rnstatus(FULL_OUTPUT)
        assert result.interfaces[2].full_name == "TCPInterface[HawaiiNet RNS/192.168.86.38:4242]"


class TestParseStatus:
    def test_all_up(self):
        result = parse_rnstatus(FULL_OUTPUT)
        assert all(i.status == InterfaceStatus.UP for i in result.interfaces)

    def test_down_interface(self):
        result = parse_rnstatus(DOWN_INTERFACE)
        assert result.interfaces[0].status == InterfaceStatus.DOWN

    def test_all_up_property_true(self):
        result = parse_rnstatus(FULL_OUTPUT)
        assert result.all_up is True

    def test_all_up_property_false_when_empty(self):
        result = parse_rnstatus("")
        assert result.all_up is False

    def test_any_down_property(self):
        result = parse_rnstatus(DOWN_INTERFACE)
        assert result.any_down is True


class TestParseMode:
    def test_full_mode(self):
        result = parse_rnstatus(FULL_OUTPUT)
        tcp = result.interfaces[2]
        assert tcp.mode == InterfaceMode.FULL

    def test_gateway_mode(self):
        result = parse_rnstatus(FULL_OUTPUT)
        turbo = result.interfaces[4]
        assert turbo.mode == InterfaceMode.GATEWAY

    def test_shared_instance_no_mode(self):
        """Shared Instance has Serving instead of Mode."""
        result = parse_rnstatus(FULL_OUTPUT)
        shared = result.interfaces[0]
        assert shared.mode == InterfaceMode.UNKNOWN

    def test_access_point_mode(self):
        """Mode with space in value must be parsed correctly."""
        output = """\
 TCPInterface[AP Node]
    Status    : Up
    Mode      : Access Point
    Rate      : 10.00 Mbps
    Traffic   : \u2191100 B  0 bps
                \u2193200 B  0 bps
"""
        result = parse_rnstatus(output)
        assert result.interfaces[0].mode == InterfaceMode.ACCESS_POINT


class TestParseRate:
    def test_gbps_rate(self):
        result = parse_rnstatus(FULL_OUTPUT)
        shared = result.interfaces[0]
        assert "1.00 Gbps" in shared.rate

    def test_mbps_rate(self):
        result = parse_rnstatus(FULL_OUTPUT)
        tcp = result.interfaces[2]
        assert "10.00 Mbps" in tcp.rate

    def test_bps_rate(self):
        result = parse_rnstatus(FULL_OUTPUT)
        mesh = result.interfaces[3]
        assert "500" in mesh.rate


class TestParsePeers:
    def test_auto_interface_peers(self):
        result = parse_rnstatus(FULL_OUTPUT)
        auto = result.interfaces[1]
        assert auto.peers == 1

    def test_tcp_interface_no_peers(self):
        result = parse_rnstatus(FULL_OUTPUT)
        tcp = result.interfaces[2]
        assert tcp.peers is None


class TestParseServing:
    def test_shared_instance_serving(self):
        result = parse_rnstatus(FULL_OUTPUT)
        shared = result.interfaces[0]
        assert shared.serving == 0

    def test_non_shared_no_serving(self):
        result = parse_rnstatus(FULL_OUTPUT)
        tcp = result.interfaces[2]
        assert tcp.serving is None


class TestParseTraffic:
    def test_tx_bytes(self):
        result = parse_rnstatus(FULL_OUTPUT)
        tcp = result.interfaces[2]
        assert tcp.tx.bytes_total == 439.0
        assert tcp.tx.bytes_unit == "B"

    def test_rx_bytes(self):
        result = parse_rnstatus(FULL_OUTPUT)
        tcp = result.interfaces[2]
        assert tcp.rx.bytes_total == 239.0
        assert tcp.rx.bytes_unit == "B"

    def test_tx_bps(self):
        result = parse_rnstatus(FULL_OUTPUT)
        shared = result.interfaces[0]
        assert shared.tx.bps == 0.0

    def test_rx_bps(self):
        result = parse_rnstatus(FULL_OUTPUT)
        shared = result.interfaces[0]
        assert shared.rx.bps == 0.0

    def test_large_traffic_comma_separated(self):
        result = parse_rnstatus(LARGE_TRAFFIC)
        iface = result.interfaces[0]
        assert iface.tx.bytes_total == 1234.0
        assert iface.tx.bytes_unit == "KiB"
        assert iface.tx.bps == 500.0
        assert iface.tx.bps_unit == "Kbps"

    def test_large_rx_values(self):
        result = parse_rnstatus(LARGE_TRAFFIC)
        iface = result.interfaces[0]
        assert iface.rx.bytes_total == 5678.0
        assert iface.rx.bytes_unit == "MiB"
        assert iface.rx.bps == 2.0
        assert iface.rx.bps_unit == "Mbps"


class TestTrafficHealth:
    def test_healthy_interface(self):
        result = parse_rnstatus(FULL_OUTPUT)
        tcp = result.interfaces[2]  # Has both TX and RX
        assert tcp.is_healthy is True

    def test_rx_only_is_unhealthy(self):
        result = parse_rnstatus(RX_ONLY_OUTPUT)
        iface = result.interfaces[0]
        assert iface.is_healthy is False
        assert iface.is_rx_only is True

    def test_zero_traffic(self):
        result = parse_rnstatus(DOWN_INTERFACE)
        iface = result.interfaces[0]
        assert iface.is_zero_traffic is True

    def test_rx_only_interfaces_property(self):
        result = parse_rnstatus(RX_ONLY_OUTPUT)
        assert len(result.rx_only_interfaces) == 1

    def test_zero_traffic_interfaces_property(self):
        """In FULL_OUTPUT all interfaces have TX > 0, so none are zero-traffic."""
        result = parse_rnstatus(FULL_OUTPUT)
        zero = result.zero_traffic_interfaces
        assert len(zero) == 0

    def test_zero_traffic_detected_in_down_interface(self):
        result = parse_rnstatus(DOWN_INTERFACE)
        assert len(result.zero_traffic_interfaces) == 1


class TestParseTransport:
    def test_transport_running(self):
        result = parse_rnstatus(FULL_OUTPUT)
        assert result.transport.running is True

    def test_transport_hash(self):
        result = parse_rnstatus(FULL_OUTPUT)
        assert result.transport.instance_hash == "1a7cc0821444f4977d6c0571141ce5f3"

    def test_uptime(self):
        result = parse_rnstatus(FULL_OUTPUT)
        assert "58m" in result.transport.uptime_str
        assert "33.58s" in result.transport.uptime_str

    def test_no_transport_in_minimal_output(self):
        result = parse_rnstatus(DOWN_INTERFACE)
        assert result.transport.running is False
        assert result.transport.uptime_str == ""


class TestErrorHandling:
    def test_error_output_sets_parse_error(self):
        result = parse_rnstatus(ERROR_OUTPUT)
        assert result.parse_error is not None
        assert len(result.interfaces) == 0

    def test_empty_string(self):
        result = parse_rnstatus("")
        assert result.parse_error is None
        assert len(result.interfaces) == 0

    def test_none_like_empty(self):
        """Whitespace-only output."""
        result = parse_rnstatus("   \n\n  ")
        assert len(result.interfaces) == 0

    def test_raw_output_preserved(self):
        result = parse_rnstatus(FULL_OUTPUT)
        assert result.raw_output == FULL_OUTPUT

    def test_shared_instance_error(self):
        result = parse_rnstatus("Could not get shared instance status")
        assert result.parse_error is not None

    def test_auth_error(self):
        result = parse_rnstatus("AuthenticationError: digest mismatch on shared instance")
        assert result.parse_error is not None


class TestFindBinary:
    @patch('shutil.which', return_value='/usr/bin/rnstatus')
    def test_found_on_path(self, mock_which):
        assert _find_rnstatus_binary() == '/usr/bin/rnstatus'

    @patch('shutil.which', return_value=None)
    @patch('utils.rns_status_parser.get_real_user_home')
    def test_found_in_local_bin(self, mock_home, mock_which):
        mock_home.return_value = Path('/home/testuser')
        candidate = Path('/home/testuser/.local/bin/rnstatus')
        with patch.object(Path, 'exists', return_value=True):
            result = _find_rnstatus_binary()
            assert result is not None

    @patch('shutil.which', return_value=None)
    @patch('utils.rns_status_parser.get_real_user_home')
    def test_not_found(self, mock_home, mock_which):
        mock_home.return_value = Path('/home/testuser')
        with patch.object(Path, 'exists', return_value=False):
            assert _find_rnstatus_binary() is None


class TestRunRnstatus:
    @patch('utils.rns_status_parser._find_rnstatus_binary', return_value=None)
    def test_missing_binary(self, mock_find):
        result = run_rnstatus()
        assert result.parse_error is not None
        assert "not found" in result.parse_error

    @patch('utils.rns_status_parser._find_rnstatus_binary', return_value='/usr/bin/rnstatus')
    @patch('subprocess.run')
    def test_successful_run(self, mock_run, mock_find):
        mock_run.return_value = MagicMock(
            stdout=FULL_OUTPUT,
            stderr="",
        )
        result = run_rnstatus()
        assert len(result.interfaces) == 5
        assert result.parse_error is None

    @patch('utils.rns_status_parser._find_rnstatus_binary', return_value='/usr/bin/rnstatus')
    @patch('subprocess.run', side_effect=subprocess.TimeoutExpired(cmd='rnstatus', timeout=15))
    def test_timeout(self, mock_run, mock_find):
        result = run_rnstatus()
        assert result.parse_error is not None
        assert "timed out" in result.parse_error

    @patch('utils.rns_status_parser._find_rnstatus_binary', return_value='/usr/bin/rnstatus')
    @patch('subprocess.run', side_effect=OSError("Permission denied"))
    def test_os_error(self, mock_run, mock_find):
        result = run_rnstatus()
        assert result.parse_error is not None
        assert "Failed" in result.parse_error
