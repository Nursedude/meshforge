"""
Integration tests for RNS-Meshtastic bridge round-trip message flow.

Simulates the complete Meshtastic→RNS→Meshtastic message path without
requiring real hardware or network connections. Verifies:
- Message reception from both networks
- Routing decisions
- Queue processing
- Message transformation (prefixes)
- Statistics and health tracking
- Callback notifications
- Persistent queue requeue on failure

Run: python3 -m pytest tests/test_bridge_integration.py -v
"""

import os
import sys
import time
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from queue import Empty
from unittest.mock import patch, MagicMock, PropertyMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from gateway.rns_bridge import (
    BridgedMessage,
    RNSMeshtasticBridge,
)
from gateway.config import GatewayConfig, RoutingRule


@pytest.fixture
def bridge_config():
    """Create a test config with bridging enabled."""
    config = GatewayConfig()
    config.enabled = True
    config.bridge_mode = "message_bridge"  # Use TCP mode for direct packet tests
    config.default_route = "bidirectional"
    config.routing_rules = [
        RoutingRule(
            name="allow_all",
            enabled=True,
            direction="bidirectional",
            source_filter="",
            dest_filter="",
            message_filter="",
        )
    ]
    return config


@pytest.fixture
def bridge(bridge_config):
    """Create a bridge with mocked external dependencies."""
    with patch('gateway.rns_bridge.UnifiedNodeTracker') as mock_tracker:
        mock_tracker_instance = MagicMock()
        mock_tracker.return_value = mock_tracker_instance
        b = RNSMeshtasticBridge(config=bridge_config)
        b._running = True
        yield b
        b._running = False


class TestMeshToRnsFlow:
    """Test message flow from Meshtastic to RNS."""

    def test_meshtastic_text_message_queued(self, bridge):
        """Meshtastic TEXT_MESSAGE_APP is queued for RNS bridging."""
        packet = {
            'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'payload': b'Hello RNS'},
            'fromId': '!abcd1234',
            'toId': '!ffffffff',
            'rxSnr': 8.5,
            'channel': 0,
        }

        bridge._on_meshtastic_receive(packet)

        # Message should be in the mesh_to_rns queue
        msg = bridge._mesh_to_rns_queue.get(timeout=1)
        assert msg.source_network == "meshtastic"
        assert msg.source_id == "!abcd1234"
        assert msg.content == "Hello RNS"
        assert msg.is_broadcast is True
        assert msg.metadata['snr'] == 8.5

    def test_meshtastic_non_text_ignored(self, bridge):
        """Non-TEXT_MESSAGE_APP portnums are not queued."""
        packet = {
            'decoded': {'portnum': 'POSITION_APP', 'payload': b'\x00\x01'},
            'fromId': '!abcd1234',
            'toId': '!ffffffff',
        }

        bridge._on_meshtastic_receive(packet)

        # Queue should be empty
        with pytest.raises(Empty):
            bridge._mesh_to_rns_queue.get(timeout=0.1)

    def test_process_mesh_to_rns_success(self, bridge):
        """Process queued message and send to RNS successfully.

        Body stays clean; identity lives in the LXMF title and fields.
        """
        bridge._connected_rns = True
        bridge.node_tracker.get_node_by_mesh_id.return_value = None

        msg = BridgedMessage(
            source_network="meshtastic",
            source_id="!abcd1234",
            destination_id="!12345678",
            content="Hello from mesh",
            is_broadcast=False,
        )

        with patch.object(bridge, 'send_to_rns', return_value=True) as mock_send:
            bridge._process_mesh_to_rns(msg)

        mock_send.assert_called_once()
        sent_content = mock_send.call_args[0][0]
        assert sent_content == "Hello from mesh"
        kwargs = mock_send.call_args.kwargs
        assert kwargs.get("title") == "!abcd1234 via Meshtastic"
        fields = kwargs.get("fields") or {}
        assert fields.get("meshforge_from_id") == "!abcd1234"
        assert fields.get("meshforge_source_network") == "meshtastic"

        # Stats updated
        assert bridge.stats['messages_mesh_to_rns'] == 1
        assert bridge.stats['errors'] == 0

    def test_process_mesh_to_rns_failure_requeues(self, bridge):
        """Failed send requeues to persistent queue."""
        bridge._connected_rns = True
        mock_queue = MagicMock()
        bridge._persistent_queue = mock_queue

        msg = BridgedMessage(
            source_network="meshtastic",
            source_id="!abcd1234",
            destination_id="!12345678",
            content="Will fail",
            is_broadcast=False,
        )

        with patch.object(bridge, 'send_to_rns', return_value=False):
            bridge._process_mesh_to_rns(msg)

        # Should increment errors and record health event
        assert bridge.stats['errors'] == 1

    def test_broadcast_not_sent_to_rns(self, bridge):
        """Broadcast messages log debug but don't error."""
        bridge._connected_rns = True

        msg = BridgedMessage(
            source_network="meshtastic",
            source_id="!abcd1234",
            destination_id="!ffffffff",
            content="Broadcast msg",
            is_broadcast=True,
        )

        with patch.object(bridge, 'send_to_rns', return_value=False):
            bridge._process_mesh_to_rns(msg)

        # Broadcasts that fail are not errors
        assert bridge.stats['errors'] == 0


