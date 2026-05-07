"""AnalysisSpec — the contract every aggregator/leaderboard/renderer reads.

Specs are constructed by presets (see cli/presets.py) or by the CLI from
arg parsing. They are immutable dataclasses so a single spec round-trips
through aggregators, leaderboards, JSON dumps, and SVG renderers without
ambiguity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class TimeWindow:
    """Inclusive [start, end] window in UTC."""
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("TimeWindow start/end must be timezone-aware")
        if self.start > self.end:
            raise ValueError(f"start {self.start} > end {self.end}")

    @classmethod
    def last_n_hours(cls, hours: int) -> "TimeWindow":
        end = datetime.now(timezone.utc)
        start = end - _timedelta_hours(hours)
        return cls(start=start, end=end)

    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600.0


@dataclass(frozen=True)
class AnalysisSpec:
    """Configuration for a single analysis run.

    Fields:
        name:
            Short identifier — drives output dir naming (e.g.
            ``hawaii_may2026``).
        region_key:
            Key into ``utils.region_presets.REGION_PRESETS``. ``None``
            means "no region filter; include every observed node".
        window:
            UTC time window the analysis covers.
        network_filter:
            Optional preset filter (``longfast``, ``shortturbo``, ...).
            ``None`` means all presets observed by the box.
        out_dir:
            Where renderers + JSON exports land. The CLI defaults to
            ``~/meshforge_data/moc_analyses/<name>/`` if unset.
        db_path:
            Path to the SQLite DB the aggregators read. Defaults to the
            ``presentation_capture`` DBSpec path.
        node_history_path:
            Path to ``node_history.db`` for uptime / position rollups.
        top_n:
            How many rows each leaderboard returns.
        snr_min_samples:
            Minimum packet count for a node to qualify for the
            best-SNR leaderboard (filters one-shot anomalies).
    """

    name: str
    region_key: Optional[str]
    window: TimeWindow
    network_filter: Optional[str] = None
    out_dir: Optional[Path] = None
    db_path: Optional[Path] = None
    node_history_path: Optional[Path] = None
    top_n: int = 10
    snr_min_samples: int = 30

    def resolved_out_dir(self) -> Path:
        if self.out_dir is not None:
            return self.out_dir
        from utils.paths import get_real_user_home
        return (
            get_real_user_home()
            / "meshforge_data"
            / "moc_analyses"
            / self.name
        )

    def resolved_db_path(self) -> Path:
        if self.db_path is not None:
            return self.db_path
        from utils.db_inventory import find_spec
        spec = find_spec("presentation_capture")
        if spec is None:
            raise RuntimeError(
                "presentation_capture DBSpec missing from utils.db_inventory"
            )
        return spec.path_factory()

    def resolved_node_history_path(self) -> Path:
        if self.node_history_path is not None:
            return self.node_history_path
        from utils.db_inventory import find_spec
        spec = find_spec("node_history")
        if spec is None:
            raise RuntimeError(
                "node_history DBSpec missing from utils.db_inventory"
            )
        return spec.path_factory()


def _timedelta_hours(hours: int):
    from datetime import timedelta
    return timedelta(hours=hours)
