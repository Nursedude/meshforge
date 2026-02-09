"""
Meshtastic API Proxy - MeshForge owns the web client API.

Solves the fundamental "single client" limitation of meshtasticd's HTTP API
by making MeshForge the sole consumer of /api/v1/fromradio and multiplexing
packets to all connected web clients via per-client buffers.

Architecture:
    Browser(s) <-> MeshForge Proxy (:5000) <-> meshtasticd (:9443)

    1. MeshForge polls meshtasticd's /api/v1/fromradio in a background thread
    2. Each received protobuf packet is copied to per-client ring buffers
    3. Web clients poll MeshForge's /api/v1/fromradio (NOT meshtasticd directly)
    4. Outbound /api/v1/toradio requests are forwarded to meshtasticd
    5. /json/* endpoints are transparently proxied

This means:
    - ACK packets are properly delivered to EVERY web client
    - Multiple browser tabs work simultaneously
    - No more "waiting for delivery" caused by packet contention
    - MeshForge can inspect packets for monitoring/logging

Usage:
    proxy = MeshtasticApiProxy(host='localhost', port=9443)
    proxy.start()

    # Per-client fromradio:
    packet = proxy.get_next_packet(client_id='browser-1')

    # Forward toradio:
    proxy.send_toradio(protobuf_bytes)

    proxy.stop()

Reference:
    - https://meshtastic.org/docs/development/device/http-api/
    - Meshtastic web client: https://github.com/meshtastic/web
"""

import collections
import logging
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Deque, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Proxy defaults
DEFAULT_MESHTASTICD_PORT = 9443
POLL_INTERVAL = 0.3       # Seconds between fromradio polls (when idle)
POLL_FAST = 0.05          # Seconds between polls when actively receiving
MAX_EMPTY_FAST = 5        # Empty responses before switching back to slow poll
CLIENT_BUFFER_SIZE = 500  # Max packets buffered per client
CLIENT_TIMEOUT = 300      # Seconds before inactive client is pruned
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 10.0


@dataclass
class ProxyStats:
    """Statistics for the API proxy."""
    packets_received: int = 0
    packets_forwarded: int = 0
    toradio_forwarded: int = 0
    json_proxied: int = 0
    active_clients: int = 0
    errors: int = 0
    started_at: Optional[datetime] = None
    last_packet_time: Optional[datetime] = None


@dataclass
class ClientSession:
    """Per-client state for fromradio multiplexing."""
    client_id: str
    buffer: Deque[bytes] = field(default_factory=lambda: collections.deque(maxlen=CLIENT_BUFFER_SIZE))
    created_at: float = field(default_factory=time.time)
    last_poll: float = field(default_factory=time.time)
    packets_served: int = 0


