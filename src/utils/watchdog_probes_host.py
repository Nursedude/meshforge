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

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from .watchdog_probe_core import (
    Signal,
    note_disposition,
)

# Same "watchdog" namespace the runner logs under, so a swallowed state-write
# failure lands where the operator already greps (honest_failure_modes #9).
logger = logging.getLogger("watchdog")

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

# Top-N consumers named in the detail. Enough to identify a runaway without
# turning a page into a process listing.
_TOP_CONSUMERS = 5

# ── RATE leg (added 2026-07-24 after the allocator dig) ──────────────────
#
# The level legs above are the tombstone, not the warning. Measured against the
# real reset: MemAvailable was 61% at 23:43:01, 32% at 23:44:01 and 15% at
# 23:45:01, so a 20%-level gate first sees trouble at 23:45:01 and (with its
# 2-tick debounce) fires ~4 s before the box dies. The TELL was never the level
# — it was the SLOPE, ~4.6 GB/min. Judged on drop-across-a-window this fires at
# 23:44:01: ~94 s of warning instead of 4.
#
# Fires only when the drop is large AND the box has landed under a floor. The
# floor is what keeps legitimate big allocations quiet: on this box an ollama
# model load costs ~6 GB in ~43 s (measured: cgroup peak 6,122 MB) and settles
# near 47% available — a bare slope gate would page on every model load. The
# 07-24 sample was 32.3% with a 31% drop, so it clears both.
#
# Window-based, never a single delta — the claw-battery lesson (2026-07-24): a
# quantised gauge plus a fixed single-step gate invents trends that are not
# there. Requires a minimum span so one sample can never constitute a slope.
DEFAULT_RATE_WINDOW_S = 180.0
DEFAULT_RATE_MIN_SPAN_S = 45.0
DEFAULT_RATE_DROP_RATIO = 0.20      # of MemTotal, lost inside the window
DEFAULT_RATE_FLOOR_RATIO = 0.35     # only if availability landed under this
_HIST_MAX = 40                      # ~20 min at a 30 s tick; bounded on disk

# Non-RSS places memory hides on this fleet. If the allocation lands here, a
# top-RSS roster names NOTHING: tmpfs/shm is charged to no process and the OOM
# killer cannot reclaim it, and slab is kernel-side. Measured on this box right
# now: Shmem 412 MB, Slab 1150 MB. /tmp and /dev/shm are 8 GB each on a 16 GB
# board, so a runaway writing to the scratchpad can exhaust RAM while every
# process looks small.
_MEMINFO_EXTRA_KEYS = (
    "Shmem", "Slab", "SReclaimable", "SUnreclaim", "Dirty", "Writeback",
    "SwapTotal", "SwapFree", "CmaTotal", "CmaFree", "Committed_AS",
)


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
) -> Optional[List[Dict[str, Any]]]:
    """The top ``limit`` rows of ``_scan_processes`` — the current-RSS view."""
    rows = _scan_processes(proc_root=proc_root)
    return None if rows is None else rows[:limit]


