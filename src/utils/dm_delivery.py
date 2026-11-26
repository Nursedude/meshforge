"""Classify Meshtastic DM delivery from a sender's own API session.

The rule, measured live on 2026-10-03 (one fleet box, 10 want_ack DMs) and read
in firmware v2.7.26:

* DELIVERED only when a positive ROUTING_APP ack carrying the DM's packet id
  comes FROM the DM's destination. Our own radio emits a positive ack too —
  one arrived ~20 ms after EVERY send, before any relay could repeat it — and
  again on overhearing a relay (ReliableRouter.cpp:54) or an MQTT echo
  (MQTT.cpp:97). Those prove the DM LEFT, not that it ARRIVED.
* FAILED on any NAK: only our radio (MAX_RETRANSMIT) or the destination ever
  sends one (NextHopRouter.cpp:281-284, isFromUs guard).
* LEFT_ONLY when the only positive acks came from someone else.
* NO_WITNESS when nothing came back in the listening window — unknown, never
  counted as a failure reason of its own.

The journal cannot be the witness (every ack line is LOG_DEBUG; the fleet
logs at info) and meshtasticd's JSONFile cannot either (its serializer
writes no request_id for routing packets). The sender's own API session
sees request_id + sender for each ack, so it is the witness.

Pure functions; no I/O. The sending half is scripts/dm_delivery_check.py.

The /inbox row rule (BIC-2 DM slice 1) is ``row_verdict`` below, built ON
``classify`` with ``give_up_fails=False`` and ``require_origin=True``: its
source is the per-box ``/e/`` MQTT ack witness, not a radio API session, so
a retry give-up is a hint, and a destination ack is delivery only once this
box can rule out a DM to its own radio -- from its own node id (an existing
connection, ``utils.own_radio``) or the DM's own uplinked header.
"""

import time
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

from gateway.ack_tracker import _node_num

DELIVERED = "DELIVERED"
FAILED = "FAILED"
LEFT_ONLY = "LEFT_ONLY"
NO_WITNESS = "NO_WITNESS"

# Below this many sends a rate is not printed: "too small to judge".
MIN_N_FOR_RATE = 10


@dataclass(frozen=True)
class Send:
    t: float            # wall-clock send time (s)
    packet_id: int
    dest: int           # destination node number
    #: the DM's OWN ``from``, read from its uplinked header (None = not seen).
    #: dest == sender is a DM to this box's own radio (r3 #1: decided from
    #: the DM, never from an ack's gateway).
    sender: Optional[int] = None
    #: this box's own node id from an EXISTING connection (None = unknown).
    #: When known it decides self-DM and outranks ``sender`` (r4 #1).
    own: Optional[int] = None


@dataclass(frozen=True)
class RoutingReply:
    t: float            # wall-clock arrival time (s)
    sender: int         # the reply packet's `from`
    request_id: int     # the DM packet id it answers
    error: str          # "NONE" for a positive ack, else the NAK reason


@dataclass(frozen=True)
class Outcome:
    send: Send
    status: str
    latency_s: Optional[float] = None   # to the destination's ack, if DELIVERED
    reason: str = ""                    # NAK reason, if FAILED
    other_acks: int = 0                 # positive acks not from the destination
    nak_sender: Optional[int] = None    # who said the NAK, if FAILED
    gave_up: str = ""                   # retry give-up seen (give_up_fails=False)
    early: int = 0                      # same id, stamped just BEFORE the send
    own_dest_acks: int = 0              # acks on a DM to our own radio (not delivery)
    origin_unknown_acks: int = 0        # dest acks, self-DM not ruled out (require_origin)
    long_after: int = 0                 # same id, stamped after REPLY_MAX_S


@dataclass
class Summary:
    sent: int = 0
    counts: dict = field(default_factory=dict)
    rate: Optional[float] = None        # delivered / sent, None if too small
    median_latency_s: Optional[float] = None


def _is_positive(error) -> bool:
    return error in (None, "", "NONE")


