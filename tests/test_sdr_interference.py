"""SDR Phase 1 step 2 — scripts/sdr_interference.py (the writer).

Capture is injected: every test feeds REAL moc5 IQ (tests/fixtures/sdr/)
through the writer's aggregation, never a live Airspy. What is pinned is the
honesty contract of a row: a dead SDR writes `unknown` with reasons and no
floor; our own TX writes `unjudgeable`; a clipped front end writes
`overload`; a refused run still leaves a witness row; a carrier is a
finding only after two consecutive runs.
"""
import fcntl
import importlib.util
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
_spec = importlib.util.spec_from_file_location("sdr_interference", _ROOT / "scripts" / "sdr_interference.py")
si = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(si)

from utils import sdr_analysis as sa  # noqa: E402

FX = _ROOT / "tests" / "fixtures" / "sdr"
Q906 = np.load(FX / "fx_906_quiet.npy")
Q910 = np.load(FX / "fx_910_quiet.npy")
NEARTX = np.load(FX / "fx_lf_neartx.npy")


def quiet_capture(center, gain):
    return (Q910 if center > 909 else Q906), None


def dead_capture(center, gain):
    return None, "airspy_rx rc=1: AIRSPY_ERROR_NOT_FOUND"


def _with_carrier(raw, center, target_mhz, above_db=25):
    iq = raw[0::2].astype(np.float64) + 1j * raw[1::2].astype(np.float64)
    floor = sa.analyse_window(raw, center)["floor_dbfs"]
    step = sa.SAMPLE_RATE / sa.FFT
    off = round((target_mhz - center) * 1e6 / step) * step
    iq = iq + sa.FULL_SCALE * 10 ** ((floor + above_db) / 20) * np.exp(
        2j * np.pi * off * np.arange(iq.size) / sa.SAMPLE_RATE)
    out = np.empty(raw.size, np.int16)
    out[0::2] = np.round(iq.real)
    out[1::2] = np.round(iq.imag)
    return out


def test_quiet_run_is_ok_with_no_findings_and_honest_b_deltas():
    row = si.run_fleet(quiet_capture, None, {}, [])
    assert row["status"] == "ok"
    for key, w in row["windows"].items():
        assert w["status"] == "ok", key
        assert w["carriers"] == [] and w["carriers_persistent"] == [] and w["foreign"] == []
        assert w["b"]["status"] == "ok"
        assert w["b"]["delta_ref_db"] is None          # no reference yet — None, never 0
        assert w["b"]["delta_rolling_db"] is None      # < 12 rows of history


def test_dead_sdr_is_unknown_with_reasons_and_no_floor():
    row = si.run_fleet(dead_capture, None, {}, [])
    assert row["status"] == "unknown"
    for w in row["windows"].values():
        assert w["status"] == "unknown" and w["b"]["status"] == "unknown"
        assert "floor_dbfs" not in w and "carriers" not in w and "foreign" not in w
        assert "AIRSPY_ERROR_NOT_FOUND" in w["reasons"][0]


def test_our_own_tx_is_unjudgeable_not_ok():
    row = si.run_fleet(lambda c, g: (NEARTX, None), None, {}, [])
    w = row["windows"]["906.300"]
    assert w["status"] == "unjudgeable"
    assert "foreign" not in w and w["own_tx_frames"] > 0
    assert row["status"] != "ok"


def test_a_clipped_front_end_is_overload():
    clipped = Q906.copy()
    clipped[::50] = -32768                               # the negative rail, the one int16 abs() hid
    row = si.run_fleet(lambda c, g: (clipped, None), None, {}, [])
    assert all(w["status"] == "overload" for w in row["windows"].values())


