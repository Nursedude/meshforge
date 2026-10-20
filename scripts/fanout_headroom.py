#!/usr/bin/env python3
"""fanout_headroom.py — may this box take more than one agent / heavy job now?

The preflight and the abort check for the manager-box fan-out rule
(meshforge skill, Fleet Facts). One verdict from MEASURED ratios, so the
thresholds live in ONE place instead of in prose (honest_failure_modes #5):

    GO      every ratio inside its band                      exit 0
    HOLD    over a GO line, under every ABORT line           exit 1
    UNKNOWN an input could not be read, or the slice cap is
            gone — never GO (unobservable is not healthy)    exit 2
    ABORT   over an ABORT line: stop the agents now          exit 3

Where the lines come from (measured, not chosen):
  * 2026-07-21 freeze (memory exhaustion, Ollama eval): MemAvailable 650 MB
    of 16 GB (0.04), PSI memory some avg10 7.07, load1 4.82 on 4 cores
    (1.2/core), 66.4 C -> BCM watchdog power-cycle. ABORT sits well short of
    every one of those.
  * The binding memory limit for agent work is the user slice, not the box:
    user-1000.slice MemoryMax=8G (10-memory-cap.conf, 07-24). Its
    memory.current counts reclaimable page cache (6 GB of 8.3 GB on
    2026-10-20), so headroom = cap - (anon + shmem + kernel), never
    cap - current. The same slice holds the user units (mini, lxmd, echo):
    an OOM there kills the worst process in it, which may be one of them.
  * 2026-10-20 baseline: avail 0.79, slice headroom 0.70, swap 0.17,
    load 0.14/core, PSI 0, 43.9 C -> GO.
NOT measured yet: the per-agent memory cost (agents run inside the claude
process). The first fan-out under this rule should record its own peak.

Watch during a fan-out (Monitor):
  while true; do python3 scripts/fanout_headroom.py --quiet; rc=$?
    [ $rc -ge 2 ] && echo "fanout_headroom rc=$rc"; sleep 15; done
"""
import argparse
import os
import re
import sys

MB = 1024 * 1024
# (GO-ceiling, ABORT-line) per ratio; "higher is worse" unless noted.
HOST_AVAIL_MIN = (0.50, 0.15)      # MemAvailable / MemTotal      (lower is worse)
SLICE_HEADROOM_MIN = (0.40, 0.15)  # (cap - anon-shmem-kernel)/cap (lower is worse)
SWAP_USED_MAX = (0.50, 0.85)       # swap used / swap total
LOAD_PER_CORE_MAX = (0.50, 1.10)   # load1 / cores
PSI_SOME10_MAX = (1.0, 5.0)        # memory pressure, some avg10
PSI_FULL60_MAX = 0.5               # memory pressure, full avg60 (HOLD only)
TEMP_C_MAX = (70.0, 78.0)          # SoC temperature
HEAVY_NEIGHBOURS = ("ollama",)     # may hold 8 GB of its own; 8 + 8 > 16


class Unreadable(Exception):
    pass


