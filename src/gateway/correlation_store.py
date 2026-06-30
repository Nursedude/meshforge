"""Durable per-message correlation store (Thread-2 bidirectional addressability).

The mautrix ``Message``-table analogue: one row per bridged message,
mapping the originating mesh node, the RNS (LXMF) peer, and — for later
steps — the Meshtastic packet id / LXMF message hash needed for ACK
matching and native reply threading.

Its first consumer is **restart-survivable reply routing**. When a stock
NomadNet/Sideband peer replies to the gateway's LXMF thread with no
explicit ``@addr`` token and no echoed ``meshforge_reply_to`` field, the
bridge resolves the directed downlink back to the mesh node that last
messaged that peer. The in-memory ``ReplyContextStore``
(``reply_context.py``) loses that mapping on a bridge restart and silently
downgrades the reply to broadcast; this durable store survives the
restart. ``ReplyContextStore`` stays in front as a fast in-process cache —
this store is the durable fallback that warms it.

Mirrors ``SessionStore`` idioms (Theme-A step 3): constructing the store
does NO I/O — the DB opens on first real use, so flag-off deploys and the
test fixtures never create the file. ``threading.RLock``;
``_sanitize_positive`` on config numerics (MagicMock-safe); per-call
``connect_tuned`` connections (MF013); every public method swallows and
logs — correlation bookkeeping never breaks the bridge hot path. No
sweeper thread (MF010): TTL is enforced lazily on access and via
``expire_idle()`` from the bridge's ~30s sweep; the row cap is
oldest-evicted in ``expire_idle()``.

The schema carries the full research §5.1 columns up front
(``mesh_packet_id``/``lxmf_msg_hash`` nullable) so later steps (3 =
``FIELD_REPLY_TO`` threading, 4 = ``ROUTING_APP`` ACK match) need no
migration. This slice populates and reads only the subset it needs.
DBSpec registered in ``utils.db_inventory`` (``gateway_correlation``).
"""

import logging
import re
import sqlite3
import threading
import time
import uuid
from typing import Dict, List, Optional

from utils.db_helpers import connect_tuned
from utils.paths import get_real_user_home

from .canonical_message import format_reply_token
from .reply_context import _sanitize_positive

logger = logging.getLogger(__name__)

DEFAULT_TTL_SEC = 86400        # a reply to yesterday's message still threads
DEFAULT_MAX_ROWS = 4096        # per-message (not per-pair) — larger than sessions

_MESHTASTIC_RE = re.compile(r'^!([0-9a-f]{8})$')
_HEX32_RE = re.compile(r'^[0-9a-f]{32}$')
_DIRECTIONS = ('m2r', 'r2m')


