"""Honest delivery lifecycle counters — Fork C of the federation→DB
pressure→wedge stability arc.

The existing ``PersistentMessageQueue._stats`` tracks queue *operations*
(``enqueued`` / ``delivered`` / ``failed`` / ``retried`` / ``shed`` /
``deduplicated``), which conflates two things operators actually want
separated:

* **Sent** — message was handed off to the destination network. For RNS
  this is ``LXMRouter.handle_outbound`` returning without raising; for
  Meshtastic this is the toRadio POST succeeding. Today's
  ``_stats["delivered"]`` measures this — but the name is misleading
  because actual end-to-end delivery is not yet known.
* **Confirmed** — the *receiver* signaled receipt. For RNS this is the
  ``register_delivery_callback`` firing (LXMF delivery proof); for
  Meshtastic this is the wantAck path returning a proof. The bridge
  tracks this via ``bridge_health.DeliveryTracker.confirm_delivery``
  but never aggregates it into the queue-level stats. So
  ``_stats["delivered"]`` is structurally optimistic.

Fork C adds a parallel set of counters that *don't* conflate the two,
indexed by a single ``DeliveryState`` enum and a ``DropReason``
taxonomy. Each lifecycle event carries the message id (CanonicalMessage
.id when available, the queue's auto-generated id otherwise) so an
operator following one message can find every counter bump it caused.

Scope: this module is **observability only** — it does not change
control flow, never validates state transitions, never raises. A
caller that bumps ``confirmed`` without an earlier ``sent`` is fine
from this module's perspective; the queue's lifecycle history table
(``record_lifecycle_event``) is the validator. These counters are the
operator-facing aggregate.

Wiring: see commit message + ``record(state, msg_id, ...)`` call sites
in ``message_queue.py`` (queue transitions) and ``rns_bridge.py``
(LXMF delivery callbacks). Adding new call sites is intentional — the
module exists so they accumulate honestly.

Surface: ``snapshot()`` returns a JSON-serializable dict suitable for
splicing into ``/api/status.delivery`` or a dedicated
``/api/gateway/delivery`` endpoint. Operators read it; nothing else
depends on its shape.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Deque, Dict, List, Optional


logger = logging.getLogger(__name__)


# ── State + reason taxonomy ──────────────────────────────────────────


class DeliveryState(Enum):
    """The four states a message visits in its delivery lifecycle.

    Deliberately coarse — the queue's ``MessageLifecycleState`` keeps
    the fine-grained CREATED/QUEUED/SENT/RELAYED/DELIVERED/ACK trace.
    These four are the operator-relevant rollups.
    """

    QUEUED = "queued"
    """Message entered the persistent queue. Includes initial enqueue
    plus re-queue after a transient failure (a single message can
    transition into QUEUED multiple times; the counter is per-event,
    not per-id)."""

    SENT = "sent"
    """Handed off to the destination network. Subprocess / RPC returned
    success; the network has the bytes. Does NOT imply the receiver
    got them."""

    CONFIRMED = "confirmed"
    """Receiver confirmed receipt — LXMF delivery proof, Meshtastic
    wantAck proof, MQTT broker QoS-1 PUBACK. Final positive outcome."""

    DROPPED = "dropped"
    """Final negative outcome. Always carries a ``DropReason`` so the
    operator can answer "why did we lose this message?" without
    digging into logs."""


class DropReason(Enum):
    """Why a message reached the DROPPED terminal state.

    Intentionally a closed taxonomy — adding a new reason is a
    deliberate code change, not an ad-hoc string. New entries should
    name the *cause* (not the symptom): ``CIRCUIT_OPEN`` is the
    circuit breaker's decision; ``DESTINATION_UNREACHABLE`` is the
    routing layer's decision; both might apply to the same outage but
    they're separate stories.
    """

    DEDUP = "dedup"
    """Identical payload already in the queue (or recently delivered);
    enqueue suppressed. Not a failure — desired behavior — but counted
    as a drop because the message did not progress."""

    QUEUE_PRESSURE = "queue_pressure"
    """Enqueue rejected because the queue was at capacity and no
    lower-priority message could be shed. Operator signal: queue is
    sustained-full; check downstream delivery rate."""

    QUEUE_SHED = "queue_shed"
    """An already-queued lower-priority message was evicted to make
    room for a higher-priority incoming message. Distinct from
    QUEUE_PRESSURE: this one *was* admitted, then dropped under load."""

    RETRIES_EXHAUSTED = "retries_exhausted"
    """Message was retried up to ``max_retries`` and never succeeded.
    Moved to dead letter; counted as DROPPED here."""

    NON_RETRIABLE_ERROR = "non_retriable_error"
    """Retry policy classified the error as permanent (e.g. 404,
    malformed payload, no such destination). Moved to dead letter
    immediately."""

    CIRCUIT_OPEN = "circuit_open"
    """Per-destination circuit breaker rejected the send. The breaker
    will half-open after its recovery timeout; meanwhile the message
    is dropped (caller decides whether to re-enqueue)."""

    WEDGED = "wedged"
    """The RNS RPC hot-path watchdog (Fork A) aborted the send via
    process exit. Companion fingerprint: ``rns_rpc_wedge``."""

    DESTINATION_UNREACHABLE = "destination_unreachable"
    """Routing layer couldn't resolve the destination (no path in
    RNS path_table, no Meshtastic neighbor known, etc.)."""

    RNS_DELIVERY_FAILED = "rns_delivery_failed"
    """LXMF delivery callback fired ``failed`` rather than
    ``delivered`` — receiver got the packet but rejected, or
    transport gave up. The receipt's ``failure_reason`` (when
    available) is stamped in the event note."""

    DELIVERY_TIMEOUT = "delivery_timeout"
    """``DeliveryTracker`` aged out a pending delivery without seeing
    a confirm callback. The bytes left the gateway; whether they
    arrived is unknown. Counted as a drop because the operator-visible
    outcome is "no confirmation"."""

    EVICTED_OVERFLOW = "evicted_overflow"
    """``DeliveryTracker._force_timeout_oldest`` evicted a pending
    record because the in-memory tracker exceeded MAX_HISTORY. Rare
    but worth a discrete bucket: an operator seeing this means the
    tracker is undersized for current traffic."""

    INVALID_PAYLOAD = "invalid_payload"
    """Payload failed pre-flight validation (e.g. non-UTF-8 where
    text was required, oversized for protocol). Caller-side drop;
    nothing was actually sent."""

    UNKNOWN = "unknown"
    """Escape hatch for legacy call sites that haven't been migrated
    to a precise reason yet. Treat its count as a TODO marker."""


# ── Event record + ring buffer ───────────────────────────────────────


@dataclass(frozen=True)
class DeliveryEvent:
    """One transition observation.

    ``id`` is whatever string the caller has at the call site: the
    queue's auto-generated msg_id, the CanonicalMessage.id (uuid4),
    or — for bare-protocol paths that don't use either — a synthesized
    "lxmf-<ts>" / similar. Stamping it lets operators correlate
    across counter rings + the durable lifecycle table.

    ``protocol`` is the destination network ("meshtastic" / "rns" /
    "meshcore" / "mqtt"). None for transitions that don't have one
    (a dedup drop at enqueue may not know the protocol if the queue
    layer is upstream of routing).

    ``drop_reason`` is required when ``state == DROPPED``. The
    constructor accepts it for any state — non-drop states ignore it —
    so callers don't have to branch on state shape.

    ``note`` is operator-friendly free text (e.g. "rc=1 unit not
    found", "circuit half-open"). Kept short — this rides in a ring
    buffer that operators scrape.
    """

    ts: float
    id: str
    state: DeliveryState
    protocol: Optional[str] = None
    drop_reason: Optional[DropReason] = None
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "ts": self.ts,
            "id": self.id,
            "state": self.state.value,
            "protocol": self.protocol,
            "drop_reason": self.drop_reason.value if self.drop_reason else None,
        }
        if self.note:
            d["note"] = self.note
        return d


# ── Defaults ─────────────────────────────────────────────────────────


RING_BUFFER_CAP = 500
"""Most recent events retained in memory. Sized for ~1 hour of healthy
gateway traffic on a 5-box fleet (a few dozen msgs/min peak); enough
forensic context for incident investigation without bloating the
process. Per-id history rolls into the queue's durable
``message_lifecycle`` SQLite table — this in-memory ring is the fast
"what just happened?" view."""


# ── Counters (the operator-facing aggregate) ─────────────────────────


class DeliveryCounters:
    """Process-wide delivery lifecycle counters + recent-events ring.

    Constructor params exist for tests; production code uses
    ``get_singleton()`` so call sites share a single counter set.

    All public methods are thread-safe. Internally:

    * A single ``threading.Lock`` serializes all writes. Reads
      (``snapshot()``) hold the lock briefly to take a consistent
      copy. The lock window is bounded by the dict / deque operation
      cost — both O(1) — so contention from many publisher threads
      is not a concern at gateway-traffic rates.
    * Counters are plain Python ints. We don't use atomic primitives
      because we always read them under the lock when snapshotting.
    * The ring buffer is a single ``collections.deque(maxlen=cap)``;
      eviction is O(1) on append.

    Per-protocol breakdown is stored as a nested dict
    ``state_by_protocol[state][protocol] -> count`` so the snapshot
    shape is uniform regardless of which protocols this box has seen
    traffic for.
    """

    def __init__(self, ring_cap: int = RING_BUFFER_CAP) -> None:
        self._lock = threading.Lock()
        self._state_totals: Dict[str, int] = {
            s.value: 0 for s in DeliveryState
        }
        self._drop_reasons: Dict[str, int] = {
            r.value: 0 for r in DropReason
        }
        self._state_by_protocol: Dict[str, Dict[str, int]] = {
            s.value: {} for s in DeliveryState
        }
        self._ring: Deque[DeliveryEvent] = deque(maxlen=ring_cap)
        self._first_event_ts: Optional[float] = None
        self._last_event_ts: Optional[float] = None

    # ── ingest ──────────────────────────────────────────────────

    def record(
        self,
        state: DeliveryState,
        msg_id: str,
        *,
        protocol: Optional[str] = None,
        drop_reason: Optional[DropReason] = None,
        note: str = "",
        ts: Optional[float] = None,
    ) -> DeliveryEvent:
        """Record one lifecycle transition.

        Returns the resulting ``DeliveryEvent`` so callers can inspect
        it in tests / log it without a second timestamp call. Never
        raises (silently coerces unknown enums into UNKNOWN-flavored
        equivalents) — the gateway's hot paths must not crash because
        a counter call site has a typo.
        """
        if not isinstance(state, DeliveryState):
            logger.warning(
                "delivery_counters.record: bad state %r — ignoring", state,
            )
            return DeliveryEvent(
                ts=ts or time.time(),
                id=msg_id,
                state=DeliveryState.DROPPED,
                drop_reason=DropReason.UNKNOWN,
                note=f"bad_state:{state!r}",
            )
        if drop_reason is not None and not isinstance(drop_reason, DropReason):
            logger.warning(
                "delivery_counters.record: bad drop_reason %r — coercing to UNKNOWN",
                drop_reason,
            )
            drop_reason = DropReason.UNKNOWN

        # Enforce the only invariant we care about: DROPPED must have
        # a reason. Soft-default to UNKNOWN rather than raise — the
        # caller may have a legacy code path that hasn't been migrated.
        if state == DeliveryState.DROPPED and drop_reason is None:
            drop_reason = DropReason.UNKNOWN

        event = DeliveryEvent(
            ts=ts if ts is not None else time.time(),
            id=str(msg_id) if msg_id is not None else "",
            state=state,
            protocol=protocol,
            drop_reason=drop_reason,
            note=note,
        )

        with self._lock:
            self._state_totals[state.value] += 1
            if drop_reason is not None:
                self._drop_reasons[drop_reason.value] += 1
            if protocol:
                bucket = self._state_by_protocol[state.value]
                bucket[protocol] = bucket.get(protocol, 0) + 1
            self._ring.append(event)
            if self._first_event_ts is None:
                self._first_event_ts = event.ts
            self._last_event_ts = event.ts

        return event

    # ── operator surface ───────────────────────────────────────

    def snapshot(self, recent_limit: int = 50) -> Dict[str, Any]:
        """Operator-readable aggregate. Includes:

        * ``state_totals`` — counter per ``DeliveryState`` value.
        * ``drop_reasons`` — histogram across ``DropReason`` values.
        * ``state_by_protocol`` — per-state breakdown across the
          protocols this box has actually seen, sparse (no zero
          entries for protocols we've never recorded).
        * ``confirmation_rate`` — ``confirmed / sent`` ratio (or None
          when sent == 0). Floors at 0; uncapped at the top — a single
          rogue confirmed without a prior sent can push it above 1, by
          design, because the counters are observability not validation.
        * ``recent`` — last N events as dicts, newest last. Capped at
          ``recent_limit`` (default 50) so the JSON stays small.
        * ``first_event_ts`` / ``last_event_ts`` — window the operator
          is reading. Useful to spot "no traffic since restart" vs
          "active gateway".
        """
        with self._lock:
            sent = self._state_totals[DeliveryState.SENT.value]
            confirmed = self._state_totals[DeliveryState.CONFIRMED.value]
            confirmation_rate = (
                confirmed / sent if sent > 0 else None
            )
            ring = list(self._ring)
            state_totals = dict(self._state_totals)
            drop_reasons = dict(self._drop_reasons)
            state_by_protocol = {
                s: dict(v) for s, v in self._state_by_protocol.items()
            }
            first_ts = self._first_event_ts
            last_ts = self._last_event_ts

        recent = [e.to_dict() for e in ring[-recent_limit:]] if recent_limit > 0 else []
        return {
            "state_totals": state_totals,
            "drop_reasons": drop_reasons,
            "state_by_protocol": state_by_protocol,
            "confirmation_rate": confirmation_rate,
            "recent": recent,
            "first_event_ts": first_ts,
            "last_event_ts": last_ts,
            "ring_capacity": self._ring.maxlen,
        }

    def recent(self, limit: Optional[int] = None) -> List[DeliveryEvent]:
        """Return recent events (newest last). ``limit`` clamps the
        returned list; ``None`` returns everything in the ring."""
        with self._lock:
            buf = list(self._ring)
        if limit is None or limit >= len(buf):
            return buf
        if limit <= 0:
            return []
        return buf[-limit:]

    def history_for(self, msg_id: str) -> List[DeliveryEvent]:
        """All retained events for one message id, oldest first.

        Bounded by the ring's capacity — older events for the same id
        may have already aged out. The durable trace lives in
        ``PersistentMessageQueue.message_lifecycle``; this is the
        in-memory fast lookup."""
        with self._lock:
            return [e for e in self._ring if e.id == msg_id]

    # ── test helpers ───────────────────────────────────────────

    def _reset_for_tests(self) -> None:
        """Drop all counters + ring. Production code must never call."""
        with self._lock:
            self._state_totals = {s.value: 0 for s in DeliveryState}
            self._drop_reasons = {r.value: 0 for r in DropReason}
            self._state_by_protocol = {s.value: {} for s in DeliveryState}
            self._ring.clear()
            self._first_event_ts = None
            self._last_event_ts = None


# ── Process-wide singleton ───────────────────────────────────────────


_singleton: Optional[DeliveryCounters] = None
_singleton_lock = threading.Lock()


def get_singleton() -> DeliveryCounters:
    """Return the process-wide counter set, constructing on first call.

    The gateway daemon and the map daemon each have their own
    instance — they're independent processes — but within one process
    all call sites land in the same counter set."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = DeliveryCounters()
        return _singleton


def record(
    state: DeliveryState,
    msg_id: str,
    *,
    protocol: Optional[str] = None,
    drop_reason: Optional[DropReason] = None,
    note: str = "",
) -> DeliveryEvent:
    """Module-level convenience — bumps the singleton's counters.

    Lets call sites avoid the ``get_singleton().record(...)`` dance;
    matches the ``wedge_events.publish(...)`` ergonomic so the two
    Fork A/Fork B/Fork C primitives feel symmetric."""
    return get_singleton().record(
        state, msg_id,
        protocol=protocol, drop_reason=drop_reason, note=note,
    )


def snapshot(recent_limit: int = 50) -> Dict[str, Any]:
    """Module-level convenience for the HTTP handler."""
    return get_singleton().snapshot(recent_limit=recent_limit)


def _reset_singleton_for_tests() -> None:
    """Test-only: drop the singleton. Production code must never call."""
    global _singleton
    with _singleton_lock:
        _singleton = None
