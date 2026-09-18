"""
Tests for MeshCore CHANNEL_MSG_RECV dual-path metrics (Feature #5).
"""

import threading
import time
from datetime import datetime
from unittest.mock import MagicMock, patch, AsyncMock
from queue import Queue

import pytest


@pytest.fixture
def mock_config():
    """Create mock gateway config for MeshCore handler."""
    from gateway.config import GatewayConfig, MeshCoreConfig

    config = GatewayConfig()
    config.meshcore = MeshCoreConfig(
        enabled=True,
        simulation_mode=True,
        channel_poll_interval_sec=1,
    )
    return config


@pytest.fixture
def handler(mock_config):
    """Create MeshCoreHandler with simulation mode."""
    from gateway.meshcore_handler import MeshCoreHandler

    node_tracker = MagicMock()
    health = MagicMock()
    health.record_connection_event = MagicMock()
    health.record_error = MagicMock(return_value="test")
    health.record_message_sent = MagicMock()

    stop_event = threading.Event()
    stats = {}
    stats_lock = threading.Lock()

    return MeshCoreHandler(
        config=mock_config,
        node_tracker=node_tracker,
        health=health,
        stop_event=stop_event,
        stats=stats,
        stats_lock=stats_lock,
        message_queue=Queue(maxsize=100),
    )


class TestChannelMetrics:

    def test_initial_metrics(self, handler):
        metrics = handler.get_channel_metrics()
        assert metrics['event_received'] == 0
        assert metrics['poll_discovered'] == 0
        assert metrics['event_missed'] == 0
        assert metrics['duplicate_reconciled'] == 0
        assert metrics['poll_cycles'] == 0

    def test_compute_channel_hash(self, handler):
        from gateway.canonical_message import CanonicalMessage

        msg1 = CanonicalMessage(
            source_address="abc123",
            content="Hello world",
        )
        msg2 = CanonicalMessage(
            source_address="abc123",
            content="Hello world",
        )
        msg3 = CanonicalMessage(
            source_address="abc123",
            content="Different message",
        )

        hash1 = handler._compute_channel_hash(msg1)
        hash2 = handler._compute_channel_hash(msg2)
        hash3 = handler._compute_channel_hash(msg3)

        # Same content from same source = same hash
        assert hash1 == hash2
        # Different content = different hash
        assert hash1 != hash3

    # ⚠️ Rewritten 2026-09-18 with the ChannelPath extraction. These tests
    # used to hand-simulate the bookkeeping ("Simulate what
    # _on_channel_message does to metrics") and then assert their own
    # mutation — they would have passed with the handler deleted. The
    # dual-path bookkeeping is now a real method, so they call it. The async
    # legs themselves are driven end-to-end in
    # tests/test_meshcore_channel_allowlist.py, which the old comment here
    # ("we can't easily test the async method directly") said was infeasible.

    def test_cleanup_channel_hashes(self, handler):
        cp = handler._channel_path
        old_time = time.monotonic() - 200  # Older than the 120s window
        cp.record_event("old1", old_time)
        cp.record_event("old2", old_time)
        cp.record_poll("old3", old_time)
        cp.record_event("recent", time.monotonic())

        handler._cleanup_channel_hashes()

        assert "old1" not in cp._event_hashes
        assert "old2" not in cp._event_hashes
        assert "old3" not in cp._poll_hashes
        assert "recent" in cp._event_hashes

    def test_log_channel_metrics_no_messages(self, handler):
        """log_channel_metrics should not error when no messages."""
        handler._log_channel_metrics()  # Should not raise

    def test_log_channel_metrics_with_data(self, handler):
        cp = handler._channel_path
        now = time.monotonic()
        for i in range(10):
            cp.record_event(f"e{i}", now)
        for i in range(2):
            cp.record_poll(f"p{i}", now)
        cp.begin_poll_cycle()

        m = handler.get_channel_metrics()
        assert m['event_received'] == 10
        assert m['poll_discovered'] == 2
        assert m['event_missed'] == 2
        assert m['poll_cycles'] == 1

        handler._log_channel_metrics()  # Should not raise

    def test_metrics_tracking_on_channel_message(self, handler):
        """The real event-path bookkeeping, not a re-implementation."""
        from gateway.canonical_message import CanonicalMessage

        msg = CanonicalMessage(
            source_address="test123",
            content="Test channel broadcast",
        )
        cp = handler._channel_path
        content_hash = handler._compute_channel_hash(msg)

        is_poll_dup = cp.record_event(content_hash, time.monotonic())

        assert is_poll_dup is False, "nothing polled it first"
        assert handler.get_channel_metrics()['event_received'] == 1
        assert content_hash in cp._event_hashes

    def test_dual_path_reconciliation(self, handler):
        """Poll sees it first, then the event path delivers the same message."""
        cp = handler._channel_path
        content_hash = "test_hash_abc"
        now = time.monotonic()

        assert cp.record_poll(content_hash, now) is False
        # The event path must RECOGNISE it as already seen.
        assert cp.record_event(content_hash, now) is True

        m = handler.get_channel_metrics()
        assert m['poll_discovered'] == 1
        assert m['duplicate_reconciled'] == 1

    def test_event_missed_tracking(self, handler):
        """Poll finds a message the event subscription never delivered (#1232)."""
        cp = handler._channel_path

        assert cp.record_poll("polled_only_hash", time.monotonic()) is False

        m = handler.get_channel_metrics()
        assert m['event_missed'] == 1
        assert m['poll_discovered'] == 1
