"""
Tests for AREDN mesh network utilities.

Run: python3 -m pytest tests/test_aredn.py -v
"""

import pytest
from unittest.mock import patch, MagicMock

from src.utils.aredn import (
    LinkType,
    AREDNLink,
    AREDNService,
    AREDNNode,
    AREDNClient,
)


class TestLinkType:
    """Tests for LinkType enum."""

    def test_all_types_exist(self):
        """Test all expected link types exist."""
        assert LinkType.RF.value == "RF"
        assert LinkType.DTD.value == "DTD"
        assert LinkType.TUN.value == "TUN"
        assert LinkType.UNKNOWN.value == "Unknown"

    def test_link_type_count(self):
        """Test expected number of link types."""
        assert len(LinkType) == 4


class TestAREDNLink:
    """Tests for AREDNLink dataclass."""

    def test_creation(self):
        """Test creating a link."""
        link = AREDNLink(
            ip="10.0.0.1",
            hostname="KK6XXX-node",
            link_type=LinkType.RF
        )

        assert link.ip == "10.0.0.1"
        assert link.hostname == "KK6XXX-node"
        assert link.link_type == LinkType.RF
        assert link.link_quality == 0.0

    def test_snr_calculation(self):
        """Test SNR is calculated from signal and noise."""
        link = AREDNLink(
            ip="10.0.0.1",
            hostname="test",
            link_type=LinkType.RF,
            signal=-60,
            noise=-95
        )

        assert link.snr == 35  # -60 - (-95) = 35

    def test_snr_zero_when_no_signal(self):
        """Test SNR is 0 when no signal data."""
        link = AREDNLink(
            ip="10.0.0.1",
            hostname="test",
            link_type=LinkType.DTD
        )

        assert link.snr == 0

    def test_to_dict(self):
        """Test serialization to dict."""
        link = AREDNLink(
            ip="10.0.0.1",
            hostname="node1",
            link_type=LinkType.TUN,
            link_quality=0.95,
            neighbor_link_quality=0.90,
            tx_rate=300
        )

        d = link.to_dict()

        assert d['ip'] == "10.0.0.1"
        assert d['hostname'] == "node1"
        assert d['link_type'] == "TUN"
        assert d['link_quality'] == 0.95
        assert d['tx_rate'] == 300


class TestAREDNService:
    """Tests for AREDNService dataclass."""

    def test_creation(self):
        """Test creating a service."""
        svc = AREDNService(
            name="MeshChat",
            protocol="http",
            host="10.0.0.5",
            port=8080
        )

        assert svc.name == "MeshChat"
        assert svc.protocol == "http"
        assert svc.host == "10.0.0.5"
        assert svc.port == 8080

    def test_with_url(self):
        """Test service with URL."""
        svc = AREDNService(
            name="Web",
            protocol="http",
            host="node",
            port=80,
            url="http://node.local.mesh/"
        )

        assert svc.url == "http://node.local.mesh/"

    def test_to_dict(self):
        """Test serialization to dict."""
        svc = AREDNService(
            name="SSH",
            protocol="tcp",
            host="10.0.0.1",
            port=22
        )

        d = svc.to_dict()

        assert d['name'] == "SSH"
        assert d['protocol'] == "tcp"
        assert d['port'] == 22


