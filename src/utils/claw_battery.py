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
- Wall clock is forgeable on this RTC-less fleet (honest_failure_modes #6), so
  samples are sorted, duplicate/backward timestamps are dropped, and an absurd
  projection is clamped to None rather than rendered.

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
#: |ΔV| under this over a whole window is noise, not a trend (ADC jitter).
NOISE_V = 0.03
#: Slope magnitude that counts as really moving, V/hr. Over a 6 h window this
#: is exactly NOISE_V, so the two thresholds cannot drift apart in meaning.
TREND_V_PER_HR = NOISE_V / 6.0

#: A trend claim needs enough evidence: this many readings spanning this long.
#: Below either bar the slope is None (unknown), never zero.
MIN_TREND_SAMPLES = 4
MIN_TREND_WINDOW_S = 1800.0          # 30 min

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
             soak_armed: bool = False) -> Dict[str, Any]:
    """Classify one claw's pack from its reading series.

    ``soak_armed`` = this device is the one a battery_soak config deliberately
    points at, i.e. its discharge is the EXPERIMENT. It never changes the
    measured facts — only ``expected``, which tells an alerting layer that this
    decline was asked for. A soak-armed pack that is charging or flat is still
    reported as charging or flat; intent cannot invent a state.

    Returns (all keys always present, unknown as None — never a stand-in):
        state             one of the module's state constants
        volts             newest reading, or None
        slope_v_per_hr    fitted trend, or None when evidence is too thin
        hours_to_cutoff   LINEAR PROJECTION off the slope, or None
        window_hr         span of the series used
        n                 usable readings
        expected          bool: soak-armed AND actually declining
        summary           one honest human sentence
    """
    pts = _clean(samples)
    out: Dict[str, Any] = {
        "state": UNKNOWN, "volts": None, "slope_v_per_hr": None,
        "hours_to_cutoff": None, "window_hr": None, "n": len(pts),
        "expected": False, "summary": "",
    }
    if not pts:
        out["summary"] = ("no battery reading — unknown, which is neither "
                          "charged nor flat")
        return out

    volts = pts[-1][1]
    out["volts"] = volts
    out["window_hr"] = round((pts[-1][0] - pts[0][0]) / 3600.0, 3)
    slope = fit_slope_v_per_hr(pts)
    out["slope_v_per_hr"] = round(slope, 5) if slope is not None else None

    # Level decides the urgent states; trend decides the rest. A pack under the
    # floor is in the knee whichever way it is drifting — being on a charger
    # does not undo where the cell currently sits.
    if volts <= cutoff_v:
        state = CRITICAL
    elif volts < floor_v:
        state = KNEE
    elif slope is None:
        state = UNKNOWN          # healthy level, but direction unobserved
    elif slope <= -TREND_V_PER_HR:
        state = DISCHARGING
    elif slope >= TREND_V_PER_HR:
        state = CHARGING
    else:
        state = FLOAT
    out["state"] = state

    # Project ONLY off a slope strong enough to be called a trend at all. A
    # -1 mV/h drift is inside the ADC's noise, and dividing by it produces a
    # confident-looking "~692 h to cutoff" that is arithmetic on noise, not a
    # forecast. A pack we call FLOAT must not also carry a countdown.
    if slope is not None and slope <= -TREND_V_PER_HR and volts > cutoff_v:
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
    bits = [f"{v:.2f} V"]
    if slope is None:
        bits.append(f"trend unknown ({c['n']} reading(s) over "
                    f"{c['window_hr'] or 0:.1f} h — too thin to fit)")
    else:
        arrow = "falling" if slope < 0 else ("rising" if slope > 0 else "flat")
        bits.append(f"{arrow} {abs(slope) * 1000:.0f} mV/h over {c['window_hr']:.1f} h")
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
    real, honest answer here; the classifier renders it as unknown)."""
    rows: List[Dict[str, Any]] = []
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
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    if max_age_s is not None and now is not None:
        rows = [r for r in rows
                if isinstance(r.get("ts"), (int, float))
                and (now - float(r["ts"])) <= max_age_s]
    return rows


def append_sample(path: str, ts: float, volts: Optional[float], *,
                  cap: int = DEFAULT_HISTORY_CAP) -> bool:
    """Append one reading if it is NEW, capping the file. Returns True when a
    row was written; False on a duplicate/unusable reading or an I/O failure.

    Deduplicated by capture timestamp: the probe re-reads the SAME tick file
    every watchdog tick (once a minute) while the capture cron only writes
    every 5 minutes, so appending blindly would stack ~5 identical readings per
    capture and let a stalled capture masquerade as a dense flat trend.

    Never raises — a probe must not die on a full disk. The caller records the
    False as a witness (honest_failure_modes #9).
    """
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return False
    if not isinstance(volts, (int, float)) or isinstance(volts, bool):
        return False
    existing = read_series(path)
    if existing and any(r.get("ts") == ts for r in existing[-8:]):
        return False
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
        return True
    except OSError:
        return False
