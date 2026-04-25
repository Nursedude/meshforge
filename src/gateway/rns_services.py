"""
RNS Service Registry and Announce Parsers

Handles different RNS service types beyond LXMF, including:
- LXMF messaging (Sideband, NomadNet)
- Nomad Network pages
- Propagation nodes
- Generic/unknown services

Reference: https://reticulum.network/manual/
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Dict, List, Optional, Callable, Any, Tuple

from utils.safe_import import safe_import

# Import msgpack for LXMF telemetry parsing (optional)
_msgpack_mod, _HAS_MSGPACK = safe_import('msgpack')
if _HAS_MSGPACK:
    msgpack = _msgpack_mod

logger = logging.getLogger(__name__)


class RNSServiceType(Enum):
    """Known RNS service types based on announce aspects"""
    LXMF_DELIVERY = auto()       # lxmf.delivery - Sideband, NomadNet messaging
    LXMF_PROPAGATION = auto()    # lxmf.propagation - LXMF propagation nodes
    NOMAD_PAGE = auto()          # nomadnetwork.node - Nomad Network pages
    PROPAGATION_NODE = auto()    # Reticulum propagation node
    FILE_SHARE = auto()          # File sharing services
    UNKNOWN = auto()             # Unknown service type


# Aspect filter to service type mapping
ASPECT_TO_SERVICE: Dict[str, RNSServiceType] = {
    "lxmf.delivery": RNSServiceType.LXMF_DELIVERY,
    "lxmf.propagation": RNSServiceType.LXMF_PROPAGATION,
    "nomadnetwork.node": RNSServiceType.NOMAD_PAGE,
    "nomadnetwork.page": RNSServiceType.NOMAD_PAGE,  # Alternative aspect
}


@dataclass
class ServiceInfo:
    """Information about a discovered RNS service"""
    service_type: RNSServiceType
    aspect: str
    display_name: str = ""
    description: str = ""
    capabilities: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Telemetry extracted from app_data
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[float] = None
    battery: Optional[int] = None
    speed: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "service_type": self.service_type.name,
            "aspect": self.aspect,
            "display_name": self.display_name,
            "description": self.description,
            "capabilities": self.capabilities,
            "metadata": self.metadata,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "altitude": self.altitude,
            "battery": self.battery,
            "speed": self.speed,
        }


@dataclass
class AnnounceEvent:
    """Represents an RNS announce event with parsed data"""
    destination_hash: bytes
    announced_identity: Any  # RNS.Identity
    app_data: Optional[bytes]
    aspect: Optional[str]
    timestamp: datetime = field(default_factory=datetime.now)

    # Parsed data
    service_info: Optional[ServiceInfo] = None
    raw_name: str = ""

    @property
    def hash_hex(self) -> str:
        return self.destination_hash.hex()

    @property
    def short_hash(self) -> str:
        return self.hash_hex[:8]


class ServiceParser:
    """Base class for service-specific announce parsers"""

    @staticmethod
    def parse(app_data: bytes, aspect: str) -> ServiceInfo:
        """Parse app_data and return ServiceInfo. Override in subclasses."""
        raise NotImplementedError


class LXMFParser(ServiceParser):
    """Parser for LXMF announce app_data (Sideband, NomadNet messaging)"""

    @staticmethod
    def parse(app_data: bytes, aspect: str) -> ServiceInfo:
        """Parse LXMF app_data format.

        LXMF (LXMRouter.announce) framing — verified against real wire
        bytes captured on the fleet 2026-04-25:

            byte 0      : uint8 length of the display-name field
            bytes 1..L  : display name (UTF-8, no null terminator)
            bytes L+1..: optional msgpack-encoded telemetry dict
                          (Sideband-style; absent for plain LXMF clients)

        It is NOT a single msgpack object at the top level — older
        versions of this parser assumed it was, and the length-prefix
        byte (which happens to be a printable ASCII int for typical
        name lengths, e.g. 33 = '!', 34 = '"') leaked into the rendered
        display name as a leading garbage character. Hence the
        fleet-wide ``!MeshForge Gateway (...)`` and ``"MeshForge
        Gateway (...)`` symptoms before this fix.

        Msgpack telemetry keys (Sideband format):
        - latitude/lat, longitude/lon/lng, altitude/alt
        - speed, heading, accuracy, battery
        """
        info = ServiceInfo(
            service_type=RNSServiceType.LXMF_DELIVERY if aspect == "lxmf.delivery"
                        else RNSServiceType.LXMF_PROPAGATION,
            aspect=aspect,
        )

        if not app_data or len(app_data) == 0:
            return info

        name_bytes, telemetry_offset = LXMFParser._extract_name_and_telemetry(app_data)

        if 0 < len(name_bytes) < 128:
            try:
                decoded = name_bytes.decode('utf-8', errors='ignore').strip('\x00').strip()
                if decoded:
                    clean_name = ''.join(c for c in decoded if c.isprintable())
                    if clean_name:
                        info.display_name = clean_name[:64]
            except UnicodeDecodeError:
                pass

        # Parse msgpack telemetry if any bytes remain after the name.
        if telemetry_offset is not None and telemetry_offset < len(app_data):
            LXMFParser._parse_msgpack_telemetry(app_data[telemetry_offset:], info)

        return info

    @staticmethod
    def _extract_name_and_telemetry(app_data: bytes) -> tuple:
        """Split app_data into (name_bytes, telemetry_offset).

        Strategy:
          1. Treat byte 0 as a uint8 length prefix. If ``app_data[1:1+L]``
             decodes cleanly to a printable UTF-8 string, that's the name
             and telemetry begins at ``1+L``.
          2. Otherwise fall back to the legacy heuristic: scan for the
             first msgpack map marker (fixmap/map16/map32) and treat
             everything before it as the raw name. This preserves
             compatibility with any non-LXMF announce that happens to
             share the lxmf.delivery aspect.

        Returns ``(name_bytes, telemetry_offset_or_None)``.
        """
        # Strategy 1: 1-byte length prefix (LXMRouter.announce framing)
        prefix_len = app_data[0]
        if 1 <= prefix_len <= 127 and 1 + prefix_len <= len(app_data):
            candidate = app_data[1:1 + prefix_len]
            try:
                decoded = candidate.decode('utf-8')
            except UnicodeDecodeError:
                decoded = None
            if decoded and all(c.isprintable() or c.isspace() for c in decoded):
                telemetry_offset = 1 + prefix_len
                return candidate, telemetry_offset

        # Strategy 2: legacy fallback — scan for msgpack map marker
        msgpack_start = LXMFParser._find_msgpack_start(app_data)
        if msgpack_start > 0:
            return app_data[:msgpack_start], msgpack_start
        return app_data, None

    @staticmethod
    def _find_msgpack_start(app_data: bytes) -> int:
        """Find the start of msgpack data in app_data.

        Scans for msgpack dict markers:
        - fixmap: 0x80-0x8f (up to 15 entries)
        - map16: 0xde
        - map32: 0xdf
        """
        for i in range(len(app_data)):
            byte = app_data[i]
            if 0x80 <= byte <= 0x8f:  # fixmap
                return i
            elif byte in (0xde, 0xdf):  # map16 or map32
                return i
        return -1

    @staticmethod
    def _parse_msgpack_telemetry(data: bytes, info: ServiceInfo):
        """Extract telemetry from msgpack data into ServiceInfo"""
        if not _HAS_MSGPACK:
            logger.debug("msgpack not installed - skipping telemetry parsing")
            return

        try:
            telemetry = msgpack.unpackb(data, raw=False, strict_map_key=False)
            if not isinstance(telemetry, dict):
                return

            # Position extraction with multiple key formats
            lat = telemetry.get('latitude') or telemetry.get('lat')
            lon = telemetry.get('longitude') or telemetry.get('lon') or telemetry.get('lng')

            if lat is not None and lon is not None:
                try:
                    info.latitude = float(lat)
                    info.longitude = float(lon)
                    info.altitude = float(telemetry.get('altitude') or telemetry.get('alt') or 0.0)
                except (TypeError, ValueError):
                    pass

            # Other telemetry
            if 'speed' in telemetry:
                try:
                    info.speed = float(telemetry['speed'])
                except (TypeError, ValueError):
                    pass

            if 'battery' in telemetry:
                try:
                    info.battery = int(telemetry['battery'])
                except (TypeError, ValueError):
                    pass

            # Store raw telemetry in metadata for debugging
            info.metadata['raw_telemetry'] = {k: v for k, v in telemetry.items()
                                               if k in ('latitude', 'lat', 'longitude', 'lon', 'lng',
                                                       'altitude', 'alt', 'speed', 'heading',
                                                       'accuracy', 'battery')}

        except Exception as e:
            logger.debug(f"Failed to parse msgpack telemetry: {e}")


class NomadParser(ServiceParser):
    """Parser for Nomad Network node/page announces"""

    @staticmethod
    def parse(app_data: bytes, aspect: str) -> ServiceInfo:
        """Parse Nomad Network app_data.

        Nomad Network announces contain:
        - Page name/title as UTF-8
        - Optional page description
        """
        info = ServiceInfo(
            service_type=RNSServiceType.NOMAD_PAGE,
            aspect=aspect,
            capabilities=["pages", "microns"],
        )

        if not app_data or len(app_data) == 0:
            return info

        try:
            # Nomad typically sends plain UTF-8 for page info
            decoded = app_data.decode('utf-8', errors='ignore').strip('\x00').strip()
            if decoded:
                # First line is usually the node/page name
                lines = decoded.split('\n')
                info.display_name = lines[0][:64] if lines else ""
                if len(lines) > 1:
                    info.description = '\n'.join(lines[1:])[:256]
        except Exception as e:
            logger.debug(f"Failed to parse Nomad app_data: {e}")

        return info


class GenericParser(ServiceParser):
    """Parser for unknown/generic RNS announces"""

    @staticmethod
    def parse(app_data: bytes, aspect: str) -> ServiceInfo:
        """Parse generic app_data - attempt basic name extraction"""
        info = ServiceInfo(
            service_type=RNSServiceType.UNKNOWN,
            aspect=aspect or "unknown",
        )

        if not app_data or len(app_data) == 0:
            return info

        # Attempt UTF-8 decode for display name
        try:
            decoded = app_data.decode('utf-8', errors='ignore').strip('\x00').strip()
            # Filter to printable chars only
            clean = ''.join(c for c in decoded if c.isprintable())
            if clean and len(clean) >= 2:
                info.display_name = clean[:64]
        except Exception:
            pass

        # Store raw app_data length for debugging
        info.metadata['app_data_length'] = len(app_data)
        info.metadata['app_data_hex_preview'] = app_data[:32].hex() if len(app_data) > 0 else ""

        return info


class RNSServiceRegistry:
    """
    Registry for RNS service parsers.

    Manages aspect-to-parser mappings and handles announce parsing.
    """

    def __init__(self):
        self._parsers: Dict[str, type] = {}
        self._service_callbacks: List[Callable[[AnnounceEvent], None]] = []
        self._discovered_services: Dict[str, ServiceInfo] = {}  # hash_hex -> ServiceInfo

        # Register built-in parsers
        self._register_builtin_parsers()

    def _register_builtin_parsers(self):
        """Register default parsers for known service types"""
        # LXMF services
        self.register_parser("lxmf.delivery", LXMFParser)
        self.register_parser("lxmf.propagation", LXMFParser)

        # Nomad Network
        self.register_parser("nomadnetwork.node", NomadParser)
        self.register_parser("nomadnetwork.page", NomadParser)

    def register_parser(self, aspect: str, parser_class: type):
        """Register a parser for a specific aspect"""
        self._parsers[aspect] = parser_class
        logger.debug(f"Registered parser for aspect: {aspect}")

    def register_callback(self, callback: Callable[[AnnounceEvent], None]):
        """Register callback for service discovery events"""
        self._service_callbacks.append(callback)

    def parse_announce(self, dest_hash: bytes, identity: Any,
                      app_data: Optional[bytes], aspect: Optional[str] = None) -> AnnounceEvent:
        """Parse an RNS announce and return structured event data.

        Args:
            dest_hash: 16-byte destination hash
            identity: RNS Identity object
            app_data: Raw announce app_data
            aspect: Optional aspect filter (if known)

        Returns:
            AnnounceEvent with parsed ServiceInfo
        """
        event = AnnounceEvent(
            destination_hash=dest_hash,
            announced_identity=identity,
            app_data=app_data,
            aspect=aspect,
        )

        # Get appropriate parser
        parser_class = self._parsers.get(aspect, GenericParser)

        # Parse app_data
        try:
            event.service_info = parser_class.parse(app_data or b'', aspect or "")
            if event.service_info.display_name:
                event.raw_name = event.service_info.display_name
        except Exception as e:
            logger.error(f"Failed to parse announce for {event.short_hash}: {e}")
            event.service_info = ServiceInfo(
                service_type=RNSServiceType.UNKNOWN,
                aspect=aspect or "unknown",
            )

        # Store discovered service
        self._discovered_services[event.hash_hex] = event.service_info

        # Notify callbacks
        for callback in self._service_callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Service callback error: {e}")

        return event

    def get_service_type(self, aspect: str) -> RNSServiceType:
        """Get service type for an aspect"""
        return ASPECT_TO_SERVICE.get(aspect, RNSServiceType.UNKNOWN)

    def get_discovered_service(self, hash_hex: str) -> Optional[ServiceInfo]:
        """Get previously discovered service info by hash"""
        return self._discovered_services.get(hash_hex)

    def get_all_discovered(self) -> Dict[str, ServiceInfo]:
        """Get all discovered services"""
        return dict(self._discovered_services)

    def get_stats(self) -> Dict[str, int]:
        """Get counts by service type"""
        stats: Dict[str, int] = {}
        for service in self._discovered_services.values():
            type_name = service.service_type.name
            stats[type_name] = stats.get(type_name, 0) + 1
        return stats


# Global registry instance
_registry: Optional[RNSServiceRegistry] = None


def get_service_registry() -> RNSServiceRegistry:
    """Get the global service registry instance"""
    global _registry
    if _registry is None:
        _registry = RNSServiceRegistry()
    return _registry
