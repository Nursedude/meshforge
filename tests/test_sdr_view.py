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



def test_recurrence_uses_only_rows_from_the_current_analysis_code():
    old = _row(si.run_fleet(quiet, None, {}, []), NOW - 600)            # no stamp = old code
    new = [dict(_row(si.run_fleet(quiet, None, {}, []), NOW - 60 * k), analysis="abc1234567") for k in (2, 1)]
    t = v.render("ok", [old] + new, now=NOW)
    assert "1 older-code rows excluded" in t and "use 2 rows" in t



def test_an_alias_tagged_slice_is_labelled_a_receiver_product():
    t_mod = importlib.util.spec_from_file_location("_sdr_a_tests", _ROOT / "tests" / "test_sdr_analysis.py")
    m = importlib.util.module_from_spec(t_mod)
    t_mod.loader.exec_module(m)
    img = m._plant_band_noise(Q910, 910.525, 911.175, 100, 20, frames=[2, 3, 9, 15, 16, 24])
    cap = lambda c, g: ((img if c > 909 else Q906), None)  # noqa: E731
    t = v.render("ok", [_row(si.run_fleet(cap, None, {}, []), NOW - 60)], now=NOW)
    assert "C foreign 911.1" in t and "alias of our own channel — receiver product" in t



def test_class_d_labels_a_product_line_as_a_candidate():
    t_mod = importlib.util.spec_from_file_location("_sdr_a_tests2", _ROOT / "tests" / "test_sdr_analysis.py")
    m = importlib.util.module_from_spec(t_mod)
    t_mod.loader.exec_module(m)
    floor = m.a.analyse_window(Q906, 906.3)["floor_dbfs"]
    mirror = 2 * 911.0 - (906.875 + 3.0)
    img = m._plant_tone(Q906, 911.0, mirror, 30, floor)
    adj = _row(si.run_adjacent(lambda c, g: ((img if abs(c - 911.0) < 1e-6 else Q906), None)), NOW - 60)
    t = v.render("ok", [_row(si.run_fleet(quiet, None, {}, []), NOW - 60), adj], now=NOW)
    line = next(ln for ln in t.splitlines() if f"{mirror:.3f} MHz" in ln)
    assert "at a product position of OUR channels (candidate; " in line and "% of this window is product positions" in line
    assert "alias-mirror of meshtastic-LF-ch20" in line                 # full label, not cut mid-word (#5)



# ---- review 3 (Fable, 2026-09-24): class D legibility ----

def _adj_row(windows):
    return {"mode": "adjacent", "status": "ok", "ts": NOW - 60, "windows": windows}


def _w(freq, above, tags=None, product_frac=0.2, clean=None):
    return {"center_mhz": 900.0, "status": "ok", "product_frac": product_frac,
            "peak": {"freq_mhz": freq, "above_floor_db": above, "level_dbfs": -90 + above, "tags": tags},
            "clean_peak": clean}


def test_a_fully_covered_window_says_its_label_is_uninformative():
    t = v.render("ok", [_row(si.run_fleet(quiet, None, {}, []), NOW - 60),
                        _adj_row([_w(908.6, 30, ["2xmeshtastic-LF-ch20-rnode", "x", "y"], product_frac=1.0)])], now=NOW)
    line = next(ln for ln in t.splitlines() if "908.600 MHz" in ln)
    assert "label uninformative: this whole window is product positions" in line
    assert "2xmeshtastic-LF-ch20-rnode (+2 more)" in line


def test_a_skirt_is_labelled_plainly_not_as_a_candidate():
    t = v.render("ok", [_row(si.run_fleet(quiet, None, {}, []), NOW - 60),
                        _adj_row([_w(907.047, 60, ["skirt of meshtastic-LF-ch20"])])], now=NOW)
    line = next(ln for ln in t.splitlines() if "907.047 MHz" in ln)
    assert line.endswith("← skirt of meshtastic-LF-ch20") and "candidate" not in line