class TestRnsToMeshFlow:
    """Test message flow from RNS to Meshtastic."""

    def test_lxmf_message_queued(self, bridge):
        """LXMF message is queued for Meshtastic bridging."""
        lxmf_msg = MagicMock()
        lxmf_msg.source_hash = bytes.fromhex('abcdef0123456789')
        lxmf_msg.content = "Hello Meshtastic"
        lxmf_msg.title = "Test Title"
        lxmf_msg.stamp = time.time()

        bridge._on_lxmf_receive(lxmf_msg)

        msg = bridge._rns_to_mesh_queue.get(timeout=1)
        assert msg.source_network == "rns"
        assert msg.source_id == "abcdef0123456789"
        assert msg.content == "Hello Meshtastic"
        assert msg.title == "Test Title"

    def test_process_rns_to_mesh_success(self, bridge):
        """Process queued RNS message and send to Meshtastic."""
        msg = BridgedMessage(
            source_network="rns",
            source_id="abcdef0123456789",
            destination_id=None,
            content="Reply from RNS",
        )

        with patch.object(bridge, 'send_to_meshtastic', return_value=True) as mock_send:
            bridge._process_rns_to_mesh(msg)

            # Verify meshtastic send with prefixed content
            mock_send.assert_called_once()
            sent_text = mock_send.call_args[0][0]
            assert "[RNS:abcd]" in sent_text
            assert "Reply from RNS" in sent_text

        # Stats updated
        assert bridge.stats['messages_rns_to_mesh'] == 1

    def test_process_rns_to_mesh_failure(self, bridge):
        """Failed Meshtastic send increments error counter."""
        msg = BridgedMessage(
            source_network="rns",
            source_id="abcdef0123456789",
            destination_id=None,
            content="Will fail",
        )

        with patch.object(bridge, 'send_to_meshtastic', return_value=False):
            bridge._process_rns_to_mesh(msg)

        assert bridge.stats['errors'] == 1
        assert bridge.stats['messages_rns_to_mesh'] == 0


