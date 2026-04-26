"""
MeshForge Native Messaging

Unified messaging across Meshtastic and RNS networks.
Inspired by meshing-around patterns but native to MeshForge architecture.

Usage:
    from commands import messaging

    # Start listening for messages (enables RX)
    result = messaging.start_receiving()

    # Send message (uses smart hop limits)
    result = messaging.send_message("Hello mesh!", destination="!abcd1234")

    # Send with high reliability for difficult paths
    result = messaging.send_message("Emergency!", destination="!abcd1234", high_reliability=True)

    # Get messages
    result = messaging.get_messages(limit=20)

    # Diagnose messaging issues
    result = messaging.diagnose()
"""

import logging
import time
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field

from .base import CommandResult
from utils.db_helpers import connect_tuned
from utils.paths import get_real_user_home

from utils.event_bus import emit_message
from commands import gateway as cmd_gateway
from commands import meshtastic as cmd_meshtastic
from utils.message_listener import (
    start_listener, get_listener, stop_listener,
    get_listener_status, diagnose_pubsub,
)

logger = logging.getLogger(__name__)

# Maximum message length before chunking
MAX_MESSAGE_LENGTH = 160

# Auto-prune cadence for messages.db. Without this the DB grew unbounded
# because clear_messages() is manual-only — same class of bug as the
# fleet-host 2026-04-26 node_history wedge.
_MESSAGE_RETENTION_DAYS = 30
_MESSAGE_PRUNE_INTERVAL_SECONDS = 3600
_last_message_prune_ts: float = 0.0


@dataclass
class Message:
    """Represents a mesh message."""
    id: Optional[int] = None
    timestamp: datetime = field(default_factory=datetime.now)
    network: str = "meshtastic"  # meshtastic, rns
    from_id: str = ""
    to_id: Optional[str] = None  # None = broadcast
    content: str = ""
    channel: int = 0  # 0 = DM, 1+ = channels
    is_dm: bool = True
    snr: Optional[float] = None
    rssi: Optional[int] = None
    delivered: bool = False

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'network': self.network,
            'from_id': self.from_id,
            'to_id': self.to_id,
            'content': self.content,
            'channel': self.channel,
            'is_dm': self.is_dm,
            'snr': self.snr,
            'rssi': self.rssi,
            'delivered': self.delivered,
        }


def _get_db_path() -> Path:
    """Get path to message database."""
    db_dir = get_real_user_home() / ".config" / "meshforge"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "messages.db"