def test_class_d_shows_exactly_top_d_lines_plus_the_strongest_clean_line():
    ws = [_w(870.0 + i, 10 + i) for i in range(6)]
    ws[1]["clean_peak"] = {"freq_mhz": 871.5, "above_floor_db": 44.0, "level_dbfs": -46.0}
    t = v.render("ok", [_row(si.run_fleet(quiet, None, {}, []), NOW - 60), _adj_row(ws)], now=NOW)
    section = t.split("Class D")[1].split("A filter helps")[0]
    shown = [ln for ln in section.splitlines() if ln.strip().startswith("87") and "MHz" in ln]
    assert [ln.split()[0] for ln in shown] == ["875.000", "874.000", "873.000"]    # TOP_D, ranked
    assert "strongest line clear of every product position: 871.500 MHz +44 dB" in section


def test_load_reports_unreadable_even_when_exists_itself_fails(tmp_path, monkeypatch):
    """Review 3 #8: Path.exists() raises PermissionError on an unreadable
    parent (Python 3.13) — load() must still say `unreadable`, never raise."""
    p = tmp_path / "interference.jsonl"
    monkeypatch.setattr(type(p), "exists", lambda self: (_ for _ in ()).throw(PermissionError(13, "denied")))
    assert v.load(p) == ("unreadable", [])


# ---- fleet SDR line (2026-10-05): one compact summary per box for the
# Fleet Watchers rollup. Same freshness contract as render(); an SDR on USB
# that nothing reads is BLINDNESS and must say so, never stay silent.

AIRSPY = (("1d50", "60a1"),)
RTL = (("0bda", "2838"),)
HUB = (("1d6b", "0002"),)          # a root hub: proves the bus was readable


def _fresh_rows():
    return [_row(si.run_fleet(quiet, None, {}, []), NOW - 60),
            _row(si.run_adjacent(quiet), NOW - 600)]


def test_sdr_devices_names_known_sdrs_and_keeps_unobservable_distinct():
    assert v.sdr_devices(HUB + AIRSPY + RTL) == ["Airspy", "RTL-SDR"]
    assert v.sdr_devices(HUB) == []
    assert v.sdr_devices(None) is None          # bus unreadable ≠ no SDR


@pytest.mark.parametrize("usb", [[], None])
def test_no_data_and_no_sdr_is_silent(usb):
    assert v.summarize("absent", [], usb, now=NOW) is None


def test_an_sdr_nothing_reads_is_blindness_not_silence():
    s = v.summarize("absent", [], ["RTL-SDR"], now=NOW)
    assert s["status"] == "no_consumer"
    line = v.summary_line(s)
    assert "RTL-SDR" in line and "nothing reads it" in line


def test_a_fresh_run_summarises_busy_per_channel_and_the_adjacent_pass():
    s = v.summarize("ok", _fresh_rows(), ["Airspy"], now=NOW)
    assert s["status"] == "fresh" and s["stale_windows"] == []
    line = v.summary_line(s)
    assert "fresh" in line and "LF-ch20" in line and "busy" in line
    assert "adjacent 10 min ago" in line
    assert "STALE" not in line and "UNKNOWN" not in line


def test_old_rows_read_stale_with_the_check_to_run():
    rows = [_row(si.run_fleet(quiet, None, {}, []), NOW - 20 * 60)]
    s = v.summarize("ok", rows, ["Airspy"], now=NOW)
    assert s["status"] == "stale" and len(s["stale_windows"]) == 3
    line = v.summary_line(s)
    assert "STALE" in line and "meshforge-sdr.timer" in line
    assert "busy" not in line          # never healthy numbers from a dead capture


def test_fresh_failed_rows_do_not_make_the_line_fresh():
    """R9 in summary form: the witness is the newest OK window."""
    ok = _row(si.run_fleet(quiet, None, {}, []), NOW - 20 * 60)
    dead_rows = [_row(si.run_fleet(dead, None, {}, []), NOW - 60 * k) for k in (3, 2, 1)]
    s = v.summarize("ok", [ok] + dead_rows, ["Airspy"], now=NOW)
    assert s["status"] == "stale" and s["unknown_streak"] == 3
    assert "captured nothing" in v.summary_line(s)


def test_a_future_timestamp_is_not_fresh():
    rows = [_row(si.run_fleet(quiet, None, {}, []), NOW + 3600)]
    assert v.summarize("ok", rows, ["Airspy"], now=NOW)["status"] == "stale"


