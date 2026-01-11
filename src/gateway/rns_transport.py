"""
RNS Over Meshtastic Transport Interface

Implements RNS packet transport over Meshtastic LoRa mesh networks.
Based on: https://github.com/landandair/RNS_Over_Meshtastic

This module provides:
- Packet fragmentation for 200-byte LoRa payloads
- Fragment reassembly with timeout handling
- Transport statistics and monitoring
- Integration with Meshtastic TCP/serial/BLE interfaces
"""

import threading
import time
import logging
import hashlib
from queue import Queue, Empty
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Callable, Any
from collections import defaultdict

from .config import RNSOverMeshtasticConfig

logger = logging.getLogger(__name__)


# ============================================================================
# Constants
# ============================================================================

# Maximum payload size for Meshtastic packets
MAX_FRAGMENT_SIZE = 200

# Fragment header size (sequence + total + checksum)
FRAGMENT_HEADER_SIZE = 6

# Effective payload per fragment
PAYLOAD_PER_FRAGMENT = MAX_FRAGMENT_SIZE - FRAGMENT_HEADER_SIZE

# RNS packet type identifiers
RNS_PACKET_DATA = 0x01
RNS_PACKET_ANNOUNCE = 0x02
RNS_PACKET_LINK = 0x03
RNS_PACKET_PROOF = 0x04


# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class Fragment:
    """A single packet fragment"""
    packet_id: bytes  # 4-byte packet identifier
    sequence: int     # Fragment sequence number (0-255)
    total: int        # Total number of fragments
    payload: bytes    # Fragment payload
    timestamp: datetime = field(default_factory=datetime.now)

    def to_bytes(self) -> bytes:
        """Serialize fragment for transmission"""
        return (
            self.packet_id +
            bytes([self.sequence, self.total]) +
            self.payload
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> 'Fragment':
        """Deserialize fragment from received bytes"""
        if len(data) < FRAGMENT_HEADER_SIZE:
            raise ValueError("Fragment too short")

        packet_id = data[:4]
        sequence = data[4]
        total = data[5]
        payload = data[6:]

        return cls(
            packet_id=packet_id,
            sequence=sequence,
            total=total,
            payload=payload
        )


@dataclass
class PendingPacket:
    """Tracks fragments for a packet being reassembled"""
    packet_id: bytes
    total_fragments: int
    fragments: Dict[int, bytes] = field(default_factory=dict)
    first_seen: datetime = field(default_factory=datetime.now)

    @property
    def is_complete(self) -> bool:
        return len(self.fragments) == self.total_fragments

    def add_fragment(self, sequence: int, payload: bytes):
        """Add a fragment to the pending packet"""
        self.fragments[sequence] = payload

    def reassemble(self) -> bytes:
        """Reassemble complete packet from fragments"""
        if not self.is_complete:
            raise ValueError("Cannot reassemble incomplete packet")

        # Concatenate fragments in order
        return b''.join(
            self.fragments[i] for i in range(self.total_fragments)
        )


@dataclass
class TransportStats:
    """Transport layer statistics"""
    packets_sent: int = 0
    packets_received: int = 0
    fragments_sent: int = 0
    fragments_received: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    reassembly_timeouts: int = 0
    reassembly_successes: int = 0
    crc_errors: int = 0
    start_time: Optional[datetime] = None
    last_activity: Optional[datetime] = None

    # Latency tracking (milliseconds)
    latency_samples: List[float] = field(default_factory=list)

    def record_latency(self, latency_ms: float):
        """Record a latency sample"""
        self.latency_samples.append(latency_ms)
        # Keep last 100 samples
        if len(self.latency_samples) > 100:
            self.latency_samples = self.latency_samples[-100:]

    @property
    def avg_latency_ms(self) -> float:
        if not self.latency_samples:
            return 0.0
        return sum(self.latency_samples) / len(self.latency_samples)

    @property
    def packet_loss_rate(self) -> float:
        total = self.reassembly_successes + self.reassembly_timeouts
        if total == 0:
            return 0.0
        return self.reassembly_timeouts / total

    @property
    def uptime_seconds(self) -> float:
        if not self.start_time:
            return 0.0
        return (datetime.now() - self.start_time).total_seconds()

    def to_dict(self) -> dict:
        return {
            'packets_sent': self.packets_sent,
            'packets_received': self.packets_received,
            'fragments_sent': self.fragments_sent,
            'fragments_received': self.fragments_received,
            'bytes_sent': self.bytes_sent,
            'bytes_received': self.bytes_received,
            'reassembly_timeouts': self.reassembly_timeouts,
            'reassembly_successes': self.reassembly_successes,
            'crc_errors': self.crc_errors,
            'packet_loss_rate': round(self.packet_loss_rate, 4),
            'avg_latency_ms': round(self.avg_latency_ms, 2),
            'uptime_seconds': round(self.uptime_seconds, 1),
            'last_activity': self.last_activity.isoformat() if self.last_activity else None,
        }


# ============================================================================
# Transport Interface
# ============================================================================

class RNSMeshtasticTransport:
    """
    RNS packet transport over Meshtastic networks.

    Handles:
    - Packet fragmentation for LoRa payload limits
    - Fragment reassembly with timeout
    - Connection management (TCP/serial/BLE)
    - Statistics collection
    """

    def __init__(self, config: Optional[RNSOverMeshtasticConfig] = None):
        self.config = config or RNSOverMeshtasticConfig()

        # State
        self._running = False
        self._connected = False
        self._interface = None

        # Fragment reassembly
        self._pending_packets: Dict[bytes, PendingPacket] = {}
        self._pending_lock = threading.Lock()

        # Queues
        self._outbound_queue: Queue = Queue()
        self._inbound_queue: Queue = Queue()

        # Threads
        self._send_thread: Optional[threading.Thread] = None
        self._receive_thread: Optional[threading.Thread] = None
        self._cleanup_thread: Optional[threading.Thread] = None

        # Statistics
        self.stats = TransportStats()

        # Callbacks
        self._packet_callbacks: List[Callable[[bytes], None]] = []
        self._status_callbacks: List[Callable[[str, dict], None]] = []

        # Speed preset delay (seconds between fragments)
        self._fragment_delay = self.config.get_throughput_estimate()['delay']

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_connected(self) -> bool:
        return self._connected

    def start(self) -> bool:
        """Start the transport layer"""
        if self._running:
            logger.warning("Transport already running")
            return True

        logger.info("Starting RNS over Meshtastic transport...")

        # Connect to Meshtastic
        if not self._connect():
            return False

        self._running = True
        self.stats.start_time = datetime.now()

        # Start worker threads
        self._send_thread = threading.Thread(
            target=self._send_loop,
            daemon=True,
            name="RNS-Mesh-Send"
        )
        self._send_thread.start()

        self._receive_thread = threading.Thread(
            target=self._receive_loop,
            daemon=True,
            name="RNS-Mesh-Recv"
        )
        self._receive_thread.start()

        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            daemon=True,
            name="RNS-Mesh-Cleanup"
        )
        self._cleanup_thread.start()

        logger.info(f"Transport started (speed preset: {self.config.data_speed})")
        self._notify_status("started")
        return True

    def stop(self):
        """Stop the transport layer"""
        if not self._running:
            return

        logger.info("Stopping transport...")
        self._running = False

        # Disconnect from Meshtastic
        self._disconnect()

        # Wait for threads
        for thread in [self._send_thread, self._receive_thread, self._cleanup_thread]:
            if thread and thread.is_alive():
                thread.join(timeout=5)

        logger.info("Transport stopped")
        self._notify_status("stopped")

    def send_packet(self, packet: bytes, destination: Optional[str] = None) -> bool:
        """
        Queue a packet for transmission.

        Args:
            packet: Raw RNS packet bytes
            destination: Optional Meshtastic destination ID

        Returns:
            True if queued successfully
        """
        if not self._running:
            return False

        self._outbound_queue.put((packet, destination))
        return True

    def register_packet_callback(self, callback: Callable[[bytes], None]):
        """Register callback for received packets"""
        self._packet_callbacks.append(callback)

    def register_status_callback(self, callback: Callable[[str, dict], None]):
        """Register callback for status changes"""
        self._status_callbacks.append(callback)

    def get_status(self) -> dict:
        """Get current transport status"""
        throughput = self.config.get_throughput_estimate()

        return {
            'running': self._running,
            'connected': self._connected,
            'connection_type': self.config.connection_type,
            'device_path': self.config.device_path,
            'speed_preset': throughput['name'],
            'estimated_bps': throughput['bps'],
            'range_estimate': throughput['range'],
            'hop_limit': self.config.hop_limit,
            'pending_fragments': len(self._pending_packets),
            'outbound_queue_size': self._outbound_queue.qsize(),
            'statistics': self.stats.to_dict(),
        }

    # ========================================
    # Private Methods
    # ========================================

    def _connect(self) -> bool:
        """Connect to Meshtastic interface"""
        try:
            import meshtastic
            from pubsub import pub

            conn_type = self.config.connection_type.lower()
            device = self.config.device_path

            logger.info(f"Connecting to Meshtastic ({conn_type}: {device})")

            if conn_type == "tcp":
                import meshtastic.tcp_interface
                # Parse host:port
                if ':' in device:
                    host, port = device.rsplit(':', 1)
                    port = int(port)
                else:
                    host = device
                    port = 4403
                self._interface = meshtastic.tcp_interface.TCPInterface(
                    hostname=host
                )

            elif conn_type == "serial":
                import meshtastic.serial_interface
                self._interface = meshtastic.serial_interface.SerialInterface(
                    devPath=device
                )

            elif conn_type == "ble":
                import meshtastic.ble_interface
                self._interface = meshtastic.ble_interface.BLEInterface(
                    address=device
                )

            else:
                logger.error(f"Unknown connection type: {conn_type}")
                return False

            # Subscribe to incoming data
            def on_receive(packet, interface):
                self._on_meshtastic_receive(packet)

            pub.subscribe(on_receive, "meshtastic.receive")

            self._connected = True
            logger.info("Connected to Meshtastic")
            return True

        except ImportError as e:
            logger.error(f"Meshtastic library not available: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to connect to Meshtastic: {e}")
            return False

    def _disconnect(self):
        """Disconnect from Meshtastic"""
        if self._interface:
            try:
                self._interface.close()
            except Exception as e:
                logger.debug(f"Error closing interface: {e}")
            self._interface = None
        self._connected = False

    def _generate_packet_id(self, packet: bytes) -> bytes:
        """Generate 4-byte packet ID using djb2 hash"""
        h = 5381
        for byte in packet[:32]:  # Hash first 32 bytes
            h = ((h << 5) + h) + byte
        return (h & 0xFFFFFFFF).to_bytes(4, 'big')

    def _fragment_packet(self, packet: bytes) -> List[Fragment]:
        """Split packet into fragments"""
        packet_id = self._generate_packet_id(packet)
        fragments = []

        total = (len(packet) + PAYLOAD_PER_FRAGMENT - 1) // PAYLOAD_PER_FRAGMENT

        for i in range(total):
            start = i * PAYLOAD_PER_FRAGMENT
            end = start + PAYLOAD_PER_FRAGMENT
            payload = packet[start:end]

            fragments.append(Fragment(
                packet_id=packet_id,
                sequence=i,
                total=total,
                payload=payload
            ))

        return fragments

    def _send_loop(self):
        """Worker thread for sending packets"""
        while self._running:
            try:
                # Get packet from queue (with timeout for clean shutdown)
                try:
                    packet, destination = self._outbound_queue.get(timeout=1.0)
                except Empty:
                    continue

                if not self._connected:
                    logger.warning("Not connected, dropping packet")
                    continue

                # Fragment the packet
                fragments = self._fragment_packet(packet)
                logger.debug(f"Sending packet ({len(packet)} bytes) in {len(fragments)} fragments")

                # Send each fragment with delay
                for fragment in fragments:
                    if not self._running:
                        break

                    self._send_fragment(fragment, destination)

                    # Delay between fragments based on speed preset
                    if len(fragments) > 1:
                        time.sleep(self._fragment_delay)

                self.stats.packets_sent += 1
                self.stats.bytes_sent += len(packet)
                self.stats.last_activity = datetime.now()

            except Exception as e:
                logger.error(f"Send loop error: {e}")

    def _send_fragment(self, fragment: Fragment, destination: Optional[str] = None):
        """Send a single fragment over Meshtastic"""
        try:
            data = fragment.to_bytes()

            if self._interface:
                # Send as private data packet
                self._interface.sendData(
                    data,
                    destinationId=destination,
                    portNum=256,  # Private app port for RNS
                    hopLimit=self.config.hop_limit
                )
                self.stats.fragments_sent += 1

        except Exception as e:
            logger.error(f"Failed to send fragment: {e}")

    def _receive_loop(self):
        """Worker thread for processing received packets"""
        while self._running:
            try:
                # Get received data from queue
                try:
                    data = self._inbound_queue.get(timeout=1.0)
                except Empty:
                    continue

                # Parse fragment
                try:
                    fragment = Fragment.from_bytes(data)
                except ValueError as e:
                    logger.warning(f"Invalid fragment: {e}")
                    self.stats.crc_errors += 1
                    continue

                self.stats.fragments_received += 1

                # Add to pending packets
                with self._pending_lock:
                    packet_id = fragment.packet_id

                    if packet_id not in self._pending_packets:
                        self._pending_packets[packet_id] = PendingPacket(
                            packet_id=packet_id,
                            total_fragments=fragment.total
                        )

                    pending = self._pending_packets[packet_id]
                    pending.add_fragment(fragment.sequence, fragment.payload)

                    # Check if complete
                    if pending.is_complete:
                        try:
                            packet = pending.reassemble()
                            del self._pending_packets[packet_id]

                            self.stats.packets_received += 1
                            self.stats.bytes_received += len(packet)
                            self.stats.reassembly_successes += 1
                            self.stats.last_activity = datetime.now()

                            # Notify callbacks
                            self._notify_packet(packet)

                        except Exception as e:
                            logger.error(f"Reassembly failed: {e}")

            except Exception as e:
                logger.error(f"Receive loop error: {e}")

    def _cleanup_loop(self):
        """Worker thread for cleaning up stale fragments"""
        while self._running:
            try:
                time.sleep(5)  # Check every 5 seconds

                timeout = timedelta(seconds=self.config.fragment_timeout_sec)
                now = datetime.now()

                with self._pending_lock:
                    expired = []

                    for packet_id, pending in self._pending_packets.items():
                        if now - pending.first_seen > timeout:
                            expired.append(packet_id)

                    for packet_id in expired:
                        del self._pending_packets[packet_id]
                        self.stats.reassembly_timeouts += 1
                        logger.debug(f"Fragment timeout: {packet_id.hex()}")

                    # Enforce max pending limit
                    while len(self._pending_packets) > self.config.max_pending_fragments:
                        # Remove oldest
                        oldest_id = min(
                            self._pending_packets.keys(),
                            key=lambda k: self._pending_packets[k].first_seen
                        )
                        del self._pending_packets[oldest_id]
                        self.stats.reassembly_timeouts += 1

            except Exception as e:
                logger.error(f"Cleanup loop error: {e}")

    def _on_meshtastic_receive(self, packet: dict):
        """Handle incoming Meshtastic packet"""
        try:
            decoded = packet.get('decoded', {})
            portnum = decoded.get('portnum')

            # Only process private app packets (port 256 for RNS)
            if portnum != 'PRIVATE_APP' and portnum != 256:
                return

            payload = decoded.get('payload')
            if payload:
                if isinstance(payload, str):
                    payload = payload.encode('latin-1')
                self._inbound_queue.put(payload)

        except Exception as e:
            logger.error(f"Error processing Meshtastic packet: {e}")

    def _notify_packet(self, packet: bytes):
        """Notify packet callbacks"""
        for callback in self._packet_callbacks:
            try:
                callback(packet)
            except Exception as e:
                logger.error(f"Packet callback error: {e}")

    def _notify_status(self, status: str):
        """Notify status callbacks"""
        status_data = self.get_status()
        for callback in self._status_callbacks:
            try:
                callback(status, status_data)
            except Exception as e:
                logger.error(f"Status callback error: {e}")


