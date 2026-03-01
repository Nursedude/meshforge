"""
Tests for MessageFlowTracker — in-memory message lifecycle tracking.

Covers: record/retrieve, state transitions, bounded deque, thread safety,
summary aggregation, latest state lookup, gateway_cli integration.
"""

import threading
import time
from unittest.mock import patch, MagicMock

import pytest

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from gateway.message_lifecycle import MessageFlowTracker, FlowRecord, MAX_FLOW_RECORDS
from gateway.message_queue import MessageLifecycleState


# ===========================================================================
# FlowRecord
# ===========================================================================

class TestFlowRecord:
    """Test FlowRecord dataclass."""

    def test_to_dict_all_fields(self):
        record = FlowRecord(
            message_id="msg-123",
            state=MessageLifecycleState.SENT,
            timestamp=1000.0,
            source_network="meshtastic",
            destination_network="rns",
            content_preview="Hello",
            details="first hop",
        )
        d = record.to_dict()
        assert d["message_id"] == "msg-123"
        assert d["state"] == "sent"
        assert d["timestamp"] == 1000.0
        assert d["source_network"] == "meshtastic"
        assert d["destination_network"] == "rns"
        assert d["content_preview"] == "Hello"
        assert d["details"] == "first hop"

    def test_to_dict_defaults(self):
        record = FlowRecord(
            message_id="msg-456",
            state=MessageLifecycleState.DELIVERED,
            timestamp=2000.0,
        )
        d = record.to_dict()
        assert d["source_network"] == ""
        assert d["destination_network"] == ""
        assert d["content_preview"] == ""
        assert d["details"] == ""


# ===========================================================================
# MessageFlowTracker — Core Operations
# ===========================================================================

class TestMessageFlowTracker:
    """Test MessageFlowTracker record and retrieval."""

    def test_record_and_get_recent(self):
        tracker = MessageFlowTracker()
        tracker.record_event("m1", MessageLifecycleState.SENT,
                             source_network="meshtastic", destination_network="rns",
                             content_preview="hello")
        recent = tracker.get_recent(limit=10)
        assert len(recent) == 1
        assert recent[0]["message_id"] == "m1"
        assert recent[0]["state"] == "sent"

    def test_get_recent_newest_first(self):
        tracker = MessageFlowTracker()
        tracker.record_event("m1", MessageLifecycleState.SENT)
        tracker.record_event("m2", MessageLifecycleState.DELIVERED)
        recent = tracker.get_recent(limit=10)
        assert len(recent) == 2
        assert recent[0]["message_id"] == "m2"
        assert recent[1]["message_id"] == "m1"

    def test_get_recent_limit(self):
        tracker = MessageFlowTracker()
        for i in range(10):
            tracker.record_event(f"m{i}", MessageLifecycleState.SENT)
        recent = tracker.get_recent(limit=3)
        assert len(recent) == 3
        assert recent[0]["message_id"] == "m9"

    def test_content_preview_truncated(self):
        tracker = MessageFlowTracker()
        long_content = "x" * 100
        tracker.record_event("m1", MessageLifecycleState.SENT,
                             content_preview=long_content)
        recent = tracker.get_recent(limit=1)
        assert len(recent[0]["content_preview"]) == 50

    def test_empty_tracker(self):
        tracker = MessageFlowTracker()
        assert tracker.get_recent() == []
        summary = tracker.get_summary()
        assert summary["total_tracked"] == 0

    def test_clear(self):
        tracker = MessageFlowTracker()
        tracker.record_event("m1", MessageLifecycleState.SENT)
        tracker.record_event("m2", MessageLifecycleState.DELIVERED)
        assert tracker.get_summary()["total_tracked"] == 2
        tracker.clear()
        assert tracker.get_summary()["total_tracked"] == 0
        assert tracker.get_recent() == []

    def test_record_with_details(self):
        tracker = MessageFlowTracker()
        tracker.record_event("m1", MessageLifecycleState.FAILED,
                             details="Connection refused")
        recent = tracker.get_recent(limit=1)
        assert recent[0]["details"] == "Connection refused"

    def test_timestamp_is_set(self):
        tracker = MessageFlowTracker()
        before = time.time()
        tracker.record_event("m1", MessageLifecycleState.SENT)
        after = time.time()
        recent = tracker.get_recent(limit=1)
        assert before <= recent[0]["timestamp"] <= after


# ===========================================================================
# MessageFlowTracker — Summary
# ===========================================================================

