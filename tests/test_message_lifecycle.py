"""
Tests for Message Lifecycle Tracking (Sprint C: Message Visibility)

Tests the message lifecycle state machine and tracing functionality.
"""

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gateway.message_queue import (
    PersistentMessageQueue,
    MessageLifecycleState,
    MessageLifecycleEvent,
    MessagePriority,
    MessageStatus,
)


class TestMessageLifecycleState:
    """Tests for MessageLifecycleState enum."""

    def test_all_states_defined(self):
        """Test that all expected lifecycle states are defined."""
        expected_states = [
            'CREATED', 'QUEUED', 'SENT', 'RELAYED',
            'DELIVERED', 'ACK', 'TIMEOUT', 'FAILED', 'RETRYING'
        ]
        for state in expected_states:
            assert hasattr(MessageLifecycleState, state)

    def test_state_values(self):
        """Test that states have correct string values."""
        assert MessageLifecycleState.CREATED.value == 'created'
        assert MessageLifecycleState.DELIVERED.value == 'delivered'
        assert MessageLifecycleState.FAILED.value == 'failed'


class TestMessageLifecycleEvent:
    """Tests for MessageLifecycleEvent dataclass."""

    def test_event_creation(self):
        """Test creating a lifecycle event."""
        event = MessageLifecycleEvent(
            message_id='test-123',
            state=MessageLifecycleState.CREATED,
            timestamp=datetime.now(),
            details='Test event',
            node_id='!abc123',
            hop_count=0,
        )

        assert event.message_id == 'test-123'
        assert event.state == MessageLifecycleState.CREATED
        assert event.details == 'Test event'
        assert event.hop_count == 0

    def test_event_to_dict(self):
        """Test converting event to dictionary."""
        now = datetime.now()
        event = MessageLifecycleEvent(
            message_id='test-123',
            state=MessageLifecycleState.SENT,
            timestamp=now,
            details='Sent to meshtastic',
            node_id='!abc123',
            hop_count=1,
        )

        data = event.to_dict()
        assert data['message_id'] == 'test-123'
        assert data['state'] == 'sent'
        assert data['timestamp'] == now.isoformat()
        assert data['hop_count'] == 1

    def test_event_from_dict(self):
        """Test creating event from dictionary."""
        data = {
            'message_id': 'test-456',
            'state': 'delivered',
            'timestamp': '2026-01-17T10:00:00',
            'details': 'Message delivered',
            'node_id': '!def456',
            'hop_count': 2,
        }

        event = MessageLifecycleEvent.from_dict(data)
        assert event.message_id == 'test-456'
        assert event.state == MessageLifecycleState.DELIVERED
        assert event.hop_count == 2


