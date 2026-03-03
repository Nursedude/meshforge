"""
Multi-hop path loss calculator — cumulative analysis across relay chains.

Models Meshtastic's store-and-forward mesh behavior where each relay node
fully demodulates and retransmits at full power. Key insight: overall path
reliability is determined by the weakest single hop, not cumulative signal loss.

Key calculations:
- Per-hop FSPL and link margin
- End-to-end success probability (product of per-hop probabilities)
- Total latency (airtime + relay processing per hop)
- Channel occupancy (each retransmission uses airtime)
- Weakest link identification
- Hop count penalty (Meshtastic max 7 hops)

Usage:
    from utils.multihop import HopAnalyzer, RelayNode

    nodes = [
        RelayNode(lat=21.3069, lon=-157.8583, elevation_m=10, name="Base"),
        RelayNode(lat=21.3200, lon=-157.8400, elevation_m=450, name="Ridge"),
        RelayNode(lat=21.3400, lon=-157.8200, elevation_m=5, name="Remote"),
    ]
    analyzer = HopAnalyzer(preset='LONG_FAST')
    path = analyzer.analyze_path(nodes)
    print(f"Success probability: {path.success_probability:.1%}")
    print(f"Weakest link: {path.weakest_hop.description}")
"""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.rf.preset_impact import (
    PresetAnalyzer,
    PRESET_PARAMS,
    DEFAULT_TX_POWER_DBM,
    DEFAULT_FREQ_MHZ,
    DEFAULT_PAYLOAD_BYTES,
)
from core.rf.calculator import (
    DeployEnvironment,
    log_distance_path_loss,
)


# Meshtastic protocol constants
MAX_HOPS = 7  # Maximum hop limit in Meshtastic firmware
RELAY_PROCESSING_MS = 50.0  # Estimated relay processing delay (ms)
FADE_MARGIN_DB = 10.0  # Standard fade margin for reliability

# Success probability model: sigmoid based on link margin
# At 0 dB margin: 50% success, +10 dB: ~95%, -10 dB: ~5%
SIGMOID_STEEPNESS = 0.5  # Controls how quickly probability transitions


@dataclass
class RelayNode:
    """A node in the relay chain.

    Attributes:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.
        elevation_m: Ground elevation in meters (ASL).
        antenna_height_m: Antenna height above ground.
        tx_power_dbm: Transmit power (None uses analyzer default).
        name: Human-readable node identifier.
    """
    lat: float
    lon: float
    elevation_m: float = 0.0
    antenna_height_m: float = 2.0
    tx_power_dbm: Optional[int] = None
    name: str = ""

    @property
    def total_height_m(self) -> float:
        """Total antenna height above sea level."""
        return self.elevation_m + self.antenna_height_m


@dataclass
class HopResult:
    """Analysis result for a single hop between two nodes."""
    from_node: str
    to_node: str
    distance_km: float
    fspl_db: float
    link_budget_db: float
    link_margin_db: float
    success_probability: float
    airtime_ms: float
    latency_ms: float  # airtime + processing
    los_clear: bool = True  # Assume LOS unless terrain analysis says otherwise
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            'from': self.from_node,
            'to': self.to_node,
            'distance_km': round(self.distance_km, 2),
            'fspl_db': round(self.fspl_db, 1),
            'link_budget_db': round(self.link_budget_db, 1),
            'link_margin_db': round(self.link_margin_db, 1),
            'success_probability': round(self.success_probability, 3),
            'airtime_ms': round(self.airtime_ms, 1),
            'latency_ms': round(self.latency_ms, 1),
            'los_clear': self.los_clear,
        }


