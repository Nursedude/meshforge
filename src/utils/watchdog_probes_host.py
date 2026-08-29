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

# PER-BOX OVERRIDE for the two availability levels (2026-07-30).
#
# WHY, and why only the LEVEL legs: the ratio above is fleet-wide on purpose,
# but a ratio still assumes every box's NORMAL sits well above it. moc3 (905 MB,
# gateway + rnsd + watchdog + echo) lives at 19.8-20.3% available and has done so
# for FIVE WEEKS of unbroken uptime with no reset. So on that box the 20% gate
# fires on the box's own steady state: it flaps across the line every few
# minutes, carries no information, and trains the operator to ignore the one line
# that is supposed to mean "this box is about to be reset". Measured, not assumed
# — 10 min of sampling plus `uptime -s` = 2026-06-24.
#
# This does NOT weaken moc3's real protection, and that is the whole reason it is
# safe: this file's own RATE-leg comment shows the level legs fire ~4 s before the
# 07-24 reset while the rate leg fires ~94 s before it. The level legs are the
# tombstone. The rate leg is untouched here AND is permanently armed on moc3,
# because its floor is 35% availability and moc3 sits below that always.
#
# FAILS TOWARD THE STRICT DEFAULT, deliberately: this is a switch whose only
# power is to make a warning quieter, so an unreadable, malformed or
# out-of-order file must fall back to the fleet constants and say so. A
# silence-manufacturing switch must never fail silent (honest_failure_modes #1/#9,
# and the same rule `_soak_armed_devices` follows for battery soaks).
HOST_MEMORY_THRESHOLDS_REL = os.path.join(
    ".config", "meshforge", "host_memory_thresholds.json")
#: Logged-once witness per bad config path, so a typo is findable but does not
#: reprint every 30 s tick.
_AVAIL_OVERRIDE_WARNED: set = set()


def default_host_memory_thresholds_path() -> Optional[str]:
    """Where the per-box override lives, or None if the operator home is unknown.

    The RUNNER resolves and passes this explicitly; the probe does NOT reach for
    it on its own. That is deliberate: if the probe defaulted to reading the real
    operator home, every existing test that omits the ratios would silently
    depend on whether the box running the suite happens to carry an override —
    green on one box, opposite on another (feedback_tests_must_pin_ambient_state,
    the exact trap fixed in the claw-uplink tests hours earlier). Wiring it at
    the call site also puts what the probe consumes where a reader is looking.

    Deliberately NOT pathlib's home helper (MF001 — spelled out rather than
    quoted, because the lint rule matches the literal call text anywhere in the
    file, docstrings included) and NOT ``get_real_user_home()``: the watchdog
    runs as root with sudo blocked, so both answer ``/root``.
    ``watchdog_probes_liveness._operator_home`` is the resolver the claw probes
    already use for this (no cycle — liveness does not import this module).
    """
    try:
        from .watchdog_probes_liveness import _operator_home
        home = _operator_home()
    except Exception:
        return None
    return os.path.join(home, HOST_MEMORY_THRESHOLDS_REL) if home else None


def _read_avail_overrides(
    config_path: Optional[str],
) -> Tuple[Optional[float], Optional[float]]:
    """``(degraded, wedge)`` from the per-box override, or ``(None, None)``.

    ABSENT is the normal case and is silent — most boxes have no file. Anything
    PRESENT but unusable is a loud fallback to the fleet defaults, never a
    silent one: this switch can only ever make the warning quieter, so a typo
    must not be able to disarm the level legs.

    Rejected (each → strict defaults + a one-shot witness):
      * unreadable / not JSON / not an object
      * a value that is not a real number, or is a bool (``True`` is not 1.0 here)
      * ``not 0 < wedge < degraded < 1`` — an inverted or out-of-range pair would
        make the wedge rung unreachable, i.e. quietly delete the escalation.
    """
    # None = the caller did not wire an override at all (the hermetic default,
    # and what every test gets unless it opts in). Not "look somewhere sensible".
    path = config_path
    if not path or not os.path.exists(path):
        return None, None

    def _bad(why: str) -> Tuple[None, None]:
        if path not in _AVAIL_OVERRIDE_WARNED:
            _AVAIL_OVERRIDE_WARNED.add(path)
            logger.warning(
                "host memory threshold override %s IGNORED (%s) — using fleet "
                "defaults degraded=%.2f wedge=%.2f. Fix or remove the file; a "
                "malformed override must not quieten the level legs.",
                path, why, DEFAULT_DEGRADED_AVAIL_RATIO,
                DEFAULT_WEDGE_AVAIL_RATIO)
        return None, None

    try:
        with open(path) as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        return _bad("%s" % e.__class__.__name__)
    if not isinstance(doc, dict):
        return _bad("not a JSON object")

    def _num(key: str) -> Optional[float]:
        v = doc.get(key)
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError("%s is not a number" % key)
        return float(v)

    try:
        deg = _num("degraded_avail_ratio")
        wed = _num("wedge_avail_ratio")
    except ValueError as e:
        return _bad(str(e))
    if deg is None and wed is None:
        return _bad("no threshold keys present")

    eff_deg = DEFAULT_DEGRADED_AVAIL_RATIO if deg is None else deg
    eff_wed = DEFAULT_WEDGE_AVAIL_RATIO if wed is None else wed
    if not (0.0 < eff_wed < eff_deg < 1.0):
        return _bad("need 0 < wedge (%.3f) < degraded (%.3f) < 1"
                    % (eff_wed, eff_deg))
    return deg, wed

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