#: Routing.Error NAMES meaning the radio stopped retrying (TIMEOUT,
#: MAX_RETRANSMIT). The one list both sources' give-ups are judged by.
GIVE_UP_NAMES = ("TIMEOUT", "MAX_RETRANSMIT")
#: A same-id reply this far BEFORE the send is a clock step, not a reused id.
EARLY_S = 120
#: A reply may precede the stored send stamp by this much: the row is stamped
#: AFTER the HTTP send returns, in whole SQLite seconds (r3 #10: one constant).
REPLY_SLOP_S = 2
#: A reply stamped later than this after the send is not this send's answer
#: (r4 #7, #10: a forward clock step, or a 32-bit id reused). BELIEVED: the
#: measured acks took <= 24 s and retries span ~17 s; 10 min is headroom.
REPLY_MAX_S = 600


def classify(sends: Iterable[Send], replies: Iterable[RoutingReply], *,
             give_up_fails: bool = True, require_origin: bool = False) -> List[Outcome]:
    """One Outcome per send -- THE source-authority rule (one classifier).

    ``give_up_fails``: the 10-03 drill reads the radio's own API session,
    where its give-up IS its answer (default True). An /inbox row reads
    the uplinked ``/e/``, where a give-up proves only that a radio heard no
    ack -- rows pass False and get ``gave_up`` instead (§4b, r1 F2, r2 #3).
    A DM to our own radio (``s.sender == s.dest``, from the DM's own
    header) is never delivered (r2 #7, r3 #1). The destination's ack
    outranks any NAK; the destination's NAK is preferred (r2 #5, r3 #8).

    ``require_origin`` (rows, r4 #1): the destination's answer counts as the
    destination's only once self-DM is ruled out -- by ``s.own`` (this box's
    id, which outranks a header) or ``s.sender``. With neither known its acks
    are ``origin_unknown_acks`` and its NAK is said by its id, not "the
    destination". The drill (its own API session) keeps the plain rule."""
    replies = list(replies)
    out = []
    for s in sends:
        dest = _node_num(s.dest)
        mine = [r for r in replies if r.request_id == s.packet_id
                and s.t - REPLY_SLOP_S <= r.t <= s.t + REPLY_MAX_S]
        early = sum(1 for r in replies if r.request_id == s.packet_id
                    and s.t - EARLY_S <= r.t < s.t - REPLY_SLOP_S)
        long_after = sum(1 for r in replies if r.request_id == s.packet_id
                         and r.t > s.t + REPLY_MAX_S)
        origin = s.own if s.own is not None else s.sender
        self_dm = dest is not None and origin is not None and _node_num(origin) == dest
        unproven = require_origin and origin is None
        by_dest = [dest is not None and not self_dm and _node_num(r.sender) == dest
                   for r in mine]
        is_dest = [d and not unproven for d in by_dest]
        origin_unknown = sum(1 for r, d in zip(mine, by_dest)
                             if d and unproven and _is_positive(r.error))
        from_dest = sorted(r.t for r, d in zip(mine, is_dest) if d and _is_positive(r.error))
        own_dest = sum(1 for r in mine if self_dm and _is_positive(r.error)
                       and _node_num(r.sender) == dest)
        give_ups = [r for r in mine if not _is_positive(r.error)
                    and str(r.error) in GIVE_UP_NAMES]
        naks = [(d, r) for r, d in zip(mine, is_dest) if not _is_positive(r.error)
                and (give_up_fails or str(r.error) not in GIVE_UP_NAMES)]
        others = sum(1 for r, d in zip(mine, is_dest) if _is_positive(r.error) and not d)
        gave = "" if give_up_fails or not give_ups else str(give_ups[0].error)
        extra = dict(gave_up=gave, early=early, own_dest_acks=own_dest,
                     origin_unknown_acks=origin_unknown, long_after=long_after)
        if from_dest:
            out.append(Outcome(s, DELIVERED, latency_s=from_dest[0] - s.t,
                               other_acks=others, **extra))
        elif naks:
            d, nak = sorted(naks, key=lambda dn: (not dn[0], dn[1].t))[0]
            out.append(Outcome(s, FAILED, reason=str(nak.error), other_acks=others,
                               nak_sender=_node_num(nak.sender), **extra))
        elif others:
            out.append(Outcome(s, LEFT_ONLY, other_acks=others, **extra))
        else:
            out.append(Outcome(s, NO_WITNESS, **extra))
    return out


