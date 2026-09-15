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
import os
import shutil
import struct
import threading
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
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
        return self.get_profile_with_coverage(
            lat1, lon1, lat2, lon2, num_points
        )[0]

    def has_elevation(self, lat: float, lon: float) -> bool:
        """Whether real data backs :meth:`get_elevation` at this point.

        Providers that synthesise terrain always have an answer. Providers
        backed by tiles must override: ``get_elevation`` returns 0.0 for a
        missing tile, which is indistinguishable from sea level, so a caller
        that must tell "flat" from "unknown" asks HERE rather than inferring
        it from the elevation (honest_failure_modes #1).
        """
        return True

    def get_profile_with_coverage(
        self, lat1: float, lon1: float, lat2: float, lon2: float,
        num_points: int = 100,
    ) -> Tuple[List[float], int]:
        """Elevation profile plus a count of samples with NO backing data.

        ONE loop, two answers, so a profile and its coverage can never
        disagree about which points were sampled. :meth:`get_profile` is a
        thin wrapper over this — do not grow a second sampling loop beside
        it, or the count starts describing different points than the data.

        Returns:
            ``(profile, missing)`` where ``missing`` is how many of the
            returned elevations are placeholders rather than measurements.
        """
        profile: List[float] = []
        missing = 0
        for i in range(num_points):
            t = i / max(1, num_points - 1)
            lat = lat1 + t * (lat2 - lat1)
            lon = lon1 + t * (lon2 - lon1)
            if not self.has_elevation(lat, lon):
                missing += 1
            profile.append(self.get_elevation(lat, lon))
        return profile, missing


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


class TileCorrupt(ValueError):
    """A tile on disk is not a whole SRTM1/SRTM3 file.

    Raised by :meth:`SRTMProvider._interpolate` instead of guessing a
    resolution. Before 2026-09-14 an unknown length fell through to "try
    SRTM3", so a HALF-WRITTEN tile (a concurrent download mid-flight, a
    power cut during the write) was indexed as terrain and published
    elevations off by thousands of metres with ``has_elevation()`` True.
    """


