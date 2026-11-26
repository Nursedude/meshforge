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


def classify(sends: Iterable[Send], replies: Iterable[RoutingReply], *,
             give_up_fails: bool = True) -> List[Outcome]:
    """One Outcome per send -- THE source-authority rule (one classifier).

    ``give_up_fails``: the 10-03 drill reads the radio's own API session,
    where its give-up IS its answer (default True). An /inbox row reads
    the uplinked ``/e/``, where a give-up proves only that a radio heard no
    ack -- rows pass False and get ``gave_up`` instead (§4b, r1 F2, r2 #3).
    A DM to our own radio (``s.sender == s.dest``, from the DM's own
    header) is never delivered (r2 #7, r3 #1). The destination's ack
    outranks any NAK; the destination's NAK is preferred (r2 #5, r3 #8)."""
    replies = list(replies)
    out = []
    for s in sends:
        dest = _node_num(s.dest)
        mine = [r for r in replies if r.request_id == s.packet_id
                and r.t >= s.t - REPLY_SLOP_S]
        early = sum(1 for r in replies if r.request_id == s.packet_id
                    and s.t - EARLY_S <= r.t < s.t - REPLY_SLOP_S)
        self_dm = dest is not None and s.sender is not None and _node_num(s.sender) == dest
        is_dest = [dest is not None and not self_dm and _node_num(r.sender) == dest
                   for r in mine]
        from_dest = sorted(r.t for r, d in zip(mine, is_dest) if d and _is_positive(r.error))
        own_dest = sum(1 for r in mine if self_dm and _is_positive(r.error)
                       and _node_num(r.sender) == dest)
        give_ups = [r for r in mine if not _is_positive(r.error)
                    and str(r.error) in GIVE_UP_NAMES]
        naks = [(d, r) for r, d in zip(mine, is_dest) if not _is_positive(r.error)
                and (give_up_fails or str(r.error) not in GIVE_UP_NAMES)]
        others = sum(1 for r, d in zip(mine, is_dest) if _is_positive(r.error) and not d)
        gave = "" if give_up_fails or not give_ups else str(give_ups[0].error)
        extra = dict(gave_up=gave, early=early, own_dest_acks=own_dest)
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
@dataclass(frozen=True)
class Witness:
    """The /e/ ack witness's posture on THIS box. ``since`` = when it began
    listening (None = not listening); ``why`` = why not, when it is not."""
    since: Optional[float]
    why: str = ""


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


def _sent(n: int) -> str:
    return f" (sent {n}×)" if n > 1 else ""


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
    (o,) = classify([send], replies, give_up_fails=False)

    if o.status == DELIVERED:
        late = o.latency_s is not None and o.latency_s > WAITING_S
        return RowVerdict(
            ROW_DELIVERED,
            f"delivered — acked by the destination in {o.latency_s:.1f} s"
            + (" (after the waiting window)" if late else ""),
            latency_s=o.latency_s, late=late, sent_n=sent_n)

    # Hints: /e/ facts that never settle the row, shown beside its state.
    hints = []
    if o.gave_up:
        hints.append(f"a radio gave up after retries ({o.gave_up})")
    if o.own_dest_acks:
        hints.append("DM to this box's own radio — its own ack is not delivery")
    if o.early:
        hints.append("an ack for this id is stamped before the send (clock step?)")
    tail = "".join(f" · {h}" for h in hints) + _sent(sent_n)

    if o.status == FAILED:
        who = ("the destination" if o.nak_sender == dest
               else f"!{o.nak_sender or 0:08x}")
        return RowVerdict(ROW_FAILED, f"failed: {o.reason} (said by {who})" + tail,
                          reason=o.reason, said_by=who, sent_n=sent_n,
                          hints=tuple(hints))

    if witness.since is None:
        why = witness.why or "this box cannot see delivery acks"
    elif witness.since > send.t:
        why = "witness started after this send"
    elif now - send.t < -CLOCK_SLOP_S:
        why = "this box's clock went backward since the send (it is stamped in the future)"
    elif now - send.t < WAITING_S:
        return RowVerdict(ROW_WAITING,
                          f"waiting — {max(0, int(now - send.t))} s of {WAITING_S} s" + tail,
                          sent_n=sent_n, hints=tuple(hints))
    elif o.status == LEFT_ONLY:
        return RowVerdict(ROW_LEFT_UNCONFIRMED,
                          "left, not confirmed — acked only by our radio or a "
                          "relay, never the destination" + tail,
                          sent_n=sent_n, hints=tuple(hints))
    else:
        why = "window closed"
    return RowVerdict(ROW_NO_ACK, f"no ack seen: {why}" + tail, why=why,
                      sent_n=sent_n, hints=tuple(hints))


#: The states a row can settle in -- the only ones the store keeps.
ROW_FINAL_STATES = (ROW_DELIVERED, ROW_FAILED)
#: Store RANK only (never "final"): a failure the destination itself said
#: outranks anyone else's. Only ``delivered`` is final -- the destination's
#: later ack replaces even its own NAK (operator 11-25, Fable r3 #8).
TERMINAL_SAID_BY = ("the destination",)
