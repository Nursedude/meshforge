"""
Geographic bounding-box filter for external-bulk node ingestion.

External-bulk sources (meshcore_public ~40k worldwide, aredn_worldmap,
mqtt_global, public_fallback) shove the whole world into the local
nodes table. On a Pi-class fleet box that has no RF reach to 99% of
those nodes, the result is a multi-GB node_history.db and a slow
/api/nodes/geojson cold path. Filtering at ingestion to a configurable
bbox (auto-derived from operator position if not set) reduces the
external-bulk surface to nodes within ~500 km — operator-meaningful
context — without changing the local-radio paths.

Public API:
    is_within_bbox(lat, lon, bbox) -> bool
    derive_default_bbox(operator_lat, operator_lon, radius_km) -> dict
    load_external_bulk_bbox(settings) -> Optional[dict]
"""

import json
import logging
import math
from pathlib import Path
from typing import Any, Optional

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)

# Flat-earth latitude-degree length (km). Constant good to ~0.5% at any
# latitude — fine for a coarse 500 km bbox. Longitude shortens with
# cos(lat); derive_default_bbox handles that.
_KM_PER_DEGREE_LAT = 111.32


def is_within_bbox(lat: Any, lon: Any, bbox: Optional[dict]) -> bool:
    """Return True iff (lat, lon) falls inside bbox.

    bbox shape: {"lat_min", "lat_max", "lon_min", "lon_max"}.
    Returns False for None lat/lon, malformed bbox, or coords outside.

    Supports antimeridian crossing: when lon_min > lon_max the
    longitude check is OR'd (e.g., lon_min=170, lon_max=-170 matches
    longitudes in [170, 180] OR [-180, -170]).
    """
    if bbox is None:
        return False
    if lat is None or lon is None:
        return False
    try:
        lat_f = float(lat)
        lon_f = float(lon)
        lat_min = float(bbox["lat_min"])
        lat_max = float(bbox["lat_max"])
        lon_min = float(bbox["lon_min"])
        lon_max = float(bbox["lon_max"])
    except (TypeError, ValueError, KeyError):
        return False
    if not (math.isfinite(lat_f) and math.isfinite(lon_f)):
        return False

    if lat_f < lat_min or lat_f > lat_max:
        return False

    if lon_min <= lon_max:
        return lon_min <= lon_f <= lon_max
    # Antimeridian wrap
    return lon_f >= lon_min or lon_f <= lon_max


def derive_default_bbox(operator_lat: float,
                        operator_lon: float,
                        radius_km: float) -> dict:
    """Build a bbox of radius_km around (operator_lat, operator_lon).

    Flat-earth approximation — good enough at radii up to ~1000 km.
    Latitude clamps to [-90, 90]; longitude is allowed to fall outside
    [-180, 180] so antimeridian-crossing bboxes are representable and
    is_within_bbox can wrap them.
    """
    dlat = radius_km / _KM_PER_DEGREE_LAT
    cos_lat = math.cos(math.radians(operator_lat))
    if abs(cos_lat) < 1e-9:
        # Near the poles, longitude degree length collapses; widen to
        # cover the full circle rather than divide by zero.
        dlon = 180.0
    else:
        dlon = radius_km / (_KM_PER_DEGREE_LAT * cos_lat)

    lat_min = max(-90.0, operator_lat - dlat)
    lat_max = min(90.0, operator_lat + dlat)

    lon_min = operator_lon - dlon
    lon_max = operator_lon + dlon
    # Normalize longitudes into [-180, 180]; the wrap case is detected
    # by lon_min > lon_max after normalization (antimeridian crossing).
    while lon_min < -180.0:
        lon_min += 360.0
    while lon_min > 180.0:
        lon_min -= 360.0
    while lon_max < -180.0:
        lon_max += 360.0
    while lon_max > 180.0:
        lon_max -= 360.0

    return {
        "lat_min": lat_min,
        "lat_max": lat_max,
        "lon_min": lon_min,
        "lon_max": lon_max,
    }


def _load_operator_position() -> Optional[tuple]:
    """Read operator lat/lon from ~/.config/meshforge/operator_position.json.

    Returns (lat, lon) or None if file missing/malformed/coords invalid.
    Schema mirrors utils.gps_integration.Position.
    """
    path = get_real_user_home() / ".config" / "meshforge" / "operator_position.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        logger.debug(f"operator_position.json unreadable: {e}")
        return None
    lat = data.get("lat")
    lon = data.get("lon")
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat_f) and math.isfinite(lon_f)):
        return None
    if not (-90.0 <= lat_f <= 90.0 and -180.0 <= lon_f <= 180.0):
        return None
    return (lat_f, lon_f)


def load_external_bulk_bbox(settings) -> Optional[dict]:
    """Resolve the active external-bulk bbox from settings + operator position.

    Precedence:
      1. Explicit ``external_bulk_bbox`` setting (full dict) → used as-is.
      2. ``external_bulk_radius_km`` + operator position file → derived bbox.
      3. None → filter disabled (caller passes all features through).

    The None case is the fresh-install fallback: a box that hasn't set
    operator position yet shouldn't accidentally drop ingestion. Once
    the operator records a position (or sets external_bulk_bbox
    explicitly), the filter engages.
    """
    if settings is None:
        return None

    explicit = settings.get("external_bulk_bbox") if hasattr(settings, "get") else None
    if isinstance(explicit, dict):
        keys = {"lat_min", "lat_max", "lon_min", "lon_max"}
        if keys.issubset(explicit.keys()):
            return {k: float(explicit[k]) for k in keys}
        logger.warning(
            f"external_bulk_bbox missing required keys ({keys}); ignoring"
        )

    radius_km = settings.get("external_bulk_radius_km", 500) if hasattr(settings, "get") else 500
    try:
        radius_f = float(radius_km)
    except (TypeError, ValueError):
        radius_f = 500.0
    if radius_f <= 0:
        return None

    op_pos = _load_operator_position()
    if op_pos is None:
        return None
    lat, lon = op_pos
    return derive_default_bbox(lat, lon, radius_f)
