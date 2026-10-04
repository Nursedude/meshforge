"""In-flight ACK correlation for gateway→Meshtastic sends (Thread-2 step 4).

When the gateway sends a directed downlink (a DM to a specific mesh node)
with ``wantAck=True``, the recipient's firmware returns a ``ROUTING_APP``
packet whose ``request_id`` equals the sent packet's ``id``. This module
is the in-process bridge between those two events:

  * ``register(packet_id -> msg_id)`` at send time, and
  * ``resolve(request_id)`` when the ACK arrives,

so ``delivery_counters`` can record the honest ``CONFIRMED`` (or
``DROPPED`` + the real NAK reason) terminal state. Before this, the
gateway recorded ``SENT`` for Meshtastic and never anything more — so
Meshtastic was structurally *unconfirmable* and the #74
``delivery_confirmation_stall`` probe excluded it. With CONFIRMED events
flowing, Meshtastic joins the confirmable set: honest end-to-end proof
instead of the "Sent (not guaranteed)" ceiling (#16).

Works in **both** bridge modes. TCP mode: the ACK is observed via the
persistent ``meshtastic.receive`` stream in ``MeshtasticHandler`` (which
carries *every* packet, including ``ROUTING_APP``). ``mqtt_bridge`` mode
(the fleet default): ``MQTTBridgeHandler`` decodes the ACK from the
encrypted ``/e/`` ServiceEnvelope MQTT topic (``utils.meshtastic_se_crypto``)
— still within MQTT, **no** fromradio read, so the #17/#75 zero-interference
invariant holds. This ``AckTracker`` is the shared in-flight map both wiring
paths feed; only the *ingestion source* of the ACK differs.

Meshtastic only ACKs DMs — a broadcast gets an *implicit* overhear, not a
``ROUTING_APP`` packet — so this tracker is populated for DM downlinks
only. It is deliberately **in-memory** (no DB): an ACK arrives within
seconds to a couple of minutes, well inside one gateway lifetime, and a
pending entry lost to a restart simply stays ``SENT`` (honest — the proof
was never seen). Bounded + monotonic-TTL'd so a never-acked DM can't leak
(the #74 monotonic-clock lesson: wall-clock NTP backsteps must not freeze
the expiry timer). No sweeper thread (MF010): TTL is enforced lazily on
``register``/``resolve`` and via ``expire_idle()`` from the bridge sweep.

A positive ACK confirms only when it came FROM the DM's destination.
Firmware v2.7.26 has our OWN radio emit an error-NONE "implicit" ack
carrying our request_id in two places: on overhearing a relay rebroadcast
the packet over LoRa (ReliableRouter.cpp:54-57, DMs included), and on
seeing its own packet echoed back via MQTT downlink (MQTT.cpp:97). Every
other positive ack is sent from inside isToUs(), i.e. by the destination.
The implicit ones prove the DM left, not that it arrived. Such an ack is refused,
counted (in-process only; the INFO log line is the persistent trace),
and the entry stays pending for the destination's own ACK. NAKs resolve
whoever sent them: only our radio (MAX_RETRANSMIT, guarded by isFromUs,
NextHopRouter.cpp:281-284) or the destination ever sends one.
⚠️ A relayed-then-lost DM gets no MAX_RETRANSMIT (the implicit ack
already stopped retransmission), so it stays SENT when its entry
expires — unknown, never counted as success or failure.

Mirrors the store idioms (``correlation_store``/``reply_context``):
``threading.RLock``, ``_sanitize_positive`` on config numerics
(MagicMock-safe), every public method swallows and logs — ACK bookkeeping
never breaks the bridge hot path.

The ``ROUTING_APP`` parse + NAK→``DropReason`` mapping live here too
(co-located: everything about consuming a Meshtastic ACK in one place).
The parse mirrors the meshtastic library's own ``isAck`` semantics
(``mesh_interface._handleFromRadio``): a ``routing`` dict whose
``errorReason`` is absent or ``"NONE"`` is a positive ACK; any other
reason is a NAK.
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from .delivery_counters import DropReason
from .reply_context import _sanitize_positive

logger = logging.getLogger(__name__)

DEFAULT_TTL_SEC = 600        # forget an un-acked DM after ~10 min (retransmits)
DEFAULT_MAX_PENDING = 1024   # in-flight DM cap, oldest-evicted
BROADCAST_NUM = 0xFFFFFFFF   # no single node can ACK a broadcast


@dataclass(frozen=True)
class RoutingAck:
    """A parsed Meshtastic ``ROUTING_APP`` response correlated to a send.

    ``ok`` True == positive end-to-end ACK (``errorReason`` NONE/absent).
    ``reason`` is the raw meshtastic enum name on a NAK ("" when ``ok``).
    """

    request_id: int
    ok: bool
    reason: str = ""


# Meshtastic Routing.Error enum name → DropReason (closed taxonomy). Every
# value here is a member of watchdog_probes._DELIVERY_FAILURE_REASONS so a
# NAK counts as a real delivery failure in the #74 confirmation-rate ring.
# An unrecognized / future reason falls through to NON_RETRIABLE_ERROR — a
# NAK is by definition a failed delivery; the raw enum is preserved in the
# event ``note`` so the exact cause is never lost.
_NAK_DROP_REASON = {
    "NO_ROUTE": DropReason.DESTINATION_UNREACHABLE,
    "GOT_NAK": DropReason.NON_RETRIABLE_ERROR,
    "TIMEOUT": DropReason.DELIVERY_TIMEOUT,
    "NO_INTERFACE": DropReason.DESTINATION_UNREACHABLE,
    "MAX_RETRANSMIT": DropReason.RETRIES_EXHAUSTED,
    "NO_CHANNEL": DropReason.NON_RETRIABLE_ERROR,
    "TOO_LARGE": DropReason.NON_RETRIABLE_ERROR,
    "NO_RESPONSE": DropReason.DESTINATION_UNREACHABLE,
    "DUTY_CYCLE_LIMIT": DropReason.DELIVERY_TIMEOUT,
    "BAD_REQUEST": DropReason.NON_RETRIABLE_ERROR,
    "NOT_AUTHORIZED": DropReason.NON_RETRIABLE_ERROR,
    "PKI_FAILED": DropReason.NON_RETRIABLE_ERROR,
    "PKI_UNKNOWN_PUBKEY": DropReason.NON_RETRIABLE_ERROR,
    "RATE_LIMIT_EXCEEDED": DropReason.DELIVERY_TIMEOUT,
}


def routing_error_to_drop_reason(reason: str) -> DropReason:
    """Map a meshtastic ``Routing.Error`` enum name to a DropReason.

    Unknown / future reasons → NON_RETRIABLE_ERROR (a NAK is a real
    failure). Always pair the returned reason with a ``note`` carrying
    the raw enum so an unmapped value is still forensically visible.
    """
    if not isinstance(reason, str):
        return DropReason.NON_RETRIABLE_ERROR
    return _NAK_DROP_REASON.get(reason.strip().upper(),
                                DropReason.NON_RETRIABLE_ERROR)


def _node_num(value) -> Optional[int]:
    """A Meshtastic node number (uint32) or None. Accepts an int or a
    ``!hex`` node id; bools, negatives and junk are None (unknown, never
    a match)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 0xFFFFFFFF else None
    if isinstance(value, str) and value.startswith("!"):
        try:
            n = int(value[1:], 16)
        except ValueError:
            return None
        return n if 0 <= n <= 0xFFFFFFFF else None
    return None


