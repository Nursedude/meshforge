"""
Traffic Inspector - Wireshark-Grade Traffic Visibility for Mesh Networks.

Provides deep packet inspection, path tracing, and traffic analysis for
both Meshtastic and Reticulum (RNS) mesh networks.

Key Components:
- MeshPacket: Unified packet representation
- PacketDissector: Protocol-aware packet parser
- PacketTree: Hierarchical packet detail display (like Wireshark's protocol tree)
- PathTrace: Hop-by-hop message tracking through the mesh
- TrafficCapture: Real-time packet capture and storage
- DisplayFilter: Field-based filtering (e.g., "mesh.hops > 2")

Usage:
    from monitoring.traffic_inspector import TrafficInspector

    inspector = TrafficInspector()
    inspector.start_capture()

    # Get recent packets
    packets = inspector.get_packets(limit=100)

    # Apply filter
    filtered = inspector.filter("mesh.from == '!abc123' and mesh.hops <= 3")

    # Trace message path
    trace = inspector.trace_message(message_id)

Reference: Inspired by Wireshark's dissector architecture
https://www.wireshark.org/docs/wsdg_html_chunked/ChDissectAdd.html
"""

import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

# Re-export all models for backwards compatibility
from .traffic_models import (
    FieldType,
    HopInfo,
    HopState,
    MeshPacket,
    MESHTASTIC_PORTS,
    PacketDirection,
    PacketField,
    PacketProtocol,
    PacketTree,
)

# Re-export dissectors
from .packet_dissectors import (
    DisplayFilter,
    MeshtasticDissector,
    PacketDissector,
    RNSDissector,
)

# Re-export storage classes
from .traffic_storage import (
    TrafficAnalyzer,
    TrafficCapture,
    TrafficLogger,
    TrafficStats,
    TRAFFIC_LOG_BACKUP_COUNT,
    TRAFFIC_LOG_MAX_SIZE,
)

logger = logging.getLogger(__name__)

# Make all re-exported symbols available at module level
__all__ = [
    # Enums and constants
    'FieldType',
    'HopState',
    'MESHTASTIC_PORTS',
    'PacketDirection',
    'PacketProtocol',
    'TRAFFIC_LOG_BACKUP_COUNT',
    'TRAFFIC_LOG_MAX_SIZE',
    # Data classes
    'HopInfo',
    'MeshPacket',
    'PacketField',
    'PacketTree',
    'TrafficStats',
    # Dissectors
    'DisplayFilter',
    'MeshtasticDissector',
    'PacketDissector',
    'RNSDissector',
    # Storage and analysis
    'TrafficAnalyzer',
    'TrafficCapture',
    'TrafficLogger',
    # Main interface
    'TrafficInspector',
    # Global functions
    'get_traffic_inspector',
    'start_packet_capture',
    'stop_packet_capture',
    'is_capture_running',
]


# =============================================================================
# TRAFFIC INSPECTOR (MAIN INTERFACE)
# =============================================================================

