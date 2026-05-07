"""PortNum rollups — network-wide app-mix + per-node pivot.

Two views emit:

- ``collect_network_portnum_distribution`` — total packets per
  ``port_name`` across the whole region/window. Drives the app-mix
  treemap slide.

- The per-node pivot is already populated on ``NodeRollup.port_distribution``
  by ``node_rollups.collect_node_rollups``; no separate function needed.
"""

from __future__ import annotations

import sqlite3
from collections import OrderedDict
from typing import Dict, Optional, Set

from utils.db_helpers import connect_tuned

from moc_analysis_tool.spec import AnalysisSpec


def _open_ro(path) -> sqlite3.Connection:
    return connect_tuned(f"file:{path}?mode=ro", uri=True)


def collect_network_portnum_distribution(
    spec: AnalysisSpec,
    *,
    node_id_filter: Optional[Set[str]] = None,
) -> "OrderedDict[str, int]":
    """Total packet counts grouped by port_name within the window.

    Returns an OrderedDict sorted high-to-low so renderers get a stable
    iteration order.
    """
    db = spec.resolved_db_path()
    win_start = spec.window.start.timestamp()
    win_end = spec.window.end.timestamp()

    counts: Dict[str, int] = {}
    with _open_ro(db) as conn:
        if node_id_filter is None:
            rows = conn.execute(
                "SELECT port_name, COUNT(*) FROM packets "
                "WHERE timestamp BETWEEN ? AND ? GROUP BY port_name",
                (win_start, win_end),
            )
            for port_name, n in rows:
                counts[port_name] = int(n)
        else:
            # SQLite doesn't bind sets; build a temp filter table.
            # Fall back to Python-side filter for small filter sets.
            rows = conn.execute(
                "SELECT source, port_name, COUNT(*) FROM packets "
                "WHERE timestamp BETWEEN ? AND ? GROUP BY source, port_name",
                (win_start, win_end),
            )
            for source, port_name, n in rows:
                if source in node_id_filter:
                    counts[port_name] = counts.get(port_name, 0) + int(n)

    return OrderedDict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))
