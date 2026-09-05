#!/usr/bin/env python3
"""battery_discharge_track — durable, honest battery-discharge tracking.

Born 2026-09-05, replacing an analyzer that reported a flat "100.0% after
1910.2h" for 79 days. Four things went wrong there, and each is a rule here:

  1. It measured SoC%. Meshtastic's `batteryLevel` is 1/16-quantized: one
     6.25% step is ~3h on a 4000mAh pack, so it cannot draw a curve. This
     tracks VOLTAGE (mV resolution) and keeps SoC% only as context.
  2. Its span came from a start marker nobody disarmed, so `now - t0` grew
     forever while the data behind it covered 2 days. Here a run REFUSES to
     report when its t0 predates the oldest sample available (see
     `--report`), and `--start` stamps a fresh run.
  3. It had no upper bound: `drop < STEP` printed AWAITING-RESOLUTION
     indefinitely. Silence past a plausibility horizon is a BROKEN test, not
     patience, and says so.
  4. It inferred "plugged in" from `battery_level >= 101`, a sentinel the
     device in question had never once emitted, so that branch was dead code.
     Voltage says it directly: a pack that RISES is charging.

TRACKING (the operator's actual complaint — "the data has not been tracked
well"). node_history.db prunes at 48h, which is the length of one test, so a
completed run's evidence would age out from under it. Every `--sample` appends
to a per-run JSONL under ~/.local/share/meshforge/discharge/ that nothing
prunes. The DB is the source; the log is the record.

CONFIG (operator values stay out of the repo — MF014):
    ~/.config/meshforge/discharge_test.json
    {"node_id": "!xxxxxxxx", "capacity_mah": 4000, "label": "my node"}

USAGE
    battery_discharge_track.py --start        # stamp a new run (unplug now)
    battery_discharge_track.py --sample       # cron: append one observation
    battery_discharge_track.py --report       # verdict + curve
Exit: 0 ok/inert, 1 a real finding (broken test, or run complete), 2
unobservable (config/DB/log unreadable — never a pass).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from utils.db_helpers import connect_tuned  # noqa: E402
from utils.paths import get_real_user_home  # noqa: E402

# A pack that has not moved this long is not a slow discharge, it is a broken
# test. Deliberately generous against the ~48h expected life so a genuinely
# low-draw configuration is not called broken.
IMPLAUSIBLE_FLAT_H = 12.0
# Voltage rise that means "charging", not measurement noise. ADC jitter on
# these devices is a few mV; 20mV is comfortably above it.
CHARGING_RISE_V = 0.020
# A LiPo at or below this is effectively empty.
CUTOFF_V = 3.30


def _home() -> str:
    return str(get_real_user_home())


def _cfg_path() -> str:
    return os.environ.get("MESHFORGE_DISCHARGE_CONFIG") or os.path.join(
        _home(), ".config", "meshforge", "discharge_test.json")


def _run_dir() -> str:
    return os.environ.get("MESHFORGE_DISCHARGE_DIR") or os.path.join(
        _home(), ".local", "share", "meshforge", "discharge")


def _db_path() -> str:
    return os.environ.get("MESHFORGE_NODE_HISTORY_DB") or os.path.join(
        _home(), ".local", "share", "meshforge", "node_history.db")


def _current_run_path() -> str:
    return os.path.join(_run_dir(), "current_run.json")


def load_config() -> Tuple[Optional[dict], Optional[str]]:
    """(config, error). Absent config is a LOUD refusal, never an empty check
    that would look identical to a healthy one."""
    p = _cfg_path()
    try:
        with open(p, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except FileNotFoundError:
        return None, (f"no config at {p} — refusing to run (a tracker with no "
                      f"node would report nothing about a battery it never "
                      f"looked at)")
    except (ValueError, OSError) as exc:
        return None, f"config unreadable: {type(exc).__name__}: {exc}"
    if not cfg.get("node_id"):
        return None, "config needs node_id"
    return cfg, None


def latest_observation(node_id: str, db_path: str
                       ) -> Tuple[str, Optional[Dict[str, Any]]]:
    """(status, {ts, battery, voltage}) — newest row carrying a VOLTAGE.

    Rows with a NULL voltage are skipped rather than treated as 0: absent is
    not zero (node_history._clean_voltage already rejects the 0.0 'no pack
    measured' sentinel that USB nodes send).
    """
    try:
        # MF013: connect_tuned, not bare sqlite3.connect — it supports uri=
        # so the read-only open is preserved. Read-only matters here: this
        # tracker must never be able to modify the observation history it is
        # measuring (a checker must not mutate the artifact it validates).
        con = connect_tuned(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return f"unobservable: cannot open node history ({exc})", None
    try:
        row = con.execute(
            "SELECT timestamp, battery, voltage FROM node_observations "
            "WHERE node_id = ? AND voltage IS NOT NULL "
            "ORDER BY timestamp DESC LIMIT 1", (node_id,)).fetchone()
    except sqlite3.Error as exc:
        return f"unobservable: query failed ({exc})", None
    finally:
        con.close()
    if not row:
        return ("no voltage yet for this node — it may not be reporting "
                "telemetry, or the column predates its last report"), None
    return "ok", {"ts": float(row[0]), "battery": row[1],
                  "voltage": float(row[2])}


def append_sample(run_id: str, sample: Dict[str, Any]) -> Optional[str]:
    """Append one line to the run's durable log. Returns an error string.

    A sampler whose writes fail silently produces a log that looks merely
    quiet — the same shape as the flat curve this tool exists to prevent.
    """
    try:
        os.makedirs(_run_dir(), exist_ok=True)
        with open(os.path.join(_run_dir(), f"{run_id}.jsonl"), "a",
                  encoding="utf-8") as fh:
            fh.write(json.dumps(sample) + "\n")
        return None
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"


def read_run_log(run_id: str) -> Tuple[str, List[Dict[str, Any]]]:
    path = os.path.join(_run_dir(), f"{run_id}.jsonl")
    out: List[Dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue          # one bad line never voids the run
    except FileNotFoundError:
        return "no samples recorded yet", []
    except OSError as exc:
        return f"unobservable: log unreadable ({exc})", []
    return "ok", out


def analyse(samples: List[Dict[str, Any]], t0: float, capacity_mah: float,
            now: float) -> Tuple[str, str]:
    """(verdict, detail). Pure — testable without a fleet or a battery.

    verdicts: running | complete | charging | broken | awaiting | unobservable
    """
    volts = [s for s in samples
             if isinstance(s.get("voltage"), (int, float))
             and s.get("ts") is not None]
    if not volts:
        elapsed = (now - t0) / 3600.0
        if elapsed > IMPLAUSIBLE_FLAT_H:
            return ("broken",
                    f"no voltage samples in {elapsed:.1f}h — the node is not "
                    f"reporting telemetry this tracker can see. Not a slow "
                    f"discharge; nothing is being measured.")
        return ("awaiting",
                f"no voltage samples yet ({elapsed:.1f}h in) — waiting for the "
                f"first telemetry report.")

    first, last = volts[0], volts[-1]
    v0, v1 = float(first["voltage"]), float(last["voltage"])
    span_h = (float(last["ts"]) - float(first["ts"])) / 3600.0
    drop = v0 - v1
    curve = "  ".join(
        f"{time.strftime('%H:%MZ', time.gmtime(float(s['ts'])))} "
        f"{float(s['voltage']):.3f}V" for s in volts[-8:])
    head = (f"{len(volts)} samples over {span_h:.1f}h, "
            f"{v0:.3f}V -> {v1:.3f}V (drop {drop * 1000:.0f} mV)\ncurve: {curve}")

    if drop <= -CHARGING_RISE_V:
        return ("charging",
                f"{head}\nvoltage ROSE {abs(drop) * 1000:.0f} mV — this pack is "
                f"CHARGING or still on USB. Not a discharge run; unplug and "
                f"--start again.")
    if v1 <= CUTOFF_V:
        rate = (drop / span_h) if span_h > 0 else 0.0
        return ("complete",
                f"{head}\nreached cutoff ({v1:.3f}V <= {CUTOFF_V}V) after "
                f"{span_h:.1f}h (~{span_h / 24:.1f}d) at {rate * 1000:.1f} mV/h. "
                f"Runtime measured. Disarm the sampler.")
    # Flat. This is where the old analyzer said AWAITING forever.
    if abs(drop) < CHARGING_RISE_V:
        if span_h >= IMPLAUSIBLE_FLAT_H:
            return ("broken",
                    f"{head}\nvoltage has not moved beyond noise "
                    f"({abs(drop) * 1000:.0f} mV) in {span_h:.1f}h. Past "
                    f"{IMPLAUSIBLE_FLAT_H:.0f}h that is a BROKEN test, not "
                    f"patience: the pack is on USB, the gauge is pinned, or "
                    f"the samples are one stale value repeated.")
        return ("awaiting",
                f"{head}\nflat so far ({abs(drop) * 1000:.0f} mV in "
                f"{span_h:.1f}h) — below the {CHARGING_RISE_V * 1000:.0f} mV "
                f"noise floor. Real movement or a broken test will separate by "
                f"{IMPLAUSIBLE_FLAT_H:.0f}h.")

    rate_v_h = drop / span_h if span_h > 0 else 0.0
    to_cutoff_h = (v1 - CUTOFF_V) / rate_v_h if rate_v_h > 0 else 0.0
    detail = (f"{head}\ndischarging at {rate_v_h * 1000:.1f} mV/h; "
              f"~{to_cutoff_h:.1f}h (~{to_cutoff_h / 24:.1f}d) to {CUTOFF_V}V "
              f"at this rate, total ~{span_h + to_cutoff_h:.1f}h.")
    if capacity_mah:
        # Only honest once the pack is off the flat top of the LiPo curve;
        # say so rather than quoting a number the curve cannot support.
        detail += ("\nNOTE: mA cannot be derived from voltage alone — the "
                   "LiPo curve is non-linear and flat mid-range. Use a shunt "
                   "(INA219/INA226) for draw; this run gives RUNTIME.")
    return "running", detail


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--start", action="store_true", help="stamp a new run")
    g.add_argument("--sample", action="store_true", help="append one sample")
    g.add_argument("--report", action="store_true", help="verdict + curve")
    args = ap.parse_args(argv)

    cfg, err = load_config()
    if err:
        print(f"FATAL {err}", file=sys.stderr)
        return 2
    node_id = cfg["node_id"]
    capacity = float(cfg.get("capacity_mah") or 0)
    label = cfg.get("label") or node_id

    if args.start:
        run_id = time.strftime("run-%Y%m%d-%H%M%S", time.gmtime())
        try:
            os.makedirs(_run_dir(), exist_ok=True)
            with open(_current_run_path(), "w", encoding="utf-8") as fh:
                json.dump({"run_id": run_id, "t0": time.time(),
                           "node_id": node_id, "capacity_mah": capacity,
                           "label": label}, fh)
        except OSError as exc:
            print(f"FATAL cannot stamp run: {exc}", file=sys.stderr)
            return 2
        print(f"discharge run {run_id} started for {label} ({node_id}). "
              f"Unplug now; samples land in {_run_dir()}/{run_id}.jsonl")
        return 0

    try:
        with open(_current_run_path(), encoding="utf-8") as fh:
            run = json.load(fh)
    except FileNotFoundError:
        print("discharge: INERT — no run started (--start to begin one)")
        return 0
    except (ValueError, OSError) as exc:
        print(f"discharge: UNOBSERVABLE — run marker unreadable: {exc}",
              file=sys.stderr)
        return 2

    if args.sample:
        status, obs = latest_observation(node_id, _db_path())
        if obs is None:
            print(f"discharge sample: {status}")
            return 2 if status.startswith("unobservable") else 0
        # Only record an observation NEWER than the run's start; a stale row
        # from before the unplug is not evidence about this run.
        if obs["ts"] < float(run["t0"]) - 120:
            print(f"discharge sample: skipped — newest observation predates "
                  f"the run start")
            return 0
        # ...and newer than the last one already recorded. The sampler runs on
        # a fixed cron, but a node is only OBSERVED when it reports — the scout
        # lands a row about hourly. Appending the newest row every tick would
        # write the same observation 6x an hour, and N identical points is
        # exactly how the predecessor's flat line was manufactured: repeated
        # sampling of a source that has not updated looks like a measured
        # curve. One observation, one sample.
        log_status, existing = read_run_log(run["run_id"])
        if log_status.startswith("unobservable"):
            print(f"discharge sample: {log_status}", file=sys.stderr)
            return 2
        if existing:
            last_ts = max((float(s.get("ts", 0)) for s in existing), default=0.0)
            if obs["ts"] <= last_ts:
                print(f"discharge sample: no new observation since "
                      f"{time.strftime('%H:%M:%SZ', time.gmtime(last_ts))} "
                      f"(node reports periodically; nothing to add)")
                return 0
        werr = append_sample(run["run_id"], obs)
        if werr:
            print(f"discharge sample: WRITE FAILED ({werr}) — the run log is "
                  f"not durable; treat this run as unrecorded", file=sys.stderr)
            return 2
        print(f"discharge sample: {obs['voltage']:.3f}V "
              f"(battery={obs['battery']}) recorded to {run['run_id']}")
        return 0

    status, samples = read_run_log(run["run_id"])
    if status.startswith("unobservable"):
        print(f"discharge: {status}", file=sys.stderr)
        return 2
    verdict, detail = analyse(samples, float(run["t0"]), capacity, time.time())
    print(f"discharge [{run['run_id']}] {label}: {verdict.upper()}")
    print(detail)
    return 1 if verdict in ("broken", "complete") else 0


if __name__ == "__main__":
    sys.exit(main())
