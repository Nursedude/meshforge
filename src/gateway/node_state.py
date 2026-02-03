"""
Node State Machine for MeshForge

Provides granular state tracking for mesh nodes beyond simple online/offline.

States:
- DISCOVERED: First time node seen, collecting initial data
- ONLINE: Node responding normally with good signal
- WEAK_SIGNAL: Low SNR/RSSI but still responding
- INTERMITTENT: Sporadic responses, signal degrading
- SUSPECTED_OFFLINE: No recent response but within grace period
- OFFLINE: Confirmed offline after timeout
- UNREACHABLE: No path available (RNS specific)
- STALE_CACHE: Data loaded from cache, not yet verified

State Transitions:
- STALE_CACHE -> DISCOVERED (on first live update)
- DISCOVERED -> ONLINE (after sufficient good responses)
- ONLINE -> WEAK_SIGNAL (when signal drops below threshold)
- ONLINE/WEAK_SIGNAL -> INTERMITTENT (irregular response pattern)
- ANY -> SUSPECTED_OFFLINE (no response for SUSPECT_THRESHOLD)
- SUSPECTED_OFFLINE -> OFFLINE (no response for OFFLINE_THRESHOLD)
- ANY -> ONLINE (on good response after being offline)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class NodeState(Enum):
    """Granular node states for mesh network tracking."""

    # Initial states
    DISCOVERED = auto()      # First time seen, collecting data
    STALE_CACHE = auto()     # Loaded from cache, not yet verified

    # Active states
    ONLINE = auto()          # Responding normally with good signal
    WEAK_SIGNAL = auto()     # Low SNR/RSSI but still responding
    INTERMITTENT = auto()    # Sporadic responses, degrading

    # Inactive states
    SUSPECTED_OFFLINE = auto()  # No response but within grace period
    OFFLINE = auto()            # Confirmed offline after timeout
    UNREACHABLE = auto()        # No path available (RNS)

    def is_active(self) -> bool:
        """Check if this state indicates the node is active."""
        return self in (NodeState.ONLINE, NodeState.WEAK_SIGNAL,
                       NodeState.INTERMITTENT, NodeState.DISCOVERED)

    def is_healthy(self) -> bool:
        """Check if this state indicates good connectivity."""
        return self in (NodeState.ONLINE, NodeState.DISCOVERED)

    @property
    def display_name(self) -> str:
        """Human-readable state name."""
        return {
            NodeState.DISCOVERED: "Discovered",
            NodeState.STALE_CACHE: "Cached",
            NodeState.ONLINE: "Online",
            NodeState.WEAK_SIGNAL: "Weak Signal",
            NodeState.INTERMITTENT: "Intermittent",
            NodeState.SUSPECTED_OFFLINE: "Checking...",
            NodeState.OFFLINE: "Offline",
            NodeState.UNREACHABLE: "Unreachable",
        }.get(self, self.name)

    @property
    def icon(self) -> str:
        """Status icon for UI display."""
        return {
            NodeState.DISCOVERED: "?",
            NodeState.STALE_CACHE: "~",
            NodeState.ONLINE: "+",
            NodeState.WEAK_SIGNAL: "!",
            NodeState.INTERMITTENT: "~",
            NodeState.SUSPECTED_OFFLINE: "?",
            NodeState.OFFLINE: "-",
            NodeState.UNREACHABLE: "X",
        }.get(self, "?")


@dataclass
class StateTransition:
    """Record of a state transition for debugging/history."""
    from_state: NodeState
    to_state: NodeState
    timestamp: datetime
    reason: str

    def to_dict(self) -> dict:
        return {
            "from": self.from_state.name,
            "to": self.to_state.name,
            "timestamp": self.timestamp.isoformat(),
            "reason": self.reason
        }


@dataclass
class NodeStateConfig:
    """Configuration for node state machine thresholds."""

    # Time thresholds (in seconds)
    suspect_threshold: float = 300.0     # 5 min: online -> suspected_offline
    offline_threshold: float = 3600.0    # 1 hour: suspected -> offline
    discovery_period: float = 60.0       # 1 min: stay in discovered state

    # Signal thresholds
    weak_signal_snr: float = -5.0        # Below this SNR = weak signal
    weak_signal_rssi: int = -110         # Below this RSSI = weak signal

    # Intermittent detection
    intermittent_miss_ratio: float = 0.3  # 30% missed responses = intermittent
    intermittent_window: int = 10         # Check last N expected responses

    # History limits
    max_transitions: int = 50             # Keep last N transitions


class NodeStateMachine:
    """
    State machine for tracking node connectivity status.

    Integrates with UnifiedNode to provide granular state tracking.
    Tracks response patterns and signal quality for state decisions.
    """

    def __init__(self, config: Optional[NodeStateConfig] = None,
                 initial_state: NodeState = NodeState.DISCOVERED):
        self.config = config or NodeStateConfig()
        self._state = initial_state
        self._state_since = datetime.now()
        self._transitions: List[StateTransition] = []

        # Response tracking for intermittent detection
        self._expected_responses: int = 0
        self._actual_responses: int = 0
        self._last_response: Optional[datetime] = None

        # Signal tracking
        self._recent_snr: List[float] = []
        self._recent_rssi: List[int] = []

    @property
    def state(self) -> NodeState:
        """Current node state."""
        return self._state

    @property
    def state_since(self) -> datetime:
        """Timestamp of last state change."""
        return self._state_since

    @property
    def time_in_state(self) -> timedelta:
        """Duration in current state."""
        return datetime.now() - self._state_since

    @property
    def is_online(self) -> bool:
        """Legacy compatibility: check if node is considered online."""
        return self._state.is_active()

    def transition_to(self, new_state: NodeState, reason: str = "") -> bool:
        """
        Transition to a new state.

        Args:
            new_state: Target state
            reason: Human-readable reason for transition

        Returns:
            True if transition occurred, False if already in state
        """
        if new_state == self._state:
            return False

        transition = StateTransition(
            from_state=self._state,
            to_state=new_state,
            timestamp=datetime.now(),
            reason=reason
        )

        self._transitions.append(transition)

        # Trim history
        if len(self._transitions) > self.config.max_transitions:
            self._transitions = self._transitions[-self.config.max_transitions:]

        old_state = self._state
        self._state = new_state
        self._state_since = datetime.now()

        logger.debug(f"State transition: {old_state.name} -> {new_state.name} ({reason})")
        return True

    def expect_response(self) -> None:
        """
        Record that a response was expected (for intermittent detection).

        Call this during periodic polling intervals when you expect to hear
        from the node. The ratio of actual_responses / expected_responses
        is used to detect intermittent connectivity.

        Example usage:
            # During each polling cycle
            state_machine.expect_response()

            # When node actually responds
            state_machine.record_response(snr=8.5)
        """
        self._expected_responses += 1

        # Prevent counters from growing unbounded - reset after window size
        if self._expected_responses > self.config.intermittent_window * 10:
            # Keep the ratio but scale down
            if self._expected_responses > 0:
                ratio = self._actual_responses / self._expected_responses
                self._expected_responses = self.config.intermittent_window
                self._actual_responses = int(self._expected_responses * ratio)

    def record_response(self, snr: Optional[float] = None,
                        rssi: Optional[int] = None) -> NodeState:
        """
        Record a response from the node and update state.

        Call this whenever the node is heard from (message, telemetry, etc.)

        Args:
            snr: Signal-to-Noise Ratio (dB)
            rssi: Received Signal Strength (dBm)

        Returns:
            Current state after update
        """
        now = datetime.now()
        self._last_response = now
        self._actual_responses += 1

        # Auto-increment expected if not being tracked externally
        # This ensures intermittent detection works even without explicit expect_response() calls
        if self._expected_responses == 0:
            self._expected_responses = 1

        # Record signal quality
        if snr is not None:
            self._recent_snr.append(snr)
            if len(self._recent_snr) > 10:
                self._recent_snr = self._recent_snr[-10:]

        if rssi is not None:
            self._recent_rssi.append(rssi)
            if len(self._recent_rssi) > 10:
                self._recent_rssi = self._recent_rssi[-10:]

        # Determine appropriate state based on signal quality
        has_weak_signal = self._check_weak_signal(snr, rssi)

        # State transitions on response
        if self._state in (NodeState.STALE_CACHE, NodeState.DISCOVERED):
            # First real response
            if has_weak_signal:
                self.transition_to(NodeState.WEAK_SIGNAL, "Initial response with weak signal")
            else:
                self.transition_to(NodeState.ONLINE, "Initial response received")

        elif self._state in (NodeState.OFFLINE, NodeState.SUSPECTED_OFFLINE,
                            NodeState.UNREACHABLE):
            # Came back online
            if has_weak_signal:
                self.transition_to(NodeState.WEAK_SIGNAL, "Node responding again (weak)")
            else:
                self.transition_to(NodeState.ONLINE, "Node responding again")

        elif self._state == NodeState.ONLINE:
            # Check for signal degradation
            if has_weak_signal:
                self.transition_to(NodeState.WEAK_SIGNAL, "Signal degraded")

        elif self._state == NodeState.WEAK_SIGNAL:
            # Check for signal improvement
            if not has_weak_signal:
                self.transition_to(NodeState.ONLINE, "Signal improved")

        elif self._state == NodeState.INTERMITTENT:
            # Check if becoming stable again
            if self._check_stable_responses():
                if has_weak_signal:
                    self.transition_to(NodeState.WEAK_SIGNAL, "Responses stabilized (weak)")
                else:
                    self.transition_to(NodeState.ONLINE, "Responses stabilized")

        return self._state

    def check_timeout(self, last_seen: Optional[datetime] = None) -> NodeState:
        """
        Check for timeout and update state if needed.

        Call this periodically (e.g., in cleanup loop) to detect offline nodes.

        Args:
            last_seen: Override for last seen time (uses internal if None)

        Returns:
            Current state after check
        """
        last = last_seen or self._last_response
        if last is None:
            # Never seen - stay in current state
            return self._state

        now = datetime.now()
        silence = (now - last).total_seconds()

        # Check for timeout transitions
        if self._state.is_active():
            if silence > self.config.suspect_threshold:
                self.transition_to(
                    NodeState.SUSPECTED_OFFLINE,
                    f"No response for {silence:.0f}s"
                )

        if self._state == NodeState.SUSPECTED_OFFLINE:
            if silence > self.config.offline_threshold:
                self.transition_to(
                    NodeState.OFFLINE,
                    f"Offline after {silence:.0f}s silence"
                )

        return self._state

    def mark_unreachable(self, reason: str = "No path available") -> NodeState:
        """Mark node as unreachable (no routing path)."""
        self.transition_to(NodeState.UNREACHABLE, reason)
        return self._state

    def _check_weak_signal(self, snr: Optional[float],
                           rssi: Optional[int]) -> bool:
        """Check if current signal indicates weak connectivity."""
        if snr is not None and snr < self.config.weak_signal_snr:
            return True
        if rssi is not None and rssi < self.config.weak_signal_rssi:
            return True

        # Also check recent averages
        if self._recent_snr:
            avg_snr = sum(self._recent_snr) / len(self._recent_snr)
            if avg_snr < self.config.weak_signal_snr:
                return True

        if self._recent_rssi:
            avg_rssi = sum(self._recent_rssi) / len(self._recent_rssi)
            if avg_rssi < self.config.weak_signal_rssi:
                return True

        return False

    def _check_stable_responses(self) -> bool:
        """Check if response pattern has stabilized.

        Returns True if the node is responding reliably enough to be
        considered stable (not intermittent).
        """
        # Need a recent response to consider stable
        if self._last_response is None:
            return False

        # Must be in INTERMITTENT state for at least 60 seconds
        # before we can declare it stable again
        time_since = (datetime.now() - self._state_since).total_seconds()
        if time_since < 60:
            return False

        # If not enough data to evaluate, use time-based heuristic:
        # At least 3 responses in the last minute suggests stability
        if self._expected_responses < 3:
            # Fall back to response count since entering state
            return self._actual_responses >= 3

        # Use response ratio when we have enough data
        ratio = self._actual_responses / self._expected_responses
        return ratio > (1 - self.config.intermittent_miss_ratio)

    def get_transitions(self, count: int = 10) -> List[StateTransition]:
        """Get recent state transitions."""
        return self._transitions[-count:]

    def to_dict(self) -> dict:
        """Serialize state machine for persistence."""
        return {
            "state": self._state.name,
            "state_since": self._state_since.isoformat(),
            "last_response": self._last_response.isoformat() if self._last_response else None,
            "transitions": [t.to_dict() for t in self._transitions[-10:]]
        }

    @classmethod
    def from_dict(cls, data: dict, config: Optional[NodeStateConfig] = None) -> 'NodeStateMachine':
        """Restore state machine from persisted data."""
        try:
            state = NodeState[data.get("state", "STALE_CACHE")]
        except KeyError:
            state = NodeState.STALE_CACHE

        sm = cls(config=config, initial_state=state)

        if data.get("state_since"):
            try:
                sm._state_since = datetime.fromisoformat(data["state_since"])
            except (ValueError, TypeError):
                pass

        if data.get("last_response"):
            try:
                sm._last_response = datetime.fromisoformat(data["last_response"])
            except (ValueError, TypeError):
                pass

        return sm


# Default configuration instance
_default_config = NodeStateConfig()


def get_default_state_config() -> NodeStateConfig:
    """Get the default state machine configuration."""
    return _default_config