def _read(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError as e:
        raise Unreadable(f"{path}: {e.strerror}")


def _kv(text, sep=":"):
    out = {}
    for line in text.splitlines():
        parts = line.replace(sep, " ").split()
        if len(parts) >= 2:
            out[parts[0]] = parts[1]
    return out


def sample(root, cgroup):
    p = lambda rel: os.path.join(root, rel)
    mem = _kv(_read(p("proc/meminfo")))
    stat = _kv(_read(os.path.join(cgroup, "memory.stat")), sep=" ")
    cap_raw = _read(os.path.join(cgroup, "memory.max")).strip()
    psi = _read(p("proc/pressure/memory"))
    some10 = re.search(r"^some avg10=([\d.]+)", psi, re.M)
    full60 = re.search(r"^full avg10=[\d.]+ avg60=([\d.]+)", psi, re.M)
    if not (some10 and full60):
        raise Unreadable("proc/pressure/memory: unrecognised format")
    cores = _read(p("proc/cpuinfo")).count("processor")
    if cores < 1:
        raise Unreadable("proc/cpuinfo: no processors listed")
    procs = set()
    for name in os.listdir(p("proc")):
        if name.isdigit():
            try:
                procs.add(_read(p(f"proc/{name}/comm")).strip())
            except Unreadable:
                pass                       # a process that exited mid-scan
    try:
        return {
            "total": int(mem["MemTotal"]) * 1024, "avail": int(mem["MemAvailable"]) * 1024,
            "swap_total": int(mem["SwapTotal"]) * 1024, "swap_free": int(mem["SwapFree"]) * 1024,
            "load1": float(_read(p("proc/loadavg")).split()[0]), "cores": cores,
            "psi_some10": float(some10.group(1)), "psi_full60": float(full60.group(1)),
            "temp_c": int(_read(p("sys/class/thermal/thermal_zone0/temp")).strip()) / 1000.0,
            "cap": None if cap_raw == "max" else int(cap_raw),
            "slice_used": sum(int(stat[k]) for k in ("anon", "shmem", "kernel")),
            "procs": procs,
        }
    except (KeyError, ValueError, IndexError) as e:
        raise Unreadable(f"unparseable input: {e!r}")


def judge(s):
    """-> (verdict, reasons, ratios)."""
    if s["cap"] is None:
        return "UNKNOWN", ["user slice memory cap is 'max' — the blast-radius cap is gone"], {}
    r = {
        "MemAvailable/total": s["avail"] / s["total"],
        "user slice headroom/cap": (s["cap"] - s["slice_used"]) / s["cap"],
        "swap used": (1 - s["swap_free"] / s["swap_total"]) if s["swap_total"] else 0.0,
        "load1/core": s["load1"] / s["cores"],
        "memory pressure some10": s["psi_some10"],
        "temp C": s["temp_c"],
    }
    abort, hold = [], []
    def low(name, bands):
        if r[name] < bands[1]: abort.append(f"{name} {r[name]:.2f} < {bands[1]}")
        elif r[name] < bands[0]: hold.append(f"{name} {r[name]:.2f} < {bands[0]}")
    def high(name, bands):
        if r[name] >= bands[1]: abort.append(f"{name} {r[name]:.2f} >= {bands[1]}")
        elif r[name] > bands[0]: hold.append(f"{name} {r[name]:.2f} > {bands[0]}")
    low("MemAvailable/total", HOST_AVAIL_MIN)
    low("user slice headroom/cap", SLICE_HEADROOM_MIN)
    high("swap used", SWAP_USED_MAX)
    high("load1/core", LOAD_PER_CORE_MAX)
    high("memory pressure some10", PSI_SOME10_MAX)
    high("temp C", TEMP_C_MAX)
    if s["psi_full60"] > PSI_FULL60_MAX:
        hold.append(f"memory pressure full60 {s['psi_full60']:.2f} > {PSI_FULL60_MAX}")
    for n in HEAVY_NEIGHBOURS:
        if n in s["procs"]:
            hold.append(f"{n} is running (may hold 8 GB of its own)")
    if abort:
        return "ABORT", abort + hold, r
    return ("HOLD", hold, r) if hold else ("GO", [], r)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default="/")
    ap.add_argument("--cgroup", default=f"/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice")
    ap.add_argument("--quiet", action="store_true", help="print only when not GO")
    a = ap.parse_args(argv)
    try:
        verdict, reasons, ratios = judge(sample(a.root, a.cgroup))
    except (Unreadable, OSError) as e:
        verdict, reasons, ratios = "UNKNOWN", [str(e)], {}
    if not (a.quiet and verdict == "GO"):
        print(f"{verdict}" + (" — " + "; ".join(reasons) if reasons else ""))
        for k, v in ratios.items():
            print(f"  {k}: {v:.2f}")
    return {"GO": 0, "HOLD": 1, "UNKNOWN": 2, "ABORT": 3}[verdict]


if __name__ == "__main__":
    sys.exit(main())