def _init_db() -> sqlite3.Connection:
    """Initialize message database."""
    db_path = _get_db_path()
    conn = connect_tuned(db_path)
    conn.row_factory = sqlite3.Row

    conn.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            network TEXT NOT NULL,
            from_id TEXT NOT NULL,
            to_id TEXT,
            content TEXT NOT NULL,
            channel INTEGER DEFAULT 0,
            is_dm BOOLEAN DEFAULT 1,
            snr REAL,
            rssi INTEGER,
            delivered BOOLEAN DEFAULT 0
        )
    ''')

    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_messages_timestamp
        ON messages(timestamp DESC)
    ''')

    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_messages_from
        ON messages(from_id)
    ''')

    conn.commit()
    return conn


def _maybe_prune_messages(now: Optional[float] = None) -> None:
    """Hourly auto-prune of messages older than retention.

    Same pattern as NodeHistoryDB._maybe_prune. Idempotent and cheap when
    the cadence window hasn't elapsed. Called from store_incoming so it
    runs naturally on a live RX path; quiet meshes still see prunes via
    explicit clear_messages() calls.
    """
    global _last_message_prune_ts
    if _MESSAGE_PRUNE_INTERVAL_SECONDS <= 0 or _MESSAGE_RETENTION_DAYS <= 0:
        return
    if now is None:
        now = time.time()
    if now - _last_message_prune_ts < _MESSAGE_PRUNE_INTERVAL_SECONDS:
        return
    try:
        conn = _init_db()
        try:
            cursor = conn.execute(
                "DELETE FROM messages WHERE timestamp < datetime('now', ? || ' days')",
                (f'-{_MESSAGE_RETENTION_DAYS}',),
            )
            conn.commit()
            deleted = cursor.rowcount
            _last_message_prune_ts = now
            if deleted > 0:
                logger.info(
                    f"Messages auto-prune: deleted {deleted} rows older than {_MESSAGE_RETENTION_DAYS}d"
                )
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.error(f"Messages auto-prune failed: {e}")


def _chunk_message(content: str, max_length: int = MAX_MESSAGE_LENGTH) -> List[str]:
    """
    Split long message into chunks for reliable delivery.

    Based on meshing-around pattern for multi-hop reliability.
    """
    if len(content) <= max_length:
        return [content]

    chunks = []
    words = content.split()
    current_chunk = ""

    for word in words:
        if len(current_chunk) + len(word) + 1 <= max_length:
            current_chunk = f"{current_chunk} {word}".strip()
        else:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = word

    if current_chunk:
        chunks.append(current_chunk)

    # Add chunk indicators
    if len(chunks) > 1:
        total = len(chunks)
        chunks = [f"[{i+1}/{total}] {chunk}" for i, chunk in enumerate(chunks)]

    return chunks


# ============================================================================
# PUBLIC API
# ============================================================================

def send_message(
    content: str,
    destination: Optional[str] = None,
    network: str = "auto",
    channel: int = 0,
    high_reliability: bool = False
) -> CommandResult:
    """
    Send message to mesh network with smart routing.

    Args:
        content: Message text
        destination: Node ID (!abcd1234) or RNS hash, None for broadcast
        network: "meshtastic", "rns", or "auto"
        channel: Channel number (0 = DM, 1+ = public channels)
        high_reliability: Use max hops (7) for difficult paths or emergency

    Returns:
        CommandResult with delivery status

    Note on routing (from Meshtastic mesh-algo):
        - DMs use next-hop routing: first message floods to find path,
          subsequent messages use shortest discovered path
        - Broadcasts are flooded to the entire mesh
        - high_reliability=True forces max hops for difficult paths
    """
    if not content:
        return CommandResult.fail("Message content cannot be empty")

    if not content.strip():
        return CommandResult.fail("Message cannot be only whitespace")

    # Determine network if auto
    if network == "auto":
        if destination and destination.startswith('!'):
            network = "meshtastic"
        elif destination and len(destination) == 32:
            network = "rns"
        else:
            network = "meshtastic"  # Default to Meshtastic for broadcast

    # Chunk message if needed
    chunks = _chunk_message(content)

    try:
        # Get bridge instance (if available)
        # RNSMeshtasticBridge available via direct import
        # bridge = get_active_bridge()  # Would get active bridge here

        # Store message
        conn = _init_db()
        cursor = conn.cursor()

        for chunk in chunks:
            cursor.execute('''
                INSERT INTO messages (network, from_id, to_id, content, channel, is_dm, delivered)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (network, "local", destination, chunk, channel, destination is not None, False))

        conn.commit()
        message_id = cursor.lastrowid

        # Actually send the message
        # Priority: 1) Gateway bridge (if running) 2) Direct meshtastic CLI
        send_success = False
        send_error = None

        if network == "meshtastic":
            # Try gateway first, then fall back to direct CLI
            gateway_available = False
            try:
                status = cmd_gateway.get_status()
                if status.success and status.data.get('running') and status.data.get('meshtastic_connected'):
                    gateway_available = True
            except Exception:
                pass

            if gateway_available:
                # Use gateway bridge
                try:
                    for chunk in chunks:
                        result = cmd_gateway.send_to_meshtastic(chunk, destination, channel)
                        if not result.success:
                            send_error = result.message
                            break
                    else:
                        send_success = True
                except Exception as e:
                    send_error = f"Gateway send failed: {e}"
                    logger.debug(f"Gateway send failed, will try direct: {e}")
            else:
                logger.debug("Gateway not available, using direct meshtastic command")

            # Fallback to direct meshtastic CLI if gateway failed
            if not send_success:
                try:
                    for chunk in chunks:
                        # Use send_dm for direct messages, send_broadcast for channels
                        if destination and destination != '!ffffffff':
                            result = cmd_meshtastic.send_dm(
                                text=chunk,
                                dest=destination,
                                ack=False,
                                high_reliability=high_reliability
                            )
                        else:
                            result = cmd_meshtastic.send_broadcast(
                                text=chunk,
                                channel_index=channel if channel > 0 else 1,
                                hop_limit=7 if high_reliability else 3
                            )
                        if not result.success:
                            send_error = result.message
                            break
                    else:
                        send_success = True
                        send_error = None  # Clear any previous error
                except Exception as e:
                    if not send_error:  # Don't overwrite gateway error
                        send_error = f"Direct send failed: {e}"
                    logger.error(f"Failed to send via direct meshtastic: {e}")

        elif network == "rns":
            # RNS messages go through gateway only
            try:
                # Validate destination is valid hex before attempting conversion
                if destination:
                    clean_dest = destination.lstrip('!')
                    if not all(c in '0123456789abcdefABCDEF' for c in clean_dest):
                        return CommandResult.fail(
                            f"Invalid RNS destination: {destination}\n"
                            "Must be a hex hash (e.g. a1b2c3d4e5f6...)"
                        )
                for chunk in chunks:
                    dest_bytes = bytes.fromhex(destination.lstrip('!')) if destination else None
                    result = cmd_gateway.send_to_rns(chunk, dest_bytes)
                    if not result.success:
                        send_error = result.message
                        break
                else:
                    send_success = True
            except Exception as e:
                send_error = f"RNS send failed: {e}"
                logger.error(f"Failed to send via RNS: {e}")

        # Update delivery status
        if send_success:
            cursor = conn.cursor()
            cursor.execute('UPDATE messages SET delivered = 1 WHERE id = ?', (message_id,))
            conn.commit()

        conn.close()

        if send_success:
            # Emit TX event to EventBus for status bar and subscribers
            try:
                emit_message(
                    direction='tx',
                    content=content[:100],  # Truncate for event
                    node_id=destination or "broadcast",
                    network=network,
                    raw_data={
                        'message_id': message_id,
                        'chunks': len(chunks),
                        'destination': destination,
                    },
                )
            except Exception as e:
                logger.debug(f"TX event emission failed: {e}")

            return CommandResult.ok(
                f"Message sent ({len(chunks)} chunk(s))",
                data={
                    'message_id': message_id,
                    'chunks': len(chunks),
                    'network': network,
                    'destination': destination,
                    'length': len(content),
                    'delivered': True,
                }
            )
        else:
            return CommandResult.ok(
                f"Message queued but not sent: {send_error}",
                data={
                    'message_id': message_id,
                    'chunks': len(chunks),
                    'network': network,
                    'destination': destination,
                    'length': len(content),
                    'delivered': False,
                    'error': send_error,
                }
            )

    except Exception as e:
        logger.error(f"Failed to send message: {e}")
        return CommandResult.fail(f"Failed to send message: {e}")


