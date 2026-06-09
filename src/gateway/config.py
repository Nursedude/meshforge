"""
Gateway Configuration Management
Handles persistent configuration for RNS-Meshtastic bridge
"""

import json
import os
import re
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict, Any, Tuple
import logging

from utils.safe_import import safe_import
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)

# Import config drift validation (optional)
_validate_gateway_rns_config, _HAS_CONFIG_DRIFT = safe_import(
    'utils.config_drift', 'validate_gateway_rns_config'
)


# =============================================================================
# CONFIGURATION VALIDATION
# =============================================================================

class ConfigValidationError:
    """Represents a configuration validation error or warning."""
    def __init__(self, field: str, message: str, severity: str = "error"):
        self.field = field
        self.message = message
        self.severity = severity  # "error", "warning", "info"

    def __str__(self):
        return f"[{self.severity.upper()}] {self.field}: {self.message}"


def validate_regex(pattern: str, field_name: str) -> Optional[ConfigValidationError]:
    """Validate that a string is a valid regex pattern."""
    if not pattern:
        return None  # Empty is valid (means "match all")
    try:
        re.compile(pattern)
        return None
    except re.error as e:
        return ConfigValidationError(field_name, f"Invalid regex: {e}")


def validate_port(port: int, field_name: str) -> Optional[ConfigValidationError]:
    """Validate that a port number is in valid range."""
    if not 1 <= port <= 65535:
        return ConfigValidationError(field_name, f"Port {port} out of range (1-65535)")
    return None


def validate_hop_limit(hop_limit: int, field_name: str) -> Optional[ConfigValidationError]:
    """Validate hop limit is in Meshtastic range."""
    if not 1 <= hop_limit <= 7:
        return ConfigValidationError(field_name, f"Hop limit {hop_limit} out of range (1-7)")
    return None


def validate_data_speed(speed: int, field_name: str) -> Optional[ConfigValidationError]:
    """Validate data speed preset."""
    if not 0 <= speed <= 8:
        return ConfigValidationError(field_name, f"Data speed {speed} out of range (0-8)")
    return None


def validate_bridge_mode(mode: str, field_name: str) -> Optional[ConfigValidationError]:
    """Validate bridge mode."""
    valid_modes = [
        "mqtt_bridge", "message_bridge", "rns_transport", "mesh_bridge",
        "meshcore_bridge", "tri_bridge",
    ]
    if mode not in valid_modes:
        return ConfigValidationError(field_name, f"Invalid bridge mode '{mode}'. Valid: {valid_modes}")
    return None


def validate_meshcore_connection(conn_type: str, field_name: str) -> Optional[ConfigValidationError]:
    """Validate MeshCore connection type."""
    valid = ["serial", "tcp", "ble"]
    if conn_type not in valid:
        return ConfigValidationError(field_name, f"Invalid connection type '{conn_type}'. Valid: {valid}")
    return None


def validate_direction(direction: str, field_name: str) -> Optional[ConfigValidationError]:
    """Validate routing direction."""
    valid = [
        "bidirectional", "mesh_to_rns", "rns_to_mesh",
        "primary_to_secondary", "secondary_to_primary",
        "mesh_to_meshcore", "meshcore_to_mesh",
        "rns_to_meshcore", "meshcore_to_rns",
        "all_to_all",
    ]
    if direction not in valid:
        return ConfigValidationError(field_name, f"Invalid direction '{direction}'. Valid: {valid}")
    return None


def validate_dedup_window(seconds: int, field_name: str) -> Optional[ConfigValidationError]:
    """Validate dedup window is reasonable."""
    if seconds < 10:
        return ConfigValidationError(
            field_name,
            f"Dedup window {seconds}s is very short (may miss duplicates)",
            severity="warning"
        )
    if seconds > 600:
        return ConfigValidationError(
            field_name,
            f"Dedup window {seconds}s is very long (may block legitimate messages)",
            severity="warning"
        )
    return None


def validate_speed_hop_combination(speed: int, hop_limit: int) -> Optional[ConfigValidationError]:
    """Check for incompatible speed/hop combinations."""
    # High speed + high hops = likely packet loss due to timing
    if speed >= 7 and hop_limit >= 5:
        return ConfigValidationError(
            "rns_transport",
            f"Speed {speed} with hop_limit {hop_limit} may cause reliability issues (fast speed + many hops)",
            severity="warning"
        )
    # Low speed + low hops = underutilizing range
    if speed <= 2 and hop_limit <= 2:
        return ConfigValidationError(
            "rns_transport",
            f"Speed {speed} with hop_limit {hop_limit} may underutilize range capability",
            severity="info"
        )
    return None


def validate_log_level(level: str, field_name: str) -> Optional[ConfigValidationError]:
    """Validate that log_level is a standard Python logging level."""
    valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    if level.upper() not in valid_levels:
        return ConfigValidationError(
            field_name, f"Invalid log level '{level}'. Valid: {valid_levels}")
    return None


def validate_channel(channel: int, field_name: str) -> Optional[ConfigValidationError]:
    """Validate Meshtastic channel index (0-7)."""
    if not 0 <= channel <= 7:
        return ConfigValidationError(
            field_name, f"Channel {channel} out of range (0-7)")
    return None


def validate_channel_list(channels, field_name: str) -> List[ConfigValidationError]:
    """Validate a channel allow-list: a list of Meshtastic channel indexes.

    Returns a list of errors (empty = valid). Rejects non-list values and
    non-integer entries loudly — a typo'd allow-list silently bridging the
    wrong channels is exactly the failure shape this feature exists to stop.
    Note: bool is an int subclass in Python, so True/False are rejected
    explicitly.
    """
    if not isinstance(channels, list):
        return [ConfigValidationError(
            field_name,
            f"Must be a list of channel indexes 0-7, got {type(channels).__name__}")]
    errors = []
    for i, ch in enumerate(channels):
        if isinstance(ch, bool) or not isinstance(ch, int):
            errors.append(ConfigValidationError(
                f"{field_name}[{i}]",
                f"Channel index must be an integer 0-7, got {ch!r}"))
            continue
        err = validate_channel(ch, f"{field_name}[{i}]")
        if err:
            errors.append(err)
    return errors


def validate_baud_rate(baud: int, field_name: str) -> Optional[ConfigValidationError]:
    """Validate serial baud rate is a standard value."""
    standard_rates = [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]
    if baud not in standard_rates:
        return ConfigValidationError(
            field_name,
            f"Non-standard baud rate {baud}. Standard: {standard_rates}",
            severity="warning"
        )
    return None


def validate_position_precision(precision: int, field_name: str) -> Optional[ConfigValidationError]:
    """Validate telemetry position precision (decimal places)."""
    if not 0 <= precision <= 10:
        return ConfigValidationError(
            field_name, f"Position precision {precision} out of range (0-10)")
    return None


def validate_update_interval(interval: int, field_name: str) -> Optional[ConfigValidationError]:
    """Validate telemetry update interval is reasonable."""
    if interval < 10:
        return ConfigValidationError(
            field_name,
            f"Update interval {interval}s is very short (min recommended: 10s)",
            severity="warning"
        )
    if interval > 86400:
        return ConfigValidationError(
            field_name,
            f"Update interval {interval}s exceeds 24 hours",
            severity="warning"
        )
    return None


