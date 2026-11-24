"""MOC Inbox (BIC-1): one thread list over the messages store, for a browser.

``/inbox`` is the page; ``GET /api/inbox`` and ``POST /api/inbox/send`` are
its API. Pure functions here, the HTTP gates stay in ``map_http_handler``.

Honesty rules this module keeps (honest_failure_modes #1, #2):
  * a store that cannot be read is ``unavailable`` — never an empty inbox;
  * an outbound row reads ``handed_off`` / ``not_sent`` — the store's
    ``delivered`` column means "the send call handed it to the radio",
    which is not delivery, so the word "delivered" is never shown;
  * RNS threads are read-only: ``commands.gateway._get_bridge()`` builds a
    NEW unstarted bridge in the calling process, so a send from the map
    process would never reach the running gateway daemon. They say so.
"""
import logging
import re
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from commands import messaging
from utils.inbox_heard import JOIN_AFTER_S, KEPT_NOTE, BoxHeard

logger = logging.getLogger(__name__)

#: Every spelling the stores use for "no single recipient".
BROADCAST_IDS = (None, "", "^all", "!ffffffff")

#: Meshtastic text payload is ~233 bytes; stay under it with headroom.
MAX_TEXT_BYTES = 200

#: Meshtastic channel indices are 0 (primary) .. 7.
MAX_CHANNEL = 7

RNS_SEND_BLOCKED = (
    "Replying over RNS needs the gateway send channel, which is not wired "
    "to the web inbox yet. Reply from the TUI (Messaging) for now."
)

_NODE_ID = re.compile(r"^![0-9a-fA-F]{1,8}$")


def _thread_key(msg: Dict[str, Any]) -> Tuple[str, str, Optional[str], Optional[int]]:
    """(key, kind, partner, channel) for one stored row."""
    network = msg.get("network") or "unknown"
    outbound = msg.get("from_id") == "local"
    peer = msg.get("to_id") if outbound else msg.get("from_id")
    if network == "rns":
        # LXMF is always point-to-point; inbound rows are stored to_id=None.
        return f"rns:{peer}", "dm", peer, None
    if msg.get("to_id") in BROADCAST_IDS:
        ch = msg.get("channel") or 0
        return f"{network}:channel:{ch}", "channel", None, ch
    return f"{network}:{peer}", "dm", peer, None


_HAS_OFFSET = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")


def _utc(ts: Optional[str]) -> Optional[str]:
    """Tag a stored timestamp as UTC. Rows are written with SQLite
    ``CURRENT_TIMESTAMP`` (UTC, no offset); a browser reads an offset-less
    ISO time as LOCAL, so on an HST box every message showed 10 h off."""
    if not ts or _HAS_OFFSET.search(ts):
        return ts
    return ts.replace(" ", "T") + "+00:00"


def _state(msg: Dict[str, Any]) -> str:
    if msg.get("from_id") != "local":
        return "received"
    if msg.get("_radio_echo"):
        return "radio_echo"
    # ``delivered`` is each WRITER's own hand-off record: inbox send() sets
    # it only for a real radio id; the TUI path (send_message) sets it when
    # the gateway/CLI accepted the text and has no id to give (review r3
    # LOW, kept: "not_sent" would be false there). Deferred:
    # tui-send-records-radio-packet-id-2026-11-14.
    return "handed_off" if msg.get("delivered") else "not_sent"


#: An echo / second-leg copy of one packet lands within a second; 10 s is
#: headroom, not a mesh round trip.
ECHO_WINDOW_S = 10


