"""JSON dumps — canonical exported form of an analysis run.

The static JSON dumps are the talk's primary artifact: reproducible
offline, embeddable into any slide deck, deterministic given the same
inputs. The /api/moc/* HTTP routes (web_api.py) read the same dumps
so live demos and slide content never disagree.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from moc_analysis_tool.aggregators.coverage_rollups import collect_coverage_pins
from moc_analysis_tool.aggregators.node_rollups import NodeRollupSet
from moc_analysis_tool.aggregators.portnum_rollups import (
    collect_network_portnum_distribution,
)
from moc_analysis_tool.aggregators.timeline_rollups import collect_hourly_timeline
from moc_analysis_tool.leaderboards.base import Leaderboard
from moc_analysis_tool.spec import AnalysisSpec


def _default(obj: Any) -> Any:
    """JSON serializer for dataclasses + datetime + sets."""
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, set):
        return sorted(obj)
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def dump_leaderboard(leaderboard: Leaderboard, out_dir: Path) -> Path:
    """Write a single leaderboard as ``leaderboard_<metric>.json``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "metric": leaderboard.metric,
        "title": leaderboard.title,
        "subtitle": leaderboard.subtitle,
        "note": leaderboard.note,
        "spec": _spec_to_dict(leaderboard.spec),
        "rows": [asdict(r) for r in leaderboard.rows],
    }
    path = out_dir / f"leaderboard_{leaderboard.metric}.json"
    path.write_text(json.dumps(payload, indent=2, default=_default), encoding="utf-8")
    return path


def dump_node_rollups(rollup_set: NodeRollupSet, out_dir: Path) -> Path:
    """Write all NodeRollups as ``node_rollups.json``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "spec": _spec_to_dict(rollup_set.spec) if rollup_set.spec else None,
        "node_count": len(rollup_set),
        "nodes": [asdict(r) for r in rollup_set.values()],
    }
    path = out_dir / "node_rollups.json"
    path.write_text(json.dumps(payload, indent=2, default=_default), encoding="utf-8")
    return path


def dump_aggregate_summary(
    spec: AnalysisSpec,
    rollup_set: NodeRollupSet,
    out_dir: Path,
) -> Path:
    """Write a top-level summary covering every aggregator output.

    This is the JSON the slide deck imports as "raw numbers" — one file
    per analysis run, contains everything except the leaderboards
    (which get their own per-metric files).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    pins = collect_coverage_pins(rollup_set)
    dist = collect_network_portnum_distribution(
        spec, node_id_filter=set(rollup_set.rollups.keys())
    )
    buckets = collect_hourly_timeline(
        spec, node_id_filter=set(rollup_set.rollups.keys())
    )

    payload = {
        "spec": _spec_to_dict(spec),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "node_count": len(rollup_set),
            "node_count_with_position": sum(
                1 for r in rollup_set.values() if r.has_position
            ),
            "node_count_with_packets": sum(
                1 for r in rollup_set.values() if r.packet_count > 0
            ),
            "total_packets_in_window": sum(
                r.packet_count for r in rollup_set.values()
            ),
            "hourly_buckets": len(buckets),
        },
        "portnum_distribution": dict(dist),
        "coverage_pin_count": len(pins),
        "timeline_summary": {
            "first_hour": buckets[0].hour_start if buckets else None,
            "last_hour": buckets[-1].hour_start if buckets else None,
            "non_empty_hours": sum(1 for b in buckets if b.counts),
        },
    }
    path = out_dir / "aggregate_summary.json"
    path.write_text(json.dumps(payload, indent=2, default=_default), encoding="utf-8")
    return path


def _spec_to_dict(spec: AnalysisSpec) -> Dict[str, Any]:
    """AnalysisSpec → JSON-friendly dict.

    Skips Path fields (let the caller re-resolve at read time) and
    converts TimeWindow into ISO start/end strings.
    """
    if spec is None:
        return {}
    return {
        "name": spec.name,
        "region_key": spec.region_key,
        "network_filter": spec.network_filter,
        "top_n": spec.top_n,
        "snr_min_samples": spec.snr_min_samples,
        "window": {
            "start": spec.window.start.isoformat(),
            "end": spec.window.end.isoformat(),
            "hours": spec.window.hours(),
        },
    }
