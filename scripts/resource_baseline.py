#!/usr/bin/env python3
"""resource_baseline.py — 1.0 gate 8 (resource budget): RAM + SD writes per
day, per box, recorded as a baseline that rc1 is judged "no worse than".

READ-ONLY. Each capture reads /proc and /sys on every fleet box (ssh) and on
this box; nothing is installed, restarted or written remotely.

    scripts/resource_baseline.py capture --label t0
    scripts/resource_baseline.py capture --label t1          # >= 12 h later
    scripts/resource_baseline.py report  T0.jsonl T1.jsonl   # markdown table

Captures land in ~/.local/state/meshforge/resource_baseline/<date>-<label>.jsonl.

WHAT THE NUMBERS ARE OF
-----------------------
* writes = sectors written to the block device holding `/` (the SD card's
  wear), from /sys/block/<disk>/stat field 7, x 512 B. Whole-device: every
  writer counts, which is the point.
* the window is the /proc/uptime delta (monotonic), never wall clock — RTC-less
  Pis step their clocks (honest_failure_modes #6).
* RAM = MemTotal - MemAvailable at each sample instant; two instants, not a
  peak. Top RSS processes are shown so the number has an owner.

WHAT MAKES A WINDOW UNKNOWN (never a number)
--------------------------------------------
a reboot inside the window (boot_id changed: counters reset), uptime or the
counter going backward, a window under MIN_WINDOW_H, a missing counter, or an
unreachable box. A box that reads UNKNOWN is re-measured, not estimated.

Known contamination is the operator's to annotate (e.g. a drill that stopped
a service inside the window) — the instrument cannot see intent.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import socket
import subprocess
import sys
from typing import Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from utils.fleet_hosts import (  # noqa: E402
    FleetHostsUnreadable, resolve_fleet_hosts)
from utils.paths import get_real_user_home  # noqa: E402

OK = "OK"
UNKNOWN = "UNKNOWN"
SECTOR_BYTES = 512
MIN_WINDOW_H = 12.0
SSH_TIMEOUT_S = 25

#: Runs on each box. Plain key=value lines; every read is guarded so one
#: missing source yields an empty value (-> None), never a fake zero.
REMOTE_CMD = r"""
printf 'boot_id=%s\n' "$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)"
printf 'uptime_s=%s\n' "$(cut -d' ' -f1 /proc/uptime 2>/dev/null)"
printf 'model=%s\n' "$(tr -d '\0' </proc/device-tree/model 2>/dev/null)"
awk '/^(MemTotal|MemAvailable|SwapTotal|SwapFree):/{k=tolower($1); sub(":","",k); print k"="$2}' /proc/meminfo 2>/dev/null
d=$(lsblk -no PKNAME "$(findmnt -no SOURCE / 2>/dev/null)" 2>/dev/null | head -1)
printf 'root_disk=%s\n' "$d"
[ -n "$d" ] && [ -r "/sys/block/$d/stat" ] && awk '{print "sectors_written="$7}' "/sys/block/$d/stat"
ps -eo rss=,comm= --sort=-rss 2>/dev/null | head -8 | awk '{print "proc="$1" "$2}'
"""


def _num(v: Optional[str], cast=float):
    try:
        return cast(v) if v not in (None, "") else None
    except ValueError:
        return None


def parse_sample(text: str) -> Dict:
    kv: Dict[str, str] = {}
    procs: List[Dict] = []
    for line in text.splitlines():
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if k == "proc":
            rss, _, comm = v.partition(" ")
            r = _num(rss, int)
            if r is not None:
                procs.append({"rss_mb": round(r / 1024, 1), "comm": comm})
        else:
            kv[k] = v

    def used(total_k, free_k):
        t, f = _num(kv.get(total_k), int), _num(kv.get(free_k), int)
        return round((t - f) / 1024, 1) if t is not None and f is not None else None

    return {
        "boot_id": kv.get("boot_id") or None,
        "uptime_s": _num(kv.get("uptime_s")),
        "model": kv.get("model") or None,
        "mem_total_mb": (round(_num(kv.get("memtotal"), int) / 1024, 1)
                         if _num(kv.get("memtotal"), int) is not None else None),
        "mem_used_mb": used("memtotal", "memavailable"),
        "swap_used_mb": used("swaptotal", "swapfree"),
        "root_disk": kv.get("root_disk") or None,
        "sectors_written": _num(kv.get("sectors_written"), int),
        "top_rss": procs,
    }


def _unknown(reason: str) -> Dict:
    return {"status": UNKNOWN, "writes_mb_per_day": None, "window_h": None,
            "reason": reason}


def judge_window(t0: Dict, t1: Dict) -> Dict:
    if t0.get("error") or t1.get("error"):
        return _unknown("sample missing: %s" % (t0.get("error") or t1.get("error")))
    if not t0.get("boot_id") or t0.get("boot_id") != t1.get("boot_id"):
        return _unknown("reboot inside the window (boot_id changed) — counters reset")
    u0, u1 = t0.get("uptime_s"), t1.get("uptime_s")
    s0, s1 = t0.get("sectors_written"), t1.get("sectors_written")
    if u0 is None or u1 is None:
        return _unknown("uptime unreadable")
    if s0 is None or s1 is None:
        return _unknown("write counter unreadable (root disk not resolved)")
    dt = u1 - u0
    if dt <= 0:
        return _unknown("uptime went backward or did not advance")
    if s1 < s0:
        return _unknown("write counter went backward")
    window_h = round(dt / 3600, 2)
    if window_h < MIN_WINDOW_H:
        return _unknown("window too short (%.1f h < %.0f h)" % (window_h, MIN_WINDOW_H))
    mb = (s1 - s0) * SECTOR_BYTES / 1_000_000  # decimal MB, as SD endurance is rated
    return {"status": OK, "writes_mb_per_day": round(mb * 86400 / dt, 1),
            "window_h": window_h, "reason": ""}


def _run_capture(host: Optional[str]) -> Dict:
    if host is None:
        cmd = ["bash", "-c", REMOTE_CMD]
    else:
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
               host, REMOTE_CMD]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=SSH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"error": "timed out after %ss" % SSH_TIMEOUT_S}
    except OSError as e:
        return {"error": "could not run: %s" % e}
    if p.returncode != 0 and "uptime_s=" not in p.stdout:
        return {"error": "rc=%d %s" % (p.returncode, p.stderr.strip()[:120])}
    return parse_sample(p.stdout)


def _out_dir() -> str:
    return os.path.join(str(get_real_user_home()), ".local", "state",
                        "meshforge", "resource_baseline")


def cmd_capture(label: str) -> int:
    local = socket.gethostname()
    try:
        fleet = resolve_fleet_hosts()
    except FleetHostsUnreadable as e:   # r7e #2: a self-only baseline is not the fleet's
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    hosts = [h for h in fleet if h.lower() != local.lower()]
    rows = []
    now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    for name, target in [(local, None)] + [(h, h) for h in hosts]:
        s = _run_capture(target)
        s.update({"box": name, "label": label, "captured": now})
        rows.append(s)
    os.makedirs(_out_dir(), exist_ok=True)
    path = os.path.join(_out_dir(), "%s-%s.jsonl" % (_dt.date.today().isoformat(), label))
    if os.path.exists(path):
        print("refusing to overwrite %s — pick a new --label" % path, file=sys.stderr)
        return 2
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    bad = [r["box"] for r in rows if r.get("error")]
    print("captured %d box(es) -> %s" % (len(rows), path))
    if bad:
        print("UNOBSERVED: %s" % ", ".join(bad))
    return 1 if bad else 0


def _load(path: str) -> Dict[str, Dict]:
    with open(path, encoding="utf-8") as f:
        return {r["box"]: r for r in (json.loads(l) for l in f if l.strip())}


def cmd_report(p0: str, p1: str) -> int:
    a, b = _load(p0), _load(p1)
    print("| box | board | RAM used t0 → t1 (MB) | swap t1 | SD writes MB/day | window h | top RSS t1 |")
    print("|---|---|---|---|---|---|---|")
    unknown = 0
    for box in sorted(set(a) | set(b)):
        t0, t1 = a.get(box, {"error": "absent from t0"}), b.get(box, {"error": "absent from t1"})
        v = judge_window(t0, t1)
        unknown += v["status"] != OK
        top = ", ".join("%s %.0f" % (p["comm"], p["rss_mb"]) for p in t1.get("top_rss", [])[:3])
        w = v["writes_mb_per_day"] if v["status"] == OK else "UNKNOWN — " + v["reason"]
        print("| %s | %s (%s MB) | %s → %s | %s | %s | %s | %s |" % (
            box, t1.get("model") or t0.get("model") or "?", t1.get("mem_total_mb"),
            t0.get("mem_used_mb"), t1.get("mem_used_mb"), t1.get("swap_used_mb"),
            w, v["window_h"], top))
    print("\n%d box(es) UNKNOWN — re-measure, never estimate." % unknown if unknown
          else "\nall windows judged.")
    return 2 if unknown else 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture")
    c.add_argument("--label", required=True)
    r = sub.add_parser("report")
    r.add_argument("t0")
    r.add_argument("t1")
    args = ap.parse_args(argv)
    if args.cmd == "capture":
        return cmd_capture(args.label)
    return cmd_report(args.t0, args.t1)


if __name__ == "__main__":
    sys.exit(main())
