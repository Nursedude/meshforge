"""Tests for the gateway round-trip canary (src/lab/gateway_rt_canary.py).

The load-bearing property: the canary's SQL predicates are pinned against
DBs created by the REAL producer classes (PersistentMessageQueue,
DeliveryCounters) — if either schema drifts, these tests break instead of
the canary silently mis-reading production. Verdict classification is the
honest-failure-modes surface: every degraded path must map to a DISTINCT
reason, and unobservable (DB error) must never read as OK or as a vague
timeout.
"""

import sys
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from lab.gateway_rt_canary import (  # noqa: E402
    CanaryResult,
    EXIT_CONCERN,
    EXIT_FAIL,
    EXIT_OK,
    ack_like_pattern,
    classify,
    poll_ack_row,
    poll_counters_terminal,
    resolve_target,
)
from lab.lxmf_tracer import parse_peers_file  # noqa: E402
from lab._lab_common import make_ping_body  # noqa: E402


# ---------------------------------------------------------------- peers


# Synthetic names + hashes only (MF014 + never commit live lab_peers
# hashes — hash-obscurity v1 means the real ones stay off public GitHub).
PEERS_TEXT = """\
# comment line
box-a=00112233445566778899aabbccddeeff
echo-peer=ffeeddccbbaa99887766554433221100
"""


class TestResolveTarget:
    def test_resolves_known_peer(self):
        peers = parse_peers_file(PEERS_TEXT)
        assert resolve_target("echo-peer", peers) == (
            "ffeeddccbbaa99887766554433221100")

    def test_unknown_peer_is_none(self):
        peers = parse_peers_file(PEERS_TEXT)
        assert resolve_target("nope", peers) is None

    def test_empty_peers_is_none(self):
        assert resolve_target("echo-peer", []) is None


# ---------------------------------------------------------------- leg 2 SQL


class TestAckRowPredicate:
    """Pin the LIKE predicate against rows the REAL queue class writes."""

    def _queue(self, tmp_path):
        from gateway.message_queue import PersistentMessageQueue
        return PersistentMessageQueue(
            db_path=str(tmp_path / "queue.db"))

    def test_matches_bridged_ack_row(self, tmp_path):
        q = self._queue(tmp_path)
        # Shape mirrors _rns_bridge_xform's R->M enqueue: the bridged text
        # carries a leading [RNS:xxxx] tag before the echo's ACK body.
        q.enqueue(
            payload={
                "message": "[RNS:ab12cd34] ACK seq=1718000000 "
                           "orig=canary-moc recv_at=2026-06-09T21:00:00Z",
                "channel": 0,
            },
            destination="meshtastic",
        )
        outcome, detail, elapsed = poll_ack_row(
            Path(q._db_path), 1718000000, "canary-moc", timeout_s=0)
        assert outcome == "found"
        assert detail  # the row id

    def test_wrong_seq_does_not_match(self, tmp_path):
        q = self._queue(tmp_path)
        q.enqueue(
            payload={"message": "[RNS:ab12cd34] ACK seq=111 orig=canary-moc"},
            destination="meshtastic",
        )
        outcome, _, _ = poll_ack_row(
            Path(q._db_path), 222, "canary-moc", timeout_s=0)
        assert outcome == "timeout"

    def test_wrong_destination_does_not_match(self, tmp_path):
        q = self._queue(tmp_path)
        q.enqueue(
            payload={"message": "ACK seq=333 orig=canary-moc"},
            destination="rns",
        )
        outcome, _, _ = poll_ack_row(
            Path(q._db_path), 333, "canary-moc", timeout_s=0)
        assert outcome == "timeout"

    def test_unreadable_db_is_error_not_timeout(self, tmp_path):
        # Honest failure mode: unobservable != not-arrived.
        bogus = tmp_path / "not-a-dir" / "queue.db"
        outcome, detail, _ = poll_ack_row(bogus, 1, "x", timeout_s=0)
        assert outcome == "error"
        assert "unreadable" in detail

    def test_pattern_embeds_seq_and_sender(self):
        assert ack_like_pattern(42, "canary-moc") == (
            "%ACK seq=42 orig=canary-moc%")


# ---------------------------------------------------------------- leg 1 SQL


