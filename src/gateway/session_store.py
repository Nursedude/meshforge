"""Durable session layer for the gateway (Theme-A step 3).

A session is a durable record of an active conversation between a
Meshtastic node and an RNS (LXMF) peer: one row per
``(mesh_id, rns_peer_hash)`` pair with created/last-activity timestamps
and a message count. Sessions are idle-TTL'd (default ~24h), oldest-
evicted past a row cap, and survive bridge restarts (own SQLite file,
``gateway_sessions.db`` — DBSpec registered in ``utils.db_inventory``).

The consumer is the DM-to-gateway private-reply leg: an R→M directed
downlink arrives at the mesh user as a DM *from the gateway's node*, so
their natural reply is a DM back to the gateway; the bridge looks up the
sender's most-recent active session here and routes the reply privately
to that RNS peer.

Mirrors IdentityBinder idioms (Theme-A step 2): constructing the store
does NO I/O — the DB opens on first real use, so flag-off deploys and
the test fixtures never create the file. ``threading.RLock``;
``_sanitize_positive`` on config numerics (MagicMock-safe); per-call
``connect_tuned`` connections (MF013); every public method swallows and
logs — session bookkeeping never breaks the bridge hot path.
"""

import logging
import re
import sqlite3
import threading
import time
from typing import Dict, List, Optional

from utils.db_helpers import connect_tuned
from utils.paths import get_real_user_home

from .reply_context import _sanitize_positive

logger = logging.getLogger(__name__)

DEFAULT_IDLE_TIMEOUT_SEC = 86400   # a reply to yesterday's message still threads
DEFAULT_MAX_SESSIONS = 1024        # bounded; oldest-evicted

_MESHTASTIC_RE = re.compile(r'^!([0-9a-f]{8})$')
_HEX32_RE = re.compile(r'^[0-9a-f]{32}$')


