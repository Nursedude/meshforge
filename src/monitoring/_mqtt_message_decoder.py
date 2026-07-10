"""MQTT message decoding and node update methods for MQTTNodelessSubscriber.

Handles parsing of JSON-formatted Meshtastic messages (nodeinfo, position,
telemetry, text) and encrypted message tracking. Also handles relay node
discovery and merging (Meshtastic 2.6+).

Extracted from mqtt_subscriber.py for file size compliance (CLAUDE.md #6).

Expects the following attributes on the host class:
- self._nodes: Dict[str, MQTTNode]
- self._nodes_lock: threading.Lock
- self._stats: Dict[str, Any]
- self._stats_lock: threading.Lock
- self._messages: deque of MQTTMessage
- self._messages_lock: threading.Lock
- self._node_callbacks: List[Callable]
- self._message_callbacks: List[Callable]
- self._packet_callbacks: List[Callable]  (raw (topic, data) observers)
"""

import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional

from monitoring._mqtt_types import (
    MQTTMessage,
    MQTTNode,
    VALID_LAT_RANGE,
    VALID_LON_RANGE,
    VALID_SNR_RANGE,
    VALID_RSSI_RANGE,
    node_num_to_id,
)

logger = logging.getLogger(__name__)

# Hostile-input bounds (2026-07-09 frontier review Pri-3). Broker peers are
# untrusted — in nodeless mode this parses mqtt.meshtastic.org, where any
# internet stranger authors the JSON. Canonical node keys are 9 chars
# ("!%08x"); partial relay keys 7 ("!????xx"). Anything much longer is not a
# node id, and unbounded attacker strings otherwise become dict keys and
# cached/persisted labels (64 KB payload cap × 10k node cap = memory, disk
# and downstream-consumer exhaustion).
_MAX_NODE_KEY_LEN = 16
_MAX_LONG_NAME = 64
_MAX_SHORT_NAME = 16
_MAX_HW_MODEL = 48
_MAX_ROLE = 32
_MAX_TEXT_LEN = 2048  # LoRa text tops out ~237 bytes; generous ceiling
_MAX_MSG_ID_LEN = 64


def _clean_label(value: Any, max_len: int) -> Optional[str]:
    """Coerce an untrusted label field to a bounded string.

    Accepts str (clamped) and the numeric enum forms live firmwares emit
    for hardware/role; rejects containers and None so junk can never
    replace a previously-valid label (error must not read as data)."""
    if isinstance(value, str):
        return value[:max_len]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)[:max_len]
    return None


