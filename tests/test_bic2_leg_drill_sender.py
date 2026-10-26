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
import time
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
    def _log(self, tmp_path, tokens, handoff="handed",
             bridge_mode="mqtt_bridge", ts0=None):
        p = tmp_path / "run.jsonl"
        ts0 = time.time() - 600 if ts0 is None else ts0
        drill.append_log(p, {"event": "start", "run_id": "r", "source": SRC,
                             "bridge_mode": bridge_mode, "ts": ts0})
        for i, t in enumerate(tokens, 1):
            drill.append_log(p, {"event": "send", "seq": i, "token": t,
                                 "ts": ts0 + i, "handoff": handoff,
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
                                 "unaccounted": 0, "unobservable": 0}
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


# ── review fixes 2026-10-25 (S1..S5) ───────────────────────────────────────

def _send_log(tmp_path, n=1, bridge_mode="mqtt_bridge", ts0=None, start=True):
    p = tmp_path / "run.jsonl"
    ts0 = time.time() - 600 if ts0 is None else ts0
    if start:
        drill.append_log(p, {"event": "start", "run_id": "r", "source": SRC,
                             "bridge_mode": bridge_mode, "ts": ts0})
    for i in range(1, n + 1):
        drill.append_log(p, {"event": "send", "seq": i,
                             "token": drill.make_token("r", i),
                             "ts": ts0 + i, "handoff": "handed", "source": SRC})
    return p


class TestS1QueueIsTheRecordOnlyUnderMqttBridge:
    """The gateway queues RNS→Mesh only in bridge_mode=mqtt_bridge
    (_rns_bridge_xform: direct send leaves no row on success and requeues a
    failed chunk with source_id ''). A missing row is LOSS only there."""

    def test_no_row_outside_mqtt_bridge_is_unknown_not_fail(self, tmp_path):
        rep = drill.reconcile(_send_log(tmp_path, bridge_mode="message_bridge"),
                              _queue_db(tmp_path, []))
        assert rep["exit"] == 2
        assert rep["counts"]["unaccounted"] == 0

    def test_no_row_without_a_start_event_is_unknown(self, tmp_path):
        rep = drill.reconcile(_send_log(tmp_path, start=False),
                              _queue_db(tmp_path, []))
        assert rep["exit"] == 2

    def test_no_row_past_the_queue_purge_window_is_unknown(self, tmp_path):
        # purge_old(days=1) deletes terminal rows ~24 h after updated_at
        old = time.time() - 30 * 3600
        rep = drill.reconcile(_send_log(tmp_path, ts0=old),
                              _queue_db(tmp_path, []))
        assert rep["exit"] == 2
        assert rep["counts"]["unaccounted"] == 0

    def test_send_preflight_refuses_a_non_queue_bridge_mode(self):
        gw = {"error": None, "ids": [SRC], "policy": "enforce",
              "bridge_mode": "message_bridge", "peers": []}
        rc, lines = drill.gateway_preflight(SRC, gw, ("ok", "live ok"))
        assert rc == 1
        assert any("mqtt_bridge" in ln for ln in lines)

    def test_send_preflight_passes_mqtt_bridge_listed_live(self):
        gw = {"error": None, "ids": [SRC], "policy": "enforce",
              "bridge_mode": "mqtt_bridge", "peers": []}
        rc, _ = drill.gateway_preflight(SRC, gw, ("ok", "live ok"))
        assert rc == 0


class TestS2LogDamage:
    def test_torn_final_line_is_tolerated_and_reported(self, tmp_path):
        t1 = drill.make_token("r", 1)
        log = _send_log(tmp_path, n=1)
        with open(log, "a") as f:
            f.write('{"event": "send", "seq": 2, "tok')   # kill mid-write
        db = _queue_db(tmp_path, [(drill.message_text(t1), SRC, "delivered",
                                   "meshtastic")])
        rep = drill.reconcile(log, db)
        assert rep["exit"] == 0
        assert rep["log_note"] and "torn" in rep["log_note"]

    def test_corrupt_middle_line_is_unknown(self, tmp_path):
        log = _send_log(tmp_path, n=1)
        text = log.read_text()
        log.write_text("garbage{\n" + text)
        rep = drill.reconcile(log, _queue_db(tmp_path, []))
        assert rep["exit"] == 2
        assert "line 1" in rep["error"]

    def test_missing_log_is_unknown(self, tmp_path):
        rep = drill.reconcile(tmp_path / "nope.jsonl", _queue_db(tmp_path, []))
        assert rep["exit"] == 2
        assert rep["error"]


class TestS3LiveGatewayLoadedTheConfig:
    NOW_WALL, NOW_MONO = 1_000_000.0, 5_000.0

    def _props(self, started_mono_s, state="active"):
        return {"ActiveState": state,
                "ActiveEnterTimestampMonotonic": str(int(started_mono_s * 1e6))}

    def test_config_edited_after_gateway_start_refuses(self):
        # gateway started 1000 s ago, config edited 10 s ago
        v, why = drill.live_config_check(
            self.NOW_WALL - 10, self._props(self.NOW_MONO - 1000),
            self.NOW_WALL, self.NOW_MONO)
        assert v == "stale"
        assert "restart meshforge-gateway" in why

    def test_gateway_started_after_config_edit_passes(self):
        v, _ = drill.live_config_check(
            self.NOW_WALL - 1000, self._props(self.NOW_MONO - 10),
            self.NOW_WALL, self.NOW_MONO)
        assert v == "ok"

    def test_gateway_not_active_refuses(self):
        v, _ = drill.live_config_check(
            self.NOW_WALL - 1000, self._props(self.NOW_MONO - 10, "inactive"),
            self.NOW_WALL, self.NOW_MONO)
        assert v == "stale"

    def test_unreadable_unit_is_unknown(self):
        v, _ = drill.live_config_check(self.NOW_WALL - 1000, None,
                                       self.NOW_WALL, self.NOW_MONO)
        assert v == "unknown"

    def test_preflight_refuses_on_stale_live_config(self):
        gw = {"error": None, "ids": [SRC], "policy": "enforce",
              "bridge_mode": "mqtt_bridge", "peers": []}
        rc, _ = drill.gateway_preflight(SRC, gw, ("stale", "restart it"))
        assert rc == 1
        rc, _ = drill.gateway_preflight(SRC, gw, ("unknown", "?"))
        assert rc == 2

    def test_unreadable_gateway_config_is_unknown(self):
        rc, _ = drill.gateway_preflight(SRC, {"error": "JSONDecodeError"},
                                        ("ok", "live ok"))
        assert rc == 2


class TestS4MarkPicksTheOpenRun:
    def _run(self, d, name, ended):
        p = d / f"{name}.jsonl"
        drill.append_log(p, {"event": "start", "run_id": name, "ts": 1.0})
        if ended:
            drill.append_log(p, {"event": "end", "ts": 2.0})
        return p

    def test_open_run_wins_over_a_later_sorting_ended_run(self, tmp_path):
        live = self._run(tmp_path, "20261025T080000", ended=False)
        self._run(tmp_path, "zz-custom-run-id", ended=True)
        path, _ = drill.pick_mark_log(tmp_path)
        assert path == live

    def test_two_open_runs_refuse(self, tmp_path):
        self._run(tmp_path, "a", ended=False)
        self._run(tmp_path, "b", ended=False)
        path, why = drill.pick_mark_log(tmp_path)
        assert path is None and "--log" in why

    def test_no_open_run_refuses(self, tmp_path):
        self._run(tmp_path, "a", ended=True)
        path, why = drill.pick_mark_log(tmp_path)
        assert path is None and "--log" in why


class TestS5PeerRelayAirtime:
    OWN = "c" * 32

    def test_multiplier_counts_what_the_relay_loop_would_send_to(self):
        peers = ["a" * 32, "b" * 32, self.OWN, "short", "z" * 32]
        mult, used, line = drill.airtime_estimate(35, peers, self.OWN)
        # own hash, wrong length and non-hex are skipped by _maybe_relay_to_peers
        assert used == ["a" * 32, "b" * 32]
        assert mult == 3
        assert "105" in line and "aaaaaaaa" in line

    def test_no_peers_is_local_only(self):
        mult, used, _ = drill.airtime_estimate(35, [], self.OWN)
        assert (mult, used) == (1, [])

    def test_reconcile_states_its_scope(self, tmp_path, capsys):
        import argparse
        log = _send_log(tmp_path, n=1)
        db = _queue_db(tmp_path, [])
        drill.cmd_reconcile(argparse.Namespace(log=str(log), db=str(db),
                                               json=False))
        assert "THIS gateway" in capsys.readouterr().out
