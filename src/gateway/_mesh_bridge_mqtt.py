"""MQTT leg for the Meshtastic preset bridge (split from mesh_bridge.py, MF025).

RX subscribes to meshtasticd's MQTT JSON topics; TX goes over HTTP protobuf
(``/api/v1/toradio``) — never a TCPInterface, so the :9443 web client stays
undisturbed. ``MQTTMeshInterface`` is re-exported from ``gateway.mesh_bridge``.

Logs under the ``gateway.mesh_bridge`` logger on purpose, so journal greps and
caplog filters keep working across the split (same as ``_mesh_bridge_legs``).
"""

import json
import threading
import time
import logging
from typing import Optional, Dict, Callable

from .base_handler import mqtt_content_dedup_key, FallbackDropWitness
from .config import MeshtasticConfig
from utils.safe_import import safe_import

logger = logging.getLogger("gateway.mesh_bridge")

# Optional MQTT client (for MQTT mode)
_mqtt_mod, _HAS_PAHO_MQTT = safe_import('paho.mqtt.client')

# Optional protobuf client (for MQTT mode TX)
_get_protobuf_client, _HAS_PROTOBUF_CLIENT = safe_import(
    '.meshtastic_protobuf_client', 'get_protobuf_client', package='gateway',
)
_ProtobufTransportConfig, _HAS_PROTOBUF_CONFIG = safe_import(
    '.meshtastic_protobuf_ops', 'ProtobufTransportConfig', package='gateway',
)


