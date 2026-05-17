"""Tests for the Fork C delivery lifecycle counters.

These tests pin the operator contract for `/api/gateway/delivery`:

* The four-state taxonomy (QUEUED / SENT / CONFIRMED / DROPPED).
* DropReason coverage — every enum value bumps the right bucket.
* Per-protocol breakdown is sparse (no zero rows) but uniform across
  states (every recorded state has a per-protocol dict).
* confirmation_rate = confirmed / sent (None when sent == 0).
* Snapshot is JSON-serializable.
* Counters are thread-safe (many publisher threads, no lost events).
* Module-level convenience matches singleton behavior.
"""
from __future__ import annotations

import json
import threading
import time
from typing import List

import pytest

from gateway import delivery_counters as dc
from gateway.delivery_counters import (
    DeliveryCounters,
    DeliveryEvent,
    DeliveryState,
    DropReason,
    RING_BUFFER_CAP,
)


@pytest.fixture(autouse=True)
def _reset_singleton(tmp_path, monkeypatch):
    """Reset the singleton AND point its DB at a per-test tmp file.

    The module is SQLite-backed (cross-process visibility) — without
    redirecting the path, tests would write to / read from the
    operator's real ``~/.local/share/meshforge/delivery_counters.db``.
    Pytest gives each test a unique ``tmp_path``, so cases stay
    isolated even though they share the env-var-based path resolution.
    """
    monkeypatch.setenv(
        "MESHFORGE_DELIVERY_COUNTERS_DB",
        str(tmp_path / "singleton.db"),
    )
    dc._reset_singleton_for_tests()
    yield
    dc._reset_singleton_for_tests()


# ── State taxonomy ───────────────────────────────────────────────────


class TestDeliveryStateEnum:
    def test_has_four_states(self):
        names = {s.name for s in DeliveryState}
        assert names == {"QUEUED", "SENT", "CONFIRMED", "DROPPED"}

    def test_values_are_lowercase_strings(self):
        """Stable on-the-wire JSON identifiers — locked so a rename
        doesn't silently change the operator contract."""
        for s in DeliveryState:
            assert s.value == s.name.lower()


class TestDropReasonEnum:
    def test_required_reasons_present(self):
        names = {r.name for r in DropReason}
        for required in [
            "DEDUP", "QUEUE_PRESSURE", "QUEUE_SHED",
            "RETRIES_EXHAUSTED", "NON_RETRIABLE_ERROR",
            "CIRCUIT_OPEN", "WEDGED", "DESTINATION_UNREACHABLE",
            "RNS_DELIVERY_FAILED", "DELIVERY_TIMEOUT",
            "EVICTED_OVERFLOW", "INVALID_PAYLOAD", "UNKNOWN",
        ]:
            assert required in names, f"DropReason.{required} missing"

    def test_unknown_is_present_as_escape_hatch(self):
        """Legacy call sites can use UNKNOWN until migrated."""
        assert DropReason.UNKNOWN.value == "unknown"


# ── DeliveryEvent shape ──────────────────────────────────────────────


class TestDeliveryEventShape:
    def test_to_dict_round_trips_required_fields(self):
        ev = DeliveryEvent(
            ts=1.5, id="abc-123", state=DeliveryState.SENT,
            protocol="rns",
        )
        d = ev.to_dict()
        assert d["ts"] == 1.5
        assert d["id"] == "abc-123"
        assert d["state"] == "sent"
        assert d["protocol"] == "rns"
        assert d["drop_reason"] is None

    def test_to_dict_includes_drop_reason_when_set(self):
        ev = DeliveryEvent(
            ts=1.0, id="x", state=DeliveryState.DROPPED,
            drop_reason=DropReason.DEDUP,
        )
        assert ev.to_dict()["drop_reason"] == "dedup"

    def test_to_dict_omits_note_when_empty(self):
        """Operator-facing JSON stays compact when there's nothing
        to say."""
        ev = DeliveryEvent(
            ts=1.0, id="x", state=DeliveryState.QUEUED,
        )
        assert "note" not in ev.to_dict()

    def test_to_dict_includes_note_when_set(self):
        ev = DeliveryEvent(
            ts=1.0, id="x", state=DeliveryState.DROPPED,
            drop_reason=DropReason.NON_RETRIABLE_ERROR,
            note="404 from upstream",
        )
        assert ev.to_dict()["note"] == "404 from upstream"

    def test_frozen(self):
        ev = DeliveryEvent(ts=1.0, id="x", state=DeliveryState.QUEUED)
        with pytest.raises((AttributeError, Exception)):
            ev.ts = 2.0  # type: ignore[misc]


