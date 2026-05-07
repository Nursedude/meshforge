"""App-mix treemap renderer — squarified treemap of port_name distribution.

Uses a simple squarified treemap algorithm so the rectangles look
roughly square at any aspect ratio, not the long thin slivers a naive
left-to-right slice would produce.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from moc_analysis_tool.renderers.svg_base import (
    THEME, RenderResult, get_env, color_for_app,
)
from moc_analysis_tool.spec import AnalysisSpec


def _squarify(values: List[float], x: float, y: float, w: float, h: float):
    """Squarified treemap layout. Returns list of (x, y, w, h) per value.

    Standard Bruls/Huijsen/van Wijk algorithm, simplified for our small
    bucket count (≤8 apps).
    """
    if not values:
        return []
    total = sum(values)
    if total <= 0:
        return [(x, y, 0, 0) for _ in values]

    rects = []
    remaining = list(values)
    rx, ry, rw, rh = x, y, w, h

    while remaining:
        # Place values one row/column at a time, choosing whichever
        # minimizes worst-case aspect ratio of the row.
        if rw <= 0 or rh <= 0:
            break
        # Greedy: try adding values to a row until aspect ratio worsens.
        row = [remaining[0]]
        i = 1
        while i < len(remaining):
            candidate = row + [remaining[i]]
            if _worst_ratio(candidate, min(rw, rh)) <= _worst_ratio(row, min(rw, rh)):
                row = candidate
                i += 1
            else:
                break

        # Lay out row along the shorter side
        row_total = sum(row)
        proportion = row_total / sum(remaining)
        if rw < rh:
            row_w = rw
            row_h = rh * proportion
            cx = rx
            cy = ry
            for v in row:
                vw = row_w * (v / row_total)
                rects.append((cx, cy, vw, row_h))
                cx += vw
            ry += row_h
            rh -= row_h
        else:
            row_w = rw * proportion
            row_h = rh
            cx = rx
            cy = ry
            for v in row:
                vh = row_h * (v / row_total)
                rects.append((cx, cy, row_w, vh))
                cy += vh
            rx += row_w
            rw -= row_w

        remaining = remaining[len(row):]

    return rects


def _worst_ratio(row: List[float], side: float) -> float:
    if not row or side <= 0:
        return float("inf")
    s = sum(row)
    if s <= 0:
        return float("inf")
    s2 = side * side
    worst = 0.0
    for v in row:
        ratio = max(s2 * v / (s * s), s * s / (s2 * v))
        worst = max(worst, ratio)
    return worst


def render_portnum_treemap(
    distribution: Dict[str, int],
    spec: AnalysisSpec,
    *,
    canvas_w: int = 1280,
    canvas_h: int = 720,
    filename: str = "app_mix.svg",
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
) -> RenderResult:
    """Render the network app-mix as a treemap."""
    items = sorted(distribution.items(), key=lambda kv: kv[1], reverse=True)
    values = [float(v) for _, v in items]
    total = sum(values)

    margin = THEME["canvas"]["margin"]
    chart_x = margin
    chart_y = margin + 80
    chart_w = canvas_w - 2 * margin
    chart_h = canvas_h - chart_y - margin - 40

    raw_rects = _squarify(values, chart_x, chart_y, chart_w, chart_h)

    rects = []
    for (port_name, count), (x, y, w, h) in zip(items, raw_rects):
        pct = (count / total * 100) if total else 0.0
        # Strip _APP suffix for label clarity.
        label = port_name.replace("_APP", "")
        rects.append({
            "x": x, "y": y, "w": w, "h": h,
            "fill": color_for_app(port_name),
            "label": label, "count": count, "pct": pct,
        })

    if title is None:
        title = "Application Mix"
    if subtitle is None:
        subtitle = (
            f"Distribution of {int(total):,} packets by Meshtastic PortNum "
            f"over {spec.window.hours():.0f} h"
        )

    env = get_env()
    template = env.get_template("portnum_treemap.svg.j2")
    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    svg = template.render(
        canvas_w=canvas_w, canvas_h=canvas_h,
        rects=rects, title=title, subtitle=subtitle,
        footer_left="meshforge.moc_analysis_tool",
        footer_right=f"MOC Analysis · {spec.name} · {run_at}",
    )

    return RenderResult(
        filename=filename, svg=svg,
        metadata={"total_packets": int(total), "categories": len(rects)},
    )
