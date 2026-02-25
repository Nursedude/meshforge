"""
Tile Cache Module for MeshForge.

Provides geographic tile downloading and caching for offline map use.
Supports region-based downloads with bounding boxes, zoom ranges,
rate limiting, and dateline-crossing regions.

Key features:
- Mercator projection coordinate conversion
- Region-based tile enumeration with dateline handling
- Size-bounded downloads (max 2 MB per tile)
- Thread-safe rate limiting
- Tile expiration and cache statistics

Usage:
    from utils.tile_cache import TileCache, BoundingBox

    cache = TileCache()
    result = cache.download_region(
        bounds=(21.0, -158.5, 21.7, -157.5),
        zoom_range=(8, 14)
    )
    print(f"Downloaded: {result['downloaded']} tiles")
"""

import logging
import math
import time
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)

# User agent for tile requests (be a good citizen)
USER_AGENT = 'MeshForge/0.4.7 (mesh network operations; offline caching)'

# Tile expiration (days)
TILE_EXPIRY_DAYS = 30

# Maximum tile size (bytes) - prevents memory exhaustion from malicious servers
MAX_TILE_BYTES = 2 * 1024 * 1024  # 2 MB

# Mercator projection latitude limit (~85.05 degrees)
MAX_MERCATOR_LAT = 85.0511287798

# Default zoom range
DEFAULT_ZOOM_MIN = 8
DEFAULT_ZOOM_MAX = 14

# Maximum tiles per download session (safety limit)
MAX_TILES_PER_SESSION = 10000

# Hawaii bounds (default for MeshForge development)
HAWAII_BOUNDS = (18.5, -160.5, 22.5, -154.5)  # (south, west, north, east)

# Tile providers (no API key required)
TILE_PROVIDERS = {
    'openstreetmap': {
        'url': 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
        'attr': '(C) OpenStreetMap contributors',
        'name': 'OpenStreetMap',
    },
    'opentopomap': {
        'url': 'https://tile.opentopomap.org/{z}/{x}/{y}.png',
        'attr': '(C) OpenTopoMap (CC-BY-SA)',
        'name': 'OpenTopoMap',
    },
    'stamen_terrain': {
        'url': 'https://tiles.stadiamaps.com/tiles/stamen_terrain/{z}/{x}/{y}.png',
        'attr': '(C) Stadia Maps, Stamen Design, OpenStreetMap',
        'name': 'Terrain',
    },
}


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class BoundingBox:
    """Geographic bounding box (south, west, north, east).

    Handles dateline-crossing boxes where west > east in longitude.
    """
    south: float
    west: float
    north: float
    east: float

    @classmethod
    def from_tuple(cls, bounds: Tuple[float, float, float, float]) -> 'BoundingBox':
        """Create from (south, west, north, east) tuple."""
        return cls(south=bounds[0], west=bounds[1],
                   north=bounds[2], east=bounds[3])

    @property
    def is_valid(self) -> bool:
        """Check if bounding box has valid coordinates."""
        return (
            -90 <= self.south <= 90 and
            -90 <= self.north <= 90 and
            -180 <= self.west <= 180 and
            -180 <= self.east <= 180 and
            self.south < self.north
        )


# =============================================================================
# Coordinate Conversion
# =============================================================================


def lon_to_tile_x(lon: float, zoom: int) -> int:
    """Convert longitude to tile X coordinate.

    Args:
        lon: Longitude in degrees (-180 to 180).
        zoom: Zoom level.

    Returns:
        Tile X coordinate.
    """
    n = 2 ** zoom
    x = (lon + 180.0) / 360.0 * n
    return int(x) % n


def lat_to_tile_y(lat: float, zoom: int) -> int:
    """Convert latitude to tile Y coordinate.

    Args:
        lat: Latitude in degrees (clamped to Mercator limits ~+/-85.05).
        zoom: Zoom level.

    Returns:
        Tile Y coordinate.
    """
    # Clamp to Mercator projection limits to avoid math domain errors
    lat = max(-MAX_MERCATOR_LAT, min(MAX_MERCATOR_LAT, lat))
    n = 2 ** zoom
    lat_rad = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return int(y)


