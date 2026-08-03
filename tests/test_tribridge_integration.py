"""
Integration tests for 3-way bridging: Meshtastic <-> MeshCore <-> RNS.

Tests the full message routing lifecycle through all three protocols
without requiring hardware. Validates:
- MeshCore -> Meshtastic + RNS routing
- Meshtastic -> MeshCore + RNS routing
- RNS -> Meshtastic + MeshCore routing
- Internet-origin filtering (MQTT messages don't go to MeshCore)
- MessageRouter direction rules for all 3 protocols
- CanonicalMessage serialization across protocols

Run: python3 -m pytest tests/test_tribridge_integration.py -v
"""

import os
import sys
import threading
import time
from datetime import datetime
from queue import Queue, Empty
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from gateway.canonical_message import CanonicalMessage, MessageType, Protocol
from gateway.config import GatewayConfig, MeshCoreConfig, RoutingRule
from gateway.message_routing import MessageRouter
from gateway.bridge_health import MessageOrigin


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def tribridge_config():
    """Gateway config with all three protocols enabled."""
    config = GatewayConfig()
    config.enabled = True
    config.bridge_mode = "tri_bridge"
    config.default_route = "all_to_all"

    # Enable MeshCore
    config.meshcore = MeshCoreConfig(
        enabled=True,
        simulation_mode=True,
        device_path="/dev/ttyUSB1",
    )

    return config


@pytest.fixture
def bidirectional_config():
    """Config with explicit bidirectional routing rules."""
    config = GatewayConfig()
    config.enabled = True
    config.bridge_mode = "tri_bridge"
    config.default_route = "bidirectional"

    config.routing_rules = [
        RoutingRule(
            name="mesh_to_rns",
            enabled=True,
            direction="mesh_to_rns",
        ),
        RoutingRule(
            name="rns_to_mesh",
            enabled=True,
            direction="rns_to_mesh",
        ),
        RoutingRule(
            name="mesh_to_meshcore",
            enabled=True,
            direction="mesh_to_meshcore",
        ),
        RoutingRule(
            name="meshcore_to_mesh",
            enabled=True,
            direction="meshcore_to_mesh",
        ),
        RoutingRule(
            name="rns_to_meshcore",
            enabled=True,
            direction="rns_to_meshcore",
        ),
        RoutingRule(
            name="meshcore_to_rns",
            enabled=True,
            direction="meshcore_to_rns",
        ),
    ]

    config.meshcore = MeshCoreConfig(enabled=True, simulation_mode=True)
    return config


@pytest.fixture
def router(tribridge_config):
    """MessageRouter with all_to_all routing."""
    stats = {'bounced': 0}
    lock = threading.Lock()
    return MessageRouter(tribridge_config, stats, lock)


@pytest.fixture
def directional_router(bidirectional_config):
    """MessageRouter with explicit directional rules."""
    stats = {'bounced': 0}
    lock = threading.Lock()
    return MessageRouter(bidirectional_config, stats, lock)


def _make_msg(source_network, content="Hello mesh", source_id="node123",
              destination_id=None, is_broadcast=True, via_internet=False,
              origin=None):
    """Helper to create a message object for routing tests.

    Uses SimpleNamespace to provide both CanonicalMessage-style attributes
    (source_address/destination_address) and BridgedMessage-style attributes
    (source_id/destination_id) so tests work with both message types.
    """
    msg = SimpleNamespace(
        content=content,
        source_network=source_network,
        source_address=source_id,
        source_id=source_id,
        destination_address=destination_id or "",
        destination_id=destination_id or "",
        is_broadcast=is_broadcast,
        message_type=MessageType.TEXT,
        via_internet=via_internet,
        origin=origin or MessageOrigin.UNKNOWN,
        timestamp=datetime.now(),
        metadata={},
    )
    return msg


# =============================================================================
# TEST: 3-Way Routing — Direction Determination
# =============================================================================

