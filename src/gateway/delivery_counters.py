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

Storage: SQLite-backed at
``~/.local/share/meshforge/delivery_counters.db`` (WAL via
``utils.db_helpers.connect_tuned``, DBSpec entry in
``utils.db_inventory``). The gateway daemon writes; the map daemon
serves ``/api/gateway/delivery`` from a read of the same file. This
closes the per-process gap that an in-memory singleton can't bridge
without losing the architectural separation between the two daemons.
Counters become true monotonic across the gateway's lifetime — restart
no longer zeroes the operator's view. The events ring is FIFO-bounded
at ``RING_BUFFER_CAP`` rows via prune-on-insert.

Scope: this module is **observability only** — it does not change
control flow, never validates state transitions, never raises. A
caller that bumps ``confirmed`` without an earlier ``sent`` is fine
from this module's perspective; the queue's lifecycle history table
(``record_lifecycle_event``) is the validator. These counters are the
operator-facing aggregate.

Wiring: see ``record(state, msg_id, ...)`` call sites in
``message_queue.py`` (queue transitions) and ``rns_bridge.py`` (LXMF
delivery callbacks).

Surface: ``snapshot()`` returns a JSON-serializable dict suitable for
splicing into ``/api/status.delivery`` or a dedicated
``/api/gateway/delivery`` endpoint. Operators read it; nothing else
depends on its shape.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.db_helpers import connect_tuned
from utils.paths import atomic_write_text, get_real_user_home


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
    QUEUE_PRESSURE = "queue_pressure"
    QUEUE_SHED = "queue_shed"
    RETRIES_EXHAUSTED = "retries_exhausted"
    NON_RETRIABLE_ERROR = "non_retriable_error"
    CIRCUIT_OPEN = "circuit_open"
    WEDGED = "wedged"
    DESTINATION_UNREACHABLE = "destination_unreachable"
    RNS_DELIVERY_FAILED = "rns_delivery_failed"
    DELIVERY_TIMEOUT = "delivery_timeout"
    EVICTED_OVERFLOW = "evicted_overflow"
    INVALID_PAYLOAD = "invalid_payload"
    UNKNOWN = "unknown"


# Drop reasons that mean a delivery was ATTEMPTED and FAILED — the
# denominator-mates of `confirmed` for a confirmable protocol. Benign
# drops (dedup, capacity shedding, invalid payload) are NOT delivery
# failures and must never count against the confirmation rate. Kept
# byte-for-byte in sync with
# ``utils.watchdog_probes_gateway._DELIVERY_FAILURE_REASONS`` by test
# (``TestDeliveryFailureReasonsParity``): the watchdog can't import this
# module — ``gateway/__init__`` pulls RNS into the sandboxed probe loop —
# so honest_failure_modes #5 is satisfied by test-pin, not shared import.
DELIVERY_FAILURE_REASONS = frozenset({
    DropReason.RNS_DELIVERY_FAILED.value,
    DropReason.RETRIES_EXHAUSTED.value,
    DropReason.DESTINATION_UNREACHABLE.value,
    DropReason.DELIVERY_TIMEOUT.value,
    DropReason.NON_RETRIABLE_ERROR.value,
    DropReason.CIRCUIT_OPEN.value,
    DropReason.WEDGED.value,
})


def compute_confirmation_view(
    state_totals: Dict[str, int],
    state_by_protocol: Dict[str, Dict[str, int]],
    drop_reasons: Dict[str, int],
) -> Dict[str, Any]:
    """Honest confirmation accounting from raw counters (Issue #74 display fix).

    The pre-2026-06-15 ``confirmed / sent`` was a CROSS-POPULATION ratio:
    ``confirmed`` only exists for protocols with a confirmation mechanism
    (RNS — LXMF delivery proofs), while ``sent`` is dominated by
    fire-and-forget mesh sends that can NEVER confirm (no ACK consumption
    yet). The quotient exceeded 1.0 on every mesh-heavy gateway (observed
    1.64) and an operator read it as ">100% confirmed" while the mesh
    direction had ZERO delivery proof — a valid-looking value masking a
    blind spot (the #80 defect class; a dishonest operator display IS an
    app failure, MF018).

    Honest replacement:
      - ``confirmation_rate`` = confirmed / (confirmed + delivery-failures)
        over the confirmable population only — bounded [0,1], None when
        there are no confirmable terminal events yet (no data must NOT read
        as 0% = total failure). Delivery-failures come from the global
        failure-reason drops; if the mesh direction ever exhausts retries
        those count too, biasing the rate DOWN (the safe, problem-surfacing
        direction), never up.
      - ``unconfirmable_sent`` = sends on protocols with NO confirmed event
        (the mesh direction) — surfaced so the blind spot is VISIBLE, never
        averaged into a healthy-looking scalar.
      - ``confirmable_protocols`` = the protocols that record confirmations,
        so the operator knows exactly what the rate covers.
    """
    def _pos_int(v) -> int:
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0

    confirmed = _pos_int((state_totals or {}).get(DeliveryState.CONFIRMED.value, 0))
    confirmed_by_proto = (state_by_protocol or {}).get(
        DeliveryState.CONFIRMED.value, {}) or {}
    sent_by_proto = (state_by_protocol or {}).get(
        DeliveryState.SENT.value, {}) or {}

    confirmable = sorted(p for p, c in confirmed_by_proto.items() if _pos_int(c) > 0)
    confirmable_set = set(confirmable)
    failures = sum(
        _pos_int((drop_reasons or {}).get(r, 0)) for r in DELIVERY_FAILURE_REASONS
    )
    terminal = confirmed + failures
    rate = confirmed / terminal if terminal > 0 else None
    unconfirmable_sent = sum(
        _pos_int(v) for p, v in sent_by_proto.items() if p not in confirmable_set
    )
    return {
        "confirmation_rate": rate,
        "confirmable_protocols": confirmable,
        "unconfirmable_sent": unconfirmable_sent,
    }


