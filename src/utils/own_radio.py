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


#: The reader's closed answer set (r1 #8): anything else is refused, said.
CONFIG_STATES = ("unset", "ok", "invalid", "unreadable")
#: Trust of a configured id (operator 11-27 "corroborate first").
TRUST_CONTRADICTED = "contradicted"
TRUST_ASSERTED = "asserted"
#: Bound on the own-DM packet ids already counted as evidence.
SEEN_PIDS_MAX = 4096

#: THE corroboration evidence (Fable r2 fold, the seam after r1 I2 + r2 I-A/
#: I-B were one defect kind twice: "a config id rules a self-DM out when the
#: evidence no longer supports it"). IN-MEMORY, per process, with the arc's own
#: shelf life (OWN_FRESH_S) -- a persisted record outlived radio swaps and
#: restarts, could be hand-forged, and failed OPEN on a write error. Keyed on
#: the configured value: a config change forgets the old value's evidence.
#: Each own-DM header counts ONCE, at first sight (a re-read of the witness
#: ring can neither refresh a match nor undo a contradiction).
_EVID_LOCK = threading.Lock()
_EVID: Dict[str, Any] = {"cnum": None, "contra_ts": None, "contra_seen": None, "pids": {}}


def _evid_for(cnum: int) -> Dict[str, Any]:
    """Caller holds _EVID_LOCK. Resets the evidence when the config value moved."""
    if _EVID["cnum"] != cnum:
        _EVID.update(cnum=cnum, contra_ts=None, contra_seen=None, pids={})
    return _EVID


def observe_own_headers(items: Any, *, now: Optional[float] = None) -> None:
    """``[(packet_id, header_from), ...]`` of THIS box's own DMs (the ack
    witness's ``tx_from``, matched by packet id) -- evidence for or against the
    configured id. Config read ONCE per batch; garbage items are no evidence;
    no configured id = nothing to judge. Never raises."""
    try:
        pairs = [(pid, num) for pid, num in (x for x in (items or ())
                                             if isinstance(x, tuple) and len(x) == 2)
                 if pid is not None and _valid(num)]
    except Exception:
        return
    if not pairs:
        return
    cfg = _read_config()
    if not (isinstance(cfg, dict) and cfg.get("state") == "ok" and _valid(cfg.get("num"))):
        return
    cnum = int(cfg["num"])
    t = time.time() if now is None else now
    with _EVID_LOCK:
        ev = _evid_for(cnum)
        for pid, num in pairs:
            if pid in ev["pids"]:
                continue
            ev["pids"][pid] = t
            if len(ev["pids"]) > SEEN_PIDS_MAX:
                ev["pids"].pop(next(iter(ev["pids"])))
            if int(num) != cnum and (ev["contra_ts"] is None or t > ev["contra_ts"]):
                ev["contra_ts"], ev["contra_seen"] = t, int(num)


def _within(ts: Optional[float], now: float) -> bool:
    return ts is not None and 0 <= now - ts <= OWN_FRESH_S


def resolve_own(now: Optional[float] = None, *, fresh: bool = True) -> Dict[str, Any]:
    """THE own-id answer every consumer reads.

    ``{"num", "self_only", "source", "note", "info", "config", "trust"}``:
      ``num``       an id that may RULE OUT a self-DM -- only ever a radio
                    connection's read (fresh for a send; ``fresh=False`` = the
                    witness's last-known view). Config alone NEVER rules a
                    self-DM out (Fable r1-r3: an asserted id is only as good
                    as fresh evidence, and the evidence's age was unmeasured
                    three ways; operator 11-27: "measure the id instead");
      ``self_only`` the configured id: a send may stamp it only on a DM
                    addressed to it (-> self, never delivered).
    Vetoes (doubt refuses, said in ``note``): a connection read of ANY age that
    disagrees (r1 I1); an own-DM header that disagreed within OWN_FRESH_S, or
    stamped in the future (hfm #6). ``info`` says the asserted state without a
    warning (r3 F-1). Evidence is written only by ``observe_own_headers`` and
    ``note_own_node``.
    """
    t = time.time() if now is None else now
    cfg = _read_config()
    out = {"num": None, "self_only": None, "source": "", "note": "", "info": "",
           "config": "unreadable", "trust": ""}
    if not isinstance(cfg, dict) or cfg.get("state") not in CONFIG_STATES:
        cfg = {"state": "unreadable", "num": None,
               "why": "the config reader gave an answer outside its closed set"}
    state = cfg["state"]
    cnum = cfg.get("num") if state == "ok" and _valid(cfg.get("num")) else None
    if state == "ok" and cnum is None:
        state = "invalid"
        cfg = dict(cfg, why=cfg.get("why") or "the configured id is not a node")
    out["config"] = state
    if state in ("invalid", "unreadable"):
        out["note"] = f"configured own_node_id refused: {cfg.get('why') or state}"
    st = own_node_status()
    conn_any = st.get("num") if _valid(st.get("num")) else None
    csrc = st.get("source") or "a connection"
    if cnum is None:
        conn = fresh_own_node(now) if fresh else conn_any
        if conn is not None:
            out.update(num=conn, source=csrc)
        return out
    if conn_any is not None and conn_any != cnum:
        out.update(config="mismatch", trust=TRUST_CONTRADICTED, note=(
            f"config says !{cnum:08x} but {csrc} read !{conn_any:08x} — not used "
            "(stale config? radio swapped?); fix [meshtastic] own_node_id in global.ini"))
        return out
    with _EVID_LOCK:
        ev = dict(_evid_for(cnum))
    ct = ev["contra_ts"]
    if ct is not None and (_within(ct, t) or ct > t):
        out.update(trust=TRUST_CONTRADICTED, note=(
            f"config says !{cnum:08x} but this box's own DM header showed "
            f"!{ev['contra_seen']:08x} — not used; fix [meshtastic] own_node_id in global.ini"))
        return out
    conn_ts = st.get("ts") if conn_any == cnum else None
    if (fresh or conn_any is None) and conn_any == cnum and not _within(
            conn_ts if isinstance(conn_ts, (int, float)) else None, t):
        conn_any = None          # a send stamps a connection id only while fresh
    if conn_any == cnum:
        # the CONNECTION's own authority (today's rule), config named beside it
        out.update(num=cnum, source=f"{csrc}, matching the configured id")
    else:
        out.update(self_only=cnum, trust=TRUST_ASSERTED, info=(
            f"configured !{cnum:08x} marks a DM to it as self only — config alone never "
            "rules a self-DM out (this box does not yet measure its own radio id)"))
    return out


def _reset() -> None:
    """Tests only."""
    global _OWN
    with _LOCK:
        _OWN = None
    with _EVID_LOCK:
        _EVID.update(cnum=None, contra_ts=None, contra_seen=None, pids={})