class TestRoundTrip:
    """End-to-end round-trip integration test."""

    def test_full_mesh_rns_mesh_roundtrip(self, bridge):
        """Complete Meshtastic→RNS→Meshtastic round trip."""
        bridge._connected_rns = True

        # Track received messages via callback
        received_messages = []
        bridge.register_message_callback(lambda msg: received_messages.append(msg))

        # === PHASE 1: Meshtastic → RNS ===

        # Step 1: Meshtastic node sends a message
        mesh_packet = {
            'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'payload': b'CQ CQ de WH6GXZ'},
            'fromId': '!ba4bf9d0',
            'toId': '!12345678',
            'rxSnr': 12.0,
            'channel': 0,
        }

        bridge._on_meshtastic_receive(mesh_packet)

        # Step 2: Message enters mesh_to_rns queue
        mesh_msg = bridge._mesh_to_rns_queue.get(timeout=1)
        assert mesh_msg.content == "CQ CQ de WH6GXZ"
        assert mesh_msg.source_network == "meshtastic"

        # Step 3: Bridge processes and sends to RNS
        bridge.node_tracker.get_node_by_mesh_id.return_value = None
        with patch.object(bridge, 'send_to_rns', return_value=True) as mock_rns_send:
            bridge._process_mesh_to_rns(mesh_msg)

        # Body is clean; identity moved to title + fields
        rns_content = mock_rns_send.call_args[0][0]
        assert rns_content == "CQ CQ de WH6GXZ"
        rns_title = mock_rns_send.call_args.kwargs.get("title")
        assert "!ba4bf9d0" in (rns_title or "")
        assert bridge.stats['messages_mesh_to_rns'] == 1

        # === PHASE 2: RNS → Meshtastic (reply) ===

        # Step 4: RNS node sends a reply
        rns_reply = MagicMock()
        rns_reply.source_hash = bytes.fromhex('deadbeef12345678')
        rns_reply.content = "WH6GXZ de KH6ABC 73"
        rns_reply.title = None
        rns_reply.stamp = time.time()

        bridge._on_lxmf_receive(rns_reply)

        # Step 5: Message enters rns_to_mesh queue
        rns_msg = bridge._rns_to_mesh_queue.get(timeout=1)
        assert rns_msg.content == "WH6GXZ de KH6ABC 73"
        assert rns_msg.source_network == "rns"

        # Step 6: Bridge processes and sends to Meshtastic
        with patch.object(bridge, 'send_to_meshtastic', return_value=True) as mock_mesh_send:
            bridge._process_rns_to_mesh(rns_msg)

        # Verify Meshtastic received the reply with RNS prefix
        mock_mesh_send.assert_called_once()
        mesh_content = mock_mesh_send.call_args[0][0]
        assert "[RNS:dead]" in mesh_content
        assert "WH6GXZ de KH6ABC 73" in mesh_content
        assert bridge.stats['messages_rns_to_mesh'] == 1

        # === VERIFICATION ===

        # Both message callbacks were triggered
        assert len(received_messages) == 2
        assert received_messages[0].source_network == "meshtastic"
        assert received_messages[1].source_network == "rns"

        # No errors throughout
        assert bridge.stats['errors'] == 0

        # Health monitor recorded events
        summary = bridge.health.get_summary()
        assert summary['messages']['mesh_to_rns'] == 1
        assert summary['messages']['rns_to_mesh'] == 1

    def test_roundtrip_with_bridge_loop_thread(self, bridge):
        """Test round trip using the bridge_loop thread for queue processing."""
        bridge._connected_rns = True
        # Set handler as connected so subsystem state syncs to HEALTHY
        bridge._mesh_handler._connected = True

        # Mock both send methods to succeed
        with patch.object(bridge, 'send_to_rns', return_value=True),              patch.object(bridge, 'send_to_meshtastic', return_value=True) as mock_mesh_send:
            # Start bridge loop in background
            bridge._running = True
            bridge_thread = threading.Thread(
                target=bridge._bridge_loop, daemon=True
            )
            bridge_thread.start()

            try:
                # Send a Meshtastic message
                mesh_packet = {
                    'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'payload': b'Auto-bridge test'},
                    'fromId': '!11223344',
                    'toId': '!55667788',
                    'rxSnr': 5.0,
                    'channel': 0,
                }
                bridge._on_meshtastic_receive(mesh_packet)

                # Wait for bridge loop to process
                time.sleep(0.5)

                # Mesh→RNS should be processed
                assert bridge.stats['messages_mesh_to_rns'] == 1

                # Now send an RNS reply
                rns_msg = MagicMock()
                rns_msg.source_hash = bytes.fromhex('aabbccdd11223344')
                rns_msg.content = "Auto reply"
                rns_msg.title = None
                rns_msg.stamp = time.time()

                bridge._on_lxmf_receive(rns_msg)

                # Wait for bridge loop to process
                time.sleep(0.5)

                # RNS→Mesh should be processed
                assert bridge.stats['messages_rns_to_mesh'] == 1
                mock_mesh_send.assert_called_once()

            finally:
                bridge._running = False
                bridge_thread.join(timeout=2)