CONTENT_ID_VIEW_PAIR_CAP = 250
"""Max (content_id, recipient) pairs listed in the content_id_view
``confirmed`` array. Bounds the ``/api/gateway/delivery`` JSON; pairs are
sorted confirmed-count DESC so any local duplicate (count>1) sorts to the
top and is never truncated away, and ``confirmed_truncated`` surfaces the
drop honestly (no silent cap — honest_failure_modes #9). The events ring
itself is capped at ``RING_BUFFER_CAP`` rows, so confirmed pairs are
already bounded; this is a second belt for a pathological burst."""


def compute_content_id_view(
    agg_rows: "List[Tuple[Any, Any, Any, Any]]",
    pair_cap: int = CONTENT_ID_VIEW_PAIR_CAP,
) -> Dict[str, Any]:
    """Per-box dup/miss aggregation keyed by (content_id, recipient).

    The FIRST consumer of the dedup/identity-arc content_id substrate
    (STEP 4b, measure-only). ``agg_rows`` is the events ring grouped as
    ``(content_id, recipient, state, count)`` — see ``snapshot()``.

    A per-box DUPLICATE is the SAME (content_id, recipient) reaching the
    CONFIRMED state more than once: a real LOCAL double-delivery (e.g. a
    dual-path / peer-relay overlap landing the same logical message twice
    on one gateway). The by-design M→R fan-out delivers exactly one copy
    per DISTINCT recipient, so it confirms each (content_id, recipient)
    once and does NOT false-alarm here. The cross-GATEWAY dup-A (the same
    pair confirmed by >1 gateway — moc 3dfbdb5d AND moc3 f68c2f56 both →
    6b1a0120) is a DIFFERENT signal, detected by the 4c federation rollup
    that joins these per-box ``confirmed`` pairs across boxes.

    Honest blind-spot accounting (honest_failure_modes #2/#9):
      - ``untracked_events`` — events with NO content_id (the mesh leg and
        any not-yet-threaded path). Surfaced, never hidden: this is the
        coverage gap, not a clean zero.
      - ``unattributed_events`` — content_id known but recipient empty;
        cannot key a per-recipient dup, so NOT counted as a pair (an empty
        recipient must never masquerade as a real (content_id, recipient)).

    MEASURE-ONLY: aggregates and surfaces; never suppresses.
    """
    def _pos_int(v) -> int:
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0

    confirmed_v = DeliveryState.CONFIRMED.value
    untracked_events = 0
    unattributed_events = 0
    tracked_events = 0
    # (content_id, recipient) -> {state: count}
    pairs: Dict[Tuple[str, str], Dict[str, int]] = {}

    for row in agg_rows:
        content_id, recipient, state, count = row
        n = _pos_int(count)
        cid = str(content_id or "")
        rcp = str(recipient or "")
        if not cid:
            untracked_events += n
            continue
        tracked_events += n
        if not rcp:
            unattributed_events += n
            continue
        bucket = pairs.setdefault((cid, rcp), {})
        bucket[state] = bucket.get(state, 0) + n

    confirmed_list: List[Dict[str, Any]] = []
    local_duplicate_pairs = 0
    local_duplicate_deliveries = 0
    for (cid, rcp), states in pairs.items():
        ck = _pos_int(states.get(confirmed_v, 0))
        if ck >= 1:
            confirmed_list.append(
                {"content_id": cid, "recipient": rcp, "confirmed": ck})
        if ck > 1:
            local_duplicate_pairs += 1
            local_duplicate_deliveries += ck - 1

    # confirmed-count DESC (then content_id) so a local dup (count>1) sorts
    # first and survives any truncation; cap for JSON size, drop surfaced.
    confirmed_list.sort(key=lambda p: (-p["confirmed"], p["content_id"]))
    confirmed_pairs = len(confirmed_list)
    cap = max(0, pair_cap)
    truncated = confirmed_pairs > cap
    if truncated:
        confirmed_list = confirmed_list[:cap]

    return {
        "tracked_events": tracked_events,
        "untracked_events": untracked_events,
        "unattributed_events": unattributed_events,
        "tracked_pairs": len(pairs),
        "confirmed_pairs": confirmed_pairs,
        "local_duplicate_pairs": local_duplicate_pairs,
        "local_duplicate_deliveries": local_duplicate_deliveries,
        "confirmed": confirmed_list,
        "confirmed_truncated": truncated,
    }


