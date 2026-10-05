"""SDR interference watch — the read-only VIEW (Phase 1 step 4).

Pure: rows in, text out. Reads the JSONL that ``scripts/sdr_interference.py``
writes (one row per timer run) and renders what the Airspy saw, with the
honesty contract from the design (`.claude/plans/sdr_interference_phase1.md`):

* The witness is PER WINDOW: the age of the newest row whose window status is
  ``ok`` — never merely the newest row. A timer whose every capture fails
  writes fresh ``unknown`` rows forever (review R9); this reads STALE.
* No data is UNKNOWN or absent, never "no interference".
* The blind spots print every time: dBFS not dBm, one receiver at one site,
  nothing below the noise floor, not foreign LoRa ON our exact channel, and
  frames holding our own near-field TX are not judged.
* A carrier / foreign slice is shown with its RECURRENCE ("seen in N of the
  last M runs, first seen …") — the design leaves that to the pane.

⚠️ READ-ONLY. Never starts a capture: the Airspy belongs to the timer.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from utils.paths import MeshForgePaths

CADENCE_S = 300                 # meshforge-sdr.timer
STALE_AFTER_S = 3 * CADENCE_S   # witness: older than this = STALE
HISTORY_ROWS = 288              # 24 h of 5-min runs for recurrence
UNKNOWN_STREAK_ALERT = 3        # consecutive unknown fleet rows -> remediation
FLEET_WINDOWS = ("903.625", "906.300", "910.525")

BLIND_SPOTS = (
    "Blind spots: levels are dBFS at a fixed gain, not dBm · one receiver at "
    "one position · nothing below the noise floor (LoRa decodes ~20 dB under it) · "
    "foreign LoRa ON our exact channel looks like ours · frames holding our own "
    "near-field TX are not judged.")


#: Home-relative, so the fleet rollup's ssh `tail` (which lands in the remote
#: $HOME) reads the same file as jsonl_path() — pinned equal by a test.
SDR_JSONL_RELPATH = ".local/share/meshforge/sdr/interference.jsonl"


def jsonl_path() -> Path:
    """Must equal the writer's data_dir()/interference.jsonl (pinned by a test)."""
    return MeshForgePaths.get_data_dir() / "sdr" / "interference.jsonl"


def _tail(path: Path, want: int) -> List[bytes]:
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        buf = b""
        while pos > 0 and buf.count(b"\n") <= want:
            step = min(65536, pos)
            pos -= step
            fh.seek(pos)
            buf = fh.read(step) + buf
    # Always the LAST `want` lines — returning the whole file when it fit in
    # one chunk handed back more rows than asked (caught by its own test; the
    # writer's twin _tail_lines has the same shape, masked there by a final
    # out[-limit:] trim).
    return buf.splitlines()[-want:] if want > 0 else []


def load(path: Optional[Path] = None, limit: int = HISTORY_ROWS * 2) -> Tuple[str, List[Dict]]:
    """(state, rows parsed from the newest `limit` LINES — garbled lines are
    skipped, so possibly fewer rows). state: ok | absent | unreadable.
    Reaches into `.1`. Never raises OSError: even `exists()` can raise on an
    unreadable parent directory (review 3 #8, Python 3.13)."""
    path = path or jsonl_path()
    try:
        if not path.exists():
            return "absent", []
        lines = _tail(path, limit)
        rotated = path.with_suffix(path.suffix + ".1")
        if len(lines) < limit and rotated.exists():
            lines = _tail(rotated, limit - len(lines)) + lines
    except OSError:
        return "unreadable", []
    rows = []
    for ln in lines:
        try:
            rows.append(json.loads(ln))
        except (ValueError, UnicodeDecodeError):
            continue
    return "ok", rows


TOP_D = 3                      # class-D headline lines shown


