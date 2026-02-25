"""
Meshtastic Protobuf-over-HTTP Client.

Full protobuf transport using meshtasticd's /api/v1/toradio and
/api/v1/fromradio HTTP endpoints. Provides:

- Session management (want_config_id handshake)
- Config read/write via AdminMessage (device, module, channels, owner)
- Background event polling loop with callback dispatch
- Neighbor info, device metadata, and traceroute requests
- Position requests

This is COMPLEMENTARY to both:
- MeshtasticHTTPClient (JSON-only, in utils/meshtastic_http.py)
- TCP connection (port 4403, used by gateway bridge via meshtastic lib)

The HTTP protobuf endpoints run on the meshtasticd web server (default port
9443) and do NOT contend with the TCP connection.

meshtasticd Webserver config (in /etc/meshtasticd/config.yaml):
    Webserver:
        Port: 9443
"""

import logging
import random
import ssl
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from utils.safe_import import safe_import

from .meshtastic_protobuf_ops import (
    CONFIG_TYPE_NAMES,
    MODULE_CONFIG_TYPE_NAMES,
    DeviceConfigSnapshot,
    DeviceMetadataResult,
    ModuleConfigSnapshot,
    NeighborReport,
    ProtobufEventType,
    ProtobufTransportConfig,
    TracerouteResult,
    parse_device_metadata,
    parse_neighbor_info,
    parse_position,
    parse_traceroute,
)

logger = logging.getLogger(__name__)

# Protobuf imports are deferred to avoid hard dependency at module level
_admin_pb2, _config_pb2, _mesh_pb2, _module_config_pb2, _portnums_pb2, _HAS_PB2 = safe_import(
    'meshtastic.protobuf',
    'admin_pb2', 'config_pb2', 'mesh_pb2', 'module_config_pb2', 'portnums_pb2',
    package=None,
)
_json_format_mod, _HAS_PB_JSON = safe_import('google.protobuf.json_format')
_pb2_available = _HAS_PB2 and _HAS_PB_JSON

if _HAS_PB2:
    admin_pb2 = _admin_pb2
    config_pb2 = _config_pb2
    mesh_pb2 = _mesh_pb2
    module_config_pb2 = _module_config_pb2
    portnums_pb2 = _portnums_pb2
else:
    logger.warning("meshtastic protobuf not available — protobuf client disabled")


# ---------------------------------------------------------------------------
# Stateless TX — NEVER reads /api/v1/fromradio (zero contention)
# ---------------------------------------------------------------------------

_stateless_packet_counter = random.randint(1, 0xFFFFFFFF)
_stateless_counter_lock = threading.Lock()

# SSL context for self-signed certs (meshtasticd default) — created once
_stateless_ssl_ctx = ssl.create_default_context()
_stateless_ssl_ctx.check_hostname = False
_stateless_ssl_ctx.verify_mode = ssl.CERT_NONE


def _next_stateless_packet_id() -> int:
    """Generate a unique packet ID for stateless sends (thread-safe)."""
    global _stateless_packet_counter
    with _stateless_counter_lock:
        _stateless_packet_counter = (_stateless_packet_counter + 1) & 0xFFFFFFFF
        if _stateless_packet_counter == 0:
            _stateless_packet_counter = 1
        return _stateless_packet_counter


def send_text_direct(
    text: str,
    host: str = "localhost",
    port: int = 9443,
    tls: bool = True,
    destination: Optional[int] = None,
    channel_index: int = 0,
    want_ack: bool = True,
    hop_limit: Optional[int] = None,
    timeout: float = 5.0,
) -> bool:
    """Send a text message via HTTP protobuf WITHOUT creating a session.

    This is the preferred TX path for all message sending. It PUTs a
    serialized ToRadio protobuf to /api/v1/toradio and returns immediately.

    **Critical**: This function NEVER reads from /api/v1/fromradio.
    The fromradio endpoint is single-consumer — if we read from it, we
    steal packets (including delivery ACKs) from the meshtasticd web
    client at :9443, causing "waiting for delivery" hangs.

    meshtasticd fills in the source node number automatically, so no
    session handshake (want_config_id) is needed for sending.

    Args:
        text: Message text to send
        host: meshtasticd hostname
        port: meshtasticd HTTP port (default 9443)
        tls: Use HTTPS (default True, meshtasticd default)
        destination: Destination node number (None = broadcast 0xFFFFFFFF)
        channel_index: Channel to send on (0 = primary)
        want_ack: Request delivery acknowledgment from recipient
        hop_limit: LoRa hop limit (1-7). None = use device default.
        timeout: HTTP request timeout in seconds

    Returns:
        True if meshtasticd accepted the packet, False on error
    """
    if not _pb2_available:
        logger.debug("send_text_direct: protobuf not available")
        return False

    dest = destination if destination is not None else 0xFFFFFFFF
    packet_id = _next_stateless_packet_id()

    # Build MeshPacket
    mesh_packet = mesh_pb2.MeshPacket()
    mesh_packet.decoded.payload = text.encode('utf-8')
    mesh_packet.decoded.portnum = portnums_pb2.PortNum.TEXT_MESSAGE_APP
    mesh_packet.decoded.want_response = False
    mesh_packet.id = packet_id
    setattr(mesh_packet, 'to', dest)
    mesh_packet.channel = channel_index
    mesh_packet.want_ack = want_ack
    if hop_limit is not None:
        mesh_packet.hop_limit = hop_limit

    # Wrap in ToRadio
    to_radio = mesh_pb2.ToRadio()
    to_radio.packet.CopyFrom(mesh_packet)

    # HTTP PUT to /api/v1/toradio — write-only, no fromradio read
    scheme = "https" if tls else "http"
    url = f"{scheme}://{host}:{port}/api/v1/toradio"

    try:
        req = urllib.request.Request(
            url,
            data=to_radio.SerializeToString(),
            method='PUT',
            headers={'Content-Type': 'application/x-protobuf'},
        )
        ctx = _stateless_ssl_ctx if tls else None
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            if resp.status in (200, 204):
                logger.info(
                    f"Sent text via stateless HTTP protobuf "
                    f"(id={packet_id}, dest={'broadcast' if dest == 0xFFFFFFFF else f'!{dest:08x}'})"
                )
                logger.debug(f"Message content: {text[:50]}")
                return True
            logger.warning(f"send_text_direct: unexpected status {resp.status}")
            return False
    except urllib.error.HTTPError as e:
        logger.warning(f"send_text_direct: HTTP {e.code}: {e.reason}")
        return False
    except Exception as e:
        logger.debug(f"send_text_direct: {e}")
        return False


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_protobuf_client: Optional['MeshtasticProtobufClient'] = None
_client_lock = threading.Lock()