# ── Event record ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class DeliveryEvent:
    """One transition observation.

    ``id`` is whatever string the caller has at the call site: the
    queue's auto-generated msg_id, the CanonicalMessage.id (uuid4),
    or — for bare-protocol paths that don't use either — a synthesized
    "lxmf-<ts>" / similar.

    ``protocol`` is the destination network ("meshtastic" / "rns" /
    "meshcore" / "mqtt"). None for transitions that don't have one
    (a dedup drop at enqueue may not know the protocol).

    ``drop_reason`` is required when ``state == DROPPED``. The
    constructor accepts it for any state — non-drop states ignore it.

    ``note`` is operator-friendly free text (e.g. "rc=1 unit not
    found", "circuit half-open"). Kept short — this rides in the
    events table that operators scrape.
    """

    ts: float
    id: str
    state: DeliveryState
    protocol: Optional[str] = None
    drop_reason: Optional[DropReason] = None
    note: str = ""
    # dedup/identity arc STEP 4a (measure-only): the logical content_id and the
    # delivery recipient. '' when not threaded through this lifecycle path yet.
    content_id: str = ""
    recipient: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "ts": self.ts,
            "id": self.id,
            "state": self.state.value,
            "protocol": self.protocol,
            "drop_reason": self.drop_reason.value if self.drop_reason else None,
        }
        if self.content_id:
            d["content_id"] = self.content_id
        if self.recipient:
            d["recipient"] = self.recipient
        if self.note:
            d["note"] = self.note
        return d


# ── Defaults ─────────────────────────────────────────────────────────


RING_BUFFER_CAP = 500
"""Maximum rows retained in the events table. Sized for ~1 hour of
healthy gateway traffic on a 5-box fleet (a few dozen msgs/min peak);
enough forensic context for incident investigation without bloating
the DB. Per-id history rolls into the queue's durable
``message_lifecycle`` SQLite table — this events table is the fast
"what just happened?" view for ``/api/gateway/delivery``.

Pruned on insert via ``DELETE FROM events WHERE rowid IN (oldest)``,
so the cap is enforced even when the gateway runs for weeks."""


def default_db_path() -> Path:
    """Resolve the counters DB path.

    Env var ``MESHFORGE_DELIVERY_COUNTERS_DB`` wins (test seam; also
    useful for ops who want the DB on a different volume). Otherwise
    matches the standard MeshForge data dir layout per
    ``utils.db_inventory._meshforge_data_dir``.
    """
    override = os.environ.get("MESHFORGE_DELIVERY_COUNTERS_DB")
    if override:
        return Path(override)
    return get_real_user_home() / ".local" / "share" / "meshforge" / "delivery_counters.db"


# ── Counters (DB-backed) ─────────────────────────────────────────────