class MQTTMessageDecoderMixin:
    """Mixin providing MQTT message decoding and node update methods."""

    def _handle_json_message(self, topic: str, payload: bytes) -> None:
        """Handle JSON-formatted Meshtastic message."""
        try:
            data = json.loads(payload.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        # Canonicalize wire identities ONCE at entry — live uplinks carry
        # from/to as NUMBERS, so every downstream handler and observer
        # must see one vocabulary ('!hex'). Fixing only _ensure_node left
        # _handle_text_message shipping numeric from_id to the messages
        # DB/WS/TUI feed, and numeric to=4294967295 never matched the
        # '!ffffffff' broadcast check. Pre-formed '!…' strings pass
        # untouched; non-numeric junk stays as-is (parse witnesses fire).
        for _k in ("from", "to"):
            _v = data.get(_k)
            if _v is not None and not (isinstance(_v, str)
                                       and _v.startswith("!")):
                _canonical = node_num_to_id(_v)
                if _canonical is not None:
                    data[_k] = _canonical

        # Raw packet observers first (any type, before type handling) —
        # snapshot for thread-safe iteration, never let one break decode.
        for cb in list(getattr(self, '_packet_callbacks', [])):
            try:
                cb(topic, data)
            except Exception as e:
                logger.debug(f"Packet callback error: {e}")

        # Extract node info from sender
        sender = data.get("sender") or data.get("from")
        if sender:
            self._update_node_from_json(sender, data)

        # Handle specific message types
        msg_type = data.get("type", "")

        if msg_type == "nodeinfo":
            self._handle_nodeinfo(data)
        elif msg_type == "position":
            self._handle_position(data)
        elif msg_type == "telemetry":
            self._handle_telemetry(data)
        elif msg_type == "text":
            self._handle_text_message(data)

    def _handle_encrypted_message(self, topic: str, payload: bytes) -> None:
        """Handle encrypted message - track node existence and optionally decrypt."""
        # Topic format: msh/US/2/e/LongFast/!abcd1234
        parts = topic.split("/")
        if len(parts) >= 6:
            node_id = parts[-1]
            if node_id.startswith("!"):
                self._ensure_node(node_id)

        # Attempt decryption if crypto bridge is available
        try:
            from utils.mqtt_decryptor import get_decryptor
            decryptor = get_decryptor()
            if decryptor.is_available:
                key = getattr(self, '_config', {}).get("key", "AQ==") if hasattr(self, '_config') else "AQ=="
                result = decryptor.decrypt_packet(payload, key)
                if result and result.get("text"):
                    self._process_decrypted_text(result, parts)
        except Exception:
            pass  # Crypto not available — existing behavior unchanged

    def _process_decrypted_text(self, result: dict, topic_parts: list) -> None:
        """Process a successfully decrypted text message."""
        try:
            sender_num = result.get("sender", 0)
            sender_id = (node_num_to_id(sender_num) or "") if sender_num \
                else ""
            text = result.get("text", "")
            if not text or not sender_id:
                return

            self._ensure_node(sender_id)

            msg = MQTTMessage(
                message_id=str(result.get("packet_id", "")),
                from_id=sender_id,
                to_id="",
                text=text,
                channel=result.get("channel", 0) if isinstance(result.get("channel"), int) else 0,
            )
            with self._messages_lock:
                self._messages.append(msg)
            with self._stats_lock:
                self._stats["messages_received"] = self._stats.get("messages_received", 0) + 1
                self._stats["encrypted_decrypted"] = self._stats.get("encrypted_decrypted", 0) + 1

            # Notify message callbacks
            for cb in getattr(self, '_message_callbacks', []):
                try:
                    cb(msg)
                except Exception:
                    pass
        except Exception:
            pass  # Decrypted text processing is best-effort

    def _ensure_node(self, node_id) -> Optional[MQTTNode]:
        """Ensure a node exists in our tracking. Returns None (with a
        ``nodes_rejected`` stats witness) for keys that survive
        canonicalization but are not plausible node ids — callers skip.

        Accepts the canonical '!hex' string OR a raw numeric node number
        (int or numeric string): live meshtasticd json uplinks carry
        ``from`` as a NUMBER. History note (corrected 2026-07-05): before
        canonicalization, only the FIRST packet per numeric sender died on
        the swallowed AttributeError — the dict insert preceded the raise,
        so packets 2+ landed position/telemetry on int-KEYED ghost nodes
        that persisted into mqtt_nodes.json and the node_history
        directory. No migration re-keys those rows; they age out via
        retention, and a long-running subscriber keeps writing them until
        its service restarts on the fixed code (deploy-restart gap, #79
        class). _handle_json_message now canonicalizes at entry; this
        guard stays as defense-in-depth for other callers.
        """
        if not (isinstance(node_id, str) and node_id.startswith("!")):
            _canonical = node_num_to_id(node_id)
            if _canonical is not None:
                node_id = _canonical
        # Hostile-key gate: after canonicalization the only acceptable keys
        # are short "!"-prefixed strings. Arbitrary attacker strings (up to
        # the 64 KB payload cap) must never become node-dict keys — the
        # MAX_NODES prune bounds COUNT, not per-key SIZE. Rejection leaves
        # a stats witness, never a silent drop.
        if not (isinstance(node_id, str) and node_id.startswith("!")
                and 0 < len(node_id) <= _MAX_NODE_KEY_LEN):
            with self._stats_lock:
                self._stats["nodes_rejected"] = \
                    self._stats.get("nodes_rejected", 0) + 1
            return None
        with self._nodes_lock:
            if node_id not in self._nodes:
                self._nodes[node_id] = MQTTNode(node_id=node_id)
                with self._stats_lock:
                    self._stats["nodes_discovered"] += 1

                # Check if this node matches a previously discovered relay node
                if node_id.startswith("!") and not node_id.startswith("!????"):
                    self._try_merge_relay_node(node_id)
            else:
                self._nodes[node_id].last_seen = datetime.now()
            return self._nodes[node_id]

    def _try_merge_relay_node(self, full_node_id: str) -> None:
        """
        Check if this full node ID matches a partial relay node and merge them.
        Called under _nodes_lock.
        """
        try:
            full_num = int(full_node_id[1:], 16)
            last_byte = full_num & 0xFF
            partial_id = f"!????{last_byte:02x}"

            if partial_id in self._nodes:
                # Found matching partial relay node - merge
                partial_node = self._nodes[partial_id]
                full_node = self._nodes[full_node_id]

                # Transfer relay discovery flag
                full_node.discovered_via_relay = True

                # Remove partial entry
                del self._nodes[partial_id]

                with self._stats_lock:
                    self._stats["relay_nodes_merged"] += 1

                logger.info(
                    f"Merged relay node {partial_id} -> {full_node_id} "
                    f"({partial_node.long_name} -> {full_node.long_name or 'unknown'})"
                )
        except (ValueError, TypeError, KeyError) as e:
            logger.debug(f"Relay node merge failed: {e}")

    def _discover_relay_node(self, relay_byte: int, data: Dict) -> Optional[MQTTNode]:
        """
        Discover a relay node from its last byte (Meshtastic 2.6+ relay_node field).

        The relay_node field only contains the last byte of the node ID. We try to:
        1. Match it against existing nodes
        2. If no match, create a partial entry that can be filled in later

        This allows us to discover nodes that relay packets but never send their
        own telemetry/position, which is common for managed flood routing.
        """
        if relay_byte <= 0 or relay_byte > 255:
            return None

        # Try to find an existing node matching this last byte
        with self._nodes_lock:
            for node_id, node in self._nodes.items():
                if node_id.startswith("!"):
                    try:
                        # Extract last byte of node ID
                        node_num = int(node_id[1:], 16)
                        if (node_num & 0xFF) == relay_byte:
                            # Found matching node - update last_seen
                            node.last_seen = datetime.now()
                            logger.debug(f"Relay activity from known node: {node_id}")
                            return node
                    except (ValueError, TypeError):
                        continue

            # No match found - create a placeholder node with partial ID
            # Format: !????xxxx where xxxx is the hex of the last byte
            partial_id = f"!????{relay_byte:02x}"

            if partial_id not in self._nodes:
                # Create new node discovered via relay
                relay_node = MQTTNode(
                    node_id=partial_id,
                    discovered_via_relay=True,
                    long_name=f"Relay-{relay_byte:02x}",
                    short_name=f"R{relay_byte:02x}",
                )
                self._nodes[partial_id] = relay_node
                with self._stats_lock:
                    self._stats["nodes_discovered"] += 1
                    self._stats["nodes_discovered_via_relay"] += 1
                logger.info(f"Discovered relay node via packet routing: {partial_id}")
                return relay_node
            else:
                # Update existing partial node
                self._nodes[partial_id].last_seen = datetime.now()
                return self._nodes[partial_id]

    def _match_relay_to_full_node(self, partial_id: str, full_node_id: str) -> bool:
        """
        Match a partial relay node ID to a full node ID when we learn the full ID.

        When a node that was discovered via relay sends its own telemetry,
        we can merge the partial entry with the full node info.
        """
        if not partial_id.startswith("!????"):
            return False

        try:
            relay_byte = int(partial_id[-2:], 16)
            full_num = int(full_node_id[1:], 16)
            if (full_num & 0xFF) == relay_byte:
                # Match! Merge the nodes
                with self._nodes_lock:
                    if partial_id in self._nodes and full_node_id in self._nodes:
                        # Copy relay discovery flag to full node
                        self._nodes[full_node_id].discovered_via_relay = True
                        # Remove partial entry
                        del self._nodes[partial_id]
                        logger.info(f"Merged relay node {partial_id} -> {full_node_id}")
                        return True
        except (ValueError, TypeError) as e:
            logger.debug(f"Relay node match failed for {partial_id}: {e}")
        return False

    def _safe_float(self, value: Any, min_val: float, max_val: float) -> Optional[float]:
        """Safely extract and validate a float value within range."""
        if value is None:
            return None
        try:
            f = float(value)
            if min_val <= f <= max_val:
                return f
        except (TypeError, ValueError):
            pass
        return None

    def _safe_int(self, value: Any, min_val: int, max_val: int) -> Optional[int]:
        """Safely extract and validate an int value within range."""
        if value is None:
            return None
        try:
            i = int(value)
            if min_val <= i <= max_val:
                return i
        except (TypeError, ValueError):
            pass
        return None

    def _update_node_from_json(self, node_id: str, data: Dict) -> None:
        """Update node info from JSON message with input validation."""
        node = self._ensure_node(node_id)
        if node is None:
            return

        # Validate and update fields
        if "snr" in data:
            snr = self._safe_float(data["snr"], *VALID_SNR_RANGE)
            if snr is not None:
                node.snr = snr
        if "rssi" in data:
            rssi = self._safe_int(data["rssi"], *VALID_RSSI_RANGE)
            if rssi is not None:
                node.rssi = rssi
        if "hop_start" in data:
            hop = self._safe_int(data["hop_start"], 0, 15)
            if hop is not None:
                node.hop_start = hop
        if "hops_away" in data:
            hops = self._safe_int(data["hops_away"], 0, 15)
            if hops is not None:
                node.hops_away = hops

        # Extract relay node info (Meshtastic 2.6+)
        # relay_node is the last byte of the node ID that relayed this packet
        if "relay_node" in data or "relayNode" in data:
            relay = self._safe_int(
                data.get("relay_node") or data.get("relayNode"), 0, 255
            )
            if relay is not None and relay > 0:
                node.relay_node = relay
                # Discover the relay node if we haven't seen it
                self._discover_relay_node(relay, data)

        # Extract next_hop info (Meshtastic 2.6+)
        if "next_hop" in data or "nextHop" in data:
            next_hop = self._safe_int(
                data.get("next_hop") or data.get("nextHop"), 0, 255
            )
            if next_hop is not None and next_hop > 0:
                node.next_hop = next_hop

        # Notify callbacks (snapshot for thread-safe iteration)
        for callback in list(self._node_callbacks):
            try:
                callback(node)
            except Exception as e:
                logger.debug(f"Node callback error: {e}")

    def _handle_nodeinfo(self, data: Dict) -> None:
        """Handle nodeinfo message."""
        payload = data.get("payload", {})
        node_id = payload.get("id") or data.get("from")
        if not node_id:
            return

        node = self._ensure_node(node_id)
        if node is None:
            return
        # Bounded, type-checked label updates: attacker-authored containers
        # or multi-KB strings must neither replace a valid label nor bloat
        # the node cache / mqtt_nodes.json / downstream map consumers.
        for attr, key, cap in (
            ("long_name", "longname", _MAX_LONG_NAME),
            ("short_name", "shortname", _MAX_SHORT_NAME),
            ("hardware_model", "hardware", _MAX_HW_MODEL),
            ("role", "role", _MAX_ROLE),
        ):
            if key in payload:
                cleaned = _clean_label(payload.get(key), cap)
                if cleaned is not None:
                    setattr(node, attr, cleaned)

    def _handle_position(self, data: Dict) -> None:
        """Handle position message with coordinate validation."""
        payload = data.get("payload", {})
        node_id = data.get("from")
        if not node_id:
            return

        node = self._ensure_node(node_id)
        if node is None:
            return

        # Position may be in different formats
        lat = None
        lon = None

        if "latitude_i" in payload:
            lat = self._safe_float(payload.get("latitude_i"), -900000000, 900000000)
            lon = self._safe_float(payload.get("longitude_i"), -1800000000, 1800000000)
            if lat is not None:
                lat = lat / 1e7
            if lon is not None:
                lon = lon / 1e7
        elif "latitude" in payload:
            lat = self._safe_float(payload.get("latitude"), *VALID_LAT_RANGE)
            lon = self._safe_float(payload.get("longitude"), *VALID_LON_RANGE)

        # Only update if both lat/lon are valid and non-zero
        if lat is not None and lon is not None and (lat != 0.0 or lon != 0.0):
            if VALID_LAT_RANGE[0] <= lat <= VALID_LAT_RANGE[1] and \
               VALID_LON_RANGE[0] <= lon <= VALID_LON_RANGE[1]:
                node.latitude = lat
                node.longitude = lon

        if "altitude" in payload:
            alt = self._safe_float(payload.get("altitude"), -500, 100000)
            if alt is not None:
                node.altitude = alt

    def _handle_telemetry(self, data: Dict) -> None:
        """Handle telemetry message with value validation."""
        payload = data.get("payload", {})
        node_id = data.get("from")
        if not node_id:
            return

        node = self._ensure_node(node_id)
        if node is None:
            return

        # Device metrics
        device = payload.get("device_metrics", {})
        if isinstance(device, dict) and device:
            battery = self._safe_int(device.get("battery_level"), 0, 101)
            if battery is not None:
                node.battery_level = battery

            voltage = self._safe_float(device.get("voltage"), 0.0, 10.0)
            if voltage is not None:
                node.voltage = voltage

            ch_util = self._safe_float(device.get("channel_utilization"), 0.0, 100.0)
            if ch_util is not None:
                node.channel_utilization = ch_util

            air_util = self._safe_float(device.get("air_util_tx"), 0.0, 100.0)
            if air_util is not None:
                node.air_util_tx = air_util

        # Environment metrics (BME280, BME680, BMP280, etc.)
        env = payload.get("environment_metrics", {})
        if isinstance(env, dict) and env:
            temp = self._safe_float(env.get("temperature"), -50.0, 100.0)
            if temp is not None:
                node.temperature = temp

            humidity = self._safe_float(env.get("relative_humidity"), 0.0, 100.0)
            if humidity is not None:
                node.humidity = humidity

            pressure = self._safe_float(env.get("barometric_pressure"), 300.0, 1200.0)
            if pressure is not None:
                node.pressure = pressure

            gas = self._safe_float(env.get("gas_resistance"), 0.0, 1000000.0)
            if gas is not None:
                node.gas_resistance = gas

        # Air quality metrics (PMSA003I, SCD4X)
        aq = payload.get("air_quality_metrics", {})
        if isinstance(aq, dict) and aq:
            pm25_std = self._safe_int(aq.get("pm25_standard"), 0, 1000)
            if pm25_std is not None:
                node.pm25_standard = pm25_std

            pm25_env = self._safe_int(aq.get("pm25_environmental"), 0, 1000)
            if pm25_env is not None:
                node.pm25_environmental = pm25_env

            pm10_std = self._safe_int(aq.get("pm10_standard"), 0, 1000)
            if pm10_std is not None:
                node.pm10_standard = pm10_std

            pm10_env = self._safe_int(aq.get("pm10_environmental"), 0, 1000)
            if pm10_env is not None:
                node.pm10_environmental = pm10_env

            co2 = self._safe_int(aq.get("co2"), 0, 10000)
            if co2 is not None:
                node.co2 = co2

            iaq = self._safe_int(aq.get("iaq"), 0, 500)
            if iaq is not None:
                node.iaq = iaq

        # Health metrics (MAX30102, pulse oximeters) - Meshtastic 2.7+
        # Protobuf: health_metrics { heart_bpm, spO2, temperature }
        health = payload.get("health_metrics", {})
        if isinstance(health, dict) and health:
            heart_bpm = self._safe_int(health.get("heart_bpm"), 30, 250)
            if heart_bpm is not None:
                node.heart_bpm = heart_bpm

            spo2 = self._safe_int(health.get("spO2"), 70, 100)
            if spo2 is not None:
                node.spo2 = spo2

            # Body temperature (different from environment temperature)
            body_temp = self._safe_float(health.get("temperature"), 30.0, 45.0)
            if body_temp is not None:
                node.body_temperature = body_temp

            # Track nodes with health sensors
            with self._stats_lock:
                if "nodes_with_health_metrics" not in self._stats:
                    self._stats["nodes_with_health_metrics"] = 0
                # Count unique nodes with health data (simple count for now)
                if heart_bpm is not None or spo2 is not None:
                    self._stats["nodes_with_health_metrics"] += 1

    def _handle_text_message(self, data: Dict) -> None:
        """Handle text message."""
        payload = data.get("payload", {})
        text = payload.get("text") or data.get("text", "")
        # Type + size gate: a truthy non-string (attacker dict/list) passed
        # the old check and rode into the messages feed/DB as junk; an
        # unbounded string is a memory/storage vector (payload cap is 64 KB,
        # real LoRa text ~237 bytes).
        if not isinstance(text, str) or not text:
            return
        text = text[:_MAX_TEXT_LEN]

        from_id = data.get("from", "")
        to_id = data.get("to", "")
        msg = MQTTMessage(
            message_id=str(data.get("id", time.time()))[:_MAX_MSG_ID_LEN],
            from_id=from_id if isinstance(from_id, str) else "",
            to_id=to_id if isinstance(to_id, str) else "",
            text=text,
            channel=self._safe_int(data.get("channel"), 0, 255) or 0,
            snr=self._safe_float(data.get("snr"), *VALID_SNR_RANGE),
            rssi=self._safe_int(data.get("rssi"), *VALID_RSSI_RANGE),
            hop_start=self._safe_int(data.get("hop_start"), 0, 15),
        )

        with self._messages_lock:
            self._messages.append(msg)

        # Notify callbacks (snapshot for thread-safe iteration)
        for callback in list(self._message_callbacks):
            try:
                callback(msg)
            except Exception as e:
                logger.debug(f"Message callback error: {e}")
