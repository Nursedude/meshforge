"""Region presets — bounding-box definitions for geographic filtering.

Single source of truth for "what counts as Hawaii / West Coast / US / etc.",
shared between the map HTTP handler (filters /api/nodes/geojson by region)
and the MOC Analysis Tool (filters analysis runs by region).

Each preset has:
- ``label``: human-readable name shown in the View dropdown.
- ``map_center_lat`` / ``map_center_lon``: where the map opens for this region.
- ``map_default_zoom``: zoom level used when this preset is selected.
- ``bbox``: ``[south, west, north, east]`` decimal degrees, OR a list of such
  bboxes for non-contiguous regions like the United States, OR ``None`` for
  the world preset (no spatial filter).

Coordinates are decimal degrees, WGS84. Bounding boxes are inclusive on all
edges. Multi-bbox preset semantics: a point matches the preset if it falls
inside ANY of the listed bboxes.
"""

from __future__ import annotations

from typing import Iterable, Optional

# Public dict — primary import target. Mutating in place is forbidden;
# callers should treat as immutable.
REGION_PRESETS = {
    "hawaii": {
        "label": "Hawaii",
        "map_center_lat": 20.5, "map_center_lon": -157.0,
        "map_default_zoom": 7,
        "bbox": [18.5, -161.0, 22.5, -154.0],
    },
    "west_coast": {
        "label": "West Coast",
        "map_center_lat": 37.5, "map_center_lon": -122.0,
        "map_default_zoom": 6,
        "bbox": [32.0, -125.0, 49.0, -114.0],
    },
    "us": {
        "label": "United States",
        "map_center_lat": 39.0, "map_center_lon": -98.0,
        "map_default_zoom": 4,
        "bbox": [
            [24.5, -125.0, 49.5, -66.0],   # CONUS
            [51.0, -180.0, 72.0, -130.0],   # Alaska
            [18.5, -161.0, 22.5, -154.0],   # Hawaii
            [17.5, -68.0, 18.6, -64.0],     # PR + USVI
        ],
    },
    "americas": {
        "label": "Americas",
        "map_center_lat": 15.0, "map_center_lon": -80.0,
        "map_default_zoom": 3,
        "bbox": [-56.0, -180.0, 72.0, -34.0],
    },
    "world": {
        "label": "World",
        "map_center_lat": 20.0, "map_center_lon": 0.0,
        "map_default_zoom": 3,
        "bbox": None,
    },
}


def _bbox_contains(bbox: Iterable[float], lat: float, lon: float) -> bool:
    south, west, north, east = bbox
    return south <= lat <= north and west <= lon <= east


def point_in_region(region_key: str, lat: float, lon: float) -> bool:
    """Return True if ``(lat, lon)`` falls inside the named preset.

    The world preset (bbox=None) matches every coordinate.
    A multi-bbox preset matches if the point is inside any constituent bbox.
    Unknown region keys return False rather than raising — callers asking
    "is X in Y?" don't need to also handle KeyError.
    """
    preset = REGION_PRESETS.get(region_key)
    if preset is None:
        return False
    bbox = preset["bbox"]
    if bbox is None:
        return True
    if isinstance(bbox[0], (list, tuple)):
        return any(_bbox_contains(b, lat, lon) for b in bbox)
    return _bbox_contains(bbox, lat, lon)


def get_preset(region_key: str) -> Optional[dict]:
    """Return the preset dict for ``region_key`` or None if unknown."""
    return REGION_PRESETS.get(region_key)