class TestLifecycleTracking:
    """Tests for message lifecycle tracking in PersistentMessageQueue."""

    @pytest.fixture
    def queue(self):
        """Create a queue with temporary database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test_queue.db")
            q = PersistentMessageQueue(db_path=db_path)
            yield q

    def test_record_lifecycle_event(self, queue):
        """Test recording a lifecycle event."""
        result = queue.record_lifecycle_event(
            message_id='test-123',
            state=MessageLifecycleState.CREATED,
            details='Message created',
        )
        assert result is True

    def test_get_message_trace_empty(self, queue):
        """Test getting trace for non-existent message."""
        trace = queue.get_message_trace('nonexistent')
        assert trace == []

    def test_get_message_trace_with_events(self, queue):
        """Test getting trace for message with multiple events."""
        msg_id = 'test-trace-123'

        # Record multiple lifecycle events
        queue.record_lifecycle_event(msg_id, MessageLifecycleState.CREATED)
        queue.record_lifecycle_event(msg_id, MessageLifecycleState.QUEUED)
        queue.record_lifecycle_event(msg_id, MessageLifecycleState.SENT)
        queue.record_lifecycle_event(msg_id, MessageLifecycleState.DELIVERED)

        trace = queue.get_message_trace(msg_id)

        assert len(trace) == 4
        assert trace[0].state == MessageLifecycleState.CREATED
        assert trace[1].state == MessageLifecycleState.QUEUED
        assert trace[2].state == MessageLifecycleState.SENT
        assert trace[3].state == MessageLifecycleState.DELIVERED

    def test_trace_chronological_order(self, queue):
        """Test that trace events are in chronological order."""
        msg_id = 'test-order-123'

        # Record events (they should be ordered by timestamp)
        queue.record_lifecycle_event(msg_id, MessageLifecycleState.CREATED)
        queue.record_lifecycle_event(msg_id, MessageLifecycleState.SENT)
        queue.record_lifecycle_event(msg_id, MessageLifecycleState.DELIVERED)

        trace = queue.get_message_trace(msg_id)

        # Verify chronological order
        for i in range(len(trace) - 1):
            assert trace[i].timestamp <= trace[i + 1].timestamp

    def test_get_recent_events(self, queue):
        """Test getting recent events across messages."""
        # Record events for multiple messages
        queue.record_lifecycle_event('msg1', MessageLifecycleState.CREATED)
        queue.record_lifecycle_event('msg2', MessageLifecycleState.CREATED)
        queue.record_lifecycle_event('msg1', MessageLifecycleState.SENT)

        events = queue.get_recent_events(limit=10)

        assert len(events) == 3
        # Most recent first
        assert events[0].message_id == 'msg1'
        assert events[0].state == MessageLifecycleState.SENT

    def test_get_recent_events_with_filter(self, queue):
        """Test filtering recent events by state."""
        queue.record_lifecycle_event('msg1', MessageLifecycleState.CREATED)
        queue.record_lifecycle_event('msg2', MessageLifecycleState.CREATED)
        queue.record_lifecycle_event('msg1', MessageLifecycleState.FAILED, details='Network error')

        # Filter for failed only
        events = queue.get_recent_events(
            limit=10,
            state_filter=MessageLifecycleState.FAILED
        )

        assert len(events) == 1
        assert events[0].state == MessageLifecycleState.FAILED

    def test_hop_count_tracking(self, queue):
        """Test that hop count is tracked correctly."""
        msg_id = 'test-hops-123'

        queue.record_lifecycle_event(msg_id, MessageLifecycleState.CREATED, hop_count=0)
        queue.record_lifecycle_event(msg_id, MessageLifecycleState.SENT, hop_count=0)
        queue.record_lifecycle_event(msg_id, MessageLifecycleState.RELAYED, hop_count=1, node_id='!relay1')
        queue.record_lifecycle_event(msg_id, MessageLifecycleState.RELAYED, hop_count=2, node_id='!relay2')
        queue.record_lifecycle_event(msg_id, MessageLifecycleState.DELIVERED, hop_count=2)

        trace = queue.get_message_trace(msg_id)
        hop_counts = [e.hop_count for e in trace]

        assert hop_counts == [0, 0, 1, 2, 2]


class TestMessageSummary:
    """Tests for message summary functionality."""

    @pytest.fixture
    def queue_with_message(self):
        """Create queue with a test message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test_queue.db")
            q = PersistentMessageQueue(db_path=db_path)

            # Enqueue a message
            msg_id = q.enqueue(
                payload={'text': 'Test message', 'from': 'user1', 'to': 'user2'},
                destination='meshtastic',
            )

            yield q, msg_id

    def test_get_message_summary_not_found(self, queue_with_message):
        """Test summary for non-existent message."""
        queue, _ = queue_with_message
        summary = queue.get_message_summary('nonexistent')
        assert summary is None

    def test_get_message_summary_basic(self, queue_with_message):
        """Test basic message summary."""
        queue, msg_id = queue_with_message

        # Add some lifecycle events
        queue.record_lifecycle_event(msg_id, MessageLifecycleState.CREATED)
        queue.record_lifecycle_event(msg_id, MessageLifecycleState.QUEUED)

        summary = queue.get_message_summary(msg_id)

        assert summary is not None
        assert summary['message_id'] == msg_id
        assert summary['destination'] == 'meshtastic'
        assert 'lifecycle' in summary
        assert len(summary['lifecycle']['states_reached']) == 2

    def test_get_message_summary_with_failure(self, queue_with_message):
        """Test summary includes failure reason."""
        queue, msg_id = queue_with_message

        queue.record_lifecycle_event(msg_id, MessageLifecycleState.CREATED)
        queue.record_lifecycle_event(msg_id, MessageLifecycleState.SENT)
        queue.record_lifecycle_event(
            msg_id,
            MessageLifecycleState.FAILED,
            details='Connection refused'
        )

        summary = queue.get_message_summary(msg_id)

        assert summary['failure_reason'] == 'Connection refused'


