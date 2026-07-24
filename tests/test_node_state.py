"""
Tests for Node State Machine

Tests granular node state tracking and transitions.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gateway.node_state import (
    NodeState,
    NodeStateMachine,
    NodeStateConfig,
    StateTransition,
    get_default_state_config,
)


class TestNodeState:
    """Tests for NodeState enum."""

    def test_state_is_active(self):
        """Test is_active() for different states."""
        active_states = [
            NodeState.ONLINE,
            NodeState.WEAK_SIGNAL,
            NodeState.INTERMITTENT,
            NodeState.DISCOVERED,
        ]
        inactive_states = [
            NodeState.OFFLINE,
            NodeState.SUSPECTED_OFFLINE,
            NodeState.UNREACHABLE,
            NodeState.STALE_CACHE,
        ]

        for state in active_states:
            assert state.is_active(), f"{state} should be active"

        for state in inactive_states:
            assert not state.is_active(), f"{state} should not be active"

    def test_state_is_healthy(self):
        """Test is_healthy() for different states."""
        healthy_states = [NodeState.ONLINE, NodeState.DISCOVERED]
        unhealthy_states = [
            NodeState.WEAK_SIGNAL,
            NodeState.INTERMITTENT,
            NodeState.OFFLINE,
            NodeState.SUSPECTED_OFFLINE,
        ]

        for state in healthy_states:
            assert state.is_healthy(), f"{state} should be healthy"

        for state in unhealthy_states:
            assert not state.is_healthy(), f"{state} should not be healthy"

    def test_state_display_name(self):
        """Test human-readable display names."""
        assert NodeState.ONLINE.display_name == "Online"
        assert NodeState.WEAK_SIGNAL.display_name == "Weak Signal"
        assert NodeState.SUSPECTED_OFFLINE.display_name == "Checking..."

    def test_state_icon(self):
        """Test state icons for UI."""
        assert NodeState.ONLINE.icon == "+"
        assert NodeState.OFFLINE.icon == "-"
        assert NodeState.WEAK_SIGNAL.icon == "!"


class TestNodeStateMachine:
    """Tests for NodeStateMachine."""

    def test_initial_state(self):
        """Test initial state configuration."""
        sm = NodeStateMachine()
        assert sm.state == NodeState.DISCOVERED

        sm_cached = NodeStateMachine(initial_state=NodeState.STALE_CACHE)
        assert sm_cached.state == NodeState.STALE_CACHE

    def test_transition_to_new_state(self):
        """Test state transitions."""
        sm = NodeStateMachine()

        # Should return True on actual transition
        result = sm.transition_to(NodeState.ONLINE, "test transition")
        assert result is True
        assert sm.state == NodeState.ONLINE

        # Should return False if already in state
        result = sm.transition_to(NodeState.ONLINE, "same state")
        assert result is False

    def test_transition_records_history(self):
        """Test that transitions are recorded."""
        sm = NodeStateMachine()
        sm.transition_to(NodeState.ONLINE, "initial")
        sm.transition_to(NodeState.WEAK_SIGNAL, "signal degraded")

        history = sm.get_transitions(10)
        assert len(history) == 2
        assert history[0].from_state == NodeState.DISCOVERED
        assert history[0].to_state == NodeState.ONLINE
        assert history[1].from_state == NodeState.ONLINE
        assert history[1].to_state == NodeState.WEAK_SIGNAL

    def test_record_response_updates_state(self):
        """Test that recording responses updates state."""
        sm = NodeStateMachine(initial_state=NodeState.STALE_CACHE)

        # First response should transition from STALE_CACHE
        sm.record_response(snr=10.0, rssi=-60)
        assert sm.state == NodeState.ONLINE

    def test_record_response_weak_signal(self):
        """Test weak signal detection."""
        config = NodeStateConfig(weak_signal_snr=-5.0, weak_signal_rssi=-110)
        sm = NodeStateMachine(config=config, initial_state=NodeState.ONLINE)

        # Strong signal should stay online
        sm.record_response(snr=5.0, rssi=-70)
        assert sm.state == NodeState.ONLINE

        # Weak signal should transition
        sm.record_response(snr=-10.0, rssi=-120)
        assert sm.state == NodeState.WEAK_SIGNAL

    def test_record_response_recovery(self):
        """Test recovery from offline state."""
        sm = NodeStateMachine(initial_state=NodeState.OFFLINE)

        # Response should bring it back online
        sm.record_response(snr=10.0)
        assert sm.state == NodeState.ONLINE

    def test_check_timeout_to_suspected(self):
        """Test timeout transitions to suspected offline."""
        config = NodeStateConfig(suspect_threshold=10.0)  # 10 seconds
        sm = NodeStateMachine(config=config)
        sm.transition_to(NodeState.ONLINE, "initial")

        # Fake an old last_seen time
        old_time = datetime.now() - timedelta(seconds=15)
        sm._last_response = old_time

        sm.check_timeout()
        assert sm.state == NodeState.SUSPECTED_OFFLINE

    def test_check_timeout_to_offline(self):
        """Test timeout transitions to confirmed offline."""
        config = NodeStateConfig(suspect_threshold=10.0, offline_threshold=30.0)
        sm = NodeStateMachine(config=config)
        sm.transition_to(NodeState.SUSPECTED_OFFLINE, "initial")

        # Fake a very old last_seen time
        old_time = datetime.now() - timedelta(seconds=60)
        sm._last_response = old_time

        sm.check_timeout()
        assert sm.state == NodeState.OFFLINE

    def test_mark_unreachable(self):
        """Test marking node as unreachable."""
        sm = NodeStateMachine()
        sm.mark_unreachable("No route found")
        assert sm.state == NodeState.UNREACHABLE

    def test_is_online_property(self):
        """Test legacy is_online property."""
        sm = NodeStateMachine(initial_state=NodeState.ONLINE)
        assert sm.is_online is True

        sm.transition_to(NodeState.OFFLINE, "timeout")
        assert sm.is_online is False

    def test_time_in_state(self):
        """Test time_in_state calculation."""
        sm = NodeStateMachine()
        # Should be non-negative
        assert sm.time_in_state.total_seconds() >= 0

    def test_serialization(self):
        """Test to_dict and from_dict."""
        sm = NodeStateMachine()
        sm.transition_to(NodeState.ONLINE, "test")
        sm.record_response(snr=5.0)

        data = sm.to_dict()
        assert data["state"] == "ONLINE"
        assert "state_since" in data

        # Restore from dict
        sm2 = NodeStateMachine.from_dict(data)
        assert sm2.state == NodeState.ONLINE

    def test_history_limit(self):
        """Test that transition history is limited."""
        config = NodeStateConfig(max_transitions=5)
        sm = NodeStateMachine(config=config)

        # Create many transitions
        states = [NodeState.ONLINE, NodeState.WEAK_SIGNAL] * 10
        for i, state in enumerate(states):
            sm.transition_to(state, f"transition {i}")

        history = sm.get_transitions(100)
        assert len(history) <= 5


class TestMarkCacheRestored:
    """Pri-3 (07-23 review): after restoring a machine from persisted cache,
    an ACTIVE live-state must reset to STALE_CACHE (liveness unverified across
    a restart) so it can't contradict the loader's forced is_online=False;
    non-active states are preserved as the more-informative label."""

    def test_active_state_resets_to_stale_cache(self):
        sm = NodeStateMachine(initial_state=NodeState.ONLINE)
        sm.record_response(snr=10.0)  # stamp a real last_response fact
        assert sm.state == NodeState.ONLINE
        result = sm.mark_cache_restored()
        assert result == NodeState.STALE_CACHE
        assert sm.state == NodeState.STALE_CACHE
        assert sm.state.is_active() is False

    def test_weak_signal_and_discovered_also_reset(self):
        for active in (NodeState.WEAK_SIGNAL, NodeState.INTERMITTENT,
                       NodeState.DISCOVERED):
            sm = NodeStateMachine(initial_state=active)
            sm.mark_cache_restored()
            assert sm.state == NodeState.STALE_CACHE

    def test_offline_is_preserved(self):
        sm = NodeStateMachine(initial_state=NodeState.OFFLINE)
        assert sm.mark_cache_restored() == NodeState.OFFLINE
        assert sm.state == NodeState.OFFLINE

    def test_suspected_offline_is_preserved(self):
        sm = NodeStateMachine(initial_state=NodeState.SUSPECTED_OFFLINE)
        sm.mark_cache_restored()
        assert sm.state == NodeState.SUSPECTED_OFFLINE

    def test_last_response_and_history_survive_reset(self):
        sm = NodeStateMachine(initial_state=NodeState.STALE_CACHE)
        sm.record_response(snr=8.0)  # STALE_CACHE -> ONLINE, stamps last_response
        assert sm.state == NodeState.ONLINE
        last = sm._last_response
        sm.mark_cache_restored()
        assert sm.state == NodeState.STALE_CACHE
        assert sm._last_response == last            # fact preserved
        assert len(sm.get_transitions()) >= 2       # ONLINE + restore transition

    def test_idempotent_on_stale_cache(self):
        sm = NodeStateMachine(initial_state=NodeState.STALE_CACHE)
        before = len(sm.get_transitions())
        sm.mark_cache_restored()
        assert sm.state == NodeState.STALE_CACHE
        assert len(sm.get_transitions()) == before  # no spurious transition


class TestNodeStateConfig:
    """Tests for NodeStateConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = get_default_state_config()
        assert config.suspect_threshold == 300.0  # 5 min
        assert config.offline_threshold == 3600.0  # 1 hour
        assert config.weak_signal_snr == -5.0

    def test_custom_config(self):
        """Test custom configuration."""
        config = NodeStateConfig(
            suspect_threshold=60.0,
            offline_threshold=300.0,
            weak_signal_snr=-10.0,
        )
        assert config.suspect_threshold == 60.0
        assert config.weak_signal_snr == -10.0


class TestStateTransition:
    """Tests for StateTransition dataclass."""

    def test_to_dict(self):
        """Test serialization."""
        transition = StateTransition(
            from_state=NodeState.ONLINE,
            to_state=NodeState.WEAK_SIGNAL,
            timestamp=datetime(2024, 1, 1, 12, 0, 0),
            reason="Signal degraded"
        )

        data = transition.to_dict()
        assert data["from"] == "ONLINE"
        assert data["to"] == "WEAK_SIGNAL"
        assert data["reason"] == "Signal degraded"
        assert "2024-01-01" in data["timestamp"]
