"""
MeshCore Handler for Gateway Bridge.

Manages MeshCore companion radio connection, message handling, and node tracking
via the meshcore_py library (async, event-driven).

Uses the same dependency injection pattern as MeshtasticHandler:
- config: Gateway configuration
- node_tracker: Unified node tracking
- health: Bridge health monitoring
- stats: Shared statistics dict
- callbacks: Message/status notification

Connection methods (meshcore-cli / meshcore_py):
- Serial (USB): Direct USB to companion radio (e.g. /dev/ttyUSB1 @ 115200)
- TCP/IP:       Network connection to companion radio on WiFi firmware
                or a serial-to-TCP bridge (default port 4000)
- BLE:          Bluetooth LE to companion radio (config ready, handler
                pending meshcore_py BLE transport support)

Typical gateway setup uses two radios on the same host:
  Meshtastic radio  -->  meshtasticd (USB)  -->  TCP :4403  -->  MeshForge
  MeshCore radio    -->  USB serial or TCP  ------------------>  MeshForge

MeshCore differences from Meshtastic:
- No daemon (MeshForge connects directly via meshcore_py)
- Async API (wrapped in dedicated asyncio event loop thread)
- Pure radio (no MQTT/internet origin — all messages are radio)
- Up to 64 hops (vs 7 for Meshtastic)
- Max text payload: ~160 bytes

Requires: pip install meshcore (Python 3.10+)
"""

import asyncio
import logging
import threading
import time
from datetime import datetime
from queue import Empty, Full, Queue
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from .base_handler import BaseMessageHandler
from .canonical_message import CanonicalMessage, Protocol
from .meshcore_channel_path import ChannelPath
from .config import GatewayConfig
from .reconnect import ReconnectConfig, ReconnectStrategy
from utils.safe_import import safe_import
from utils.tx_guard import TransmitBlocked

if TYPE_CHECKING:
    from .bridge_health import BridgeHealthMonitor
    from .node_tracker import UnifiedNodeTracker

logger = logging.getLogger(__name__)

# meshcore_py is an optional external dependency
_meshcore_mod, _HAS_MESHCORE = safe_import('meshcore')


def detect_meshcore_devices() -> List[str]:
    """
    Scan for potential MeshCore companion radio serial devices.

    Returns list of device paths (e.g., ['/dev/ttyUSB0', '/dev/ttyACM1']).
    Does NOT verify that the device is actually running MeshCore firmware.
    """
    import glob
    devices = []
    for pattern in ['/dev/ttyUSB*', '/dev/ttyACM*']:
        devices.extend(sorted(glob.glob(pattern)))
    return devices