def _d_label(tags: Optional[Sequence[str]], w: Dict) -> str:
    """The tag, honest about how much it can say. Full first label + a count
    (review 3 #5: [:50] cut labels mid-word); a skirt is plain; a product
    candidate carries how much of its window is product positions (review 3
    #2: in 3 windows every line is tagged and the tag says nothing)."""
    if not tags:
        return ""
    first = tags[0] + (f" (+{len(tags) - 1} more)" if len(tags) > 1 else "")
    if first.startswith("skirt of"):
        return f"  ← {first}"
    frac = w.get("product_frac")
    if frac is not None and frac >= 0.999:
        return f"  ← {first} — label uninformative: this whole window is product positions"
    share = f"; {100 * frac:.0f}% of this window is product positions" if frac is not None else ""
    return f"  ← at a product position of OUR channels (candidate{share}): {first}"


def _age(now: float, ts: Optional[float]) -> str:
    if ts is None:
        return "never"
    d = now - ts
    if d < 0:
        return "in the FUTURE (clock stepped?)"
    if d < 120:
        return f"{int(d)} s ago"
    if d < 7200:
        return f"{int(d // 60)} min ago"
    return f"{d / 3600:.1f} h ago"


def _recurrence(fleet: Sequence[Dict], window: str, key: str, freq_key: str,
                freq: float, tol_mhz: float) -> Tuple[int, int, Optional[float]]:
    """(runs where a matching entry appeared, runs where the window was ok, first-seen ts)."""
    seen = ok = 0
    first = None
    for r in fleet:
        w = r.get("windows", {}).get(window)
        if not w or w.get("status") != "ok":
            continue
        ok += 1
        if any(abs(e.get(freq_key, 1e9) - freq) <= tol_mhz for e in w.get(key, [])):
            seen += 1
            if first is None:
                first = r.get("ts")
    return seen, ok, first