class TestTriBridgeDestinations:
    """Test that MessageRouter.get_destination_networks returns correct targets."""

    def test_meshtastic_routes_to_meshcore_and_rns(self, router):
        """Meshtastic message should route to both MeshCore and RNS."""
        msg = _make_msg("meshtastic", "Hello from Meshtastic")
        destinations = router.get_destination_networks(msg)
        assert "meshcore" in destinations
        assert "rns" in destinations
        assert "meshtastic" not in destinations

    def test_meshcore_routes_to_meshtastic_and_rns(self, router):
        """MeshCore message should route to both Meshtastic and RNS."""
        msg = _make_msg("meshcore", "Hello from MeshCore")
        destinations = router.get_destination_networks(msg)
        assert "meshtastic" in destinations
        assert "rns" in destinations
        assert "meshcore" not in destinations

    def test_rns_routes_to_meshtastic_and_meshcore(self, router):
        """RNS message should route to both Meshtastic and MeshCore."""
        msg = _make_msg("rns", "Hello from RNS")
        destinations = router.get_destination_networks(msg)
        assert "meshtastic" in destinations
        assert "meshcore" in destinations
        assert "rns" not in destinations

    def test_never_routes_back_to_source(self, router):
        """Messages should never route back to their source network."""
        for network in ["meshtastic", "meshcore", "rns"]:
            msg = _make_msg(network, f"From {network}")
            destinations = router.get_destination_networks(msg)
            assert network not in destinations, \
                f"Message from {network} should not route back to {network}"


# =============================================================================
# TEST: Internet Origin Filtering
# =============================================================================

class TestInternetOriginFiltering:
    """MeshCore is pure radio — internet-origin messages must not reach it."""

    def test_mqtt_origin_excluded_from_meshcore(self, router):
        """MQTT-origin message should NOT route to MeshCore."""
        msg = _make_msg("meshtastic", "From MQTT", origin=MessageOrigin.MQTT)
        destinations = router.get_destination_networks(msg)
        assert "meshcore" not in destinations
        assert "rns" in destinations  # RNS is fine

    def test_via_internet_excluded_from_meshcore(self, router):
        """via_internet flag should block MeshCore routing."""
        msg = _make_msg("meshtastic", "From internet", via_internet=True)
        destinations = router.get_destination_networks(msg)
        assert "meshcore" not in destinations

    def test_radio_origin_reaches_meshcore(self, router):
        """Non-internet messages should still reach MeshCore."""
        msg = _make_msg("meshtastic", "Pure radio message")
        destinations = router.get_destination_networks(msg)
        assert "meshcore" in destinations

    def test_meshcore_origin_never_internet(self, router):
        """MeshCore messages are always radio — should reach all targets."""
        msg = _make_msg("meshcore", "Radio only")
        destinations = router.get_destination_networks(msg)
        assert "meshtastic" in destinations
        assert "rns" in destinations


# =============================================================================
# TEST: Directional Routing Rules
# =============================================================================

