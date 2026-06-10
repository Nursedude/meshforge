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


def _day(date="2026-06-11", hosts=None, canary_ok=24, canary_concern=0,
         canary_fail=0, canary_observable=True):
    rec = msw.DayRecord(date=date, since="x")
    rec.hosts = hosts if hosts is not None else [_host()]
    rec.canary_ok = canary_ok
    rec.canary_concern = canary_concern
    rec.canary_fail = canary_fail
    rec.canary_observable = canary_observable
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


def _full_window(**day_kwargs):
    """One clean record for every day of the window."""
    out = []
    for date in msw._window_days():
        rec = _day(date=date, **day_kwargs)
        d = {"date": rec.date, "since": rec.since, "hosts": rec.hosts,
             "canary_ok": rec.canary_ok, "canary_concern": rec.canary_concern,
             "canary_fail": rec.canary_fail,
             "canary_observable": rec.canary_observable}
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
                         "canary_fail": 0, "canary_observable": True})
        status, _ = msw.render_final(evidence)
        assert status == "OK"


class TestWindowDays:
    def test_window_is_fourteen_days(self):
        days = msw._window_days()
        assert len(days) == 14
        assert days[0] == "2026-06-10"
        assert days[-1] == "2026-06-23"