def render(state: str, rows: Sequence[Dict], now: Optional[float] = None) -> str:
    now = time.time() if now is None else now
    out = ["SDR Interference Watch — what the Airspy timer recorded (read-only)", ""]
    if state == "absent":
        out += ["No SDR data file on this box — absent.",
                "The watch runs only on a box hosting an Airspy. If THIS box",
                "hosts one, the timer is not running: check",
                "  systemctl --user status meshforge-sdr.timer", "", BLIND_SPOTS]
        return "\n".join(out)
    if state == "unreadable" or not rows:
        out += [f"UNKNOWN — the SDR data file could not be read ({state if state != 'ok' else 'no parseable rows'}).",
                "UNKNOWN is not a pass: nothing below is a claim about the band.", "", BLIND_SPOTS]
        return "\n".join(out)

    fleet_all = [r for r in rows if r.get("mode") == "fleet"][-HISTORY_ROWS:]
    # Recurrence counts only rows from the SAME analysis code as the newest
    # row (the maths changed mid-history; older rows carry no stamp).
    stamp = next((r.get("analysis") for r in reversed(fleet_all) if r.get("windows")), None)
    fleet = [r for r in fleet_all if r.get("analysis") == stamp] if stamp else fleet_all
    excluded = len(fleet_all) - len(fleet)
    newest = rows[-1]
    out.append(f"Newest row: {_age(now, newest.get('ts'))} · {newest.get('mode')} {newest.get('status', '?').upper()}"
               + (f" · {newest['note']}" if newest.get("note") else ""))

    # Unknown streak -> remediation (in-app, never an auto-action)
    streak = 0
    for r in reversed(fleet):
        if r.get("status") in ("unknown", "error"):
            streak += 1
        else:
            break
    if streak >= UNKNOWN_STREAK_ALERT:
        reason = next((w["reasons"][0] for w in fleet[-1].get("windows", {}).values() if w.get("reasons")),
                      fleet[-1].get("note", "no reason recorded"))
        out += [f"⚠ The last {streak} fleet runs captured NOTHING: {reason}",
                "  Check: `airspy_info` on this box. \"not found\" → reseat the Airspy's USB.",
                "  Never reset the USB hub: the LoRa radio shares it."]

    # Per-window witness: age of the newest row whose WINDOW was ok
    out += ["", "Witness — newest OK capture per window (stale after 15 min = UNKNOWN):"]
    last_ok: Dict[str, Optional[Dict]] = {}
    for win in FLEET_WINDOWS:
        r = next((r for r in reversed(fleet) if r.get("windows", {}).get(win, {}).get("status") == "ok"), None)
        last_ok[win] = r
        ts = r.get("ts") if r else None
        stale = ts is None or now - ts > STALE_AFTER_S or now - ts < 0
        out.append(f"  {win} MHz  {_age(now, ts):<18} {'STALE — UNKNOWN' if stale else 'fresh'}")

    latest = fleet[-1] if fleet else None
    if latest and latest.get("windows"):
        out += ["", f"Latest fleet run ({_age(now, latest.get('ts'))}, gain {latest.get('gain_ac')} / B at "
                f"{latest.get('gain_b')}; reference {latest.get('reference', '?')}):"]
        for win in FLEET_WINDOWS:
            w = latest["windows"].get(win)
            if not w:
                continue
            head = f"  {win}  {w.get('status', '?').upper()}"
            if w.get("floor_dbfs") is not None:
                head += f"  floor {w['floor_dbfs']:.1f} dBFS  judged {100 * w.get('kept_frac', 0):.0f}% of frames"
            out.append(head)
            if w.get("status") != "ok":
                b = w.get("bursts", {})
                out.append(f"      bursts: {b}  " + ("; ".join(w.get("reasons", [])[:1])))
            for label, c in (w.get("channels") or {}).items():
                out.append(f"      {label:<20} busy {c['busy_pct']:.1f}% (our traffic seen above the floor)")
            pers = w.get("carriers_persistent") or []
            for c in pers:
                n, m, first = _recurrence(fleet, win, "carriers", "freq_mhz", c["freq_mhz"], 0.003)
                out.append(f"      A carrier {c['freq_mhz']:.4f} MHz +{c['above_floor_db']:.0f} dB — "
                           f"seen in {n} of {m} ok runs, first {_age(now, first)}")
            if w.get("status") == "ok" and not pers:
                new = len(w.get("carriers") or [])
                out.append("      A: no persistent carrier seen" + (f" ({new} new this run, unconfirmed)" if new else ""))
            for f in (w.get("foreign") or [])[:4]:
                n, m, first = _recurrence(fleet, win, "foreign", "slice_mhz", f["slice_mhz"], 0.0625)
                tag = (" (alias of our own channel — receiver product)" if f.get("alias_candidate")
                       else " (our own IM3 candidate)" if f.get("im3_candidate") else "")
                out.append(f"      C foreign {f['slice_mhz']:.3f} MHz busy {f['busy_pct']:.0f}% +{f['peak_above_floor_db']:.0f} dB"
                           f"{tag} — seen in {n} of {m} ok runs, first {_age(now, first)}")
            if w.get("status") == "ok" and not w.get("foreign"):
                out.append("      C: no foreign energy seen above the floor")
            b = w.get("b") or {}
            if b.get("status") == "ok":
                dref = "no reference" if b.get("delta_ref_db") is None else f"{b['delta_ref_db']:+.1f} dB"
                # "rolling", not "24 h": the writer's baseline switches on at 12 rows
                # (1 h) and only reaches 24 h after a day — the first render said
                # "vs 24 h" over an hour of history.
                droll = "< 1 h of history" if b.get("delta_rolling_db") is None else f"{b['delta_rolling_db']:+.1f} dB"
                out.append(f"      B floor (gain {b.get('gain')}) {b.get('floor_dbfs'):.1f} dBFS · vs reference {dref} · vs rolling (1–24 h) {droll}")
            else:
                out.append(f"      B: {str(b.get('status', 'unknown')).upper()} — raised-floor check not judged this run")

    if excluded:
        out.append(f"  (recurrence counts use {len(fleet)} rows from the current analysis code; "
                   f"{excluded} older-code rows excluded)")
    adj = next((r for r in reversed(rows) if r.get("mode") == "adjacent"), None)
    out.append("")
    if adj and adj.get("windows"):
        oks = [w for w in adj["windows"] if w.get("status") == "ok"]
        gated = [w for w in adj["windows"] if w.get("status") == "unjudgeable"]
        top = sorted(oks, key=lambda w: -w["peak"]["above_floor_db"])[:TOP_D]
        out.append(f"Class D — strongest OUT-of-our-channels signals 869–940 MHz ({_age(now, adj.get('ts'))}, "
                   f"{len(oks)}/{len(adj['windows'])} windows judged"
                   + (f", {len(gated)} unjudgeable — our own TX filled them" if gated else "") + "):")
        for w in top:
            pk = w["peak"]
            out.append(f"      {pk['freq_mhz']:.3f} MHz  +{pk['above_floor_db']:.0f} dB "
                       f"({pk['level_dbfs']:.0f} dBFS){_d_label(pk.get('tags'), w)}")
        cleans = [w for w in oks if w.get("clean_peak")]
        if cleans:
            cw = max(cleans, key=lambda w: w["clean_peak"]["above_floor_db"])
            cp = cw["clean_peak"]
            out.append(f"      strongest line clear of every product position: {cp['freq_mhz']:.3f} MHz "
                       f"+{cp['above_floor_db']:.0f} dB ({cp['level_dbfs']:.0f} dBFS)")
        out.append("      A filter helps OUT-of-band trouble only; in-band noise is never filterable.")
    else:
        out.append("Class D (adjacent band): no hourly pass recorded yet — UNKNOWN.")
    out += ["", BLIND_SPOTS]
    return "\n".join(out)


