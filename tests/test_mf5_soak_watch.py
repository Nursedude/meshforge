"""Tests for the mf.5/mf.4 soak watch (scripts/mf5_soak_watch.py).

Pin the criteria mapping: every C1–C5 violation must surface with its
criterion tag, blindness must never read as healthy, and the final
aggregation must FAIL on missing days (absence of evidence is not
evidence of a clean soak)."""

import importlib.util
import sys
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parent.parent
          / "scripts" / "mf5_soak_watch.py")
spec = importlib.util.spec_from_file_location("mf5_soak_watch", SCRIPT)
msw = importlib.util.module_from_spec(spec)
sys.modules["mf5_soak_watch"] = msw
spec.loader.exec_module(msw)


def _host(name="moc", observable=True, exit75=0, restarts=0,
          stop_kills=0, collision_edges=0, error=""):
    return {"host": name, "observable": observable, "error": error,
            "exit75": exit75, "restarts": restarts,
            "stop_kills": stop_kills, "collision_edges": collision_edges}


def _wd(name="moc", observable=True, wedge=0, degraded=0, stale=False,
        detail=""):
    """One per-host C6 watchdog result (shape of collect_watchdog())."""
    return {"host": name, "observable": observable, "wedge": wedge,
            "degraded": degraded, "stale": stale, "detail": detail}


def _clean_wd():
    """A clean watchdog read for every meshforge-watchdog box."""
    return [_wd(name=h) for h in msw.WATCHDOG_HOSTS]


def _day(date="2026-06-11", hosts=None, canary_ok=24, canary_concern=0,
         canary_fail=0, canary_observable=True, watchdog=None):
    rec = msw.DayRecord(date=date, since="x")
    rec.hosts = hosts if hosts is not None else [_host()]
    rec.canary_ok = canary_ok
    rec.canary_concern = canary_concern
    rec.canary_fail = canary_fail
    rec.canary_observable = canary_observable
    rec.watchdog = watchdog if watchdog is not None else _clean_wd()
    return rec


class TestClassifyDay:
    def test_clean_day_is_ok(self):
        status, msg = msw.classify_day(_day())
        assert status == "OK"

    def test_stop_kill_fails_c3(self):
        status, msg = msw.classify_day(
            _day(hosts=[_host(stop_kills=1)]))
        assert status == "FAIL"
        assert "C3" in msg

    def test_exit75_without_restart_fails_c1(self):
        status, msg = msw.classify_day(
            _day(hosts=[_host(exit75=1, restarts=0, collision_edges=1)]))
        assert status == "FAIL"
        assert "C1" in msg

    def test_exit75_without_witness_is_c2_concern(self):
        status, msg = msw.classify_day(
            _day(hosts=[_host(exit75=1, restarts=2, collision_edges=0)]))
        assert status == "CONCERN"
        assert "C2" in msg

    def test_self_healed_inversion_is_concern_note_not_fail(self):
        status, msg = msw.classify_day(
            _day(hosts=[_host(exit75=1, restarts=2, collision_edges=3)]))
        assert status == "CONCERN"
        assert "self-healed" in msg

    def test_unobservable_host_is_concern_c5_never_ok(self):
        status, msg = msw.classify_day(
            _day(hosts=[_host(observable=False)]))
        assert status == "CONCERN"
        assert "C5" in msg

    def test_canary_fail_fails_c4(self):
        status, msg = msw.classify_day(_day(canary_fail=1))
        assert status == "FAIL"
        assert "C4" in msg

    def test_canary_concern_does_not_fail(self):
        status, _ = msw.classify_day(_day(canary_concern=3))
        assert status == "OK"

    def test_canary_unobservable_is_concern(self):
        status, msg = msw.classify_day(_day(canary_observable=False))
        assert status == "CONCERN"
        assert "C5" in msg

    # --- C6: fleet watchdog health ---

    def test_watchdog_wedge_fails_c6(self):
        # A wedge during the window is real fleet breakage — the exact
        # mesh-RF bot-dark class C1-C5 (RNS-only) never saw. FAIL.
        status, msg = msw.classify_day(
            _day(watchdog=[_wd(name="moc", wedge=1,
                               detail="channel_feed_dark(wedge)")]))
        assert status == "FAIL"
        assert "C6" in msg

    def test_watchdog_degraded_is_c6_concern_not_fail(self):
        status, msg = msw.classify_day(
            _day(watchdog=[_wd(name="moc1", degraded=1,
                               detail="role_drift(degraded)")]))
        assert status == "CONCERN"
        assert "C6" in msg

    def test_watchdog_unobservable_is_c6_concern_never_ok(self):
        # Unobservable is not healthy — must surface, never absorbed.
        status, msg = msw.classify_day(
            _day(watchdog=[_wd(name="moc2", observable=False)]))
        assert status == "CONCERN"
        assert "C6" in msg

    def test_watchdog_stale_is_c6_concern_never_ok(self):
        # A stale snapshot is a wedged loop — unobservable, not clean.
        status, msg = msw.classify_day(
            _day(watchdog=[_wd(name="moc3", observable=False, stale=True,
                               detail="stale (640s)")]))
        assert status == "CONCERN"
        assert "C6" in msg

    def test_clean_watchdog_and_clean_day_is_ok(self):
        # C6 does not false-fire when everything (C1-C5 + C6) is clean.
        status, msg = msw.classify_day(_day())
        assert status == "OK"
        assert "C6" not in msg

    def test_watchdog_wedge_outranks_degraded(self):
        # Severity precedence: a wedge anywhere FAILs even alongside a
        # degraded box (wedge=FAIL > degraded=CONCERN).
        status, msg = msw.classify_day(
            _day(watchdog=[_wd(name="moc", wedge=1),
                           _wd(name="moc1", degraded=1)]))
        assert status == "FAIL"
        assert "C6" in msg


