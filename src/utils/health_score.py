"""
Network health scoring — synthesize node metrics into a unified 0-100 score.

Aggregates health signals from multiple MeshForge subsystems into a single
composite score with categorical breakdowns. Provides both point-in-time
scoring and historical trend tracking.

Score categories:
- Connectivity (0-100): Service availability, node reachability
- Performance (0-100): SNR/RSSI quality, channel utilization
- Reliability (0-100): Message success rate, error frequency
- Freshness (0-100): Data staleness, last-seen recency

Overall score = weighted average of category scores.

Thresholds:
- 75-100: Healthy (green)
- 50-74:  Fair (yellow)
- 25-49:  Degraded (orange)
- 0-24:   Critical (red)

Usage:
    from utils.health_score import HealthScorer, HealthSnapshot

    scorer = HealthScorer()

    # Add signals from various sources
    scorer.report_service_status('meshtasticd', running=True)
    scorer.report_service_status('rnsd', running=False)
    scorer.report_node_metrics(node_id='!abc123', snr=-5.0, rssi=-90)
    scorer.report_message_stats(sent=100, delivered=95, failed=5)

    # Get current health
    snapshot = scorer.get_snapshot()
    print(f"Network Health: {snapshot.overall_score}/100 ({snapshot.status})")
    print(f"  Connectivity: {snapshot.connectivity_score}")
    print(f"  Performance:  {snapshot.performance_score}")
    print(f"  Reliability:  {snapshot.reliability_score}")
    print(f"  Freshness:    {snapshot.freshness_score}")
"""

import threading
import time
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# EventBus for automatic service status updates
from utils.event_bus import event_bus


# Score thresholds
THRESHOLD_HEALTHY = 75
THRESHOLD_FAIR = 50
THRESHOLD_DEGRADED = 25

# Category weights (must sum to 1.0)
WEIGHT_CONNECTIVITY = 0.30
WEIGHT_PERFORMANCE = 0.25
WEIGHT_RELIABILITY = 0.30
WEIGHT_FRESHNESS = 0.15

# Staleness thresholds (seconds)
FRESH_THRESHOLD = 300       # 5 minutes — very fresh
STALE_THRESHOLD = 3600      # 1 hour — getting stale
DEAD_THRESHOLD = 7200       # 2 hours — essentially dead

# Signal quality thresholds (dB)
SNR_EXCELLENT = -5.0   # Above this: great signal
SNR_GOOD = -10.0       # Above this: usable
SNR_FAIR = -15.0       # Above this: marginal
# Below SNR_FAIR: poor

RSSI_EXCELLENT = -80   # Above this: strong
RSSI_GOOD = -100       # Above this: moderate
RSSI_FAIR = -115       # Above this: weak
# Below RSSI_FAIR: barely receiving

# History limits
MAX_HISTORY = 1000
MAX_NODE_HISTORY = 200


@dataclass
class ServiceStatus:
    """Status of a single service."""
    name: str
    running: bool
    critical: bool = True  # Is this service critical for operation?
    last_check: float = 0.0

    def __post_init__(self):
        if self.last_check == 0.0:
            self.last_check = time.time()


@dataclass
class NodeMetrics:
    """Metrics for a single node."""
    node_id: str
    snr: Optional[float] = None
    rssi: Optional[int] = None
    last_seen: float = 0.0
    battery_level: Optional[float] = None  # 0-100
    channel_util: Optional[float] = None   # 0-100%

    def __post_init__(self):
        if self.last_seen == 0.0:
            self.last_seen = time.time()


@dataclass
class MessageStats:
    """Message delivery statistics."""
    sent: int = 0
    delivered: int = 0
    failed: int = 0
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    @property
    def success_rate(self) -> float:
        """Delivery success rate (0.0 to 1.0)."""
        total = self.sent
        if total <= 0:
            return 1.0  # No messages = no failures
        return self.delivered / total


@dataclass
class HealthSnapshot:
    """Point-in-time health assessment."""
    overall_score: float
    connectivity_score: float
    performance_score: float
    reliability_score: float
    freshness_score: float
    status: str  # 'healthy', 'fair', 'degraded', 'critical'
    timestamp: float = 0.0
    node_count: int = 0
    service_count: int = 0
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    @property
    def category_scores(self) -> Dict[str, float]:
        """Per-category score breakdown as a dict."""
        return {
            'connectivity': self.connectivity_score,
            'performance': self.performance_score,
            'reliability': self.reliability_score,
            'freshness': self.freshness_score,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            'overall_score': round(self.overall_score, 1),
            'status': self.status,
            'categories': {
                cat: round(score, 1)
                for cat, score in self.category_scores.items()
            },
            'node_count': self.node_count,
            'service_count': self.service_count,
            'timestamp': self.timestamp,
            'details': self.details,
        }


