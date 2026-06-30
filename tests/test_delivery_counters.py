"""Tests for the Fork C delivery lifecycle counters.

These tests pin the operator contract for `/api/gateway/delivery`:

* The four-state taxonomy (QUEUED / SENT / CONFIRMED / DROPPED).
* DropReason coverage — every enum value bumps the right bucket.
* Per-protocol breakdown is sparse (no zero rows) but uniform across
  states (every recorded state has a per-protocol dict).
* confirmation_rate = confirmed / (confirmed + delivery-failures) over the
  confirmable population, bounded [0,1], None when no confirmable terminal
  outcome yet (Issue #74 display fix — was the >1.0 cross-population
  confirmed/sent); unconfirmable_sent surfaces the mesh-direction blind spot.
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

    def test_rate_is_none_when_sent_but_no_terminal_outcome(self):
        """Sent-but-not-yet-confirmed/failed is PENDING, not failure. With
        no confirmable terminal outcome the rate is None ('no data'), never
        a false 0% that reads as total failure of a still-in-flight send
        (honest_failure_modes #2: absence of terminal evidence != failure).
        The pendency is visible in state_totals.sent, not faked into a 0%."""
        c = DeliveryCounters()
        c.record(DeliveryState.SENT, "1", protocol="rns")
        c.record(DeliveryState.SENT, "2", protocol="rns")
        assert c.snapshot()["confirmation_rate"] is None

    def test_rate_is_zero_when_confirmable_deliveries_all_fail(self):
        """0% is the honest reading only when there ARE terminal outcomes
        and none confirmed — here one delivery FAILED (not merely pending)."""
        c = DeliveryCounters()
        c.record(DeliveryState.DROPPED, "1", protocol="rns",
                 drop_reason=DropReason.RNS_DELIVERY_FAILED)
        assert c.snapshot()["confirmation_rate"] == 0.0

    def test_rate_is_one_when_every_confirmable_delivery_confirmed(self):
        c = DeliveryCounters()
        c.record(DeliveryState.SENT, "1", protocol="rns")
        c.record(DeliveryState.CONFIRMED, "1", protocol="rns")
        assert c.snapshot()["confirmation_rate"] == 1.0

    def test_rate_never_exceeds_one_confirms_without_sends(self):
        """Issue #74 fix: the rate is bounded [0,1]. Confirms without a
        matching send (fan-out / canary / proof-after-restart) used to push
        confirmed/sent above 1.0 (read as '>100% confirmed'); the honest
        denominator is confirmed+failures, so the rate caps at 1.0."""
        c = DeliveryCounters()
        c.record(DeliveryState.SENT, "1", protocol="rns")
        c.record(DeliveryState.CONFIRMED, "1", protocol="rns")
        c.record(DeliveryState.CONFIRMED, "2", protocol="rns")
        assert c.snapshot()["confirmation_rate"] == 1.0

    def test_rate_not_inflated_by_unconfirmable_mesh_sends(self):
        """The live-data shape (#74): RNS confirms + many fire-and-forget
        mesh sends. The old confirmed/all-sent read 1.64 (">164% confirmed")
        while mesh had ZERO proof. Honest: rate covers the confirmable
        population only (1.0 here) and the mesh sends surface SEPARATELY in
        unconfirmable_sent — the blind spot is visible, not averaged away."""
        c = DeliveryCounters()
        c.record(DeliveryState.CONFIRMED, "r1", protocol="rns")
        c.record(DeliveryState.CONFIRMED, "r2", protocol="rns")
        for i in range(5):
            c.record(DeliveryState.SENT, f"m{i}", protocol="meshtastic")
        snap = c.snapshot()
        assert snap["confirmation_rate"] == 1.0      # confirmable-only, <= 1
        assert snap["unconfirmable_sent"] == 5        # mesh blind spot visible
        assert snap["confirmable_protocols"] == ["rns"]


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


# ── State-transition contract ─────────────────────────────────────────
#
# These tests pin the end-to-end lifecycle for one msg_id traveling
# through real call sites. test_history_for_filters_by_id (above) covers
# the basic ordering; this class adds the operator-visible contract:
# DROP terminates the lifecycle, and confirmation_rate is honest in
# the SENT-then-DROP case (i.e., DROP after SEND is NOT a confirm).


class TestStateTransitionContract:
    def test_full_lifecycle_q_s_c_in_order(self):
        c = DeliveryCounters()
        msg_id = "lifecycle-1"
        c.record(DeliveryState.QUEUED, msg_id, protocol="rns")
        c.record(DeliveryState.SENT, msg_id, protocol="rns")
        c.record(DeliveryState.CONFIRMED, msg_id, protocol="rns")
        states = [e.state for e in c.history_for(msg_id)]
        assert states == [
            DeliveryState.QUEUED,
            DeliveryState.SENT,
            DeliveryState.CONFIRMED,
        ]

    def test_drop_after_send_is_not_a_confirm(self):
        """A delivery-failure DROP bumps the rate's denominator (it's a
        confirmable terminal outcome) but NOT the numerator. So with one
        failed delivery and no confirms the rate is an honest 0%."""
        c = DeliveryCounters()
        msg_id = "lifecycle-2"
        c.record(DeliveryState.QUEUED, msg_id, protocol="rns")
        c.record(DeliveryState.SENT, msg_id, protocol="rns")
        c.record(
            DeliveryState.DROPPED, msg_id, protocol="rns",
            drop_reason=DropReason.RNS_DELIVERY_FAILED,
            note="receiver rejected",
        )
        snap = c.snapshot()
        assert snap["confirmation_rate"] == 0.0
        states = [e.state for e in c.history_for(msg_id)]
        assert states[-1] == DeliveryState.DROPPED

    def test_lifecycle_independent_per_msg_id(self):
        """Two messages in flight at once each carry their own history;
        history_for keeps them separate. Rate is over confirmable TERMINAL
        outcomes: a confirmed (terminal), b still sent-pending (not
        terminal, not a failure) → 1/1 = 1.0. b's pendency shows in
        state_totals.sent, not as a rate drag."""
        c = DeliveryCounters()
        c.record(DeliveryState.QUEUED, "a", protocol="rns")
        c.record(DeliveryState.QUEUED, "b", protocol="rns")
        c.record(DeliveryState.SENT, "a", protocol="rns")
        c.record(DeliveryState.SENT, "b", protocol="rns")
        c.record(DeliveryState.CONFIRMED, "a", protocol="rns")  # only a
        states_a = [e.state for e in c.history_for("a")]
        states_b = [e.state for e in c.history_for("b")]
        assert states_a == [
            DeliveryState.QUEUED, DeliveryState.SENT,
            DeliveryState.CONFIRMED,
        ]
        assert states_b == [DeliveryState.QUEUED, DeliveryState.SENT]
        assert c.snapshot()["confirmation_rate"] == 1.0

    def test_queue_retried_message_can_reach_confirmed(self):
        """The Fork-D-fixed gap: messages reaching RNS via the persistent
        queue retry path used to bump SENT (from mark_delivered) but
        never CONFIRMED. With the retry path now wiring the LXMF
        delivery callback against the queue row's id, the same msg_id
        progresses Q→S→C. This test pins the counter-side of that
        contract; tests/test_rns_bridge.py::TestSynAckCallbackSymmetry
        pins the bridge wiring."""
        c = DeliveryCounters()
        queue_id = "1700000000000-abcd1234"  # shape of message_queue ids
        c.record(DeliveryState.QUEUED, queue_id, protocol="rns")
        c.record(DeliveryState.SENT, queue_id, protocol="rns")
        c.record(DeliveryState.CONFIRMED, queue_id, protocol="rns")
        assert c.snapshot()["confirmation_rate"] == 1.0
        hist = [e.state for e in c.history_for(queue_id)]
        assert DeliveryState.CONFIRMED in hist


# ── Snapshot contract ────────────────────────────────────────────────


class TestSnapshot:
    def test_all_required_keys_present(self):
        c = DeliveryCounters()
        c.record(DeliveryState.QUEUED, "m", protocol="rns")
        snap = c.snapshot()
        for key in (
            "state_totals", "drop_reasons", "state_by_protocol",
            "confirmation_rate", "confirmable_protocols", "unconfirmable_sent",
            "recent", "first_event_ts", "last_event_ts", "ring_capacity",
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


# ── Write-path canary (Issue #63 / reliability backlog #2) ───────────


class TestPreflightHealthy:
    """The startup canary catches the Issue #58 class — sandbox path
    drift, schema corruption, disk full at startup — at the moment of
    construction, before natural traffic flows hours later. Healthy
    case verifies preflight sets `preflight_ok=True` and is visible
    in snapshot.health.
    """

    def test_preflight_runs_at_construction_and_passes(self, tmp_path):
        c = DeliveryCounters(db_path=tmp_path / "h.db")
        assert c._preflight_ok is True
        assert c._preflight_error is None
        assert c._preflight_ts is not None

    def test_health_block_populated_in_snapshot(self, tmp_path):
        c = DeliveryCounters(db_path=tmp_path / "h.db")
        snap = c.snapshot()
        assert "health" in snap, (
            "snapshot must include a health block — operators read this "
            "to confirm the write path works before trusting state_totals"
        )
        health = snap["health"]
        assert health["preflight_ok"] is True
        assert health["preflight_ts"] is not None
        assert health["preflight_error"] is None
        assert health["db_path"] == str(tmp_path / "h.db")
        assert health["consecutive_write_errors"] == 0
        assert health["last_write_error"] is None
        assert health["last_write_error_ts"] is None

    def test_preflight_persists_to_db_for_cross_process_read(self, tmp_path):
        """The map daemon reads `meta.preflight_ts` / `meta.preflight_ok`
        from the SAME DB the gateway wrote. Verify a fresh reader
        instance (with run_preflight=False to avoid overwriting) picks
        up the writer's preflight result."""
        db_path = tmp_path / "x.db"
        writer = DeliveryCounters(db_path=db_path)
        assert writer._preflight_ok is True

        reader = DeliveryCounters(db_path=db_path, run_preflight=False)
        # Reader didn't run its own preflight, so instance state is
        # default-False — BUT the snapshot pulls from the persisted
        # `meta.preflight_*` keys, so health.preflight_ok reads True.
        assert reader._preflight_ok is False  # local default
        health = reader.snapshot()["health"]
        assert health["preflight_ok"] is True, (
            "Reader's snapshot must reflect the writer's persisted "
            "preflight result — that's the cross-process contract "
            "between the gateway (writer) and the map daemon (reader)."
        )
        assert health["preflight_ts"] is not None


class TestPreflightFailureSurfaces:
    """When the write path is broken at construction, preflight must
    log loudly AND make the failure visible via the snapshot — so the
    map daemon's `/api/gateway/delivery` shows "writes are broken"
    without operators having to grep journald for warnings."""

    def test_preflight_failure_logged_at_error_level(
        self, tmp_path, caplog,
    ):
        """The Issue #58 18h-of-silent-warnings class — log loudly so
        the failure is noticed immediately."""
        import sqlite3
        # Construct against a working DB first, then break it by
        # patching _connect to fail. Mirrors a sandbox-tightening
        # mid-run, or a disk-full or read-only-remount.
        c = DeliveryCounters(db_path=tmp_path / "ok.db", run_preflight=False)

        with caplog.at_level("ERROR"):
            original_connect = c._connect

            def broken_connect():
                raise sqlite3.OperationalError("unable to open database file")

            c._connect = broken_connect  # type: ignore[assignment]
            c._run_preflight()
            c._connect = original_connect  # type: ignore[assignment]

        # Error log present and useful — operators reading journalctl
        # see "FAILED" + the actual error, not just a generic message.
        error_records = [
            r for r in caplog.records
            if r.levelname == "ERROR" and "preflight FAILED" in r.message
        ]
        assert len(error_records) == 1, (
            "Preflight failure must log exactly once at ERROR level."
        )
        assert "unable to open database file" in error_records[0].message

    def test_preflight_failure_visible_in_snapshot(self, tmp_path):
        """snapshot.health.preflight_ok must be False after a failed
        preflight — this is the API surface for /api/gateway/delivery."""
        import sqlite3
        c = DeliveryCounters(db_path=tmp_path / "ok.db", run_preflight=False)

        original_connect = c._connect

        def broken_connect():
            raise sqlite3.OperationalError("disk I/O error")

        c._connect = broken_connect  # type: ignore[assignment]
        c._run_preflight()
        c._connect = original_connect  # type: ignore[assignment]

        # Snapshot reads from the DB (which the broken instance never
        # wrote to with preflight_ok=0 — best-effort failed too) AND
        # falls back to instance state. Instance state is now False.
        health = c.snapshot()["health"]
        assert health["preflight_ok"] is False
        assert health["preflight_error"] is not None
        assert "disk I/O error" in health["preflight_error"]


class TestRuntimeWriteErrorTracking:
    """Mid-run write failures (the more insidious cousin of preflight
    failure) must surface too. consecutive_write_errors gives operators
    the "writes are currently broken" signal at API level; recovery
    clears it cleanly."""

    def test_runtime_write_error_increments_counter(self, tmp_path):
        import sqlite3
        c = DeliveryCounters(db_path=tmp_path / "x.db")
        # Healthy at construction.
        assert c.snapshot()["health"]["consecutive_write_errors"] == 0

        # Now break writes mid-run.
        def broken_persist(event):
            raise sqlite3.OperationalError("attempt to write a readonly database")

        c._persist = broken_persist  # type: ignore[assignment]
        c.record(DeliveryState.SENT, "m1", protocol="rns")
        c.record(DeliveryState.SENT, "m2", protocol="rns")
        c.record(DeliveryState.SENT, "m3", protocol="rns")

        health = c.snapshot()["health"]
        assert health["consecutive_write_errors"] == 3
        assert health["last_write_error_ts"] is not None
        assert "readonly database" in (health["last_write_error"] or "")

    def test_runtime_write_recovery_clears_error_state(self, tmp_path):
        """When the underlying issue is fixed (operator chowned the
        file, fleet sync restored the unit, etc.) the next successful
        write must clear the error counters — otherwise operators
        chasing /api/gateway/delivery.health.consecutive_write_errors
        never know things are healthy again."""
        import sqlite3
        c = DeliveryCounters(db_path=tmp_path / "x.db")
        original_persist = c._persist

        def broken_persist(event):
            raise sqlite3.OperationalError("transient I/O")

        c._persist = broken_persist  # type: ignore[assignment]
        c.record(DeliveryState.SENT, "m1", protocol="rns")
        c.record(DeliveryState.SENT, "m2", protocol="rns")
        assert c.snapshot()["health"]["consecutive_write_errors"] == 2

        # Restore + record once → error state must clear.
        c._persist = original_persist  # type: ignore[assignment]
        c.record(DeliveryState.SENT, "m3", protocol="rns")

        health = c.snapshot()["health"]
        assert health["consecutive_write_errors"] == 0
        assert health["last_write_error"] is None
        assert health["last_write_error_ts"] is None

    def test_first_write_error_logged_at_error_level(self, tmp_path, caplog):
        """Throttling: first failure logs ERROR; subsequent failures
        log DEBUG so journald doesn't flood. The
        consecutive_write_errors count keeps the running total."""
        import sqlite3
        c = DeliveryCounters(db_path=tmp_path / "x.db")

        def broken_persist(event):
            raise sqlite3.OperationalError("fail")

        c._persist = broken_persist  # type: ignore[assignment]

        with caplog.at_level("ERROR"):
            c.record(DeliveryState.SENT, "m1", protocol="rns")
            c.record(DeliveryState.SENT, "m2", protocol="rns")
            c.record(DeliveryState.SENT, "m3", protocol="rns")

        error_records = [
            r for r in caplog.records
            if r.levelname == "ERROR" and "DB write FAILED" in r.message
        ]
        assert len(error_records) == 1, (
            f"Expected exactly 1 ERROR log (first failure), got "
            f"{len(error_records)} — throttling regressed and journald "
            f"will flood under sustained write outages."
        )

    def test_recovery_logs_at_info(self, tmp_path, caplog):
        """The "writes are healthy again" event is operator-visible.
        Otherwise an outage's resolution is invisible — the only way
        to confirm recovery would be to keep polling the API."""
        import sqlite3
        c = DeliveryCounters(db_path=tmp_path / "x.db")
        original_persist = c._persist

        def broken_persist(event):
            raise sqlite3.OperationalError("fail")

        c._persist = broken_persist  # type: ignore[assignment]
        c.record(DeliveryState.SENT, "m1", protocol="rns")
        c._persist = original_persist  # type: ignore[assignment]

        with caplog.at_level("INFO"):
            c.record(DeliveryState.SENT, "m2", protocol="rns")

        recovery_records = [
            r for r in caplog.records
            if r.levelname == "INFO" and "write-path recovered" in r.message
        ]
        assert len(recovery_records) == 1
        assert "1 consecutive errors" in recovery_records[0].message


class TestLastSuccessfulWriteTsHeartbeat:
    """The cross-process heartbeat for "writes still flowing." If
    `meta.last_event_ts` is stale relative to expected traffic, the
    write path has been broken since that timestamp — even if the
    process is still running and reporting preflight_ok=True from an
    earlier successful start."""

    def test_last_successful_write_ts_aliases_last_event_ts(self, tmp_path):
        c = DeliveryCounters(db_path=tmp_path / "h.db")
        c.record(DeliveryState.SENT, "m1", protocol="rns")
        snap = c.snapshot()
        assert snap["last_event_ts"] is not None
        assert snap["health"]["last_successful_write_ts"] == snap["last_event_ts"]

    def test_heartbeat_unchanged_when_writes_fail(self, tmp_path):
        """If a write fails, last_event_ts must NOT advance — that's
        what makes it a real heartbeat. Stale heartbeat = silent
        write-path failure even if the process is running."""
        import sqlite3
        c = DeliveryCounters(db_path=tmp_path / "h.db")
        c.record(DeliveryState.SENT, "m1", protocol="rns")
        ts_after_first = c.snapshot()["health"]["last_successful_write_ts"]

        def broken_persist(event):
            raise sqlite3.OperationalError("fail")

        c._persist = broken_persist  # type: ignore[assignment]
        c.record(DeliveryState.SENT, "m2", protocol="rns")

        ts_after_failed = c.snapshot()["health"]["last_successful_write_ts"]
        assert ts_after_failed == ts_after_first, (
            "last_successful_write_ts advanced even though the write "
            "failed — the heartbeat is no longer a real heartbeat."
        )


class TestCrossProcessWriteErrorTruthIssue74:
    """Issue #74: the runtime write-error count must be visible to a
    READER process. Before this fix, `consecutive_write_errors` was
    local-only — the map daemon (which serves snapshot() to the
    watchdog's delivery_write_canary probe) always read 0, so the
    probe's degraded branch could NEVER fire on a mid-run write
    regression. Only the persisted preflight (wedge branch) worked.
    """

    @staticmethod
    def _break_persist(counters):
        import sqlite3 as _sq

        def broken_persist(event):
            raise _sq.OperationalError("attempt to write a readonly database")

        original = counters._persist
        counters._persist = broken_persist  # type: ignore[assignment]
        return original

    def test_reader_snapshot_shows_writer_runtime_errors(self, tmp_path):
        """Two instances, one DB — the cross-process contract."""
        db_path = tmp_path / "x.db"
        writer = DeliveryCounters(db_path=db_path)
        self._break_persist(writer)
        for i in range(3):
            writer.record(DeliveryState.SENT, f"m{i}", protocol="rns")

        reader = DeliveryCounters(db_path=db_path, run_preflight=False)
        health = reader.snapshot()["health"]
        assert health["consecutive_write_errors"] == 3, (
            "Reader must see the writer's persisted write-error count "
            "— that's what makes the watchdog probe's degraded branch "
            "live (it polls the map daemon, not the gateway)."
        )
        assert health["last_write_error_ts"] is not None

    def test_recovery_clears_persisted_state_for_reader(self, tmp_path):
        db_path = tmp_path / "x.db"
        writer = DeliveryCounters(db_path=db_path)
        original = self._break_persist(writer)
        writer.record(DeliveryState.SENT, "m1", protocol="rns")
        writer.record(DeliveryState.SENT, "m2", protocol="rns")
        writer._persist = original  # type: ignore[assignment]
        writer.record(DeliveryState.SENT, "m3", protocol="rns")  # recovery

        reader = DeliveryCounters(db_path=db_path, run_preflight=False)
        health = reader.snapshot()["health"]
        assert health["consecutive_write_errors"] == 0, (
            "Recovery must clear the persisted count too, or the probe "
            "alarms forever on a healed write path."
        )

    def test_writer_local_count_wins_when_db_fully_down(self, tmp_path):
        """When the error-state persist itself fails (DB fully down),
        the WRITER's own snapshot still carries its local count via
        max(local, persisted)."""
        import sqlite3 as _sq
        c = DeliveryCounters(db_path=tmp_path / "x.db")

        def broken_connect():
            raise _sq.OperationalError("unable to open database file")

        c._connect = broken_connect  # type: ignore[assignment]
        c.record(DeliveryState.SENT, "m1", protocol="rns")
        c.record(DeliveryState.SENT, "m2", protocol="rns")
        health = c.snapshot()["health"]
        assert health["consecutive_write_errors"] == 2


# ── Honest confirmation view (Issue #74 display fix, 2026-06-15) ──────


class TestComputeConfirmationView:
    """The pure helper behind the snapshot's honest confirmation fields."""

    def test_live_cross_population_shape_no_longer_exceeds_one(self):
        """The exact live shape that read 1.64: confirmed dominated by RNS
        proofs, sent dominated by unconfirmable mesh. Honest rate is bounded
        and the mesh sends are surfaced separately."""
        view = dc.compute_confirmation_view(
            state_totals={"sent": 3390, "confirmed": 5566, "dropped": 1112},
            state_by_protocol={
                "confirmed": {"rns": 5566},
                "sent": {"meshtastic": 2423, "secondary": 267,
                         "primary": 570, "rns": 130},
            },
            drop_reasons={"dedup": 1070, "rns_delivery_failed": 32,
                          "retries_exhausted": 10},
        )
        assert view["confirmation_rate"] is not None
        assert 0.0 <= view["confirmation_rate"] <= 1.0
        assert view["confirmation_rate"] == 5566 / (5566 + 42)
        assert view["confirmable_protocols"] == ["rns"]
        # meshtastic + secondary + primary sent; rns excluded (confirmable).
        assert view["unconfirmable_sent"] == 2423 + 267 + 570

    def test_none_when_no_confirmable_terminal_outcomes(self):
        view = dc.compute_confirmation_view(
            state_totals={"sent": 5},
            state_by_protocol={"sent": {"meshtastic": 5}},
            drop_reasons={"dedup": 3},
        )
        assert view["confirmation_rate"] is None
        assert view["unconfirmable_sent"] == 5
        assert view["confirmable_protocols"] == []

    def test_benign_drops_never_count_as_failures(self):
        """dedup / queue_pressure / queue_shed / invalid_payload are not
        delivery failures — they must not drag the rate."""
        view = dc.compute_confirmation_view(
            state_totals={"confirmed": 4},
            state_by_protocol={"confirmed": {"rns": 4}},
            drop_reasons={"dedup": 100, "queue_pressure": 50,
                          "queue_shed": 20, "invalid_payload": 5},
        )
        assert view["confirmation_rate"] == 1.0

    def test_tolerates_empty_and_missing_inputs(self):
        view = dc.compute_confirmation_view({}, {}, {})
        assert view == {"confirmation_rate": None,
                        "confirmable_protocols": [],
                        "unconfirmable_sent": 0}


class TestDeliveryFailureReasonsParity:
    """honest_failure_modes #5: the failure-reason set is defined twice (the
    watchdog can't import this module — gateway/__init__ pulls RNS into the
    sandboxed probe loop) and MUST stay byte-identical. Pinned here."""

    def test_matches_watchdog_set(self):
        from utils.watchdog_probes_gateway import _DELIVERY_FAILURE_REASONS
        assert dc.DELIVERY_FAILURE_REASONS == _DELIVERY_FAILURE_REASONS

    def test_all_members_are_real_drop_reasons(self):
        valid = {r.value for r in DropReason}
        assert dc.DELIVERY_FAILURE_REASONS <= valid


class TestContentIdRecipientSubstrate:
    """STEP 4a-i (dedup/identity arc): events carry content_id + recipient so
    the dup/miss detector can later key on (content_id, recipient) — a
    content_id alone false-alarms on the by-design M→R fan-out."""

    def test_record_persists_content_id_and_recipient(self):
        c = DeliveryCounters()
        c.record(DeliveryState.CONFIRMED, "m1", protocol="rns",
                 content_id="c1:abc", recipient="6b1a0120")
        ev = c.snapshot()["recent"][-1]
        assert ev["content_id"] == "c1:abc"
        assert ev["recipient"] == "6b1a0120"

    def test_defaults_empty_when_not_supplied(self):
        c = DeliveryCounters()
        c.record(DeliveryState.SENT, "m2", protocol="rns")
        ev = c.snapshot()["recent"][-1]
        assert ev.get("content_id", "") == ""
        assert ev.get("recipient", "") == ""

    def test_migration_adds_columns_to_legacy_events_table(self, tmp_path):
        # A pre-existing fleet DB has the old events schema; _init_db must
        # ALTER-ADD the columns without error (guarded, additive).
        import sqlite3
        db = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE events (ts REAL NOT NULL, id TEXT NOT NULL DEFAULT '',"
            " state TEXT NOT NULL, protocol TEXT, drop_reason TEXT,"
            " note TEXT NOT NULL DEFAULT '')")
        conn.execute("CREATE TABLE counters (key TEXT PRIMARY KEY,"
                     " value INTEGER NOT NULL DEFAULT 0)")
        conn.commit()
        conn.close()
        c = DeliveryCounters(db_path=db, run_preflight=False)
        cols = {r[1] for r in
                sqlite3.connect(str(db)).execute("PRAGMA table_info(events)")}
        assert "content_id" in cols and "recipient" in cols
        c.record(DeliveryState.CONFIRMED, "m3", content_id="c1:z", recipient="rr")
        ev = c.snapshot()["recent"][-1]
        assert ev["content_id"] == "c1:z" and ev["recipient"] == "rr"


class TestComputeContentIdView:
    """STEP 4b (dedup/identity arc): the pure aggregation behind the snapshot's
    ``content_id_view`` block — the FIRST consumer of the content_id substrate.
    Keys on (content_id, recipient); measure-only."""

    CONF = DeliveryState.CONFIRMED.value
    SENT = DeliveryState.SENT.value

    def test_empty_rows_is_all_zero_not_truncated(self):
        view = dc.compute_content_id_view([])
        assert view["tracked_events"] == 0
        assert view["untracked_events"] == 0
        assert view["unattributed_events"] == 0
        assert view["tracked_pairs"] == 0
        assert view["confirmed_pairs"] == 0
        assert view["local_duplicate_pairs"] == 0
        assert view["local_duplicate_deliveries"] == 0
        assert view["confirmed"] == []
        assert view["confirmed_truncated"] is False

    def test_local_duplicate_detected(self):
        """The SAME (content_id, recipient) CONFIRMED twice = a real local
        double-delivery (the per-box dup signal)."""
        view = dc.compute_content_id_view([
            ("c1:aa", "6b1a0120", self.CONF, 2),
        ])
        assert view["tracked_pairs"] == 1
        assert view["confirmed_pairs"] == 1
        assert view["local_duplicate_pairs"] == 1
        assert view["local_duplicate_deliveries"] == 1  # excess = 2 - 1
        assert view["confirmed"] == [
            {"content_id": "c1:aa", "recipient": "6b1a0120", "confirmed": 2}]

    def test_fanout_to_distinct_recipients_is_not_a_local_dup(self):
        """The by-design M→R fan-out confirms one copy per DISTINCT recipient.
        Same content_id, two recipients, each confirmed once → 2 confirmed
        pairs, ZERO local dups. (The cross-gateway dup-A is 4c's job.)"""
        view = dc.compute_content_id_view([
            ("c1:aa", "6b1a0120", self.CONF, 1),
            ("c1:aa", "7cda0fab", self.CONF, 1),
        ])
        assert view["confirmed_pairs"] == 2
        assert view["local_duplicate_pairs"] == 0
        assert view["local_duplicate_deliveries"] == 0

    def test_untracked_events_surfaced_not_hidden(self):
        """Events with NO content_id (the mesh-leg blind spot) are counted as
        untracked, never folded into a clean zero (honest_failure_modes #2)."""
        view = dc.compute_content_id_view([
            ("", "", self.SENT, 17),
            ("c1:aa", "6b1a0120", self.CONF, 1),
        ])
        assert view["untracked_events"] == 17
        assert view["tracked_events"] == 1
        assert view["tracked_pairs"] == 1

    def test_unattributed_recipient_is_not_a_pair(self):
        """content_id known but recipient empty cannot key a per-recipient dup;
        an empty recipient must never masquerade as a real pair/dup."""
        view = dc.compute_content_id_view([
            ("c1:aa", "", self.CONF, 3),
        ])
        assert view["unattributed_events"] == 3
        assert view["tracked_events"] == 3
        assert view["tracked_pairs"] == 0
        assert view["confirmed_pairs"] == 0
        assert view["local_duplicate_pairs"] == 0

    def test_non_confirmed_pair_tracked_but_not_confirmed(self):
        view = dc.compute_content_id_view([
            ("c1:bb", "rr", self.SENT, 1),
        ])
        assert view["tracked_pairs"] == 1
        assert view["confirmed_pairs"] == 0
        assert view["confirmed"] == []

    def test_confirmed_list_sorted_desc_and_truncated_honestly(self):
        """Pairs sort confirmed-count DESC so a local dup is never truncated
        away; the cap surfaces the drop (no silent cap, #9)."""
        rows = [(f"c1:{i:03d}", "rr", self.CONF, 1) for i in range(5)]
        rows.append(("c1:dup", "rr", self.CONF, 3))  # the dup — must survive
        view = dc.compute_content_id_view(rows, pair_cap=2)
        assert view["confirmed_pairs"] == 6
        assert view["confirmed_truncated"] is True
        assert len(view["confirmed"]) == 2
        # highest-count pair (the dup) sorts first and is retained
        assert view["confirmed"][0] == {
            "content_id": "c1:dup", "recipient": "rr", "confirmed": 3}
        assert view["local_duplicate_pairs"] == 1
        assert view["local_duplicate_deliveries"] == 2

    def test_tolerates_none_and_bad_counts(self):
        view = dc.compute_content_id_view([
            (None, None, self.SENT, None),
            ("c1:aa", "rr", self.CONF, 1),
        ])
        # (None, None) → treated as untracked with 0 count; no crash
        assert view["confirmed_pairs"] == 1


class TestSnapshotContentIdViewStep4b:
    """STEP 4b integration: snapshot exposes content_id_view at the
    /api/gateway/delivery boundary (additive, measure-only)."""

    def test_snapshot_exposes_content_id_view(self):
        snap = DeliveryCounters().snapshot()
        assert "content_id_view" in snap
        v = snap["content_id_view"]
        for k in ("tracked_events", "untracked_events", "unattributed_events",
                  "tracked_pairs", "confirmed_pairs", "local_duplicate_pairs",
                  "local_duplicate_deliveries", "confirmed", "confirmed_truncated"):
            assert k in v

    def test_local_double_delivery_shows_in_view(self):
        c = DeliveryCounters()
        # same logical message confirmed twice to the same recipient
        c.record(DeliveryState.CONFIRMED, "m1", protocol="rns",
                 content_id="c1:dup", recipient="6b1a0120")
        c.record(DeliveryState.CONFIRMED, "m2", protocol="rns",
                 content_id="c1:dup", recipient="6b1a0120")
        v = c.snapshot()["content_id_view"]
        assert v["local_duplicate_pairs"] == 1
        assert v["local_duplicate_deliveries"] == 1
        assert v["confirmed"][0]["content_id"] == "c1:dup"

    def test_plain_events_count_as_untracked(self):
        c = DeliveryCounters()
        c.record(DeliveryState.SENT, "m", protocol="meshtastic")
        v = c.snapshot()["content_id_view"]
        assert v["untracked_events"] == 1
        assert v["tracked_pairs"] == 0

    def test_view_is_json_serializable(self):
        c = DeliveryCounters()
        c.record(DeliveryState.CONFIRMED, "m1", protocol="rns",
                 content_id="c1:aa", recipient="rr")
        json.dumps(c.snapshot()["content_id_view"])


class TestContentIdViewStateProducerStep4c:
    """STEP 4c producer: each gateway box PUBLISHES its content_id_view to
    an operator-owned state file so the cross-box JOIN can ssh-collect it.

    This is the ONLY reachable transport for a gateway-only box (moc3
    serves no HTTP — the moc3 hardware constraint), so the producer must
    run as the operator (the gateway process owns the DB; root can't
    safely open the operator's WAL DB) and be best-effort (a publish
    failure must never touch the bridge)."""

    def test_build_state_wraps_view_with_schema_host_ts(self):
        view = {"tracked_events": 3, "confirmed": []}
        st = dc.build_content_id_view_state(view, host="moc3", now=123.5)
        assert st["schema"] == dc.CONTENT_ID_VIEW_STATE_SCHEMA
        assert st["host"] == "moc3"
        assert st["ts"] == 123.5
        assert st["content_id_view"] is view

    def test_build_state_defaults_host_and_ts(self, monkeypatch):
        monkeypatch.setattr(dc.socket, "gethostname", lambda: "boxX")
        monkeypatch.setattr(dc.time, "time", lambda: 999.0)
        st = dc.build_content_id_view_state({})
        assert st["host"] == "boxX"
        assert st["ts"] == 999.0

    def test_build_state_carries_unconfirmable_sent(self):
        st = dc.build_content_id_view_state({}, unconfirmable_sent=42)
        assert st["unconfirmable_sent"] == 42

    def test_build_state_unconfirmable_sent_defaults_zero_on_garbage(self):
        st = dc.build_content_id_view_state({}, unconfirmable_sent="nope")
        assert st["unconfirmable_sent"] == 0
        # bool must not slip through as 1
        st2 = dc.build_content_id_view_state({}, unconfirmable_sent=True)
        assert st2["unconfirmable_sent"] == 0

    def test_write_publishes_unconfirmable_sent_from_snapshot(self, tmp_path):
        # An unconfirmable mesh send bumps the top-level counter; the
        # published envelope must carry it (the JOIN's blind-spot line).
        dc.record(DeliveryState.SENT, "m1", protocol="meshtastic")
        target = tmp_path / "v.json"
        assert dc.write_content_id_view_state(path=target) is True
        payload = json.loads(target.read_text())
        assert payload["unconfirmable_sent"] >= 1

    def test_state_path_is_in_share_dir_next_to_db(self, monkeypatch):
        # Co-located with the DB so it lands inside the gateway sandbox's
        # ReadWritePaths (Issue #60 — ~/.local/state is read-only there).
        monkeypatch.delenv("MESHFORGE_CONTENT_ID_VIEW_STATE", raising=False)
        monkeypatch.delenv("MESHFORGE_DELIVERY_COUNTERS_DB", raising=False)
        monkeypatch.setattr(dc, "get_real_user_home",
                            lambda: dc.Path("/home/op"))
        p = dc.content_id_view_state_path()
        assert str(p) == "/home/op/.local/share/meshforge/content_id_view.json"
        assert p.parent == dc.default_db_path().parent  # shared sandbox dir

    def test_state_path_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("MESHFORGE_CONTENT_ID_VIEW_STATE", "/srv/cid.json")
        assert str(dc.content_id_view_state_path()) == "/srv/cid.json"

    def test_write_publishes_singleton_view(self, tmp_path):
        # The _reset_singleton fixture points the singleton DB at tmp_path.
        dc.record(DeliveryState.CONFIRMED, "m1", protocol="rns",
                  content_id="c1:aa", recipient="6b1a0120")
        target = tmp_path / "content_id_view.json"
        assert dc.write_content_id_view_state(path=target, host="moc3",
                                              now=1.0) is True
        payload = json.loads(target.read_text())
        assert payload["host"] == "moc3"
        assert payload["ts"] == 1.0
        assert payload["schema"] == dc.CONTENT_ID_VIEW_STATE_SCHEMA
        v = payload["content_id_view"]
        assert v["confirmed"][0]["content_id"] == "c1:aa"
        assert v["confirmed"][0]["recipient"] == "6b1a0120"

    def test_write_reflects_local_double_delivery(self, tmp_path):
        for mid in ("m1", "m2"):
            dc.record(DeliveryState.CONFIRMED, mid, protocol="rns",
                      content_id="c1:dup", recipient="6b1a0120")
        target = tmp_path / "v.json"
        assert dc.write_content_id_view_state(path=target) is True
        v = json.loads(target.read_text())["content_id_view"]
        assert v["local_duplicate_pairs"] == 1

    def test_write_accepts_explicit_view_without_singleton(self, tmp_path):
        target = tmp_path / "v.json"
        ok = dc.write_content_id_view_state(
            view={"tracked_events": 0, "confirmed": []},
            path=target, host="h", now=2.0)
        assert ok is True
        assert json.loads(target.read_text())["content_id_view"][
            "tracked_events"] == 0

    def test_write_is_best_effort_returns_false_on_bad_path(self, tmp_path):
        # Parent is a regular FILE → mkdir(parents=True) raises; the
        # writer swallows it and reports False (never raises).
        blocker = tmp_path / "afile"
        blocker.write_text("x")
        bad = blocker / "sub" / "content_id_view.json"
        assert dc.write_content_id_view_state(
            view={"confirmed": []}, path=bad) is False

    def test_write_returns_false_when_view_unavailable(self, tmp_path):
        # A non-dict view (e.g. snapshot returned no block) is not published.
        assert dc.write_content_id_view_state(
            view=None, path=tmp_path / "v.json") in (True, False)
        ok = dc.write_content_id_view_state(
            view="not-a-dict", path=tmp_path / "v2.json")  # type: ignore[arg-type]
        assert ok is False
        assert not (tmp_path / "v2.json").exists()
