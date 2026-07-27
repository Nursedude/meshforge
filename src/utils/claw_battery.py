"""Dude-claw battery intelligence — level + TREND + intent, not a bare volt.

Why this exists (2026-07-24, operator: *"agreed on the claw batt discharge,
it's not failed — a bit more intelligence with these metrics would help"*):

The claw's battery metric was a single instantaneous voltage compared against
one threshold. That cannot tell apart the three things an operator actually
needs to distinguish:

    a pack DELIBERATELY discharging on a soak    (expected, on plan)
    a pack losing charge nobody asked to lose    (the real warning, and early)
    a node that CRASHED with a healthy pack      (not a battery problem at all)

All three looked identical: ``volts < 3.5`` → "charge or swap the pack". So the
one claw the fleet was deliberately running to cutoff paged as degraded for the
whole experiment, and — worse — a claw that dies at 4.0 V got the same "check
the battery" advice as one that genuinely ran flat.

This module is the pure layer that makes the difference sayable. It takes a
series of ``(ts, volts)`` readings and returns a CLASSIFICATION: what state the
pack is in, which way it is going, how long it has (labelled as a projection,
never as a measurement), and whether that is what we intended.

Honesty rules it inherits from the domain:

- A reading we do not have is ``unknown`` — never "charged" and never "flat"
  (honest_failure_modes #1: the degraded value must not overlap the healthy one).
- Too few samples / too short a window yields ``slope=None``, NOT 0.0. A
  fabricated flat trend would read as "on a charger" — the healthy domain again.
- ``hours_to_cutoff`` is a LINEAR PROJECTION off the fitted slope. Every caller
  that renders it must say so; it is not a measured runtime (that is
  battery_soak's MEASURED verdict, derived from a real cutoff crossing).
- Wall clock is forgeable on this RTC-less fleet (honest_failure_modes #6):
  ``read_series`` DROPS rows whose timestamp steps backward relative to the
  running max (a backward step re-sorted into fake order can fabricate
  CHARGING from a discharging pack), duplicate timestamps are deduped, and an
  absurd projection is clamped to None rather than rendered.

It is also the SSOT for the pack thresholds. Before this, ``watchdog_probes_
liveness.CLAW_BATTERY_FLOOR_V`` (3.5) and ``battery_soak.DEFAULT_CUTOFF_V``
(3.4) were independent hardcodes describing the same 1S LiPo — two consumers of
one artifact WILL drift (honest_failure_modes #5). Both import from here now.

Pure + stdlib-only: no domain imports, no I/O beyond the small append-only
series helpers at the bottom, so it is trivially testable and safe to call from
a watchdog probe tick.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ── Pack thresholds — the SSOT for a single-cell LiPo on a dude-claw ──────
#: Below this the cell is in the knee: hours, not days, of useful runtime.
FLOOR_V = 3.5
#: End-of-discharge. A soak measures runtime TO this; below it the node is
#: about to drop off and its RF/witness coverage goes with it.
CUTOFF_V = 3.4
#: |ΔV| under this across the OBSERVED window is noise, not a trend. The claw
#: gauge quantises to 10 mV (`Battery: 3.46 V (adc 706 mV)`), so this is three
#: ADC steps — the smallest movement that cannot be a rounding artifact.
NOISE_V = 0.03

# ── The trend gate is WINDOW-AWARE, not a fixed slope (2026-07-24) ────────
# Measured on moc2/dudeclaw-02 (227 samples, 37.5 h, 4.03 V → 3.46 V): a pack
# that has plainly lost 570 mV fits at −2.8 mV/h over its trailing 6 h, i.e.
# 16 mV of movement — under the noise floor and unresolvable, while the SAME
# pack fits −7.5 mV/h (89 mV) over 12 h, which is unmistakably real.
#
# A fixed slope threshold cannot serve both: set it low enough to catch the
# slow-but-real 12 h decline and a 30-minute window can declare a trend from
# 1.4 mV — one seventh of an ADC step, i.e. a slope manufactured out of
# quantisation noise. So the gate is on the FITTED CHANGE ACROSS THE WINDOW
# (|slope| × window_hr ≥ NOISE_V), which self-calibrates: 30 mV/h over half an
# hour, 3 mV/h over ten hours. One threshold, honest at every timescale.


def is_trend(slope_v_per_hr: Optional[float], window_hr: Optional[float]) -> bool:
    """Does this fit represent real movement, or the noise floor?"""
    if slope_v_per_hr is None or not window_hr or window_hr <= 0:
        return False
    return abs(slope_v_per_hr) * window_hr >= NOISE_V


#: A trend claim needs enough evidence: this many readings spanning this long.
#: Below either bar the slope is None (unknown), never zero.
MIN_TREND_SAMPLES = 4
MIN_TREND_WINDOW_S = 1800.0          # 30 min

#: Fit only the TRAILING window. A discharge curve is not a line: fitting all
#: 37.5 h of the moc2 soak gives −14.6 mV/h ("3.4 h to cutoff") because the
#: average is dominated by the steep opening drop, while the trailing 12 h
#: gives −7.5 mV/h (~6.7 h). "At the current rate" has to mean RECENT.
FIT_WINDOW_S = 12 * 3600.0

#: Under this much observed history, "not moving" is NOT evidence of a stable,
#: charge-backed pack — it is a window too short to resolve movement. Calling
#: it FLOAT would dress an unobserved direction as health (the pack above sat
#: at exactly 3.46 V for 20 minutes while down 570 mV on the run).
MIN_FLOAT_WINDOW_HR = 3.0

#: A linear projection beyond this is not information, it is arithmetic on
#: noise — report None instead of "1,900 h to cutoff".
MAX_PROJECTION_HR = 720.0            # 30 days

#: Rolling series cap (~7 days at the */5 capture cadence). The series exists
#: to answer "which way was it going", not to be an archive — battery_soak
#: owns the durable experiment record.
DEFAULT_HISTORY_CAP = 2016

# ── Pack states ──────────────────────────────────────────────────────────
UNKNOWN = "unknown"           # no reading — NOT charged, NOT flat
CHARGING = "charging"         # rising: on a charge source
FLOAT = "float"               # flat and healthy: USB-backed / topped off
DISCHARGING = "discharging"   # falling, still above the working floor
KNEE = "knee"                 # below the floor: hours left
CRITICAL = "critical"         # at/below cutoff: about to drop off

#: Worst-first, for rolling several claws into one verdict.
_STATE_RANK = {CRITICAL: 0, KNEE: 1, DISCHARGING: 2, UNKNOWN: 3,
               FLOAT: 4, CHARGING: 5}


def _clean(samples: Sequence[Any]) -> List[Tuple[float, float]]:
    """(ts, volts) pairs from loose input: dicts, tuples, junk. Sorted, with
    non-numeric/bool voltages and duplicate timestamps dropped.

    The sort exists to INTERLEAVE multiple per-source-monotonic series (the
    rolling probe series merged with battery_soak's record of the same pack) —
    per-file backward wall-clock steps are dropped at read time by
    ``read_series``, never re-sorted into fake chronology here.

    bool is checked explicitly because ``isinstance(True, int)`` is True in
    Python and a stray True would become 1.0 V — a fabricated flat pack.
    """
    out: List[Tuple[float, float]] = []
    for s in samples or []:
        if isinstance(s, dict):
            ts, v = s.get("ts"), s.get("volts", s.get("vbat"))
        elif isinstance(s, (tuple, list)) and len(s) >= 2:
            ts, v = s[0], s[1]
        else:
            continue
        if isinstance(ts, bool) or isinstance(v, bool):
            continue
        if not isinstance(ts, (int, float)) or not isinstance(v, (int, float)):
            continue
        out.append((float(ts), float(v)))
    out.sort(key=lambda p: p[0])
    # Drop duplicate timestamps (a re-read of the same capture): they add no
    # evidence and would let one tick dominate a least-squares fit.
    deduped: List[Tuple[float, float]] = []
    for ts, v in out:
        if deduped and deduped[-1][0] == ts:
            deduped[-1] = (ts, v)
            continue
        deduped.append((ts, v))
    return deduped


def fit_slope_v_per_hr(samples: Sequence[Any]) -> Optional[float]:
    """Least-squares slope in V/hr, or None when the evidence is too thin.

    Least squares (not first-vs-last) because a single jittery endpoint would
    otherwise set the whole trend — and this number feeds a projection the
    operator plans around. None means "we cannot say", which callers must
    render as unknown rather than as flat.
    """
    pts = _clean(samples)
    if len(pts) < MIN_TREND_SAMPLES:
        return None
    if (pts[-1][0] - pts[0][0]) < MIN_TREND_WINDOW_S:
        return None
    n = float(len(pts))
    xs = [(ts - pts[0][0]) / 3600.0 for ts, _ in pts]
    ys = [v for _, v in pts]
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom


def classify(samples: Sequence[Any], *,
             floor_v: float = FLOOR_V,
             cutoff_v: float = CUTOFF_V,
             soak_armed: bool = False,
             fit_window_s: Optional[float] = FIT_WINDOW_S) -> Dict[str, Any]:
    """Classify one claw's pack from its reading series.

    ``soak_armed`` = this device is the one a battery_soak config deliberately
    points at, i.e. its discharge is the EXPERIMENT. It never changes the
    measured facts — only ``expected``, which tells an alerting layer that this
    decline was asked for. A soak-armed pack that is charging or flat is still
    reported as charging or flat; intent cannot invent a state.

    Returns (all keys always present, unknown as None — never a stand-in):
        state             one of the module's state constants
        volts             newest reading, or None
        slope_v_per_hr    fitted trend over the trailing window, or None
        hours_to_cutoff   LINEAR PROJECTION off the slope, or None
        window_hr         span actually fitted (≤ fit_window_s)
        n                 usable readings held
        n_fit             readings inside the fitted window
        trend             bool: the fit clears the noise floor
        expected          bool: soak-armed AND actually declining
        summary           one honest human sentence
    """
    pts = _clean(samples)
    out: Dict[str, Any] = {
        "state": UNKNOWN, "volts": None, "slope_v_per_hr": None,
        "hours_to_cutoff": None, "window_hr": None, "n": len(pts),
        "n_fit": 0, "trend": False, "expected": False, "summary": "",
    }
    if not pts:
        out["summary"] = ("no battery reading — unknown, which is neither "
                          "charged nor flat")
        return out

    volts = pts[-1][1]          # the NEWEST reading, whatever the fit window
    out["volts"] = volts

    # Fit the trailing window only — see FIT_WINDOW_S.
    fit_pts = pts
    if fit_window_s:
        cut = pts[-1][0] - float(fit_window_s)
        fit_pts = [p for p in pts if p[0] >= cut] or pts[-1:]
    window_hr = (fit_pts[-1][0] - fit_pts[0][0]) / 3600.0
    out["window_hr"] = round(window_hr, 3)
    out["n_fit"] = len(fit_pts)
    slope = fit_slope_v_per_hr(fit_pts)
    out["slope_v_per_hr"] = round(slope, 5) if slope is not None else None
    trend = is_trend(slope, window_hr)
    out["trend"] = trend

    # Level decides the urgent states; trend decides the rest. A pack under the
    # floor is in the knee whichever way it is drifting — being on a charger
    # does not undo where the cell currently sits.
    if volts <= cutoff_v:
        state = CRITICAL
    elif volts < floor_v:
        state = KNEE
    elif slope is None:
        state = UNKNOWN          # healthy level, but direction unobserved
    elif trend:
        state = DISCHARGING if slope < 0 else CHARGING
    elif window_hr < MIN_FLOAT_WINDOW_HR:
        # No resolvable movement, but not enough history to call it stable.
        # FLOAT would assert a charge-backed pack we have not observed.
        state = UNKNOWN
    else:
        state = FLOAT
    out["state"] = state

    # Project ONLY off a fit that cleared the noise floor. Dividing by a
    # noise-level drift produces a confident-looking "~692 h to cutoff" that is
    # arithmetic on noise, not a forecast. A pack we call FLOAT (or whose
    # direction we cannot resolve) must never carry a countdown.
    if trend and slope < 0 and volts > cutoff_v:
        hrs = (volts - cutoff_v) / (-slope)
        # Arithmetic on noise is not a forecast — say nothing rather than a
        # number the operator would plan around.
        out["hours_to_cutoff"] = round(hrs, 1) if hrs <= MAX_PROJECTION_HR else None

    declining = state in (DISCHARGING, KNEE, CRITICAL) and (
        slope is None or slope < 0)
    out["expected"] = bool(soak_armed and declining)

    out["summary"] = _summarize(out, floor_v=floor_v, cutoff_v=cutoff_v)
    return out


def _summarize(c: Dict[str, Any], *, floor_v: float, cutoff_v: float) -> str:
    v = c["volts"]
    slope = c["slope_v_per_hr"]
    win = c["window_hr"] or 0.0
    bits = [f"{v:.2f} V"]
    if slope is None:
        bits.append(f"trend unknown ({c['n_fit']} reading(s) over "
                    f"{win:.1f} h — too thin to fit)")
    elif c["trend"]:
        arrow = "falling" if slope < 0 else "rising"
        bits.append(f"{arrow} {abs(slope) * 1000:.0f} mV/h over {win:.1f} h")
    elif win < MIN_FLOAT_WINDOW_HR:
        bits.append(f"direction unobserved — only {win:.1f} h of readings, and "
                    f"any movement in it is under the {NOISE_V * 1000:.0f} mV "
                    f"noise floor")
    else:
        bits.append(f"no resolvable movement over {win:.1f} h "
                    f"(under the {NOISE_V * 1000:.0f} mV noise floor)")
    if c["hours_to_cutoff"] is not None:
        bits.append(f"~{c['hours_to_cutoff']:.0f} h to {cutoff_v:.2f} V "
                    f"(LINEAR projection, not measured)")
    state_txt = {
        CRITICAL: f"at/below cutoff {cutoff_v:.2f} V",
        KNEE: f"in the knee (under {floor_v:.2f} V)",
        DISCHARGING: "discharging",
        CHARGING: "charging",
        FLOAT: "flat/charge-backed",
        UNKNOWN: "level OK, direction unobserved",
    }[c["state"]]
    tail = " — EXPECTED: this device is the armed battery soak" if c["expected"] else ""
    return f"{state_txt}: {', '.join(bits)}{tail}"


def worst_state(states: Sequence[str]) -> str:
    """Worst pack state in the list (critical > knee > discharging > unknown >
    float > charging). Empty → unknown: nothing observed cannot be health."""
    if not states:
        return UNKNOWN
    return min(states, key=lambda s: _STATE_RANK.get(s, _STATE_RANK[UNKNOWN]))


# ── The rolling series (small, append-only, capped) ──────────────────────
# The watchdog probe only ever sees the LAST tick, so without this there is no
# trend to fit and no way to know what a claw's pack was doing just before it
# went dark. battery_soak.jsonl is the durable experiment record for the ONE
# armed device; this is the cheap always-on series for every claw.

def series_path(state_dir: str, device: str) -> str:
    """Per-device series file beside the probe's own debounce state."""
    safe = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in str(device))
    return os.path.join(state_dir, f"claw_battery_series.{safe}.jsonl")