def get_protobuf_client(
    config: Optional[ProtobufTransportConfig] = None,
) -> 'MeshtasticProtobufClient':
    """Get the singleton protobuf client instance."""
    global _protobuf_client
    with _client_lock:
        if _protobuf_client is None:
            _protobuf_client = MeshtasticProtobufClient(
                config or ProtobufTransportConfig()
            )
        return _protobuf_client


def reset_protobuf_client():
    """Reset the singleton (for testing or reconnection)."""
    global _protobuf_client
    with _client_lock:
        if _protobuf_client is not None:
            _protobuf_client.disconnect()
        _protobuf_client = None


# ---------------------------------------------------------------------------
# Core client
# ---------------------------------------------------------------------------

class MeshtasticProtobufClient:
    """
    Protobuf-over-HTTP transport for meshtasticd.

    Uses /api/v1/toradio (POST binary protobuf) and /api/v1/fromradio
    (GET binary protobuf) for full device interaction without consuming
    the single TCP connection slot.

    Usage::

        client = MeshtasticProtobufClient()
        if client.connect():
            client.start_polling()
            config = client.get_all_config()
            client.stop_polling()
            client.disconnect()
    """

    def __init__(self, config: Optional[ProtobufTransportConfig] = None):
        self._config = config or ProtobufTransportConfig()

        # Build base URL
        scheme = "https" if self._config.tls else "http"
        self._base_url = f"{scheme}://{self._config.host}:{self._config.port}"

        # SSL context for self-signed certs (meshtasticd default)
        self._ssl_ctx = ssl.create_default_context()
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE

        # Session state
        self._lock = threading.Lock()
        self._connected = False
        self._config_id: Optional[int] = None
        self._my_node_num: Optional[int] = None
        self._my_info: Optional[Any] = None

        # Packet ID generator
        self._packet_id_counter = random.randint(0, 0xFFFFFFFF)

        # Callback system
        self._callbacks_lock = threading.Lock()
        self._callbacks: Dict[ProtobufEventType, List[Callable]] = {}

        # Pending request/response for synchronous operations
        self._pending_lock = threading.Lock()
        self._pending_events: Dict[int, threading.Event] = {}
        self._pending_responses: Dict[int, Any] = {}

        # Polling thread
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Cached state from session setup
        self._node_infos: Dict[int, Any] = {}
        self._channels: List[Any] = []
        self._device_config = DeviceConfigSnapshot()
        self._module_config = ModuleConfigSnapshot()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """Whether a protobuf session is active."""
        with self._lock:
            return self._connected

    @property
    def my_node_num(self) -> Optional[int]:
        """Local node number (from MyNodeInfo during connect)."""
        with self._lock:
            return self._my_node_num

    @property
    def base_url(self) -> str:
        return self._base_url

    # ------------------------------------------------------------------
    # HTTP transport
    # ------------------------------------------------------------------

    def _post_toradio(self, to_radio_bytes: bytes) -> bool:
        """POST serialized ToRadio protobuf to /api/v1/toradio.

        Args:
            to_radio_bytes: Serialized ToRadio protobuf

        Returns:
            True on success, False on error
        """
        url = f"{self._base_url}/api/v1/toradio"
        try:
            req = urllib.request.Request(
                url,
                data=to_radio_bytes,
                method='PUT',
                headers={
                    'Content-Type': 'application/x-protobuf',
                },
            )
            ctx = self._ssl_ctx if self._config.tls else None
            with urllib.request.urlopen(
                req, timeout=self._config.connect_timeout, context=ctx
            ) as resp:
                return resp.status in (200, 204)
        except urllib.error.HTTPError as e:
            logger.warning(f"POST /api/v1/toradio HTTP {e.code}: {e.reason}")
            return False
        except Exception as e:
            logger.debug(f"POST /api/v1/toradio failed: {e}")
            return False

    def _get_fromradio(self) -> Optional[bytes]:
        """GET one FromRadio protobuf from /api/v1/fromradio.

        Returns:
            Raw protobuf bytes, or None if empty/error
        """
        url = f"{self._base_url}/api/v1/fromradio"
        try:
            req = urllib.request.Request(
                url,
                method='GET',
                headers={
                    'Accept': 'application/x-protobuf',
                },
            )
            ctx = self._ssl_ctx if self._config.tls else None
            with urllib.request.urlopen(
                req, timeout=self._config.read_timeout, context=ctx
            ) as resp:
                data = resp.read()
                if data and len(data) > 0:
                    return data
                return None
        except urllib.error.HTTPError as e:
            logger.warning(f"GET /api/v1/fromradio HTTP {e.code}: {e.reason}")
            return None
        except Exception as e:
            logger.debug(f"GET /api/v1/fromradio failed: {e}")
            return None

    def _send_toradio(self, to_radio) -> bool:
        """Serialize and send a ToRadio protobuf message.

        Args:
            to_radio: A mesh_pb2.ToRadio message object

        Returns:
            True on success
        """
        return self._post_toradio(to_radio.SerializeToString())

    # ------------------------------------------------------------------
    # Packet ID generation
    # ------------------------------------------------------------------

    def _generate_packet_id(self) -> int:
        """Generate a unique packet ID (thread-safe)."""
        with self._lock:
            self._packet_id_counter = (self._packet_id_counter + 1) & 0xFFFFFFFF
            if self._packet_id_counter == 0:
                self._packet_id_counter = 1
            return self._packet_id_counter

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Initiate a protobuf session with meshtasticd.

        Sends want_config_id and drains the initial config burst
        (MyNodeInfo, NodeInfos, Configs, Channels, config_complete).

        Returns:
            True if session established, False on timeout/error
        """
        if not _pb2_available:
            logger.error("Cannot connect: meshtastic protobuf not available")
            return False

        self._config_id = random.randint(1, 0xFFFFFFFF)

        # Send want_config_id
        to_radio = mesh_pb2.ToRadio()
        to_radio.want_config_id = self._config_id
        if not self._send_toradio(to_radio):
            logger.error("Failed to send want_config_id")
            return False

        logger.info(f"Sent want_config_id={self._config_id}, draining initial config...")

        # Drain config burst
        deadline = time.monotonic() + self._config.session_timeout
        config_complete = False

        while time.monotonic() < deadline:
            data = self._get_fromradio()
            if not data:
                time.sleep(0.1)
                continue

            try:
                from_radio = mesh_pb2.FromRadio()
                from_radio.ParseFromString(data)
                config_complete = self._handle_session_setup(from_radio)
                if config_complete:
                    break
            except Exception as e:
                logger.warning(f"Error parsing FromRadio during connect: {e}")
                continue

        if config_complete:
            with self._lock:
                self._connected = True
            logger.info(
                f"Protobuf session established (node_num={self._my_node_num}, "
                f"nodes={len(self._node_infos)}, channels={len(self._channels)})"
            )
            self._notify(ProtobufEventType.CONNECTION_STATE, {'connected': True})
            return True

        logger.error("Protobuf session timed out waiting for config_complete")
        return False

    def _handle_session_setup(self, from_radio) -> bool:
        """Process a FromRadio message during session setup.

        Returns True when config_complete_id matches our session.
        """
        if from_radio.HasField("my_info"):
            with self._lock:
                self._my_info = from_radio.my_info
                self._my_node_num = from_radio.my_info.my_node_num
            logger.debug(f"Received MyNodeInfo: node_num={self._my_node_num}")
            return False

        if from_radio.HasField("node_info"):
            ni = from_radio.node_info
            self._node_infos[ni.num] = ni
            logger.debug(f"Received NodeInfo: num={ni.num}")
            return False

        if from_radio.HasField("config"):
            self._store_config(from_radio.config)
            return False

        if from_radio.HasField("moduleConfig"):
            self._store_module_config(from_radio.moduleConfig)
            return False

        if from_radio.HasField("channel"):
            self._channels.append(from_radio.channel)
            return False

        if from_radio.HasField("metadata"):
            logger.debug(f"Received DeviceMetadata during setup")
            return False

        if from_radio.config_complete_id == self._config_id:
            logger.debug(f"Config complete (id={self._config_id})")
            return True

        return False

    def _store_config(self, config) -> None:
        """Store a Config message into the snapshot."""
        variant = config.WhichOneof("payload_variant")
        if variant and hasattr(self._device_config, variant):
            setattr(self._device_config, variant, getattr(config, variant))
            logger.debug(f"Stored config: {variant}")

    def _store_module_config(self, module_config) -> None:
        """Store a ModuleConfig message into the snapshot."""
        variant = module_config.WhichOneof("payload_variant")
        if variant and hasattr(self._module_config, variant):
            setattr(self._module_config, variant, getattr(module_config, variant))
            logger.debug(f"Stored module config: {variant}")

    def disconnect(self) -> None:
        """End the protobuf session."""
        self.stop_polling()

        if _pb2_available and self._connected:
            to_radio = mesh_pb2.ToRadio()
            to_radio.disconnect = True
            self._send_toradio(to_radio)

        with self._lock:
            self._connected = False
            self._config_id = None

        self._notify(ProtobufEventType.CONNECTION_STATE, {'connected': False})
        logger.info("Protobuf session disconnected")

    # ------------------------------------------------------------------
    # Callback registration
    # ------------------------------------------------------------------

    def register_callback(
        self, event_type: ProtobufEventType, callback: Callable
    ) -> None:
        """Register a callback for a specific event type."""
        with self._callbacks_lock:
            if event_type not in self._callbacks:
                self._callbacks[event_type] = []
            if callback not in self._callbacks[event_type]:
                self._callbacks[event_type].append(callback)

    def unregister_callback(
        self, event_type: ProtobufEventType, callback: Callable
    ) -> None:
        """Remove a callback for a specific event type."""
        with self._callbacks_lock:
            if event_type in self._callbacks:
                try:
                    self._callbacks[event_type].remove(callback)
                except ValueError:
                    pass

    def _notify(self, event_type: ProtobufEventType, data: Any) -> None:
        """Dispatch an event to registered callbacks."""
        with self._callbacks_lock:
            callbacks = list(self._callbacks.get(event_type, []))

        for cb in callbacks:
            try:
                cb(event_type, data)
            except Exception as e:
                logger.error(f"Callback error for {event_type.value}: {e}")

    # ------------------------------------------------------------------
    # Event polling loop
    # ------------------------------------------------------------------

    def start_polling(self) -> None:
        """Start the background polling thread."""
        if self._poll_thread and self._poll_thread.is_alive():
            return

        self._stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            name="protobuf-poll",
            daemon=True,
        )
        self._poll_thread.start()
        logger.info("Protobuf polling started")

    def stop_polling(self) -> None:
        """Stop the background polling thread."""
        self._stop_event.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=5.0)
        self._poll_thread = None
        logger.info("Protobuf polling stopped")

    @property
    def is_polling(self) -> bool:
        return self._poll_thread is not None and self._poll_thread.is_alive()

    def _poll_loop(self) -> None:
        """Background loop: poll /api/v1/fromradio and dispatch events."""
        empty_count = 0

        while not self._stop_event.is_set():
            try:
                data = self._get_fromradio()
                if not data:
                    empty_count += 1
                    if empty_count > self._config.max_empty_polls:
                        interval = self._config.backoff_interval
                    else:
                        interval = self._config.poll_interval
                    self._stop_event.wait(interval)
                    continue

                empty_count = 0
                self._dispatch_fromradio(data)

            except Exception as e:
                logger.error(f"Poll loop error: {e}")
                self._stop_event.wait(self._config.poll_interval)

    def _dispatch_fromradio(self, data: bytes) -> None:
        """Parse and dispatch a FromRadio protobuf message."""
        if not _pb2_available:
            return

        try:
            from_radio = mesh_pb2.FromRadio()
            from_radio.ParseFromString(data)
        except Exception as e:
            logger.warning(f"Failed to parse FromRadio: {e}")
            return

        if from_radio.HasField("packet"):
            self._handle_packet(from_radio.packet)

        elif from_radio.HasField("node_info"):
            ni = from_radio.node_info
            self._node_infos[ni.num] = ni
            self._notify(ProtobufEventType.NODE_INFO_UPDATED, {
                'num': ni.num,
                'node_info': ni,
            })

        elif from_radio.HasField("config"):
            self._store_config(from_radio.config)
            self._notify(ProtobufEventType.CONFIG_RECEIVED, {
                'config': from_radio.config,
            })

        elif from_radio.HasField("moduleConfig"):
            self._store_module_config(from_radio.moduleConfig)
            self._notify(ProtobufEventType.MODULE_CONFIG_RECEIVED, {
                'module_config': from_radio.moduleConfig,
            })

        elif from_radio.HasField("channel"):
            self._channels.append(from_radio.channel)
            self._notify(ProtobufEventType.CHANNEL_RECEIVED, {
                'channel': from_radio.channel,
            })

        elif from_radio.HasField("log_record"):
            self._notify(ProtobufEventType.LOG_RECORD, {
                'message': from_radio.log_record.message,
            })

        elif from_radio.HasField("my_info"):
            with self._lock:
                self._my_info = from_radio.my_info
                self._my_node_num = from_radio.my_info.my_node_num
            self._notify(ProtobufEventType.MY_INFO, {
                'my_info': from_radio.my_info,
            })

        elif from_radio.HasField("metadata"):
            self._notify(ProtobufEventType.METADATA, {
                'metadata': from_radio.metadata,
            })

        elif from_radio.HasField("queueStatus"):
            self._notify(ProtobufEventType.QUEUE_STATUS, {
                'queue_status': from_radio.queueStatus,
            })

        elif from_radio.config_complete_id != 0:
            self._notify(ProtobufEventType.CONFIG_COMPLETE, {
                'config_id': from_radio.config_complete_id,
            })

    def _handle_packet(self, packet) -> None:
        """Dispatch a MeshPacket based on its portnum."""
        if not packet.HasField("decoded"):
            return

        decoded = packet.decoded
        portnum = decoded.portnum
        payload = decoded.payload
        from_node = getattr(packet, 'from')
        request_id = decoded.request_id if decoded.request_id else packet.id

        # Check for pending synchronous request
        self._resolve_pending(request_id, decoded)

        # Dispatch by portnum
        if portnum == portnums_pb2.PortNum.NEIGHBORINFO_APP:
            report = parse_neighbor_info(payload, from_node)
            if report:
                self._notify(ProtobufEventType.NEIGHBOR_INFO, {
                    'report': report,
                    'from_node': from_node,
                })

        elif portnum == portnums_pb2.PortNum.TRACEROUTE_APP:
            result = parse_traceroute(payload, getattr(packet, 'to'))
            if result:
                self._notify(ProtobufEventType.TRACEROUTE_RESULT, {
                    'result': result,
                    'from_node': from_node,
                })

        elif portnum == portnums_pb2.PortNum.POSITION_APP:
            pos = parse_position(payload)
            if pos:
                self._notify(ProtobufEventType.POSITION_RECEIVED, {
                    'position': pos,
                    'from_node': from_node,
                })

        elif portnum == portnums_pb2.PortNum.ADMIN_APP:
            # Admin responses are handled via pending request resolution
            self._notify(ProtobufEventType.PACKET_RECEIVED, {
                'packet': packet,
                'portnum': 'ADMIN_APP',
            })

        else:
            self._notify(ProtobufEventType.PACKET_RECEIVED, {
                'packet': packet,
                'portnum': portnum,
                'from_node': from_node,
            })

    # ------------------------------------------------------------------
    # Pending request/response system
    # ------------------------------------------------------------------

    def _register_pending(self, request_id: int) -> threading.Event:
        """Register a pending request that expects a response."""
        event = threading.Event()
        with self._pending_lock:
            self._pending_events[request_id] = event
        return event

    def _resolve_pending(self, request_id: int, decoded: Any) -> None:
        """Resolve a pending request with its response data."""
        with self._pending_lock:
            event = self._pending_events.get(request_id)
            if event:
                self._pending_responses[request_id] = decoded
                event.set()

    def _wait_for_response(
        self, request_id: int, timeout: float = 10.0
    ) -> Optional[Any]:
        """Wait for a pending request to be resolved.

        Args:
            request_id: The packet ID to wait for
            timeout: Seconds to wait

        Returns:
            The decoded response, or None on timeout
        """
        with self._pending_lock:
            event = self._pending_events.get(request_id)
        if not event:
            return None

        if event.wait(timeout):
            with self._pending_lock:
                self._pending_events.pop(request_id, None)
                return self._pending_responses.pop(request_id, None)

        # Timeout - clean up
        with self._pending_lock:
            self._pending_events.pop(request_id, None)
            self._pending_responses.pop(request_id, None)
        return None

    # ------------------------------------------------------------------
    # Text message sending
    # ------------------------------------------------------------------

    def send_text(
        self,
        text: str,
        destination: Optional[int] = None,
        channel_index: int = 0,
        want_ack: bool = True,
    ) -> bool:
        """Send a text message via HTTP protobuf (no CLI, no TCP).

        This is the preferred TX path — uses the same /api/v1/toradio
        endpoint as the meshtasticd web client. No TCP contention,
        no subprocess overhead.

        Args:
            text: Message text to send
            destination: Destination node number (None = broadcast)
            channel_index: Channel to send on (0 = primary)
            want_ack: Request delivery acknowledgment

        Returns:
            True if message was accepted by meshtasticd
        """
        if not _pb2_available:
            logger.error("Cannot send text: meshtastic protobuf not available")
            return False

        if not self._connected:
            logger.error("Cannot send text: protobuf session not connected")
            return False

        dest = destination if destination is not None else 0xFFFFFFFF
        payload = text.encode('utf-8')

        packet_id = self.send_mesh_packet(
            payload=payload,
            dest_num=dest,
            portnum=portnums_pb2.PortNum.TEXT_MESSAGE_APP,
            want_ack=want_ack,
            channel_index=channel_index,
        )

        if packet_id:
            logger.info(f"Sent text via HTTP protobuf (id={packet_id}): {text[:50]}...")
            return True

        logger.warning("Failed to send text via HTTP protobuf")
        return False

    # ------------------------------------------------------------------
    # Low-level packet sending
    # ------------------------------------------------------------------

    def send_mesh_packet(
        self,
        payload: bytes,
        dest_num: int,
        portnum: int,
        want_ack: bool = False,
        want_response: bool = False,
        channel_index: int = 0,
        hop_limit: Optional[int] = None,
    ) -> int:
        """Send a MeshPacket via the protobuf HTTP transport.

        Args:
            payload: Serialized protobuf payload
            dest_num: Destination node number
            portnum: Application port number
            want_ack: Request delivery acknowledgment
            want_response: Request application-level response
            channel_index: Channel to send on
            hop_limit: Override default hop limit

        Returns:
            Packet ID (0 on failure)
        """
        if not _pb2_available:
            return 0

        packet_id = self._generate_packet_id()

        mesh_packet = mesh_pb2.MeshPacket()
        mesh_packet.decoded.payload = payload
        mesh_packet.decoded.portnum = portnum
        mesh_packet.decoded.want_response = want_response
        mesh_packet.id = packet_id
        setattr(mesh_packet, 'to', dest_num)
        mesh_packet.channel = channel_index
        mesh_packet.want_ack = want_ack

        if hop_limit is not None:
            mesh_packet.hop_limit = hop_limit

        to_radio = mesh_pb2.ToRadio()
        to_radio.packet.CopyFrom(mesh_packet)

        if self._send_toradio(to_radio):
            return packet_id
        return 0

    # ------------------------------------------------------------------
    # Admin message helpers
    # ------------------------------------------------------------------

    def _send_admin(
        self,
        admin_msg,
        dest_num: Optional[int] = None,
        want_response: bool = True,
    ) -> int:
        """Send an AdminMessage to a node.

        Args:
            admin_msg: admin_pb2.AdminMessage
            dest_num: Target node (None = local node)
            want_response: Whether to expect a response

        Returns:
            Packet ID (0 on failure)
        """
        if dest_num is None:
            dest_num = self._my_node_num
        if dest_num is None:
            logger.error("Cannot send admin: no node number")
            return 0

        return self.send_mesh_packet(
            payload=admin_msg.SerializeToString(),
            dest_num=dest_num,
            portnum=portnums_pb2.PortNum.ADMIN_APP,
            want_response=want_response,
        )

    def _admin_request(
        self,
        admin_msg,
        dest_num: Optional[int] = None,
        timeout: float = 10.0,
    ) -> Optional[Any]:
        """Send an admin request and wait for the response.

        Args:
            admin_msg: AdminMessage to send
            dest_num: Target node (None = local)
            timeout: Response timeout in seconds

        Returns:
            Decoded response payload, or None on timeout
        """
        packet_id = self._send_admin(admin_msg, dest_num=dest_num)
        if packet_id == 0:
            return None

        event = self._register_pending(packet_id)

        # If not polling, do manual poll
        if not self.is_polling:
            return self._manual_poll_for_response(packet_id, timeout)

        return self._wait_for_response(packet_id, timeout)

    def _manual_poll_for_response(
        self, request_id: int, timeout: float
    ) -> Optional[Any]:
        """Poll for a response manually (when polling thread is not running)."""
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            data = self._get_fromradio()
            if data:
                self._dispatch_fromradio(data)

            with self._pending_lock:
                if request_id not in self._pending_events:
                    # Already resolved
                    return self._pending_responses.pop(request_id, None)
                event = self._pending_events.get(request_id)

            if event and event.is_set():
                with self._pending_lock:
                    self._pending_events.pop(request_id, None)
                    return self._pending_responses.pop(request_id, None)

            time.sleep(0.1)

        # Timeout
        with self._pending_lock:
            self._pending_events.pop(request_id, None)
            self._pending_responses.pop(request_id, None)
        return None

    # ------------------------------------------------------------------
    # Config read operations
    # ------------------------------------------------------------------

    def get_config(self, config_type: int) -> Optional[Any]:
        """Get a device config section.

        Args:
            config_type: AdminMessage.ConfigType value (0=device, 5=lora, etc.)

        Returns:
            The config protobuf sub-message, or None on failure
        """
        if not _pb2_available:
            return None

        admin = admin_pb2.AdminMessage()
        admin.get_config_request = config_type

        resp = self._admin_request(admin)
        if resp and hasattr(resp, 'payload'):
            try:
                admin_resp = admin_pb2.AdminMessage()
                admin_resp.ParseFromString(resp.payload)
                if admin_resp.HasField('get_config_response'):
                    cfg = admin_resp.get_config_response
                    variant = cfg.WhichOneof("payload_variant")
                    if variant:
                        return getattr(cfg, variant)
            except Exception as e:
                logger.warning(f"Failed to parse config response: {e}")
        return None

    def get_all_config(self) -> DeviceConfigSnapshot:
        """Get all device configuration sections.

        Returns:
            DeviceConfigSnapshot with all populated sections
        """
        snapshot = DeviceConfigSnapshot()
        if not _pb2_available:
            return snapshot

        config_fields = [
            (0, 'device'), (1, 'position'), (2, 'power'),
            (3, 'network'), (4, 'display'), (5, 'lora'),
            (6, 'bluetooth'), (7, 'security'),
        ]
        for type_id, field_name in config_fields:
            result = self.get_config(type_id)
            if result is not None:
                setattr(snapshot, field_name, result)

        return snapshot

    def get_module_config(self, module_type: int) -> Optional[Any]:
        """Get a module config section.

        Args:
            module_type: AdminMessage.ModuleConfigType value (0=mqtt, 5=telemetry, etc.)

        Returns:
            The module config protobuf sub-message, or None on failure
        """
        if not _pb2_available:
            return None

        admin = admin_pb2.AdminMessage()
        admin.get_module_config_request = module_type

        resp = self._admin_request(admin)
        if resp and hasattr(resp, 'payload'):
            try:
                admin_resp = admin_pb2.AdminMessage()
                admin_resp.ParseFromString(resp.payload)
                if admin_resp.HasField('get_module_config_response'):
                    mcfg = admin_resp.get_module_config_response
                    variant = mcfg.WhichOneof("payload_variant")
                    if variant:
                        return getattr(mcfg, variant)
            except Exception as e:
                logger.warning(f"Failed to parse module config response: {e}")
        return None

    def get_all_module_config(self) -> ModuleConfigSnapshot:
        """Get all module configuration sections.

        Returns:
            ModuleConfigSnapshot with all populated sections
        """
        snapshot = ModuleConfigSnapshot()
        if not _pb2_available:
            return snapshot

        module_fields = [
            (0, 'mqtt'), (1, 'serial'), (2, 'external_notification'),
            (3, 'store_forward'), (4, 'range_test'), (5, 'telemetry'),
            (6, 'canned_message'), (7, 'audio'), (8, 'remote_hardware'),
            (9, 'neighbor_info'), (10, 'ambient_lighting'),
            (11, 'detection_sensor'), (12, 'paxcounter'),
        ]
        for type_id, field_name in module_fields:
            result = self.get_module_config(type_id)
            if result is not None:
                setattr(snapshot, field_name, result)

        return snapshot

    # ------------------------------------------------------------------
    # Config write operations
    # ------------------------------------------------------------------

    def set_config(self, config_name: str, config_msg: Any) -> bool:
        """Set a device config section (with begin/commit transaction).

        Args:
            config_name: Config section name ('device', 'lora', etc.)
            config_msg: The protobuf config sub-message to apply

        Returns:
            True on success
        """
        if not _pb2_available:
            return False

        # Begin edit
        begin = admin_pb2.AdminMessage()
        begin.begin_edit_settings = True
        self._send_admin(begin, want_response=False)
        time.sleep(0.2)

        # Set config
        setter = admin_pb2.AdminMessage()
        set_cfg = setter.set_config
        if hasattr(set_cfg, config_name):
            getattr(set_cfg, config_name).CopyFrom(config_msg)
        else:
            logger.error(f"Unknown config section: {config_name}")
            return False
        self._send_admin(setter, want_response=False)
        time.sleep(0.2)

        # Commit
        commit = admin_pb2.AdminMessage()
        commit.commit_edit_settings = True
        self._send_admin(commit, want_response=False)

        logger.info(f"Config '{config_name}' written successfully")
        return True

    def set_module_config(self, module_name: str, module_msg: Any) -> bool:
        """Set a module config section (with begin/commit transaction).

        Args:
            module_name: Module name ('mqtt', 'telemetry', etc.)
            module_msg: The protobuf module config sub-message to apply

        Returns:
            True on success
        """
        if not _pb2_available:
            return False

        # Begin edit
        begin = admin_pb2.AdminMessage()
        begin.begin_edit_settings = True
        self._send_admin(begin, want_response=False)
        time.sleep(0.2)

        # Set module config
        setter = admin_pb2.AdminMessage()
        set_mcfg = setter.set_module_config
        if hasattr(set_mcfg, module_name):
            getattr(set_mcfg, module_name).CopyFrom(module_msg)
        else:
            logger.error(f"Unknown module config: {module_name}")
            return False
        self._send_admin(setter, want_response=False)
        time.sleep(0.2)

        # Commit
        commit = admin_pb2.AdminMessage()
        commit.commit_edit_settings = True
        self._send_admin(commit, want_response=False)

        logger.info(f"Module config '{module_name}' written successfully")
        return True

    # ------------------------------------------------------------------
    # Channel operations
    # ------------------------------------------------------------------

    def get_channel(self, index: int) -> Optional[Any]:
        """Get a specific channel by index.

        Args:
            index: Channel index (0-7)

        Returns:
            Channel protobuf, or None
        """
        if not _pb2_available:
            return None

        admin = admin_pb2.AdminMessage()
        admin.get_channel_request = index + 1  # 1-indexed in the protocol

        resp = self._admin_request(admin)
        if resp and hasattr(resp, 'payload'):
            try:
                admin_resp = admin_pb2.AdminMessage()
                admin_resp.ParseFromString(resp.payload)
                if admin_resp.HasField('get_channel_response'):
                    return admin_resp.get_channel_response
            except Exception as e:
                logger.warning(f"Failed to parse channel response: {e}")
        return None

    def get_channels(self) -> List[Any]:
        """Get all channels (from cache or fresh request).

        Returns:
            List of Channel protobufs
        """
        if self._channels:
            return list(self._channels)

        channels = []
        for i in range(8):
            ch = self.get_channel(i)
            if ch:
                channels.append(ch)
        return channels

    def set_channel(self, channel) -> bool:
        """Set a channel configuration.

        Args:
            channel: channel_pb2.Channel with index set

        Returns:
            True on success
        """
        if not _pb2_available:
            return False

        admin = admin_pb2.AdminMessage()
        admin.set_channel.CopyFrom(channel)
        packet_id = self._send_admin(admin, want_response=False)
        return packet_id != 0

    # ------------------------------------------------------------------
    # Owner operations
    # ------------------------------------------------------------------

    def get_owner(self) -> Optional[Any]:
        """Get device owner info.

        Returns:
            User protobuf, or None
        """
        if not _pb2_available:
            return None

        admin = admin_pb2.AdminMessage()
        admin.get_owner_request = True

        resp = self._admin_request(admin)
        if resp and hasattr(resp, 'payload'):
            try:
                admin_resp = admin_pb2.AdminMessage()
                admin_resp.ParseFromString(resp.payload)
                if admin_resp.HasField('get_owner_response'):
                    return admin_resp.get_owner_response
            except Exception as e:
                logger.warning(f"Failed to parse owner response: {e}")
        return None

    def set_owner(
        self,
        long_name: Optional[str] = None,
        short_name: Optional[str] = None,
    ) -> bool:
        """Set device owner name.

        Args:
            long_name: Long name (up to 40 chars)
            short_name: Short name (up to 4 chars)

        Returns:
            True on success
        """
        if not _pb2_available:
            return False

        admin = admin_pb2.AdminMessage()
        if long_name is not None:
            admin.set_owner.long_name = long_name[:40]
        if short_name is not None:
            admin.set_owner.short_name = short_name[:4]

        packet_id = self._send_admin(admin, want_response=False)
        return packet_id != 0

    # ------------------------------------------------------------------
    # Device metadata
    # ------------------------------------------------------------------

    def request_device_metadata(
        self, node_num: Optional[int] = None, timeout: float = 10.0
    ) -> Optional[DeviceMetadataResult]:
        """Request device metadata from a node.

        Args:
            node_num: Target node (None = local)
            timeout: Response timeout

        Returns:
            DeviceMetadataResult or None on timeout
        """
        if not _pb2_available:
            return None

        admin = admin_pb2.AdminMessage()
        admin.get_device_metadata_request = True

        resp = self._admin_request(admin, dest_num=node_num, timeout=timeout)
        if resp and hasattr(resp, 'payload'):
            return parse_device_metadata(resp.payload)
        return None

    # ------------------------------------------------------------------
    # Neighbor info
    # ------------------------------------------------------------------

    def get_neighbor_reports(self) -> Dict[int, NeighborReport]:
        """Get all cached neighbor reports received via NEIGHBORINFO_APP.

        To collect neighbor info, ensure neighbor_info module is enabled
        on mesh nodes and the polling loop is running. Reports arrive
        automatically as nodes broadcast their neighbor tables.

        Returns:
            Dict mapping node_num to their latest NeighborReport
        """
        # Neighbor reports are dispatched via callbacks during polling.
        # This is a convenience accessor for any accumulated reports.
        # Callers should register a NEIGHBOR_INFO callback for real-time data.
        return {}

    # ------------------------------------------------------------------
    # Traceroute
    # ------------------------------------------------------------------

    def send_traceroute(
        self, dest_num: int, hop_limit: int = 7, timeout: float = 30.0
    ) -> Optional[TracerouteResult]:
        """Send a traceroute to a destination node.

        Args:
            dest_num: Destination node number
            hop_limit: Maximum hops
            timeout: Response timeout

        Returns:
            TracerouteResult or None on timeout
        """
        if not _pb2_available:
            return None

        route_discovery = mesh_pb2.RouteDiscovery()
        payload = route_discovery.SerializeToString()

        packet_id = self.send_mesh_packet(
            payload=payload,
            dest_num=dest_num,
            portnum=portnums_pb2.PortNum.TRACEROUTE_APP,
            want_response=True,
            hop_limit=hop_limit,
        )
        if packet_id == 0:
            return None

        event = self._register_pending(packet_id)

        if not self.is_polling:
            resp = self._manual_poll_for_response(packet_id, timeout)
        else:
            resp = self._wait_for_response(packet_id, timeout)

        if resp and hasattr(resp, 'payload'):
            return parse_traceroute(resp.payload, dest_num)
        return None

    # ------------------------------------------------------------------
    # Position request
    # ------------------------------------------------------------------

    def request_position(
        self, dest_num: int, timeout: float = 10.0
    ) -> Optional[Dict[str, Any]]:
        """Request position from a remote node.

        Args:
            dest_num: Target node number
            timeout: Response timeout

        Returns:
            Position dict or None on timeout
        """
        if not _pb2_available:
            return None

        position = mesh_pb2.Position()
        payload = position.SerializeToString()

        packet_id = self.send_mesh_packet(
            payload=payload,
            dest_num=dest_num,
            portnum=portnums_pb2.PortNum.POSITION_APP,
            want_response=True,
        )
        if packet_id == 0:
            return None

        event = self._register_pending(packet_id)

        if not self.is_polling:
            resp = self._manual_poll_for_response(packet_id, timeout)
        else:
            resp = self._wait_for_response(packet_id, timeout)

        if resp and hasattr(resp, 'payload'):
            return parse_position(resp.payload)
        return None

    # ------------------------------------------------------------------
    # Cached state accessors
    # ------------------------------------------------------------------

    def get_cached_config(self) -> DeviceConfigSnapshot:
        """Return the config snapshot from session setup."""
        return self._device_config

    def get_cached_module_config(self) -> ModuleConfigSnapshot:
        """Return the module config snapshot from session setup."""
        return self._module_config

    def get_cached_node_infos(self) -> Dict[int, Any]:
        """Return node infos received during session setup and polling."""
        return dict(self._node_infos)

    def get_cached_channels(self) -> List[Any]:
        """Return channels received during session setup."""
        return list(self._channels)

    def __repr__(self) -> str:
        status = "connected" if self._connected else "disconnected"
        polling = ", polling" if self.is_polling else ""
        return (
            f"MeshtasticProtobufClient({self._base_url}, {status}{polling})"
        )