# In-process last-known debounce/history state, keyed by state-file path, plus
# a consecutive-write-error count per path. Both exist because the disk copy is
# the RESTART-survival mechanism, not the per-tick one: when the file cannot be
# written, holding the last state in memory keeps the debounced legs alive,
# and the counter makes the degradation visible to the operator instead of
# silently shortening the probe to its tombstone leg (audit finding, 07-26).
_MEM_FALLBACK: Dict[str, Dict[str, Any]] = {}
_WRITE_ERRORS: Dict[str, int] = {}


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

    Within one process the in-memory copy is always at-least-as-new as the
    file (``_save_hist_state`` writes it unconditionally, before touching
    disk), so it is preferred WHENEVER present — not only when the read
    fails. A broken state dir usually leaves the OLD file readable while
    every write fails (ro-remount, ENOSPC, perms flip): consulting memory
    only on a failed read let that stale disk value win every tick, zeroing
    the streak (indistinguishable from "the box has never been under
    pressure") and starving the RATE leg of samples. Drill-measured
    2026-07-26: 0/10 degraded ticks fired vs 9/10 on a writable path — the
    probe silently collapsed to its wedge "tombstone" leg. The disk copy is
    read only at process start; it exists to survive a RESTART, not to carry
    a single tick.
    """
    held = _MEM_FALLBACK.get(path)
    if held is not None:
        if held.get("boot_id") == boot_id:
            return {"streak": held["streak"], "boot_id": boot_id,
                    "hist": list(held["hist"])}
        return {"streak": 0, "boot_id": boot_id, "hist": []}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict):
            raise ValueError("not an object")
    except (OSError, ValueError, TypeError):
        return {"streak": 0, "boot_id": boot_id, "hist": []}

    hist = doc.get("hist")
    if doc.get("boot_id") != boot_id:
        # A streak banked under another boot is as meaningless as its history
        # — the fallback path above resets both, and the two paths must agree.
        return {"streak": 0, "boot_id": boot_id, "hist": []}
    if not isinstance(hist, list):
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
    """Persist streak+history atomically. Never raises (best-effort witness).

    ALWAYS updates the in-process fallback first, so a failing disk cannot
    suppress the debounce (see ``_load_hist_state``). A write failure is
    counted and logged at ERROR on the first occurrence — not debug, which the
    runner's default INFO level hid completely, making this the swallow with
    no witness that honest_failure_modes #9 exists to forbid.
    """
    _MEM_FALLBACK[path] = {
        "streak": int(state.get("streak", 0)),
        "boot_id": state.get("boot_id", ""),
        "hist": list(state.get("hist", [])[-_HIST_MAX:]),
    }
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
        prior = _WRITE_ERRORS.get(path, 0)
        _WRITE_ERRORS[path] = prior + 1
        if prior == 0:
            logger.error(
                "host_memory_pressure state write FAILED (%s): %s — debounce "
                "and rate history are now held in-process only and will NOT "
                "survive a restart; check the unit's writable paths (#60)",
                path, exc,
            )
        else:
            logger.debug("host_memory_pressure state write still failing "
                         "(%d consecutive): %s", prior + 1, exc)
        return
    if _WRITE_ERRORS.get(path):
        logger.info("host_memory_pressure state write RECOVERED after %d "
                    "consecutive failures (%s)", _WRITE_ERRORS[path], path)
        _WRITE_ERRORS[path] = 0


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


def probe_host_memory_pressure(**kwargs: Any) -> Optional[Signal]:
    """Blanket-guarded entry point — full contract on the impl below.

    The runner dispatches every probe inside ONE tick sweep, so an unexpected
    raise here costs the tick every OTHER probe's signals too. The specific
    handlers inside stay authoritative for their cases; this is the outermost
    layer only: a crash is an ``indeterminate`` observation with a log
    witness, never a silent green and never a dead tick.
    """
    try:
        return _probe_host_memory_pressure_impl(**kwargs)
    except Exception as exc:
        note_disposition("host_memory_pressure", "indeterminate",
                         reason=f"probe crashed: {exc!r}")
        logger.warning("probe_host_memory_pressure crashed: %r", exc)
        return None


def _probe_host_memory_pressure_impl(
    *,
    meminfo_path: str = "/proc/meminfo",
    psi_path: str = "/proc/pressure/memory",
    proc_root: str = "/proc",
    # None = "not specified by the caller", which is what lets the per-box
    # override apply. An explicit value always WINS over the config file, so
    # every existing test that pins a ratio keeps pinning it and cannot be
    # perturbed by a config file on whatever box runs the suite
    # (feedback_tests_must_pin_ambient_state — the trap this very session fixed
    # in the claw-uplink tests).
    degraded_avail_ratio: Optional[float] = None,
    wedge_avail_ratio: Optional[float] = None,
    avail_config_path: Optional[str] = None,
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

    # Resolve the two LEVEL thresholds: explicit argument > per-box override
    # file > fleet default. Only the level legs are tunable — the rate leg (the
    # one that actually buys warning time) is deliberately NOT overridable,
    # because a box tuned quiet on level must keep its early leg at full
    # sensitivity or the tuning becomes a blindfold.
    _cfg_deg, _cfg_wed = _read_avail_overrides(avail_config_path)
    avail_thresholds_source = "fleet-default"
    if degraded_avail_ratio is None:
        if _cfg_deg is not None:
            degraded_avail_ratio = _cfg_deg
            avail_thresholds_source = "per-box override"
        else:
            degraded_avail_ratio = DEFAULT_DEGRADED_AVAIL_RATIO
    if wedge_avail_ratio is None:
        if _cfg_wed is not None:
            wedge_avail_ratio = _cfg_wed
            avail_thresholds_source = "per-box override"
        else:
            wedge_avail_ratio = DEFAULT_WEDGE_AVAIL_RATIO

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
            # The gate that judged this reading, and WHERE it came from. A
            # per-box override can only make the level legs quieter, so it must
            # travel with the signal: an operator reading a page (or a later
            # reviewer asking "why didn't this fire sooner?") has to be able to
            # see that the box was tuned, without going to look for a config
            # file they may not know exists.
            "degraded_avail_ratio": degraded_avail_ratio,
            "wedge_avail_ratio": wedge_avail_ratio,
            "avail_thresholds_source": avail_thresholds_source,
            "psi_some_avg60": psi60,
            "rate_drop_ratio": (None if rate is None else round(rate[0], 4)),
            "rate_span_s": (None if rate is None else round(rate[1], 1)),
            "rate_fired": rate_fired,
            # Non-zero => the debounce/rate history is surviving in-process
            # only and will be lost on restart. Surfaced rather than logged so
            # a probe/operator can SEE a degraded detector (#9, #63).
            "state_write_errors": _WRITE_ERRORS.get(sp, 0),
            "top_rss": consumers,
            "released_peaks": peaks,
            "top_cgroups": cgroups,
            "non_rss_kb": {k: mem[k] for k in _MEMINFO_EXTRA_KEYS if k in mem},
        },
    )


# ─────────────────────────────────────────────────────────────────────
# memory_cap_engaged — did a MemoryMax cap actually BITE? (2026-07-24)
#
# The blind spot this closes was created by its own session: on 2026-07-24 eight
# fleet boxes gained hard `MemoryMax` caps on user-1000.slice (plus ollama's
# pre-existing 8G), and NOTHING observed them firing. A cap that OOM-kills the
# operator's ssh session or a user unit is invisible — the process is simply gone.
# That is the honest_failure_modes #9 shape exactly: a real, consequential event
# with no witness, discovered later and outside the app.
#
# It also replaces a WORSE plan. The original follow-up was "read memory.peak in a
# week and re-tighten the caps" — willpower, not harness, and pointed the wrong
# way: a too-GENEROUS cap still bounds a runaway (which climbs to many GB), while a
# too-TIGHT one kills legitimate work, which is the failure that already happened
# this session. So the trigger to revisit a number should be evidence the ceiling
# is actually being hit, not a calendar entry. `memory.peak` is also reset by a
# reboot, which this fleet does unexpectedly.
_CAP_STATE_PATH = "/var/lib/meshforge/memory_cap_engaged_state.json"

# Consecutive ticks the ceiling leg must persist. A single `max` increment is a
# transient touch of the limit and reclaim handled it; living AT the ceiling is
# what warrants a look. Kills deliberately bypass this — see below.
_CAP_CEILING_DEBOUNCE_TICKS = 2

# Cap-carrying cgroups reported per tick. A bound, not a filter: exceeding it is
# itself surfaced rather than silently truncated (no_silent_caps).
_CAP_MAX_SUBJECTS = 12

# Ceiling-leg benignity gate (2026-08-28). One box's user slice rode its 8 GB
# cap through 18k+ max_hits with PSI 0.00 and zero kills: 5.2 GB of the charge
# was clean page cache, which ALWAYS fills to a cap during I/O-heavy work and
# reclaims for free — that is the cap working as designed, not a finding, and
# paging on it would make the ceiling leg chronically re-fire on every busy
# session. The dangerous shape is different on two axes this gate reads:
# reclaim that COSTS time (PSI), or a charge the kernel CANNOT reclaim
# (anon+shmem). Below both thresholds the ceiling-riding is suppressed WITH a
# witness in the clean disposition (honest_failure_modes #9); at-or-above
# either, or when PSI/memory.stat cannot be read, the leg pages exactly as
# before — an unreadable discriminator fails TOWARD the page, never toward
# silence (#2: the benign claim needs positive evidence).
_CAP_PSI_BENIGN_SOME_AVG10 = 5.0     # % — below this, reclaim is effectively free
_CAP_BENIGN_UNRECLAIMABLE_SHARE = 0.5  # anon+shmem over cap — above, one burst from kills

# In-process last-written cap baselines + consecutive-write-error count, per
# state path — the same pair `_MEM_FALLBACK`/`_WRITE_ERRORS` carries for the
# pressure probe, and for the same reason: the disk copy is the RESTART-
# survival mechanism, not the per-tick one. Without it an unwritable path made
# every tick read `fresh`, so ONE historical kill re-paged forever and the
# ceiling streak restarted from 0 — permanently silenced (audit finding,
# 07-26).
_CAP_MEM_FALLBACK: Dict[str, Dict[str, Any]] = {}
_CAP_WRITE_ERRORS: Dict[str, int] = {}


def _read_memory_events(path: str) -> Optional[Dict[str, int]]:
    """Parse a cgroup ``memory.events`` file into {key: count}.

    None when unreadable/garbage — the caller maps that to ``indeterminate``,
    never to "no kills". An unreadable counter is unobservable, and on this
    fleet unobservable is never healthy (honest_failure_modes #2).
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return None
    out: Dict[str, int] = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            out[parts[0]] = int(parts[1])
        except (ValueError, TypeError):
            continue
    return out or None


def _read_psi_some_avg10(path: str) -> Optional[float]:
    """The ``some avg10`` value from a cgroup ``memory.pressure`` file.

    None when unreadable or unparseable (CONFIG_PSI off, cgroup gone) — the
    ceiling leg maps that to "cannot rule benign", never to "no pressure".
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return None
    for line in raw.splitlines():
        parts = line.split()
        if not parts or parts[0] != "some":
            continue
        for tok in parts[1:]:
            if tok.startswith("avg10="):
                try:
                    return float(tok[len("avg10="):])
                except (ValueError, TypeError):
                    return None
    return None


def _read_unreclaimable_kb(path: str) -> Optional[int]:
    """``anon + shmem`` from a cgroup ``memory.stat``, in KB.

    The unreclaimable part of the charge: file cache drops for free under
    reclaim, anon and shmem do not. None when the file is unreadable or the
    ``anon`` key is absent — a stat we could not read supports no claim.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return None
    fields: Dict[str, int] = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in ("anon", "shmem"):
            try:
                fields[parts[0]] = int(parts[1])
            except (ValueError, TypeError):
                return None
    if "anon" not in fields:
        return None
    return (fields["anon"] + fields.get("shmem", 0)) // 1024


def _capped_cgroups(*, cgroup_root: str = "/sys/fs/cgroup") -> Optional[List[Dict[str, Any]]]:
    """Every cgroup carrying a FINITE ``memory.max``, with its events counters.

    Returns None only when the tree cannot be walked at all (indeterminate).
    An empty list is a positive observation: nothing on this box is capped, so
    there is nothing for this probe to judge (INERT — e.g. moc3, where the
    controller is disabled entirely, or any box predating the 07-24 rollout).
    """
    # os.walk() does NOT raise on a missing/unreadable root — it silently yields
    # nothing, which would render "could not look" as the positive observation
    # "nothing here is capped". That is the exact defect this probe exists to
    # catch, so the root is checked explicitly and mid-walk errors are captured.
    if not os.path.isdir(cgroup_root):
        return None
    walk_errors: List[OSError] = []
    rows: List[Dict[str, Any]] = []
    try:
        for dirpath, _dirnames, filenames in os.walk(
                cgroup_root, onerror=walk_errors.append):
            if "memory.max" not in filenames:
                continue
            raw = _read_small(os.path.join(dirpath, "memory.max"))
            if not raw or raw == "max":
                continue          # uncapped is the overwhelmingly common case
            try:
                cap = int(raw)
            except (ValueError, TypeError):
                continue
            rel = dirpath[len(cgroup_root):].lstrip("/") or "/"
            rows.append({
                "cgroup": rel,
                "cap_kb": cap // 1024,
                "events": _read_memory_events(os.path.join(dirpath, "memory.events")),
                "current_kb": (lambda v: int(v) // 1024 if v and v.isdigit() else None)(
                    _read_small(os.path.join(dirpath, "memory.current"))),
            })
    except OSError as exc:
        walk_errors.append(exc)
    if walk_errors and not rows:
        # Saw nothing AND hit errors: that is unobservable, not "uncapped".
        return None
    return rows


def _load_cap_state(path: str, boot_id: str) -> Dict[str, Any]:
    """Read ``{boot_id, cgroups:{name:{oom,max,streak}}}``; reset across a reboot.

    The counters in ``memory.events`` are cumulative SINCE THE CGROUP WAS CREATED
    and therefore restart at zero every boot. Carrying baselines across a reboot
    would make the post-boot counters look like a decrease and hide real kills,
    so the whole baseline is keyed to ``boot_id`` (honest_failure_modes #6 —
    wall clock is forgeable on RTC-less Pis, boot_id is not).

    Prefers the in-process copy WHENEVER present (``_save_cap_state`` writes
    it unconditionally, before touching disk, so within one process it is
    always at-least-as-new than the file); disk is read only at process
    start. See ``_load_hist_state`` for why memory-only-on-failed-read left
    the readable-stale route open — here the stakes were worse: with no
    fallback at all, an unwritable path made every tick ``fresh``, so one
    historical kill re-fired forever and the ceiling streak never reached
    its threshold.
    """
    held = _CAP_MEM_FALLBACK.get(path)
    if held is not None:
        if held.get("boot_id") == boot_id:
            return {"boot_id": boot_id,
                    "cgroups": {n: dict(v)
                                for n, v in held.get("cgroups", {}).items()},
                    "fresh": False}
        return {"boot_id": boot_id, "cgroups": {}, "fresh": True}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict):
            raise ValueError("not an object")
    except (OSError, ValueError, TypeError):
        return {"boot_id": boot_id, "cgroups": {}, "fresh": True}
    if doc.get("boot_id") != boot_id:
        # New boot: baselines are meaningless, but a nonzero counter seen on the
        # FIRST look still means something died THIS boot — reported, not lost.
        return {"boot_id": boot_id, "cgroups": {}, "fresh": True}
    cg = doc.get("cgroups")
    return {"boot_id": boot_id,
            "cgroups": cg if isinstance(cg, dict) else {},
            "fresh": False}


def _save_cap_state(path: str, state: Dict[str, Any]) -> None:
    """Persist baselines atomically. Never raises; a failure leaves a witness.

    ALWAYS updates the in-process fallback first, so a failing disk cannot
    reset the baselines every tick (see ``_load_cap_state``). Inner cgroup
    dicts are copied defensively — the probe builds new ones each tick, but
    the fallback must never share references a caller might mutate.
    """
    _CAP_MEM_FALLBACK[path] = {
        "boot_id": state.get("boot_id", ""),
        "cgroups": {n: dict(v)
                    for n, v in (state.get("cgroups") or {}).items()
                    if isinstance(v, dict)},
    }
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"boot_id": state.get("boot_id", ""),
                       "cgroups": state.get("cgroups", {})},
                      fh, separators=(",", ":"))
        os.replace(tmp, path)
    except OSError as exc:
        prior = _CAP_WRITE_ERRORS.get(path, 0)
        _CAP_WRITE_ERRORS[path] = prior + 1
        if prior == 0:
            logger.error(
                "memory_cap_engaged state unwritable (%s): %s — kill deltas "
                "and the ceiling streak are held in-process only and will NOT "
                "survive a restart; check the unit's writable paths (#60)",
                path, exc,
            )
        else:
            logger.debug("memory_cap_engaged state write still failing "
                         "(%d consecutive): %s", prior + 1, exc)
        return
    if _CAP_WRITE_ERRORS.get(path):
        logger.info("memory_cap_engaged state write RECOVERED after %d "
                    "consecutive failures (%s)", _CAP_WRITE_ERRORS[path], path)
        _CAP_WRITE_ERRORS[path] = 0


