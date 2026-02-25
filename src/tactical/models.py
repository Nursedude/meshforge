"""
Tactical Message Models for MeshForge.

Typed dataclasses for structured tactical messages:
SITREP, TASK, CHECKIN, ZONE, RESOURCE, MISSION, EVENT, ASSET.

Each template has typed fields for compact X1 wire format encoding.
Priority levels align with ARES/RACES ICS standards.

Usage:
    from tactical.models import TacticalMessage, TacticalType, CheckIn

    msg = TacticalMessage(
        tactical_type=TacticalType.CHECKIN,
        sender_id="WH6GXZ",
        content=CheckIn(callsign="WH6GXZ", status="ok").to_dict(),
    )
"""

import logging
import os
import random
import string
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Crockford Base32 alphabet (excludes I, L, O, U to avoid ambiguity)
_CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class TacticalType(Enum):
    """Tactical message template types (X1 protocol T field)."""
    SITREP = 1      # Situation report
    TASK = 2        # Work assignment with status
    RESOURCE = 3    # Equipment/supply tracking
    CHECKIN = 4     # Position report / check-in
    ZONE = 5        # Geographic area marking
    MISSION = 6     # Operation definition
    EVENT = 7       # Timeline entry
    ASSET = 8       # Vehicle/equipment registry


class TacticalPriority(Enum):
    """Message priority levels (aligned with ICS/ARES standards)."""
    ROUTINE = "R"
    PRIORITY = "P"
    IMMEDIATE = "O"     # Operations Immediate
    FLASH = "F"


class EncryptionMode(Enum):
    """Encryption mode for ham compliance (X1 protocol M field)."""
    CLEAR = "C"         # Ham-legal, FCC Part 97 compliant — no encryption
    SECURE = "S"        # AES-256-GCM for non-ham use


def generate_message_id(length: int = 5) -> str:
    """Generate a Crockford Base32 message ID for dedup and reassembly.

    Args:
        length: Number of characters (default 5 = 32^5 = ~33 million IDs).

    Returns:
        Crockford Base32 string (e.g., "K7V3N").
    """
    return ''.join(random.choice(_CROCKFORD_ALPHABET) for _ in range(length))


