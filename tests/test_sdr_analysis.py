"""SDR Phase 1 step 1 — utils/sdr_analysis.py against REAL moc5 IQ.

The fixtures in tests/fixtures/sdr/ are 20 ms crops recorded on moc5
(2026-09-24, Airspy Mini, linearity gain 10) — not synthetic. The review of
the design showed clean synthetic IQ passes the own-TX case trivially,
because the failure it guards is ANALOG (reciprocal-mixing skirts of a
near-field transmitter). So every "must NOT fire" case runs on the real
recording, and the gate that prevents it is proven load-bearing by
disabling it and watching the false finding appear.

Planted signals (CW, bursty band noise, a floor rise, a blocker) are added
ON TOP of real receiver noise, at levels stated relative to its floor.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from utils import sdr_analysis as a

FX = Path(__file__).resolve().parent / "fixtures" / "sdr"
RNG = np.random.default_rng(20260924)


def _load(name):
    return np.load(FX / name)


@pytest.fixture(scope="module")
def q906():
    return _load("fx_906_quiet.npy")


@pytest.fixture(scope="module")
def q910():
    return _load("fx_910_quiet.npy")


@pytest.fixture(scope="module")
def neartx():
    return _load("fx_lf_neartx.npy")


def _iq(raw):
    return raw[0::2].astype(np.float64) + 1j * raw[1::2].astype(np.float64)


def _raw(iq):
    out = np.empty(iq.size * 2, np.int16)
    out[0::2] = np.clip(np.round(iq.real), -32768, 32767)
    out[1::2] = np.clip(np.round(iq.imag), -32768, 32767)
    return out


def _bin_freq(center_mhz, target_mhz):
    """Nearest FFT bin centre to target (no scalloping loss), as an offset in Hz."""
    step = a.SAMPLE_RATE / a.FFT
    return round((target_mhz - center_mhz) * 1e6 / step) * step


def _plant_tone(raw, center, target_mhz, above_floor_db, floor_dbfs, frames=None):
    iq = _iq(raw)
    n = np.arange(iq.size)
    amp = a.FULL_SCALE * 10 ** ((floor_dbfs + above_floor_db) / 20)
    tone = amp * np.exp(2j * np.pi * _bin_freq(center, target_mhz) * n / a.SAMPLE_RATE)
    if frames is not None:
        gate = np.zeros(iq.size, bool)
        for fr in frames:
            gate[fr * a.FFT:(fr + 1) * a.FFT] = True
        tone = tone * gate
    return _raw(iq + tone)


def _plant_band_noise(raw, center, target_mhz, width_khz, above_floor_db, frames):
    """Band-limited complex noise in the given frames, scaled so its in-band
    per-frame power sits `above_floor_db` over the recording's floor."""
    iq = _iq(raw)
    noise = RNG.normal(size=iq.size) + 1j * RNG.normal(size=iq.size)
    spec = np.fft.fft(noise)
    f = np.fft.fftfreq(iq.size, 1 / a.SAMPLE_RATE)
    spec[np.abs(f - (target_mhz - center) * 1e6) > width_khz * 500] = 0
    band = np.fft.ifft(spec)
    gate = np.zeros(iq.size, bool)
    for fr in frames:
        gate[fr * a.FFT:(fr + 1) * a.FFT] = True
    band = band * gate
    # calibrate: measure what 1.0 scale gives, then scale to target
    base = a.analyse_window(raw, center)["floor_dbfs"]
    fr_, p = a.spectrogram(_raw(band * 1000.0), center)
    sel = np.abs(fr_ - target_mhz) <= width_khz / 2000
    got = 10 * np.log10(np.mean(10 ** (p[frames[0]][sel] / 10)))
    scale = 1000.0 * 10 ** ((base + above_floor_db - got) / 20)
    return _raw(iq + band * scale)


# ---- the real recordings, unplanted ------------------------------------------

def test_fixture_metadata_says_what_it_is():
    meta = json.loads((FX / "fx_meta.json").read_text())
    assert meta["host"] == "moc5" and meta["gain_linearity"] == 10
    assert meta["sample_rate"] == a.SAMPLE_RATE
    assert meta["lf_neartx"]["peak_lf_above_floor_db"] > 45


