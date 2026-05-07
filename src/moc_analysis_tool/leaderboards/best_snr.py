"""Best avg-SNR leaderboard — top-N by mean SNR with sample-count floor.

The ``snr_min_samples`` floor (default 30 from AnalysisSpec) prevents
one-shot anomalies from winning the chart — a node we heard once at
+12 dB shouldn't outrank a node we heard 500 times at +9 dB.
"""

from __future__ import annotations

from moc_analysis_tool.aggregators.node_rollups import NodeRollupSet
from moc_analysis_tool.leaderboards.base import (
    Leaderboard, LeaderRow, display_name_for,
)
from moc_analysis_tool.spec import AnalysisSpec


def build(
    rollup_set: NodeRollupSet,
    spec: AnalysisSpec,
) -> Leaderboard:
    candidates = [
        r for r in rollup_set.values()
        if r.avg_snr is not None and r.snr_sample_count >= spec.snr_min_samples
    ]
    candidates.sort(key=lambda r: r.avg_snr, reverse=True)
    top = candidates[:spec.top_n]

    rows = []
    for i, r in enumerate(top, start=1):
        rows.append(LeaderRow(
            rank=i,
            node_id=r.node_id,
            display_name=display_name_for(r),
            primary_value=r.avg_snr,
            primary_label=f"{r.avg_snr:+.1f} dB",
            secondary={
                "snr_samples": r.snr_sample_count,
                "packet_count": r.packet_count,
                "role": r.role,
                "hardware": r.hardware,
            },
        ))

    return Leaderboard(
        metric="best_snr",
        title="Best Average SNR",
        subtitle=f"Top {len(rows)} of {len(candidates)} qualifying nodes",
        rows=rows,
        spec=spec,
        note=f"Minimum {spec.snr_min_samples} SNR samples required to qualify.",
    )