@dataclass
class PathResult:
    """Complete multi-hop path analysis."""
    hops: List[HopResult]
    total_distance_km: float
    total_latency_ms: float
    success_probability: float  # Product of per-hop probabilities
    channel_occupancy_ms: float  # Total airtime consumed (all hops)
    hop_count: int
    weakest_hop: Optional[HopResult] = None
    within_hop_limit: bool = True
    preset_name: str = ""
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'hops': [h.to_dict() for h in self.hops],
            'summary': {
                'total_distance_km': round(self.total_distance_km, 2),
                'total_latency_ms': round(self.total_latency_ms, 1),
                'success_probability': round(self.success_probability, 3),
                'channel_occupancy_ms': round(self.channel_occupancy_ms, 1),
                'hop_count': self.hop_count,
                'within_hop_limit': self.within_hop_limit,
                'weakest_link': self.weakest_hop.description if self.weakest_hop else '',
                'preset': self.preset_name,
                'warnings': self.warnings,
            }
        }


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two points in km.

    Args:
        lat1, lon1: First point in decimal degrees.
        lat2, lon2: Second point in decimal degrees.

    Returns:
        Distance in kilometers.
    """
    R = 6371.0  # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def fspl_db(distance_m: float, freq_mhz: float) -> float:
    """Calculate Free Space Path Loss in dB.

    Formula: FSPL = 20*log10(d) + 20*log10(f) - 27.55

    Args:
        distance_m: Distance in meters (must be > 0).
        freq_mhz: Frequency in MHz.

    Returns:
        Path loss in dB.

    Raises:
        ValueError: If distance is not positive.
    """
    if distance_m <= 0:
        raise ValueError("Distance must be positive")
    return 20 * math.log10(distance_m) + 20 * math.log10(freq_mhz) - 27.55


def margin_to_probability(margin_db: float) -> float:
    """Convert link margin to success probability using sigmoid model.

    Models the probability of successful packet reception based on
    how much signal margin exists above the sensitivity threshold.

    - At 0 dB margin: 50% success (at sensitivity threshold)
    - At +10 dB margin: ~99% success
    - At -10 dB margin: ~1% success

    Args:
        margin_db: Link margin in dB (positive = above sensitivity).

    Returns:
        Probability of successful reception (0.0 to 1.0).
    """
    return 1.0 / (1.0 + math.exp(-SIGMOID_STEEPNESS * margin_db))


class HopAnalyzer:
    """Analyzes multi-hop paths in a Meshtastic mesh network.

    Combines environment-aware propagation with LoRa preset parameters to
    determine per-hop link quality and end-to-end path reliability.

    Args:
        preset: Meshtastic LoRa preset name (e.g., 'LONG_FAST').
        tx_power_dbm: Default transmit power for nodes.
        freq_mhz: Operating frequency in MHz.
        payload_bytes: Payload size for airtime calculation.
        tx_gain_dbi: Transmit antenna gain.
        rx_gain_dbi: Receive antenna gain.
        environment: Deployment environment for path loss modeling.
    """

    def __init__(self,
                 preset: str = 'LONG_FAST',
                 tx_power_dbm: int = DEFAULT_TX_POWER_DBM,
                 freq_mhz: float = DEFAULT_FREQ_MHZ,
                 payload_bytes: int = DEFAULT_PAYLOAD_BYTES,
                 tx_gain_dbi: float = 2.15,
                 rx_gain_dbi: float = 2.15,
                 environment: DeployEnvironment = DeployEnvironment.FREE_SPACE):
        if preset not in PRESET_PARAMS:
            raise ValueError(f"Unknown preset: {preset}")

        self.preset = preset
        self.tx_power_dbm = tx_power_dbm
        self.freq_mhz = freq_mhz
        self.payload_bytes = payload_bytes
        self.tx_gain_dbi = tx_gain_dbi
        self.rx_gain_dbi = rx_gain_dbi
        self.environment = environment

        # Pre-calculate preset-dependent values
        self._analyzer = PresetAnalyzer(
            tx_power_dbm=tx_power_dbm,
            tx_gain_dbi=tx_gain_dbi,
            rx_gain_dbi=rx_gain_dbi,
            freq_mhz=freq_mhz,
            payload_bytes=payload_bytes,
        )
        self._impact = self._analyzer.analyze_preset(preset)

    @property
    def sensitivity_dbm(self) -> float:
        """Receiver sensitivity for the configured preset."""
        return self._impact.sensitivity_dbm

    @property
    def airtime_ms(self) -> float:
        """Packet airtime for the configured preset and payload."""
        return self._impact.airtime_ms

    def analyze_hop(self, from_node: RelayNode, to_node: RelayNode) -> HopResult:
        """Analyze a single hop between two nodes.

        Uses environment-aware log-distance path loss model when an
        environment is set, falling back to FSPL for FREE_SPACE.

        Args:
            from_node: Transmitting node.
            to_node: Receiving node.

        Returns:
            HopResult with link analysis.
        """
        # Distance
        dist_km = haversine_km(from_node.lat, from_node.lon,
                               to_node.lat, to_node.lon)
        dist_m = max(dist_km * 1000, 1.0)  # Minimum 1m to avoid log(0)

        # Environment-aware path loss (log-distance model)
        path_loss = log_distance_path_loss(dist_m, self.freq_mhz,
                                           environment=self.environment)

        # Transmit power (node-specific or default)
        tx_power = from_node.tx_power_dbm or self.tx_power_dbm

        # Link budget: TX power + gains - sensitivity
        link_budget = tx_power + self.tx_gain_dbi + self.rx_gain_dbi - self.sensitivity_dbm

        # Link margin: how much spare dB we have
        margin = link_budget - path_loss

        # Success probability from margin
        prob = margin_to_probability(margin)

        # Latency: airtime + relay processing
        latency = self.airtime_ms + RELAY_PROCESSING_MS

        # Description
        from_name = from_node.name or f"({from_node.lat:.4f},{from_node.lon:.4f})"
        to_name = to_node.name or f"({to_node.lat:.4f},{to_node.lon:.4f})"
        desc = f"{from_name} → {to_name}: {dist_km:.1f} km, margin {margin:.1f} dB"

        return HopResult(
            from_node=from_name,
            to_node=to_name,
            distance_km=dist_km,
            fspl_db=path_loss,
            link_budget_db=link_budget,
            link_margin_db=margin,
            success_probability=prob,
            airtime_ms=self.airtime_ms,
            latency_ms=latency,
            description=desc,
        )

    def analyze_path(self, nodes: List[RelayNode]) -> PathResult:
        """Analyze a complete multi-hop path.

        Calculates per-hop metrics and aggregates end-to-end reliability,
        latency, and channel occupancy.

        Args:
            nodes: Ordered list of relay nodes (source first, destination last).
                   Minimum 2 nodes required.

        Returns:
            PathResult with complete path analysis.

        Raises:
            ValueError: If fewer than 2 nodes provided.
        """
        if len(nodes) < 2:
            raise ValueError("Path requires at least 2 nodes")

        hops: List[HopResult] = []
        warnings: List[str] = []

        for i in range(len(nodes) - 1):
            hop = self.analyze_hop(nodes[i], nodes[i + 1])
            hops.append(hop)

        # Aggregate metrics
        hop_count = len(hops)
        total_distance = sum(h.distance_km for h in hops)

        # End-to-end success probability (independent hops)
        success_prob = 1.0
        for h in hops:
            success_prob *= h.success_probability

        # Total latency: first hop is just airtime, subsequent add processing
        total_latency = hops[0].airtime_ms
        for h in hops[1:]:
            total_latency += h.latency_ms

        # Channel occupancy: each hop transmits the full packet
        channel_occupancy = sum(h.airtime_ms for h in hops)

        # Weakest link
        weakest = min(hops, key=lambda h: h.link_margin_db)

        # Hop limit check
        within_limit = hop_count <= MAX_HOPS
        if not within_limit:
            warnings.append(
                f"Path exceeds Meshtastic hop limit ({hop_count} > {MAX_HOPS})")

        # Warning for weak links
        for h in hops:
            if h.link_margin_db < 0:
                warnings.append(
                    f"Hop {h.from_node}→{h.to_node} has negative margin "
                    f"({h.link_margin_db:.1f} dB)")
            elif h.link_margin_db < FADE_MARGIN_DB:
                warnings.append(
                    f"Hop {h.from_node}→{h.to_node} below fade margin "
                    f"({h.link_margin_db:.1f} dB < {FADE_MARGIN_DB:.0f} dB)")

        return PathResult(
            hops=hops,
            total_distance_km=total_distance,
            total_latency_ms=total_latency,
            success_probability=success_prob,
            channel_occupancy_ms=channel_occupancy,
            hop_count=hop_count,
            weakest_hop=weakest,
            within_hop_limit=within_limit,
            preset_name=self.preset,
            warnings=warnings,
        )

    def compare_presets_for_path(self, nodes: List[RelayNode],
                                 presets: Optional[List[str]] = None
                                 ) -> List[PathResult]:
        """Compare how different presets perform on the same path.

        Args:
            nodes: Relay path nodes.
            presets: List of preset names to compare (default: all).

        Returns:
            List of PathResult, one per preset, sorted by success probability.
        """
        if presets is None:
            presets = list(PRESET_PARAMS.keys())

        results = []
        for preset_name in presets:
            analyzer = HopAnalyzer(
                preset=preset_name,
                tx_power_dbm=self.tx_power_dbm,
                freq_mhz=self.freq_mhz,
                payload_bytes=self.payload_bytes,
                tx_gain_dbi=self.tx_gain_dbi,
                rx_gain_dbi=self.rx_gain_dbi,
                environment=self.environment,
            )
            results.append(analyzer.analyze_path(nodes))

        results.sort(key=lambda r: r.success_probability, reverse=True)
        return results

    def find_optimal_relay(self, source: RelayNode, destination: RelayNode,
                           candidates: List[RelayNode]) -> Optional[RelayNode]:
        """Find the best single relay node from a list of candidates.

        Evaluates which candidate relay gives the highest end-to-end
        success probability for a source→relay→destination path.

        Args:
            source: Source node.
            destination: Destination node.
            candidates: Potential relay nodes to evaluate.

        Returns:
            Best relay node, or None if direct path is better than all relays.
        """
        # Direct path baseline
        direct = self.analyze_path([source, destination])

        best_relay = None
        best_prob = direct.success_probability

        for candidate in candidates:
            relayed = self.analyze_path([source, candidate, destination])
            if relayed.success_probability > best_prob:
                best_prob = relayed.success_probability
                best_relay = candidate

        return best_relay


def format_path_report(result: PathResult) -> str:
    """Format a path analysis as a text report for TUI display.

    Args:
        result: PathResult from HopAnalyzer.analyze_path().

    Returns:
        Formatted multi-line string report.
    """
    lines = []
    lines.append("=" * 70)
    lines.append(f"  Multi-Hop Path Analysis — {result.preset_name}")
    lines.append(f"  {result.hop_count} hops, {result.total_distance_km:.1f} km total")
    lines.append("=" * 70)
    lines.append("")

    for i, hop in enumerate(result.hops, 1):
        prob_pct = hop.success_probability * 100
        margin_indicator = "OK" if hop.link_margin_db >= FADE_MARGIN_DB else (
            "WEAK" if hop.link_margin_db >= 0 else "FAIL")
        lines.append(
            f"  Hop {i}: {hop.from_node} → {hop.to_node}")
        lines.append(
            f"         {hop.distance_km:.1f} km  |  PL {hop.fspl_db:.1f} dB  |  "
            f"Margin {hop.link_margin_db:+.1f} dB [{margin_indicator}]  |  "
            f"P(success) {prob_pct:.0f}%")
        lines.append("")

    lines.append("-" * 70)
    lines.append(f"  End-to-end success: {result.success_probability * 100:.1f}%")
    lines.append(f"  Total latency:      {result.total_latency_ms:.0f} ms")
    lines.append(f"  Channel occupancy:  {result.channel_occupancy_ms:.0f} ms")
    if result.weakest_hop:
        lines.append(f"  Weakest link:       {result.weakest_hop.description}")
    if not result.within_hop_limit:
        lines.append(f"  WARNING: Exceeds {MAX_HOPS}-hop limit!")

    if result.warnings:
        lines.append("")
        lines.append("  Warnings:")
        for w in result.warnings:
            lines.append(f"    - {w}")

    lines.append("=" * 70)
    return "\n".join(lines)
