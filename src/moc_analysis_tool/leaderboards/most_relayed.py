"""Backbone repeaters leaderboard — top ROUTER/ROUTER_LATE nodes.

The original plan was to rank by ``path_traces.node_id WHERE state =
'relayed'`` — but path_traces is populated by the live traffic
inspector, which isn't running on the canonical capture box. The
diag24h log we DO have doesn't carry per-hop relay info.

Honest pivot: rank nodes whose Meshtastic ``role`` is one of
``{ROUTER, ROUTER_LATE, REPEATER}`` by activity (packet count) within
the window. These are the operator-declared backbone of the mesh; the
ones doing the most work are the ones to highlight.

The slide footnote should make the methodology explicit so audience
members reading "backbone repeaters" don't assume we measured actual
relay-hop counts.
"""

from __future__ import annotations

from moc_analysis_tool.aggregators.node_rollups import NodeRollupSet
from moc_analysis_tool.leaderboards.base import (
    Leaderboard, LeaderRow, display_name_for,
)
from moc_analysis_tool.spec import AnalysisSpec


# Roles that participate in mesh-wide rebroadcast. Per Meshtastic role
# semantics (firmware): ROUTER and ROUTER_LATE re-emit broadcast packets
# they hear; REPEATER is the dedicated relay-only role.
_BACKBONE_ROLES = {"ROUTER", "ROUTER_LATE", "REPEATER"}


def build(
    rollup_set: NodeRollupSet,
    spec: AnalysisSpec,
) -> Leaderboard:
    candidates = [
        r for r in rollup_set.values()
        if r.role in _BACKBONE_ROLES
    ]
    # Sort by packet count, then SNR for tiebreaks (higher SNR wins
    # a tie — better placement matters for a backbone node).
    candidates.sort(
        key=lambda r: (r.packet_count, r.avg_snr or float("-inf")),
        reverse=True,
    )
    top = candidates[:spec.top_n]

    rows = []
    for i, r in enumerate(top, start=1):
        snr_str = f"{r.avg_snr:+.1f} dB" if r.avg_snr is not None else "—"
        rows.append(LeaderRow(
            rank=i,
            node_id=r.node_id,
            display_name=display_name_for(r),
            primary_value=r.packet_count,
            primary_label=f"{r.packet_count:,} pkts",
            secondary={
                "role": r.role,
                "avg_snr": r.avg_snr,
                "avg_snr_str": snr_str,
                "hardware": r.hardware,
                "altitude": r.last_altitude,
            },
        ))

    role_counts = {}
    for r in rollup_set.values():
        if r.role in _BACKBONE_ROLES:
            role_counts[r.role] = role_counts.get(r.role, 0) + 1

    return Leaderboard(
        metric="most_relayed",
        title="Backbone Repeaters",
        subtitle=(
            f"Top {len(rows)} of {len(candidates)} backbone-class nodes "
            f"({', '.join(f'{n} {role}' for role, n in role_counts.items())})"
        ),
        rows=rows,
        spec=spec,
        note=(
            "Ranked by packet activity, restricted to nodes with a "
            "ROUTER, ROUTER_LATE, or REPEATER role. Per-hop relay counts "
            "from path traces would require live packet capture, which "
            "wasn't running on the box during this capture window."
        ),
    )
