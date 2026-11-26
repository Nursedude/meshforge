"""BIC-2 DM slice 1, piece 3: the per-box ``/e/`` ack witness (fork A).

Design: ``.claude/plans/bic2_dm_delivery_design_2026_11_25.md`` Q1/Q5/Q7/Q8.

The map's local MQTT subscriber hands EVERY ``msh/2/e/<channel>/<gw>``
envelope here. The witness keeps, in a bounded in-memory ring, ids only:

* each ROUTING_APP reply (``from``, ``request_id``, error name) that
  decrypts with the DEFAULT key -- acks ride the channel the destination
  answers on, so every channel counts (measured 11-25: LongFast and the regional primary);
* how many copies of each NON-broadcast packet id rode ``/e/`` (header only,
  plaintext) -- our radio's retransmissions of a DM = "sent N×";
* which channel names it has seen, and when (Q19: the witness line names
  what it covers from the broker, never from the radio).

Never text, never a position, never a key. PKI envelopes are skipped and
COUNTED, never an error (Q5). A broadcast envelope is counted, never
decrypted. No own-node id is inferred here: a DM to our own radio is
judged from the DM's own uplinked header (``tx_from``) or from the id an
existing connection read (``utils.own_radio``) -- never from an ack (r4 #1).

Thread-safe: the MQTT thread writes, the HTTP thread reads ``snapshot()``.
"""
from __future__ import annotations

import re
import threading
import time
from collections import Counter, OrderedDict, deque
from typing import Any, Dict, List, Optional, Tuple

from utils.dm_delivery import ACK_RING_S, RoutingReply, Witness
from utils.safe_import import safe_import

_mqtt_pb2, _HAS_MQTT_PB = safe_import("meshtastic.protobuf.mqtt_pb2")

BROADCAST = 0xFFFFFFFF
#: Ring bounds. ~13 log lines/min on a busy box; routing replies are a small
#: fraction. 24 h / 4000 is headroom, not a measurement.
RING_WINDOW_S = ACK_RING_S
RING_CAP = 4000
TX_CAP = 4000
#: Counters, closed vocabulary (hfm #7) -- observe before alarm (Q8).
COUNTERS = ("envelopes", "broadcast", "pki_skipped", "unparseable",
            "decrypt_failed", "decrypted", "routing", "routing_unparseable",
            "other_gateway_copy", "tx_from_conflict", "reply_evicted", "decode_error")


