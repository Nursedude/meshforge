#!/usr/bin/env python3
"""battery_soak — field power-draw soak collector (§5 item a, 2026-06-19).

Closes second-brain Open Question #1: replace the DERIVED ~120 mA central
estimate with a *measured* dude-claw average draw before sizing a solar panel
(`.claude/research/dudeclaw_second_brain_2026_06_17.md` RQ3 / step 2).

The claw's ``battery_read`` tool reports **VBAT terminal voltage**, not current
(`Battery: 3.87 V (adc 790 mV)`), so this does NOT directly measure current.
Instead it logs a timestamped VBAT series over a battery-only soak; the
``--summary`` judge derives a **measured average current by the runtime-to-cutoff
method**: once the pack crosses its cutoff voltage,

    avg_mA = usable_capacity_mAh / runtime_to_cutoff_hours

which needs only voltage (to detect the cutoff crossing) plus a known pack
capacity. e.g. a 5000 mAh LiPo (~4100 mAh usable to 3.4 V) that runs the board
34 h ⇒ ~120 mA (validates the estimate); 50 h ⇒ ~82 mA (DTIM modem-sleep is
working). **Honest by design:** before cutoff the average is NOT yet
measurable — the judge reports the voltage trend + a clearly-labelled linear
projection, never a fabricated "measured" number (honest_failure_modes #2).

Like ``host_probe_check.py`` this is the **out-of-band collector half** (the NATS
call lives here, not in the sandboxed watchdog) and **self-gates** to the claw's
brain box: it runs only where its config file exists, so it is a clean no-op
everywhere else. The soak is a *temporary experiment* (mirrors the BLE-soak
lifecycle) — the operator drops the config + a temporary cron on the brain box,
runs it on **battery power with no charge source**, reads ``--summary``, then
removes the config + cron.

Config (operator-specific values live HERE, never in repo source — MF014):

    ~/.config/meshforge/battery_soak.json
    {
      "nats": "localhost:4222",
      "claw_device": "dudeclaw-01",
      "timeout_s": 8,
      "battery": {"usable_capacity_mah": 4100, "cutoff_v": 3.4,
                  "label": "5000mAh LiPo (usable to 3.4V)"}
    }

Temporary cron on the brain box (a dead collector is caught by cron_verdict_stale):

    */10 * * * * /opt/meshforge/scripts/battery_soak.py >/dev/null 2>&1; \
        /opt/meshforge/scripts/cron_verdict.sh battery_soak $?
    # daily judge (optional, mirrors claw_ble_soak_judge):
    5 13 * * * /opt/meshforge/scripts/battery_soak.py --summary >> ~/battery_soak_verdict.txt 2>&1

Exit 0 when a sample was captured (even a *blind* sample where the claw could not
be reached — that is recorded honestly as ``vbat=null``, not a fake reading).
Non-zero only if the collector itself could not run (no/unreadable config, or it
could not append the sample).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

# Make src/ importable however we're invoked (cron may not set PYTHONPATH).
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from utils import claw_battery  # noqa: E402
from utils.paths import get_real_user_home  # noqa: E402

CONFIG_REL = os.path.join(".config", "meshforge", "battery_soak.json")
STATE_BASENAME = "battery_soak.jsonl"
DEFAULT_NATS = "localhost:4222"
DEFAULT_DEVICE = "dudeclaw-01"
DEFAULT_TIMEOUT_S = 8.0
# Pack thresholds come from utils.claw_battery — the SSOT for this chemistry.
# They used to be hardcoded here AND in watchdog_probes_liveness (3.4 / 3.5),
# two independent constants describing the same 1S LiPo, which is the drift
# class honest_failure_modes #5 exists to prevent. Names kept as aliases so
# every existing caller and test is untouched.
DEFAULT_CUTOFF_V = claw_battery.CUTOFF_V
# A pack is "discharging" only once its terminal voltage has dropped this far
# below the first sample — smaller swings are ADC noise / load ripple, not draw.
DISCHARGE_NOISE_V = claw_battery.NOISE_V

_READING_RE = re.compile(r"Battery:\s*([0-9]+(?:\.[0-9]+)?)\s*V\s*\(adc\s*([0-9]+)\s*mV\)",
                         re.IGNORECASE)


def parse_battery_reading(result_line: str):
    """Parse the claw's ``battery_read`` result string → (vbat, adc_mv, error).

    A real reading → (float, int, None). The firmware's explicit error
    ("no battery sense …") or any unparseable text → (None, None, <reason>).
    Never invents a voltage the radio did not report (honest_failure_modes #1)."""
    text = (result_line or "").strip()
    if not text:
        return None, None, "empty reply"
    m = _READING_RE.search(text)
    if m:
        try:
            return float(m.group(1)), int(m.group(2)), None
        except (TypeError, ValueError):
            return None, None, f"unparseable numbers: {text!r}"
    # Distinguish a known firmware error from genuinely unexpected text.
    if text.lower().startswith("error"):
        return None, None, text
    return None, None, f"unparseable: {text!r}"


def _slope_v_per_hr(first, last):
    """Linear V/hr between two (ts, vbat) points; None if span is zero/invalid."""
    (t0, v0), (t1, v1) = first, last
    dt_hr = (t1 - t0) / 3600.0
    if dt_hr <= 0:
        return None
    return (v1 - v0) / dt_hr


def summarize_samples(samples, usable_mah, cutoff_v=DEFAULT_CUTOFF_V,
                      stale_after_s=None):
    """Reduce a list of captured samples to an honest power verdict.

    ``samples``: list of dicts with ``ts`` (epoch) and ``vbat`` (float or None).
    Returns a dict with a ``status`` of:
      MEASURED        — pack crossed cutoff; ``avg_ma`` is a real runtime-derived
                        average current.
      IN_PROGRESS     — discharging but not yet at cutoff; trend + a *labelled*
                        linear projection only, ``avg_ma`` is None.
      NOT_DISCHARGING — voltage flat/rising ⇒ on a charge source/USB; a draw
                        average cannot be derived (soak must run battery-only).
      STALE           — newest sample older than ``stale_after_s`` ⇒ collector
                        stopped (silence is a failure for a fixed-cadence series).
      INDETERMINATE   — fewer than 2 readable samples (incl. all-blind).
    Blind samples (vbat None) are counted and surfaced, never read as healthy.
    """
    valid = [(float(s["ts"]), float(s["vbat"])) for s in samples
             if isinstance(s, dict) and s.get("vbat") is not None
             and s.get("ts") is not None]
    n_total = len([s for s in samples if isinstance(s, dict)])
    n_valid = len(valid)
    n_blind = n_total - n_valid
    out = {"status": "INDETERMINATE", "n_samples": n_total, "n_valid": n_valid,
           "n_blind": n_blind, "avg_ma": None, "summary": ""}

    if n_valid < 2:
        out["summary"] = (f"INDETERMINATE — {n_valid} readable sample(s) "
                          f"({n_blind} blind); need ≥2 for a trend.")
        return out

    valid.sort(key=lambda p: p[0])
    t0, v0 = valid[0]
    t1, v1 = valid[-1]
    vbats = [v for _, v in valid]
    duration_hr = (t1 - t0) / 3600.0
    out.update({"t_start": t0, "t_end": t1, "duration_hr": round(duration_hr, 3),
                "vbat_first": v0, "vbat_last": v1,
                "vbat_min": min(vbats), "vbat_max": max(vbats)})

    # Collector stopped? Silence is the failure mode for a fixed-cadence series.
    if stale_after_s is not None:
        age = time.time() - t1
        if age > stale_after_s:
            out["status"] = "STALE"
            out["summary"] = (f"STALE — newest sample {age / 3600.0:.1f} h old "
                              f"(> {stale_after_s / 3600.0:.1f} h); collector stopped?")
            return out

    # On a charge source the terminal voltage is flat or rising — a draw average
    # is not derivable; say so rather than emit a nonsense current.
    if v1 >= v0 - DISCHARGE_NOISE_V:
        out["status"] = "NOT_DISCHARGING"
        out["summary"] = (f"NOT_DISCHARGING — VBAT {v0:.2f}→{v1:.2f} V over "
                          f"{duration_hr:.1f} h (flat/rising). Run the soak on "
                          f"battery only, no USB/solar, to measure draw.")
        return out

    # Discharging. Did it cross the cutoff? → a real runtime-derived average.
    crossing = next((ts for ts, v in valid if v <= cutoff_v), None)
    if crossing is not None and usable_mah:
        runtime_hr = (crossing - t0) / 3600.0
        out["runtime_to_cutoff_hr"] = round(runtime_hr, 3)
        if runtime_hr > 0:
            avg_ma = float(usable_mah) / runtime_hr
            out["status"] = "MEASURED"
            out["avg_ma"] = round(avg_ma, 1)
            out["summary"] = (f"MEASURED — {usable_mah} mAh usable / {runtime_hr:.1f} h "
                              f"to {cutoff_v:.2f} V ⇒ **{avg_ma:.0f} mA** average "
                              f"({n_valid} samples, {n_blind} blind).")
            return out

    # Discharging, not yet at cutoff: trend + a clearly-labelled projection only.
    slope = _slope_v_per_hr((t0, v0), (t1, v1))  # negative V/hr
    proj = None
    if slope and slope < 0 and v1 > cutoff_v:
        proj = (v1 - cutoff_v) / (-slope)  # hours from now to cutoff (linear)
    out["status"] = "IN_PROGRESS"
    out["proj_hr_to_cutoff"] = round(proj, 1) if proj is not None else None
    proj_txt = (f"; ~{proj:.0f} h more to {cutoff_v:.2f} V (LINEAR projection, "
                f"not measured)" if proj is not None else "")
    out["summary"] = (f"IN_PROGRESS — VBAT {v0:.2f}→{v1:.2f} V over {duration_hr:.1f} h, "
                      f"not yet at cutoff{proj_txt}. Average not measurable until "
                      f"cutoff ({n_valid} samples, {n_blind} blind).")
    return out


def _load_config(home):
    config_path = os.path.join(str(home), CONFIG_REL)
    with open(config_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _capture(home, cfg):
    """Poll battery_read once and append one sample. Returns process exit code."""
    server = str(cfg.get("nats") or DEFAULT_NATS)
    device = str(cfg.get("claw_device") or DEFAULT_DEVICE)
    token = cfg.get("nats_token") or None
    timeout_s = float(cfg.get("timeout_s") or DEFAULT_TIMEOUT_S)

    from mini_dudeai.nats_client import request as _request

    vbat = adc_mv = None
    error = None
    raw = ""
    try:
        reply = _request(server, f"{device}.tool_exec",
                         json.dumps({"tool": "battery_read"}),
                         token=token, timeout_s=timeout_s)
        if isinstance(reply, (bytes, bytearray)):
            reply = reply.decode("utf-8", "replace")
        doc = json.loads(reply) if isinstance(reply, str) else reply
        if not isinstance(doc, dict):
            error = f"unexpected reply type: {type(reply).__name__}"
        else:
            raw = str(doc.get("result") or "")
            if not doc.get("ok"):
                error = str(doc.get("error") or "claw returned ok=false")
            else:
                vbat, adc_mv, error = parse_battery_reading(raw)
    except Exception as e:  # transport/parse failure → blind sample, not healthy
        error = f"{type(e).__name__}: {e}"

    sample = {"ts": time.time(), "vbat": vbat, "adc_mv": adc_mv,
              "device": device, "error": error, "raw": raw}
    state_path = os.path.join(str(home), STATE_BASENAME)
    try:
        with open(state_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(sample, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as e:
        sys.stderr.write(f"battery_soak: cannot append {state_path}: {e}\n")
        return 1

    shown = f"{vbat:.2f} V" if vbat is not None else f"blind ({error})"
    sys.stdout.write(f"battery_soak: captured {shown}\n")
    return 0


def _read_samples(home, device=None):
    """Load the sample series, optionally filtered to ONE claw.

    When ``device`` is given, only rows whose ``device`` field matches are
    returned, and the count of dropped (other-device) rows is reported as the
    second tuple element. This keeps a RETARGETED soak honest: if the config
    is repointed dudeclaw-01 → dudeclaw-02 without rotating the append-only
    ``battery_soak.jsonl``, the old claw's flat-USB samples must NOT blend into
    the new claw's discharge curve (that would corrupt vbat_first/min/crossing
    — the row-7 "one device's state read as another's" class).

    Returns ``(kept, dropped_other, dropped_legacy)``. Rows written BEFORE the
    ``device`` field existed carry no identity at all, so they are dropped too
    — but counted separately (07-24 audit): reporting them as "samples from
    other devices" sent the operator hunting for a second claw that was never
    there, when the real story is "this file predates device tagging".
    """
    state_path = os.path.join(str(home), STATE_BASENAME)
    samples = []
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    samples.append(json.loads(line))
                except ValueError:
                    continue  # torn/partial line — skip, never crash the judge
    except FileNotFoundError:
        return ([], 0, 0) if device is not None else []
    if device is None:
        return samples
    kept, other, legacy = [], 0, 0
    for s in samples:
        if not isinstance(s, dict):
            continue
        d = s.get("device")
        if d == device:
            kept.append(s)
        elif d is None:
            legacy += 1     # pre-device-field row: unlabelled, not foreign
        else:
            other += 1
    return kept, other, legacy


def _summary(home, cfg):
    batt = cfg.get("battery") or {}
    usable_mah = batt.get("usable_capacity_mah")
    cutoff_v = float(batt.get("cutoff_v") or DEFAULT_CUTOFF_V)
    label = batt.get("label") or "battery"
    device = str(cfg.get("claw_device") or DEFAULT_DEVICE)
    # Scope to the configured claw so a retarget can't blend two packs' curves.
    samples, dropped_other, dropped_legacy = _read_samples(home, device=device)
    # Stale window: 3× a nominal 10-min cadence, floor 1 h (mirrors the
    # synth-soak/#78 "silence is the failure" cadence-derived threshold).
    res = summarize_samples(samples, usable_mah, cutoff_v, stale_after_s=3600.0)
    # Name the two drop reasons apart — "from other devices" over a file that
    # simply predates device tagging is a false lead, not a diagnosis.
    notes = []
    if dropped_other:
        notes.append(f"ignored {dropped_other} sample(s) from other devices")
    if dropped_legacy:
        notes.append(f"ignored {dropped_legacy} untagged sample(s) written "
                     f"before device tagging — rotate {STATE_BASENAME} to "
                     f"start a clean series")
    other = f" ({'; '.join(notes)})" if notes else ""
    sys.stdout.write(
        f"battery_soak {res['status']}: {res['summary']} "
        f"[{device} · {label}]{other}\n")
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    home = get_real_user_home()
    try:
        cfg = _load_config(home)
    except FileNotFoundError:
        # No config here → not the claw's brain box / soak not armed. Clean no-op.
        return 0
    except (OSError, ValueError) as e:
        sys.stderr.write(f"battery_soak: unreadable config: {e}\n")
        return 1

    if "--summary" in argv or "--judge" in argv:
        return _summary(home, cfg)
    return _capture(home, cfg)


if __name__ == "__main__":
    sys.exit(main())