@pytest.mark.parametrize("name,center", [("fx_906_quiet.npy", 906.3), ("fx_910_quiet.npy", 910.525)])
def test_quiet_real_recording_finds_nothing(name, center):
    r = a.analyse_window(_load(name), center, spur_mhz=a.SPUR_MAP_MHZ.get((center, 10), ()))
    assert r["status"] == "ok"
    assert r["carriers"] == [] and r["foreign"] == []
    assert r["kept_frames"] == r["frames"] == 29
    assert -96 < r["floor_dbfs"] < -93          # matches the 18:40 reference sweep (-94.4)


def test_near_field_own_tx_is_unjudgeable_not_foreign(neartx):
    r = a.analyse_window(neartx, 906.3)
    assert r["status"] == "unjudgeable"          # our own TX filled the burst
    assert r["own_tx_frames"] == r["frames"]
    assert r["foreign"] == [] and r["carriers"] == []


def test_the_own_tx_gate_is_load_bearing(neartx):
    """The control that can fail: with the gate off, the SAME real recording
    reports our own near-field burst as foreign energy — Phase 0's first-run
    failure, reproduced from recorded IQ. If this stops failing the fixture
    no longer carries the analog skirt and the test above proves nothing."""
    r = a.analyse_window(neartx, 906.3, own_tx_dbfs=999.0, rel_tx_db=999.0)
    assert r["status"] == "ok"
    assert r["channels"]["meshtastic-LF-ch20"]["saturated_in_sample"] is True
    assert len(r["foreign"]) >= 2


def test_quiet_then_own_tx_judges_only_the_quiet_half(q906, neartx):
    r = a.analyse_window(np.concatenate([q906, neartx]), 906.3,
                         spur_mhz=a.SPUR_MAP_MHZ[(906.3, 10)])
    assert r["status"] == "ok"
    assert r["own_tx_frames"] == 29 and r["kept_frames"] == 29
    assert r["foreign"] == [] and r["carriers"] == []


# ---- class A: persistent carrier, off-channel only -------------------------

def test_class_a_fires_on_a_planted_off_channel_carrier(q906):
    floor = a.analyse_window(q906, 906.3)["floor_dbfs"]
    r = a.analyse_window(_plant_tone(q906, 906.3, 907.30, 25, floor), 906.3)
    assert len(r["carriers"]) == 1
    c = r["carriers"][0]
    assert abs(c["freq_mhz"] - 907.30) < 0.003
    assert 20 < c["above_floor_db"] < 30


def test_class_a_lists_a_known_spur_separately(q906):
    floor = a.analyse_window(q906, 906.3)["floor_dbfs"]
    spur = a.SPUR_MAP_MHZ[(906.3, 10)][-1]      # 907.1335, the Airspy's own
    r = a.analyse_window(_plant_tone(q906, 906.3, spur, 20, floor), 906.3,
                         spur_mhz=a.SPUR_MAP_MHZ[(906.3, 10)])
    assert r["carriers"] == []
    assert len(r["spurs"]) == 1


def test_class_a_ignores_a_carrier_inside_a_fleet_channel(q906):
    floor = a.analyse_window(q906, 906.3)["floor_dbfs"]
    r = a.analyse_window(_plant_tone(q906, 906.3, 906.875, 25, floor), 906.3)
    assert r["carriers"] == []                   # review R2: in-channel is not A's call


def test_class_a_does_not_fire_below_half_duty_but_c_does(q906):
    floor = a.analyse_window(q906, 906.3)["floor_dbfs"]
    r = a.analyse_window(_plant_tone(q906, 906.3, 906.40, 25, floor, frames=range(0, 11)), 906.3)
    assert r["carriers"] == []                   # 11/29 = 38 % duty: time-median stays at floor
    hits = [f for f in r["foreign"] if abs(f["slice_mhz"] - 906.40) <= 0.0625]
    assert hits and 30 < hits[0]["busy_pct"] < 45


# ---- class C: foreign bursty energy -----------------------------------------

def test_class_c_fires_on_planted_bursty_band_noise(q906):
    raw = _plant_band_noise(q906, 906.3, 906.40, 100, 20, frames=[3, 4, 10, 11, 20, 21, 22, 25])
    r = a.analyse_window(raw, 906.3, spur_mhz=a.SPUR_MAP_MHZ[(906.3, 10)])
    hits = [f for f in r["foreign"] if abs(f["slice_mhz"] - 906.40) <= 0.0625]
    assert hits, r["foreign"]
    assert hits[0]["im3_candidate"] is None
    assert r["carriers"] == []