class TestAREDNNode:
    """Tests for AREDNNode dataclass."""

    def test_creation_minimal(self):
        """Test creating a node with minimal data."""
        node = AREDNNode(hostname="KK6XXX-node")

        assert node.hostname == "KK6XXX-node"
        assert node.ip == ""
        assert node.links == []
        assert node.services == []

    def test_creation_full(self):
        """Test creating a node with full data."""
        node = AREDNNode(
            hostname="WH6GXZ-router",
            ip="10.0.0.1",
            firmware_version="3.24.4.0",
            model="MikroTik hAP ac lite",
            ssid="AREDN-10-v3",
            channel=177,
            frequency="5.885 GHz",
            mesh_status="active"
        )

        assert node.firmware_version == "3.24.4.0"
        assert node.model == "MikroTik hAP ac lite"
        assert node.channel == 177

    def test_base_url_with_ip(self):
        """Test base_url uses IP when available."""
        node = AREDNNode(hostname="test", ip="10.0.0.5")

        assert node.base_url == "http://10.0.0.5"

    def test_base_url_without_ip(self):
        """Test base_url uses hostname when no IP."""
        node = AREDNNode(hostname="mynode")

        assert node.base_url == "http://mynode.local.mesh"

    def test_to_dict(self):
        """Test serialization to dict."""
        link = AREDNLink(
            ip="10.0.0.2",
            hostname="neighbor",
            link_type=LinkType.RF
        )
        service = AREDNService(
            name="Web",
            protocol="http",
            host="10.0.0.1",
            port=80
        )
        node = AREDNNode(
            hostname="test",
            ip="10.0.0.1",
            links=[link],
            services=[service]
        )

        d = node.to_dict()

        assert d['hostname'] == "test"
        assert len(d['links']) == 1
        assert len(d['services']) == 1
        assert d['links'][0]['hostname'] == "neighbor"


class TestAREDNClient:
    """Tests for AREDNClient class."""

    def test_init_with_hostname(self):
        """Test initialization with hostname."""
        client = AREDNClient("KK6XXX-node")

        assert "KK6XXX-node" in client.base_url

    def test_init_with_ip(self):
        """Test initialization with IP address."""
        client = AREDNClient("10.0.0.1")

        assert "10.0.0.1" in client.base_url

    def test_default_timeout(self):
        """Test default timeout is set."""
        client = AREDNClient("test")

        assert client.timeout == 5

    def test_custom_timeout(self):
        """Test custom timeout."""
        client = AREDNClient("test", timeout=30)

        assert client.timeout == 30

    def test_get_sysinfo_no_connection(self):
        """Test get_sysinfo returns None on connection error."""
        client = AREDNClient("nonexistent.local.mesh", timeout=1)

        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.side_effect = Exception("Connection refused")

            result = client.get_sysinfo()

            assert result is None

    def test_get_sysinfo_success(self):
        """Test get_sysinfo returns dict response."""
        client = AREDNClient("10.0.0.1")

        mock_response = MagicMock()
        mock_response.read.return_value = b'''{
            "node": "KK6XXX-node",
            "api_version": "1.12",
            "model": "MikroTik hAP ac lite",
            "firmware_version": "3.24.4.0",
            "node_details": {
                "description": "Test node"
            }
        }'''
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch('urllib.request.urlopen', return_value=mock_response):
            result = client.get_sysinfo()

            assert result is not None
            assert isinstance(result, dict)
            assert result['node'] == "KK6XXX-node"
            assert result['api_version'] == "1.12"

    def test_get_neighbors_no_connection(self):
        """Test get_neighbors returns empty list on error."""
        client = AREDNClient("nonexistent", timeout=1)

        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.side_effect = Exception("Timeout")

            result = client.get_neighbors()

            assert result == []

    def test_check_connectivity_true(self):
        """Test check_connectivity returns True when node responds."""
        client = AREDNClient("10.0.0.1")

        mock_response = MagicMock()
        mock_response.read.return_value = b'{"node": "test"}'
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch('urllib.request.urlopen', return_value=mock_response):
            result = client.check_connectivity()

            assert result is True

    def test_check_connectivity_false(self):
        """Test check_connectivity returns False when node doesn't respond."""
        client = AREDNClient("10.0.0.1")

        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.side_effect = Exception("Connection refused")

            result = client.check_connectivity()

            assert result is False


class TestLinkQuality:
    """Tests for link quality calculations."""

    def test_rf_link_quality(self):
        """Test RF link with signal/noise data."""
        link = AREDNLink(
            ip="10.0.0.1",
            hostname="test",
            link_type=LinkType.RF,
            link_quality=0.85,
            signal=-65,
            noise=-95
        )

        assert link.link_quality == 0.85
        assert link.snr == 30

    def test_tunnel_link_no_rf_metrics(self):
        """Test tunnel link has no RF metrics."""
        link = AREDNLink(
            ip="172.16.0.1",
            hostname="tunnel-node",
            link_type=LinkType.TUN,
            link_quality=1.0
        )

        assert link.signal == 0
        assert link.noise == 0
        assert link.snr == 0
