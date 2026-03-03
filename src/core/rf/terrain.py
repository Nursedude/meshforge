"""
Terrain Elevation and Line-of-Sight Analysis.

Provides elevation data from SRTM (Shuttle Radar Topography Mission)
and line-of-sight calculations for terrain-based coverage prediction.

Usage:
    from utils.terrain import SRTMProvider, LOSAnalyzer

    # Get elevation at a point
    provider = SRTMProvider()
    elev = provider.get_elevation(19.7, -155.08)  # Mauna Kea area

    # Check line of sight between two points
    los = LOSAnalyzer(provider)
    result = los.analyze(
        lat1=19.72, lon1=-155.08, alt1=10,  # Node A (10m antenna)
        lat2=19.80, lon2=-155.10, alt2=5,   # Node B (5m antenna)
        freq_mhz=915.0
    )
    print(result.is_clear)      # True/False
    print(result.terrain_loss)  # dB of additional loss from terrain
"""

import logging
import math
import struct
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.safe_import import safe_import
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)

# Import existing RF calculations
(
    _haversine_distance, _earth_bulge, _fresnel_radius,
    _knife_edge_diffraction, _multi_obstacle_loss, _free_space_path_loss,
    _HAS_RF,
) = safe_import(
    'utils.rf',
    'haversine_distance', 'earth_bulge', 'fresnel_radius',
    'knife_edge_diffraction', 'multi_obstacle_loss', 'free_space_path_loss',
)

if _HAS_RF:
    haversine_distance = _haversine_distance
    earth_bulge = _earth_bulge
    fresnel_radius = _fresnel_radius
    knife_edge_diffraction = _knife_edge_diffraction
    multi_obstacle_loss = _multi_obstacle_loss
    free_space_path_loss = _free_space_path_loss
else:
    # Fallback implementations for standalone use
    def haversine_distance(lat1, lon1, lat2, lon2):
        R = 6371000
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

    def earth_bulge(distance_m):
        R = 6371000
        k = 4/3
        return distance_m**2 / (8 * R * k)

    def fresnel_radius(distance_km, freq_ghz):
        return 17.3 * math.sqrt(distance_km / (4 * freq_ghz))

    def knife_edge_diffraction(distance_m, obstacle_height_m, freq_mhz, obstacle_position=0.5):
        wavelength = 300 / freq_mhz
        d1 = distance_m * obstacle_position
        d2 = distance_m * (1 - obstacle_position)
        v = obstacle_height_m * math.sqrt(2 * (d1 + d2) / (wavelength * d1 * d2))
        if v <= -0.78:
            return 0
        return 6.9 + 20 * math.log10(math.sqrt((v - 0.1)**2 + 1) + v - 0.1)

    def multi_obstacle_loss(distance_m, obstacles, freq_mhz):
        total = 0
        for pos, height in obstacles:
            if height > 0:
                total += knife_edge_diffraction(distance_m, height, freq_mhz, pos)
        return total

    def free_space_path_loss(distance_m, freq_mhz):
        if distance_m <= 0 or freq_mhz <= 0:
            return 0
        return 20*math.log10(distance_m) + 20*math.log10(freq_mhz) - 27.55


# ============================================================================
# ELEVATION DATA PROVIDERS
# ============================================================================

class TerrainProvider(ABC):
    """Abstract base class for terrain elevation data sources."""

    @abstractmethod
    def get_elevation(self, lat: float, lon: float) -> float:
        """Get ground elevation at a point.

        Args:
            lat: Latitude in decimal degrees.
            lon: Longitude in decimal degrees.

        Returns:
            Elevation in meters above sea level.
        """
        ...

    def get_profile(self, lat1: float, lon1: float,
                    lat2: float, lon2: float,
                    num_points: int = 100) -> List[float]:
        """Get elevation profile along a path.

        Args:
            lat1, lon1: Start point coordinates.
            lat2, lon2: End point coordinates.
            num_points: Number of sample points along the path.

        Returns:
            List of elevations (meters) from start to end.
        """
        profile = []
        for i in range(num_points):
            t = i / max(1, num_points - 1)
            lat = lat1 + t * (lat2 - lat1)
            lon = lon1 + t * (lon2 - lon1)
            profile.append(self.get_elevation(lat, lon))
        return profile