def _epoch(ts: Optional[str]) -> Optional[float]:
    u = _utc(ts)
    if not u:
        return None
    if u.endswith("Z"):  # fromisoformat before 3.11 rejects the Z suffix
        u = u[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(u).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def _fold_duplicates(messages: List[Dict[str, Any]],
                     era: Optional[float] = None) -> List[Dict[str, Any]]:
    """One send / one packet → one row (2026-10-21, measured on the store).

    (a) the local MQTT subscriber's ENCRYPTED leg stored a second copy of a
        JSON-leg text with no channel (→ 0) and to_id "" — drop it when its
        JSON twin (same sender + text, within the window, wire recipient) is
        present;
    (b) an uplinked channel publishes this radio's OWN packet, which reads
        back as "received" — fold it into the matching handed-off send (same
        channel + text, within the window) and mark that send radio-echoed.
        That proves the radio took the packet and uplinked it — NOT that it
        reached the air (the uplink can precede TX) and never delivery.
        A legacy (id-less) echo carries NO rx SNR/RSSI (the radio never
        received it); a node HEARD over the air always does, so a neighbour
        repeating the same text is never swallowed.
    Matching is on stored facts only; an unmatched row is always kept.
    Measured 10-21: 32 send/echo pairs, max gap 1 s.

    BIC-2 (INBOX-T F1, 2026-11-14): when a received row carries the radio's
    packet id, the id DECIDES — it pairs only with the send that owns that
    id, never with a send of the same text (a stranger repeating my words is
    a real message). Bounded by the heard-by window (``JOIN_AFTER_S``, one
    constant with ``inbox_heard``): a 32-bit id match an hour later is not
    proof. The text/time match survives only for rows stored before ids.
    ⚠️ An id-matched copy WITH rx stats (my packet relayed back and uplinked
    by another gateway) also folds — it IS my packet; "heard over the air"
    is the heard-by legs' state (BIC-2 step 2), not this fold's (review r1 F3).

    (a) and (b) now share ONE fallback rule (the era cutoff below): a row
    carrying an id is never pre-era, so a one-sided id never text-folds in
    either step (closes review r1 F6's asymmetry).
    """
    rows = [m for m in messages if m.get("network") == "meshtastic"]
    when = {id(m): _epoch(m.get("timestamp")) for m in rows}

    # THE ERA CUTOFF (review r3, operator 11-14): the text/time match may
    # fold ONLY rows stored before this store began keeping packet ids —
    # judged by row id (monotonic), never the wall clock (hfm #6). A row with
    # no id stored AFTER that point never folds by text: a duplicate is
    # shown, a message is never hidden. Third finding of one kind (r1 F1,
    # r2 MED, r3 MED) — the property test in test_inbox_packet_id_fold is
    # the gate. Gap (accepted): a read set with NO id'd row at all is all
    # "pre-era" — a box that never stored an id keeps the old match.
    # Review r4: the era is the STORE's first RECEIVED row with an id
    # (``messaging.first_received_id_row``, passed by read_inbox) — never the
    # read window (it slid: older id'd rows fall out of the newest-500 and
    # post-era rows turned "pre-era"), never an OUTGOING id (sends kept ids
    # from 11-08, received rows only from 11-14 20:16). The read-set value is
    # only the pure-caller fallback. ``inf`` = no received id yet (all
    # pre-era); ``0`` = era unknown (nothing text-folds).
    if era is None:
        ided = [m.get("id") for m in rows
                if m.get("packet_id") is not None and m.get("id") is not None
                and m.get("from_id") != "local"]
        era = min(ided) if ided else float("inf")

    def pre_era(m):
        # THE invariant: a row is hidden by TEXT only if it is pre-era itself
        return m.get("id") is not None and m.get("id") < era

    def order(m):
        return (when[id(m)] is None, when[id(m)] or 0, m.get("id") or 0)

    def near(a, b, window=ECHO_WINDOW_S):
        ta, tb = when[id(a)], when[id(b)]
        return ta is not None and tb is not None and abs(ta - tb) <= window

    def same_packet_time(a, b):
        return near(a, b, JOIN_AFTER_S)

    def twin(a, b):
        """(a) same sender + text already keyed: two legs of ONE packet?"""
        pa, pb = a.get("packet_id"), b.get("packet_id")      # b = the copy hidden
        if pa is not None and pb is not None:
            return pa == pb and same_packet_time(a, b)   # bounded like (b)/(c) — r2
        # an id'd copy is never pre-era (the era IS the first received id'd
        # row), so only an id-less, pre-era copy can be dropped by text
        return pre_era(b) and near(a, b)

    def echo_of(send, rx):
        """(b) is ``rx`` my radio's copy of ``send``? The wire id decides."""
        ps, pr = send.get("packet_id"), rx.get("packet_id")
        if pr is not None:
            # an id'd received row is hidden ONLY by its own send's id (an
            # id-less TUI send's id'd echo shows twice — a duplicate, r2)
            return ps == pr and same_packet_time(send, rx)
        # an id-less received row: text match ONLY when it is pre-era (r2/r3
        # MED: an id-less stranger stored after the era is never hidden). Its
        # send may carry an id — the transitional pairs of r4 F2.
        return (pre_era(rx) and rx.get("snr") is None and rx.get("rssi") is None
                and near(send, rx))

    by_sender: Dict[Tuple[Any, Any], List[Dict[str, Any]]] = {}
    sends: Dict[Tuple[Any, Any], List[Dict[str, Any]]] = {}
    for m in rows:
        by_sender.setdefault((m.get("from_id"), m.get("content")), []).append(m)
        if (m.get("from_id") == "local" and m.get("delivered")
                and m.get("to_id") in BROADCAST_IDS):
            sends.setdefault((m.get("channel"), m.get("content")), []).append(m)

    drop = set()
    for m in rows:
        if m.get("from_id") == "local" or m.get("to_id") != "":
            continue
        if any(o is not m and o.get("to_id") != "" and twin(o, m)
               for o in by_sender[(m.get("from_id"), m.get("content"))]):
            drop.add(id(m))

    # (c) one packet stored twice (operator 11-14: "Oops doubled", rows
    # 6089/6090 — this radio's own text from another client, two copies
    # 2-8 s apart): the same SENDER + packet id is one message. A packet id
    # is unique per sender, not per mesh; a row with no id is never merged
    # (a real resend and a storage double look identical without one).
    # Review r1: same TEXT and inside the heard window too (a rebooted node
    # can reuse an id for another message, F5); the kept copy is the one
    # with rx evidence, then the first stored (F7).
    def better(a, b):
        ev = lambda r: r.get("snr") is not None or r.get("rssi") is not None
        if ev(a) != ev(b):
            return a if ev(a) else b
        return a if (a.get("id") or 0) <= (b.get("id") or 0) else b

    # Oldest first, each group ANCHORED at its first copy (the window never
    # slides with the winner): the result is a function of the row SET, not
    # the order the store returned it (review r2).
    cand = sorted((m for m in rows if id(m) not in drop
                   and m.get("from_id") != "local"
                   and m.get("packet_id") is not None),
                  key=order)
    kept: Dict[Tuple[Any, int, Any], List[List[Dict[str, Any]]]] = {}
    for m in cand:
        groups = kept.setdefault((m.get("from_id"), m.get("packet_id"), m.get("content")), [])
        for g in groups:                       # g = [anchor, winner]
            if same_packet_time(g[0], m):
                win = better(g[1], m)
                drop.add(id(m) if win is g[1] else id(g[1]))
                g[1] = win
                break
        else:
            groups.append([m, m])

    # oldest first on both sides: which echo pairs with which send is a
    # function of the row SET, not the store's return order (review r3)
    echoed, used = set(), set()
    for m in sorted(rows, key=order):
        if (id(m) in drop or m.get("from_id") == "local"
                or m.get("to_id") not in BROADCAST_IDS):
            continue
        for o in sorted(sends.get((m.get("channel"), m.get("content")), ()), key=order):
            if id(o) not in used and echo_of(o, m):
                echoed.add(id(m))
                used.add(id(o))
                break
    out = []
    for m in messages:
        if id(m) in drop or id(m) in echoed:
            continue
        out.append(dict(m, _radio_echo=True) if id(m) in used else m)
    return out


def build_threads(messages: List[Dict[str, Any]],
                  era: Optional[float] = None) -> List[Dict[str, Any]]:
    """Group stored rows into conversations, newest conversation first.
    ``era``: the store's first received id'd row (see ``_fold_duplicates``)."""
    threads: Dict[str, Dict[str, Any]] = {}
    for msg in _fold_duplicates(messages, era):
        key, kind, partner, channel = _thread_key(msg)
        t = threads.get(key)
        if t is None:
            network = msg.get("network") or "unknown"
            can_send = network == "meshtastic"
            t = threads[key] = {
                "key": key, "network": network, "kind": kind,
                "partner": partner, "channel": channel,
                "can_send": can_send,
                "send_blocked_reason": None if can_send else (
                    RNS_SEND_BLOCKED if network == "rns"
                    else f"Sending on {network} is not supported from the inbox."),
                "messages": [],
            }
        t["messages"].append({
            "id": msg.get("id"),
            "ts": _utc(msg.get("timestamp")),
            "direction": "out" if msg.get("from_id") == "local" else "in",
            "from_id": msg.get("from_id"),
            "content": msg.get("content", ""),
            "snr": msg.get("snr"),
            "rssi": msg.get("rssi"),
            "state": _state(msg),
            # the radio's id — the one wire fact a heard-by join keys on
            "packet_id": msg.get("packet_id"),
        })
    out = []
    for t in threads.values():
        t["messages"].sort(key=lambda m: (m["ts"] or "", m["id"] or 0))
        last = t["messages"][-1]
        t["last_ts"] = last["ts"]
        t["preview"] = last["content"][:80]
        t["count"] = len(t["messages"])
        out.append(t)
    out.sort(key=lambda t: t["last_ts"] or "", reverse=True)
    return out


def read_inbox(limit: int = 500) -> Dict[str, Any]:
    """Tri-state read: ``ok`` with threads, or ``unavailable`` with the reason."""
    try:
        result = messaging.get_messages(limit=limit, network="all")
    except Exception as e:  # the store itself raised — still unobservable, not empty
        logger.warning("inbox: message store raised: %s", e)
        return {"state": "unavailable", "error": f"message store error: {e}"}
    if not result.success:
        return {"state": "unavailable", "error": result.message}
    rows = (result.data or {}).get("messages", [])
    era_error = None
    try:
        first = messaging.first_received_id_row()
        era = float("inf") if first is None else first
    except Exception as e:   # unknown era → fold nothing by text (a duplicate, never a hide)
        logger.warning("inbox: era unreadable, text fold off: %s", e)
        era, era_error = 0, f"era unreadable: {e}"
    threads = build_threads(rows, era=era)
    heard_error = _attach_heard(threads)
    # hfm #9 witness: sends whose verdict aged out of a box's ring before
    # any read kept it (the "freeze on read" gap, operator 11-23)
    shown = sum(t["count"] for t in threads)
    rx = _rx_coverage()
    unseen = _unseen(rx)
    # message_count = rows read from the store; folded = second copies of one
    # packet (radio echo / encrypted leg) not shown as their own messages
    return {"state": "ok", "threads": threads, "message_count": len(rows),
            "shown_count": shown, "folded_count": len(rows) - shown,
            "rx": rx, **({"unseen": unseen} if unseen is not None else {}),
            **({"era_error": era_error} if era_error else {}),
            **({"heard_error": heard_error} if heard_error else {})}


#: what a send says when the fleet evidence itself could not be read
UNREADABLE_WHY = "fleet evidence unreadable"
HEARD_UNREADABLE = f"heard by: unobservable ({UNREADABLE_WHY})"
#: a kept hearing beside ONE send's judging fault — the fleet evidence read
#: fine; never worded as a fleet-wide failure (r1 nit 1)
SEND_UNJUDGED_WHY = "this send could not be judged (see heard_error)"
#: the per-send exit's own blind line, kept or not (Fable r2 LOW 2)
HEARD_SEND_UNJUDGED = f"heard by: unobservable ({SEND_UNJUDGED_WHY})"


def _attach_heard(threads: List[Dict[str, Any]]) -> Optional[str]:
    """BIC-2 step 3 (INBOX-T F2): every outbound BROADCAST send with a radio
    packet id carries ``heard`` — the fleet's spools judged once for the
    whole read (``inbox_legs``). DMs carry none (their truth is the ack path,
    not hearings); a box with no fleet says it cannot see hearings. A
    failure reading the evidence never 503s the inbox: each send says the
    fleet is unobservable — or, where the store KEPT hearings for it, names
    them beside why it cannot judge now — and the read returns why (hfm #9)."""
    from utils import inbox_legs
    sends = [m for t in threads if t.get("network") == "meshtastic" and t.get("kind") == "channel"
             for m in t["messages"]
             if m.get("direction") == "out" and m.get("packet_id") is not None
             and m.get("state") in ("handed_off", "radio_echo")]
    if not sends:
        return None
    now = time.time()
    errors = []
    # BIC-2 11-23: verdicts judged while a box's 24 h ring still covered the
    # send are KEPT in the store and merged back (inbox_heard.merge_frozen) —
    # never re-judged blind. A store that cannot read them says so; the live
    # judgement still shows. Read FIRST, before any exit that cannot judge
    # now (seam 11-24): every such exit goes through ``inbox_legs.blind_heard``
    # so none drops what the store kept (r1 11-24 no list; the damaged-list
    # and per-send exits dropped them until this seam).
    try:
        kept, refused = messaging.kept_heard([(m["id"], m["packet_id"]) for m in sends])
        if refused:
            errors.append(f"{refused} kept heard-by row(s) refused (not a kept hearing)")
    except Exception as e:
        logger.warning("inbox: kept heard-by verdicts unreadable: %s", e)
        kept = None
        errors.append(f"kept heard-by verdicts unreadable: {type(e).__name__}: {e}")

    def blind(m: Dict[str, Any], line: str, why: str) -> Dict[str, Any]:
        # runs inside except handlers: a kept row it cannot read falls back
        # to the exit's blind line and is witnessed — never a dead read (r1 nit 2)
        try:
            return inbox_legs.blind_heard((kept or {}).get(m["id"], []), line=line, why=why)
        except Exception as e:
            logger.warning("inbox: kept heard-by rows unreadable for send %s: %s", m.get("id"), e)
            errors.append(f"send {m.get('id')}: kept heard-by row(s) unreadable: "
                          f"{type(e).__name__}: {e}")
            return {"line": line}

    try:
        sections = inbox_legs.load_sections(now)
    except Exception as e:
        logger.warning("inbox: fleet evidence unreadable: %s", e)
        for m in sends:
            m["heard"] = blind(m, HEARD_UNREADABLE, UNREADABLE_WHY)
        errors.insert(0, f"fleet evidence unreadable: {e}")
        return "; ".join(errors)
    if sections is None:
        # no target list (standalone, or a non-center fleet box): the
        # blindness is SAID on each send (BIC-2 one-box, 11-24); kept
        # hearings still show. Nothing failed → no heard_error of its own.
        for m in sends:
            m["heard"] = blind(m, inbox_legs.NO_FLEET_LINE, inbox_legs.NO_TARGETS)
        return "; ".join(errors) or None
    # cannot_hear (BIC-2, 11-16): the DECLARED segments. Absent file = inert;
    # an unreadable or wrong one = no segments AND the read says why (never a
    # silent "nothing declared").
    from utils import rf_segments
    try:
        decl = rf_segments.load()
    except Exception as e:   # r1 H1: guarded like every other step here; never a dead read
        decl = rf_segments.Declared({}, None,
                                    f"declared segments unreadable ({type(e).__name__})")
    if decl.error:
        logger.warning("inbox: %s", decl.error)
        errors.append(decl.error)
    # r1 M1: a declared alias the fleet does not list (a typo) left its box
    # silently undeclared. SAY it; the valid declarations still apply.
    # r2 MED: the sender is exempt. parse() requires send_from in segments,
    # and the target list is opt-in, so a center need not list itself.
    stray = sorted(a for a in decl.segments if a not in sections and a != decl.send_from)
    if stray:
        errors.append(f"declared segments name box(es) not in the fleet list: {', '.join(stray)}")
    to_keep = []
    for m in sends:   # each send guarded on its own: one bad box never breaks the read (r5)
        sent = _epoch(m.get("ts"))
        try:
            frozen = {b["alias"]: BoxHeard(b["alias"], b["state"],
                                           b["reason"] + KEPT_NOTE,
                                           rx_ts=b["rx_ts"], skewed=b["skewed"], kept=True)
                      for b in (kept or {}).get(m["id"], [])}
            heard = inbox_legs.heard_from(m["packet_id"], sent if sent is not None
                                          else float("nan"), sections, now=now,
                                          segments=decl.segments,
                                          send_segment=decl.send_segment,
                                          frozen=frozen)
            freeze = heard.pop("freeze")
            m["heard"] = heard
            if kept is not None and freeze:
                to_keep.append((m["id"], m["packet_id"],
                                [{k: v for k, v in asdict(b).items() if k != "kept"}
                                 for b in freeze]))
        except Exception as e:
            logger.warning("inbox: heard-by failed for send %s: %s", m.get("id"), e)
            errors.append(f"send {m.get('id')}: {type(e).__name__}: {e}")
            m["heard"] = blind(m, HEARD_SEND_UNJUDGED, SEND_UNJUDGED_WHY)
    if to_keep:
        try:
            messaging.keep_heard(to_keep, now=now)
        except Exception as e:
            logger.warning("inbox: keeping heard-by verdicts failed: %s", e)
            errors.append(f"heard-by not kept for {len(to_keep)} send(s): "
                          f"{type(e).__name__}: {e}")
    contradicted = _declaration_contradictions(sends, decl)
    if contradicted:
        errors.append("declared on another preset/slot but decoded a send in its window "
                      "(the declaration is wrong, or the box changed segment since that send): "
                      f"{', '.join(contradicted)}")
    return "; ".join(errors) or None


def _declaration_contradictions(sends: List[Dict[str, Any]], decl) -> List[str]:
    """r3 MED (11-16): a bridged copy carries a NEW packet id, so a box declared
    on another segment that decoded THIS id in its join window is measured
    proof that the declaration is wrong (e.g. a send_from copied from another
    center). The hearing already wins for that box on that send; this SAYS so
    once per read. A skewed hearing is excluded: it may be an unrelated packet
    that reused the id."""
    send_seg = decl.send_segment
    if not send_seg:
        return []
    out = set()
    for m in sends:
        for b in (m.get("heard") or {}).get("boxes") or []:
            seg = decl.segments.get(b.get("alias"))
            # r2 L-A: a KEPT hearing is history — the box may have moved since,
            # correctly re-declared; only a live hearing contradicts today
            if (b.get("state") == "heard" and not b.get("skewed") and not b.get("kept")
                    and seg and seg != send_seg):
                out.add(b["alias"])
    return sorted(out)


def _unseen(rx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The downlink gap (11-17): texts the radio heard that never reached the
    MQTT uplink this page reads (``inbox_unseen``), with the page's ``line``
    and ``tone`` built here (tested here; r1: the page's tri-state was
    unpinned). None where this box does not read MQTT — there an unpublished
    text still reaches the page, so the count would mean nothing. Guarded: a
    witness never takes the read down — it says it could not look."""
    if not str((rx or {}).get("subscription") or "").startswith("mqtt "):
        return None
    try:
        from utils import inbox_unseen
        u = inbox_unseen.read_unseen()
        u["line"], u["tone"] = inbox_unseen.describe(u)
        return u
    except Exception as e:
        logger.warning("inbox: unseen witness raised: %s", e)
        return {"state": "unobservable", "error": f"{type(e).__name__}: {e}", "tone": "warn",
                "line": f"Radio journal: unobservable ({type(e).__name__}) — this page cannot "
                        "count texts the radio heard that are not on this page."}


def _rx_coverage() -> Dict[str, Any]:
    """What the receive path can actually hear (2026-10-13): an empty inbox
    beside a radio that journals texts is a blind listener, not a quiet mesh.
    The page names heard vs not-heard channels; unknown is said, not assumed."""
    try:
        from utils.message_listener import get_listener_status
        st = get_listener_status()
        return {k: st.get(k) for k in ("state", "messages_received", "channels_seen",
                                       "subscription", "connected_since", "error")}
    except Exception as e:
        logger.debug("inbox: listener status unreadable: %s", e)
        return {"state": "unknown"}


def validate_send(data: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return (params, None) for a sendable request, or (None, reason)."""
    if not isinstance(data, dict):
        return None, "body must be a JSON object"
    network = data.get("network")
    if network == "rns":
        return None, RNS_SEND_BLOCKED
    if network != "meshtastic":
        return None, "network must be 'meshtastic'"
    text = data.get("text")
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        return None, f"text is required (1-{MAX_TEXT_BYTES} bytes)"
    channel = data.get("channel", 0)
    if isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel <= MAX_CHANNEL:
        return None, f"channel must be an integer 0-{MAX_CHANNEL}"
    destination = data.get("destination", "^all")
    if destination in BROADCAST_IDS:
        return {"text": text, "channel": channel, "dest_num": None, "to_id": None}, None
    if not isinstance(destination, str) or not _NODE_ID.match(destination):
        return None, "destination must be '^all' or a node id like !a1b2c3d4"
    return {"text": text, "channel": channel,
            "dest_num": int(destination[1:], 16), "to_id": destination.lower()}, None


def send(params: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """Transmit a validated Meshtastic message and record it. Returns (HTTP status, body)."""
    from gateway.meshtastic_protobuf_client import send_text_direct_with_id
    try:
        packet_id = send_text_direct_with_id(
            text=params["text"], destination=params["dest_num"],
            channel_index=params["channel"],
            want_ack=params["dest_num"] is not None)
    except Exception as e:
        logger.warning("inbox: transmit raised: %s", e)
        packet_id = None
    # only a real radio id is a hand-off: 0 / False / out-of-range are not
    # (INBOX-T F3) — the same validator the store applies
    packet_id = messaging.valid_packet_id(packet_id)
    handed_off = packet_id is not None
    rec = messaging.store_outgoing(
        content=params["text"], network="meshtastic", to_id=params["to_id"],
        channel=params["channel"], delivered=handed_off, packet_id=packet_id)
    body = {
        "state": "handed_off" if handed_off else "not_sent",
        "detail": ("Handed to the radio. Delivery is unconfirmed." if handed_off
                   else "The radio did not accept the message (is meshtasticd running?)."),
        "recorded": bool(rec.success),
    }
    if not rec.success:
        body["record_error"] = rec.message
    return (200 if handed_off else 502), body
