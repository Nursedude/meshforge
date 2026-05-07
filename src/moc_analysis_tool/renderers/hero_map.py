"""Hero map renderer — Hawaii silhouette + pinned nodes.

The crown-jewel slide. Each node with position lands as a circle:
- Position: lat/lon projected via _hawaii_path.Projection
- Radius: scaled by packet_count (sqrt scale to keep dots readable)
- Fill: color_for_snr(avg_snr) — diverging deep-ocean → volcanic-orange
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import List, Optional

from moc_analysis_tool.aggregators.coverage_rollups import CoveragePin
from moc_analysis_tool.renderers._hawaii_path import Projection, islands_as_path_d
from moc_analysis_tool.renderers.svg_base import (
    THEME, RenderResult, get_env, color_for_snr,
)
from moc_analysis_tool.spec import AnalysisSpec
from utils.region_presets import REGION_PRESETS


def _radius_for_count(packet_count: int, max_count: int) -> float:
    """Scale dot radius by packet count, sqrt to keep small dots visible."""
    if max_count <= 0:
        return 4.0
    base = 4.0
    extra = 14.0
    return base + extra * math.sqrt(packet_count / max_count)


def render_hero_map(
    pins: List[CoveragePin],
    spec: AnalysisSpec,
    *,
    canvas_w: int = 1280,
    canvas_h: int = 720,
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    filename: str = "hero_map.svg",
) -> RenderResult:
    """Render the Hawaii hero map."""
    region = REGION_PRESETS.get(spec.region_key) if spec.region_key else None
    if region is None:
        # Fall back to Hawaii bbox so we still produce *something*.
        region = REGION_PRESETS["hawaii"]

    bbox = region["bbox"]
    if isinstance(bbox[0], (list, tuple)):
        bbox = bbox[0]
    south, west, north, east = bbox

    proj = Projection(
        bbox_south=south, bbox_west=west,
        bbox_north=north, bbox_east=east,
        canvas_w=canvas_w, canvas_h=canvas_h,
        pad=80,
    )

    islands_path = islands_as_path_d(proj)

    max_count = max((p.packet_count for p in pins), default=0)
    pin_dicts = []
    for p in pins:
        x, y = proj.project(p.lat, p.lon)
        pin_dicts.append({
            "x": x, "y": y,
            "r": _radius_for_count(p.packet_count, max_count),
            "fill": color_for_snr(p.avg_snr),
            "label": p.short_name or p.name[:6],
        })

    win_start = spec.window.start.strftime("%Y-%m-%d %H:%M UTC")
    win_end = spec.window.end.strftime("%Y-%m-%d %H:%M UTC")
    window_label = f"{win_start} → {win_end}"

    total_packets = sum(p.packet_count for p in pins)

    stats = {
        "total_nodes": len(pins),
        "total_packets": total_packets,
        "window_label": window_label,
        "region_label": region["label"],
    }

    if title is None:
        title = f"Mesh Activity — {region['label']}"
    if subtitle is None:
        subtitle = (
            f"{spec.window.hours():.0f} hours observed from one Big Island "
            f"vantage point"
        )

    env = get_env()
    template = env.get_template("hero_map.svg.j2")
    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    svg = template.render(
        canvas_w=canvas_w, canvas_h=canvas_h,
        islands_path=islands_path,
        pins=pin_dicts,
        stats=stats,
        title=title, subtitle=subtitle,
        footer_left="meshforge.moc_analysis_tool",
        footer_right=f"MOC Analysis · {spec.name} · {run_at}",
    )

    return RenderResult(
        filename=filename,
        svg=svg,
        metadata={
            "pin_count": len(pins),
            "total_packets": total_packets,
            "region": region["label"],
        },
    )