class AckWitness:
    def __init__(self, *, now: Optional[float] = None) -> None:
        self._lock = threading.Lock()
        self._since: Optional[float] = None
        self._why = "the MQTT ack witness has not started"
        self._replies: deque = deque()          # bounded by hand: evictions are counted
        #: request ids in the ring -> count: a header guarding a reply is kept
        self._reply_ids: Counter = Counter()
        #: newest reply the ring DROPPED for room (r4 #3), None = none lost
        self._lost_until: Optional[float] = None
        self._tx: "OrderedDict[int, int]" = OrderedDict()
        #: packet id -> its OWN ``from``, from the sender's own uplink (r3 #1:
        #: a DM to our own radio is decided from the DM, never from an ack).
        #: First writer wins (r4 #1: a same-id envelope never overwrites it),
        #: and it outlives ``_tx`` while a reply for it is in the ring.
        self._tx_from: Dict[int, int] = {}
        #: ids whose header came only from another gateway's copy: bounds
        #: ``_tx_from`` the way ``_tx`` bounds own uplinks (r5 #9)
        self._tx_order: deque = deque()
        self._tx_order_ids: set = set()
        self._channels: Dict[str, float] = {}
        #: channels on which the DEFAULT key opened something (r2 #4: "seen"
        #: is presence, not function -- only these can carry a visible ack)
        self._decrypted: Dict[str, float] = {}
        self._counts = {k: 0 for k in COUNTERS}

    # -- lifecycle (called by the subscriber) --------------------------------

    def started(self, now: Optional[float] = None) -> None:
        """The subscription is live. Resets nothing already seen."""
        with self._lock:
            if self._since is None:
                self._since = time.time() if now is None else now
            self._why = ""

    def stopped(self, why: str) -> None:
        """The subscription dropped: the witness is blind from now on. Its
        coverage restarts at the next ``started`` (a gap is never vouched)."""
        with self._lock:
            self._since = None
            self._why = why or "the MQTT ack witness stopped"

    # -- the MQTT thread -----------------------------------------------------

    def observe(self, topic: str, payload: bytes, *,
                now: Optional[float] = None) -> None:
        """One ``/e/`` envelope. Never raises."""
        t = time.time() if now is None else now
        with self._lock:
            self._counts["envelopes"] += 1
        if not _HAS_MQTT_PB:
            self._bump("unparseable")
            return
        try:
            se = _mqtt_pb2.ServiceEnvelope()
            se.ParseFromString(payload)
            pkt = se.packet
            to, pid, frm = int(pkt.to), int(pkt.id), int(getattr(pkt, "from"))
            channel = str(se.channel_id)[:32]
            pki = bool(getattr(pkt, "pki_encrypted", False))
            gateway = str(se.gateway_id)
        except Exception:
            self._bump("unparseable")
            return
        with self._lock:
            if channel:
                self._channels[channel] = t
            if to == BROADCAST:
                self._counts["broadcast"] += 1
                return
            # "sent N×" counts only the SENDER's own uplink of its packet
            # (gateway == from, compared as NUMBERS -- r4 #8). A bridged/other
            # gateway's copy is the same transmission heard elsewhere -- r1 F6.
            own_uplink = bool(pid) and _gateway_num(gateway) == frm
            if pid:
                # The DM's own ``from`` rides every copy in plaintext, so ANY
                # gateway's copy is its header (r5 #9); first writer wins.
                first = self._tx_from.setdefault(pid, frm)
                if first != frm:
                    self._counts["tx_from_conflict"] += 1
            if own_uplink:
                self._tx[pid] = self._tx.pop(pid, 0) + 1
                while len(self._tx) > TX_CAP:
                    old, _ = self._tx.popitem(last=False)
                    # r7 #7: kept while its id is still tracked anywhere
                    if not self._reply_ids.get(old) and old not in self._tx_order_ids:
                        self._tx_from.pop(old, None)
            elif pid:
                self._counts["other_gateway_copy"] += 1
                # one slot per id, not per copy (r6 #7)
                if pid not in self._tx and pid not in self._tx_order_ids:
                    self._tx_order.append(pid)
                    self._tx_order_ids.add(pid)
                    while len(self._tx_order) > TX_CAP:
                        old = self._tx_order.popleft()
                        self._tx_order_ids.discard(old)
                        if old not in self._tx and not self._reply_ids.get(old):
                            self._tx_from.pop(old, None)
            if pki:
                self._counts["pki_skipped"] += 1
                return
        try:
            from utils.meshtastic_se_crypto import decode_service_envelope
            dp = decode_service_envelope(payload)
        except Exception:
            # a library fault, never "key not held" (r4 #4)
            self._bump("decode_error")
            return
        if dp is None:
            self._bump("decrypt_failed")
            return
        with self._lock:
            self._counts["decrypted"] += 1
            if channel:
                self._decrypted[channel] = t
        if not dp.is_routing or not dp.request_id:
            return
        err = dp.routing_error_name()
        if err is None:
            # Never a reply: "UNPARSEABLE" read as a NAK -> a kept "failed"
            # from a degraded parse (hfm #1, r1 F4). Counted, so a probe sees it.
            self._bump("routing_unparseable")
            return
        rid = int(dp.request_id)
        with self._lock:
            self._counts["routing"] += 1
            if len(self._replies) >= RING_CAP:
                old = self._replies.popleft()
                # r5 #6: a reply already past the 24 h window was not LOST;
                # r5 #5: a future-stamped one never moves the horizon past now
                if old.t >= t - RING_WINDOW_S:
                    self._counts["reply_evicted"] += 1
                    lost = min(old.t, t)
                    self._lost_until = lost if self._lost_until is None else max(
                        self._lost_until, lost)
                self._reply_ids[old.request_id] -= 1
                if self._reply_ids[old.request_id] <= 0:
                    del self._reply_ids[old.request_id]
                    # the reply being added may guard the same header
                    # r6 #8: kept while its id is still tracked anywhere
                    if (old.request_id not in self._tx and old.request_id != rid
                            and old.request_id not in self._tx_order_ids):
                        self._tx_from.pop(old.request_id, None)
            self._replies.append(RoutingReply(t, int(dp.from_node), rid, err))
            self._reply_ids[rid] += 1

    def _bump(self, k: str) -> None:
        with self._lock:
            self._counts[k] += 1

    # -- the HTTP thread -----------------------------------------------------

    def snapshot(self, *, now: Optional[float] = None) -> Dict[str, Any]:
        """Everything a reader needs, copied under the lock."""
        t = time.time() if now is None else now
        with self._lock:
            floor = t - RING_WINDOW_S
            replies = [r for r in self._replies if r.t >= floor]
            if not _HAS_MQTT_PB:
                # r4 #4: every envelope would be "unparseable" -- blind, said
                w = Witness(None, "this box cannot decode MQTT envelopes "
                                  "(meshtastic protobuf not installed)")
            elif not _crypto_ok():
                # r5 #4: no decryption = no ack can ever be seen -- blind, said
                w = Witness(None, "this box cannot decrypt MQTT envelopes "
                                  "(cryptography or meshtastic protobufs not installed)")
            else:
                w = Witness(self._since, self._why, self._lost_until)
            return {"witness": w,
                    "replies": replies,
                    "tx": dict(self._tx),
                    "tx_from": dict(self._tx_from),
                    "channels": dict(self._channels),
                    "decrypted": dict(self._decrypted),
                    "counts": dict(self._counts)}


_GATEWAY_ID = re.compile(r"![0-9a-fA-F]{8}")


def _gateway_num(gateway: str) -> Optional[int]:
    """``!6201ce25`` (any case) -> its number; anything else -> None
    (r5 #8: strict -- ``int(x, 16)`` alone took ``!+201ce25``)."""
    return int(gateway[1:], 16) if _GATEWAY_ID.fullmatch(gateway) else None


def _crypto_ok() -> bool:
    try:
        from utils.meshtastic_se_crypto import crypto_available
        return bool(crypto_available())
    except Exception:
        return False


_WITNESS: Optional[AckWitness] = None
_WITNESS_LOCK = threading.Lock()


def get_witness() -> AckWitness:
    """This process's one witness (the map). Created blind."""
    global _WITNESS
    with _WITNESS_LOCK:
        if _WITNESS is None:
            _WITNESS = AckWitness()
        return _WITNESS