def probe_memory_cap_engaged(**kwargs: Any) -> List[Signal]:
    """Blanket-guarded entry point — full contract on the impl below.

    Same rationale as ``probe_host_memory_pressure``: one raising probe must
    not take down the whole runner tick's dispatch. A crash is an
    ``indeterminate`` observation with a log witness, never a silent green.
    """
    try:
        return _probe_memory_cap_engaged_impl(**kwargs)
    except Exception as exc:
        note_disposition("memory_cap_engaged", "indeterminate",
                         reason=f"probe crashed: {exc!r}")
        logger.warning("probe_memory_cap_engaged crashed: %r", exc)
        return []


def _probe_memory_cap_engaged_impl(
    *,
    cgroup_root: str = "/sys/fs/cgroup",
    state_path: str = _CAP_STATE_PATH,
    boot_id_path: str = "/proc/sys/kernel/random/boot_id",
    ceiling_debounce_ticks: int = _CAP_CEILING_DEBOUNCE_TICKS,
    max_subjects: int = _CAP_MAX_SUBJECTS,
) -> List[Signal]:
    """A ``MemoryMax`` cap actually engaged — something was killed, or the slice
    is living at its ceiling.

    Two legs, per capped cgroup (subject = the cgroup path, so identity is stable
    across ticks and each cap is tracked independently):

      - **KILL** (``wedge``, fires IMMEDIATELY): ``oom_kill`` or
        ``oom_group_kill`` rose since the last tick. A kill is a discrete,
        irreversible event — debouncing it would only delay the page while the
        process stays dead. Either a runaway was correctly bounded (worth
        knowing) or legitimate work was killed (urgent: the cap is too tight).
        The detail cannot tell those apart, so it says so and names both reads.
      - **CEILING** (``degraded``, debounced ``ceiling_debounce_ticks``): the
        ``max`` counter keeps rising without kills, i.e. the cgroup repeatedly
        hits its hard limit and reclaim is absorbing it. This is the honest,
        evidence-based trigger to revisit the number — it replaces the
        "re-read memory.peak in a week" calendar plan that this probe retires.
        Gated on benignity (2026-08-28): page cache ALWAYS fills to a cap
        during I/O-heavy work and reclaims for free, so ceiling-riding alone
        re-fires chronically on every busy session while meaning nothing. The
        leg now pages only when the riding is costly or dangerous — PSI ``some
        avg10`` at/above ``_CAP_PSI_BENIGN_SOME_AVG10``, unreclaimable charge
        (anon+shmem) above ``_CAP_BENIGN_UNRECLAIMABLE_SHARE`` of the cap, or
        either discriminator unreadable (an unverifiable "benign" pages, never
        stays silent). The suppressed benign shape leaves its witness in the
        clean disposition. The streak keeps counting through benign ticks, so
        a turn dangerous pages immediately, without re-serving the debounce.

    Self-guards, each a distinct honest answer rather than a shared silence:
    the tree unwalkable → ``indeterminate``; no capped cgroup anywhere →
    ``inert``; a capped cgroup whose ``memory.events`` is unreadable →
    ``indeterminate`` (never "no kills"); a counter that DECREASED (cgroup
    destroyed and recreated — the user slice does this on last-logout without
    linger) → baseline re-established silently, never a negative delta and never
    read as recovery.
    """
    rows = _capped_cgroups(cgroup_root=cgroup_root)
    if rows is None:
        note_disposition("memory_cap_engaged", "indeterminate",
                         reason=f"cgroup tree unwalkable at {cgroup_root}")
        return []
    if not rows:
        note_disposition("memory_cap_engaged", "inert",
                         reason="no cgroup on this box carries a finite MemoryMax")
        return []

    boot = _boot_id(boot_id_path)
    state = _load_cap_state(state_path, boot)
    prev = state["cgroups"]
    fresh_boot = state.get("fresh", False)
    new_state: Dict[str, Any] = {}
    signals: List[Signal] = []
    unreadable: List[str] = []
    benign_riders: List[str] = []

    for row in sorted(rows, key=lambda r: r["cgroup"])[:max_subjects]:
        name = row["cgroup"]
        ev = row["events"]
        if ev is None:
            unreadable.append(name)
            # Preserve any prior baseline: losing it would turn the next
            # readable tick into a false "first observation".
            if name in prev:
                new_state[name] = prev[name]
            continue
        kills = int(ev.get("oom_kill", 0)) + int(ev.get("oom_group_kill", 0))
        hits = int(ev.get("max", 0))
        old = prev.get(name) or {}
        old_kills = int(old.get("oom", 0) or 0)
        old_hits = int(old.get("max", 0) or 0)
        streak = int(old.get("streak", 0) or 0)

        # A decrease means the cgroup was recreated (or we are post-reboot):
        # re-baseline, never a negative delta.
        recreated = kills < old_kills or hits < old_hits
        d_kills = 0 if recreated else kills - old_kills
        d_hits = 0 if recreated else hits - old_hits
        if fresh_boot:
            # First look of this boot: the cumulative count IS the news.
            d_kills = kills

        streak = streak + 1 if d_hits > 0 else 0
        new_state[name] = {"oom": kills, "max": hits, "streak": streak}

        cap_mb = row["cap_kb"] // 1024 if row["cap_kb"] else 0
        cur_mb = (row["current_kb"] // 1024) if row["current_kb"] else 0
        where = f"{name} (cap {cap_mb} MB, current {cur_mb} MB)"

        if d_kills > 0:
            signals.append(Signal(
                cls="memory_cap_engaged", subject=name, severity="wedge",
                detail=(
                    f"MemoryMax cap KILLED {d_kills} process(es) in {where}"
                    f"{' — cumulative since boot' if fresh_boot else ''}"
                    f"; oom_kill={ev.get('oom_kill', 0)} "
                    f"oom_group_kill={ev.get('oom_group_kill', 0)} "
                    f"max_hits={hits}. TWO READINGS, this probe cannot tell them "
                    f"apart: (a) a runaway was correctly bounded — the cap did "
                    f"its job, find what allocated; or (b) LEGITIMATE work was "
                    f"killed — the cap is too tight and should be raised. Check "
                    f"which: journalctl -k | grep -i 'killed process' names the "
                    f"victim. A killed interactive/agent session or user unit is "
                    f"case (b). Cap lives in "
                    f"/etc/systemd/system/<slice>.d/10-memory-cap.conf; verify "
                    f"any change at the KERNEL (cat memory.max), never at "
                    f"systemctl show."),
                extra={"cgroup": name, "cap_kb": row["cap_kb"],
                       "new_kills": d_kills, "events": ev,
                       "first_look_this_boot": fresh_boot},
            ))
        elif streak >= ceiling_debounce_ticks:
            # Benignity gate: a slice riding its cap on clean page cache with
            # PSI ~0 is the cap WORKING (cache always fills to a ceiling during
            # I/O). Page only when reclaim costs time, unreclaimable memory
            # dominates, or the discriminators cannot be read.
            cg_dir = cgroup_root if name == "/" else os.path.join(cgroup_root, name)
            psi = _read_psi_some_avg10(os.path.join(cg_dir, "memory.pressure"))
            unrecl_kb = _read_unreclaimable_kb(os.path.join(cg_dir, "memory.stat"))
            unrecl_mb = unrecl_kb // 1024 if unrecl_kb is not None else None
            if psi is None or unrecl_kb is None:
                why = ("memory.pressure/memory.stat unreadable — cannot rule "
                       "the ceiling-riding benign, so it pages")
            elif psi >= _CAP_PSI_BENIGN_SOME_AVG10:
                why = (f"reclaim is COSTING time: PSI some avg10 {psi:.1f}% >= "
                       f"{_CAP_PSI_BENIGN_SOME_AVG10:.0f}%")
            elif row["cap_kb"] and unrecl_kb > row["cap_kb"] * _CAP_BENIGN_UNRECLAIMABLE_SHARE:
                why = (f"UNRECLAIMABLE memory dominates: anon+shmem {unrecl_mb} MB "
                       f"is over {_CAP_BENIGN_UNRECLAIMABLE_SHARE:.0%} of the "
                       f"{cap_mb} MB cap — one allocation burst from kills")
            else:
                benign_riders.append(
                    f"{name} (psi {psi:.2f}%, anon+shmem {unrecl_mb} MB of "
                    f"{cap_mb} MB cap)")
                continue
            signals.append(Signal(
                cls="memory_cap_engaged", subject=name, severity="degraded",
                detail=(
                    f"{where} is living AT its MemoryMax ceiling: max_hits rose "
                    f"{d_hits} this tick, {streak} ticks running, no kills — and "
                    f"it is NOT the benign cache-riding shape: {why}. This is "
                    f"the evidence-based trigger to revisit the number — read "
                    f"memory.peak now that it means something: "
                    f"cat /sys/fs/cgroup/{name}/memory.peak. "
                    f"Raise the cap if the workload is legitimate; find the "
                    f"allocator if not. Not urgent — nothing has died."),
                extra={"cgroup": name, "cap_kb": row["cap_kb"],
                       "max_hits": hits, "delta_hits": d_hits,
                       "streak": streak, "events": ev,
                       "psi_some_avg10": psi,
                       "unreclaimable_kb": unrecl_kb},
            ))

    _save_cap_state(state_path, {"boot_id": boot, "cgroups": new_state})

    # Exactly one disposition every tick, always. Recording nothing on the
    # healthy path would leave this class ABSENT from the coverage view, which
    # conflates "ran, observed, clean" with "never ran" — the conflation the
    # disposition recorder exists to remove. Caught by running the probe against
    # the real tree, not by a fixture (the tests were happy).
    if unreadable:
        note_disposition("memory_cap_engaged", "indeterminate",
                         reason=f"memory.events unreadable for {unreadable[:3]}")
    elif len(rows) > max_subjects:
        # INDETERMINATE, not clean: the judged ones are fine but the remainder
        # were never looked at, and "clean" would claim a verdict on caps this
        # tick did not observe (no_silent_caps — surface the bound, do not let
        # it read as coverage).
        note_disposition("memory_cap_engaged", "indeterminate",
                         reason=f"{len(rows)} capped cgroups exceed the "
                                f"{max_subjects}-subject bound; the remainder "
                                f"were NOT judged this tick")
    else:
        # A suppressed benign rider is still an observation — it must appear
        # here or the suppression is a silent swallow (#9). Bounded to two
        # entries so the reason stays legible.
        benign_note = ""
        if benign_riders:
            shown = "; ".join(benign_riders[:2])
            more = len(benign_riders) - 2
            benign_note = (f"; {len(benign_riders)} riding the ceiling on "
                           f"reclaimable cache, suppressed as benign: {shown}"
                           + (f" (+{more} more)" if more > 0 else ""))
        note_disposition("memory_cap_engaged", "clean",
                         reason=f"{len(rows)} capped cgroup(s) judged; "
                                f"{len(signals)} engaged{benign_note}")
    return signals