def get_messages(
    limit: int = 50,
    network: str = "all",
    conversation_with: Optional[str] = None
) -> CommandResult:
    """
    Retrieve messages from storage.

    Args:
        limit: Maximum messages to return
        network: Filter by network ("all", "meshtastic", "rns")
        conversation_with: Filter to conversation with specific node

    Returns:
        CommandResult with message list
    """
    try:
        conn = _init_db()
        cursor = conn.cursor()

        query = "SELECT * FROM messages"
        params = []
        conditions = []

        if network != "all":
            conditions.append("network = ?")
            params.append(network)

        if conversation_with:
            conditions.append("(from_id = ? OR to_id = ?)")
            params.extend([conversation_with, conversation_with])

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        messages = []
        for row in rows:
            msg = Message(
                id=row['id'],
                timestamp=datetime.fromisoformat(row['timestamp']) if row['timestamp'] else None,
                network=row['network'],
                from_id=row['from_id'],
                to_id=row['to_id'],
                content=row['content'],
                channel=row['channel'],
                is_dm=bool(row['is_dm']),
                snr=row['snr'],
                rssi=row['rssi'],
                delivered=bool(row['delivered']),
            )
            messages.append(msg.to_dict())

        return CommandResult.ok(
            f"Retrieved {len(messages)} messages",
            data={'messages': messages, 'count': len(messages)}
        )

    except Exception as e:
        logger.error(f"Failed to get messages: {e}")
        return CommandResult.fail(f"Failed to retrieve messages: {e}")