def summarize(outcomes: Iterable[Outcome], min_n: int = MIN_N_FOR_RATE) -> Summary:
    """Counts per status; a delivery rate only once `min_n` DMs were sent."""
    outcomes = list(outcomes)
    s = Summary(sent=len(outcomes))
    for st in (DELIVERED, FAILED, LEFT_ONLY, NO_WITNESS):
        s.counts[st] = sum(1 for o in outcomes if o.status == st)
    if s.sent >= max(1, min_n):
        s.rate = s.counts[DELIVERED] / s.sent
    lat = sorted(o.latency_s for o in outcomes if o.latency_s is not None)
    if lat:
        mid = len(lat) // 2
        s.median_latency_s = lat[mid] if len(lat) % 2 else (lat[mid - 1] + lat[mid]) / 2
    return s


# ---------------------------------------------------------------------------
# BIC-2 DM slice 1 (2026-11-25): one /inbox DM row -> exactly ONE of five
# states. Design: .claude/plans/bic2_dm_delivery_design_2026_11_25.md §1 + §4b
# (§4b wins). Built ON ``classify`` above -- one classifier, not two (Q2).
# ---------------------------------------------------------------------------

ROW_DELIVERED = "delivered"
ROW_FAILED = "failed"
ROW_LEFT_UNCONFIRMED = "left_unconfirmed"
ROW_WAITING = "waiting"
ROW_NO_ACK = "no_ack_seen"
#: Closed enum (hfm #7): every consumer of a row state switches on this.
ROW_STATES = (ROW_DELIVERED, ROW_FAILED, ROW_LEFT_UNCONFIRMED, ROW_WAITING,
              ROW_NO_ACK)

#: BELIEVED, not measured as a bound (§4b Q2'): n=6 destination acks, max
#: 24 s; our radio's retries span ~17 s; ONE sender box. A match after it
#: still flips the row and is marked ``late`` -- the caller counts those as
#: the witness that this is too short. Re-derive after a soak.
WAITING_S = 60
#: hfm #6: a send stamped further ahead of now than this is a clock step,
#: never "waiting" (which would then last until the clock caught up).
CLOCK_SLOP_S = 60
#: How long the witness's reply ring keeps an answer (dm_ack_witness reads
#: this): a send older than it, never judged while its answer was held,
#: says so (r4 #3).
ACK_RING_S = 24 * 3600


@dataclass(frozen=True)
class Witness:
    """The /e/ ack witness's posture on THIS box. ``since`` = when it began
    listening (None = not listening); ``why`` = why not, when it is not;
    ``lost_until`` = the newest reply the bounded ring has DROPPED (r4 #3:
    an answer for a send before it may be gone -- said, never "closed")."""
    since: Optional[float]
    why: str = ""
    lost_until: Optional[float] = None


@dataclass(frozen=True)
class RowVerdict:
    state: str
    detail: str
    reason: str = ""          # failed: the error name
    said_by: str = ""         # failed: "the destination" / "our radio" / !id
    why: str = ""             # no_ack_seen: always set
    latency_s: Optional[float] = None
    late: bool = False        # delivered after WAITING_S
    sent_n: int = 0           # copies of the DM seen on /e/ (retransmissions)
    hints: tuple = ()         # evidence that never settles a row (see seam)
    #: ``detail`` without its live tail (hints, "sent N×") -- what the store
    #: keeps; the tail is rebuilt from the live witness on every read (r4 #5)
    base: str = ""
    tail: str = ""


def sent_suffix(n: int) -> str:
    """The live "(sent N×)" tail -- one wording for row and kept display."""
    return f" (sent {n}×)" if n > 1 else ""


_sent = sent_suffix


