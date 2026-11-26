"""This box's own Meshtastic node id -- read from an EXISTING radio
connection, never inferred from traffic.

BIC-2 DM slice 1, Fable r4 #1 (operator 2026-11-26, "Both: send-time +
hardened"): a DM to our own radio must never read "delivered". The ack
witness's copy of the DM's own uplinked header (``tx_from``) can be lost,
overwritten or late, so the judge also takes this id when a connection
already in this process has read it (``myInfo.my_node_num`` /
``localNode.nodeNum``). Nothing here opens a connection: the TX path must
never read ``/api/v1/fromradio`` (it steals acks), and a broker that
bridges the public server carries other boxes' traffic (memory:
own identity is never inferred from traffic).

Unknown is a first-class answer (``None``), and the witness line says so.
Thread-safe; one per process (the map).
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

_LOCK = threading.Lock()
_OWN: Optional[Dict[str, Any]] = None

#: 0 and 0xFFFFFFFF (broadcast) are never a node.
_MAX = 0xFFFFFFFF
#: A send stamps the id only if a connection re-read it this recently (r5 #1:
#: "last good id stands" for a process lifetime would outlive a radio swap).
#: BELIEVED: the map collector re-reads it every collect cycle (minutes).
OWN_FRESH_S = 3600


def _valid(num: Any) -> bool:
    return (isinstance(num, int) and not isinstance(num, bool) and 0 < num < _MAX)


def note_own_node(num: Any, *, source: str, now: Optional[float] = None) -> None:
    """Record the id a connection read. Anything that cannot be a node is
    ignored (the last good id stands). Never raises."""
    global _OWN
    if not _valid(num):
        return
    with _LOCK:
        _OWN = {"num": int(num), "source": str(source)[:80],
                "ts": time.time() if now is None else now}


def own_node() -> Optional[int]:
    with _LOCK:
        return None if _OWN is None else _OWN["num"]


def fresh_own_node(now: Optional[float] = None) -> Optional[int]:
    """The id only if noted within ``OWN_FRESH_S`` (and not in the future) --
    what a send stamps on its row."""
    t = time.time() if now is None else now
    with _LOCK:
        if _OWN is None or not (0 <= t - _OWN["ts"] <= OWN_FRESH_S):
            return None
        return _OWN["num"]


def own_node_status() -> Dict[str, Any]:
    """``{"num", "source", "ts"}`` or ``{"num": None, ...}`` when unknown."""
    with _LOCK:
        if _OWN is None:
            return {"num": None, "source": "", "ts": None}
        return dict(_OWN)


def _reset() -> None:
    """Tests only."""
    global _OWN
    with _LOCK:
        _OWN = None