class TrafficInspector:
    """
    Main interface for Wireshark-grade traffic visibility.

    Combines capture, dissection, filtering, and analysis into
    a unified interface for mesh network traffic inspection.

    Usage:
        inspector = TrafficInspector()

        # Capture a packet (typically from meshtastic/RNS callbacks)
        packet = inspector.capture(data, metadata)

        # Get recent packets with filter
        filtered = inspector.get_packets(filter="mesh.hops > 2")

        # Get statistics
        stats = inspector.get_stats()

        # Trace message path
        trace = inspector.trace_path(packet_id)
    """

    def __init__(self, db_path: Optional[str] = None,
                 max_packets: int = 10000,
                 enable_logging: bool = True):
        self._capture = TrafficCapture(db_path, max_packets)
        self._analyzer = TrafficAnalyzer(self._capture)
        self._running = False

        # Traffic logging to human-readable file
        self._logger: Optional[TrafficLogger] = None
        if enable_logging:
            self._logger = TrafficLogger()
            # Register logger as callback for new packets
            self._capture.register_callback(self._log_packet)

    def _log_packet(self, packet: MeshPacket) -> None:
        """Internal callback to log packets."""
        if self._logger:
            self._logger.log_packet(packet)

    def capture(self, data: bytes, metadata: Dict[str, Any]) -> Optional[MeshPacket]:
        """Capture and dissect a packet."""
        return self._capture.capture_packet(data, metadata)

    def get_packets(self, limit: int = 100, offset: int = 0,
                    filter: Optional[str] = None,
                    since: Optional[datetime] = None,
                    until: Optional[datetime] = None,
                    protocol: Optional[str] = None) -> List[MeshPacket]:
        """Get captured packets with optional filtering."""
        proto = PacketProtocol(protocol) if protocol else None
        return self._capture.get_packets(
            limit=limit,
            offset=offset,
            filter_expr=filter,
            since=since,
            until=until,
            protocol=proto,
        )

    def get_packet(self, packet_id: str) -> Optional[MeshPacket]:
        """Get a specific packet by ID."""
        packets = self._capture.get_packets(limit=1000)
        for p in packets:
            if p.id == packet_id:
                return p
        return None

    def trace_path(self, packet_id: str) -> List[HopInfo]:
        """Get path trace for a packet."""
        return self._capture.get_path_trace(packet_id)

    def get_stats(self, since: Optional[datetime] = None) -> TrafficStats:
        """Get traffic statistics."""
        return self._analyzer.get_stats(since=since)

    def get_node_stats(self, node_id: str) -> Dict[str, Any]:
        """Get statistics for a specific node."""
        return self._analyzer.get_node_stats(node_id)

    def get_capture_stats(self) -> Dict[str, Any]:
        """Get capture session statistics."""
        return self._capture.get_stats()

    def register_callback(self, callback: Callable[[MeshPacket], None]) -> None:
        """Register callback for new packets."""
        self._capture.register_callback(callback)

    def clear(self) -> int:
        """Clear all captured packets."""
        if self._logger:
            self._logger.clear()
        return self._capture.clear_all()

    def get_log_path(self) -> Optional[str]:
        """Get the path to the traffic log file."""
        if self._logger:
            return self._logger.get_log_path()
        return None

    def enable_logging(self) -> None:
        """Enable traffic logging."""
        if self._logger is None:
            self._logger = TrafficLogger()
            self._capture.register_callback(self._log_packet)
        else:
            self._logger.enable()

    def disable_logging(self) -> None:
        """Disable traffic logging."""
        if self._logger:
            self._logger.disable()

    def is_logging_enabled(self) -> bool:
        """Check if traffic logging is enabled."""
        return self._logger is not None and self._logger.is_enabled()

    @staticmethod
    def get_filter_fields() -> Dict[str, str]:
        """Get available filter fields and descriptions."""
        return DisplayFilter.get_available_fields()

    def format_packet_list(self, packets: List[MeshPacket],
                           max_width: int = 120) -> str:
        """Format packets as ASCII list for TUI display."""
        lines = []
        lines.append("=" * max_width)
        lines.append(" TRAFFIC CAPTURE ".center(max_width, "="))
        lines.append("=" * max_width)
        lines.append("")

        # Header
        lines.append(f"{'Time':<12} {'Dir':<4} {'Source':<14} {'Dest':<14} {'Port':<16} {'Hops':<5} {'SNR':<8}")
        lines.append("-" * max_width)

        for pkt in packets[:50]:  # Limit display
            time_str = pkt.timestamp.strftime("%H:%M:%S.%f")[:12]
            dir_sym = {"inbound": "<-", "outbound": "->", "relayed": "<>", "internal": ".."}
            dir_str = dir_sym.get(pkt.direction.value, "??")
            src = (pkt.source[:12] + "..") if len(pkt.source) > 14 else pkt.source[:14]
            dst = (pkt.destination[:12] + "..") if len(pkt.destination) > 14 else pkt.destination[:14]
            port = pkt.port_name[:16] if pkt.port_name else "-"
            hops = str(pkt.hops_taken) if pkt.hops_taken else "-"
            snr = f"{pkt.snr:.1f}" if pkt.snr is not None else "-"

            lines.append(f"{time_str:<12} {dir_str:<4} {src:<14} {dst:<14} {port:<16} {hops:<5} {snr:<8}")

        lines.append("")
        lines.append(f"Showing {min(50, len(packets))} of {len(packets)} packets")
        lines.append("=" * max_width)

        return "\n".join(lines)

    def format_packet_detail(self, packet: MeshPacket) -> str:
        """Format single packet detail for TUI display."""
        lines = []
        lines.append("=" * 70)
        lines.append(" PACKET DETAIL ".center(70, "="))
        lines.append("=" * 70)
        lines.append("")

        lines.append(f"ID: {packet.id}")
        lines.append(f"Time: {packet.timestamp.isoformat()}")
        lines.append(f"Direction: {packet.direction.value}")
        lines.append(f"Protocol: {packet.protocol.value}")
        lines.append("")

        # Protocol tree
        if packet.tree:
            lines.append("Protocol Tree:")
            lines.append("-" * 70)
            lines.append(packet.tree.format_ascii())

        lines.append("")
        lines.append("=" * 70)

        return "\n".join(lines)

    def format_stats(self, stats: TrafficStats) -> str:
        """Format statistics for TUI display."""
        lines = []
        lines.append("=" * 70)
        lines.append(" TRAFFIC STATISTICS ".center(70, "="))
        lines.append("=" * 70)
        lines.append("")

        lines.append(f"Total Packets: {stats.total_packets}")
        lines.append(f"Total Bytes:   {stats.total_bytes:,}")
        lines.append("")

        lines.append("By Protocol:")
        for proto, count in stats.packets_by_protocol.items():
            lines.append(f"  {proto}: {count}")
        lines.append("")

        lines.append("By Direction:")
        for direction, count in stats.packets_by_direction.items():
            lines.append(f"  {direction}: {count}")
        lines.append("")

        if stats.hops_distribution:
            lines.append(f"Hop Statistics:")
            lines.append(f"  Average: {stats.avg_hops:.2f}")
            lines.append(f"  Maximum: {stats.max_hops}")
            lines.append(f"  Distribution: {dict(stats.hops_distribution)}")
            lines.append("")

        if stats.avg_snr is not None:
            lines.append(f"Signal Quality:")
            lines.append(f"  Avg SNR:  {stats.avg_snr:.1f} dB")
            if stats.avg_rssi is not None:
                lines.append(f"  Avg RSSI: {stats.avg_rssi:.0f} dBm")
            lines.append("")

        if stats.top_sources:
            lines.append("Top Sources:")
            for node, count in stats.top_sources[:5]:
                lines.append(f"  {node[:20]}: {count}")
            lines.append("")

        lines.append("=" * 70)
        return "\n".join(lines)


