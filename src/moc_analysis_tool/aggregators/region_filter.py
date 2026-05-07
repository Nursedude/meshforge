"""Region filter — restricts an analysis to nodes within a region bbox.

A node qualifies if its last known position from ``node_history.nodes``
falls inside the preset's bounding box. Nodes without position data
(``last_lat`` IS NULL or 0) are excluded by default — they're heard via
RF but not place-able on the map.

The optional ``include_positionless`` flag is offered for leaderboards
that don't need geography (uptime, raw activity counts) — those should
not silently drop a Hawaii-radio node just because it never broadcast a
GPS fix.
"""

from __future__ import annotations

import sqlite3
from typing import Optional, Set

from utils.db_helpers import connect_tuned
from utils.region_presets import REGION_PRESETS, point_in_region

from moc_analysis_tool.spec import AnalysisSpec


def _open_ro(path) -> sqlite3.Connection:
    """Open a SQLite DB read-only via URI mode."""
    return connect_tuned(f"file:{path}?mode=ro", uri=True)


def collect_region_node_ids(
    spec: AnalysisSpec,
    *,
    include_positionless: bool = False,
) -> Set[str]:
    """Return the set of node_id strings that match ``spec.region_key``.

    When ``spec.region_key`` is ``None``, every observed node_id is
    returned (no spatial filter). When it's set, the node's last known
    position from ``node_history.nodes`` is checked against the preset's
    bounding box.

    Args:
        spec: AnalysisSpec carrying region_key + node_history path.
        include_positionless:
            If True, nodes without a recorded last_lat/last_lon are also
            included. Useful for leaderboards that don't depend on
            geography. Defaults to False to keep the hero map honest.

    Returns:
        A set of canonical ``!aabbccdd`` strings. Empty if the region key
        is unknown or no nodes match.
    """
    nh_path = spec.resolved_node_history_path()
    region_key = spec.region_key

    if region_key is not None and region_key not in REGION_PRESETS:
        return set()

    out: Set[str] = set()
    with _open_ro(nh_path) as conn:
        rows = conn.execute(
            "SELECT node_id, last_lat, last_lon FROM nodes "
            "WHERE network = 'meshtastic'"
        )
        for node_id, lat, lon in rows:
            if lat is None or lon is None:
                if include_positionless or region_key is None:
                    out.add(node_id)
                continue
            if region_key is None:
                out.add(node_id)
                continue
            if point_in_region(region_key, lat, lon):
                out.add(node_id)
    return out


def collect_all_packet_sources(spec: AnalysisSpec) -> Set[str]:
    """Return every distinct ``source`` seen in presentation_capture.

    Useful when the leaderboard needs to consider RF-heard nodes that
    never made it into the directory (e.g. heard but never tracked).
    """
    db = spec.resolved_db_path()
    with _open_ro(db) as conn:
        return {
            row[0]
            for row in conn.execute("SELECT DISTINCT source FROM packets")
        }