def validate_hostname_config(host: str, field_name: str) -> Optional[ConfigValidationError]:
    """Validate hostname using shared validator from utils.validation."""
    from utils.validation import validate_hostname as _validate_host
    if not host:
        return ConfigValidationError(
            field_name, "Hostname is empty", severity="warning")
    if not _validate_host(host):
        return ConfigValidationError(
            field_name, f"Invalid hostname/IP: '{host}'")
    return None


@dataclass
class MeshtasticConfig:
    """Meshtastic connection configuration"""
    host: str = "localhost"
    port: int = 4403
    channel: int = 0  # Primary channel for gateway messages
    use_mqtt: bool = False
    mqtt_topic: str = ""
    # MQTT connection settings (used when use_mqtt=True)
    mqtt_broker: str = "localhost"
    mqtt_port: int = 1883
    mqtt_channel: str = "LongFast"
    mqtt_region: str = "US"
    # HTTP port for protobuf TX (meshtasticd web server). meshtasticd
    # serves its web API on 9443 (see /etc/meshtasticd/config.yaml and
    # Issue #58 — a :443 webserver override is a forbidden config shape).
    # 443 was a wrong default that shipped in rendered fleet configs;
    # load() migrates it (see _migrate_stale_http_port).
    http_port: int = 9443
    # LoRa preset identifier (for documentation/display)
    # Values: LONG_FAST, LONG_SLOW, MEDIUM_FAST, MEDIUM_SLOW,
    #         SHORT_FAST, SHORT_SLOW, SHORT_TURBO
    preset: str = ""
    # Friendly name for this connection
    name: str = "primary"
    # Connection type: "" (auto — tcp unless use_mqtt set), "tcp", "mqtt", "serial"
    # "serial" talks directly to a USB-attached Meshtastic device via
    # meshtastic.serial_interface.SerialInterface — no second meshtasticd needed.
    connection_type: str = ""
    # Serial device path when connection_type="serial". Empty = auto-detect.
    serial_device: str = ""
    # The gateway's own Meshtastic node ID (e.g., "!ebfa1b11"). When set, inbound
    # MQTT messages with `sender == gateway_node_id` are dropped to break the
    # echo loop where meshtasticd republishes the gateway's own outbound TX as
    # an incoming JSON/protobuf message — which would otherwise re-bridge back
    # to RNS. Empty disables the filter (no echo dropping).
    gateway_node_id: str = ""
    # Mesh injection mode for cross-bridge traffic forwarded onto THIS radio:
    #   "toradio"  (default) — HTTP /api/v1/toradio; `from` becomes the local
    #              radio, so :9443 never shows it as incoming (self-TX).
    #   "downlink" — publish a true-origin protobuf MQTT downlink so meshtasticd
    #              attributes it to the real source node and the web client
    #              renders it (see gateway/mqtt_downlink_inject.py). Requires
    #              downlink_psk; falls back to toradio on any failure.
    injection_mode: str = "toradio"
    # Channel PSK (base64) used to encrypt downlink envelopes. MUST match the
    # radio's channel PSK. Operator-specific secret — source at runtime / keep
    # out of committed configs (security rules MF014/MF015). Empty disables
    # downlink injection (falls back to toradio).
    downlink_psk: str = ""


@dataclass
class MeshtasticBridgeConfig:
    """
    Configuration for Meshtastic-to-Meshtastic preset bridging.

    Bridges two separate Meshtastic networks with different LoRa presets.
    Requires two radios/meshtasticd instances, one for each preset.

    Use case: Bridge a LONG_FAST rural mesh with a SHORT_TURBO local mesh.
    """
    enabled: bool = False

    # Primary interface (usually LONG_FAST for wider coverage)
    primary: MeshtasticConfig = field(default_factory=lambda: MeshtasticConfig(
        host="localhost",
        port=4403,
        preset="LONG_FAST",
        name="longfast"
    ))

    # Secondary interface (usually SHORT_TURBO for local high-speed)
    secondary: MeshtasticConfig = field(default_factory=lambda: MeshtasticConfig(
        host="localhost",
        port=4404,  # Different port for second meshtasticd
        preset="SHORT_TURBO",
        name="shortturbo"
    ))

    # Bridging direction
    # "bidirectional" - Forward messages both ways
    # "primary_to_secondary" - Only forward from primary to secondary
    # "secondary_to_primary" - Only forward from secondary to primary
    direction: str = "bidirectional"

    # Channel allow-list (Meshtastic channel indexes 0-7). When non-empty,
    # only text received on these channel indexes is bridged — applies to
    # BOTH directions and ALL connection types (serial RX hears every
    # channel of its radio, so without this a secondary's ch0 text would
    # be re-TXed on the primary's ch0, which may be a public channel).
    # Empty = bridge all channels (backward compatible).
    # Forwards preserve channel index, so the allow-listed indexes should
    # carry the same channel (name + PSK) on both radios.
    channels: List[int] = field(default_factory=list)

    # Message filtering
    # Forward only messages matching these patterns (empty = all)
    message_filter: str = ""
    # Exclude messages matching these patterns
    exclude_filter: str = ""

    # Duplicate suppression (seconds)
    # Prevent message loops by not re-forwarding recently seen messages
    dedup_window_sec: int = 60

    # Add prefix to forwarded messages. The default uses the "[Mesh:" tag
    # from BRIDGE_TAG_PREFIXES (base_handler.is_already_bridged) so that NO
    # gateway — this one or an LXMF peer sharing an RF segment — ever
    # re-bridges a mesh_bridge forward. An untagged prefix re-opens the
    # cross-gateway echo-amplification loop (moc, 2026-06-03).
    # Placeholders: {source_preset}, {source_id} (last 4 chars).
    add_prefix: bool = True
    prefix_format: str = "[Mesh:{source_preset}] "


@dataclass
class MQTTBridgeConfig:
    """
    MQTT configuration for gateway bridge transport.

    meshtasticd publishes mesh packets to MQTT natively. The gateway
    subscribes to receive mesh traffic without holding a TCP connection.

    This is the zero-interference path: meshtasticd simultaneously
    serves the web client on :9443, accepts TCP on :4403, AND publishes
    to MQTT. These are independent subsystems.

    Requires:
        - MQTT broker running (apt install mosquitto)
        - meshtasticd mqtt.enabled=true, mqtt.json_enabled=true
    """
    broker: str = "localhost"
    port: int = 1883
    use_tls: bool = False
    username: str = ""
    password: str = ""
    # Topic structure: {root_topic}/[{region}/]2/json/{channel}/{node_id}
    # Region is optional — meshtasticd 2.7.x publishes without it. Leave empty
    # unless your daemon build includes region in the topic path (older releases).
    # RX always subscribes to both shapes; TX uses region only when set.
    root_topic: str = "msh"
    region: str = ""
    channel: str = "LongFast"
    # JSON mode (recommended - human-readable, no protobuf dependency)
    json_enabled: bool = True