def score_to_status(score: float) -> str:
    """Convert numeric score to status string.

    Args:
        score: Health score 0-100.

    Returns:
        Status string: 'healthy', 'fair', 'degraded', or 'critical'.
    """
    if score >= THRESHOLD_HEALTHY:
        return 'healthy'
    elif score >= THRESHOLD_FAIR:
        return 'fair'
    elif score >= THRESHOLD_DEGRADED:
        return 'degraded'
    else:
        return 'critical'


def clamp(value: float, min_val: float = 0.0, max_val: float = 100.0) -> float:
    """Clamp value to range."""
    return max(min_val, min(max_val, value))


class HealthScorer:
    """Unified network health scoring engine.

    Collects health signals from multiple sources and synthesizes
    them into a composite 0-100 score with categorical breakdown.

    Thread-safe for concurrent signal reporting.
    """

    def __init__(self,
                 weight_connectivity: float = WEIGHT_CONNECTIVITY,
                 weight_performance: float = WEIGHT_PERFORMANCE,
                 weight_reliability: float = WEIGHT_RELIABILITY,
                 weight_freshness: float = WEIGHT_FRESHNESS):
        """Initialize health scorer.

        Args:
            weight_connectivity: Weight for connectivity score (0-1).
            weight_performance: Weight for performance score (0-1).
            weight_reliability: Weight for reliability score (0-1).
            weight_freshness: Weight for freshness score (0-1).
        """
        self.weights = {
            'connectivity': weight_connectivity,
            'performance': weight_performance,
            'reliability': weight_reliability,
            'freshness': weight_freshness,
        }

        # Thread safety
        self._lock = threading.Lock()

        # Current state
        self._services: Dict[str, ServiceStatus] = {}
        self._nodes: Dict[str, NodeMetrics] = {}
        self._message_stats = MessageStats()
        self._error_count: int = 0
        self._error_window: deque = deque(maxlen=MAX_HISTORY)

        # History for trend detection
        self._history: deque = deque(maxlen=MAX_HISTORY)

    def report_service_status(self, name: str, running: bool,
                              critical: bool = True) -> None:
        """Report a service's current status.

        Args:
            name: Service name (e.g., 'meshtasticd', 'rnsd').
            running: Whether the service is running.
            critical: Whether this service is critical for operation.
        """
        with self._lock:
            self._services[name] = ServiceStatus(
                name=name, running=running, critical=critical)

    def report_node_metrics(self, node_id: str,
                           snr: Optional[float] = None,
                           rssi: Optional[int] = None,
                           battery_level: Optional[float] = None,
                           channel_util: Optional[float] = None,
                           last_seen: Optional[float] = None) -> None:
        """Report metrics for a specific node.

        Args:
            node_id: Node identifier.
            snr: Signal-to-noise ratio in dB.
            rssi: Received signal strength in dBm.
            battery_level: Battery percentage (0-100).
            channel_util: Channel utilization percentage (0-100).
            last_seen: Timestamp of last contact (default: now).
        """
        with self._lock:
            self._nodes[node_id] = NodeMetrics(
                node_id=node_id,
                snr=snr,
                rssi=rssi,
                battery_level=battery_level,
                channel_util=channel_util,
                last_seen=last_seen or time.time(),
            )

    def report_message_stats(self, sent: int = 0, delivered: int = 0,
                            failed: int = 0) -> None:
        """Report message delivery statistics.

        Args:
            sent: Total messages sent.
            delivered: Successfully delivered messages.
            failed: Failed messages.
        """
        with self._lock:
            self._message_stats = MessageStats(
                sent=sent, delivered=delivered, failed=failed)

    def report_error(self, timestamp: Optional[float] = None) -> None:
        """Report an error occurrence for error rate calculation.

        Args:
            timestamp: When the error occurred (default: now).
        """
        with self._lock:
            self._error_window.append(timestamp or time.time())
            self._error_count += 1

    def _score_connectivity(self) -> Tuple[float, Dict[str, Any]]:
        """Calculate connectivity subscore.

        Based on:
        - Critical services running
        - Number of visible nodes
        - Node reachability

        Returns:
            Tuple of (score, details_dict).
        """
        details: Dict[str, Any] = {}

        if not self._services:
            # No services reported — assume neutral
            return 50.0, {'note': 'no services reported'}

        # Service health: critical services are weighted more
        critical_services = [s for s in self._services.values() if s.critical]
        optional_services = [s for s in self._services.values() if not s.critical]

        if critical_services:
            critical_up = sum(1 for s in critical_services if s.running)
            critical_score = (critical_up / len(critical_services)) * 100
        else:
            critical_score = 100.0

        if optional_services:
            optional_up = sum(1 for s in optional_services if s.running)
            optional_score = (optional_up / len(optional_services)) * 100
        else:
            optional_score = 100.0

        # Critical services are 80% of connectivity, optional 20%
        service_score = critical_score * 0.8 + optional_score * 0.2

        # Node count bonus: more nodes = better connectivity
        node_count = len(self._nodes)
        if node_count == 0:
            node_bonus = 0.0
        elif node_count < 3:
            node_bonus = node_count * 10.0  # Up to 30 bonus points
        else:
            node_bonus = 30.0  # Cap at 30

        # Combine: service_score (70%) + node presence (30%)
        score = service_score * 0.7 + min(node_bonus / 30.0 * 100, 100) * 0.3

        details['critical_services_up'] = (
            sum(1 for s in critical_services if s.running)
            if critical_services else 0)
        details['critical_services_total'] = len(critical_services)
        details['nodes_visible'] = node_count

        return clamp(score), details

    def _score_performance(self) -> Tuple[float, Dict[str, Any]]:
        """Calculate performance subscore.

        Based on:
        - Average SNR across nodes
        - Average RSSI across nodes
        - Channel utilization

        Returns:
            Tuple of (score, details_dict).
        """
        details: Dict[str, Any] = {}

        if not self._nodes:
            return 50.0, {'note': 'no node metrics'}

        # Collect signal quality metrics
        snr_scores = []
        rssi_scores = []
        util_penalties = []

        for node in self._nodes.values():
            if node.snr is not None:
                snr_scores.append(self._snr_to_score(node.snr))
            if node.rssi is not None:
                rssi_scores.append(self._rssi_to_score(node.rssi))
            if node.channel_util is not None:
                # High utilization is bad
                util_penalties.append(max(0, node.channel_util - 25) * 2)

        # Average signal quality
        if snr_scores:
            avg_snr_score = sum(snr_scores) / len(snr_scores)
        else:
            avg_snr_score = 50.0

        if rssi_scores:
            avg_rssi_score = sum(rssi_scores) / len(rssi_scores)
        else:
            avg_rssi_score = 50.0

        # Signal score: average of SNR and RSSI scores
        signal_score = (avg_snr_score + avg_rssi_score) / 2.0

        # Apply utilization penalty
        if util_penalties:
            avg_penalty = sum(util_penalties) / len(util_penalties)
            signal_score = max(0, signal_score - avg_penalty)

        details['avg_snr_score'] = round(avg_snr_score, 1)
        details['avg_rssi_score'] = round(avg_rssi_score, 1)
        details['nodes_with_signal'] = len(snr_scores) + len(rssi_scores)

        return clamp(signal_score), details

    def _score_reliability(self) -> Tuple[float, Dict[str, Any]]:
        """Calculate reliability subscore.

        Based on:
        - Message delivery success rate
        - Error frequency
        - Connection stability

        Returns:
            Tuple of (score, details_dict).
        """
        details: Dict[str, Any] = {}

        # Message delivery rate (0-100)
        if self._message_stats.sent > 0:
            delivery_score = self._message_stats.success_rate * 100
        else:
            delivery_score = 100.0  # No messages = no failures

        # Error rate penalty (errors in last 5 minutes)
        now = time.time()
        recent_errors = sum(1 for t in self._error_window
                           if now - t < 300)
        # Each error reduces score by 5, up to 50 point reduction
        error_penalty = min(recent_errors * 5, 50)

        score = max(0, delivery_score - error_penalty)

        details['delivery_rate'] = round(self._message_stats.success_rate * 100, 1)
        details['recent_errors'] = recent_errors
        details['total_messages'] = self._message_stats.sent

        return clamp(score), details

    def _score_freshness(self) -> Tuple[float, Dict[str, Any]]:
        """Calculate freshness subscore.

        Based on:
        - How recently nodes were seen
        - Data staleness

        Returns:
            Tuple of (score, details_dict).
        """
        details: Dict[str, Any] = {}

        if not self._nodes:
            return 50.0, {'note': 'no nodes tracked'}

        now = time.time()
        freshness_scores = []

        for node in self._nodes.values():
            age = now - node.last_seen
            if age <= FRESH_THRESHOLD:
                # Fresh: linear 100→75 over 5 minutes
                freshness_scores.append(100 - (age / FRESH_THRESHOLD) * 25)
            elif age <= STALE_THRESHOLD:
                # Stale: linear 75→25 over next hour
                progress = (age - FRESH_THRESHOLD) / (STALE_THRESHOLD - FRESH_THRESHOLD)
                freshness_scores.append(75 - progress * 50)
            elif age <= DEAD_THRESHOLD:
                # Dead: linear 25→0 over next hour
                progress = (age - STALE_THRESHOLD) / (DEAD_THRESHOLD - STALE_THRESHOLD)
                freshness_scores.append(25 - progress * 25)
            else:
                freshness_scores.append(0.0)

        avg_freshness = sum(freshness_scores) / len(freshness_scores)

        # Count fresh vs stale nodes
        fresh_count = sum(1 for s in freshness_scores if s >= 75)
        stale_count = sum(1 for s in freshness_scores if s < 75)

        details['fresh_nodes'] = fresh_count
        details['stale_nodes'] = stale_count
        details['avg_freshness'] = round(avg_freshness, 1)

        return clamp(avg_freshness), details

    def _snr_to_score(self, snr: float) -> float:
        """Convert SNR value to quality score (0-100).

        Args:
            snr: Signal-to-noise ratio in dB.

        Returns:
            Quality score 0-100.
        """
        if snr >= SNR_EXCELLENT:
            return 100.0
        elif snr >= SNR_GOOD:
            # Linear interpolation between excellent and good
            progress = (snr - SNR_GOOD) / (SNR_EXCELLENT - SNR_GOOD)
            return 75.0 + progress * 25.0
        elif snr >= SNR_FAIR:
            progress = (snr - SNR_FAIR) / (SNR_GOOD - SNR_FAIR)
            return 50.0 + progress * 25.0
        else:
            # Below fair: linear down to 0
            # At -25 dB: score = 0
            below = SNR_FAIR - snr
            return max(0.0, 50.0 - below * 5.0)

    def _rssi_to_score(self, rssi: int) -> float:
        """Convert RSSI value to quality score (0-100).

        Args:
            rssi: Received signal strength in dBm.

        Returns:
            Quality score 0-100.
        """
        if rssi >= RSSI_EXCELLENT:
            return 100.0
        elif rssi >= RSSI_GOOD:
            progress = (rssi - RSSI_GOOD) / (RSSI_EXCELLENT - RSSI_GOOD)
            return 75.0 + progress * 25.0
        elif rssi >= RSSI_FAIR:
            progress = (rssi - RSSI_FAIR) / (RSSI_GOOD - RSSI_FAIR)
            return 50.0 + progress * 25.0
        else:
            below = RSSI_FAIR - rssi
            return max(0.0, 50.0 - below * 2.5)

    def get_snapshot(self) -> HealthSnapshot:
        """Calculate current health snapshot.

        Returns:
            HealthSnapshot with overall and per-category scores.
        """
        with self._lock:
            conn_score, conn_details = self._score_connectivity()
            perf_score, perf_details = self._score_performance()
            rel_score, rel_details = self._score_reliability()
            fresh_score, fresh_details = self._score_freshness()

            # Weighted average
            overall = (
                conn_score * self.weights['connectivity'] +
                perf_score * self.weights['performance'] +
                rel_score * self.weights['reliability'] +
                fresh_score * self.weights['freshness']
            )

            snapshot = HealthSnapshot(
                overall_score=clamp(overall),
                connectivity_score=conn_score,
                performance_score=perf_score,
                reliability_score=rel_score,
                freshness_score=fresh_score,
                status=score_to_status(overall),
                node_count=len(self._nodes),
                service_count=len(self._services),
                details={
                    'connectivity': conn_details,
                    'performance': perf_details,
                    'reliability': rel_details,
                    'freshness': fresh_details,
                }
            )

            # Store in history
            self._history.append(snapshot)

            return snapshot

    def get_trend(self, window: int = 10) -> Optional[str]:
        """Determine health trend from recent history.

        Args:
            window: Number of recent snapshots to analyze.

        Returns:
            'improving', 'stable', 'declining', or None if insufficient data.
        """
        if len(self._history) < 3:
            return None

        recent = list(self._history)[-window:]
        if len(recent) < 3:
            return None

        # Simple linear trend: compare first half average to second half
        mid = len(recent) // 2
        first_half = sum(s.overall_score for s in recent[:mid]) / mid
        second_half = sum(s.overall_score for s in recent[mid:]) / (len(recent) - mid)

        diff = second_half - first_half
        if diff > 5.0:
            return 'improving'
        elif diff < -5.0:
            return 'declining'
        else:
            return 'stable'

    def get_history(self, count: int = 20) -> List[HealthSnapshot]:
        """Get recent health history.

        Args:
            count: Number of recent snapshots to return.

        Returns:
            List of recent HealthSnapshots (newest last).
        """
        return list(self._history)[-count:]

    def get_node_health(self, node_id: str) -> Optional[float]:
        """Get health score for a specific node.

        Combines signal quality and freshness for single-node assessment.

        Args:
            node_id: Node identifier.

        Returns:
            Node health score (0-100) or None if node unknown.
        """
        node = self._nodes.get(node_id)
        if node is None:
            return None

        # Signal quality (50% weight)
        signal_scores = []
        if node.snr is not None:
            signal_scores.append(self._snr_to_score(node.snr))
        if node.rssi is not None:
            signal_scores.append(self._rssi_to_score(node.rssi))

        if signal_scores:
            signal_score = sum(signal_scores) / len(signal_scores)
        else:
            signal_score = 50.0

        # Freshness (30% weight)
        now = time.time()
        age = now - node.last_seen
        if age <= FRESH_THRESHOLD:
            fresh_score = 100.0
        elif age <= STALE_THRESHOLD:
            progress = (age - FRESH_THRESHOLD) / (STALE_THRESHOLD - FRESH_THRESHOLD)
            fresh_score = 100.0 - progress * 75.0
        else:
            fresh_score = max(0.0, 25.0 - (age - STALE_THRESHOLD) / 3600 * 25)

        # Battery (20% weight)
        if node.battery_level is not None:
            battery_score = node.battery_level  # Already 0-100
        else:
            battery_score = 75.0  # Assume OK if unknown

        return clamp(signal_score * 0.5 + fresh_score * 0.3 + battery_score * 0.2)

    def reset(self) -> None:
        """Clear all state and history."""
        self._services.clear()
        self._nodes.clear()
        self._message_stats = MessageStats()
        self._error_count = 0
        self._error_window.clear()
        self._history.clear()


