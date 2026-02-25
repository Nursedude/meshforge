"""
Cross-Protocol Contact Mapping Table.

Persistent SQLite-backed mapping between identities across Meshtastic,
RNS, and MeshCore networks. Enables DM routing across protocol boundaries
by resolving which protocol addresses belong to the same person.

Identity formats:
- Meshtastic: !aabbccdd (hex node ID)
- RNS: 16-char hex destination hash
- MeshCore: 12-char hex public key prefix

Usage:
    mapping = ContactMappingTable()
    mapping.add_contact("Alice", {
        "meshtastic": "!aabb1234",
        "rns": "deadbeef01234567",
    })

    # Resolve cross-protocol DM
    rns_addr = mapping.resolve_destination(
        source_protocol="meshtastic",
        source_address="!aabb1234",
        target_protocol="rns",
    )
    # Returns "deadbeef01234567"
"""

import logging
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)

VALID_PROTOCOLS = frozenset({"meshtastic", "rns", "meshcore"})


@dataclass
class Contact:
    """A cross-protocol identity."""
    id: str
    display_name: str
    addresses: Dict[str, str] = field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class ContactAddress:
    """A single protocol address linked to a contact."""
    contact_id: str
    protocol: str
    address: str
    verified: bool = False
    last_seen: Optional[datetime] = None