def test_class_c_tags_a_slice_on_our_own_im3_product(q906):
    # 907.275 = meshcore + rnode - LF: our own mixing product, tagged not dropped
    raw = _plant_band_noise(q906, 906.3, 907.30, 100, 20, frames=[2, 3, 9, 15, 16, 24])
    r = a.analyse_window(raw, 906.3)
    hits = [f for f in r["foreign"] if abs(f["slice_mhz"] - 907.30) <= 0.0625]
    assert hits and hits[0]["im3_candidate"]
    assert any("rnode" in p and "meshcore" in p for p in hits[0]["im3_candidate"])


# ---- blocker gate, overload, unjudgeable --------------------------------------

def test_a_frame_wide_floor_jump_is_a_blocker_not_foreign(q906):
    iq = _iq(q906)
    sigma = np.sqrt(np.var(iq) / 2)
    frames = [5, 6, 7]
    for fr in frames:
        sl = slice(fr * a.FFT, (fr + 1) * a.FFT)
        iq[sl] += 4 * sigma * (RNG.normal(size=a.FFT) + 1j * RNG.normal(size=a.FFT))
    r = a.analyse_window(_raw(iq), 906.3, spur_mhz=a.SPUR_MAP_MHZ[(906.3, 10)])
    assert r["blocker_frames"] == 3 and r["kept_frames"] == 26
    assert r["foreign"] == [] and r["status"] == "ok"


@pytest.mark.parametrize("rail", [32767, -32768])
def test_clipped_input_is_overload_with_no_findings(q906, rail):
    """Both rails. -32768 is what an ADC pinned LOW produces ((0-2048)<<4),
    and np.abs(int16(-32768)) overflows to -32768 — the first cut read that
    saturated front end as clean."""
    raw = q906.copy()
    raw[::50] = rail                              # 2 % of samples on one rail
    r = a.analyse_window(raw, 906.3)
    assert r["status"] == "overload"
    assert r["carriers"] == [] and r["foreign"] == [] and r["floor_dbfs"] is None


def test_unjudgeable_never_carries_findings_or_a_floor(neartx):
    r = a.analyse_window(neartx, 906.3)
    assert r["status"] == "unjudgeable" and r["floor_dbfs"] is None and r["channels"] == {}


def test_short_or_odd_input_raises():
    with pytest.raises(ValueError):
        a.analyse_window(np.zeros(100, np.int16), 906.3)
    with pytest.raises(ValueError):
        a.analyse_window(np.zeros(2 * a.FFT + 1, np.int16), 906.3)


# ---- class B: floor delta -----------------------------------------------------

def test_class_b_sees_a_planted_six_db_floor_rise(q906):
    ref = a.analyse_window(q906, 906.3)["floor_profile"]
    iq = _iq(q906)
    sigma = np.sqrt(np.var(iq) / 2)
    raised = iq + np.sqrt(3) * sigma * (RNG.normal(size=iq.size) + 1j * RNG.normal(size=iq.size))
    cur = a.analyse_window(_raw(raised), 906.3)["floor_profile"]
    assert 5.0 < a.floor_delta_db(cur, ref) < 7.0
    assert abs(a.floor_delta_db(ref, ref)) < 1e-9


def test_floor_delta_refuses_mismatched_profiles(q906, q910):
    p = a.analyse_window(q906, 906.3)["floor_profile"]
    with pytest.raises(ValueError):
        a.floor_delta_db(p, p[:-1])


# ---- helpers ----------------------------------------------------------------------

def test_persistent_needs_two_consecutive_runs():
    now = [{"freq_mhz": 907.3000}, {"freq_mhz": 908.0}]
    assert a.persistent(now, None) == []
    assert a.persistent(now, []) == []
    assert a.persistent(now, [{"freq_mhz": 907.3015}]) == [now[0]]
    assert a.persistent(now, [{"freq_mhz": 907.3100}]) == []


def test_im3_set_contains_the_reviewed_products():
    ims = {(round(c, 3), round(hw, 3)): p for c, hw, p in a.im3_products()}
    assert (910.125, 0.375) in ims                  # 2xLF - RNode, 750 kHz wide
    assert any(abs(c - 903.225) < 1e-6 for c, _ in ims)   # 2xLF - MeshCore


def test_spur_map_lines_sit_inside_their_windows():
    for (center, gain), lines in a.SPUR_MAP_MHZ.items():
        assert gain in (10, 21)
        for f in lines:
            assert abs(f - center) <= a.USABLE_HALF_MHZ, (center, f)