@dataclass
class MeshCoreConfig:
    """
    MeshCore companion radio configuration.

    Unlike Meshtastic (which uses meshtasticd as a daemon), MeshForge connects
    directly to the MeshCore companion radio via meshcore_py. Three connection
    methods are supported:

      serial  — USB cable to companion radio (most common for gateway setups)
      tcp     — Network connection to a companion radio running WiFi firmware,
                or to a serial-to-TCP bridge (e.g. ser2net)
      ble     — Bluetooth LE (config ready; pending meshcore_py BLE transport)

    Requires: pip install meshcore (Python 3.10+)
    Hardware: MeshCore companion radio (RAK4631, Heltec V3, T-Deck, etc.)
    """
    enabled: bool = False

    # Connection settings — choose one of: serial | tcp | ble
    device_path: str = "/dev/ttyUSB1"    # Serial: USB device path
    baud_rate: int = 115200              # Serial: baud rate
    connection_type: str = "serial"       # serial | tcp | ble
    tcp_host: str = ""                   # TCP: hostname or IP
    tcp_port: int = 4000                 # TCP: port (meshcore-cli default 5000)

    # Message handling
    auto_fetch_messages: bool = True      # Start auto message fetching on connect
    bridge_channels: bool = True          # Bridge channel (broadcast) messages
    bridge_dms: bool = True               # Bridge direct messages

    # Testing
    simulation_mode: bool = False         # Run without hardware (fake events)

    # Polling fallback for CHANNEL_MSG_RECV event bug (meshcore_py #1232)
    channel_poll_interval_sec: int = 5


@dataclass
class RNSConfig:
    """Reticulum Network Stack configuration"""
    config_dir: str = ""  # Empty = default ~/.reticulum
    identity_name: str = "meshforge_gateway"
    gateway_name: str = ""  # Announced LXMF display_name; empty = "MeshForge Gateway ({hostname})"
    announce_interval: int = 300  # seconds
    propagation_node: str = ""  # Optional propagation node address
    # Hex hash(es) where broadcast Mesh→RNS is delivered. Accepts either a single
    # 32-hex string (legacy) or a list of 32-hex strings (multi-recipient — gateway
    # broadcasts the same Meshtastic message to each LXMF destination). Use the
    # list form when multiple NomadNet operators want to see the same bridged feed.
    default_lxmf_destination: Any = ""
    # Hex hash(es) of OTHER MeshForge gateways in this operator's cluster. When
    # set, R→M arrivals that did not originate from a peer (no
    # ``meshforge_relayed_by`` LXMF field) are relayed to each peer gateway
    # so a NomadNet send into one gateway thread reaches every RF preset the
    # cluster covers. Distinct from default_lxmf_destination, which is the
    # M→R fan-out list (operator NomadNet inboxes). Same single-or-list
    # shape as default_lxmf_destination.
    peer_gateway_destinations: Any = ""
    # Theme-A step 1 — reply routing. When True, the bridge records which
    # mesh node last messaged each RNS peer (reply-context memory) and
    # honors reply addressing on R→M: explicit @addr > echoed
    # meshforge_reply_to field > reply-context memory > broadcast.
    # Default False (observe-first rollout); the meshforge_reply_to LXMF
    # field is emitted M→R regardless (pure metadata).
    reply_routing_enabled: bool = False
    reply_context_ttl_sec: int = 86400      # forget a peer's thread after ~24h
    reply_context_max_entries: int = 1024   # LRU cap on remembered peers
    # Theme-A step 2 — cross-protocol identity SSOT (IdentityBinder over the
    # contact_mapping.db). When True, the bridge auto-populates contacts from
    # observed traffic (conservative name-based auto-bind, unverified), the
    # R→M reply chain gains a contact rung (after reply-context memory), and
    # M→R directed DMs fall back to contact mapping when node_tracker has no
    # rns_hash. Default False (observe-first rollout, moc first). The
    # operator CLI (scripts/gateway_contacts.py) works regardless of this
    # flag — explicit links are always allowed (verified=1).
    cross_protocol_identity_enabled: bool = False
    identity_population_throttle_sec: int = 900   # per-address SQLite write throttle
    identity_max_contacts: int = 5000             # auto-discover growth guard
    # Theme-A step 3 — durable session layer. When True, a Meshtastic DM
    # addressed to the gateway's OWN node (meshtastic.gateway_node_id) is
    # routed as a private LXMF send to the peer of the sender's most-recent
    # active session (idle-TTL'd, survives restart in gateway_sessions.db).
    # Default False. No-session fallback = today's broadcast fan-out.
    sessions_enabled: bool = False
    session_idle_timeout_sec: int = 86400   # forget an idle session after ~24h
    session_max_entries: int = 1024         # row cap, oldest-evicted
    # Dual-path dedup (2026-06-04). On a box whose LOCAL mesh_bridge also
    # carries the same RF traffic (e.g. an ST serial leg forwarding onto the
    # primary radio), a peer gateway's Mesh→RNS relay arrives back here and
    # goes out as a SECOND copy of content the mesh_bridge already
    # transmitted seconds earlier. When True, the R→M broadcast path checks
    # the process-wide RecentRfTxRegistry and suppresses its copy ONLY on a
    # hit — unconditional suppression would lose messages (live trace: ~40%
    # of relayed events arrived ONLY via RNS because the local radio missed
    # them on RF). Default off (canary-first). DMs are never suppressed.
    dual_path_dedup_enabled: bool = False
    dual_path_dedup_window_sec: int = 60    # registry hit window (seconds)
    # Theme-A step 4 (Thread-2 Phase 2) — honest Meshtastic delivery
    # confirmation. When True, directed downlinks (DMs to a specific mesh
    # node) are sent wantAck=True and the gateway consumes the recipient's
    # ROUTING_APP ACK/NAK, recording the real CONFIRMED / DROPPED(reason)
    # terminal state in delivery_counters. This makes Meshtastic join the
    # confirmable set (Issue #74) with end-to-end proof instead of the
    # "Sent (not guaranteed)" ceiling (#16). Default False (observe-first
    # rollout). Broadcasts are unaffected (no per-node ACK exists). RX
    # parsing of ROUTING_APP is inert when off: without wantAck the
    # recipient sends no ACK, so the in-flight tracker stays empty.
    # ⚠️ TCP-MODE ONLY: the ACK is consumed via the persistent
    # meshtastic.receive stream in the TCP MeshtasticHandler. In
    # bridge_mode=mqtt_bridge (the fleet default, zero-interference) the
    # gateway TXes via HTTP toradio and RXes via MQTT json — which carries
    # no ROUTING_APP — so this flag is INERT there and the handler warns at
    # startup. Honest mesh confirmation in mqtt_bridge mode would need a
    # different signal (the ACK is unreachable without reading fromradio,
    # which that mode exists to avoid per #17/#75).
    meshtastic_ack_consumption_enabled: bool = False
    ack_pending_ttl_sec: int = 600          # forget an un-acked DM after ~10 min
    ack_pending_max: int = 1024             # in-flight DM cap, oldest-evicted

    def get_lxmf_destinations(self) -> List[str]:
        """Return default_lxmf_destination normalized to a list of non-empty hex strings."""
        raw = self.default_lxmf_destination
        if isinstance(raw, str):
            return [raw] if raw else []
        if isinstance(raw, (list, tuple)):
            return [d for d in raw if isinstance(d, str) and d]
        return []

    def get_peer_gateway_destinations(self) -> List[str]:
        """Return peer_gateway_destinations normalized to a list of non-empty hex strings."""
        raw = self.peer_gateway_destinations
        if isinstance(raw, str):
            return [raw] if raw else []
        if isinstance(raw, (list, tuple)):
            return [d for d in raw if isinstance(d, str) and d]
        return []