class SessionStore:
    """Lazy, durable, TTL'd map of mesh node ↔ RNS peer conversations."""

    def __init__(self, db_path: Optional[str] = None,
                 idle_timeout_sec: Optional[float] = None,
                 max_sessions: Optional[int] = None):
        self._db_path = db_path
        self._idle = _sanitize_positive(idle_timeout_sec,
                                        DEFAULT_IDLE_TIMEOUT_SEC)
        self._max = int(_sanitize_positive(max_sessions,
                                           DEFAULT_MAX_SESSIONS))
        self._lock = threading.RLock()
        self._initialized = False

    # --- lazy DB ---------------------------------------------------------

    def _resolve_db_path(self) -> str:
        if self._db_path is None:
            config_dir = get_real_user_home() / ".config" / "meshforge"
            config_dir.mkdir(parents=True, exist_ok=True)
            self._db_path = str(config_dir / "gateway_sessions.db")
        return self._db_path

    def _ensure_db(self) -> str:
        """Create schema on first use (double-checked); returns DB path."""
        with self._lock:
            path = self._resolve_db_path()
            if not self._initialized:
                conn = connect_tuned(path)
                try:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS sessions (
                            mesh_id TEXT NOT NULL,
                            rns_peer_hash TEXT NOT NULL,
                            created_at REAL,
                            last_activity REAL,
                            message_count INTEGER DEFAULT 0,
                            PRIMARY KEY (mesh_id, rns_peer_hash)
                        )
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_sessions_last_activity
                        ON sessions (last_activity)
                    """)
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

    def touch(self, mesh_id, peer_hash) -> bool:
        """Open or refresh the (mesh_id, peer) session. UPSERT.

        Called only for *directed* traffic (one row per directed message),
        so per-message writes are fine — no throttle needed.
        """
        try:
            m = self._norm_mesh(mesh_id)
            p = self._norm_rns(peer_hash)
            if m is None or p is None:
                return False
            now = time.time()
            path = self._ensure_db()
            with self._lock:
                conn = connect_tuned(path)
                try:
                    conn.execute(
                        "INSERT INTO sessions (mesh_id, rns_peer_hash, "
                        "created_at, last_activity, message_count) "
                        "VALUES (?, ?, ?, ?, 1) "
                        "ON CONFLICT(mesh_id, rns_peer_hash) DO UPDATE SET "
                        "last_activity = excluded.last_activity, "
                        "message_count = message_count + 1",
                        (m, p, now, now),
                    )
                    conn.commit()
                finally:
                    conn.close()
            return True
        except Exception as e:
            logger.warning(f"session touch failed: {e}")
            return False

    def lookup(self, mesh_id) -> Optional[str]:
        """Most-recent non-expired RNS peer for a mesh node, or None."""
        try:
            m = self._norm_mesh(mesh_id)
            if m is None:
                return None
            cutoff = time.time() - self._idle  # lazy TTL in the WHERE
            path = self._ensure_db()
            with self._lock:
                conn = connect_tuned(path)
                try:
                    row = conn.execute(
                        "SELECT rns_peer_hash FROM sessions "
                        "WHERE mesh_id = ? AND last_activity >= ? "
                        "ORDER BY last_activity DESC LIMIT 1",
                        (m, cutoff),
                    ).fetchone()
                finally:
                    conn.close()
            return row[0] if row else None
        except Exception as e:
            logger.warning(f"session lookup failed: {e}")
            return None

    def expire_idle(self) -> int:
        """Prune idle sessions + enforce the row cap. Returns rows removed."""
        try:
            cutoff = time.time() - self._idle
            path = self._ensure_db()
            removed = 0
            with self._lock:
                conn = connect_tuned(path)
                try:
                    cur = conn.execute(
                        "DELETE FROM sessions WHERE last_activity < ?",
                        (cutoff,),
                    )
                    removed += cur.rowcount
                    cnt = conn.execute(
                        "SELECT COUNT(*) FROM sessions").fetchone()[0]
                    if cnt > self._max:
                        cur = conn.execute(
                            "DELETE FROM sessions WHERE rowid IN "
                            "(SELECT rowid FROM sessions "
                            " ORDER BY last_activity ASC LIMIT ?)",
                            (cnt - self._max,),
                        )
                        removed += cur.rowcount
                    conn.commit()
                finally:
                    conn.close()
            return removed
        except Exception as e:
            logger.warning(f"session expire failed: {e}")
            return 0

    def active_count(self) -> int:
        """Count of non-expired sessions. Opens the DB lazily — callers
        must gate behind the sessions flag for flag-off deployments."""
        try:
            cutoff = time.time() - self._idle
            path = self._ensure_db()
            with self._lock:
                conn = connect_tuned(path)
                try:
                    return conn.execute(
                        "SELECT COUNT(*) FROM sessions "
                        "WHERE last_activity >= ?",
                        (cutoff,),
                    ).fetchone()[0]
                finally:
                    conn.close()
        except Exception as e:
            logger.warning(f"session count failed: {e}")
            return 0

    # --- operator CLI surface ---------------------------------------------

    def list_active(self) -> List[Dict]:
        try:
            cutoff = time.time() - self._idle
            path = self._ensure_db()
            with self._lock:
                conn = connect_tuned(path)
                try:
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute(
                        "SELECT mesh_id, rns_peer_hash, created_at, "
                        "last_activity, message_count FROM sessions "
                        "WHERE last_activity >= ? "
                        "ORDER BY last_activity DESC",
                        (cutoff,),
                    ).fetchall()
                    return [dict(r) for r in rows]
                finally:
                    conn.close()
        except Exception as e:
            logger.warning(f"session list failed: {e}")
            return []

    def expire_all(self, mesh_id: Optional[str] = None) -> int:
        """Force-expire all sessions, or just one mesh node's."""
        try:
            path = self._ensure_db()
            with self._lock:
                conn = connect_tuned(path)
                try:
                    if mesh_id is not None:
                        m = self._norm_mesh(mesh_id)
                        if m is None:
                            return 0
                        cur = conn.execute(
                            "DELETE FROM sessions WHERE mesh_id = ?", (m,))
                    else:
                        cur = conn.execute("DELETE FROM sessions")
                    conn.commit()
                    return cur.rowcount
                finally:
                    conn.close()
        except Exception as e:
            logger.warning(f"session expire_all failed: {e}")
            return 0