# ---- review 2026-09-24 (Fable, non-author): the cases the first suite let through ----

@pytest.mark.parametrize("atten_db", [15, 20, 30])
def test_a_weaker_own_emitter_is_never_foreign(q906, neartx, atten_db):
    """Review #1: our own emitter 15-36 dB weaker than the recording sits
    under the absolute -40 dBFS gate but 30-50 dB over the floor; class C
    called it foreign on 4-11 slices. dudeclaw-02 (2 dBm, ~3 ft) is this."""
    tx = _iq(neartx) * 10 ** (-atten_db / 20)
    raw = np.concatenate([q906, _raw(tx + _iq(q906))])
    r = a.analyse_window(raw, 906.3, spur_mhz=a.SPUR_MAP_MHZ[(906.3, 10)])
    assert r["foreign"] == [], r["foreign"]
    assert r["own_tx_frames"] == 29


def test_the_weaker_emitter_case_is_meaningful(q906, neartx):
    """Control: with both own-TX gates off, the same attenuated burst DOES
    report foreign slices — so the test above is testing the gate."""
    tx = _iq(neartx) * 10 ** (-20 / 20)
    raw = np.concatenate([q906, _raw(tx + _iq(q906))])
    r = a.analyse_window(raw, 906.3, own_tx_dbfs=999.0, rel_tx_db=999.0)
    assert r["foreign"], "attenuated own TX no longer leaks — the case above is untested"


def test_a_burst_mostly_gated_is_unjudgeable_not_ok(q906, neartx):
    """Review #5: 8 kept frames of 732 once read `ok`."""
    raw = np.concatenate([neartx] * 25 + [q906])       # 725 TX frames + 29 quiet
    r = a.analyse_window(raw, 906.3)
    assert r["status"] == "unjudgeable"
    assert r["kept_frac"] < a.MIN_KEPT_FRAC


def test_a_tone_just_outside_a_channel_edge_is_not_a_carrier(q906):
    floor = a.analyse_window(q906, 906.3)["floor_dbfs"]
    r = a.analyse_window(_plant_tone(q906, 906.3, 907.010, 25, floor), 906.3)   # LF edge 907.0 + 10 kHz
    assert r["carriers"] == []


def test_foreign_needs_more_than_the_busy_threshold(q906):
    raw = np.concatenate([q906, q906])                   # 58 frames
    one = _plant_band_noise(raw, 906.3, 906.40, 100, 20, frames=[10])   # 1/58 = 1.7 %
    two = _plant_band_noise(raw, 906.3, 906.40, 100, 20, frames=[10, 40])  # 3.4 %
    assert a.analyse_window(one, 906.3, spur_mhz=a.SPUR_MAP_MHZ[(906.3, 10)])["foreign"] == []
    assert a.analyse_window(two, 906.3, spur_mhz=a.SPUR_MAP_MHZ[(906.3, 10)])["foreign"]


def test_in_channel_activity_counts_our_own_traffic(q906, neartx):
    """The own-TX gate removes frames from interference judgement, never from
    the channel's own busy % — our traffic is what that number measures."""
    r = a.analyse_window(np.concatenate([q906, neartx]), 906.3)
    assert 45 < r["channels"]["meshtastic-LF-ch20"]["busy_pct"] <= 55


def test_spur_map_covers_every_fleet_window_at_both_writer_gains():
    for c in (903.625, 906.3, 910.525):
        for g in (10, 21):
            assert (c, g) in a.SPUR_MAP_MHZ, (c, g)


def test_a_neighbour_channel_is_not_busy_from_our_leak(q906, neartx):
    """LEAK_DB between fleet channels: while LF transmits at -28 dBFS, the
    ShortTurbo band 1 MHz away reads ~20 dB over the floor from leakage —
    that is LF's skirt, not ST traffic."""
    r = a.analyse_window(np.concatenate([q906, neartx]), 906.3)
    assert r["channels"]["meshtastic-ST-ch8"]["busy_pct"] < 5



def _lifted(base, frames, floor, carrier_db=15):
    planted = _plant_tone(base, 906.3, 907.30, carrier_db, floor)
    iq = _iq(planted)
    sigma = np.sqrt(np.var(_iq(base)) / 2)
    for fr in frames:
        sl = slice(fr * a.FFT, (fr + 1) * a.FFT)
        iq[sl] += 2.3 * sigma * (RNG.normal(size=a.FFT) + 1j * RNG.normal(size=a.FFT))  # ~ +8 dB
    return _raw(iq)