@dataclass
class RNSOverMeshtasticConfig:
    """
    RNS Over Meshtastic transport configuration.

    When enabled, RNS uses Meshtastic as a network transport layer,
    allowing RNS packets to traverse LoRa mesh networks.

    Based on: https://github.com/landandair/RNS_Over_Meshtastic
    """
    enabled: bool = False

    # Connection type: "serial", "tcp", "ble"
    connection_type: str = "tcp"

    # Device path based on connection type:
    # - serial: /dev/ttyUSB0, /dev/ttyACM0
    # - tcp: localhost:4403 (meshtasticd)
    # - ble: device_name or MAC address
    device_path: str = "localhost:4403"

    # LoRa speed preset (0-8, maps to Meshtastic modem presets)
    # 8 = SHORT_TURBO (fastest, ~500 B/s, shortest range)
    # 6 = SHORT_FAST (~300 B/s)
    # 5 = SHORT_SLOW (~150 B/s)
    # 4 = MEDIUM_FAST (~100 B/s)
    # 0 = LONG_FAST (slowest, ~50 B/s, longest range)
    data_speed: int = 8  # Default: SHORT_TURBO for RNS

    # Mesh hop limit (1-7)
    hop_limit: int = 3

    # Packet handling
    fragment_timeout_sec: int = 30  # Discard incomplete after timeout
    max_pending_fragments: int = 100  # Prevent memory exhaustion

    # Monitoring
    enable_stats: bool = True
    stats_interval_sec: int = 60

    # Performance thresholds for alerts
    packet_loss_threshold: float = 0.1  # Alert if >10% loss
    latency_threshold_ms: int = 5000  # Alert if >5s roundtrip

    def get_throughput_estimate(self) -> dict:
        """Estimate throughput based on speed preset."""
        speed_info = {
            8: {'name': 'SHORT_TURBO', 'delay': 0.4, 'bps': 500, 'range': 'short'},
            7: {'name': 'SHORT_FAST+', 'delay': 0.5, 'bps': 400, 'range': 'short'},
            6: {'name': 'SHORT_FAST', 'delay': 1.0, 'bps': 300, 'range': 'medium'},
            5: {'name': 'SHORT_SLOW', 'delay': 3.0, 'bps': 150, 'range': 'medium-long'},
            4: {'name': 'MEDIUM_FAST', 'delay': 4.0, 'bps': 100, 'range': 'long'},
            3: {'name': 'MEDIUM_SLOW', 'delay': 5.0, 'bps': 80, 'range': 'long'},
            2: {'name': 'LONG_MODERATE', 'delay': 6.0, 'bps': 60, 'range': 'very long'},
            1: {'name': 'LONG_SLOW', 'delay': 7.0, 'bps': 55, 'range': 'very long'},
            0: {'name': 'LONG_FAST', 'delay': 8.0, 'bps': 50, 'range': 'maximum'},
        }
        return speed_info.get(self.data_speed, speed_info[8])


@dataclass
class RoutingRule:
    """Message routing rule between networks"""
    name: str
    enabled: bool = True
    direction: str = "bidirectional"  # "rns_to_mesh", "mesh_to_rns", "bidirectional"
    source_filter: str = ""  # Regex for source address filtering
    dest_filter: str = ""  # Regex for destination filtering
    message_filter: str = ""  # Regex for message content filtering
    transform: str = ""  # Optional message transformation
    priority: int = 0


@dataclass
class TelemetryConfig:
    """Telemetry sharing configuration"""
    share_position: bool = True
    share_battery: bool = True
    share_environment: bool = True
    position_precision: int = 5  # Decimal places for lat/lon
    update_interval: int = 60  # seconds


@dataclass
class MeshtasticBroadcastConfig:
    """Meshtastic broadcast bridge — fan-out Meshtastic channels as LXMF DMs.

    Symmetric mirror of MeshAnchor's LXMFBroadcastConfig: a separate LXMF
    identity announces over RNS; subscribers DM the identity
    ("subscribe" / "unsubscribe") and receive every Meshtastic channel
    broadcast on the allowlisted channels as an LXMF DM. RX-only fan-out
    today; no reverse path back into Meshtastic.
    """
    enabled: bool = False
    # Meshtastic channels to fan out (channel indexes, e.g. 0=primary,
    # 2=meshforge on moc-class boxes).
    channels: List[int] = field(default_factory=lambda: [0])
    # Display name shown to NomadNet/peer LXMF clients in announces.
    display_name: str = "MeshForge Meshtastic Broadcast"
    # Re-announce period (seconds). 0 disables periodic announce.
    announce_interval_sec: int = 600
    # Outbound message format. Available fields: channel, sender, text.
    prefix_format: str = "[meshtastic ch{channel}:{sender}] {text}"
    # If True, any LXMF source that announces is auto-subscribed.
    # Default False — subscription is opt-in via "subscribe" DM.
    autosubscribe: bool = False
    # Optional override paths. Empty = default under ~/.config/meshforge/.
    identity_file: str = ""
    db_file: str = ""

    # Issue #66 first-caller opt-in: when True, every broadcast fan-out
    # registers ONE pending-ack record with the parent bridge's substrate
    # and the first subscriber whose LXMF receipt confirms delivery causes
    # a synthetic [delivered:<id>] to be emitted back to the originating
    # Meshtastic channel as a placeholder origin (channel:<idx>). See
    # [[sync-ack-gateway-arc]] and project_issue_66_first_caller_2026_05_18.md.
    ack_required: bool = False


