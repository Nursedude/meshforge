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
import math
import time
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field

from .base import CommandResult
from utils.db_helpers import connect_tuned, ensure_column
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
# 2026-04-26 fleet node_history wedge.
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
    packet_id: Optional[int] = None  # the radio's id; outgoing rows (INBOX-2)

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
            'packet_id': self.packet_id,
        }


def _get_db_path() -> Path:
    """Get path to message database."""
    db_dir = get_real_user_home() / ".config" / "meshforge"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "messages.db"


#: Meshtastic packet ids are 32-bit and never 0 (0 = "not assigned").
_PACKET_ID_MAX = 0xFFFFFFFF


def _schema_is_current(conn) -> bool:
    """Probe THIS file's schema on every connection (two prepares, no rows).

    BIC-2 r6 keyed a once-per-process cache on the file's identity; r7 showed
    ext4 hands a deleted file's inode to the next one (measured 20 of 20), and
    a backup copied over the store in place keeps it — the cache then skipped
    the migration and every id'd RX was lost. Asking the file is the seam."""
    try:
        conn.execute("SELECT packet_id FROM messages LIMIT 0")
        conn.execute("SELECT value FROM messages_meta LIMIT 0")
        # r2 NIT: every column a reader or writer names, not one
        conn.execute("SELECT message_id, packet_id, alias, state, reason, rx_ts, "
                     "skewed, kept_at FROM heard_kept LIMIT 0")
        conn.execute("SELECT message_id, packet_id, state, detail, reason, said_by, "
                     "latency_s, sent_n, kept_at FROM dm_kept LIMIT 0")
    except sqlite3.OperationalError:
        return False
    return True


def _valid_packet_id(packet_id) -> Optional[int]:
    """The id if it can be a real Meshtastic packet id, else None. An
    impossible value is logged LOUDLY (hfm #3) — the message row is still
    kept, with the id unknown."""
    if packet_id is None:
        return None
    if (isinstance(packet_id, bool) or not isinstance(packet_id, int)
            or not 0 < packet_id <= _PACKET_ID_MAX):
        logger.warning("messages: refusing impossible packet_id %r "
                       "(stored as unknown)", packet_id)
        return None
    return packet_id


_RX_ERA_KEY = "rx_id_era"


def _record_rx_era(conn) -> None:
    """Write the era ONCE: the first received row stored with a packet id.
    INSERT OR IGNORE = write-once; called at schema check (backfill) and
    after every id'd store_incoming, always BEFORE a prune can run."""
    conn.execute(
        "INSERT OR IGNORE INTO messages_meta (key, value) "
        "SELECT ?, MIN(id) FROM messages WHERE packet_id IS NOT NULL "
        "AND from_id != 'local' HAVING MIN(id) IS NOT NULL", (_RX_ERA_KEY,))


def first_received_id_row() -> Optional[int]:
    """Row id of the first RECEIVED message stored with a packet id — the
    moment this store's receive writers began keeping ids (the inbox era,
    BIC-2 review r4). None = no such row yet. Raises on a store error: the
    caller decides (the inbox then folds nothing by text — a duplicate,
    never a hide)."""
    conn = _init_db()
    try:
        row = conn.execute("SELECT value FROM messages_meta WHERE key = ?",
                           (_RX_ERA_KEY,)).fetchone()
        if row is None:
            _record_rx_era(conn)
            conn.commit()
            row = conn.execute("SELECT value FROM messages_meta WHERE key = ?",
                               (_RX_ERA_KEY,)).fetchone()
        if row is not None:
            _check_era(conn, row[0])
    finally:
        conn.close()
    # PERSISTED, never re-derived: MIN(id) slid forward when the 30-day prune
    # removed the era row itself (review r5 HIGH — a stranger was hidden)
    return row[0] if row else None