# ── record() basic transitions ───────────────────────────────────────


class TestRecordTransitions:
    def test_queued_bumps_state_total(self):
        c = DeliveryCounters()
        c.record(DeliveryState.QUEUED, "msg-1", protocol="rns")
        snap = c.snapshot()
        assert snap["state_totals"]["queued"] == 1

    def test_sent_bumps_state_total(self):
        c = DeliveryCounters()
        c.record(DeliveryState.SENT, "msg-1", protocol="rns")
        assert c.snapshot()["state_totals"]["sent"] == 1

    def test_confirmed_bumps_state_total(self):
        c = DeliveryCounters()
        c.record(DeliveryState.CONFIRMED, "msg-1", protocol="rns")
        assert c.snapshot()["state_totals"]["confirmed"] == 1

    def test_dropped_with_reason_bumps_both_state_and_reason(self):
        c = DeliveryCounters()
        c.record(
            DeliveryState.DROPPED, "msg-1",
            protocol="rns", drop_reason=DropReason.DEDUP,
        )
        snap = c.snapshot()
        assert snap["state_totals"]["dropped"] == 1
        assert snap["drop_reasons"]["dedup"] == 1

    def test_dropped_without_reason_defaults_to_unknown(self):
        """The DROPPED-must-have-reason invariant — soft-defaults rather
        than crashes a hot path."""
        c = DeliveryCounters()
        c.record(DeliveryState.DROPPED, "msg-1", protocol="rns")
        snap = c.snapshot()
        assert snap["drop_reasons"]["unknown"] == 1


# ── Per-protocol breakdown ───────────────────────────────────────────


class TestPerProtocolBreakdown:
    def test_breakdown_sparse_for_unrecorded_protocols(self):
        """A box that's only seen RNS traffic should not have a
        'meshtastic' row in its breakdown."""
        c = DeliveryCounters()
        c.record(DeliveryState.SENT, "msg-1", protocol="rns")
        sent = c.snapshot()["state_by_protocol"]["sent"]
        assert "rns" in sent
        assert "meshtastic" not in sent

    def test_breakdown_per_state(self):
        c = DeliveryCounters()
        c.record(DeliveryState.QUEUED, "1", protocol="rns")
        c.record(DeliveryState.SENT, "1", protocol="rns")
        c.record(DeliveryState.SENT, "2", protocol="meshtastic")
        snap = c.snapshot()
        assert snap["state_by_protocol"]["queued"] == {"rns": 1}
        assert snap["state_by_protocol"]["sent"] == {
            "rns": 1, "meshtastic": 1,
        }

    def test_protocol_none_does_not_bump_breakdown(self):
        """Calls without a protocol still bump state_totals but not
        the per-protocol dict — keeps the breakdown honest."""
        c = DeliveryCounters()
        c.record(DeliveryState.SENT, "1", protocol=None)
        snap = c.snapshot()
        assert snap["state_totals"]["sent"] == 1
        assert snap["state_by_protocol"]["sent"] == {}


# ── confirmation_rate ────────────────────────────────────────────────


class TestConfirmationRate:
    def test_rate_is_none_when_no_sends(self):
        c = DeliveryCounters()
        c.record(DeliveryState.QUEUED, "1", protocol="rns")
        assert c.snapshot()["confirmation_rate"] is None

    def test_rate_is_zero_when_sends_but_no_confirms(self):
        c = DeliveryCounters()
        c.record(DeliveryState.SENT, "1", protocol="rns")
        c.record(DeliveryState.SENT, "2", protocol="rns")
        assert c.snapshot()["confirmation_rate"] == 0.0

    def test_rate_is_one_when_every_send_confirmed(self):
        c = DeliveryCounters()
        c.record(DeliveryState.SENT, "1", protocol="rns")
        c.record(DeliveryState.CONFIRMED, "1", protocol="rns")
        assert c.snapshot()["confirmation_rate"] == 1.0

    def test_rate_above_one_when_legacy_confirms_have_no_send(self):
        """Counters are observability — they don't pretend a stale
        confirm wasn't real. Operators reading rate > 1 know to look
        at the call site that's stamping confirms without sends."""
        c = DeliveryCounters()
        c.record(DeliveryState.SENT, "1", protocol="rns")
        c.record(DeliveryState.CONFIRMED, "1", protocol="rns")
        c.record(DeliveryState.CONFIRMED, "2", protocol="rns")
        assert c.snapshot()["confirmation_rate"] == 2.0