def _full_window(**day_kwargs):
    """One clean record for every day of the window."""
    out = []
    for date in msw._window_days():
        rec = _day(date=date, **day_kwargs)
        d = {"date": rec.date, "since": rec.since, "hosts": rec.hosts,
             "canary_ok": rec.canary_ok, "canary_concern": rec.canary_concern,
             "canary_fail": rec.canary_fail,
             "canary_observable": rec.canary_observable,
             "watchdog": rec.watchdog}
        out.append(d)
    return out


class TestRenderFinal:
    def test_clean_window_passes_and_opens_retire_gate(self):
        status, msg = msw.render_final(_full_window())
        assert status == "OK"
        assert "PASS" in msg
        assert "Retire-decision gate OPEN" in msg

    def test_missing_days_fail(self):
        evidence = _full_window()[:-3]  # drop the last 3 days
        status, msg = msw.render_final(evidence)
        assert status == "FAIL"
        assert "no evidence" in msg

    def test_empty_evidence_fails_not_passes(self):
        # Absence of evidence is not a clean soak.
        status, msg = msw.render_final([])
        assert status == "FAIL"

    def test_one_stop_kill_anywhere_fails_c3(self):
        evidence = _full_window()
        evidence[5]["hosts"] = [_host(stop_kills=1)]
        status, msg = msw.render_final(evidence)
        assert status == "FAIL"
        assert "C3" in msg

    def test_canary_fails_accumulate_c4(self):
        evidence = _full_window()
        evidence[2]["canary_fail"] = 2
        status, msg = msw.render_final(evidence)
        assert status == "FAIL"
        assert "C4" in msg

    def test_two_blind_days_one_host_fails_c5(self):
        evidence = _full_window()
        evidence[1]["hosts"] = [_host(observable=False)]
        evidence[2]["hosts"] = [_host(observable=False)]
        status, msg = msw.render_final(evidence)
        assert status == "FAIL"
        assert "C5" in msg

    def test_single_blind_day_does_not_fail(self):
        evidence = _full_window()
        evidence[1]["hosts"] = [_host(observable=False)]
        status, msg = msw.render_final(evidence)
        assert status == "OK"

    def test_healed_inversions_with_witness_still_pass(self):
        evidence = _full_window()
        evidence[4]["hosts"] = [
            _host(exit75=1, restarts=2, collision_edges=2)]
        status, msg = msw.render_final(evidence)
        assert status == "OK"

    def test_out_of_window_records_ignored(self):
        evidence = _full_window()
        evidence.append({"date": "2026-06-24", "since": "x",
                         "hosts": [_host(stop_kills=9)],
                         "canary_ok": 0, "canary_concern": 0,
                         "canary_fail": 0, "canary_observable": True,
                         "watchdog": _clean_wd()})
        status, _ = msw.render_final(evidence)
        assert status == "OK"

    def test_watchdog_wedge_day_fails_final_c6(self):
        # A wedge on any window day = not a clean window -> final FAIL.
        evidence = _full_window()
        evidence[6]["watchdog"] = [_wd(name="moc", wedge=1,
                                       detail="lxmf_process_wedge(wedge)")]
        status, msg = msw.render_final(evidence)
        assert status == "FAIL"
        assert "C6" in msg

    def test_watchdog_degraded_window_still_passes_with_note(self):
        # Degraded/unobservable are CONCERN noted, NOT soak-fatal (C4-style).
        evidence = _full_window()
        evidence[3]["watchdog"] = [_wd(name="moc1", degraded=1)]
        evidence[8]["watchdog"] = [_wd(name="moc2", observable=False)]
        status, msg = msw.render_final(evidence)
        assert status == "OK"
        assert "C6 CONCERN" in msg

    def test_clean_watchdog_window_passes(self):
        # C6 must not block an otherwise-clean window.
        status, msg = msw.render_final(_full_window())
        assert status == "OK"
        assert "PASS" in msg

    def test_render_final_tolerates_missing_watchdog_field(self):
        # Older evidence lines (pre-C6) have no watchdog key; .get([])
        # must not crash and must not false-fire.
        evidence = _full_window()
        for d in evidence:
            d.pop("watchdog", None)
        status, msg = msw.render_final(evidence)
        assert status == "OK"