@pytest.mark.parametrize("state", ["unreadable", "ok"])
def test_unreadable_or_empty_is_unknown_in_summary(state):
    s = v.summarize(state, [], ["Airspy"], now=NOW)
    assert s["status"] == "unknown" and "UNKNOWN" in v.summary_line(s)


def test_a_second_sdr_beside_the_watched_one_is_named_unread():
    s = v.summarize("ok", _fresh_rows(), ["Airspy", "RTL-SDR"], now=NOW)
    assert s["unread"] == ["RTL-SDR"]
    assert "RTL-SDR on USB, nothing reads it" in v.summary_line(s)


def test_data_with_no_sdr_on_usb_says_so():
    s = v.summarize("ok", _fresh_rows(), [], now=NOW)
    assert "no SDR on USB now" in v.summary_line(s)


def test_remote_relpath_is_the_reader_path_under_home(tmp_path, monkeypatch):
    """hfm #5: the rollup's ssh tail and the local reader share one path."""
    import utils.paths as paths
    monkeypatch.setattr(paths, "get_real_user_home", lambda: tmp_path)
    assert tmp_path / v.SDR_JSONL_RELPATH == v.jsonl_path()


def test_no_host_is_hardcoded_into_the_view_text():
    """A second SDR host must not read as 'moc5 only'."""
    assert "moc5" not in v.BLIND_SPOTS
    assert "moc5" not in v.render("absent", [], now=NOW)


def test_busy_is_a_labelled_lower_bound_mean_over_runs_not_one_snapshot():
    """2026-10-05 live: one 5-min run read LF 0.0% while 23 h of runs averaged
    5.2% (105 of 277 non-zero) — a single burst is a moment, not the channel."""
    r1 = _row(si.run_fleet(quiet, None, {}, []), NOW - 360)
    r2 = _row(si.run_fleet(quiet, None, {}, []), NOW - 60)
    r1["windows"]["906.300"]["channels"]["meshtastic-LF-ch20"]["busy_pct"] = 10.0
    r2["windows"]["906.300"]["channels"]["meshtastic-LF-ch20"]["busy_pct"] = 0.0
    s = v.summarize("ok", [r1, r2], ["Airspy"], now=NOW)
    assert s["busy"]["LF-ch20"] == pytest.approx(5.0) and s["busy_runs"] == 2
    line = v.summary_line(s)
    assert "busy ≥ (mean of 2 runs)" in line and "LF-ch20 5.0%" in line


# ---- web fleet page (2026-10-05): the network manager's centralized view.
# One mapping to the page's state classes, here, so the page never re-derives.

def test_web_block_maps_only_a_clean_fresh_sdr_to_healthy():
    fresh = v.summarize("ok", _fresh_rows(), ["Airspy"], now=NOW)
    assert v.web_block(fresh) == {"status": "fresh", "state": "healthy",
                                  "line": v.summary_line(fresh)}
    stale = v.summarize("ok", [_row(si.run_fleet(quiet, None, {}, []), NOW - 20 * 60)],
                        ["Airspy"], now=NOW)
    assert v.web_block(stale)["state"] == "failed"
    blind = v.summarize("absent", [], ["RTL-SDR"], now=NOW)
    assert v.web_block(blind)["state"] == "failed"       # unread SDR = blindness
    extra = v.summarize("ok", _fresh_rows(), ["Airspy", "RTL-SDR"], now=NOW)
    assert v.web_block(extra)["state"] == "failed"       # fresh, but one SDR unread
    unk = v.summarize("unreadable", [], ["Airspy"], now=NOW)
    assert v.web_block(unk)["state"] == "dark"
    assert v.web_block(None) is None


def test_local_block_reads_this_box_bounded(tmp_path):
    f = tmp_path / "interference.jsonl"
    f.write_text("".join(json.dumps(r) + "\n" for r in _fresh_rows()))
    b = v.local_web_block(path=f, usb_pairs=[("1d50", "60a1")], now=NOW)
    assert b["status"] == "fresh" and b["state"] == "healthy"
    assert v.local_web_block(path=tmp_path / "none.jsonl", usb_pairs=[], now=NOW) is None