class DeliveryCounters:
    """Cross-process delivery lifecycle counters + events ring.

    SQLite-backed at ``db_path``. The gateway daemon and map daemon
    point at the same file: gateway writes, map reads. Counters are
    monotonic across process lifecycle — restart no longer zeroes the
    operator's view.

    Two tables:

    * ``counters`` — KV pairs. State totals
      (``state.<value>``), drop-reason histogram
      (``drop.<value>``), per-protocol breakdown
      (``state_proto.<state>.<protocol>``), first/last event ts
      (``meta.first_event_ts``, ``meta.last_event_ts``). KV shape
      keeps the schema sparse — a protocol we've never seen doesn't
      occupy a row.
    * ``events`` — FIFO ring. INTEGER PRIMARY KEY rowid orders
      chronologically; prune-on-insert keeps row count at
      ``ring_cap``.

    Concurrency: a per-instance ``threading.Lock`` serializes writes
    within one process. SQLite's WAL mode + per-connection
    ``busy_timeout`` (30 s from ``connect_tuned``) handles cross-
    process contention between the gateway writer and the map reader.

    Each ``record()`` opens + closes a connection (cheap on a WAL DB).
    The alternative — a long-lived connection — would require
    ``check_same_thread=False`` plus an explicit GIL-aware locking
    discipline; the open-per-call pattern matches the rest of
    MeshForge (PersistentMessageQueue, NodeHistoryDB).
    """

    def __init__(
        self,
        *,
        db_path: Optional[Path] = None,
        ring_cap: int = RING_BUFFER_CAP,
        run_preflight: bool = True,
    ) -> None:
        self._db_path = db_path or default_db_path()
        self._ring_cap = int(ring_cap)
        self._lock = threading.Lock()
        # Write-path health (Issue #63 / reliability backlog #2).
        # `preflight_ok` reflects the at-construction write+read canary;
        # the `_write_error_*` fields track runtime regressions caught
        # during real record() calls. Surfaced via snapshot().health
        # so /api/gateway/delivery shows "writes are broken" without
        # operators having to grep journald.
        self._preflight_ok: bool = False
        self._preflight_ts: Optional[float] = None
        self._preflight_error: Optional[str] = None
        self._consecutive_write_errors: int = 0
        self._last_write_error_ts: Optional[float] = None
        self._last_write_error: Optional[str] = None
        self._init_db()
        if run_preflight:
            self._run_preflight()

    # ── schema ──────────────────────────────────────────────────

    def _init_db(self) -> None:
        """Create the schema. Idempotent; safe to call from multiple
        processes (CREATE TABLE IF NOT EXISTS guards both sides)."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS counters ("
                " key TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0"
                ")"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                " ts REAL NOT NULL,"
                " id TEXT NOT NULL DEFAULT '',"
                " state TEXT NOT NULL,"
                " protocol TEXT,"
                " drop_reason TEXT,"
                # dedup/identity arc STEP 4a: the stable logical content_id and
                # the delivery recipient — together they key honest dup/miss
                # accounting (a content_id alone false-alarms on the by-design
                # M→R fan-out to many recipients).
                " content_id TEXT NOT NULL DEFAULT '',"
                " recipient TEXT NOT NULL DEFAULT '',"
                " note TEXT NOT NULL DEFAULT ''"
                ")"
            )
            # Additive migration for pre-existing fleet DBs (the CREATE above is
            # a no-op when the table exists, so the new columns need ALTER).
            existing = {row[1] for row in
                        conn.execute("PRAGMA table_info(events)")}
            for col in ("content_id", "recipient"):
                if col not in existing:
                    conn.execute(
                        f"ALTER TABLE events ADD COLUMN {col} "
                        "TEXT NOT NULL DEFAULT ''")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_id ON events(id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_content_id "
                "ON events(content_id)"
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        """Open a tuned connection. Caller owns lifecycle."""
        return connect_tuned(self._db_path)

    # ── preflight / health canary (Issue #63) ──────────────────────────

    def _run_preflight(self) -> None:
        """One-shot write+read at construction. Catches the Issue #58
        class — sandbox path drift, schema corruption, disk full, or any
        other write-path failure — before natural traffic flows hours
        later.

        Persists `meta.preflight_ts` (ms) and `meta.preflight_ok` (1/0)
        in the counters table so a reader (the map daemon) can include
        them in its `/api/gateway/delivery.health` snapshot. The writer
        ALSO holds the result in instance state for ERROR-level logging.

        Failure is NOT fatal — the gateway keeps running; observability
        just becomes degraded. Sandbox-path drift was already cured
        loudly in Issue #60 (`assert_writable_or_exit`); this is a
        defense-in-depth for the silent-failure classes that don't trip
        `assert_writable_or_exit` (e.g. mid-run permission change, disk
        full, schema corruption from an aborted upgrade).
        """
        canary_ts = time.time()
        canary_ms = int(canary_ts * 1000)
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    "INSERT INTO counters(key, value) "
                    "VALUES('meta.preflight_ts', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (canary_ms,),
                )
                conn.execute(
                    "INSERT INTO counters(key, value) "
                    "VALUES('meta.preflight_ok', 1) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                )
                conn.commit()
                # Read back to confirm — catches the (rare) class where
                # the write returns success but the value doesn't land
                # (e.g. a stale WAL on a read-only mount).
                row = conn.execute(
                    "SELECT value FROM counters WHERE key = 'meta.preflight_ts'"
                ).fetchone()
                if row is None or row[0] != canary_ms:
                    raise RuntimeError(
                        f"readback mismatch: wrote={canary_ms}, got={row}"
                    )
            self._preflight_ok = True
            self._preflight_ts = canary_ts
            self._preflight_error = None
            logger.info(
                "delivery_counters preflight OK at %s", self._db_path,
            )
        except (sqlite3.Error, OSError, RuntimeError) as e:
            # Best-effort: persist the FAILED preflight state too, so the
            # map daemon's snapshot reflects reality. If even THIS write
            # fails (worst case: read-only filesystem), the local
            # instance state + ERROR log are the canonical signals.
            try:
                with self._lock, self._connect() as conn:
                    conn.execute(
                        "INSERT INTO counters(key, value) "
                        "VALUES('meta.preflight_ts', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (canary_ms,),
                    )
                    conn.execute(
                        "INSERT INTO counters(key, value) "
                        "VALUES('meta.preflight_ok', 0) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    )
                    conn.commit()
            except Exception:  # pragma: no cover — degenerate case
                pass
            self._preflight_ok = False
            self._preflight_ts = canary_ts
            self._preflight_error = str(e)
            logger.error(
                "delivery_counters preflight FAILED at %s: %s — "
                "operator visibility into delivery events will silently "
                "degrade until fixed. Check the database path, "
                "permissions, and disk space.",
                self._db_path, e,
            )

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
        content_id: str = "",
        recipient: str = "",
    ) -> DeliveryEvent:
        """Record one lifecycle transition.

        Never raises (silently coerces unknown enums, swallows DB
        errors after logging) — the gateway's hot paths must not
        crash because a counter call site has a typo or the disk
        is full.

        Returns the constructed event so callers can inspect it in
        tests without a second timestamp call.
        """
        if not isinstance(state, DeliveryState):
            logger.warning(
                "delivery_counters.record: bad state %r — ignoring", state,
            )
            return DeliveryEvent(
                ts=ts if ts is not None else time.time(),
                id=str(msg_id) if msg_id is not None else "",
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
        if state == DeliveryState.DROPPED and drop_reason is None:
            drop_reason = DropReason.UNKNOWN

        event = DeliveryEvent(
            ts=ts if ts is not None else time.time(),
            id=str(msg_id) if msg_id is not None else "",
            state=state,
            protocol=protocol,
            drop_reason=drop_reason,
            note=note,
            content_id=str(content_id or ""),
            recipient=str(recipient or ""),
        )

        try:
            self._persist(event)
            # Successful write → clear runtime error state. If we had
            # been failing, log the recovery so it's visible without
            # operators having to diff timestamps.
            with self._lock:
                prior_errors = self._consecutive_write_errors
                if prior_errors > 0:
                    logger.info(
                        "delivery_counters: write-path recovered after "
                        "%d consecutive errors", prior_errors,
                    )
                self._consecutive_write_errors = 0
                self._last_write_error = None
                self._last_write_error_ts = None
            if prior_errors > 0:
                # Clear the persisted count too so cross-process
                # readers see the recovery (Issue #74).
                self._persist_write_error_state(0, None)
        except sqlite3.Error as e:
            with self._lock:
                self._consecutive_write_errors += 1
                self._last_write_error_ts = time.time()
                self._last_write_error = str(e)
                error_count = self._consecutive_write_errors
                error_ts_ms = round(self._last_write_error_ts * 1000)
            # First failure logs ERROR (operator-visible). Subsequent
            # failures throttle to DEBUG to avoid flooding journald —
            # snapshot.health.consecutive_write_errors keeps the count.
            if error_count == 1:
                logger.error(
                    "delivery_counters: DB write FAILED (%s) — event "
                    "lost. snapshot.health.last_write_error tracks this; "
                    "next successful write logs recovery.", e,
                )
            else:
                logger.debug(
                    "delivery_counters: write failure #%d: %s",
                    error_count, e,
                )
            self._persist_write_error_state(error_count, error_ts_ms)
        return event

    def _persist_write_error_state(
        self, count: int, ts_ms: Optional[int]
    ) -> None:
        """Best-effort UPSERT of the runtime write-error state (Issue #74).

        Cross-process honesty: the map daemon serves ``snapshot()`` to
        the watchdog's delivery_write_canary probe, but the in-memory
        error counters live in the WRITER (gateway) process — before
        this persist, the probe's degraded branch always read 0 from
        the map daemon's reader instance and could never fire on a
        mid-run write regression. Wrapped in its own try/except so a
        failure here can't recurse or mask the original error (this is
        often called BECAUSE a write just failed — when the DB is fully
        down, the local fields plus ``last_successful_write_ts``
        staleness still carry the signal).
        """
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO counters(key, value) "
                    "VALUES('meta.consecutive_write_errors', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (count,),
                )
                if ts_ms is not None:
                    conn.execute(
                        "INSERT INTO counters(key, value) "
                        "VALUES('meta.last_write_error_ts', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (ts_ms,),
                    )
                else:
                    # Recovery: clear the persisted ts too — the
                    # health contract is "no error state after a
                    # successful write" (see
                    # test_runtime_write_recovery_clears_error_state).
                    conn.execute(
                        "DELETE FROM counters "
                        "WHERE key = 'meta.last_write_error_ts'"
                    )
                conn.commit()
        except sqlite3.Error as e:
            logger.debug(
                "delivery_counters: could not persist write-error "
                "state (%s) — local fields carry it", e,
            )

    def _persist(self, event: DeliveryEvent) -> None:
        """Single-transaction write: bump counters + insert event +
        prune oldest row(s) if over cap.

        Held under ``self._lock`` so we don't issue overlapping
        UPSERTs from the same process; cross-process serialization
        is SQLite's busy_timeout."""
        with self._lock, self._connect() as conn:
            # Counter bumps via UPSERT. SQLite's ON CONFLICT clause is
            # the idiomatic atomic increment.
            counter_keys: List[str] = [f"state.{event.state.value}"]
            if event.drop_reason is not None:
                counter_keys.append(f"drop.{event.drop_reason.value}")
            if event.protocol:
                counter_keys.append(
                    f"state_proto.{event.state.value}.{event.protocol}"
                )
            for key in counter_keys:
                conn.execute(
                    "INSERT INTO counters(key, value) VALUES(?, 1) "
                    "ON CONFLICT(key) DO UPDATE SET value = value + 1",
                    (key,),
                )
            # Meta: first/last ts. first_event_ts is set only on first
            # write; last_event_ts is overwritten every time.
            conn.execute(
                "INSERT INTO counters(key, value) VALUES('meta.first_event_ts', ?) "
                "ON CONFLICT(key) DO NOTHING",
                (round(event.ts * 1000),),
            )
            conn.execute(
                "INSERT INTO counters(key, value) VALUES('meta.last_event_ts', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (round(event.ts * 1000),),
            )
            # Event row.
            conn.execute(
                "INSERT INTO events("
                "ts, id, state, protocol, drop_reason, content_id, recipient, note"
                ") VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.ts,
                    event.id,
                    event.state.value,
                    event.protocol,
                    event.drop_reason.value if event.drop_reason else None,
                    event.content_id,
                    event.recipient,
                    event.note,
                ),
            )
            # Ring cap via prune-on-insert. SELECT COUNT(*) is O(N) on
            # an unindexed scan but N is tiny (cap 500), and we only
            # pay it on the writes that are likely to push us over.
            count = conn.execute(
                "SELECT COUNT(*) FROM events"
            ).fetchone()[0]
            if count > self._ring_cap:
                excess = count - self._ring_cap
                conn.execute(
                    "DELETE FROM events WHERE rowid IN "
                    "(SELECT rowid FROM events ORDER BY rowid ASC LIMIT ?)",
                    (excess,),
                )
            conn.commit()

    # ── operator surface ───────────────────────────────────────

    def snapshot(self, recent_limit: int = 50) -> Dict[str, Any]:
        """Operator-readable aggregate. Same shape as the in-memory
        predecessor — switching backends is invisible at the
        ``/api/gateway/delivery`` boundary.

        Reads happen via the same WAL DB the gateway is writing into;
        SQLite's snapshot isolation gives a consistent view. The
        ``recent_limit`` argument caps the JSON size (default 50).
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT key, value FROM counters"
                ).fetchall()
                events_rows = conn.execute(
                    "SELECT ts, id, state, protocol, drop_reason, "
                    "content_id, recipient, note "
                    "FROM events ORDER BY rowid DESC "
                    "LIMIT ?",
                    (max(0, recent_limit),),
                ).fetchall()
                # dedup/identity arc STEP 4b: aggregate the WHOLE ring (not
                # just recent_limit) by (content_id, recipient, state) so the
                # content_id_view counts dups/misses across the full window.
                # Bounded — the ring is pruned to RING_BUFFER_CAP rows.
                agg_rows = conn.execute(
                    "SELECT content_id, recipient, state, COUNT(*) "
                    "FROM events GROUP BY content_id, recipient, state"
                ).fetchall()
                ring_capacity = self._ring_cap
        except sqlite3.Error as e:
            logger.warning(
                "delivery_counters: snapshot read failed (%s) — "
                "returning empty defaults", e,
            )
            rows = []
            events_rows = []
            agg_rows = []
            ring_capacity = self._ring_cap

        state_totals: Dict[str, int] = {s.value: 0 for s in DeliveryState}
        drop_reasons: Dict[str, int] = {r.value: 0 for r in DropReason}
        state_by_protocol: Dict[str, Dict[str, int]] = {
            s.value: {} for s in DeliveryState
        }
        first_event_ts: Optional[float] = None
        last_event_ts: Optional[float] = None
        # Cross-process health fields (set by the last writer's
        # preflight / write-error persist; see `_run_preflight` and
        # `_persist_write_error_state`). May be None when no writer
        # has touched this DB yet.
        db_preflight_ok: Optional[bool] = None
        db_preflight_ts: Optional[float] = None
        db_write_errors: Optional[int] = None
        db_write_error_ts: Optional[float] = None

        for key, value in rows:
            if key.startswith("state."):
                state_totals[key[6:]] = value
            elif key.startswith("drop."):
                drop_reasons[key[5:]] = value
            elif key.startswith("state_proto."):
                _, state_v, proto = key.split(".", 2)
                state_by_protocol.setdefault(state_v, {})[proto] = value
            elif key == "meta.first_event_ts":
                first_event_ts = value / 1000.0
            elif key == "meta.last_event_ts":
                last_event_ts = value / 1000.0
            elif key == "meta.preflight_ts":
                db_preflight_ts = value / 1000.0
            elif key == "meta.preflight_ok":
                db_preflight_ok = bool(value)
            elif key == "meta.consecutive_write_errors":
                db_write_errors = int(value)
            elif key == "meta.last_write_error_ts":
                db_write_error_ts = value / 1000.0

        # Honest confirmation accounting (Issue #74 display residual fixed
        # 2026-06-15). The old cross-population `confirmed / sent` read as
        # ">100% confirmed" (observed 1.64) while the mesh direction had zero
        # delivery proof — a valid-looking value masking a blind spot.
        confirmation = compute_confirmation_view(
            state_totals, state_by_protocol, drop_reasons)

        # dedup/identity arc STEP 4b (measure-only): per-box dup/miss
        # aggregation keyed by (content_id, recipient) — the first consumer
        # of the content_id substrate. The 4c federation rollup joins each
        # box's ``confirmed`` pairs to detect the cross-gateway dup-A.
        content_id_view = compute_content_id_view(agg_rows)

        # events_rows comes back newest-first from the DESC ORDER BY;
        # the snapshot contract is newest-LAST so the operator can
        # tail a JSON dump and read the latest at the bottom.
        recent: List[Dict[str, Any]] = []
        for (ts, msg_id, state, protocol, drop_reason,
                content_id, recipient, note) in reversed(events_rows):
            d: Dict[str, Any] = {
                "ts": ts,
                "id": msg_id,
                "state": state,
                "protocol": protocol,
                "drop_reason": drop_reason,
            }
            if content_id:
                d["content_id"] = content_id
            if recipient:
                d["recipient"] = recipient
            if note:
                d["note"] = note
            recent.append(d)

        # Health block (Issue #63 / reliability backlog #2; #74
        # cross-process write-error truth). The cross-process truth is
        # what's persisted in the DB (`db_preflight_*` and
        # `db_write_errors*` — set by the last writer). Before #74 the
        # runtime write-error fields were local-only, so a READER
        # (the map daemon serving the watchdog probe) always saw 0 and
        # the probe's degraded branch could never fire. Now the count
        # merges via max(local, persisted): the reader (local 0) gets
        # the writer's persisted truth; the writer wins when its own
        # error-state persist also failed (DB fully down). The error
        # STRING stays local-only (counters values are numeric).
        # Operators interpret: `db_preflight_ok=false` → the last
        # writer failed at startup. `consecutive_write_errors>0` →
        # mid-run write regression (also: `last_event_ts` stale
        # relative to expected traffic).
        with self._lock:
            write_errors = max(
                self._consecutive_write_errors, db_write_errors or 0
            )
            # ts only meaningful alongside a nonzero count — a healed
            # write path reports no error state at all.
            write_error_ts = None if write_errors == 0 else max(
                (t for t in (self._last_write_error_ts, db_write_error_ts)
                 if t is not None),
                default=None,
            )
            health = {
                "db_path": str(self._db_path),
                "preflight_ok": (
                    db_preflight_ok if db_preflight_ok is not None
                    else self._preflight_ok
                ),
                "preflight_ts": (
                    db_preflight_ts if db_preflight_ts is not None
                    else self._preflight_ts
                ),
                "preflight_error": self._preflight_error,
                "last_successful_write_ts": last_event_ts,
                "consecutive_write_errors": write_errors,
                "last_write_error_ts": write_error_ts,
                "last_write_error": self._last_write_error,
            }

        return {
            "state_totals": state_totals,
            "drop_reasons": drop_reasons,
            "state_by_protocol": state_by_protocol,
            "confirmation_rate": confirmation["confirmation_rate"],
            "confirmable_protocols": confirmation["confirmable_protocols"],
            "unconfirmable_sent": confirmation["unconfirmable_sent"],
            "content_id_view": content_id_view,
            "recent": recent,
            "first_event_ts": first_event_ts,
            "last_event_ts": last_event_ts,
            "ring_capacity": ring_capacity,
            "health": health,
        }

    def recent(self, limit: Optional[int] = None) -> List[DeliveryEvent]:
        """Return recent events (newest last). ``limit`` clamps the
        returned list; ``None`` returns everything in the events
        table."""
        try:
            with self._connect() as conn:
                if limit is None:
                    cursor = conn.execute(
                        "SELECT ts, id, state, protocol, drop_reason, note "
                        "FROM events ORDER BY rowid ASC"
                    )
                elif limit <= 0:
                    return []
                else:
                    cursor = conn.execute(
                        "SELECT ts, id, state, protocol, drop_reason, note "
                        "FROM events ORDER BY rowid DESC LIMIT ?",
                        (limit,),
                    )
                rows = cursor.fetchall()
        except sqlite3.Error:
            return []
        if limit is not None and limit > 0:
            rows = list(reversed(rows))
        return [self._row_to_event(r) for r in rows]

    def history_for(self, msg_id: str) -> List[DeliveryEvent]:
        """All retained events for one message id, oldest first.

        Bounded by the ring cap — older events may have aged out. The
        durable trace lives in
        ``PersistentMessageQueue.message_lifecycle``; this is the
        fast in-memory-ish lookup."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT ts, id, state, protocol, drop_reason, note "
                    "FROM events WHERE id = ? ORDER BY rowid ASC",
                    (str(msg_id),),
                ).fetchall()
        except sqlite3.Error:
            return []
        return [self._row_to_event(r) for r in rows]

    @staticmethod
    def _row_to_event(
        row: Tuple[float, str, str, Optional[str], Optional[str], str],
    ) -> DeliveryEvent:
        ts, msg_id, state, protocol, drop_reason, note = row
        try:
            state_enum = DeliveryState(state)
        except ValueError:
            state_enum = DeliveryState.DROPPED
        dr_enum: Optional[DropReason] = None
        if drop_reason:
            try:
                dr_enum = DropReason(drop_reason)
            except ValueError:
                dr_enum = DropReason.UNKNOWN
        return DeliveryEvent(
            ts=ts, id=msg_id, state=state_enum,
            protocol=protocol, drop_reason=dr_enum, note=note or "",
        )

    # ── test helpers ───────────────────────────────────────────

    def _reset_for_tests(self) -> None:
        """Drop all rows. Production code must never call."""
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM counters")
                conn.execute("DELETE FROM events")
                conn.commit()
        except sqlite3.Error:
            pass