class TestFailedMessages:
    """Tests for failed message analysis."""

    @pytest.fixture
    def queue_with_failures(self):
        """Create queue with some failed messages that have exhausted retries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test_queue.db")
            q = PersistentMessageQueue(db_path=db_path)

            # Create a message with max_retries=1 so it fails faster
            msg_id = q.enqueue(
                payload={'text': 'Failed message'},
                destination='rns',
                max_retries=1,  # Only 1 retry before dead letter
            )

            # max_retries=1: the FIRST failure dead-letters the row. A late
            # second mark_failed on the finished row is refused (returns
            # False) and must not rewrite it (2026-10-18: it used to
            # overwrite the dead row's error — the double-terminal class).
            q.mark_in_progress(msg_id)
            assert q.mark_failed(msg_id, 'Test error 1') is True

            q.mark_in_progress(msg_id)
            assert q.mark_failed(msg_id, 'Test error 2') is False

            q.record_lifecycle_event(
                msg_id,
                MessageLifecycleState.FAILED,
                details='Network timeout'
            )

            yield q

    def test_get_failed_messages_with_reason(self, queue_with_failures):
        """Test getting failed messages with reasons."""
        results = queue_with_failures.get_failed_messages_with_reason(hours=1)

        # Should have at least one dead letter message
        assert len(results) >= 1
        assert results[0]['error_message'] == 'Test error 1'


class TestLifecyclePurge:
    """Tests for lifecycle history cleanup."""

    @pytest.fixture
    def queue(self):
        """Create a queue with temporary database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test_queue.db")
            q = PersistentMessageQueue(db_path=db_path)
            yield q

    def test_purge_lifecycle_history(self, queue):
        """Test purging old lifecycle entries."""
        # Record some events
        queue.record_lifecycle_event('msg1', MessageLifecycleState.CREATED)
        queue.record_lifecycle_event('msg1', MessageLifecycleState.DELIVERED)

        # Verify events exist
        events_before = queue.get_recent_events(limit=100)
        assert len(events_before) == 2

        # Purge with 30 days should NOT purge recent events
        count = queue.purge_lifecycle_history(days=30)
        assert count == 0  # Nothing should be purged

        # Verify events still exist
        events_after = queue.get_recent_events(limit=100)
        assert len(events_after) == 2


class TestLifecycleIntegration:
    """Integration tests for lifecycle tracking with queue operations."""

    @pytest.fixture
    def queue(self):
        """Create a queue with temporary database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test_queue.db")
            q = PersistentMessageQueue(db_path=db_path)
            yield q

    def test_full_message_lifecycle(self, queue):
        """Test complete message lifecycle tracking."""
        # Enqueue message
        msg_id = queue.enqueue(
            payload={'text': 'Hello', 'from': 'A', 'to': 'B'},
            destination='meshtastic',
        )
        assert msg_id is not None

        # Record lifecycle events simulating message journey
        queue.record_lifecycle_event(msg_id, MessageLifecycleState.CREATED)
        queue.record_lifecycle_event(msg_id, MessageLifecycleState.QUEUED)

        # Simulate processing
        queue.mark_in_progress(msg_id)
        queue.record_lifecycle_event(msg_id, MessageLifecycleState.SENT)

        # Simulate relay
        queue.record_lifecycle_event(
            msg_id,
            MessageLifecycleState.RELAYED,
            node_id='!relay1',
            hop_count=1
        )

        # Simulate delivery
        queue.mark_delivered(msg_id)
        queue.record_lifecycle_event(msg_id, MessageLifecycleState.DELIVERED)
        queue.record_lifecycle_event(msg_id, MessageLifecycleState.ACK)

        # Verify complete trace
        trace = queue.get_message_trace(msg_id)
        states = [e.state for e in trace]

        assert MessageLifecycleState.CREATED in states
        assert MessageLifecycleState.QUEUED in states
        assert MessageLifecycleState.SENT in states
        assert MessageLifecycleState.RELAYED in states
        assert MessageLifecycleState.DELIVERED in states
        assert MessageLifecycleState.ACK in states

        # Verify summary
        summary = queue.get_message_summary(msg_id)
        assert summary['current_status'] == 'delivered'
        assert summary['lifecycle']['event_count'] == 6
        assert summary['lifecycle']['max_hops'] == 1
