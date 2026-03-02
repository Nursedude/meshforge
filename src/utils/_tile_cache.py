"""Offline map tile caching for MeshForge coverage maps.

Extracted from coverage_map.py for file size compliance (CLAUDE.md #6).

Provides:
  OFFLINE_TILE_PROVIDERS   — Free tile provider URLs (OSM, OpenTopo, Stamen)
  TileCacheManager         — Download, cache, and manage offline map tiles
  get_tile_cache_dir()     — ~/.cache/meshforge/tiles (sudo-safe)
  download_tile()          — Fetch a single tile to disk
  cache_tiles_for_area()   — Bulk cache tiles for a geographic area
"""

import logging
import math
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


# Offline tile providers that don't require API keys
OFFLINE_TILE_PROVIDERS = {
    'openstreetmap': {
        'url': 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
        'attr': '© OpenStreetMap contributors',
        'name': 'OpenStreetMap',
    },
    'opentopomap': {
        'url': 'https://tile.opentopomap.org/{z}/{x}/{y}.png',
        'attr': '© OpenTopoMap (CC-BY-SA)',
        'name': 'OpenTopoMap',
    },
    'stamen_terrain': {
        'url': 'https://tiles.stadiamaps.com/tiles/stamen_terrain/{z}/{x}/{y}.png',
        'attr': '© Stadia Maps, Stamen Design, OpenStreetMap',
        'name': 'Terrain',
    },
}


