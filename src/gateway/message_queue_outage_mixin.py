"""
Outage behaviour for PersistentMessageQueue: defer-don't-count + max age.

ROADMAP 6f (F2+F5, operator grilling 2026-10-17). Split out of
message_queue.py (1,500-line rule, MF025) in the lifecycle-mixin idiom: the
queue inherits this mixin, so these run as bound methods on the queue's own
``self._get_connection()`` (the connect_tuned chokepoint stays in
message_queue.py).

- ``mark_deferred``: a sender raised SendDeferred — back to pending,
  retry_after set, retry_count untouched, ``_deferrals`` counted on the row.
- ``expire_overdue``: rows of a destination registered with ``max_age_s``
  that are older than it become dead letters, each witnessed (counter,
  DROPPED ``expired`` delivery record, WARNING). Runs from process_once
  BEFORE the ready_fn gate, so a gated destination still ages out.
- Clock jump (operator Q15): ``note_clock`` compares Δwall with Δmonotonic
  every process_once tick; a step beyond CLOCK_JUMP_S shifts every pending
  row's created_at by the jump (``clock_jumps``), so age stays time actually
  waited. A step that happened while the process was down shows up as an
  age < 0 or > AGE_SANITY_CAP_S: the row is re-stamped once
  (``age_unknown``) — the backstop, never an expiry.
- Witnesses fire only when the UPDATE changed the row (review F2: two
  process_once callers race), and one expiry pass handles at most
  _EXPIRE_BATCH rows per destination (review F5: 1000 rows stalled the
  worker ~5 s). The batch is oldest-first and the cap applies before the
  age test, so 200 ancient (> 7 d, re-stamped) rows delay a real expiry by
  one pass (_EXPIRE_EVERY_S) — bounded, by design.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from gateway import delivery_counters as _dc
from .message_queue_models import AGE_SANITY_CAP_S

logger = logging.getLogger(__name__)

#: |Δwall − Δmonotonic| beyond this between two ticks is a clock step.
CLOCK_JUMP_S = 60.0


class MessageQueueOutageMixin:
    """Defer + expiry methods mixed into PersistentMessageQueue."""

    _EXPIRE_BATCH = 200
    _clock_wall: Optional[datetime] = None
    _clock_mono: Optional[float] = None

    def note_clock(self, wall: datetime, mono: float) -> None:
        """Detect a wall-clock step between two ticks and shift queued rows'
        created_at AND retry_after by it (a backward step otherwise strands a
        deferred row for the step's length)."""
        # Two process_once callers: read-compare-store the pair atomically so
        # one step is applied once, never twice. Callers sample outside the
        # lock, so an older sample can land second: drop it (equal mono too —
        # no elapsed time to compare), or it reads as the jump undone and the
        # next tick re-applies the jump.
        with self._lock:
            last_wall, last_mono = self._clock_wall, self._clock_mono
            if last_mono is not None and mono <= last_mono:
                return
            self._clock_wall, self._clock_mono = wall, mono
        if last_wall is None or last_mono is None:
            return
        jump = (wall - last_wall).total_seconds() - (mono - last_mono)
        if abs(jump) <= CLOCK_JUMP_S:
            return
        shift = timedelta(seconds=jump)
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT id, created_at, retry_after FROM messages "
                "WHERE status IN ('pending', 'in_progress')").fetchall()
            for row in rows:
                try:
                    created = (datetime.fromisoformat(row["created_at"])
                               + shift).isoformat()
                except (TypeError, ValueError):
                    continue
                try:
                    retry = ((datetime.fromisoformat(row["retry_after"])
                              + shift).isoformat()
                             if row["retry_after"] else None)
                except (TypeError, ValueError):
                    retry = row["retry_after"]
                conn.execute(
                    "UPDATE messages SET created_at = ?, retry_after = ? "
                    "WHERE id = ?", (created, retry, row["id"]))
        with self._lock:
            self._stats["clock_jumps"] += 1
        logger.warning(
            f"Wall clock stepped {jump:+.0f}s against monotonic time — "
            f"shifted {len(rows)} queued row(s) so their age stays real")

    def mark_deferred(self, msg_id: str, delay_s: float, reason: str = "") -> bool:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT payload FROM messages WHERE id = ?", (msg_id,)
            ).fetchone()
            if not row:
                return False
            try:
                payload = json.loads(row["payload"])
            except (TypeError, ValueError):
                payload = {}
            payload["_deferrals"] = int(payload.get("_deferrals", 0) or 0) + 1
            now = datetime.now()
            changed = conn.execute("""
                UPDATE messages
                SET status = 'pending', payload = ?, updated_at = ?,
                    retry_after = ?, error_message = ?
                WHERE id = ? AND status IN ('pending', 'in_progress')
            """, (json.dumps(payload), now.isoformat(),
                  (now + timedelta(seconds=delay_s)).isoformat(),
                  f"deferred: {reason}"[:200], msg_id)).rowcount
        if changed != 1:
            return False
        with self._lock:
            self._stats["deferred_total"] += 1
        logger.debug(f"Message {msg_id} deferred {delay_s:.0f}s ({reason})")
        return True

    def expire_overdue(self) -> int:
        """Dead-letter rows older than their destination's max_age_s."""
        expired = 0
        now = datetime.now()
        for destination, max_age in list(self._send_max_age.items()):
            with self._get_connection() as conn:
                rows = conn.execute("""
                    SELECT id, created_at FROM messages
                    WHERE destination = ? AND status = 'pending'
                    ORDER BY created_at ASC LIMIT ?
                """, (destination, self._EXPIRE_BATCH)).fetchall()
            for row in rows:
                age = self._row_age_s(row["created_at"], now)
                if age is None or age < 0 or age > AGE_SANITY_CAP_S:
                    self._restamp(row["id"], now)
                    continue
                if age > max_age and self._expire(
                        row["id"], destination, age, max_age):
                    expired += 1
        return expired

    def oldest_pending_age_s(self) -> Optional[float]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT MIN(created_at) AS oldest FROM messages "
                "WHERE status = 'pending'").fetchone()
        if not row or not row["oldest"]:
            return None
        return self._row_age_s(row["oldest"], datetime.now())

    @staticmethod
    def _row_age_s(created_at: str, now: datetime) -> Optional[float]:
        try:
            return (now - datetime.fromisoformat(created_at)).total_seconds()
        except (TypeError, ValueError):
            return None

    def _restamp(self, msg_id: str, now: datetime) -> None:
        with self._get_connection() as conn:
            changed = conn.execute(
                "UPDATE messages SET created_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (now.isoformat(), msg_id)).rowcount
        if changed != 1:
            return
        with self._lock:
            self._stats["age_unknown"] += 1
        logger.warning(
            f"Message {msg_id}: age unknowable (wall clock jumped) — "
            f"re-stamped once, NOT expired")

    def _expire(self, msg_id: str, destination: str, age: float,
                max_age: float) -> bool:
        with self._get_connection() as conn:
            changed = conn.execute("""
                UPDATE messages
                SET status = 'dead_letter', updated_at = ?, error_message = ?
                WHERE id = ? AND status = 'pending'
            """, (datetime.now().isoformat(),
                  f"expired after {age:.0f}s (max {max_age:.0f}s)",
                  msg_id)).rowcount
        if changed != 1:
            return False  # delivered/claimed meanwhile: not ours to witness
        with self._lock:
            self._stats["expired_total"] += 1
        _dc.record(
            _dc.DeliveryState.DROPPED, msg_id=msg_id, protocol=destination,
            drop_reason=_dc.DropReason.EXPIRED,
            note=f"age={age:.0f}s max={max_age:.0f}s",
        )
        logger.warning(
            f"Message {msg_id} ({destination}) EXPIRED after {age / 3600:.1f} h "
            f"undelivered — dead-lettered")
        return True
