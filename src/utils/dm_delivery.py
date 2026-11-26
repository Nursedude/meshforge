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


@dataclass
class Summary:
    sent: int = 0
    counts: dict = field(default_factory=dict)
    rate: Optional[float] = None        # delivered / sent, None if too small
    median_latency_s: Optional[float] = None


def _is_positive(error) -> bool:
    return error in (None, "", "NONE")


def classify(sends: Iterable[Send], replies: Iterable[RoutingReply]) -> List[Outcome]:
    """One Outcome per send, by the rule in the module docstring."""
    replies = list(replies)
    out = []
    for s in sends:
        dest = _node_num(s.dest)
        mine = [r for r in replies if r.request_id == s.packet_id and r.t >= s.t - 1]
        from_dest = sorted(r.t for r in mine
                           if _is_positive(r.error) and dest is not None
                           and _node_num(r.sender) == dest)
        naks = [r for r in mine if not _is_positive(r.error)]
        others = sum(1 for r in mine
                     if _is_positive(r.error) and _node_num(r.sender) != dest)
        if from_dest:
            out.append(Outcome(s, DELIVERED, latency_s=from_dest[0] - s.t,
                               other_acks=others))
        elif naks:
            out.append(Outcome(s, FAILED, reason=str(naks[0].error),
                               other_acks=others))
        elif others:
            out.append(Outcome(s, LEFT_ONLY, other_acks=others))
        else:
            out.append(Outcome(s, NO_WITNESS))
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
#: Routing.Error codes that mean OUR radio stopped retrying -- never a
#: delivery failure (r1 F2): TIMEOUT, MAX_RETRANSMIT.
RETRY_GIVE_UP = (3, 5)
#: hfm #6: a send stamped further ahead of now than this is a clock step,
#: never "waiting" (which would then last until the clock caught up).
CLOCK_SLOP_S = 60

#: mesh.proto ``Routing.Error`` at firmware 54e0d8d (pinned by test).
_ROUTING_ERRORS = {
    0: "NONE", 1: "NO_ROUTE", 2: "GOT_NAK", 3: "TIMEOUT", 4: "NO_INTERFACE",
    5: "MAX_RETRANSMIT", 6: "NO_CHANNEL", 7: "TOO_LARGE", 8: "NO_RESPONSE",
    9: "DUTY_CYCLE_LIMIT", 32: "BAD_REQUEST", 33: "NOT_AUTHORIZED",
    34: "PKI_FAILED", 35: "PKI_UNKNOWN_PUBKEY", 36: "ADMIN_BAD_SESSION_KEY",
    37: "ADMIN_PUBLIC_KEY_UNAUTHORIZED", 38: "RATE_LIMIT_EXCEEDED",
    39: "PKI_SEND_FAIL_PUBLIC_KEY",
}


def routing_error_name(code: int) -> str:
    """The firmware's name for a Routing.Error code; an unknown code is
    named by its number, never dropped."""
    return _ROUTING_ERRORS.get(code, f"err={code}")


@dataclass(frozen=True)
class Witness:
    """The /e/ ack witness's posture on THIS box. ``since`` = when it began
    listening (None = not listening); ``why`` = why not, when it is not."""
    since: Optional[float]
    why: str = ""


# One definition with its parser (hfm #5): ``box_truth_rx.parse_dm_journal``
# yields these; D-1 allowlist, ids only.
from utils.box_truth_rx import JournalLine  # noqa: E402


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
    possible_ack: bool = False


def _sent(n: int) -> str:
    return f" (sent {n}×)" if n > 1 else ""