def test_a_lift_over_most_of_a_burst_is_still_a_blocker_without_history(q906):
    """Review #2 (exp 5): referenced to the burst MEDIAN, a +8 dB lift on 40
    of 58 frames read blocker_frames=0 and hid a +15 dB carrier. The 20th
    percentile stays on the unlifted frames — no history needed."""
    base = np.concatenate([q906, q906])
    floor = a.analyse_window(base, 906.3)["floor_dbfs"]
    r = a.analyse_window(_lifted(base, range(40), floor), 906.3)
    assert r["blocker_frames"] >= 38


def test_a_fully_lifted_burst_is_caught_by_the_previous_floor(q906):
    """When EVERY frame is lifted no in-burst quantile can see it; the previous
    ok run's floor can. Without it the burst would read `ok` on a +8 dB floor."""
    base = np.concatenate([q906, q906])
    floor = a.analyse_window(base, 906.3)["floor_dbfs"]
    r = a.analyse_window(_lifted(base, range(58), floor), 906.3, ref_floor_dbfs=floor)
    assert r["status"] == "unjudgeable" and r["blocker_frames"] == 58


def test_alias_positions_of_our_lf_are_tagged_where_they_were_seen_live():
    tags = {round(c, 3): p for c, hw, p in a.alias_products(910.525)}
    assert any(abs(c - 911.175) < 1e-6 and "meshtastic-LF-ch20" in p for c, p in tags.items())
    tags903 = [(c, p) for c, hw, p in a.alias_products(903.625)]
    assert any(abs(c - 903.375) < 1e-6 and "LF" in p for c, p in tags903)
    # an in-window channel never aliases into its own window
    assert not any("LF" in p for c, hw, p in a.alias_products(906.3))


def test_a_foreign_slice_on_an_alias_position_carries_the_tag(q910):
    t = _plant_band_noise(q910, 910.525, 911.175, 100, 20, frames=[2, 3, 9, 15, 16, 24])
    r = a.analyse_window(t, 910.525)
    hits = [f for f in r["foreign"] if abs(f["slice_mhz"] - 911.175) <= 0.0625]
    assert hits and hits[0]["alias_candidate"] and "LF" in hits[0]["alias_candidate"][0]


# ---- receiver profiles (RTL Phase 1, 2026-10-05) ---------------------------
# moc1's RTL-SDR (Elonics E4000, zero-IF, 2.048 MS/s). MEASURED on moc1: a
# channel at the tuned centre read 100 % busy from the DC spike alone; a
# channel on another's MIRROR (2c - f) picks up its image. The Airspy
# profile must reproduce the pre-refactor constants EXACTLY.

import pytest as _pytest  # noqa: E402

sa = a


@_pytest.fixture
def rtl():
    sa.use_receiver(sa.RTL)
    yield sa.RTL
    sa.use_receiver(sa.AIRSPY)


def test_airspy_profile_is_the_old_constants():
    sa.use_receiver(sa.AIRSPY)
    assert sa.SAMPLE_RATE == 3_000_000 and sa.USABLE_HALF_MHZ == 1.2
    assert sa.RECEIVER.dc_guard_khz == 0 and sa.RECEIVER.zero_if is False


def test_rtl_profile_switches_rate_and_span_and_back(rtl):
    assert sa.SAMPLE_RATE == 2_048_000
    assert sa.USABLE_HALF_MHZ == _pytest.approx(0.8 * 2.048 / 2)
    sa.use_receiver(sa.AIRSPY)
    assert sa.SAMPLE_RATE == 3_000_000


def test_alias_products_follow_the_active_rate(rtl):
    """The default fs was bound at import (fs_mhz=SAMPLE_RATE/1e6) — a profile
    switch must move it, or the RTL's alias tags sit at Airspy positions."""
    for pos, hw, label in sa.alias_products(907.2):
        if label.startswith("alias of") or label.startswith("alias-mirror of"):
            parent = next(c for c in sa.FLEET_CHANNELS if label.endswith(c.label))
            assert any(abs(abs(pos - d) - 2.048) < 1e-6 for d in (parent.center_mhz,
                       2 * 907.2 - parent.center_mhz))


