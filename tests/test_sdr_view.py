"""SDR Phase 1 step 4 — utils/sdr_view.py (the read-only pane's renderer).

Rows are produced by the WRITER's own run_fleet/run_adjacent on the real
moc5 fixtures, so the reader is pinned to the schema the writer actually
emits — not to a hand-written row the author imagined.
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
_spec = importlib.util.spec_from_file_location("sdr_interference", _ROOT / "scripts" / "sdr_interference.py")
si = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(si)

from utils import sdr_view as v  # noqa: E402

FX = _ROOT / "tests" / "fixtures" / "sdr"
Q906 = np.load(FX / "fx_906_quiet.npy")
Q910 = np.load(FX / "fx_910_quiet.npy")
NOW = 1_790_000_000.0


def quiet(c, g):
    return (Q910 if c > 909 else Q906), None


def dead(c, g):
    return None, "airspy_rx rc=1: airspy_open() failed: AIRSPY_ERROR_NOT_FOUND (-5)"


def _row(frag, ts):
    return dict(frag, v=1, ts=ts, host="moc5")


def test_reader_and_writer_agree_on_the_path(tmp_path, monkeypatch):
    """hfm #5: two consumers of one artifact share ONE location."""
    import utils.paths as paths
    monkeypatch.setattr(paths, "get_real_user_home", lambda: tmp_path)
    assert v.jsonl_path() == si.data_dir() / "interference.jsonl"


def test_absent_file_says_absent_and_how_to_check():
    t = v.render("absent", [], now=NOW)
    assert "absent" in t and "meshforge-sdr.timer" in t
    assert "no interference" not in t.lower()
    assert v.BLIND_SPOTS in t


@pytest.mark.parametrize("state", ["unreadable", "ok"])
def test_unreadable_or_empty_is_unknown(state):
    t = v.render(state, [], now=NOW)
    assert "UNKNOWN" in t and v.BLIND_SPOTS in t


def test_a_fresh_quiet_run_reads_fresh_with_every_class_accounted():
    rows = [_row(si.run_fleet(quiet, None, {}, []), NOW - 60),
            _row(si.run_adjacent(quiet), NOW - 600)]
    rows[0]["reference"] = "absent"
    t = v.render("ok", rows, now=NOW)
    assert t.count("fresh") == 3 and "STALE" not in t
    assert "C: no foreign energy seen above the floor" in t
    assert "A: no persistent carrier seen" in t
    assert "vs reference no reference" in t and "< 1 h of history" in t
    assert "Class D" in t and "UNKNOWN" not in t.split("Class D")[1].split("\n")[0]
    assert v.BLIND_SPOTS in t


def test_old_rows_read_stale_unknown():
    rows = [_row(si.run_fleet(quiet, None, {}, []), NOW - 20 * 60)]
    t = v.render("ok", rows, now=NOW)
    assert t.count("STALE — UNKNOWN") == 3


def test_the_witness_is_the_newest_OK_window_not_the_newest_row():
    """R9: fresh unknown rows must not make the witness read fresh."""
    ok = _row(si.run_fleet(quiet, None, {}, []), NOW - 20 * 60)
    dead_rows = [_row(si.run_fleet(dead, None, {}, []), NOW - 60 * k) for k in (3, 2, 1)]
    t = v.render("ok", [ok] + dead_rows, now=NOW)
    assert t.count("STALE — UNKNOWN") == 3
    assert "captured NOTHING" in t and "AIRSPY_ERROR_NOT_FOUND" in t
    assert "reseat" in t and "Never reset the USB hub" in t
    assert "Newest row: 60 s ago · fleet UNKNOWN" in t


def test_a_persistent_carrier_shows_its_recurrence():
    t_mod = importlib.util.spec_from_file_location("_sdr_i_tests", _ROOT / "tests" / "test_sdr_interference.py")
    m = importlib.util.module_from_spec(t_mod)
    t_mod.loader.exec_module(m)
    planted = m._with_carrier(Q906, 906.3, 907.30)
    cap = lambda c, g: (planted if abs(c - 906.3) < 1e-6 else quiet(c, g)[0], None)  # noqa: E731
    r1 = _row(si.run_fleet(cap, None, {}, []), NOW - 360)
    r2 = _row(si.run_fleet(cap, r1, {}, [r1]), NOW - 60)
    t = v.render("ok", [r1, r2], now=NOW)
    assert "A carrier 907.30" in t and "seen in 2 of 2 ok runs, first 6 min ago" in t


def test_a_future_timestamp_is_flagged_not_fresh():
    rows = [_row(si.run_fleet(quiet, None, {}, []), NOW + 3600)]
    t = v.render("ok", rows, now=NOW)
    assert "FUTURE" in t and t.count("STALE — UNKNOWN") == 3


def test_no_adjacent_pass_is_unknown():
    rows = [_row(si.run_fleet(quiet, None, {}, []), NOW - 60)]
    assert "no hourly pass recorded yet — UNKNOWN" in v.render("ok", rows, now=NOW)


def test_load_skips_torn_lines_and_reports_unreadable(tmp_path):
    p = tmp_path / "interference.jsonl"
    p.write_text(json.dumps({"mode": "fleet", "ts": 1}) + "\n" + '{"torn": \n' + json.dumps({"mode": "fleet", "ts": 2}) + "\n")
    state, rows = v.load(p)
    assert state == "ok" and [r["ts"] for r in rows] == [1, 2]
    assert v.load(tmp_path / "missing.jsonl") == ("absent", [])
    d = tmp_path / "isdir.jsonl"
    d.mkdir()
    assert v.load(d)[0] == "unreadable"


def test_load_reaches_into_the_rotated_file(tmp_path):
    p = tmp_path / "interference.jsonl"
    (tmp_path / "interference.jsonl.1").write_text("".join(json.dumps({"ts": i}) + "\n" for i in range(3)))
    p.write_text(json.dumps({"ts": 3}) + "\n")
    assert [r["ts"] for r in v.load(p, limit=3)[1]] == [1, 2, 3]
