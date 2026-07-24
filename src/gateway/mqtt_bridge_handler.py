"""
MQTT Bridge Handler for RNS Gateway.

Replaces TCP-based MeshtasticHandler with zero-interference approach.

RX: Receives mesh traffic via MQTT subscription (no TCP connection needed).
TX: Sends to mesh via HTTP protobuf (/api/v1/toradio), CLI as fallback.

Architecture:
    RX: Meshtastic mesh -> meshtasticd -> MQTT broker -> MQTTBridgeHandler
    TX: MQTTBridgeHandler -> HTTP protobuf -> meshtasticd -> Meshtastic mesh
        (fallback: CLI subprocess -> meshtasticd TCP -> Meshtastic mesh)

Zero interference:
    - RX via MQTT: no TCP connection to meshtasticd
    - TX via HTTP protobuf: uses /api/v1/toradio (same as web client)
    - Web client on :9443 works uninterrupted
    - Multiple monitoring tools can coexist

Requires:
    - mosquitto (or any MQTT broker) running locally
    - meshtasticd configured with mqtt.enabled=true, mqtt.json_enabled=true
    - paho-mqtt (pip install paho-mqtt)
    - meshtastic Python package (for protobuf TX; CLI used as fallback)

Usage:
    handler = MQTTBridgeHandler(config, node_tracker, health, ...)
    handler.run_loop()  # Blocks, runs in thread
"""

import json
import logging
import re
import subprocess
import threading
import time
from datetime import datetime
from queue import Full
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from gateway import delivery_counters as _dc
from .ack_tracker import AckTracker, routing_error_to_drop_reason
from .base_handler import (
    UNKNOWN_ORIGIN, BaseMessageHandler, dual_path_dedup_enabled,
    dual_path_dedup_window_s, get_rf_tx_registry, is_bridge_loop,
    mesh_origin_content_id, mqtt_content_dedup_key,
    true_origin_downlink_enabled, true_origin_loop_guard_window_s,
)
from utils.meshtastic_se_crypto import (
    DEFAULT_KEY_B64, crypto_available, decode_service_envelope,
)
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# Optional MQTT client
_mqtt_mod, _HAS_PAHO_MQTT = safe_import('paho.mqtt.client')

# Optional protobuf client
_get_protobuf_client, _HAS_PROTOBUF_CLIENT = safe_import(
    '.meshtastic_protobuf_client', 'get_protobuf_client', package='gateway',
)

# Sudo-safe home directory — first-party, always available (MF001)
from utils.paths import get_real_user_home as _get_real_user_home_fn
from utils.service_check import check_service as _check_service

if TYPE_CHECKING:
    from .bridge_health import BridgeHealthMonitor
    from .config import GatewayConfig
    from .node_tracker import UnifiedNodeTracker


