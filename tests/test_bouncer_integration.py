"""
Tests for bouncer queue integration in MessageRouter.

Tests cover:
- drain_bounced() retrieval and queue clearing
- clear_expired_bounced() TTL enforcement
- Edge cases (empty queue, no classifier)
"""

import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from gateway.message_routing import MessageRouter
from utils.classifier import (
    Bouncer,
    BouncerConfig,
    ClassificationResult,
)


@pytest.fixture
def mock_router_config():
    """Create a minimal gateway config for MessageRouter."""
    rule = SimpleNamespace(
        name='test_rule',
        direction='bidirectional',
        enabled=True,
        priority=1,
        source_filter='',
        dest_filter='',
        message_filter='',
    )
    config = SimpleNamespace(
        routing_rules=[rule],
        meshcore=SimpleNamespace(enabled=True),
    )
    return config


@pytest.fixture
def bouncer():
    """Create a bouncer with items in queue."""
    b = Bouncer(BouncerConfig(threshold=0.3, max_queue_size=100))
    return b


@pytest.fixture
def router_with_bouncer(mock_router_config, bouncer):
    """Create a MessageRouter with a classifier that has a bouncer."""
    classifier = MagicMock()
    classifier.bouncer = bouncer
    classifier.get_stats.return_value = {}

    # Bypass classifier init — we inject our own
    with patch('gateway.message_routing.CLASSIFIER_AVAILABLE', False):
        router = MessageRouter(
            config=mock_router_config,
            stats={},
            stats_lock=MagicMock(),
        )
    router._classifier = classifier
    return router


class TestDrainBounced:
    """Test drain_bounced() method."""

    def test_empty_queue_returns_empty(self, router_with_bouncer):
        """Empty bouncer queue returns empty list."""
        result = router_with_bouncer.drain_bounced()
        assert result == []

    def test_drain_returns_items(self, router_with_bouncer, bouncer):
        """drain_bounced returns queued items as dicts."""
        # Add a bounced item manually
        item = ClassificationResult(
            input_id="msg_001",
            category="unknown",
            confidence=0.1,
        )
        item.bounced = True
        item.bounce_reason = "Low confidence"
        bouncer._queue.append(item)

        result = router_with_bouncer.drain_bounced()
        assert len(result) == 1
        assert result[0]['input_id'] == "msg_001"
        assert result[0]['bounced'] is True
        assert result[0]['confidence'] == 0.1

    def test_drain_clears_queue(self, router_with_bouncer, bouncer):
        """After draining all items, queue is empty."""
        item = ClassificationResult(
            input_id="msg_002",
            category="unknown",
            confidence=0.2,
        )
        bouncer._queue.append(item)

        router_with_bouncer.drain_bounced()
        assert len(bouncer.get_queue()) == 0

    def test_drain_respects_max_items(self, router_with_bouncer, bouncer):
        """drain_bounced only takes up to max_items."""
        for i in range(5):
            item = ClassificationResult(
                input_id=f"msg_{i}",
                category="unknown",
                confidence=0.1,
            )
            bouncer._queue.append(item)

        result = router_with_bouncer.drain_bounced(max_items=2)
        assert len(result) == 2
        assert len(bouncer.get_queue()) == 3

    def test_drain_no_classifier(self, mock_router_config):
        """drain_bounced returns empty when no classifier."""
        with patch('gateway.message_routing.CLASSIFIER_AVAILABLE', False):
            router = MessageRouter(
                config=mock_router_config,
                stats={},
                stats_lock=MagicMock(),
            )
        router._classifier = None
        assert router.drain_bounced() == []


class TestClearExpiredBounced:
    """Test clear_expired_bounced() method."""

    def test_empty_queue_returns_zero(self, router_with_bouncer):
        """No items to clear returns 0."""
        assert router_with_bouncer.clear_expired_bounced() == 0

    def test_no_classifier_returns_zero(self, mock_router_config):
        """No classifier returns 0."""
        with patch('gateway.message_routing.CLASSIFIER_AVAILABLE', False):
            router = MessageRouter(
                config=mock_router_config,
                stats={},
                stats_lock=MagicMock(),
            )
        router._classifier = None
        assert router.clear_expired_bounced() == 0

    def test_items_without_timestamp_kept(self, router_with_bouncer, bouncer):
        """Items with no timestamp are kept (age unknown)."""
        item = ClassificationResult(
            input_id="msg_no_ts",
            category="unknown",
            confidence=0.1,
        )
        bouncer._queue.append(item)

        cleared = router_with_bouncer.clear_expired_bounced(max_age_seconds=1.0)
        assert cleared == 0
        assert len(bouncer.get_queue()) == 1