class TestFlowTrackerSummary:
    """Test summary/aggregation methods."""

    def test_summary_counts_by_state(self):
        tracker = MessageFlowTracker()
        tracker.record_event("m1", MessageLifecycleState.SENT)
        tracker.record_event("m2", MessageLifecycleState.SENT)
        tracker.record_event("m3", MessageLifecycleState.DELIVERED)
        tracker.record_event("m4", MessageLifecycleState.FAILED)

        summary = tracker.get_summary()
        assert summary["sent"] == 2
        assert summary["delivered"] == 1
        assert summary["failed"] == 1
        assert summary["timeout"] == 0
        assert summary["total_tracked"] == 4
        assert summary["by_state"]["sent"] == 2

    def test_summary_empty(self):
        tracker = MessageFlowTracker()
        summary = tracker.get_summary()
        assert summary["total_tracked"] == 0
        assert summary["sent"] == 0
        assert summary["delivered"] == 0


# ===========================================================================
# MessageFlowTracker — Bounded Deque
# ===========================================================================

class TestFlowTrackerBounded:
    """Test bounded deque behavior."""

    def test_bounded_deque_evicts_oldest(self):
        tracker = MessageFlowTracker(max_records=5)
        for i in range(10):
            tracker.record_event(f"m{i}", MessageLifecycleState.SENT)
        recent = tracker.get_recent(limit=10)
        assert len(recent) == 5
        # Newest should be m9, oldest retained should be m5
        assert recent[0]["message_id"] == "m9"
        assert recent[4]["message_id"] == "m5"

    def test_default_max_records(self):
        assert MAX_FLOW_RECORDS == 500


# ===========================================================================
# MessageFlowTracker — Latest State
# ===========================================================================

class TestFlowTrackerLatestState:
    """Test get_latest_state()."""

    def test_latest_state_found(self):
        tracker = MessageFlowTracker()
        tracker.record_event("m1", MessageLifecycleState.SENT)
        tracker.record_event("m1", MessageLifecycleState.DELIVERED)
        assert tracker.get_latest_state("m1") == "delivered"

    def test_latest_state_not_found(self):
        tracker = MessageFlowTracker()
        assert tracker.get_latest_state("nonexistent") is None

    def test_latest_state_multiple_messages(self):
        tracker = MessageFlowTracker()
        tracker.record_event("m1", MessageLifecycleState.SENT)
        tracker.record_event("m2", MessageLifecycleState.FAILED)
        tracker.record_event("m1", MessageLifecycleState.DELIVERED)
        assert tracker.get_latest_state("m1") == "delivered"
        assert tracker.get_latest_state("m2") == "failed"


# ===========================================================================
# Thread Safety
# ===========================================================================

class TestFlowTrackerThreadSafety:
    """Test thread-safe concurrent access."""

    def test_concurrent_writes(self):
        tracker = MessageFlowTracker()
        errors = []

        def writer(start_id, count):
            try:
                for i in range(count):
                    tracker.record_event(
                        f"t{start_id}-{i}",
                        MessageLifecycleState.SENT,
                        source_network="meshtastic",
                    )
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=(t, 50))
            for t in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        summary = tracker.get_summary()
        assert summary["total_tracked"] == 200


# ===========================================================================
# Gateway CLI Integration
# ===========================================================================

class TestGetMessageFlow:
    """Test get_message_flow() gateway CLI function."""

    def test_no_bridge_running(self):
        with patch('gateway.gateway_cli._active_bridge', None):
            from gateway.gateway_cli import get_message_flow
            result = get_message_flow()
            assert result['status'] == 'Bridge not running'
            assert result['recent'] == []

    def test_bridge_running(self):
        mock_bridge = MagicMock()
        mock_bridge._running = True
        mock_tracker = MessageFlowTracker()
        mock_tracker.record_event("m1", MessageLifecycleState.DELIVERED,
                                  source_network="rns", destination_network="meshtastic")
        mock_bridge.flow_tracker = mock_tracker

        with patch('gateway.gateway_cli._active_bridge', mock_bridge):
            from gateway.gateway_cli import get_message_flow
            result = get_message_flow(limit=10)
            assert result['status'] == 'OK'
            assert len(result['recent']) == 1
            assert result['recent'][0]['state'] == 'delivered'
            assert result['summary']['delivered'] == 1

    def test_bridge_stopped(self):
        mock_bridge = MagicMock()
        mock_bridge._running = False

        with patch('gateway.gateway_cli._active_bridge', mock_bridge):
            from gateway.gateway_cli import get_message_flow
            result = get_message_flow()
            assert result['status'] == 'Bridge not running'
