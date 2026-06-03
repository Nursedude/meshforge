"""Cross-protocol identity SSOT binder (Theme-A step 2).

Wires the (previously dormant) ``ContactMappingTable`` into the gateway as
the single source of truth for "these protocol addresses are the same
entity". One canonical contact can span a Meshtastic node-id, an RNS
(LXMF) destination hash, and a MeshCore pubkey prefix.

Design contract (operator-ratified 2026-06-03):

- **Conservative auto-bind**: an observed (protocol, address, name) joins an
  EXISTING contact only on an exact normalized-name match that is
  unambiguous (exactly one candidate contact, and it lacks an address on
  this protocol) with a "bindable" name (not empty/short/hex-ish/
  auto-generated). Anything less certain becomes a standalone unverified
  contact. Auto-binds are recorded ``verified=0``; operator CLI links are
  ``verified=1``.
- **Lazy DB**: constructing the binder does NO I/O. The SQLite table opens
  on first real use, so with the gating flag off (all tests, default
  deploys) no ``contact_mapping.db`` file is ever created.
- **Hot-path safety**: ``populate()`` is throttled per address
  (``time.monotonic``, no sleeps/threads), bounded by a max-contacts guard,
  and swallows+logs every exception — bridging never breaks on identity
  bookkeeping.
- ``contact_mapping.py`` itself stays UNTOUCHED (MeshAnchor carries a
  near-identical copy; the binder is gateway-specific orchestration). The
  two per-address mutations its API lacks (verify/unlink a single address)
  are done here via ``connect_tuned`` against the same DB file —
  deliberate, MF013-clean (the DB has its DBSpec in utils.db_inventory).
"""

import logging
import re
import sqlite3
import threading
import time
from typing import Dict, List, Optional, Tuple

from utils.db_helpers import connect_tuned

from .contact_mapping import Contact, ContactMappingTable, VALID_PROTOCOLS
from .reply_context import _sanitize_positive

logger = logging.getLogger(__name__)

DEFAULT_THROTTLE_SEC = 900        # per-address write throttle (~15 min)
DEFAULT_MAX_CONTACTS = 5000       # auto-discover growth guard
_SEEN_CAP = 4096                  # throttle-map size bound

_MESHTASTIC_RE = re.compile(r'^!([0-9a-f]{8})$')
_HEX_RE = re.compile(r'^[0-9a-fA-F]+$')


