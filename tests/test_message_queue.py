"""
Tests for PersistentMessageQueue — SQLite-backed message queue with
retry, deduplication, priority ordering, dead letter queue, lifecycle
tracking, and overflow management.

Uses in-memory SQLite (:memory:) for fast, isolated tests.

Run: python3 -m pytest tests/test_message_queue.py -v
"""

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gateway.message_queue import (
    MessageLifecycleEvent,
    MessageLifecycleState,
    MessagePriority,
    MessageStatus,
    PersistentMessageQueue,
    QueuedMessage,
    RetryDecision,
    RetryPolicy,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def queue(tmp_path):
    """Queue backed by temp file (each _get_connection shares the same DB)."""
    db_file = str(tmp_path / "test_queue.db")
    q = PersistentMessageQueue(db_path=db_file)
    yield q
    q.stop_processing()


@pytest.fixture
def queue_with_policy(tmp_path):
    """Queue with retry policy backed by temp file."""
    db_file = str(tmp_path / "test_queue_policy.db")
    policy = RetryPolicy(max_tries=3, base_delay=0.01)
    q = PersistentMessageQueue(db_path=db_file, retry_policy=policy)
    yield q
    q.stop_processing()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TestEnums:
    """Test enum values."""

    def test_priority_ordering(self):
        assert MessagePriority.LOW.value < MessagePriority.NORMAL.value
        assert MessagePriority.NORMAL.value < MessagePriority.HIGH.value
        assert MessagePriority.HIGH.value < MessagePriority.URGENT.value

    def test_status_values(self):
        assert MessageStatus.PENDING.value == "pending"
        assert MessageStatus.DEAD_LETTER.value == "dead_letter"

    def test_lifecycle_states(self):
        assert MessageLifecycleState.CREATED.value == "created"
        assert MessageLifecycleState.ACK.value == "ack"


# ---------------------------------------------------------------------------
# QueuedMessage
# ---------------------------------------------------------------------------

class TestQueuedMessage:
    """Tests for QueuedMessage data class."""

    def test_to_dict_roundtrip(self):
        msg = QueuedMessage(
            id="test-1",
            payload={"text": "hello", "from": "!abc"},
            destination="meshtastic",
            priority=MessagePriority.HIGH,
        )
        d = msg.to_dict()
        restored = QueuedMessage.from_dict(d)
        assert restored.id == msg.id
        assert restored.payload == msg.payload
        assert restored.destination == msg.destination
        assert restored.priority == MessagePriority.HIGH

    def test_payload_serialized_as_json(self):
        msg = QueuedMessage(
            id="test-2",
            payload={"key": "value"},
            destination="rns",
        )
        d = msg.to_dict()
        assert isinstance(d["payload"], str)
        assert json.loads(d["payload"]) == {"key": "value"}

    def test_retry_after_none(self):
        msg = QueuedMessage(id="t", payload={}, destination="x")
        d = msg.to_dict()
        assert d["retry_after"] is None

    def test_retry_after_set(self):
        future = datetime.now() + timedelta(seconds=30)
        msg = QueuedMessage(id="t", payload={}, destination="x", retry_after=future)
        d = msg.to_dict()
        restored = QueuedMessage.from_dict(d)
        assert restored.retry_after is not None


# ---------------------------------------------------------------------------
# MessageLifecycleEvent
# ---------------------------------------------------------------------------

class TestMessageLifecycleEvent:
    """Tests for lifecycle event serialization."""

    def test_to_dict_roundtrip(self):
        event = MessageLifecycleEvent(
            message_id="msg-1",
            state=MessageLifecycleState.SENT,
            timestamp=datetime.now(),
            details="via meshtastic",
            node_id="!aabb",
            hop_count=2,
        )
        d = event.to_dict()
        restored = MessageLifecycleEvent.from_dict(d)
        assert restored.message_id == "msg-1"
        assert restored.state == MessageLifecycleState.SENT
        assert restored.node_id == "!aabb"
        assert restored.hop_count == 2


# ---------------------------------------------------------------------------
# RetryPolicy
# ---------------------------------------------------------------------------

class TestRetryPolicy:
    """Tests for NGINX-style retry policy."""

    def test_retriable_error_retries(self):
        policy = RetryPolicy(max_tries=3)
        decision = policy.should_retry("connection refused", attempt=1)
        assert decision.retry is True
        assert decision.delay > 0

    def test_non_retriable_error_no_retry(self):
        policy = RetryPolicy(max_tries=3)
        decision = policy.should_retry("permission denied", attempt=1)
        assert decision.retry is False
        assert "permanent_error" in decision.reason

    def test_max_attempts_exceeded(self):
        policy = RetryPolicy(max_tries=3)
        decision = policy.should_retry("timeout", attempt=3)
        assert decision.retry is False
        assert "max_attempts" in decision.reason

    def test_unknown_error_retries_once(self):
        policy = RetryPolicy(max_tries=5)
        d1 = policy.should_retry("something weird", attempt=1)
        assert d1.retry is True
        d2 = policy.should_retry("something weird", attempt=2)
        assert d2.retry is False

    def test_exponential_backoff_delay(self):
        policy = RetryPolicy(base_delay=2.0, max_delay=60.0)
        assert policy.get_delay_for_attempt(1) == 2.0
        assert policy.get_delay_for_attempt(2) == 4.0
        assert policy.get_delay_for_attempt(3) == 8.0

    def test_delay_capped_at_max(self):
        policy = RetryPolicy(base_delay=2.0, max_delay=10.0)
        assert policy.get_delay_for_attempt(10) == 10.0

    def test_timeout_is_retriable(self):
        policy = RetryPolicy()
        d = policy.should_retry("operation timed out", attempt=1)
        assert d.retry is True

    def test_broken_pipe_is_retriable(self):
        policy = RetryPolicy()
        d = policy.should_retry("broken pipe", attempt=1)
        assert d.retry is True

    def test_message_too_large_not_retriable(self):
        policy = RetryPolicy()
        d = policy.should_retry("message too large", attempt=1)
        assert d.retry is False

    def test_for_meshtastic(self):
        p = RetryPolicy.for_meshtastic()
        assert p.max_tries == 3
        assert p.base_delay == 5.0

    def test_for_rns(self):
        p = RetryPolicy.for_rns()
        assert p.max_tries == 5
        assert p.base_delay == 2.0

    def test_classify_case_insensitive(self):
        policy = RetryPolicy()
        d = policy.should_retry("CONNECTION REFUSED by remote", attempt=1)
        assert d.retry is True


# ---------------------------------------------------------------------------
# PersistentMessageQueue — enqueue
# ---------------------------------------------------------------------------

class TestEnqueue:
    """Tests for message enqueue."""

    def test_enqueue_returns_id(self, queue):
        msg_id = queue.enqueue({"text": "hello"}, "meshtastic")
        assert msg_id is not None
        assert isinstance(msg_id, str)

    def test_enqueue_increments_stats(self, queue):
        queue.enqueue({"text": "hello"}, "meshtastic")
        assert queue._stats["enqueued"] == 1

    def test_enqueue_multiple(self, queue):
        id1 = queue.enqueue({"text": "one"}, "meshtastic")
        id2 = queue.enqueue({"text": "two"}, "rns")
        assert id1 != id2

    def test_enqueue_with_priority(self, queue):
        queue.enqueue({"text": "urgent"}, "meshtastic", priority=MessagePriority.URGENT)
        msgs = queue.get_pending()
        assert len(msgs) == 1
        assert msgs[0].priority == MessagePriority.URGENT


# ---------------------------------------------------------------------------
# PersistentMessageQueue — deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    """Tests for message deduplication."""

    def test_duplicate_suppressed(self, queue):
        id1 = queue.enqueue({"text": "hello", "from": "a", "to": "b"}, "meshtastic")
        id2 = queue.enqueue({"text": "hello", "from": "a", "to": "b"}, "meshtastic")
        assert id1 is not None
        assert id2 is None
        assert queue._stats["deduplicated"] == 1

    def test_different_messages_not_deduplicated(self, queue):
        id1 = queue.enqueue({"text": "hello"}, "meshtastic")
        id2 = queue.enqueue({"text": "world"}, "meshtastic")
        assert id1 is not None
        assert id2 is not None

    def test_same_content_different_destination_not_deduplicated(self, queue):
        id1 = queue.enqueue({"text": "hello"}, "meshtastic")
        id2 = queue.enqueue({"text": "hello"}, "rns")
        assert id1 is not None
        assert id2 is not None

    def test_dedup_disabled(self, queue):
        id1 = queue.enqueue({"text": "hello"}, "meshtastic", deduplicate=False)
        id2 = queue.enqueue({"text": "hello"}, "meshtastic", deduplicate=False)
        assert id1 is not None
        assert id2 is not None

    def test_identical_content_same_millisecond_gets_distinct_ids(
            self, queue, monkeypatch):
        """Regression: the ms-timestamp + content-hash id was NOT unique —
        identical content enqueued twice in the same millisecond collided
        on the TEXT PRIMARY KEY and the second INSERT failed (the
        test_dedup_disabled flake on fast boxes). Clock frozen so the
        collision shape is deterministic, not timing-dependent."""
        from unittest.mock import patch
        import gateway.message_queue as mq
        monkeypatch.setattr(mq.time, "time", lambda: 1759000000.123)
        id1 = queue.enqueue({"text": "hello"}, "meshtastic", deduplicate=False)
        id2 = queue.enqueue({"text": "hello"}, "meshtastic", deduplicate=False)
        assert id1 is not None
        assert id2 is not None
        assert id1 != id2

    def test_same_content_two_destinations_same_millisecond(
            self, queue, monkeypatch):
        """Same latent collision, the other legitimate shape: same content
        to two destinations in one millisecond (dedup is per-destination,
        so both must enqueue — and the PRIMARY KEY must not collide)."""
        from unittest.mock import patch
        import gateway.message_queue as mq
        monkeypatch.setattr(mq.time, "time", lambda: 1759000000.456)
        id1 = queue.enqueue({"text": "hello"}, "meshtastic")
        id2 = queue.enqueue({"text": "hello"}, "rns")
        assert id1 is not None
        assert id2 is not None
        assert id1 != id2

    def test_message_shaped_payloads_hash_distinctly(self, queue):
        """Regression: RNS→Mesh payloads use `message`/`destination`, not
        `text`/`from`/`to`. The old hash keyed only on the latter, so every
        such payload hashed identically and all but the first within
        DEDUP_WINDOW were dropped as false duplicates."""
        h1 = queue._compute_hash({"message": "Memorial Day: rain", "destination": None})
        h2 = queue._compute_hash({"message": "Tonight: clear", "destination": None})
        assert h1 != h2

    def test_distinct_message_payloads_all_enqueue(self, queue):
        """Two different `message`-shaped payloads to the same destination
        within the dedup window must BOTH enqueue (the degenerate hash
        collapsed them, dropping the second)."""
        id1 = queue.enqueue({"message": "chunk one", "channel": 2}, "meshtastic")
        id2 = queue.enqueue({"message": "chunk two", "channel": 2}, "meshtastic")
        assert id1 is not None
        assert id2 is not None

    def test_message_payload_dedup_still_works_for_identical(self, queue):
        """The hash is still content-sensitive: a genuinely identical
        `message` payload within the window dedups (multi-path protection)."""
        id1 = queue.enqueue({"message": "same", "destination": "x"}, "meshtastic")
        id2 = queue.enqueue({"message": "same", "destination": "x"}, "meshtastic")
        assert id1 is not None
        assert id2 is None

    def test_multipath_duplicate_dedups_despite_different_source(self, queue):
        """The SAME logical RNS→Mesh message arriving via two transport paths
        (direct vs peer-gateway relay → different source_id) must dedup. The
        body already encodes origin via its prefix, so source_id must NOT be
        in the key. Regression for identical [RNS:627f] commands enqueued 2x."""
        p1 = {"message": "[RNS:627f] [ch0:p4] wx", "channel": 2,
              "source_id": "627fa566...", "destination": None}
        p2 = {"message": "[RNS:627f] [ch0:p4] wx", "channel": 2,
              "source_id": "f68c2f56...", "destination": None}  # relayed: diff source
        assert queue._compute_hash(p1) == queue._compute_hash(p2)
        id1 = queue.enqueue(p1, "meshtastic")
        id2 = queue.enqueue(p2, "meshtastic")
        assert id1 is not None
        assert id2 is None  # multi-path duplicate suppressed

    def test_content_shaped_spill_payloads_hash_distinctly(self, queue):
        """Regression: the Mesh→RNS spill payload (_spill_to_persistent_queue)
        is `content`-shaped — no `message`, no from/to/text/type. The shape
        branch missed it, so it hit the degenerate all-None text hash and
        every distinct spilled message collapsed to one hash (dropped as false
        duplicates exactly during the outage the spill exists to survive)."""
        h1 = queue._compute_hash(
            {"content": "hello via mesh", "source_id": "!a", "destination_id": "!z"})
        h2 = queue._compute_hash(
            {"content": "different body", "source_id": "!a", "destination_id": "!z"})
        assert h1 != h2

    def test_distinct_content_spills_all_enqueue(self, queue):
        """Two different `content`-shaped spills to the same destination within
        the dedup window must BOTH enqueue (the degenerate hash collapsed
        them)."""
        id1 = queue.enqueue({"content": "spill one", "source_id": "!a"}, "rns_xform")
        id2 = queue.enqueue({"content": "spill two", "source_id": "!a"}, "rns_xform")
        assert id1 is not None
        assert id2 is not None

    def test_content_spill_dedup_still_works_for_identical(self, queue):
        """Content hash stays content-sensitive: an identical spill within the
        window still dedups."""
        p = {"content": "same body", "source_id": "!a", "destination_id": "!z"}
        assert queue.enqueue(dict(p), "rns_xform") is not None
        assert queue.enqueue(dict(p), "rns_xform") is None

    def test_is_recent_duplicate_distinguishes_dedup_from_pressure(self, queue):
        """is_recent_duplicate() lets a caller tell a benign dedup-suppression
        (enqueue→None because already queued) from a real queue rejection
        (enqueue→None because full): only the former is a 'recent duplicate'."""
        p = {"message": "hi", "channel": 2, "destination": None}
        assert queue.is_recent_duplicate(p, "meshtastic") is False  # not seen yet
        assert queue.enqueue(p, "meshtastic") is not None
        # Now it IS a recent duplicate (a second enqueue would dedup-drop).
        assert queue.is_recent_duplicate(p, "meshtastic") is True
        assert queue.enqueue(p, "meshtastic") is None


# ---------------------------------------------------------------------------
# PersistentMessageQueue — get_pending / priority ordering
# ---------------------------------------------------------------------------

class TestGetPending:
    """Tests for pending message retrieval."""

    def test_empty_queue(self, queue):
        assert queue.get_pending() == []

    def test_priority_ordering(self, queue):
        queue.enqueue({"text": "low"}, "meshtastic", priority=MessagePriority.LOW, deduplicate=False)
        queue.enqueue({"text": "urgent"}, "meshtastic", priority=MessagePriority.URGENT, deduplicate=False)
        queue.enqueue({"text": "normal"}, "meshtastic", priority=MessagePriority.NORMAL, deduplicate=False)

        msgs = queue.get_pending()
        assert len(msgs) == 3
        assert msgs[0].priority == MessagePriority.URGENT
        assert msgs[1].priority == MessagePriority.NORMAL
        assert msgs[2].priority == MessagePriority.LOW

    def test_filter_by_destination(self, queue):
        queue.enqueue({"text": "mesh"}, "meshtastic")
        queue.enqueue({"text": "rns"}, "rns")

        mesh_msgs = queue.get_pending(destination="meshtastic")
        assert len(mesh_msgs) == 1
        assert mesh_msgs[0].destination == "meshtastic"

    def test_limit(self, queue):
        for i in range(10):
            queue.enqueue({"text": f"msg{i}", "id": i}, "meshtastic", deduplicate=False)
        msgs = queue.get_pending(limit=3)
        assert len(msgs) == 3


# ---------------------------------------------------------------------------
# PersistentMessageQueue — status transitions
# ---------------------------------------------------------------------------

class TestStatusTransitions:
    """Tests for mark_in_progress, mark_delivered, mark_failed."""

    def test_mark_in_progress(self, queue):
        msg_id = queue.enqueue({"text": "test"}, "meshtastic")
        assert queue.mark_in_progress(msg_id) is True
        # Should not appear in pending anymore
        assert len(queue.get_pending()) == 0

    def test_mark_in_progress_idempotent(self, queue):
        msg_id = queue.enqueue({"text": "test"}, "meshtastic")
        queue.mark_in_progress(msg_id)
        # Second call fails (already in_progress, not pending)
        assert queue.mark_in_progress(msg_id) is False

    def test_mark_delivered(self, queue):
        msg_id = queue.enqueue({"text": "test"}, "meshtastic")
        queue.mark_in_progress(msg_id)
        assert queue.mark_delivered(msg_id) is True
        assert queue._stats["delivered"] == 1

    def test_mark_failed_schedules_retry(self, queue):
        msg_id = queue.enqueue({"text": "test"}, "meshtastic", max_retries=3)
        queue.mark_in_progress(msg_id)
        queue.mark_failed(msg_id, "connection reset")
        # Should be back to pending with retry_after set
        assert queue._stats["retried"] == 1

    def test_mark_failed_dead_letter_after_max(self, queue):
        msg_id = queue.enqueue({"text": "test"}, "meshtastic", max_retries=1)
        queue.mark_in_progress(msg_id)
        queue.mark_failed(msg_id, "error")
        # retry_count=1 >= max_retries=1 -> dead letter
        assert queue._stats["failed"] == 1
        dead = queue.get_dead_letters()
        assert len(dead) == 1

    def test_mark_failed_nonexistent(self, queue):
        assert queue.mark_failed("nonexistent", "error") is False


# ---------------------------------------------------------------------------
# PersistentMessageQueue — RetryPolicy integration
# ---------------------------------------------------------------------------

class TestRetryPolicyIntegration:
    """Tests for intelligent retry with RetryPolicy."""

    def test_permanent_error_goes_to_dead_letter(self, queue_with_policy):
        msg_id = queue_with_policy.enqueue({"text": "test"}, "meshtastic")
        queue_with_policy.mark_in_progress(msg_id)
        queue_with_policy.mark_failed(msg_id, "permission denied")
        assert queue_with_policy._stats["permanent_failures"] == 1
        dead = queue_with_policy.get_dead_letters()
        assert len(dead) == 1

    def test_transient_error_retries(self, queue_with_policy):
        msg_id = queue_with_policy.enqueue({"text": "test"}, "meshtastic")
        queue_with_policy.mark_in_progress(msg_id)
        queue_with_policy.mark_failed(msg_id, "connection refused")
        assert queue_with_policy._stats["retried"] == 1

    def test_set_retry_policy(self, queue):
        policy = RetryPolicy(max_tries=5)
        queue.set_retry_policy(policy)
        assert queue._retry_policy is policy


# ---------------------------------------------------------------------------
# PersistentMessageQueue — process_once
# ---------------------------------------------------------------------------

class TestProcessOnce:
    """Tests for single-batch processing."""

    def test_process_delivers_message(self, queue):
        sender = MagicMock(return_value=True)
        queue.register_sender("meshtastic", sender)
        queue.enqueue({"text": "hello"}, "meshtastic")

        processed = queue.process_once()
        assert processed == 1
        sender.assert_called_once()

    def test_process_failed_send(self, queue):
        sender = MagicMock(return_value=False)
        queue.register_sender("meshtastic", sender)
        queue.enqueue({"text": "hello"}, "meshtastic")

        processed = queue.process_once()
        assert processed == 1
        # Message should be scheduled for retry or dead_letter

    def test_process_exception(self, queue):
        sender = MagicMock(side_effect=RuntimeError("network down"))
        queue.register_sender("meshtastic", sender)
        queue.enqueue({"text": "hello"}, "meshtastic")

        processed = queue.process_once()
        assert processed == 1

    def test_success_callback(self, queue):
        sender = MagicMock(return_value=True)
        success_cb = MagicMock()
        queue.register_sender("meshtastic", sender)
        queue.register_success_callback(success_cb)
        queue.enqueue({"text": "hello"}, "meshtastic")

        queue.process_once()
        success_cb.assert_called_once()

    def test_failure_callback(self, queue):
        sender = MagicMock(side_effect=RuntimeError("fail"))
        failure_cb = MagicMock()
        queue.register_sender("meshtastic", sender)
        queue.register_failure_callback(failure_cb)
        queue.enqueue({"text": "hello"}, "meshtastic")

        queue.process_once()
        failure_cb.assert_called_once()

    def test_no_sender_registered(self, queue):
        queue.enqueue({"text": "hello"}, "meshtastic")
        processed = queue.process_once()
        assert processed == 0  # No sender for this destination


# ---------------------------------------------------------------------------
# PersistentMessageQueue — background processing
# ---------------------------------------------------------------------------

class TestBackgroundProcessing:
    """Tests for start/stop processing."""

    def test_start_stop(self, queue):
        queue.start_processing(interval=0.01)
        assert queue._processing is True
        queue.stop_processing()
        time.sleep(0.1)
        assert queue._processing is False

    def test_start_idempotent(self, queue):
        queue.start_processing(interval=0.01)
        queue.start_processing(interval=0.01)  # Should be no-op
        queue.stop_processing()

    def test_background_processes_messages(self, queue):
        sender = MagicMock(return_value=True)
        queue.register_sender("meshtastic", sender)
        queue.enqueue({"text": "bg-test"}, "meshtastic")

        queue.start_processing(interval=0.01)
        time.sleep(0.15)
        queue.stop_processing()

        sender.assert_called()


# ---------------------------------------------------------------------------
# PersistentMessageQueue — stats
# ---------------------------------------------------------------------------

class TestStats:
    """Tests for queue statistics."""

    def test_initial_stats(self, queue):
        stats = queue.get_stats()
        assert stats["enqueued"] == 0
        assert stats["pending"] == 0
        assert stats["queue_depth"] == 0
        assert stats["max_queue_size"] == 1000

    def test_stats_after_enqueue(self, queue):
        queue.enqueue({"text": "hello"}, "meshtastic")
        stats = queue.get_stats()
        assert stats["enqueued"] == 1
        assert stats["pending"] == 1
        assert stats["queue_depth"] == 1

    def test_queue_usage_pct(self, queue):
        queue.enqueue({"text": "hello"}, "meshtastic")
        stats = queue.get_stats()
        assert stats["queue_usage_pct"] == 0.1  # 1/1000 * 100

    def test_get_queue_depth(self, queue):
        queue.enqueue({"text": "a"}, "meshtastic")
        queue.enqueue({"text": "b"}, "rns")
        assert queue.get_queue_depth() == 2


# ---------------------------------------------------------------------------
# PersistentMessageQueue — overflow / shedding
# ---------------------------------------------------------------------------

class TestOverflow:
    """Tests for queue overflow management."""

    def test_queue_enforces_max_size(self, tmp_path):
        db_file = str(tmp_path / "overflow1.db")
        q = PersistentMessageQueue(db_path=db_file, max_queue_size=3)
        q.enqueue({"text": "1", "id": 1}, "meshtastic", priority=MessagePriority.LOW, deduplicate=False)
        q.enqueue({"text": "2", "id": 2}, "meshtastic", priority=MessagePriority.LOW, deduplicate=False)
        q.enqueue({"text": "3", "id": 3}, "meshtastic", priority=MessagePriority.LOW, deduplicate=False)
        # Queue is full, next enqueue should shed
        id4 = q.enqueue({"text": "4", "id": 4}, "meshtastic", priority=MessagePriority.NORMAL, deduplicate=False)
        assert id4 is not None
        assert q._stats["shed"] >= 1
        q.stop_processing()

    def test_high_priority_not_shed(self, tmp_path):
        db_file = str(tmp_path / "overflow2.db")
        q = PersistentMessageQueue(db_path=db_file, max_queue_size=2)
        q.enqueue({"text": "hi1"}, "meshtastic", priority=MessagePriority.HIGH, deduplicate=False)
        q.enqueue({"text": "hi2"}, "meshtastic", priority=MessagePriority.HIGH, deduplicate=False)
        # Can't shed high-priority, so next enqueue returns None
        id3 = q.enqueue({"text": "low"}, "meshtastic", priority=MessagePriority.LOW, deduplicate=False)
        assert id3 is None
        assert q._stats["shed_rejected"] >= 1
        q.stop_processing()


# ---------------------------------------------------------------------------
# PersistentMessageQueue — dead letter
# ---------------------------------------------------------------------------

class TestDeadLetter:
    """Tests for dead letter queue."""

    def test_dead_letter_retrieval(self, queue):
        msg_id = queue.enqueue({"text": "test"}, "meshtastic", max_retries=1)
        queue.mark_in_progress(msg_id)
        queue.mark_failed(msg_id, "permanent failure")

        dead = queue.get_dead_letters()
        assert len(dead) == 1
        assert dead[0].id == msg_id

    def test_retry_dead_letter(self, queue):
        msg_id = queue.enqueue({"text": "test"}, "meshtastic", max_retries=1)
        queue.mark_in_progress(msg_id)
        queue.mark_failed(msg_id, "error")

        assert queue.retry_dead_letter(msg_id) is True
        pending = queue.get_pending()
        assert any(m.id == msg_id for m in pending)

    def test_retry_nonexistent_dead_letter(self, queue):
        assert queue.retry_dead_letter("nonexistent") is False


# ---------------------------------------------------------------------------
# PersistentMessageQueue — cleanup
# ---------------------------------------------------------------------------

class TestCleanup:
    """Tests for cleanup and purge operations."""

    def test_clear_all(self, queue):
        queue.enqueue({"text": "a"}, "meshtastic")
        queue.enqueue({"text": "b"}, "rns")
        count = queue.clear_all()
        assert count == 2
        assert queue.get_queue_depth() == 0

    def test_cleanup_stale(self, queue):
        msg_id = queue.enqueue({"text": "test"}, "meshtastic")
        queue.mark_in_progress(msg_id)

        # Manually make it stale by backdating updated_at
        old_time = (datetime.now() - timedelta(seconds=600)).isoformat()
        with queue._get_connection() as conn:
            conn.execute(
                "UPDATE messages SET updated_at = ? WHERE id = ?",
                (old_time, msg_id)
            )

        count = queue.cleanup_stale()
        assert count == 1
        # Should be back to pending
        pending = queue.get_pending()
        assert len(pending) == 1

    def test_purge_old(self, queue):
        msg_id = queue.enqueue({"text": "test"}, "meshtastic")
        queue.mark_in_progress(msg_id)
        queue.mark_delivered(msg_id)

        # Backdate to 10 days ago
        old_time = (datetime.now() - timedelta(days=10)).isoformat()
        with queue._get_connection() as conn:
            conn.execute(
                "UPDATE messages SET updated_at = ? WHERE id = ?",
                (old_time, msg_id)
            )

        count = queue.purge_old(days=7)
        assert count == 1


# ---------------------------------------------------------------------------
# Message lifecycle tracking
# ---------------------------------------------------------------------------

class TestLifecycleTracking:
    """Tests for message lifecycle event recording and retrieval."""

    def test_record_event(self, queue):
        result = queue.record_lifecycle_event(
            "msg-1", MessageLifecycleState.CREATED, details="test"
        )
        assert result is True

    def test_get_message_trace(self, queue):
        queue.record_lifecycle_event("msg-1", MessageLifecycleState.CREATED)
        queue.record_lifecycle_event("msg-1", MessageLifecycleState.QUEUED)
        queue.record_lifecycle_event("msg-1", MessageLifecycleState.SENT)

        trace = queue.get_message_trace("msg-1")
        assert len(trace) == 3
        assert trace[0].state == MessageLifecycleState.CREATED
        assert trace[2].state == MessageLifecycleState.SENT

    def test_trace_empty_for_unknown(self, queue):
        assert queue.get_message_trace("unknown") == []

    def test_get_recent_events(self, queue):
        queue.record_lifecycle_event("msg-1", MessageLifecycleState.CREATED)
        queue.record_lifecycle_event("msg-2", MessageLifecycleState.SENT)

        events = queue.get_recent_events(limit=10)
        assert len(events) == 2

    def test_recent_events_with_filter(self, queue):
        queue.record_lifecycle_event("msg-1", MessageLifecycleState.CREATED)
        queue.record_lifecycle_event("msg-1", MessageLifecycleState.SENT)
        queue.record_lifecycle_event("msg-2", MessageLifecycleState.CREATED)

        sent = queue.get_recent_events(state_filter=MessageLifecycleState.SENT)
        assert len(sent) == 1
        assert sent[0].state == MessageLifecycleState.SENT

    def test_message_summary(self, queue):
        msg_id = queue.enqueue({"text": "hello"}, "meshtastic")
        queue.record_lifecycle_event(msg_id, MessageLifecycleState.CREATED)
        queue.record_lifecycle_event(msg_id, MessageLifecycleState.QUEUED)

        summary = queue.get_message_summary(msg_id)
        assert summary is not None
        assert summary["message_id"] == msg_id
        assert summary["destination"] == "meshtastic"
        assert len(summary["lifecycle"]["states_reached"]) == 2

    def test_message_summary_not_found(self, queue):
        assert queue.get_message_summary("nonexistent") is None

    def test_lifecycle_with_hop_count(self, queue):
        queue.record_lifecycle_event(
            "msg-1", MessageLifecycleState.RELAYED,
            node_id="!relay1", hop_count=2
        )
        trace = queue.get_message_trace("msg-1")
        assert trace[0].hop_count == 2
        assert trace[0].node_id == "!relay1"

    def test_purge_lifecycle_history(self, queue):
        queue.record_lifecycle_event("msg-1", MessageLifecycleState.CREATED)

        # Backdate the entry
        old_time = (datetime.now() - timedelta(days=40)).isoformat()
        with queue._get_connection() as conn:
            conn.execute(
                "UPDATE message_lifecycle SET timestamp = ?",
                (old_time,)
            )

        count = queue.purge_lifecycle_history(days=30)
        assert count == 1

    def test_failed_messages_with_reason(self, queue):
        msg_id = queue.enqueue({"text": "test"}, "meshtastic", max_retries=1)
        queue.mark_in_progress(msg_id)
        queue.mark_failed(msg_id, "radio timeout")
        queue.record_lifecycle_event(
            msg_id, MessageLifecycleState.FAILED, details="radio timeout"
        )

        results = queue.get_failed_messages_with_reason(hours=1)
        assert len(results) == 1
        assert results[0]["error_message"] == "radio timeout"


# ---------------------------------------------------------------------------
# MQTT retry policy and destination
# ---------------------------------------------------------------------------

class TestMQTTRetryPolicy:
    """Tests for MQTT-specific retry policy and destination support."""

    def test_for_mqtt_defaults(self):
        """RetryPolicy.for_mqtt() returns expected configuration."""
        p = RetryPolicy.for_mqtt()
        assert p.max_tries == 5
        assert p.base_delay == 1.0
        assert p.max_delay == 15.0

    def test_mqtt_not_connected_retriable(self):
        """MQTT 'not connected' error should be retriable."""
        policy = RetryPolicy()
        d = policy.should_retry("not connected", attempt=1)
        assert d.retry is True

    def test_mqtt_queue_full_retriable(self):
        """MQTT 'queue full' error should be retriable."""
        policy = RetryPolicy()
        d = policy.should_retry("queue full", attempt=1)
        assert d.retry is True

    def test_mqtt_not_authorised_non_retriable(self):
        """MQTT 'not authorised' error should not be retriable."""
        policy = RetryPolicy()
        d = policy.should_retry("not authorised", attempt=1)
        assert d.retry is False
        assert "permanent_error" in d.reason

    def test_mqtt_topic_invalid_non_retriable(self):
        """MQTT 'topic invalid' error should not be retriable."""
        policy = RetryPolicy()
        d = policy.should_retry("topic invalid", attempt=1)
        assert d.retry is False

    def test_mqtt_destination_enqueue(self, queue):
        """Enqueue with destination='mqtt' should succeed."""
        msg_id = queue.enqueue({"text": "hello mqtt"}, "mqtt")
        assert msg_id is not None

        pending = queue.get_pending(destination="mqtt")
        assert len(pending) == 1
        assert pending[0].destination == "mqtt"

    def test_mqtt_sender_callback(self, queue):
        """Mock sender registered for 'mqtt' should be called during processing."""
        sender = MagicMock(return_value=True)
        queue.register_sender("mqtt", sender)
        queue.enqueue({"text": "test"}, "mqtt")

        processed = queue.process_once()
        assert processed == 1
        sender.assert_called_once()


# ---------------------------------------------------------------------------
# Issue #66: application-layer ack tracking substrate
# ---------------------------------------------------------------------------

class TestPendingAckTrackingIssue66:
    """
    register_pending_ack / mark_acked / mark_timeout / find_overdue_acks /
    get_ack_status — the queue-side substrate that step 3b will wire into
    the LXMF delivery callback and the periodic overdue sweep.
    """

    def test_register_pending_ack_returns_true_for_existing_message(
        self, queue,
    ):
        msg_id = queue.enqueue({"text": "weather pls"}, "rns")
        assert queue.register_pending_ack(
            msg_id,
            origin_network="meshcore",
            origin_address="abc123",
            timeout_seconds=60,
        ) is True
        assert queue.get_ack_status(msg_id) == 'pending'

    def test_register_pending_ack_returns_false_for_missing_message(
        self, queue,
    ):
        assert queue.register_pending_ack(
            "ghost-id",
            origin_network="meshcore",
            origin_address="abc123",
        ) is False
        assert queue.get_ack_status("ghost-id") is None

    def test_register_pending_ack_allow_orphan_inserts_bookkeeping_row(
        self, queue,
    ):
        """allow_orphan=True path used by MeshtasticBroadcastBridge — fan-out
        bypasses enqueue() so there's no underlying row; we still need a
        substrate record so mark_acked / find_overdue_acks correlate the
        downstream LXMF receipt to this msg_id."""
        msg_id = "mbcast-1234567890-ch2"
        assert queue.register_pending_ack(
            msg_id,
            origin_network="meshtastic",
            origin_address="channel:2",
            timeout_seconds=60,
            allow_orphan=True,
        ) is True
        assert queue.get_ack_status(msg_id) == 'pending'

    def test_allow_orphan_makes_mark_acked_work_for_synthetic_rows(
        self, queue,
    ):
        """End-to-end: orphan-inserted row participates in mark_acked
        correlation so the LXMF delivery callback drives ACK synthesis
        back to the originating channel placeholder address."""
        msg_id = "mbcast-9999999999-ch7"
        queue.register_pending_ack(
            msg_id,
            origin_network="meshtastic",
            origin_address="channel:7",
            timeout_seconds=60,
            allow_orphan=True,
        )
        result = queue.mark_acked(msg_id)
        assert result is not None
        assert result['message_id'] == msg_id
        assert result['origin_network'] == "meshtastic"
        assert result['origin_address'] == "channel:7"

    def test_allow_orphan_synthetic_row_has_delivered_status(self, queue):
        """Synthetic bookkeeping rows must NOT enter dispatch loops —
        status='delivered' is the gate."""
        import sqlite3
        msg_id = "mbcast-stat-check"
        queue.register_pending_ack(
            msg_id,
            origin_network="meshtastic",
            origin_address="channel:0",
            allow_orphan=True,
        )
        with sqlite3.connect(str(queue._db_path)) as conn:
            row = conn.execute(
                "SELECT status, destination FROM messages WHERE id = ?",
                (msg_id,),
            ).fetchone()
        assert row is not None
        assert row[0] == "delivered"
        assert row[1] == "ack_bookkeeping"

    def test_allow_orphan_false_is_default_behavior(self, queue):
        """Default kwarg stays False — pre-existing callers see no change."""
        assert queue.register_pending_ack(
            "ghost-id-2",
            origin_network="meshcore",
            origin_address="abc",
        ) is False
        assert queue.get_ack_status("ghost-id-2") is None

    def test_mark_acked_returns_origin_for_correlation(self, queue):
        """
        mark_acked() returns the origin (network, address) so the caller
        can synthesize a CanonicalMessage(type=ACK, ack_of=<id>) back to
        the original sender.

        Why pinned: this return contract is what bridges _on_ack /
        LXMF delivery callbacks to the synthesis path.
        """
        msg_id = queue.enqueue({"text": "weather pls"}, "rns")
        queue.register_pending_ack(
            msg_id, origin_network="meshcore", origin_address="abc123",
        )
        result = queue.mark_acked(msg_id)
        assert result is not None
        assert result['message_id'] == msg_id
        assert result['origin_network'] == "meshcore"
        assert result['origin_address'] == "abc123"
        assert queue.get_ack_status(msg_id) == 'acked'

    def test_mark_acked_is_idempotent(self, queue):
        """
        A second mark_acked() on the same id returns None — caller must
        not double-emit a synthetic ACK CanonicalMessage.
        """
        msg_id = queue.enqueue({"text": "hi"}, "rns")
        queue.register_pending_ack(
            msg_id, origin_network="meshcore", origin_address="abc123",
        )
        first = queue.mark_acked(msg_id)
        second = queue.mark_acked(msg_id)
        assert first is not None
        assert second is None

    def test_mark_acked_on_untracked_message_returns_none(self, queue):
        msg_id = queue.enqueue({"text": "hi"}, "rns")
        # Never called register_pending_ack — message has ack_required=0
        assert queue.mark_acked(msg_id) is None

    def test_mark_acked_on_ghost_id_returns_none(self, queue):
        assert queue.mark_acked("ghost-id") is None

    def test_find_overdue_acks_returns_only_past_deadline(self, queue):
        """
        Records whose ack_timeout_at is in the future stay invisible;
        only past-due rows surface so the sweep emits one TIMEOUT per
        pending-and-expired record.
        """
        now = datetime.now()
        future_id = queue.enqueue({"text": "fresh"}, "rns")
        past_id = queue.enqueue({"text": "stale"}, "rns")
        queue.register_pending_ack(
            future_id, origin_network="meshcore",
            origin_address="abc", timeout_seconds=3600,  # 1h
        )
        queue.register_pending_ack(
            past_id, origin_network="meshtastic",
            origin_address="!aabb", timeout_seconds=1,  # 1s ago after sleep
        )
        # Look back from "now + 5s" — past_id is overdue, future_id is not.
        overdue = queue.find_overdue_acks(now=now + timedelta(seconds=5))
        ids = {r['message_id'] for r in overdue}
        assert past_id in ids
        assert future_id not in ids
        past = next(r for r in overdue if r['message_id'] == past_id)
        assert past['origin_network'] == "meshtastic"
        assert past['origin_address'] == "!aabb"

    def test_find_overdue_acks_excludes_already_acked(self, queue):
        """
        Acked records don't surface even if their timeout has passed —
        the sweep should never emit a TIMEOUT for a message that was
        delivery-confirmed.
        """
        now = datetime.now()
        msg_id = queue.enqueue({"text": "hi"}, "rns")
        queue.register_pending_ack(
            msg_id, origin_network="meshcore",
            origin_address="abc", timeout_seconds=1,
        )
        queue.mark_acked(msg_id)
        overdue = queue.find_overdue_acks(now=now + timedelta(seconds=5))
        assert msg_id not in {r['message_id'] for r in overdue}

    def test_find_overdue_acks_excludes_already_timed_out(self, queue):
        """
        A record that's already been finalised by mark_timeout() doesn't
        re-surface on subsequent sweeps — idempotent semantics keep the
        sweep from re-emitting TIMEOUT ACKs.
        """
        now = datetime.now()
        msg_id = queue.enqueue({"text": "hi"}, "rns")
        queue.register_pending_ack(
            msg_id, origin_network="meshcore",
            origin_address="abc", timeout_seconds=1,
        )
        queue.mark_timeout(msg_id)
        overdue = queue.find_overdue_acks(now=now + timedelta(seconds=5))
        assert msg_id not in {r['message_id'] for r in overdue}

    def test_mark_timeout_returns_false_after_ack(self, queue):
        """
        A race between an in-flight ACK and the timeout sweep: ACK wins,
        timeout call returns False so the caller skips synthesising a
        spurious TIMEOUT.
        """
        msg_id = queue.enqueue({"text": "hi"}, "rns")
        queue.register_pending_ack(
            msg_id, origin_network="meshcore", origin_address="abc",
        )
        queue.mark_acked(msg_id)
        assert queue.mark_timeout(msg_id) is False
        assert queue.get_ack_status(msg_id) == 'acked'  # not flipped

    def test_get_ack_status_three_states(self, queue):
        """get_ack_status returns 'pending', 'acked', 'timeout' across lifecycle."""
        msg_id = queue.enqueue({"text": "hi"}, "rns")
        assert queue.get_ack_status(msg_id) is None  # not yet tracking
        queue.register_pending_ack(
            msg_id, origin_network="meshcore", origin_address="abc",
        )
        assert queue.get_ack_status(msg_id) == 'pending'
        queue.mark_timeout(msg_id)
        assert queue.get_ack_status(msg_id) == 'timeout'

    def test_default_timeout_is_300_seconds(self, queue):
        """
        Documents the default — 5 minutes balances false-TIMEOUT risk
        (mesh latency, retries) against operator patience. Locked here
        so a future refactor can't quietly change it.
        """
        import inspect
        sig = inspect.signature(queue.register_pending_ack)
        assert sig.parameters['timeout_seconds'].default == 300

    def test_schema_migration_is_additive(self, tmp_path):
        """
        Issue #66 columns are ALTER'd in on first open, not dropped onto a
        rebuilt table. Pre-Issue-#66 rows survive untouched (so dedup
        windows + in-flight retries don't reset on upgrade).

        Why: the lab is hardening for handoff; an upgrade that wiped
        in-flight messages would be a regression. This test pins the
        additive contract on the migration.
        """
        # Build a "pre-Issue-#66" DB: the messages table without ack_* cols.
        import sqlite3
        db_file = str(tmp_path / "legacy.db")
        legacy = sqlite3.connect(db_file)
        legacy.execute("""
            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                destination TEXT NOT NULL,
                priority INTEGER DEFAULT 2,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 3,
                retry_after TEXT,
                error_message TEXT DEFAULT '',
                content_hash TEXT DEFAULT ''
            )
        """)
        legacy.execute("""
            INSERT INTO messages
            (id, payload, destination, status, created_at, updated_at,
             content_hash)
            VALUES ('legacy-1', '{}', 'rns', 'delivered',
                    '2026-05-01T00:00:00', '2026-05-01T00:00:00', 'legacyhash')
        """)
        legacy.commit()
        legacy.close()

        # Open via PersistentMessageQueue — the migration should add ack
        # columns without touching the legacy row.
        q = PersistentMessageQueue(db_path=db_file)
        try:
            with q._get_connection() as conn:
                cursor = conn.execute("PRAGMA table_info(messages)")
                cols = {row[1] for row in cursor.fetchall()}
                for col in ('ack_required', 'ack_of', 'ack_status',
                            'ack_timeout_at', 'ack_at',
                            'ack_origin_network', 'ack_origin_address'):
                    assert col in cols, f"migration missed {col}"
                # The legacy row survived and its ack tracking is at defaults.
                row = conn.execute(
                    "SELECT status, ack_required, ack_status "
                    "FROM messages WHERE id = 'legacy-1'"
                ).fetchone()
                assert row['status'] == 'delivered'
                assert row['ack_required'] == 0
                assert row['ack_status'] is None
        finally:
            q.stop_processing()


# ---------------------------------------------------------------------------
# PersistentMessageQueue — per-destination TX pacing (2026-06-04)
# ---------------------------------------------------------------------------
# meshtasticd 2.7.x NAKs burst API text broadcasts with Routing.Error
# RATE_LIMIT_EXCEEDED (=38) while the toradio HTTP hand-off returns 200 —
# a 3-chunk RNS→Mesh message dispatched ~45ms apart silently lost chunks
# 2-3 on RF. register_sender(min_spacing_s=...) paces the dispatch loop:
# not-yet-due messages stay 'pending' for a later process_once pass.

class TestTxPacing:
    """register_sender(min_spacing_s) paces consecutive dispatches."""

    def _rewind_last_dispatch(self, queue, destination, seconds):
        """Simulate `seconds` of wall time passing for the pacing clock."""
        queue._last_dispatch_ts[destination] -= seconds

    def test_paced_destination_sends_one_per_pass(self, queue):
        sent = []
        queue.register_sender("meshtastic", lambda p: sent.append(p["text"]) or True,
                              min_spacing_s=3.0)
        for i in range(3):
            queue.enqueue({"text": f"chunk{i}"}, "meshtastic")

        assert queue.process_once() == 1          # first is immediately due
        assert queue.process_once() == 0          # second blocked by spacing
        assert sent == ["chunk0"]

        self._rewind_last_dispatch(queue, "meshtastic", 3.1)
        assert queue.process_once() == 1
        self._rewind_last_dispatch(queue, "meshtastic", 3.1)
        assert queue.process_once() == 1
        # FIFO order preserved across passes — chunk order is content order.
        assert sent == ["chunk0", "chunk1", "chunk2"]

    def test_unpaced_destination_drains_batch_in_one_pass(self, queue):
        """Default (no spacing) keeps the historical drain-the-batch behavior."""
        sent = []
        queue.register_sender("meshtastic", lambda p: sent.append(p["text"]) or True)
        for i in range(3):
            queue.enqueue({"text": f"msg{i}"}, "meshtastic")

        assert queue.process_once() == 3
        assert sent == ["msg0", "msg1", "msg2"]

    def test_pacing_does_not_block_other_destinations(self, queue):
        mesh_sent, rns_sent = [], []
        queue.register_sender("meshtastic", lambda p: mesh_sent.append(1) or True,
                              min_spacing_s=60.0)
        queue.register_sender("rns", lambda p: rns_sent.append(1) or True)
        queue.enqueue({"text": "m1"}, "meshtastic")
        queue.enqueue({"text": "m2"}, "meshtastic")
        queue.enqueue({"text": "r1"}, "rns")
        queue.enqueue({"text": "r2"}, "rns")

        processed = queue.process_once()
        # meshtastic: only the first is due; rns: both drain unpaced.
        assert len(mesh_sent) == 1
        assert len(rns_sent) == 2
        assert processed == 3

    def test_failed_attempt_still_stamps_the_pacing_clock(self, queue):
        """The radio's limiter counts attempts — a failed send must pace too."""
        attempts = []
        queue.register_sender("meshtastic", lambda p: attempts.append(1) and False,
                              min_spacing_s=3.0)
        queue.enqueue({"text": "a"}, "meshtastic")
        queue.enqueue({"text": "b"}, "meshtastic")

        assert queue.process_once() == 1          # attempt 1 (fails)
        assert queue.process_once() == 0          # paced even after failure
        assert len(attempts) == 1

    def test_blocked_messages_remain_pending_not_lost(self, queue):
        queue.register_sender("meshtastic", lambda p: True, min_spacing_s=30.0)
        for i in range(3):
            queue.enqueue({"text": f"c{i}"}, "meshtastic")
        queue.process_once()                       # sends c0, paces the rest

        pending = queue.get_pending(destination="meshtastic")
        assert [m.payload["text"] for m in pending] == ["c1", "c2"]


# ---------------------------------------------------------------------------
# 2026-07-09 frontier review Pri-2 fixes
# ---------------------------------------------------------------------------

class TestFrontierReviewFixes20260709:
    """Pins for the message_queue findings of the 2026-07-09 frontier
    worklist Pri-2 pass (see review_provenance.md): shed messages leave a
    QUEUE_SHED witness, mark_acked has exactly one winner under
    concurrency, mark_delivered is idempotent."""

    def test_shed_records_queue_shed_witness(self, queue):
        """A shed message was recorded QUEUED at enqueue — without a
        terminal DROPPED/QUEUE_SHED its lifecycle reads QUEUED-then-nothing
        forever. QUEUE_SHED was in the closed DropReason taxonomy with NO
        recording site before this fix."""
        from unittest.mock import patch
        import gateway.message_queue as mq
        msg_id = queue.enqueue(
            {"text": "sheddable"}, "rns", priority=MessagePriority.LOW)
        assert msg_id is not None
        with patch.object(mq._dc, "record") as record:
            shed = queue._shed_overflow(count=1)
        assert shed == 1
        dropped = [
            c for c in record.call_args_list
            if c.args and c.args[0] == mq._dc.DeliveryState.DROPPED
        ]
        assert len(dropped) == 1
        kwargs = dropped[0].kwargs
        assert kwargs["msg_id"] == msg_id
        assert kwargs["protocol"] == "rns"
        assert kwargs["drop_reason"] == mq._dc.DropReason.QUEUE_SHED

    def test_mark_acked_concurrent_single_winner(self, queue):
        """The documented idempotency contract ('second call returns None')
        must hold for CONCURRENT calls too: an LXMF delivery callback racing
        the overdue-ack sweep used to both read 'pending' via
        SELECT-then-UPDATE and both emit a synthetic ACK."""
        import threading
        msg_id = queue.enqueue({"text": "ack me"}, "rns")
        assert queue.register_pending_ack(
            msg_id, origin_network="meshcore", origin_address="abc123",
        ) is True

        n = 8
        barrier = threading.Barrier(n)
        results = []
        lock = threading.Lock()

        def worker():
            barrier.wait()
            r = queue.mark_acked(msg_id)
            with lock:
                results.append(r)

        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        winners = [r for r in results if r is not None]
        assert len(results) == n
        assert len(winners) == 1, (
            f"expected exactly one mark_acked winner, got {len(winners)}")
        assert winners[0]["origin_network"] == "meshcore"
        assert queue.get_ack_status(msg_id) == "acked"

    def test_mark_delivered_idempotent(self, queue):
        """Second mark_delivered on the same row returns False and neither
        double-counts the delivered stat nor re-records SENT."""
        msg_id = queue.enqueue({"text": "once"}, "rns")
        assert queue.mark_delivered(msg_id) is True
        assert queue.mark_delivered(msg_id) is False
        assert queue._stats["delivered"] == 1