class SRTMProvider(TerrainProvider):
    """SRTM elevation data provider.

    Downloads and caches SRTM HGT tiles (1 arc-second resolution,
    ~30m per pixel). Tiles are ~25MB each and cover 1 degree x 1 degree.

    Data source: Mapzen "skadi" tiles on AWS Open Data. Two properties of
    that source, measured 2026-09-14 on the fleet's cached Big Island tiles:
    it is **void-filled** (0 ``-32768`` samples in 77.8 M), and it carries
    **bathymetry** (ocean floor down to -5,994 m). The second one matters
    for RF: the sea floor is not the ground a radio path sees, the sea
    SURFACE is, so :meth:`get_elevation` floors negative samples at 0 by
    default (``sea_level_floor``). The trade is documented below.

    Security shape (frontier pass 2026-09-14): the map's request path
    constructs this with ``auto_download=False`` — a coordinate an
    unauthenticated client picks must never turn into a synchronous 25 MB
    fetch inside an HTTP handler. Tiles arrive via :meth:`warm_tiles`
    (bounded, off the request thread) or an operator's deliberate call.
    """

    # SRTM data URLs (multiple fallback sources)
    SRTM_URLS = [
        "https://elevation-tiles-prod.s3.amazonaws.com/skadi/{ns}{lat:02d}/{ns}{lat:02d}{ew}{lon:03d}.hgt.gz",
    ]

    # HGT file specifications
    SRTM1_SAMPLES = 3601  # 1 arc-second (SRTM1)
    SRTM3_SAMPLES = 1201  # 3 arc-second (SRTM3)
    _VALID_LENGTHS = (SRTM1_SAMPLES * SRTM1_SAMPLES * 2,
                      SRTM3_SAMPLES * SRTM3_SAMPLES * 2)

    # A tile that is not on disk is re-checked after this long. Short,
    # because the warm path (once per map start) or an operator running
    # scripts/srtm_warm.py may land it; long enough that a 172,800-lookup
    # coverage request does not stat the disk per sample.
    MISSING_TTL_S = 60.0
    # Decoded tiles held in RAM (~25.9 MB each). The map's request path
    # shares ONE provider across requests, so this bounds the process.
    DEFAULT_MAX_TILES_IN_MEMORY = 8
    # warm_tiles() refuses to fill the disk: keep at least this much free.
    DEFAULT_MIN_FREE_BYTES = 2 * 1024 ** 3
    # A real gzipped tile is ~7-10 MB; anything past this is not a tile.
    MAX_COMPRESSED_BYTES = 64 * 1024 * 1024
    # Bound on the per-name bookkeeping dicts (a LAN client sweeping the
    # globe could otherwise grow them to the 64,800-name coordinate space).
    MAX_TRACKED_NAMES = 4096

    def __init__(self, cache_dir: Optional[Path] = None,
                 auto_download: bool = True,
                 sea_level_floor: bool = True,
                 max_tiles_in_memory: int = DEFAULT_MAX_TILES_IN_MEMORY):
        """Initialize SRTM provider.

        Args:
            cache_dir: Directory for cached HGT files.
                      Default: ~/.local/share/meshforge/srtm/
            auto_download: Whether :meth:`get_elevation` may download a
                      missing tile on the spot. The HTTP request path
                      passes False; see :meth:`warm_tiles`.
            sea_level_floor: Clamp negative elevations to 0.0. The tiles
                      carry bathymetry, and for an RF path the sea surface
                      is the ground. Cost: genuine below-sea-level land
                      (Death Valley, Dead Sea shores) reads as 0 — rare,
                      and conservative in the safe direction for LoS.
            max_tiles_in_memory: LRU bound on decoded tiles held in RAM.
        """
        if cache_dir is None:
            cache_dir = get_real_user_home() / ".local" / "share" / "meshforge" / "srtm"
        cache_dir.mkdir(parents=True, exist_ok=True)

        self._cache_dir = cache_dir
        self._auto_download = auto_download
        self._sea_level_floor = sea_level_floor
        self._max_tiles = max(1, int(max_tiles_in_memory))
        self._tile_cache: "OrderedDict[str, bytes]" = OrderedDict()
        self._missing_until: Dict[str, float] = {}
        self._lock = threading.Lock()
        # One lock per tile name so two requests missing the same tile
        # serialise on the disk read / download instead of both doing it.
        self._tile_locks: Dict[str, threading.Lock] = {}

    # ── public API ────────────────────────────────────────────────────

    def get_elevation(self, lat: float, lon: float) -> float:
        """Get elevation from SRTM data.

        Returns 0.0 if the tile is not available — callers that publish a
        verdict must ask :meth:`has_elevation` first, because 0.0 is also
        a legitimate sea-level answer.
        """
        tile_data = self._get_tile(lat, lon)
        if tile_data is None:
            return 0.0
        z = self._interpolate(tile_data, lat, lon)
        if self._sea_level_floor and z < 0.0:
            return 0.0
        return z

    def has_elevation(self, lat: float, lon: float) -> bool:
        """True when a whole, valid tile actually covers this point.

        The 0.0 that :meth:`get_elevation` returns for a missing tile sits
        squarely inside the healthy domain (sea level), so callers that
        publish a verdict must ask this first. A corrupt or half-written
        tile on disk answers False here, never a number.
        """
        return self._get_tile(lat, lon) is not None

    def tile_name_for(self, lat: float, lon: float) -> str:
        """Public spelling of the tile that covers a point (``N19W156.hgt``)."""
        return self._get_tile_name(lat, lon)

    def missing_tiles_for(self, points) -> List[str]:
        """Tile names (sorted, unique) not on disk for these ``(lat, lon)`` points.

        A disk check, not a download — used by the map to tell a client
        WHICH tiles a ``terrain_complete: false`` answer is missing.
        """
        names = set()
        for lat, lon in points:
            try:
                name = self._get_tile_name(lat, lon)
            except (ValueError, OverflowError):
                continue
            if not (self._cache_dir / name).exists():
                names.add(name)
        return sorted(names)

    def warm_tiles(self, points, max_tiles: int = 9,
                   min_free_bytes: int = DEFAULT_MIN_FREE_BYTES,
                   neighbors: bool = True) -> Dict[str, Any]:
        """Download the tiles around ``points`` — bounded, never on a request thread.

        This is the ONLY sanctioned downloader for the map process. The
        request path has ``auto_download=False`` so an unauthenticated
        client cannot choose what this box fetches; the warm path fetches
        what the box's OWN nodes need (their tiles plus the eight
        neighbours, so a coverage radius or an inter-node path that
        crosses a tile edge is covered).

        Bounds, in order: ``max_tiles`` downloads per call (0 disables),
        ``min_free_bytes`` of free disk that must remain, and the source's
        own 30 s socket timeout per tile. Every fetch is one log line.

        Returns a summary dict: ``wanted`` (tile names after dedupe),
        ``cached`` (already on disk), ``downloaded``, ``failed``,
        ``skipped_budget`` (over ``max_tiles``), ``skipped_disk``
        (free space below the floor).
        """
        wanted: List[str] = []
        seen = set()
        for lat, lon in points:
            try:
                lat_i = int(math.floor(float(lat)))
                lon_i = int(math.floor(float(lon)))
            except (ValueError, OverflowError, TypeError):
                continue
            if not (-90 <= lat_i < 90 and -180 <= lon_i < 180):
                continue
            offsets = [(0, 0)]
            if neighbors:
                offsets = [(dy, dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
            for dy, dx in offsets:
                la, lo = lat_i + dy, lon_i + dx
                if not (-90 <= la < 90):
                    continue
                if lo < -180:
                    lo += 360
                elif lo >= 180:
                    lo -= 360
                name = self._get_tile_name(la + 0.5, lo + 0.5)
                if name not in seen:
                    seen.add(name)
                    wanted.append(name)

        summary: Dict[str, Any] = {
            "wanted": wanted, "cached": [], "downloaded": [],
            "failed": [], "skipped_budget": [], "skipped_disk": [],
        }
        budget = max(0, int(max_tiles))
        for name in wanted:
            if (self._cache_dir / name).exists():
                summary["cached"].append(name)
                continue
            if len(summary["downloaded"]) + len(summary["failed"]) >= budget:
                summary["skipped_budget"].append(name)
                continue
            try:
                free = shutil.disk_usage(self._cache_dir).free
            except OSError as e:
                logger.warning("SRTM warm: cannot read free disk for %s: %s", self._cache_dir, e)
                free = 0
            if free < min_free_bytes:
                summary["skipped_disk"].append(name)
                continue
            lat_i, lon_i = self._tile_name_to_corner(name)
            data = self._download_tile(lat_i + 0.5, lon_i + 0.5)
            if data and len(data) in self._VALID_LENGTHS:
                self._atomic_write(self._cache_dir / name, data)
                summary["downloaded"].append(name)
                logger.info("SRTM warm: cached %s (%d bytes)", name, len(data))
            else:
                summary["failed"].append(name)
        if summary["skipped_disk"]:
            logger.warning("SRTM warm: free disk below floor (%d B); not fetching %s",
                           min_free_bytes, summary["skipped_disk"])
        return summary

    # ── tile naming ───────────────────────────────────────────────────

    def _get_tile_name(self, lat: float, lon: float) -> str:
        """Get SRTM tile filename for a coordinate."""
        lat_int = int(math.floor(lat))
        lon_int = int(math.floor(lon))

        ns = "N" if lat_int >= 0 else "S"
        ew = "E" if lon_int >= 0 else "W"

        return f"{ns}{abs(lat_int):02d}{ew}{abs(lon_int):03d}.hgt"

    @staticmethod
    def _tile_name_to_corner(name: str):
        """Inverse of :meth:`_get_tile_name`: ``N19W156.hgt`` -> ``(19, -156)``."""
        base = name[:-4] if name.endswith(".hgt") else name
        lat = int(base[1:3]) * (1 if base[0] == "N" else -1)
        lon = int(base[4:7]) * (1 if base[3] == "E" else -1)
        return lat, lon

    # ── cache machinery ───────────────────────────────────────────────

    def _tile_lock(self, tile_name: str) -> threading.Lock:
        with self._lock:
            lock = self._tile_locks.get(tile_name)
            if lock is None:
                lock = self._tile_locks[tile_name] = threading.Lock()
            return lock

    def _remember(self, tile_name: str, data: bytes) -> None:
        with self._lock:
            self._tile_cache[tile_name] = data
            self._tile_cache.move_to_end(tile_name)
            self._missing_until.pop(tile_name, None)
            while len(self._tile_cache) > self._max_tiles:
                self._tile_cache.popitem(last=False)

    def _forget(self, tile_name: str, ttl_s: float) -> None:
        with self._lock:
            self._tile_cache.pop(tile_name, None)
            now = time.monotonic()
            self._missing_until[tile_name] = now + ttl_s
            if len(self._missing_until) > self.MAX_TRACKED_NAMES:
                live = {k: v for k, v in self._missing_until.items() if v > now}
                if len(live) > self.MAX_TRACKED_NAMES:
                    # Still over the cap with nothing expired: keep the half
                    # that expires latest; a re-check costs one stat.
                    keep = sorted(live.items(), key=lambda kv: kv[1])[-(self.MAX_TRACKED_NAMES // 2):]
                    live = dict(keep)
                self._missing_until = live
            if len(self._tile_locks) > self.MAX_TRACKED_NAMES:
                # Locks are held only across one disk read / download; an
                # unheld lock can be dropped and re-minted on demand.
                self._tile_locks = {k: l for k, l in self._tile_locks.items() if l.locked()}

    def _get_tile(self, lat: float, lon: float) -> Optional[bytes]:
        """Get tile data: memory, then disk, then (only if allowed) download.

        Returns None for a tile that is absent OR not a whole valid file.
        """
        tile_name = self._get_tile_name(lat, lon)

        with self._lock:
            data = self._tile_cache.get(tile_name)
            if data is not None:
                self._tile_cache.move_to_end(tile_name)
                return data
            until = self._missing_until.get(tile_name)
            if until is not None and time.monotonic() < until:
                return None

        with self._tile_lock(tile_name):
            # Re-check: another thread may have loaded it while we waited.
            with self._lock:
                data = self._tile_cache.get(tile_name)
                if data is not None:
                    return data

            tile_path = self._cache_dir / tile_name
            if tile_path.exists():
                try:
                    data = tile_path.read_bytes()
                except OSError as e:
                    # A dying SD or a sandbox path drift (#60 class) must read
                    # as MISSING with a witness, not as a 500 per request.
                    logger.warning("SRTM tile %s unreadable (%s); reads as MISSING", tile_name, e)
                    self._forget(tile_name, self.MISSING_TTL_S)
                    return None
                if len(data) in self._VALID_LENGTHS:
                    self._remember(tile_name, data)
                    return data
                self._quarantine(tile_path, len(data))
                self._forget(tile_name, self.MISSING_TTL_S)
                return None

            # Check for gzipped version
            gz_path = self._cache_dir / f"{tile_name}.gz"
            if gz_path.exists():
                import gzip
                try:
                    with gzip.open(gz_path, 'rb') as f:
                        data = f.read()
                except (OSError, EOFError) as e:
                    logger.warning("SRTM tile %s.gz unreadable: %s", tile_name, e)
                    data = b""
                if len(data) in self._VALID_LENGTHS:
                    # Cache uncompressed, whole-file-or-nothing; a failed
                    # write still serves the tile from memory this once.
                    try:
                        self._atomic_write(tile_path, data)
                    except OSError as e:
                        logger.warning("SRTM: could not cache %s uncompressed (%s)", tile_name, e)
                    self._remember(tile_name, data)
                    return data
                self._quarantine(gz_path, len(data))
                self._forget(tile_name, self.MISSING_TTL_S)
                return None

            # Download if allowed
            if self._auto_download:
                data = self._download_tile(lat, lon)
                if data and len(data) in self._VALID_LENGTHS:
                    self._atomic_write(tile_path, data)
                    self._remember(tile_name, data)
                    return data
                # A failed download is remembered longer than a plain miss:
                # the source is not going to change in the next minute, and
                # each retry is a network round trip on this thread.
                self._forget(tile_name, 10 * self.MISSING_TTL_S)
                return None

            # No data available here; the warm path may add it — re-check soon.
            self._forget(tile_name, self.MISSING_TTL_S)
            return None

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        """Write a tile whole-or-nothing.

        A plain ``write_bytes`` let a concurrent reader see a half-written
        file that ``exists()`` — and until 2026-09-14 that reader would
        publish it as terrain. Write beside the target, fsync, then
        ``os.replace`` (atomic on POSIX) so a reader sees either no file or
        the whole file.
        """
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with open(tmp, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

    @staticmethod
    def _quarantine(path: Path, length: int) -> None:
        """Move a not-whole tile aside so it is never read as terrain again.

        Loud (WARNING) — a corrupt tile is a finding about the disk or a
        killed download, not something to absorb silently (hfm #9).
        """
        target = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
        try:
            os.replace(path, target)
            logger.warning("SRTM tile %s is %d bytes, not a whole SRTM1/SRTM3 file — "
                           "quarantined as %s; it will read as MISSING, never as terrain",
                           path.name, length, target.name)
        except OSError as e:
            logger.warning("SRTM tile %s is %d bytes (not whole) and could not be "
                           "quarantined (%s); it reads as MISSING", path.name, length, e)

    def _download_tile(self, lat: float, lon: float) -> Optional[bytes]:
        """Download an SRTM tile from the web (one 30 s socket timeout per source)."""
        lat_int = int(math.floor(lat))
        lon_int = int(math.floor(lon))
        if not (-90 <= lat_int < 90 and -180 <= lon_int < 180):
            logger.warning("SRTM: refusing to fetch a tile for impossible coordinate (%s, %s)", lat, lon)
            return None

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
                    compressed = response.read(self.MAX_COMPRESSED_BYTES + 1)
                if len(compressed) > self.MAX_COMPRESSED_BYTES:
                    logger.warning("SRTM tile %s: compressed body exceeds %d bytes; refusing",
                                   url, self.MAX_COMPRESSED_BYTES)
                    return None

                # Decompress with a ceiling — a whole tile is at most SRTM1 size.
                import zlib
                d = zlib.decompressobj(16 + zlib.MAX_WBITS)
                data = d.decompress(compressed, self._VALID_LENGTHS[0] + 1)
                if d.unconsumed_tail or len(data) > self._VALID_LENGTHS[0]:
                    logger.warning("SRTM tile %s: decompresses past a whole tile; refusing", url)
                    return None
                logger.info(f"Downloaded SRTM tile ({len(data)} bytes)")
                return data

            except Exception as e:
                logger.debug(f"SRTM download failed from {url}: {e}")
                continue

        logger.warning(f"Could not download SRTM tile for ({lat}, {lon})")
        return None

    def _interpolate(self, tile_data: bytes, lat: float, lon: float) -> float:
        """Bilinear interpolation of elevation from tile data.

        Raises :class:`TileCorrupt` for a length that is neither SRTM1 nor
        SRTM3 — guessing a resolution over a partial file produced
        confident garbage (see the class docstring).
        """
        data_len = len(tile_data)
        if data_len == self.SRTM1_SAMPLES * self.SRTM1_SAMPLES * 2:
            samples = self.SRTM1_SAMPLES
        elif data_len == self.SRTM3_SAMPLES * self.SRTM3_SAMPLES * 2:
            samples = self.SRTM3_SAMPLES
        else:
            raise TileCorrupt(
                f"tile is {data_len} bytes; a whole SRTM1 tile is "
                f"{self._VALID_LENGTHS[0]} and SRTM3 {self._VALID_LENGTHS[1]}"
            )

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

        floor = self._sea_level_floor

        # Read 4 surrounding samples (big-endian int16)
        def read_sample(r, c):
            idx = (r * samples + c) * 2
            if idx + 2 > len(tile_data):
                return 0
            val = struct.unpack('>h', tile_data[idx:idx+2])[0]
            # SRTM void value (never seen in the skadi source — kept as a
            # guard for a tile that came from somewhere else)
            if val == -32768:
                return 0
            # The sea-surface floor is applied PER SAMPLE, before the blend.
            # Applied after it (the first cut of 2026-09-14, caught in
            # review), a 300 m cliff beside a -3000 m sea-floor sample
            # blended to a negative number and was then floored to 0 — the
            # shoreline obstruction erased, in the OPTIMISTIC direction.
            if floor and val < 0:
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
        # Fresnel radius (m) at each profile point — 0.0 at the endpoints,
        # where the first Fresnel zone has no width. Published because
        # analyze() already computes it; a consumer that re-derives the
        # formula is a THIRD copy of the same math.
        self.fresnel_radii: List[float] = []
        self.earth_bulge_m: float = 0.0
        # Terrain coverage for this path. missing > 0 means some samples are
        # placeholders, NOT measurements — is_clear is then an opinion about
        # invented ground and consumers must render UNKNOWN, not a verdict.
        self.terrain_samples_total: int = 0
        self.terrain_samples_missing: int = 0

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
            "terrain_samples_total": self.terrain_samples_total,
            "terrain_samples_missing": self.terrain_samples_missing,
            "terrain_complete": self.terrain_complete,
        }

    @property
    def terrain_complete(self) -> bool:
        """True only when every profile sample had real data behind it.

        False means the analysis ran over invented ground somewhere. It does
        NOT mean the path is bad — it means we do not know.
        """
        return (
            self.terrain_samples_total > 0
            and self.terrain_samples_missing == 0
        )


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

        # Get elevation profile AND how much of it is real data
        profile, missing = self._provider.get_profile_with_coverage(
            lat1, lon1, lat2, lon2, self._profile_points
        )
        result.elevation_profile = profile
        result.terrain_samples_total = len(profile)
        result.terrain_samples_missing = missing

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

            # Fresnel radius at this point. Re-zeroed EVERY iteration: it
            # used to be assigned only inside the interior branch, so an
            # endpoint sample silently reused the previous point's radius.
            # Harmless while it stayed a local; a real defect now that it is
            # published per point.
            local_fresnel = 0.0
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

            result.fresnel_radii.append(local_fresnel)

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