# ── fleet line (2026-10-05) ────────────────────────────────────────────────
# One compact summary per box for the Fleet Watchers rollup. Same witness as
# render(): freshness is the newest OK window, never the newest row. An SDR on
# USB that nothing reads is BLINDNESS — it gets a line, never silence (moc1's
# RTL-SDR sat unread from 10-03 with no surface saying so).

#: vendor:product → name. The bus is read from sysfs, never lsusb.
SDR_USB_IDS = {
    ("1d50", "60a1"): "Airspy",
    ("0bda", "2838"): "RTL-SDR",
    ("0bda", "2832"): "RTL-SDR",
    ("1d50", "6089"): "HackRF",
}
#: The only device scripts/sdr_interference.py captures from (airspy_rx).
WRITER_DEVICE = "Airspy"
#: Rows the rollup tails per box: 14 x 5 min covers the hourly adjacent pass.
SUMMARY_ROWS = 14
ADJACENT_STALE_S = 3 * 3600


def sdr_devices(pairs) -> Optional[List[str]]:
    """Known SDR names among (vendor, product) pairs. None = the bus could not
    be read — unobservable is not 'no SDR'."""
    if pairs is None:
        return None
    return sorted({SDR_USB_IDS[p] for p in pairs if p in SDR_USB_IDS})


def local_usb_pairs() -> Optional[List[Tuple[str, str]]]:
    """(vendor, product) for every device on THIS box's bus; None if unreadable."""
    root = Path("/sys/bus/usb/devices")
    try:
        devs = list(root.iterdir())
    except OSError:
        return None
    pairs = []
    for dev in devs:
        try:
            pairs.append(((dev / "idVendor").read_text().strip(),
                          (dev / "idProduct").read_text().strip()))
        except OSError:
            continue
    return pairs or None