class MQTTBridgeHandler(BaseMessageHandler):
    """
    MQTT-based Meshtastic handler for the gateway bridge.

    Subscribes to meshtasticd's MQTT topics to receive mesh traffic.
    Uses meshtastic CLI for sending messages (transient, no interference).

    This replaces the TCP-based MeshtasticHandler that held a persistent
    connection to port 4403, blocking the web client.

    Args:
        config: Gateway configuration object
        node_tracker: Unified node tracker instance
        health: Bridge health monitor instance
        stop_event: Threading event for graceful shutdown
        stats: Shared statistics dictionary
        stats_lock: Lock for thread-safe stats updates
        message_queue: Queue for messages to be bridged to RNS
        message_callback: Callback for received messages
        status_callback: Callback for status changes
        should_bridge: Callback to check routing rules
    """

    def __init__(
        self,
        config: 'GatewayConfig',
        node_tracker: 'UnifiedNodeTracker',
        health: 'BridgeHealthMonitor',
        stop_event: threading.Event,
        stats: Dict[str, Any],
        stats_lock: threading.Lock,
        message_queue,
        message_callback: Optional[Callable] = None,
        status_callback: Optional[Callable] = None,
        should_bridge: Optional[Callable] = None,
        load_balancer=None,
        persistent_queue=None,
    ):
        super().__init__(
            config=config,
            node_tracker=node_tracker,
            health=health,
            stop_event=stop_event,
            stats=stats,
            stats_lock=stats_lock,
            message_queue=message_queue,
            message_callback=message_callback,
            status_callback=status_callback,
            should_bridge=should_bridge,
        )

        # Thread-2 step 4 — honest Meshtastic delivery confirmation in
        # mqtt_bridge mode, via the /e/ ServiceEnvelope MQTT topic (the path
        # the soak proved out, .claude/research/mqtt_bridge_ack_feasibility_*).
        # The gateway already TXes wantAck DMs (send_text_direct default) and
        # already subscribes to /2/e/; the recipient's ROUTING_APP ACK rides
        # /e/ encrypted, so we decode it there — staying entirely within MQTT,
        # NO fromradio read (#17/#75 preserved). In-flight packet_id->msg_id
        # map; the ACK resolves it to CONFIRMED / DROPPED(real NAK reason).
        self._ack_tracker = AckTracker(
            ttl_sec=getattr(config.rns, 'ack_pending_ttl_sec', None),
            max_pending=getattr(config.rns, 'ack_pending_max', None),
        )
        if getattr(config.rns, 'meshtastic_ack_consumption_enabled', False):
            if crypto_available():
                logger.info(
                    "Meshtastic ACK consumption ACTIVE (mqtt_bridge / #74): "
                    "decoding ROUTING_APP from the /e/ ServiceEnvelope topic "
                    "to confirm wantAck DMs — no fromradio read.")
            else:
                logger.warning(
                    "rns.meshtastic_ack_consumption_enabled is set but the "
                    "cryptography/meshtastic-protobuf deps needed to decode "
                    "the /e/ ServiceEnvelope topic are unavailable — ACK "
                    "consumption is INERT (delivery stays 'Sent, not "
                    "guaranteed'). Install requirements to enable.")

        # TX load balancer (optional, for dual-radio setups)
        self._load_balancer = load_balancer

        # Persistent SQLite queue for M→R overflow durability (Hardening B).
        # When the in-memory _message_queue.put_nowait() fails with Full,
        # the message gets persisted here under destination="rns_xform"
        # for the bridge worker to drain through _process_mesh_to_rns
        # later. Survives crash mid-burst; bounded by the queue's own
        # max-depth + auto-cleanup of delivered/dead-letter rows.
        self._persistent_queue = persistent_queue

        # MQTT client (handler-specific)
        self._client = None
        self._mqtt_lock = threading.Lock()

        # Meshtastic CLI path (cached)
        self._cli_path: Optional[str] = None

        # Deduplication: track recent message IDs to avoid loops
        self._recent_ids: Dict[str, float] = {}
        self._dedup_window = 60  # seconds

        # Hardening A: bridge channel deployment diagnostic. Set on the
        # first MQTT JSON message received that matches the configured
        # channel. Stays None when fleet clients haven't been provisioned
        # with the bridge channel — a state today's bridge can't detect.
        # The gateway main loop polls this past a configurable window
        # (default 30 min, override MESHFORGE_BRIDGE_RX_STALE_SEC) to
        # surface the deployment gap as a journal WARNING + TUI status
        # signal instead of silently frozen counters.
        self._last_uplink_at: Optional[float] = None
        self._stale_warning_emitted: bool = False

        # Mesh oracle (read-only "ask dude-AI over the mesh" responder) — the
        # MQTT-bridge RX leg. THIS is the leg that actually fires on fleet
        # gateways, which ingest Meshtastic via MQTT (zero-interference mode),
        # NOT the PhoneAPI MeshtasticHandler. Default OFF; inert unless
        # MESHFORGE_ORACLE_ENABLED is set (self._oracle stays None and the
        # _bridge_text_message hook is a no-op).
        self._oracle = None
        try:
            self._oracle = self._build_mqtt_oracle_responder()
        except Exception as e:  # pragma: no cover - never break handler init
            logger.debug(f"mqtt oracle not initialized: {e}")

    def _build_mqtt_oracle_responder(self):
        """Construct the read-only mesh-oracle responder for the MQTT-bridge RX
        path, or None if disabled (default OFF via MESHFORGE_ORACLE_ENABLED,
        shared across legs; checked BEFORE importing the oracle).

        Channel access is keyed on the channel NAME from the MQTT topic
        (``_topic_channel_name`` — fleet-stable, unlike the box-local per-message
        index; needs no startup radio query, so it can't be defeated by RX churn
        at restart), additive with the node allowlist (MESHFORGE_ORACLE_ALLOWLIST
        — same env as the PhoneAPI leg). Replies go DIRECTED to the sender on the
        gateway's configured channel via send_text -> /api/v1/toradio (no TCP, no
        fromradio read; #17/#75 preserved). Read-only — never mutates state.
        """
        import os
        if str(os.environ.get("MESHFORGE_ORACLE_ENABLED", "")).strip().lower() \
                not in ("1", "true", "yes", "on"):
            return None
        from oracle import fetch_api_status, read_snapshot
        from oracle.responder import MeshOracleResponder

        def _snapshot():
            return read_snapshot(status=fetch_api_status())

        def _send(text: str, dest: str, channel) -> bool:
            # Reply as a channel BROADCAST on the configured channel index — NOT
            # a directed DM to `dest`. The asker's node-id is unreliable here:
            # a re-emit / relay path (meshtastic_reemit, a mini-mesh repeater)
            # collapses the original sender, so the gateway often sees the query
            # `from` the gateway's own or a relay node — a DM would target the
            # wrong node and a remote portable would never get it. Broadcasting
            # on the channel reaches every node on it (incl. the real asker),
            # matching the MeshCore leg + the private-channel model. (`channel`
            # here is the matched channel NAME, not a TX index.)
            reply_idx = int(getattr(self.config.meshtastic, "channel", 0) or 0)
            return self.send_text(text, destination=None, channel=reply_idx)

        def _log(record: dict) -> None:
            try:
                from mini_dudeai.history import append_jsonl
                from oracle import oracle_log_path
                p = oracle_log_path()
                p.parent.mkdir(parents=True, exist_ok=True)
                err = append_jsonl(str(p), [record], 2 * 1024 * 1024)
                if err:  # #60/#9: a swallowed sandbox write must leave a witness
                    logger.warning(f"mesh oracle audit log write failed: {err}")
            except Exception as e:  # pragma: no cover - best-effort audit log
                logger.debug(f"mqtt oracle log append failed: {e}")

        names = {n.strip().lower() for n in os.environ.get(
            "MESHFORGE_ORACLE_CHANNELS", "").split(",") if n.strip()}

        return MeshOracleResponder.from_env(
            snapshot_fn=_snapshot, send_fn=_send, log_fn=_log,
            allowed_channels=names)

    # --- Thread-2 step 4: ACK consumption via /e/ ------------------------

    @property
    def ack_tracker(self) -> AckTracker:
        """In-flight Meshtastic DM ACK tracker (shared sweep contract)."""
        return self._ack_tracker

    def _ack_consumption_enabled(self) -> bool:
        try:
            return bool(getattr(
                self.config.rns, 'meshtastic_ack_consumption_enabled', False))
        except Exception:
            return False

    def _channel_keys(self) -> List[str]:
        """Base64 channel PSKs to try when decrypting /e/ packets.

        The default key (LongFast) plus the operator's downlink_psk if set —
        a directed downlink's ACK rides the channel it was sent on, so these
        two cover the primary + the gateway's keyed channel.
        """
        keys = [DEFAULT_KEY_B64]
        psk = getattr(self.config.meshtastic, 'downlink_psk', '') or ''
        if isinstance(psk, str) and psk and psk not in keys:
            keys.append(psk)
        for k in getattr(self.config.meshtastic, 'channel_keys', None) or []:
            if isinstance(k, str) and k and k not in keys:
                keys.append(k)
        return keys

    def _maybe_register_ack(self, packet_id, dest_num, msg_id,
                            record_sent: bool) -> None:
        """Arm ACK confirmation for a just-sent DM (mqtt_bridge / #74).

        No-op for broadcasts (no per-node ACK) and when disabled. The direct
        send path synthesizes an id + records SENT (record_sent=True); the
        queue path passes its _queue_msg_id (SENT owned by mark_delivered).
        """
        try:
            if not self._ack_consumption_enabled():
                return
            if (dest_num is None or dest_num == 0xFFFFFFFF
                    or not isinstance(packet_id, int) or packet_id <= 0):
                return
            if not msg_id:
                msg_id = f"mesh-{packet_id:08x}"
            if record_sent:
                _dc.record(_dc.DeliveryState.SENT, msg_id=msg_id,
                           protocol="meshtastic")
            self._ack_tracker.register(packet_id, msg_id, protocol="meshtastic")
        except Exception as e:
            logger.debug(f"Could not arm ACK tracking: {e}")

    def _handle_routing_envelope(self, dp) -> None:
        """A decoded ROUTING_APP /e/ packet → CONFIRMED / DROPPED if it
        matches one of our in-flight DMs. Never raises into the MQTT loop."""
        try:
            resolved = self._ack_tracker.resolve(dp.request_id)
            if resolved is None:
                return
            msg_id, protocol = resolved
            err = dp.routing_error_name()
            if err in (None, "", "NONE"):
                _dc.record(_dc.DeliveryState.CONFIRMED, msg_id=msg_id,
                           protocol=protocol)
                with self._stats_lock:
                    self.stats['mesh_ack_confirmed'] = (
                        self.stats.get('mesh_ack_confirmed', 0) + 1)
                logger.info(
                    f"Meshtastic ACK confirmed delivery of {msg_id} "
                    f"(pkt={dp.request_id:#0x}, via /e/)")
            else:
                reason = routing_error_to_drop_reason(err)
                _dc.record(_dc.DeliveryState.DROPPED, msg_id=msg_id,
                           protocol=protocol, drop_reason=reason,
                           note=f"meshtastic_nak:{err}"[:80])
                with self._stats_lock:
                    self.stats['mesh_ack_failed'] = (
                        self.stats.get('mesh_ack_failed', 0) + 1)
                logger.warning(
                    f"Meshtastic NAK for {msg_id} (pkt={dp.request_id:#0x}): "
                    f"{err} -> {reason.value}")
        except Exception as e:
            logger.debug(f"Error handling /e/ routing envelope: {e}")

    def run_loop(self) -> None:
        """
        Main loop: connect to MQTT and process messages.

        Blocks until stop_event is set. Handles reconnection automatically.
        """
        while not self._stop_event.is_set():
            try:
                if not self._connected:
                    logger.info("Connecting to MQTT broker for gateway bridge...")
                    self._connect()

                    if self._connected:
                        self.health.record_connection_event("meshtastic", "connected")
                        logger.info("MQTT bridge handler connected")
                        self._notify_status("meshtastic_connected")
                    else:
                        self.health.record_connection_event("meshtastic", "retry")
                        self._stop_event.wait(5)
                        continue

                # MQTT client has its own event loop via loop_start()
                # We just need to stay alive and do periodic maintenance
                self._cleanup_dedup()
                self._stop_event.wait(1)

            except Exception as e:
                self.health.record_error("meshtastic", e)
                logger.error(f"MQTT bridge loop error: {e}")
                self._connected = False
                self.health.record_connection_event("meshtastic", "error", str(e))
                self._stop_event.wait(5)

    def connect(self) -> bool:
        """Connect to MQTT broker (ABC contract)."""
        return self._connect()

    def _connect(self) -> bool:
        """Connect to MQTT broker and subscribe to meshtasticd topics."""
        if not _HAS_PAHO_MQTT:
            logger.error("paho-mqtt not installed. Install with: pip install paho-mqtt")
            return False

        # Pre-flight: verify MQTT broker is running
        mqtt_cfg = self.config.mqtt_bridge
        if mqtt_cfg.broker in ('localhost', '127.0.0.1', '::1'):
            broker_status = _check_service('mosquitto')
            if not broker_status.available:
                logger.warning("mosquitto service check: %s (attempting connection anyway)",
                               broker_status.message)
                if broker_status.fix_hint:
                    logger.info("Fix: %s", broker_status.fix_hint)
                # Continue — mosquitto may be running outside systemd

        mqtt = _mqtt_mod

        try:
            # Create MQTT client
            client_id = f"meshforge-gateway-{int(time.time()) % 10000}"
            self._client = mqtt.Client(
                client_id=client_id,
                protocol=mqtt.MQTTv311,
            )

            # Auth if configured
            if mqtt_cfg.username:
                self._client.username_pw_set(mqtt_cfg.username, mqtt_cfg.password)

            # TLS if configured
            if mqtt_cfg.use_tls:
                self._client.tls_set()

            # Callbacks
            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.on_message = self._on_message

            # Connect
            self._client.connect(
                mqtt_cfg.broker,
                mqtt_cfg.port,
                keepalive=60,
            )

            # Start background thread for MQTT event loop
            self._client.loop_start()

            # Wait briefly for connection
            for _ in range(50):
                if self._connected:
                    return True
                if self._stop_event.wait(0.1):
                    return False

            if not self._connected:
                logger.warning("MQTT connection timed out")
                return False

            return True

        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            self._connected = False
            return False

    def _dm_to_gateway_node_num(self):
        """Gateway's own node number when the DM-to-gateway leg is armed.

        Theme-A step 3. Returns the int node number iff
        rns.sessions_enabled is strictly True AND
        meshtastic.gateway_node_id parses as !hex8; else None (leg
        dormant — MQTT subscriptions and filtering stay exactly legacy).
        """
        rns_cfg = getattr(self.config, 'rns', None)
        if getattr(rns_cfg, 'sessions_enabled', False) is not True:
            return None
        mesh_cfg = getattr(self.config, 'meshtastic', None)
        raw = getattr(mesh_cfg, 'gateway_node_id', '') or ''
        if not isinstance(raw, str):
            return None
        norm = raw.strip().lower()
        if not re.match(r'^![0-9a-f]{8}$', norm):
            return None
        return int(norm[1:], 16)

    @staticmethod
    def _topic_channel_name(topic: str):
        """Channel name segment from msh/.../2/json/{CHANNEL}/{NODE}."""
        parts = topic.split('/')
        try:
            idx = parts.index('json')
        except ValueError:
            return None
        return parts[idx + 1] if len(parts) > idx + 1 else None

    def _counts_as_bridge_uplink(self, topic: str) -> bool:
        """Whether an MQTT-json arrival is bridge-channel liveness evidence.

        ``_last_uplink_at`` is the "bridge channel alive" heartbeat that
        :meth:`_channel_diagnostic_loop` watches to catch a dark bridge
        channel (fleet clients transmitting on a different channel name — the
        moc3 stall, 2026-04-27).

        Pri-5 (gateway review 2026-07-23): when the DM-to-gateway leg is armed
        the json subscription widens to a ``+`` wildcard, so foreign-channel
        traffic (e.g. the primary, which carries DMs) also reaches
        :meth:`_handle_json_message`. Counting it would refresh the heartbeat
        off a channel the bridge doesn't watch, masking exactly the dark
        bridge channel this signal exists to surface (honest_failure_modes #2:
        absence of bridge-channel traffic must not read as presence).

        Legacy scoped-subscription mode (leg dormant) is unaffected — every
        arrival is already on the bridge channel, so this passes. While the
        wildcard is active, only a *confidently foreign* channel is excluded;
        an unparseable topic or an unconfigured channel is held as counting,
        so ambiguity never manufactures a false "dark" (hfm #2).
        """
        if self._dm_to_gateway_node_num() is None:
            return True  # scoped subscription — arrival is already on-channel
        cfg_chan = getattr(
            getattr(self.config, 'mqtt_bridge', None), 'channel', None)
        topic_chan = self._topic_channel_name(topic)
        if cfg_chan and topic_chan is not None and topic_chan != cfg_chan:
            return False
        return True

    def _on_connect(self, client, userdata, flags, rc):
        """MQTT connect callback - subscribe to meshtasticd topics."""
        if rc == 0:
            self._connected = True
            mqtt_cfg = self.config.mqtt_bridge

            # meshtasticd publishes in two topic shapes depending on build:
            #   region-ful:  msh/{REGION}/2/json/{CHANNEL}/{NODE}
            #   region-less: msh/2/json/{CHANNEL}/{NODE}   (e.g. 2.7.15)
            # Subscribe to both so the bridge works regardless of daemon version.
            root = mqtt_cfg.root_topic
            chan = mqtt_cfg.channel

            if mqtt_cfg.json_enabled:
                # Theme-A step 3 (radio-smoke finding 2026-06-03): DMs ride
                # the PRIMARY channel, whose NAME this config doesn't know —
                # a DM-to-gateway published under .../json/<primary>/... can
                # never match a channel-scoped subscription. When the
                # DM-to-gateway leg is armed, subscribe the json wildcard and
                # filter per-message in _bridge_text_message (configured
                # channel passes as before; foreign channels pass ONLY when
                # addressed to the gateway's own node). Leg dormant = exactly
                # the legacy scoped subscriptions.
                json_chan = "+" if self._dm_to_gateway_node_num() is not None else chan
                for t in (f"{root}/+/2/json/{json_chan}/#",
                          f"{root}/2/json/{json_chan}/#"):
                    client.subscribe(t)
                    logger.debug(f"Subscribed to JSON topic: {t}")

            for t in (f"{root}/+/2/e/{chan}/#", f"{root}/2/e/{chan}/#"):
                client.subscribe(t)
                logger.debug(f"Subscribed to protobuf topic: {t}")

            logger.info(f"MQTT bridge connected to {mqtt_cfg.broker}:{mqtt_cfg.port}")
        else:
            logger.error(f"MQTT connection failed with code {rc}")
            self._connected = False

    def _on_disconnect(self, client, userdata, rc):
        """MQTT disconnect callback."""
        was_connected = self._connected
        self._connected = False
        if was_connected:
            if rc == 0:
                logger.info("MQTT bridge disconnected cleanly")
            else:
                logger.warning(f"MQTT bridge disconnected unexpectedly (rc={rc})")
                self.health.record_connection_event("meshtastic", "disconnected", f"rc={rc}")
            self._notify_status("meshtastic_disconnected")

    def _on_message(self, client, userdata, msg):
        """Handle incoming MQTT message from meshtasticd."""
        try:
            topic = msg.topic
            payload = msg.payload

            # Determine if JSON or protobuf based on topic
            if "/json/" in topic:
                self._handle_json_message(topic, payload)
            else:
                # Protobuf messages need decoding - skip for now,
                # JSON mode is the recommended path
                self._handle_protobuf_message(topic, payload)

        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")

    def _handle_json_message(self, topic: str, payload: bytes) -> None:
        """
        Handle JSON-encoded message from meshtasticd MQTT.

        JSON messages have this structure:
        {
            "channel": 0,
            "from": 1234567890,
            "id": 12345678,
            "payload": {"text": "Hello"},
            "sender": "!abcd1234",
            "timestamp": 1234567890,
            "to": 4294967295,
            "type": "text"
        }
        """
        try:
            data = json.loads(payload.decode('utf-8', errors='ignore'))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.debug(f"Failed to parse MQTT JSON: {e}")
            return

        # Hardening A: a well-formed JSON arrival on the CONFIGURED bridge
        # channel proves a fleet client is publishing there. Recorded BEFORE
        # the type-specific dispatch so even nodeinfo/telemetry/position
        # broadcasts (not just text) count as deployment evidence. Pri-5
        # (gateway review 2026-07-23): scoped to the bridge channel so the
        # DM-to-gateway wildcard's foreign-channel traffic can't forge this
        # heartbeat and mask a dark bridge channel (_counts_as_bridge_uplink).
        if self._counts_as_bridge_uplink(topic):
            self._last_uplink_at = time.time()

        msg_type = data.get('type', '')
        sender = data.get('sender', '')
        msg_id = str(data.get('id', ''))

        # Dedup check. When the JSON carries no stable packet `id` (some
        # meshtasticd builds / malformed publishes omit it) the old
        # `if msg_id and ...` guard skipped dedup ENTIRELY, so an identical
        # packet delivered twice (overlapping topic subscriptions, a retained
        # message, or QoS-1 redelivery) bridged twice (#34 hardening). Fall
        # back to a content-derived key so the dedup layer is never silently
        # bypassed; an empty key (no usable content) preserves the legacy
        # skip rather than collapsing distinct empties.
        dedup_key = msg_id if msg_id else mqtt_content_dedup_key(data)
        if dedup_key and self._is_duplicate(dedup_key):
            return

        # Update node tracking
        from_num = data.get('from', 0)
        if from_num:
            self._update_node_from_mqtt(data)

        # Handle text messages for bridging
        if msg_type == 'text':
            self._bridge_text_message(data, topic)

        # Handle telemetry for node tracking
        elif msg_type == 'telemetry':
            self._update_telemetry(data)

        # Handle position for maps
        elif msg_type == 'position':
            self._update_position(data)

        # Handle nodeinfo for discovery
        elif msg_type == 'nodeinfo':
            self._update_nodeinfo(data)

    def _handle_protobuf_message(self, topic: str, payload: bytes) -> None:
        """Handle a protobuf ServiceEnvelope (/e/) from meshtasticd MQTT.

        Thread-2 step 4: the /e/ topic carries every MeshPacket the radio
        hears, including the ROUTING_APP ACK for a wantAck DM we sent. We
        decode it (channel-decrypt — no fromradio read) to confirm delivery.

        Cost-bounded: only decode when ACK consumption is enabled AND at
        least one DM is awaiting its ACK — so a quiet/disabled gateway pays
        nothing, and a busy one only decrypts while a confirmation is
        actually pending. Bridging of mesh TEXT still goes via the /json/
        path (this only consumes routing)."""
        if not self._ack_consumption_enabled():
            return
        try:
            if self._ack_tracker.pending_count() == 0:
                return
            dp = decode_service_envelope(payload, self._channel_keys())
            if dp is None or not dp.is_routing or dp.request_id <= 0:
                return
            self._handle_routing_envelope(dp)
        except Exception as e:
            logger.debug(f"Protobuf /e/ decode failed on {topic}: {e}")

    def _bridge_text_message(self, data: dict, topic: str) -> None:
        """Bridge a text message from Meshtastic to RNS."""
        from .rns_bridge import BridgedMessage
        from .bridge_health import MessageOrigin
        from .canonical_message import compute_content_id

        # `sender` is the LAST-HOP node that delivered the packet to this
        # gateway's radio (often a rebroadcaster, or the gateway's own node);
        # `from` is the ORIGINATING node. Attribution must use `from` — using
        # `sender` collapsed every multi-hop source (e.g. the bot, reached
        # over several hops) into whichever node last rebroadcast it, so all
        # of them surfaced as one identity downstream (and then one
        # [RNS:<gw>] tag after the RNS hop). `sender` is still used below for
        # the self-echo filter, which legitimately needs the last hop.
        sender = data.get('sender', '')
        from_num = data.get('from', 0)
        # Type-guarded (review 2026-07-01): a non-int/negative `from` from a
        # malformed or foreign publisher must fall back to sender attribution,
        # not ValueError out of the handler. UNKNOWN_ORIGIN is the shared
        # sentinel mesh_origin_content_id refuses as an identity.
        from_id = (f"!{from_num & 0xFFFFFFFF:08x}"
                   if isinstance(from_num, int)
                   and not isinstance(from_num, bool) and from_num > 0
                   else (sender or UNKNOWN_ORIGIN))
        to_num = data.get('to', 0)
        payload = data.get('payload', {})
        text = payload.get('text', '') if isinstance(payload, dict) else str(payload)
        channel = data.get('channel', 0)

        if not text:
            return

        # Theme-A step 3: with the DM-to-gateway leg armed, the json
        # subscription is a channel wildcard — enforce the channel scope
        # here instead. Configured-channel traffic passes exactly as
        # before; a foreign channel (e.g. the primary, which carries DMs)
        # passes ONLY when the packet is addressed to the gateway's own
        # node. Everything else on foreign channels is dropped.
        own_num = self._dm_to_gateway_node_num()
        if own_num is not None:
            topic_chan = self._topic_channel_name(topic)
            cfg_chan = getattr(self.config.mqtt_bridge, 'channel', None)
            if (topic_chan is not None and cfg_chan
                    and topic_chan != cfg_chan and to_num != own_num):
                logger.debug(
                    f"Foreign-channel packet ignored ({topic_chan}, "
                    f"to=!{to_num:08x})")
                return

        # Seen-on-RF registration (dual-path dedup, cross-BOX direction):
        # a broadcast heard here IS on this radio's mesh, whoever TX'd it —
        # including another box's radio on the same RF segment (live
        # 2026-06-04: moc's serial leg TX'd [Mesh:LONG_FAST:..] Cmd onto the
        # ST segment that is moc3's primary; moc3's own TX bookkeeping could
        # never see it, so moc3's RNS relay copy of the same content went
        # out too and the bot answered both). Registering at RX lets the
        # inject-side checks suppress that relay copy. Deliberately BEFORE
        # the loop guard below — tagged content is dropped for bridging but
        # is still on the mesh. After the channel-scope guard above, so
        # foreign-channel traffic doesn't poison the configured channel's
        # registry. Registration unconditional/cheap; suppression flag-gated.
        if to_num == 0xFFFFFFFF:
            get_rf_tx_registry().register(text)

        # Loop guard: a leading [RNS:xxxx] tag marks content a gateway
        # already injected FROM the RNS network — it is by definition already
        # in RNS, so bridging it back (Mesh→RNS) loops/duplicates. This drops
        # both this gateway's own echo (meshtasticd republishes our TX via
        # MQTT) AND a sibling fleet gateway's injection heard on the shared
        # channel (which previously got re-bridged → cross-gateway loop:
        # a co-located gateway re-bridging a peer's [RNS:] injection back
        # into RNS, duplicating it for upstream peers). Genuine
        # operator content (web UI / CLI sends) has no [RNS:] prefix and still
        # bridges so operators see their own activity in NomadNet.
        #
        # Content_id augmentation (transport-truth arc Phase 2): when true-
        # origin downlink delivery is enabled, content this box delivered
        # UNTAGGED as its true mesh origin (the [RNS:] tag dropped) can't be
        # caught by the tag test above — it is recognized instead by the
        # content_id registered at delivery. Channel-agnostic id so this check
        # matches the registration regardless of ingress mode (#77). Flag off
        # (fleet default): loop_cid stays '' and is_bridge_loop reduces exactly
        # to is_already_bridged — no behavior change, id not even computed.
        loop_cid = ""
        if true_origin_downlink_enabled(self.config):
            loop_cid = mesh_origin_content_id(from_id, text)
        if is_bridge_loop(
                text, loop_cid,
                registry=get_rf_tx_registry(),
                cid_window_s=true_origin_loop_guard_window_s(self.config)):
            logger.debug(f"Not re-bridging RNS-tagged content (loop guard): {text[:40]}")
            return

        # Cross-box loop guard (transport-truth arc Option A): the content
        # PASSED the guard, so this gateway is about to bridge this broadcast
        # mesh→RNS. Register its content_id so a PEER gateway's true-origin
        # re-injection of the SAME logical content — heard back on RF UNTAGGED,
        # which the tag test can't catch — is recognized by the loop guard above
        # on THIS box and not re-bridged again. This is what makes untagged
        # true-origin delivery safe on a segment SHARED with a peer gateway (the
        # moc↔moc3 case Phase-2's intra-box registry alone can't cover): every
        # gateway that heard the original registers the id, so it recognizes the
        # echo. Registered AFTER the guard (never suppresses the original) and
        # only when the flag armed loop_cid (inert when off). Broadcast-only, to
        # match Phase-3's broadcast-only true-origin delivery.
        if loop_cid and to_num == 0xFFFFFFFF:
            get_rf_tx_registry().register_content_id(loop_cid)

        # Mesh oracle (read-only): answer a query DIRECTED back to the sender,
        # consumed (NOT bridged/stored onward) when consume=True (default).
        # consume=False = bridge-through: still answered, but the command keeps
        # bridging to RNS so the far mesh's NOC sees the activity. Channel-gated
        # by NAME from the topic (fleet-stable) + additive node allowlist.
        # Non-queries (incl. our own [non-tagged] replies — is_query()==False)
        # pass through untouched; per-sender cooldown bounds airtime. Placed
        # AFTER the loop guard so a gateway's own re-heard reply is already
        # filtered, and BEFORE store/queue so a consumed query is not bridged.
        _oracle = getattr(self, "_oracle", None)
        if _oracle is not None:
            try:
                chan_name = (self._topic_channel_name(topic) or "").lower()
                reply = _oracle.handle(from_id, text, chan_name)
                if reply is not None and _oracle.consume:
                    return
            except Exception as e:
                logger.debug(f"mqtt oracle handle error: {e}")

        # Determine destination
        to_id = f"!{to_num:08x}" if to_num else None
        is_broadcast = to_num == 0xFFFFFFFF

        # Logical content identity (dedup/identity arc, STEP 2 — measure-only).
        # Keyed on the ORIGINATOR (data['from'] via from_id, not the last-hop
        # sender), tag-stripped content, and the channel NAME from the topic
        # (NOT the box-local numeric slot index, #77) — so the SAME logical
        # broadcast minted independently on moc and moc3 gets the SAME id.
        content_id = compute_content_id(
            f"meshtastic:{from_id}", text, self._topic_channel_name(topic) or "")

        msg = BridgedMessage(
            source_network="meshtastic",
            source_id=from_id,
            destination_id=to_id,
            content=text,
            is_broadcast=is_broadcast,
            origin=MessageOrigin.MQTT,
            via_internet=False,  # Local MQTT, not internet relay
            content_id=content_id,
            metadata={
                'channel': channel,
                'mqtt_topic': topic,
                'msg_id': data.get('id'),
                'timestamp': data.get('timestamp'),
            },
        )

        # Store incoming message for UI/history
        try:
            from commands import messaging
            dest = None if is_broadcast else to_id
            messaging.store_incoming(
                from_id=from_id,
                content=text,
                network="meshtastic",
                to_id=dest,
                channel=channel,
            )
        except Exception as e:
            logger.debug(f"Could not store incoming message: {e}")

        # Queue for bridging if routing rules allow
        if self._message_queue is not None:
            if self._should_bridge and not self._should_bridge(msg):
                logger.debug(f"Message from {sender} blocked by routing rules")
            else:
                try:
                    # Hardening B: short timeout backpressure absorbs typical
                    # bursts; persistent spill catches sustained overload so
                    # we don't lose traffic on a slow consumer.
                    self._message_queue.put(msg, timeout=0.5)
                except Full:
                    persisted = self._spill_to_persistent_queue(msg)
                    if persisted:
                        logger.warning(
                            "Mesh→RNS in-memory queue full; persisted to "
                            "SQLite spill for later xform"
                        )
                    else:
                        logger.error(
                            "Mesh→RNS queue full and persistent spill "
                            "unavailable — message dropped: %r",
                            text[:50] if text else "",
                        )
                        with self._stats_lock:
                            self.stats['errors'] += 1
                            self.stats['mesh_to_rns_dropped'] += 1

        # Notify callback
        if self._message_callback:
            try:
                self._message_callback(msg)
            except Exception as e:
                logger.error(f"Message callback error: {e}")

        # Emit to event bus for TUI live feed (Issue #17 Phase 3)
        try:
            from utils.event_bus import emit_message
            emit_message(
                direction='rx',
                content=text,
                node_id=sender,
                channel=channel,
                network='meshtastic',
                raw_data={
                    'to_id': to_id,
                    'is_broadcast': is_broadcast,
                    'mqtt_topic': topic,
                    'msg_id': data.get('id'),
                    'timestamp': data.get('timestamp'),
                }
            )
        except Exception as e:
            logger.debug(f"Event bus emit failed: {e}")

    @staticmethod
    def _originator_id(data: dict) -> str:
        """Node ID of the packet's ORIGINATOR, not its MQTT uplinker.

        `sender` is the gateway radio that published the packet to MQTT —
        on a localhost broker that is ALWAYS this box's own radio, so
        keying node-tracker updates on it wrote every heard node's
        nodeinfo/position onto the gateway's own node id (names churned
        to whichever node was heard last; positions teleported between
        sites). Same sender-vs-from class as the 9554f06 text-attribution
        fix. `from` is the originating node; fall back to `sender` only
        when `from` is absent.
        """
        from_num = data.get('from', 0)
        if from_num:
            return f"!{from_num:08x}"
        return data.get('sender', '')

    def _update_node_from_mqtt(self, data: dict) -> None:
        """Update node tracker from MQTT message data."""
        try:
            from .node_tracker import UnifiedNode

            node_id = self._originator_id(data)
            if not node_id:
                return

            node = UnifiedNode(
                id=node_id,
                name=node_id,
                network="meshtastic",
                meshtastic_id=node_id,
            )
            self.node_tracker.add_node(node)
        except Exception as e:
            logger.debug(f"Error updating node from MQTT: {e}")

    def _update_telemetry(self, data: dict) -> None:
        """Update node with telemetry data from MQTT."""
        try:
            node_id = self._originator_id(data)
            payload = data.get('payload', {})
            if not isinstance(payload, dict) or not node_id:
                return

            # Device metrics
            device = payload.get('device_metrics', {})
            if device:
                logger.debug(f"Telemetry from {node_id}: "
                            f"battery={device.get('battery_level')}%, "
                            f"chUtil={device.get('channel_utilization')}%")

            # Environment metrics
            env = payload.get('environment_metrics', {})
            if env:
                logger.debug(f"Environment from {node_id}: "
                            f"temp={env.get('temperature')}C, "
                            f"humidity={env.get('relative_humidity')}%")
        except Exception as e:
            logger.debug(f"Error processing telemetry: {e}")

    def _update_position(self, data: dict) -> None:
        """Update node position from MQTT for maps."""
        try:
            node_id = self._originator_id(data)
            payload = data.get('payload', {})
            if not isinstance(payload, dict) or not node_id:
                return

            lat = payload.get('latitude_i', 0) / 1e7 if payload.get('latitude_i') else None
            lon = payload.get('longitude_i', 0) / 1e7 if payload.get('longitude_i') else None
            alt = payload.get('altitude')

            if lat and lon:
                logger.debug(f"Position from {node_id}: {lat:.6f}, {lon:.6f}")
                # Node tracker update with position would go here
        except Exception as e:
            logger.debug(f"Error processing position: {e}")

    def _update_nodeinfo(self, data: dict) -> None:
        """Update node info from MQTT."""
        try:
            from .node_tracker import UnifiedNode

            node_id = self._originator_id(data)
            payload = data.get('payload', {})
            if not isinstance(payload, dict) or not node_id:
                return

            long_name = payload.get('longname', '')
            short_name = payload.get('shortname', '')
            hw_model = payload.get('hardware', '')

            node = UnifiedNode(
                id=node_id,
                name=long_name or short_name or node_id,
                network="meshtastic",
                meshtastic_id=node_id,
            )
            self.node_tracker.add_node(node)
            logger.debug(f"NodeInfo from {node_id}: {long_name} ({short_name})")
        except Exception as e:
            logger.debug(f"Error processing nodeinfo: {e}")

    def send_text(self, message: str, destination: str = None, channel: int = 0,
                  msg_id: Optional[str] = None, record_sent: bool = True) -> bool:
        """
        Send a text message to Meshtastic network.

        Primary: HTTP protobuf via /api/v1/toradio (no TCP, no subprocess).
        Fallback: meshtastic CLI (transient subprocess).

        Args:
            message: Text content to send
            destination: Destination node ID (None for broadcast)
            channel: Channel index to send on
            msg_id: delivery_counters id to confirm against the ROUTING_APP
                ACK (queue path passes _queue_msg_id; direct path synthesizes)
            record_sent: record a SENT transition here (direct path); False
                when the persistent queue already owns the SENT (queue path)

        Returns:
            True if message sent successfully, False otherwise.
        """
        message = self._truncate_if_needed(message)

        # Try HTTP protobuf first (preferred — no TCP contention, no subprocess)
        if self._send_via_http_protobuf(message, destination, channel,
                                        msg_id=msg_id, record_sent=record_sent):
            # Dual-path dedup: record broadcast TX so the OTHER path to this
            # radio (mesh_bridge's cross-preset forward of the same content)
            # can suppress its duplicate. This toradio route is the LIVE R→M
            # dispatch path — before 2026-06-04 only meshtastic_handler (the
            # unused TCP path) registered, so relay TXs were invisible to the
            # registry and a second relay copy of the same content (e.g.
            # [MC:p4] wx then [RNS:xxxx] [ch0:p4] wx) always hit RF twice.
            # Registration is unconditional/cheap; suppression is flag-gated
            # at the check side. The registry normalizes bridge tags away.
            if not destination:
                get_rf_tx_registry().register(message)
            return True

        # Fall back to CLI
        logger.debug("HTTP protobuf TX unavailable, falling back to CLI")
        if self._send_via_cli(message, destination, channel):
            if not destination:
                get_rf_tx_registry().register(message)
            return True
        return False

    def _send_via_http_protobuf(
        self, message: str, destination: str = None, channel: int = 0,
        msg_id: Optional[str] = None, record_sent: bool = True,
    ) -> bool:
        """Send text via HTTP protobuf transport (preferred TX path).

        Primary: Stateless direct POST to /api/v1/toradio — NEVER reads
        from /api/v1/fromradio, so the web client at :9443 is never
        starved of delivery ACK packets.

        Fallback: Session-based protobuf client (legacy, only if direct
        send fails).
        """
        # Convert hex node ID string to int (e.g. "!aabbccdd" -> 0xaabbccdd)
        dest_num = None
        if destination:
            dest_num = self._node_id_to_num(destination)

        # Primary: stateless direct send — zero fromradio contention
        try:
            from .meshtastic_protobuf_client import send_text_direct_with_id
            host = self.config.meshtastic.host

            # Use load balancer for port selection if available
            if self._load_balancer and self._load_balancer.state.value != "disabled":
                http_port = self._load_balancer.get_tx_port()
                logger.debug("TX load balancer selected port %d", http_port)
            else:
                http_port = getattr(self.config.meshtastic, 'http_port', 9443) or 9443

            # Capture the minted packet_id so a DM's ROUTING_APP ACK (which
            # rides /e/) can confirm it (Thread-2 step 4 / #74).
            pkt_id = send_text_direct_with_id(
                text=message, host=host, port=http_port,
                destination=dest_num, channel_index=channel)
            if pkt_id is not None:
                self._maybe_register_ack(pkt_id, dest_num, msg_id, record_sent)
                return True
        except Exception as e:
            logger.debug(f"Stateless HTTP protobuf TX failed: {e}")

        # Fallback: session-based send (reads fromradio during connect)
        # Skip fallback when load balancer selected a non-primary port — the
        # session client only connects to the primary radio and would bypass
        # the load balancer's port selection.
        if self._load_balancer and http_port != getattr(
            self.config.meshtastic, 'http_port', 9443
        ):
            logger.debug(
                "Skipping session fallback: load balancer selected port %d", http_port
            )
            return False

        if not _HAS_PROTOBUF_CLIENT:
            return False

        get_protobuf_client = _get_protobuf_client

        try:
            client = get_protobuf_client()

            if not client.is_connected:
                if not client.connect():
                    logger.debug("Protobuf client failed to connect for TX")
                    return False

            return client.send_text(
                text=message,
                destination=dest_num,
                channel_index=channel,
            )
        except Exception as e:
            logger.debug(f"Session-based HTTP protobuf TX failed: {e}")
            return False

    @staticmethod
    def _node_id_to_num(node_id: str) -> Optional[int]:
        """Convert a Meshtastic node ID string to numeric form.

        Args:
            node_id: Node ID like "!aabbccdd" or "0xaabbccdd" or decimal string

        Returns:
            Integer node number, or None if unparseable
        """
        if not node_id:
            return None
        try:
            cleaned = node_id.lstrip('!')
            return int(cleaned, 16)
        except ValueError:
            try:
                return int(node_id)
            except ValueError:
                logger.warning(f"Cannot parse node ID: {node_id}")
                return None

    def _send_via_cli(self, message: str, destination: str = None, channel: int = 0) -> bool:
        """Send text via meshtastic CLI (fallback TX path).

        Spawns a transient CLI process that connects via TCP, sends, exits.
        Works but slower and uses the TCP slot briefly.
        """
        cli = self._find_cli()
        if not cli:
            logger.error("meshtastic CLI not found. Install with: pip install meshtastic")
            return False

        try:
            host = self.config.meshtastic.host
            cmd = [cli, '--host', host, '--sendtext', message]

            if destination:
                cmd.extend(['--dest', destination])
            if channel > 0:
                cmd.extend(['--ch-index', str(channel)])

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                logger.info(f"Sent to Meshtastic via CLI: {message[:50]}...")
                return True
            else:
                logger.warning(f"CLI send failed (rc={result.returncode}): {result.stderr[:200]}")
                return False

        except subprocess.TimeoutExpired:
            logger.error("meshtastic CLI timed out")
            return False
        except FileNotFoundError:
            logger.error(f"meshtastic CLI not found at: {cli}")
            self._cli_path = None  # Reset cache
            return False
        except Exception as e:
            logger.error(f"CLI send failed: {e}")
            return False

    def queue_send(self, payload: Dict) -> bool:
        """
        Send handler for persistent queue - Meshtastic destination.

        Args:
            payload: Dictionary with 'message', 'destination', 'channel' keys

        Returns:
            True if sent successfully, False otherwise.
        """
        message = self._truncate_if_needed(payload.get('message', ''))
        destination = payload.get('destination')
        channel = payload.get('channel', 0)

        # Dispatch-time dual-path dedup re-check (gated, broadcast only).
        # The enqueue-side check (_rns_bridge_xform) races mesh_bridge's
        # RF-TX registration: a LAN-fast RNS relay copy is checked ~250ms
        # BEFORE the serial leg registers its downlink, misses, and queues.
        # By dispatch time — ≥min_spacing_s (3s) later — the registry is
        # settled, so re-checking here closes the race deterministically
        # (observed live 2026-06-04: every bot-reply pair on moc leaked).
        # Both namespaces (Phase 4, review 2026-07-01): the payload carries
        # the logical message's content_id so a TRUE-ORIGIN delivery of the
        # same message — which registers only its cid, never raw text this
        # tagged chunk could match — is recognized here too.
        # Suppress-only-on-hit: return True so the queue marks it done.
        if not destination and dual_path_dedup_enabled(self.config):
            hit = get_rf_tx_registry().seen_namespace_within(
                message, str(payload.get('content_id') or ''),
                dual_path_dedup_window_s(self.config))
            if hit:
                with self._stats_lock:
                    self.stats['dispatch_dedup_suppressed'] = (
                        self.stats.get('dispatch_dedup_suppressed', 0) + 1)
                    if hit == 'cid':
                        # Witness: broker-publish-only evidence (see
                        # _deliver_true_origin) — the loss-exposure meter.
                        self.stats['dispatch_dedup_suppressed_cid_only'] = (
                            self.stats.get(
                                'dispatch_dedup_suppressed_cid_only', 0) + 1)
                logger.info(
                    f"Queue dispatch suppressed (dual-path dedup [{hit}] — "
                    f"already on RF): {message[:50]}...")
                return True

        # Queue owns the SENT transition (mark_delivered on _queue_msg_id);
        # arm ACK confirmation against that same id (record_sent=False).
        return self.send_text(message, destination, channel,
                              msg_id=payload.get('_queue_msg_id'),
                              record_sent=False)

    def test_connection(self) -> bool:
        """Test MQTT broker connectivity."""
        import socket
        mqtt_cfg = self.config.mqtt_bridge
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((mqtt_cfg.broker, mqtt_cfg.port))
            return result == 0
        except (OSError, Exception) as e:
            logger.debug(f"MQTT broker connection test failed: {e}")
            return False
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

    def disconnect(self) -> None:
        """Disconnect from MQTT broker."""
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception as e:
                logger.debug(f"Error disconnecting MQTT: {e}")
        self._connected = False

    def _find_cli(self) -> Optional[str]:
        """Find meshtastic CLI binary (cached)."""
        if self._cli_path:
            return self._cli_path

        import shutil
        path = shutil.which('meshtastic')
        if path:
            self._cli_path = path
            return path

        # Check common locations
        for candidate in [
            '/usr/local/bin/meshtastic',
            '/usr/bin/meshtastic',
            str(self._get_user_bin() / 'meshtastic'),
        ]:
            if self._path_exists(candidate):
                self._cli_path = candidate
                return candidate

        return None

    def _get_user_bin(self):
        """Get user's local bin directory."""
        return _get_real_user_home_fn() / '.local' / 'bin'

    @staticmethod
    def _path_exists(path: str) -> bool:
        """Check if a file exists at path."""
        import os
        return os.path.isfile(path) and os.access(path, os.X_OK)

    def _spill_to_persistent_queue(self, msg) -> bool:
        """Persist a M→R BridgedMessage when the in-memory queue is full.

        Hardening B (Issue #29 derivative): the in-memory queue used to
        silently drop on Full. Now we serialize the BridgedMessage's
        salient fields and enqueue under destination="rns_xform"; the
        bridge's registered "rns_xform" sender (rns_bridge.py) re-runs
        _process_mesh_to_rns on the persisted payload, so the message
        gets a second chance through the proper xform pipeline.

        Returns True on successful persist, False if no persistent queue
        is wired (caller falls through to the dropped counter).
        """
        if not self._persistent_queue:
            return False
        try:
            from gateway.message_queue import MessagePriority
            payload = {
                'source_id': getattr(msg, 'source_id', '') or '',
                'destination_id': getattr(msg, 'destination_id', '') or '',
                'content': msg.content,
                'title': getattr(msg, 'title', None),
                'is_broadcast': bool(getattr(msg, 'is_broadcast', False)),
                # Preserve the logical content_id across the spill→requeue so a
                # queue-full overflow copy stays correlatable (dedup/identity
                # arc STEP 2b; honest #4 — carry the field, don't drop it).
                'content_id': getattr(msg, 'content_id', '') or '',
                'metadata': dict(getattr(msg, 'metadata', None) or {}),
            }
            msg_id = self._persistent_queue.enqueue(
                payload=payload,
                destination="rns_xform",
                priority=MessagePriority.NORMAL,
            )
            return msg_id is not None
        except Exception as e:
            logger.error("Persistent spill failed: %s", e)
            return False

    def _is_duplicate(self, msg_id: str) -> bool:
        """Check if message ID was seen recently (dedup)."""
        now = time.time()
        with self._mqtt_lock:
            if msg_id in self._recent_ids:
                return True
            self._recent_ids[msg_id] = now
        return False

    def _cleanup_dedup(self) -> None:
        """Remove expired entries from dedup cache."""
        now = time.time()
        with self._mqtt_lock:
            expired = [
                k for k, v in self._recent_ids.items()
                if now - v > self._dedup_window
            ]
            for k in expired:
                del self._recent_ids[k]