def tile_to_lon(x: int, zoom: int) -> float:
    """Convert tile X coordinate to longitude (west edge).

    Args:
        x: Tile X coordinate.
        zoom: Zoom level.

    Returns:
        Longitude in degrees.
    """
    n = 2 ** zoom
    return x / n * 360.0 - 180.0


def tile_to_lat(y: int, zoom: int) -> float:
    """Convert tile Y coordinate to latitude (north edge).

    Args:
        y: Tile Y coordinate.
        zoom: Zoom level.

    Returns:
        Latitude in degrees.
    """
    n = 2 ** zoom
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    return math.degrees(lat_rad)


# =============================================================================
# Region Tile Enumeration
# =============================================================================


def count_tiles_in_region(bbox: BoundingBox, zoom_min: int, zoom_max: int) -> int:
    """Count total tiles in a region across zoom levels.

    Handles dateline-crossing bounding boxes (west > east in tile coords).

    Args:
        bbox: Geographic bounding box.
        zoom_min: Minimum zoom level.
        zoom_max: Maximum zoom level.

    Returns:
        Total number of tiles.
    """
    total = 0
    for z in range(zoom_min, zoom_max + 1):
        x_min = lon_to_tile_x(bbox.west, z)
        x_max = lon_to_tile_x(bbox.east, z)
        y_min = lat_to_tile_y(bbox.north, z)  # Note: y increases southward
        y_max = lat_to_tile_y(bbox.south, z)

        n = 2 ** z
        # Handle dateline crossing (west > east in tile coords)
        if x_min <= x_max:
            x_count = x_max - x_min + 1
        else:
            x_count = (n - x_min) + (x_max + 1)
        total += x_count * (y_max - y_min + 1)
    return total


def get_tiles_for_region(bounds: BoundingBox,
                         zoom: int) -> List[Tuple[int, int, int]]:
    """Get list of (z, x, y) tile coordinates for a region at one zoom level.

    Handles dateline-crossing bounding boxes (west > east in tile coords).

    Args:
        bounds: Geographic bounding box.
        zoom: Zoom level.

    Returns:
        List of (z, x, y) tuples.
    """
    x_min = lon_to_tile_x(bounds.west, zoom)
    x_max = lon_to_tile_x(bounds.east, zoom)
    y_min = lat_to_tile_y(bounds.north, zoom)
    y_max = lat_to_tile_y(bounds.south, zoom)

    n = 2 ** zoom
    tiles = []
    # Handle dateline crossing
    if x_min <= x_max:
        x_range = range(x_min, x_max + 1)
    else:
        x_range = list(range(x_min, n)) + list(range(0, x_max + 1))

    for x in x_range:
        for y in range(y_min, y_max + 1):
            tiles.append((zoom, x, y))
    return tiles


# =============================================================================
# Tile Cache
# =============================================================================