class MeshtasticApiProxy:
    """
    Proxy for meshtasticd's HTTP API with per-client packet multiplexing.

    MeshForge becomes the sole consumer of meshtasticd's /api/v1/fromradio
    and fans out packets to all registered web clients. This eliminates
    the single-client limitation and ensures ACK packets reach every client.
    """

    def __init__(
        self,
        host: str = 'localhost',
        port: int = DEFAULT_MESHTASTICD_PORT,
        tls: bool = True,
    ):
        self.host = host
        self.port = port
        self.tls = tls

        # Build base URL
        scheme = "https" if tls else "http"
        self._base_url = f"{scheme}://{host}:{port}"

        # SSL context for meshtasticd's self-signed cert
        self._ssl_ctx = ssl.create_default_context()
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE

        # Per-client packet buffers
        self._clients: Dict[str, ClientSession] = {}
        self._lock = threading.Lock()

        # Polling state
        self._polling = False
        self._poll_thread: Optional[threading.Thread] = None
        self._connected = False
        self._config_complete = False

        # Stats
        self._stats = ProxyStats()

        # Packet inspection callbacks (for WebSocket broadcast, logging, etc.)
        self._packet_callbacks: List = []

    @property
    def stats(self) -> ProxyStats:
        """Get proxy statistics."""
        with self._lock:
            self._stats.active_clients = len(self._clients)
        return self._stats

    @property
    def is_connected(self) -> bool:
        """Check if proxy is connected to meshtasticd."""
        return self._connected

    def start(self) -> bool:
        """Start the background fromradio poller.

        Returns:
            True if started, False if meshtasticd is unreachable.
        """
        if self._polling:
            logger.warning("API proxy already running")
            return True

        # Probe meshtasticd before starting
        if not self._probe():
            logger.warning(
                f"meshtasticd HTTP API not reachable at {self._base_url}. "
                "Proxy will start anyway and retry."
            )

        self._polling = True
        self._stats.started_at = datetime.now()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="meshforge-api-proxy"
        )
        self._poll_thread.start()
        logger.info(f"Meshtastic API proxy started → {self._base_url}")
        return True

    def stop(self):
        """Stop the background poller."""
        if not self._polling:
            return

        self._polling = False
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=5)
        self._poll_thread = None
        self._connected = False
        logger.info("Meshtastic API proxy stopped")

    def add_packet_callback(self, callback):
        """Register a callback for inspecting proxied packets.

        Callback signature: callback(packet_bytes: bytes)
        Called for each FromRadio packet received from meshtasticd.
        """
        self._packet_callbacks.append(callback)

    # ─────────────────────────────────────────────────────────────────
    # Client management
    # ─────────────────────────────────────────────────────────────────

    def register_client(self, client_id: str) -> None:
        """Register a new web client to receive fromradio packets."""
        with self._lock:
            if client_id not in self._clients:
                self._clients[client_id] = ClientSession(client_id=client_id)
                logger.debug(f"API proxy: registered client {client_id}")

    def unregister_client(self, client_id: str) -> None:
        """Remove a web client."""
        with self._lock:
            self._clients.pop(client_id, None)

    def get_next_packet(self, client_id: str) -> Optional[bytes]:
        """Get the next fromradio packet for a specific client.

        Returns:
            Raw protobuf bytes, or None if no packets available.
        """
        with self._lock:
            session = self._clients.get(client_id)
            if not session:
                # Auto-register on first poll
                session = ClientSession(client_id=client_id)
                self._clients[client_id] = session

            session.last_poll = time.time()

            try:
                packet = session.buffer.popleft()
                session.packets_served += 1
                return packet
            except IndexError:
                return None

    # ─────────────────────────────────────────────────────────────────
    # Outbound: toradio forwarding
    # ─────────────────────────────────────────────────────────────────

    def send_toradio(self, data: bytes) -> bool:
        """Forward a ToRadio protobuf to meshtasticd.

        Args:
            data: Raw protobuf bytes from the web client.

        Returns:
            True if forwarded successfully.
        """
        url = f"{self._base_url}/api/v1/toradio"
        try:
            req = urllib.request.Request(
                url,
                method='PUT',
                data=data,
                headers={
                    'Content-Type': 'application/x-protobuf',
                    'Content-Length': str(len(data)),
                },
            )
            ctx = self._ssl_ctx if self.tls else None
            with urllib.request.urlopen(req, timeout=CONNECT_TIMEOUT, context=ctx) as resp:
                if resp.status in (200, 204):
                    self._stats.toradio_forwarded += 1
                    return True
                logger.warning(f"toradio forward got HTTP {resp.status}")
                return False
        except Exception as e:
            logger.debug(f"toradio forward failed: {e}")
            self._stats.errors += 1
            return False

    # ─────────────────────────────────────────────────────────────────
    # JSON endpoint proxying
    # ─────────────────────────────────────────────────────────────────

    def proxy_json(self, path: str) -> Optional[bytes]:
        """Proxy a JSON endpoint from meshtasticd.

        Args:
            path: URL path (e.g., '/json/nodes', '/json/report')

        Returns:
            Raw response bytes, or None on error.
        """
        # Reject path traversal and authority injection
        if '..' in path or not path.startswith('/'):
            logger.warning(f"Rejected suspicious proxy path: {path}")
            return None

        url = f"{self._base_url}{path}"
        try:
            req = urllib.request.Request(
                url,
                method='GET',
                headers={'Accept': 'application/json'},
            )
            ctx = self._ssl_ctx if self.tls else None
            with urllib.request.urlopen(req, timeout=READ_TIMEOUT, context=ctx) as resp:
                if resp.status == 200:
                    self._stats.json_proxied += 1
                    return resp.read()
            return None
        except Exception as e:
            logger.debug(f"JSON proxy failed for {path}: {e}")
            return None

    def proxy_static(self, path: str) -> Optional[tuple]:
        """Proxy a static file from meshtasticd's web server.

        Returns:
            Tuple of (content_bytes, content_type) or None on error.
        """
        # Reject path traversal and authority injection
        if '..' in path or not path.startswith('/'):
            logger.warning(f"Rejected suspicious proxy path: {path}")
            return None

        url = f"{self._base_url}{path}"
        try:
            req = urllib.request.Request(url, method='GET')
            ctx = self._ssl_ctx if self.tls else None
            with urllib.request.urlopen(req, timeout=READ_TIMEOUT, context=ctx) as resp:
                if resp.status == 200:
                    content_type = resp.headers.get('Content-Type', 'application/octet-stream')
                    # Cap response size at 10MB to prevent memory exhaustion
                    data = resp.read(10 * 1024 * 1024)
                    return (data, content_type)
            return None
        except Exception as e:
            logger.debug(f"Static proxy failed for {path}: {e}")
            return None

    # ─────────────────────────────────────────────────────────────────
    # Background polling
    # ─────────────────────────────────────────────────────────────────

    def _poll_loop(self):
        """Background loop: poll meshtasticd's fromradio and distribute."""
        empty_count = 0

        while self._polling:
            try:
                data = self._fetch_fromradio()

                if data and len(data) > 0:
                    self._connected = True
                    empty_count = 0
                    self._distribute_packet(data)
                    self._stats.packets_received += 1
                    self._stats.last_packet_time = datetime.now()

                    # Notify callbacks (for WebSocket, monitoring, etc.)
                    for cb in self._packet_callbacks:
                        try:
                            cb(data)
                        except Exception as e:
                            logger.debug(f"Packet callback error: {e}")

                    # Fast poll when actively receiving
                    time.sleep(POLL_FAST)
                else:
                    empty_count += 1
                    if empty_count > MAX_EMPTY_FAST:
                        time.sleep(POLL_INTERVAL)
                    else:
                        time.sleep(POLL_FAST)

            except urllib.error.URLError as e:
                if self._connected:
                    logger.warning(f"Lost connection to meshtasticd: {e}")
                    self._connected = False
                time.sleep(2.0)  # Back off on connection errors
            except Exception as e:
                logger.debug(f"Poll error: {e}")
                self._stats.errors += 1
                time.sleep(1.0)

            # Prune stale clients periodically
            if empty_count > 0 and empty_count % 100 == 0:
                self._prune_stale_clients()

    def _fetch_fromradio(self) -> Optional[bytes]:
        """GET one FromRadio protobuf packet from meshtasticd."""
        url = f"{self._base_url}/api/v1/fromradio"
        try:
            req = urllib.request.Request(
                url,
                method='GET',
                headers={'Accept': 'application/x-protobuf'},
            )
            ctx = self._ssl_ctx if self.tls else None
            with urllib.request.urlopen(req, timeout=READ_TIMEOUT, context=ctx) as resp:
                if resp.status == 200:
                    data = resp.read()
                    if data and len(data) > 0:
                        return data
            return None
        except urllib.error.URLError:
            raise  # Let poll_loop handle connection errors
        except Exception as e:
            logger.debug(f"fromradio fetch error: {e}")
            return None

    def _distribute_packet(self, data: bytes):
        """Copy a FromRadio packet to all registered client buffers."""
        with self._lock:
            delivered = 0
            for session in self._clients.values():
                session.buffer.append(data)
                delivered += 1
            self._stats.packets_forwarded += delivered

    def _prune_stale_clients(self):
        """Remove clients that haven't polled recently."""
        now = time.time()
        with self._lock:
            stale = [
                cid for cid, session in self._clients.items()
                if (now - session.last_poll) > CLIENT_TIMEOUT
            ]
            for cid in stale:
                del self._clients[cid]
                logger.debug(f"Pruned stale client: {cid}")

    def _probe(self) -> bool:
        """Check if meshtasticd HTTP API is reachable."""
        try:
            req = urllib.request.Request(
                f"{self._base_url}/json/report",
                method='GET',
                headers={'Accept': 'application/json'},
            )
            ctx = self._ssl_ctx if self.tls else None
            with urllib.request.urlopen(req, timeout=CONNECT_TIMEOUT, context=ctx) as resp:
                return resp.status == 200
        except Exception:
            return False

    def auto_detect_port(self) -> bool:
        """Try common ports to find meshtasticd's web server.

        Returns:
            True if detected, updates self.port and self._base_url.
        """
        for port in [9443, 443, 80]:
            for scheme in ['https', 'http']:
                test_url = f"{scheme}://{self.host}:{port}"
                try:
                    req = urllib.request.Request(
                        f"{test_url}/json/report",
                        method='GET',
                    )
                    ctx = self._ssl_ctx if scheme == 'https' else None
                    with urllib.request.urlopen(req, timeout=3, context=ctx) as resp:
                        if resp.status == 200:
                            self.port = port
                            self.tls = (scheme == 'https')
                            self._base_url = test_url
                            logger.info(f"meshtasticd web server detected at {test_url}")
                            return True
                except Exception:
                    continue
        return False


# ─────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────

_api_proxy: Optional[MeshtasticApiProxy] = None
_proxy_lock = threading.Lock()


def get_api_proxy(
    host: str = 'localhost',
    port: int = DEFAULT_MESHTASTICD_PORT,
    tls: bool = True,
) -> MeshtasticApiProxy:
    """Get or create the global API proxy instance."""
    global _api_proxy
    with _proxy_lock:
        if _api_proxy is None:
            _api_proxy = MeshtasticApiProxy(host=host, port=port, tls=tls)
        return _api_proxy


def start_api_proxy(
    host: str = 'localhost',
    port: int = DEFAULT_MESHTASTICD_PORT,
    tls: bool = True,
) -> bool:
    """Start the global API proxy."""
    proxy = get_api_proxy(host=host, port=port, tls=tls)
    return proxy.start()


def stop_api_proxy():
    """Stop the global API proxy."""
    if _api_proxy:
        _api_proxy.stop()