class MQTTMeshInterface:
    """
    MQTT-based Meshtastic interface (zero-interference alternative to TCP).

    RX: Subscribes to meshtasticd MQTT topics (JSON mode).
    TX: Sends via HTTP protobuf to /api/v1/toradio.

    Implements sendText() compatible with meshtastic TCPInterface,
    so the bridge can use either connection mode transparently.
    """

    def __init__(self, config: MeshtasticConfig, name: str,
                 message_callback: Callable, stop_event: threading.Event):
        self._config = config
        self._name = name
        self._message_callback = message_callback
        self._stop_event = stop_event
        self._client = None
        self._connected = False
        self._mqtt_lock = threading.Lock()
        self._recent_ids: Dict[str, float] = {}
        self._dedup_window = 60
        self._fb_witness = FallbackDropWitness(f"[{name}] ")  # id-less dups dropped (hfm #9)

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> bool:
        """Connect to MQTT broker and subscribe to meshtasticd topics."""
        if not _HAS_PAHO_MQTT:
            logger.error("paho-mqtt not installed for MQTT bridge mode")
            return False

        # Advisory pre-flight for localhost brokers (Issue #3)
        broker = self._config.mqtt_broker
        if broker in ('localhost', '127.0.0.1', '::1'):
            try:
                from utils.service_check import check_service
                broker_status = check_service('mosquitto')
                if not broker_status.available:
                    logger.warning("mosquitto pre-flight: %s (attempting connection anyway)", broker_status.message)
            except ImportError:
                pass

        mqtt = _mqtt_mod
        try:
            client_id = f"meshforge-presetbridge-{self._name}-{int(time.time()) % 10000}"
            self._client = mqtt.Client(
                client_id=client_id,
                protocol=mqtt.MQTTv311,
            )

            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.on_message = self._on_message

            self._client.connect(
                self._config.mqtt_broker,
                self._config.mqtt_port,
                keepalive=60,
            )
            self._client.loop_start()

            # Wait for connection
            for _ in range(50):
                if self._connected:
                    return True
                if self._stop_event.wait(0.1):
                    return False

            if not self._connected:
                logger.warning(f"MQTT connection timed out for {self._name}")
            return self._connected

        except Exception as e:
            logger.error(f"Failed to connect MQTT for {self._name}: {e}")
            return False

    def _on_connect(self, client, userdata, flags, rc):
        """Subscribe to meshtasticd MQTT topics on connect."""
        if rc == 0:
            self._connected = True
            cfg = self._config
            root = "msh"

            # Subscribe to BOTH topic shapes — with and without the region
            # segment. meshtasticd 2.7.x publishes region-less topics
            # (msh/2/json/...); older builds include the region
            # (msh/US/2/json/...). Same dual-shape fix as Issue #34 in
            # mqtt_bridge_handler; per-message dedup absorbs any overlap.
            json_topics = [f"{root}/2/json/{cfg.mqtt_channel}/#"]
            proto_topics = [f"{root}/2/e/{cfg.mqtt_channel}/#"]
            if cfg.mqtt_region:
                json_topics.append(
                    f"{root}/{cfg.mqtt_region}/2/json/{cfg.mqtt_channel}/#")
                proto_topics.append(
                    f"{root}/{cfg.mqtt_region}/2/e/{cfg.mqtt_channel}/#")

            for topic in json_topics:
                client.subscribe(topic)
                logger.info(f"[{self._name}] Subscribed to: {topic}")

            # Also subscribe to encrypted topics for node discovery
            for topic in proto_topics:
                client.subscribe(topic)
        else:
            logger.error(f"[{self._name}] MQTT connect failed (rc={rc})")
            self._connected = False

    def _on_disconnect(self, client, userdata, rc):
        """Handle MQTT disconnection."""
        was_connected = self._connected
        self._connected = False
        if was_connected and rc != 0:
            logger.warning(f"[{self._name}] MQTT disconnected (rc={rc})")

    def _on_message(self, client, userdata, msg):
        """Handle incoming MQTT message — parse and forward to bridge."""
        try:
            if "/json/" not in msg.topic:
                return  # Skip protobuf messages for now

            data = json.loads(msg.payload.decode('utf-8', errors='ignore'))
            msg_type = data.get('type', '')
            msg_id = str(data.get('id', ''))

            # Dedup. Fall back to a shared content key when the packet carries
            # no `id`, so an id-less message is never silently un-deduped
            # (#34 hardening — same fix + shared key as MQTTBridgeHandler).
            dedup_key = msg_id if msg_id else mqtt_content_dedup_key(data)
            if dedup_key and self._is_duplicate(dedup_key):
                if not msg_id:  # counted + journaled (rate-limited INFO)
                    self._fb_witness.note()
                return

            if msg_type != 'text':
                return

            sender = data.get('sender', '')
            from_num = data.get('from', 0)
            to_num = data.get('to', 0)
            payload = data.get('payload', {})
            text = payload.get('text', '') if isinstance(payload, dict) else str(payload)

            if not text:
                return

            to_id = f"!{to_num:08x}" if to_num else None
            is_broadcast = to_num == 0xFFFFFFFF

            # Attribution SSOT (same rule as MQTTBridgeHandler._handle_json
            # _message): `from` is the ORIGINATING node; `sender` is the
            # LAST-HOP radio that uplinked the packet (often the gateway's
            # own node). fromId keyed on sender here silently diverged the
            # Phase-4 dedup/loop-guard content_id from the rns_bridge side
            # (which keys on the originator) on MQTT-leg topologies
            # (review 2026-07-01). Type-guarded: a non-int or negative
            # `from` (malformed/foreign publisher) must fall back to sender
            # attribution, not ValueError into the broad except below and
            # silently drop the whole message.
            from_id = (f"!{from_num & 0xFFFFFFFF:08x}"
                       if isinstance(from_num, int)
                       and not isinstance(from_num, bool) and from_num > 0
                       else sender)

            # Build a packet dict compatible with _process_receive
            packet = {
                'fromId': from_id,
                'toId': to_id or '!ffffffff',
                'channel': data.get('channel', 0),
                'rxSnr': data.get('snr'),
                'rxRssi': data.get('rssi'),
                'decoded': {
                    'portnum': 'TEXT_MESSAGE_APP',
                    'payload': text,
                },
            }

            self._message_callback(packet)

        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"[{self._name}] Failed to parse MQTT JSON: {e}")
        except Exception as e:
            logger.error(f"[{self._name}] Error processing MQTT message: {e}")

    def sendText(self, text: str, destinationId: str = None,
                 channelIndex: int = 0) -> bool:
        """
        Send text message via HTTP protobuf (compatible with TCPInterface API).

        Primary: Stateless direct POST to /api/v1/toradio — NEVER reads
        from /api/v1/fromradio, so the web client at :9443 is never
        starved of delivery ACK packets.

        Fallback: Session-based protobuf client (legacy).
        """
        from .meshtastic_protobuf_client import refuse_if_oversize  # F18: before EITHER path
        if refuse_if_oversize(text, destinationId, f"[{self._name}] mesh_bridge sendText"):
            return False  # never the fromradio-reading session fallback (#17)
        dest_num = None
        if destinationId:
            dest_num = self._node_id_to_num(destinationId)

        # Primary: stateless direct send — zero fromradio contention
        try:
            from .meshtastic_protobuf_client import send_text_direct
            if send_text_direct(
                text=text,
                host=self._config.host,
                port=self._config.http_port,
                tls=self._config.tls_for_port(self._config.http_port),
                destination=dest_num,
                channel_index=channelIndex,
            ):
                return True
        except Exception as e:
            logger.warning(f"[{self._name}] Stateless TX failed: {e}")

        # Fallback: session-based send (reads fromradio during connect)
        if not _HAS_PROTOBUF_CLIENT or not _HAS_PROTOBUF_CONFIG:
            logger.warning(f"[{self._name}] Protobuf client unavailable for TX")
            return False

        try:
            get_client = _get_protobuf_client
            cfg = _ProtobufTransportConfig(
                host=self._config.host,
                port=self._config.http_port,
            )
            client = get_client(cfg)

            if not client.is_connected:
                if not client.connect():
                    logger.warning(f"[{self._name}] Protobuf client connect failed")
                    return False

            return client.send_text(
                text=text,
                destination=dest_num,
                channel_index=channelIndex,
            )
        except Exception as e:
            logger.error(f"[{self._name}] Session-based TX failed: {e}")
            return False

    @staticmethod
    def _node_id_to_num(node_id: str) -> Optional[int]:
        """Convert Meshtastic node ID string to numeric form."""
        if not node_id:
            return None
        try:
            cleaned = node_id.lstrip('!')
            return int(cleaned, 16)
        except ValueError:
            try:
                return int(node_id)
            except ValueError:
                return None

    def _is_duplicate(self, msg_id: str) -> bool:
        """Check if message ID was seen recently (monotonic age: a wall-clock
        step on an RTC-less Pi must not stretch or shrink the window)."""
        now = time.monotonic()
        with self._mqtt_lock:
            seen = self._recent_ids.get(msg_id)
            if seen is not None and now - seen <= self._dedup_window:
                return True
            self._recent_ids[msg_id] = now
            # Cleanup old entries
            expired = [k for k, v in self._recent_ids.items()
                       if now - v > self._dedup_window]
            for k in expired:
                del self._recent_ids[k]
        return False

    def close(self):
        """Disconnect from MQTT broker."""
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception as e:
                logger.debug(f"[{self._name}] Error disconnecting MQTT: {e}")
        self._connected = False