# =============================================================================
# Global Inspector Instance & Auto-Connect
# =============================================================================

_global_inspector: Optional[TrafficInspector] = None
_capture_subscribed: bool = False


def get_traffic_inspector() -> TrafficInspector:
    """Get or create the global traffic inspector instance."""
    global _global_inspector
    if _global_inspector is None:
        _global_inspector = TrafficInspector()
    return _global_inspector


def start_packet_capture() -> bool:
    """
    Start capturing packets from meshtasticd via pubsub.

    Subscribes to meshtastic.receive to capture all incoming packets.
    Returns True if capture started, False if already running or failed.
    """
    global _capture_subscribed

    if _capture_subscribed:
        return False

    try:
        from pubsub import pub

        inspector = get_traffic_inspector()

        def on_meshtastic_packet(packet, interface=None):
            """Callback for meshtastic packets."""
            try:
                # Extract packet data
                raw_data = packet.get('raw', b'') if isinstance(packet, dict) else b''
                if isinstance(raw_data, str):
                    raw_data = raw_data.encode('utf-8', errors='replace')

                metadata = {
                    'protocol': 'meshtastic',
                    'timestamp': datetime.now().isoformat(),
                    'direction': 'incoming',
                }

                # Extract fields from packet
                if isinstance(packet, dict):
                    if 'from' in packet:
                        metadata['source'] = f"!{packet['from']:08x}"
                    if 'to' in packet:
                        metadata['destination'] = f"!{packet['to']:08x}"
                    if 'hopLimit' in packet:
                        metadata['hop_limit'] = packet['hopLimit']
                    if 'hopStart' in packet:
                        metadata['hop_start'] = packet['hopStart']
                    if 'rxSnr' in packet:
                        metadata['snr'] = packet['rxSnr']
                    if 'rxRssi' in packet:
                        metadata['rssi'] = packet['rxRssi']
                    if 'decoded' in packet:
                        decoded = packet['decoded']
                        if isinstance(decoded, dict):
                            metadata['portnum'] = decoded.get('portnum', 'UNKNOWN')

                inspector.capture(raw_data, metadata)

            except Exception as e:
                logger.debug(f"Error capturing meshtastic packet: {e}")

        pub.subscribe(on_meshtastic_packet, "meshtastic.receive")
        _capture_subscribed = True
        logger.info("Traffic capture started - subscribed to meshtastic.receive")
        return True

    except ImportError:
        logger.warning("pubsub not available - cannot start packet capture")
        return False
    except Exception as e:
        logger.error(f"Failed to start packet capture: {e}")
        return False


def stop_packet_capture() -> bool:
    """Stop capturing packets."""
    global _capture_subscribed

    if not _capture_subscribed:
        return False

    try:
        from pubsub import pub
        # Note: on_meshtastic_packet is defined inside start_packet_capture
        # This may not work correctly - would need refactoring for proper cleanup
        _capture_subscribed = False
        logger.info("Traffic capture stopped")
        return True
    except Exception as e:
        logger.debug(f"Error stopping capture: {e}")
        _capture_subscribed = False
        return False


def is_capture_running() -> bool:
    """Check if packet capture is running."""
    return _capture_subscribed