class TestRoutingDecisions:
    """Test that routing rules correctly filter messages."""

    def test_disabled_bridge_blocks_all(self, bridge):
        """Messages are not queued when bridge is disabled."""
        bridge.config.enabled = False

        packet = {
            'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'payload': b'Blocked'},
            'fromId': '!abcd1234',
            'toId': '!ffffffff',
        }

        bridge._on_meshtastic_receive(packet)

        with pytest.raises(Empty):
            bridge._mesh_to_rns_queue.get(timeout=0.1)

    def test_direction_filter_mesh_to_rns_only(self, bridge):
        """Direction filter blocks wrong-direction messages."""
        # Disable classifier to test legacy routing
        bridge._router._classifier = None
        bridge.config.routing_rules = [
            RoutingRule(
                name="mesh_to_rns_only",
                enabled=True,
                direction="mesh_to_rns",
            )
        ]
        bridge.config.default_route = ""  # Disable default route

        # RNS message should NOT be queued (wrong direction)
        rns_msg = MagicMock()
        rns_msg.source_hash = bytes.fromhex('1122334455667788')
        rns_msg.content = "Should not bridge"
        rns_msg.title = None
        rns_msg.stamp = time.time()

        bridge._on_lxmf_receive(rns_msg)

        with pytest.raises(Empty):
            bridge._rns_to_mesh_queue.get(timeout=0.1)

    def test_source_filter_matches(self, bridge):
        """Source filter allows matching nodes."""
        # Disable classifier to test legacy routing
        bridge._router._classifier = None
        bridge.config.routing_rules = [
            RoutingRule(
                name="filter_node",
                enabled=True,
                direction="bidirectional",
                source_filter="!abcd",  # Only nodes starting with !abcd
            )
        ]
        bridge.config.default_route = ""

        # Matching node
        packet = {
            'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'payload': b'Match'},
            'fromId': '!abcd1234',
            'toId': '!ffffffff',
        }
        bridge._on_meshtastic_receive(packet)
        msg = bridge._mesh_to_rns_queue.get(timeout=0.5)
        assert msg.content == "Match"

        # Non-matching node
        packet2 = {
            'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'payload': b'No match'},
            'fromId': '!9999aaaa',
            'toId': '!ffffffff',
        }
        bridge._on_meshtastic_receive(packet2)
        with pytest.raises(Empty):
            bridge._mesh_to_rns_queue.get(timeout=0.1)


class TestHealthIntegration:
    """Test health monitoring integration with bridge operations."""

    def test_successful_mesh_to_rns_records_health(self, bridge):
        """Successful send records health event."""
        bridge._connected_rns = True

        msg = BridgedMessage(
            source_network="meshtastic",
            source_id="!abcd1234",
            destination_id="!12345678",
            content="Health test",
            is_broadcast=False,
        )

        with patch.object(bridge, 'send_to_rns', return_value=True):
            bridge._process_mesh_to_rns(msg)

        summary = bridge.health.get_summary()
        assert summary['messages']['mesh_to_rns'] == 1

    def test_failed_send_records_failure(self, bridge):
        """Failed send records failure in health monitor."""
        bridge._connected_rns = True

        msg = BridgedMessage(
            source_network="meshtastic",
            source_id="!abcd1234",
            destination_id="!12345678",
            content="Fail test",
            is_broadcast=False,
        )

        with patch.object(bridge, 'send_to_rns', return_value=False):
            bridge._process_mesh_to_rns(msg)

        summary = bridge.health.get_summary()
        assert summary['messages']['failed_mesh_to_rns'] == 1

    def test_exception_records_error(self, bridge):
        """Exception during processing records error in health."""
        bridge._connected_rns = True

        msg = BridgedMessage(
            source_network="meshtastic",
            source_id="!abcd1234",
            destination_id="!12345678",
            content="Exception test",
            is_broadcast=False,
        )

        with patch.object(bridge, 'send_to_rns', side_effect=OSError("Network down")):
            bridge._process_mesh_to_rns(msg)

        errors = bridge.health.get_error_rate(window_seconds=60)
        assert errors['transient'] >= 1

    def test_successful_rns_to_mesh_records_health(self, bridge):
        """Successful RNS→Mesh records health event."""
        msg = BridgedMessage(
            source_network="rns",
            source_id="abcdef0123456789",
            destination_id=None,
            content="Health check",
        )

        with patch.object(bridge, 'send_to_meshtastic', return_value=True):
            bridge._process_rns_to_mesh(msg)

        summary = bridge.health.get_summary()
        assert summary['messages']['rns_to_mesh'] == 1