class ContactMappingTable:
    """
    SQLite-backed cross-protocol contact mapping.

    Thread-safe. Uses WAL journal mode for crash resilience.
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            config_dir = get_real_user_home() / ".config" / "meshforge"
            config_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(config_dir / "contact_mapping.db")

        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    @contextmanager
    def _get_connection(self):
        """Get database connection with WAL mode."""
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
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
                CREATE TABLE IF NOT EXISTS contacts (
                    id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS contact_addresses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    contact_id TEXT NOT NULL REFERENCES contacts(id)
                        ON DELETE CASCADE,
                    protocol TEXT NOT NULL,
                    address TEXT NOT NULL,
                    verified INTEGER DEFAULT 0,
                    last_seen TEXT,
                    UNIQUE(protocol, address)
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_addr_contact
                ON contact_addresses(contact_id)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_addr_lookup
                ON contact_addresses(protocol, address)
            """)

    def add_contact(self, name: str,
                    addresses: Optional[Dict[str, str]] = None,
                    verified: bool = False) -> str:
        """
        Create a new contact with optional protocol addresses.

        Args:
            name: Display name for the contact.
            addresses: Dict mapping protocol -> address.
            verified: Whether addresses are manually verified.

        Returns:
            Contact ID (UUID).
        """
        contact_id = str(uuid.uuid4())
        now = datetime.now().isoformat()

        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT INTO contacts (id, display_name, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (contact_id, name, now, now),
                )

                if addresses:
                    for protocol, address in addresses.items():
                        if protocol not in VALID_PROTOCOLS:
                            logger.warning(f"Skipping unknown protocol: {protocol}")
                            continue
                        conn.execute(
                            "INSERT OR IGNORE INTO contact_addresses "
                            "(contact_id, protocol, address, verified, last_seen) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (contact_id, protocol, address, int(verified), now),
                        )

        logger.debug(f"Created contact {name} ({contact_id[:8]})")
        return contact_id

    def add_address(self, contact_id: str, protocol: str, address: str,
                    verified: bool = False) -> bool:
        """
        Link a new protocol address to an existing contact.

        Returns:
            True if added successfully.
        """
        if protocol not in VALID_PROTOCOLS:
            logger.warning(f"Invalid protocol: {protocol}")
            return False

        now = datetime.now().isoformat()

        with self._lock:
            with self._get_connection() as conn:
                # Verify contact exists
                row = conn.execute(
                    "SELECT id FROM contacts WHERE id = ?", (contact_id,)
                ).fetchone()
                if not row:
                    logger.warning(f"Contact {contact_id[:8]} not found")
                    return False

                try:
                    conn.execute(
                        "INSERT INTO contact_addresses "
                        "(contact_id, protocol, address, verified, last_seen) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (contact_id, protocol, address, int(verified), now),
                    )
                    conn.execute(
                        "UPDATE contacts SET updated_at = ? WHERE id = ?",
                        (now, contact_id),
                    )
                    return True
                except sqlite3.IntegrityError:
                    logger.debug(f"Address {protocol}:{address} already mapped")
                    return False

    def lookup_by_address(self, protocol: str,
                          address: str) -> Optional[Contact]:
        """
        Find a contact by any of their protocol addresses.

        Returns:
            Contact with all addresses, or None.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT contact_id FROM contact_addresses "
                "WHERE protocol = ? AND address = ?",
                (protocol, address),
            ).fetchone()

            if not row:
                return None

            return self._load_contact(conn, row['contact_id'])

    def get_address(self, contact_id: str,
                    protocol: str) -> Optional[str]:
        """
        Get a specific protocol address for a contact.

        Returns:
            Address string or None.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT address FROM contact_addresses "
                "WHERE contact_id = ? AND protocol = ?",
                (contact_id, protocol),
            ).fetchone()

            return row['address'] if row else None

    def resolve_destination(self, source_protocol: str,
                            source_address: str,
                            target_protocol: str) -> Optional[str]:
        """
        Resolve a cross-protocol destination address.

        Given a source identity and target protocol, find the corresponding
        address on the target protocol for the same contact.

        Args:
            source_protocol: Protocol the message came from.
            source_address: Sender's address on source protocol.
            target_protocol: Protocol to deliver to.

        Returns:
            Target protocol address, or None if no mapping exists.
        """
        contact = self.lookup_by_address(source_protocol, source_address)
        if not contact:
            return None

        return contact.addresses.get(target_protocol)

    def auto_discover(self, protocol: str, address: str,
                      name_hint: str = "") -> str:
        """
        Auto-create or update an unverified contact entry from observed traffic.

        If the address already exists, updates last_seen. Otherwise creates
        a new contact with this single address (unverified).

        Args:
            protocol: Protocol the address was observed on.
            address: The protocol address.
            name_hint: Optional display name hint.

        Returns:
            Contact ID (existing or new).
        """
        if protocol not in VALID_PROTOCOLS:
            return ""

        now = datetime.now().isoformat()

        with self._lock:
            with self._get_connection() as conn:
                # Check if address already mapped
                row = conn.execute(
                    "SELECT contact_id FROM contact_addresses "
                    "WHERE protocol = ? AND address = ?",
                    (protocol, address),
                ).fetchone()

                if row:
                    # Update last_seen
                    conn.execute(
                        "UPDATE contact_addresses SET last_seen = ? "
                        "WHERE protocol = ? AND address = ?",
                        (now, protocol, address),
                    )
                    return row['contact_id']

                # Create new contact
                contact_id = str(uuid.uuid4())
                display = name_hint or f"{protocol}:{address[:12]}"

                conn.execute(
                    "INSERT INTO contacts (id, display_name, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (contact_id, display, now, now),
                )
                conn.execute(
                    "INSERT INTO contact_addresses "
                    "(contact_id, protocol, address, verified, last_seen) "
                    "VALUES (?, ?, ?, 0, ?)",
                    (contact_id, protocol, address, now),
                )

                logger.debug(f"Auto-discovered {protocol}:{address[:12]} as {display}")
                return contact_id

    def merge_contacts(self, id_a: str, id_b: str) -> Optional[str]:
        """
        Merge two contacts that are the same person.

        Moves all addresses from id_b to id_a and deletes id_b.

        Returns:
            Surviving contact ID (id_a), or None on failure.
        """
        now = datetime.now().isoformat()

        with self._lock:
            with self._get_connection() as conn:
                # Verify both exist
                a = conn.execute(
                    "SELECT id FROM contacts WHERE id = ?", (id_a,)
                ).fetchone()
                b = conn.execute(
                    "SELECT id FROM contacts WHERE id = ?", (id_b,)
                ).fetchone()

                if not a or not b:
                    logger.warning("Cannot merge: one or both contacts not found")
                    return None

                # Move addresses from b to a by UPDATE (avoids UNIQUE conflicts
                # and CASCADE deletion issues)
                # First, find which protocols id_a already has
                a_protocols = {
                    row['protocol']
                    for row in conn.execute(
                        "SELECT protocol FROM contact_addresses "
                        "WHERE contact_id = ?", (id_a,)
                    ).fetchall()
                }

                # Update addresses from b to point to a (skip conflicts)
                b_addrs = conn.execute(
                    "SELECT id, protocol FROM contact_addresses "
                    "WHERE contact_id = ?",
                    (id_b,),
                ).fetchall()

                for addr in b_addrs:
                    if addr['protocol'] in a_protocols:
                        # id_a already has this protocol — delete b's copy
                        conn.execute(
                            "DELETE FROM contact_addresses WHERE id = ?",
                            (addr['id'],),
                        )
                    else:
                        # Re-point to id_a
                        conn.execute(
                            "UPDATE contact_addresses SET contact_id = ? "
                            "WHERE id = ?",
                            (id_a, addr['id']),
                        )

                # Delete id_b (no addresses should remain)
                conn.execute("DELETE FROM contacts WHERE id = ?", (id_b,))
                conn.execute(
                    "UPDATE contacts SET updated_at = ? WHERE id = ?",
                    (now, id_a),
                )

                logger.info(f"Merged contact {id_b[:8]} into {id_a[:8]}")
                return id_a

    def list_contacts(self) -> List[Contact]:
        """Return all contacts with their addresses."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT id FROM contacts ORDER BY display_name"
            ).fetchall()

            return [self._load_contact(conn, row['id']) for row in rows]

    def delete_contact(self, contact_id: str) -> bool:
        """Delete a contact and all its addresses."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "DELETE FROM contacts WHERE id = ?", (contact_id,)
                )
                return cursor.rowcount > 0

    def update_last_seen(self, protocol: str, address: str) -> None:
        """Update last_seen timestamp for an address."""
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE contact_addresses SET last_seen = ? "
                "WHERE protocol = ? AND address = ?",
                (now, protocol, address),
            )

    def _load_contact(self, conn, contact_id: str) -> Contact:
        """Load a full contact with all addresses from DB."""
        row = conn.execute(
            "SELECT * FROM contacts WHERE id = ?", (contact_id,)
        ).fetchone()

        if not row:
            return Contact(id=contact_id, display_name="unknown")

        addrs = conn.execute(
            "SELECT protocol, address FROM contact_addresses "
            "WHERE contact_id = ?",
            (contact_id,),
        ).fetchall()

        addresses = {a['protocol']: a['address'] for a in addrs}

        return Contact(
            id=row['id'],
            display_name=row['display_name'],
            addresses=addresses,
            created_at=datetime.fromisoformat(row['created_at']),
            updated_at=datetime.fromisoformat(row['updated_at']),
        )