class TestDirectionalRouting:
    """Test specific directional routing rules for 3-way bridging.

    Tests both classifier and legacy routing paths for all three networks
    (meshtastic, meshcore, rns) including meshcore-specific directions.
    """

    def test_mesh_to_meshcore_rule_matches(self, directional_router):
        """mesh_to_meshcore rule allows Meshtastic->MeshCore."""
        msg = _make_msg("meshtastic", "To MeshCore")
        assert directional_router.should_bridge(msg) is True

    def test_meshcore_to_mesh_rule_matches(self, directional_router):
        """meshcore_to_mesh rule allows MeshCore->Meshtastic via classifier."""
        msg = _make_msg("meshcore", "To Meshtastic")
        assert directional_router.should_bridge(msg) is True

    @patch('gateway.message_routing.CLASSIFIER_AVAILABLE', False)
    def test_meshcore_to_mesh_rule_legacy(self, bidirectional_config):
        """meshcore_to_mesh rule allows MeshCore->Meshtastic (legacy path)."""
        stats = {'bounced': 0}
        lock = threading.Lock()
        router = MessageRouter(bidirectional_config, stats, lock)
        msg = _make_msg("meshcore", "To Meshtastic")
        assert router._should_bridge_legacy(msg) is True

    def test_rns_to_meshcore_rule_matches(self, directional_router):
        """rns_to_meshcore rule allows RNS->MeshCore."""
        msg = _make_msg("rns", "To MeshCore")
        assert directional_router.should_bridge(msg) is True

    def test_meshcore_to_rns_rule_matches(self, directional_router):
        """meshcore_to_rns rule allows MeshCore->RNS via classifier."""
        msg = _make_msg("meshcore", "To RNS")
        assert directional_router.should_bridge(msg) is True

    @patch('gateway.message_routing.CLASSIFIER_AVAILABLE', False)
    def test_meshcore_to_rns_rule_legacy(self, bidirectional_config):
        """meshcore_to_rns rule allows MeshCore->RNS (legacy path)."""
        stats = {'bounced': 0}
        lock = threading.Lock()
        router = MessageRouter(bidirectional_config, stats, lock)
        msg = _make_msg("meshcore", "To RNS")
        assert router._should_bridge_legacy(msg) is True

    @patch('gateway.message_routing.CLASSIFIER_AVAILABLE', False)
    def test_disabled_rule_blocks_legacy(self):
        """Disabled rules should not allow routing (legacy path)."""
        config = GatewayConfig()
        config.enabled = True
        config.default_route = "none"
        config.routing_rules = [
            RoutingRule(
                name="disabled_rule",
                enabled=False,
                direction="mesh_to_meshcore",
            ),
        ]
        config.meshcore = MeshCoreConfig(enabled=True, simulation_mode=True)
        stats = {'bounced': 0}
        lock = threading.Lock()
        router = MessageRouter(config, stats, lock)

        msg = _make_msg("meshtastic", "Should be blocked")
        assert router._should_bridge_legacy(msg) is False

    def test_gateway_disabled_blocks_all(self, tribridge_config):
        """Disabled gateway blocks all routing."""
        tribridge_config.enabled = False
        stats = {'bounced': 0}
        lock = threading.Lock()
        router = MessageRouter(tribridge_config, stats, lock)

        msg = _make_msg("meshtastic", "Gateway off")
        assert router.should_bridge(msg) is False


# =============================================================================
# TEST: CanonicalMessage — MeshCore Serialization
# =============================================================================

class TestCanonicalMessageMeshCore:
    """Test CanonicalMessage with MeshCore protocol."""

    def test_meshcore_source_network(self):
        """CanonicalMessage preserves meshcore as source network."""
        msg = CanonicalMessage(
            content="Test content",
            source_network="meshcore",
            source_address="mc:abc123",
        )
        assert msg.source_network == "meshcore"

    def test_meshcore_text_truncation(self):
        """MeshCore text output truncates to 160 bytes."""
        long_text = "A" * 300
        msg = CanonicalMessage(
            content=long_text,
            source_network="meshtastic",
            source_address="!abc123",
        )
        truncated = msg.to_meshcore_text()
        assert len(truncated.encode('utf-8')) <= 160

    def test_from_meshcore_event(self):
        """CanonicalMessage.from_meshcore creates valid message."""
        event = SimpleNamespace(
            payload=SimpleNamespace(
                text="Hello from MeshCore radio",
                sender=SimpleNamespace(
                    adv_name="MC-Node-1",
                    public_key=b'\x01\x02\x03\x04\x05\x06',
                ),
            ),
        )
        msg = CanonicalMessage.from_meshcore(event)
        assert msg.source_network == Protocol.MESHCORE.value
        assert "Hello from MeshCore radio" in msg.content

    def test_meshcore_should_bridge_filters_internet(self):
        """CanonicalMessage.should_bridge filters internet->meshcore."""
        msg = CanonicalMessage(
            content="Test",
            source_network="meshtastic",
            destination_network="meshcore",
            via_internet=True,
        )
        assert msg.should_bridge(filter_internet_to_meshcore=True) is False

    def test_meshcore_should_bridge_allows_radio(self):
        """CanonicalMessage.should_bridge allows radio->meshcore."""
        msg = CanonicalMessage(
            content="Test",
            source_network="meshtastic",
            destination_network="meshcore",
            via_internet=False,
        )
        assert msg.should_bridge(filter_internet_to_meshcore=True) is True


# =============================================================================
# TEST: 3-Way Message Flow Simulation
# =============================================================================

