"""BIC-2 leg-kill drill sender — the pure parts (no RNS, no radio).

The acceptance sentence (review_provenance 2026-10-25) needs a sender that
keeps its OWN log and a reconcile that proves "every message in the sender's
log ends sent or failed in the gateway record — none unaccounted". These tests
pin the reconcile verdicts against a real queue-shaped SQLite file, because a
reconcile that cannot say UNACCOUNTED would pass the drill by construction.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
_spec = importlib.util.spec_from_file_location(
    "bic2_leg_drill_sender", _ROOT / "scripts" / "bic2_leg_drill_sender.py")
drill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drill)

from utils.db_helpers import connect_tuned  # noqa: E402

SRC = "3ac3d1e30734b402c5d159b25cf4f279"


def _queue_db(tmp_path, rows):
    """A file with the gateway queue's ``messages`` columns the reconcile reads."""
    p = tmp_path / "message_queue.db"
    c = connect_tuned(p)
    c.execute("""CREATE TABLE messages (
        id TEXT PRIMARY KEY, payload TEXT NOT NULL, destination TEXT NOT NULL,
        status TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        retry_count INTEGER DEFAULT 0, error_message TEXT DEFAULT '')""")
    for i, (text, source, status, dest) in enumerate(rows):
        payload = json.dumps({"message": text, "channel": 2,
                              "source_id": source})
        c.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)",
                  (f"m{i}", payload, dest, status, "2026-10-26T08:00:00",
                   "2026-10-26T08:00:01", 0, ""))
    c.commit()
    c.close()
    return p


class TestToken:
    def test_token_is_unique_per_seq_and_carried_in_text(self):
        a, b = drill.make_token("r1", 1), drill.make_token("r1", 2)
        assert a != b
        assert a in drill.message_text(a)

    def test_token_is_not_a_prefix_of_a_later_token(self):
        # seq 1 must not match seq 10..19 by substring in the reconcile
        assert drill.make_token("r1", 1) not in drill.message_text(
            drill.make_token("r1", 10))


class TestAllowlistPreflight:
    def test_enforce_unlisted_refuses_with_what_to_do(self):
        ok, why = drill.allowlist_preflight(SRC, ["a" * 32], "enforce")
        assert not ok
        assert SRC in why and "bridge_source_identities" in why

    def test_enforce_listed_passes(self):
        ok, _ = drill.allowlist_preflight(SRC, [SRC], "enforce")
        assert ok

    def test_open_and_observe_pass(self):
        assert drill.allowlist_preflight(SRC, [], "open")[0]
        assert drill.allowlist_preflight(SRC, ["a" * 32], "observe")[0]


class TestClassify:
    @pytest.mark.parametrize("statuses,want", [
        ([], "unaccounted"),
        (["delivered"], "sent"),
        (["dead_letter"], "failed"),
        (["failed"], "failed"),
        (["pending"], "open"),
        (["in_progress"], "open"),
        (["delivered", "pending"], "open"),       # a chunk still in flight
        (["delivered", "dead_letter"], "failed"),  # one chunk lost = lost
        (["weird"], "open"),                       # unknown is never terminal
    ])
    def test_verdicts(self, statuses, want):
        assert drill.classify(statuses) == want


class TestReconcile:
    def _log(self, tmp_path, tokens, handoff="handed"):
        p = tmp_path / "run.jsonl"
        for i, t in enumerate(tokens, 1):
            drill.append_log(p, {"event": "send", "seq": i, "token": t,
                                 "ts": 1.0 + i, "handoff": handoff,
                                 "source": SRC})
        return p

    def test_every_message_accounted(self, tmp_path):
        t1, t2 = drill.make_token("r", 1), drill.make_token("r", 2)
        db = _queue_db(tmp_path, [
            (f"[RNS:3ac3] {drill.message_text(t1)}", SRC, "delivered",
             "meshtastic"),
            (f"[RNS:3ac3] {drill.message_text(t2)}", SRC, "dead_letter",
             "meshtastic"),
        ])
        rep = drill.reconcile(self._log(tmp_path, [t1, t2]), db)
        assert rep["counts"] == {"sent": 1, "failed": 1, "open": 0,
                                 "unaccounted": 0}
        assert rep["exit"] == 0

    def test_missing_row_is_unaccounted_and_fails(self, tmp_path):
        t1, t2 = drill.make_token("r", 1), drill.make_token("r", 2)
        db = _queue_db(tmp_path, [
            (f"[RNS:3ac3] {drill.message_text(t1)}", SRC, "delivered",
             "meshtastic")])
        rep = drill.reconcile(self._log(tmp_path, [t1, t2]), db)
        assert rep["counts"]["unaccounted"] == 1
        assert rep["exit"] == 1

    def test_open_row_is_unknown_not_pass(self, tmp_path):
        t1 = drill.make_token("r", 1)
        db = _queue_db(tmp_path, [
            (f"[RNS:3ac3] {drill.message_text(t1)}", SRC, "pending",
             "meshtastic")])
        rep = drill.reconcile(self._log(tmp_path, [t1]), db)
        assert rep["exit"] == 2

    def test_row_from_another_source_or_leg_does_not_count(self, tmp_path):
        # same text echoed by someone else, or our token on the rns leg,
        # is not OUR rns>meshtastic message
        t1 = drill.make_token("r", 1)
        db = _queue_db(tmp_path, [
            (drill.message_text(t1), "b" * 32, "delivered", "meshtastic"),
            (drill.message_text(t1), SRC, "delivered", "rns"),
        ])
        rep = drill.reconcile(self._log(tmp_path, [t1]), db)
        assert rep["counts"]["unaccounted"] == 1

    def test_send_that_never_left_the_sender_is_reported_apart(self, tmp_path):
        t1 = drill.make_token("r", 1)
        db = _queue_db(tmp_path, [])
        rep = drill.reconcile(self._log(tmp_path, [t1], handoff="error"), db)
        assert rep["counts"]["unaccounted"] == 0
        assert rep["sender_errors"] == 1
        assert rep["exit"] == 1  # a drill whose sender failed proves nothing

    def test_empty_log_is_unknown(self, tmp_path):
        p = tmp_path / "run.jsonl"
        p.write_text("")
        rep = drill.reconcile(p, _queue_db(tmp_path, []))
        assert rep["exit"] == 2

    def test_unreadable_db_is_unknown_never_unaccounted(self, tmp_path):
        t1 = drill.make_token("r", 1)
        rep = drill.reconcile(self._log(tmp_path, [t1]),
                              tmp_path / "absent.db")
        assert rep["exit"] == 2
        assert rep["counts"]["unaccounted"] == 0

    def test_reconcile_never_writes_the_gateway_db(self, tmp_path):
        t1 = drill.make_token("r", 1)
        db = _queue_db(tmp_path, [
            (f"[RNS:3ac3] {drill.message_text(t1)}", SRC, "delivered",
             "meshtastic")])
        before = db.read_bytes()
        drill.reconcile(self._log(tmp_path, [t1]), db)
        assert db.read_bytes() == before
