"""
Link Quality Scoring for MeshForge.

Provides a composite link quality scoring algorithm that combines multiple
metrics to produce a unified quality score for mesh network links.

Factors considered:
- SNR (Signal-to-Noise Ratio): Primary signal quality indicator
- RSSI (Received Signal Strength): Absolute power level
- Hop count: Path length (fewer hops = better)
- Link age: Time since last update (fresher = better)
- Announce frequency: How often link is refreshed (more = better)
- Packet loss: Historical success rate (if available)

Output: Composite score 0-100 with quality classification

Usage:
    from utils.link_quality import LinkQualityScorer, compute_link_score

    scorer = LinkQualityScorer()
    score = scorer.score(
        snr=8.5,
        rssi=-85,
        hops=2,
        age_seconds=300,
        announce_count=10
    )
    print(f"Quality: {score.quality} ({score.score}/100)")
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger(__name__)


class LinkQuality(Enum):
    """Link quality classification levels."""
    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"
    BAD = "bad"
    UNKNOWN = "unknown"


@dataclass
class LinkScore:
    """Complete link quality assessment result."""
    # Composite score (0-100)
    score: float

    # Quality classification
    quality: LinkQuality

    # Individual component scores (0-100 each)
    snr_score: float = 0.0
    rssi_score: float = 0.0
    hops_score: float = 0.0
    age_score: float = 0.0
    stability_score: float = 0.0

    # Weights used for calculation
    weights: Dict[str, float] = field(default_factory=dict)

    # Original input values
    inputs: Dict[str, Any] = field(default_factory=dict)

    # Recommendations for improvement
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "score": round(self.score, 1),
            "quality": self.quality.value,
            "components": {
                "snr": round(self.snr_score, 1),
                "rssi": round(self.rssi_score, 1),
                "hops": round(self.hops_score, 1),
                "age": round(self.age_score, 1),
                "stability": round(self.stability_score, 1),
            },
            "weights": self.weights,
            "inputs": self.inputs,
            "recommendations": self.recommendations,
        }

    def get_color(self) -> str:
        """Get color code for visual display."""
        colors = {
            LinkQuality.EXCELLENT: "#22c55e",  # Green
            LinkQuality.GOOD: "#84cc16",       # Light green
            LinkQuality.FAIR: "#eab308",       # Yellow
            LinkQuality.POOR: "#f97316",       # Orange
            LinkQuality.BAD: "#ef4444",        # Red
            LinkQuality.UNKNOWN: "#6b7280",    # Gray
        }
        return colors.get(self.quality, "#6b7280")


class LinkQualityScorer:
    """
    Composite link quality scoring algorithm.

    Uses weighted combination of multiple factors to produce a unified
    quality score. Weights can be customized for different use cases.
    """

    # Default weights for each component (must sum to 1.0)
    DEFAULT_WEIGHTS = {
        "snr": 0.35,        # SNR is primary quality indicator
        "rssi": 0.15,       # RSSI adds absolute power context
        "hops": 0.20,       # Path length matters for latency
        "age": 0.15,        # Freshness of data
        "stability": 0.15,  # Link stability over time
    }

    # SNR thresholds for LoRa (dB)
    SNR_EXCELLENT = 10.0
    SNR_GOOD = 5.0
    SNR_FAIR = 0.0
    SNR_POOR = -5.0
    SNR_MIN = -15.0  # Below this, link is unusable

    # RSSI thresholds (dBm) - typical for LoRa
    RSSI_EXCELLENT = -80
    RSSI_GOOD = -100
    RSSI_FAIR = -110
    RSSI_POOR = -120
    RSSI_MIN = -140  # Sensitivity floor

    # Age thresholds (seconds)
    AGE_EXCELLENT = 60         # Updated in last minute
    AGE_GOOD = 300             # Within 5 minutes
    AGE_FAIR = 900             # Within 15 minutes
    AGE_POOR = 3600            # Within an hour
    AGE_MAX = 86400            # Beyond 24h is stale

    # Hop count scoring
    MAX_USEFUL_HOPS = 10       # Beyond this, quality degrades severely

    # Quality thresholds for composite score
    QUALITY_THRESHOLDS = {
        90: LinkQuality.EXCELLENT,
        70: LinkQuality.GOOD,
        50: LinkQuality.FAIR,
        30: LinkQuality.POOR,
        0: LinkQuality.BAD,
    }

    def __init__(self, weights: Dict[str, float] = None):
        """
        Initialize the scorer with optional custom weights.

        Args:
            weights: Custom weights dict (keys: snr, rssi, hops, age, stability)
                     Values should sum to 1.0
        """
        self.weights = dict(self.DEFAULT_WEIGHTS)
        if weights:
            self.weights.update(weights)

        # Normalize weights to ensure they sum to 1.0
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.01:
            for key in self.weights:
                self.weights[key] /= total

    def score(
        self,
        snr: float = None,
        rssi: int = None,
        hops: int = 1,
        age_seconds: float = None,
        announce_count: int = None,
        packet_loss: float = None,
        last_seen: datetime = None,
    ) -> LinkScore:
        """
        Calculate composite link quality score.

        Args:
            snr: Signal-to-Noise Ratio in dB
            rssi: Received Signal Strength in dBm
            hops: Number of hops in path (1 = direct)
            age_seconds: Seconds since last update
            announce_count: Number of announcements received
            packet_loss: Packet loss rate 0.0-1.0 (if available)
            last_seen: Datetime of last activity

        Returns:
            LinkScore with composite score and component breakdown
        """
        inputs = {
            "snr": snr,
            "rssi": rssi,
            "hops": hops,
            "age_seconds": age_seconds,
            "announce_count": announce_count,
            "packet_loss": packet_loss,
        }

        # Calculate age if last_seen provided
        if age_seconds is None and last_seen is not None:
            age_seconds = (datetime.now() - last_seen).total_seconds()
            inputs["age_seconds"] = age_seconds

        # Calculate individual component scores
        snr_score = self._score_snr(snr)
        rssi_score = self._score_rssi(rssi)
        hops_score = self._score_hops(hops)
        age_score = self._score_age(age_seconds)
        stability_score = self._score_stability(announce_count, packet_loss)

        # Calculate weighted composite
        composite = (
            self.weights["snr"] * snr_score +
            self.weights["rssi"] * rssi_score +
            self.weights["hops"] * hops_score +
            self.weights["age"] * age_score +
            self.weights["stability"] * stability_score
        )

        # Clamp to 0-100
        composite = max(0.0, min(100.0, composite))

        # Determine quality classification
        quality = self._classify_quality(composite)

        # Generate recommendations
        recommendations = self._generate_recommendations(
            snr_score, rssi_score, hops_score, age_score, stability_score,
            snr, rssi, hops, age_seconds
        )

        return LinkScore(
            score=composite,
            quality=quality,
            snr_score=snr_score,
            rssi_score=rssi_score,
            hops_score=hops_score,
            age_score=age_score,
            stability_score=stability_score,
            weights=dict(self.weights),
            inputs=inputs,
            recommendations=recommendations,
        )

    def _score_snr(self, snr: float = None) -> float:
        """Score SNR on 0-100 scale."""
        if snr is None:
            return 50.0  # Unknown - neutral score

        # Map SNR to 0-100 using sigmoid-like curve
        if snr >= self.SNR_EXCELLENT:
            return 100.0
        if snr <= self.SNR_MIN:
            return 0.0

        # Linear interpolation between thresholds
        if snr >= self.SNR_GOOD:
            return 80.0 + 20.0 * (snr - self.SNR_GOOD) / (self.SNR_EXCELLENT - self.SNR_GOOD)
        if snr >= self.SNR_FAIR:
            return 60.0 + 20.0 * (snr - self.SNR_FAIR) / (self.SNR_GOOD - self.SNR_FAIR)
        if snr >= self.SNR_POOR:
            return 40.0 + 20.0 * (snr - self.SNR_POOR) / (self.SNR_FAIR - self.SNR_POOR)

        # Below poor threshold
        return 40.0 * (snr - self.SNR_MIN) / (self.SNR_POOR - self.SNR_MIN)

    def _score_rssi(self, rssi: int = None) -> float:
        """Score RSSI on 0-100 scale."""
        if rssi is None:
            return 50.0  # Unknown - neutral score

        # Map RSSI to 0-100
        if rssi >= self.RSSI_EXCELLENT:
            return 100.0
        if rssi <= self.RSSI_MIN:
            return 0.0

        # Linear interpolation
        if rssi >= self.RSSI_GOOD:
            return 80.0 + 20.0 * (rssi - self.RSSI_GOOD) / (self.RSSI_EXCELLENT - self.RSSI_GOOD)
        if rssi >= self.RSSI_FAIR:
            return 60.0 + 20.0 * (rssi - self.RSSI_FAIR) / (self.RSSI_GOOD - self.RSSI_FAIR)
        if rssi >= self.RSSI_POOR:
            return 40.0 + 20.0 * (rssi - self.RSSI_POOR) / (self.RSSI_FAIR - self.RSSI_POOR)

        # Below poor threshold
        return 40.0 * (rssi - self.RSSI_MIN) / (self.RSSI_POOR - self.RSSI_MIN)

    def _score_hops(self, hops: int = 1) -> float:
        """Score hop count on 0-100 scale (fewer is better)."""
        if hops is None or hops < 1:
            hops = 1

        if hops == 1:
            return 100.0  # Direct connection
        if hops == 2:
            return 85.0   # One relay
        if hops == 3:
            return 70.0   # Two relays
        if hops == 4:
            return 55.0
        if hops == 5:
            return 45.0

        # Beyond 5 hops, quality degrades faster
        if hops >= self.MAX_USEFUL_HOPS:
            return 10.0

        # Linear decay from 45 to 10
        return 45.0 - (hops - 5) * (35.0 / (self.MAX_USEFUL_HOPS - 5))

    def _score_age(self, age_seconds: float = None) -> float:
        """Score age on 0-100 scale (fresher is better)."""
        if age_seconds is None:
            return 50.0  # Unknown - neutral score

        if age_seconds <= 0:
            return 100.0  # Just now

        if age_seconds <= self.AGE_EXCELLENT:
            return 100.0
        if age_seconds <= self.AGE_GOOD:
            return 80.0 + 20.0 * (1 - (age_seconds - self.AGE_EXCELLENT) /
                                  (self.AGE_GOOD - self.AGE_EXCELLENT))
        if age_seconds <= self.AGE_FAIR:
            return 60.0 + 20.0 * (1 - (age_seconds - self.AGE_GOOD) /
                                  (self.AGE_FAIR - self.AGE_GOOD))
        if age_seconds <= self.AGE_POOR:
            return 40.0 + 20.0 * (1 - (age_seconds - self.AGE_FAIR) /
                                  (self.AGE_POOR - self.AGE_FAIR))
        if age_seconds <= self.AGE_MAX:
            return 20.0 + 20.0 * (1 - (age_seconds - self.AGE_POOR) /
                                  (self.AGE_MAX - self.AGE_POOR))

        # Stale data
        return 10.0

    def _score_stability(self, announce_count: int = None,
                         packet_loss: float = None) -> float:
        """
        Score link stability based on announce frequency and packet loss.

        Args:
            announce_count: Number of announces received (more = more stable)
            packet_loss: Packet loss rate 0.0-1.0 (lower = better)
        """
        scores = []

        # Announce count scoring
        if announce_count is not None and announce_count >= 0:
            if announce_count >= 50:
                scores.append(100.0)
            elif announce_count >= 20:
                scores.append(80.0 + 20.0 * (announce_count - 20) / 30)
            elif announce_count >= 10:
                scores.append(60.0 + 20.0 * (announce_count - 10) / 10)
            elif announce_count >= 5:
                scores.append(40.0 + 20.0 * (announce_count - 5) / 5)
            elif announce_count >= 1:
                scores.append(20.0 + 20.0 * (announce_count - 1) / 4)
            else:
                scores.append(10.0)

        # Packet loss scoring
        if packet_loss is not None:
            packet_loss = max(0.0, min(1.0, packet_loss))
            # 0% loss = 100, 100% loss = 0, exponential decay
            scores.append(100.0 * (1 - packet_loss) ** 2)

        if scores:
            return sum(scores) / len(scores)

        return 50.0  # Unknown - neutral score

    def _classify_quality(self, score: float) -> LinkQuality:
        """Classify score into quality category."""
        for threshold, quality in sorted(self.QUALITY_THRESHOLDS.items(), reverse=True):
            if score >= threshold:
                return quality
        return LinkQuality.BAD

    def _generate_recommendations(
        self,
        snr_score: float,
        rssi_score: float,
        hops_score: float,
        age_score: float,
        stability_score: float,
        snr: float = None,
        rssi: int = None,
        hops: int = None,
        age_seconds: float = None,
    ) -> List[str]:
        """Generate improvement recommendations based on weak components."""
        recommendations = []

        # SNR issues
        if snr_score < 50 and snr is not None:
            if snr < -5:
                recommendations.append(
                    "Critical: Very low SNR ({:.1f} dB). Consider antenna upgrade or "
                    "relocating node to improve signal quality.".format(snr)
                )
            else:
                recommendations.append(
                    "SNR is marginal ({:.1f} dB). Check for interference or "
                    "antenna alignment.".format(snr)
                )

        # RSSI issues
        if rssi_score < 50 and rssi is not None:
            if rssi < -120:
                recommendations.append(
                    "Signal strength is near sensitivity limit ({} dBm). "
                    "Node may be at edge of range.".format(rssi)
                )
            else:
                recommendations.append(
                    "Weak signal ({} dBm). Consider higher gain antenna or "
                    "reducing distance.".format(rssi)
                )

        # Hop count issues
        if hops_score < 50 and hops is not None and hops > 4:
            recommendations.append(
                f"High hop count ({hops}). Path may have reliability issues. "
                "Consider adding relay node for better direct path."
            )

        # Age issues
        if age_score < 50 and age_seconds is not None:
            if age_seconds > 3600:
                hours = age_seconds / 3600
                recommendations.append(
                    f"Link data is stale ({hours:.1f}h old). Node may be offline "
                    "or out of range."
                )
            else:
                recommendations.append(
                    "Link hasn't been updated recently. Check node activity."
                )

        # Stability issues
        if stability_score < 50:
            recommendations.append(
                "Link stability is low. This could indicate intermittent "
                "connectivity or node issues."
            )

        return recommendations


def compute_link_score(
    snr: float = None,
    rssi: int = None,
    hops: int = 1,
    age_seconds: float = None,
    announce_count: int = None,
    **kwargs
) -> LinkScore:
    """
    Convenience function to compute link quality score.

    Args:
        snr: Signal-to-Noise Ratio in dB
        rssi: Received Signal Strength in dBm
        hops: Number of hops in path
        age_seconds: Seconds since last update
        announce_count: Number of announcements received
        **kwargs: Additional arguments passed to scorer

    Returns:
        LinkScore with quality assessment
    """
    scorer = LinkQualityScorer()
    return scorer.score(
        snr=snr,
        rssi=rssi,
        hops=hops,
        age_seconds=age_seconds,
        announce_count=announce_count,
        **kwargs
    )


def score_topology_edges(topology) -> Dict[str, LinkScore]:
    """
    Score all edges in a network topology.

    Args:
        topology: NetworkTopology instance

    Returns:
        Dict mapping edge IDs to LinkScore objects
    """
    scorer = LinkQualityScorer()
    scores = {}

    try:
        topo_dict = topology.to_dict()

        for edge in topo_dict.get("edges", []):
            edge_id = f"{edge.get('source_id', '')}_{edge.get('dest_id', '')}"

            score = scorer.score(
                snr=edge.get("snr"),
                rssi=edge.get("rssi"),
                hops=edge.get("hops", 1),
                announce_count=edge.get("announce_count"),
            )

            scores[edge_id] = score

    except Exception as e:
        logger.warning(f"Error scoring topology edges: {e}")

    return scores


class LinkQualityTracker:
    """
    Tracks link quality scores over time for trend analysis.

    Maintains rolling history of scores for each link and provides
    trend detection and alerting.
    """

    def __init__(self, history_size: int = 100):
        """
        Initialize tracker.

        Args:
            history_size: Maximum samples to keep per link
        """
        self._history: Dict[str, List[Tuple[datetime, LinkScore]]] = {}
        self._history_size = history_size
        self._scorer = LinkQualityScorer()

    def record(self, link_id: str, score: LinkScore = None, **kwargs) -> LinkScore:
        """
        Record a link quality measurement.

        Args:
            link_id: Unique identifier for the link
            score: Pre-computed LinkScore (optional)
            **kwargs: Arguments for scorer if score not provided

        Returns:
            The recorded LinkScore
        """
        if score is None:
            score = self._scorer.score(**kwargs)

        if link_id not in self._history:
            self._history[link_id] = []

        self._history[link_id].append((datetime.now(), score))

        # Trim to history size
        if len(self._history[link_id]) > self._history_size:
            self._history[link_id] = self._history[link_id][-self._history_size:]

        return score

    def get_trend(self, link_id: str, window: int = 10) -> Optional[str]:
        """
        Get quality trend for a link.

        Args:
            link_id: Link identifier
            window: Number of recent samples to analyze

        Returns:
            "improving", "degrading", "stable", or None if insufficient data
        """
        if link_id not in self._history:
            return None

        history = self._history[link_id]
        if len(history) < window:
            return None

        recent = history[-window:]
        scores = [s.score for _, s in recent]

        # Calculate linear regression slope
        n = len(scores)
        sum_x = sum(range(n))
        sum_y = sum(scores)
        sum_xy = sum(i * s for i, s in enumerate(scores))
        sum_xx = sum(i * i for i in range(n))

        # Slope of least squares fit
        denominator = n * sum_xx - sum_x * sum_x
        if denominator == 0:
            return "stable"

        slope = (n * sum_xy - sum_x * sum_y) / denominator

        # Threshold for trend detection
        if slope > 2.0:
            return "improving"
        if slope < -2.0:
            return "degrading"
        return "stable"

    def get_average(self, link_id: str, window: int = 10) -> Optional[float]:
        """Get average score over recent window."""
        if link_id not in self._history:
            return None

        history = self._history[link_id]
        if not history:
            return None

        recent = history[-window:]
        scores = [s.score for _, s in recent]
        return sum(scores) / len(scores)

    def get_stats(self, link_id: str) -> Optional[Dict[str, Any]]:
        """
        Get comprehensive statistics for a link.

        Returns:
            Dict with min, max, avg, current, trend, samples
        """
        if link_id not in self._history:
            return None

        history = self._history[link_id]
        if not history:
            return None

        scores = [s.score for _, s in history]

        return {
            "current": scores[-1],
            "min": min(scores),
            "max": max(scores),
            "avg": sum(scores) / len(scores),
            "trend": self.get_trend(link_id),
            "samples": len(scores),
            "first_seen": history[0][0].isoformat(),
            "last_seen": history[-1][0].isoformat(),
        }

    def get_alerts(self, threshold: float = 40.0) -> List[Dict[str, Any]]:
        """
        Get alerts for links below quality threshold.

        Args:
            threshold: Quality score threshold (default 40 = poor)

        Returns:
            List of alert dicts with link_id, score, trend, recommendations
        """
        alerts = []

        for link_id, history in self._history.items():
            if not history:
                continue

            _, latest = history[-1]
            if latest.score < threshold:
                alerts.append({
                    "link_id": link_id,
                    "score": latest.score,
                    "quality": latest.quality.value,
                    "trend": self.get_trend(link_id),
                    "recommendations": latest.recommendations,
                })

        # Sort by score (worst first)
        return sorted(alerts, key=lambda a: a["score"])