# ── Ring buffer ──────────────────────────────────────────────────────


class TestRingBuffer:
    def test_recent_returns_newest_last(self):
        c = DeliveryCounters()
        for i in range(5):
            c.record(DeliveryState.QUEUED, f"m{i}", protocol="rns")
        ids = [e.id for e in c.recent()]
        assert ids == ["m0", "m1", "m2", "m3", "m4"]

    def test_recent_limit_clamps(self):
        c = DeliveryCounters()
        for i in range(20):
            c.record(DeliveryState.QUEUED, f"m{i}", protocol="rns")
        last3 = c.recent(3)
        assert [e.id for e in last3] == ["m17", "m18", "m19"]

    def test_ring_evicts_oldest_at_cap(self):
        c = DeliveryCounters()
        for i in range(RING_BUFFER_CAP + 10):
            c.record(DeliveryState.QUEUED, f"m{i}", protocol="rns")
        evts = c.recent()
        assert len(evts) == RING_BUFFER_CAP
        assert evts[0].id == "m10"
        assert evts[-1].id == f"m{RING_BUFFER_CAP + 9}"

    def test_history_for_filters_by_id(self):
        c = DeliveryCounters()
        c.record(DeliveryState.QUEUED, "abc", protocol="rns")
        c.record(DeliveryState.SENT, "abc", protocol="rns")
        c.record(DeliveryState.QUEUED, "xyz", protocol="rns")
        c.record(DeliveryState.CONFIRMED, "abc", protocol="rns")
        hist = c.history_for("abc")
        assert [e.state for e in hist] == [
            DeliveryState.QUEUED,
            DeliveryState.SENT,
            DeliveryState.CONFIRMED,
        ]


# ── Snapshot contract ────────────────────────────────────────────────


class TestSnapshot:
    def test_all_required_keys_present(self):
        c = DeliveryCounters()
        c.record(DeliveryState.QUEUED, "m", protocol="rns")
        snap = c.snapshot()
        for key in (
            "state_totals", "drop_reasons", "state_by_protocol",
            "confirmation_rate", "recent", "first_event_ts",
            "last_event_ts", "ring_capacity",
        ):
            assert key in snap

    def test_snapshot_is_json_serializable(self):
        c = DeliveryCounters()
        c.record(DeliveryState.QUEUED, "m", protocol="rns")
        c.record(
            DeliveryState.DROPPED, "m",
            protocol="rns", drop_reason=DropReason.RETRIES_EXHAUSTED,
            note="3/3 attempts",
        )
        snap = c.snapshot()
        # round-trip through json
        s = json.dumps(snap)
        parsed = json.loads(s)
        assert parsed["state_totals"]["queued"] == 1
        assert parsed["drop_reasons"]["retries_exhausted"] == 1

    def test_recent_limit_caps_serialized_size(self):
        """Operators reading /api/gateway/delivery shouldn't get a 500-
        event payload by default. The recent_limit knob controls that."""
        c = DeliveryCounters()
        for i in range(100):
            c.record(DeliveryState.QUEUED, f"m{i}", protocol="rns")
        snap = c.snapshot(recent_limit=10)
        assert len(snap["recent"]) == 10
        assert snap["recent"][-1]["id"] == "m99"

    def test_recent_limit_zero_returns_empty(self):
        c = DeliveryCounters()
        c.record(DeliveryState.QUEUED, "m", protocol="rns")
        assert c.snapshot(recent_limit=0)["recent"] == []

    def test_first_and_last_event_ts_populated(self):
        c = DeliveryCounters()
        t0 = time.time()
        c.record(DeliveryState.QUEUED, "m", protocol="rns")
        snap = c.snapshot()
        # SQLite stores ts as ms-precision INTEGER (see _persist) so
        # the recovered value can be up to 1 ms below the wall-clock t0
        # we sampled at fixture entry. Operator-visible tolerance is
        # generous — these counters drive a 5-second dashboard refresh.
        assert snap["first_event_ts"] >= t0 - 0.002
        assert snap["last_event_ts"] >= snap["first_event_ts"]


# ── Defensive: bad input ─────────────────────────────────────────────


