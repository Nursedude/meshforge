"""Leaderboard protocol + LeaderRow shared shape.

Every leaderboard implements the same protocol so renderers don't need
to know which metric they're showing — they just iterate ``rows``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from moc_analysis_tool.aggregators.node_rollups import NodeRollupSet
from moc_analysis_tool.spec import AnalysisSpec


@dataclass
class LeaderRow:
    """One ranked row in a leaderboard.

    Fields:
        rank: 1-indexed position.
        node_id: canonical ``!aabbccdd`` form.
        display_name:
            Best-effort renderable label. Long-name when available,
            falling back to last-4-hex-chars of node_id.
        primary_value:
            The metric this leaderboard ranked by, in raw units.
        primary_label:
            Short string suitable for the bar tip — formatted for the
            metric (e.g. ``"1,080 pkts"``, ``"6.3 dB"``, ``"99.4% online"``).
        secondary:
            Free-form secondary stats the renderer may surface in a
            sub-line (e.g. avg SNR on the activity leaderboard, packet
            count on the SNR leaderboard).
    """

    rank: int
    node_id: str
    display_name: str
    primary_value: Any
    primary_label: str
    secondary: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Leaderboard:
    """A complete leaderboard ready for JSON export or SVG rendering."""

    metric: str
    title: str
    subtitle: str
    rows: List[LeaderRow]
    spec: AnalysisSpec
    note: Optional[str] = None  # Footnote (e.g. "n>=30 sample floor")


class LeaderboardBuilder(Protocol):
    """Every leaderboard module exposes a ``build(rollup_set, spec)`` callable."""

    def build(
        self,
        rollup_set: NodeRollupSet,
        spec: AnalysisSpec,
    ) -> Leaderboard: ...


def display_name_for(rollup) -> str:
    """Render-friendly label for a node.

    Falls back to ``last4`` hex tag when ``name`` is empty — matches
    what Meshtastic clients show for un-introduced nodes.
    """
    if rollup.name:
        return rollup.name
    last4 = rollup.node_id[-4:] if rollup.node_id else "????"
    return f"<{last4}>"