class TestTriBridgeMessageFlow:
    """Simulate full 3-way message flow using queues."""

    def test_meshcore_message_reaches_both_queues(self, router):
        """MeshCore message should be routable to both Mesh and RNS."""
        mesh_queue = Queue()
        rns_queue = Queue()

        msg = _make_msg("meshcore", "From MeshCore radio", source_id="mc:abc123")
        destinations = router.get_destination_networks(msg)

        # Simulate bridge loop dispatching to destination queues
        for dest in destinations:
            if dest == "meshtastic":
                mesh_queue.put(msg)
            elif dest == "rns":
                rns_queue.put(msg)

        assert mesh_queue.qsize() == 1
        assert rns_queue.qsize() == 1

    def test_meshtastic_message_reaches_meshcore_and_rns(self, router):
        """Meshtastic radio message routes to MeshCore and RNS."""
        meshcore_queue = Queue()
        rns_queue = Queue()

        msg = _make_msg("meshtastic", "From Meshtastic radio", source_id="!abc12345")
        destinations = router.get_destination_networks(msg)

        for dest in destinations:
            if dest == "meshcore":
                meshcore_queue.put(msg)
            elif dest == "rns":
                rns_queue.put(msg)

        assert meshcore_queue.qsize() == 1
        assert rns_queue.qsize() == 1

    def test_rns_message_reaches_meshtastic_and_meshcore(self, router):
        """RNS message routes to Meshtastic and MeshCore."""
        mesh_queue = Queue()
        meshcore_queue = Queue()

        msg = _make_msg("rns", "From RNS", source_id="rns:hash123")
        destinations = router.get_destination_networks(msg)

        for dest in destinations:
            if dest == "meshtastic":
                mesh_queue.put(msg)
            elif dest == "meshcore":
                meshcore_queue.put(msg)

        assert mesh_queue.qsize() == 1
        assert meshcore_queue.qsize() == 1

    def test_mqtt_message_skips_meshcore(self, router):
        """MQTT-origin message routes to RNS only, not MeshCore."""
        meshcore_queue = Queue()
        rns_queue = Queue()

        msg = _make_msg("meshtastic", "From MQTT uplink", origin=MessageOrigin.MQTT)
        destinations = router.get_destination_networks(msg)

        for dest in destinations:
            if dest == "meshcore":
                meshcore_queue.put(msg)
            elif dest == "rns":
                rns_queue.put(msg)

        assert meshcore_queue.qsize() == 0  # Filtered out
        assert rns_queue.qsize() == 1

    def test_round_trip_three_way(self, router):
        """Message hops: MeshCore -> Bridge -> Meshtastic & RNS queues."""
        # Stage 1: MeshCore originates
        mc_msg = _make_msg("meshcore", "Round trip test", source_id="mc:rnd001")
        dests = router.get_destination_networks(mc_msg)
        assert set(dests) == {"meshtastic", "rns"}

        # Stage 2: If Meshtastic relays back (different source)
        mesh_msg = _make_msg("meshtastic", "[MC:rnd001] Round trip test",
                             source_id="!relay456")
        dests2 = router.get_destination_networks(mesh_msg)
        assert "meshcore" in dests2
        assert "rns" in dests2

    def test_all_directions_covered(self, router):
        """Every source network reaches exactly the other two."""
        networks = ["meshtastic", "meshcore", "rns"]
        for src in networks:
            msg = _make_msg(src, f"From {src}")
            dests = router.get_destination_networks(msg)
            expected = [n for n in networks if n != src]
            assert set(dests) == set(expected), \
                f"{src} should route to {expected}, got {dests}"


# =============================================================================
# TEST: Router Statistics
# =============================================================================

class TestRouterStats:
    """Test routing statistics tracking."""

    def test_stats_initialized(self, router):
        """Router starts with clean stats."""
        stats = router.get_routing_stats()
        assert stats.get('bounced', 0) == 0

    def test_routing_decision_is_consistent(self, router):
        """Same message produces same routing decision."""
        msg = _make_msg("meshcore", "Consistency check")
        result1 = router.should_bridge(msg)
        result2 = router.should_bridge(msg)
        assert result1 == result2


