"""BIC-2 slice 1 — per-leg delivery counts over 15 min / 1 h / 24 h.

A leg is ``<source>><destination>`` transport (operator 2026-10-23:
"transport + direction"). Counts live in 15-minute buckets inside the
existing delivery_counters.db so a gateway restart does not blind them
(the DET-DCS class). Observe before alarm: these are counts only.

Seam: ``DeliveryCounters.record(..., source=...)`` → ``snapshot()["legs"]``.
"""
from __future__ import annotations

import time

import pytest

from gateway import delivery_counters as dc
from gateway.delivery_counters import DeliveryCounters, DeliveryState


@pytest.fixture
def counters(tmp_path):
    return DeliveryCounters(db_path=tmp_path / "dc.db", run_preflight=False)


def test_sent_on_a_leg_is_counted_in_every_window(counters):
    now = time.time()
    counters.record(DeliveryState.SENT, "m1", protocol="rns",
                    source="meshtastic", ts=now - 60)

    leg = counters.snapshot()["legs"]["meshtastic>rns"]

    assert leg["15m"]["sent"] == 1
    assert leg["1h"]["sent"] == 1
    assert leg["24h"]["sent"] == 1


def test_a_caller_that_names_no_source_is_counted_under_question_mark(counters):
    counters.record(DeliveryState.SENT, "m1", protocol="rns")

    legs = counters.snapshot()["legs"]

    assert "?>rns" in legs
    assert not any(k.endswith(">rns") and k != "?>rns" for k in legs)


def test_window_edges_two_hours_ago_is_only_in_24h(counters):
    now = time.time()
    counters.record(DeliveryState.SENT, "old", protocol="rns",
                    source="meshtastic", ts=now - 2 * 3600)

    leg = counters.snapshot()["legs"]["meshtastic>rns"]

    assert "15m" not in leg      # absent, not a zero
    assert "1h" not in leg
    assert leg["24h"]["sent"] == 1


def test_older_than_24h_is_in_no_window(counters):
    now = time.time()
    counters.record(DeliveryState.SENT, "ancient", protocol="rns",
                    source="meshtastic", ts=now - 3 * 86400)
    counters.record(DeliveryState.SENT, "fresh", protocol="meshtastic",
                    source="rns", ts=now)

    legs = counters.snapshot()["legs"]

    assert "meshtastic>rns" not in legs
    assert legs["rns>meshtastic"]["15m"]["sent"] == 1


def test_counts_survive_a_restart(tmp_path):
    db = tmp_path / "dc.db"
    DeliveryCounters(db_path=db, run_preflight=False).record(
        DeliveryState.CONFIRMED, "m1", protocol="rns", source="meshtastic")

    reopened = DeliveryCounters(db_path=db, run_preflight=False)

    assert reopened.snapshot()["legs"]["meshtastic>rns"]["15m"]["confirmed"] == 1


def test_delivery_failures_and_benign_drops_are_separate(counters):
    counters.record(DeliveryState.DROPPED, "f", protocol="rns",
                    source="meshtastic",
                    drop_reason=dc.DropReason.RETRIES_EXHAUSTED)
    counters.record(DeliveryState.DROPPED, "d", protocol="rns",
                    source="meshtastic", drop_reason=dc.DropReason.DEDUP)

    win = counters.snapshot()["legs"]["meshtastic>rns"]["15m"]

    assert win["failed"] == 1
    assert win["dropped_other"] == 1


def test_a_lane_is_counted_under_its_transport(counters):
    counters.record(DeliveryState.SENT, "m1", protocol="secondary",
                    source="rns")

    assert counters.snapshot()["legs"]["rns>meshtastic"]["15m"]["sent"] == 1


def test_an_unreadable_db_reads_unobservable_not_quiet(counters, monkeypatch):
    import sqlite3

    def boom():
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(counters, "_connect", boom)

    assert counters.snapshot()["legs"] is None


def test_a_lane_named_as_source_is_counted_under_its_transport(counters):
    counters.record(DeliveryState.SENT, "m1", protocol="rns",
                    source="secondary")

    assert "meshtastic>rns" in counters.snapshot()["legs"]


