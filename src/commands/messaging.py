"""
MeshForge Native Messaging

Unified messaging across Meshtastic and RNS networks.
Inspired by meshing-around patterns but native to MeshForge architecture.

Usage:
    from commands import messaging

    # Send message
    result = messaging.send_message("Hello mesh!", destination="!abcd1234")

    # Get messages
    result = messaging.get_messages(limit=20)
"""

import logging
import time
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

from .base import CommandResult

# Import centralized path utility
try:
    from utils.paths import get_real_user_home
except ImportError:
    import os
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()

logger = logging.getLogger(__name__)

# Maximum message length before chunking
MAX_MESSAGE_LENGTH = 160


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
    conn = sqlite3.connect(str(db_path))
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
    channel: int = 0
) -> CommandResult:
    """
    Send message to mesh network.

    Args:
        content: Message text
        destination: Node ID (!abcd1234) or RNS hash, None for broadcast
        network: "meshtastic", "rns", or "auto"
        channel: Channel number (0 = DM, 1+ = public channels)

    Returns:
        CommandResult with delivery status
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
        # Get bridge instance
        try:
            from gateway import RNSMeshtasticBridge
            # Would get active bridge here
            # bridge = get_active_bridge()
        except ImportError:
            pass

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
        conn.close()

        # TODO: Actually send via bridge when available
        # For now, just store and report

        return CommandResult.ok(
            f"Message queued ({len(chunks)} chunk(s))",
            data={
                'message_id': message_id,
                'chunks': len(chunks),
                'network': network,
                'destination': destination,
                'length': len(content),
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
