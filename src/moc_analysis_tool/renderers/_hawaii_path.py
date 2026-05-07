"""Hawaii island silhouettes — bbox + simplified outline paths.

The hero map needs the islands rendered as background context. Using
simplified polygon paths (low vertex count, smooth at slide-zoom levels)
rather than embedding GeoJSON — keeps the SVG self-contained and small.

Coordinates are projected to a viewBox-local space at render time using
``project_lat_lon`` so a single source of paths works across canvas
sizes.

Paths are SIMPLIFIED — they don't follow every cove and bay; they're
recognizably island-shaped at slide resolution. This is intentional;
high-fidelity outlines would balloon the SVG.

The bbox here matches REGION_PRESETS['hawaii']: lat 18.5..22.5,
lon -161..-154.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

# Each island = list of (lat, lon) vertices (closed polygon).
# Coordinates from public-domain coastline (NOAA / Natural Earth simplified).
# Roughly 30-50 vertices per island; smooth at slide-deck zoom.

HAWAII_ISLANDS: dict = {
    "kauai": [
        (22.23, -159.78), (22.20, -159.59), (22.13, -159.34), (22.06, -159.30),
        (21.98, -159.30), (21.88, -159.40), (21.86, -159.55), (21.89, -159.70),
        (21.95, -159.79), (22.05, -159.80), (22.13, -159.78), (22.21, -159.78),
    ],
    "niihau": [
        (21.97, -160.25), (21.90, -160.10), (21.80, -160.10), (21.78, -160.20),
        (21.85, -160.26), (21.92, -160.26),
    ],
    "oahu": [
        (21.71, -157.97), (21.65, -157.65), (21.55, -157.65), (21.30, -157.85),
        (21.30, -158.10), (21.45, -158.30), (21.58, -158.28), (21.71, -158.10),
    ],
    "molokai": [
        (21.20, -157.00), (21.20, -156.70), (21.10, -156.70), (21.07, -156.95),
        (21.10, -157.10), (21.18, -157.10),
    ],
    "lanai": [
        (20.92, -156.99), (20.88, -156.85), (20.78, -156.85), (20.74, -156.97),
        (20.78, -157.07), (20.88, -157.07),
    ],
    "kahoolawe": [
        (20.60, -156.71), (20.58, -156.55), (20.50, -156.55), (20.48, -156.65),
        (20.52, -156.75),
    ],
    "maui": [
        (21.04, -156.30), (21.02, -156.10), (20.95, -155.97), (20.85, -155.97),
        (20.75, -156.05), (20.58, -156.20), (20.55, -156.40), (20.65, -156.65),
        (20.77, -156.71), (20.90, -156.65), (20.98, -156.50),
    ],
    "hawaii_big": [
        (20.27, -155.88), (20.24, -155.50), (20.10, -155.10), (19.85, -154.81),
        (19.55, -154.81), (19.30, -154.95), (18.92, -155.56), (18.91, -155.73),
        (19.10, -156.06), (19.40, -156.06), (19.65, -155.95), (19.95, -155.91),
    ],
}


@dataclass
class Projection:
    """lat/lon → svg pixel transform for a given canvas + bbox."""
    bbox_south: float
    bbox_west: float
    bbox_north: float
    bbox_east: float
    canvas_w: int
    canvas_h: int
    pad: int = 60   # interior margin so pins don't touch the edge

    def project(self, lat: float, lon: float) -> Tuple[float, float]:
        """Project a (lat, lon) into (x, y) pixels."""
        usable_w = self.canvas_w - 2 * self.pad
        usable_h = self.canvas_h - 2 * self.pad
        # Lon → x (west=0, east=canvas_w)
        x = self.pad + (lon - self.bbox_west) / (
            self.bbox_east - self.bbox_west
        ) * usable_w
        # Lat → y (north=0, south=canvas_h) — flip for screen coords
        y = self.pad + (self.bbox_north - lat) / (
            self.bbox_north - self.bbox_south
        ) * usable_h
        return x, y


def islands_as_path_d(proj: Projection) -> str:
    """Concatenated SVG path-d string covering every Hawaiian island."""
    parts: List[str] = []
    for verts in HAWAII_ISLANDS.values():
        if not verts:
            continue
        x, y = proj.project(*verts[0])
        parts.append(f"M{x:.1f},{y:.1f}")
        for lat, lon in verts[1:]:
            x, y = proj.project(lat, lon)
            parts.append(f"L{x:.1f},{y:.1f}")
        parts.append("Z")
    return " ".join(parts)