def test_tracking_start_is_stamped_by_the_first_leg_write_not_by_a_reader(
        tmp_path):
    # Review R5: the map daemon (a reader) opening the file first must not
    # start the clock while an old gateway writes no leg counts at all.
    db = tmp_path / "dc.db"
    reader = DeliveryCounters(db_path=db, run_preflight=False).snapshot()
    assert reader["legs_since_ts"] is None
    assert {"15m", "1h", "24h"} <= set(reader["legs_partial_windows"])

    before = time.time()
    DeliveryCounters(db_path=db, run_preflight=False).record(
        DeliveryState.SENT, "m1", protocol="rns", source="meshtastic")
    since = DeliveryCounters(db_path=db, run_preflight=False).snapshot()[
        "legs_since_ts"]
    assert before - 1 <= since <= time.time() + 1

    DeliveryCounters(db_path=db, run_preflight=False).record(
        DeliveryState.SENT, "m2", protocol="rns", source="meshtastic")
    assert DeliveryCounters(db_path=db, run_preflight=False).snapshot()[
        "legs_since_ts"] == since


def test_windows_longer_than_tracking_are_named_partial(counters):
    # A fresh DB: 15m may be whole, 1h and 24h cannot be.
    assert {"1h", "24h"} <= set(counters.snapshot()["legs_partial_windows"])


# ── seam 2: the direct RNS send records SENT on its leg ───────────────
# Before BIC-2 only the queue path recorded SENT, so on moc (10-23) the
# lifetime RNS `confirmed` (37,626) ran 4.6x RNS `sent` (8,081): every
# direct send confirmed without ever being counted as sent.

from unittest.mock import MagicMock, patch  # noqa: E402

from tests.test_rns_bridge import bridge  # noqa: E402,F401 — fixture
from tests.test_rns_bridge import TestSynAckCallbackSymmetry as _Sym  # noqa: E402


@pytest.fixture
def singleton_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHFORGE_DELIVERY_COUNTERS_DB", str(tmp_path / "s.db"))
    dc._reset_singleton_for_tests()
    yield
    dc._reset_singleton_for_tests()


def _send(bridge, outbound_error=None, **kw):
    import sys
    from utils.tx_guard import allow_rns_egress
    fake_rns, fake_lxmf, _ = _Sym._fake_rns_lxmf_modules()
    _Sym()._prime_bridge_for_send(bridge)
    if outbound_error is not None:
        bridge._lxmf_router.handle_outbound.side_effect = outbound_error
    with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}), \
            allow_rns_egress():
        return bridge.send_to_rns("hello", b"\xab" * 16, **kw)


def test_direct_rns_send_records_sent_on_its_leg(bridge, singleton_db):
    assert _send(bridge, source="meshtastic")

    legs = dc.get_singleton().snapshot()["legs"]

    assert legs["meshtastic>rns"]["15m"]["sent"] == 1


def test_direct_rns_send_without_a_source_is_counted_under_question_mark(
        bridge, singleton_db):
    assert _send(bridge)

    assert dc.get_singleton().snapshot()["legs"]["?>rns"]["15m"]["sent"] == 1


def test_a_failed_direct_rns_send_records_no_sent(bridge, singleton_db):
    result = _send(bridge, outbound_error=RuntimeError("rnsd gone"),
                   source="meshtastic")

    assert not result
    assert "meshtastic>rns" not in (dc.get_singleton().snapshot()["legs"] or {})


def test_a_counter_error_never_turns_a_sent_message_into_a_failure(
        bridge, singleton_db, monkeypatch):
    # The message is already handed to LXMF; reporting failure would
    # requeue it and put a duplicate on the air.
    def boom(*a, **k):
        raise RuntimeError("counter broke")
    monkeypatch.setattr(dc, "record", boom)

    result = _send(bridge, source="meshtastic")

    assert result
    assert bridge.stats["delivery_record_errors"] == 1


# ── seam 3: every gateway caller of send_to_rns names its leg ─────────

# Callers that genuinely cannot know the origin. Each one counts under
# ``?>rns`` — visible, never guessed. Adding to this list is a decision.
_SOURCELESS_SEND_TO_RNS = {
    # enqueue_message()'s no-queue fallback: a generic entry point whose
    # callers pass no origin.
    ("bridge_send_mixin.py", "enqueue_message"),
}


def test_every_gateway_send_to_rns_call_names_a_source():
    import ast
    from pathlib import Path

    gw = Path(__file__).resolve().parents[1] / "src" / "gateway"
    missing = []
    for path in sorted(gw.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "send_to_rns"
                        and not any(k.arg == "source" for k in node.keywords)
                        and (path.name, fn.name) not in _SOURCELESS_SEND_TO_RNS):
                    missing.append(f"{path.name}:{node.lineno} in {fn.name}()")
    assert not missing, (
        "send_to_rns() without source= counts the message under '?>rns'. "
        "Pass the origin transport, or add the caller to "
        "_SOURCELESS_SEND_TO_RNS with the reason it cannot know: "
        + ", ".join(sorted(set(missing))))


