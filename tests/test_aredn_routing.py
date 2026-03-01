"""
Tests for AREDN topology integration into message routing.

Validates:
- AREDNTopologyOverlay.get_routing_hint() returns correct hints
- AREDNTopologyOverlay.get_all_routing_hints() aggregates gateway hints
- MessageRouter annotates AREDN hints when overlay is available
- MessageRouter works unchanged when overlay is None (backward compat)
- Edge cases: no gateways, low quality links, overlay errors

Run: python3 -m pytest tests/test_aredn_routing.py -v
"""

import os
import sys
import threading
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from gateway.aredn_topology import (
    AREDNTopologyOverlay,
    AREDNBackhaulLink,
    GatewayReachability,
    LinkType,
)
from gateway.config import GatewayConfig, RoutingRule
from gateway.message_routing import MessageRouter
from gateway.bridge_health import MessageOrigin
from gateway.canonical_message import CanonicalMessage, MessageType


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_overlay():
    """AREDNTopologyOverlay with mocked AREDN module (no real network)."""
    with patch.dict('sys.modules', {'utils.aredn': MagicMock()}):
        overlay = AREDNTopologyOverlay.__new__(AREDNTopologyOverlay)
        overlay._router_ip = "10.10.0.1"
        overlay._auto_detect = False
        overlay._poll_interval = 60
        overlay._lock = threading.Lock()
        overlay._last_scan = datetime.now()
        overlay._connected = True

        # Two backhaul links, one to a gateway
        overlay._links = [
            AREDNBackhaulLink(
                local_aredn_ip="10.10.0.1",
                remote_aredn_ip="10.10.0.2",
                remote_node_name="GW-NORTH",
                link_type=LinkType.RF,
                link_quality=0.85,
                neighbor_quality=0.80,
                last_checked=datetime.now(),
            ),
            AREDNBackhaulLink(
                local_aredn_ip="10.10.0.1",
                remote_aredn_ip="10.10.0.3",
                remote_node_name="NODE-EAST",
                link_type=LinkType.DTD,
                link_quality=0.40,
                neighbor_quality=0.35,
                last_checked=datetime.now(),
            ),
        ]

        # Only the first link leads to a gateway
        overlay._remote_gateways = {"10.10.0.2": "GW-NORTH"}

        return overlay


@pytest.fixture
def empty_overlay():
    """AREDNTopologyOverlay with no links or gateways."""
    overlay = AREDNTopologyOverlay.__new__(AREDNTopologyOverlay)
    overlay._router_ip = "10.10.0.1"
    overlay._auto_detect = False
    overlay._poll_interval = 60
    overlay._lock = threading.Lock()
    overlay._last_scan = None
    overlay._connected = False
    overlay._links = []
    overlay._remote_gateways = {}
    return overlay


@pytest.fixture
def all_to_all_config():
    """Gateway config with all_to_all default route."""
    config = GatewayConfig()
    config.enabled = True
    config.default_route = "all_to_all"
    return config


@pytest.fixture
def make_msg():
    """Factory for creating test messages."""
    def _make(source_network="meshtastic", destination_id="!abcd1234",
              content="test message", is_broadcast=False, via_internet=False):
        msg = CanonicalMessage(
            source_network=source_network,
            source_address="!sender01",
            destination_address=destination_id if destination_id else None,
            content=content,
            message_type=MessageType.TEXT,
            is_broadcast=is_broadcast,
            metadata={},
        )
        msg.via_internet = via_internet
        return msg
    return _make


# =============================================================================
# AREDNTopologyOverlay.get_routing_hint() tests
# =============================================================================