def read_series(path: str, *, now: Optional[float] = None,
                max_age_s: Optional[float] = None) -> List[Dict[str, Any]]:
    """Read a series file. A torn/partial line is skipped, never fatal — one
    bad append must not blind the trend. Missing file → [] (no readings is a
    real, honest answer here; the classifier renders it as unknown).

    A row whose timestamp steps BACKWARD relative to the running max is
    dropped, keeping the first occurrence (a backward wall-clock step on this
    RTC-less fleet, honest_failure_modes #6): re-sorting such rows into fake
    temporal order can fabricate CHARGING from a discharging pack."""
    rows: List[Dict[str, Any]] = []
    max_ts: Optional[float] = None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                ts = row.get("ts")
                if isinstance(ts, (int, float)) and not isinstance(ts, bool):
                    if max_ts is not None and float(ts) < max_ts:
                        continue        # backward step — dropped, not re-sorted
                    max_ts = float(ts)
                rows.append(row)
    except OSError:
        return []
    if max_age_s is not None and now is not None:
        rows = [r for r in rows
                if isinstance(r.get("ts"), (int, float))
                and (now - float(r["ts"])) <= max_age_s]
    return rows


#: The battery soak's own append-only record, in the operator's home. For a
#: soak-armed claw this is the LONGER, DENSER, authoritative series — 227
#: samples over 37.5 h on moc2 where the probe's rolling one had 5 over 20 min.
#: Building a second record of one pack and then reading the shorter one is the
#: two-records-of-one-artifact trap (honest_failure_modes #5); merge instead.
SOAK_SERIES_BASENAME = "battery_soak.jsonl"


