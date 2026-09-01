"""
Meshtastic Handler for RNS Bridge.

Manages Meshtastic connection, message handling, and node tracking.
Extracted from rns_bridge.py for maintainability (Issue #6).

Uses dependency injection for shared state:
- config: Gateway configuration
- node_tracker: Unified node tracking
- health: Bridge health monitoring
- stats: Shared statistics dict
- callbacks: Message/status notification
"""

import logging
import subprocess
import threading
import time
from datetime import datetime
from queue import Full
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from gateway import delivery_counters as _dc
from .ack_tracker import (
    AckTracker, parse_routing_ack, routing_error_to_drop_reason,
)
from .base_handler import (
    BaseMessageHandler, dual_path_dedup_enabled, dual_path_dedup_window_s,
    get_rf_tx_registry, is_bridge_loop, mesh_origin_content_id,
    true_origin_downlink_enabled, true_origin_loop_guard_window_s,
)
from .config import GatewayConfig
from .node_tracker import UnifiedNode
from .reconnect import ReconnectStrategy
from utils.meshtastic_connection import (
    clear_stale_connections, get_connection_manager, wait_for_cooldown
)
try:
    from utils.websocket_server import broadcast_message
except ImportError:
    def broadcast_message(*args, **kwargs):
        pass
from utils.boundary_timing import timed_boundary
from utils.tx_guard import (
    DEFAULT_MESH_TCP_PORT, assert_iface_tx_allowed, assert_tx_allowed,
)
from utils.safe_import import safe_import
from utils.service_check import check_service

if TYPE_CHECKING:
    from .bridge_health import BridgeHealthMonitor
    from .node_tracker import UnifiedNodeTracker

logger = logging.getLogger(__name__)

# pubsub is an external dependency (pypubsub) - keep safe_import
pub, _HAS_PUBSUB = safe_import('pubsub', 'pub')


