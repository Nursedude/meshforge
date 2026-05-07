"""Timeline strip renderer — packets/hour stacked by app over the window."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import List, Optional

from moc_analysis_tool.aggregators.timeline_rollups import HourBucket
from moc_analysis_tool.renderers.svg_base import (
    THEME, RenderResult, get_env, color_for_app,
)
from moc_analysis_tool.spec import AnalysisSpec


def _nice_max(value: float) -> int:
    """Round up to a 'nice' axis maximum (50, 100, 200, 500, ...)."""
    if value <= 0:
        return 10
    nice_steps = [10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000]
    for s in nice_steps:
        if value <= s:
            return s
    # Round up to the next 1000 for very large totals
    return int(math.ceil(value / 1000.0) * 1000)


def render_timeline_strip(
    buckets: List[HourBucket],
    spec: AnalysisSpec,
    *,
    canvas_w: int = 1280,
    canvas_h: int = 720,
    filename: str = "timeline.svg",
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
) -> RenderResult:
    """Render packets-per-hour as stacked bars colored by app."""
    if not buckets:
        # Empty timeline — render the chrome anyway so the slide isn't blank.
        buckets = []

    # Find the top-N port_names so the legend stays small.
    cumulative = {}
    for b in buckets:
        for port_name, n in b.counts.items():
            cumulative[port_name] = cumulative.get(port_name, 0) + n
    top_apps = sorted(cumulative.items(), key=lambda kv: kv[1], reverse=True)
    legend_apps = [a for a, _ in top_apps[:5]]
    if any(a not in legend_apps for a, _ in top_apps):
        legend_apps.append("OTHER")

    margin = THEME["canvas"]["margin"]
    chart_x = margin + 60
    chart_y = margin + 80
    chart_w = canvas_w - chart_x - margin
    chart_h = canvas_h - chart_y - margin - 90  # extra for x-axis + legend

    # Y-axis: total packets per hour
    bar_totals = [sum(b.counts.values()) for b in buckets]
    y_max = _nice_max(max(bar_totals) if bar_totals else 10)

    # Y ticks at 0, ¼, ½, ¾, max
    y_axis = []
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        v = int(y_max * frac)
        y = chart_y + chart_h - chart_h * frac
        y_axis.append({"y": y, "label": f"{v:,}"})

    # X-axis: bars + day-boundary ticks
    n = max(len(buckets), 1)
    bar_w = max(chart_w / n - 1, 1.0)
    bars = []
    x_axis_ticks = []
    last_day = None
    for i, b in enumerate(buckets):
        bx = chart_x + i * (chart_w / n)
        total = sum(b.counts.values())
        bar_h = chart_h * (total / y_max) if y_max > 0 else 0
        by = chart_y + chart_h - bar_h
        # Stack segments tallest-first for visual consistency
        sorted_counts = sorted(b.counts.items(), key=lambda kv: kv[1], reverse=True)
        segments = []
        for port_name, count in sorted_counts:
            if total <= 0:
                continue
            seg_h = chart_h * (count / y_max) if y_max > 0 else 0
            segments.append({
                "h": seg_h,
                "fill": color_for_app(port_name),
                "label": port_name,
            })
        bars.append({
            "x": bx, "y": by, "w": bar_w, "segments": segments,
        })

        # Mark day boundaries on the X axis
        dt = datetime.fromtimestamp(b.hour_start, tz=timezone.utc)
        day_str = dt.strftime("%b %d")
        if day_str != last_day:
            x_axis_ticks.append({
                "x": bx + bar_w / 2,
                "label": day_str,
            })
            last_day = day_str

    legend = [
        {"label": a.replace("_APP", ""), "fill": color_for_app(a)}
        for a in legend_apps if a != "OTHER"
    ]

    if title is None:
        title = "Activity Timeline"
    if subtitle is None:
        subtitle = (
            f"Packets per hour over {spec.window.hours():.0f} h, stacked by app"
        )

    env = get_env()
    template = env.get_template("timeline_strip.svg.j2")
    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    svg = template.render(
        canvas_w=canvas_w, canvas_h=canvas_h,
        chart_x=chart_x, chart_y=chart_y,
        chart_w=chart_w, chart_h=chart_h,
        title=title, subtitle=subtitle,
        bars=bars, y_axis=y_axis, x_axis=x_axis_ticks, legend=legend,
        footer_left="meshforge.moc_analysis_tool",
        footer_right=f"MOC Analysis · {spec.name} · {run_at}",
    )
    return RenderResult(
        filename=filename, svg=svg,
        metadata={
            "buckets": len(buckets),
            "y_max": y_max,
            "total_packets": sum(bar_totals),
        },
    )
