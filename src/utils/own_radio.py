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
#: The only writer today is the map collector's TCP-interface drain, which
#: runs only when its HTTP collect yields nothing (Fable r6 #2) -- so on a
#: healthy box this registry is usually EMPTY and rows fall back to the DM's
#: header. A config-supplied id (slice 2, ``resolve_own``) now stands
#: beside it. Residual (r6 W2):
#: a radio swapped inside this window, with no header seen, can be stamped
#: with the old id.
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


def _read_config() -> Dict[str, Any]:
    """``[meshtastic] own_node_id`` from global.ini (BIC-2 DM slice 2). A
    seam of its own so tests pin it (conftest: never this box's real file)."""
    try:
        from utils.global_config import read_own_node_id
        return read_own_node_id()
    except Exception as e:      # never let a config fault cost a send
        return {"state": "unreadable", "num": None, "why": f"config reader raised: {type(e).__name__}"}


def resolve_own(now: Optional[float] = None, *, fresh: bool = True) -> Dict[str, Any]:
    """THE own-id answer every consumer reads: the configured id beside the
    id an existing connection read (``fresh`` = within ``OWN_FRESH_S``, what a
    send may stamp; ``fresh=False`` = the witness line's last-known view).

    ``{"num": int|None, "source": str, "note": str, "config": <state>}``.
    Both present and DISAGREEING -> ``num`` None and the note names both ids:
    a stale config (radio swapped) is said, never absorbed, and the send is
    judged from the DM's own header alone (the safe direction). A refused
    config value never hides a connection id, and is always noted.
    """
    cfg = _read_config()
    if not isinstance(cfg, dict):
        cfg = {"state": "unreadable", "num": None, "why": "config reader returned garbage"}
    state = cfg.get("state")
    cnum = cfg.get("num") if state == "ok" and _valid(cfg.get("num")) else None
    if state == "ok" and cnum is None:
        state = "invalid"
    conn = fresh_own_node(now) if fresh else own_node()
    csrc = own_node_status().get("source") or "a connection"
    note = ""
    if state in ("invalid", "unreadable"):
        note = f"configured own_node_id refused: {cfg.get('why') or state}"
    if cnum is not None and conn is not None and cnum != conn:
        return {"num": None, "source": "", "config": "mismatch",
                "note": (f"config says !{cnum:08x} but {csrc} read !{conn:08x} — "
                         "not used (stale config? radio swapped?); fix "
                         "[meshtastic] own_node_id in global.ini")}
    if cnum is not None and conn is not None:
        return {"num": cnum, "source": f"config, confirmed by {csrc}",
                "config": state, "note": note}
    if cnum is not None:
        return {"num": cnum, "source": "config", "config": state, "note": note}
    if conn is not None:
        return {"num": conn, "source": csrc, "config": state, "note": note}
    return {"num": None, "source": "", "config": state, "note": note}


def _reset() -> None:
    """Tests only."""
    global _OWN
    with _LOCK:
        _OWN = None