class TestGetRoutingHint:
    """Tests for AREDNTopologyOverlay.get_routing_hint()."""

    def test_returns_hint_when_gateways_exist(self, mock_overlay):
        hint = mock_overlay.get_routing_hint("!abcd1234")
        assert hint["available"] is True
        assert hint["quality"] == 0.85
        assert hint["router_ip"] == "10.10.0.2"
        assert hint["link_type"] == "rf"
        assert hint["gateway_name"] == "GW-NORTH"

    def test_returns_unavailable_when_no_gateways(self, empty_overlay):
        hint = empty_overlay.get_routing_hint("!abcd1234")
        assert hint["available"] is False
        assert hint["quality"] == 0.0
        assert hint["router_ip"] == ""

    def test_gateway_name_populated(self, mock_overlay):
        hint = mock_overlay.get_routing_hint("!anynode")
        assert hint["gateway_name"] == "GW-NORTH"

    def test_uses_best_quality_link(self, mock_overlay):
        """When multiple gateways exist, get_reachability picks best quality."""
        # Add second gateway with lower quality
        mock_overlay._remote_gateways["10.10.0.3"] = "GW-SOUTH"
        # Link to 10.10.0.3 has quality 0.40, link to 10.10.0.2 has 0.85
        hint = mock_overlay.get_routing_hint("!anynode")
        assert hint["quality"] == 0.85  # Best quality link
        assert hint["router_ip"] == "10.10.0.2"


class TestGetAllRoutingHints:
    """Tests for AREDNTopologyOverlay.get_all_routing_hints()."""

    def test_returns_hints_for_all_gateways(self, mock_overlay):
        hints = mock_overlay.get_all_routing_hints()
        assert len(hints) == 1
        assert "10.10.0.2" in hints
        gw = hints["10.10.0.2"]
        assert gw["available"] is True
        assert gw["quality"] == 0.85
        assert gw["link_type"] == "rf"
        assert gw["gateway_name"] == "GW-NORTH"

    def test_empty_when_no_gateways(self, empty_overlay):
        hints = empty_overlay.get_all_routing_hints()
        assert hints == {}

    def test_filters_non_gateway_links(self, mock_overlay):
        """Non-gateway links should not appear in routing hints."""
        hints = mock_overlay.get_all_routing_hints()
        assert "10.10.0.3" not in hints  # NODE-EAST is not a gateway


# =============================================================================
# MessageRouter AREDN integration tests
# =============================================================================