def test_rtl_tags_the_mirror_of_an_in_window_channel(rtl):
    tags = [(round(p, 4), lbl) for p, hw, lbl in sa.alias_products(907.2)]
    assert (round(2 * 907.2 - 906.875, 4), "mirror of meshtastic-LF-ch20") in tags
    sa.use_receiver(sa.AIRSPY)
    assert not any(lbl.startswith("mirror of") for _, _, lbl in sa.alias_products(906.3))


def _rtl_burst(center, tones, n=2048 * 24, seed=3):
    """Interleaved int16 like rtl_to_int16 emits: noise + a DC offset + tones."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / sa.SAMPLE_RATE
    iq = 0.004 * (rng.standard_normal(n) + 1j * rng.standard_normal(n)) + (0.06 + 0.05j)
    for f, a in tones:
        iq = iq + a * np.exp(2j * np.pi * (f - center) * 1e6 * t)
    raw = np.empty(2 * n, np.int16)
    raw[0::2] = np.clip(iq.real * 32767, -32767, 32767)
    raw[1::2] = np.clip(iq.imag * 32767, -32767, 32767)
    return raw


def test_the_dc_spike_is_never_a_carrier(rtl):
    r = sa.analyse_window(_rtl_burst(907.2, []), 907.2)
    assert r["status"] == "ok"
    assert not any(abs(c["freq_mhz"] - 907.2) < 0.2 for c in r["carriers"]), r["carriers"]


def test_a_real_off_centre_carrier_is_still_found(rtl):
    r = sa.analyse_window(_rtl_burst(907.2, [(907.55, 0.05)]), 907.2)
    assert any(abs(c["freq_mhz"] - 907.55) < 0.01 for c in r["carriers"]), r["carriers"]


def test_airspy_outputs_match_the_pre_refactor_baseline():
    """Byte-for-byte on the real moc5 fixture: the refactor moves nothing."""
    sa.use_receiver(sa.AIRSPY)
    q = np.load(Path(__file__).parent / "fixtures" / "sdr" / "fx_906_quiet.npy")
    r = sa.analyse_window(q, 906.3)
    base = json.loads((Path(__file__).parent / "fixtures" / "sdr" / "airspy_baseline_2026_10_05.json").read_text())
    assert r["floor_dbfs"] == base["fx906"]["floor_dbfs"]
    assert r["kept_frames"] == base["fx906"]["kept_frames"]
    assert r["channels"] == base["fx906"]["channels"]
    assert len(r["carriers"]) == len(base["fx906"]["carriers"])
    for c in ("903.625", "906.3", "910.525"):
        assert [list(map(str, a)) for a in sa.alias_products(float(c))] == \
               [list(map(str, a)) for a in base["alias"][c]]


def test_a_slice_touching_the_dc_guard_is_not_judged(rtl):
    """LIVE moc1 2026-10-05: a class-C 'foreign' slice at 907.3183 (2.4 %
    busy) in 2 of 3 runs — 118 kHz off centre, HALF inside the 150 kHz DC
    guard, judged on the spike's own skirt. A slice that touches the guard is
    the receiver, exactly like the bins inside it."""
    raw = _rtl_burst(907.2, [(907.36, 0.02)])      # energy hugging the guard edge
    r = sa.analyse_window(raw, 907.2)
    guard = sa.RTL.dc_guard_khz / 1000.0
    half = sa.SLICE_KHZ / 2000.0
    for f in r["foreign"]:
        assert abs(f["slice_mhz"] - 907.2) - half >= guard - 1e-9, f


def test_a_foreign_carriers_zero_if_image_is_tagged_not_counted_twice(rtl):
    """Review (contextless, 2026-10-05) #1: an external carrier at c+0.55 MHz
    leaves an image at c-0.55 that clears CARRIER_DB and persists exactly as
    long as its parent — a phantom second carrier. Tagged, never dropped."""
    raw = _rtl_burst(903.3, [(903.85, 0.05), (902.75, 0.003)])
    r = sa.analyse_window(raw, 903.3)
    by = {round(c["freq_mhz"], 2): c for c in r["carriers"]}
    assert 903.85 in by and 902.75 in by, r["carriers"]
    assert by[902.75].get("image_of") == _pytest.approx(903.85, abs=0.005)
    assert not by[903.85].get("image_of")


def test_airspy_carriers_never_carry_an_image_tag():
    sa.use_receiver(sa.AIRSPY)
    q = np.load(Path(__file__).parent / "fixtures" / "sdr" / "fx_906_quiet.npy")
    assert all("image_of" not in c for c in sa.analyse_window(q, 906.3)["carriers"])