class MeshtasticHandler(BaseMessageHandler):
    """
    Handles Meshtastic connection and message processing.

    This class manages the Meshtastic side of the bridge:
    - Connection establishment and reconnection
    - Message receiving and sending
    - Node tracking updates
    - Health monitoring

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
    """

    def __init__(
        self,
        config: GatewayConfig,
        node_tracker: 'UnifiedNodeTracker',
        health: 'BridgeHealthMonitor',
        stop_event: threading.Event,
        stats: Dict[str, Any],
        stats_lock: threading.Lock,
        message_queue,  # Queue for mesh->rns messages
        message_callback: Optional[Callable] = None,
        status_callback: Optional[Callable] = None,
        should_bridge: Optional[Callable] = None,
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

        # Connection state (handler-specific)
        self._interface = None
        self._conn_manager = None
        self._pubsub_handler = None

        # Reconnection strategy
        self._reconnect = ReconnectStrategy.for_meshtastic()

        # Network topology reference (optional)
        self._network_topology = None

        # Thread-2 step 4 — honest Meshtastic delivery confirmation. In-flight
        # map of a sent DM's packet_id -> its delivery_counters msg_id, so an
        # arriving ROUTING_APP ACK/NAK resolves to CONFIRMED / DROPPED. Survives
        # reconnects (handler instance persists); lost only on a full restart
        # (a still-pending DM stays SENT — honest). Gated by config; inert when
        # off (no DM is ever registered, so the ROUTING_APP branch no-ops).
        self._ack_tracker = AckTracker(
            ttl_sec=getattr(self.config.rns, 'ack_pending_ttl_sec', None),
            max_pending=getattr(self.config.rns, 'ack_pending_max', None),
        )

        # Mesh oracle (read-only "ask dude-AI over the mesh" responder).
        # Default OFF — built only when MESHFORGE_ORACLE_ENABLED is set; inert
        # otherwise (self._oracle stays None and the _handle_text_message hook
        # no-ops). Building it must NEVER break the bridge.
        self._oracle = None
        try:
            self._oracle = self._build_oracle_responder()
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(f"mesh oracle not initialized: {e}")

    def _build_oracle_responder(self):
        """Construct the read-only mesh-oracle responder, or None if disabled.

        Default OFF (opt-in via MESHFORGE_ORACLE_ENABLED) — the env is checked
        BEFORE importing the oracle so a disabled gateway pays no import cost.
        Wires the oracle's injected deps to this handler: a read-only NOC
        snapshot, the existing directed send_text (reply only), and an
        append-only audit log under the operator home. The oracle never controls
        services or mutates config (autonomy rung 1 — report).
        """
        import os
        if str(os.environ.get("MESHFORGE_ORACLE_ENABLED", "")).strip().lower() \
                not in ("1", "true", "yes", "on"):
            return None
        from oracle import fetch_api_status, read_snapshot
        from oracle.responder import MeshOracleResponder

        def _snapshot():
            # Enrich with the local /api/status summary (directory + federation)
            # so `status` reports the real directory + federation counts;
            # degrades to nodes:? on any fetch failure (read-only, never
            # perturbs the radio).
            return read_snapshot(status=fetch_api_status())

        def _send(text: str, dest: str, channel: int) -> bool:
            # Reply as a channel BROADCAST on the inbound channel index, NOT a
            # DM to `dest`: re-emit/relay paths collapse the original sender, so
            # a DM can target the wrong node and miss a remote asker. Broadcasting
            # on the channel reaches every node on it (incl. the real asker) —
            # consistent with the MQTT + MeshCore legs (private-channel model).
            return self.send_text(text, destination=None, channel=channel)

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
                logger.debug(f"mesh oracle log append failed: {e}")

        allowed_channels = self._resolve_oracle_channels(
            os.environ.get("MESHFORGE_ORACLE_CHANNELS", ""))
        return MeshOracleResponder.from_env(
            snapshot_fn=_snapshot, send_fn=_send, log_fn=_log,
            allowed_channels=allowed_channels)

    def _resolve_oracle_channels(self, names_csv: str):
        """Resolve a comma-separated channel-NAME list to local slot indices.

        The oracle hook receives the inbound packet's box-local numeric channel
        index (``packet.get('channel')``); a fleet-stable whitelist is keyed on
        channel NAMES (e.g. ``meshforge``). Resolve each name to THIS box's index
        once at startup via the same live channel-list query the bridge already
        uses to reconcile its TX channel (``_channel_resolver``) — so no new
        PhoneAPI probe pattern is introduced (#17/#75-safe). Names that cannot be
        resolved are logged and skipped (never silently treated as index 0).
        Returns a set of ints (empty when unset / nothing resolved).
        """
        names = [n.strip() for n in (names_csv or "").split(",") if n.strip()]
        if not names:
            return set()
        try:
            from gateway._channel_resolver import resolve_tx_channel_index
        except ImportError as e:  # pragma: no cover - defensive
            logger.warning(f"mesh oracle channel resolver unavailable: {e}")
            return set()
        resolved = set()
        for name in names:
            # fallback=-1 is a sentinel that can never match a real inbound
            # channel index, so an unresolved name contributes nothing.
            idx, status = resolve_tx_channel_index(name, -1)
            if status in ("matches_config", "resolved") and idx >= 0:
                resolved.add(idx)
                logger.info(
                    f"mesh oracle channel {name!r} -> index {idx} ({status})")
            else:
                logger.warning(
                    f"mesh oracle channel {name!r} not resolved ({status}); "
                    f"skipped — nodes on it will not be channel-allowed")
        return resolved

    @property
    def interface(self):
        """Get the Meshtastic interface."""
        return self._interface

    def set_network_topology(self, topology) -> None:
        """Set network topology reference for relay node tracking."""
        self._network_topology = topology

    @property
    def ack_tracker(self) -> AckTracker:
        """In-flight Meshtastic DM ACK tracker (Thread-2 step 4)."""
        return self._ack_tracker

    def _ack_consumption_enabled(self) -> bool:
        """Whether to send DMs wantAck + consume their ROUTING_APP ACKs."""
        try:
            return bool(getattr(
                self.config.rns, 'meshtastic_ack_consumption_enabled', False))
        except Exception:
            return False

    def _register_ack_for_send(self, result, destination, msg_id,
                               record_sent: bool) -> None:
        """Arm ACK confirmation for a just-sent DM (Thread-2 step 4).

        No-op for broadcasts (no per-node ACK exists) and when ack
        consumption is disabled. ``result`` is the meshtastic library's
        returned MeshPacket — its ``.id`` is the packet_id the recipient
        will echo as the ROUTING_APP ``request_id``.

        ``msg_id`` ties CONFIRMED to the SAME id earlier states were
        recorded against. The queue dispatch path passes its
        ``_queue_msg_id`` (QUEUED/SENT already on that id — do NOT
        re-record SENT, the queue owns it). The direct send path has no
        queue lifecycle, so it synthesizes an id and records SENT here so
        the eventual CONFIRMED is not an orphan.
        """
        try:
            if not destination or not self._ack_consumption_enabled():
                return
            packet_id = getattr(result, 'id', None)
            if not isinstance(packet_id, int) or isinstance(packet_id, bool) \
                    or packet_id <= 0:
                return
            if not msg_id:
                msg_id = f"mesh-{packet_id:08x}"
            if record_sent:
                _dc.record(
                    _dc.DeliveryState.SENT,
                    msg_id=msg_id,
                    protocol="meshtastic",
                )
            self._ack_tracker.register(packet_id, msg_id, protocol="meshtastic")
        except Exception as e:
            logger.debug(f"Could not arm ACK tracking: {e}")

    def run_loop(self) -> None:
        """
        Main loop for Meshtastic connection with auto-reconnect.

        Uses ReconnectStrategy for exponential backoff with jitter.
        Records events to BridgeHealthMonitor for metrics.
        """
        while not self._stop_event.is_set():
            try:
                if not self._connected:
                    if not self._reconnect.should_retry():
                        logger.warning("Meshtastic reconnection: max attempts reached, resetting")
                        self._reconnect.reset()
                        self._stop_event.wait(self._reconnect.config.max_delay)
                        continue

                    # After 3 consecutive failures, check for CLOSE-WAIT zombies.
                    # meshtasticd only allows ONE TCP client — a zombie connection
                    # blocks all reconnection. Detect and clear it early (3 attempts
                    # ≈ 7 seconds) instead of waiting for all 10 to exhaust.
                    if self._reconnect.attempts == 3:
                        cleared = clear_stale_connections(self.config.meshtastic.port)
                        if cleared:
                            self.health.record_connection_event(
                                "meshtastic", "self_healed",
                                "Cleared zombie CLOSE-WAIT connection"
                            )
                            self._reconnect.reset()

                    logger.info(f"Attempting Meshtastic connection "
                               f"(attempt {self._reconnect.attempts + 1})...")
                    self.health.record_connection_event("meshtastic", "retry")
                    self.connect()

                    if self._connected:
                        self._reconnect.record_success()
                        self.health.record_connection_event("meshtastic", "connected")
                        logger.info("Meshtastic connection established")
                    else:
                        self._reconnect.record_failure()
                        self._reconnect.wait(self._stop_event)
                        continue

                if self._connected:
                    self._poll()

                self._stop_event.wait(1)

            except (BrokenPipeError, ConnectionResetError, OSError) as e:
                category = self.health.record_error("meshtastic", e)
                logger.warning(f"Meshtastic connection error ({category}): {e}")
                self._handle_connection_lost()
                self.health.record_connection_event("meshtastic", "disconnected", str(e))
                self._reconnect.record_failure()
                self._reconnect.wait(self._stop_event)
            except Exception as e:
                category = self.health.record_error("meshtastic", e)
                logger.error(f"Meshtastic loop error ({category}): {e}")
                self._connected = False
                self.health.record_connection_event("meshtastic", "error", str(e))
                self._reconnect.record_failure()
                self._reconnect.wait(self._stop_event)

    def connect(self) -> bool:
        """
        Connect to Meshtastic via TCP using singleton connection manager.

        Returns:
            True if connection successful, False otherwise.
        """
        if not _HAS_PUBSUB:
            logger.warning("pubsub not available, using CLI fallback")
            self._connected = self._test_cli()
            return self._connected

        # Advisory pre-flight: warn if meshtasticd not detected, but attempt
        # connection anyway — service may be running outside systemd (Docker, manual)
        status = check_service('meshtasticd')
        if not status.available:
            logger.warning("meshtasticd service check: %s (attempting connection anyway)",
                           status.message)
            if status.fix_hint:
                logger.info("Fix: %s", status.fix_hint)

        try:
            host = self.config.meshtastic.host
            port = self.config.meshtastic.port

            logger.info(f"Connecting to Meshtastic at {host}:{port}")

            # Use singleton connection manager to prevent connection conflicts
            # meshtasticd only allows ONE TCP client - this ensures we share
            self._conn_manager = get_connection_manager(host, port)

            # Acquire persistent connection (stays open for message receiving)
            if not self._conn_manager.acquire_persistent(owner="gateway_bridge"):
                logger.error("Could not acquire persistent Meshtastic connection")
                self._connected = False
                return False

            # Get the interface for operations
            self._interface = self._conn_manager.get_interface()

            if self._interface is None:
                logger.error("Failed to get Meshtastic interface from connection manager")
                self._connected = False
                return False

            # Subscribe to messages (store reference for proper unsubscribe)
            def on_receive(packet, interface):
                self._on_receive(packet)

            self._pubsub_handler = on_receive
            pub.subscribe(self._pubsub_handler, "meshtastic.receive")

            # Get initial node list
            self._update_nodes()

            self._connected = True
            logger.info("Connected to Meshtastic via connection manager")
            self._notify_status("meshtastic_connected")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to Meshtastic: {e}")
            self._connected = False
            return False

    def disconnect(self) -> None:
        """Disconnect from Meshtastic via connection manager."""
        # Unsubscribe from pub/sub
        try:
            from pubsub import pub
            if self._pubsub_handler:
                pub.unsubscribe(self._pubsub_handler, "meshtastic.receive")
                self._pubsub_handler = None
        except Exception as e:
            logger.debug(f"Pubsub unsubscribe during disconnect: {e}")

        # Release persistent connection through the manager
        if self._conn_manager:
            try:
                self._conn_manager.release_persistent()
            except Exception as e:
                logger.debug(f"Error releasing persistent connection: {e}")

        self._interface = None
        self._connected = False

    def send_text(self, message: str, destination: str = None, channel: int = 0) -> bool:
        """
        Send a text message to Meshtastic network.

        Args:
            message: Text content to send
            destination: Destination node ID (None for broadcast)
            channel: Channel index to send on

        Returns:
            True if message sent successfully, False otherwise.
        """
        message = self._truncate_if_needed(message)

        if not self._connected:
            logger.warning("Not connected to Meshtastic")
            return False

        try:
            if self._interface:
                # For broadcasts, use ^all instead of None
                dest = destination if destination else "^all"
                # Thread-2 step 4: request an end-to-end ACK for DMs so the
                # recipient's ROUTING_APP reply confirms delivery. Broadcasts
                # get no per-node ACK, so leave wantAck off (firmware default).
                want_ack = self._ack_consumption_enabled() and bool(destination)
                logger.debug(f"Sending to Meshtastic: dest={dest}, ch={channel}, "
                             f"wantAck={want_ack}, msg={message[:50]}")
                assert_iface_tx_allowed(
                    self._interface, kind="tcp_sendtext",
                    detail=f"meshtastic_handler.send_text dest={dest}",
                    default_host=self.config.meshtastic.host)
                with timed_boundary("meshtasticd.send_text", target=str(dest)):
                    result = self._interface.sendText(
                        message,
                        destinationId=dest,
                        channelIndex=channel,
                        wantAck=want_ack,
                    )
                if result is None or result is False:
                    logger.warning(f"sendText returned {result} — TX may have failed "
                                   f"(dest={dest}, ch={channel})")
                    return False
                logger.debug(f"sendText returned: {result}")
                # Direct send path has no queue lifecycle — record SENT + arm
                # ACK confirmation against a synthesized id (record_sent=True).
                self._register_ack_for_send(
                    result, destination, msg_id=None, record_sent=True)
                # Dual-path dedup: record broadcast TX so the OTHER path to
                # this radio (mesh_bridge's cross-preset forward of the same
                # content) can suppress its duplicate. Registration is
                # unconditional/cheap; suppression is flag-gated at the
                # check side. The registry normalizes bridge tags away, so a
                # tagged [RNS:] send matches the raw downlink content.
                if not destination:
                    get_rf_tx_registry().register(message)
                return True
            else:
                # Fallback to CLI
                return self._send_via_cli(message, destination, channel)
        except Exception as e:
            logger.error(f"Failed to send to Meshtastic: {e}")
            with self._stats_lock:
                self.stats['errors'] += 1
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

        # Dispatch-time dual-path dedup re-check (gated, broadcast only) —
        # see MQTTBridgeHandler.queue_send for the race this closes. The
        # enqueue-side check misses when the RNS relay copy arrives before
        # mesh_bridge registers its RF TX; by dispatch time the registry is
        # settled. Both namespaces (Phase 4): the payload's content_id
        # recognizes a true-origin (cid-only) delivery of the same message.
        # Suppress-only-on-hit: True marks the queue entry done.
        if not destination and dual_path_dedup_enabled(self.config):
            hit = get_rf_tx_registry().seen_namespace_within(
                message, str(payload.get('content_id') or ''),
                dual_path_dedup_window_s(self.config))
            if hit:
                with self._stats_lock:
                    self.stats['dispatch_dedup_suppressed'] = (
                        self.stats.get('dispatch_dedup_suppressed', 0) + 1)
                    if hit == 'cid':
                        # Witness: broker-publish-only evidence — the
                        # loss-exposure meter (see _deliver_true_origin).
                        self.stats['dispatch_dedup_suppressed_cid_only'] = (
                            self.stats.get(
                                'dispatch_dedup_suppressed_cid_only', 0) + 1)
                logger.info(
                    f"Queue dispatch suppressed (dual-path dedup [{hit}] — "
                    f"already on RF): {message[:50]}...")
                return True

        if not self._connected:
            return False

        try:
            if self._interface:
                dest = destination if destination else "^all"
                # Thread-2 step 4: wantAck for DMs (see send_text).
                want_ack = self._ack_consumption_enabled() and bool(destination)
                assert_iface_tx_allowed(
                    self._interface, kind="tcp_sendtext",
                    detail=f"meshtastic_handler queue send dest={dest}",
                    default_host=self.config.meshtastic.host)
                with timed_boundary("meshtasticd.send_text", target=str(dest)):
                    result = self._interface.sendText(
                        message, destinationId=dest, channelIndex=channel,
                        wantAck=want_ack)
                if result is None or result is False:
                    logger.warning(f"Queue sendText returned {result} — TX may have failed")
                    return False
                # The queue records SENT via mark_delivered() on the SAME
                # _queue_msg_id; arm ACK confirmation against it (record_sent
                # =False so SENT isn't double-counted). CONFIRMED then joins
                # QUEUED→SENT on one id for end-to-end lifecycle.
                self._register_ack_for_send(
                    result, destination, msg_id=payload.get('_queue_msg_id'),
                    record_sent=False)
                # Dual-path dedup: see send_text — same registration for the
                # persistent-queue dispatch path (the live R→M route).
                if not destination:
                    get_rf_tx_registry().register(message)
                return True
            return False
        except Exception as e:
            logger.error(f"Queue send to Meshtastic failed: {e}")
            return False

    def test_connection(self) -> bool:
        """
        Test Meshtastic connection via TCP socket.

        Returns:
            True if connection test passes, False otherwise.
        """
        sock = None
        try:
            import socket
            host = self.config.meshtastic.host
            port = self.config.meshtastic.port
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            with timed_boundary("meshtasticd.tcp_probe",
                                target=f"{host}:{port}"):
                result = sock.connect_ex((host, port))
            return result == 0
        except (OSError, Exception) as e:
            logger.debug(f"Meshtastic connection test failed: {e}")
            return False
        finally:
            if sock:
                try:
                    sock.close()
                except Exception as e:
                    logger.debug(f"Socket close during cleanup: {e}")

    def _on_receive(self, packet: dict) -> None:
        """Handle incoming Meshtastic message."""
        try:
            decoded = packet.get('decoded', {})
            portnum = decoded.get('portnum')

            # Update node info
            from_id = packet.get('fromId')
            if from_id:
                node = UnifiedNode.from_meshtastic({
                    'num': int(from_id[1:], 16) if from_id.startswith('!') else 0,
                    'snr': packet.get('rxSnr'),
                    'hopsAway': packet.get('hopStart', 0) - packet.get('hopLimit', 0),
                })
                self.node_tracker.add_node(node)

            # Extract relay node (Meshtastic 2.6+)
            relay_node = packet.get('relayNode')
            if relay_node and relay_node > 0:
                self._discover_relay_node(relay_node, from_id, packet)

            # Handle text messages
            if portnum == 'TEXT_MESSAGE_APP':
                self._handle_text_message(packet, decoded, from_id)
            # Thread-2 step 4: a ROUTING_APP packet is the recipient's
            # end-to-end ACK/NAK for one of our wantAck DMs. Consume it so
            # delivery_counters records the honest terminal state (#74).
            elif portnum == 'ROUTING_APP':
                self._handle_routing_ack(decoded)

        except Exception as e:
            logger.error(f"Error processing Meshtastic message: {e}")

    def _handle_routing_ack(self, decoded: dict) -> None:
        """Resolve a ROUTING_APP ACK/NAK to a CONFIRMED / DROPPED transition.

        Inert unless the packet's ``request_id`` matches a DM we sent with
        wantAck (i.e. ack consumption is enabled and we registered it). An
        unmatched routing packet — someone else's, or a duplicate ACK we
        already resolved — is silently ignored. Never raises into the RX
        thread; the bridge's hot path must not break on a counter call.
        """
        try:
            ack = parse_routing_ack(decoded)
            if ack is None:
                return
            resolved = self._ack_tracker.resolve(ack.request_id)
            if resolved is None:
                return
            msg_id, protocol = resolved
            if ack.ok:
                _dc.record(
                    _dc.DeliveryState.CONFIRMED,
                    msg_id=msg_id,
                    protocol=protocol,
                )
                with self._stats_lock:
                    self.stats['mesh_ack_confirmed'] = (
                        self.stats.get('mesh_ack_confirmed', 0) + 1)
                logger.info(
                    f"Meshtastic ACK confirmed delivery of {msg_id} "
                    f"(pkt={ack.request_id:#0x})"
                )
            else:
                reason = routing_error_to_drop_reason(ack.reason)
                _dc.record(
                    _dc.DeliveryState.DROPPED,
                    msg_id=msg_id,
                    protocol=protocol,
                    drop_reason=reason,
                    note=f"meshtastic_nak:{ack.reason}"[:80],
                )
                with self._stats_lock:
                    self.stats['mesh_ack_failed'] = (
                        self.stats.get('mesh_ack_failed', 0) + 1)
                logger.warning(
                    f"Meshtastic NAK for {msg_id} (pkt={ack.request_id:#0x}): "
                    f"{ack.reason} -> {reason.value}"
                )
        except Exception as e:
            logger.debug(f"Error handling ROUTING_APP ack: {e}")

    def _handle_text_message(self, packet: dict, decoded: dict, from_id: str) -> None:
        """Process a text message from Meshtastic."""
        # Import BridgedMessage locally to avoid circular imports
        from .rns_bridge import BridgedMessage

        payload = decoded.get('payload', b'')
        if isinstance(payload, bytes):
            text = payload.decode('utf-8', errors='ignore')
        else:
            text = str(payload)

        # Loop guard: [RNS:xxxx]-tagged content was injected from RNS and is
        # already there — never bridge it back (see is_already_bridged).
        # Content_id augmentation (transport-truth arc Phase 2): when true-
        # origin downlink delivery is enabled, content delivered UNTAGGED as
        # its true mesh origin (tag dropped) is recognized by its registered
        # content_id instead. Channel-agnostic id (matches registration across
        # ingress modes, #77). Flag off: loop_cid '' → is_bridge_loop reduces
        # to is_already_bridged (no behavior change).
        loop_cid = ""
        if true_origin_downlink_enabled(self.config):
            loop_cid = mesh_origin_content_id(from_id, text)
        if is_bridge_loop(
                text, loop_cid,
                registry=get_rf_tx_registry(),
                cid_window_s=true_origin_loop_guard_window_s(self.config)):
            logger.debug(f"Not re-bridging RNS-tagged content (loop guard): {text[:40]}")
            return

        # Cross-box loop guard (transport-truth arc Option A): passed the guard,
        # so this gateway is bridging this broadcast mesh→RNS. Register its
        # content_id so a PEER gateway's untagged true-origin re-injection of the
        # same logical content (heard back on RF) is recognized by the guard
        # above and not re-bridged — the shared-segment case Phase-2's intra-box
        # registry alone can't cover. After the guard (never suppresses the
        # original); only when the flag armed loop_cid (inert off); broadcast-only.
        if loop_cid and packet.get('toId') == '!ffffffff':
            get_rf_tx_registry().register_content_id(loop_cid)

        # Mesh oracle (read-only): answer a query (status/whatsup/...) DIRECTED
        # back to the sender — off-grid, no cloud. Default OFF; never breaks the
        # bridge. Consume only when the leg's consume flag is set — same as the
        # MQTT/MeshCore/RNS legs; previously this leg ALWAYS consumed, silently
        # dead-lettering MESHFORGE_ORACLE_CONSUME=0 (bridge-through) on the
        # PhoneAPI/TCP path.
        if self._oracle is not None:
            try:
                reply = self._oracle.handle(from_id, text,
                                            packet.get('channel', 0))
                if reply is not None and self._oracle.consume:
                    return
            except Exception as e:
                logger.debug(f"mesh oracle handle error: {e}")

        to_id = packet.get('toId')
        msg = BridgedMessage(
            source_network="meshtastic",
            source_id=from_id,
            destination_id=to_id,
            content=text,
            is_broadcast=to_id == '!ffffffff',
            metadata={
                'channel': packet.get('channel', 0),
                'snr': packet.get('rxSnr'),
            }
        )

        # Store incoming message for UI/history
        try:
            from commands import messaging
            # Convert broadcast marker to None
            if to_id == '!ffffffff' or to_id == '^all':
                to_id = None
            messaging.store_incoming(
                from_id=from_id,
                content=text,
                network="meshtastic",
                to_id=to_id,
                channel=packet.get('channel', 0),
                snr=packet.get('rxSnr'),
                rssi=packet.get('rxRssi'),
            )
        except Exception as e:
            logger.debug(f"Could not store incoming message: {e}")

        # Broadcast to WebSocket for real-time web UI updates
        try:
            broadcast_message({
                'from_id': from_id,
                'to_id': to_id,
                'content': text,
                'channel': packet.get('channel', 0),
                'snr': packet.get('rxSnr'),
                'rssi': packet.get('rxRssi'),
                'timestamp': datetime.now().isoformat(),
                'is_broadcast': to_id is None,
            })
        except Exception as e:
            logger.debug(f"Could not broadcast to WebSocket: {e}")

        # Queue for bridging if routing rules allow it (non-blocking to prevent deadlock)
        if self._message_queue is not None:
            # Check routing rules before queueing
            if self._should_bridge and not self._should_bridge(msg):
                logger.debug(f"Message from {from_id} blocked by routing rules")
            else:
                try:
                    self._message_queue.put_nowait(msg)
                except Full:
                    logger.warning("Mesh→RNS queue full, dropping message")
                    with self._stats_lock:
                        self.stats['errors'] += 1

        # Notify callback
        if self._message_callback:
            try:
                self._message_callback(msg)
            except Exception as e:
                logger.error(f"Message callback error: {e}")

    def _discover_relay_node(self, relay_byte: int, from_id: str, packet: dict) -> None:
        """
        Discover relay node from Meshtastic 2.6+ relayNode field.

        The relayNode field only contains the last byte of the relay node's ID.
        We try to match it against known nodes or create a placeholder.
        """
        try:
            if relay_byte <= 0 or relay_byte > 255:
                return

            # Try to find existing node matching this last byte
            for node in self.node_tracker.get_meshtastic_nodes():
                if node.meshtastic_id:
                    try:
                        node_num = int(node.meshtastic_id[1:], 16)
                        if (node_num & 0xFF) == relay_byte:
                            # Found the relay node - update topology
                            if self._network_topology and from_id:
                                self._network_topology.add_edge(
                                    source_id=node.id,
                                    dest_id=from_id,
                                    hops=1,
                                    snr=packet.get('rxSnr'),
                                    rssi=packet.get('rxRssi'),
                                )
                            logger.debug(f"Relay path: {node.meshtastic_id} -> {from_id}")
                            return
                    except (ValueError, TypeError):
                        continue

            # No match - create partial relay node for tracking
            partial_id = f"!????{relay_byte:02x}"
            node = UnifiedNode(
                id=partial_id,
                name=f"Relay-{relay_byte:02x}",
                network="meshtastic",
                meshtastic_id=partial_id,
            )
            self.node_tracker.add_node(node)

            # Add topology edge from unknown relay to sender
            if self._network_topology and from_id:
                self._network_topology.add_edge(
                    source_id=partial_id,
                    dest_id=from_id,
                    hops=1,
                    snr=packet.get('rxSnr'),
                    rssi=packet.get('rxRssi'),
                )

            logger.info(f"Discovered relay node via packet routing: {partial_id}")

        except Exception as e:
            logger.debug(f"Error discovering relay node: {e}")

    def _poll(self) -> None:
        """Poll Meshtastic for health check and updates."""
        if self._interface:
            try:
                # Check if interface is still connected
                if hasattr(self._interface, 'isConnected'):
                    if not self._interface.isConnected:
                        logger.warning("Meshtastic connection lost (isConnected=False)")
                        self._handle_connection_lost()
                        return
                # Also check if we can access basic properties (catches broken pipes)
                if hasattr(self._interface, 'nodes'):
                    _ = len(self._interface.nodes)
            except (BrokenPipeError, ConnectionResetError, OSError) as e:
                logger.warning(f"Meshtastic connection lost: {e}")
                self._handle_connection_lost()
                return
            except Exception as e:
                logger.debug(f"Meshtastic health check error: {e}")

    def _handle_connection_lost(self) -> None:
        """Handle lost meshtastic connection - cleanup and prepare for reconnect."""
        logger.info("Handling lost Meshtastic connection...")
        self._connected = False

        # Release the persistent connection properly
        if self._conn_manager:
            try:
                self._conn_manager.release_persistent()
            except Exception as e:
                logger.debug(f"Error releasing connection after loss: {e}")

        # Unsubscribe from pub/sub to avoid stale callbacks
        try:
            from pubsub import pub
            if self._pubsub_handler:
                pub.unsubscribe(self._pubsub_handler, "meshtastic.receive")
                self._pubsub_handler = None
        except Exception as e:
            logger.debug(f"Pubsub unsubscribe error: {e}")

        self._interface = None
        self._notify_status("meshtastic_disconnected")

        # Wait for cooldown before reconnect attempt
        wait_for_cooldown()

    def _update_nodes(self) -> None:
        """Update node tracker with Meshtastic nodes."""
        if not self._interface:
            return

        try:
            my_info = self._interface.getMyNodeInfo()
            my_id = my_info.get('num', 0)

            for node_id, node_data in self._interface.nodes.items():
                is_local = node_data.get('num') == my_id
                node = UnifiedNode.from_meshtastic(node_data, is_local=is_local)
                self.node_tracker.add_node(node)

        except Exception as e:
            logger.error(f"Error updating Meshtastic nodes: {e}")

    def _test_cli(self) -> bool:
        """Test Meshtastic CLI availability."""
        try:
            from utils.cli import find_meshtastic_cli
            cli_path = find_meshtastic_cli()
            if not cli_path:
                logger.debug("Meshtastic CLI not found")
                return False

            # CLI shells out to meshtasticd; subprocess timeout is 10s
            with timed_boundary("meshtasticd.cli_info", threshold_s=10.0):
                result = subprocess.run(
                    [cli_path, '--info'],
                    capture_output=True,
                    timeout=10
                )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError) as e:
            logger.debug(f"Meshtastic CLI test failed: {e}")
            return False

    def _send_via_cli(self, message: str, destination: str = None, channel: int = 0) -> bool:
        """Send via Meshtastic CLI as fallback."""
        # RF egress chokepoint — outside the try, so a refusal cannot be
        # absorbed into the "CLI send failed" path. Subprocess egress is
        # invisible to any in-process socket tripwire.
        assert_tx_allowed(self.config.meshtastic.host, DEFAULT_MESH_TCP_PORT,
                          kind="meshtastic_cli",
                          detail=f"meshtastic_handler._send_via_cli text={message[:40]!r}")
        try:
            from utils.cli import find_meshtastic_cli
            cli_path = find_meshtastic_cli() or 'meshtastic'
            cmd = [cli_path, '--host', self.config.meshtastic.host, '--sendtext', message]
            if destination:
                cmd.extend(['--dest', destination])
            if channel > 0:
                cmd.extend(['--ch-index', str(channel)])

            # CLI fallback shells out to meshtasticd; subprocess timeout is 30s
            with timed_boundary("meshtasticd.cli_send",
                                target=destination or "broadcast",
                                threshold_s=30.0):
                result = subprocess.run(cmd, capture_output=True, timeout=30)
            return result.returncode == 0
        except Exception as e:
            logger.error(f"CLI send failed: {e}")
            return False

