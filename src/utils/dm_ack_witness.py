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
decrypted. No own-node id is needed or inferred: our radio's own ack is
"someone else" because its ``from`` is not the DM's destination.

Thread-safe: the MQTT thread writes, the HTTP thread reads ``snapshot()``.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from typing import Any, Dict, List, Optional, Tuple

from utils.dm_delivery import RoutingReply, Witness
from utils.safe_import import safe_import

_mqtt_pb2, _HAS_MQTT_PB = safe_import("meshtastic.protobuf.mqtt_pb2")

BROADCAST = 0xFFFFFFFF
#: Ring bounds. ~13 log lines/min on a busy box; routing replies are a small
#: fraction. 24 h / 4000 is headroom, not a measurement.
RING_WINDOW_S = 24 * 3600
RING_CAP = 4000
TX_CAP = 4000
#: Counters, closed vocabulary (hfm #7) -- observe before alarm (Q8).
COUNTERS = ("envelopes", "broadcast", "pki_skipped", "unparseable",
            "decrypt_failed", "decrypted", "routing", "routing_unparseable",
            "other_gateway_copy")


class AckWitness:
    def __init__(self, *, now: Optional[float] = None) -> None:
        self._lock = threading.Lock()
        self._since: Optional[float] = None
        self._why = "the MQTT ack witness has not started"
        self._replies: deque = deque(maxlen=RING_CAP)
        self._tx: "OrderedDict[int, int]" = OrderedDict()
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
            # (gateway == from). A bridged/other gateway's copy is the same
            # transmission heard elsewhere -- r1 F6.
            if pid and gateway == f"!{frm:08x}":
                self._tx[pid] = self._tx.pop(pid, 0) + 1
                while len(self._tx) > TX_CAP:
                    self._tx.popitem(last=False)
            elif pid:
                self._counts["other_gateway_copy"] += 1
            if pki:
                self._counts["pki_skipped"] += 1
                return
        from utils.meshtastic_se_crypto import decode_service_envelope
        try:
            dp = decode_service_envelope(payload)
        except Exception:
            dp = None
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
        with self._lock:
            self._counts["routing"] += 1
            # via_own: the envelope's gateway IS the reply's sender -- our
            # radio uplinking its own ack (wire fact, r2 #7)
            self._replies.append(RoutingReply(t, int(dp.from_node), int(dp.request_id),
                                              err, via_own=gateway == f"!{int(dp.from_node):08x}"))

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
            return {"witness": Witness(self._since, self._why),
                    "replies": replies,
                    "tx": dict(self._tx),
                    "channels": dict(self._channels),
                    "decrypted": dict(self._decrypted),
                    "counts": dict(self._counts)}


_WITNESS: Optional[AckWitness] = None
_WITNESS_LOCK = threading.Lock()


def get_witness() -> AckWitness:
    """This process's one witness (the map). Created blind."""
    global _WITNESS
    with _WITNESS_LOCK:
        if _WITNESS is None:
            _WITNESS = AckWitness()
        return _WITNESS