# =============================================================================
# TEST: MeshCoreHandler in Simulation — Queue Integration
# =============================================================================



def _join_or_fail(thread, stop_event, timeout=10.0):
    """Join a handler thread and PROVE it died.

    A bare ``thread.join(timeout=...)`` returns None whether the thread
    stopped or not, so an expired join is indistinguishable from a clean
    one — the swallow-with-no-witness pattern, in a test
    (honest_failure_modes #9).

    The consequence is not hypothetical. These threads run the MeshCore
    handler's asyncio loop, which logs; a survivor keeps writing to the
    stdout file descriptor pytest captured for whatever test runs NEXT. On
    2026-08-03 CI job 91798063845 a "Captured stdout teardown" block for an
    unrelated test contained a 103-byte NUL hole followed by a MeshCore
    simulator log line stamped 2.4 minutes earlier — a writer at a stale
    offset in the capture file. That NUL turned the whole pytest log binary,
    which truncated the CI verdict gate's grep and classified a
    10117-passed suite as FAIL (fixed separately in pytest_verdict.sh).

    So: a leaked thread here is not tidy-up debt, it corrupts another test's
    captured output. Fail loudly instead.
    """
    stop_event.set()
    thread.join(timeout=timeout)
    assert not thread.is_alive(), (
        "MeshCore handler thread did not stop within %.0fs after stop_event "
        "was set. A survivor logs into the NEXT test's captured stdout." % timeout
    )


class TestMeshCoreHandlerQueueIntegration:
    """Test MeshCoreHandler message queue flow in simulation mode."""

    @pytest.fixture
    def handler_with_queues(self):
        """Create handler with observable queues."""
        meshcore_cfg = SimpleNamespace(
            enabled=True,
            device_path='/dev/ttyUSB1',
            baud_rate=115200,
            connection_type='serial',
            tcp_host='localhost',
            tcp_port=4000,
            auto_fetch_messages=True,
            bridge_channels=True,
            bridge_dms=True,
            simulation_mode=True,
            channel_poll_interval_sec=5,
        )
        config = SimpleNamespace(meshcore=meshcore_cfg)

        health = MagicMock()
        health.record_error.return_value = 'transient'
        tracker = MagicMock()

        stop_event = threading.Event()
        outbound_queue = Queue(maxsize=100)
        stats = {'errors': 0}
        stats_lock = threading.Lock()

        from gateway.meshcore_handler import MeshCoreHandler
        handler = MeshCoreHandler(
            config=config,
            node_tracker=tracker,
            health=health,
            stop_event=stop_event,
            stats=stats,
            stats_lock=stats_lock,
            message_queue=outbound_queue,
        )
        return handler, outbound_queue, stop_event

    def test_handler_starts_in_simulation(self, handler_with_queues):
        """Handler starts and connects in simulation mode."""
        handler, queue, stop = handler_with_queues

        # Start in background, stop quickly
        thread = threading.Thread(target=handler.run_loop, daemon=True)
        thread.start()

        # Wait for connection
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if handler.is_connected:
                break
            time.sleep(0.05)

        assert handler.is_connected
        _join_or_fail(thread, stop)

    def test_send_text_queues_message(self, handler_with_queues):
        """send_text queues a CanonicalMessage for async processing."""
        handler, queue, stop = handler_with_queues

        # Start handler
        thread = threading.Thread(target=handler.run_loop, daemon=True)
        thread.start()

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if handler.is_connected:
                break
            time.sleep(0.05)

        assert handler.is_connected

        # Queue a message
        result = handler.send_text("Test message to MeshCore")
        assert result is True

        _join_or_fail(thread, stop)

    def test_simulation_disconnect_clean(self, handler_with_queues):
        """Handler disconnects cleanly in simulation mode."""
        handler, queue, stop = handler_with_queues

        thread = threading.Thread(target=handler.run_loop, daemon=True)
        thread.start()

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if handler.is_connected:
                break
            time.sleep(0.05)

        # Disconnect
        handler.disconnect()
        assert handler.is_connected is False

        _join_or_fail(thread, stop)