def row_verdict(send: Send, replies: Iterable[RoutingReply], witness: Witness,
                tx_seen: int = 0, now: Optional[float] = None) -> RowVerdict:
    """The five-state verdict for one DM row (design §1 + §4b), on THE
    source-authority rule (``classify(give_up_fails=False)``, pinned by
    ``tests/test_dm_authority.py``).

    The ``/e/`` ring is the ONE source (operator 11-25, after Fable r3: the
    journal hints were cut from slice 1). Delivered = the destination's ack
    and outranks everything; failed = a NAK. Coverage only picks the why;
    the ring decides whenever it holds an answer (r1 F3).
    """
    now = time.time() if now is None else now
    dest = _node_num(send.dest)
    sent_n = max(0, int(tx_seen))
    (o,) = classify([send], replies, give_up_fails=False, require_origin=True)

    if o.status == DELIVERED:
        late = o.latency_s is not None and o.latency_s > WAITING_S
        detail = (f"delivered — acked by the destination in {o.latency_s:.1f} s"
                  + (" (after the waiting window)" if late else ""))
        return RowVerdict(ROW_DELIVERED, detail, latency_s=o.latency_s, late=late,
                          sent_n=sent_n, base=detail)

    # Hints: /e/ facts that never settle the row, shown beside its state.
    hints = []
    if o.gave_up:
        hints.append(f"a radio gave up after retries ({o.gave_up})")
    if o.own_dest_acks:
        hints.append("DM to this box's own radio — its own ack is not delivery")
    if o.early:
        hints.append("an ack for this id is stamped before the send (clock step?)")
    if o.long_after:
        hints.append("an ack for this id arrived long after the send "
                     "(clock step or reused id?) — not counted")
    if o.origin_unknown_acks:
        hints.append("the destination acked, but this box has not seen the DM's own "
                     "header and does not know its own radio id — a DM to itself "
                     "is not ruled out")
    tail = "".join(f" · {h}" for h in hints) + _sent(sent_n)
    H = tuple(hints)

    def v(state, base, **kw):
        return RowVerdict(state, base + tail, sent_n=sent_n, hints=H, base=base,
                          tail=tail, **kw)

    if o.status == FAILED:
        # "the destination" only once self-DM is ruled out (r4 #1): with no
        # origin known -- or a DM to ourselves -- the NAK is said by its id
        origin = send.own if send.own is not None else send.sender
        proven = origin is not None and _node_num(origin) != dest
        who = ("the destination" if proven and o.nak_sender == dest
               else f"!{o.nak_sender or 0:08x}")
        return v(ROW_FAILED, f"failed: {o.reason} (said by {who})",
                 reason=o.reason, said_by=who)

    if witness.since is None:
        why = witness.why or "this box cannot see delivery acks"
    elif witness.since > send.t:
        why = "witness started after this send"
    elif now - send.t < -CLOCK_SLOP_S:
        why = "this box's clock went backward since the send (it is stamped in the future)"
    elif now - send.t < WAITING_S:
        return v(ROW_WAITING, f"waiting — {max(0, int(now - send.t))} s of {WAITING_S} s")
    elif o.status == LEFT_ONLY and o.origin_unknown_acks:
        return v(ROW_LEFT_UNCONFIRMED,
                 "left, not confirmed — acked by the destination's id, but this box "
                 "cannot yet rule out a DM to its own radio (own header not seen)")
    elif o.status == LEFT_ONLY:
        return v(ROW_LEFT_UNCONFIRMED,
                 "left, not confirmed — acked only by our radio or a relay, never "
                 "the destination")
    elif witness.lost_until is not None and witness.lost_until >= send.t - REPLY_SLOP_S:
        why = "the ack ring overflowed after this send (an answer may have been lost)"
    elif now - send.t > ACK_RING_S:
        why = (f"sent more than {ACK_RING_S // 3600} h ago — older than the ack ring, "
               "and never judged while an answer could be held")
    else:
        why = "window closed"
    return v(ROW_NO_ACK, f"no ack seen: {why}", why=why)


#: The states a row can settle in -- the only ones the store keeps.
ROW_FINAL_STATES = (ROW_DELIVERED, ROW_FAILED)
#: Store RANK only (never "final"): a failure the destination itself said
#: outranks anyone else's. Only ``delivered`` is final -- the destination's
#: later ack replaces even its own NAK (operator 11-25, Fable r3 #8).
TERMINAL_SAID_BY = ("the destination",)
