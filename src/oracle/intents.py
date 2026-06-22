"""Pure intent engine for the mesh oracle (Phase 0).

``answer(query, snap)`` and ``is_query(text)`` are pure functions: query text +
a read-only ``NocSnapshot`` in, a ≤237-byte reply string out. No I/O, no clock,
no network, no LLM — fully unit-testable with fixture snapshots.

Calibration discipline (calibrated_claims.md / honest_failure_modes.md): every
answer is a real value or an explicit ``unknown`` / ``stale`` — a blind or stale
source is never phrased as healthy. ``link`` figures using default RF params are
labelled ``(est)``.

Intents: ``status`` ``whatsup`` ``node <id>`` ``wd`` ``link <a> <b>`` ``help``.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from utils.rf import (
    free_space_path_loss,
    haversine_distance,
    link_budget,
    radio_horizon_km,
    snr_estimate,
)

from .snapshot import NocSnapshot, NodeRec

# Reply byte budget. Kept local so the oracle has no gateway import; pinned
# equal to gateway.canonical_message.MESHTASTIC_MAX_PAYLOAD by a test so the two
# consumers of "the Meshtastic payload cap" cannot drift (honest_failure_modes #5).
MAX_REPLY_BYTES = 237
_ELLIPSIS = "…"

# Default RF params for `link` estimates (fleet LongFast/US + typical node).
_LINK_FREQ_MHZ = 906.875
_LINK_TX_DBM = 22.0
_LINK_GAIN_DBI = 2.0
_LINK_NOISE_DBM = -120.0

# Query heads we recognize, after stripping a leading '?'. Aliases fold in.
_ALIASES = {"?": "status", "s": "status", "sup": "whatsup", "watchdog": "wd"}
_QUERY_HEADS = {
    "status", "s", "whatsup", "sup", "node", "wd", "watchdog", "link", "help",
}


# --------------------------------------------------------------------------- #
# Parsing / dispatch
# --------------------------------------------------------------------------- #
def _parse(query: str) -> Tuple[str, List[str]]:
    text = (query or "").strip()
    if not text:
        return "", []
    toks = text.split()
    head = toks[0].lstrip("?").lower() or "status"  # bare "?" -> status
    intent = _ALIASES.get(head, head)
    return intent, toks[1:]


def is_query(text: str) -> bool:
    """True if inbound mesh text looks like an oracle query.

    Conservative on purpose: an exact known head (or a leading '?') triggers,
    arbitrary chatter does not — so the responder never answers random traffic
    or its own replies. Phase 1 adds an address prefix + sender allowlist on
    top of this; this gate stays the floor.
    """
    s = (text or "").strip()
    if not s:
        return False
    if s.startswith("?"):
        return True
    return s.split()[0].lower() in _QUERY_HEADS


def answer(query: str, snap: NocSnapshot) -> str:
    """Resolve a query against a read-only snapshot → a ≤237-byte reply."""
    intent, args = _parse(query)
    if intent == "status":
        body = _status(snap)
    elif intent == "whatsup":
        body = _whatsup(snap)
    elif intent == "wd":
        body = _wd(snap)
    elif intent == "node":
        body = _node(snap, args)
    elif intent == "link":
        body = _link(snap, args)
    elif intent == "help":
        body = _help()
    else:
        body = "try: status whatsup node <id> wd link <a> <b> help"
    prefix = f"dude-AI@{snap.box}: " if snap.box else "dude-AI: "
    return _truncate(prefix + body, MAX_REPLY_BYTES)


# --------------------------------------------------------------------------- #
# Intents
# --------------------------------------------------------------------------- #
def _status(snap: NocSnapshot) -> str:
    parts: List[str] = []
    parts.append(
        f"fleet:{snap.directory_total}" if snap.directory_total is not None
        else "fleet:?"
    )
    if snap.federation_peers is not None:
        ok = snap.federation_ok if snap.federation_ok is not None else "?"
        parts.append(f"fed:{ok}/{snap.federation_peers}")
    parts.append(_wd_short(snap))
    parts.append(_mini_short(snap))
    return " ".join(parts)


def _whatsup(snap: NocSnapshot) -> str:
    items: List[str] = []
    seen = set()
    for sig in snap.wd_signals:
        if sig.get("severity") in ("degraded", "wedge"):
            label = f"{sig.get('class')}({sig.get('subject')})"
            if label not in seen:
                seen.add(label)
                items.append(label)
    for rule in snap.mini_active:
        label = str(rule.get("rule_id"))
        if label not in seen:
            seen.add(label)
            items.append(label)

    notes = _blind_notes(snap)
    if not items:
        base = "all quiet" if not notes else "no active signals"
        return f"{base} [{';'.join(notes)}]" if notes else base
    body = "active: " + "; ".join(items)
    if notes:
        body += f" [{';'.join(notes)}]"
    return body


def _wd(snap: NocSnapshot) -> str:
    if not snap.wd_installed:
        return f"watchdog not installed ({snap.wd_reason or 'unknown'})"
    if snap.wd_stale:
        return f"watchdog STALE {_age(snap.wd_age_s)} — daemon may be down"
    n = len(snap.wd_signals)
    if n == 0:
        return "wd:0 all clear"
    worst = "wedge" if any(s.get("severity") == "wedge" for s in snap.wd_signals) else "degraded"
    first = snap.wd_signals[0]
    return f"wd:{n} {worst} e.g. {first.get('class')}:{first.get('subject')}"


def _node(snap: NocSnapshot, args: List[str]) -> str:
    if not args:
        return "node: need an id, e.g. node !a1b2c3d4"
    key = args[0]
    rec = (
        snap.nodes.get(key)
        or snap.nodes.get(key.lstrip("!"))
        or snap.nodes.get("!" + key.lstrip("!"))
        or _by_name(snap.nodes, key)
    )
    if rec is None:
        if not snap.nodes:
            return f"node {key}: lookup unavailable (no directory)"
        return f"node {key}: unknown (not in directory)"
    bits = [f"node {rec.node_id}"]
    if rec.name:
        bits.append(f"'{rec.name}'")
    bits.append(f"seen {_age(rec.last_seen_age_s)}" if rec.last_seen_age_s is not None else "seen ?")
    if rec.snr is not None:
        bits.append(f"snr {rec.snr:g}")
    if rec.hops is not None:
        bits.append(f"hops {rec.hops}")
    return " ".join(bits)


def _link(snap: NocSnapshot, args: List[str]) -> str:
    if len(args) < 2:
        return "link: need 'lat,lon lat,lon' (opt ,height_m) or known node ids"
    a = _parse_point(args[0], snap)
    b = _parse_point(args[1], snap)
    if a is None or b is None:
        return "link: bad point — use lat,lon[,height_m] or a known node id"
    lat1, lon1, h1 = a
    lat2, lon2, h2 = b
    dist_m = haversine_distance(lat1, lon1, lat2, lon2)
    fspl = free_space_path_loss(dist_m, _LINK_FREQ_MHZ)
    horizon_km = radio_horizon_km(h1 or 0.0, h2 or 0.0)
    rxp = link_budget(_LINK_TX_DBM, _LINK_GAIN_DBI, _LINK_GAIN_DBI, dist_m, _LINK_FREQ_MHZ)
    snr = snr_estimate(rxp, _LINK_NOISE_DBM)
    los = "LOS ok" if dist_m / 1000.0 <= horizon_km else "beyond horizon"
    return (
        f"link {dist_m / 1000.0:.1f}km fspl {fspl:.0f}dB hor {horizon_km:.0f}km "
        f"rxP {rxp:.0f}dBm snr {snr:.0f}dB(est) {los}"
    )


def _help() -> str:
    return "ask: status | whatsup | node <id> | wd | link <lat,lon> <lat,lon> | help"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _wd_short(snap: NocSnapshot) -> str:
    if not snap.wd_installed:
        return "wd:?"
    if snap.wd_stale:
        return f"wd:stale{_age(snap.wd_age_s)}"
    n = len(snap.wd_signals)
    return f"wd:{n} {'OK' if n == 0 else 'SIG'}"


def _mini_short(snap: NocSnapshot) -> str:
    if not snap.mini_installed:
        return "mini:?"
    if snap.mini_stale:
        return f"mini:stale{_age(snap.mini_age_s)}"
    if snap.mini_error_count:
        return f"mini:err{snap.mini_error_count}"
    return f"mini:{len(snap.mini_active)}act"


def _blind_notes(snap: NocSnapshot) -> List[str]:
    notes: List[str] = []
    if not snap.wd_installed:
        notes.append("wd:?")
    elif snap.wd_stale:
        notes.append(f"wd stale{_age(snap.wd_age_s)}")
    if snap.mini_installed and snap.mini_stale:
        notes.append(f"mini stale{_age(snap.mini_age_s)}")
    return notes


def _by_name(nodes: dict, key: str) -> Optional[NodeRec]:
    low = key.lower()
    for rec in nodes.values():
        if rec.name and rec.name.lower() == low:
            return rec
    return None


def _parse_point(token: str, snap: NocSnapshot) -> Optional[Tuple[float, float, Optional[float]]]:
    """Parse 'lat,lon[,height_m]' or resolve a known node id with a position."""
    if "," in token:
        bits = token.split(",")
        try:
            lat = float(bits[0])
            lon = float(bits[1])
            height = float(bits[2]) if len(bits) >= 3 and bits[2] != "" else None
        except (ValueError, IndexError):
            return None
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            return None
        return lat, lon, height
    rec = (
        snap.nodes.get(token)
        or snap.nodes.get(token.lstrip("!"))
        or snap.nodes.get("!" + token.lstrip("!"))
        or _by_name(snap.nodes, token)
    )
    if rec is not None and rec.lat is not None and rec.lon is not None:
        return rec.lat, rec.lon, rec.height_m
    return None


def _age(seconds: Optional[float]) -> str:
    """Compact human age: 12s / 3m / 2h / 1d ('?' if unknown)."""
    if seconds is None:
        return "?"
    s = max(0.0, float(seconds))
    if s < 90:
        return f"{int(s)}s"
    if s < 5400:
        return f"{int(s / 60)}m"
    if s < 172800:
        return f"{int(s / 3600)}h"
    return f"{int(s / 86400)}d"


def _truncate(text: str, max_bytes: int) -> str:
    """UTF-8-safe truncate to ``max_bytes`` with an ellipsis (no broken bytes).

    Mirrors gateway.canonical_message._truncate_utf8; reimplemented locally to
    keep the oracle free of a gateway import (the cap constant is test-pinned).
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    target = max_bytes - len(_ELLIPSIS.encode("utf-8"))
    return encoded[:target].decode("utf-8", errors="ignore") + _ELLIPSIS