class TileCache:
    """Thread-safe tile cache with rate limiting and size bounds.

    Manages downloading, storing, and retrieving map tiles for offline use.
    Respects tile server rate limits and enforces per-tile size limits.

    Usage:
        cache = TileCache()
        result = cache.download_region(
            bounds=(21.0, -158.5, 21.7, -157.5),
            zoom_range=(8, 14)
        )
    """

    def __init__(self, provider: str = 'openstreetmap',
                 cache_dir: Optional[Path] = None,
                 rate_limit: float = 0.1):
        """Initialize tile cache.

        Args:
            provider: Tile provider key (from TILE_PROVIDERS).
            cache_dir: Override cache directory (default: auto-detect).
            rate_limit: Minimum seconds between requests.
        """
        self._provider = provider
        self._provider_info = TILE_PROVIDERS.get(
            provider, TILE_PROVIDERS['openstreetmap']
        )
        self._cache_dir = cache_dir or self._get_default_dir()
        self._rate_limit = rate_limit
        self._last_request = 0.0
        self._lock = threading.Lock()

    @staticmethod
    def _get_default_dir() -> Path:
        """Get default cache directory with sudo awareness."""
        data_dir = get_real_user_home() / ".local" / "share" / "meshforge"
        return data_dir / "tiles"

    def _download_tile(self, z: int, x: int, y: int) -> bool:
        """Download a single tile to cache.

        Enforces rate limiting and size bounds. Skips tiles already cached.

        Args:
            z: Zoom level.
            x: Tile X coordinate.
            y: Tile Y coordinate.

        Returns:
            True if tile was downloaded (or already cached), False on failure.
        """
        tile_path = self._cache_dir / self._provider / str(z) / str(x) / f"{y}.png"
        if tile_path.exists():
            return True

        # Rate limiting (sleep outside lock to avoid blocking other threads)
        with self._lock:
            elapsed = time.time() - self._last_request
            sleep_time = max(0.0, self._rate_limit - elapsed)
            self._last_request = time.time() + sleep_time

        if sleep_time > 0:
            time.sleep(sleep_time)

        url = self._provider_info['url'].format(z=z, x=x, y=y)
        try:
            tile_path.parent.mkdir(parents=True, exist_ok=True)
            req = Request(url, headers={'User-Agent': USER_AGENT})
            with urlopen(req, timeout=10) as response:
                data = response.read(MAX_TILE_BYTES + 1)
                if len(data) > MAX_TILE_BYTES:
                    logger.warning(f"Tile {z}/{x}/{y}: response too large, skipping")
                    return False
                if len(data) < 100:
                    logger.debug(f"Tile {z}/{x}/{y}: suspiciously small ({len(data)} bytes)")
                    return False
                tile_path.write_bytes(data)
                return True
        except Exception as e:
            logger.debug(f"Tile {z}/{x}/{y} download failed: {e}")
            return False

    def download_region(self, bounds: Tuple[float, float, float, float],
                        zoom_range: Tuple[int, int] = (DEFAULT_ZOOM_MIN, DEFAULT_ZOOM_MAX),
                        progress_callback: Optional[Callable] = None) -> dict:
        """Download all tiles for a geographic region.

        Args:
            bounds: (south, west, north, east) bounding box.
            zoom_range: (min_zoom, max_zoom) inclusive.
            progress_callback: Optional callback(current, total).

        Returns:
            Dict with 'downloaded', 'skipped', 'failed', 'error' keys.
        """
        bbox = BoundingBox.from_tuple(bounds)
        if not bbox.is_valid:
            return {'error': 'Invalid bounding box', 'downloaded': 0}

        zoom_min, zoom_max = zoom_range
        if zoom_min < 0 or zoom_max > 19 or zoom_min > zoom_max:
            return {'error': f'Invalid zoom range ({zoom_min}, {zoom_max})',
                    'downloaded': 0}

        total_tiles = count_tiles_in_region(bbox, zoom_min, zoom_max)
        if total_tiles > MAX_TILES_PER_SESSION:
            return {'error': f'Too many tiles ({total_tiles} > {MAX_TILES_PER_SESSION})',
                    'downloaded': 0}

        result = {'downloaded': 0, 'skipped': 0, 'failed': 0, 'total': total_tiles}
        current = 0

        for z in range(zoom_min, zoom_max + 1):
            tiles = get_tiles_for_region(bbox, z)
            for zz, x, y in tiles:
                current += 1
                tile_path = self._cache_dir / self._provider / str(z) / str(x) / f"{y}.png"
                if tile_path.exists():
                    result['skipped'] += 1
                elif self._download_tile(z, x, y):
                    result['downloaded'] += 1
                else:
                    result['failed'] += 1

                if progress_callback:
                    progress_callback(current, total_tiles)

        return result

    def get_tile_path(self, z: int, x: int, y: int) -> Optional[Path]:
        """Get path to cached tile, or None if not cached.

        Args:
            z: Zoom level.
            x: Tile X coordinate.
            y: Tile Y coordinate.

        Returns:
            Path if tile exists in cache, None otherwise.
        """
        tile_path = self._cache_dir / self._provider / str(z) / str(x) / f"{y}.png"
        return tile_path if tile_path.exists() else None

    def get_stats(self) -> dict:
        """Get cache statistics.

        Returns:
            Dict with tile_count, total_bytes, size_mb, oldest, newest.
        """
        tile_count = 0
        total_bytes = 0
        oldest = None
        newest = None

        provider_dir = self._cache_dir / self._provider
        if not provider_dir.exists():
            return {'tile_count': 0, 'total_bytes': 0, 'size_mb': 0.0,
                    'oldest': None, 'newest': None}

        for png_file in provider_dir.rglob("*.png"):
            tile_count += 1
            st = png_file.stat()
            total_bytes += st.st_size
            mtime = st.st_mtime
            if oldest is None or mtime < oldest:
                oldest = mtime
            if newest is None or mtime > newest:
                newest = mtime

        return {
            'tile_count': tile_count,
            'total_bytes': total_bytes,
            'size_mb': total_bytes / (1024 * 1024),
            'oldest': datetime.fromtimestamp(oldest).isoformat() if oldest else None,
            'newest': datetime.fromtimestamp(newest).isoformat() if newest else None,
        }

    def clear_expired(self, max_age_days: int = TILE_EXPIRY_DAYS) -> dict:
        """Remove tiles older than max_age_days.

        Args:
            max_age_days: Maximum age in days before removal.

        Returns:
            Dict with 'removed' count and 'bytes_freed'.
        """
        cutoff = time.time() - (max_age_days * 86400)
        removed = 0
        bytes_freed = 0

        provider_dir = self._cache_dir / self._provider
        if not provider_dir.exists():
            return {'removed': 0, 'bytes_freed': 0}

        for png_file in list(provider_dir.rglob("*.png")):
            st = png_file.stat()
            if st.st_mtime < cutoff:
                bytes_freed += st.st_size
                png_file.unlink()
                removed += 1

        # Clean empty directories
        for dirpath in sorted(provider_dir.rglob("*"), reverse=True):
            if dirpath.is_dir() and not any(dirpath.iterdir()):
                dirpath.rmdir()

        return {'removed': removed, 'bytes_freed': bytes_freed}

    @staticmethod
    def estimate_download_size(bounds: Tuple[float, float, float, float],
                               zoom_range: Tuple[int, int] = (DEFAULT_ZOOM_MIN, DEFAULT_ZOOM_MAX),
                               avg_tile_kb: float = 50.0) -> dict:
        """Estimate download size for a region without downloading.

        Args:
            bounds: (south, west, north, east) bounding box.
            zoom_range: (min_zoom, max_zoom) inclusive.
            avg_tile_kb: Assumed average tile size in KB.

        Returns:
            Dict with total_tiles, per_zoom, estimated_mb, within_limit.
        """
        bbox = BoundingBox.from_tuple(bounds)
        if not bbox.is_valid:
            return {'total_tiles': 0, 'per_zoom': {},
                    'estimated_mb': 0.0, 'within_limit': True}

        zoom_min, zoom_max = zoom_range
        if zoom_min < 0 or zoom_max > 19 or zoom_min > zoom_max:
            return {'total_tiles': 0, 'per_zoom': {},
                    'estimated_mb': 0.0, 'within_limit': True}

        per_zoom = {}
        total = 0
        for z in range(zoom_min, zoom_max + 1):
            count = count_tiles_in_region(bbox, z, z)
            per_zoom[z] = count
            total += count

        estimated_mb = (total * avg_tile_kb) / 1024.0
        return {
            'total_tiles': total,
            'per_zoom': per_zoom,
            'estimated_mb': round(estimated_mb, 2),
            'within_limit': total <= MAX_TILES_PER_SESSION,
        }