def _scan_processes(
    *, proc_root: str = "/proc",
) -> Optional[List[Dict[str, Any]]]:
    """EVERY process as ``[{comm, pid, rss_kb, vmhwm_kb, cmdline, cgroup}, ...]``,
    sorted by current RSS descending.

    Returns the full set, not a top-N, because the peak (VmHWM) view has to see
    processes the RSS ranking buries — a burst allocator sitting at 40 MB now
    but with a 5 GB high-water mark would never appear in a current-RSS top-5,
    which is precisely why it went unfound on 07-24. One scan feeds both views.

    Reads /proc/<pid>/statm directly — no subprocess, so this works inside the
    watchdog's hardened sandbox and costs one small read per pid. Returns None
    only when /proc itself cannot be listed; individual pids that vanish
    mid-scan are skipped (a race, not a failure).

    ``cmdline`` and ``cgroup`` are carried because ``comm`` alone cannot
    attribute anything: on this box ``comm`` reports two separate processes as
    plain ``python``, one 651 MB. With cmdline+cgroup the same rows read as
    ``map_data_service`` in ``meshforge-map.service`` and ``claude`` in
    ``session-2.scope`` — the difference between "a python is big" and knowing
    whether a SERVICE or an interactive/agent session took the memory. That
    distinction is exactly the question the 07-24 post-mortem could not answer.
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

    rows: List[Dict[str, Any]] = []
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
        rows.append({
            "comm": _read_small(f"{proc_root}/{name}/comm") or "?",
            "pid": pid,
            "rss_kb": resident_pages * page_kb,
            "vmhwm_kb": _read_vmhwm(f"{proc_root}/{name}/status"),
            "cmdline": _read_cmdline(f"{proc_root}/{name}/cmdline"),
            "cgroup": _read_cgroup(f"{proc_root}/{name}/cgroup"),
        })

    rows.sort(key=lambda r: r["rss_kb"], reverse=True)
    return rows


def _read_vmhwm(status_path: str) -> Optional[int]:
    """Peak RSS in kB from /proc/<pid>/status ``VmHWM``, else None.

    The high-WATER mark: the kernel remembers it after the process shrinks. That
    is the only per-process signal that survives a spike, and it is what makes a
    BURST allocator findable — one that grabs GBs and releases them between two
    samples is invisible to current-RSS at every sampling instant, yet its VmHWM
    still indicts it. None for kernel threads (no mm) and on any read failure;
    None means UNKNOWN, never zero.
    """
    try:
        with open(status_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("VmHWM:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        try:
                            return int(parts[1])
                        except (ValueError, TypeError):
                            return None
                    return None
                if line.startswith("VmSwap:"):
                    break          # VmHWM precedes VmSwap; absent => no mm
    except OSError:
        return None
    return None


def _peak_only_consumers(
    all_rows: List[Dict[str, Any]], shown_pids: set, *,
    limit: int = _TOP_CONSUMERS, min_excess_ratio: float = 2.0,
    min_peak_kb: int = 262_144,
) -> List[Dict[str, Any]]:
    """Processes whose PEAK dwarfs their current RSS and that the RSS roster
    therefore never shows — the burst allocators.

    Only additive rows: a pid already in the RSS top-N is skipped (it is
    reported there, with its peak alongside). Thresholds exist so this stays
    signal: peak must exceed ``min_peak_kb`` (256 MB — a small process that
    doubled is noise on a 16 GB board) and be at least ``min_excess_ratio``x its
    current RSS (2x — proof it actually released, not merely grew a little).
    """
    out: List[Dict[str, Any]] = []
    for r in all_rows:
        peak = r.get("vmhwm_kb")
        if not peak or peak < min_peak_kb:
            continue
        if r["pid"] in shown_pids:
            continue
        rss = max(1, r.get("rss_kb") or 1)
        if peak / rss < min_excess_ratio:
            continue
        out.append(r)
    out.sort(key=lambda r: r["vmhwm_kb"], reverse=True)
    return out[:limit]


def _format_peaks(rows: List[Dict[str, Any]]) -> str:
    """Render the burst-allocator view. Empty is a real, useful answer here."""
    if not rows:
        return "no released peaks (no process is far below its own high-water mark)"
    parts = []
    for r in rows:
        who = r.get("cmdline") or r.get("comm") or "?"
        unit = (r.get("cgroup") or "").rsplit("/", 1)[-1]
        parts.append(f"{who}[{r['pid']}] peaked {r['vmhwm_kb'] // 1024}MB "
                     f"now {r['rss_kb'] // 1024}MB"
                     + (f" <{unit}>" if unit else ""))
    return "RELEASED peaks (spiked between samples): " + "; ".join(parts)


def _read_small(path: str, limit: int = 4096) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit).strip()
    except OSError:
        return None


def _read_cmdline(path: str, limit: int = 120) -> str:
    """NUL-joined argv, truncated. '' when unreadable (kernel threads have none)."""
    raw = _read_small(path)
    if not raw:
        return ""
    return " ".join(raw.replace("\x00", " ").split())[:limit]


def _read_cgroup(path: str, limit: int = 90) -> str:
    """The cgroup-v2 path for a pid — i.e. WHICH unit/scope owns this memory."""
    raw = _read_small(path)
    if not raw:
        return ""
    for line in raw.splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[0] == "0":      # v2 unified
            return parts[2][:limit]
    return raw.splitlines()[0][:limit]


def _top_cgroups(
    *, cgroup_root: str = "/sys/fs/cgroup", limit: int = _TOP_CONSUMERS,
) -> Optional[List[Dict[str, Any]]]:
    """Biggest cgroups by ``memory.current`` — attribution by UNIT, not pid.

    The single most useful addition from the 07-24 allocator dig. A top-RSS
    roster cannot see an aggregate: a user session that reaches GBs as forty
    ~100 MB processes is invisible per-process yet obvious per-cgroup. Measured
    on this box, ``memory.peak`` was 5,839 MB for ollama.service and 3,965 MB
    for one ``session-2.scope`` — the latter being precisely the class the
    reset implicated and the one no per-process view would have named.

    Leaf-ish only: a cgroup whose ``memory.current`` merely aggregates children
    (system.slice, user.slice) is reported too, but children are kept alongside
    it so the reader can see where inside the slice it actually sits.
    """
    rows: List[Dict[str, Any]] = []
    try:
        walker = os.walk(cgroup_root)
    except OSError:
        return None
    try:
        for dirpath, _dirnames, filenames in walker:
            if "memory.current" not in filenames:
                continue
            cur = _read_small(os.path.join(dirpath, "memory.current"))
            if not cur:
                continue
            try:
                val = int(cur)
            except (ValueError, TypeError):
                continue
            if val <= 0:
                continue
            peak_raw = _read_small(os.path.join(dirpath, "memory.peak"))
            try:
                peak = int(peak_raw) if peak_raw else None
            except (ValueError, TypeError):
                peak = None
            rel = dirpath[len(cgroup_root):].lstrip("/") or "/"
            rows.append({"cgroup": rel[-90:], "current_kb": val // 1024,
                         "peak_kb": (peak // 1024) if peak else None})
    except OSError:
        pass  # partial walk still beats nothing; we report what we got
    if not rows:
        return None
    rows.sort(key=lambda r: r["current_kb"], reverse=True)
    return rows[:limit]


def _format_consumers(rows: Optional[List[Dict[str, Any]]]) -> str:
    """Render the roster, distinguishing 'unreadable' from 'nothing big'."""
    if rows is None:
        return "top consumers UNREADABLE (/proc unlistable)"
    if not rows:
        return "top consumers: none resident (unexpected — treat as unreadable)"
    parts = []
    for r in rows:
        who = r.get("cmdline") or r.get("comm") or "?"
        unit = (r.get("cgroup") or "").rsplit("/", 1)[-1]
        parts.append(f"{who}[{r['pid']}] {r['rss_kb'] // 1024}MB"
                     + (f" <{unit}>" if unit else ""))
    return "top RSS: " + "; ".join(parts)


def _format_cgroups(rows: Optional[List[Dict[str, Any]]]) -> str:
    if rows is None:
        return "cgroup attribution UNREADABLE"
    if not rows:
        return "cgroup attribution: no accounted cgroups (memory controller off?)"
    return "top cgroups: " + ", ".join(
        f"{r['cgroup']} {r['current_kb'] // 1024}MB" for r in rows
    )


def _load_hist_state(path: str, boot_id: str) -> Dict[str, Any]:
    """Read ``{streak, boot_id, hist}``; reset hist across a reboot.

    The history is keyed to ``boot_id`` because the samples are stamped with
    ``time.monotonic()``, which RESTARTS at zero every boot. Carrying samples
    across a reboot would make the newest reading look older than the oldest
    and manufacture an absurd slope — and this fleet reboots unexpectedly, which
    is the whole reason this probe exists. Wall clock is not an option:
    RTC-less Pis and NTP steps make it forgeable (honest_failure_modes #6).
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict):
            raise ValueError("not an object")
    except (OSError, ValueError, TypeError):
        return {"streak": 0, "boot_id": boot_id, "hist": []}

    hist = doc.get("hist")
    if doc.get("boot_id") != boot_id or not isinstance(hist, list):
        hist = []
    clean: List[List[float]] = []
    for item in hist[-_HIST_MAX:]:
        if (isinstance(item, list) and len(item) == 2
                and all(isinstance(x, (int, float)) for x in item)):
            clean.append([float(item[0]), float(item[1])])
    try:
        streak = max(0, int(doc.get("streak", 0)))
    except (ValueError, TypeError):
        streak = 0
    return {"streak": streak, "boot_id": boot_id, "hist": clean}