def read_soak_series(home: Optional[str], device: str) -> List[Dict[str, Any]]:
    """The soak's readings for ONE device, normalised to the series shape.

    Rows are ``{ts, vbat, device}``. Rows tagged for another device are not
    ours; rows with NO device tag predate tagging and cannot be attributed, so
    they are dropped too rather than blended into this pack's curve (the same
    policy battery_soak's own device-scoped read uses). A blind sample
    (``vbat: null``) is not a reading and never becomes 0 V.
    """
    if not home:
        return []
    rows: List[Dict[str, Any]] = []
    for r in read_series(os.path.join(str(home), SOAK_SERIES_BASENAME)):
        if r.get("device") != device:
            continue
        ts, v = r.get("ts"), r.get("vbat")
        if isinstance(ts, bool) or isinstance(v, bool):
            continue
        if isinstance(ts, (int, float)) and isinstance(v, (int, float)):
            rows.append({"ts": float(ts), "volts": float(v)})
    return rows


def append_sample(path: str, ts: float, volts: Optional[float], *,
                  cap: int = DEFAULT_HISTORY_CAP) -> str:
    """Append one reading if it is NEW, capping the file.

    Returns a witness string, never a bool (2026-07-26 review C3 — a single
    False conflated "nothing new" with "the series is silently dying"):

        "written"    a row was appended
        "duplicate"  same capture timestamp already recorded (benign — the
                     probe re-reads the same tick file every minute while the
                     capture cron writes every 5)
        "unusable"   ts/volts not a usable numeric reading (never recorded)
        "io_error"   the write FAILED — the series is frozen; the caller must
                     log/witness this (honest_failure_modes #9), because a
                     frozen series serves a stale trend as current

    Never raises — a probe must not die on a full disk.
    """
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return "unusable"
    if not isinstance(volts, (int, float)) or isinstance(volts, bool):
        return "unusable"
    existing = read_series(path)
    if existing and any(r.get("ts") == ts for r in existing[-8:]):
        return "duplicate"
    rows = existing + [{"ts": float(ts), "volts": float(volts)}]
    if len(rows) > cap:
        rows = rows[-cap:]
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return "written"
    except OSError:
        return "io_error"