def parse_routing_ack(decoded) -> Optional[RoutingAck]:
    """Parse a meshtastic-library ``packet['decoded']`` dict for an ACK/NAK.

    Returns a :class:`RoutingAck` only for a ``ROUTING_APP`` packet that
    carries a positive ``request_id`` (i.e. a response correlated to one
    of our sends). Everything else — wrong portnum, no request_id,
    malformed input — returns ``None`` (not our concern).

    Mirrors ``meshtastic.mesh_interface``'s own ``isAck`` rule: a
    ``routing`` dict whose ``errorReason`` is absent or ``"NONE"`` is a
    positive ACK; any other reason is a NAK. (``MessageToDict`` omits the
    zero-valued NONE enum, so a successful ACK presents ``routing == {}``.)
    """
    try:
        if not isinstance(decoded, dict):
            return None
        if decoded.get("portnum") != "ROUTING_APP":
            return None
        raw_req = decoded.get("requestId", decoded.get("request_id"))
        # MessageToDict renders uint32 request_id as an int; coerce a
        # numeric string defensively but reject bools / junk.
        if isinstance(raw_req, bool):
            return None
        try:
            request_id = int(raw_req)
        except (TypeError, ValueError):
            return None
        if request_id <= 0:
            return None
        routing = decoded.get("routing")
        reason = ""
        if isinstance(routing, dict):
            reason = (routing.get("errorReason")
                      or routing.get("error_reason") or "")
        ok = (reason == "" or reason == "NONE")
        return RoutingAck(
            request_id=request_id,
            ok=ok,
            reason="" if ok else str(reason),
        )
    except Exception as e:  # never raise into the RX hot path
        logger.debug(f"parse_routing_ack failed: {e}")
        return None


