"""
GPS Integration — operator position on map, distance to nodes.

Reads operator position from gpsd (if available) or manual input,
calculates distance and bearing to known mesh nodes, and provides
position data for map display.

Sources (priority order):
    1. gpsd daemon (localhost:2947, JSON protocol)
    2. Cached last-known position (persisted to config)
    3. Manual entry (lat/lon)

Usage:
    from utils.gps_integration import GPSManager

    gps = GPSManager()
    pos = gps.get_position()
    if pos:
        print(f"Operator at {pos.lat:.6f}, {pos.lon:.6f}")
        distances = gps.distances_to_nodes(node_list)
"""

import json
import logging
import math
import socket
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)

# gpsd connection settings
GPSD_HOST = "localhost"
GPSD_PORT = 2947
GPSD_TIMEOUT = 3.0

# Position cache TTL (seconds)
POSITION_CACHE_TTL = 10.0

# Position is "stale" if older than this (seconds)
POSITION_STALE_SEC = 300  # 5 minutes


@dataclass
class Position:
    """Geographic position with optional altitude and metadata."""
    lat: float
    lon: float
    alt: Optional[float] = None
    speed: Optional[float] = None      # m/s
    heading: Optional[float] = None    # degrees true
    accuracy: Optional[float] = None   # meters (horizontal)
    timestamp: float = 0.0
    source: str = "unknown"            # "gpsd", "manual", "cached"

    @property
    def is_valid(self) -> bool:
        """Check if position is within valid geographic bounds."""
        return -90.0 <= self.lat <= 90.0 and -180.0 <= self.lon <= 180.0

    @property
    def is_stale(self) -> bool:
        """Check if position data is too old to be useful."""
        if self.timestamp == 0.0:
            return True
        return (time.time() - self.timestamp) > POSITION_STALE_SEC

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'Position':
        """Create from dictionary."""
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


@dataclass
class NodeDistance:
    """Distance and bearing from operator to a mesh node."""
    node_id: str
    node_name: str
    distance_m: float
    bearing_deg: float     # True bearing (0-360)
    node_lat: float
    node_lon: float

    @property
    def distance_km(self) -> float:
        """Distance in kilometers."""
        return self.distance_m / 1000.0

    @property
    def cardinal_direction(self) -> str:
        """Convert bearing to cardinal direction."""
        directions = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
                      'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
        idx = round(self.bearing_deg / 22.5) % 16
        return directions[idx]

    @property
    def distance_display(self) -> str:
        """Human-readable distance string."""
        if self.distance_m < 1000:
            return f"{self.distance_m:.0f}m"
        elif self.distance_m < 10000:
            return f"{self.distance_km:.2f}km"
        else:
            return f"{self.distance_km:.1f}km"


def haversine_distance(lat1: float, lon1: float,
                       lat2: float, lon2: float) -> float:
    """Calculate distance between two points using Haversine formula.

    Args:
        lat1, lon1: First point (degrees).
        lat2, lon2: Second point (degrees).

    Returns:
        Distance in meters.
    """
    R = 6371000  # Earth radius in meters
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (math.sin(dlat / 2) ** 2 +
         math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def initial_bearing(lat1: float, lon1: float,
                    lat2: float, lon2: float) -> float:
    """Calculate initial bearing from point 1 to point 2.

    Args:
        lat1, lon1: Start point (degrees).
        lat2, lon2: End point (degrees).

    Returns:
        Bearing in degrees (0-360, 0=North, 90=East).
    """
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlon = math.radians(lon2 - lon1)

    x = math.sin(dlon) * math.cos(lat2_r)
    y = (math.cos(lat1_r) * math.sin(lat2_r) -
         math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon))

    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360