@dataclass
class TacticalMessage:
    """
    Protocol-agnostic tactical message container.

    Wraps a template-specific content dict with routing and wire metadata.
    The content dict holds template-specific fields (SITREP, TASK, etc.).
    """
    # Identity
    id: str = field(default_factory=generate_message_id)

    # Classification
    tactical_type: TacticalType = TacticalType.CHECKIN
    priority: TacticalPriority = TacticalPriority.ROUTINE
    encryption_mode: EncryptionMode = EncryptionMode.CLEAR

    # Timing
    timestamp: datetime = field(default_factory=datetime.now)

    # Source
    sender_id: str = ""     # Node ID or callsign

    # Template-specific fields (serializable dict)
    content: Dict[str, Any] = field(default_factory=dict)

    # Wire format metadata (populated after encode/decode)
    raw_x1: Optional[str] = None

    def __str__(self) -> str:
        badge = f"[{self.encryption_mode.value}]"
        priority_str = self.priority.name
        return (
            f"{badge} {self.tactical_type.name} "
            f"(ID:{self.id}, {priority_str}) "
            f"from {self.sender_id}"
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for storage/transport."""
        return {
            'id': self.id,
            'tactical_type': self.tactical_type.value,
            'priority': self.priority.value,
            'encryption_mode': self.encryption_mode.value,
            'timestamp': self.timestamp.isoformat(),
            'sender_id': self.sender_id,
            'content': self.content,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TacticalMessage':
        """Deserialize from dictionary."""
        return cls(
            id=data.get('id', generate_message_id()),
            tactical_type=TacticalType(data['tactical_type']),
            priority=TacticalPriority(data.get('priority', 'R')),
            encryption_mode=EncryptionMode(data.get('encryption_mode', 'C')),
            timestamp=datetime.fromisoformat(data['timestamp'])
            if 'timestamp' in data else datetime.now(),
            sender_id=data.get('sender_id', ''),
            content=data.get('content', {}),
        )


# ============================================================================
# Template-Specific Dataclasses
# ============================================================================
# Each template defines the fields that go into TacticalMessage.content.
# They all provide to_dict() for serialization and from_dict() for parsing.


@dataclass
class SITREP:
    """Situation report (TacticalType.SITREP = 1)."""
    situation: str = ""
    actions_taken: str = ""
    resources_needed: str = ""
    casualties: str = ""
    # Optional position (lat, lon, alt)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {'situation': self.situation}
        if self.actions_taken:
            result['actions_taken'] = self.actions_taken
        if self.resources_needed:
            result['resources_needed'] = self.resources_needed
        if self.casualties:
            result['casualties'] = self.casualties
        if self.latitude is not None and self.longitude is not None:
            result['latitude'] = self.latitude
            result['longitude'] = self.longitude
            if self.altitude is not None:
                result['altitude'] = self.altitude
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SITREP':
        return cls(
            situation=data.get('situation', ''),
            actions_taken=data.get('actions_taken', ''),
            resources_needed=data.get('resources_needed', ''),
            casualties=data.get('casualties', ''),
            latitude=data.get('latitude'),
            longitude=data.get('longitude'),
            altitude=data.get('altitude'),
        )


@dataclass
class TaskAssignment:
    """Work assignment (TacticalType.TASK = 2)."""
    description: str = ""
    assignee: str = ""
    status: str = "assigned"    # assigned, in_progress, complete, cancelled
    due: Optional[str] = None   # ISO date string or free text

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            'description': self.description,
            'status': self.status,
        }
        if self.assignee:
            result['assignee'] = self.assignee
        if self.due:
            result['due'] = self.due
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TaskAssignment':
        return cls(
            description=data.get('description', ''),
            assignee=data.get('assignee', ''),
            status=data.get('status', 'assigned'),
            due=data.get('due'),
        )


@dataclass
class Resource:
    """Equipment/supply tracking (TacticalType.RESOURCE = 3)."""
    name: str = ""
    resource_type: str = ""     # medical, comms, transport, shelter, food, water
    quantity: int = 0
    status: str = "available"   # available, deployed, exhausted, requested
    location_name: str = ""

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            'name': self.name,
            'resource_type': self.resource_type,
            'quantity': self.quantity,
            'status': self.status,
        }
        if self.location_name:
            result['location_name'] = self.location_name
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Resource':
        return cls(
            name=data.get('name', ''),
            resource_type=data.get('resource_type', ''),
            quantity=int(data.get('quantity', 0)),
            status=data.get('status', 'available'),
            location_name=data.get('location_name', ''),
        )


@dataclass
class CheckIn:
    """Position report / check-in (TacticalType.CHECKIN = 4)."""
    callsign: str = ""
    status: str = "ok"          # ok, needs_help, injured, evacuating
    personnel_count: int = 1
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[float] = None
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            'callsign': self.callsign,
            'status': self.status,
            'personnel_count': self.personnel_count,
        }
        if self.latitude is not None and self.longitude is not None:
            result['latitude'] = self.latitude
            result['longitude'] = self.longitude
            if self.altitude is not None:
                result['altitude'] = self.altitude
        if self.notes:
            result['notes'] = self.notes
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CheckIn':
        return cls(
            callsign=data.get('callsign', ''),
            status=data.get('status', 'ok'),
            personnel_count=int(data.get('personnel_count', 1)),
            latitude=data.get('latitude'),
            longitude=data.get('longitude'),
            altitude=data.get('altitude'),
            notes=data.get('notes', ''),
        )


@dataclass
class ZoneMarking:
    """Geographic area marking (TacticalType.ZONE = 5)."""
    name: str = ""
    zone_type: str = ""         # hazard, safe, staging, exclusion, operations
    center_lat: float = 0.0
    center_lon: float = 0.0
    radius_m: float = 0.0      # Circle radius in meters; 0 = point marker
    vertices: List[Tuple[float, float]] = field(default_factory=list)

    def is_polygon(self) -> bool:
        """Check if zone is a polygon (vs circle)."""
        return len(self.vertices) >= 3

    def is_circle(self) -> bool:
        """Check if zone is a circle."""
        return self.radius_m > 0 and not self.is_polygon()

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            'name': self.name,
            'zone_type': self.zone_type,
            'center_lat': self.center_lat,
            'center_lon': self.center_lon,
        }
        if self.radius_m > 0:
            result['radius_m'] = self.radius_m
        if self.vertices:
            result['vertices'] = self.vertices
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ZoneMarking':
        return cls(
            name=data.get('name', ''),
            zone_type=data.get('zone_type', ''),
            center_lat=float(data.get('center_lat', 0.0)),
            center_lon=float(data.get('center_lon', 0.0)),
            radius_m=float(data.get('radius_m', 0.0)),
            vertices=[tuple(v) for v in data.get('vertices', [])],
        )


@dataclass
class Mission:
    """Operation definition (TacticalType.MISSION = 6)."""
    name: str = ""
    objective: str = ""
    commander: str = ""
    start_time: Optional[str] = None    # ISO datetime
    end_time: Optional[str] = None
    status: str = "planned"     # planned, active, complete, aborted

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            'name': self.name,
            'objective': self.objective,
            'status': self.status,
        }
        if self.commander:
            result['commander'] = self.commander
        if self.start_time:
            result['start_time'] = self.start_time
        if self.end_time:
            result['end_time'] = self.end_time
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Mission':
        return cls(
            name=data.get('name', ''),
            objective=data.get('objective', ''),
            commander=data.get('commander', ''),
            start_time=data.get('start_time'),
            end_time=data.get('end_time'),
            status=data.get('status', 'planned'),
        )


@dataclass
class Event:
    """Timeline entry (TacticalType.EVENT = 7)."""
    description: str = ""
    event_type: str = ""        # info, warning, critical, milestone
    reported_by: str = ""

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            'description': self.description,
        }
        if self.event_type:
            result['event_type'] = self.event_type
        if self.reported_by:
            result['reported_by'] = self.reported_by
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Event':
        return cls(
            description=data.get('description', ''),
            event_type=data.get('event_type', ''),
            reported_by=data.get('reported_by', ''),
        )


@dataclass
class Asset:
    """Vehicle/equipment registry (TacticalType.ASSET = 8)."""
    name: str = ""
    asset_type: str = ""        # vehicle, radio, generator, antenna, shelter
    identifier: str = ""        # Serial number, plate number, etc.
    status: str = "available"   # available, deployed, maintenance, lost
    assigned_to: str = ""

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            'name': self.name,
            'asset_type': self.asset_type,
            'status': self.status,
        }
        if self.identifier:
            result['identifier'] = self.identifier
        if self.assigned_to:
            result['assigned_to'] = self.assigned_to
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Asset':
        return cls(
            name=data.get('name', ''),
            asset_type=data.get('asset_type', ''),
            identifier=data.get('identifier', ''),
            status=data.get('status', 'available'),
            assigned_to=data.get('assigned_to', ''),
        )


# Template class registry for lookup by TacticalType
TEMPLATE_CLASSES = {
    TacticalType.SITREP: SITREP,
    TacticalType.TASK: TaskAssignment,
    TacticalType.RESOURCE: Resource,
    TacticalType.CHECKIN: CheckIn,
    TacticalType.ZONE: ZoneMarking,
    TacticalType.MISSION: Mission,
    TacticalType.EVENT: Event,
    TacticalType.ASSET: Asset,
}