class TileCacheManager:
    """
    Manages offline map tile caching for field use without internet.

    Inspired by meshtastic/standalone-ui FileLoader pattern.
    Provides tile download, storage, and retrieval for offline map viewing.

    Usage:
        cache = TileCacheManager()

        # Cache tiles for a location
        cache.cache_area(lat=21.3, lon=-157.8, radius_km=20)

        # Get cache statistics
        stats = cache.get_stats()
        print(f"Cached: {stats['tile_count']} tiles, {stats['size_mb']:.1f} MB")

        # Clear old tiles
        cache.clear(older_than_days=30)
    """

    # Default max cache size in MB (500 MB)
    DEFAULT_MAX_CACHE_MB = 500

    def __init__(self, provider: str = 'openstreetmap',
                 max_cache_mb: int = DEFAULT_MAX_CACHE_MB):
        self.provider = provider
        self._cache_dir = get_tile_cache_dir()
        self._max_cache_bytes = max_cache_mb * 1024 * 1024
        self._provider_info = OFFLINE_TILE_PROVIDERS.get(
            provider, OFFLINE_TILE_PROVIDERS['openstreetmap']
        )

    @property
    def cache_dir(self) -> Path:
        """Get the cache directory for current provider."""
        return self._cache_dir / self.provider

    def cache_area(self, lat: float, lon: float, radius_km: float = 10,
                   zoom_levels: Optional[List[int]] = None,
                   progress_callback: Optional[callable] = None) -> Dict[str, Any]:
        """
        Cache tiles for an area.

        Args:
            lat: Center latitude
            lon: Center longitude
            radius_km: Radius in kilometers
            zoom_levels: Zoom levels to cache (default: [8, 10, 12, 14])
            progress_callback: Optional callback(current, total, tile_path)

        Returns:
            Dict with caching results
        """
        if zoom_levels is None:
            zoom_levels = [8, 10, 12, 14]

        url_template = self._provider_info['url']
        result = {
            'tiles_cached': 0,
            'tiles_skipped': 0,
            'tiles_failed': 0,
            'total_size': 0,
            'zoom_levels': zoom_levels,
            'center': (lat, lon),
            'radius_km': radius_km
        }

        # Calculate total tiles for progress
        all_tiles = []
        for zoom in zoom_levels:
            tiles = _get_tiles_for_area(lat, lon, radius_km, zoom)
            all_tiles.extend([(zoom, x, y) for x, y in tiles])

        total = len(all_tiles)

        for i, (zoom, x, y) in enumerate(all_tiles):
            tile_path = self.cache_dir / str(zoom) / str(x) / f"{y}.png"

            if progress_callback:
                progress_callback(i + 1, total, str(tile_path))

            if tile_path.exists():
                result['tiles_skipped'] += 1
                result['total_size'] += tile_path.stat().st_size
                continue

            url = url_template.format(z=zoom, x=x, y=y)
            if download_tile(url, tile_path):
                result['tiles_cached'] += 1
                if tile_path.exists():
                    result['total_size'] += tile_path.stat().st_size
            else:
                result['tiles_failed'] += 1

        # Auto-enforce cache size limit
        self._enforce_cache_limit()

        return result

    def _enforce_cache_limit(self) -> None:
        """Remove oldest tiles if total cache exceeds max_cache_bytes.

        Prevents unbounded disk growth on resource-constrained systems.
        """
        if not self._cache_dir.exists():
            return

        try:
            # Collect all tiles with size and mtime
            tiles = []
            total_size = 0
            for tile_path in self._cache_dir.rglob("*.png"):
                try:
                    stat = tile_path.stat()
                    tiles.append((stat.st_mtime, stat.st_size, tile_path))
                    total_size += stat.st_size
                except OSError:
                    continue

            if total_size <= self._max_cache_bytes:
                return

            # Sort oldest first, remove until under limit
            tiles.sort()
            removed = 0
            for mtime, size, path in tiles:
                if total_size <= self._max_cache_bytes:
                    break
                try:
                    path.unlink()
                    total_size -= size
                    removed += 1
                except OSError:
                    continue

            if removed:
                logger.info(
                    f"Tile cache cleanup: removed {removed} oldest tiles "
                    f"to stay under {self._max_cache_bytes // (1024*1024)} MB limit"
                )
        except Exception:
            pass  # Cache enforcement is non-critical

    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dict with tile_count, size_bytes, size_mb, providers, oldest_tile, newest_tile
        """
        stats = {
            'tile_count': 0,
            'size_bytes': 0,
            'size_mb': 0.0,
            'providers': [],
            'oldest_tile': None,
            'newest_tile': None,
            'zoom_levels': set()
        }

        if not self._cache_dir.exists():
            return stats

        oldest_time = None
        newest_time = None

        for provider_dir in self._cache_dir.iterdir():
            if provider_dir.is_dir():
                stats['providers'].append(provider_dir.name)

                for tile_path in provider_dir.rglob("*.png"):
                    stats['tile_count'] += 1
                    stats['size_bytes'] += tile_path.stat().st_size

                    mtime = tile_path.stat().st_mtime
                    if oldest_time is None or mtime < oldest_time:
                        oldest_time = mtime
                    if newest_time is None or mtime > newest_time:
                        newest_time = mtime

                    # Extract zoom level from path
                    try:
                        zoom = int(tile_path.parent.parent.name)
                        stats['zoom_levels'].add(zoom)
                    except (ValueError, AttributeError):
                        pass

        stats['size_mb'] = stats['size_bytes'] / (1024 * 1024)
        stats['zoom_levels'] = sorted(stats['zoom_levels'])

        if oldest_time:
            stats['oldest_tile'] = datetime.fromtimestamp(oldest_time).isoformat()
        if newest_time:
            stats['newest_tile'] = datetime.fromtimestamp(newest_time).isoformat()

        return stats

    def clear(self, older_than_days: Optional[int] = None,
              provider: Optional[str] = None) -> Dict[str, int]:
        """
        Clear cached tiles.

        Args:
            older_than_days: Only clear tiles older than N days (None = all)
            provider: Only clear specific provider (None = all)

        Returns:
            Dict with 'tiles_removed' and 'bytes_freed'
        """
        import shutil

        result = {'tiles_removed': 0, 'bytes_freed': 0}

        if not self._cache_dir.exists():
            return result

        cutoff_time = None
        if older_than_days is not None:
            cutoff_time = datetime.now().timestamp() - (older_than_days * 86400)

        dirs_to_check = []
        if provider:
            provider_dir = self._cache_dir / provider
            if provider_dir.exists():
                dirs_to_check.append(provider_dir)
        else:
            dirs_to_check = [d for d in self._cache_dir.iterdir() if d.is_dir()]

        for provider_dir in dirs_to_check:
            for tile_path in list(provider_dir.rglob("*.png")):
                should_remove = True

                if cutoff_time is not None:
                    if tile_path.stat().st_mtime >= cutoff_time:
                        should_remove = False

                if should_remove:
                    result['bytes_freed'] += tile_path.stat().st_size
                    tile_path.unlink()
                    result['tiles_removed'] += 1

            # Clean up empty directories
            for dirpath in sorted(provider_dir.rglob("*"), reverse=True):
                if dirpath.is_dir() and not any(dirpath.iterdir()):
                    dirpath.rmdir()

        return result

    def get_tile_path(self, zoom: int, x: int, y: int) -> Optional[Path]:
        """
        Get path to a cached tile, or None if not cached.

        Args:
            zoom: Zoom level
            x: Tile X coordinate
            y: Tile Y coordinate

        Returns:
            Path to tile file if cached, None otherwise
        """
        tile_path = self.cache_dir / str(zoom) / str(x) / f"{y}.png"
        return tile_path if tile_path.exists() else None

    def is_area_cached(self, lat: float, lon: float, radius_km: float = 5,
                       zoom: int = 12) -> Tuple[bool, float]:
        """
        Check if an area is cached.

        Args:
            lat: Center latitude
            lon: Center longitude
            radius_km: Radius to check
            zoom: Zoom level to check

        Returns:
            Tuple of (is_fully_cached, coverage_percent)
        """
        tiles = _get_tiles_for_area(lat, lon, radius_km, zoom)
        cached = sum(1 for x, y in tiles if self.get_tile_path(zoom, x, y))

        if not tiles:
            return True, 100.0

        coverage = (cached / len(tiles)) * 100
        return cached == len(tiles), coverage


def get_tile_cache_dir() -> Path:
    """Get the tile cache directory."""
    cache_dir = get_real_user_home() / ".cache" / "meshforge" / "tiles"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def download_tile(url: str, cache_path: Path) -> bool:
    """Download a tile to cache."""
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={'User-Agent': 'MeshForge/1.0'})
        with urllib.request.urlopen(req, timeout=15) as response:
            cache_path.write_bytes(response.read())
        return True
    except Exception as e:
        logger.debug(f"Failed to download tile: {e}")
        return False


def cache_tiles_for_area(lat: float, lon: float, radius_km: float = 10,
                         zoom_levels: List[int] = None,
                         provider: str = 'openstreetmap') -> Dict:
    """
    Pre-cache tiles for an area for offline use.

    Args:
        lat: Center latitude
        lon: Center longitude
        radius_km: Radius in kilometers to cache
        zoom_levels: List of zoom levels to cache (default: [8, 10, 12, 14])
        provider: Tile provider name

    Returns:
        Dict with 'tiles_cached', 'tiles_failed', 'total_size' keys
    """
    if zoom_levels is None:
        zoom_levels = [8, 10, 12, 14]

    provider_info = OFFLINE_TILE_PROVIDERS.get(provider, OFFLINE_TILE_PROVIDERS['openstreetmap'])
    url_template = provider_info['url']
    cache_dir = get_tile_cache_dir() / provider

    result = {'tiles_cached': 0, 'tiles_failed': 0, 'total_size': 0}

    for zoom in zoom_levels:
        # Calculate tile range for this zoom level
        tiles = _get_tiles_for_area(lat, lon, radius_km, zoom)

        for x, y in tiles:
            tile_path = cache_dir / str(zoom) / str(x) / f"{y}.png"

            if tile_path.exists():
                result['tiles_cached'] += 1
                continue

            url = url_template.format(z=zoom, x=x, y=y)
            if download_tile(url, tile_path):
                result['tiles_cached'] += 1
                if tile_path.exists():
                    result['total_size'] += tile_path.stat().st_size
            else:
                result['tiles_failed'] += 1

    return result


def _get_tiles_for_area(lat: float, lon: float, radius_km: float, zoom: int) -> List[Tuple[int, int]]:
    """Get list of tile coordinates covering an area."""
    # Convert radius to degrees (approximate)
    lat_deg = radius_km / 111.0
    cos_lat = max(math.cos(math.radians(lat)), 0.01)
    lon_deg = radius_km / (111.0 * cos_lat)

    min_lat, max_lat = lat - lat_deg, lat + lat_deg
    min_lon, max_lon = lon - lon_deg, lon + lon_deg

    # Get tile bounds (note: y increases southward)
    x_min, _ = _latlon_to_tile(min_lat, min_lon, zoom)
    x_max, _ = _latlon_to_tile(max_lat, max_lon, zoom)
    _, y_min = _latlon_to_tile(max_lat, min_lon, zoom)
    _, y_max = _latlon_to_tile(min_lat, max_lon, zoom)

    # Ensure correct ordering
    if x_min > x_max:
        x_min, x_max = x_max, x_min
    if y_min > y_max:
        y_min, y_max = y_max, y_min

    tiles = []
    for x in range(x_min, x_max + 1):
        for y in range(y_min, y_max + 1):
            tiles.append((x, y))

    return tiles


def _latlon_to_tile(lat: float, lon: float, zoom: int) -> Tuple[int, int]:
    """Convert lat/lon to tile coordinates."""
    n = 2 ** zoom
    x = int((lon + 180) / 360 * n) % n
    # Clamp to Mercator limits to avoid math domain errors
    lat = max(-85.0511, min(85.0511, lat))
    lat_rad = math.radians(lat)
    y = int((1 - math.log(math.tan(lat_rad) + 1/math.cos(lat_rad)) / math.pi) / 2 * n)
    return x, y
