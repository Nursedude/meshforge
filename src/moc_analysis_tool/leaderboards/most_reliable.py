"""Most-reliable / longest-uptime leaderboard.

Ranks nodes by their ``is_online`` ratio in ``node_observations`` over
the window — the fraction of observation snapshots where the node was
considered alive. A node observed continuously every snapshot wins;
intermittent nodes drop. Ties broken by total observation count
(longer-tracked node wins).
"""

from __future__ import annotations

from moc_analysis_tool.aggregators.node_rollups import NodeRollupSet
from moc_analysis_tool.leaderboards.base import (
    Leaderboard, LeaderRow, display_name_for,
)
from moc_analysis_tool.spec import AnalysisSpec


# Minimum observation count before a node is eligible. Same spirit as the
# SNR floor — a node observed only once shouldn't claim 100% uptime.
_DEFAULT_MIN_OBS = 50


def build(
    rollup_set: NodeRollupSet,
    spec: AnalysisSpec,
    min_obs: int = _DEFAULT_MIN_OBS,
) -> Leaderboard:
    candidates = [
        r for r in rollup_set.values()
        if r.uptime_ratio is not None and r.obs_count >= min_obs
    ]
    candidates.sort(
        key=lambda r: (r.uptime_ratio or 0, r.obs_count),
        reverse=True,
    )
    top = candidates[:spec.top_n]

    rows = []
    for i, r in enumerate(top, start=1):
        pct = (r.uptime_ratio or 0) * 100.0
        rows.append(LeaderRow(
            rank=i,
            node_id=r.node_id,
            display_name=display_name_for(r),
            primary_value=r.uptime_ratio,
            primary_label=f"{pct:.1f}% online",
            secondary={
                "obs_count": r.obs_count,
                "packet_count": r.packet_count,
                "role": r.role,
                "hardware": r.hardware,
            },
        ))

    return Leaderboard(
        metric="most_reliable",
        title="Most Reliable Nodes",
        subtitle=f"Top {len(rows)} of {len(candidates)} qualifying nodes",
        rows=rows,
        spec=spec,
        note=f"Minimum {min_obs} observation snapshots required to qualify.",
    )