def get_conversations() -> CommandResult:
    """
    Get list of active conversations.

    Returns:
        CommandResult with conversation list
    """
    try:
        conn = _init_db()
        cursor = conn.cursor()

        # Get unique conversation partners
        cursor.execute('''
            SELECT
                CASE
                    WHEN from_id = 'local' THEN to_id
                    ELSE from_id
                END as partner,
                network,
                MAX(timestamp) as last_message,
                COUNT(*) as message_count
            FROM messages
            WHERE from_id != to_id OR to_id IS NOT NULL
            GROUP BY partner, network
            ORDER BY last_message DESC
        ''')

        rows = cursor.fetchall()
        conn.close()

        conversations = []
        for row in rows:
            if row['partner']:  # Skip broadcast messages
                conversations.append({
                    'partner': row['partner'],
                    'network': row['network'],
                    'last_message': row['last_message'],
                    'message_count': row['message_count'],
                })

        return CommandResult.ok(
            f"Found {len(conversations)} conversations",
            data={'conversations': conversations}
        )

    except Exception as e:
        logger.error(f"Failed to get conversations: {e}")
        return CommandResult.fail(f"Failed to get conversations: {e}")


def store_incoming(
    from_id: str,
    content: str,
    network: str = "meshtastic",
    to_id: Optional[str] = None,
    channel: int = 0,
    snr: Optional[float] = None,
    rssi: Optional[int] = None
) -> CommandResult:
    """
    Store incoming message from mesh network.

    Called by gateway bridge when messages are received.

    Args:
        from_id: Sender node ID
        content: Message content
        network: Source network
        to_id: Destination (None for broadcast)
        channel: Channel number
        snr: Signal-to-noise ratio
        rssi: Signal strength

    Returns:
        CommandResult with storage confirmation
    """
    try:
        conn = _init_db()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO messages
            (network, from_id, to_id, content, channel, is_dm, snr, rssi, delivered)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
        ''', (network, from_id, to_id, content, channel, to_id is not None, snr, rssi))

        conn.commit()
        message_id = cursor.lastrowid
        conn.close()

        logger.info(f"Stored message {message_id} from {from_id}")

        _maybe_prune_messages()

        return CommandResult.ok(
            f"Message stored",
            data={'message_id': message_id}
        )

    except Exception as e:
        logger.error(f"Failed to store message: {e}")
        return CommandResult.fail(f"Failed to store message: {e}")


def get_stats() -> CommandResult:
    """
    Get messaging statistics.

    Returns:
        CommandResult with message counts and stats
    """
    try:
        conn = _init_db()
        cursor = conn.cursor()

        # Total messages
        cursor.execute("SELECT COUNT(*) as total FROM messages")
        total = cursor.fetchone()['total']

        # By network
        cursor.execute('''
            SELECT network, COUNT(*) as count
            FROM messages
            GROUP BY network
        ''')
        by_network = {row['network']: row['count'] for row in cursor.fetchall()}

        # Sent vs received
        cursor.execute("SELECT COUNT(*) as sent FROM messages WHERE from_id = 'local'")
        sent = cursor.fetchone()['sent']
        received = total - sent

        # Last 24 hours
        cursor.execute('''
            SELECT COUNT(*) as recent
            FROM messages
            WHERE timestamp > datetime('now', '-24 hours')
        ''')
        last_24h = cursor.fetchone()['recent']

        conn.close()

        return CommandResult.ok(
            f"{total} total messages",
            data={
                'total': total,
                'sent': sent,
                'received': received,
                'last_24h': last_24h,
                'by_network': by_network,
            }
        )

    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        return CommandResult.fail(f"Failed to get stats: {e}")


def clear_messages(older_than_days: int = 30) -> CommandResult:
    """
    Clear old messages from storage.

    Args:
        older_than_days: Delete messages older than this many days

    Returns:
        CommandResult with deletion count
    """
    try:
        conn = _init_db()
        cursor = conn.cursor()

        cursor.execute('''
            DELETE FROM messages
            WHERE timestamp < datetime('now', ? || ' days')
        ''', (f'-{older_than_days}',))

        deleted = cursor.rowcount
        conn.commit()
        conn.close()

        return CommandResult.ok(
            f"Deleted {deleted} messages older than {older_than_days} days",
            data={'deleted': deleted}
        )

    except Exception as e:
        logger.error(f"Failed to clear messages: {e}")
        return CommandResult.fail(f"Failed to clear messages: {e}")


# ============================================================================
# RX (RECEIVING) FUNCTIONS
# ============================================================================

def start_receiving(
    host: str = "localhost",
    callback: Optional[Callable[[Dict[str, Any]], None]] = None
) -> CommandResult:
    """
    Start listening for incoming messages.

    This enables RX without requiring the full gateway bridge.
    Messages are automatically stored in the database.

    Args:
        host: Meshtastic host (default: localhost for meshtasticd)
        callback: Optional callback for real-time notifications

    Returns:
        CommandResult with listener status

    Usage:
        # Simple - just store messages
        result = messaging.start_receiving()

        # With callback for real-time updates
        def on_message(msg):
            print(f"New message from {msg['from_id']}: {msg['content']}")
        result = messaging.start_receiving(callback=on_message)
    """
    try:
        success = start_listener(host=host)

        if callback:
            listener = get_listener()
            listener.add_callback(callback)

        if success:
            return CommandResult.ok(
                "Message listener started - RX enabled",
                data={
                    'host': host,
                    'status': 'connected',
                    'has_callback': callback is not None,
                }
            )
        else:
            return CommandResult.fail(
                "Failed to start message listener",
                error="Could not connect to meshtastic"
            )

    except Exception as e:
        logger.error(f"Failed to start receiving: {e}")
        return CommandResult.fail(f"Failed to start listener: {e}")


def stop_receiving() -> CommandResult:
    """
    Stop listening for incoming messages.

    Returns:
        CommandResult with status
    """
    try:
        stop_listener()
        return CommandResult.ok("Message listener stopped")
    except Exception as e:
        return CommandResult.fail(f"Error stopping listener: {e}")


def get_rx_status() -> CommandResult:
    """
    Get current RX listener status.

    Returns:
        CommandResult with listener state, message count, etc.
    """
    status = get_listener_status()
    return CommandResult.ok(
        f"RX status: {status['state']}",
        data=status
    )


# ============================================================================
# DIAGNOSTICS
# ============================================================================

def diagnose() -> CommandResult:
    """
    Diagnose messaging configuration and connectivity.

    Checks:
    - Device connection
    - Hop limit setting
    - Device role (affects routing)
    - RX listener status
    - Pubsub subscription

    Returns:
        CommandResult with diagnostic data and recommendations
    """
    return cmd_meshtastic.diagnose_messaging()


def get_routing_info() -> CommandResult:
    """
    Get current routing configuration information.

    Returns:
        CommandResult with hop_limit, device_role, and routing notes
    """
    info = {
        'hop_limit': None,
        'device_role': None,
        'routing_behavior': None,
        'recommendations': [],
    }

    # Get hop limit
    hop_result = cmd_meshtastic.get_hop_limit()
    if hop_result.success and hop_result.data:
        info['hop_limit'] = hop_result.data.get('hop_limit')

    # Get device role
    role_result = cmd_meshtastic.get_device_role()
    if role_result.success and role_result.data:
        info['device_role'] = role_result.data.get('role')
        info['role_description'] = role_result.data.get('description')

    # Determine routing behavior
    role = info['device_role']
    hop = info['hop_limit']

    if role == 'CLIENT_MUTE':
        info['routing_behavior'] = 'Receive only - no rebroadcast'
        info['recommendations'].append(
            "Messages won't be relayed. Good for monitoring, bad for mesh health."
        )
    elif role in ('ROUTER', 'ROUTER_CLIENT'):
        info['routing_behavior'] = 'Full router - always rebroadcasts with max hops'
    elif role == 'REPEATER':
        info['routing_behavior'] = 'Dedicated repeater - minimal processing'
    else:
        info['routing_behavior'] = f'Standard client with hop_limit={hop}'

        if hop and hop < 3:
            info['recommendations'].append(
                f"Low hop limit ({hop}) may cause messages to not reach distant nodes"
            )

    return CommandResult.ok(
        f"Routing: {info['routing_behavior']}",
        data=info
    )