class TestWindowDays:
    def test_window_is_fourteen_days(self):
        days = msw._window_days()
        assert len(days) == 14
        assert days[0] == "2026-06-10"
        assert days[-1] == "2026-06-23"


import json as _json
import time as _time


class TestCollectWatchdog:
    """Pin collect_watchdog's honest-failure modes via the _run seam —
    no ssh. An unobservable watchdog must NEVER read as healthy
    (wedge=0/degraded=0 on a clean read only)."""

    @staticmethod
    def _patch_run(monkeypatch, raw):
        # collect_watchdog calls _run(_host_cmd(host, ...)) — replace _run.
        monkeypatch.setattr(msw, "_run", lambda *a, **k: raw)

    @staticmethod
    def _payload(box_now, signals, ts):
        wd = _json.dumps({"signals": signals, "ts": ts})
        return f"{box_now}\n{msw.WD_SEP}\n{wd}"

    def test_meshanchor_excluded_from_watchdog_hosts(self):
        assert "meshanchor-server" not in msw.WATCHDOG_HOSTS
        assert "local" in msw.WATCHDOG_HOSTS
        assert set(msw.WATCHDOG_HOSTS) <= set(msw.HOSTS)

    def test_clean_read_is_observable_zero(self, monkeypatch):
        now = int(_time.time())
        self._patch_run(monkeypatch, self._payload(now, [], now))
        r = msw.collect_watchdog("moc")
        assert r["observable"] is True
        assert r["wedge"] == 0 and r["degraded"] == 0

    def test_wedge_signal_counted(self, monkeypatch):
        now = int(_time.time())
        sigs = [{"class": "channel_feed_dark", "severity": "wedge"},
                {"class": "role_drift", "severity": "degraded"}]
        self._patch_run(monkeypatch, self._payload(now, sigs, now))
        r = msw.collect_watchdog("moc")
        assert r["observable"] is True
        assert r["wedge"] == 1 and r["degraded"] == 1
        assert "channel_feed_dark(wedge)" in r["detail"]

    def test_unreachable_host_is_unobservable(self, monkeypatch):
        # _run returns None on any command failure.
        self._patch_run(monkeypatch, None)
        r = msw.collect_watchdog("moc")
        assert r["observable"] is False
        assert r["wedge"] == 0 and r["degraded"] == 0

    def test_missing_json_is_unobservable_not_clean(self, monkeypatch):
        # date printed, separator present, but cat produced nothing.
        now = int(_time.time())
        self._patch_run(monkeypatch, f"{now}\n{msw.WD_SEP}\n")
        r = msw.collect_watchdog("moc")
        assert r["observable"] is False

    def test_unparseable_json_is_unobservable_not_clean(self, monkeypatch):
        now = int(_time.time())
        self._patch_run(
            monkeypatch, f"{now}\n{msw.WD_SEP}\nNOT JSON {{")
        r = msw.collect_watchdog("moc")
        assert r["observable"] is False

    def test_missing_separator_is_unobservable(self, monkeypatch):
        self._patch_run(monkeypatch, '{"signals": [], "ts": 1}')
        r = msw.collect_watchdog("moc")
        assert r["observable"] is False

    def test_signals_absent_is_unobservable_not_clean(self, monkeypatch):
        # No "signals" key at all must NOT read as zero-signal clean.
        now = int(_time.time())
        wd = _json.dumps({"ts": now})
        self._patch_run(monkeypatch, f"{now}\n{msw.WD_SEP}\n{wd}")
        r = msw.collect_watchdog("moc")
        assert r["observable"] is False

    def test_stale_snapshot_is_unobservable(self, monkeypatch):
        # ts older than the box's own clock by > WD_STALE_S = wedged loop.
        now = int(_time.time())
        old = now - (msw.WD_STALE_S + 100)
        self._patch_run(monkeypatch, self._payload(now, [], old))
        r = msw.collect_watchdog("moc")
        assert r["observable"] is False
        assert r["stale"] is True

    def test_fresh_snapshot_within_threshold_is_observable(self, monkeypatch):
        now = int(_time.time())
        recent = now - (msw.WD_STALE_S - 50)
        self._patch_run(monkeypatch, self._payload(now, [], recent))
        r = msw.collect_watchdog("moc")
        assert r["observable"] is True
        assert r["stale"] is False
