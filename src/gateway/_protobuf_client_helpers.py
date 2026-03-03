"""
Protobuf Client Helpers — Stateless TX and Singleton Management.

Extracted from meshtastic_protobuf_client.py to keep files under 1,500 lines.
These are module-level utilities that don't depend on the MeshtasticProtobufClient
class at import time.

The stateless TX path (send_text_direct) NEVER reads /api/v1/fromradio,
ensuring zero contention with the meshtasticd web client.
"""

import logging
import random
import ssl
import threading
import urllib.error
import urllib.request
from typing import Optional

from utils.safe_import import safe_import

from .meshtastic_protobuf_ops import ProtobufTransportConfig

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
    mesh_pb2 = _mesh_pb2
    portnums_pb2 = _portnums_pb2


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
            # Deferred import to avoid circular dependency
            from gateway.meshtastic_protobuf_client import MeshtasticProtobufClient
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