class TestCountersPolling:
    """Pin the terminal-state query against the REAL counters class."""

    def _counters(self, tmp_path):
        from gateway.delivery_counters import DeliveryCounters
        return DeliveryCounters(db_path=tmp_path / "counters.db")

    def test_confirmed_is_terminal(self, tmp_path):
        from gateway.delivery_counters import DeliveryState
        dc = self._counters(tmp_path)
        dc.record(DeliveryState.QUEUED, msg_id="m1", protocol="rns")
        dc.record(DeliveryState.SENT, msg_id="m1", protocol="rns")
        dc.record(DeliveryState.CONFIRMED, msg_id="m1", protocol="rns")
        outcome, detail, _ = poll_counters_terminal(
            dc._db_path, "m1", timeout_s=0)
        assert outcome == "confirmed"
        assert detail is None

    def test_dropped_carries_reason(self, tmp_path):
        from gateway.delivery_counters import DeliveryState, DropReason
        dc = self._counters(tmp_path)
        dc.record(DeliveryState.QUEUED, msg_id="m2", protocol="rns")
        dc.record(DeliveryState.DROPPED, msg_id="m2", protocol="rns",
                  drop_reason=DropReason.RNS_DELIVERY_FAILED)
        outcome, detail, _ = poll_counters_terminal(
            dc._db_path, "m2", timeout_s=0)
        assert outcome == "dropped"
        assert detail  # the reason string, never empty

    def test_in_flight_times_out(self, tmp_path):
        from gateway.delivery_counters import DeliveryState
        dc = self._counters(tmp_path)
        dc.record(DeliveryState.QUEUED, msg_id="m3", protocol="rns")
        dc.record(DeliveryState.SENT, msg_id="m3", protocol="rns")
        outcome, _, _ = poll_counters_terminal(
            dc._db_path, "m3", timeout_s=0)
        assert outcome == "timeout"

    def test_other_message_ids_do_not_leak(self, tmp_path):
        from gateway.delivery_counters import DeliveryState
        dc = self._counters(tmp_path)
        dc.record(DeliveryState.CONFIRMED, msg_id="other", protocol="rns")
        outcome, _, _ = poll_counters_terminal(
            dc._db_path, "mine", timeout_s=0)
        assert outcome == "timeout"

    def test_unreadable_db_is_error_not_timeout(self, tmp_path):
        bogus = tmp_path / "not-a-dir" / "counters.db"
        outcome, detail, _ = poll_counters_terminal(bogus, "m", timeout_s=0)
        assert outcome == "error"
        assert "unreadable" in detail


# ---------------------------------------------------------------- verdicts


class TestClassify:
    """Every degraded path maps to a DISTINCT verdict + reason."""

    def test_both_legs_green_is_ok(self):
        r = classify(("confirmed", None, 3.2), ("found", "row1", 8.0),
                     seq=42, msg_id="m")
        assert r.verdict == "OK"
        assert r.exit_code == EXIT_OK
        assert "seq=42" in r.reason
        assert r.confirm_s == pytest.approx(3.2)
        assert r.ack_s == pytest.approx(8.0)

    def test_leg1_dropped_is_fail_with_reason(self):
        r = classify(("dropped", "rns_delivery_failed", 5.0), None,
                     seq=1, msg_id="m")
        assert r.verdict == "FAIL"
        assert "rns_delivery_failed" in r.reason
        assert r.exit_code == EXIT_FAIL

    def test_leg1_timeout_is_fail(self):
        r = classify(("timeout", None, 120.0), None, seq=1, msg_id="m")
        assert r.verdict == "FAIL"
        assert "not confirmed" in r.reason

    def test_leg1_db_error_is_fail_with_distinct_reason(self):
        r = classify(("error", "counters DB unreadable: boom", 0.1), None,
                     seq=1, msg_id="m")
        assert r.verdict == "FAIL"
        assert "unreadable" in r.reason
        assert "not confirmed" not in r.reason  # never folded into timeout

    def test_leg2_timeout_is_concern_not_fail(self):
        r = classify(("confirmed", None, 3.0), ("timeout", None, 120.0),
                     seq=1, msg_id="m")
        assert r.verdict == "CONCERN"
        assert r.exit_code == EXIT_CONCERN
        assert "no ACK" in r.reason

    def test_leg2_db_error_is_concern_with_distinct_reason(self):
        r = classify(("confirmed", None, 3.0),
                     ("error", "queue DB unreadable: boom", 0.1),
                     seq=1, msg_id="m")
        assert r.verdict == "CONCERN"
        assert "unreadable" in r.reason


# ---------------------------------------------------------------- e2e seam


class TestEnqueueContract:
    """The canary's payload reaches the queue in the shape
    _queue_send_rns consumes (message + destination_hash, dest 'rns')."""

    def test_ping_enqueue_roundtrip(self, tmp_path):
        from gateway.message_queue import PersistentMessageQueue
        q = PersistentMessageQueue(db_path=str(tmp_path / "queue.db"))
        seq = int(time.time())
        body = make_ping_body(seq, "canary-test")
        msg_id = q.enqueue(
            payload={
                "message": body,
                "destination_hash": "ffeeddccbbaa99887766554433221100",
            },
            destination="rns",
        )
        assert msg_id
        pending = q.get_pending(destination="rns")
        assert any(m.id == msg_id for m in pending)
        mine = next(m for m in pending if m.id == msg_id)
        assert mine.payload["message"] == body
        assert mine.payload["destination_hash"] == (
            "ffeeddccbbaa99887766554433221100")

    def test_unique_seq_never_dedups(self, tmp_path):
        from gateway.message_queue import PersistentMessageQueue
        q = PersistentMessageQueue(db_path=str(tmp_path / "queue.db"))
        ids = set()
        for seq in (1000, 1001, 1002):
            mid = q.enqueue(
                payload={"message": make_ping_body(seq, "canary-test"),
                         "destination_hash": "ab" * 16},
                destination="rns",
            )
            ids.add(mid)
        assert None not in ids
        assert len(ids) == 3