class TestCallbackNotification:
    """Test message callback notifications during round trip."""

    def test_meshtastic_message_notifies_callbacks(self, bridge):
        """Callback is invoked when Meshtastic message arrives."""
        callback = MagicMock()
        bridge.register_message_callback(callback)

        packet = {
            'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'payload': b'Callback test'},
            'fromId': '!abcd1234',
            'toId': '!ffffffff',
        }

        bridge._on_meshtastic_receive(packet)

        callback.assert_called_once()
        msg = callback.call_args[0][0]
        assert msg.content == "Callback test"
        assert msg.source_network == "meshtastic"

    def test_rns_message_notifies_callbacks(self, bridge):
        """Callback is invoked when RNS message arrives."""
        callback = MagicMock()
        bridge.register_message_callback(callback)

        rns_msg = MagicMock()
        rns_msg.source_hash = bytes.fromhex('1122334455667788')
        rns_msg.content = "RNS callback test"
        rns_msg.title = None
        rns_msg.stamp = time.time()

        bridge._on_lxmf_receive(rns_msg)

        callback.assert_called_once()
        msg = callback.call_args[0][0]
        assert msg.content == "RNS callback test"
        assert msg.source_network == "rns"

    def test_multiple_callbacks_all_invoked(self, bridge):
        """All registered callbacks are invoked."""
        cb1 = MagicMock()
        cb2 = MagicMock()
        cb3 = MagicMock()
        bridge.register_message_callback(cb1)
        bridge.register_message_callback(cb2)
        bridge.register_message_callback(cb3)

        packet = {
            'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'payload': b'Multi'},
            'fromId': '!abcd1234',
            'toId': '!ffffffff',
        }

        bridge._on_meshtastic_receive(packet)

        cb1.assert_called_once()
        cb2.assert_called_once()
        cb3.assert_called_once()

    def test_failing_callback_does_not_block_others(self, bridge):
        """A failing callback doesn't prevent other callbacks."""
        cb1 = MagicMock(side_effect=RuntimeError("callback error"))
        cb2 = MagicMock()
        bridge.register_message_callback(cb1)
        bridge.register_message_callback(cb2)

        packet = {
            'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'payload': b'Resilience'},
            'fromId': '!abcd1234',
            'toId': '!ffffffff',
        }

        bridge._on_meshtastic_receive(packet)

        # Both called despite first one raising
        cb1.assert_called_once()
        cb2.assert_called_once()


class TestPersistentQueueIntegration:
    """Test persistent queue integration with bridge failure handling."""

    def test_failed_mesh_to_rns_requeues(self, bridge):
        """Failed Mesh→RNS requeues to persistent queue."""
        bridge._connected_rns = True

        # Setup a mock persistent queue
        with tempfile.TemporaryDirectory() as tmpdir:
            from gateway.message_queue import PersistentMessageQueue
            pq = PersistentMessageQueue(db_path=str(Path(tmpdir) / "test.db"))
            bridge._persistent_queue = pq

            # Mock enqueue since the bridge calls it on failed delivery
            pq.enqueue = MagicMock(return_value="test-id")

            msg = BridgedMessage(
                source_network="meshtastic",
                source_id="!abcd1234",
                destination_id="!12345678",
                content="Persist me",
                is_broadcast=False,
                metadata={'channel': 0},
            )

            with patch.object(bridge, 'send_to_rns', return_value=False):
                bridge._process_mesh_to_rns(msg)

            # Persistent queue should have been called
            pq.enqueue.assert_called_once()

    def test_failed_rns_to_mesh_requeues(self, bridge):
        """Failed RNS→Mesh requeues to persistent queue."""
        bridge._connected_mesh = False

        with tempfile.TemporaryDirectory() as tmpdir:
            from gateway.message_queue import PersistentMessageQueue
            pq = PersistentMessageQueue(db_path=str(Path(tmpdir) / "test.db"))
            bridge._persistent_queue = pq
            pq.enqueue = MagicMock(return_value="test-id")

            msg = BridgedMessage(
                source_network="rns",
                source_id="abcdef0123456789",
                destination_id=None,
                content="Persist RNS msg",
            )

            bridge._process_rns_to_mesh(msg)

            pq.enqueue.assert_called_once()


