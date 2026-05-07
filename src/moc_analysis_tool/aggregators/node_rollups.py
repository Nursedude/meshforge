"""Node rollups — one row per node, ready to feed leaderboards/renderers.

Joins ``presentation_capture.packets`` (per-packet activity from the
diag24h log) with ``node_history.nodes`` (directory: name, hardware,
role, last position) and ``node_history.node_observations`` (SNR
samples, uptime), within an analysis time window.

Why this lives in aggregators/: leaderboards consume rolled-up rows;
they shouldn't be writing SQL. Renderers (hero map, leaderboard panels)
consume the same rolled-up rows. One source of truth.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from utils.db_helpers import connect_tuned

from moc_analysis_tool.spec import AnalysisSpec


@dataclass
class NodeRollup:
    """Aggregated stats for a single node across the analysis window.

    Some fields are nullable because the join doesn't always land —
    e.g. a node heard via RF that never made it into the directory has
    a packet_count but no name/hardware/position. Renderers must handle
    None gracefully.
    """

    node_id: str

    # From node_history.nodes
    name: str = ""
    role: str = ""
    hardware: str = ""
    last_lat: Optional[float] = None
    last_lon: Optional[float] = None
    last_altitude: Optional[float] = None
    first_seen: Optional[float] = None
    last_seen: Optional[float] = None

    # From presentation_capture.packets (window-bound)
    packet_count: int = 0
    first_packet_ts: Optional[float] = None
    last_packet_ts: Optional[float] = None
    port_distribution: Dict[str, int] = field(default_factory=dict)

    # From node_history.node_observations (window-bound)
    obs_count: int = 0
    avg_snr: Optional[float] = None
    snr_sample_count: int = 0
    uptime_ratio: Optional[float] = None  # SUM(is_online) / COUNT(*)

    # Convenience
    has_position: bool = False
    short_name: Optional[str] = None  # parsed from name when name has the
                                       # "Short (Long)" Meshtastic convention


@dataclass
class NodeRollupSet:
    """Container holding all NodeRollups for one analysis run."""
    rollups: Dict[str, NodeRollup] = field(default_factory=dict)
    spec: Optional[AnalysisSpec] = None

    def add(self, r: NodeRollup) -> None:
        self.rollups[r.node_id] = r

    def get(self, node_id: str) -> Optional[NodeRollup]:
        return self.rollups.get(node_id)

    def values(self) -> List[NodeRollup]:
        return list(self.rollups.values())

    def __len__(self) -> int:
        return len(self.rollups)


def _open_ro(path) -> sqlite3.Connection:
    return connect_tuned(f"file:{path}?mode=ro", uri=True)


def _parse_short_long(name: str) -> Optional[str]:
    """Extract the short_name from Meshtastic ``Short (Long)`` form."""
    if not name:
        return None
    name = name.strip()
    if " (" in name and name.endswith(")"):
        return name.split(" (", 1)[0]
    return None




def collect_node_rollups(
    spec: AnalysisSpec,
    *,
    node_id_filter: Optional[Set[str]] = None,
) -> NodeRollupSet:
    """Build per-node rollups for the analysis window.

    Args:
        spec: AnalysisSpec carrying window + DB paths.
        node_id_filter: Optional set restricting which nodes are rolled
            up. Pass the result of ``collect_region_node_ids`` to focus
            on a region. None means "every node observed in either DB".

    Returns:
        A NodeRollupSet containing one rollup per qualifying node.
    """
    cap_path = spec.resolved_db_path()
    nh_path = spec.resolved_node_history_path()
    win_start = spec.window.start.timestamp()
    win_end = spec.window.end.timestamp()

    rollups: Dict[str, NodeRollup] = {}

    # ── Step 1: per-source packet stats from presentation_capture ─────
    with _open_ro(cap_path) as conn:
        rows = conn.execute(
            """
            SELECT source, COUNT(*) AS pkts,
                   MIN(timestamp) AS first_ts, MAX(timestamp) AS last_ts
            FROM packets
            WHERE timestamp BETWEEN ? AND ?
            GROUP BY source
            """,
            (win_start, win_end),
        )
        for source, pkts, first_ts, last_ts in rows:
            if node_id_filter is not None and source not in node_id_filter:
                continue
            rollups[source] = NodeRollup(
                node_id=source,
                packet_count=int(pkts),
                first_packet_ts=float(first_ts) if first_ts else None,
                last_packet_ts=float(last_ts) if last_ts else None,
            )

        # Per-node port_name distribution
        rows = conn.execute(
            """
            SELECT source, port_name, COUNT(*) AS n
            FROM packets
            WHERE timestamp BETWEEN ? AND ?
            GROUP BY source, port_name
            """,
            (win_start, win_end),
        )
        for source, port_name, n in rows:
            r = rollups.get(source)
            if r is None:
                continue
            r.port_distribution[port_name] = int(n)

    # ── Step 2: enrich with directory info from node_history.nodes ────
    with _open_ro(nh_path) as conn:
        rows = conn.execute(
            """
            SELECT node_id, name, role, hardware,
                   last_lat, last_lon, last_altitude,
                   first_seen, last_seen, obs_count
            FROM nodes
            WHERE network = 'meshtastic'
            """
        )
        for (node_id, name, role, hardware, lat, lon, alt,
             first_seen, last_seen, obs_count) in rows:
            if node_id_filter is not None and node_id not in node_id_filter:
                continue
            r = rollups.get(node_id)
            if r is None:
                # Node in directory but no packets in window. Add a
                # zero-packet rollup so leaderboards that don't depend
                # on packet count (uptime, position) can still rank it.
                r = NodeRollup(node_id=node_id)
                rollups[node_id] = r
            r.name = name or ""
            r.role = role or ""
            r.hardware = hardware or ""
            r.last_lat = float(lat) if lat is not None else None
            r.last_lon = float(lon) if lon is not None else None
            r.last_altitude = float(alt) if alt is not None else None
            r.first_seen = float(first_seen) if first_seen else None
            r.last_seen = float(last_seen) if last_seen else None
            r.obs_count = int(obs_count or 0)
            r.has_position = (
                r.last_lat is not None and r.last_lon is not None
                and not (r.last_lat == 0.0 and r.last_lon == 0.0)
            )
            r.short_name = _parse_short_long(r.name)

        # ── Step 3: SNR + uptime from node_observations (window-bound)
        # Restrict to bare ``!aabbccdd`` rows (network='meshtastic' AND
        # no namespace prefix). The ``mesh_!`` prefixed rows in this
        # table are federation echoes from other fleet boxes — they
        # describe the same physical node but with a different
        # online/offline pattern (when the federating box couldn't
        # reach that node, it logged offline, biasing uptime down).
        # The bare rows are the local view, which is what the talk
        # cares about ("what did vail hear and see").
        rows = conn.execute(
            """
            SELECT node_id,
                   AVG(snr) AS avg_snr,
                   COUNT(snr) AS snr_n,
                   AVG(is_online) AS uptime_ratio
            FROM node_observations
            WHERE timestamp BETWEEN ? AND ?
              AND network = 'meshtastic'
              AND node_id LIKE '!%'
            GROUP BY node_id
            """,
            (win_start, win_end),
        )
        for node_id, avg_snr, snr_n, uptime_ratio in rows:
            if node_id_filter is not None and node_id not in node_id_filter:
                continue
            r = rollups.get(node_id)
            if r is None:
                continue
            r.avg_snr = float(avg_snr) if avg_snr is not None else None
            r.snr_sample_count = int(snr_n or 0)
            r.uptime_ratio = float(uptime_ratio) if uptime_ratio is not None else None

    out = NodeRollupSet(spec=spec)
    for r in rollups.values():
        out.add(r)
    return out