class TestBadInput:
    def test_non_enum_state_is_logged_and_normalized(self, caplog):
        c = DeliveryCounters()
        ev = c.record(state="not-an-enum", msg_id="m")  # type: ignore[arg-type]
        # No crash. Returned event surfaces the issue via note.
        assert ev.state == DeliveryState.DROPPED
        assert "bad_state" in ev.note

    def test_non_enum_drop_reason_coerced_to_unknown(self):
        c = DeliveryCounters()
        c.record(
            DeliveryState.DROPPED, "m",
            protocol="rns",
            drop_reason="not-an-enum",  # type: ignore[arg-type]
        )
        assert c.snapshot()["drop_reasons"]["unknown"] == 1

    def test_none_msg_id_normalized_to_empty_string(self):
        c = DeliveryCounters()
        ev = c.record(DeliveryState.QUEUED, msg_id=None)  # type: ignore[arg-type]
        assert ev.id == ""


# ── Thread safety ────────────────────────────────────────────────────


class TestThreadSafety:
    def test_concurrent_record_no_lost_events(self):
        """Many threads simultaneously bumping counters must produce
        the exact expected totals."""
        c = DeliveryCounters()
        N_THREADS = 20
        PER_THREAD = 100

        def worker(tid: int):
            for i in range(PER_THREAD):
                c.record(
                    DeliveryState.QUEUED, f"t{tid}-{i}", protocol="rns",
                )

        threads = [
            threading.Thread(target=worker, args=(t,))
            for t in range(N_THREADS)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        snap = c.snapshot()
        assert snap["state_totals"]["queued"] == N_THREADS * PER_THREAD
        assert snap["state_by_protocol"]["queued"]["rns"] == \
            N_THREADS * PER_THREAD


# ── Module-level convenience ─────────────────────────────────────────


class TestModuleLevelAPI:
    def test_module_record_lands_in_singleton(self):
        dc.record(DeliveryState.QUEUED, "m", protocol="rns")
        snap = dc.snapshot()
        assert snap["state_totals"]["queued"] == 1

    def test_singleton_is_stable(self):
        a = dc.get_singleton()
        b = dc.get_singleton()
        assert a is b

    def test_reset_for_tests_drops_singleton(self):
        a = dc.get_singleton()
        dc._reset_singleton_for_tests()
        b = dc.get_singleton()
        assert a is not b


# ── Operator example: a full lifecycle through one id ────────────────


class TestEndToEndLifecycle:
    def test_one_message_progresses_through_states(self):
        c = DeliveryCounters()
        msg_id = "lifecycle-1"
        c.record(DeliveryState.QUEUED, msg_id, protocol="rns")
        c.record(DeliveryState.SENT, msg_id, protocol="rns")
        c.record(DeliveryState.CONFIRMED, msg_id, protocol="rns")

        snap = c.snapshot()
        assert snap["state_totals"] == {
            "queued": 1, "sent": 1, "confirmed": 1, "dropped": 0,
        }
        assert snap["confirmation_rate"] == 1.0
        assert all(d["id"] == msg_id for d in snap["recent"])

    def test_message_dedup_drop_short_circuits(self):
        """A dedup at enqueue means QUEUED was never recorded — the
        counter ring shows DROPPED with reason=DEDUP and nothing else."""
        c = DeliveryCounters()
        c.record(
            DeliveryState.DROPPED, "dedup-aaaa",
            protocol="rns", drop_reason=DropReason.DEDUP,
        )
        snap = c.snapshot()
        assert snap["state_totals"]["queued"] == 0
        assert snap["state_totals"]["dropped"] == 1
        assert snap["drop_reasons"]["dedup"] == 1
        assert snap["confirmation_rate"] is None  # no sends

    def test_message_dropped_after_retries_pattern(self):
        c = DeliveryCounters()
        msg_id = "retry-1"
        c.record(DeliveryState.QUEUED, msg_id, protocol="rns")
        c.record(
            DeliveryState.DROPPED, msg_id,
            protocol="rns",
            drop_reason=DropReason.RETRIES_EXHAUSTED,
            note="3/3 attempts",
        )
        snap = c.snapshot()
        assert snap["state_totals"]["queued"] == 1
        assert snap["state_totals"]["sent"] == 0
        assert snap["state_totals"]["dropped"] == 1
        assert snap["drop_reasons"]["retries_exhausted"] == 1
        hist = c.history_for(msg_id)
        assert [e.state for e in hist] == [
            DeliveryState.QUEUED, DeliveryState.DROPPED,
        ]
        assert hist[-1].note == "3/3 attempts"
