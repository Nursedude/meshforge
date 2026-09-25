#!/usr/bin/env python3
"""Phase 0 SDR look at the fleet's LoRa channels — one shot, read-only.

Born 2026-09-24 (operator: "watch the 915 MHz LoRa band around the fleet").
An Airspy Mini on moc5 captures short bursts around each fleet channel and
reports, per channel: band power above the window's noise floor, and the
share of FFT frames in which the channel was BUSY (> floor + BUSY_DB).

What the numbers are OF (stated, not implied):
  * dBFS relative to this receiver's own floor — NOT dBm. Uncalibrated.
  * "busy %" is the share of ~0.7 ms frames over the captured bursts only;
    bursts cover roughly half of wall time, so a short packet can fall
    between them. It is occupancy SEEN, not occupancy.
  * A clipped burst (ADC at full scale) is reported OVERLOAD and its levels
    are not trusted — a strong nearby TX can saturate the front end.
  * Any capture failure reads UNKNOWN for that window, never 0 %.

--witness N correlates the SDR against a record this script did not write:
meshtasticd's own "Received"/"Packet" journal lines on the same box. For
each radio RX event the SDR must have seen the radio's channel busy just
before it; a CONTROL band between ST and LF (nothing of ours transmits
there) is scored the same way and must NOT correlate. If the control hits
as often as the channel, the instrument is measuring something else.

USB: moc5 is a Pi 4 — every port shares one USB2 hub with the CH341 LoRa
radio. Bursts are short and 12-bit packed at 3 MSPS (~9 MB/s) on purpose.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime

import numpy as np

SAMPLE_RATE = 3_000_000
FFT = 2048
USABLE = 0.8          # central share of the window kept (Airspy filter edges)
BUSY_DB = 6.0         # a frame is busy when channel power > floor + this
# A strong burst leaks ~46-55 dB down into its neighbours (MEASURED on moc5
# 2026-09-24: 865 of 866 "busy" frames in the empty control band coincided
# with LF > +30 dB, 0 with LF quiet). A frame is not busy on a channel while
# another fleet channel in the window is this much stronger in that frame.
LEAK_DB = 30.0
FULL_SCALE = 32767
CLIP_FRAC = 1e-4      # > this share of samples at >= 95 % full scale = OVERLOAD

# (label, centre MHz, bandwidth kHz) — DECLARED config, not measured: RNode from
# /etc/reticulum/config, Meshtastic slots from the US band plan
# (902 + BW/2 + (slot-1)*BW), MeshCore from the companion's radio_freq_mhz.
FLEET_CHANNELS = [
    ("rnode", 903.625, 250.0),
    ("meshtastic-ST-ch8", 905.750, 500.0),
    ("control-gap", 906.375, 250.0),   # between ST and LF: nothing of ours
    ("meshtastic-LF-ch20", 906.875, 250.0),
    ("meshcore", 910.525, 62.5),
]
WINDOWS = [903.625, 906.300, 910.525]  # 3 MSPS each; ±1.2 MHz usable


def capture(center_mhz: float, n: int, gain: int, tmpdir: str):
    """(iq complex64 array, start_time) or (None, reason)."""
    fd, path = tempfile.mkstemp(prefix="aspy_", suffix=".iq", dir=tmpdir)
    os.close(fd)
    try:
        t0 = time.time()
        r = subprocess.run(
            ["airspy_rx", "-r", path, "-f", f"{center_mhz:.4f}",
             "-a", str(SAMPLE_RATE), "-t", "2", "-p", "1",
             "-g", str(gain), "-n", str(n)],
            capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            return None, f"airspy_rx rc={r.returncode}: {(r.stderr or r.stdout).strip()[-160:]}"
        raw = np.fromfile(path, dtype=np.int16)
        if raw.size < 2 * FFT:
            return None, f"short capture ({raw.size} int16 values)"
        return raw, t0
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"airspy_rx could not run: {e}"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def analyse(raw: np.ndarray, center_mhz: float, channels):
    """Per-channel stats for one burst. Pure."""
    clip = float(np.mean(np.abs(raw) >= 0.95 * FULL_SCALE))
    iq = raw[0::2].astype(np.float32) + 1j * raw[1::2].astype(np.float32)
    frames = iq.size // FFT
    spec = np.fft.fftshift(
        np.fft.fft(iq[: frames * FFT].reshape(frames, FFT) * np.hanning(FFT), axis=1), axes=1)
    pwr = 10 * np.log10(np.abs(spec) ** 2 + 1e-12)            # frames x bins, dB
    freqs = center_mhz + np.fft.fftshift(np.fft.fftfreq(FFT, 1 / SAMPLE_RATE)) / 1e6
    keep = np.abs(freqs - center_mhz) <= USABLE * SAMPLE_RATE / 2e6
    in_any = np.zeros(FFT, bool)
    for _, c, bw in channels:
        in_any |= np.abs(freqs - c) <= bw / 2000
    floor_bins = keep & ~in_any
    # floor = median over time, then over the bins no channel of ours occupies
    floor = float(np.median(np.median(pwr[:, floor_bins], axis=0))) if floor_bins.any() else float("nan")
    band = {}
    for label, c, bw in channels:
        sel = keep & (np.abs(freqs - c) <= bw / 2000)
        if sel.any():
            band[label] = 10 * np.log10(np.mean(10 ** (pwr[:, sel] / 10), axis=1))  # per-frame
    out = {}
    for label, ch in band.items():
        others = [v for k, v in band.items() if k != label]
        leak = np.zeros(frames, bool)
        for v in others:
            leak |= v - ch > LEAK_DB
        busy = (ch > floor + BUSY_DB) & ~leak
        out[label] = {
            "busy_frames": int(np.sum(busy)), "frames": int(frames),
            "leak_masked_frames": int(np.sum((ch > floor + BUSY_DB) & leak)),
            "median_above_floor_db": float(np.median(ch) - floor),
            "peak_above_floor_db": float(np.max(ch) - floor),
            "busy_mask": busy,
        }
    return out, floor, clip


def sweep(bursts: int, gain: int, tmpdir: str, windows=tuple(WINDOWS), record_busy: bool = False):
    per = {label: {"busy": 0, "frames": 0, "med": [], "peak": -1e9} for label, *_ in FLEET_CHANNELS}
    status = {}
    timeline = []  # (t_start, frame_s, {label: mask}) when record_busy
    n = int(SAMPLE_RATE * 0.5)
    for center in windows:
        chans = [c for c in FLEET_CHANNELS if abs(c[1] - center) <= USABLE * SAMPLE_RATE / 2e6]
        clipped = errors = 0
        for _ in range(bursts):
            raw, t0 = capture(center, n, gain, tmpdir)
            if raw is None:
                errors += 1
                status[center] = f"UNKNOWN — {t0}"
                continue
            stats, floor, clip = analyse(raw, center, chans)
            if clip > CLIP_FRAC:
                clipped += 1
                continue  # levels from a saturated front end are not evidence
            for label, s in stats.items():
                p = per[label]
                p["busy"] += s["busy_frames"]
                p["frames"] += s["frames"]
                p["med"].append(s["median_above_floor_db"])
                p["peak"] = max(p["peak"], s["peak_above_floor_db"])
            if record_busy:
                timeline.append((t0, FFT / SAMPLE_RATE, {k: v["busy_mask"] for k, v in stats.items()}))
        if clipped:
            status[center] = f"OVERLOAD in {clipped}/{bursts} bursts (dropped; lower -g)"
        elif errors == bursts:
            pass  # status already UNKNOWN
        else:
            status.setdefault(center, "ok")
    return per, status, timeline


def radio_rx_times(since: float, until: float):
    """meshtasticd's own received-packet timestamps (epoch s). None = unobservable."""
    try:
        r = subprocess.run(
            ["journalctl", "-u", "meshtasticd", "--since", f"@{int(since) - 1}",
             "--until", f"@{int(until) + 2}", "-o", "short-unix", "--no-pager", "-q"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"journalctl could not run: {e}"
    if r.returncode != 0:
        return None, f"journalctl rc={r.returncode} (need sudo / systemd-journal group?)"
    pat = re.compile(r"^(\d+\.\d+) .*\[Router\] (Received |Rebroadcast received)")
    return [float(m.group(1)) for ln in r.stdout.splitlines() if (m := pat.match(ln))], None


def hit_rate(events, timeline, label, lookback=1.5):
    """Share of events whose preceding `lookback` s the SDR covered AND saw busy."""
    covered = hits = 0
    for t in events:
        seen_cover = seen_busy = False
        for t0, fs, masks in timeline:
            m = masks.get(label)
            if m is None:
                continue
            idx = np.arange(m.size)
            ft = t0 + idx * fs
            win = (ft >= t - lookback) & (ft <= t)
            if win.any():
                seen_cover = True
                seen_busy |= bool(m[win].any())
        covered += seen_cover
        hits += seen_cover and seen_busy
    return covered, hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bursts", type=int, default=10, help="0.5 s bursts per window")
    ap.add_argument("--gain", type=int, default=10, help="airspy linearity gain 0-21")
    ap.add_argument("--witness", type=int, default=0, metavar="SECONDS",
                    help="also correlate LF-window bursts against meshtasticd RX for this long")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if shutil.which("airspy_rx") is None:
        print("UNKNOWN — airspy_rx is not installed on this box; nothing was captured.")
        return 2
    tmpdir = "/dev/shm" if os.path.isdir("/dev/shm") else tempfile.gettempdir()

    per, status, _ = sweep(a.bursts, a.gain, tmpdir)
    report = {"when": datetime.now().isoformat(timespec="seconds"), "gain": a.gain,
              "bursts_per_window": a.bursts, "windows": {f"{k:.3f}": v for k, v in status.items()},
              "channels": {}}
    for label, c, bw in FLEET_CHANNELS:
        p = per[label]
        report["channels"][label] = None if not p["frames"] else {
            "centre_mhz": c, "bw_khz": bw,
            "busy_pct": round(100 * p["busy"] / p["frames"], 2),
            "median_above_floor_db": round(float(np.median(p["med"])), 1),
            "peak_above_floor_db": round(p["peak"], 1)}

    if a.witness:
        timeline = []
        t_start = time.time()
        while time.time() - t_start < a.witness:
            _, _, tl = sweep(1, a.gain, tmpdir, windows=(906.300,), record_busy=True)
            timeline += tl
        events, why = radio_rx_times(t_start, time.time())
        if events is None:
            report["witness"] = f"UNKNOWN — {why}"
        else:
            w = {"radio_rx_events": len(events), "bursts": len(timeline)}
            for label in ("meshtastic-LF-ch20", "meshtastic-ST-ch8", "control-gap"):
                cov, hits = hit_rate(events, timeline, label)
                w[label] = {"covered": cov, "hits": hits,
                            "hit_pct": round(100 * hits / cov, 1) if cov else None}
            report["witness"] = w

    if a.json:
        print(json.dumps(report, indent=1))
        return 0
    print(f"SDR fleet-channel look — {report['when']}  (gain {a.gain}, "
          f"{a.bursts} x 0.5 s bursts/window; dB above THIS receiver's floor, not dBm)")
    for k, v in report["windows"].items():
        if v != "ok":
            print(f"  window {k} MHz: {v}")
    print(f"  {'channel':<20} {'MHz':>8} {'busy%':>6} {'median':>7} {'peak':>6}")
    for label, v in report["channels"].items():
        if v is None:
            print(f"  {label:<20} {'':>8} UNKNOWN — no usable burst")
        else:
            print(f"  {label:<20} {v['centre_mhz']:>8.3f} {v['busy_pct']:>6.2f} "
                  f"{v['median_above_floor_db']:>6.1f}dB {v['peak_above_floor_db']:>5.1f}dB")
    if "witness" in report:
        print(f"  witness: {json.dumps(report['witness'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