class MeshCoreSimulator:
    """
    Simulates MeshCore companion radio for testing without hardware.

    Generates fake events at configurable intervals so the bridge loop
    and routing can be tested end-to-end without a physical radio.
    """

    def __init__(self):
        self._running = False
        self._subscribers: Dict[str, List[Callable]] = {}
        self._contacts = self._generate_fake_contacts()

    def _generate_fake_contacts(self) -> List[Dict[str, Any]]:
        """Generate fake MeshCore contacts for simulation."""
        return [
            {
                'adv_name': 'SimNode-Alpha',
                'public_key': b'\x01\x02\x03\x04\x05\x06',
                'last_seen': datetime.now(),
            },
            {
                'adv_name': 'SimNode-Bravo',
                'public_key': b'\x0a\x0b\x0c\x0d\x0e\x0f',
                'last_seen': datetime.now(),
            },
            {
                'adv_name': 'SimRepeater-01',
                'public_key': b'\xaa\xbb\xcc\xdd\xee\xff',
                'last_seen': datetime.now(),
                'role': 'repeater',
            },
        ]

    def subscribe(self, event_type: str, handler: Callable) -> None:
        """Subscribe to simulated events."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)

    async def start(self):
        """Start generating simulated events."""
        self._running = True
        logger.info("MeshCore simulator started")

    async def stop(self):
        """Stop the simulator."""
        self._running = False

    async def get_contacts(self) -> List[Dict]:
        """Return simulated contacts."""
        return self._contacts

    async def send_msg(self, contact: Any, text: str) -> bool:
        """Simulate sending a message."""
        logger.info(f"[SIM] MeshCore TX: {text[:50]}")
        return True

    async def send_channel_txt_msg(self, text: str) -> bool:
        """Simulate sending a channel broadcast."""
        logger.info(f"[SIM] MeshCore channel TX: {text[:50]}")
        return True


class MeshCoreHandler(BaseMessageHandler):
    """
    Handles MeshCore companion radio connection and message processing.

    Wraps the async meshcore_py library in a threaded interface compatible
    with the gateway bridge's synchronous handler pattern.

    Args:
        config: Gateway configuration object
        node_tracker: Unified node tracker instance
        health: Bridge health monitor instance
        stop_event: Threading event for graceful shutdown
        stats: Shared statistics dictionary
        stats_lock: Lock for thread-safe stats updates
        message_queue: Queue for messages to be bridged
        message_callback: Callback for received messages
        status_callback: Callback for status changes
        should_bridge: Callback to check routing rules
    """

    def __init__(
        self,
        config: GatewayConfig,
        node_tracker: 'UnifiedNodeTracker',
        health: 'BridgeHealthMonitor',
        stop_event: threading.Event,
        stats: Dict[str, Any],
        stats_lock: threading.Lock,
        message_queue,  # Queue for meshcore->bridge messages
        message_callback: Optional[Callable] = None,
        status_callback: Optional[Callable] = None,
        should_bridge: Optional[Callable] = None,
        identity_binder=None,  # Theme-A step 2: optional IdentityBinder
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

        # Theme-A step 2: cross-protocol identity SSOT (population only —
        # MeshCore routing legs are a later step). None keeps standalone /
        # test construction fully inert.
        self._identity_binder = identity_binder

        # Connection state (handler-specific)
        self._meshcore = None  # meshcore_py MeshCore instance or simulator
        self._loop = None      # Dedicated asyncio event loop
        self._subscriptions = []

        # Outbound message queue (bridge loop → MeshCore handler)
        self._send_queue: Queue = Queue(maxsize=100)

        # Reconnection strategy
        self._reconnect = ReconnectStrategy(
            config=ReconnectConfig(
                initial_delay=2.0,
                max_delay=60.0,
                multiplier=2.0,
                jitter=0.15,
                max_attempts=10,
            )
        )

        # Simulation mode detection
        meshcore_config = getattr(config, 'meshcore', None)
        self._simulation_mode = (
            not _HAS_MESHCORE
            or (meshcore_config and getattr(meshcore_config, 'simulation_mode', False))
        )

        # Dual-path channel state, inbound source-channel policy and
        # metrics live in ONE owner (2026-09-18). Nine attributes used to sit
        # here while the code consuming them lived hundreds of lines away in
        # two async legs — a reader/writer pair split across a boundary
        # nothing enforced (honest_failure_modes #4). See
        # gateway/meshcore_channel_path.py for why this is a collaborator
        # rather than a mixin, and for what deliberately did NOT move.
        self._channel_path = ChannelPath(meshcore_config)

        # Mesh oracle (read-only "ask dude-AI over MeshCore" responder).
        # Default OFF — built only when MESHFORGE_ORACLE_ENABLED is set; inert
        # otherwise (self._oracle stays None and the RX hooks are no-ops).
        # MeshAnchor-environment access: a known sender (MESHFORGE_ORACLE_
        # MESHCORE_ALLOWLIST) OR a whitelisted channel (MESHFORGE_ORACLE_
        # MESHCORE_CHANNELS), additive — see _build_meshcore_oracle_responder.
        self._oracle = None
        # A BUILD FAILURE must not be indistinguishable from "off by
        # design": both leave self._oracle None, so without this witness
        # the posture surface (utils.meshcore_status_api) would report a
        # broken oracle as a deliberately disabled one (honest_failure_modes
        # #1 and #9 — the swallow leaves something a reader can see).
        self._oracle_error = None
        try:
            self._oracle = self._build_meshcore_oracle_responder()
        except Exception as e:  # pragma: no cover - never break handler init
            self._oracle_error = f"{type(e).__name__}: {e}"
            logger.warning(f"meshcore oracle FAILED to build: {e}")

    def _build_meshcore_oracle_responder(self):
        """Construct the read-only MeshCore oracle responder, or None if disabled.

        Default OFF (opt-in via MESHFORGE_ORACLE_ENABLED, shared across legs) —
        the env is checked BEFORE importing the oracle so a disabled gateway pays
        no import cost. Mirrors the Meshtastic/RNS legs: a read-only NOC snapshot,
        the existing directed send_text (reply only), and an append-only audit log
        under the operator home. Access is additive — a known sender
        (MESHFORGE_ORACLE_MESHCORE_ALLOWLIST, keyed on the MeshCore source_address
        / pubkey-prefix / adv_name) OR a whitelisted channel index
        (MESHFORGE_ORACLE_MESHCORE_CHANNELS — MeshCore channels are numeric
        indices, so no name resolution). The oracle never controls services or
        mutates config (autonomy rung 1 — report).
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
            # dm_only: an oracle reply is a DIRECTED answer — if the asker
            # isn't a known contact it must be DROPPED, never fall through
            # to a channel broadcast (the "broadcast is not auto-answered"
            # rail; a stranger on a whitelisted channel would otherwise get
            # the whole channel spammed with a reply meant only for them).
            return self.send_text(text, destination=dest,
                                  channel=channel or 0, dm_only=True)

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
                logger.debug(f"mesh oracle (meshcore) log append failed: {e}")

        allowed_channels = set()
        for tok in os.environ.get(
                "MESHFORGE_ORACLE_MESHCORE_CHANNELS", "").split(","):
            tok = tok.strip()
            if not tok:
                continue
            try:
                allowed_channels.add(int(tok))
            except ValueError:
                logger.warning(
                    f"mesh oracle meshcore channel {tok!r} not an int; skipped")

        return MeshOracleResponder.from_env(
            snapshot_fn=_snapshot, send_fn=_send, log_fn=_log,
            transport="meshcore",
            allowlist_env="MESHFORGE_ORACLE_MESHCORE_ALLOWLIST",
            allowed_channels=allowed_channels)

    def connect(self) -> bool:
        """MeshCore connection is managed by run_loop() via async _connect()."""
        logger.warning("MeshCoreHandler.connect() called directly; use run_loop()")
        return False

    def run_loop(self) -> None:
        """
        Main loop for MeshCore connection.

        Creates a dedicated asyncio event loop in this thread and runs
        the async connection/event handler until stop_event is set.
        """
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_run())
        except Exception as e:
            logger.error(f"MeshCore event loop error: {e}")
        finally:
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            except Exception as e:
                logger.debug(f"Async cleanup error: {e}")
            self._loop.close()
            self._loop = None

    async def _async_run(self) -> None:
        """
        Async main loop with auto-reconnect.

        Manages connection lifecycle, event subscription, and outbound
        message processing. Uses ReconnectStrategy for backoff.
        """
        while not self._stop_event.is_set():
            try:
                if not self._connected:
                    if not self._reconnect.should_retry():
                        logger.warning("MeshCore reconnection: max attempts reached, resetting")
                        self._reconnect.reset()
                        await self._async_wait(self._reconnect.config.max_delay)
                        continue

                    logger.info(
                        f"Attempting MeshCore connection "
                        f"(attempt {self._reconnect.attempts + 1})"
                    )
                    self.health.record_connection_event("meshcore", "retry")
                    await self._connect()

                    if self._connected:
                        self._reconnect.record_success()
                        self.health.record_connection_event("meshcore", "connected")
                        logger.info("MeshCore connection established")
                        self._notify_status("meshcore_connected")
                    else:
                        self._reconnect.record_failure()
                        delay = self._reconnect.get_delay()
                        await self._async_wait(delay)
                        continue

                # Connected — process events and outbound messages
                if self._connected:
                    await self._process_outbound()
                    await self._poll_channel_messages()

                await self._async_wait(0.1)

            except (OSError, ConnectionError) as e:
                category = self.health.record_error("meshcore", e)
                logger.warning(f"MeshCore connection error ({category}): {e}")
                await self._handle_disconnection(str(e))
                self._reconnect.record_failure()
                delay = self._reconnect.get_delay()
                await self._async_wait(delay)
            except Exception as e:
                category = self.health.record_error("meshcore", e)
                logger.error(f"MeshCore loop error ({category}): {e}")
                self._connected = False
                self.health.record_connection_event("meshcore", "error", str(e))
                self._reconnect.record_failure()
                delay = self._reconnect.get_delay()
                await self._async_wait(delay)

    async def _connect(self) -> None:
        """Connect to MeshCore companion radio or start simulator."""
        try:
            if self._simulation_mode:
                logger.info("MeshCore: starting in simulation mode")
                self._meshcore = MeshCoreSimulator()
                await self._meshcore.start()
                self._setup_simulator_events()
                self._connected = True
                return

            # Real connection via meshcore_py
            if not _HAS_MESHCORE:
                logger.error("meshcore_py not installed (pip install meshcore)")
                return

            meshcore_config = getattr(self.config, 'meshcore', None)
            if not meshcore_config:
                logger.error("MeshCore configuration not found")
                return

            MeshCore = _meshcore_mod.MeshCore
            conn_type = getattr(meshcore_config, 'connection_type', 'serial')
            device_path = getattr(meshcore_config, 'device_path', '/dev/ttyUSB1')
            baud_rate = getattr(meshcore_config, 'baud_rate', 115200)

            # RF egress attach backstop (2026-08-09 review): the companion is
            # a REAL LoRa radio on serial/TCP — the socket tripwire cannot see
            # serial and the meshtastic sweeps do not cover these methods.
            # Under pytest, refuse the live attach unless the test declared
            # tx_guard.allow_meshcore_egress(); staying disconnected is the
            # same path a box with no companion radio takes.
            from utils.tx_guard import (
                meshcore_attach_allowed, note_meshcore_attach_blocked,
            )
            if not meshcore_attach_allowed():
                note_meshcore_attach_blocked(f"{conn_type}:{device_path}")
                return

            if conn_type == 'serial':
                logger.info(f"Connecting to MeshCore via serial: {device_path}")
                self._meshcore = await MeshCore.create_serial(
                    device_path, baud_rate
                )
            elif conn_type == 'tcp':
                tcp_host = getattr(meshcore_config, 'tcp_host', 'localhost')
                tcp_port = getattr(meshcore_config, 'tcp_port', 4000)
                logger.info(f"Connecting to MeshCore via TCP: {tcp_host}:{tcp_port}")
                self._meshcore = await MeshCore.create_tcp(tcp_host, tcp_port)
            else:
                logger.error(f"Unsupported MeshCore connection type: {conn_type}")
                return

            # Subscribe to events
            self._subscribe_events()

            # Start auto-fetching messages
            if getattr(meshcore_config, 'auto_fetch_messages', True):
                await self._meshcore.start_auto_message_fetching()

            self._connected = True

        except Exception as e:
            logger.error(f"Failed to connect to MeshCore: {e}")
            self._connected = False

    def _subscribe_events(self) -> None:
        """Subscribe to meshcore_py events for message and node tracking."""
        if not self._meshcore:
            return

        try:
            EventType = _meshcore_mod.EventType

            # Direct messages
            sub = self._meshcore.subscribe(
                EventType.CONTACT_MSG_RECV, self._on_contact_message
            )
            self._subscriptions.append(sub)

            # Channel messages
            sub = self._meshcore.subscribe(
                EventType.CHANNEL_MSG_RECV, self._on_channel_message
            )
            self._subscriptions.append(sub)

            # Node advertisements
            sub = self._meshcore.subscribe(
                EventType.ADVERTISEMENT, self._on_advertisement
            )
            self._subscriptions.append(sub)

            # Delivery confirmations
            sub = self._meshcore.subscribe(
                EventType.ACK, self._on_ack
            )
            self._subscriptions.append(sub)

            logger.debug("MeshCore event subscriptions registered")

        except Exception as e:
            logger.error(f"Failed to subscribe to MeshCore events: {e}")

    def _setup_simulator_events(self) -> None:
        """Register event handlers with the simulator."""
        sim = self._meshcore
        if isinstance(sim, MeshCoreSimulator):
            sim.subscribe('CONTACT_MSG_RECV', self._on_contact_message)
            sim.subscribe('CHANNEL_MSG_RECV', self._on_channel_message)
            sim.subscribe('ADVERTISEMENT', self._on_advertisement)

    async def _on_contact_message(self, event: Any) -> None:
        """Handle incoming MeshCore direct message."""
        try:
            msg = CanonicalMessage.from_meshcore(event)

            # Mesh oracle (read-only): answer a query DIRECTED back to the
            # sender; a handled query is consumed (NOT bridged onward) when
            # consume=True (default), or bridged-through when consume=False. A DM
            # has no channel, so it is identity-gated only (channel=None) — the
            # MESHFORGE_ORACLE_MESHCORE_ALLOWLIST or answer-all grants it.
            if self._oracle is not None:
                try:
                    reply = self._oracle.handle(msg.source_address, msg.content, None)
                    if reply is not None and self._oracle.consume:
                        return
                except Exception as e:
                    logger.debug(f"meshcore oracle handle error: {e}")

            # Check routing rules
            if self._should_bridge and not self._should_bridge(msg):
                logger.debug(f"MeshCore message blocked by routing rules")
                return

            # Queue for bridge
            if self._message_queue is not None:
                try:
                    self._message_queue.put_nowait(msg)
                    with self._stats_lock:
                        self.stats.setdefault('meshcore_rx', 0)
                        self.stats['meshcore_rx'] += 1
                except Full:
                    logger.warning("MeshCore→bridge queue full, dropping message")
                    with self._stats_lock:
                        self.stats.setdefault('errors', 0)
                        self.stats['errors'] += 1

            # Notify callback
            if self._message_callback:
                try:
                    self._message_callback(msg)
                except Exception as e:
                    logger.error(f"Message callback error: {e}")

        except Exception as e:
            logger.error(f"Error processing MeshCore direct message: {e}")

    def _disclose_channel_ingress(self, event: Any, msg: Any) -> None:
        """INGRESS disclosure — the slot INDEX as the wire delivered it.

        Born 2026-09-18: for months every channel message read as slot 0
        because ``from_meshcore`` read a key the library never sends, and
        nothing at ingress ever said what the payload carried. This is the
        witness that would have caught it: one INFO line per channel message
        naming the index (``None`` = the payload named no slot — unknown,
        NOT Public), and, for the first few messages of a process, the
        payload's actual keys so the wire shape is captured in the journal
        rather than assumed from a fixture.
        """
        try:
            payload = getattr(event, 'payload', None)
            idx = (msg.metadata or {}).get('channel')
            text = msg.content or ''
            n = getattr(self, '_ingress_keys_logged', 0)
            if n < 3:
                self._ingress_keys_logged = n + 1
                keys = (sorted(payload.keys()) if isinstance(payload, dict)
                        else type(payload).__name__)
                logger.info(
                    f"MeshCore channel rx idx={idx} keys={keys} "
                    f"text={text[:40]!r}")
            else:
                logger.info(f"MeshCore channel rx idx={idx} text={text[:40]!r}")
        except Exception as e:  # disclosure must never break ingress
            logger.debug(f"channel ingress disclosure failed: {e}")

    async def _on_channel_message(self, event: Any) -> None:
        """Handle incoming MeshCore channel (broadcast) message via event."""
        try:
            msg = CanonicalMessage.from_meshcore(event)
            msg.is_broadcast = True
            self._disclose_channel_ingress(event, msg)

            # Track for dual-path reconciliation BEFORE the oracle may
            # consume: registering the hash first means a consumed query is
            # marked event-seen, so _poll_channel_messages won't count it
            # 'event_missed' and re-bridge the very message the oracle took
            # off the wire (the consume invariant — a consumed query must
            # never reach the far mesh).
            content_hash = self._channel_path.compute_hash(msg)
            is_poll_dup = self._channel_path.record_event(
                content_hash, time.monotonic())

            # Mesh oracle (read-only): a query on a whitelisted channel is
            # answered DIRECTED back to the asker (a DM, never a channel
            # broadcast — honors the "broadcast is not auto-answered" rail) and
            # consumed (NOT bridged) when consume=True (default), or
            # bridged-through when consume=False. Channel-gated via
            # MESHFORGE_ORACLE_MESHCORE_CHANNELS; per-sender cooldown also dedups
            # the dual-path (event vs poll) delivery of the same query.
            if self._oracle is not None:
                try:
                    # Absent is UNKNOWN, never Public. This read carried a
                    # `, 0` default that was DEAD — from_meshcore always sets
                    # metadata['channel'], to None when the wire named no slot
                    # (2026-09-18 cure) — so it never fired. Dropped anyway:
                    # the safety of this line rested entirely on that coupling,
                    # and if the key ever goes missing the default would fold
                    # unknown into Public one frame before
                    # responder._allowed's `channel is not None` can refuse it.
                    # The oracle runs BEFORE _channel_path.bridge_allowed and
                    # may return-consume, so it gets first look at unslotted
                    # traffic — no downstream gate would catch it.
                    chan = (msg.metadata or {}).get('channel')
                    reply = self._oracle.handle(msg.source_address, msg.content, chan)
                    if reply is not None and self._oracle.consume:
                        return
                except Exception as e:
                    logger.debug(f"meshcore oracle (channel) handle error: {e}")

            if is_poll_dup:
                logger.debug("Channel message already delivered via poll, skipping event path")
                return

            if not self._channel_path.bridge_allowed(msg):
                self._channel_path.note_suppressed(msg)
                return

            if self._should_bridge and not self._should_bridge(msg):
                logger.debug("MeshCore channel message blocked by routing rules")
                return

            if self._message_queue is not None:
                try:
                    self._message_queue.put_nowait(msg)
                    with self._stats_lock:
                        self.stats.setdefault('meshcore_rx', 0)
                        self.stats['meshcore_rx'] += 1
                except Full:
                    logger.warning("MeshCore→bridge queue full, dropping channel message")

            if self._message_callback:
                try:
                    self._message_callback(msg)
                except Exception as e:
                    logger.error(f"Channel message callback error: {e}")

        except Exception as e:
            logger.error(f"Error processing MeshCore channel message: {e}")

    async def _on_advertisement(self, event: Any) -> None:
        """Handle MeshCore node advertisement (discovery)."""
        try:
            from .node_tracker import UnifiedNode

            payload = getattr(event, 'payload', None)
            if not payload:
                return

            # Extract node info from advertisement
            adv_name = ''
            pubkey = ''
            if isinstance(payload, dict):
                adv_name = payload.get('adv_name', '') or payload.get('name', '')
                pubkey = payload.get('pubkey_prefix', '') or payload.get('public_key', '')
            else:
                adv_name = getattr(payload, 'adv_name', '') or getattr(payload, 'name', '')
                raw_key = getattr(payload, 'public_key', b'')
                if isinstance(raw_key, bytes):
                    pubkey = raw_key.hex()[:12]
                else:
                    pubkey = str(raw_key)[:12] if raw_key else ''

            if not pubkey:
                return

            node_id = f"meshcore:{pubkey}"
            node = UnifiedNode(
                id=node_id,
                name=adv_name or f"MC-{pubkey[:6]}",
                network="meshcore",
            )

            # Set optional fields if available
            if hasattr(node, 'meshcore_pubkey'):
                node.meshcore_pubkey = pubkey

            self.node_tracker.add_node(node)
            logger.debug(f"MeshCore node discovered: {adv_name} ({pubkey[:8]})")

            # Theme-A step 2: populate the identity SSOT from the
            # advertisement (gated + throttled; population only, no
            # MeshCore routing this step).
            rns_cfg = getattr(self.config, 'rns', None)
            if (self._identity_binder is not None
                    and getattr(rns_cfg, 'cross_protocol_identity_enabled',
                                False) is True):
                try:
                    self._identity_binder.populate(
                        'meshcore', pubkey.lower(), adv_name)
                except Exception as e:
                    logger.debug(f"identity populate (meshcore) failed: {e}")

        except Exception as e:
            logger.error(f"Error processing MeshCore advertisement: {e}")

    async def _on_ack(self, event: Any) -> None:
        """Handle MeshCore delivery acknowledgment."""
        try:
            payload = getattr(event, 'payload', {})
            logger.debug(f"MeshCore ACK received: {payload}")
            with self._stats_lock:
                self.stats.setdefault('meshcore_acks', 0)
                self.stats['meshcore_acks'] += 1
        except Exception as e:
            logger.debug(f"Error processing MeshCore ACK: {e}")

    async def _poll_channel_messages(self) -> None:
        """
        Dual-path polling for channel messages.

        meshcore_py CHANNEL_MSG_RECV events sometimes don't fire (#1232).
        This method actively polls for new messages and reconciles with the
        event subscription path. Metrics track when events fire vs when
        polling catches them, providing data for upstream bug analysis.
        """
        now = time.monotonic()
        if not self._channel_path.poll_due(now):
            return
        self._channel_path.mark_polled(now)

        if not self._meshcore or not self._connected:
            return

        # Only poll in real mode (not simulation)
        if self._simulation_mode:
            return

        poll_cycle = self._channel_path.begin_poll_cycle()

        try:
            if not hasattr(self._meshcore, 'commands'):
                return

            # Retrieve any pending channel messages
            messages = []
            if hasattr(self._meshcore.commands, 'get_channel_messages'):
                messages = await self._meshcore.commands.get_channel_messages()
            elif hasattr(self._meshcore.commands, 'get_messages'):
                messages = await self._meshcore.commands.get_messages()

            if not messages:
                # Periodic metric logging
                if poll_cycle % self._channel_path.metrics_log_interval == 0:
                    self._log_channel_metrics()
                return

            for raw_msg in messages:
                try:
                    msg = CanonicalMessage.from_meshcore(raw_msg)
                    msg.is_broadcast = True

                    content_hash = self._channel_path.compute_hash(msg)
                    is_event_dup = self._channel_path.record_poll(
                        content_hash, now)

                    if is_event_dup:
                        continue  # Already processed via event path

                    # Process the message (event path missed it)
                    logger.debug(
                        f"Poll discovered channel message missed by event: "
                        f"{msg.content[:30]}..."
                    )

                    if not self._channel_path.bridge_allowed(msg):
                        self._channel_path.note_suppressed(msg)
                        continue

                    if self._should_bridge and not self._should_bridge(msg):
                        continue

                    if self._message_queue is not None:
                        try:
                            self._message_queue.put_nowait(msg)
                            with self._stats_lock:
                                self.stats.setdefault('meshcore_rx', 0)
                                self.stats['meshcore_rx'] += 1
                        except Full:
                            logger.warning("MeshCore→bridge queue full (poll)")

                    if self._message_callback:
                        try:
                            self._message_callback(msg)
                        except Exception as e:
                            logger.error(f"Poll message callback error: {e}")

                except Exception as e:
                    logger.debug(f"Error processing polled channel message: {e}")

        except Exception as e:
            logger.debug(f"Channel poll error: {e}")

        # Cleanup old hash entries
        self._cleanup_channel_hashes()

        # Periodic metric logging
        if poll_cycle % self._channel_path.metrics_log_interval == 0:
            self._log_channel_metrics()

    # The dual-path channel state now lives in ChannelPath. These stay as
    # thin delegates because callers outside this file (and the existing
    # test suite) reach for them by these names.
    def _compute_channel_hash(self, msg: CanonicalMessage) -> str:
        """Compute content hash for channel message dedup across paths."""
        return self._channel_path.compute_hash(msg)

    def _cleanup_channel_hashes(self) -> None:
        """Remove expired entries from dual-path hash maps."""
        self._channel_path.cleanup()

    def _log_channel_metrics(self) -> None:
        """Log periodic summary of channel message dual-path metrics."""
        self._channel_path.log_metrics()

    def get_channel_metrics(self) -> dict:
        """Get channel message dual-path metrics snapshot."""
        return self._channel_path.snapshot()

    async def _process_outbound(self) -> None:
        """Process outbound messages from the bridge → MeshCore."""
        try:
            msg = self._send_queue.get_nowait()
        except Empty:
            return

        try:
            dm_only = False
            if isinstance(msg, CanonicalMessage):
                text = msg.to_meshcore_text()
                dest = msg.destination_address
                dm_only = bool((msg.metadata or {}).get('dm_only'))
            elif isinstance(msg, dict):
                text = msg.get('message', '')
                dest = msg.get('destination')
                dm_only = bool(msg.get('dm_only'))
            else:
                text = str(msg)
                dest = None

            success = await self._send_message(
                text, dest, broadcast_fallback=not dm_only)

            if success:
                with self._stats_lock:
                    self.stats.setdefault('meshcore_tx', 0)
                    self.stats['meshcore_tx'] += 1
                self.health.record_message_sent("to_meshcore")
            else:
                with self._stats_lock:
                    self.stats.setdefault('errors', 0)
                    self.stats['errors'] += 1

        except TransmitBlocked as e:
            # Deliberate catch (see tx_guard docstring): the refusal is
            # already recorded+logged by the guard; letting it fly would kill
            # the outbound task mid-bookkeeping (the finding-5 class). The
            # message is dropped, witnessed by the stat.
            with self._stats_lock:
                self.stats.setdefault('tx_blocked', 0)
                self.stats['tx_blocked'] += 1
            logger.warning(
                f"MeshCore outbound refused by tx_guard — dropped: {e}")
        except Exception as e:
            logger.error(f"Error processing outbound MeshCore message: {e}")

    async def _send_message(self, text: str, destination: Optional[str] = None,
                            broadcast_fallback: bool = True) -> bool:
        """
        Send a text message to the MeshCore network.

        Args:
            text: Message text (will be truncated to 160 bytes if needed)
            destination: Destination address (None = channel broadcast)

        Returns:
            True if sent successfully.
        """
        if not self._meshcore or not self._connected:
            return False

        # RF egress chokepoint — every MeshCore send funnels through here,
        # and the companion is a real LoRa radio (2026-08-09 review: this
        # second radio sat entirely outside the egress architecture). The
        # in-process simulator is not egress. OUTSIDE the try, so the
        # refusal cannot be absorbed into "send failed".
        if not isinstance(self._meshcore, MeshCoreSimulator):
            from utils.tx_guard import assert_meshcore_tx_allowed
            assert_meshcore_tx_allowed(
                kind="meshcore_tx",
                detail=f"meshcore_handler send dest={destination!r} "
                       f"text={text[:40]!r}")

        try:
            if destination:
                # Direct message — need to resolve contact
                if hasattr(self._meshcore, 'commands'):
                    contacts = self._extract_contacts(
                        await self._meshcore.commands.get_contacts())
                    contact = self._find_contact(contacts, destination)
                    if contact:
                        await self._meshcore.commands.send_msg(contact, text)
                        return True
                    if not broadcast_fallback:
                        # DM-only (oracle reply): a directed answer to an
                        # unknown contact is DROPPED, never broadcast to the
                        # whole channel.
                        logger.warning(
                            f"MeshCore contact not found for {destination}, "
                            f"dm_only set — dropping (not broadcasting)")
                        return False
                    logger.warning(
                        f"MeshCore contact not found for {destination}, "
                        f"sending as channel broadcast")
                # Fall through to broadcast
                await self._meshcore.commands.send_channel_txt_msg(text)
                return True
            else:
                # Channel broadcast
                if hasattr(self._meshcore, 'commands'):
                    await self._meshcore.commands.send_channel_txt_msg(text)
                elif hasattr(self._meshcore, 'send_channel_txt_msg'):
                    await self._meshcore.send_channel_txt_msg(text)
                else:
                    logger.error("MeshCore instance has no send method")
                    return False
                return True

        except Exception as e:
            logger.error(f"Failed to send MeshCore message: {e}")
            return False

    # ── read-only snapshots for the status API (roadmap 1e, 2026-09-22) ──
    #
    # Served over the gateway's own :9090 listener by
    # utils.meshcore_status_api so a TUI in ANOTHER process can render the
    # radio's contact table, its firmware fact and the oracle posture. Every
    # snapshot is tri-state on failure: ``observed=False`` + a reason, never
    # an empty list that would read as "the radio knows nobody"
    # (honest_failure_modes #1: unobservable ≠ empty).

    #: MeshCore contact ``type`` on the wire → role (meshcore_py reader.py).
    CONTACT_TYPES = {1: "companion", 2: "repeater", 3: "room", 4: "sensor"}
    DEVICE_INFO_TTL_S = 300.0

    def _run_on_loop(self, coro, timeout: float = 10.0):
        """Run a radio coroutine from a foreign (HTTP) thread.

        The handler owns a dedicated asyncio loop thread; a caller outside
        it schedules onto that loop and blocks. With no loop running
        (tests, pre-connect) the coroutine runs inline.
        """
        loop = self._loop
        if loop is None or not loop.is_running():
            return asyncio.run(coro)
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=timeout)

    @staticmethod
    def _extract_contacts(contacts_evt: Any) -> List[Any]:
        """The contact list out of meshcore_py's ``get_contacts`` Event.

        The real Event carries ``.payload`` (a dict keyed by pubkey, or a
        list); the simulator returns a plain list. Iterating the Event
        itself yields nothing — which is why a DM to a real contact could
        silently resolve to "not found" before this existed.
        """
        if contacts_evt is None:
            return []
        payload = getattr(contacts_evt, 'payload', contacts_evt)
        if isinstance(payload, dict):
            return list(payload.values())
        if isinstance(payload, list):
            return payload
        return []

    @staticmethod
    def _local_iso(epoch: Any) -> Optional[str]:
        """Epoch → local ISO, or None when absent/unusable — never the epoch
        rendered as 1970 (the 2026-09-02 sentinel-leak class)."""
        try:
            return (time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(float(epoch)))
                    if epoch else None)
        except (TypeError, ValueError, OverflowError, OSError):
            return None

    @classmethod
    def _normalise_contact(cls, c: Any) -> Dict[str, Any]:
        """One contact → plain JSON. Keys are meshcore_py's; absent = None."""
        g = (lambda k: c.get(k)) if isinstance(c, dict) else (lambda k: getattr(c, k, None))
        pk = g("public_key")
        pk_hex = pk.hex() if isinstance(pk, (bytes, bytearray)) else (str(pk) if pk else "")
        ctype = g("type")
        la, lm = g("last_advert"), g("lastmod")
        return {
            "name": g("adv_name") or "",
            "public_key": pk_hex,
            "prefix": pk_hex[:12],          # what a DM's pubkey_prefix carries
            "type": ctype,
            "role": cls.CONTACT_TYPES.get(ctype) if ctype is not None else g("role"),
            # The SENDER's clock — a claim, not a receipt (live range on one
            # radio: 2022..2084). Published as-is; never rendered as heard-at.
            "last_advert": la,
            "last_advert_iso": cls._local_iso(la),
            # meshcore_py's If-Modified-Since sync cursor: stamped when the
            # RECORD changed, not when the node was heard.
            "lastmod": lm,
            "lastmod_iso": cls._local_iso(lm),
            "out_path_len": g("out_path_len"),  # -1 = no path (flood), else hops
            "adv_lat": g("adv_lat"),
            "adv_lon": g("adv_lon"),
            "flags": g("flags"),
        }

    def get_contacts_snapshot(self) -> Dict[str, Any]:
        """The radio's OWN contact table, read live through the handler's loop."""
        out: Dict[str, Any] = {"ts": time.time(), "observed": False,
                               "count": 0, "contacts": [], "reason": None}
        mc = self._meshcore
        if mc is None or not self._connected:
            out["reason"] = "meshcore not connected"
            return out
        try:
            cmds = getattr(mc, "commands", mc)
            raw = self._extract_contacts(self._run_on_loop(cmds.get_contacts()))
        except Exception as e:  # the radio did not answer — say so, never []
            out["reason"] = f"get_contacts failed: {e}"
            return out
        contacts = [self._normalise_contact(c) for c in raw]
        contacts.sort(key=lambda c: ((c.get("lastmod") or 0),
                                     (c.get("last_advert") or 0)), reverse=True)
        out.update(observed=True, count=len(contacts), contacts=contacts)
        return out

    def get_device_info_snapshot(self, refresh: bool = False) -> Dict[str, Any]:
        """What the radio reports about itself — ``model``, ``fw_build``,
        ``fw_ver`` — cached for DEVICE_INFO_TTL_S (a radio round trip).

        ⚠️ Neither field is a release version (measured on a RAK 2026-09-21):
        ``fw_build`` is a BUILD DATE string and ``fw_ver`` is the COMPANION
        PROTOCOL version byte the library feature-gates on. The radio never
        reports "1.15.0"; a renderer that says "firmware v11" is confidently
        wrong on the exact fact an operator decides a flash against.
        """
        cached = getattr(self, "_device_info_cache", None)
        if cached and not refresh and time.time() - cached[0] < self.DEVICE_INFO_TTL_S:
            return dict(cached[1])
        out: Dict[str, Any] = {"ts": time.time(), "observed": False, "model": None,
                               "fw_build": None, "fw_ver": None, "source": None,
                               "reason": None}
        mc = self._meshcore
        if mc is None or not self._connected:
            out["reason"] = "meshcore not connected"
            return out
        if isinstance(mc, MeshCoreSimulator):
            out.update(observed=True, model="MeshCoreSimulator", fw_build="sim",
                       fw_ver=None, source="simulator")
        else:
            cmds = getattr(mc, "commands", None)
            if cmds is None or not hasattr(cmds, "send_device_query"):
                out["reason"] = "radio object exposes no device query"
                return out
            try:
                evt = self._run_on_loop(cmds.send_device_query(), timeout=5.0)
            except Exception as e:
                out["reason"] = f"device query failed: {e}"
                return out
            info = getattr(evt, "payload", None)
            if not isinstance(info, dict):
                out["reason"] = f"device query returned {type(evt).__name__}, no payload"
                return out
            ver = info.get("fw ver")
            try:
                ver = int(ver) if ver is not None else None
            except (TypeError, ValueError):
                ver = None
            out.update(observed=True, model=info.get("model") or None,
                       fw_build=info.get("fw_build") or None, fw_ver=ver,
                       source="radio")
        self._device_info_cache = (time.time(), dict(out))
        return out

    def _find_contact(self, contacts: List[Any], address: str) -> Optional[Any]:
        """
        Find a MeshCore contact matching the given address.

        Args:
            contacts: List of contact objects from meshcore_py
            address: Address to match (pubkey prefix or name)

        Returns:
            Matching contact object or None.
        """
        if not contacts:
            return None

        for contact in contacts:
            # Match by public key prefix
            if isinstance(contact, dict):
                pk = contact.get('public_key', b'')
                name = contact.get('adv_name', '')
            else:
                pk = getattr(contact, 'public_key', b'')
                name = getattr(contact, 'adv_name', '')

            if isinstance(pk, bytes):
                pk_hex = pk.hex()
            else:
                pk_hex = str(pk)

            if address in pk_hex or address == name:
                return contact

        return None

    def send_text(self, message: str, destination: str = None,
                  channel: int = 0, dm_only: bool = False) -> bool:
        """
        Send a text message to MeshCore (synchronous interface).

        Queues the message for async processing in the event loop.

        Args:
            message: Text content to send
            destination: Destination address (None for broadcast)
            channel: Channel index (MeshCore uses channels differently)
            dm_only: when True with a destination, a contact-not-found DROPS
                the message instead of falling through to a channel
                broadcast (oracle replies are directed-or-nothing).

        Returns:
            True if queued successfully, False otherwise.
        """
        if not self._connected:
            logger.warning("Not connected to MeshCore")
            return False

        try:
            msg = CanonicalMessage(
                content=message,
                destination_address=destination,
                is_broadcast=destination is None,
                source_network=Protocol.MESHCORE.value,
                metadata={"dm_only": True} if dm_only else {},
            )
            self._send_queue.put_nowait(msg)
            return True
        except Full:
            logger.warning("MeshCore send queue full")
            return False

    def queue_send(self, payload: Dict) -> bool:
        """
        Send handler for persistent queue — MeshCore destination.

        Args:
            payload: Dictionary with 'message', 'destination', 'channel' keys

        Returns:
            True if queued successfully.
        """
        message = payload.get('message', '')
        destination = payload.get('destination')

        if not self._connected:
            return False

        try:
            self._send_queue.put_nowait(payload)
            return True
        except Full:
            logger.warning("MeshCore send queue full (persistent)")
            return False

    def disconnect(self) -> None:
        """Disconnect from MeshCore companion radio."""
        if self._meshcore and self._loop:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._async_disconnect(), self._loop
                )
                future.result(timeout=5)
            except Exception as e:
                logger.debug(f"Error during MeshCore disconnect: {e}")

        self._connected = False
        self._meshcore = None
        self._subscriptions.clear()
        self._notify_status("meshcore_disconnected")

    async def _async_disconnect(self) -> None:
        """Async disconnect cleanup."""
        if self._meshcore:
            try:
                if isinstance(self._meshcore, MeshCoreSimulator):
                    await self._meshcore.stop()
                elif hasattr(self._meshcore, 'disconnect'):
                    await self._meshcore.disconnect()
                elif hasattr(self._meshcore, 'close'):
                    await self._meshcore.close()
            except Exception as e:
                logger.debug(f"Error closing MeshCore connection: {e}")

    async def _handle_disconnection(self, reason: str = "") -> None:
        """Handle lost MeshCore connection."""
        logger.info(f"MeshCore connection lost: {reason}")
        self._connected = False

        try:
            await self._async_disconnect()
        except Exception as e:
            logger.debug(f"Disconnect cleanup error: {e}")

        self.health.record_connection_event("meshcore", "disconnected", reason)
        self._notify_status("meshcore_disconnected")

    async def _async_wait(self, seconds: float) -> None:
        """
        Async wait that checks stop_event for early termination.

        Args:
            seconds: Maximum time to wait.
        """
        end_time = time.monotonic() + seconds
        while time.monotonic() < end_time:
            if self._stop_event.is_set():
                return
            remaining = end_time - time.monotonic()
            await asyncio.sleep(min(0.1, remaining))

    def test_connection(self) -> bool:
        """
        Test MeshCore device availability.

        For serial connections, checks if the device path exists.
        For TCP, attempts a socket connection.

        Returns:
            True if device appears available.
        """
        meshcore_config = getattr(self.config, 'meshcore', None)
        if not meshcore_config:
            return False

        conn_type = getattr(meshcore_config, 'connection_type', 'serial')
        if conn_type == 'serial':
            import os
            device_path = getattr(meshcore_config, 'device_path', '/dev/ttyUSB1')
            return os.path.exists(device_path)
        elif conn_type == 'tcp':
            import socket
            tcp_host = getattr(meshcore_config, 'tcp_host', 'localhost')
            tcp_port = getattr(meshcore_config, 'tcp_port', 4000)
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                result = sock.connect_ex((tcp_host, tcp_port))
                return result == 0
            except (OSError, Exception):
                return False
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass
        return False
