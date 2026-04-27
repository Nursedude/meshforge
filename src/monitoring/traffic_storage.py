"""
Traffic Storage - Capture, storage, and analysis of mesh traffic.

Contains:
- TrafficCapture: SQLite-backed packet storage and retrieval
- TrafficStats: Aggregated traffic statistics dataclass
- TrafficAnalyzer: Traffic analysis and statistics calculation
- TrafficLogger: Human-readable traffic log file writer
"""

import json
import logging
import os
import sqlite3
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .traffic_models import (
    HopInfo,
    HopState,
    MeshPacket,
    PacketProtocol,
)
from .packet_dissectors import (
    DisplayFilter,
    MeshtasticDissector,
    PacketDissector,
    RNSDissector,
)

# Sudo-safe home directory — first-party, always available (MF001)
from utils.db_helpers import connect_tuned
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


# Traffic logging configuration
TRAFFIC_LOG_MAX_SIZE = 10 * 1024 * 1024  # 10MB max log size
TRAFFIC_LOG_BACKUP_COUNT = 3


# =============================================================================
# TRAFFIC CAPTURE (STORAGE AND RETRIEVAL)
# =============================================================================

class TrafficCapture:
    """
    Captures and stores mesh traffic for analysis.

    Features:
    - SQLite-backed persistent storage
    - Real-time packet callbacks
    - Time-based and filter-based queries
    - Path trace aggregation
    """

    DEFAULT_MAX_PACKETS = 10000
    CLEANUP_INTERVAL = 3600  # 1 hour
    # Time-based retention complements the row-count cap. Without this,
    # a quiet mesh that never hits MAX_PACKETS could still hoard
    # multi-month-old traffic. 24h is enough for "what happened this
    # session" forensics; longer-term archival is PacketArchive's job.
    DEFAULT_RETENTION_HOURS = 24

    def __init__(self, db_path: Optional[str] = None,
                 max_packets: int = DEFAULT_MAX_PACKETS,
                 retention_hours: float = DEFAULT_RETENTION_HOURS):
        if db_path is None:
            config_dir = get_real_user_home() / ".config" / "meshforge"
            config_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(config_dir / "traffic_capture.db")

        self._db_path = db_path
        self._max_packets = max_packets
        self._retention_hours = retention_hours
        self._lock = threading.Lock()
        self._callbacks: List[Callable[[MeshPacket], None]] = []
        self._running = False
        self._last_cleanup = 0.0

        # Dissectors
        self._dissectors: List[PacketDissector] = [
            MeshtasticDissector(),
            RNSDissector(),
        ]

        # Statistics
        self._stats = {
            "packets_captured": 0,
            "packets_meshtastic": 0,
            "packets_rns": 0,
            "bytes_captured": 0,
        }

        self._init_db()

    @contextmanager
    def _get_connection(self):
        """Get database connection with context management.

        Tuned via utils.db_helpers.connect_tuned (WAL + sync=NORMAL +
        64MB journal cap). Phase 1 of post-2026-04-26 fleet closure."""
        conn = connect_tuned(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS packets (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    source TEXT,
                    destination TEXT,
                    channel INTEGER,
                    hop_limit INTEGER,
                    hop_start INTEGER,
                    hops_taken INTEGER,
                    portnum INTEGER,
                    port_name TEXT,
                    snr REAL,
                    rssi INTEGER,
                    size INTEGER,
                    data TEXT
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_packets_timestamp
                ON packets(timestamp DESC)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_packets_source
                ON packets(source)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_packets_protocol
                ON packets(protocol)
            """)

            # Path traces table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS path_traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    packet_id TEXT NOT NULL,
                    hop_number INTEGER NOT NULL,
                    node_id TEXT NOT NULL,
                    node_name TEXT,
                    state TEXT,
                    timestamp TEXT,
                    snr REAL,
                    rssi INTEGER,
                    latency_ms REAL,
                    latitude REAL,
                    longitude REAL
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_traces_packet
                ON path_traces(packet_id)
            """)

    def capture_packet(self, data: bytes, metadata: Dict[str, Any]) -> Optional[MeshPacket]:
        """
        Capture and dissect a packet.

        Args:
            data: Raw packet bytes (can be empty for metadata-only)
            metadata: Packet metadata from the source

        Returns:
            Dissected MeshPacket, or None if cannot dissect
        """
        # Find appropriate dissector
        packet = None
        for dissector in self._dissectors:
            if dissector.can_dissect(data, metadata):
                packet = dissector.dissect(data, metadata)
                break

        if packet is None:
            # Create basic packet from metadata
            packet = MeshPacket(
                protocol=PacketProtocol.UNKNOWN,
                raw_bytes=data,
                size=len(data) if data else 0,
            )

        # Store packet
        self._store_packet(packet)

        # Update stats
        with self._lock:
            self._stats["packets_captured"] += 1
            self._stats["bytes_captured"] += packet.size
            if packet.protocol == PacketProtocol.MESHTASTIC:
                self._stats["packets_meshtastic"] += 1
            elif packet.protocol == PacketProtocol.RNS:
                self._stats["packets_rns"] += 1

        # Notify callbacks
        for callback in self._callbacks:
            try:
                callback(packet)
            except Exception as e:
                logger.debug(f"Packet callback error: {e}")

        # Periodic cleanup
        self._maybe_cleanup()

        return packet

    def _store_packet(self, packet: MeshPacket) -> None:
        """Store packet in database."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO packets
                (id, timestamp, direction, protocol, source, destination,
                 channel, hop_limit, hop_start, hops_taken, portnum,
                 port_name, snr, rssi, size, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                packet.id,
                packet.timestamp.isoformat(),
                packet.direction.value,
                packet.protocol.value,
                packet.source,
                packet.destination,
                packet.channel,
                packet.hop_limit,
                packet.hop_start,
                packet.hops_taken,
                packet.portnum,
                packet.port_name,
                packet.snr,
                packet.rssi,
                packet.size,
                json.dumps(packet.to_dict()),
            ))

            # Store path traces
            for hop in packet.path_trace:
                conn.execute("""
                    INSERT INTO path_traces
                    (packet_id, hop_number, node_id, node_name, state,
                     timestamp, snr, rssi, latency_ms, latitude, longitude)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    packet.id,
                    hop.hop_number,
                    hop.node_id,
                    hop.node_name,
                    hop.state.value,
                    hop.timestamp.isoformat(),
                    hop.snr,
                    hop.rssi,
                    hop.latency_ms,
                    hop.latitude,
                    hop.longitude,
                ))

    def get_packets(self, limit: int = 100, offset: int = 0,
                    filter_expr: Optional[str] = None,
                    since: Optional[datetime] = None,
                    until: Optional[datetime] = None,
                    protocol: Optional[PacketProtocol] = None,
                    source: Optional[str] = None) -> List[MeshPacket]:
        """
        Retrieve packets from capture database.

        Args:
            limit: Maximum packets to return
            offset: Skip first N packets
            filter_expr: Display filter expression
            since: Only packets after this time
            until: Only packets before this time
            protocol: Filter by protocol
            source: Filter by source node

        Returns:
            List of MeshPacket objects
        """
        query = "SELECT data FROM packets WHERE 1=1"
        params: List[Any] = []

        if since:
            query += " AND timestamp >= ?"
            params.append(since.isoformat())

        if until:
            query += " AND timestamp <= ?"
            params.append(until.isoformat())

        if protocol:
            query += " AND protocol = ?"
            params.append(protocol.value)

        if source:
            query += " AND source = ?"
            params.append(source)

        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        packets = []
        display_filter = DisplayFilter(filter_expr) if filter_expr else None

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            for row in cursor.fetchall():
                try:
                    data = json.loads(row["data"])
                    packet = MeshPacket.from_dict(data)

                    # Rebuild tree for filtering
                    for dissector in self._dissectors:
                        if dissector.can_dissect(b"", {"protocol": packet.protocol.value}):
                            packet.tree = dissector._build_tree(packet, data)
                            break

                    # Apply display filter
                    if display_filter and not display_filter.matches(packet):
                        continue

                    packets.append(packet)

                except (json.JSONDecodeError, KeyError) as e:
                    logger.debug(f"Error parsing packet: {e}")

        return packets

    def get_packet_count(self, protocol: Optional[PacketProtocol] = None) -> int:
        """Get total packet count."""
        with self._get_connection() as conn:
            if protocol:
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM packets WHERE protocol = ?",
                    (protocol.value,)
                )
            else:
                cursor = conn.execute("SELECT COUNT(*) FROM packets")
            return cursor.fetchone()[0]

    def get_path_trace(self, packet_id: str) -> List[HopInfo]:
        """Get path trace for a packet."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM path_traces
                WHERE packet_id = ?
                ORDER BY hop_number ASC
            """, (packet_id,))

            hops = []
            for row in cursor.fetchall():
                hops.append(HopInfo(
                    hop_number=row["hop_number"],
                    node_id=row["node_id"],
                    node_name=row["node_name"] or "",
                    state=HopState(row["state"]) if row["state"] else HopState.RECEIVED,
                    timestamp=datetime.fromisoformat(row["timestamp"]) if row["timestamp"] else datetime.now(),
                    snr=row["snr"],
                    rssi=row["rssi"],
                    latency_ms=row["latency_ms"],
                    latitude=row["latitude"],
                    longitude=row["longitude"],
                ))

            return hops

    def register_callback(self, callback: Callable[[MeshPacket], None]) -> None:
        """Register callback for new packets."""
        with self._lock:
            self._callbacks.append(callback)

    def get_stats(self) -> Dict[str, Any]:
        """Get capture statistics."""
        with self._lock:
            stats = dict(self._stats)

        stats["packet_count"] = self.get_packet_count()
        stats["meshtastic_count"] = self.get_packet_count(PacketProtocol.MESHTASTIC)
        stats["rns_count"] = self.get_packet_count(PacketProtocol.RNS)

        return stats

    def _maybe_cleanup(self) -> None:
        """Periodically clean up old packets."""
        now = time.time()
        if now - self._last_cleanup < self.CLEANUP_INTERVAL:
            return

        self._last_cleanup = now
        self._cleanup_old_packets()

    def _cleanup_old_packets(self) -> int:
        """Remove packets older than retention OR over the row-count cap.

        Two-pass: time-based DELETE first (a quiet mesh could otherwise
        hoard month-old packets indefinitely under just the row cap),
        then count-based DELETE if still over MAX_PACKETS. Returns total
        deleted across both passes."""
        deleted = 0
        with self._get_connection() as conn:
            if self._retention_hours and self._retention_hours > 0:
                cutoff_iso = (
                    datetime.now() - timedelta(hours=self._retention_hours)
                ).isoformat()
                cursor = conn.execute(
                    "DELETE FROM packets WHERE timestamp < ?", (cutoff_iso,)
                )
                deleted += cursor.rowcount or 0
            count = conn.execute("SELECT COUNT(*) FROM packets").fetchone()[0]
            if count > self._max_packets:
                to_delete = count - self._max_packets
                conn.execute("""
                    DELETE FROM packets WHERE id IN (
                        SELECT id FROM packets
                        ORDER BY timestamp ASC
                        LIMIT ?
                    )
                """, (to_delete,))
                deleted += to_delete
        if deleted:
            logger.debug(f"Cleaned up {deleted} old packets")
        return deleted

    def clear_all(self) -> int:
        """Clear all captured packets."""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM packets")
            deleted = cursor.rowcount
            conn.execute("DELETE FROM path_traces")
            return deleted