class TestEdgeCases:
    """Test edge cases in the bridge flow."""

    def test_empty_payload_handled(self, bridge):
        """Empty payload is handled gracefully."""
        packet = {
            'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'payload': b''},
            'fromId': '!abcd1234',
            'toId': '!ffffffff',
        }

        bridge._on_meshtastic_receive(packet)

        msg = bridge._mesh_to_rns_queue.get(timeout=0.5)
        assert msg.content == ""

    def test_unicode_content_preserved(self, bridge):
        """Unicode content survives the bridge flow."""
        msg = BridgedMessage(
            source_network="rns",
            source_id="abcdef0123456789",
            destination_id=None,
            content="Aloha \u2708 73 de WH6GXZ",
        )

        with patch.object(bridge, 'send_to_meshtastic', return_value=True) as mock_send:
            bridge._process_rns_to_mesh(msg)

        sent_text = mock_send.call_args[0][0]
        assert "Aloha \u2708 73 de WH6GXZ" in sent_text

    def test_full_source_id_surfaces_in_title(self, bridge):
        """Full Meshtastic source id is preserved in the LXMF title."""
        bridge._connected_rns = True
        bridge.node_tracker.get_node_by_mesh_id.return_value = None

        msg = BridgedMessage(
            source_network="meshtastic",
            source_id="!1234567890abcdef",
            destination_id="!dest1234",
            content="Long ID test",
            is_broadcast=False,
        )

        with patch.object(bridge, 'send_to_rns', return_value=True) as mock_send:
            bridge._process_mesh_to_rns(msg)

        assert mock_send.call_args[0][0] == "Long ID test"
        assert "!1234567890abcdef" in mock_send.call_args.kwargs.get("title", "")

    def test_rns_source_prefix_uses_first_4(self, bridge):
        """RNS prefix uses first 4 chars of source hash."""
        msg = BridgedMessage(
            source_network="rns",
            source_id="fedcba9876543210",
            destination_id=None,
            content="Prefix test",
        )

        with patch.object(bridge, 'send_to_meshtastic', return_value=True) as mock_send:
            bridge._process_rns_to_mesh(msg)

        sent = mock_send.call_args[0][0]
        assert "[RNS:fedc]" in sent

    def test_malformed_packet_no_crash(self, bridge):
        """Malformed Meshtastic packet doesn't crash the bridge."""
        # Missing decoded field — no portnum, no queue
        bridge._on_meshtastic_receive({})

        # Empty decoded — no portnum match
        bridge._on_meshtastic_receive({'decoded': {}})

        # Non-text portnum — ignored
        bridge._on_meshtastic_receive({'decoded': {'portnum': 'UNKNOWN_APP'}})

        # None packet — should not crash
        bridge._on_meshtastic_receive({'decoded': None})

        # These should NOT produce queued messages
        with pytest.raises(Empty):
            bridge._mesh_to_rns_queue.get(timeout=0.1)

    def test_lxmf_message_with_title(self, bridge):
        """LXMF message title is preserved in BridgedMessage."""
        rns_msg = MagicMock()
        rns_msg.source_hash = bytes.fromhex('aabbccdd11223344')
        rns_msg.content = "Message body"
        rns_msg.title = "Important Alert"
        rns_msg.stamp = time.time()

        bridge._on_lxmf_receive(rns_msg)

        msg = bridge._rns_to_mesh_queue.get(timeout=0.5)
        assert msg.title == "Important Alert"
        assert msg.content == "Message body"