class GPSReader:
    """Reads position from gpsd daemon.

    Connects to gpsd on localhost:2947 using the JSON protocol.
    Falls back gracefully if gpsd is not available.
    """

    def __init__(self, host: str = GPSD_HOST, port: int = GPSD_PORT,
                 timeout: float = GPSD_TIMEOUT):
        """Initialize GPS reader.

        Args:
            host: gpsd host address.
            port: gpsd port number.
            timeout: Socket timeout in seconds.
        """
        self._host = host
        self._port = port
        self._timeout = timeout

    def read_position(self) -> Optional[Position]:
        """Attempt to read current position from gpsd.

        Returns:
            Position if GPS has a fix, None otherwise.
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(self._timeout)
                s.connect((self._host, self._port))

                # Send WATCH command to start JSON streaming
                s.sendall(b'?WATCH={"enable":true,"json":true}\n')

                # Read responses until we get a TPV (position) fix
                buffer = b""
                max_buffer = 65536  # 64KB limit
                deadline = time.time() + self._timeout
                while time.time() < deadline:
                    try:
                        data = s.recv(4096)
                        if not data:
                            break
                        buffer += data
                        if len(buffer) > max_buffer:
                            logger.debug("gpsd buffer overflow, aborting")
                            break

                        # Process complete JSON lines
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            position = self._parse_tpv(line.decode('utf-8',
                                                                   errors='replace'))
                            if position:
                                return position
                    except socket.timeout:
                        break

        except (ConnectionRefusedError, OSError) as e:
            logger.debug(f"gpsd not available: {e}")
        except Exception as e:
            logger.debug(f"GPS read error: {e}")

        return None

    def _parse_tpv(self, line: str) -> Optional[Position]:
        """Parse a gpsd TPV (Time-Position-Velocity) JSON line.

        Args:
            line: JSON string from gpsd.

        Returns:
            Position if valid TPV with fix, None otherwise.
        """
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return None

        if data.get("class") != "TPV":
            return None

        # Mode: 0=unknown, 1=no fix, 2=2D, 3=3D
        mode = data.get("mode", 0)
        if mode < 2:
            return None  # No fix

        lat = data.get("lat")
        lon = data.get("lon")
        if lat is None or lon is None:
            return None

        return Position(
            lat=float(lat),
            lon=float(lon),
            alt=data.get("altMSL") or data.get("alt"),
            speed=data.get("speed"),
            heading=data.get("track"),
            accuracy=data.get("epx"),  # Horizontal error
            timestamp=time.time(),
            source="gpsd",
        )

    @property
    def is_available(self) -> bool:
        """Check if gpsd is reachable."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                result = s.connect_ex((self._host, self._port))
                return result == 0
        except (OSError, socket.error):
            return False


