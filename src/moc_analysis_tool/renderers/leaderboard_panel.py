"""Leaderboard panel renderer — one SVG per Leaderboard.

Used by 3 of the 6 slide assets (most-active, best-snr, backbone-relays).
The fourth leaderboard (most-reliable) optionally renders a 4th panel
if the slide deck wants it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from moc_analysis_tool.leaderboards.base import Leaderboard
from moc_analysis_tool.renderers.svg_base import (
    THEME, RenderResult, get_env, color_for_app,
)


# Map metric name → bar color. Lets all four panels read distinct.
_METRIC_BAR_COLOR = {
    "most_active":   "#0B3C6F",   # ramp_low — depth
    "best_snr":      "#E8794F",   # ramp_high — heat
    "most_reliable": "#7FB069",   # green — health
    "most_relayed":  "#9B7EBD",   # purple — backbone
}


def render_leaderboard(
    leaderboard: Leaderboard,
    *,
    canvas_w: int = 1280,
    canvas_h: int = 720,
    filename: Optional[str] = None,
    bar_color: Optional[str] = None,
    footer_right: Optional[str] = None,
) -> RenderResult:
    """Render a Leaderboard into a slide-ready SVG."""
    env = get_env()
    template = env.get_template("leaderboard_panel.svg.j2")

    if bar_color is None:
        bar_color = _METRIC_BAR_COLOR.get(
            leaderboard.metric, THEME["palette"]["fg"]
        )

    if filename is None:
        # Stable per-metric filenames so a re-run overwrites the
        # previous version cleanly.
        filename = f"leaderboard_{leaderboard.metric}.svg"

    if footer_right is None:
        run_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        footer_right = f"MOC Analysis · {leaderboard.spec.name} · {run_at}"

    svg = template.render(
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        leaderboard=leaderboard,
        bar_color=bar_color,
        footer_left="meshforge.moc_analysis_tool",
        footer_right=footer_right,
    )

    return RenderResult(
        filename=filename,
        svg=svg,
        metadata={
            "metric": leaderboard.metric,
            "row_count": len(leaderboard.rows),
            "title": leaderboard.title,
        },
    )