@dataclass
class GatewayConfig:
    """Complete gateway configuration"""
    enabled: bool = False
    auto_start: bool = False

    # Bridge mode: advisory label only since the composable-bridges refactor.
    # Each bridge's ``.enabled`` field (or ``rns_bridge_enabled`` below) is
    # what actually gates startup — bridge_mode is kept for back-compat and
    # for the "Mode:" line printed at startup. Legacy values still parse.
    # - mqtt_bridge / message_bridge: implies rns_bridge_enabled=True
    # - rns_transport: implies rns_transport.enabled=True
    # - mesh_bridge: implies mesh_bridge.enabled=True
    # - meshcore_bridge / tri_bridge: implies meshcore.enabled=True
    bridge_mode: str = "mqtt_bridge"

    # Explicit gate for the default RNS <-> Meshtastic bridge
    # (RNSMeshtasticBridge). True by default — the common deployment.
    # Set False only for specialized boxes (e.g. a pure cross-preset
    # mesh_bridge testbed that does not carry RNS/NomadNet traffic).
    rns_bridge_enabled: bool = True

    # Network configurations
    meshtastic: MeshtasticConfig = field(default_factory=MeshtasticConfig)
    rns: RNSConfig = field(default_factory=RNSConfig)

    # MQTT bridge transport (used when bridge_mode="mqtt_bridge")
    mqtt_bridge: MQTTBridgeConfig = field(default_factory=MQTTBridgeConfig)

    # RNS Over Meshtastic transport (used when bridge_mode="rns_transport")
    rns_transport: RNSOverMeshtasticConfig = field(default_factory=RNSOverMeshtasticConfig)

    # Meshtastic-to-Meshtastic bridge (used when bridge_mode="mesh_bridge")
    # Bridges different LoRa presets (e.g., LONG_FAST <> SHORT_TURBO)
    mesh_bridge: MeshtasticBridgeConfig = field(default_factory=MeshtasticBridgeConfig)

    # MeshCore companion radio (used when bridge_mode="meshcore_bridge" or "tri_bridge")
    meshcore: MeshCoreConfig = field(default_factory=MeshCoreConfig)

    # Routing (used when bridge_mode="message_bridge")
    routing_rules: List[RoutingRule] = field(default_factory=list)
    default_route: str = "bidirectional"

    # Telemetry
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)

    # Meshtastic broadcast bridge (symmetric mirror of MeshAnchor's
    # lxmf_broadcast). Off by default — operator opts in on a gateway box
    # to fan Meshtastic channels out as LXMF DMs to subscribed RNS peers.
    meshtastic_broadcast: MeshtasticBroadcastConfig = field(
        default_factory=MeshtasticBroadcastConfig
    )

    # Logging
    log_level: str = "INFO"
    log_messages: bool = True

    # Dual-radio failover (requires two meshtasticd instances)
    failover_enabled: bool = False
    failover_utilization_threshold: float = 25.0   # % channel utilization trigger
    failover_utilization_duration: int = 30         # Seconds sustained above threshold
    failover_recovery_threshold: float = 15.0      # % utilization for switchback
    failover_recovery_duration: int = 60            # Seconds stable below threshold
    failover_health_poll_interval: float = 5.0     # Seconds between health checks

    # Service watchdog — auto-restart crashed meshtasticd (requires failover_enabled)
    failover_watchdog_enabled: bool = True          # Enable service watchdog
    failover_restart_after_failures: int = 5        # Poll failures before restart attempt
    failover_max_restarts_per_hour: int = 3         # Prevent restart loops
    failover_restart_cooldown: int = 60             # Seconds between restart attempts
    failover_primary_service: str = "meshtasticd"   # systemd service name (primary)
    failover_secondary_service: str = "meshtasticd-alt"  # systemd service name (secondary)

    # Cross-gateway failover (requires MQTT broker)
    gateway_heartbeat_enabled: bool = False
    gateway_heartbeat_broker: str = "localhost"
    gateway_heartbeat_port: int = 1883
    gateway_heartbeat_interval: float = 15.0        # Seconds between heartbeats
    gateway_heartbeat_missed_threshold: int = 4     # Misses before declaring peer dead
    gateway_role: str = "primary"                   # "primary" or "secondary"
    gateway_id: str = ""                            # Auto-generated from hostname if empty

    # TX load balancing (requires two meshtasticd instances)
    # Uses tx_utilization (our TX contribution) instead of channel_utilization
    # (identical on same-channel radios). Splits outbound traffic across two
    # radios to keep each radio's TX duty cycle below firmware throttle limits.
    load_balancer_enabled: bool = False
    load_balancer_tx_threshold: float = 10.0       # % TX airtime to start splitting
    load_balancer_tx_max: float = 20.0             # % TX airtime for full offload
    load_balancer_health_poll_interval: float = 5.0  # Seconds between health checks
    load_balancer_recovery_margin: float = 2.0     # Hysteresis margin for IDLE recovery

    # AI Diagnostics
    ai_diagnostics_enabled: bool = False
    snr_analysis: bool = True
    anomaly_detection: bool = False

    @classmethod
    def get_config_path(cls) -> Path:
        """Get the configuration file path"""
        config_dir = get_real_user_home() / ".config" / "meshforge"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "gateway.json"

    @staticmethod
    def _migrate_stale_http_port(meshtastic_data: dict) -> dict:
        """Migrate the stale http_port=443 default (Issue #62 pattern).

        http_port defaulted to 443 for a long stretch and got baked into
        every rendered/saved gateway.json, while meshtasticd's web API
        lives on 9443 — so the primary stateless TX path was dead
        (connection refused, circuit breaker permanently flapping) and
        every send rode the legacy session fallback (#17 contention
        class). 443 was never a valid value for us (Issue #58 treats a
        :443 webserver override as forbidden), so it is safe to treat
        a saved 443 as the stale default rather than operator intent.
        """
        if meshtastic_data.get('http_port') == 443:
            meshtastic_data = dict(meshtastic_data)
            meshtastic_data['http_port'] = 9443
            logger.info(
                "Migrated stale http_port 443 -> 9443 "
                "(saved default predating the 9443 fix)")
        return meshtastic_data

    @classmethod
    def load(cls) -> 'GatewayConfig':
        """Load configuration from file"""
        config_path = cls.get_config_path()

        if not config_path.exists():
            logger.info(f"No gateway config found, using defaults")
            return cls()

        try:
            with open(config_path, 'r') as f:
                data = json.load(f)

            # Handle RNSOverMeshtasticConfig separately (has method, can't use **)
            rns_transport_data = data.get('rns_transport', {})
            rns_transport = RNSOverMeshtasticConfig(
                enabled=rns_transport_data.get('enabled', False),
                connection_type=rns_transport_data.get('connection_type', 'tcp'),
                device_path=rns_transport_data.get('device_path', 'localhost:4403'),
                data_speed=rns_transport_data.get('data_speed', 8),
                hop_limit=rns_transport_data.get('hop_limit', 3),
                fragment_timeout_sec=rns_transport_data.get('fragment_timeout_sec', 30),
                max_pending_fragments=rns_transport_data.get('max_pending_fragments', 100),
                enable_stats=rns_transport_data.get('enable_stats', True),
                stats_interval_sec=rns_transport_data.get('stats_interval_sec', 60),
                packet_loss_threshold=rns_transport_data.get('packet_loss_threshold', 0.1),
                latency_threshold_ms=rns_transport_data.get('latency_threshold_ms', 5000),
            )

            # Handle MeshtasticBridgeConfig (has nested MeshtasticConfig objects)
            mesh_bridge_data = data.get('mesh_bridge', {})
            mesh_bridge = MeshtasticBridgeConfig(
                enabled=mesh_bridge_data.get('enabled', False),
                primary=MeshtasticConfig(**cls._migrate_stale_http_port(mesh_bridge_data.get('primary', {}))) if mesh_bridge_data.get('primary') else MeshtasticConfig(port=4403, preset="LONG_FAST", name="longfast"),
                secondary=MeshtasticConfig(**cls._migrate_stale_http_port(mesh_bridge_data.get('secondary', {}))) if mesh_bridge_data.get('secondary') else MeshtasticConfig(port=4404, preset="SHORT_TURBO", name="shortturbo"),
                direction=mesh_bridge_data.get('direction', 'bidirectional'),
                channels=mesh_bridge_data.get('channels', []) or [],
                message_filter=mesh_bridge_data.get('message_filter', ''),
                exclude_filter=mesh_bridge_data.get('exclude_filter', ''),
                dedup_window_sec=mesh_bridge_data.get('dedup_window_sec', 60),
                add_prefix=mesh_bridge_data.get('add_prefix', True),
                prefix_format=mesh_bridge_data.get('prefix_format', '[Mesh:{source_preset}] '),
            )

            # Handle MQTTBridgeConfig
            mqtt_bridge_data = data.get('mqtt_bridge', {})
            mqtt_bridge = MQTTBridgeConfig(**mqtt_bridge_data) if mqtt_bridge_data else MQTTBridgeConfig()

            # Handle MeshCoreConfig
            meshcore_data = data.get('meshcore', {})
            meshcore = MeshCoreConfig(**meshcore_data) if meshcore_data else MeshCoreConfig()

            # Handle MeshtasticBroadcastConfig (channels list — explicit copy
            # avoids shared-default mutability traps when multiple configs load
            # in the same process during tests).
            mb_data = data.get('meshtastic_broadcast', {}) or {}
            meshtastic_broadcast = MeshtasticBroadcastConfig(
                enabled=mb_data.get('enabled', False),
                channels=list(mb_data.get('channels', [0])),
                display_name=mb_data.get(
                    'display_name', 'MeshForge Meshtastic Broadcast'
                ),
                announce_interval_sec=mb_data.get('announce_interval_sec', 600),
                prefix_format=mb_data.get(
                    'prefix_format',
                    '[meshtastic ch{channel}:{sender}] {text}',
                ),
                autosubscribe=mb_data.get('autosubscribe', False),
                identity_file=mb_data.get('identity_file', ''),
                db_file=mb_data.get('db_file', ''),
                ack_required=mb_data.get('ack_required', False),
            )

            # Reconstruct nested dataclasses
            config = cls(
                enabled=data.get('enabled', False),
                auto_start=data.get('auto_start', False),
                bridge_mode=data.get('bridge_mode', 'mqtt_bridge'),
                rns_bridge_enabled=data.get('rns_bridge_enabled', True),
                meshtastic=MeshtasticConfig(
                    **cls._migrate_stale_http_port(data.get('meshtastic', {}))),
                rns=RNSConfig(**data.get('rns', {})),
                mqtt_bridge=mqtt_bridge,
                rns_transport=rns_transport,
                mesh_bridge=mesh_bridge,
                meshcore=meshcore,
                routing_rules=[RoutingRule(**r) for r in data.get('routing_rules', [])],
                default_route=data.get('default_route', 'bidirectional'),
                telemetry=TelemetryConfig(**data.get('telemetry', {})),
                meshtastic_broadcast=meshtastic_broadcast,
                log_level=data.get('log_level', 'INFO'),
                log_messages=data.get('log_messages', True),
                ai_diagnostics_enabled=data.get('ai_diagnostics_enabled', False),
                snr_analysis=data.get('snr_analysis', True),
                anomaly_detection=data.get('anomaly_detection', False),
            )

            logger.info(f"Loaded gateway config from {config_path}")
            return config

        except Exception as e:
            logger.error(f"Failed to load gateway config: {e}")
            return cls()

    def save(self) -> bool:
        """Save configuration to file"""
        config_path = self.get_config_path()

        try:
            # Convert RNSOverMeshtasticConfig manually (has method that shouldn't be serialized)
            rns_transport_data = {
                'enabled': self.rns_transport.enabled,
                'connection_type': self.rns_transport.connection_type,
                'device_path': self.rns_transport.device_path,
                'data_speed': self.rns_transport.data_speed,
                'hop_limit': self.rns_transport.hop_limit,
                'fragment_timeout_sec': self.rns_transport.fragment_timeout_sec,
                'max_pending_fragments': self.rns_transport.max_pending_fragments,
                'enable_stats': self.rns_transport.enable_stats,
                'stats_interval_sec': self.rns_transport.stats_interval_sec,
                'packet_loss_threshold': self.rns_transport.packet_loss_threshold,
                'latency_threshold_ms': self.rns_transport.latency_threshold_ms,
            }

            # Convert MeshtasticBridgeConfig manually (has nested dataclasses)
            mesh_bridge_data = {
                'enabled': self.mesh_bridge.enabled,
                'primary': asdict(self.mesh_bridge.primary),
                'secondary': asdict(self.mesh_bridge.secondary),
                'direction': self.mesh_bridge.direction,
                'channels': self.mesh_bridge.channels,
                'message_filter': self.mesh_bridge.message_filter,
                'exclude_filter': self.mesh_bridge.exclude_filter,
                'dedup_window_sec': self.mesh_bridge.dedup_window_sec,
                'add_prefix': self.mesh_bridge.add_prefix,
                'prefix_format': self.mesh_bridge.prefix_format,
            }

            # Convert to dict with nested dataclasses
            data = {
                'enabled': self.enabled,
                'auto_start': self.auto_start,
                'bridge_mode': self.bridge_mode,
                'rns_bridge_enabled': self.rns_bridge_enabled,
                'meshtastic': asdict(self.meshtastic),
                'rns': asdict(self.rns),
                'mqtt_bridge': asdict(self.mqtt_bridge),
                'rns_transport': rns_transport_data,
                'mesh_bridge': mesh_bridge_data,
                'meshcore': asdict(self.meshcore),
                'routing_rules': [asdict(r) for r in self.routing_rules],
                'default_route': self.default_route,
                'telemetry': asdict(self.telemetry),
                'meshtastic_broadcast': asdict(self.meshtastic_broadcast),
                'log_level': self.log_level,
                'log_messages': self.log_messages,
                'ai_diagnostics_enabled': self.ai_diagnostics_enabled,
                'snr_analysis': self.snr_analysis,
                'anomaly_detection': self.anomaly_detection,
            }

            with open(config_path, 'w') as f:
                json.dump(data, f, indent=2)

            logger.info(f"Saved gateway config to {config_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to save gateway config: {e}")
            return False

    def add_routing_rule(self, rule: RoutingRule):
        """Add a routing rule"""
        self.routing_rules.append(rule)
        self.routing_rules.sort(key=lambda r: r.priority, reverse=True)

    def remove_routing_rule(self, name: str):
        """Remove a routing rule by name"""
        self.routing_rules = [r for r in self.routing_rules if r.name != name]

    def get_default_rules(self) -> List[RoutingRule]:
        """Get default routing rules"""
        return [
            RoutingRule(
                name="broadcast_mesh_to_rns",
                direction="mesh_to_rns",
                source_filter="",
                dest_filter="^!ffffffff$",  # Broadcast address
                message_filter="",
                priority=10,
            ),
            RoutingRule(
                name="broadcast_rns_to_mesh",
                direction="rns_to_mesh",
                source_filter="",
                dest_filter="",
                message_filter="",
                priority=10,
            ),
            RoutingRule(
                name="direct_messages",
                direction="bidirectional",
                source_filter="",
                dest_filter="^!(?!ffffffff)",  # Non-broadcast
                message_filter="",
                priority=5,
            ),
        ]

    def validate(self) -> Tuple[bool, List[ConfigValidationError]]:
        """
        Validate the entire configuration.

        Returns:
            Tuple of (is_valid, list_of_errors)
            is_valid is False only if there are severity="error" issues
        """
        errors: List[ConfigValidationError] = []

        # Validate bridge mode
        err = validate_bridge_mode(self.bridge_mode, "bridge_mode")
        if err:
            errors.append(err)

        # Validate meshtastic config
        err = validate_port(self.meshtastic.port, "meshtastic.port")
        if err:
            errors.append(err)

        err = validate_channel(self.meshtastic.channel, "meshtastic.channel")
        if err:
            errors.append(err)

        # Validate meshtastic MQTT sub-config when enabled
        if self.meshtastic.use_mqtt:
            err = validate_port(self.meshtastic.mqtt_port, "meshtastic.mqtt_port")
            if err:
                errors.append(err)

        # Validate log level
        err = validate_log_level(self.log_level, "log_level")
        if err:
            errors.append(err)

        # Validate telemetry config
        err = validate_position_precision(
            self.telemetry.position_precision, "telemetry.position_precision")
        if err:
            errors.append(err)

        err = validate_update_interval(
            self.telemetry.update_interval, "telemetry.update_interval")
        if err:
            errors.append(err)

        # Validate RNS transport config
        err = validate_data_speed(self.rns_transport.data_speed, "rns_transport.data_speed")
        if err:
            errors.append(err)

        err = validate_hop_limit(self.rns_transport.hop_limit, "rns_transport.hop_limit")
        if err:
            errors.append(err)

        # Check speed/hop combination
        err = validate_speed_hop_combination(
            self.rns_transport.data_speed,
            self.rns_transport.hop_limit
        )
        if err:
            errors.append(err)

        # Validate mesh bridge config. Composable-bridges model: mesh_bridge
        # can be enabled alongside another bridge_mode (e.g. mqtt_bridge +
        # rns_bridge + mesh_bridge on a dual-radio gateway), so gate on
        # enabled OR the legacy mode selector.
        if self.bridge_mode == "mesh_bridge" or self.mesh_bridge.enabled:
            err = validate_port(self.mesh_bridge.primary.port, "mesh_bridge.primary.port")
            if err:
                errors.append(err)

            err = validate_port(self.mesh_bridge.secondary.port, "mesh_bridge.secondary.port")
            if err:
                errors.append(err)

            # Check for same port (would conflict)
            if self.mesh_bridge.primary.port == self.mesh_bridge.secondary.port:
                errors.append(ConfigValidationError(
                    "mesh_bridge",
                    f"Primary and secondary cannot use same port ({self.mesh_bridge.primary.port})"
                ))

            err = validate_direction(self.mesh_bridge.direction, "mesh_bridge.direction")
            if err:
                errors.append(err)

            err = validate_dedup_window(self.mesh_bridge.dedup_window_sec, "mesh_bridge.dedup_window_sec")
            if err:
                errors.append(err)

            # Validate message filters
            err = validate_regex(self.mesh_bridge.message_filter, "mesh_bridge.message_filter")
            if err:
                errors.append(err)

            err = validate_regex(self.mesh_bridge.exclude_filter, "mesh_bridge.exclude_filter")
            if err:
                errors.append(err)

            # Validate channel allow-list
            errors.extend(validate_channel_list(
                self.mesh_bridge.channels, "mesh_bridge.channels"))

            # Validate injection mode on each leg
            for _leg_name, _leg in (("primary", self.mesh_bridge.primary),
                                    ("secondary", self.mesh_bridge.secondary)):
                mode = (_leg.injection_mode or "toradio").lower()
                if mode not in ("toradio", "downlink"):
                    errors.append(ConfigValidationError(
                        f"mesh_bridge.{_leg_name}.injection_mode",
                        f"Must be 'toradio' or 'downlink', got {_leg.injection_mode!r}"))
                elif mode == "downlink" and not _leg.downlink_psk:
                    errors.append(ConfigValidationError(
                        f"mesh_bridge.{_leg_name}.injection_mode",
                        "injection_mode='downlink' requires downlink_psk (channel "
                        "PSK, base64). Set it or use 'toradio'.",
                        severity="warning"))

            # Echo-loop tag check: an untagged prefix_format means other
            # gateways (and our own MQTT->RNS handler) won't recognize the
            # forward as bridged content and will re-bridge it — the
            # cross-gateway amplification loop. Warning, not error: a truly
            # standalone two-radio bridge with no RNS/LXMF anywhere has no
            # loop path.
            if self.mesh_bridge.add_prefix:
                try:
                    from gateway.base_handler import BRIDGE_TAG_PREFIXES
                    rendered = self.mesh_bridge.prefix_format.format(
                        source_preset="X", source_id="0000")
                    if not rendered.lstrip().startswith(BRIDGE_TAG_PREFIXES):
                        errors.append(ConfigValidationError(
                            "mesh_bridge.prefix_format",
                            f"Prefix {self.mesh_bridge.prefix_format!r} is not a "
                            "recognized bridge tag — other gateways will re-bridge "
                            "forwarded messages (echo-amplification risk). Use a "
                            "'[Mesh:'-prefixed format, e.g. '[Mesh:{source_preset}] '.",
                            severity="warning"))
                except (KeyError, IndexError, ValueError) as e:
                    errors.append(ConfigValidationError(
                        "mesh_bridge.prefix_format",
                        f"Invalid prefix template: {e}"))

        # Mode-specific: mqtt_bridge
        if self.bridge_mode == "mqtt_bridge":
            err = validate_port(self.mqtt_bridge.port, "mqtt_bridge.port")
            if err:
                errors.append(err)

            err = validate_hostname_config(
                self.mqtt_bridge.broker, "mqtt_bridge.broker")
            if err:
                errors.append(err)

        # Mode-specific: meshcore_bridge or tri_bridge
        if self.bridge_mode in ("meshcore_bridge", "tri_bridge"):
            err = validate_meshcore_connection(
                self.meshcore.connection_type, "meshcore.connection_type")
            if err:
                errors.append(err)

            if self.meshcore.connection_type == "tcp":
                err = validate_port(self.meshcore.tcp_port, "meshcore.tcp_port")
                if err:
                    errors.append(err)

            if self.meshcore.connection_type == "serial":
                err = validate_baud_rate(
                    self.meshcore.baud_rate, "meshcore.baud_rate")
                if err:
                    errors.append(err)

        # Validate routing rules
        for i, rule in enumerate(self.routing_rules):
            prefix = f"routing_rules[{i}]"

            err = validate_direction(rule.direction, f"{prefix}.direction")
            if err:
                errors.append(err)

            err = validate_regex(rule.source_filter, f"{prefix}.source_filter")
            if err:
                errors.append(err)

            err = validate_regex(rule.dest_filter, f"{prefix}.dest_filter")
            if err:
                errors.append(err)

            err = validate_regex(rule.message_filter, f"{prefix}.message_filter")
            if err:
                errors.append(err)

        # Check for duplicate rule names
        rule_names = [r.name for r in self.routing_rules]
        seen = set()
        for name in rule_names:
            if name in seen:
                errors.append(ConfigValidationError(
                    "routing_rules",
                    f"Duplicate rule name: '{name}'"
                ))
            seen.add(name)

        # Config drift detection: check if gateway's RNS config path
        # matches what rnsd is actually using
        if _HAS_CONFIG_DRIFT:
            try:
                drift_errors = _validate_gateway_rns_config(self)
                errors.extend(drift_errors)
            except Exception as e:
                logger.debug("Config drift check failed: %s", e)

        # Determine if valid (only errors count, not warnings/info)
        is_valid = not any(e.severity == "error" for e in errors)

        return is_valid, errors

    def validate_and_log(self) -> bool:
        """Validate config and log any issues. Returns True if valid."""
        is_valid, errors = self.validate()

        for err in errors:
            if err.severity == "error":
                logger.error(str(err))
            elif err.severity == "warning":
                logger.warning(str(err))
            else:
                logger.info(str(err))

        return is_valid

    # =========================================================================
    # CONFIGURATION TEMPLATES
    # Pre-configured setups for common use cases
    # =========================================================================

    @classmethod
    def template_mqtt_bridge(cls, broker: str = "localhost",
                              region: str = "US",
                              channel: str = "LongFast") -> 'GatewayConfig':
        """
        MQTT-based bridge between Meshtastic and RNS (RECOMMENDED).

        Zero interference with meshtasticd web client. Uses MQTT for
        receiving mesh traffic and meshtastic CLI for sending.

        Use case: Bridge Meshtastic <-> RNS without blocking web client
        Requirements:
            - mosquitto running on localhost:1883
            - meshtasticd with mqtt.enabled=true, mqtt.json_enabled=true
            - rnsd running (user systemd service)

        Args:
            broker: MQTT broker address
            region: LoRa region code (US, EU_868, etc.)
            channel: Meshtastic channel name
        """
        config = cls()
        config.enabled = True
        config.bridge_mode = "mqtt_bridge"
        config.mqtt_bridge.broker = broker
        config.mqtt_bridge.region = region
        config.mqtt_bridge.channel = channel
        config.mqtt_bridge.json_enabled = True
        config.default_route = "bidirectional"
        config.routing_rules = config.get_default_rules()
        return config

    @classmethod
    def template_basic_bridge(cls) -> 'GatewayConfig':
        """
        Basic message bridge between Meshtastic and RNS (LEGACY).

        WARNING: Uses TCP connection that blocks meshtasticd web client.
        Prefer template_mqtt_bridge() instead.

        Use case: Simple bidirectional message forwarding
        Requirements: meshtasticd running on localhost:4403, rnsd running
        """
        config = cls()
        config.enabled = True
        config.bridge_mode = "message_bridge"
        config.meshtastic.host = "localhost"
        config.meshtastic.port = 4403
        config.default_route = "bidirectional"
        config.routing_rules = config.get_default_rules()
        return config

    @classmethod
    def template_rns_over_mesh(cls, speed: int = 8, hop_limit: int = 3) -> 'GatewayConfig':
        """
        RNS transport over Meshtastic (RNS uses LoRa as network layer).

        Use case: Run RNS apps (NomadNet, Sideband) over LoRa mesh
        Requirements: meshtasticd on localhost:4403 with radio

        Args:
            speed: LoRa speed preset (0-8, higher=faster/shorter range)
            hop_limit: Mesh hop limit (1-7)
        """
        config = cls()
        config.enabled = True
        config.bridge_mode = "rns_transport"
        config.rns_transport.enabled = True
        config.rns_transport.connection_type = "tcp"
        config.rns_transport.device_path = "localhost:4403"
        config.rns_transport.data_speed = speed
        config.rns_transport.hop_limit = hop_limit
        return config

    @classmethod
    def template_dual_preset_bridge(cls,
                                     primary_port: int = 4403,
                                     secondary_port: int = 4404,
                                     primary_preset: str = "LONG_FAST",
                                     secondary_preset: str = "SHORT_TURBO") -> 'GatewayConfig':
        """
        Bridge two Meshtastic networks with different LoRa presets.

        Use case: Connect a long-range mesh to a high-speed local mesh
        Requirements: Two meshtasticd instances on different ports

        Args:
            primary_port: Port for primary (usually long-range) meshtasticd
            secondary_port: Port for secondary (usually fast) meshtasticd
            primary_preset: LoRa preset for primary network
            secondary_preset: LoRa preset for secondary network
        """
        config = cls()
        config.enabled = True
        config.bridge_mode = "mesh_bridge"
        config.mesh_bridge.enabled = True
        config.mesh_bridge.primary = MeshtasticConfig(
            host="localhost",
            port=primary_port,
            preset=primary_preset,
            name="longrange"
        )
        config.mesh_bridge.secondary = MeshtasticConfig(
            host="localhost",
            port=secondary_port,
            preset=secondary_preset,
            name="highspeed"
        )
        config.mesh_bridge.direction = "bidirectional"
        config.mesh_bridge.dedup_window_sec = 60
        config.mesh_bridge.add_prefix = True
        return config

    @classmethod
    def template_mqtt_monitor(cls, mqtt_topic: str = "msh/+/json/+") -> 'GatewayConfig':
        """
        Meshtastic MQTT monitoring (receive-only, no radio needed).

        Use case: Monitor a Meshtastic network via public MQTT
        Requirements: Network connection to MQTT broker

        Args:
            mqtt_topic: MQTT topic pattern to subscribe
        """
        config = cls()
        config.enabled = True
        config.bridge_mode = "message_bridge"
        config.meshtastic.use_mqtt = True
        config.meshtastic.mqtt_topic = mqtt_topic
        config.default_route = "mesh_to_rns"  # Receive only
        return config

    @classmethod
    def template_relay_node(cls) -> 'GatewayConfig':
        """
        Relay node configuration (optimized for forwarding).

        Use case: Dedicated relay/repeater node
        Requirements: meshtasticd with radio
        """
        config = cls()
        config.enabled = True
        config.bridge_mode = "message_bridge"
        config.meshtastic.host = "localhost"
        config.meshtastic.port = 4403
        config.default_route = "bidirectional"
        config.telemetry.share_position = True
        config.telemetry.share_battery = True
        config.telemetry.update_interval = 300  # Less frequent for relay
        config.log_messages = True
        config.ai_diagnostics_enabled = True
        config.snr_analysis = True
        return config

    @classmethod
    def get_available_templates(cls) -> Dict[str, str]:
        """Get list of available configuration templates with descriptions."""
        return {
            "mqtt_bridge": "MQTT-based Meshtastic <-> RNS bridge (RECOMMENDED, zero interference)",
            "basic_bridge": "TCP-based Meshtastic <-> RNS bridge (legacy, blocks web client)",
            "rns_over_mesh": "Run RNS apps over LoRa mesh (transport mode)",
            "dual_preset_bridge": "Bridge two Meshtastic networks with different presets",
            "mqtt_monitor": "Monitor Meshtastic network via MQTT (no radio needed)",
            "relay_node": "Dedicated relay/repeater node configuration",
        }

    @classmethod
    def from_template(cls, template_name: str, **kwargs) -> Optional['GatewayConfig']:
        """
        Create a configuration from a template name.

        Args:
            template_name: One of the template names from get_available_templates()
            **kwargs: Optional overrides for template parameters

        Returns:
            GatewayConfig or None if template not found
        """
        templates = {
            "mqtt_bridge": cls.template_mqtt_bridge,
            "basic_bridge": cls.template_basic_bridge,
            "rns_over_mesh": cls.template_rns_over_mesh,
            "dual_preset_bridge": cls.template_dual_preset_bridge,
            "mqtt_monitor": cls.template_mqtt_monitor,
            "relay_node": cls.template_relay_node,
        }

        factory = templates.get(template_name)
        if factory:
            try:
                return factory(**kwargs)
            except TypeError:
                # kwargs not supported by this template
                return factory()
        return None
