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

from .canonical_message import CanonicalMessage, MessageType, Protocol
from .config import GatewayConfig
from .reconnect import ReconnectConfig, ReconnectStrategy
from utils.safe_import import safe_import

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


class MeshCoreHandler:
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
    ):
        self.config = config
        self.node_tracker = node_tracker
        self.health = health
        self._stop_event = stop_event
        self.stats = stats
        self._stats_lock = stats_lock
        self._outbound_queue = message_queue

        # Callbacks
        self._message_callback = message_callback
        self._status_callback = status_callback
        self._should_bridge = should_bridge

        # Connection state
        self._connected = False
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

        # Channel message polling fallback (for meshcore_py #1232 bug)
        self._last_channel_poll = 0.0
        self._channel_poll_interval = (
            getattr(meshcore_config, 'channel_poll_interval_sec', 5)
            if meshcore_config else 5
        )

        # Dual-path tracking: event subscription + polling reconciliation
        # Messages seen via event subscription (content_hash -> timestamp)
        self._event_msg_hashes: Dict[str, float] = {}
        # Messages discovered via polling (content_hash -> timestamp)
        self._poll_msg_hashes: Dict[str, float] = {}
        self._channel_hash_lock = threading.Lock()
        self._channel_hash_window = 120  # seconds to keep hashes

        # Channel message metrics (dual-path tracking for upstream bug #1232)
        self._channel_metrics = {
            'event_received': 0,      # Messages received via event subscription
            'poll_discovered': 0,     # Messages discovered via polling
            'event_missed': 0,        # Found by poll but not by event
            'duplicate_reconciled': 0,  # Same message seen from both paths
            'poll_cycles': 0,         # Total poll cycles run
            'last_event_time': None,  # Timestamp of last event-delivered msg
            'last_poll_time': None,   # Timestamp of last poll-delivered msg
        }
        self._metrics_log_interval = 50  # Log summary every N poll cycles

    @property
    def is_connected(self) -> bool:
        """Check if connected to MeshCore companion radio."""
        return self._connected

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

            # Check routing rules
            if self._should_bridge and not self._should_bridge(msg):
                logger.debug(f"MeshCore message blocked by routing rules")
                return

            # Queue for bridge
            if self._outbound_queue is not None:
                try:
                    self._outbound_queue.put_nowait(msg)
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

    async def _on_channel_message(self, event: Any) -> None:
        """Handle incoming MeshCore channel (broadcast) message via event."""
        try:
            msg = CanonicalMessage.from_meshcore(event)
            msg.is_broadcast = True

            # Track for dual-path reconciliation
            content_hash = self._compute_channel_hash(msg)
            now = time.monotonic()
            is_poll_dup = False

            with self._channel_hash_lock:
                self._event_msg_hashes[content_hash] = now
                self._channel_metrics['event_received'] += 1
                self._channel_metrics['last_event_time'] = datetime.now().isoformat()

                # Check if polling already found this message
                if content_hash in self._poll_msg_hashes:
                    self._channel_metrics['duplicate_reconciled'] += 1
                    is_poll_dup = True

            if is_poll_dup:
                logger.debug("Channel message already delivered via poll, skipping event path")
                return

            if self._should_bridge and not self._should_bridge(msg):
                logger.debug("MeshCore channel message blocked by routing rules")
                return

            if self._outbound_queue is not None:
                try:
                    self._outbound_queue.put_nowait(msg)
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
        if now - self._last_channel_poll < self._channel_poll_interval:
            return
        self._last_channel_poll = now

        if not self._meshcore or not self._connected:
            return

        # Only poll in real mode (not simulation)
        if self._simulation_mode:
            return

        with self._channel_hash_lock:
            self._channel_metrics['poll_cycles'] += 1
            poll_cycle = self._channel_metrics['poll_cycles']

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
                if poll_cycle % self._metrics_log_interval == 0:
                    self._log_channel_metrics()
                return

            for raw_msg in messages:
                try:
                    msg = CanonicalMessage.from_meshcore(raw_msg)
                    msg.is_broadcast = True

                    content_hash = self._compute_channel_hash(msg)
                    is_event_dup = False

                    with self._channel_hash_lock:
                        self._poll_msg_hashes[content_hash] = now
                        self._channel_metrics['last_poll_time'] = (
                            datetime.now().isoformat()
                        )

                        if content_hash in self._event_msg_hashes:
                            # Event already delivered this message
                            self._channel_metrics['duplicate_reconciled'] += 1
                            is_event_dup = True
                        else:
                            # Event MISSED this message — poll found it
                            self._channel_metrics['poll_discovered'] += 1
                            self._channel_metrics['event_missed'] += 1

                    if is_event_dup:
                        continue  # Already processed via event path

                    # Process the message (event path missed it)
                    logger.debug(
                        f"Poll discovered channel message missed by event: "
                        f"{msg.content[:30]}..."
                    )

                    if self._should_bridge and not self._should_bridge(msg):
                        continue

                    if self._outbound_queue is not None:
                        try:
                            self._outbound_queue.put_nowait(msg)
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
        if poll_cycle % self._metrics_log_interval == 0:
            self._log_channel_metrics()

    def _compute_channel_hash(self, msg: CanonicalMessage) -> str:
        """Compute content hash for channel message dedup across paths."""
        import hashlib
        key = f"{msg.source_address}:{msg.content}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def _cleanup_channel_hashes(self) -> None:
        """Remove expired entries from dual-path hash maps."""
        now = time.monotonic()
        cutoff = now - self._channel_hash_window

        with self._channel_hash_lock:
            expired_event = [k for k, v in self._event_msg_hashes.items()
                             if v < cutoff]
            for k in expired_event:
                del self._event_msg_hashes[k]

            expired_poll = [k for k, v in self._poll_msg_hashes.items()
                            if v < cutoff]
            for k in expired_poll:
                del self._poll_msg_hashes[k]

    def _log_channel_metrics(self) -> None:
        """Log periodic summary of channel message dual-path metrics."""
        with self._channel_hash_lock:
            m = self._channel_metrics.copy()

        total = m['event_received'] + m['poll_discovered']
        if total == 0:
            return

        event_pct = (m['event_received'] / total * 100) if total else 0
        miss_pct = (m['event_missed'] / total * 100) if total else 0

        logger.info(
            f"MeshCore channel metrics: "
            f"event={m['event_received']} ({event_pct:.0f}%), "
            f"poll_discovered={m['poll_discovered']}, "
            f"event_missed={m['event_missed']} ({miss_pct:.0f}%), "
            f"reconciled={m['duplicate_reconciled']}, "
            f"poll_cycles={m['poll_cycles']}"
        )

    def get_channel_metrics(self) -> dict:
        """Get channel message dual-path metrics snapshot."""
        with self._channel_hash_lock:
            return self._channel_metrics.copy()

    async def _process_outbound(self) -> None:
        """Process outbound messages from the bridge → MeshCore."""
        try:
            msg = self._send_queue.get_nowait()
        except Empty:
            return

        try:
            if isinstance(msg, CanonicalMessage):
                text = msg.to_meshcore_text()
                dest = msg.destination_address
            elif isinstance(msg, dict):
                text = msg.get('message', '')
                dest = msg.get('destination')
            else:
                text = str(msg)
                dest = None

            success = await self._send_message(text, dest)

            if success:
                with self._stats_lock:
                    self.stats.setdefault('meshcore_tx', 0)
                    self.stats['meshcore_tx'] += 1
                self.health.record_message_sent("to_meshcore")
            else:
                with self._stats_lock:
                    self.stats.setdefault('errors', 0)
                    self.stats['errors'] += 1

        except Exception as e:
            logger.error(f"Error processing outbound MeshCore message: {e}")

    async def _send_message(self, text: str, destination: Optional[str] = None) -> bool:
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

        try:
            if destination:
                # Direct message — need to resolve contact
                if hasattr(self._meshcore, 'commands'):
                    contacts = await self._meshcore.commands.get_contacts()
                    contact = self._find_contact(contacts, destination)
                    if contact:
                        await self._meshcore.commands.send_msg(contact, text)
                        return True
                    else:
                        logger.warning(
                            f"MeshCore contact not found for {destination}, "
                            f"sending as channel broadcast"
                        )
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
                  channel: int = 0) -> bool:
        """
        Send a text message to MeshCore (synchronous interface).

        Queues the message for async processing in the event loop.

        Args:
            message: Text content to send
            destination: Destination address (None for broadcast)
            channel: Channel index (MeshCore uses channels differently)

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

    def _notify_status(self, status: str) -> None:
        """Notify status callback."""
        if self._status_callback:
            try:
                self._status_callback(status)
            except Exception as e:
                logger.error(f"Status callback error: {e}")

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