# ── Process-wide singleton ───────────────────────────────────────────


_singleton: Optional[DeliveryCounters] = None
_singleton_lock = threading.Lock()


def get_singleton() -> DeliveryCounters:
    """Return the process-wide counter handle.

    All call sites in one process share this handle; multiple
    processes (gateway daemon + map daemon) construct independent
    handles that point at the same SQLite DB."""
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
    content_id: str = "",
    recipient: str = "",
) -> DeliveryEvent:
    """Module-level convenience — bumps the singleton's counters."""
    return get_singleton().record(
        state, msg_id,
        protocol=protocol, drop_reason=drop_reason, note=note,
        content_id=content_id, recipient=recipient,
    )


def snapshot(recent_limit: int = 50) -> Dict[str, Any]:
    """Module-level convenience for the HTTP handler."""
    return get_singleton().snapshot(recent_limit=recent_limit)


# ── content_id_view state-file producer (dedup/identity arc STEP 4c) ──
#
# The 4b ``content_id_view`` rides ``/api/gateway/delivery`` for boxes that
# run a map daemon. But the cross-box JOIN's whole point is the active-active
# gateways — and one of them (moc3) is gateway-only and serves NO HTTP (the
# moc3 hardware constraint). So each active gateway PUBLISHES its view to an
# operator-owned state file the JOIN can ssh-collect (the lab-rollup ssh+cat
# pattern). The producer MUST run as the operator: root can't safely open the
# operator's WAL DB (its -wal/-shm would land root-owned and break writes),
# so the gateway process — which already owns the counters — writes the file.