class FlatTerrainProvider(TerrainProvider):
    """Returns constant elevation (for testing and areas without data)."""

    def __init__(self, elevation: float = 0.0):
        self._elevation = elevation

    def get_elevation(self, lat: float, lon: float) -> float:
        return self._elevation


class SyntheticTerrainProvider(TerrainProvider):
    """Generates synthetic terrain for testing.

    Creates reproducible terrain patterns based on coordinates,
    useful for unit testing LOS calculations without real data.
    """

    def __init__(self, base_elevation: float = 100.0,
                 ridge_height: float = 50.0,
                 ridge_spacing_deg: float = 0.01):
        self._base = base_elevation
        self._ridge_height = ridge_height
        self._spacing = ridge_spacing_deg

    def get_elevation(self, lat: float, lon: float) -> float:
        # Create a repeating ridge pattern
        phase = (lat + lon) / self._spacing
        return self._base + self._ridge_height * abs(math.sin(phase * math.pi))


class SRTMProvider(TerrainProvider):
    """SRTM elevation data provider.

    Downloads and caches SRTM HGT tiles (1 arc-second resolution,
    ~30m per pixel). Tiles are ~25MB each and cover 1 degree x 1 degree.

    Data source: NASA SRTM via AWS Open Data or CGIAR-CSI mirrors.
    """

    # SRTM data URLs (multiple fallback sources)
    SRTM_URLS = [
        "https://elevation-tiles-prod.s3.amazonaws.com/skadi/{ns}{lat:02d}/{ns}{lat:02d}{ew}{lon:03d}.hgt.gz",
    ]

    # HGT file specifications
    SRTM1_SAMPLES = 3601  # 1 arc-second (SRTM1)
    SRTM3_SAMPLES = 1201  # 3 arc-second (SRTM3)

    def __init__(self, cache_dir: Optional[Path] = None,
                 auto_download: bool = True):
        """Initialize SRTM provider.

        Args:
            cache_dir: Directory for cached HGT files.
                      Default: ~/.local/share/meshforge/srtm/
            auto_download: Whether to download tiles automatically.
        """
        if cache_dir is None:
            cache_dir = get_real_user_home() / ".local" / "share" / "meshforge" / "srtm"
        cache_dir.mkdir(parents=True, exist_ok=True)

        self._cache_dir = cache_dir
        self._auto_download = auto_download
        self._tile_cache: Dict[str, Optional[bytes]] = {}
        self._lock = threading.Lock()

    def get_elevation(self, lat: float, lon: float) -> float:
        """Get elevation from SRTM data.

        Returns 0.0 if tile is not available and auto_download is False.
        """
        tile_data = self._get_tile(lat, lon)
        if tile_data is None:
            return 0.0

        return self._interpolate(tile_data, lat, lon)

    def _get_tile_name(self, lat: float, lon: float) -> str:
        """Get SRTM tile filename for a coordinate."""
        lat_int = int(math.floor(lat))
        lon_int = int(math.floor(lon))

        ns = "N" if lat_int >= 0 else "S"
        ew = "E" if lon_int >= 0 else "W"

        return f"{ns}{abs(lat_int):02d}{ew}{abs(lon_int):03d}.hgt"

    def _get_tile(self, lat: float, lon: float) -> Optional[bytes]:
        """Get tile data, downloading if necessary."""
        tile_name = self._get_tile_name(lat, lon)

        with self._lock:
            if tile_name in self._tile_cache:
                return self._tile_cache[tile_name]

        # Check disk cache
        tile_path = self._cache_dir / tile_name
        if tile_path.exists():
            data = tile_path.read_bytes()
            with self._lock:
                self._tile_cache[tile_name] = data
            return data

        # Check for gzipped version
        gz_path = self._cache_dir / f"{tile_name}.gz"
        if gz_path.exists():
            import gzip
            with gzip.open(gz_path, 'rb') as f:
                data = f.read()
            # Cache uncompressed
            tile_path.write_bytes(data)
            with self._lock:
                self._tile_cache[tile_name] = data
            return data

        # Download if allowed
        if self._auto_download:
            data = self._download_tile(lat, lon)
            if data:
                tile_path.write_bytes(data)
                with self._lock:
                    self._tile_cache[tile_name] = data
                return data

        # No data available
        with self._lock:
            self._tile_cache[tile_name] = None
        return None

    def _download_tile(self, lat: float, lon: float) -> Optional[bytes]:
        """Download an SRTM tile from the web."""
        lat_int = int(math.floor(lat))
        lon_int = int(math.floor(lon))

        ns = "N" if lat_int >= 0 else "S"
        ew = "E" if lon_int >= 0 else "W"

        for url_template in self.SRTM_URLS:
            url = url_template.format(
                ns=ns, lat=abs(lat_int),
                ew=ew, lon=abs(lon_int)
            )
            try:
                import urllib.request
                import gzip

                logger.info(f"Downloading SRTM tile: {url}")
                req = urllib.request.Request(url, headers={'User-Agent': 'MeshForge/0.4'})
                with urllib.request.urlopen(req, timeout=30) as response:
                    compressed = response.read()

                # Decompress
                data = gzip.decompress(compressed)
                logger.info(f"Downloaded SRTM tile ({len(data)} bytes)")
                return data

            except Exception as e:
                logger.debug(f"SRTM download failed from {url}: {e}")
                continue

        logger.warning(f"Could not download SRTM tile for ({lat}, {lon})")
        return None

    def _interpolate(self, tile_data: bytes, lat: float, lon: float) -> float:
        """Bilinear interpolation of elevation from tile data."""
        # Determine tile resolution
        data_len = len(tile_data)
        if data_len == self.SRTM1_SAMPLES * self.SRTM1_SAMPLES * 2:
            samples = self.SRTM1_SAMPLES
        elif data_len == self.SRTM3_SAMPLES * self.SRTM3_SAMPLES * 2:
            samples = self.SRTM3_SAMPLES
        else:
            # Unknown format, try SRTM3
            samples = self.SRTM3_SAMPLES

        lat_int = int(math.floor(lat))
        lon_int = int(math.floor(lon))

        # Fractional position within tile (0-1)
        lat_frac = lat - lat_int
        lon_frac = lon - lon_int

        # Convert to pixel coordinates
        # SRTM tiles are stored with row 0 = north edge
        row = (1 - lat_frac) * (samples - 1)
        col = lon_frac * (samples - 1)

        row_i = int(row)
        col_i = int(col)
        row_f = row - row_i
        col_f = col - col_i

        # Clamp to valid range
        row_i = min(row_i, samples - 2)
        col_i = min(col_i, samples - 2)

        # Read 4 surrounding samples (big-endian int16)
        def read_sample(r, c):
            idx = (r * samples + c) * 2
            if idx + 2 > len(tile_data):
                return 0
            val = struct.unpack('>h', tile_data[idx:idx+2])[0]
            # SRTM void value
            if val == -32768:
                return 0
            return val

        z00 = read_sample(row_i, col_i)
        z01 = read_sample(row_i, col_i + 1)
        z10 = read_sample(row_i + 1, col_i)
        z11 = read_sample(row_i + 1, col_i + 1)

        # Bilinear interpolation
        z = (z00 * (1 - row_f) * (1 - col_f) +
             z01 * (1 - row_f) * col_f +
             z10 * row_f * (1 - col_f) +
             z11 * row_f * col_f)

        return float(z)

    def get_cached_tiles(self) -> List[str]:
        """List cached tile files."""
        return [f.name for f in self._cache_dir.glob("*.hgt")]

    def get_cache_size_mb(self) -> float:
        """Get total size of cached tiles in MB."""
        total = sum(f.stat().st_size for f in self._cache_dir.glob("*.hgt*"))
        return total / (1024 * 1024)


