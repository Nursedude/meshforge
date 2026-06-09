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

Meshtastic only ACKs DMs — a broadcast gets an *implicit* overhear, not a
``ROUTING_APP`` packet — so this tracker is populated for DM downlinks
only. It is deliberately **in-memory** (no DB): an ACK arrives within
seconds to a couple of minutes, well inside one gateway lifetime, and a
pending entry lost to a restart simply stays ``SENT`` (honest — the proof
was never seen). Bounded + monotonic-TTL'd so a never-acked DM can't leak
(the #74 monotonic-clock lesson: wall-clock NTP backsteps must not freeze
the expiry timer). No sweeper thread (MF010): TTL is enforced lazily on
``register``/``resolve`` and via ``expire_idle()`` from the bridge sweep.

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
        # packet_id -> (msg_id, protocol, registered_monotonic_ts)
        self._pending: Dict[int, Tuple[str, str, float]] = {}

    def register(self, packet_id, msg_id, protocol: str = "meshtastic") -> bool:
        """Remember that ``packet_id`` carries ``msg_id`` awaiting an ACK.

        Returns False on bad input. Enforces TTL + cap on every call so
        the map stays bounded without a sweeper thread (MF010).
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
            with self._lock:
                self._pending[packet_id] = (msg_id, proto, now)
                self._expire_locked(now)
            return True
        except Exception as e:
            logger.warning(f"ack register failed: {e}")
            return False

    def resolve(self, request_id) -> Optional[Tuple[str, str]]:
        """Pop and return ``(msg_id, protocol)`` for an arriving ACK, or None.

        Single-shot: a duplicate / retransmitted ACK for the same
        ``request_id`` resolves to None the second time, so the caller
        records CONFIRMED at most once (idempotent). An entry that aged
        past the TTL between register and resolve is treated as a miss.
        """
        try:
            if not isinstance(request_id, int) or isinstance(request_id, bool):
                return None
            with self._lock:
                entry = self._pending.pop(request_id, None)
            if entry is None:
                return None
            msg_id, proto, ts = entry
            if (time.monotonic() - ts) > self._ttl:
                return None
            return (msg_id, proto)
        except Exception as e:
            logger.warning(f"ack resolve failed: {e}")
            return None

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
        stale = [pid for pid, (_, _, ts) in self._pending.items()
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