CONTENT_ID_VIEW_STATE_SCHEMA = 1
"""Bump when the published envelope shape changes so a collector reading an
old file can tell. The inner ``content_id_view`` is versioned implicitly by
``compute_content_id_view``'s field set."""


def content_id_view_state_path() -> Path:
    """``~/.local/share/meshforge/content_id_view.json`` — CO-LOCATED with
    ``delivery_counters.db`` (``default_db_path``'s dir).

    NOT ``~/.local/state`` (the lab-tooling convention): the gateway service
    runs under ``ProtectHome=read-only`` and its ``ReadWritePaths`` covers
    ``~/.local/share/meshforge`` (where the DB lives) but NOT ``~/.local/
    state`` — writing there hit ``Errno 30`` on every publish (the Issue #60
    sandbox-path-drift class, caught at live 4c activation: a silent write
    failure while the service stays active). Writing next to the DB inherits
    its already-proven-writable sandbox coverage. The lab tools live in
    ``~/.local/state`` only because they run as user-timer units without
    ``ProtectHome``.

    ``MESHFORGE_CONTENT_ID_VIEW_STATE`` (full path) overrides — a test seam
    (the suite points it at a tmp file so a test exercising the real gateway
    loop can't write the operator's actual data dir), also useful for ops.
    Mirrors ``MESHFORGE_DELIVERY_COUNTERS_DB``."""
    override = os.environ.get("MESHFORGE_CONTENT_ID_VIEW_STATE")
    if override:
        return Path(override)
    return (get_real_user_home() / ".local" / "share" / "meshforge"
            / "content_id_view.json")


