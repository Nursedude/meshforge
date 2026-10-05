"""scripts/sdr_fleet_channels.py — the device backends (2026-10-05).

moc1 carries an RTL-SDR (Elonics E4000) that nothing read; Phase 0 for it
runs this same tool with ``--device rtl`` so the analysis, the overload gate,
the leak gate and the radio witness are the ones the Airspy was judged by.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "sdr_fleet_channels", _ROOT / "scripts" / "sdr_fleet_channels.py")
fc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fc)


@pytest.fixture(autouse=True)
def _restore_device():
    yield
    fc.set_device("airspy")


def test_rtl_bytes_map_onto_the_int16_full_scale_the_analysis_gates_on():
    raw = fc.rtl_to_int16(np.array([0, 255, 127, 128], dtype=np.uint8))
    assert raw.dtype == np.int16
    assert raw[0] <= -0.95 * fc.FULL_SCALE and raw[1] >= 0.95 * fc.FULL_SCALE
    assert abs(int(raw[2])) < 300 and abs(int(raw[3])) < 300


def test_set_device_switches_the_sample_rate():
    fc.set_device("rtl")
    assert fc.SAMPLE_RATE == 2_048_000
    fc.set_device("airspy")
    assert fc.SAMPLE_RATE == 3_000_000
    with pytest.raises(ValueError):
        fc.set_device("hackrf")


@pytest.mark.parametrize("device", ["airspy", "rtl"])
def test_every_fleet_channel_fits_a_window_at_this_devices_rate(device):
    """Physics, not config: a channel outside every window's usable span is
    a channel this receiver can never judge."""
    fc.set_device(device)
    half = fc.USABLE * fc.SAMPLE_RATE / 2e6
    for label, c, bw in fc.channels():
        if label.startswith("control"):
            continue
        assert any(abs(c - w) + bw / 2000 <= half for w in fc.windows()), label


def test_rtl_capture_runs_rtl_sdr_and_returns_int16(monkeypatch, tmp_path):
    fc.set_device("rtl")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        Path(cmd[-1]).write_bytes(np.full(2 * fc.FFT * 4, 140, dtype=np.uint8).tobytes())
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(fc.subprocess, "run", fake_run)
    raw, t0 = fc.capture(906.3, fc.FFT * 4, 20, str(tmp_path))
    assert seen["cmd"][:1] == ["rtl_sdr"]
    c = seen["cmd"]
    assert c[c.index("-f") + 1] == "906300000"
    assert c[c.index("-s") + 1] == "2048000"
    assert c[c.index("-g") + 1] == "20"
    assert c[c.index("-n") + 1] == str(fc.FFT * 4)
    assert raw.dtype == np.int16 and raw.size == 2 * fc.FFT * 4
    assert list(tmp_path.iterdir()) == []          # temp file cleaned up


def test_a_tone_on_lf_reads_busy_and_a_railed_rtl_burst_reads_overload():
    fc.set_device("rtl")
    n = fc.FFT * 8
    t = np.arange(n) / fc.SAMPLE_RATE
    rng = np.random.default_rng(1)
    tone = 0.5 * np.exp(2j * np.pi * (906.875 - 906.3) * 1e6 * t)
    iq = tone + 0.01 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    u8 = np.empty(2 * n, np.uint8)
    u8[0::2] = np.clip(127.5 + 120 * iq.real, 0, 255)
    u8[1::2] = np.clip(127.5 + 120 * iq.imag, 0, 255)
    chans = [ch for ch in fc.FLEET_CHANNELS if abs(ch[1] - 906.3) <= 0.8]
    stats, floor, clip = fc.analyse(fc.rtl_to_int16(u8), 906.3, chans)
    assert stats["meshtastic-LF-ch20"]["busy_frames"] == stats["meshtastic-LF-ch20"]["frames"]
    assert stats["control-gap"]["busy_frames"] == 0
    assert clip <= fc.CLIP_FRAC
    railed = np.where(np.arange(2 * n) % 2, 255, 0).astype(np.uint8)
    _, _, clip2 = fc.analyse(fc.rtl_to_int16(railed), 906.3, chans)
    assert clip2 > fc.CLIP_FRAC


def test_missing_rtl_sdr_binary_is_unknown_not_a_reading(monkeypatch, capsys):
    monkeypatch.setattr(fc.shutil, "which", lambda b: None)
    monkeypatch.setattr(sys, "argv", ["sdr_fleet_channels.py", "--device", "rtl"])
    assert fc.main() == 2
    assert "UNKNOWN" in capsys.readouterr().out


# ---- RTL window plan (MEASURED on moc1 2026-10-05) -------------------------
# Zero-IF E4000: a channel at the tuned centre read 100 % busy and 0.0 % when
# retuned 325 kHz away (rnode; meshcore 100 -> 0.1 %); the control 75 kHz off
# centre read 98.8 %. A channel on another's MIRROR (2c - f) picks up its image.
# With the plan below: LF saw 20/22 of moc1's radio RX events, control 0/22.

DC_GUARD_KHZ = 150


def _judged(device):
    fc.set_device(device)
    half = fc.USABLE * fc.SAMPLE_RATE / 2e6
    out = []
    for w in fc.windows():
        chans = [ch for ch in fc.channels() if abs(ch[1] - w) + ch[2] / 2000 <= half]
        out.append((w, chans))
    return out


def test_rtl_windows_keep_every_channel_off_the_dc_spike():
    for w, chans in _judged("rtl"):
        for label, c, bw in chans:
            assert abs(c - w) * 1000 - bw / 2 >= DC_GUARD_KHZ, (w, label)


def test_rtl_windows_put_no_channel_on_another_channels_mirror():
    for w, chans in _judged("rtl"):
        for la, ca, ba in chans:
            for lb, cb, bb in chans:
                if la == lb:
                    continue
                mirror = 2 * w - cb
                assert abs(mirror - ca) * 1000 >= (ba + bb) / 2, (w, la, lb)


def test_rtl_windows_judge_every_fleet_channel_and_carry_a_control():
    labels = {lbl for _, chans in _judged("rtl") for lbl, *_ in chans}
    fleet = {lbl for lbl, *_ in fc.FLEET_CHANNELS if not lbl.startswith("control")}
    assert fleet <= labels
    assert any(lbl.startswith("control") for lbl in labels)
    w = fc.witness_window()
    assert any(ch[0] == "meshtastic-LF-ch20" for ch in dict(_judged("rtl"))[w])


def test_airspy_plan_is_unchanged():
    fc.set_device("airspy")
    assert fc.windows() == [903.625, 906.300, 910.525]
    assert fc.channels() == fc.FLEET_CHANNELS and fc.witness_window() == 906.300


def test_a_channel_outside_the_device_plan_is_not_reported_unknown():
    """2026-10-05 live: control-gap printed 'UNKNOWN — no usable burst' on the
    RTL, a channel its plan never judges by design — absent, not a failure."""
    fc.set_device("rtl")
    assert "control-gap" not in [c[0] for c in fc.judged_channels()]
    assert "control-rtl" in [c[0] for c in fc.judged_channels()]
    fc.set_device("airspy")
    assert [c[0] for c in fc.judged_channels()] == [c[0] for c in fc.FLEET_CHANNELS]
