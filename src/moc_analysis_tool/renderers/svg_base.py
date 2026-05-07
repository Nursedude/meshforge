"""SVG renderer base — theme tokens + Jinja2 environment.

Centralized so every slide asset reads from the same palette / type
scale / spacing grid. To re-skin the deck (e.g. for a future
presentation), edit ``THEME`` here and re-run the analysis.

Self-contained guarantee: rendered SVGs MUST embed every font and
color reference inline. No external font loads. No CSS imports. No
external image hrefs. The slide pipeline (Claude.ai design) will
embed these as raw SVG, and broken external refs would break the
slide silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import jinja2

# ── Theme tokens ──────────────────────────────────────────────────────
# Single dict so the renderer + templates read the same source of truth.
# Editable for future re-skins. Keep colors :  WCAG AA on white where
# possible; the deck background is light.

THEME: Dict[str, Any] = {
    # Color palette — Hawaii-flavored without being a tourism brochure.
    # Deep ocean blue → volcanic orange diverging scale for SNR /
    # activity metrics, neutral slate for chrome.
    "palette": {
        "bg":          "#FFFFFF",
        "fg":          "#0E1E2B",   # near-black, slight blue cast
        "muted":       "#5C6B7A",
        "rule":        "#D6DCE2",

        # Accent colors for metric ramps (low → high)
        "ramp_low":    "#0B3C6F",   # deep ocean
        "ramp_mid":    "#6FB3B8",   # turquoise
        "ramp_high":   "#E8794F",   # volcanic orange

        # Per-app colors for the treemap + timeline ribbons.
        # Picked so they're distinguishable when desaturated for print.
        "app_position":   "#3A6EA5",   # blue
        "app_nodeinfo":   "#7FB069",   # green
        "app_telemetry":  "#E8C547",   # gold
        "app_traceroute": "#C44D58",   # red
        "app_routing":    "#9B7EBD",   # purple
        "app_text":       "#E8794F",   # orange
        "app_admin":      "#5C6B7A",   # muted gray
        "app_other":      "#A8A8A8",   # neutral gray
    },

    # Type scale — system fonts only so SVG is self-contained.
    "fonts": {
        # Use a generic font stack that maps to whatever the slide tool
        # has available. SVG <text> font-family accepts a list directly.
        "stack": "'IBM Plex Sans', 'Inter', 'Helvetica Neue', Arial, sans-serif",
        "stack_mono": "'IBM Plex Mono', 'JetBrains Mono', 'SF Mono', Consolas, monospace",
    },

    # Type scale (px @ 96dpi-ish). Slide imports SVG and scales freely.
    "type": {
        "title":    {"size": 28, "weight": 700, "tracking": -0.5},
        "subtitle": {"size": 14, "weight": 400, "tracking": 0},
        "label":    {"size": 12, "weight": 600, "tracking": 0.2},
        "body":     {"size": 12, "weight": 400, "tracking": 0},
        "small":    {"size": 10, "weight": 400, "tracking": 0.5},
        "tag":      {"size":  9, "weight": 500, "tracking": 0.4},
    },

    # Canvas defaults — overridden per-template.
    "canvas": {
        "default_width":  1280,
        "default_height": 720,
        "margin":         48,
    },
}


# ── PortNum → palette key ─────────────────────────────────────────────
_PORTNAME_TO_PALETTE_KEY = {
    "POSITION_APP":     "app_position",
    "NODEINFO_APP":     "app_nodeinfo",
    "TELEMETRY_APP":    "app_telemetry",
    "TRACEROUTE_APP":   "app_traceroute",
    "ROUTING_APP":      "app_routing",
    "TEXT_MESSAGE_APP": "app_text",
    "ADMIN_APP":        "app_admin",
}


def color_for_app(port_name: str) -> str:
    """Return the hex color string for a Meshtastic port_name."""
    key = _PORTNAME_TO_PALETTE_KEY.get(port_name, "app_other")
    return THEME["palette"][key]


def color_for_snr(snr: Optional[float]) -> str:
    """Map an avg SNR value to a color along the diverging ramp.

    SNR in real Meshtastic networks ranges roughly -20 (terrible) to
    +12 (excellent). Mapping is:
       <= -10 dB → ramp_low (deep ocean)
       around 0 dB → ramp_mid (turquoise)
       >= +6 dB → ramp_high (volcanic orange)
    """
    if snr is None:
        return THEME["palette"]["muted"]
    if snr <= -10:
        return THEME["palette"]["ramp_low"]
    if snr >= 6:
        return THEME["palette"]["ramp_high"]
    if snr < 0:
        return THEME["palette"]["ramp_low"]
    return THEME["palette"]["ramp_mid"]


# ── Jinja2 environment ────────────────────────────────────────────────
def _build_env() -> jinja2.Environment:
    template_dir = Path(__file__).parent / "templates"
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(template_dir)),
        autoescape=jinja2.select_autoescape(
            disabled_extensions=(),
            default_for_string=True,
            default=True,
        ),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )
    # Expose helpers + theme into every template.
    env.globals["THEME"] = THEME
    env.globals["color_for_app"] = color_for_app
    env.globals["color_for_snr"] = color_for_snr
    env.filters["fmt_int"] = lambda n: f"{int(n):,}"
    env.filters["fmt_float"] = lambda f, p=1: f"{f:.{p}f}"
    env.filters["fmt_pct"] = lambda f, p=1: f"{f * 100:.{p}f}%"
    return env


_ENV: Optional[jinja2.Environment] = None


def get_env() -> jinja2.Environment:
    """Lazy-construct the Jinja2 environment (one per process)."""
    global _ENV
    if _ENV is None:
        _ENV = _build_env()
    return _ENV


@dataclass
class RenderResult:
    """Output of a renderer — paired SVG content + suggested filename."""
    filename: str
    svg: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def write_to(self, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / self.filename
        path.write_text(self.svg, encoding="utf-8")
        return path