class IdentityBinder:
    """Lazy, throttled, conservative cross-protocol identity binder."""

    def __init__(self, db_path: Optional[str] = None,
                 throttle_sec: Optional[float] = None,
                 max_contacts: Optional[int] = None):
        self._db_path = db_path
        self._throttle_sec = _sanitize_positive(throttle_sec, DEFAULT_THROTTLE_SEC)
        self._max_contacts = int(_sanitize_positive(max_contacts, DEFAULT_MAX_CONTACTS))
        self._lock = threading.RLock()
        self._table: Optional[ContactMappingTable] = None
        # (protocol, address) -> last write time.monotonic()
        self._seen: Dict[Tuple[str, str], float] = {}
        # normalized display_name -> [Contact, ...]; None = dirty/unbuilt
        self._name_index: Optional[Dict[str, List[Contact]]] = None
        self._max_contacts_warned = False

    # --- lazy DB -------------------------------------------------------

    def _ensure_table(self) -> ContactMappingTable:
        """Open the contact table on first use (double-checked)."""
        if self._table is None:
            with self._lock:
                if self._table is None:
                    self._table = ContactMappingTable(db_path=self._db_path)
        return self._table

    # --- normalization -------------------------------------------------

    @staticmethod
    def _norm_name(name) -> str:
        """Same normalization as utils.cross_protocol_collapse."""
        if not isinstance(name, str):
            return ""
        return name.strip().casefold()

    @staticmethod
    def _norm_address(protocol: str, address) -> Optional[str]:
        """Normalize an address for its protocol; None when invalid.

        meshtastic: ``!xxxxxxxx`` (8 hex, lowercased)
        rns:        32-hex destination hash (lowercased)
        meshcore:   12-hex pubkey prefix (lowercased)
        """
        if not isinstance(address, str) or not address:
            return None
        addr = address.strip().lower()
        if protocol == 'meshtastic':
            return addr if _MESHTASTIC_RE.match(addr) else None
        if protocol == 'rns':
            return addr if len(addr) == 32 and _HEX_RE.match(addr) else None
        if protocol == 'meshcore':
            return addr if len(addr) == 12 and _HEX_RE.match(addr) else None
        return None

    @classmethod
    def _is_bindable_name(cls, name) -> bool:
        """True when a name is trustworthy enough to auto-bind on.

        Rejects names that are empty, too short, id-shaped (leading ``!``,
        pure hex of an address length), or the auto-generated
        ``proto:addr`` fallback (contains ``:``). False positives here mean
        a directed DM to the wrong radio, so the predicate is deliberately
        strict — anything rejected still gets a standalone contact.
        """
        if not isinstance(name, str):
            return False
        n = name.strip()
        if len(n) < 3:
            return False
        if n.startswith('!') or ':' in n:
            return False
        if len(n) in (8, 12, 32) and _HEX_RE.match(n):
            return False
        return True

    # --- throttle ------------------------------------------------------

    def _should_write(self, protocol: str, addr: str) -> bool:
        """At most one SQLite touch per address per throttle window."""
        key = (protocol, addr)
        now = time.monotonic()
        with self._lock:
            last = self._seen.get(key)
            if last is not None and (now - last) < self._throttle_sec:
                return False
            self._seen[key] = now
            if len(self._seen) > _SEEN_CAP:
                # Cheap bound: drop the stalest half. Worst case some
                # addresses re-touch SQLite one window early — harmless.
                for k, _ in sorted(self._seen.items(), key=lambda kv: kv[1])[
                        :_SEEN_CAP // 2]:
                    del self._seen[k]
            return True

    # --- name index ----------------------------------------------------

    def _dirty(self) -> None:
        self._name_index = None

    def _find_unique_bindable(self, table: ContactMappingTable,
                              norm_name: str,
                              protocol: str) -> Optional[Contact]:
        """The single contact named ``norm_name`` lacking ``protocol``.

        None when zero OR more than one contact carries the name (ambiguity
        never auto-binds), or when the only candidate already has an
        address on this protocol. The index caches full Contact objects and
        is invalidated (``_dirty``) on every binder write.
        """
        with self._lock:
            if self._name_index is None:
                index: Dict[str, List[Contact]] = {}
                for c in table.list_contacts():
                    key = self._norm_name(c.display_name)
                    if key:
                        index.setdefault(key, []).append(c)
                self._name_index = index
            candidates = list(self._name_index.get(norm_name, []))
        if len(candidates) != 1:
            return None
        candidate = candidates[0]
        if protocol in candidate.addresses:
            return None
        return candidate

    # --- core population ------------------------------------------------

    def populate(self, protocol: str, address, name_hint: str = "") -> None:
        """Throttled observe-and-bind from bridge traffic. Never raises."""
        try:
            addr = self._norm_address(protocol, address)
            if addr is None or protocol not in VALID_PROTOCOLS:
                return
            if not self._should_write(protocol, addr):
                return
            table = self._ensure_table()
            self.discover_and_bind(table, protocol, addr, name_hint or "")
        except Exception as e:
            logger.warning(f"identity populate failed ({protocol}): {e}")

    def discover_and_bind(self, table: ContactMappingTable,
                          protocol: str, addr: str, name: str) -> str:
        """Conservative binder. Returns the contact id (or '')."""
        existing = table.lookup_by_address(protocol, addr)
        if existing:
            table.update_last_seen(protocol, addr)
            return existing.id

        if self._is_bindable_name(name):
            candidate = self._find_unique_bindable(
                table, self._norm_name(name), protocol)
            if candidate is not None:
                if table.add_address(candidate.id, protocol, addr,
                                     verified=False):
                    self._dirty()
                    logger.info(
                        f"identity auto-bind: {protocol}:{addr} -> "
                        f"'{candidate.display_name}' ({candidate.id[:8]}, "
                        f"unverified)"
                    )
                    return candidate.id

        # Standalone unverified contact — growth-guarded.
        if len(table.list_contacts()) >= self._max_contacts:
            if not self._max_contacts_warned:
                self._max_contacts_warned = True
                logger.warning(
                    f"identity max-contacts guard hit "
                    f"({self._max_contacts}); skipping new auto-discover "
                    f"entries (further skips silent)"
                )
            return ""
        contact_id = table.auto_discover(protocol, addr, name_hint=name)
        self._dirty()
        return contact_id

    # --- resolution ------------------------------------------------------

    def resolve(self, source_protocol: str, source_address,
                target_protocol: str) -> Optional[str]:
        """Cross-protocol address resolution via the contact table."""
        try:
            addr = self._norm_address(source_protocol, source_address)
            if addr is None:
                return None
            table = self._ensure_table()
            return table.resolve_destination(
                source_protocol, addr, target_protocol)
        except Exception as e:
            logger.warning(f"identity resolve failed: {e}")
            return None

    # --- operator CLI surface (always allowed; verified=1 writes) --------

    def list_all(self) -> List[Contact]:
        return self._ensure_table().list_contacts()

    def address_rows(self, contact_id: str) -> List[dict]:
        """Per-address detail (verified, last_seen) for ``show``/``list``."""
        table = self._ensure_table()
        conn = connect_tuned(table._db_path)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT protocol, address, verified, last_seen "
                "FROM contact_addresses WHERE contact_id = ? "
                "ORDER BY protocol",
                (contact_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def show(self, protocol: str, addr: str) -> Optional[Contact]:
        norm = self._norm_address(protocol, addr)
        if norm is None:
            return None
        return self._ensure_table().lookup_by_address(protocol, norm)

    def link(self, name: str, protocol: str, addr: str) -> Optional[str]:
        """Operator link (verified=1). Returns contact id or None."""
        norm = self._norm_address(protocol, addr)
        if norm is None or not (name or "").strip():
            return None
        table = self._ensure_table()
        taken = table.lookup_by_address(protocol, norm)
        if taken is not None:
            logger.warning(
                f"link refused: {protocol}:{norm} already belongs to "
                f"'{taken.display_name}' — unlink it first"
            )
            return None
        norm_name = self._norm_name(name)
        target = None
        for c in table.list_contacts():
            if self._norm_name(c.display_name) == norm_name:
                target = c
                break
        if target is not None and protocol not in target.addresses:
            ok = table.add_address(target.id, protocol, norm, verified=True)
            self._dirty()
            return target.id if ok else None
        if target is not None:
            logger.warning(
                f"link refused: '{target.display_name}' already has a "
                f"{protocol} address ({target.addresses.get(protocol)})"
            )
            return None
        contact_id = table.add_contact(name.strip(), {protocol: norm},
                                       verified=True)
        self._dirty()
        return contact_id

    def unlink(self, protocol: str, addr: str) -> bool:
        """Remove a single (protocol, address) row; prunes empty contacts."""
        norm = self._norm_address(protocol, addr)
        if norm is None:
            return False
        table = self._ensure_table()
        conn = connect_tuned(table._db_path)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            cur = conn.execute(
                "DELETE FROM contact_addresses "
                "WHERE protocol = ? AND address = ?",
                (protocol, norm),
            )
            # Prune contacts left with zero addresses.
            conn.execute(
                "DELETE FROM contacts WHERE id NOT IN "
                "(SELECT DISTINCT contact_id FROM contact_addresses)"
            )
            conn.commit()
            self._dirty()
            return cur.rowcount > 0
        finally:
            conn.close()

    def verify(self, protocol: str, addr: str) -> bool:
        """Flip an existing address to verified=1 (operator approval)."""
        norm = self._norm_address(protocol, addr)
        if norm is None:
            return False
        table = self._ensure_table()
        conn = connect_tuned(table._db_path)
        try:
            cur = conn.execute(
                "UPDATE contact_addresses SET verified = 1 "
                "WHERE protocol = ? AND address = ?",
                (protocol, norm),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()
