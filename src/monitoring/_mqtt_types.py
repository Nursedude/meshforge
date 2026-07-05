"""Shared MQTT data types and validation ranges.

Leaf module — imports no other MeshForge internals. Lives here so that
``mqtt_subscriber.py`` and ``_mqtt_message_decoder.py`` can both import the
shared dataclasses/constants without forming an import cycle between each
other.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


VALID_LAT_RANGE = (-90.0, 90.0)
VALID_LON_RANGE = (-180.0, 180.0)
VALID_SNR_RANGE = (-50.0, 50.0)  # dB
VALID_RSSI_RANGE = (-200, 0)  # dBm


def node_num_to_id(value) -> Optional[str]:
    """Canonical ``!%08x`` node id from a wire ``from``/``to`` value.

    THE node-number canonicalizer — accepts an int or a numeric string
    (foreign publishers on shared MQTT roots json-encode numbers as
    strings, the #34 class) and masks to 32 bits so a negative or 64-bit
    value can never mint a malformed id like ``!-0000001``. Returns the
    value unchanged when it is already a ``!hex`` id, and None for
    bools/None/non-numeric — the caller keeps its own witness for that.
    Two independent copies of this formula (kilo edges vs the decoder)
    once disagreed on numeric strings, splitting one radio's identity
    across two keys; one shared function retires the class.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and value.startswith("!"):
        return value.lower()
    try:
        return f"!{int(value) & 0xFFFFFFFF:08x}"
    except (TypeError, ValueError):
        return None


@dataclass
class MQTTNode:
    """Node discovered via MQTT."""
    node_id: str
    long_name: str = ""
    short_name: str = ""
    hardware_model: str = ""
    role: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[float] = None
    battery_level: Optional[int] = None
    voltage: Optional[float] = None
    channel_utilization: Optional[float] = None
    air_util_tx: Optional[float] = None
    snr: Optional[float] = None
    rssi: Optional[int] = None
    last_seen: datetime = field(default_factory=datetime.now)
    via_mqtt: bool = True
    hop_start: Optional[int] = None
    hops_away: Optional[int] = None
    # Relay tracking (Meshtastic 2.6+)
    relay_node: Optional[int] = None  # Last byte of relay node ID
    next_hop: Optional[int] = None    # Last byte of expected next-hop node
    discovered_via_relay: bool = False  # Node discovered by seeing it relay packets
    # Environment metrics (BME280, BME680, BMP280)
    temperature: Optional[float] = None  # Celsius
    humidity: Optional[float] = None     # 0-100%
    pressure: Optional[float] = None     # hPa (barometric)
    gas_resistance: Optional[float] = None  # Ohms (BME680 VOC)
    # Air quality metrics (PMSA003I, SCD4X)
    pm25_standard: Optional[int] = None   # PM2.5 standard µg/m³
    pm25_environmental: Optional[int] = None  # PM2.5 environmental µg/m³
    pm10_standard: Optional[int] = None   # PM10 standard µg/m³
    pm10_environmental: Optional[int] = None  # PM10 environmental µg/m³
    co2: Optional[int] = None             # CO2 ppm (SCD4X)
    iaq: Optional[int] = None             # Indoor Air Quality index
    # Health metrics (MAX30102, pulse oximeters) - Meshtastic 2.7+
    heart_bpm: Optional[int] = None       # Heart rate (beats per minute)
    spo2: Optional[int] = None            # Blood oxygen saturation %
    body_temperature: Optional[float] = None  # Body temperature (Celsius)
    # Favorites (BaseUI 2.7+)
    is_favorite: bool = False             # Marked as favorite in BaseUI

    def is_online(self, threshold_minutes: int = 15) -> bool:
        """Check if node was seen recently."""
        delta = datetime.now() - self.last_seen
        return delta.total_seconds() < threshold_minutes * 60

    def get_age_string(self) -> str:
        """Get human-readable age string."""
        delta = datetime.now() - self.last_seen
        seconds = delta.total_seconds()
        if seconds < 60:
            return f"{int(seconds)}s ago"
        elif seconds < 3600:
            return f"{int(seconds / 60)}m ago"
        elif seconds < 86400:
            return f"{int(seconds / 3600)}h ago"
        else:
            return f"{int(seconds / 86400)}d ago"


@dataclass
class MQTTMessage:
    """Message received via MQTT."""
    message_id: str
    from_id: str
    to_id: str
    text: str
    channel: int = 0
    timestamp: datetime = field(default_factory=datetime.now)
    hop_start: Optional[int] = None
    snr: Optional[float] = None
    rssi: Optional[int] = None