def test_a_carrier_becomes_persistent_only_on_the_second_run():
    planted = _with_carrier(Q906, 906.3, 907.30)
    cap = lambda c, g: (planted if abs(c - 906.3) < 1e-6 else quiet_capture(c, g)[0], None)  # noqa: E731
    first = si.run_fleet(cap, None, {}, [])
    w1 = first["windows"]["906.300"]
    assert len(w1["carriers"]) == 1 and w1["carriers_persistent"] == []
    second = si.run_fleet(cap, first, {}, [first])
    assert len(second["windows"]["906.300"]["carriers_persistent"]) == 1
    # a previous run that was NOT ok confirms nothing
    dead_prev = si.run_fleet(dead_capture, None, {}, [])
    third = si.run_fleet(cap, dead_prev, {}, [dead_prev])
    assert third["windows"]["906.300"]["carriers_persistent"] == []


def test_class_b_rolling_delta_appears_after_twelve_runs():
    hist = [si.run_fleet(quiet_capture, None, {}, [])] * 12
    row = si.run_fleet(quiet_capture, hist[-1], {}, hist)
    assert abs(row["windows"]["906.300"]["b"]["delta_rolling_db"]) < 0.01


def test_class_b_reference_delta_uses_the_fixed_reference():
    ref_prof = sa.analyse_window(Q906, 906.3)["floor_profile"] - 3.0   # reference 3 dB quieter
    row = si.run_fleet(quiet_capture, None, {906.3: ref_prof}, [])
    assert 2.9 < row["windows"]["906.300"]["b"]["delta_ref_db"] < 3.1


def test_adjacent_pass_covers_870_to_940():
    row = si.run_adjacent(quiet_capture)
    centers = [w["center_mhz"] for w in row["windows"]]
    assert row["status"] == "ok" and len(centers) == 30
    assert centers[0] == 870.2 and centers[-1] <= 940.0
    dead = si.run_adjacent(dead_capture)
    assert dead["status"] == "unknown" and all("reason" in w for w in dead["windows"])


# ---- persistence + the witness rows ----------------------------------------------

@pytest.fixture
def ddir(tmp_path, monkeypatch):
    monkeypatch.setattr(si, "data_dir", lambda: tmp_path / "sdr")
    return tmp_path / "sdr"


def test_a_refused_run_still_writes_a_witness_row(ddir):
    ddir.mkdir()
    with open(ddir / "run.lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert si.main(["--mode", "fleet"]) == 0
    rows = si.read_rows(ddir / "interference.jsonl")
    assert rows[-1]["status"] == "skipped_overlap"


def test_missing_airspy_rx_writes_unknown_not_silence(ddir, monkeypatch):
    monkeypatch.setattr(si.shutil, "which", lambda *_a, **_k: None)
    assert si.main(["--mode", "fleet"]) == 0
    row = si.read_rows(ddir / "interference.jsonl")[-1]
    assert row["status"] == "unknown" and "not installed" in row["note"]
    assert row["v"] == si.SCHEMA and "ts" in row and "soc_temp_c" in row


def test_read_rows_skips_a_torn_line_and_rotation_keeps_one(ddir, monkeypatch):
    p = ddir / "interference.jsonl"
    si.append_row(p, {"a": 1})
    with open(p, "a") as fh:
        fh.write('{"torn": ')                             # a crash mid-write
    si.append_row(p, {"a": 2})
    assert [r.get("a") for r in si.read_rows(p)] == [1, 2]
    monkeypatch.setattr(si, "MAX_BYTES", 10)
    si.append_row(p, {"a": 3})
    assert (ddir / "interference.jsonl.1").exists()
    assert [r["a"] for r in si.read_rows(p)] == [3]


def test_an_unwritable_data_dir_exits_2(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(si, "data_dir", lambda: blocker / "sdr")   # a FILE in the path
    assert si.main(["--mode", "fleet"]) == 2


def test_an_unreadable_reference_reads_as_none(ddir):
    ddir.mkdir()
    (ddir / f"reference_g{si.GAIN_B}.npz").write_bytes(b"not an npz")
    assert si.load_references(ddir) == {}
