"""Reply-context memory for cross-protocol reply routing (Theme-A step 1).

When the gateway bridges a Meshtastic message to an RNS peer, it records
``peer LXMF hash → canonical reply token`` (see
``canonical_message.format_reply_token``). When that peer later replies to
the gateway's LXMF thread — with no explicit ``@addr`` token and no echoed
``meshforge_reply_to`` field, i.e. a stock NomadNet/Sideband client — the
bridge consults this store to auto-resolve the directed downlink back to
the mesh node that messaged them. "If a client receives a message, they
can reply; it just works."

Deliberately in-memory v1 (no DB): a bridge restart loses context and the
reply falls back to today's broadcast behavior — acceptable while the
feature soaks behind ``rns.reply_routing_enabled`` (default off).

Concurrency: the bridge loop thread records while the LXMF receive path
reads — every public method takes the single internal lock.

No sweeper thread (MF010): TTL is enforced lazily on access, and the LRU
cap is enforced on record.
"""

import threading
import time
from collections import OrderedDict
from typing import Optional, Tuple

DEFAULT_TTL_SEC = 86400          # ~24h: a reply to yesterday's message still threads
DEFAULT_MAX_ENTRIES = 1024       # bounded memory; one entry per active RNS peer


def _sanitize_positive(value, default):
    """Coerce a config-sourced numeric; fall back on non-numeric/non-positive.

    Test configs are MagicMocks (truthy, non-numeric attributes) and
    gateway.json is operator-edited — both must degrade to safe defaults
    rather than poison TTL math.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if value <= 0:
        return default
    return value


class ReplyContextStore:
    """TTL'd, LRU-bounded map of RNS peer hash → canonical reply token."""

    def __init__(self, ttl_sec: float = DEFAULT_TTL_SEC,
                 max_entries: int = DEFAULT_MAX_ENTRIES):
        self._ttl_sec = _sanitize_positive(ttl_sec, DEFAULT_TTL_SEC)
        self._max_entries = int(_sanitize_positive(max_entries, DEFAULT_MAX_ENTRIES))
        self._lock = threading.Lock()
        # key: lowercased peer hash hex → (reply_token, inserted_at wall time)
        self._data: "OrderedDict[str, Tuple[str, float]]" = OrderedDict()

    def record(self, peer_hash_hex: str, reply_token: str) -> None:
        """Remember that ``reply_token``'s sender last messaged this peer."""
        if not peer_hash_hex or not reply_token:
            return
        key = peer_hash_hex.lower()
        now = time.time()
        with self._lock:
            self._data.pop(key, None)
            self._data[key] = (reply_token, now)
            # Opportunistic stale purge from the LRU end, then hard cap.
            while self._data:
                oldest_key = next(iter(self._data))
                _, ts = self._data[oldest_key]
                if now - ts > self._ttl_sec:
                    del self._data[oldest_key]
                else:
                    break
            while len(self._data) > self._max_entries:
                self._data.popitem(last=False)

    def get(self, peer_hash_hex: str) -> Optional[str]:
        """Return the reply token for a peer, or None (missing/expired)."""
        if not peer_hash_hex:
            return None
        key = peer_hash_hex.lower()
        now = time.time()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            token, ts = entry
            if now - ts > self._ttl_sec:
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return token

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