class BridgeCorrelationStore:
    """Lazy, durable, TTL'd per-message mesh<->RNS correlation log."""

    def __init__(self, db_path: Optional[str] = None,
                 ttl_sec: Optional[float] = None,
                 max_rows: Optional[int] = None):
        self._db_path = db_path
        self._ttl = _sanitize_positive(ttl_sec, DEFAULT_TTL_SEC)
        self._max = int(_sanitize_positive(max_rows, DEFAULT_MAX_ROWS))
        self._lock = threading.RLock()
        self._initialized = False

    # --- lazy DB ---------------------------------------------------------

    def _resolve_db_path(self) -> str:
        if self._db_path is None:
            config_dir = get_real_user_home() / ".config" / "meshforge"
            config_dir.mkdir(parents=True, exist_ok=True)
            self._db_path = str(config_dir / "gateway_correlation.db")
        return self._db_path

    def _ensure_db(self) -> str:
        """Create schema on first use (double-checked); returns DB path."""
        with self._lock:
            path = self._resolve_db_path()
            if not self._initialized:
                conn = connect_tuned(path)
                try:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS bridge_correlation (
                            corr_id TEXT PRIMARY KEY,
                            mesh_node TEXT,
                            mesh_packet_id INTEGER,
                            channel TEXT,
                            rns_peer_hash TEXT,
                            lxmf_msg_hash BLOB,
                            direction TEXT,
                            created_ts REAL,
                            last_interaction_ts REAL
                        )
                    """)
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_corr_peer "
                        "ON bridge_correlation (rns_peer_hash)")
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_corr_packet "
                        "ON bridge_correlation (mesh_packet_id)")
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_corr_last "
                        "ON bridge_correlation (last_interaction_ts)")
                    conn.commit()
                finally:
                    conn.close()
                self._initialized = True
            return path

    # --- normalization ---------------------------------------------------

    @staticmethod
    def _norm_mesh(mesh_id) -> Optional[str]:
        if not isinstance(mesh_id, str):
            return None
        m = mesh_id.strip().lower()
        return m if _MESHTASTIC_RE.match(m) else None

    @staticmethod
    def _norm_rns(peer_hash) -> Optional[str]:
        if not isinstance(peer_hash, str):
            return None
        p = peer_hash.strip().lower()
        return p if _HEX32_RE.match(p) else None

    # --- core API (never raises into the hot path) ------------------------

    def record(self, mesh_node, rns_peer_hash, *, channel: str = "",
               direction: str = "m2r", mesh_packet_id=None,
               lxmf_msg_hash=None) -> bool:
        """Append one correlation row. Returns False on bad input / DB error.

        ``mesh_packet_id`` and ``lxmf_msg_hash`` are provisioned for later
        steps (4 = ACK match, 3 = FIELD_REPLY_TO threading) and stay NULL
        in this slice unless a caller supplies them.
        """
        try:
            m = self._norm_mesh(mesh_node)
            p = self._norm_rns(rns_peer_hash)
            if m is None or p is None or direction not in _DIRECTIONS:
                return False
            ch = channel if isinstance(channel, str) else ""
            # Guard against bool (a subclass of int) sneaking into the int col.
            pkt = (mesh_packet_id
                   if isinstance(mesh_packet_id, int)
                   and not isinstance(mesh_packet_id, bool)
                   else None)
            now = time.time()
            corr_id = uuid.uuid4().hex
            path = self._ensure_db()
            with self._lock:
                conn = connect_tuned(path)
                try:
                    conn.execute(
                        "INSERT INTO bridge_correlation (corr_id, mesh_node, "
                        "mesh_packet_id, channel, rns_peer_hash, lxmf_msg_hash, "
                        "direction, created_ts, last_interaction_ts) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (corr_id, m, pkt, ch, p, lxmf_msg_hash,
                         direction, now, now),
                    )
                    conn.commit()
                finally:
                    conn.close()
            return True
        except Exception as e:
            logger.warning(f"correlation record failed: {e}")
            return False

    def lookup_reply_token(self, rns_peer_hash) -> Optional[str]:
        """Latest non-expired m2r mesh node for this RNS peer, as a reply token.

        Returns the canonical ``meshtastic:!hex8`` token shape so it slots
        straight into ``_resolve_reply_token`` — which re-validates the
        address (the row is untrusted-ish data, never gospel). None on
        miss / expired.
        """
        try:
            p = self._norm_rns(rns_peer_hash)
            if p is None:
                return None
            cutoff = time.time() - self._ttl  # lazy TTL in the WHERE
            path = self._ensure_db()
            with self._lock:
                conn = connect_tuned(path)
                try:
                    row = conn.execute(
                        "SELECT mesh_node FROM bridge_correlation "
                        "WHERE rns_peer_hash = ? AND direction = 'm2r' "
                        "AND last_interaction_ts >= ? "
                        "ORDER BY last_interaction_ts DESC LIMIT 1",
                        (p, cutoff),
                    ).fetchone()
                finally:
                    conn.close()
            if row and row[0]:
                return format_reply_token("meshtastic", row[0])
            return None
        except Exception as e:
            logger.warning(f"correlation lookup failed: {e}")
            return None

    def expire_idle(self) -> int:
        """Prune expired rows + enforce the row cap. Returns rows removed."""
        try:
            cutoff = time.time() - self._ttl
            path = self._ensure_db()
            removed = 0
            with self._lock:
                conn = connect_tuned(path)
                try:
                    cur = conn.execute(
                        "DELETE FROM bridge_correlation "
                        "WHERE last_interaction_ts < ?",
                        (cutoff,),
                    )
                    removed += cur.rowcount
                    cnt = conn.execute(
                        "SELECT COUNT(*) FROM bridge_correlation").fetchone()[0]
                    if cnt > self._max:
                        cur = conn.execute(
                            "DELETE FROM bridge_correlation WHERE rowid IN "
                            "(SELECT rowid FROM bridge_correlation "
                            " ORDER BY last_interaction_ts ASC LIMIT ?)",
                            (cnt - self._max,),
                        )
                        removed += cur.rowcount
                    conn.commit()
                finally:
                    conn.close()
            return removed
        except Exception as e:
            logger.warning(f"correlation expire failed: {e}")
            return 0

    def active_count(self) -> int:
        """Count of non-expired rows. Opens the DB lazily — callers must
        gate behind the reply-routing flag for flag-off deployments."""
        try:
            cutoff = time.time() - self._ttl
            path = self._ensure_db()
            with self._lock:
                conn = connect_tuned(path)
                try:
                    return conn.execute(
                        "SELECT COUNT(*) FROM bridge_correlation "
                        "WHERE last_interaction_ts >= ?",
                        (cutoff,),
                    ).fetchone()[0]
                finally:
                    conn.close()
        except Exception as e:
            logger.warning(f"correlation count failed: {e}")
            return 0

    # --- operator / API surface ------------------------------------------

    def list_recent(self, limit: int = 50) -> List[Dict]:
        """Recent non-expired rows (newest first). Omits the BLOB column."""
        try:
            cutoff = time.time() - self._ttl
            lim = int(_sanitize_positive(limit, 50))
            path = self._ensure_db()
            with self._lock:
                conn = connect_tuned(path)
                try:
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute(
                        "SELECT corr_id, mesh_node, mesh_packet_id, channel, "
                        "rns_peer_hash, direction, created_ts, "
                        "last_interaction_ts FROM bridge_correlation "
                        "WHERE last_interaction_ts >= ? "
                        "ORDER BY last_interaction_ts DESC LIMIT ?",
                        (cutoff, lim),
                    ).fetchall()
                    return [dict(r) for r in rows]
                finally:
                    conn.close()
        except Exception as e:
            logger.warning(f"correlation list failed: {e}")
            return []

    def clear(self) -> int:
        """Force-remove all rows (operator reset / tests)."""
        try:
            path = self._ensure_db()
            with self._lock:
                conn = connect_tuned(path)
                try:
                    cur = conn.execute("DELETE FROM bridge_correlation")
                    conn.commit()
                    return cur.rowcount
                finally:
                    conn.close()
        except Exception as e:
            logger.warning(f"correlation clear failed: {e}")
            return 0
