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


def test_tracking_start_is_stamped_once_and_survives_a_restart(tmp_path):
    db = tmp_path / "dc.db"
    before = time.time()
    first = DeliveryCounters(db_path=db, run_preflight=False).snapshot()
    since = first["legs_since_ts"]
    assert before - 1 <= since <= time.time() + 1

    later = DeliveryCounters(db_path=db, run_preflight=False).snapshot()

    assert later["legs_since_ts"] == since


def test_windows_longer_than_tracking_are_named_partial(counters):
    # A fresh DB: 15m may be whole, 1h and 24h cannot be.
    assert {"1h", "24h"} <= set(counters.snapshot()["legs_partial_windows"])


# ── seam 2: the direct RNS send records SENT on its leg ───────────────
# Before BIC-2 only the queue path recorded SENT, so on moc (10-23) the
# lifetime RNS `confirmed` (37,626) ran 4.6x RNS `sent` (8,081): every
# direct send confirmed without ever being counted as sent.

from unittest.mock import patch  # noqa: E402

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
