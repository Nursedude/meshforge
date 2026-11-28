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
#: Corroboration states of a configured id (operator 11-27 "corroborate first").
TRUST_CORROBORATED = "corroborated"
TRUST_CONTRADICTED = "contradicted"
TRUST_ASSERTED = "asserted"
_CORR_LOCK = threading.Lock()


def _corr_path():
    """Where the map records whether the configured id was ever corroborated.
    ONE writer (the map, ``record=True`` / ``observe_own_header``); the TUI only
    reads it."""
    from utils.paths import get_real_user_home
    return get_real_user_home() / ".local" / "share" / "meshforge" / "own_node_corroboration.json"


def _load_corr(cnum: int) -> Optional[Dict[str, Any]]:
    """The record for THIS configured value, or None (absent / garbage / for
    another value). Garbage is never corroboration."""
    import json
    try:
        with open(_corr_path(), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if (not isinstance(doc, dict) or doc.get("config_num") != cnum
            or doc.get("state") not in (TRUST_CORROBORATED, TRUST_CONTRADICTED)
            or not _valid(doc.get("seen"))):
        return None
    return doc


#: witness for a corroboration write that failed (hfm #9) — said on the line
_CORR_WRITE_ERRORS = {"n": 0, "last": ""}


def _record(cnum: int, seen: int, via: str, now: Optional[float]) -> None:
    """Persist the latest evidence about the configured id (latest wins); a
    write happens only when the verdict or the evidence changes."""
    import json
    state = TRUST_CORROBORATED if seen == cnum else TRUST_CONTRADICTED
    with _CORR_LOCK:
        prior = _load_corr(cnum)
        if prior and prior.get("state") == state and prior.get("seen") == seen \
                and prior.get("via") == via:
            return
        doc = {"config_num": cnum, "state": state, "seen": seen, "via": str(via)[:80],
               "ts": time.time() if now is None else now}
        try:
            from utils.paths import atomic_write_text
            path = _corr_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, json.dumps(doc))
        except Exception as e:     # never let bookkeeping cost a send or a read
            _CORR_WRITE_ERRORS["n"] += 1
            _CORR_WRITE_ERRORS["last"] = type(e).__name__


def observe_own_header(num: Any, *, now: Optional[float] = None) -> None:
    """The uplinked header ``from`` of one of THIS box's own DMs (matched by
    packet id in the ack witness) — evidence for or against the configured id.
    Garbage is no evidence; no configured id = nothing to corroborate."""
    if not _valid(num):
        return
    cfg = _read_config()
    if isinstance(cfg, dict) and cfg.get("state") == "ok" and _valid(cfg.get("num")):
        _record(int(cfg["num"]), int(num), "this box's own DM header", now)


def observe_own_headers(nums: Any, *, now: Optional[float] = None) -> None:
    """``observe_own_header`` for one inbox read: the config is read ONCE and
    the newest-last order is kept (latest evidence wins)."""
    seen = [int(n) for n in (nums or ()) if _valid(n)]
    if not seen:
        return
    cfg = _read_config()
    if isinstance(cfg, dict) and cfg.get("state") == "ok" and _valid(cfg.get("num")):
        cnum = int(cfg["num"])
        # one write at most: a contradiction anywhere in this read outranks
        # agreement (doubt refuses), else the agreement
        bad = [n for n in seen if n != cnum]
        _record(cnum, bad[-1] if bad else cnum, "this box's own DM header", now)


def resolve_own(now: Optional[float] = None, *, fresh: bool = True,
                record: bool = False) -> Dict[str, Any]:
    """THE own-id answer every consumer reads.

    ``{"num", "self_only", "source", "note", "config", "trust"}``:
      ``num``       an id that may RULE OUT a self-DM (a connection read, or a
                    configured id CORROBORATED by one), else None;
      ``self_only`` an ASSERTED configured id (never corroborated): a send may
                    stamp it only on a DM addressed to it (-> self, never
                    delivered). Fable r1 I2: an uncontested wrong config id
                    was a new false 'delivered'.
    A connection id of ANY age that disagrees with config vetoes both (r1 I1:
    freshness gates stamping a connection id alone, never the veto).
    ``fresh`` = what a send may stamp; ``fresh=False`` = the witness view.
    ``record`` = the map's judge/send path persists corroboration; every other
    caller is read-only.
    """
    cfg = _read_config()
    out = {"num": None, "self_only": None, "source": "", "note": "",
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
    conn_any = own_node()
    conn = fresh_own_node(now) if fresh else conn_any
    csrc = own_node_status().get("source") or "a connection"
    if cnum is not None and conn_any is not None:
        if record:
            _record(cnum, conn_any, csrc, now)
        if cnum != conn_any:
            out.update(config="mismatch", note=(
                f"config says !{cnum:08x} but {csrc} read !{conn_any:08x} — not used "
                "(stale config? radio swapped?); fix [meshtastic] own_node_id in global.ini"))
            return out
        out.update(num=cnum, source=f"config, confirmed by {csrc}", trust=TRUST_CORROBORATED)
        return out
    if cnum is not None:
        corr = _load_corr(cnum)
        if corr and corr["state"] == TRUST_CORROBORATED:
            out.update(num=cnum, trust=TRUST_CORROBORATED,
                       source=f"config, corroborated by {corr.get('via') or 'evidence'}")
        elif corr:
            out.update(trust=TRUST_CONTRADICTED, note=(
                f"config says !{cnum:08x} but {corr.get('via') or 'evidence'} showed "
                f"!{corr['seen']:08x} — not used; fix [meshtastic] own_node_id in global.ini"))
        else:
            out.update(self_only=cnum, trust=TRUST_ASSERTED, note=(
                f"configured !{cnum:08x} not yet corroborated (no radio read or own DM "
                "header has matched it) — used only to mark a DM to it as self"))
        if _CORR_WRITE_ERRORS["n"]:
            out["note"] += (f" · {_CORR_WRITE_ERRORS['n']} corroboration write(s) failed "
                            f"({_CORR_WRITE_ERRORS['last']})")
        return out
    if conn is not None:
        out.update(num=conn, source=csrc)
    return out


def _reset() -> None:
    """Tests only."""
    global _OWN
    with _LOCK:
        _OWN = None
    _CORR_WRITE_ERRORS.update(n=0, last="")