# ============================================================================
# RNS Interface Adapter
# ============================================================================

class RNSMeshtasticInterface:
    """
    RNS Interface adapter for Meshtastic transport.

    This class implements the interface expected by RNS.Reticulum
    for use as a transport layer.
    """

    def __init__(self, transport: RNSMeshtasticTransport):
        self.transport = transport
        self.name = "Meshtastic"
        self.mtu = MAX_FRAGMENT_SIZE * 10  # Approximate MTU
        self.online = False

        # Register for packets
        transport.register_packet_callback(self._on_packet)
        transport.register_status_callback(self._on_status)

        # RNS callback
        self._rns_packet_callback = None

    def set_packet_callback(self, callback):
        """Set RNS packet callback"""
        self._rns_packet_callback = callback

    def send(self, packet: bytes) -> bool:
        """Send packet through transport"""
        return self.transport.send_packet(packet)

    def start(self) -> bool:
        """Start the interface"""
        result = self.transport.start()
        self.online = result
        return result

    def stop(self):
        """Stop the interface"""
        self.transport.stop()
        self.online = False

    def _on_packet(self, packet: bytes):
        """Forward packet to RNS"""
        if self._rns_packet_callback:
            self._rns_packet_callback(packet, self)

    def _on_status(self, status: str, data: dict):
        """Update online status"""
        self.online = data.get('connected', False)


# ============================================================================
# Factory Function
# ============================================================================

def create_rns_transport(config: Optional[RNSOverMeshtasticConfig] = None) -> RNSMeshtasticTransport:
    """
    Create and configure an RNS over Meshtastic transport.

    Args:
        config: Optional configuration, uses defaults if not provided

    Returns:
        Configured transport instance (not started)
    """
    return RNSMeshtasticTransport(config)