def _save_hist_state(path: str, state: Dict[str, Any]) -> None:
    """Persist streak+history atomically. Never raises (best-effort witness)."""
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"streak": int(state.get("streak", 0)),
                       "boot_id": state.get("boot_id", ""),
                       "hist": state.get("hist", [])[-_HIST_MAX:]},
                      fh, separators=(",", ":"))
        os.replace(tmp, path)
    except OSError as exc:
        logger.debug("host_memory_pressure state write failed: %s", exc)


def _boot_id(path: str = "/proc/sys/kernel/random/boot_id") -> str:
    return _read_small(path) or "unknown-boot"


def _rate_drop(
    hist: List[List[float]], now_mono: float, total_kb: int,
    *, window_s: float, min_span_s: float,
) -> Optional[Tuple[float, float]]:
    """``(drop_ratio, span_s)`` over the window, or None if not measurable.

    drop_ratio is the FRACTION OF MemTotal lost between the oldest sample
    inside the window and now. None when there is no sample old enough — one
    reading is not a slope, and inventing one from a single quantised step is
    the claw-battery defect.
    """
    if not hist or total_kb <= 0:
        return None
    newest_avail = hist[-1][1]
    oldest: Optional[List[float]] = None
    for item in hist:
        span = now_mono - item[0]
        if span < 0:
            continue                      # monotonic went backwards: impossible
        if span > window_s:
            continue                      # older than the window
        if span >= min_span_s:
            oldest = item
            break                         # hist is chronological: first = oldest
    if oldest is None:
        return None
    span = now_mono - oldest[0]
    dropped_kb = oldest[1] - newest_avail
    if dropped_kb <= 0:
        return 0.0, span                  # flat or recovering
    return dropped_kb / float(total_kb), span


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
    cgroup_root: str = "/sys/fs/cgroup",
    boot_id_path: str = "/proc/sys/kernel/random/boot_id",
    now_mono: Optional[float] = None,
    rate_window_s: float = DEFAULT_RATE_WINDOW_S,
    rate_min_span_s: float = DEFAULT_RATE_MIN_SPAN_S,
    rate_drop_ratio: float = DEFAULT_RATE_DROP_RATIO,
    rate_floor_ratio: float = DEFAULT_RATE_FLOOR_RATIO,
) -> Optional[Signal]:
    """Host RAM is running out — page BEFORE the hardware watchdog resets it.

    Three independent legs, any of which can fire (they measure different
    things and a box can die by any of these roads):

      - AVAILABILITY: ``MemAvailable / MemTotal`` under ``degraded_avail_ratio``
        (20%), escalating to ``wedge`` under ``wedge_avail_ratio`` (8%).
      - STALL PRESSURE: ``/proc/pressure/memory`` ``some avg60`` over
        ``degraded_psi_avg60`` (10%), escalating over ``wedge_psi_avg60`` (40%).
        This is the leg that catches a box thrashing itself to death while
        MemAvailable still looks survivable — the 07-24 shape, where the reset
        came from a PID-1 stall rather than from true exhaustion.
      - RATE: availability fell ``rate_drop_ratio`` of MemTotal (20%) inside
        ``rate_window_s`` (180 s) AND landed under ``rate_floor_ratio`` (35%).
        The EARLY leg, and the one that actually buys time: on the 07-24 numbers
        the level legs fire ~4 s before the reset, the rate leg fires ~94 s
        before it. The floor is what keeps it honest about big-but-fine
        allocations — an ollama model load costs ~6 GB here and settles near 47%
        available, so it clears the slope but never the floor.

    The worst leg wins. The detail carries four attribution views, because each
    one is blind to a case the others catch:

      - top RSS, with cmdline + owning cgroup (``comm`` alone cannot tell a
        service from an interactive session) — who holds memory NOW;
      - RELEASED peaks, from ``VmHWM`` — who spiked and let go BETWEEN samples,
        which no current-RSS ranking can ever show;
      - top cgroups by ``memory.current`` — the only view that catches an
        aggregate, since a session reaching GBs as dozens of small processes is
        invisible per-process;

    and the non-RSS legs
    (Shmem/Slab/Dirty/swap), because tmpfs and kernel slab are charged to no
    process at all: /tmp and /dev/shm are 8 GB each here, so a runaway writing
    to the scratchpad can exhaust RAM while every process looks small.

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

    # RATE leg. History is appended EVERY tick (including clean ones) — a slope
    # is only measurable if the quiet samples were kept.
    mono = time.monotonic() if now_mono is None else now_mono
    state = _load_hist_state(sp, _boot_id(boot_id_path))
    state["hist"] = (state["hist"] + [[mono, float(avail_kb)]])[-_HIST_MAX:]
    rate = _rate_drop(state["hist"], mono, total_kb,
                      window_s=rate_window_s, min_span_s=rate_min_span_s)
    rate_fired = False
    if (rate is not None and rate[0] >= rate_drop_ratio
            and avail_ratio < rate_floor_ratio):
        rate_fired = True
        severity = severity or "degraded"
        reasons.append(
            f"availability fell {rate[0] * 100:.0f}% of RAM in {rate[1]:.0f}s "
            f"(>={rate_drop_ratio * 100:.0f}% within {rate_window_s:.0f}s) and "
            f"landed at {avail_ratio * 100:.1f}% (<{rate_floor_ratio * 100:.0f}%)"
        )

    if severity is None:
        note_disposition("host_memory_pressure", "clean")
        state["streak"] = 0
        _save_hist_state(sp, state)
        return None

    if severity == "degraded" and not rate_fired:
        # Level/PSI-only degraded still debounces a transient spike.
        state["streak"] = state["streak"] + 1
        _save_hist_state(sp, state)
        if state["streak"] < debounce_ticks:
            note_disposition(
                "host_memory_pressure", "indeterminate",
                reason=(f"pressure seen {state['streak']}/{debounce_ticks} "
                        f"consecutive ticks — debouncing a transient spike"),
            )
            return None
    else:
        # Wedge, or the RATE leg: fire now. The rate leg is ALREADY a
        # multi-sample measurement spanning >= rate_min_span_s, so debouncing it
        # would re-spend the very warning time it exists to buy.
        state["streak"] = state["streak"] + 1
        _save_hist_state(sp, state)

    scanned = _scan_processes(proc_root=proc_root)
    consumers = None if scanned is None else scanned[:_TOP_CONSUMERS]
    peaks = ([] if scanned is None
             else _peak_only_consumers(
                 scanned, {r["pid"] for r in (consumers or [])}))
    cgroups = _top_cgroups(cgroup_root=cgroup_root)
    psi_note = (
        f"stall pressure some/avg60={psi60:.1f}%" if psi60 is not None
        else f"stall pressure UNOBSERVABLE ({psi_path} absent) — "
             f"judged on availability alone"
    )
    hidden = " ".join(
        f"{k}={mem[k] // 1024}MB" for k in _MEMINFO_EXTRA_KEYS if k in mem
    ) or "non-RSS breakdown unavailable"

    detail = (
        f"Host memory pressure: {'; '.join(reasons)}. "
        f"{avail_kb // 1024}MB of {total_kb // 1024}MB available; {psi_note}. "
        f"{_format_consumers(consumers)}. {_format_peaks(peaks)}. "
        f"{_format_cgroups(cgroups)}. non-RSS: {hidden}. "
        f"On this fleet a sustained memory stall does NOT end in an oom-kill — "
        f"it starves PID 1 past RuntimeWatchdogUSec and the HARDWARE watchdog "
        f"hard-resets the box (manager-box hard-reset arc, 2026-07-24). "
        f"Inspect: ps -eo rss,pid,args --sort=-rss | head; "
        f"systemd-cgtop -m -n1 -b --depth=3; grep -E 'Shmem|Slab' /proc/meminfo; "
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
            "rate_drop_ratio": (None if rate is None else round(rate[0], 4)),
            "rate_span_s": (None if rate is None else round(rate[1], 1)),
            "rate_fired": rate_fired,
            "top_rss": consumers,
            "released_peaks": peaks,
            "top_cgroups": cgroups,
            "non_rss_kb": {k: mem[k] for k in _MEMINFO_EXTRA_KEYS if k in mem},
        },
    )