# =============================================================================
# TRAFFIC STATISTICS
# =============================================================================

@dataclass
class TrafficStats:
    """Aggregated traffic statistics."""
    total_packets: int = 0
    total_bytes: int = 0

    packets_by_protocol: Dict[str, int] = field(default_factory=dict)
    packets_by_port: Dict[str, int] = field(default_factory=dict)
    packets_by_direction: Dict[str, int] = field(default_factory=dict)

    # Time series data (for graphing)
    packets_per_minute: List[Tuple[datetime, int]] = field(default_factory=list)
    bytes_per_minute: List[Tuple[datetime, int]] = field(default_factory=list)

    # Hop statistics
    avg_hops: float = 0.0
    max_hops: int = 0
    hops_distribution: Dict[int, int] = field(default_factory=dict)

    # Signal statistics
    avg_snr: Optional[float] = None
    avg_rssi: Optional[float] = None
    snr_distribution: Dict[int, int] = field(default_factory=dict)

    # Top nodes
    top_sources: List[Tuple[str, int]] = field(default_factory=list)
    top_destinations: List[Tuple[str, int]] = field(default_factory=list)


class TrafficAnalyzer:
    """
    Analyzes captured traffic for statistics and patterns.

    Provides:
    - Packet/byte counts by protocol, port, direction
    - Time series data for I/O graphs
    - Hop count distribution
    - Signal quality statistics
    - Top talkers/listeners
    """

    def __init__(self, capture: TrafficCapture):
        self._capture = capture

    def get_stats(self, since: Optional[datetime] = None,
                  until: Optional[datetime] = None) -> TrafficStats:
        """
        Calculate traffic statistics for a time range.

        Args:
            since: Start of time range (default: last 24 hours)
            until: End of time range (default: now)

        Returns:
            TrafficStats with aggregated data
        """
        if since is None:
            since = datetime.now() - timedelta(hours=24)
        if until is None:
            until = datetime.now()

        stats = TrafficStats()

        # Get all packets in range
        packets = self._capture.get_packets(
            limit=10000,
            since=since,
            until=until,
        )

        if not packets:
            return stats

        stats.total_packets = len(packets)
        stats.total_bytes = sum(p.size for p in packets)

        # Aggregate by protocol
        for p in packets:
            proto = p.protocol.value
            stats.packets_by_protocol[proto] = stats.packets_by_protocol.get(proto, 0) + 1

        # Aggregate by port
        for p in packets:
            if p.port_name:
                stats.packets_by_port[p.port_name] = stats.packets_by_port.get(p.port_name, 0) + 1

        # Aggregate by direction
        for p in packets:
            direction = p.direction.value
            stats.packets_by_direction[direction] = stats.packets_by_direction.get(direction, 0) + 1

        # Hop statistics
        hops = [p.hops_taken for p in packets if p.hops_taken > 0]
        if hops:
            stats.avg_hops = sum(hops) / len(hops)
            stats.max_hops = max(hops)
            for h in hops:
                stats.hops_distribution[h] = stats.hops_distribution.get(h, 0) + 1

        # Signal statistics
        snr_values = [p.snr for p in packets if p.snr is not None]
        rssi_values = [p.rssi for p in packets if p.rssi is not None]

        if snr_values:
            stats.avg_snr = sum(snr_values) / len(snr_values)
            for snr in snr_values:
                bucket = int(snr // 5) * 5  # 5 dB buckets
                stats.snr_distribution[bucket] = stats.snr_distribution.get(bucket, 0) + 1

        if rssi_values:
            stats.avg_rssi = sum(rssi_values) / len(rssi_values)

        # Top sources
        source_counts: Dict[str, int] = defaultdict(int)
        for p in packets:
            if p.source:
                source_counts[p.source] += 1
        stats.top_sources = sorted(source_counts.items(), key=lambda x: x[1], reverse=True)[:10]

        # Top destinations
        dest_counts: Dict[str, int] = defaultdict(int)
        for p in packets:
            if p.destination:
                dest_counts[p.destination] += 1
        stats.top_destinations = sorted(dest_counts.items(), key=lambda x: x[1], reverse=True)[:10]

        # Time series (packets per minute)
        minute_buckets: Dict[str, int] = defaultdict(int)
        byte_buckets: Dict[str, int] = defaultdict(int)
        for p in packets:
            bucket = p.timestamp.replace(second=0, microsecond=0)
            bucket_key = bucket.isoformat()
            minute_buckets[bucket_key] += 1
            byte_buckets[bucket_key] += p.size

        stats.packets_per_minute = [
            (datetime.fromisoformat(k), v)
            for k, v in sorted(minute_buckets.items())
        ]
        stats.bytes_per_minute = [
            (datetime.fromisoformat(k), v)
            for k, v in sorted(byte_buckets.items())
        ]

        return stats

    def get_node_stats(self, node_id: str,
                       since: Optional[datetime] = None) -> Dict[str, Any]:
        """Get statistics for a specific node."""
        packets = self._capture.get_packets(
            limit=5000,
            source=node_id,
            since=since,
        )

        sent = [p for p in packets if p.source == node_id]
        received = [p for p in packets if p.destination == node_id]

        snr_values = [p.snr for p in packets if p.snr is not None]
        rssi_values = [p.rssi for p in packets if p.rssi is not None]

        return {
            "node_id": node_id,
            "packets_sent": len(sent),
            "packets_received": len(received),
            "bytes_sent": sum(p.size for p in sent),
            "bytes_received": sum(p.size for p in received),
            "avg_snr": sum(snr_values) / len(snr_values) if snr_values else None,
            "avg_rssi": sum(rssi_values) / len(rssi_values) if rssi_values else None,
            "ports_used": list(set(p.port_name for p in sent if p.port_name)),
        }


# =============================================================================
# TRAFFIC LOGGER (HUMAN-READABLE LOG FILE)
# =============================================================================

class TrafficLogger:
    """
    Writes mesh traffic to a human-readable log file.

    Provides real-time visibility into mesh traffic similar to Wireshark's
    packet log, but in a format suitable for terminal viewing.
    """

    def __init__(self, log_path: Optional[str] = None):
        if log_path is None:
            log_dir = get_real_user_home() / ".cache" / "meshforge" / "logs"
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
            except PermissionError as e:
                # Parent dir may be root-owned from a prior sudo install — degrade
                # gracefully rather than crash. Packet logging will be disabled.
                logger.warning(
                    "Traffic log dir not writable (%s); packet logging disabled. "
                    "Fix: sudo chown -R $(id -un):$(id -gn) %s",
                    e, log_dir.parent,
                )
                self._log_path = None
                self._enabled = False
                self._lock = threading.Lock()
                self._packet_count = 0
                return
            log_path = str(log_dir / "traffic.log")

        self._log_path = log_path
        self._enabled = True
        self._lock = threading.Lock()
        self._packet_count = 0

        # Create/truncate log file with header
        self._write_header()

    def _write_header(self) -> None:
        """Write log file header."""
        try:
            with open(self._log_path, 'w') as f:
                f.write("=" * 100 + "\n")
                f.write(" MESHFORGE TRAFFIC LOG ".center(100, "=") + "\n")
                f.write("=" * 100 + "\n")
                f.write(f"Started: {datetime.now().isoformat()}\n")
                f.write(f"Log file: {self._log_path}\n")
                f.write("-" * 100 + "\n")
                f.write(f"{'Time':<12} {'Dir':<4} {'Proto':<10} {'Source':<14} "
                        f"{'Dest':<14} {'Port':<16} {'Hops':<5} {'SNR':<8} {'Size':<8}\n")
                f.write("-" * 100 + "\n")
        except (IOError, PermissionError) as e:
            logger.warning(
                "Traffic log not writable (%s); disabling packet logging. "
                "Fix: sudo chown -R $(id -un):$(id -gn) ~/.cache/meshforge",
                e,
            )
            self._enabled = False

    def log_packet(self, packet: 'MeshPacket') -> None:
        """Log a packet to the traffic log file."""
        if not self._enabled:
            return

        with self._lock:
            try:
                self._packet_count += 1

                # Format packet line
                time_str = packet.timestamp.strftime("%H:%M:%S.%f")[:12]
                dir_sym = {"inbound": "<-", "outbound": "->", "relayed": "<>", "internal": ".."}
                dir_str = dir_sym.get(packet.direction.value, "??")
                proto = packet.protocol.value[:10]
                src = packet.source[:14] if packet.source else "?"
                dst = packet.destination[:14] if packet.destination else "bcast"
                port = (packet.port_name[:16] if packet.port_name else "-")
                hops = str(packet.hops_taken) if packet.hops_taken else "-"
                snr = f"{packet.snr:.1f}" if packet.snr is not None else "-"
                size = str(packet.size) if packet.size else "-"

                line = f"{time_str:<12} {dir_str:<4} {proto:<10} {src:<14} {dst:<14} {port:<16} {hops:<5} {snr:<8} {size:<8}\n"

                # Check file size and rotate if needed
                self._maybe_rotate()

                with open(self._log_path, 'a') as f:
                    f.write(line)

            except IOError as e:
                logger.debug(f"Failed to write traffic log: {e}")

    def _maybe_rotate(self) -> None:
        """Rotate log file if it exceeds max size."""
        try:
            if Path(self._log_path).stat().st_size > TRAFFIC_LOG_MAX_SIZE:
                # Rotate backup files
                for i in range(TRAFFIC_LOG_BACKUP_COUNT - 1, 0, -1):
                    src = f"{self._log_path}.{i}"
                    dst = f"{self._log_path}.{i + 1}"
                    if Path(src).exists():
                        Path(src).rename(dst)

                # Move current to .1
                Path(self._log_path).rename(f"{self._log_path}.1")

                # Start fresh
                self._write_header()
        except (IOError, OSError):
            pass

    def get_log_path(self) -> str:
        """Get the path to the traffic log file."""
        return self._log_path

    def get_packet_count(self) -> int:
        """Get number of packets logged."""
        return self._packet_count

    def enable(self) -> None:
        """Enable traffic logging."""
        self._enabled = True

    def disable(self) -> None:
        """Disable traffic logging."""
        self._enabled = False

    def is_enabled(self) -> bool:
        """Check if logging is enabled."""
        return self._enabled

    def clear(self) -> None:
        """Clear the log file."""
        self._packet_count = 0
        self._write_header()


# =============================================================================
# PACKET ARCHIVE (LONG-TERM RAW PACKET STORAGE)
# =============================================================================

class PacketArchive:
    """
    Long-term raw packet archival inspired by Meshstellar.

    Separate from TrafficCapture's rolling 10K buffer.  Stores raw packet
    bytes as SQLite BLOBs with time-based rotation and compaction.

    Usage:
        archive = PacketArchive()
        archive.archive(packet)

        # Query last 24 hours of Meshtastic packets
        packets = archive.query(
            since=datetime.now() - timedelta(hours=24),
            protocol="meshtastic",
        )

        # Compact old data
        archive.compact()
    """

    DEFAULT_MAX_AGE_DAYS = 30
    DEFAULT_MAX_SIZE_MB = 500

    def __init__(
        self,
        db_path: Optional[str] = None,
        max_age_days: int = DEFAULT_MAX_AGE_DAYS,
        max_size_mb: int = DEFAULT_MAX_SIZE_MB,
    ):
        """
        Initialize the packet archive.

        Args:
            db_path: Path to SQLite database (default: ~/.config/meshforge/packet_archive.db)
            max_age_days: Auto-compact packets older than this (default 30)
            max_size_mb: Warn when database exceeds this size (default 500)
        """
        if db_path is None:
            config_dir = get_real_user_home() / ".config" / "meshforge"
            config_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(config_dir / "packet_archive.db")

        self._db_path = db_path
        self._max_age_days = max_age_days
        self._max_size_mb = max_size_mb
        self._lock = threading.Lock()
        self._enabled = False
        self._archived_count = 0

        self._init_db()

    @contextmanager
    def _get_connection(self):
        """Get database connection with context management.

        Tuned via utils.db_helpers.connect_tuned (WAL + sync=NORMAL +
        64MB journal cap). Phase 1 of post-2026-04-26 fleet closure."""
        conn = connect_tuned(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize archive database schema."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS archived_packets (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    source TEXT,
                    destination TEXT,
                    port_name TEXT,
                    raw_data BLOB,
                    size INTEGER,
                    metadata TEXT
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_archive_timestamp
                ON archived_packets(timestamp DESC)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_archive_protocol
                ON archived_packets(protocol)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_archive_source
                ON archived_packets(source)
            """)

    def enable(self) -> None:
        """Enable packet archival."""
        self._enabled = True

    def disable(self) -> None:
        """Disable packet archival."""
        self._enabled = False

    @property
    def enabled(self) -> bool:
        """Check if archival is enabled."""
        return self._enabled

    def archive(self, packet: MeshPacket) -> bool:
        """
        Archive a single packet with its raw bytes.

        Args:
            packet: MeshPacket to archive

        Returns:
            True if archived successfully
        """
        if not self._enabled:
            return False

        metadata = {}
        if packet.snr is not None:
            metadata["snr"] = packet.snr
        if packet.rssi is not None:
            metadata["rssi"] = packet.rssi
        if packet.channel is not None:
            metadata["channel"] = packet.channel
        if packet.hop_limit is not None:
            metadata["hop_limit"] = packet.hop_limit
        if packet.hops_taken is not None:
            metadata["hops_taken"] = packet.hops_taken
        if packet.decoded_payload:
            metadata["decoded_payload"] = packet.decoded_payload

        with self._lock:
            try:
                with self._get_connection() as conn:
                    conn.execute(
                        """INSERT OR IGNORE INTO archived_packets
                           (id, timestamp, protocol, direction, source,
                            destination, port_name, raw_data, size, metadata)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            packet.id,
                            packet.timestamp.isoformat(),
                            packet.protocol.value if hasattr(packet.protocol, 'value') else str(packet.protocol),
                            packet.direction.value if hasattr(packet.direction, 'value') else str(packet.direction),
                            packet.source,
                            packet.destination,
                            packet.port_name,
                            packet.raw_bytes,
                            packet.size,
                            json.dumps(metadata) if metadata else None,
                        ),
                    )
                self._archived_count += 1
                return True
            except Exception as e:
                logger.error(f"Failed to archive packet {packet.id}: {e}")
                return False

    def query(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        protocol: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """
        Query archived packets with optional filters.

        Args:
            since: Start time filter
            until: End time filter
            protocol: Protocol filter (meshtastic, rns, etc.)
            source: Source node filter
            limit: Maximum results (default 1000)

        Returns:
            List of archived packet dicts
        """
        conditions = []
        params: list = []

        if since:
            conditions.append("timestamp >= ?")
            params.append(since.isoformat())
        if until:
            conditions.append("timestamp <= ?")
            params.append(until.isoformat())
        if protocol:
            conditions.append("protocol = ?")
            params.append(protocol.upper())
        if source:
            conditions.append("source = ?")
            params.append(source)

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        with self._lock:
            try:
                with self._get_connection() as conn:
                    rows = conn.execute(
                        f"SELECT * FROM archived_packets{where} ORDER BY timestamp DESC LIMIT ?",
                        params,
                    ).fetchall()
                    return [dict(row) for row in rows]
            except Exception as e:
                logger.error(f"Archive query failed: {e}")
                return []

    def compact(self) -> int:
        """
        Remove packets older than max_age_days.

        Returns:
            Number of packets removed
        """
        cutoff = datetime.now() - timedelta(days=self._max_age_days)
        with self._lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.execute(
                        "DELETE FROM archived_packets WHERE timestamp < ?",
                        (cutoff.isoformat(),),
                    )
                    removed = cursor.rowcount
                    if removed > 0:
                        conn.execute("VACUUM")
                        logger.info(
                            f"Compacted archive: removed {removed} packets "
                            f"older than {self._max_age_days} days"
                        )
                    return removed
            except Exception as e:
                logger.error(f"Archive compaction failed: {e}")
                return 0

    def get_stats(self) -> Dict[str, Any]:
        """
        Get archive statistics.

        Returns:
            Dict with count, size, oldest/newest timestamps, protocol breakdown
        """
        with self._lock:
            try:
                with self._get_connection() as conn:
                    row = conn.execute(
                        """SELECT
                            COUNT(*) as total,
                            COALESCE(SUM(size), 0) as total_bytes,
                            MIN(timestamp) as oldest,
                            MAX(timestamp) as newest
                        FROM archived_packets"""
                    ).fetchone()

                    protocols = {}
                    for prow in conn.execute(
                        "SELECT protocol, COUNT(*) as cnt FROM archived_packets GROUP BY protocol"
                    ).fetchall():
                        protocols[prow["protocol"]] = prow["cnt"]

                    db_size_mb = 0.0
                    try:
                        db_size_mb = os.path.getsize(self._db_path) / (1024 * 1024)
                    except OSError:
                        pass

                    return {
                        "total_packets": row["total"],
                        "total_bytes": row["total_bytes"],
                        "oldest_packet": row["oldest"],
                        "newest_packet": row["newest"],
                        "protocols": protocols,
                        "db_size_mb": round(db_size_mb, 2),
                        "max_age_days": self._max_age_days,
                        "session_archived": self._archived_count,
                    }
            except Exception as e:
                logger.error(f"Failed to get archive stats: {e}")
                return {"error": str(e)}