class AckTracker:
    """In-flight, bounded, TTL'd map of sent ``packet_id`` → ``msg_id``."""

    def __init__(self, ttl_sec: Optional[float] = None,
                 max_pending: Optional[int] = None):
        self._ttl = _sanitize_positive(ttl_sec, DEFAULT_TTL_SEC)
        self._max = int(_sanitize_positive(max_pending, DEFAULT_MAX_PENDING))
        self._lock = threading.RLock()
        # packet_id -> (msg_id, protocol, registered_monotonic_ts, dest_num)
        self._pending: Dict[int, Tuple[str, str, float, Optional[int]]] = {}
        # Positive ACKs refused, by why (hfm #9: every swallow leaves a witness)
        self._rejected: Dict[str, int] = {}

    def register(self, packet_id, msg_id, protocol: str = "meshtastic",
                 dest_num=None) -> bool:
        """Remember that ``packet_id`` carries ``msg_id`` awaiting an ACK.

        ``dest_num`` is the DM's destination node number; without it a
        positive ACK can never confirm (nothing to check the sender
        against). Returns False on bad input. Enforces TTL + cap on every
        call so the map stays bounded without a sweeper thread (MF010).
        """
        try:
            if (not isinstance(packet_id, int) or isinstance(packet_id, bool)
                    or packet_id <= 0):
                return False
            if not isinstance(msg_id, str) or not msg_id:
                return False
            proto = protocol if isinstance(protocol, str) and protocol \
                else "meshtastic"
            now = time.monotonic()
            dest = _node_num(dest_num)
            with self._lock:
                self._pending[packet_id] = (msg_id, proto, now, dest)
                self._expire_locked(now)
            return True
        except Exception as e:
            logger.warning(f"ack register failed: {e}")
            return False

    def resolve(self, request_id, from_num=None,
                positive: bool = True) -> Optional[Tuple[str, str]]:
        """Pop and return ``(msg_id, protocol)`` for an arriving ACK, or None.

        ``positive`` True (an ACK) confirms only when ``from_num`` equals
        the registered destination; otherwise the ACK is refused, counted
        in ``rejected_counts()``, and the entry stays pending. A NAK
        (``positive`` False) resolves regardless of sender.

        Single-shot: a duplicate / retransmitted ACK for the same
        ``request_id`` resolves to None the second time, so the caller
        records CONFIRMED at most once (idempotent). An entry that aged
        past the TTL between register and resolve is treated as a miss.
        """
        try:
            if not isinstance(request_id, int) or isinstance(request_id, bool):
                return None
            sender = _node_num(from_num)
            with self._lock:
                entry = self._pending.get(request_id)
                if entry is None:
                    return None
                msg_id, proto, ts, dest = entry
                if (time.monotonic() - ts) > self._ttl:
                    del self._pending[request_id]
                    return None
                if positive:
                    why = None
                    if dest is None or dest == BROADCAST_NUM:
                        why = "destination_unknown"
                    elif sender is None:
                        why = "sender_unknown"
                    elif sender != dest:
                        why = "not_from_destination"
                    if why is not None:
                        self._rejected[why] = self._rejected.get(why, 0) + 1
                        logger.info(
                            f"ACK for {msg_id} (pkt={request_id:#0x}) not "
                            f"counted as delivery: {why} (from="
                            f"{'?' if sender is None else f'{sender:#010x}'}, "
                            f"dest={'?' if dest is None else f'{dest:#010x}'})")
                        return None
                del self._pending[request_id]
            return (msg_id, proto)
        except Exception as e:
            logger.warning(f"ack resolve failed: {e}")
            return None

    def rejected_counts(self) -> Dict[str, int]:
        """Positive ACKs refused as delivery proof, by reason."""
        try:
            with self._lock:
                return dict(self._rejected)
        except Exception as e:
            logger.warning(f"ack rejected_counts failed: {e}")
            return {}

    def pending_count(self) -> int:
        """Count of un-resolved, non-expired in-flight entries."""
        try:
            with self._lock:
                self._expire_locked(time.monotonic())
                return len(self._pending)
        except Exception as e:
            logger.warning(f"ack pending_count failed: {e}")
            return 0

    def expire_idle(self) -> int:
        """Prune expired entries + enforce the cap. Returns entries removed."""
        try:
            with self._lock:
                return self._expire_locked(time.monotonic())
        except Exception as e:
            logger.warning(f"ack expire failed: {e}")
            return 0

    def clear(self) -> int:
        """Drop all in-flight entries (operator reset / tests)."""
        try:
            with self._lock:
                n = len(self._pending)
                self._pending.clear()
                return n
        except Exception as e:
            logger.warning(f"ack clear failed: {e}")
            return 0

    # --- internal (lock held by caller) ----------------------------------

    def _expire_locked(self, now: float) -> int:
        """Remove TTL-expired entries, then oldest-evict down to the cap."""
        removed = 0
        cutoff = now - self._ttl
        stale = [pid for pid, (_, _, ts, _) in self._pending.items()
                 if ts < cutoff]
        for pid in stale:
            del self._pending[pid]
            removed += 1
        over = len(self._pending) - self._max
        if over > 0:
            # Oldest by registration ts first — a long-unacked DM is the
            # least likely to ever confirm.
            oldest = sorted(self._pending.items(), key=lambda kv: kv[1][2])
            for pid, _ in oldest[:over]:
                del self._pending[pid]
                removed += 1
        return removed