# ---- RTL Phase 1: the view follows the ROW's device ------------------------

def _rtl_rows(ts):
    from utils.sdr_view import RX_WINDOWS
    w = {"status": "ok", "channels": {"meshtastic-LF-ch20": {"busy_pct": 4.0}},
         "foreign": [], "carriers_persistent": []}
    return [{"mode": "fleet", "status": "ok", "device": "rtl", "ts": ts,
             "windows": {f"{c:.3f}": dict(w) for c in RX_WINDOWS["rtl"]}}]


def test_rtl_rows_read_fresh_against_the_rtl_windows():
    s = v.summarize("ok", _rtl_rows(NOW - 60), ["RTL-SDR"], now=NOW)
    assert s["status"] == "fresh" and s["device"] == "RTL-SDR" and s["unread"] == []
    assert v.web_block(s)["state"] == "healthy"
    assert "📡 SDR RTL-SDR: 🟢 fresh" in v.summary_line(s)


def test_legacy_rows_without_a_device_field_are_airspy():
    s = v.summarize("ok", _fresh_rows(), ["Airspy"], now=NOW)
    assert s["device"] == "Airspy" and s["status"] == "fresh"


# ---- review (contextless, 2026-10-05) of the RTL Phase 1 diff --------------

def _err_rows(device, ts_list):
    return [{"mode": "fleet", "status": "error", "device": device, "ts": t,
             "note": "rtl_sdr rc=1: usb_claim_interface error -6"} for t in ts_list]


def test_W1_the_newest_rows_device_names_the_line_even_without_windows():
    """An Airspy row 10 min old, then 4 failing RTL rows: the line read
    'Airspy 🟢 fresh … RTL-SDR nothing reads it' — the RTL IS read, and failing."""
    rows = [_row(si.run_fleet(quiet, None, {}, []), NOW - 600)] + \
        _err_rows("rtl", [NOW - 240, NOW - 180, NOW - 120, NOW - 60])
    s = v.summarize("ok", rows, ["RTL-SDR"], now=NOW)
    assert s["device"] == "RTL-SDR" and s["status"] == "stale"
    assert s["unread"] == [] and s["unknown_streak"] == 4
    line = v.summary_line(s)
    assert "SDR RTL-SDR" in line and "captured nothing" in line and "Airspy" not in line


def test_W3_an_unsupported_adjacent_pass_is_said_not_unknown():
    rows = _rtl_rows(NOW - 60) + [{"mode": "adjacent", "status": "unsupported", "device": "rtl",
                                   "ts": NOW - 300, "note": "not built for the rtl receiver yet"}]
    line = v.summary_line(v.summarize("ok", rows, ["RTL-SDR"], now=NOW))
    assert "adjacent n/a" in line and "UNKNOWN" not in line
    t = v.render("ok", rows, now=NOW)
    assert "not built for this receiver" in t and "no hourly pass recorded yet" not in t


def test_the_remediation_names_the_check_for_THIS_device():
    rows = _err_rows("rtl", [NOW - 240, NOW - 180, NOW - 120, NOW - 60])
    t = v.render("ok", rows, now=NOW)
    assert "rtl_test" in t and "airspy_info" not in t


def test_an_unknown_device_string_is_unknown_not_airspy():
    rows = [{"mode": "fleet", "status": "ok", "device": "hackrf", "ts": NOW - 60,
             "windows": {"906.300": {"status": "ok"}}}]
    s = v.summarize("ok", rows, [], now=NOW)
    assert s["status"] == "unknown" and "hackrf" in v.summary_line(s)


def test_a_receiver_with_no_adjacent_pass_says_na_without_a_witness_row():
    """moc1 runs no hourly adjacent timer (the pass is not built for the RTL);
    'adjacent UNKNOWN' forever would be a permanent false nag (review W3)."""
    line = v.summary_line(v.summarize("ok", _rtl_rows(NOW - 60), ["RTL-SDR"], now=NOW))
    assert "adjacent n/a" in line and "UNKNOWN" not in line
    assert "adjacent UNKNOWN" in v.summary_line(
        v.summarize("ok", _fresh_rows()[:1], ["Airspy"], now=NOW))   # Airspy still owes one