def test_a_mesh_broadcast_fanned_to_rns_lands_on_the_meshtastic_leg(
        bridge, singleton_db):
    # The consumer path end to end: xform → real send_to_rns → counters.
    import sys
    from gateway.rns_bridge import BridgedMessage
    from utils.tx_guard import allow_rns_egress
    bridge.config.rns.get_lxmf_destinations.return_value = [
        "522c4ac1d2f9964e03e3782ef5b0224c",
        "d1df31d352ede66eac819a577da22b75",
    ]
    msg = BridgedMessage(
        source_network="meshtastic", source_id="!aabb0042",
        destination_id=None, content="hello fleet", is_broadcast=True,
    )
    fake_rns, fake_lxmf, _ = _Sym._fake_rns_lxmf_modules()
    _Sym()._prime_bridge_for_send(bridge)
    with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}), \
            allow_rns_egress():
        bridge._process_mesh_to_rns(msg)

    legs = dc.get_singleton().snapshot()["legs"]

    assert legs["meshtastic>rns"]["15m"]["sent"] == 2   # one per recipient
    assert "?>rns" not in legs


def test_every_state_and_drop_reason_maps_into_the_closed_outcome_set():
    # hfm #7: a new DeliveryState or DropReason must land in OUTCOMES, not
    # invent a bucket word no reader knows.
    from gateway.delivery_counters import DeliveryEvent, DropReason, _leg_outcome
    from gateway.delivery_legs import OUTCOMES

    for state in DeliveryState:
        reasons = list(DropReason) if state == DeliveryState.DROPPED else [None]
        for reason in reasons:
            ev = DeliveryEvent(ts=0.0, id="x", state=state, drop_reason=reason)
            assert _leg_outcome(ev) in OUTCOMES, (state, reason)



# ── review round 1 (non-author, 10-23) ────────────────────────────────

def _fire(bridge, which, **send_kw):
    """Send directly, then fire the LXMF delivered/failed callback."""
    import sys
    from utils.tx_guard import allow_rns_egress
    fake_rns, fake_lxmf, fake_lxm = _Sym._fake_rns_lxmf_modules()
    _Sym()._prime_bridge_for_send(bridge)
    with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}), \
            allow_rns_egress():
        assert bridge.send_to_rns("hello", b"\xab" * 16, **send_kw)
    reg = (fake_lxm.register_delivery_callback if which == "delivered"
           else fake_lxm.register_failed_callback)
    reg.call_args[0][0](MagicMock(name="receipt"))


def test_a_confirmation_lands_on_the_same_leg_as_its_send(bridge, singleton_db):
    # R1: CONFIRMED went to ?>rns while SENT went to meshtastic>rns.
    _fire(bridge, "delivered", source="meshtastic")

    win = dc.get_singleton().snapshot()["legs"]["meshtastic>rns"]["15m"]

    assert win["sent"] == 1 and win["confirmed"] == 1


def test_a_delivery_failure_lands_on_the_same_leg_as_its_send(
        bridge, singleton_db):
    _fire(bridge, "failed", source="meshtastic")

    legs = dc.get_singleton().snapshot()["legs"]

    assert legs["meshtastic>rns"]["15m"]["failed"] == 1
    assert "?>rns" not in legs


def test_a_broken_leg_table_does_not_blind_the_core_snapshot(tmp_path):
    # R6: legs are additive and measure-only; a read error there must not
    # blank the counters, the confirmation view or the health block.
    db = tmp_path / "dc.db"
    c = DeliveryCounters(db_path=db, run_preflight=False)
    c.record(DeliveryState.SENT, "m1", protocol="rns", source="meshtastic")
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE leg_buckets")

    snap = c.snapshot()

    assert snap["legs"] is None
    assert snap["state_totals"]["sent"] == 1
    assert not snap["health"].get("db_unobservable")


def test_a_leg_write_error_keeps_the_core_event_and_leaves_a_witness(
        counters, monkeypatch):
    # R7: a leg-bookkeeping failure must not roll back core accounting.
    import sqlite3
    from gateway import delivery_legs

    def boom(*a, **k):
        raise sqlite3.OperationalError("leg table broke")
    monkeypatch.setattr(delivery_legs, "bump", boom)

    counters.record(DeliveryState.SENT, "m1", protocol="rns",
                    source="meshtastic")
    snap = counters.snapshot()

    assert snap["state_totals"]["sent"] == 1
    assert snap["legs_write_errors"] == 1


