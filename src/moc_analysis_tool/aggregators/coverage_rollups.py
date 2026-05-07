"""Coverage rollups — feed the hero map.

Emits one entry per node with position, sized by activity (packet count
in the window) and colored by avg SNR. Nodes without position are
intentionally dropped — they can't be plotted on the map.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from moc_analysis_tool.aggregators.node_rollups import NodeRollupSet


@dataclass
class CoveragePin:
    """One pin on the hero map."""
    node_id: str
    name: str
    short_name: Optional[str]
    lat: float
    lon: float
    altitude: Optional[float]
    packet_count: int
    avg_snr: Optional[float]
    role: str
    hardware: str


def collect_coverage_pins(rollup_set: NodeRollupSet) -> List[CoveragePin]:
    """Extract hero-map pins from a NodeRollupSet.

    Drops anything without (lat, lon). Sorted by packet_count descending
    so renderers can take the top-N for emphasis if they want.
    """
    pins: List[CoveragePin] = []
    for r in rollup_set.values():
        if not r.has_position:
            continue
        pins.append(CoveragePin(
            node_id=r.node_id,
            name=r.name,
            short_name=r.short_name,
            lat=r.last_lat,
            lon=r.last_lon,
            altitude=r.last_altitude,
            packet_count=r.packet_count,
            avg_snr=r.avg_snr,
            role=r.role,
            hardware=r.hardware,
        ))
    pins.sort(key=lambda p: p.packet_count, reverse=True)
    return pins
