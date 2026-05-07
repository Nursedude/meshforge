"""Most-active senders leaderboard — top-N by raw packet count."""

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
    rows_in = sorted(
        (r for r in rollup_set.values() if r.packet_count > 0),
        key=lambda r: r.packet_count,
        reverse=True,
    )[:spec.top_n]

    rows = []
    for i, r in enumerate(rows_in, start=1):
        snr_str = f"{r.avg_snr:+.1f} dB" if r.avg_snr is not None else "—"
        rows.append(LeaderRow(
            rank=i,
            node_id=r.node_id,
            display_name=display_name_for(r),
            primary_value=r.packet_count,
            primary_label=f"{r.packet_count:,} pkts",
            secondary={
                "avg_snr": r.avg_snr,
                "avg_snr_str": snr_str,
                "role": r.role,
                "hardware": r.hardware,
                "port_distribution": dict(r.port_distribution),
                "ports_seen": len(r.port_distribution),
            },
        ))

    return Leaderboard(
        metric="most_active",
        title="Most Active Senders",
        subtitle=f"Top {len(rows)} by packets received over {spec.window.hours():.0f} h",
        rows=rows,
        spec=spec,
    )
