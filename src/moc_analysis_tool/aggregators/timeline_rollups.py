"""Timeline rollups — packets-per-hour ribbons for the timeline strip slide.

Buckets packets by hour, keyed by port_name, so the renderer can stack
the bars showing how the app mix evolves over the capture window.
"""

from __future__ import annotations

import math
import sqlite3
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from utils.db_helpers import connect_tuned

from moc_analysis_tool.spec import AnalysisSpec


@dataclass
class HourBucket:
    """One hour-aligned bucket of packets, per port_name."""
    hour_start: float          # epoch seconds, aligned to top of hour
    counts: Dict[str, int]     # port_name → packet count


def _open_ro(path) -> sqlite3.Connection:
    return connect_tuned(f"file:{path}?mode=ro", uri=True)


def collect_hourly_timeline(
    spec: AnalysisSpec,
    *,
    node_id_filter: Optional[Set[str]] = None,
) -> List[HourBucket]:
    """Return one HourBucket per hour the window covers.

    Hours with no packets still emit a bucket (with empty counts) so the
    timeline ribbon shows quiet stretches as gaps rather than gaps in
    the X axis.
    """
    db = spec.resolved_db_path()
    win_start = spec.window.start.timestamp()
    win_end = spec.window.end.timestamp()

    # Snap window to hour boundaries
    h_start = math.floor(win_start / 3600.0) * 3600.0
    h_end = math.ceil(win_end / 3600.0) * 3600.0

    by_hour: Dict[float, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

    with _open_ro(db) as conn:
        rows = conn.execute(
            """
            SELECT
                CAST(timestamp / 3600.0 AS INTEGER) * 3600 AS hour_bucket,
                port_name,
                source,
                COUNT(*) AS n
            FROM packets
            WHERE timestamp BETWEEN ? AND ?
            GROUP BY hour_bucket, port_name, source
            """,
            (win_start, win_end),
        )
        for hour_bucket, port_name, source, n in rows:
            if node_id_filter is not None and source not in node_id_filter:
                continue
            by_hour[float(hour_bucket)][port_name] += int(n)

    buckets: List[HourBucket] = []
    h = h_start
    while h < h_end:
        counts = OrderedDict(sorted(
            by_hour.get(h, {}).items(), key=lambda kv: kv[1], reverse=True
        ))
        buckets.append(HourBucket(hour_start=h, counts=dict(counts)))
        h += 3600.0
    return buckets