def _check_era(conn, value) -> None:
    """Refuse an era no writer can have stored (hfm #3, BIC-2 r6 LOW): not
    a positive int, or past the highest row id ever assigned (AUTOINCREMENT's
    sqlite_sequence survives prunes). A huge value would make every row
    pre-era and text-fold strangers; raising = the inbox folds nothing."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"inbox era {value!r} is not a row id")
    seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = "
                       "'messages'").fetchone()
    if seq is None or value > seq[0]:
        raise ValueError(f"inbox era {value} is past the store's last row id "
                         f"({seq[0] if seq else 'none'})")


#: Public name — the inbox's hand-off test shares this ONE validator (hfm #5).
valid_packet_id = _valid_packet_id


def wire_packet_id(raw) -> Optional[int]:
    """The ONE parser for a packet id as a writer receives it off the wire
    (BIC-2 review r1): an int, or the MQTT decoder's ``str(id)``.

    "The wire carried none" is ``None`` QUIETLY — absent, ``0``, ``""``,
    the decoder's ``str(time.time())`` fallback (a timestamp is not an id),
    a bool, any non-ASCII-digit string. Range is NOT judged here: an
    impossible id still reaches ``store_incoming``'s validator, which says
    so loudly (hfm #3) — so a real anomaly is never drowned by the absent
    case (r1 F9: an encrypted-leg ``0`` warned once per packet)."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, str):
        if not (raw.isascii() and raw.isdigit()):
            return None
        # <= 10 digits: 2**32-1 has 10; a longer string is never an id and
        # int() of 4301+ digits RAISES inside the writer's try → row lost
        # (r2). Dropped, but WITNESSED like any impossible id (r4 F4).
        if len(raw) > 10:
            logger.warning("messages: refusing impossible packet_id string of "
                           "%d digits (stored as unknown)", len(raw))
            return None
        raw = int(raw)
    if not isinstance(raw, int) or raw == 0:
        return None
    return raw


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
            delivered BOOLEAN DEFAULT 0,
            packet_id INTEGER
        )
    ''')
    if not _schema_is_current(conn):
        try:
            ensure_column(conn, "messages", "packet_id", "INTEGER")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_packet_id "
                         "ON messages(packet_id) WHERE packet_id IS NOT NULL")
            # one-row facts about the store (BIC-2 r5): the inbox era, written
            # ONCE, so a retention prune of its row can never move it
            conn.execute("CREATE TABLE IF NOT EXISTS messages_meta "
                         "(key TEXT PRIMARY KEY, value INTEGER NOT NULL)")
            _record_rx_era(conn)   # backfill: before this process can prune
            # BIC-2 11-23: each send's MEASURED heard-by verdicts, kept past
            # the boxes' 24 h rx_heard ring. packet_id rides the key so a
            # store swapped under a running map never lends one send's
            # verdicts to another row with the same id.
            conn.execute("CREATE TABLE IF NOT EXISTS heard_kept ("
                         "message_id INTEGER NOT NULL, packet_id INTEGER NOT NULL, "
                         "alias TEXT NOT NULL, state TEXT NOT NULL, reason TEXT NOT NULL, "
                         "rx_ts REAL, skewed INTEGER NOT NULL DEFAULT 0, "
                         "kept_at REAL NOT NULL, "
                         "PRIMARY KEY (message_id, packet_id, alias))")
            # BIC-2 DM slice 1: a DM send's FINAL delivery verdict (delivered /
            # failed), so a map restart -- which empties the in-memory /e/
            # witness -- never erases a settled answer (design Q7).
            conn.execute("CREATE TABLE IF NOT EXISTS dm_kept ("
                         "message_id INTEGER NOT NULL, packet_id INTEGER NOT NULL, "
                         "state TEXT NOT NULL, detail TEXT NOT NULL, "
                         "reason TEXT NOT NULL DEFAULT '', said_by TEXT NOT NULL DEFAULT '', "
                         "latency_s REAL, sent_n INTEGER NOT NULL DEFAULT 0, "
                         "kept_at REAL NOT NULL, "
                         "PRIMARY KEY (message_id, packet_id))")
            # r1 F10: a dm_kept created before sent_n (unreleased shape)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(dm_kept)")}
            if "sent_n" not in cols:
                conn.execute("ALTER TABLE dm_kept ADD COLUMN sent_n INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        except Exception:
            conn.close()  # never leak the fd on a failed migration (#73)
            raise

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
            deleted = cursor.rowcount
            _drop_orphan_heard_rows(conn)
            conn.commit()
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

    # Chunk message if needed. Meshtastic by BYTES, to the destination's
    # single-packet budget (bridge matrix F18; a DM's is smaller, F17) — the
    # 160-character chunker let one multibyte "word" leave as 900 B.
    if network == "meshtastic":
        from gateway.base_handler import chunk_for_mesh, mesh_text_budget
        budget = mesh_text_budget(destination)
        chunks = chunk_for_mesh(content, max_bytes=budget)
        if len(chunks) > 1:
            # Keep the "[i/N] " markers a reader on a radio relies on — and
            # reserve their bytes, so a marked chunk still fits the budget.
            chunks = chunk_for_mesh(content, max_bytes=budget - len("[99/99] "))
            chunks = [f"[{i}/{len(chunks)}] {c}" for i, c in enumerate(chunks, 1)]
    else:
        chunks = _chunk_message(content)

    try:
        # Get bridge instance (if available)
        # RNSMeshtasticBridge available via direct import
        # bridge = get_active_bridge()  # Would get active bridge here

        # Store message
        conn = _init_db()
        cursor = conn.cursor()

        # The index the DIRECT path transmits on (send_dm: 0; send_broadcast:
        # ``channel if channel > 0 else 1``, below). A row says where its
        # packet left, so the echo — which carries the wire index — can pair
        # with it; a gateway send overwrites it with its own on success.
        is_dm_dest = bool(destination and destination != '!ffffffff')
        direct_channel = (0 if is_dm_dest else (channel if channel > 0 else 1)) \
            if network == "meshtastic" else channel
        row_ids = []
        for chunk in chunks:
            cursor.execute('''
                INSERT INTO messages (network, from_id, to_id, content, channel, is_dm, delivered)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (network, "local", destination, chunk, direct_channel,
                  is_dm_dest, False))
            row_ids.append(cursor.lastrowid)

        conn.commit()
        message_id = cursor.lastrowid
        # per-chunk outcome, recorded the moment a chunk LEAVES, whatever
        # happens to the chunks after it: row id -> (wire channel, radio
        # packet id or None). Every chunk that left is marked delivered — not
        # only the last (F12), and not only when the whole send succeeded
        # (review r1: a refused chunk 3 erased chunks 1-2's ids). A later
        # path that re-airs a chunk overwrites its entry (pre-existing: the
        # direct fallback re-sends from chunk 1 — recorded, not fixed here).
        aired = {}

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
                    for row_id, chunk in zip(row_ids, chunks):
                        result = cmd_gateway.send_to_meshtastic(chunk, destination, channel)
                        if not result.success:
                            send_error = result.message
                            break
                        aired[row_id] = (channel, None)   # gateway returns no id
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
                    for row_id, chunk in zip(row_ids, chunks):
                        # Use send_dm for direct messages, send_broadcast for channels
                        if is_dm_dest:
                            result = cmd_meshtastic.send_dm(
                                text=chunk,
                                dest=destination,
                                ack=False,
                                high_reliability=high_reliability
                            )
                        else:
                            result = cmd_meshtastic.send_broadcast(
                                text=chunk,
                                channel_index=direct_channel,
                                hop_limit=7 if high_reliability else 3
                            )
                        if not result.success:
                            send_error = result.message
                            break
                        data = result.data if isinstance(result.data, dict) else {}
                        aired[row_id] = (direct_channel,
                                         _valid_packet_id(data.get('packet_id')))
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
                for row_id, chunk in zip(row_ids, chunks):
                    dest_bytes = bytes.fromhex(destination.lstrip('!')) if destination else None
                    result = cmd_gateway.send_to_rns(chunk, dest_bytes)
                    if not result.success:
                        send_error = result.message
                        break
                    aired[row_id] = (channel, None)
                else:
                    send_success = True
            except Exception as e:
                send_error = f"RNS send failed: {e}"
                logger.error(f"Failed to send via RNS: {e}")

        # Update delivery status
        if aired:
            cursor = conn.cursor()
            for row_id, (wire_channel, pid) in aired.items():
                cursor.execute('UPDATE messages SET delivered = 1, channel = ?, '
                               'packet_id = ? WHERE id = ?', (wire_channel, pid, row_id))
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
                packet_id=row['packet_id'],
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
    rssi: Optional[int] = None,
    packet_id: Optional[int] = None,
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
        packet_id: The radio's id for this packet, when the wire carried one
            (BIC-2: the inbox folds an echo on this, never on matching text).
            An impossible value is refused loudly and stored as unknown.

    Returns:
        CommandResult with storage confirmation
    """
    try:
        pid = _valid_packet_id(packet_id)   # validated ONCE (one warning, not two)
        conn = _init_db()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO messages
                (network, from_id, to_id, content, channel, is_dm, snr, rssi,
                 delivered, packet_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            ''', (network, from_id, to_id, content, channel, to_id is not None,
                  snr, rssi, pid))
            if pid is not None and from_id != "local":
                _record_rx_era(conn)   # before commit, before _maybe_prune below
            conn.commit()
            message_id = cursor.lastrowid
        finally:
            # a failed write must not hold the write lock until GC (r6 MED:
            # every other writer then stalled the 30 s busy timeout)
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


def store_outgoing(
    content: str,
    network: str,
    to_id: Optional[str],
    channel: int = 0,
    delivered: bool = False,
    packet_id: Optional[int] = None,
) -> CommandResult:
    """
    Record a message THIS box sent by a path that did not store it itself
    (the browser inbox transmits via ``send_text_direct_with_id``, BIC-1).

    ``delivered`` keeps the column's existing meaning: the send call handed
    the message to the radio/gateway — NOT that the far end received it.

    Args:
        content: Message text
        network: "meshtastic" or "rns"
        to_id: Destination node ID, or None for a channel broadcast
        channel: Channel index the message went out on
        delivered: True when the hand-off to the radio succeeded
        packet_id: The radio's id for this packet (``send_text_direct_with_id``),
            None when unknown — the key that joins a send to any box's RX
            record (INBOX-2). Kept as the radio gave it.

    Returns:
        CommandResult with the new row id
    """
    try:
        conn = _init_db()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO messages (network, from_id, to_id, content, channel,
                                      is_dm, delivered, packet_id)
                VALUES (?, 'local', ?, ?, ?, ?, ?, ?)
            ''', (network, to_id, content, channel, to_id is not None,
                  bool(delivered), _valid_packet_id(packet_id)))
            conn.commit()
            message_id = cursor.lastrowid
        finally:
            conn.close()
        _maybe_prune_messages()
        return CommandResult.ok("Message recorded", data={'message_id': message_id})
    except Exception as e:
        logger.error(f"Failed to record outgoing message: {e}")
        return CommandResult.fail(f"Failed to record outgoing message: {e}")


def _drop_orphan_heard_rows(conn) -> None:
    """Kept hearings go with the send they describe — by row id AND packet id
    (r3 NIT-1: a row keyed to a swapped-in row's old id outlived the swap).
    ``heard_judged`` / ``heard_covered`` from unreleased commits 2c6637fa /
    fe08a41d are never created, read or written (no live store has them)."""
    for table in ("heard_kept", "dm_kept"):
        conn.execute(f"DELETE FROM {table} WHERE NOT EXISTS (SELECT 1 FROM messages "
                     f"WHERE messages.id = {table}.message_id "
                     f"AND messages.packet_id = {table}.packet_id)")


def _kept_row_ok(state, alias, reason, rx, sk, keepable) -> bool:
    """THE one validity rule for a heard_kept row (r3 LOW-3: the reader's
    refuse-list and the writer's replace-list were two hand-kept lists)."""
    return (state in keepable and isinstance(alias, str) and bool(alias)
            and alias.isprintable() and isinstance(reason, str)
            and sk in (0, 1) and not isinstance(sk, bool)
            and (rx is None or (isinstance(rx, (int, float)) and not isinstance(rx, bool)
                                and math.isfinite(rx))))


def _keepable_states():
    # ONE constant with the judge (hfm #5, r1 L8); imported lazily so this
    # module keeps loading without utils on the path
    from utils.inbox_heard import FREEZABLE
    return FREEZABLE


def keep_heard(items: List[tuple], now: Optional[float] = None) -> None:
    """Keep heard-by verdicts for ``[(message_id, packet_id, [box dict]), ...]``
    in ONE transaction (r1 L3: one busy wait per read, not one per send).

    Ranked, never blind-replaced (r1 L1/L5): a row is written only when it
    is new or a clean hearing replaces one outside the join window — so two
    overlapping reads can never downgrade what the other kept. Refuses a
    state the judge does not keep, LOUDLY (hfm #3). Raises on a store
    error: the caller decides."""
    keepable = _keepable_states()
    bad = [b.get("state") for _, _, boxes in items for b in boxes
           if b.get("state") not in keepable]
    if bad:
        raise ValueError(f"heard_kept refuses state(s) {bad!r}")
    stamp = time.time() if now is None else now
    rows = [(mid, pid, b["alias"], b["state"], b["reason"], b.get("rx_ts"),
             int(bool(b.get("skewed"))), stamp)
            for mid, pid, boxes in items for b in boxes]
    if not rows:
        return
    conn = _init_db()
    try:
        # a row the reader would refuse never blocks the real hearing: it is
        # removed first, judged by the SAME rule the reader uses (r3 LOW-3)
        for mid, pid, alias, *_ in rows:
            old = conn.execute("SELECT state, alias, reason, rx_ts, skewed FROM heard_kept "
                               "WHERE message_id = ? AND packet_id = ? AND alias = ?",
                               (mid, pid, alias)).fetchone()
            if old is not None and not _kept_row_ok(*tuple(old), keepable):
                conn.execute("DELETE FROM heard_kept WHERE message_id = ? AND "
                             "packet_id = ? AND alias = ?", (mid, pid, alias))
        conn.executemany(
            "INSERT INTO heard_kept (message_id, packet_id, alias, state, reason, "
            "rx_ts, skewed, kept_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (message_id, packet_id, alias) DO UPDATE SET "
            "state = excluded.state, reason = excluded.reason, rx_ts = excluded.rx_ts, "
            "skewed = excluded.skewed, kept_at = excluded.kept_at "
            "WHERE excluded.skewed < heard_kept.skewed", rows)
        conn.commit()
    finally:
        conn.close()


def kept_heard(sends: List[tuple]) -> "tuple[Dict[int, List[Dict[str, Any]]], int]":
    """``({message_id: [box dict, ...]}, refused)`` for ``[(message_id,
    packet_id), ...]`` — only rows whose packet id matches. A row no writer
    here can have stored (a state the judge does not keep, a blank alias, a
    non-text reason) is LEFT OUT and counted in ``refused`` (r1 L4: the
    writer refused it, the reader must too — hfm #3, #9). Raises on a store
    error."""
    if not sends:
        return {}, 0
    keepable = _keepable_states()
    conn = _init_db()
    try:
        out: Dict[int, List[Dict[str, Any]]] = {}
        refused = 0
        for mid, pid in sends:
            for r in conn.execute(
                    "SELECT alias, state, reason, rx_ts, skewed FROM heard_kept "
                    "WHERE message_id = ? AND packet_id = ? ORDER BY alias", (mid, pid)):
                rx, sk, alias = r["rx_ts"], r["skewed"], r["alias"]
                # r2 L-D: only values the judge can produce (finite stamp,
                # 0/1 skew, a printable alias) — inf broke the JSON reply
                if not _kept_row_ok(r["state"], alias, r["reason"], rx, sk, keepable):
                    refused += 1
                    continue
                out.setdefault(mid, []).append(
                    {"alias": r["alias"], "state": r["state"], "reason": r["reason"],
                     "rx_ts": rx, "skewed": bool(r["skewed"])})
        if refused:
            logger.warning("heard_kept: %d row(s) no writer can have stored were "
                           "left out", refused)
        return out, refused
    finally:
        conn.close()


def _dm_final_states():
    # ONE vocabulary with the judge (hfm #5); lazy so this module loads alone
    from utils.dm_delivery import ROW_DELIVERED, ROW_FAILED
    return (ROW_DELIVERED, ROW_FAILED)


def _dm_terminal_said_by():
    from utils.dm_delivery import TERMINAL_SAID_BY
    return TERMINAL_SAID_BY


def _dm_row_ok(state, detail, reason, said_by, latency, finals) -> bool:
    """THE one validity rule for a dm_kept row (writer refuses, reader skips)."""
    # "our radio" was the journal's voice; the journal decides nothing now
    # (operator 11-25), so no writer can produce it -- refuse an early row.
    return (state in finals and isinstance(detail, str) and bool(detail)
            and isinstance(reason, str) and isinstance(said_by, str)
            and said_by != "our radio"
            and (latency is None or (isinstance(latency, (int, float))
                                     and not isinstance(latency, bool)
                                     and math.isfinite(latency))))


def keep_dm(items: List[tuple], now: Optional[float] = None) -> None:
    """Keep FINAL DM verdicts for ``[(message_id, packet_id, verdict dict)]``
    in one transaction. Refuses a non-final state LOUDLY (hfm #3): a kept
    "waiting" would outlive the evidence that settles it. Ranked: a kept
    ``delivered`` is never replaced; a kept ``failed`` (e.g. a relay's NAK)
    is replaced by a later ``delivered`` -- the destination's own answer
    outranks a relay's. Raises on a store error: the caller decides."""
    finals = _dm_final_states()
    rows = []
    for mid, pid, v in items:
        row = (v.get("state"), v.get("detail"), v.get("reason") or "",
               v.get("said_by") or "", v.get("latency_s"))
        if not _dm_row_ok(*row, finals):
            raise ValueError(f"dm_kept refuses verdict state {v.get('state')!r}")
        n = v.get("sent_n") or 0
        rows.append((mid, pid, *row,
                     n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else 0))
    if not rows:
        return
    stamp = time.time() if now is None else now
    terminal = tuple(_dm_terminal_said_by())
    marks = ", ".join("?" * len(terminal))
    conn = _init_db()
    try:
        # Rank: delivered > a failure a TERMINAL sender said (the
        # destination) > anyone else's failure. Never a downgrade (r1 F10;
        # pinned by test_inbox_dm_delivery.TestStoreRanking).
        conn.executemany(
            "INSERT INTO dm_kept (message_id, packet_id, state, detail, reason, said_by, "
            "latency_s, sent_n, kept_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (message_id, packet_id) DO UPDATE SET state = excluded.state, "
            "detail = excluded.detail, reason = excluded.reason, said_by = excluded.said_by, "
            "latency_s = excluded.latency_s, sent_n = MAX(excluded.sent_n, dm_kept.sent_n), "
            "kept_at = excluded.kept_at "
            "WHERE (excluded.state = ? AND dm_kept.state != ?) "
            f"OR (excluded.state = ? AND excluded.said_by IN ({marks}) "
            f"AND dm_kept.state = ? AND dm_kept.said_by NOT IN ({marks}))",
            [(*r, stamp, finals[0], finals[0], finals[1], *terminal, finals[1], *terminal)
             for r in rows])
        conn.commit()
    finally:
        conn.close()


def kept_dm(sends: List[tuple]) -> "tuple[Dict[int, Dict[str, Any]], int]":
    """``({message_id: verdict dict}, refused)`` for ``[(message_id,
    packet_id)]`` -- only rows whose packet id matches. A row no writer here
    can have stored is LEFT OUT, counted and DELETED (hfm #3, #9; Fable r4
    #2: a refused row left in place blocked every later verdict for that
    send and warned on every read). Raises on a store error."""
    if not sends:
        return {}, 0
    finals = _dm_final_states()
    conn = _init_db()
    try:
        out: Dict[int, Dict[str, Any]] = {}
        refused = 0
        for mid, pid in sends:
            r = conn.execute("SELECT state, detail, reason, said_by, latency_s, sent_n "
                             "FROM dm_kept "
                             "WHERE message_id = ? AND packet_id = ?", (mid, pid)).fetchone()
            if r is None:
                continue
            if not _dm_row_ok(r["state"], r["detail"], r["reason"], r["said_by"],
                              r["latency_s"], finals):
                refused += 1
                conn.execute("DELETE FROM dm_kept WHERE message_id = ? AND packet_id = ?",
                             (mid, pid))
                continue
            n = r["sent_n"]
            out[mid] = {"state": r["state"], "detail": r["detail"], "reason": r["reason"],
                        "said_by": r["said_by"], "latency_s": r["latency_s"],
                        "sent_n": n if isinstance(n, int) and n >= 0 else 0}
        if refused:
            conn.commit()
            logger.warning("dm_kept: %d row(s) no writer can have stored were "
                           "left out and removed", refused)
        return out, refused
    finally:
        conn.close()


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

        cursor.execute("SELECT MAX(timestamp) AS newest FROM messages")
        newest = cursor.fetchone()['newest']

        conn.close()

        return CommandResult.ok(
            f"{total} total messages",
            data={
                'total': total,
                'sent': sent,
                'received': received,
                'last_24h': last_24h,
                'newest': newest,
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
        # r1 N1: kept heard-by rows go with the sends they describe
        _drop_orphan_heard_rows(conn)
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