def row_verdict(send: Send, replies: Iterable[RoutingReply], witness: Witness,
                journal: Iterable[JournalLine] = (), tx_seen: int = 0,
                now: Optional[float] = None) -> RowVerdict:
    """The five-state verdict for one DM row (design §1 + §4b).

    Order: a final answer (delivered / failed) wins whenever it exists, even
    late; else inside the window = waiting; else left / no-ack with its why.
    Never "delivered" from anyone but the destination; never "failed" from a
    retry give-up (invisible on this fleet, §4b).
    """
    now = time.time() if now is None else now
    journal = list(journal)
    dest = _node_num(send.dest)
    sent_n = max(0, int(tx_seen))

    # The ring decides whenever it holds an answer -- whatever the witness's
    # coverage now (r1 F3: a broker reconnect moved ``since`` past the send
    # and threw away the destination's ack already in the ring).
    (o,) = classify([send], replies)
    if o.status == DELIVERED:
        # The destination's own ack outranks ANY journal error (r1 F2).
        late = o.latency_s is not None and o.latency_s > WAITING_S
        return RowVerdict(
            ROW_DELIVERED,
            f"delivered — acked by the destination in {o.latency_s:.1f} s"
            + (" (after the waiting window)" if late else ""),
            latency_s=o.latency_s, late=late, sent_n=sent_n)

    # Our radio's own refusal: journal-only, exact packet id (Q1'). A retry
    # GIVE-UP is not a refusal: it proves only that OUR radio heard no ack
    # (§4b: "failed" never comes from a give-up) -- r1 F2.
    mine = [j for j in journal if j.kind == "alloc_err" and j.packet_id == send.packet_id
            and j.err]
    for j in mine:
        if j.err not in RETRY_GIVE_UP:
            name = routing_error_name(j.err)
            return RowVerdict(ROW_FAILED, f"failed: {name} (said by our radio)",
                              reason=name, said_by="our radio", sent_n=sent_n)
    gave_up = next((routing_error_name(j.err) for j in mine), None)

    if o.status == FAILED:
        nak = next(r for r in replies if r.request_id == send.packet_id
                   and not _is_positive(r.error) and r.t >= send.t - 1)
        who = ("the destination" if _node_num(nak.sender) == dest
               else f"!{_node_num(nak.sender) or 0:08x}")
        return RowVerdict(ROW_FAILED, f"failed: {o.reason} (said by {who})",
                          reason=o.reason, said_by=who, sent_n=sent_n)

    if witness.since is None:
        why = witness.why or "this box cannot see delivery acks"
    elif witness.since > send.t:
        why = "witness started after this send"
    elif now - send.t < -CLOCK_SLOP_S:
        why = "this box's clock went backward since the send (it is stamped in the future)"
    elif now - send.t < WAITING_S:
        return RowVerdict(ROW_WAITING,
                          f"waiting — {max(0, int(now - send.t))} s of {WAITING_S} s"
                          + _sent(sent_n), sent_n=sent_n)
    elif o.status == LEFT_ONLY:
        return RowVerdict(ROW_LEFT_UNCONFIRMED,
                          "left, not confirmed — acked only by our radio or a "
                          "relay, never the destination" + _sent(sent_n),
                          sent_n=sent_n)
    else:
        why = "window closed"
    if gave_up:
        why = f"our radio gave up after retries ({gave_up}); " + why

    possible = dest is not None and any(
        j.kind == "routing_from" and j.sender is not None
        and _node_num(j.sender) == dest and send.t <= j.t <= send.t + WAITING_S
        for j in journal)
    if possible:
        why = ("possible ack, not matched — routing from the destination is in "
               "this box's journal but not on its broker (this box may not "
               "uplink the channel the ack came on); " + why)
    return RowVerdict(ROW_NO_ACK, f"no ack seen: {why}" + _sent(sent_n),
                      why=why, sent_n=sent_n, possible_ack=possible)

#: The states a row can settle in -- the only ones the store keeps.
ROW_FINAL_STATES = (ROW_DELIVERED, ROW_FAILED)
#: A ``failed`` from these is the end; a relay's NAK can still be overtaken
#: by the destination's own ack (the destination outranks a relay).
TERMINAL_SAID_BY = ("the destination", "our radio")
