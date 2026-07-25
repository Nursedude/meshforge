"""Watchdog probes — HOST-level resource exhaustion.

Split out as its own module 2026-07-24: ``watchdog_probes_service.py`` (the
natural sibling home, where ``probe_fd_exhaustion`` lives) sat at 1,488 lines
against the 1,500-line rule (MF025), and host-wide resource pressure is a
different subject anyway — ``fd_exhaustion`` judges ONE service against ITS
rlimit, this judges the whole box against its RAM.

Born from the 2026-07-24 manager-box hard reset (the 8th in that arc). The
forensic record was unambiguous about the *mechanism* and useless about the
*culprit*:

  - power_history.log (per-minute, persistent) showed MemAvailable falling
    9.85 GB -> 5.24 GB -> 2.56 GB over two minutes while PSI memory rose
    0.00 -> 1.04 -> 1.64;
  - ext5v held 5.03-5.08 V, temp <= 54 C, throttled=0x0 the whole way, so
    brownout and thermal were positively excluded;
  - the box then died mid-journal-line with ~2.5 GB still free and NO oom-kill
    in the log. It was not the OOM killer: ``RuntimeWatchdogUSec=1min`` was
    live, the stall starved PID 1 past its 60 s ping deadline (ollama's request
    latency went 40 us -> 41 ms across the same window), and the HARDWARE
    watchdog reset the Pi.

Nothing on the box watched for that. The fleet watches fd exhaustion (#73),
a meshtasticd VSZ leak (firmware#10468), and pending kernel updates -- but
host memory pressure, the *proven* mechanism of the manager-box hard-reset arc,
had no detector at all. Worse, it left no witness: /tmp is tmpfs (wiped by the
reset), sysstat is ENABLED="false", and the per-minute log records how MUCH
memory went but never WHO took it, so the allocator was unidentifiable after
the fact.

So this probe does both jobs. It pages while the box is still alive, and its
detail names the top RSS consumers -- a signal written to the watchdog state
(and mini's history) BEFORE the reset, which is the artifact the 07-24
post-mortem could not find.

Honest-failure-modes notes (the write-time checklist):
  - #2 absence-of-evidence: an unreadable /proc/meminfo is ``indeterminate``,
    never "memory fine". PSI absent (kernel built without it, or ``psi=0``)
    degrades to the MemAvailable leg alone and SAYS so, rather than silently
    judging on one leg as though both agreed.
  - #1 degraded-value overlap: the top-consumer enumeration is best-effort;
    if it fails the probe still fires, with the roster marked unavailable
    instead of an empty list that would read as "nothing was using memory".
  - #9 every swallow leaves a witness: every early return notes a disposition,
    so a class this probe silently stopped judging renders dark, not green.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from .watchdog_probe_core import (
    Signal,
    _load_parity_streak,
    _save_parity_streak,
    note_disposition,
)

# Fraction of MemTotal still available, below which the box is judged tight.
# The fleet spans 2 GB Pi 4s to this 16 GB Pi 5, so the gate is a RATIO, not an
# absolute — 500 MB free is comfortable on one box and terminal on another.
DEFAULT_DEGRADED_AVAIL_RATIO = 0.20
DEFAULT_WEDGE_AVAIL_RATIO = 0.08

# /proc/pressure/memory "some avg60" percentages. avg60 (not avg10) because a
# single heavy build or a chromium screenshot spikes avg10 routinely; sustained
# 60 s stall pressure is the tell that distinguishes work from a death spiral.
DEFAULT_DEGRADED_PSI_AVG60 = 10.0
DEFAULT_WEDGE_PSI_AVG60 = 40.0

DEFAULT_MEMORY_PRESSURE_DEBOUNCE_PATH = (
    "/var/lib/meshforge/host_memory_pressure_streak.json"
)

# How many consecutive ticks a DEGRADED reading must persist before firing.
# A wedge-level reading deliberately bypasses this: at <8% available the box
# may not survive another tick, and the 07-24 reset arrived 34 s after the
# first sub-20% sample. A false page there costs an ntfy line; the miss costs
# the whole box.
DEFAULT_MEMORY_PRESSURE_DEBOUNCE_TICKS = 2

# Top-N RSS consumers named in the detail. Enough to identify a runaway
# without turning a page into a process listing.
_TOP_CONSUMERS = 5


def _read_meminfo(path: str = "/proc/meminfo") -> Optional[Dict[str, int]]:
    """Parse /proc/meminfo into {key: kB}. None when unreadable.

    None means UNOBSERVABLE, not healthy — the caller must not treat it as a
    clean reading (honest_failure_modes #2).
    """
    out: Dict[str, int] = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                if not rest:
                    continue
                field = rest.strip().split()
                if not field:
                    continue
                try:
                    out[key.strip()] = int(field[0])
                except (ValueError, TypeError):
                    continue
    except OSError:
        return None
    return out or None


def _read_psi_memory_avg60(path: str = "/proc/pressure/memory") -> Optional[float]:
    """``some avg60`` from /proc/pressure/memory, or None when unavailable.

    Absent on kernels built without PSI or booted ``psi=0``. None is a real
    answer ("this leg cannot be judged here"), never 0.0 — a zeroed default
    would be a degraded state wearing a healthy-looking value.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                if not line.startswith("some "):
                    continue
                for token in line.split():
                    if token.startswith("avg60="):
                        try:
                            return float(token.split("=", 1)[1])
                        except (ValueError, TypeError):
                            return None
    except OSError:
        return None
    return None


def _top_rss_consumers(
    *, proc_root: str = "/proc", limit: int = _TOP_CONSUMERS,
) -> Optional[List[Tuple[str, int, int]]]:
    """``[(comm, pid, rss_kB), ...]`` for the biggest RSS holders.

    Reads /proc/<pid>/statm directly — no subprocess, so this works inside the
    watchdog's hardened sandbox and costs one small read per pid. Returns None
    only when /proc itself cannot be listed; individual pids that vanish
    mid-scan are skipped (a race, not a failure).

    This roster is the whole point of the probe for post-mortems: it is the
    answer to "who took the memory", recorded while the box can still write it.
    """
    page_kb = 4
    try:
        page_kb = max(1, os.sysconf("SC_PAGE_SIZE") // 1024)
    except (ValueError, OSError, AttributeError):
        pass

    try:
        entries = os.listdir(proc_root)
    except OSError:
        return None

    rows: List[Tuple[str, int, int]] = []
    for name in entries:
        if not name.isdigit():
            continue
        pid = int(name)
        try:
            with open(f"{proc_root}/{name}/statm", "r", encoding="utf-8") as fh:
                fields = fh.read().split()
            resident_pages = int(fields[1])
        except (OSError, IndexError, ValueError):
            continue  # process exited mid-scan, or unreadable — skip
        if resident_pages <= 0:
            continue
        try:
            with open(f"{proc_root}/{name}/comm", "r", encoding="utf-8") as fh:
                comm = fh.read().strip() or "?"
        except OSError:
            comm = "?"
        rows.append((comm, pid, resident_pages * page_kb))

    rows.sort(key=lambda r: r[2], reverse=True)
    return rows[:limit]


def _format_consumers(rows: Optional[List[Tuple[str, int, int]]]) -> str:
    """Render the roster, distinguishing 'unreadable' from 'nothing big'."""
    if rows is None:
        return "top consumers UNREADABLE (/proc unlistable)"
    if not rows:
        return "top consumers: none resident (unexpected — treat as unreadable)"
    return "top RSS: " + ", ".join(
        f"{comm}[{pid}] {rss // 1024}MB" for comm, pid, rss in rows
    )


def probe_host_memory_pressure(
    *,
    meminfo_path: str = "/proc/meminfo",
    psi_path: str = "/proc/pressure/memory",
    proc_root: str = "/proc",
    degraded_avail_ratio: float = DEFAULT_DEGRADED_AVAIL_RATIO,
    wedge_avail_ratio: float = DEFAULT_WEDGE_AVAIL_RATIO,
    degraded_psi_avg60: float = DEFAULT_DEGRADED_PSI_AVG60,
    wedge_psi_avg60: float = DEFAULT_WEDGE_PSI_AVG60,
    debounce_path: Optional[str] = None,
    debounce_ticks: int = DEFAULT_MEMORY_PRESSURE_DEBOUNCE_TICKS,
) -> Optional[Signal]:
    """Host RAM is running out — page BEFORE the hardware watchdog resets it.

    Two independent legs, either of which can fire (they measure different
    things and a box can die by either road):

      - AVAILABILITY: ``MemAvailable / MemTotal`` under ``degraded_avail_ratio``
        (20%), escalating to ``wedge`` under ``wedge_avail_ratio`` (8%).
      - STALL PRESSURE: ``/proc/pressure/memory`` ``some avg60`` over
        ``degraded_psi_avg60`` (10%), escalating over ``wedge_psi_avg60`` (40%).
        This is the leg that catches a box thrashing itself to death while
        MemAvailable still looks survivable — the 07-24 shape, where the reset
        came from a PID-1 stall rather than from true exhaustion.

    The worse of the two legs wins, and the detail names the top RSS consumers
    so the page identifies the runaway instead of merely announcing that one
    exists.

    Self-guards (favour silence on uncertainty, never a green on absence):
      - /proc/meminfo unreadable, or missing MemTotal/MemAvailable, or
        MemTotal <= 0 -> ``indeterminate`` + None. Unobservable != healthy.
      - PSI unavailable -> the availability leg judges alone and the detail
        says the stall leg was unobservable, so a one-legged verdict is never
        mistaken for a two-legged agreement.
      - a DEGRADED candidate must persist ``debounce_ticks`` consecutive ticks
        (a pytest run or a headless-chromium screenshot legitimately dips a
        small box below 20% for one tick). A WEDGE reading fires immediately —
        see the constant's rationale.
      - a healthy reading resets the streak; an unobservable one HOLDS it,
        so going blind mid-spiral neither pages nor forgets.
    """
    mem = _read_meminfo(meminfo_path)
    if mem is None:
        note_disposition(
            "host_memory_pressure", "indeterminate",
            reason=f"{meminfo_path} unreadable — host memory unobservable",
        )
        return None

    total_kb = mem.get("MemTotal", 0)
    avail_kb = mem.get("MemAvailable")
    if total_kb <= 0 or avail_kb is None:
        note_disposition(
            "host_memory_pressure", "indeterminate",
            reason="MemTotal/MemAvailable absent or nonsensical in meminfo",
        )
        return None

    avail_ratio = avail_kb / total_kb
    psi60 = _read_psi_memory_avg60(psi_path)

    # Worst-wins across the two legs; each leg contributes only if it can be
    # measured. psi None => that leg abstains (it does NOT vote "clean").
    severity: Optional[str] = None
    reasons: List[str] = []

    if avail_ratio < wedge_avail_ratio:
        severity = "wedge"
        reasons.append(
            f"only {avail_ratio * 100:.1f}% of RAM available "
            f"(<{wedge_avail_ratio * 100:.0f}%)"
        )
    elif avail_ratio < degraded_avail_ratio:
        severity = "degraded"
        reasons.append(
            f"{avail_ratio * 100:.1f}% of RAM available "
            f"(<{degraded_avail_ratio * 100:.0f}%)"
        )

    if psi60 is not None:
        if psi60 >= wedge_psi_avg60:
            severity = "wedge"
            reasons.append(
                f"memory stall pressure some/avg60={psi60:.1f}% "
                f"(>={wedge_psi_avg60:.0f}%)"
            )
        elif psi60 >= degraded_psi_avg60 and severity != "wedge":
            severity = severity or "degraded"
            reasons.append(
                f"memory stall pressure some/avg60={psi60:.1f}% "
                f"(>={degraded_psi_avg60:.0f}%)"
            )

    sp = debounce_path or DEFAULT_MEMORY_PRESSURE_DEBOUNCE_PATH

    if severity is None:
        note_disposition("host_memory_pressure", "clean")
        _save_parity_streak(sp, 0)
        return None

    if severity == "degraded":
        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            note_disposition(
                "host_memory_pressure", "indeterminate",
                reason=(f"pressure seen {streak}/{debounce_ticks} consecutive "
                        f"ticks — debouncing a transient spike"),
            )
            return None
    else:
        # Wedge: record the streak for continuity but never gate on it.
        _save_parity_streak(sp, _load_parity_streak(sp) + 1)

    consumers = _top_rss_consumers(proc_root=proc_root)
    psi_note = (
        f"stall pressure some/avg60={psi60:.1f}%" if psi60 is not None
        else f"stall pressure UNOBSERVABLE ({psi_path} absent) — "
             f"judged on availability alone"
    )

    detail = (
        f"Host memory pressure: {'; '.join(reasons)}. "
        f"{avail_kb // 1024}MB of {total_kb // 1024}MB available; {psi_note}. "
        f"{_format_consumers(consumers)}. "
        f"On this fleet a sustained memory stall does NOT end in an oom-kill — "
        f"it starves PID 1 past RuntimeWatchdogUSec and the HARDWARE watchdog "
        f"hard-resets the box (manager-box hard-reset arc, 2026-07-24). "
        f"Inspect: ps -eo rss,pid,comm --sort=-rss | head; "
        f"systemd-cgtop -m -n1 -b --depth=2; grep -E 'Mem|Shmem' /proc/meminfo; "
        f"df -h /tmp /dev/shm   # tmpfs is RAM and is NOT reclaimable"
    )

    return Signal(
        cls="host_memory_pressure",
        subject=os.uname().nodename,
        severity=severity,
        detail=detail,
        extra={
            "mem_total_kb": total_kb,
            "mem_available_kb": avail_kb,
            "avail_ratio": round(avail_ratio, 4),
            "psi_some_avg60": psi60,
            "top_rss": (
                None if consumers is None
                else [{"comm": c, "pid": p, "rss_kb": r} for c, p, r in consumers]
            ),
        },
    )
