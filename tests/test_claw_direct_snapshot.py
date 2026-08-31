"""Outcome drills for scripts/claw_direct_snapshot.py.

The F2 measurement's window exists only in claw_last_tick*.json, which the */5
metrics cron overwrites. This script is the thing standing between that window
and silence, so every one of its five outcomes is drilled here by PLANTING the
condition — not by reading the source and believing it.

That is the standing lesson from Issue #29 Layer 2: a guard that has never
failed is not evidence that it works
(feedback_a_guard_that_never_failed_is_not_evidence).
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import claw_direct_snapshot as cds  # noqa: E402

NOW = 1788203000.0

WINDOWS = {"dudeclaw-01": 78495, "dudeclaw-02": 77066, "dudeclaw-03": 77034}
BASE_DIRECT = {
    "dudeclaw-01": ["!aaa", "!bbb", "!ccc"],
    "dudeclaw-02": ["!ddd", "!eee"],
    "dudeclaw-03": ["!fff"],
}


def _write_baseline(home, windows=None, direct=None):
    windows = windows or WINDOWS
    direct = direct or BASE_DIRECT
    claws = {}
    for dev, win in windows.items():
        claws[dev] = {
            "accumulation_window_s": win,
            "uptime_s": win,
            "version": "0.4.0+dudeclaw.19",
            "direct": {n: {"direct": True, "rssi_dbm": -50, "age_s": 5,
                           "parse_error": False} for n in direct[dev]},
            "watched": {n: {"pkts": 3, "never": False} for n in direct[dev]},
            "heard_pkts": 100, "last_hops": 1, "stats_truncated": None,
        }
    path = os.path.join(
        home, "claw_direct_snapshot_pre_dudeclaw20_20260831T180729Z.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"claws": claws, "purpose": "t", "taken_utc": "t",
                   "warning": "w"}, fh)
    return path


def _write_tick(home, device, uptime, direct_nodes, primary=False, ok=True,
                captured_at=None, version="0.4.0+dudeclaw.20",
                watched_nodes=None):
    # Real claws watch a SUPERSET of what they call direct, so `watched`
    # defaults to the full baseline set for this device rather than to
    # direct_nodes -- a fixture where watched == direct cannot express the
    # difference between "no longer direct" and "left the watch list".
    name = ("claw_last_tick.json" if primary
            else "claw_last_tick.%s.json" % device)
    if watched_nodes is None:
        watched_nodes = BASE_DIRECT.get(device, direct_nodes)
    tick = {
        "captured_at": NOW - 30 if captured_at is None else captured_at,
        "device": device, "ok": ok, "reachable": ok,
        "device_info": {"uptime_s": uptime, "version": version},
        "lora": {
            "direct": {n: {"direct": True, "rssi_dbm": -50, "age_s": 5,
                           "parse_error": False} for n in direct_nodes},
            "watched": {n: {"pkts": 5, "never": False} for n in watched_nodes},
            "heard_pkts": 200, "last_hops": 1, "stats_truncated": None,
        },
    }
    with open(os.path.join(home, name), "w", encoding="utf-8") as fh:
        json.dump(tick, fh)


@pytest.fixture
def home(tmp_path):
    h = str(tmp_path)
    os.makedirs(os.path.join(h, ".local", "state", "meshforge"), exist_ok=True)
    return h


def _all_ticks(home, uptimes, direct=None):
    direct = direct or BASE_DIRECT
    _write_tick(home, "dudeclaw-01", uptimes[0], direct["dudeclaw-01"],
                primary=True)
    _write_tick(home, "dudeclaw-02", uptimes[1], direct["dudeclaw-02"],
                version="0.4.0+dudeclaw.19")
    _write_tick(home, "dudeclaw-03", uptimes[2], direct["dudeclaw-03"])


class TestWaiting:
    def test_short_window_waits_and_does_not_page(self, home):
        _write_baseline(home)
        _all_ticks(home, (1600, 1800, 1400))
        outcome, lines = cds.run(home, NOW)
        assert outcome == cds.OUT_WAITING
        assert cds.main(["--home", home, "--now", str(NOW)]) == 0
        assert any("to go" in ln for ln in lines)

    def test_waiting_writes_no_snapshot(self, home):
        _write_baseline(home)
        _all_ticks(home, (1600, 1800, 1400))
        cds.run(home, NOW)
        assert not os.path.exists(
            os.path.join(home, cds.SNAPSHOT_BASENAME))


class TestCaptured:
    def test_met_windows_capture(self, home):
        _write_baseline(home)
        _all_ticks(home, (79000, 78000, 78000),
                   direct={"dudeclaw-01": ["!aaa"],
                           "dudeclaw-02": ["!ddd", "!eee"],
                           "dudeclaw-03": ["!fff", "!zzz"]})
        outcome, _ = cds.run(home, NOW)
        assert outcome == cds.OUT_CAPTURED
        snap = json.load(open(os.path.join(home, cds.SNAPSHOT_BASENAME),
                              encoding="utf-8"))
        assert set(snap["claws"]) == set(WINDOWS)

    def test_delta_names_the_nodes_that_lost_direct(self, home):
        _write_baseline(home)
        _all_ticks(home, (79000, 78000, 78000),
                   direct={"dudeclaw-01": ["!aaa"],
                           "dudeclaw-02": ["!ddd", "!eee"],
                           "dudeclaw-03": ["!fff", "!zzz"]})
        cds.run(home, NOW)
        snap = json.load(open(os.path.join(home, cds.SNAPSHOT_BASENAME),
                              encoding="utf-8"))
        d = snap["delta_vs_baseline"]
        # Still watched and still heard, no longer direct -> the F2 signal.
        assert d["dudeclaw-01"]["lost_direct_heard"] == ["!bbb", "!ccc"]
        assert d["dudeclaw-01"]["lost_direct_unheard"] == []
        assert d["dudeclaw-01"]["dropped_from_watch"] == []
        assert d["dudeclaw-01"]["kept_direct"] == ["!aaa"]
        assert d["dudeclaw-03"]["gained_direct"] == ["!zzz"]
        # The control stayed on .19 and must be visible as such.
        assert d["dudeclaw-02"]["version_after"] == "0.4.0+dudeclaw.19"

    def test_pointer_file_written(self, home):
        _write_baseline(home)
        _all_ticks(home, (79000, 78000, 78000))
        cds.run(home, NOW)
        assert os.path.exists(
            os.path.join(home, ".claw_direct_snapshot_post_latest"))


class TestNeverClobbers:
    def test_second_run_leaves_snapshot_byte_identical(self, home):
        _write_baseline(home)
        _all_ticks(home, (79000, 78000, 78000))
        assert cds.run(home, NOW)[0] == cds.OUT_CAPTURED
        path = os.path.join(home, cds.SNAPSHOT_BASENAME)
        before = open(path, encoding="utf-8").read()
        outcome, _ = cds.run(home, NOW + 60)
        assert outcome == cds.OUT_ALREADY
        assert open(path, encoding="utf-8").read() == before

    def test_write_once_refuses_existing(self, home):
        path = os.path.join(home, "x.json")
        assert cds.write_once(path, {"a": 1}) is True
        assert cds.write_once(path, {"a": 2}) is False
        assert json.load(open(path, encoding="utf-8"))["a"] == 1


class TestWindowLost:
    def test_uptime_decrease_is_loud_exactly_once(self, home):
        _write_baseline(home)
        _all_ticks(home, (50000, 50000, 50000))
        assert cds.run(home, NOW)[0] == cds.OUT_WAITING

        _write_tick(home, "dudeclaw-03", 120, ["!fff"])  # rebooted
        outcome, lines = cds.run(home, NOW + 60)
        assert outcome == cds.OUT_WINDOW_LOST
        assert any("rebooted" in ln and "dudeclaw-03" in ln for ln in lines)

        # Reported once, then the restarted window is plain `waiting` — an
        # alarm that never clears is an alarm people learn to ignore.
        assert cds.run(home, NOW + 120)[0] == cds.OUT_WAITING

    def test_window_lost_pages(self, home):
        _write_baseline(home)
        _all_ticks(home, (50000, 50000, 50000))
        cds.main(["--home", home, "--now", str(NOW)])
        _write_tick(home, "dudeclaw-03", 120, ["!fff"])
        assert cds.main(["--home", home, "--now", str(NOW + 60)]) == 1

    def test_growth_is_not_a_reboot(self, home):
        _write_baseline(home)
        _all_ticks(home, (50000, 50000, 50000))
        cds.run(home, NOW)
        _all_ticks(home, (50600, 50600, 50600))
        assert cds.run(home, NOW + 600)[0] == cds.OUT_WAITING


class TestUnobservable:
    """Blindness must never wear the costume of a legitimate pending state."""

    def test_stale_tick_blocks_capture(self, home):
        _write_baseline(home)
        _all_ticks(home, (79000, 78000, 78000))
        _write_tick(home, "dudeclaw-01", 79000, ["!aaa"], primary=True,
                    captured_at=NOW - (cds.CLAW_STALE_S * 5))
        outcome, lines = cds.run(home, NOW)
        assert outcome == cds.OUT_UNOBSERVABLE
        assert any("STALE" in ln for ln in lines)
        assert not os.path.exists(os.path.join(home, cds.SNAPSHOT_BASENAME))

    def test_future_stamped_tick_is_refused(self, home):
        _write_baseline(home)
        _all_ticks(home, (79000, 78000, 78000))
        _write_tick(home, "dudeclaw-01", 79000, ["!aaa"], primary=True,
                    captured_at=NOW + (cds.CLAW_STALE_S * 5))
        assert cds.run(home, NOW)[0] == cds.OUT_UNOBSERVABLE

    def test_absent_claw_is_not_waiting(self, home):
        _write_baseline(home)
        _write_tick(home, "dudeclaw-01", 79000, ["!aaa"], primary=True)
        _write_tick(home, "dudeclaw-02", 78000, ["!ddd"],
                    version="0.4.0+dudeclaw.19")
        outcome, lines = cds.run(home, NOW)
        assert outcome == cds.OUT_UNOBSERVABLE
        assert any("no tick file" in ln and "dudeclaw-03" in ln for ln in lines)

    def test_unreachable_claw_is_not_zero_direct_links(self, home):
        _write_baseline(home)
        _all_ticks(home, (79000, 78000, 78000))
        _write_tick(home, "dudeclaw-03", 78000, [], ok=False)
        outcome, lines = cds.run(home, NOW)
        assert outcome == cds.OUT_UNOBSERVABLE
        assert any("did not answer" in ln for ln in lines)

    def test_missing_baseline_invents_no_window(self, home):
        _write_tick(home, "dudeclaw-01", 79000, ["!aaa"], primary=True)
        outcome, lines = cds.run(home, NOW)
        assert outcome == cds.OUT_UNOBSERVABLE
        assert any("baseline" in ln for ln in lines)

    def test_ambiguous_baseline_refuses_to_guess(self, home):
        _write_baseline(home)
        with open(os.path.join(
                home, "claw_direct_snapshot_pre_dudeclaw20_OTHER.json"),
                "w", encoding="utf-8") as fh:
            json.dump({"claws": {}}, fh)
        outcome, lines = cds.run(home, NOW)
        assert outcome == cds.OUT_UNOBSERVABLE
        assert any("ambiguous" in ln for ln in lines)

    def test_garbled_tick_is_blindness(self, home):
        _write_baseline(home)
        _all_ticks(home, (79000, 78000, 78000))
        with open(os.path.join(home, "claw_last_tick.dudeclaw-03.json"),
                  "w", encoding="utf-8") as fh:
            fh.write("{tru")
        assert cds.run(home, NOW)[0] == cds.OUT_UNOBSERVABLE

    def test_tick_without_device_is_not_attributed(self, home):
        _write_baseline(home)
        _all_ticks(home, (79000, 78000, 78000))
        with open(os.path.join(home, "claw_last_tick.dudeclaw-03.json"),
                  "w", encoding="utf-8") as fh:
            json.dump({"captured_at": NOW - 30, "ok": True}, fh)
        outcome, lines = cds.run(home, NOW)
        assert outcome == cds.OUT_UNOBSERVABLE
        assert any("device field" in ln or "no tick file" in ln for ln in lines)


class TestIdentity:
    def test_device_comes_from_the_tick_not_the_filename(self, home):
        """The host->claw map is counterintuitive by measurement; the tick's own
        device field is the authority. moc2 holds claw-01's DEFAULT basename
        while claw-03 is the board attached to it.
        """
        _write_baseline(home)
        # Primary basename carries claw-01, secondaries carry 02 and 03.
        _write_tick(home, "dudeclaw-01", 79000, ["!aaa"], primary=True)
        _write_tick(home, "dudeclaw-02", 78000, ["!ddd", "!eee"],
                    version="0.4.0+dudeclaw.19")
        _write_tick(home, "dudeclaw-03", 78000, ["!fff"])
        ticks, problems = cds.read_ticks(home, NOW)
        assert problems == []
        assert set(ticks) == {"dudeclaw-01", "dudeclaw-02", "dudeclaw-03"}
        assert ticks["dudeclaw-01"]["device"] == "dudeclaw-01"


class TestPaging:
    """Paging is keyed to the TRANSITION, not the run — a state that persists
    for a day must not send 48 notifications, and a recovery-then-relapse must
    not be swallowed as continuity.
    """

    @staticmethod
    def _spy(monkeypatch):
        sent = []
        monkeypatch.setattr(cds, "notify",
                            lambda outcome, lines: sent.append(outcome) or "sent")
        return sent

    def test_waiting_never_pages(self, home, monkeypatch):
        sent = self._spy(monkeypatch)
        assert cds.maybe_notify(home, cds.OUT_WAITING, [], True) is None
        assert sent == []

    def test_captured_pages_once_then_stays_quiet(self, home, monkeypatch):
        sent = self._spy(monkeypatch)
        cds.maybe_notify(home, cds.OUT_CAPTURED, ["delta"], True)
        for _ in range(5):
            cds.maybe_notify(home, cds.OUT_ALREADY, [], True)
        assert sent == [cds.OUT_CAPTURED]

    def test_persistent_unobservable_pages_once(self, home, monkeypatch):
        sent = self._spy(monkeypatch)
        for _ in range(48):
            cds.maybe_notify(home, cds.OUT_UNOBSERVABLE, ["blind"], True)
        assert sent == [cds.OUT_UNOBSERVABLE]

    def test_relapse_after_recovery_pages_again(self, home, monkeypatch):
        sent = self._spy(monkeypatch)
        cds.maybe_notify(home, cds.OUT_UNOBSERVABLE, ["blind"], True)
        cds.maybe_notify(home, cds.OUT_WAITING, [], True)      # recovered
        cds.maybe_notify(home, cds.OUT_UNOBSERVABLE, ["blind again"], True)
        assert sent == [cds.OUT_UNOBSERVABLE, cds.OUT_UNOBSERVABLE]

    def test_window_lost_then_waiting_then_lost_pages_twice(self, home,
                                                            monkeypatch):
        sent = self._spy(monkeypatch)
        cds.maybe_notify(home, cds.OUT_WINDOW_LOST, ["reboot"], True)
        cds.maybe_notify(home, cds.OUT_WAITING, [], True)
        cds.maybe_notify(home, cds.OUT_WINDOW_LOST, ["reboot 2"], True)
        assert sent == [cds.OUT_WINDOW_LOST, cds.OUT_WINDOW_LOST]

    def test_disabled_sends_nothing(self, home, monkeypatch):
        sent = self._spy(monkeypatch)
        cds.maybe_notify(home, cds.OUT_CAPTURED, ["delta"], False)
        assert sent == []

    def test_notify_leaves_a_witness_when_helper_is_missing(self, monkeypatch):
        monkeypatch.setattr(os.path, "exists", lambda p: False)
        out = cds.notify(cds.OUT_CAPTURED, ["x"])
        assert out is not None and "NOT PAGED" in out

    def test_notify_survives_a_failing_helper(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("helper exploded")
        monkeypatch.setattr(cds.subprocess, "run", boom)
        monkeypatch.setattr(os.path, "exists", lambda p: True)
        out = cds.notify(cds.OUT_WINDOW_LOST, ["x"])
        assert out is not None and "NOT PAGED" in out

    def test_non_notifiable_outcome_sends_nothing(self, monkeypatch):
        monkeypatch.setattr(os.path, "exists", lambda p: True)
        assert cds.notify(cds.OUT_WAITING, ["x"]) is None


class TestDeltaSeparatesSignalFromNoise:
    """A node can leave the direct-true set for three unrelated reasons and only
    ONE is about F2. The first version of build_delta called all three
    "F2's candidate forgeries" and would have put that on the operator's phone.
    """

    @staticmethod
    def _delta(base_direct, base_watched, cur_direct, cur_watched):
        baseline = {"claws": {"c": {
            "version": "0.4.0+dudeclaw.19",
            "accumulation_window_s": 100,
            "direct": {n: {"direct": True} for n in base_direct},
            "watched": {n: {"pkts": 5, "never": False} for n in base_watched},
        }}}
        claws = {"c": {
            "version": "0.4.0+dudeclaw.20",
            "accumulation_window_s": 100,
            "direct": {n: {"direct": True} for n in cur_direct},
            "watched": cur_watched,
        }}
        return cds.build_delta(baseline, claws)["c"]

    def test_heard_but_no_longer_direct_is_the_signal(self):
        d = self._delta(["!a"], ["!a"], [], {"!a": {"pkts": 9, "never": False}})
        assert d["lost_direct_heard"] == ["!a"]
        assert d["lost_direct_unheard"] == []

    def test_node_that_went_quiet_is_not_evidence(self):
        d = self._delta(["!a"], ["!a"], [], {"!a": {"pkts": 0, "never": True}})
        assert d["lost_direct_heard"] == []
        assert d["lost_direct_unheard"] == ["!a"]

    def test_zero_packets_is_not_evidence(self):
        d = self._delta(["!a"], ["!a"], [], {"!a": {"pkts": 0, "never": False}})
        assert d["lost_direct_heard"] == []
        assert d["lost_direct_unheard"] == ["!a"]

    def test_unknown_heard_state_is_its_own_bucket(self):
        d = self._delta(["!a"], ["!a"], [], {"!a": {"never": None}})
        assert d["lost_direct_heard"] == []
        assert d["lost_direct_unknown"] == ["!a"]

    def test_node_removed_from_watch_list_is_not_comparable(self):
        d = self._delta(["!a", "!b"], ["!a", "!b"], ["!b"],
                        {"!b": {"pkts": 5, "never": False}})
        assert d["lost_direct_heard"] == []
        assert d["dropped_from_watch"] == ["!a"]
        assert d["watch_set_changed"] is True
        assert d["watch_removed"] == ["!a"]

    def test_watch_set_change_is_flagged_even_when_nothing_was_lost(self):
        d = self._delta(["!a"], ["!a"], ["!a", "!z"],
                        {"!a": {"pkts": 5, "never": False},
                         "!z": {"pkts": 5, "never": False}})
        assert d["lost_direct_heard"] == []
        assert d["watch_set_changed"] is True
        assert d["watch_added"] == ["!z"]

    def test_stable_watch_set_is_not_flagged(self):
        d = self._delta(["!a"], ["!a"], ["!a"],
                        {"!a": {"pkts": 5, "never": False}})
        assert d["watch_set_changed"] is False

    def test_all_three_causes_at_once_stay_separated(self):
        d = self._delta(["!sig", "!quiet", "!gone"],
                        ["!sig", "!quiet", "!gone"], [],
                        {"!sig": {"pkts": 9, "never": False},
                         "!quiet": {"pkts": 0, "never": True}})
        assert d["lost_direct_heard"] == ["!sig"]
        assert d["lost_direct_unheard"] == ["!quiet"]
        assert d["dropped_from_watch"] == ["!gone"]

    def test_render_labels_non_evidence_and_does_not_call_it_f2(self):
        d = self._delta(["!sig", "!quiet"], ["!sig", "!quiet"], [],
                        {"!sig": {"pkts": 9, "never": False},
                         "!quiet": {"pkts": 0, "never": True}})
        out = "\n".join(cds.render_delta({"c": d}))
        assert "F2-LOST 1 !sig" in out
        assert "not evidence (not heard this window): !quiet" in out
