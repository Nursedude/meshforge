#!/usr/bin/env python3
"""SDR interference watch at moc5 — Phase 1 step 2 (the writer).

Design: `.claude/plans/sdr_interference_phase1.md` (rev 2 + §10). One run =
one JSONL row in `<data_dir>/sdr/interference.jsonl`. Analysis is
`utils.sdr_analysis` (pure); this file owns capture, locking, persistence.

Modes:
  fleet     (every 5 min) — the 3 fleet windows. Classes A/C at gain 10
            (4 x 0.5 s bursts each); class B at gain 21 (2 bursts each —
            the §10 reference showed the floor is the Airspy's own below
            gain 15, so B is blind at 10). Our near-field TX clips at 21;
            those bursts are `overload` and B judges only the quiet ones.
  adjacent  (hourly) — class D: 869–940 MHz, 1 burst per 2.4 MHz, gain 10.

A row is written for EVERY invocation — ok, partial, unknown (nothing
captured), or skipped_overlap (another run held the lock) — so the pane's
witness can tell "the timer ran and the SDR is dead" from "all quiet"
(review R9: the witness is the age of the newest row whose window status
is `ok`, never merely the newest row).

Design columns carried by other means (review #11): "sdr_run_active" is
each row's `ts` + `run_s` (the interval the Airspy streamed); a carrier's
"first seen / present in N % of runs" is derived by the pane from history.

What a row is OF: dBFS at a fixed gain (not dBm), at moc5 only, in the
frames the gates kept; blind below the noise floor. Exit 0 = a row was
written and judged something; 1 = a row was written but the analysis
crashed (`error` — systemd then records Result=failed); 2 = no row could be
written.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import numpy as np  # noqa: E402

from utils import sdr_analysis as sa  # noqa: E402
from utils import sdr_view  # noqa: E402
from utils.paths import MeshForgePaths  # noqa: E402

SCHEMA = 1
FLEET_WINDOWS = (903.625, 906.300, 910.525)
GAIN_AC = 10
GAIN_B = 21
BURSTS_AC = 4
BURSTS_B = 2
BURST_SAMPLES = int(sa.SAMPLE_RATE * 0.5)
CAPTURE_TIMEOUT_S = 5         # a 0.5 s burst takes ~1-2 s; 5 s is a dead device (review #3)
ADJ_START, ADJ_STOP, ADJ_STEP = 870.2, 940.0, 2.4
ROLLING_ROWS = 288            # 24 h of 5-min runs for class B's rolling baseline
MAX_BYTES = 20 * 1024 * 1024  # rotate to .1 past this

# Capture(center_mhz, gain) -> (raw int16 ndarray, None) | (None, reason str)
Capture = Callable[[float, int], Tuple[Optional[np.ndarray], Optional[str]]]


def data_dir() -> Path:
    return MeshForgePaths.get_data_dir() / "sdr"


def airspy_capture(center_mhz: float, gain: int, tmpdir: str = "/dev/shm") -> Tuple[Optional[np.ndarray], Optional[str]]:
    """One 0.5 s burst via airspy_rx. Never raises; a failure is a reason."""
    if not os.path.isdir(tmpdir):
        tmpdir = tempfile.gettempdir()
    fd, path = tempfile.mkstemp(prefix="aspy_", suffix=".iq", dir=tmpdir)
    os.close(fd)
    try:
        r = subprocess.run(
            ["airspy_rx", "-r", path, "-f", f"{center_mhz:.4f}", "-a", str(sa.SAMPLE_RATE),
             "-t", "2", "-p", "1", "-g", str(gain), "-n", str(BURST_SAMPLES)],
            capture_output=True, text=True, timeout=CAPTURE_TIMEOUT_S)
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [""]
            return None, f"airspy_rx rc={r.returncode}: {tail[0][:160]}"
        raw = np.fromfile(path, dtype=np.int16)
        if raw.size < 2 * sa.FFT:
            return None, f"short capture ({raw.size} int16 values)"
        return raw, None
    except subprocess.TimeoutExpired:
        return None, f"airspy_rx timed out after {CAPTURE_TIMEOUT_S}s"
    except OSError as e:
        return None, f"airspy_rx could not run: {e}"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


_STAMPED_FUNCS = ("judge_window", "run_fleet", "run_adjacent", "_window_status", "_products_mask",
                  "_line", "_skirt_of", "_label", "load_references", "set_reference")


def analysis_stamp() -> str:
    """Short hash of what DECIDES a row's numbers: the analysis module's bytes,
    plus the writer's decision functions as docstring-stripped AST, plus its
    UPPER-CASE constants. Review 3 #6: hashing the whole writer reset the pane's
    recurrence 3x in 4 h (once for a reader-only refactor); hashing the module
    alone missed a class-D maths change that lives in the writer."""
    import ast
    import hashlib
    h = hashlib.sha256()
    try:
        h.update(Path(sa.__file__).read_bytes())
    except OSError:
        h.update(b"unreadable-analysis")
    try:
        tree = ast.parse(Path(__file__).read_text())
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in _STAMPED_FUNCS:
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                        and isinstance(body[0].value.value, str):
                    body = body[1:]
                h.update(node.name.encode())
                h.update("".join(ast.dump(n) for n in body).encode())
            elif isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) and t.id.isupper() for t in node.targets):
                h.update(ast.dump(node).encode())
    except (OSError, SyntaxError):
        h.update(b"unreadable-writer")
    return h.hexdigest()[:10]


def soc_temp_c() -> Optional[float]:
    try:
        return round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()) / 1000, 1)
    except (OSError, ValueError):
        return None


# ---- aggregation (pure) --------------------------------------------------------

def _window_status(counts: Dict[str, int], majority: bool = True) -> str:
    """ok if a MAJORITY of bursts were judged (review #5: 1 of 4 once read ok
    and could confirm a carrier from 2 of 8 bursts); partial if some were;
    unknown if nothing was captured; else why none could be judged (overload
    outranks unjudgeable — a clipped front end is the more actionable fact).
    `majority=False` (class B) accepts any judged burst: B reads only the
    quiet bursts by design, and our own TX clipping the rest is expected."""
    total = sum(counts.values())
    if counts["ok"] and (not majority or counts["ok"] >= total // 2 + 1):
        return "ok"
    if counts["ok"]:
        return "partial"
    if counts["overload"] + counts["unjudgeable"] == 0:
        return "unknown"
    return "overload" if counts["overload"] >= counts["unjudgeable"] else "unjudgeable"


def judge_window(center: float, capture: Capture, previous: Optional[Dict],
                 reference_profile: Optional[np.ndarray], rolling_floors: Sequence[float]) -> Dict:
    """All bursts for one fleet window -> one row fragment. `previous` is this
    window's fragment from the last row (for class A persistence)."""
    counts = {"ok": 0, "overload": 0, "unjudgeable": 0, "failed": 0}
    reasons: List[str] = []
    oks: List[Dict] = []
    own_tx = blocker = kept = 0
    for _ in range(BURSTS_AC):
        raw, why = capture(center, GAIN_AC)
        if raw is None:
            counts["failed"] += 1
            reasons.append(why or "unknown capture failure")
            continue
        r = sa.analyse_window(raw, center, spur_mhz=sa.SPUR_MAP_MHZ.get((center, GAIN_AC), ()),
                              ref_floor_dbfs=(previous or {}).get("floor_dbfs"))
        counts[r["status"]] += 1
        own_tx += r["own_tx_frames"]
        blocker += r["blocker_frames"]
        kept += r["kept_frames"]
        if r["status"] == "ok":
            oks.append(r)

    frag: Dict = {"status": _window_status(counts), "bursts": counts, "reasons": reasons[:4],
                  "own_tx_frames": own_tx, "blocker_frames": blocker, "kept_frames": kept,
                  "kept_frac": round(kept / (BURSTS_AC * (BURST_SAMPLES // sa.FFT)), 3)}
    if oks:
        frag["floor_dbfs"] = round(float(np.median([r["floor_dbfs"] for r in oks])), 2)
        chans: Dict[str, Dict] = {}
        for r in oks:
            for label, c in r["channels"].items():
                agg = chans.setdefault(label, {"busy_pct": [], "saturated_bursts": 0})
                agg["busy_pct"].append(c["busy_pct"])
                agg["saturated_bursts"] += int(c["saturated_in_sample"])
        frag["channels"] = {k: {"busy_pct": round(float(np.mean(v["busy_pct"])), 2),
                                "saturated_bursts": v["saturated_bursts"]} for k, v in chans.items()}
        # A carrier must be in a MAJORITY of ALL this run's bursts — not of the
        # judged ones, or 1 judged burst of 4 is its own majority (review #5) —
        # then in the previous run too (>= 2 consecutive runs) to be a finding.
        allc = [c for r in oks for c in r["carriers"]]
        need = BURSTS_AC // 2 + 1
        majority = []
        for c in allc:
            n = sum(any(abs(c["freq_mhz"] - d["freq_mhz"]) * 1000 <= 3 for d in r["carriers"]) for r in oks)
            if n >= need and not any(abs(c["freq_mhz"] - m["freq_mhz"]) * 1000 <= 3 for m in majority):
                majority.append(c)
        frag["carriers"] = majority
        prev_ok = previous if previous and previous.get("status") == "ok" else None
        frag["carriers_persistent"] = sa.persistent(majority, prev_ok.get("carriers") if prev_ok else None)
        frag["spurs_seen"] = len({s["freq_mhz"] for r in oks for s in r["spurs"]})
        foreign: Dict[float, Dict] = {}
        for r in oks:
            for f in r["foreign"]:
                cur = foreign.get(f["slice_mhz"])
                if cur is None or f["busy_pct"] > cur["busy_pct"]:
                    foreign[f["slice_mhz"]] = dict(f, bursts=1 + (cur["bursts"] if cur else 0))
                else:
                    cur["bursts"] += 1
        frag["foreign"] = sorted(foreign.values(), key=lambda f: -f["busy_pct"])

    # Class B at high gain — quiet bursts only.
    b_counts = {"ok": 0, "overload": 0, "unjudgeable": 0, "failed": 0}
    b_reasons: List[str] = []
    profiles = []
    floors = []
    for _ in range(BURSTS_B):
        raw, why = capture(center, GAIN_B)
        if raw is None:
            b_counts["failed"] += 1
            b_reasons.append(why or "unknown capture failure")
            continue
        r = sa.analyse_window(raw, center, spur_mhz=sa.SPUR_MAP_MHZ.get((center, GAIN_B), ()),
                              ref_floor_dbfs=((previous or {}).get("b") or {}).get("floor_dbfs"))
        b_counts[r["status"]] += 1
        if r["status"] == "ok":
            profiles.append(r["floor_profile"])
            floors.append(r["floor_dbfs"])
    b: Dict = {"status": _window_status(b_counts, majority=False), "bursts": b_counts,
               "gain": GAIN_B, "reasons": b_reasons[:2]}
    if floors:
        b["floor_dbfs"] = round(float(np.median(floors)), 2)
        prof = np.median(np.array(profiles), axis=0)
        b["delta_ref_db"] = (round(sa.floor_delta_db(prof, reference_profile), 2)
                             if reference_profile is not None and reference_profile.shape == prof.shape
                             else None)
        b["delta_rolling_db"] = (round(b["floor_dbfs"] - float(np.median(rolling_floors)), 2)
                                 if len(rolling_floors) >= 12 else None)
    frag["b"] = b
    return frag


def run_fleet(capture: Capture, prev_row: Optional[Dict], references: Dict[float, np.ndarray],
              history: Sequence[Dict]) -> Dict:
    windows = {}
    for c in FLEET_WINDOWS:
        key = f"{c:.3f}"
        prev = (prev_row or {}).get("windows", {}).get(key)
        rolling = [h["windows"][key]["b"]["floor_dbfs"] for h in history
                   if h.get("mode") == "fleet" and key in h.get("windows", {})
                   and h["windows"][key].get("b", {}).get("floor_dbfs") is not None]
        windows[key] = judge_window(c, capture, prev, references.get(c), rolling[-ROLLING_ROWS:])
    # Run-level ok needs A/C AND B ok in every window (review #4: B wholly
    # dead once read `ok`, satisfying the pane's witness with B blind).
    sts = [w["status"] for w in windows.values()] + [w["b"]["status"] for w in windows.values()]
    status = "ok" if all(s == "ok" for s in sts) else "unknown" if all(s == "unknown" for s in sts) else "partial"
    return {"mode": "fleet", "status": status, "gain_ac": GAIN_AC, "gain_b": GAIN_B, "windows": windows}


def _products_mask(f: np.ndarray, center: float) -> Tuple[np.ndarray, List[Tuple[float, float, str]]]:
    """Bins at a known product of OUR OWN channels in this window: IM3
    (fi+fj-fk, 2fi-fj) and sample-rate aliases of out-of-window channels."""
    prods = [(c, hw, p) for c, hw, p in sa.im3_products() + sa.alias_products(center)
             if abs(c - center) <= sa.USABLE_HALF_MHZ + hw]
    m = np.zeros(f.size, bool)
    for c, hw, _p in prods:
        m |= np.abs(f - c) <= hw
    return m, prods


def _line(f: np.ndarray, med: np.ndarray, mx: np.ndarray, floor: float) -> Tuple[Dict, Dict]:
    i, j = int(np.argmax(med)), int(np.argmax(mx))
    return ({"freq_mhz": round(float(f[i]), 4), "above_floor_db": round(float(med[i] - floor), 1)},
            {"freq_mhz": round(float(f[j]), 4), "above_floor_db": round(float(mx[j] - floor), 1),
             "level_dbfs": round(float(mx[j]), 1)})


def _skirt_of(freq: float) -> Optional[str]:
    """A line within one channel-bandwidth of a fleet channel's edge is that
    channel's SKIRT before it is anything else (review 3 #1: 907.047 MHz at
    -33 dBFS, 22 kHz past LF's guard, wore an IM3 label)."""
    for ch in sa.FLEET_CHANNELS:
        if abs(freq - ch.center_mhz) <= ch.half_mhz() + ch.bw_khz / 1000.0:
            return f"skirt of {ch.label}"
    return None


def _label(freq: float, prods: List[Tuple[float, float, str]]) -> Optional[List[str]]:
    skirt = _skirt_of(freq)
    if skirt:
        return [skirt]                 # priority: a skirt is not also an IM3 story
    return sorted({lbl for cc, hw, lbl in prods if abs(freq - cc) <= hw}) or None


def run_adjacent(capture: Capture) -> Dict:
    """Class D, FRAME-GATED like every other class (review 3 #1: it was a
    single-frame max-hold over every frame, and its live headline was our own
    LF skirt at -33 dBFS). Lines are judged over kept frames only; a window
    whose kept frames fall below the shared bar is `unjudgeable`.

    Each steady/peak line that sits at a known product position of OUR
    channels carries `tags` — LABELLED, not excluded (excluding left 0 % clean
    bins in three windows and would blind class D where it matters); a line at
    a channel's skirt is labelled `skirt of <ch>` with priority. `clean_peak` is
    the strongest line clear of every product; `product_frac` says how much of
    the window a tag can even distinguish."""
    rows = []
    n = 0
    while ADJ_START + n * ADJ_STEP <= ADJ_STOP + 1e-9:
        # Compute on the SAME rounded centre the row records: accumulating
        # 870.2 + 2.4 + … gave 913.4000000000001, one bin off the recorded
        # centre in every mask (caught by the clean_frac exactness test).
        c = round(ADJ_START + n * ADJ_STEP, 3)
        n += 1
        raw, why = capture(c, GAIN_AC)
        if raw is None:
            rows.append({"center_mhz": c, "status": "unknown", "reason": why})
            continue
        clip = float(np.mean(np.abs(raw.astype(np.int32)) >= sa.CLIP_LEVEL * sa.FULL_SCALE))
        if clip > sa.CLIP_FRAC:
            rows.append({"center_mhz": round(c, 3), "status": "overload", "clip_frac": round(clip, 5)})
            continue
        f, p = sa.spectrogram(raw, c)
        try:
            g = sa.frame_gate(f, p, c)
        except ValueError:
            rows.append({"center_mhz": round(c, 3), "status": "unknown",
                         "reason": "window is entirely fleet channels"})
            continue
        kept = g["kept"]
        base = {"center_mhz": round(c, 3), "own_tx_frames": int(g["own_tx"].sum()),
                "blocker_frames": int((g["blocker"] & ~g["own_tx"]).sum()),
                "kept_frac": round(float(kept.mean()), 3)}
        if not sa.judgeable(kept):
            rows.append(dict(base, status="unjudgeable"))
            continue
        keep = g["out_bins"]                       # usable and outside our channels (+guard)
        prod, prods = _products_mask(f, c)
        pk = p[kept]
        med = np.median(pk[:, keep], axis=0)
        floor = float(np.median(med))
        steady, peak = _line(f[keep], med, np.max(pk[:, keep], axis=0), floor)
        for line in (steady, peak):
            line["tags"] = _label(line["freq_mhz"], prods)
        clean = keep & ~prod
        clean_peak = None
        if clean.any():
            _s, clean_peak = _line(f[clean], np.median(pk[:, clean], axis=0), np.max(pk[:, clean], axis=0), floor)
        rows.append(dict(base, status="ok", floor_dbfs=round(floor, 2), steady=steady, peak=peak,
                         clean_peak=clean_peak, clean_frac=round(float(clean.sum() / keep.sum()), 3),
                         product_frac=round(float((keep & prod).sum() / keep.sum()), 3)))
    sts = [r["status"] for r in rows]
    status = "ok" if all(s == "ok" for s in sts) else "unknown" if all(s == "unknown" for s in sts) else "partial"
    return {"mode": "adjacent", "status": status, "gain": GAIN_AC, "windows": rows}


# ---- persistence ------------------------------------------------------------------

def read_rows(path: Path, limit: int = (ROLLING_ROWS + 1) * 2) -> List[Dict]:
    """Rows parsed from the newest `limit` LINES (a garbled line is skipped, so
    possibly fewer rows — review 3 #7), via utils.sdr_view.load — the ONE
    reader of this file (the pane uses it too). Two private copies of the tail
    read drifted once: the view's returned the whole file when it fit one chunk
    while this one's final trim hid the same shape (2026-09-24).

    An UNREADABLE file raises: treating it as "no history" would silently reset
    class-A persistence and class B's rolling baseline. main() turns the raise
    into an `error` row and exit 1 — the failure is witnessed, not absorbed.
    """
    # The default fetch is 2x the rolling window: rows of ALL modes (hourly
    # adjacent, skipped, error) share the file, and class B's rolling baseline
    # filters to fleet rows afterwards (review 3 #9: 289 lines held ~277).
    state, rows = sdr_view.load(path, limit)
    if state == "unreadable":
        raise OSError(f"SDR history unreadable: {path}")
    return rows          # load() already returns at most `limit` rows


def append_row(path: Path, row: Dict) -> None:
    """Append one line under an append lock (short, blocking) and rotate past MAX_BYTES."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / "append.lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            if path.exists() and path.stat().st_size > MAX_BYTES:
                os.replace(path, path.with_suffix(path.suffix + ".1"))
            # A crash mid-write leaves a line with no newline; appending straight
            # after it glued the NEXT good row onto the fragment and lost both
            # (caught by test_read_rows_skips_a_torn_line). Terminate it first.
            torn = False
            if path.exists() and path.stat().st_size:
                with open(path, "rb") as rb:
                    rb.seek(-1, os.SEEK_END)
                    torn = rb.read(1) != b"\n"
            with open(path, "a") as fh:
                fh.write(("\n" if torn else "") + json.dumps(row, separators=(",", ":")) + "\n")
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def load_references(d: Path) -> Tuple[Dict[float, np.ndarray], str]:
    """Fixed class-B references (gain-tagged), written only by --set-reference.

    Returns (references, state) with state absent | ok | unreadable — the row
    carries the state (review #11, hfm #9). ANY failure reads as unreadable:
    a half-written npz raises zipfile.BadZipFile, which the first cut did not
    catch (review #3), and an unreadable reference must never read as 0 dB.
    """
    f = d / f"reference_g{GAIN_B}.npz"
    if not f.exists():
        return {}, "absent"
    try:
        with np.load(f) as z:
            return {float(k): z[k] for k in z.files}, "ok"
    except Exception:
        return {}, "unreadable"


def set_reference(d: Path, capture: Capture) -> Dict:
    """Capture a class-B reference, MERGED into the existing one and written
    atomically (review #2: a partial run overwrote good windows, and zero
    captures wrote an empty file over a good one)."""
    refs, state = load_references(d)
    new = {}
    for c in FLEET_WINDOWS:
        profs = []
        for _ in range(8):
            raw, _why = capture(c, GAIN_B)
            if raw is None:
                continue
            r = sa.analyse_window(raw, c, spur_mhz=sa.SPUR_MAP_MHZ.get((c, GAIN_B), ()))
            if r["status"] == "ok":
                profs.append(r["floor_profile"])
        if len(profs) >= 3:
            new[c] = np.median(np.array(profs), axis=0)
    if not new:
        return {"mode": "set_reference", "status": "unknown", "prior_reference": state,
                "note": "no window produced >= 3 quiet bursts; existing reference untouched"}
    refs.update(new)
    tmp = d / f"reference_g{GAIN_B}.tmp.npz"
    np.savez(tmp, **{f"{k}": v for k, v in refs.items()})
    os.replace(tmp, d / f"reference_g{GAIN_B}.npz")
    return {"mode": "set_reference", "status": "ok" if len(new) == len(FLEET_WINDOWS) else "partial",
            "prior_reference": state, "windows_updated": sorted(new), "windows_referenced": sorted(refs)}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mode", choices=("fleet", "adjacent"), default="fleet")
    ap.add_argument("--stdout", action="store_true", help="print the row instead of appending it")
    ap.add_argument("--set-reference", action="store_true",
                    help=f"capture a fixed class-B reference at gain {GAIN_B} (run when the site is normal)")
    a = ap.parse_args(argv)
    d = data_dir()
    path = d / "interference.jsonl"
    base = {"v": SCHEMA, "ts": round(time.time(), 3), "host": socket.gethostname(),
            "mode": a.mode, "soc_temp_c": soc_temp_c(), "analysis": analysis_stamp()}
    try:
        d.mkdir(parents=True, exist_ok=True)
        lock = open(d / "run.lock", "a")
    except OSError as e:
        print(f"sdr_interference: cannot use {d}: {e}", file=sys.stderr)
        return 2
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        row = dict(base, status="skipped_overlap", note="another run held run.lock")
        return _emit(path, row, a.stdout)

    try:
        t0 = time.monotonic()
        try:
            if shutil.which("airspy_rx") is None:
                row = dict(base, status="unknown", note="airspy_rx is not installed on this box")
            elif a.set_reference:
                row = dict(base, **set_reference(d, airspy_capture))
            elif a.mode == "adjacent":
                row = dict(base, **run_adjacent(airspy_capture))
            else:
                hist = read_rows(path)
                # newest fleet row that CARRIES windows: a skipped/error row
                # must not reset class-A persistence (review #7)
                prev = next((h for h in reversed(hist)
                             if h.get("mode") == "fleet" and h.get("windows")), None)
                refs, ref_state = load_references(d)
                row = dict(base, reference=ref_state, **run_fleet(airspy_capture, prev, refs, hist))
        except Exception as e:  # the witness row survives any analysis/IO bug (review #3)
            row = dict(base, status="error", note=f"{type(e).__name__}: {str(e)[:200]}")
        row["run_s"] = round(time.monotonic() - t0, 1)
        return _emit(path, row, a.stdout)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def _emit(path: Path, row: Dict, to_stdout: bool) -> int:
    if to_stdout:
        print(json.dumps(row, indent=1, default=str))
        return 1 if row.get("status") == "error" else 0
    try:
        append_row(path, row)
    except OSError as e:
        print(f"sdr_interference: could not write {path}: {e}", file=sys.stderr)
        return 2
    print(f"{row.get('mode')} {row.get('status')} -> {path}")
    # A row was written, but an `error` row means the analysis crashed: exit 1
    # so systemd records Result=failed and the fleet's user-unit probe can see
    # it (review #5: exit 0 made a permanently broken analysis invisible).
    return 1 if row.get("status") == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
