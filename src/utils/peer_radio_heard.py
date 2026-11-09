"""A peer's RADIO hearing time, with its own trust (MAP-H H14, 2026-11-08).

Federation used to carry only a peer's directory ``last_seen`` — the time the
peer's collector WROTE the row (``node_history`` stamps local rows ``now``),
not the time its radio heard the node. Read as hearing time it paints dead
nodes green (refuted 11-08: attempt 1, never shipped).

The peer now exports ``radio_heard`` (its own radio's last-heard epoch, from
its local_radio features only). This module judges it, pure:

* ``peer_radio_trust`` — is this PEER's radio a witness right now? Its newest
  ``radio_heard`` across every row must sit inside the window. A peer whose
  radio has heard NOTHING in the window has a frozen clock (meshtasticd started
  before NTP sync: lastHeard stops advancing — moc2 10-26..11-08) or a deaf
  radio; either way its claims are UNOBSERVABLE, never "not heard".
* ``peer_heard_verdict`` — for one node: ``heard`` / ``not_heard`` /
  ``unobservable`` from the trusted peers' claims, or None when no peer makes
  a claim at all (absent ≠ unobservable: an old-code peer exports nothing).

It never touches ``is_online`` — that stays THIS box's radio's judgement.
"""

import math
from typing import Any, Dict, Mapping, Optional, Tuple

from utils._map_collector_config import _ONLINE_FUTURE_SKEW_TOLERANCE_S

HEARD = "heard"
NOT_HEARD = "not_heard"
UNOBSERVABLE = "unobservable"

#: A gateway box's map hands meshtasticd to the gateway (#17 single-consumer),
#: so it has no radio rows BY DESIGN — said as that, never as a fault.
DEFERRED_REASON = "peer's map defers its radio to its gateway (#17)"


def coerce_radio_ts(value: Any, now: float) -> Optional[float]:
    """A plausible hearing epoch, or None. Rejects bool, non-finite, <= 0, and
    anything more than the skew tolerance in the future (a future stamp would
    read fresh forever)."""
    if isinstance(value, bool):
        return None
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(ts) or ts <= 0:
        return None
    try:
        if not math.isfinite(now) or ts > now + _ONLINE_FUTURE_SKEW_TOLERANCE_S:
            return None
    except TypeError:
        return None
    return ts


def ingest_radio_ts(value: Any) -> Optional[float]:
    """Ingest-side shape check only: a finite epoch > 0, else None. A FUTURE
    stamp is KEPT here so ``peer_radio_trust`` can name the skew ("clock
    ahead"); ``coerce_radio_ts`` drops it before it is ever read as a time
    (review r1 F5: dropping it at ingest made the skew read as "no rows")."""
    if isinstance(value, bool):
        return None
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    return ts if math.isfinite(ts) and ts > 0 else None


def peer_radio_trust(exported: bool, newest: Any, now: float,
                     window_s: float, *, ok: bool = True,
                     in_backoff: bool = False,
                     deferred: Optional[bool] = None) -> Tuple[bool, str]:
    """(trusted, reason) for one peer's radio ON ONE NETWORK as a witness at
    ``now``. ``newest`` = that peer's newest radio_heard on that network
    (review r1 F3: one fresh MeshCore row must not vouch for a frozen
    Meshtastic radio). A failed or backed-off poll is said as such, never as
    "old code" (r1 F7).

    ``deferred`` = the peer said its map defers this radio to its gateway
    (True), said it does not (False), or could not be asked (None). Only a
    True changes anything, and only ONE reason: a peer that exports radio
    rows but has none on this network. Every real fault keeps its own reason
    — stale/frozen, clock ahead, unreadable, old code (review r1: a benign
    label must never hide a finding) — and fresh rows still count."""
    if in_backoff:
        return False, "peer in federation backoff (not polled)"
    if not ok:
        return False, "peer fetch failed this cycle"
    if deferred is True and exported and newest is None:
        return False, DEFERRED_REASON
    return _radio_trust(exported, newest, now, window_s)


def _radio_trust(exported: bool, newest: Any, now: float,
                 window_s: float) -> Tuple[bool, str]:
    """The row-evidence half of peer_radio_trust (poll state already ok)."""
    if not exported:
        return False, "peer does not export radio hearing times (old code)"
    if newest is None:
        return False, "peer has no radio-heard rows on this network"
    try:
        newest_f = float(newest)
        if newest_f > now + _ONLINE_FUTURE_SKEW_TOLERANCE_S:
            return False, "peer radio clock is ahead of this box"
    except (TypeError, ValueError):
        return False, "peer radio-heard time unreadable"
    ts = coerce_radio_ts(newest_f, now)
    if ts is None:
        return False, "peer radio-heard time unreadable"
    try:
        fresh = (now - ts) < float(window_s)
    except (TypeError, ValueError):
        fresh = False
    if not fresh:
        return False, "peer radio heard nothing in the window (frozen clock or deaf)"
    return True, ""


def peer_heard_verdict(heard_by: Mapping[str, Any],
                       trust: Mapping[str, Tuple[bool, str]],
                       names: Mapping[str, Optional[str]],
                       now: float, window_s: float) -> Optional[Dict[str, Any]]:
    """Judge one node from every peer's radio claim.

    ``heard_by`` = {peer_host: radio_heard}; ``trust`` = {peer_host:
    peer_radio_trust(...)} for THIS node's network; ``names`` = {peer_host:
    friendly name}. Returns None when no peer makes a valid claim.

    Every untrusted claimer is SAID, never averaged away (r1 F8): an
    unobservable verdict names all of them with their reasons; a heard /
    not_heard verdict lists untrusted peers whose claim is fresher than the
    trusted one under ``unverified``.
    """
    claims = {}
    for peer, raw in (heard_by or {}).items():
        ts = coerce_radio_ts(raw, now)
        if ts is not None:
            claims[peer] = ts
    if not claims:
        return None

    def label(peer: str) -> str:
        return (names or {}).get(peer) or peer

    def why(peer: str) -> str:
        return (trust or {}).get(peer, (False, ""))[1] or "peer not polled this cycle"

    trusted = {p: ts for p, ts in claims.items()
               if (trust or {}).get(p, (False, ""))[0]}
    untrusted = sorted(p for p in claims if p not in trusted)
    if not trusted:
        return {"state": UNOBSERVABLE,
                "by": ", ".join(label(p) for p in untrusted), "ts": None,
                "age_s": None,
                "reason": "; ".join(f"{label(p)}: {why(p)}" for p in untrusted)}

    best = max(trusted, key=lambda p: trusted[p])
    ts = trusted[best]
    try:
        heard = (now - ts) < float(window_s)
    except (TypeError, ValueError):
        heard = False
    out: Dict[str, Any] = {"state": HEARD if heard else NOT_HEARD,
                           "by": label(best), "ts": ts,
                           "age_s": max(0, int(now - ts)), "reason": ""}
    fresher = [p for p in untrusted if claims[p] > ts]
    if fresher:
        out["unverified"] = "; ".join(f"{label(p)}: {why(p)}" for p in fresher)
    return out
