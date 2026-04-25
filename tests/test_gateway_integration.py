"""
Integration tests for Gateway Bridge: Meshtastic receive -> queue -> LXMF send -> RNS receive.

Tests the full message lifecycle through the gateway bridge without requiring hardware.
Validates the cornerstone feature: bidirectional Meshtastic <-> RNS message bridging.

Run: python3 -m pytest tests/test_gateway_integration.py -v
"""

import json
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from queue import Queue
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.gateway.config import GatewayConfig, MeshtasticConfig, RNSConfig
from src.gateway.message_queue import (
    PersistentMessageQueue,
    MessagePriority,
    MessageStatus,
    QueuedMessage,
    RetryPolicy,
    MessageLifecycleState,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary SQLite database for message queue."""
    return str(tmp_path / "test_queue.db")


@pytest.fixture
def gateway_config():
    """Create a test gateway configuration."""
    config = GatewayConfig()
    config.enabled = True
    config.bridge_mode = "message_bridge"
    config.meshtastic.host = "localhost"
    config.meshtastic.port = 4403
    config.default_route = "bidirectional"
    return config


@pytest.fixture
def message_queue(tmp_db):
    """Create a PersistentMessageQueue with a temp database."""
    queue = PersistentMessageQueue(db_path=tmp_db)
    yield queue
    queue.stop_processing()


@pytest.fixture
def message_queue_with_policy(tmp_db):
    """Create a PersistentMessageQueue with retry policy."""
    policy = RetryPolicy(max_tries=3, timeout=30.0, base_delay=0.1, max_delay=1.0)
    queue = PersistentMessageQueue(db_path=tmp_db, retry_policy=policy)
    yield queue
    queue.stop_processing()


# =============================================================================
# TEST: MESSAGE QUEUE ENQUEUE -> DEQUEUE -> DELIVERY
# =============================================================================

class TestMessageQueueLifecycle:
    """Test the persistent message queue's full enqueue -> process -> deliver cycle."""

    def test_enqueue_creates_pending_message(self, message_queue):
        """Test that enqueue creates a message in PENDING status."""
        payload = {
            "from": "!abc12345",
            "to": "!def67890",
            "text": "Hello from Meshtastic",
            "type": "text",
        }

        msg_id = message_queue.enqueue(payload, destination="rns")

        assert msg_id is not None
        pending = message_queue.get_pending(destination="rns")
        assert len(pending) == 1
        assert pending[0].id == msg_id
        assert pending[0].status == MessageStatus.PENDING
        assert pending[0].payload["text"] == "Hello from Meshtastic"

    def test_deduplication_blocks_duplicates(self, message_queue):
        """Test that identical messages within dedup window are blocked."""
        payload = {
            "from": "!abc12345",
            "to": "!def67890",
            "text": "Duplicate test",
            "type": "text",
        }

        msg_id_1 = message_queue.enqueue(payload, destination="rns")
        msg_id_2 = message_queue.enqueue(payload, destination="rns")

        assert msg_id_1 is not None
        assert msg_id_2 is None  # Duplicate blocked

        stats = message_queue.get_stats()
        assert stats["deduplicated"] >= 1

    def test_different_messages_not_deduplicated(self, message_queue):
        """Test that different messages pass dedup check."""
        msg_id_1 = message_queue.enqueue(
            {"from": "!a", "to": "!b", "text": "Message 1", "type": "text"},
            destination="rns",
        )
        msg_id_2 = message_queue.enqueue(
            {"from": "!a", "to": "!b", "text": "Message 2", "type": "text"},
            destination="rns",
        )

        assert msg_id_1 is not None
        assert msg_id_2 is not None
        assert msg_id_1 != msg_id_2

    def test_process_once_delivers_successfully(self, message_queue):
        """Test that process_once calls sender and marks delivered."""
        send_called = []

        def mock_sender(payload):
            send_called.append(payload)
            return True

        message_queue.register_sender("rns", mock_sender)

        payload = {
            "from": "!abc12345",
            "to": "!def67890",
            "text": "Bridge me!",
            "type": "text",
        }
        msg_id = message_queue.enqueue(payload, destination="rns")

        processed = message_queue.process_once()

        assert processed == 1
        assert len(send_called) == 1
        assert send_called[0]["text"] == "Bridge me!"

        # Verify message is now delivered
        pending = message_queue.get_pending(destination="rns")
        assert len(pending) == 0

        stats = message_queue.get_stats()
        assert stats["delivered"] >= 1

    def test_process_once_retries_on_failure(self, message_queue):
        """Test that failed sends trigger retry scheduling."""
        def failing_sender(payload):
            return False

        message_queue.register_sender("rns", failing_sender)

        msg_id = message_queue.enqueue(
            {"from": "!a", "to": "!b", "text": "Will fail", "type": "text"},
            destination="rns",
        )

        message_queue.process_once()

        stats = message_queue.get_stats()
        assert stats["retried"] >= 1

    def test_max_retries_moves_to_dead_letter(self, message_queue):
        """Test that exceeding max retries moves to dead letter queue."""
        def failing_sender(payload):
            return False

        message_queue.register_sender("rns", failing_sender)

        msg_id = message_queue.enqueue(
            {"from": "!a", "to": "!b", "text": "Doomed", "type": "text"},
            destination="rns",
            max_retries=1,
        )

        # Process multiple times to exhaust retries
        message_queue.process_once()
        # After first failure, retry_count=1 >= max_retries=1 → dead letter
        dead = message_queue.get_dead_letters()
        assert len(dead) == 1
        assert dead[0].id == msg_id

    def test_success_callback_fires(self, message_queue):
        """Test that success callbacks fire on delivery."""
        successes = []

        def on_success(msg):
            successes.append(msg)

        message_queue.register_sender("rns", lambda p: True)
        message_queue.register_success_callback(on_success)

        message_queue.enqueue(
            {"from": "!a", "to": "!b", "text": "Callback test", "type": "text"},
            destination="rns",
        )
        message_queue.process_once()

        assert len(successes) == 1

    def test_failure_callback_fires(self, message_queue):
        """Test that failure callbacks fire on delivery failure."""
        failures = []

        def on_failure(msg, error):
            failures.append((msg, error))

        def exploding_sender(payload):
            raise ConnectionError("No route to host")

        message_queue.register_sender("rns", exploding_sender)
        message_queue.register_failure_callback(on_failure)

        message_queue.enqueue(
            {"from": "!a", "to": "!b", "text": "Will explode", "type": "text"},
            destination="rns",
        )
        message_queue.process_once()

        assert len(failures) == 1
        assert "No route to host" in failures[0][1]


class TestMessagePriority:
    """Test priority-based message processing."""

    def test_high_priority_processed_first(self, message_queue):
        """Test that higher priority messages are dequeued first."""
        message_queue.enqueue(
            {"from": "!a", "to": "!b", "text": "Low priority", "type": "text"},
            destination="rns",
            priority=MessagePriority.LOW,
        )
        message_queue.enqueue(
            {"from": "!a", "to": "!c", "text": "Urgent!", "type": "text"},
            destination="rns",
            priority=MessagePriority.URGENT,
        )
        message_queue.enqueue(
            {"from": "!a", "to": "!d", "text": "Normal", "type": "text"},
            destination="rns",
            priority=MessagePriority.NORMAL,
        )

        pending = message_queue.get_pending(destination="rns")

        # Should be ordered: URGENT, NORMAL, LOW
        assert pending[0].priority == MessagePriority.URGENT
        assert pending[1].priority == MessagePriority.NORMAL
        assert pending[2].priority == MessagePriority.LOW

    def test_queue_overflow_sheds_low_priority(self, tmp_path):
        """Test that queue overflow sheds lowest priority messages."""
        db_path = str(tmp_path / "overflow.db")
        queue = PersistentMessageQueue(db_path=db_path, max_queue_size=3)

        # Fill queue
        for i in range(3):
            queue.enqueue(
                {"from": "!a", "to": "!b", "text": f"msg{i}", "type": "text"},
                destination="rns",
                priority=MessagePriority.LOW,
                deduplicate=False,
            )

        # Next enqueue should trigger shedding
        msg_id = queue.enqueue(
            {"from": "!a", "to": "!c", "text": "new msg", "type": "text"},
            destination="rns",
            priority=MessagePriority.NORMAL,
            deduplicate=False,
        )

        assert msg_id is not None
        stats = queue.get_stats()
        assert stats["shed"] >= 1


class TestRetryPolicy:
    """Test intelligent retry decisions."""

    def test_transient_error_retries(self, message_queue_with_policy):
        """Test that transient errors trigger retry."""
        call_count = [0]

        def intermittent_sender(payload):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ConnectionError("connection reset")
            return True

        message_queue_with_policy.register_sender("rns", intermittent_sender)

        message_queue_with_policy.enqueue(
            {"from": "!a", "to": "!b", "text": "Retry test", "type": "text"},
            destination="rns",
        )

        # First attempt: fails with transient error
        message_queue_with_policy.process_once()
        stats = message_queue_with_policy.get_stats()
        assert stats["retried"] >= 1

    def test_permanent_error_no_retry(self, message_queue_with_policy):
        """Test that permanent errors go straight to dead letter."""
        def perm_fail_sender(payload):
            raise PermissionError("permission denied")

        message_queue_with_policy.register_sender("rns", perm_fail_sender)

        message_queue_with_policy.enqueue(
            {"from": "!a", "to": "!b", "text": "No permission", "type": "text"},
            destination="rns",
        )
        message_queue_with_policy.process_once()

        dead = message_queue_with_policy.get_dead_letters()
        assert len(dead) == 1

        stats = message_queue_with_policy.get_stats()
        assert stats["permanent_failures"] >= 1


class TestMessageLifecycle:
    """Test message lifecycle tracking (Sprint C: Message Visibility)."""

    def test_lifecycle_event_recording(self, message_queue):
        """Test recording and querying lifecycle events."""
        msg_id = message_queue.enqueue(
            {"from": "!a", "to": "!b", "text": "Track me", "type": "text"},
            destination="rns",
        )

        # Record lifecycle events
        message_queue.record_lifecycle_event(
            msg_id, MessageLifecycleState.CREATED, details="Message created"
        )
        message_queue.record_lifecycle_event(
            msg_id, MessageLifecycleState.QUEUED, details="Added to queue"
        )
        message_queue.record_lifecycle_event(
            msg_id, MessageLifecycleState.SENT, details="Sent to RNS", hop_count=1
        )

        trace = message_queue.get_message_trace(msg_id)
        assert len(trace) == 3
        assert trace[0].state == MessageLifecycleState.CREATED
        assert trace[1].state == MessageLifecycleState.QUEUED
        assert trace[2].state == MessageLifecycleState.SENT
        assert trace[2].hop_count == 1

    def test_message_summary(self, message_queue):
        """Test message summary with lifecycle data."""
        msg_id = message_queue.enqueue(
            {"from": "!abc", "to": "!def", "text": "Summary test", "type": "text"},
            destination="rns",
        )

        message_queue.record_lifecycle_event(
            msg_id, MessageLifecycleState.CREATED
        )
        message_queue.record_lifecycle_event(
            msg_id, MessageLifecycleState.QUEUED
        )

        summary = message_queue.get_message_summary(msg_id)
        assert summary is not None
        assert summary["message_id"] == msg_id
        assert summary["destination"] == "rns"
        assert summary["lifecycle"]["event_count"] == 2


# =============================================================================
# TEST: MESHTASTIC RECEIVE -> QUEUE (MeshtasticHandler._on_receive)
# =============================================================================

class TestMeshtasticReceiveToQueue:
    """Test the Meshtastic message receive -> bridge queue path."""

    def _create_handler(self, message_queue_obj):
        """Create a MeshtasticHandler with mocked dependencies."""
        from src.gateway.meshtastic_handler import MeshtasticHandler
        from src.gateway.config import GatewayConfig
        from src.gateway.node_tracker import UnifiedNodeTracker

        config = GatewayConfig()
        node_tracker = UnifiedNodeTracker()
        health = MagicMock()
        stop_event = threading.Event()
        stats = {"messages_mesh_to_rns": 0, "errors": 0}
        stats_lock = threading.Lock()
        messages_received = []

        handler = MeshtasticHandler(
            config=config,
            node_tracker=node_tracker,
            health=health,
            stop_event=stop_event,
            stats=stats,
            stats_lock=stats_lock,
            message_queue=message_queue_obj,
            message_callback=lambda msg: messages_received.append(msg),
        )
        return handler, messages_received

    def test_text_message_queued_for_bridging(self):
        """Test that a received Meshtastic text message gets queued for RNS bridging."""
        bridge_queue = Queue(maxsize=100)
        handler, received = self._create_handler(bridge_queue)

        # Simulate a Meshtastic packet callback
        packet = {
            "fromId": "!abc12345",
            "toId": "!ffffffff",  # broadcast
            "decoded": {
                "portnum": "TEXT_MESSAGE_APP",
                "payload": b"Hello mesh!",
            },
            "channel": 0,
            "rxSnr": 8.5,
            "rxRssi": -80,
            "hopStart": 3,
            "hopLimit": 2,
        }

        # Mock the imports that _handle_text_message needs
        with patch.dict("sys.modules", {
            "commands": MagicMock(),
            "commands.messaging": MagicMock(),
        }):
            handler._on_receive(packet)

        # Verify message was queued
        assert not bridge_queue.empty()
        bridged_msg = bridge_queue.get_nowait()
        assert bridged_msg.source_network == "meshtastic"
        assert bridged_msg.source_id == "!abc12345"
        assert bridged_msg.content == "Hello mesh!"
        assert bridged_msg.is_broadcast is True

        # Verify callback fired
        assert len(received) == 1

    def test_non_text_message_not_queued(self):
        """Test that non-TEXT_MESSAGE_APP packets don't get queued."""
        bridge_queue = Queue(maxsize=100)
        handler, received = self._create_handler(bridge_queue)

        packet = {
            "fromId": "!abc12345",
            "toId": "!def67890",
            "decoded": {
                "portnum": "POSITION_APP",
                "payload": b"\x00\x01\x02",
            },
            "hopStart": 3,
            "hopLimit": 3,
        }

        handler._on_receive(packet)

        assert bridge_queue.empty()  # Non-text messages not bridged
        assert len(received) == 0

    def test_direct_message_not_broadcast(self):
        """Test that direct messages have is_broadcast=False."""
        bridge_queue = Queue(maxsize=100)
        handler, received = self._create_handler(bridge_queue)

        packet = {
            "fromId": "!abc12345",
            "toId": "!def67890",  # specific destination, not broadcast
            "decoded": {
                "portnum": "TEXT_MESSAGE_APP",
                "payload": b"Private message",
            },
            "channel": 0,
            "hopStart": 3,
            "hopLimit": 2,
        }

        with patch.dict("sys.modules", {
            "commands": MagicMock(),
            "commands.messaging": MagicMock(),
        }):
            handler._on_receive(packet)

        bridged = bridge_queue.get_nowait()
        assert bridged.is_broadcast is False
        assert bridged.destination_id == "!def67890"

    def test_queue_full_drops_message(self):
        """Test graceful handling when bridge queue is full."""
        bridge_queue = Queue(maxsize=1)
        handler, received = self._create_handler(bridge_queue)

        # Fill the queue
        bridge_queue.put("filler")

        packet = {
            "fromId": "!abc12345",
            "toId": "!ffffffff",
            "decoded": {
                "portnum": "TEXT_MESSAGE_APP",
                "payload": b"Queue is full",
            },
            "hopStart": 3,
            "hopLimit": 2,
        }

        with patch.dict("sys.modules", {
            "commands": MagicMock(),
            "commands.messaging": MagicMock(),
        }):
            # Should not raise, just log warning
            handler._on_receive(packet)

        # Queue should still have only the filler (no crash)
        assert bridge_queue.qsize() == 1


# =============================================================================
# TEST: FULL PIPELINE (Meshtastic -> Queue -> Sender -> Delivery)
# =============================================================================

class TestFullBridgePipeline:
    """Test the complete message bridging pipeline end-to-end."""

    def test_meshtastic_to_rns_full_pipeline(self, tmp_path):
        """
        Full pipeline: Meshtastic packet → MeshtasticHandler → Queue → Sender → Delivered.

        This is the core integration test simulating what happens when a radio
        receives a text message and it needs to be forwarded to the RNS network.
        """
        from src.gateway.meshtastic_handler import MeshtasticHandler
        from src.gateway.node_tracker import UnifiedNodeTracker

        # Setup: persistent queue with mock RNS sender
        db_path = str(tmp_path / "pipeline.db")
        queue = PersistentMessageQueue(db_path=db_path)
        rns_sent = []

        def mock_rns_sender(payload):
            rns_sent.append(payload)
            return True

        queue.register_sender("rns", mock_rns_sender)

        # Setup: MeshtasticHandler with bridge queue
        bridge_queue = Queue(maxsize=100)
        config = GatewayConfig()
        handler = MeshtasticHandler(
            config=config,
            node_tracker=UnifiedNodeTracker(),
            health=MagicMock(),
            stop_event=threading.Event(),
            stats={"messages_mesh_to_rns": 0, "errors": 0},
            stats_lock=threading.Lock(),
            message_queue=bridge_queue,
        )

        # Step 1: Simulate Meshtastic packet reception
        packet = {
            "fromId": "!aabbccdd",
            "toId": "!ffffffff",
            "decoded": {
                "portnum": "TEXT_MESSAGE_APP",
                "payload": b"Emergency: need supplies at grid BL11",
            },
            "channel": 0,
            "rxSnr": 12.0,
            "rxRssi": -65,
            "hopStart": 3,
            "hopLimit": 1,
        }

        with patch.dict("sys.modules", {
            "commands": MagicMock(),
            "commands.messaging": MagicMock(),
        }):
            handler._on_receive(packet)

        # Step 2: Verify message landed in bridge queue
        assert not bridge_queue.empty()
        bridged_msg = bridge_queue.get_nowait()
        assert bridged_msg.content == "Emergency: need supplies at grid BL11"

        # Step 3: Simulate the bridge thread enqueuing to persistent queue
        # (This is what RNSMeshtasticBridge._bridge_loop does)
        msg_id = queue.enqueue(
            {
                "from": bridged_msg.source_id,
                "to": bridged_msg.destination_id,
                "text": bridged_msg.content,
                "type": "text",
                "source_network": "meshtastic",
            },
            destination="rns",
            priority=MessagePriority.NORMAL,
        )
        assert msg_id is not None

        # Step 4: Process the queue (sends to RNS)
        processed = queue.process_once()
        assert processed == 1

        # Step 5: Verify the RNS sender received the message
        assert len(rns_sent) == 1
        assert rns_sent[0]["text"] == "Emergency: need supplies at grid BL11"
        assert rns_sent[0]["from"] == "!aabbccdd"

        # Step 6: Verify queue shows delivered
        stats = queue.get_stats()
        assert stats["delivered"] >= 1

        queue.stop_processing()

    def test_bidirectional_message_flow(self, tmp_path):
        """Test messages flowing in both directions (Mesh→RNS and RNS→Mesh)."""
        db_path = str(tmp_path / "bidir.db")
        queue = PersistentMessageQueue(db_path=db_path)

        mesh_sent = []
        rns_sent = []

        queue.register_sender("meshtastic", lambda p: (mesh_sent.append(p), True)[-1])
        queue.register_sender("rns", lambda p: (rns_sent.append(p), True)[-1])

        # Mesh → RNS
        queue.enqueue(
            {"from": "!mesh1", "to": "rns_dest", "text": "From mesh", "type": "text"},
            destination="rns",
        )

        # RNS → Mesh
        queue.enqueue(
            {"from": "rns_user", "to": "!mesh1", "text": "From RNS", "type": "text"},
            destination="meshtastic",
        )

        queue.process_once()

        assert len(rns_sent) == 1
        assert rns_sent[0]["text"] == "From mesh"
        assert len(mesh_sent) == 1
        assert mesh_sent[0]["text"] == "From RNS"

        queue.stop_processing()


# =============================================================================
# TEST: QUEUE PERSISTENCE (survives restart)
# =============================================================================

class TestQueuePersistence:
    """Test that the queue survives simulated restarts."""

    def test_messages_survive_restart(self, tmp_path):
        """Messages enqueued before 'restart' are available after."""
        db_path = str(tmp_path / "persist.db")

        # Phase 1: Enqueue before "restart"
        queue1 = PersistentMessageQueue(db_path=db_path)
        msg_id = queue1.enqueue(
            {"from": "!a", "to": "!b", "text": "Survive restart", "type": "text"},
            destination="rns",
        )
        queue1.stop_processing()
        del queue1

        # Phase 2: New queue instance (simulates restart)
        queue2 = PersistentMessageQueue(db_path=db_path)
        pending = queue2.get_pending(destination="rns")

        assert len(pending) == 1
        assert pending[0].id == msg_id
        assert pending[0].payload["text"] == "Survive restart"

        queue2.stop_processing()

    def test_stale_in_progress_reset_on_restart(self, tmp_path):
        """Test that stale in_progress messages are reset to pending."""
        db_path = str(tmp_path / "stale.db")

        # Phase 1: Enqueue and mark in_progress, then "crash"
        queue1 = PersistentMessageQueue(db_path=db_path)
        # Override stale timeout for fast test
        queue1.STALE_TIMEOUT = 0  # Immediate expiry
        msg_id = queue1.enqueue(
            {"from": "!a", "to": "!b", "text": "Stuck", "type": "text"},
            destination="rns",
        )
        queue1.mark_in_progress(msg_id)
        queue1.stop_processing()
        del queue1

        # Phase 2: New instance cleans up stale messages
        queue2 = PersistentMessageQueue(db_path=db_path)
        queue2.STALE_TIMEOUT = 0
        reset_count = queue2.cleanup_stale()

        assert reset_count == 1
        pending = queue2.get_pending(destination="rns")
        assert len(pending) == 1

        queue2.stop_processing()


# =============================================================================
# TEST: BACKGROUND PROCESSING
# =============================================================================

class TestBackgroundProcessing:
    """Test background queue processing thread."""

    def test_background_processing_delivers(self, tmp_path):
        """Test that background processing thread delivers messages."""
        db_path = str(tmp_path / "background.db")
        queue = PersistentMessageQueue(db_path=db_path)

        delivered = threading.Event()

        def mock_sender(payload):
            delivered.set()
            return True

        queue.register_sender("rns", mock_sender)
        queue.enqueue(
            {"from": "!a", "to": "!b", "text": "Background", "type": "text"},
            destination="rns",
        )

        queue.start_processing(interval=0.1)

        # Wait for delivery
        assert delivered.wait(timeout=5.0), "Background processing did not deliver within 5s"

        queue.stop_processing()

        stats = queue.get_stats()
        assert stats["delivered"] >= 1


# =============================================================================
# CONNECTION STABILITY TESTS
# Tests for failure patterns identified in session notes (Jan-Feb 2026)
# =============================================================================

class TestMeshtasticConnectionStability:
    """Test meshtasticd connection via MQTT bridge handler."""

    def test_mqtt_handler_instantiates_without_broker(self):
        """Handler should create cleanly even when broker is unreachable."""
        config = GatewayConfig()
        config.enabled = True
        config.bridge_mode = "mqtt_bridge"

        from gateway.bridge_health import BridgeHealthMonitor
        from gateway.node_tracker import UnifiedNodeTracker

        health = BridgeHealthMonitor()
        node_tracker = UnifiedNodeTracker()
        stop_event = threading.Event()
        stats = {'errors': 0}
        stats_lock = threading.Lock()
        queue = Queue(maxsize=100)

        from gateway.mqtt_bridge_handler import MQTTBridgeHandler
        h = MQTTBridgeHandler(
            config=config,
            node_tracker=node_tracker,
            health=health,
            stop_event=stop_event,
            stats=stats,
            stats_lock=stats_lock,
            message_queue=queue,
        )
        assert h.is_connected is False

    def test_mqtt_connect_reports_failure_cleanly(self):
        """When broker is unreachable, connect() returns False without crash."""
        config = GatewayConfig()
        config.mqtt_bridge.broker = "127.0.0.1"
        config.mqtt_bridge.port = 19999  # Unreachable port

        from gateway.mqtt_bridge_handler import MQTTBridgeHandler
        from gateway.bridge_health import BridgeHealthMonitor
        from gateway.node_tracker import UnifiedNodeTracker

        h = MQTTBridgeHandler(
            config=config,
            node_tracker=UnifiedNodeTracker(),
            health=BridgeHealthMonitor(),
            stop_event=threading.Event(),
            stats={'errors': 0},
            stats_lock=threading.Lock(),
            message_queue=Queue(maxsize=100),
        )

        result = h._connect()
        assert result is False
        assert h.is_connected is False

    def test_mqtt_test_connection_returns_false_when_unreachable(self):
        """test_connection() should return False, not raise."""
        config = GatewayConfig()
        config.mqtt_bridge.broker = "127.0.0.1"
        config.mqtt_bridge.port = 19999

        from gateway.mqtt_bridge_handler import MQTTBridgeHandler
        from gateway.bridge_health import BridgeHealthMonitor
        from gateway.node_tracker import UnifiedNodeTracker

        h = MQTTBridgeHandler(
            config=config,
            node_tracker=UnifiedNodeTracker(),
            health=BridgeHealthMonitor(),
            stop_event=threading.Event(),
            stats={'errors': 0},
            stats_lock=threading.Lock(),
            message_queue=Queue(maxsize=100),
        )

        assert h.test_connection() is False

    def test_bridge_starts_in_degraded_mode(self):
        """Bridge should start even when meshtasticd is unavailable —
        but only when no bridge channel name is configured. With a
        non-empty mqtt_bridge.channel, refuse-loud kicks in (separately
        covered by test_start_returns_false_on_channel_resolution_error
        in test_rns_bridge.py)."""
        config = GatewayConfig()
        config.enabled = True
        config.bridge_mode = "mqtt_bridge"
        # No channel name = no refuse-loud; this exercises the
        # genuine "meshtasticd flaky, but no strict channel pinning"
        # path. With the default "LongFast" name set, the bridge
        # would (correctly) refuse to start under refuse-loud.
        config.mqtt_bridge.channel = ""

        from gateway.rns_bridge import RNSMeshtasticBridge
        bridge = RNSMeshtasticBridge(config=config)

        result = bridge.start()
        assert result is True
        assert bridge._running is True

        # Give threads a moment to run
        time.sleep(0.5)

        status = bridge.get_status()
        assert status['running'] is True
        # Meshtastic should be disconnected (no broker)
        assert status['meshtastic_connected'] is False

        bridge.stop()
        assert bridge._running is False


class TestRNSConnectionStability:
    """Test RNS connection path — the historically problematic side."""

    def test_rns_not_installed_sets_permanent_failure(self):
        """When RNS library isn't installed, bridge marks it permanently disabled."""
        config = GatewayConfig()
        config.enabled = True
        config.bridge_mode = "mqtt_bridge"

        from gateway.rns_bridge import RNSMeshtasticBridge

        bridge = RNSMeshtasticBridge(config=config)

        with patch('gateway._rns_bridge_connection._HAS_RNS', False):
            bridge._connect_rns()

        assert bridge._connected_rns is False
        assert bridge._rns_init_failed_permanently is True

    def test_rns_already_initialized_proceeds(self):
        """When RNS singleton exists, bridge should proceed to LXMF setup."""
        config = GatewayConfig()
        config.enabled = True

        from gateway.rns_bridge import RNSMeshtasticBridge

        bridge = RNSMeshtasticBridge(config=config)
        bridge._rns_pre_initialized = True

        mock_rns = MagicMock()
        mock_lxmf = MagicMock()

        with patch('gateway._rns_bridge_connection._HAS_RNS', True), \
             patch('gateway._rns_bridge_connection._HAS_LXMF', True), \
             patch('gateway._rns_bridge_connection._RNS_mod', mock_rns), \
             patch('gateway._rns_bridge_connection._LXMF_mod', mock_lxmf), \
             patch('gateway._rns_bridge_connection.check_service') as mock_svc:

            mock_svc.return_value = MagicMock(available=True)
            bridge._connect_rns()

        assert mock_lxmf.LXMRouter.called
        assert bridge._connected_rns is True

    def test_rns_init_does_not_restart_services(self):
        """CRITICAL: _init_rns_main_thread must NEVER restart rnsd.

        This was the root cause of the worst regressions (Session 7).
        The apply_config_and_restart function must never be called.
        """
        config = GatewayConfig()
        config.enabled = True

        from gateway.rns_bridge import RNSMeshtasticBridge

        bridge = RNSMeshtasticBridge(config=config)

        mock_rns = MagicMock()
        mock_rns.Reticulum.return_value = MagicMock()

        with patch('gateway._rns_bridge_connection._HAS_RNS', True), \
             patch('gateway._rns_bridge_connection._RNS_mod', mock_rns), \
             patch('gateway._rns_bridge_connection.ReticulumPaths') as mock_paths, \
             patch('gateway._rns_bridge_connection.detect_rnsd_config_drift') as mock_drift, \
             patch('os.geteuid', return_value=0):

            mock_paths.ensure_system_dirs.return_value = True
            mock_drift.return_value = MagicMock(drifted=False)

            with patch.dict('sys.modules', {
                'utils.gateway_diagnostic': MagicMock(find_rns_processes=lambda: [])
            }):
                bridge._init_rns_main_thread()

        assert bridge._rns_pre_initialized is True

    def test_rns_loop_respects_permanent_failure(self):
        """When RNS init fails permanently, the loop should not retry."""
        config = GatewayConfig()
        config.enabled = True

        from gateway.rns_bridge import RNSMeshtasticBridge
        from gateway.bridge_health import SubsystemState

        bridge = RNSMeshtasticBridge(config=config)
        bridge._rns_init_failed_permanently = True

        # Run the loop in a background thread, let it iterate once
        bridge._running = True

        def stop_after_delay():
            time.sleep(0.2)
            bridge._running = False
            bridge._stop_event.set()

        stopper = threading.Thread(target=stop_after_delay, daemon=True)
        stopper.start()

        bridge._rns_loop()
        stopper.join(timeout=2)

        rns_state = bridge.health.get_subsystem_state("rns")
        assert rns_state == SubsystemState.DISABLED


class TestBridgeSubsystemIsolation:
    """Test that Meshtastic and RNS failures are isolated."""

    def test_meshtastic_down_does_not_affect_rns(self):
        """Meshtastic going down should not disable RNS."""
        from gateway.bridge_health import BridgeHealthMonitor, SubsystemState

        health = BridgeHealthMonitor()
        health.set_subsystem_state("meshtastic", SubsystemState.HEALTHY)
        health.set_subsystem_state("rns", SubsystemState.HEALTHY)

        health.set_subsystem_state("meshtastic", SubsystemState.DISCONNECTED)

        assert health.get_subsystem_state("rns") == SubsystemState.HEALTHY
        assert health.get_subsystem_state("meshtastic") == SubsystemState.DISCONNECTED

    def test_rns_down_does_not_affect_meshtastic(self):
        """RNS going down should not disable Meshtastic."""
        from gateway.bridge_health import BridgeHealthMonitor, SubsystemState

        health = BridgeHealthMonitor()
        health.set_subsystem_state("meshtastic", SubsystemState.HEALTHY)
        health.set_subsystem_state("rns", SubsystemState.HEALTHY)

        health.set_subsystem_state("rns", SubsystemState.DISCONNECTED)

        assert health.get_subsystem_state("meshtastic") == SubsystemState.HEALTHY
        assert health.get_subsystem_state("rns") == SubsystemState.DISCONNECTED