# =============================================================================
# Module-level singleton and EventBus integration
# =============================================================================

_health_scorer: Optional[HealthScorer] = None


def get_health_scorer() -> HealthScorer:
    """Get the singleton HealthScorer instance.

    Creates the scorer on first call and subscribes to the EventBus
    so that service status updates from the ActiveHealthProbe are
    automatically fed into the scoring engine.

    Returns:
        Shared HealthScorer instance.
    """
    global _health_scorer
    if _health_scorer is not None:
        return _health_scorer

    _health_scorer = HealthScorer()

    # Subscribe to EventBus service events for automatic scoring
    event_bus.subscribe('service', _on_service_event)

    return _health_scorer


def _on_service_event(event) -> None:
    """Feed EventBus ServiceEvents into the HealthScorer."""
    scorer = _health_scorer
    if scorer is None:
        return
    service_name = getattr(event, 'service_name', None)
    available = getattr(event, 'available', None)
    if service_name is not None and available is not None:
        scorer.report_service_status(
            name=service_name,
            running=available,
            critical=(service_name in ('meshtasticd', 'rnsd')),
        )


def format_health_display(snapshot: HealthSnapshot) -> str:
    """Format health snapshot for TUI display.

    Args:
        snapshot: HealthSnapshot to format.

    Returns:
        Formatted multi-line string.
    """
    # Status indicator
    indicators = {
        'healthy': '[OK]',
        'fair': '[--]',
        'degraded': '[!!]',
        'critical': '[XX]',
    }
    indicator = indicators.get(snapshot.status, '[??]')

    lines = []
    lines.append("=" * 50)
    lines.append(f"  Network Health: {snapshot.overall_score:.0f}/100 {indicator}")
    lines.append("=" * 50)
    lines.append("")

    # Category bars
    categories = [
        ('Connectivity', snapshot.connectivity_score),
        ('Performance', snapshot.performance_score),
        ('Reliability', snapshot.reliability_score),
        ('Freshness', snapshot.freshness_score),
    ]

    for name, score in categories:
        bar_len = int(score / 5)  # 20 chars max
        bar = '#' * bar_len + '.' * (20 - bar_len)
        lines.append(f"  {name:<14} [{bar}] {score:.0f}")

    lines.append("")
    lines.append(f"  Status: {snapshot.status.upper()}")
    lines.append("=" * 50)

    return "\n".join(lines)