# ============================================================================
# LINE-OF-SIGHT ANALYSIS
# ============================================================================

class LOSResult:
    """Result of a line-of-sight analysis between two points."""

    def __init__(self):
        self.is_clear: bool = True
        self.terrain_loss_db: float = 0.0
        self.fspl_db: float = 0.0
        self.total_loss_db: float = 0.0
        self.distance_m: float = 0.0
        self.num_obstructions: int = 0
        self.worst_obstruction_m: float = 0.0  # Height above LOS
        self.fresnel_clearance_pct: float = 100.0  # % of first Fresnel zone clear
        self.elevation_profile: List[float] = []
        self.los_heights: List[float] = []  # LOS line elevation at each point
        self.earth_bulge_m: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API/display."""
        return {
            "is_clear": self.is_clear,
            "terrain_loss_db": round(self.terrain_loss_db, 1),
            "fspl_db": round(self.fspl_db, 1),
            "total_loss_db": round(self.total_loss_db, 1),
            "distance_m": round(self.distance_m, 1),
            "num_obstructions": self.num_obstructions,
            "worst_obstruction_m": round(self.worst_obstruction_m, 1),
            "fresnel_clearance_pct": round(self.fresnel_clearance_pct, 1),
            "earth_bulge_m": round(self.earth_bulge_m, 1),
        }


class LOSAnalyzer:
    """Line-of-sight analyzer using terrain elevation data.

    Determines if two points have clear line of sight, accounting for:
    - Actual ground elevation between points
    - Earth curvature (4/3 effective radius model)
    - Fresnel zone clearance requirements
    - Knife-edge diffraction losses from obstructions
    """

    # Minimum Fresnel clearance for "clear" LOS (60%)
    FRESNEL_CLEARANCE_THRESHOLD = 0.6

    def __init__(self, provider: TerrainProvider,
                 profile_points: int = 100):
        """Initialize LOS analyzer.

        Args:
            provider: Terrain elevation data provider.
            profile_points: Number of samples along the path.
        """
        self._provider = provider
        self._profile_points = profile_points

    def analyze(self, lat1: float, lon1: float, alt1: float,
                lat2: float, lon2: float, alt2: float,
                freq_mhz: float = 915.0) -> LOSResult:
        """Analyze line of sight between two points.

        Args:
            lat1, lon1: Point A coordinates (decimal degrees).
            alt1: Point A antenna height above ground (meters).
            lat2, lon2: Point B coordinates (decimal degrees).
            alt2: Point B antenna height above ground (meters).
            freq_mhz: Frequency in MHz (default 915 for US LoRa).

        Returns:
            LOSResult with clearance analysis and estimated losses.
        """
        result = LOSResult()

        # Calculate distance
        result.distance_m = haversine_distance(lat1, lon1, lat2, lon2)
        if result.distance_m < 1:
            result.is_clear = True
            return result

        # Get elevation profile
        profile = self._provider.get_profile(
            lat1, lon1, lat2, lon2, self._profile_points
        )
        result.elevation_profile = profile

        # Ground elevations at endpoints
        ground_a = profile[0]
        ground_b = profile[-1]

        # Antenna heights (above sea level)
        antenna_a = ground_a + alt1
        antenna_b = ground_b + alt2

        # Calculate LOS line and earth bulge at each point
        n = len(profile)
        obstructions = []
        result.los_heights = []

        freq_ghz = freq_mhz / 1000.0
        distance_km = result.distance_m / 1000.0

        # Maximum Fresnel radius (at midpoint)
        max_fresnel = fresnel_radius(distance_km, freq_ghz)

        worst_clearance = float('inf')

        for i in range(n):
            t = i / max(1, n - 1)  # Fraction along path (0 to 1)
            d_from_a = result.distance_m * t

            # LOS height at this point (linear interpolation between antennas)
            los_height = antenna_a + t * (antenna_b - antenna_a)

            # Earth bulge correction
            bulge = earth_bulge(result.distance_m) * 4 * t * (1 - t)
            los_height -= bulge

            result.los_heights.append(los_height)

            # Check if terrain penetrates LOS
            ground = profile[i]
            clearance = los_height - ground

            # Fresnel radius at this point
            if t > 0 and t < 1:
                d1 = d_from_a / 1000.0  # km from A
                d2 = (result.distance_m - d_from_a) / 1000.0  # km from B
                if d1 > 0 and d2 > 0:
                    # Fresnel radius formula for arbitrary point
                    local_fresnel = 17.3 * math.sqrt(
                        (d1 * d2) / ((d1 + d2) * freq_ghz)
                    )
                    fresnel_clearance = clearance / local_fresnel if local_fresnel > 0 else float('inf')
                    worst_clearance = min(worst_clearance, fresnel_clearance)
                else:
                    local_fresnel = 0

            if clearance < 0:
                # Terrain above LOS — definite obstruction
                obstructions.append((t, -clearance))
                result.num_obstructions += 1
                if -clearance > result.worst_obstruction_m:
                    result.worst_obstruction_m = -clearance

        # Calculate earth bulge at midpoint for reference
        result.earth_bulge_m = earth_bulge(result.distance_m)

        # Fresnel clearance percentage
        if worst_clearance == float('inf'):
            result.fresnel_clearance_pct = 100.0
        else:
            result.fresnel_clearance_pct = max(0.0, min(100.0, worst_clearance * 100))

        # Determine if LOS is clear (60% Fresnel clearance)
        result.is_clear = (
            result.num_obstructions == 0 and
            result.fresnel_clearance_pct >= self.FRESNEL_CLEARANCE_THRESHOLD * 100
        )

        # Calculate losses
        result.fspl_db = free_space_path_loss(result.distance_m, freq_mhz)

        if obstructions:
            result.terrain_loss_db = multi_obstacle_loss(
                result.distance_m, obstructions, freq_mhz
            )

        result.total_loss_db = result.fspl_db + result.terrain_loss_db

        return result

    def coverage_grid(self, center_lat: float, center_lon: float,
                      antenna_height: float, radius_km: float,
                      freq_mhz: float = 915.0,
                      resolution: int = 36) -> List[Dict[str, Any]]:
        """Calculate coverage in a grid pattern around a node.

        Args:
            center_lat, center_lon: Node position.
            antenna_height: Antenna height above ground (meters).
            radius_km: Maximum range to check.
            freq_mhz: Operating frequency.
            resolution: Number of radial samples per direction.

        Returns:
            List of dicts with lat, lon, is_clear, total_loss_db, distance_m
            for each grid point.
        """
        points = []
        num_bearings = 36  # Every 10 degrees

        for bearing_idx in range(num_bearings):
            bearing = bearing_idx * (360 / num_bearings)

            for r_idx in range(1, resolution + 1):
                distance = (r_idx / resolution) * radius_km * 1000

                # Calculate destination point
                lat2, lon2 = self._destination_point(
                    center_lat, center_lon, bearing, distance
                )

                # Analyze LOS to this point
                result = self.analyze(
                    center_lat, center_lon, antenna_height,
                    lat2, lon2, 1.5,  # Assume 1.5m receiver height
                    freq_mhz
                )

                points.append({
                    "lat": round(lat2, 6),
                    "lon": round(lon2, 6),
                    "bearing": bearing,
                    "distance_m": round(result.distance_m, 0),
                    "is_clear": result.is_clear,
                    "total_loss_db": round(result.total_loss_db, 1),
                    "terrain_loss_db": round(result.terrain_loss_db, 1),
                    "fresnel_clearance_pct": round(result.fresnel_clearance_pct, 1),
                })

        return points

    def _destination_point(self, lat: float, lon: float,
                           bearing_deg: float, distance_m: float) -> Tuple[float, float]:
        """Calculate destination point given start, bearing, and distance."""
        R = 6371000  # Earth radius in meters
        lat_r = math.radians(lat)
        lon_r = math.radians(lon)
        bearing_r = math.radians(bearing_deg)

        d_over_r = distance_m / R

        lat2 = math.asin(
            math.sin(lat_r) * math.cos(d_over_r) +
            math.cos(lat_r) * math.sin(d_over_r) * math.cos(bearing_r)
        )
        lon2 = lon_r + math.atan2(
            math.sin(bearing_r) * math.sin(d_over_r) * math.cos(lat_r),
            math.cos(d_over_r) - math.sin(lat_r) * math.sin(lat2)
        )

        return math.degrees(lat2), math.degrees(lon2)
