"""Orchestrate one analysis run end-to-end.

Usage:
    python -m moc_analysis_tool.cli.run_analysis --preset hawaii_may2026

This runs:
    1. region filter      (collect_region_node_ids + collect_all_packet_sources)
    2. node rollups       (collect_node_rollups → NodeRollupSet)
    3. four leaderboards  (most_active, best_snr, most_reliable, most_relayed)
    4. coverage pins      (for hero map)
    5. portnum distribution (for treemap)
    6. timeline buckets   (for timeline strip)
    7. JSON dumps         (per-leaderboard + aggregate_summary)
    8. SVG renders        (7 files into <out>/svg/)

Re-running with the same preset is idempotent — outputs overwrite.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Iterable, List, Optional

from moc_analysis_tool.aggregators.coverage_rollups import collect_coverage_pins
from moc_analysis_tool.aggregators.node_rollups import collect_node_rollups
from moc_analysis_tool.aggregators.portnum_rollups import (
    collect_network_portnum_distribution,
)
from moc_analysis_tool.aggregators.region_filter import (
    collect_all_packet_sources, collect_region_node_ids,
)
from moc_analysis_tool.aggregators.timeline_rollups import collect_hourly_timeline
from moc_analysis_tool.cli.presets import get_preset, list_presets
from moc_analysis_tool.exports.json_dump import (
    dump_aggregate_summary, dump_leaderboard, dump_node_rollups,
)
from moc_analysis_tool.leaderboards import (
    best_snr, most_active, most_relayed, most_reliable,
)
from moc_analysis_tool.renderers.hero_map import render_hero_map
from moc_analysis_tool.renderers.leaderboard_panel import render_leaderboard
from moc_analysis_tool.renderers.portnum_treemap import render_portnum_treemap
from moc_analysis_tool.renderers.timeline_strip import render_timeline_strip
from moc_analysis_tool.spec import AnalysisSpec

logger = logging.getLogger(__name__)


def run_analysis(spec: AnalysisSpec) -> dict:
    """Run a full analysis from spec → JSON + SVG outputs.

    Returns a summary dict with file counts + paths.
    """
    out_dir = spec.resolved_out_dir()
    json_dir = out_dir / "json"
    svg_dir = out_dir / "svg"
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Running %s", spec.name)
    logger.info("  region: %s", spec.region_key or "all")
    logger.info("  window: %s → %s (%.1f h)",
                spec.window.start.isoformat(), spec.window.end.isoformat(),
                spec.window.hours())
    logger.info("  output: %s", out_dir)

    # 1+2: aggregate
    if spec.region_key is not None:
        node_filter = collect_region_node_ids(spec, include_positionless=True)
        node_filter |= collect_all_packet_sources(spec)
    else:
        node_filter = None
    logger.info("  node filter size: %s",
                len(node_filter) if node_filter is not None else "unrestricted")

    rollup_set = collect_node_rollups(spec, node_id_filter=node_filter)
    logger.info("  rollups produced: %d", len(rollup_set))

    # 3: leaderboards
    builders = [
        ("most_active", most_active),
        ("best_snr", best_snr),
        ("most_reliable", most_reliable),
        ("most_relayed", most_relayed),
    ]
    leaderboards = []
    for label, builder in builders:
        lb = builder.build(rollup_set, spec)
        leaderboards.append(lb)
        logger.info("  leaderboard %s: %d rows", label, len(lb.rows))

    # 4-6: derived rollups for renderers
    pins = collect_coverage_pins(rollup_set)
    dist = collect_network_portnum_distribution(spec, node_id_filter=node_filter)
    buckets = collect_hourly_timeline(spec, node_id_filter=node_filter)

    # 7: JSON dumps
    json_files: List[Path] = []
    for lb in leaderboards:
        json_files.append(dump_leaderboard(lb, json_dir))
    json_files.append(dump_node_rollups(rollup_set, json_dir))
    json_files.append(dump_aggregate_summary(spec, rollup_set, json_dir))

    # 8: SVG renders
    svg_files: List[Path] = []
    for lb in leaderboards:
        res = render_leaderboard(lb)
        svg_files.append(res.write_to(svg_dir))
    svg_files.append(render_hero_map(pins, spec).write_to(svg_dir))
    svg_files.append(render_portnum_treemap(dist, spec).write_to(svg_dir))
    svg_files.append(render_timeline_strip(buckets, spec).write_to(svg_dir))

    return {
        "out_dir": str(out_dir),
        "json_files": [str(p) for p in json_files],
        "svg_files": [str(p) for p in svg_files],
        "node_count": len(rollup_set),
        "leaderboards": [lb.metric for lb in leaderboards],
        "coverage_pins": len(pins),
        "timeline_buckets": len(buckets),
    }


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preset", type=str, default=None,
        help="Named preset (see --list-presets)",
    )
    parser.add_argument(
        "--list-presets", action="store_true",
        help="List available presets and exit",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Override output directory (default: ~/meshforge_data/moc_analyses/<preset>/)",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(message)s",
    )

    if args.list_presets:
        for name, desc in list_presets().items():
            print(f"  {name:20s} {desc}")
        return 0

    if not args.preset:
        parser.error("--preset is required (or pass --list-presets)")

    try:
        spec = get_preset(args.preset)
    except KeyError:
        print(f"unknown preset: {args.preset}", file=sys.stderr)
        print("known:", ", ".join(list_presets().keys()), file=sys.stderr)
        return 2

    if args.out is not None:
        spec = AnalysisSpec(
            name=spec.name,
            region_key=spec.region_key,
            window=spec.window,
            network_filter=spec.network_filter,
            out_dir=args.out,
            db_path=spec.db_path,
            node_history_path=spec.node_history_path,
            top_n=spec.top_n,
            snr_min_samples=spec.snr_min_samples,
        )

    summary = run_analysis(spec)
    print()
    print(f"Wrote {len(summary['json_files'])} JSON files and "
          f"{len(summary['svg_files'])} SVG files to {summary['out_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