class GPSManager:
    """Manages operator GPS position with caching and persistence.

    Provides a unified interface for GPS position regardless of
    source (gpsd, manual, cached).
    """

    def __init__(self, config_path: Optional[Path] = None):
        """Initialize GPS manager.

        Args:
            config_path: Path to persist position cache.
                        If None, uses default config directory.
        """
        self._config_path = config_path or self._get_default_path()
        self._reader = GPSReader()
        self._position: Optional[Position] = None
        self._last_read: float = 0.0
        self._lock = threading.Lock()

        # Load cached position
        self._load_cached()

    def _get_default_path(self) -> Path:
        """Get default config path."""
        config_dir = get_real_user_home() / ".config" / "meshforge"
        return config_dir / "operator_position.json"

    def _load_cached(self) -> None:
        """Load last known position from disk."""
        if not self._config_path.exists():
            return
        try:
            data = json.loads(self._config_path.read_text())
            self._position = Position.from_dict(data)
            self._position.source = "cached"
            logger.debug(f"Loaded cached position: "
                         f"{self._position.lat:.6f}, {self._position.lon:.6f}")
        except (json.JSONDecodeError, OSError, KeyError) as e:
            logger.debug(f"Failed to load cached position: {e}")

    def _save_position(self, position: Position) -> None:
        """Persist position to disk atomically.

        Args:
            position: Position to save.
        """
        try:
            from utils.paths import atomic_write_text
            atomic_write_text(self._config_path,
                              json.dumps(position.to_dict(), indent=2))
        except (ImportError, OSError) as e:
            logger.debug(f"Failed to save position: {e}")

    def get_position(self, force_refresh: bool = False) -> Optional[Position]:
        """Get current operator position.

        Tries gpsd first, falls back to cached position.

        Args:
            force_refresh: Bypass cache and read from GPS.

        Returns:
            Position if available, None otherwise.
        """
        now = time.time()

        with self._lock:
            # Use cached if fresh
            if (not force_refresh and self._position is not None and
                    (now - self._last_read) < POSITION_CACHE_TTL):
                return self._position

            # Try gpsd
            gps_pos = self._reader.read_position()
            if gps_pos and gps_pos.is_valid:
                self._position = gps_pos
                self._last_read = now
                self._save_position(gps_pos)
                return gps_pos

            # Fall back to cached (even if stale)
            self._last_read = now
            return self._position

    def set_manual_position(self, lat: float, lon: float,
                            alt: Optional[float] = None) -> Position:
        """Set operator position manually.

        Args:
            lat: Latitude in degrees (-90 to 90).
            lon: Longitude in degrees (-180 to 180).
            alt: Optional altitude in meters.

        Returns:
            The new Position object.

        Raises:
            ValueError: If coordinates are out of bounds.
        """
        if not (-90.0 <= lat <= 90.0):
            raise ValueError(f"Latitude {lat} out of range [-90, 90]")
        if not (-180.0 <= lon <= 180.0):
            raise ValueError(f"Longitude {lon} out of range [-180, 180]")

        position = Position(
            lat=lat,
            lon=lon,
            alt=alt,
            timestamp=time.time(),
            source="manual",
        )

        with self._lock:
            self._position = position
            self._last_read = time.time()
            self._save_position(position)

        return position

    def distances_to_nodes(self, nodes: List[dict]) -> List[NodeDistance]:
        """Calculate distance and bearing to a list of nodes.

        Args:
            nodes: List of node dicts with at least 'id', 'lat', 'lon'.
                   Optional: 'name' for display.

        Returns:
            List of NodeDistance objects sorted by distance (nearest first).
        """
        position = self.get_position()
        if position is None:
            return []

        results = []
        for node in nodes:
            node_lat = node.get('lat')
            node_lon = node.get('lon')
            if node_lat is None or node_lon is None:
                continue

            try:
                dist = haversine_distance(
                    position.lat, position.lon,
                    float(node_lat), float(node_lon)
                )
                bearing = initial_bearing(
                    position.lat, position.lon,
                    float(node_lat), float(node_lon)
                )
                results.append(NodeDistance(
                    node_id=node.get('id', 'unknown'),
                    node_name=node.get('name', node.get('id', 'unknown')),
                    distance_m=dist,
                    bearing_deg=bearing,
                    node_lat=float(node_lat),
                    node_lon=float(node_lon),
                ))
            except (ValueError, TypeError) as e:
                logger.debug(f"Skip node {node.get('id')}: {e}")

        results.sort(key=lambda d: d.distance_m)
        return results

    def format_position_report(self, nodes: Optional[List[dict]] = None) -> str:
        """Format a position and distance report for display.

        Args:
            nodes: Optional list of nodes for distance calculations.

        Returns:
            Formatted string report.
        """
        lines = []
        position = self.get_position()

        if position is None:
            lines.append("No position available.")
            lines.append("")
            lines.append("Options:")
            lines.append("  - Start gpsd for GPS hardware")
            lines.append("  - Set position manually")
            return "\n".join(lines)

        lines.append(f"Position: {position.lat:.6f}, {position.lon:.6f}")
        if position.alt is not None:
            lines.append(f"Altitude: {position.alt:.0f}m")
        if position.speed is not None:
            speed_kmh = position.speed * 3.6
            lines.append(f"Speed:    {speed_kmh:.1f} km/h")
        if position.heading is not None:
            lines.append(f"Heading:  {position.heading:.0f} deg")
        if position.accuracy is not None:
            lines.append(f"Accuracy: +/-{position.accuracy:.0f}m")

        lines.append(f"Source:   {position.source}")
        if position.timestamp > 0:
            age_sec = time.time() - position.timestamp
            if age_sec < 60:
                age_str = f"{age_sec:.0f}s ago"
            elif age_sec < 3600:
                age_str = f"{age_sec / 60:.0f}m ago"
            else:
                age_str = f"{age_sec / 3600:.1f}h ago"
            lines.append(f"Updated:  {age_str}")
            if position.is_stale:
                lines.append("WARNING:  Position is stale (>5 min old)")

        # Node distances
        if nodes:
            distances = self.distances_to_nodes(nodes)
            if distances:
                lines.append("")
                lines.append(f"  {'Node':<20} {'Distance':>8}  {'Bearing':>7}")
                lines.append(f"  {'-'*20} {'-'*8}  {'-'*7}")
                for nd in distances[:15]:  # Cap at 15
                    name = nd.node_name[:20]
                    lines.append(
                        f"  {name:<20} {nd.distance_display:>8}  "
                        f"{nd.bearing_deg:5.1f} {nd.cardinal_direction}")

        return "\n".join(lines)

    @property
    def has_position(self) -> bool:
        """Check if any position (even cached) is available."""
        return self._position is not None

    @property
    def gpsd_available(self) -> bool:
        """Check if gpsd daemon is reachable."""
        return self._reader.is_available