def summarize(state: str, rows: Sequence[Dict], usb: Optional[List[str]],
              now: Optional[float] = None) -> Optional[Dict]:
    """Compact per-box SDR summary, or None when the box has neither data nor
    an SDR (absent by design stays silent). status: fresh | stale | unknown |
    no_consumer."""
    now = time.time() if now is None else now
    if state == "absent":
        if usb:
            return {"status": "no_consumer", "devices": list(usb)}
        return None
    device = WRITER_DEVICE
    unread = [d for d in (usb or []) if d != WRITER_DEVICE]
    base = {"device": device, "unread": unread, "usb_missing": usb == []}
    if state != "ok" or not rows:
        return dict(base, status="unknown", reason=state if state != "ok" else "no parseable rows")
    fleet = [r for r in rows if r.get("mode") == "fleet"]
    newest_ok: Dict[str, Optional[float]] = {}
    for win in FLEET_WINDOWS:
        r = next((r for r in reversed(fleet)
                  if r.get("windows", {}).get(win, {}).get("status") == "ok"), None)
        newest_ok[win] = r.get("ts") if r else None
    stale = [w for w, ts in newest_ok.items()
             if ts is None or now - ts > STALE_AFTER_S or now - ts < 0]
    streak = 0
    for r in reversed(fleet):
        if r.get("status") in ("unknown", "error"):
            streak += 1
        else:
            break
    known = [ts for ts in newest_ok.values() if ts is not None]
    # Busy is the MEAN over every tailed run with that window ok: one run is
    # a ~30 s capture, so a lone 0.0% read as "idle" while 23 h averaged 5.2%
    # on LF (2026-10-05). Energy detection misses LoRa below the floor, so the
    # line labels it a lower bound.
    samples: Dict[str, List[float]] = {}
    runs = 0
    if not stale:
        for r in fleet:
            if r.get("status") in ("unknown", "error") or not r.get("windows"):
                continue
            runs += 1
            for w in r["windows"].values():
                if w.get("status") != "ok":
                    continue
                for label, c in (w.get("channels") or {}).items():
                    samples.setdefault(label.replace("meshtastic-", ""), []).append(
                        c.get("busy_pct") or 0.0)
    busy = {k: sum(v) / len(v) for k, v in samples.items()}
    foreign = carriers = 0
    latest = next((r for r in reversed(fleet) if r.get("windows")), None)
    if latest and not stale:
        for w in latest["windows"].values():
            if w.get("status") != "ok":
                continue
            foreign += sum(1 for f in w.get("foreign") or []
                           if not f.get("alias_candidate") and not f.get("im3_candidate"))
            carriers += len(w.get("carriers_persistent") or [])
    adj = next((r for r in reversed(rows) if r.get("mode") == "adjacent" and r.get("windows")), None)
    return dict(base, status="stale" if stale else "fresh", stale_windows=stale,
                newest_ok_ts=max(known) if known else None, unknown_streak=streak,
                busy=busy, busy_runs=runs, foreign=foreign, carriers=carriers,
                adjacent_ts=adj.get("ts") if adj else None, now=now)


def summary_line(s: Dict) -> str:
    """One line under the box in the Fleet Watchers pane."""
    if s["status"] == "no_consumer":
        return (f"📡 SDR {', '.join(s['devices'])}: ⚠️ on USB, nothing reads it — "
                "blind (no capture timer on this box)")
    head = f"📡 SDR {s['device']}: "
    tail = []
    if s.get("unread"):
        tail.append(f"⚠️ {', '.join(s['unread'])} on USB, nothing reads it")
    if s.get("usb_missing"):
        tail.append("⚠️ no SDR on USB now")
    now = s.get("now")
    if s["status"] == "unknown":
        body = f"⚪ UNKNOWN — data file {s.get('reason')}"
    elif s["status"] == "stale":
        body = (f"🔴 STALE — newest ok capture {_age(now, s['newest_ok_ts'])} "
                f"({', '.join(s['stale_windows'])} stale)")
        if s.get("unknown_streak", 0) >= UNKNOWN_STREAK_ALERT:
            body += f" · last {s['unknown_streak']} runs captured nothing"
        body += " · check: systemctl --user status meshforge-sdr.timer"
    else:
        busy = " ".join(f"{k} {v:.1f}%" for k, v in s["busy"].items())
        body = (f"🟢 fresh · newest ok {_age(now, s['newest_ok_ts'])} · "
                f"busy ≥ (mean of {s.get('busy_runs', 0)} runs) {busy} · "
                f"foreign {s['foreign']} · carriers {s['carriers']}")
        adj_ts = s.get("adjacent_ts")
        if adj_ts is None:
            body += " · adjacent UNKNOWN"
        else:
            body += f" · adjacent {_age(now, adj_ts)}"
            if now - adj_ts > ADJACENT_STALE_S:
                body += " (STALE)"
    return head + " · ".join([body] + tail)
