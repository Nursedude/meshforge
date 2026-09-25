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


def _analysis_tests():
    """The planting helpers live in test_sdr_analysis.py; load it by path."""
    spec = importlib.util.spec_from_file_location("_sdr_analysis_tests", _ROOT / "tests" / "test_sdr_analysis.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
    # rotation no longer drops history: read_rows reaches into .1 (review #11)
    assert [r["a"] for r in si.read_rows(p)] == [1, 2, 3]


def test_an_unwritable_data_dir_exits_2(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(si, "data_dir", lambda: blocker / "sdr")   # a FILE in the path
    assert si.main(["--mode", "fleet"]) == 2


def test_an_unreadable_reference_reads_as_unreadable_never_zero(ddir):
    ddir.mkdir()
    (ddir / f"reference_g{si.GAIN_B}.npz").write_bytes(b"not an npz")   # BadZipFile
    assert si.load_references(ddir) == ({}, "unreadable")
    assert si.load_references(ddir.parent / "nope") == ({}, "absent")


# ---- review 2026-09-24 (Fable, non-author) ------------------------------------------

def test_set_reference_merges_and_never_destroys(ddir):
    ddir.mkdir()
    good = {f"{c}": np.full(10, -80.0) for c in si.FLEET_WINDOWS}
    np.savez(ddir / f"reference_g{si.GAIN_B}.npz", **good)
    only906 = lambda c, g: (Q906, None) if abs(c - 906.3) < 1e-6 else (None, "busy")  # noqa: E731
    row = si.set_reference(ddir, only906)
    refs, state = si.load_references(ddir)
    assert state == "ok" and sorted(refs) == sorted(si.FLEET_WINDOWS)     # 903/910 kept
    assert row["status"] == "partial" and row["windows_updated"] == [906.3]
    row = si.set_reference(ddir, dead_capture)
    assert row["status"] == "unknown"
    assert sorted(si.load_references(ddir)[0]) == sorted(si.FLEET_WINDOWS)  # untouched
    assert not list(ddir.glob("*.tmp.npz"))


def test_an_exception_still_writes_a_witness_row(ddir, monkeypatch):
    monkeypatch.setattr(si.shutil, "which", lambda *_a, **_k: "/usr/bin/airspy_rx")
    monkeypatch.setattr(si, "airspy_capture", lambda c, g: (np.zeros(4097, np.int16), None))
    assert si.main(["--mode", "fleet"]) == 1          # row written AND systemd told (review #5)
    row = si.read_rows(ddir / "interference.jsonl")[-1]
    assert row["status"] == "error" and "ValueError" in row["note"]


def test_class_b_dead_makes_the_run_partial_with_reasons():
    cap = lambda c, g: (quiet_capture(c, g)[0], None) if g == si.GAIN_AC else (None, "AIRSPY_ERROR_BUSY")  # noqa: E731
    row = si.run_fleet(cap, None, {}, [])
    assert row["status"] == "partial"
    w = row["windows"]["906.300"]
    assert w["status"] == "ok" and w["b"]["status"] == "unknown"
    assert "AIRSPY_ERROR_BUSY" in w["b"]["reasons"][0]


def test_one_judged_burst_of_four_is_partial_and_confirms_nothing():
    planted = _with_carrier(Q906, 906.3, 907.30)
    calls = {"n": 0}

    def cap(c, g):
        if abs(c - 906.3) < 1e-6 and g == si.GAIN_AC:
            calls["n"] += 1
            return (planted if calls["n"] % 4 == 1 else NEARTX), None
        return quiet_capture(c, g)[0], None
    first = si.run_fleet(cap, None, {}, [])
    w = first["windows"]["906.300"]
    assert w["status"] == "partial" and w["carriers"] == []        # 1 of 4: no majority
    second = si.run_fleet(cap, first, {}, [first])
    assert second["windows"]["906.300"]["carriers_persistent"] == []


def test_foreign_merges_across_bursts_keeping_the_max():
    # two bursts with the same foreign slice at different duty
    t = _analysis_tests()
    lo = t._plant_band_noise(Q906, 906.3, 906.40, 100, 20, frames=[3, 4, 5])
    hi = t._plant_band_noise(Q906, 906.3, 906.40, 100, 20, frames=list(range(3, 17)))
    seq = iter([lo, hi, Q906, Q906])
    cap = lambda c, g: (next(seq), None) if abs(c - 906.3) < 1e-6 and g == si.GAIN_AC else quiet_capture(c, g)  # noqa: E731
    w = si.run_fleet(cap, None, {}, [])["windows"]["906.300"]
    hit = [f for f in w["foreign"] if abs(f["slice_mhz"] - 906.40) <= 0.0625]
    assert hit and hit[0]["bursts"] == 2 and hit[0]["busy_pct"] > 40


def test_a_mismatched_reference_reads_none_not_a_crash():
    row = si.run_fleet(quiet_capture, None, {906.3: np.zeros(5)}, [])
    assert row["windows"]["906.300"]["b"]["delta_ref_db"] is None


def test_a_skipped_row_does_not_reset_persistence(ddir, monkeypatch):
    planted = _with_carrier(Q906, 906.3, 907.30)
    cap = lambda c, g: (planted if abs(c - 906.3) < 1e-6 else quiet_capture(c, g)[0], None)  # noqa: E731
    first = dict(si.run_fleet(cap, None, {}, []), v=1, ts=1.0)
    p = ddir / "interference.jsonl"
    si.append_row(p, first)
    si.append_row(p, {"v": 1, "ts": 2.0, "mode": "fleet", "status": "skipped_overlap"})
    monkeypatch.setattr(si.shutil, "which", lambda *_a, **_k: "/usr/bin/airspy_rx")
    monkeypatch.setattr(si, "airspy_capture", cap)
    assert si.main(["--mode", "fleet"]) == 0
    row = si.read_rows(p)[-1]
    assert len(row["windows"]["906.300"]["carriers_persistent"]) == 1
    assert row["reference"] == "absent"


def test_adjacent_headline_is_never_our_own_channel():
    t = _analysis_tests()
    floor = sa.analyse_window(Q906, 906.3)["floor_dbfs"]
    own = t._plant_tone(Q906, 906.3, 906.875, 40, floor)          # a loud LF carrier
    row = si.run_adjacent(lambda c, g: (own, None))
    for w in row["windows"]:
        assert not (906.75 <= w["peak"]["freq_mhz"] <= 907.0), w


def test_read_rows_returns_the_full_window_even_with_big_rows(ddir, monkeypatch):
    p = ddir / "interference.jsonl"
    big = {"pad": "x" * 11000}
    for i in range(300):
        si.append_row(p, dict(big, i=i))
    rows = si.read_rows(p, limit=289)
    assert len(rows) == 289 and rows[-1]["i"] == 299 and rows[0]["i"] == 11


def test_read_rows_reaches_into_the_rotated_file(ddir, monkeypatch):
    p = ddir / "interference.jsonl"
    for i in range(5):
        si.append_row(p, {"i": i})
    monkeypatch.setattr(si, "MAX_BYTES", 10)
    si.append_row(p, {"i": 5})                                      # rotates first
    assert [r["i"] for r in si.read_rows(p, limit=4)] == [2, 3, 4, 5]


def test_a_truncated_reference_npz_is_unreadable(ddir):
    """Review #3: a half-written npz raises zipfile.BadZipFile, which a
    narrower except once let escape and kill the run."""
    ddir.mkdir()
    full = ddir / "full.npz"
    np.savez(full, **{"906.3": np.zeros(100)})
    (ddir / f"reference_g{si.GAIN_B}.npz").write_bytes(full.read_bytes()[:120])
    assert si.load_references(ddir) == ({}, "unreadable")


def test_any_exception_type_still_writes_a_witness_row(ddir, monkeypatch):
    monkeypatch.setattr(si.shutil, "which", lambda *_a, **_k: "/usr/bin/airspy_rx")

    def boom(*_a, **_k):
        raise KeyError("windows")
    monkeypatch.setattr(si, "run_fleet", boom)
    assert si.main(["--mode", "fleet"]) == 1          # row written AND systemd told (review #5)
    row = si.read_rows(ddir / "interference.jsonl")[-1]
    assert row["status"] == "error" and "KeyError" in row["note"]


# ---- second non-author review (Fable, 2026-09-24) — boundaries the first set missed ----

def test_two_judged_bursts_of_four_is_partial_not_ok():
    """Kills M8: the A/C window needs a MAJORITY (3 of 4); 2 of 4 is `partial`."""
    calls = {"n": 0}

    def cap(c, g):
        if g == si.GAIN_AC and abs(c - 906.3) < 1e-6:
            calls["n"] += 1
            return (Q906 if calls["n"] % 2 else NEARTX), None
        return Q906, None
    w = si.run_fleet(cap, None, {}, [])["windows"]["906.300"]
    assert w["bursts"]["ok"] == 2 and w["bursts"]["unjudgeable"] == 2
    assert w["status"] == "partial"
    assert si._window_status({"ok": 3, "overload": 1, "unjudgeable": 0, "failed": 0}) == "ok"


def test_class_b_is_ok_on_one_quiet_burst_of_two():
    """Kills M9: B judges only the quiet bursts by design; 1 of 2 is `ok`."""
    calls = {"n": 0}

    def cap(c, g):
        if g == si.GAIN_B and abs(c - 906.3) < 1e-6:
            calls["n"] += 1
            return (Q906 if calls["n"] % 2 else NEARTX), None
        return Q906, None
    row = si.run_fleet(cap, None, {}, [])
    w = row["windows"]["906.300"]
    assert w["b"]["bursts"] == {"ok": 1, "overload": 0, "unjudgeable": 1, "failed": 0}
    assert w["b"]["status"] == "ok" and "floor_dbfs" in w["b"]
    assert row["status"] == "ok"


def test_tail_lines_exact_boundary(tmp_path):
    """Kills M17: a chunk landing on exactly `want` newlines leaves a partial first line."""
    p = tmp_path / "x.jsonl"
    with open(p, "w") as fh:
        for i in range(12):
            fh.write(json.dumps({"i": i, "pad": "x" * 7000}) + "\n")
    for want in range(1, 12):
        assert [r["i"] for r in si.read_rows(p, want)] == list(range(12 - want, 12)), want


def test_an_error_row_exits_1_so_systemd_sees_it(ddir, monkeypatch):
    """Review #5: exit 0 on a crashed analysis made it invisible to the
    fleet's user-unit probe. The row is still written."""
    monkeypatch.setattr(si.shutil, "which", lambda *_a, **_k: "/usr/bin/airspy_rx")
    monkeypatch.setattr(si, "run_fleet", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert si.main(["--mode", "fleet"]) == 1
    assert si.read_rows(ddir / "interference.jsonl")[-1]["status"] == "error"


def test_every_row_carries_the_analysis_stamp(ddir, monkeypatch):
    monkeypatch.setattr(si.shutil, "which", lambda *_a, **_k: None)
    si.main(["--mode", "fleet"])
    row = si.read_rows(ddir / "interference.jsonl")[-1]
    assert row["analysis"] == si.analysis_stamp() and len(row["analysis"]) == 10



# ---- class D: products of our own channels are never the headline (2026-09-24) ----

def _clean_freq(center):
    """A bin-centred frequency in this window clear of our channels and their products."""
    f = center + np.fft.fftshift(np.fft.fftfreq(sa.FFT, 1 / sa.SAMPLE_RATE)) / 1e6
    prod, _ = si._products_mask(f, center)
    ok = (np.abs(f - center) <= 1.0) & ~prod & ~sa._in_bands(f, sa.FLEET_CHANNELS, sa.GUARD_KHZ + 50)
    return float(f[np.flatnonzero(ok)[len(np.flatnonzero(ok)) // 2]])


def test_class_d_labels_an_alias_it_does_not_hide_it():
    """Labelled, not excluded: excluding product positions left 0 % clean bins
    in three windows (measured) and would blind class D to a real blocker."""
    t = _analysis_tests()
    floor = sa.analyse_window(Q906, 906.3)["floor_dbfs"]
    mirror = 2 * 911.0 - (906.875 + 3.0)                      # LF's image in the 911.0 window
    img = t._plant_tone(Q906, 911.0, mirror, 30, floor)
    row = si.run_adjacent(lambda c, g: ((img if abs(c - 911.0) < 1e-6 else Q906), None))
    w = next(w for w in row["windows"] if abs(w["center_mhz"] - 911.0) < 1e-6)
    assert abs(w["peak"]["freq_mhz"] - mirror) < 0.003
    assert any("alias" in tag and "LF" in tag for tag in w["peak"]["tags"])
    assert w["clean_peak"] is None or abs(w["clean_peak"]["freq_mhz"] - mirror) > 0.1
    assert 0 < w["clean_frac"] < 1


def test_class_d_never_goes_blind_on_a_fully_covered_window():
    row = si.run_adjacent(quiet_capture)
    w = next(w for w in row["windows"] if abs(w["center_mhz"] - 908.6) < 1e-6)
    assert w["status"] == "ok" and w["peak"] is not None
    assert w["clean_frac"] == 0.0 and w["clean_peak"] is None


def test_class_d_headline_still_finds_a_real_clean_signal():
    t = _analysis_tests()
    floor = sa.analyse_window(Q906, 906.3)["floor_dbfs"]
    real = _clean_freq(911.0)
    sig = t._plant_tone(Q906, 911.0, real, 30, floor)
    row = si.run_adjacent(lambda c, g: ((sig if abs(c - 911.0) < 1e-6 else Q906), None))
    w = next(w for w in row["windows"] if abs(w["center_mhz"] - 911.0) < 1e-6)
    assert abs(w["peak"]["freq_mhz"] - real) < 0.003 and w["peak"]["above_floor_db"] > 25
    assert w["peak"]["tags"] is None
    assert abs(w["clean_peak"]["freq_mhz"] - real) < 0.003


def test_the_live_911_848_line_sits_on_our_own_im3():
    f = 911.0 + np.fft.fftshift(np.fft.fftfreq(sa.FFT, 1 / sa.SAMPLE_RATE)) / 1e6
    _m, prods = si._products_mask(f, 911.0)
    assert any(abs(911.848 - c) <= hw and "meshcore" in p for c, hw, p in prods)
