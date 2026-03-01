"""
Tests for the AREDN Topology Overlay.

Tests cover:
- AREDNBackhaulLink dataclass properties
- GatewayReachability dataclass
- AREDNHealthSummary
- AREDNTopologyOverlay (with mocked AREDN client)
- LinkType classification
"""

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gateway.aredn_topology import (
    AREDNBackhaulLink,
    AREDNHealthSummary,
    AREDNTopologyOverlay,
    GatewayReachability,
    LinkType,
)


class TestLinkType:
    """Test LinkType enum."""

    def test_values(self):
        assert LinkType.RF.value == "rf"
        assert LinkType.DTD.value == "dtd"
        assert LinkType.TUN.value == "tun"
        assert LinkType.UNKNOWN.value == "unknown"


class TestAREDNBackhaulLink:
    """Test AREDNBackhaulLink dataclass."""

    def test_healthy_link(self):
        link = AREDNBackhaulLink(
            local_aredn_ip="10.10.0.1",
            remote_aredn_ip="10.10.0.2",
            link_quality=0.8,
        )
        assert link.is_healthy

    def test_degraded_link(self):
        link = AREDNBackhaulLink(
            local_aredn_ip="10.10.0.1",
            remote_aredn_ip="10.10.0.2",
            link_quality=0.3,
        )
        assert not link.is_healthy

    def test_zero_quality(self):
        link = AREDNBackhaulLink(
            local_aredn_ip="10.10.0.1",
            remote_aredn_ip="10.10.0.2",
            link_quality=0.0,
        )
        assert not link.is_healthy


class TestGatewayReachability:
    """Test GatewayReachability dataclass."""

    def test_lora_only(self):
        reach = GatewayReachability(node_id="!abc123", via_lora=True)
        assert not reach.has_aredn_path

    def test_aredn_path(self):
        reach = GatewayReachability(
            node_id="!abc123",
            via_lora=True,
            via_aredn=True,
            aredn_router_ip="10.10.0.2",
            aredn_link_quality=0.9,
        )
        assert reach.has_aredn_path

    def test_aredn_path_no_quality(self):
        reach = GatewayReachability(
            node_id="!abc123",
            via_aredn=True,
            aredn_link_quality=0.0,
        )
        assert not reach.has_aredn_path


class TestAREDNHealthSummary:
    """Test AREDNHealthSummary dataclass."""

    def test_defaults(self):
        summary = AREDNHealthSummary()
        assert not summary.connected
        assert summary.neighbor_count == 0
        assert summary.remote_gateways == 0

    def test_populated(self):
        summary = AREDNHealthSummary(
            connected=True,
            local_router_ip="10.10.0.1",
            local_router_name="MF-Gateway-1",
            neighbor_count=3,
            healthy_links=2,
            degraded_links=1,
            remote_gateways=1,
            last_scan=datetime.now(),
        )
        assert summary.connected
        assert summary.neighbor_count == 3


class TestAREDNTopologyOverlay:
    """Test AREDNTopologyOverlay with mocked AREDN client."""

    def test_init_without_aredn(self):
        """Overlay initializes gracefully without AREDN module."""
        with patch('gateway.aredn_topology._HAS_AREDN', False):
            overlay = AREDNTopologyOverlay()
            assert not overlay.is_connected

    def test_init_with_ip(self):
        """Overlay accepts explicit router IP."""
        with patch('gateway.aredn_topology._HAS_AREDN', False):
            overlay = AREDNTopologyOverlay(router_ip="10.10.0.1")
            assert overlay._router_ip == "10.10.0.1"

    def test_discover_no_aredn(self):
        """Discover returns empty without AREDN module."""
        with patch('gateway.aredn_topology._HAS_AREDN', False):
            overlay = AREDNTopologyOverlay()
            links = overlay.discover_backhaul_links()
            assert links == []

    def test_get_reachability_no_gateways(self):
        """Reachability defaults to LoRa-only without gateways."""
        with patch('gateway.aredn_topology._HAS_AREDN', False):
            overlay = AREDNTopologyOverlay()
            reach = overlay.get_reachability("!abc123")
            assert reach.via_lora
            assert not reach.via_aredn

    def test_get_health_summary_disconnected(self):
        """Health summary reports disconnected."""
        with patch('gateway.aredn_topology._HAS_AREDN', False):
            overlay = AREDNTopologyOverlay()
            summary = overlay.get_health_summary()
            assert not summary.connected
            assert summary.neighbor_count == 0

    def test_get_links_empty(self):
        """Cached links start empty."""
        with patch('gateway.aredn_topology._HAS_AREDN', False):
            overlay = AREDNTopologyOverlay()
            assert overlay.get_links() == []

    def test_get_remote_gateways_empty(self):
        """Remote gateways start empty."""
        with patch('gateway.aredn_topology._HAS_AREDN', False):
            overlay = AREDNTopologyOverlay()
            assert overlay.get_remote_gateways() == {}

    def test_classify_link_rf(self):
        """RF link classification."""
        overlay = AREDNTopologyOverlay.__new__(AREDNTopologyOverlay)
        neighbor = MagicMock()
        neighbor.link_type = "RF"
        assert overlay._classify_link(neighbor) == LinkType.RF

    def test_classify_link_dtd(self):
        """DtD link classification."""
        overlay = AREDNTopologyOverlay.__new__(AREDNTopologyOverlay)
        neighbor = MagicMock()
        neighbor.link_type = "DTD"
        assert overlay._classify_link(neighbor) == LinkType.DTD

    def test_classify_link_tun(self):
        """Tunnel link classification."""
        overlay = AREDNTopologyOverlay.__new__(AREDNTopologyOverlay)
        neighbor = MagicMock()
        neighbor.link_type = "TUN"
        assert overlay._classify_link(neighbor) == LinkType.TUN

    def test_classify_link_unknown(self):
        """Unknown link type."""
        overlay = AREDNTopologyOverlay.__new__(AREDNTopologyOverlay)
        neighbor = MagicMock()
        neighbor.link_type = ""
        assert overlay._classify_link(neighbor) == LinkType.UNKNOWN