class TestMessageRouterAREDN:
    """Tests for MessageRouter with AREDN overlay."""

    def test_backward_compat_no_overlay(self, all_to_all_config, make_msg):
        """Router works unchanged when no overlay is provided."""
        stats = {"bounced": 0}
        lock = threading.Lock()
        router = MessageRouter(all_to_all_config, stats, lock)
        assert router._aredn_overlay is None

        msg = make_msg(source_network="meshtastic")
        destinations = router.get_destination_networks(msg)
        assert "rns" in destinations
        assert "meshcore" in destinations

    def test_overlay_annotates_aredn_hint(self, all_to_all_config, mock_overlay, make_msg):
        """Router annotates message metadata with AREDN hint when overlay available."""
        stats = {"bounced": 0}
        lock = threading.Lock()
        router = MessageRouter(all_to_all_config, stats, lock, aredn_overlay=mock_overlay)

        msg = make_msg(source_network="meshtastic", destination_id="!target01")
        destinations = router.get_destination_networks(msg)

        # Destinations unchanged — AREDN adds hints, doesn't change routing
        assert "rns" in destinations
        assert "meshcore" in destinations

        # Check AREDN hint was annotated
        assert "aredn_hint" in msg.metadata
        hint = msg.metadata["aredn_hint"]
        assert hint["preferred"] is True
        assert hint["quality"] == 0.85
        assert hint["router_ip"] == "10.10.0.2"
        assert hint["link_type"] == "rf"

    def test_no_hint_when_overlay_has_no_gateways(self, all_to_all_config, empty_overlay, make_msg):
        """No AREDN hint when overlay has no gateways."""
        stats = {"bounced": 0}
        lock = threading.Lock()
        router = MessageRouter(all_to_all_config, stats, lock, aredn_overlay=empty_overlay)

        msg = make_msg(source_network="meshtastic")
        router.get_destination_networks(msg)

        assert "aredn_hint" not in msg.metadata

    def test_no_hint_for_broadcast_without_dest(self, all_to_all_config, mock_overlay, make_msg):
        """Broadcast messages without destination don't get AREDN hints."""
        stats = {"bounced": 0}
        lock = threading.Lock()
        router = MessageRouter(all_to_all_config, stats, lock, aredn_overlay=mock_overlay)

        msg = make_msg(source_network="meshtastic", destination_id="", is_broadcast=True)
        router.get_destination_networks(msg)

        assert "aredn_hint" not in msg.metadata

    def test_no_hint_when_quality_below_threshold(self, all_to_all_config, make_msg):
        """No AREDN hint when link quality is below 50%."""
        # Create overlay with low-quality link
        overlay = AREDNTopologyOverlay.__new__(AREDNTopologyOverlay)
        overlay._router_ip = "10.10.0.1"
        overlay._lock = threading.Lock()
        overlay._connected = True
        overlay._links = [
            AREDNBackhaulLink(
                local_aredn_ip="10.10.0.1",
                remote_aredn_ip="10.10.0.2",
                remote_node_name="GW-WEAK",
                link_type=LinkType.TUN,
                link_quality=0.30,
            ),
        ]
        overlay._remote_gateways = {"10.10.0.2": "GW-WEAK"}

        stats = {"bounced": 0}
        lock = threading.Lock()
        router = MessageRouter(all_to_all_config, stats, lock, aredn_overlay=overlay)

        msg = make_msg(source_network="meshtastic")
        router.get_destination_networks(msg)

        assert "aredn_hint" not in msg.metadata

    def test_overlay_error_does_not_break_routing(self, all_to_all_config, make_msg):
        """If overlay raises an exception, routing continues normally."""
        overlay = MagicMock()
        overlay.get_routing_hint.side_effect = ConnectionError("AREDN down")

        stats = {"bounced": 0}
        lock = threading.Lock()
        router = MessageRouter(all_to_all_config, stats, lock, aredn_overlay=overlay)

        msg = make_msg(source_network="meshtastic")
        destinations = router.get_destination_networks(msg)

        # Routing works despite overlay error
        assert "rns" in destinations
        assert "meshcore" in destinations
        assert "aredn_hint" not in msg.metadata

    def test_get_aredn_routing_info_with_overlay(self, all_to_all_config, mock_overlay):
        """get_aredn_routing_info returns gateway data when overlay available."""
        stats = {"bounced": 0}
        lock = threading.Lock()
        router = MessageRouter(all_to_all_config, stats, lock, aredn_overlay=mock_overlay)

        info = router.get_aredn_routing_info()
        assert info["available"] is True
        assert info["connected"] is True
        assert info["gateway_count"] == 1
        assert "10.10.0.2" in info["gateways"]

    def test_get_aredn_routing_info_without_overlay(self, all_to_all_config):
        """get_aredn_routing_info returns unavailable when no overlay."""
        stats = {"bounced": 0}
        lock = threading.Lock()
        router = MessageRouter(all_to_all_config, stats, lock)

        info = router.get_aredn_routing_info()
        assert info["available"] is False

    def test_internet_origin_still_filtered_with_overlay(self, all_to_all_config, mock_overlay, make_msg):
        """MeshCore filtering for internet-origin messages still works with overlay."""
        stats = {"bounced": 0}
        lock = threading.Lock()
        router = MessageRouter(all_to_all_config, stats, lock, aredn_overlay=mock_overlay)

        msg = make_msg(source_network="meshtastic", via_internet=True)
        destinations = router.get_destination_networks(msg)

        # MeshCore should still be filtered out
        assert "meshcore" not in destinations
        assert "rns" in destinations