def test_an_events_source_is_persisted_for_the_per_message_trace(tmp_path):
    # R8: to_dict() serialised source but the events table dropped it.
    db = tmp_path / "dc.db"
    DeliveryCounters(db_path=db, run_preflight=False).record(
        DeliveryState.SENT, "m1", protocol="rns", source="meshtastic")

    recent = DeliveryCounters(db_path=db, run_preflight=False).snapshot()[
        "recent"]

    assert recent[0]["source"] == "meshtastic"


def test_the_15m_window_does_not_reach_back_20_minutes(counters):
    # R9: 15-min buckets let "15m" sum up to 30 min of traffic, so a
    # killed leg kept showing sends for half an hour.
    counters.record(DeliveryState.SENT, "m1", protocol="rns",
                    source="meshtastic", ts=time.time() - 20 * 60)

    leg = counters.snapshot()["legs"]["meshtastic>rns"]

    assert "15m" not in leg
    assert leg["1h"]["sent"] == 1


@pytest.mark.parametrize("source", ["Meshtastic", "mqtt_bridge", "radio"])
def test_a_source_outside_the_transport_vocabulary_counts_as_unknown(
        counters, source):
    # R10 / hfm #7: a typo must not mint a new leg nobody reads.
    counters.record(DeliveryState.SENT, "m1", protocol="rns", source=source)

    assert set(counters.snapshot()["legs"]) == {"?>rns"}


# ── review round 2 (non-author re-review of daa93e19, 10-23) ──────────

def test_a_half_done_leg_write_is_undone_whole(counters, monkeypatch):
    # F2: bump() is three statements; a failure partway must not leave a
    # counted bucket beside a witness that says it was not counted.
    import sqlite3
    from gateway import delivery_legs
    real = delivery_legs.bump

    def half(conn, ts, leg, outcome):
        real(conn, ts, leg, outcome)
        raise sqlite3.OperationalError("prune failed")
    monkeypatch.setattr(delivery_legs, "bump", half)

    counters.record(DeliveryState.SENT, "m1", protocol="rns",
                    source="meshtastic")
    snap = counters.snapshot()

    assert snap["state_totals"]["sent"] == 1          # core kept
    assert "meshtastic>rns" not in snap["legs"]        # leg undone whole
    assert snap["legs_since_ts"] is None               # stamp undone too
    assert snap["legs_write_errors"] == 1


def test_a_leg_error_that_rolled_back_the_core_write_is_a_core_write_error(
        counters, monkeypatch):
    # F1: SQLITE_FULL/IOERR roll the WHOLE transaction back. The core event
    # is then gone, and record() must say so — not report success.
    import sqlite3
    from gateway import delivery_legs

    def full(conn, ts, leg, outcome):
        conn.execute("ROLLBACK")       # what SQLite does on SQLITE_FULL
        raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(delivery_legs, "bump", full)

    counters.record(DeliveryState.SENT, "m1", protocol="rns",
                    source="meshtastic")
    snap = counters.snapshot()

    assert snap["health"]["consecutive_write_errors"] >= 1


def test_tracking_start_is_the_write_time_not_a_backdated_event_ts(counters):
    # F6: a replayed or clock-stepped first event must not pull since_ts
    # back and make an uncovered span read whole.
    before = time.time()
    counters.record(DeliveryState.SENT, "m1", protocol="rns",
                    source="meshtastic", ts=before - 3600)

    assert counters.snapshot()["legs_since_ts"] >= before - 1


def test_history_for_carries_the_source(counters):
    # F4: the per-message trace dropped the persisted source.
    counters.record(DeliveryState.SENT, "m1", protocol="rns",
                    source="meshtastic")

    assert [e.source for e in counters.history_for("m1")] == ["meshtastic"]


def test_a_leg_write_error_carries_when_it_last_happened(counters, monkeypatch):
    # F9: a lifetime count alone cannot tell "broke once" from "broken now".
    import sqlite3
    from gateway import delivery_legs

    def boom(*a, **k):
        raise sqlite3.OperationalError("leg table broke")
    monkeypatch.setattr(delivery_legs, "bump", boom)
    before = time.time()

    counters.record(DeliveryState.SENT, "m1", protocol="rns",
                    source="meshtastic")

    assert counters.snapshot()["legs_last_write_error_ts"] >= before - 1


def test_reset_for_tests_clears_the_leg_tables(counters):
    # F10: since_ts now comes from the first bump; a reset that kept it
    # would leak one test's legs into the next.
    counters.record(DeliveryState.SENT, "m1", protocol="rns",
                    source="meshtastic")

    counters._reset_for_tests()
    snap = counters.snapshot()

    assert snap["legs"] == {} and snap["legs_since_ts"] is None
