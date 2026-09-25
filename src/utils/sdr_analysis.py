"""Pure analysis of one Airspy IQ burst for interference — SDR Phase 1, step 1.

Design: `.claude/plans/sdr_interference_phase1.md` (rev 2, Fable-reviewed).
Pure functions only: no capture, no files, no clock — the writer
(`scripts/sdr_interference.py`, step 2) owns those. Every threshold below is
an ASSERTED starting point to be replaced from the 7-day soak.

What a result is OF (stated, never implied):
  * dBFS at a FIXED gain — not dBm. 0 dBFS = a full-scale complex tone.
  * Only frames the gates KEEP are judged. A frame is dropped when one of our
    own fleet channels is loud in it (near-field TX: five emitters sit < 10 ft
    from the antenna) or when the whole frame's floor jumps (a blocker —
    possibly outside this window, which no in-window channel gate can see).
  * Energy detection is BLIND below the noise floor, where LoRa still
    decodes (~-20 dB SNR). An empty finding list is "nothing SEEN above the
    floor in the kept frames", never "the band is clean".

Statuses: ``ok``; ``overload`` (ADC clipped — levels not evidence, no
findings); ``unjudgeable`` (too few frames survived the gates — e.g. our own
TX filled the burst; no findings, and that is not "clean").
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

SAMPLE_RATE = 3_000_000
FFT = 2048
USABLE_HALF_MHZ = 1.2         # Airspy filter edges: keep the central ±1.2 MHz
FULL_SCALE = 32767            # int16 = (adc-2048)<<4 then FIR (reviewed at source)
CLIP_LEVEL = 0.95
CLIP_FRAC = 1e-4              # > this share of samples at >= 95 % FS = overload

BUSY_DB = 6.0                 # frame busy: band power > floor + this
LEAK_DB = 30.0                # not busy while another fleet band is this much stronger
OWN_TX_DBFS = -40.0           # any fleet band above this (per-bin mean) = our near-field TX
BLOCKER_DB = 6.0              # frame out-of-channel floor > burst floor + this = blocker
CARRIER_DB = 10.0             # class A: bin time-median > floor + this
GUARD_KHZ = 25.0              # class A/C keep this far from a fleet channel edge
SLICE_KHZ = 125.0             # class C slice width
FOREIGN_BUSY_PCT = 2.0        # class C: slice busy in > this % of kept frames
MIN_KEPT_FRAMES = 8           # absolute floor on survivors
MIN_KEPT_FRAC = 0.25          # AND at least this share of the burst (review #5:
                              # 8 of 732 frames = 1 % once read as a judged burst)
REL_TX_DB = 20.0              # ALSO own TX: any fleet band > burst floor + this.
                              # Review #1: the absolute gate alone failed open for
                              # our own emitters 15-36 dB weaker than the recorded
                              # one (dudeclaw-02 is one), and class C called them
                              # foreign on 4-11 slices.

# Coherent gain of a Hann window is 0.5: a full-scale complex tone lands in
# its bin at |X| = FULL_SCALE * FFT * 0.5. That is 0 dBFS here.
_REF_DB = 20 * np.log10(FULL_SCALE * FFT * 0.5)


@dataclass(frozen=True)
class Channel:
    label: str
    center_mhz: float
    bw_khz: float

    def half_mhz(self, guard_khz: float = 0.0) -> float:
        return (self.bw_khz / 2 + guard_khz) / 1000.0


# DECLARED config, not measured: RNode from /etc/reticulum/config, Meshtastic
# slots from the US band plan (902 + BW/2 + (slot-1)*BW), MeshCore from the
# companion's radio_freq_mhz (2026-09-24).
FLEET_CHANNELS: Tuple[Channel, ...] = (
    Channel("rnode", 903.625, 250.0),
    Channel("meshtastic-ST-ch8", 905.750, 500.0),
    Channel("meshtastic-LF-ch20", 906.875, 250.0),
    Channel("meshcore", 910.525, 62.5),
)


#: The Airspy's OWN spurs — lines still >= 6 dB over the floor with the
#: antenna REMOVED (open SMA, moc5, 2026-09-24 18:43; 2 x 0.5 s per window).
#: Keyed by (window centre MHz, linearity gain): the ~1/3-MHz comb MOVES with
#: the tuning centre, so a frequency-only map would be wrong. Open input is
#: not a terminator — replace with a terminated run when one exists. Lines
#: that appear ONLY with the antenna on are not here on purpose: 925.006
#: rides the comb but is antenna-dependent (likely radiated by local
#: electronics — BELIEVED), 937.515 is off the comb (external, real).
SPUR_MAP_MHZ: Dict[Tuple[float, int], Tuple[float, ...]] = {
    # Per fleet window: every line seen with the antenna off at ANY gain —
    # the comb's frequencies follow the tuning centre, not the gain — so both
    # gains the writer uses get the same set (review #9: 910.525 had none,
    # and its 910.027 line reads +8.1 dB against CARRIER_DB 10 at gain 10).
    **{(903.625, g): (902.4604, 902.79, 903.1226, 903.3774, 903.7891, 904.126, 904.4585)
       for g in (10, 21)},
    **{(906.3, g): (905.134, 905.4021, 905.465, 905.7976, 905.9982, 906.4641, 906.801,
                    907.1335, 907.4675) for g in (10, 21)},
    **{(910.525, g): (909.69, 910.0226, 910.3609, 910.6891, 911.0245, 911.3585, 911.6939)
       for g in (10, 21)},
}


def spectrogram(raw: np.ndarray, center_mhz: float) -> Tuple[np.ndarray, np.ndarray]:
    """(freqs_mhz[bins], power_dbfs[frames, bins]) for interleaved int16 IQ."""
    raw = np.asarray(raw)
    if raw.ndim != 1 or raw.size % 2 or raw.size < 2 * FFT:
        raise ValueError(f"need >= {FFT} interleaved IQ pairs, got {raw.size} values")
    iq = raw[0::2].astype(np.float32) + 1j * raw[1::2].astype(np.float32)
    frames = iq.size // FFT
    spec = np.fft.fftshift(
        np.fft.fft(iq[: frames * FFT].reshape(frames, FFT) * np.hanning(FFT), axis=1), axes=1)
    pwr = 10 * np.log10(np.abs(spec) ** 2 + 1e-12) - _REF_DB
    freqs = center_mhz + np.fft.fftshift(np.fft.fftfreq(FFT, 1 / SAMPLE_RATE)) / 1e6
    return freqs, pwr


def im3_products(channels: Sequence[Channel] = FLEET_CHANNELS) -> List[Tuple[float, float, str]]:
    """Third-order products of our own channels: (center_mhz, half_width_mhz, parents).

    2fi - fj and fi + fj - fk. Width is the sum of the parents' bandwidths —
    the product of two chirps spreads over both.
    """
    out = []
    for a, b in permutations(channels, 2):
        out.append((2 * a.center_mhz - b.center_mhz,
                    (2 * a.bw_khz + b.bw_khz) / 2000.0, f"2x{a.label}-{b.label}"))
    for a, b, c in permutations(channels, 3):
        if a.label < b.label:  # fi+fj symmetric
            out.append((a.center_mhz + b.center_mhz - c.center_mhz,
                        (a.bw_khz + b.bw_khz + c.bw_khz) / 2000.0, f"{a.label}+{b.label}-{c.label}"))
    return out


def _in_bands(freqs: np.ndarray, channels: Iterable[Channel], guard_khz: float) -> np.ndarray:
    m = np.zeros(freqs.size, bool)
    for ch in channels:
        m |= np.abs(freqs - ch.center_mhz) <= ch.half_mhz(guard_khz)
    return m


def _mean_db(p_db: np.ndarray, axis: int) -> np.ndarray:
    return 10 * np.log10(np.mean(10 ** (p_db / 10), axis=axis))


def analyse_window(raw: np.ndarray, center_mhz: float,
                   channels: Sequence[Channel] = FLEET_CHANNELS,
                   spur_mhz: Sequence[float] = (),
                   own_tx_dbfs: float = OWN_TX_DBFS,
                   rel_tx_db: float = REL_TX_DB) -> Dict:
    """Judge one burst. Pure. See the module docstring for what it is OF."""
    raw = np.asarray(raw)
    # int32 first: np.abs(int16 -32768) overflows to -32768, and the Airspy's
    # int16 is (adc-2048)<<4 — an ADC pinned at its NEGATIVE rail is exactly
    # -32768, so an int16 abs() read a saturated front end as unclipped.
    clip = float(np.mean(np.abs(raw.astype(np.int32)) >= CLIP_LEVEL * FULL_SCALE)) if raw.size else 0.0
    freqs, pwr = spectrogram(raw, center_mhz)          # raises on short input
    frames = pwr.shape[0]
    result: Dict = {"center_mhz": center_mhz, "frames": frames, "clip_frac": clip,
                    "status": "ok", "channels": {}, "carriers": [], "spurs": [],
                    "foreign": [], "own_tx_frames": 0, "blocker_frames": 0,
                    "kept_frames": 0, "floor_dbfs": None, "floor_profile": None}
    if clip > CLIP_FRAC:
        result["status"] = "overload"
        return result

    keep = np.abs(freqs - center_mhz) <= USABLE_HALF_MHZ
    here = [c for c in channels if np.any(keep & (np.abs(freqs - c.center_mhz) <= c.half_mhz()))]
    guarded = _in_bands(freqs, channels, GUARD_KHZ)
    out_bins = keep & ~guarded
    if not out_bins.any():
        raise ValueError("window has no bins outside the fleet channels to measure a floor on")

    band = {}
    for c in here:
        sel = keep & (np.abs(freqs - c.center_mhz) <= c.half_mhz())
        band[c.label] = _mean_db(pwr[:, sel], axis=1)             # per-frame, dBFS
    frame_floor = np.median(pwr[:, out_bins], axis=1)
    burst_floor = float(np.median(frame_floor))
    own_tx = np.zeros(frames, bool)
    for v in band.values():
        own_tx |= (v > own_tx_dbfs) | (v > burst_floor + rel_tx_db)
    blocker = frame_floor > burst_floor + BLOCKER_DB
    kept = ~own_tx & ~blocker
    result.update(own_tx_frames=int(own_tx.sum()), blocker_frames=int((blocker & ~own_tx).sum()),
                  kept_frames=int(kept.sum()), kept_frac=round(float(kept.mean()), 3))
    if kept.sum() < max(MIN_KEPT_FRAMES, MIN_KEPT_FRAC * frames):
        result["status"] = "unjudgeable"
        return result

    pk = pwr[kept]
    profile = np.median(pk, axis=0)                       # per-bin time-median floor
    floor = float(np.median(profile[out_bins]))
    result["floor_dbfs"] = round(floor, 2)
    result["floor_profile"] = profile[keep]

    # In-channel activity is judged over every non-blocker frame: our own
    # traffic IS what it measures, so the own-TX gate must not hide it.
    # A near-field burst also lifts the whole frame (reciprocal mixing), so it
    # trips the blocker gate too — exclude only blockers that are NOT ours.
    act = ~(blocker & ~own_tx)
    for label, v in band.items():
        vk = v[act]
        leak = np.zeros(vk.size, bool)
        for other, ov in band.items():
            if other != label:
                leak |= ov[act] - vk > LEAK_DB
        busy = (vk > floor + BUSY_DB) & ~leak
        pct = 100.0 * busy.mean()
        result["channels"][label] = {"busy_pct": round(float(pct), 2), "saturated_in_sample": bool(pct > 50.0),
                                     "median_dbfs": round(float(np.median(vk)), 2)}

    # Class A: bins OUTSIDE every fleet channel (+guard) whose time-median
    # sits CARRIER_DB over the floor, grouped into contiguous runs.
    hot = out_bins & (profile > floor + CARRIER_DB)
    idx = np.flatnonzero(hot)
    groups: List[List[int]] = []
    for i in idx:
        if groups and i - groups[-1][-1] <= 1:
            groups[-1].append(i)
        else:
            groups.append([i])
    bin_mhz = SAMPLE_RATE / FFT / 1e6
    for g in groups:
        j = g[int(np.argmax(profile[g]))]
        hit = {"freq_mhz": round(float(freqs[j]), 4), "level_dbfs": round(float(profile[j]), 1),
               "above_floor_db": round(float(profile[j] - floor), 1), "width_bins": len(g)}
        is_spur = any(abs(freqs[j] - s) <= 2 * bin_mhz for s in spur_mhz)
        (result["spurs"] if is_spur else result["carriers"]).append(hit)

    # Class C: 125 kHz slices clear of every fleet channel (+guard); busy in
    # > FOREIGN_BUSY_PCT of the kept frames. Tagged, not dropped, when the
    # slice sits on one of our own IM3 products.
    ims = im3_products(channels)
    # No separate slice LEAK gate (design §2.C named one): a slice leak needs a
    # fleet band > floor + BUSY_DB + LEAK_DB (36 dB), and REL_TX_DB (20) has
    # already removed every such frame from `kept`. Mutation-tested 2026-09-24:
    # a slice leak gate here could never fire, and a gate that cannot fire is
    # a claim of defence, not a defence.
    half = SLICE_KHZ / 2000.0
    lo = center_mhz - USABLE_HALF_MHZ + half
    while lo <= center_mhz + USABLE_HALF_MHZ - half + 1e-9:
        sel = keep & (np.abs(freqs - lo) <= half)
        if sel.any() and not (sel & guarded).any():
            sp = _mean_db(pk[:, sel], axis=1)
            busy = sp > floor + BUSY_DB
            pct = 100.0 * busy.mean()
            if pct > FOREIGN_BUSY_PCT:
                tag = [p for c, hw, p in ims if abs(lo - c) <= hw + half]
                result["foreign"].append({
                    "slice_mhz": round(lo, 4), "busy_pct": round(pct, 2),
                    "peak_above_floor_db": round(float(sp.max() - floor), 1),
                    "im3_candidate": tag or None})
        lo += 2 * half
    return result


def floor_delta_db(current: np.ndarray, reference: np.ndarray) -> float:
    """Class B: median per-bin difference of two floor profiles (same window, same gain).

    Per-bin, not scalar, because the passband has a fixed ±1.2 dB shape that
    would otherwise bias the edge bins (review R6).
    """
    current, reference = np.asarray(current), np.asarray(reference)
    if current.shape != reference.shape:
        raise ValueError(f"profile shapes differ: {current.shape} vs {reference.shape}")
    return float(np.median(current - reference))


def persistent(current: Sequence[Dict], previous: Optional[Sequence[Dict]],
               tol_khz: float = 3.0) -> List[Dict]:
    """Class A findings present in THIS run and the previous one (>= 2 consecutive runs).

    ``previous is None`` (no prior run, or it was not ok) confirms nothing.
    """
    if not previous:
        return []
    return [c for c in current
            if any(abs(c["freq_mhz"] - p["freq_mhz"]) * 1000 <= tol_khz for p in previous)]