def build_content_id_view_state(
    view: Dict[str, Any],
    *,
    host: Optional[str] = None,
    now: Optional[float] = None,
    unconfirmable_sent: Any = 0,
) -> Dict[str, Any]:
    """Pure: wrap a ``content_id_view`` in the published envelope.

    ``host`` lets the collector attribute a confirmed pair to the box that
    delivered it (the JOIN keys fleet dups on >1 DISTINCT host). ``ts`` lets
    the collector treat a stale file as a coverage gap rather than reading a
    frozen view as live (honest_failure_modes #2: absence != healthy).
    ``unconfirmable_sent`` (the mesh blind spot — sends on protocols with no
    delivery proof) rides alongside so the JOIN can keep it on its OWN line,
    never averaged into the dup/miss numbers (the #74 lesson)."""
    usent = (int(unconfirmable_sent)
             if isinstance(unconfirmable_sent, (int, float))
             and not isinstance(unconfirmable_sent, bool) else 0)
    return {
        "schema": CONTENT_ID_VIEW_STATE_SCHEMA,
        "host": host if host is not None else socket.gethostname(),
        "ts": float(now) if now is not None else time.time(),
        "content_id_view": view,
        "unconfirmable_sent": usent,
    }


def write_content_id_view_state(
    *,
    view: Optional[Dict[str, Any]] = None,
    path: Optional[Path] = None,
    host: Optional[str] = None,
    now: Optional[float] = None,
) -> bool:
    """Best-effort: publish this process's ``content_id_view`` to the state
    file (atomic). NEVER raises — a publish failure must never touch the
    bridge. Returns True on success.

    ``view`` defaults to the live singleton's view (the gateway's own
    counters); tests pass it explicitly. A non-dict view (snapshot returned
    no block) is NOT published — an absent file is honestly "no data" to the
    collector, never a forged healthy zero."""
    try:
        usent: Any = 0
        if view is None:
            snap = snapshot()
            view = snap.get("content_id_view")
            usent = snap.get("unconfirmable_sent", 0)
        payload_view = view
        if not isinstance(payload_view, dict):
            return False
        payload = build_content_id_view_state(
            payload_view, host=host, now=now, unconfirmable_sent=usent)
        target = Path(path) if path is not None else content_id_view_state_path()
        atomic_write_text(target, json.dumps(payload, separators=(",", ":")))
        return True
    except Exception:
        logger.debug("content_id_view state publish failed", exc_info=True)
        return False


def _reset_singleton_for_tests() -> None:
    """Test-only: drop the singleton. Production code must never call."""
    global _singleton
    with _singleton_lock:
        _singleton = None
