"""Watchdog probes — local service health failure shapes.

HTTP-local unresponsive (#61/#70), fd exhaustion (#73), PhoneAPI TCP leak
(#75), service-inactive. Tracer peer unreachable moved to
``watchdog_probes_tracer`` (2026-07-19, MF025 split); channel feed dark
moved to ``watchdog_probes_channel`` (2026-08-07, same rule).
Part of the ``watchdog_probes`` split (2026-06-09) — import via the
``utils.watchdog_probes`` hub, not from here.
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.request import urlopen
from urllib.error import HTTPError, URLError

from utils.watchdog_probe_core import (
    Signal,
    _journal_count_match,
    _resolve_main_pid,
    _short_unix_ts,
    note_disposition,
)

# Same logger name the runner uses (watchdog_runner.py) so a swallowed
# state-write failure lands in the one "watchdog" namespace the operator
# already greps — honest_failure_modes #9 ("every swallow gets a witness").
logger = logging.getLogger("watchdog")

# ─────────────────────────────────────────────────────────────────────
# Probe: HTTP local unresponsive (catches socketserver-deadlock class)
# ─────────────────────────────────────────────────────────────────────


def probe_http_local(
    service_name: str,
    *,
    host: str = "127.0.0.1",
    port: int = 5000,
    path: str = "/healthz",
    timeout_s: float = 2.0,
) -> Optional[Signal]:
    """2s loopback GET on a local HTTP endpoint.

    Catches the Issue #61 class: kernel ``accept()`` succeeds (port
    appears open via ``ss``), but the handler thread is wedged and
    the response never arrives. A socket-level connect probe would
    miss this; we need to actually wait for an HTTP response.

    Returns signal when the service is reportedly active but the
    endpoint won't respond within timeout. None when the service
    is inactive (a different probe owns that) or when the endpoint
    responds (even with 503 — a 503 is honest; "no response at all"
    is the wedge).
    """
    url = f"http://{host}:{port}{path}"
    try:
        with urlopen(url, timeout=timeout_s) as resp:
            # A 2xx response: the HTTP server loop is alive. Non-2xx
            # statuses RAISE HTTPError from urlopen — handled below as
            # equally-alive (we don't gate on status; /healthz can
            # return 503 during warming and that's correct, not wedged).
            _ = resp.read(64)
            note_disposition("http_local_unresponsive", "clean")
            return None
    except HTTPError:
        # The server RESPONDED — with an error status, but for THIS class
        # ("no response at all" is the wedge) any response means the
        # handler loop is alive. Must be caught before URLError (its
        # superclass), or a 5xx would falsely read "endpoint unobservable".
        note_disposition("http_local_unresponsive", "clean")
        return None
    except socket.timeout:
        pass
    except URLError as exc:
        # ConnectionRefused means port isn't bound — usually that's
        # "service is inactive" not "wedged". Don't double-alarm here.
        if "Connection refused" in str(exc):
            note_disposition(
                "http_local_unresponsive", "indeterminate",
                reason="connection refused; port unbound — service_inactive owns that",
            )
            return None
        # DNS/other URLErrors — return None; this probe doesn't
        # speculate on network configuration.
        note_disposition(
            "http_local_unresponsive", "indeterminate",
            reason="URLError; endpoint unobservable",
        )
        return None
    except (OSError, ValueError):
        note_disposition(
            "http_local_unresponsive", "indeterminate",
            reason="socket/OS error; endpoint unobservable",
        )
        return None

    return Signal(
        cls="http_local_unresponsive",
        subject=service_name,
        severity="wedge",
        detail=(
            f"GET {url} did not respond within {timeout_s:.1f}s. "
            f"Port is bound but handler may be wedged (Issue #61 class). "
            f"Check thread stacks: ps -eLo pid,tid,comm | grep -E "
            f"'meshforge-map|shutdown'"
        ),
        issue_ref=61,
        extra={"url": url, "timeout_s": timeout_s},
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: file-descriptor exhaustion (Issue #73)
# ─────────────────────────────────────────────────────────────────────

# Soft RLIMIT_NOFILE line in /proc/<pid>/limits, e.g.:
#   Max open files            1024                 524288               files
_LIMITS_NOFILE_RE = re.compile(
    r"^Max open files\s+(\d+|unlimited)\s+(\d+|unlimited)", re.MULTILINE
)


def _read_fd_usage(
    pid: int, *, proc_root: str = "/proc",
) -> Optional[Tuple[int, int]]:
    """Return ``(open_fd_count, soft_limit)`` for ``pid`` or None.

    Counts entries in ``/proc/<pid>/fd`` and parses the *soft*
    ``Max open files`` column from ``/proc/<pid>/limits`` — the soft
    limit is the one a process actually hits ([Errno 24]); the hard
    limit only caps how high the soft limit can be raised. Returns None
    on any read failure (process vanished, permission, unlimited soft
    limit) so an unreadable target never alarms.
    """
    fd_dir = Path(proc_root) / str(pid) / "fd"
    limits_path = Path(proc_root) / str(pid) / "limits"
    try:
        open_count = sum(1 for _ in os.scandir(fd_dir))
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return None
    try:
        limits_text = limits_path.read_text()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    m = _LIMITS_NOFILE_RE.search(limits_text)
    if not m:
        return None
    soft_raw = m.group(1)
    if soft_raw == "unlimited":
        # No meaningful ceiling to measure against — never alarm.
        return None
    try:
        soft = int(soft_raw)
    except (ValueError, TypeError):
        return None
    if soft <= 0:
        return None
    return open_count, soft


def probe_fd_exhaustion(
    service_name: str,
    *,
    proc_root: str = "/proc",
    systemctl_path: str = "systemctl",
    degraded_ratio: float = 0.80,
    wedge_ratio: float = 0.95,
    main_pid: Optional[int] = None,
) -> Optional[Signal]:
    """Warn when a service's open fds approach its soft RLIMIT_NOFILE.

    This is the *proactive* companion to ``probe_http_local`` (which only
    fires once the port has already gone dark). Issue #73 (2026-05-31):
    meshanchor-map leaked one paho MQTT client socket per reconnect until
    it hit the 1024 soft fd cap; new ``accept()`` then failed with
    ``[Errno 24]`` and ``:5000`` wedged. By the time ``http_local``
    fired, the box had been unservable for an hour. Counting fds vs the
    soft limit surfaces the climb *before* the wedge — and names the
    real cause (fd leak) instead of pointing at thread stacks.

    ``degraded`` past ``degraded_ratio`` (default 80%), escalating to
    ``wedge`` past ``wedge_ratio`` (default 95% — exhaustion is
    imminent/underway). Returns None when the service is inactive
    (``MainPID`` unresolved), /proc is unreadable, or usage is healthy —
    a different probe owns "not running", and a healthy process must be
    silent.
    """
    pid = main_pid if main_pid is not None else _resolve_main_pid(
        service_name, systemctl_path=systemctl_path
    )
    if pid is None:
        note_disposition(
            "fd_exhaustion", "indeterminate",
            reason="MainPID unresolved; service inactive or systemctl error",
        )
        return None

    usage = _read_fd_usage(pid, proc_root=proc_root)
    if usage is None:
        note_disposition(
            "fd_exhaustion", "indeterminate",
            reason="fd count/soft limit unreadable from /proc",
        )
        return None
    open_count, soft = usage

    ratio = open_count / soft
    if ratio < degraded_ratio:
        note_disposition("fd_exhaustion", "clean")
        return None

    severity = "wedge" if ratio >= wedge_ratio else "degraded"
    pct = ratio * 100.0
    detail = (
        f"{service_name} (pid {pid}) holds {open_count}/{soft} open file "
        f"descriptors ({pct:.0f}% of soft RLIMIT_NOFILE). Approaching "
        f"[Errno 24] — new sockets/files will fail and the HTTP server "
        f"will stop accepting (Issue #73 fd-leak class). Inspect: "
        f"sudo ls /proc/{pid}/fd | wc -l ; "
        f"sudo ss -tanp | grep pid={pid} | awk '{{print $NF}}' | sort | uniq -c | sort -rn"
    )
    return Signal(
        cls="fd_exhaustion",
        subject=service_name,
        severity=severity,
        detail=detail,
        issue_ref=73,
        extra={
            "pid": pid,
            "open_fds": open_count,
            "soft_limit": soft,
            "ratio": round(ratio, 4),
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: PhoneAPI TCP leak (Issue #75 — #17 contention class, leak form)
# ─────────────────────────────────────────────────────────────────────


DEFAULT_PHONEAPI_LEAK_STATE_PATH = "/var/lib/meshforge/phoneapi_leak_state.json"

# How many CONSECUTIVE 30s ticks the same socket inode must persist before
# firing. The original "2 ticks" assumption false-alarmed on moc1 (2026-06-07):
# a legit demand-collect TCP nodedb sync lives 1-4 MINUTES (2-8 ticks), so the
# probe flapped NEW/CLEARED every few minutes on rotating per-collect sockets.
# A real leaked TCPInterface persists for hours — 20 ticks (~10 min) silences
# the slow-collect class with 2.5x margin while still catching a leak fast.
DEFAULT_PHONEAPI_LEAK_PERSIST_TICKS = 20


def _pid_socket_inodes(pid: int, *, proc_root: str = "/proc") -> Optional[set]:
    """Socket inodes held by ``pid``'s open fds; None when fd dir unreadable."""
    fd_dir = Path(proc_root) / str(pid) / "fd"
    inodes: set = set()
    try:
        entries = list(os.scandir(fd_dir))
    except OSError:
        return None
    for entry in entries:
        try:
            target = os.readlink(entry.path)
        except OSError:
            continue  # fd closed between scandir and readlink
        if target.startswith("socket:["):
            try:
                inodes.add(int(target[8:-1]))
            except ValueError:
                continue
    return inodes


def _estab_inodes_to_port(port: int, *, proc_root: str = "/proc") -> Optional[set]:
    """Inodes of ESTABLISHED TCP sockets whose REMOTE port is ``port``.

    Parses ``/proc/net/tcp`` + ``tcp6`` directly (fields: rem_address
    hex ``ip:port`` at [2], state at [3] — ``01`` = ESTABLISHED, inode
    at [9]). Read-only and dependency-free — works inside the hardened
    watchdog sandbox where ``ss``/sudo escalation is blocked.

    Returns ``None`` when NEITHER table could be read — sockets were
    UNOBSERVABLE, which must never be conflated with "read and empty"
    (honest_failure_modes #1/#2). A table read successfully but empty
    still contributes to a real (possibly empty) set.
    """
    inodes: set = set()
    tables_read = 0
    for name in ("tcp", "tcp6"):
        path = Path(proc_root) / "net" / name
        try:
            lines = path.read_text().splitlines()[1:]
        except OSError:
            continue
        tables_read += 1
        for line in lines:
            fields = line.split()
            if len(fields) < 10:
                continue
            try:
                rem_port = int(fields[2].rsplit(":", 1)[1], 16)
                state = fields[3]
                inode = int(fields[9])
            except (ValueError, IndexError):
                continue
            if state == "01" and rem_port == port:
                inodes.add(inode)
    if tables_read == 0:
        return None
    return inodes


def _load_phoneapi_leak_state(state_path: str) -> Tuple[Optional[int], dict]:
    """Read ``(pid, {inode: consecutive_ticks})`` from last tick.

    Back-compat: the pre-2026-06-07 format stored a bare ``inodes`` list —
    treat each as count 1 (one prior sighting), so an upgrade mid-flight
    never spuriously fires. Any error → (None, empty) — conservative: an
    unreadable state suppresses a fire, never causes one.
    """
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        counts = data.get("inode_counts")
        if counts is not None:
            return data.get("pid"), {int(i): int(c) for i, c in counts.items()}
        # Legacy format: {"pid": N, "inodes": [...]}
        return data.get("pid"), {int(i): 1 for i in data.get("inodes", [])}
    except (OSError, ValueError, TypeError, AttributeError):
        return None, {}


def _save_phoneapi_leak_state(state_path: str, pid: int, inode_counts: dict) -> None:
    """Persist this tick's per-inode consecutive-tick counts
    (atomic-rename, never raises)."""
    try:
        parent = os.path.dirname(state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"pid": int(pid),
                       "inode_counts": {str(i): int(c)
                                        for i, c in sorted(inode_counts.items())}},
                      fh, separators=(",", ":"))
        os.replace(tmp, state_path)
    except OSError:
        pass


def _fetch_persistent_owner(
    status_port: int, timeout: float = 3.0,
) -> Tuple[bool, Optional[str]]:
    """``(found, owner)`` from the map's ``/api/radio/status``.

    ``found=False`` when the endpoint is unreachable/unparseable — the
    probe then stays silent (``http_local`` owns a dark map service).
    """
    url = f"http://127.0.0.1:{status_port}/api/radio/status"
    try:
        with urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return True, data.get("persistent_owner")
    except (URLError, OSError, ValueError):
        return False, None


DEFAULT_VSZ_LEAK_THRESHOLD_GB = 768.0


def _read_vm_size_gb(
    pid: int, *, proc_root: str = "/proc",
) -> Optional[Tuple[float, Optional[int]]]:
    """Return ``(VmSize in GB, Threads)`` for ``pid`` or None.

    Parsed from ``/proc/<pid>/status`` (world-readable, no sudo — sandbox
    safe). Returns None on any read/parse failure so an unreadable target
    never alarms (a different probe owns "not running")."""
    status_path = Path(proc_root) / str(pid) / "status"
    try:
        text = status_path.read_text()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    vm_kb = None
    threads = None
    for line in text.splitlines():
        if line.startswith("VmSize:"):
            m = re.search(r"(\d+)\s*kB", line)
            if m:
                vm_kb = int(m.group(1))
        elif line.startswith("Threads:"):
            m = re.search(r"(\d+)", line)
            if m:
                threads = int(m.group(1))
    if vm_kb is None:
        return None
    return vm_kb / (1024.0 * 1024.0), threads


def probe_meshtasticd_vsz_leak(
    *,
    service_name: str = "meshtasticd",
    proc_root: str = "/proc",
    systemctl_path: str = "systemctl",
    threshold_gb: float = DEFAULT_VSZ_LEAK_THRESHOLD_GB,
    main_pid: Optional[int] = None,
) -> Optional[Signal]:
    """Guard the band-aid for upstream meshtastic/firmware#10468.

    meshtasticd on Pi 5 + USB radio leaks the pthread stacks of exited
    threads (~8.06 MB VSZ each, ~9-10 spawns/min): the operator's own
    2026-05-13 upstream report, RE-CONFIRMED live 2026-07-10 on both fleet
    Pi 5 boxes at 2.7.24.58 (~561 GB VSZ / 71k anon maps after 5 days). RSS
    stays bounded so nothing else notices until address space or swap dies
    (upstream saw VmPeak ~915 GB + VmSwap growth). The fleet's mitigation is
    the weekly ``meshtasticd-restart.timer`` — WHICH NOTHING WATCHED: if the
    timer silently dies, VSZ grows unbounded and the failure surfaces days
    later as mmap/OOM weirdness.

    This probe is calibrated to "the weekly restart missed", NOT "the leak
    exists": at the measured ~110 GB/day leak rate a healthy weekly cadence
    peaks near ~780 GB, so the default 768 GB threshold fires only when the
    restart is overdue or the leak rate materially worsened — both worth a
    page. Leaking-but-managed boxes stay silent (no alert fatigue); Pi 4 /
    SPI boxes idle near 0.3 GB and can never trip it.

    Read-only, no sudo (``/proc/<pid>/status`` is world-readable). None when
    the service is not running (another probe owns that) or /proc is
    unreadable — unobservable is silent here, never "healthy-shaped" data.
    Severity ``degraded``: the daemon still serves; the pin is remediation
    lead time. Fix: ``systemctl restart meshtasticd`` then verify
    ``systemctl list-timers meshtasticd-restart.timer``. Documented inline
    (persistent_issues MF012 cap precedent); upstream ref in detail.
    """
    pid = main_pid if main_pid is not None else _resolve_main_pid(
        service_name, systemctl_path=systemctl_path
    )
    if pid is None:
        note_disposition(
            "meshtasticd_vsz_leak", "indeterminate",
            reason="meshtasticd MainPID unresolved; service_inactive owns that",
        )
        return None

    reading = _read_vm_size_gb(pid, proc_root=proc_root)
    if reading is None:
        note_disposition(
            "meshtasticd_vsz_leak", "indeterminate",
            reason="VmSize unreadable from /proc/<pid>/status",
        )
        return None
    vsz_gb, threads = reading

    if vsz_gb < threshold_gb:
        note_disposition("meshtasticd_vsz_leak", "clean")
        return None

    detail = (
        f"{service_name} (pid {pid}) VSZ is {vsz_gb:.0f} GB — past the "
        f"{threshold_gb:.0f} GB envelope of the weekly restart mitigation for "
        f"the Pi5/USB pthread-stack leak (meshtastic/firmware#10468). Either "
        f"meshtasticd-restart.timer missed or the leak rate worsened. "
        f"Live threads: {threads if threads is not None else 'unknown'}. "
        f"Fix: systemctl restart {service_name}; then verify the timer: "
        f"systemctl list-timers meshtasticd-restart.timer"
    )
    return Signal(
        cls="meshtasticd_vsz_leak",
        subject=service_name,
        severity="degraded",
        detail=detail,
        extra={"vsz_gb": round(vsz_gb, 1), "threads": threads,
               "threshold_gb": threshold_gb},
    )


def probe_phoneapi_tcp_leak(
    service_name: str = "meshforge-map.service",
    *,
    phoneapi_port: int = 4403,
    status_port: int = 5000,
    proc_root: str = "/proc",
    systemctl_path: str = "systemctl",
    state_path: Optional[str] = None,
    owner_fetch=None,
    main_pid: Optional[int] = None,
    persist_ticks: int = DEFAULT_PHONEAPI_LEAK_PERSIST_TICKS,
) -> Optional[Signal]:
    """Detect a leaked TCPInterface to meshtasticd's PhoneAPI (:4403).

    Issue #75 (2026-06-07, moc1): something inside the map service
    created a raw ``TCPInterface`` and never closed it — outside the
    connection manager's accounting (``/api/radio/status`` reported
    ``persistent_owner: null``) — and its reader thread silently
    competed with the meshtasticd :9443 web client for the incoming
    packet stream. Operator-visible shape: web client shows no inbound
    texts and no delivery ACKs while the radio journal proves RX is
    healthy. The #17 contention class in leak form; cost an evening of
    archaeology because nothing alarmed.

    Detection (read-only, sandbox-safe, no ``ss``/sudo):

    1. The service's MainPID holds an ESTABLISHED TCP socket whose
       remote port is ``phoneapi_port`` (``/proc/net/tcp*`` inode ∩
       ``/proc/<pid>/fd`` socket inodes).
    2. The SAME socket inode (same pid) has persisted for at least
       ``persist_ticks`` CONSECUTIVE ticks (~10 min at the 30s cadence).
       The original "survives 2 ticks" rule false-alarmed on moc1
       (2026-06-07): a legit demand-collect TCP nodedb sync lives 1-4
       minutes — well past two ticks — so rotating per-collect sockets
       flapped the signal NEW/CLEARED every few minutes. A real leak
       persists hours; the higher bar keeps detection fast while never
       firing on the slow-collect class.
    3. The map's own ``/api/radio/status`` reports
       ``persistent_owner: null`` — an ACCOUNTED owner (e.g. the
       message listener's documented TCP fallback) is a known tradeoff
       that the listener already warns about, not a leak.

    Returns None when the service is inactive (``service_inactive``
    owns that), /proc is unreadable, no candidate socket exists, the
    socket is first-seen this tick, the owner is accounted, or the
    status endpoint is unreachable (``http_local`` owns a dark map).

    Recovery: ``sudo systemctl restart <service>`` releases the stolen
    stream instantly; the web client recovers on its next poll.
    """
    pid = main_pid if main_pid is not None else _resolve_main_pid(
        service_name, systemctl_path=systemctl_path
    )
    if pid is None:
        note_disposition(
            "phoneapi_tcp_leak", "indeterminate",
            reason="MainPID unresolved; service inactive or systemctl error",
        )
        return None

    sp = state_path or DEFAULT_PHONEAPI_LEAK_STATE_PATH

    pid_inodes = _pid_socket_inodes(pid, proc_root=proc_root)
    if pid_inodes is None:
        note_disposition(
            "phoneapi_tcp_leak", "indeterminate",
            reason="/proc/<pid>/fd unreadable; socket inodes unobservable",
        )
        return None
    estab_inodes = _estab_inodes_to_port(phoneapi_port, proc_root=proc_root)
    if estab_inodes is None:
        # /proc/net/tcp{,6} unreadable — established sockets UNOBSERVABLE.
        # HOLD: return without rewriting the persisted inode counts, so a
        # blind tick can neither reset nor advance the persist-ticks
        # debounce (the observation failed; nothing was learned).
        note_disposition(
            "phoneapi_tcp_leak", "indeterminate",
            reason="proc/net tcp tables unreadable — sockets unobservable",
        )
        return None
    candidates = pid_inodes & estab_inodes

    prev_pid, prev_counts = _load_phoneapi_leak_state(sp)
    # Consecutive-tick count per inode: +1 if seen last tick (same pid),
    # reset to 1 on a fresh inode or a service restart (pid change).
    counts = {
        inode: (prev_counts.get(inode, 0) + 1 if prev_pid == pid else 1)
        for inode in candidates
    }
    _save_phoneapi_leak_state(sp, pid, counts)

    if not candidates:
        note_disposition("phoneapi_tcp_leak", "clean")
        return None
    persisted = {i for i, c in counts.items() if c >= persist_ticks}
    if not persisted:
        note_disposition(
            "phoneapi_tcp_leak", "indeterminate",
            reason="candidate socket under persist-ticks debounce",
        )
        return None  # in-flight collect (lives minutes) — not yet a leak

    fetch = owner_fetch or (lambda: _fetch_persistent_owner(status_port))
    found, owner = fetch()
    if not found:
        note_disposition(
            "phoneapi_tcp_leak", "indeterminate",
            reason="radio status endpoint unreachable; http_local owns that",
        )
        return None  # status endpoint dark — http_local owns that
    if owner:
        note_disposition("phoneapi_tcp_leak", "clean")
        return None  # accounted persistent connection (listener TCP fallback)

    inode_list = sorted(persisted)
    max_ticks = max(counts[i] for i in persisted)
    detail = (
        f"{service_name} (pid {pid}) holds an UNACCOUNTED persistent TCP "
        f"connection to meshtasticd :{phoneapi_port} (socket inode(s) "
        f"{inode_list} persisted {max_ticks} consecutive ticks "
        f"(threshold {persist_ticks}), persistent_owner=null) — "
        f"a leaked TCPInterface whose reader thread silently starves the "
        f":9443 web client of inbound texts and delivery ACKs (Issue #75, "
        f"#17 contention class). Recover: sudo systemctl restart "
        f"{service_name}"
    )
    return Signal(
        cls="phoneapi_tcp_leak",
        subject=service_name,
        severity="degraded",
        detail=detail,
        issue_ref=75,
        extra={
            "pid": pid,
            "phoneapi_port": phoneapi_port,
            "leaked_inodes": inode_list,
            "persisted_ticks": max_ticks,
            "persist_ticks_threshold": persist_ticks,
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: meshtasticd PhoneAPI wedge (2026-06-15 — #17/#75 contention,
# churn form; the 2026-06-13→15 moc mesh-TX wedge)
# ─────────────────────────────────────────────────────────────────────


DEFAULT_PHONEAPI_WEDGE_STATE_PATH = (
    "/var/lib/meshforge/phoneapi_wedge_debounce.json"
)

# The signature line meshtasticd logs whenever a NEW :4403 connection
# arrives while one is still open — i.e. the single-consumer PhoneAPI
# is being CONTENDED by ≥2 overlapping clients (#17). Steady-state with
# one persistent consumer = ~0 of these (moc Jun 12 = 0); the 06-13→15
# contention incident logged thousands/day (~1.5-2.5/min).
PHONEAPI_FORCE_CLOSE_PATTERN = "Force close previous TCP connection"

# ~12 force-closes over the 10-min default lookback (~1.2/min) is the
# floor for SUSTAINED churn — comfortably above the single-consumer 0
# steady state, comfortably below the incident's ~1.5-2.5/min, so a
# stray reconnect or a momentary two-client overlap during a deploy
# never crosses it while genuine contention does.
DEFAULT_PHONEAPI_WEDGE_THRESHOLD = 12


def _load_phoneapi_wedge_streak(state_path: str) -> int:
    """Read the consecutive-over-threshold streak. Any error → 0.

    A missing/unreadable/garbage state means 'no confirmed streak yet',
    which suppresses a first-seen burst — the conservative direction the
    debounce wants (favour silence on uncertainty, not a false page).
    """
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            streak = int(json.load(fh).get("streak", 0))
        return streak if streak >= 0 else 0
    except (OSError, ValueError, TypeError):
        return 0


def _save_phoneapi_wedge_streak(state_path: str, streak: int) -> None:
    """Persist the streak counter (atomic-rename, never raises)."""
    try:
        parent = os.path.dirname(state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"streak": int(streak)}, fh, separators=(",", ":"))
        os.replace(tmp, state_path)
    except OSError:
        pass


def probe_meshtasticd_phoneapi_wedge(
    *,
    unit: str = "meshtasticd.service",
    lookback: str = "10min",
    threshold: int = DEFAULT_PHONEAPI_WEDGE_THRESHOLD,
    journalctl_path: str = "journalctl",
    systemctl_path: str = "systemctl",
    main_pid: Optional[int] = None,
    gateway_main_pid: Optional[int] = None,
    gateway_unit: str = "meshforge-gateway.service",
    count_fn=None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
    phoneapi_port: int = 4403,
    proc_root: str = "/proc",
) -> Optional[Signal]:
    """Fire when meshtasticd's PhoneAPI (:4403) is thrashed by ≥2 contenders.

    The 2026-06-13→15 moc incident: the gateway's mesh-TX silently wedged
    for ~2 days. meshtasticd's PhoneAPI is a SINGLE-consumer stream (#17) —
    when a SECOND consumer (e.g. the map's ``_collect_meshtasticd`` :4403
    source) opens a connection while one is still held, meshtasticd logs
    ``Force close previous TCP connection`` and tears the prior one down.
    Two consumers reconnecting against each other produce sustained churn
    (~1.5-2.5/min in the incident vs ~0/min steady-state with one
    persistent consumer). Under that thrash the gateway's
    stateless-HTTP-protobuf mesh-TX path can never hold the radio long
    enough to send: **the bot keeps producing output but it stops reaching
    nodes — and NOTHING alarmed, because the RNS round-trip canary measures
    the RNS leg, not mesh-RF.** This probe watches the one thing that goes
    loud during the contention: the force-close churn in meshtasticd's own
    journal.

    Detection (journal-only, sandbox-safe — never opens a :4403 connection,
    which would itself BE a contender, the #17 trap):

    1. Count ``Force close previous TCP connection`` lines in meshtasticd's
       journal over ``lookback`` (default 10 min). ``< threshold`` (default
       12, ~1.2/min) → return None: not sustained churn.
    2. **2-tick debounce** (mirrors ``probe_parity_drift`` /
       ``probe_role_drift``): a brief two-client overlap during a deploy or
       a stray reconnect produces one over-threshold tick that self-heals;
       a streak persisted to ``state_path`` requires ``debounce_ticks``
       CONSECUTIVE over-threshold ticks before firing, so a transient burst
       can't flap the signal. Any below-threshold OR unobservable tick
       resets the streak.
    3. **Held-contention harm guard** (2026-06-27 moc): fire only when a
       contender is HOLDING the radio — a currently-ESTABLISHED :4403 client
       connection (``_estab_inodes_to_port``, read-only /proc/net/tcp). Brief
       connect+close touches (e.g. ``fleet_snapshot._probe_radio``'s status
       probe) also emit force-close lines but release in <100ms and never
       starve mesh-TX (the moc benign case: real churn, healthy TX). A real
       radio-monopolising wedge can't exist without a held connection, so this
       separates harm from noise without masking a true wedge.

    Self-guards (return None — never read unobservable as healthy):

    - meshtasticd inactive (``_resolve_main_pid`` → None; ``service_inactive``
      owns that), or
    - the journal count is None (journalctl timeout/unavailable —
      *unobservable* ≠ 0; absorbing it as 0 would mask the wedge,
      honest_failure_modes #1/#2). The streak is reset on this path so an
      observability gap never counts toward a fire.

    Recovery: ``sudo systemctl restart meshtasticd`` clears the wedge
    immediately; then stop the 2nd consumer so it can't recur — typically
    the map's ``_collect_meshtasticd`` :4403 source.
    """
    pid = main_pid if main_pid is not None else _resolve_main_pid(
        unit, systemctl_path=systemctl_path
    )
    if pid is None:
        note_disposition(
            "meshtasticd_phoneapi_wedge", "indeterminate",
            reason="meshtasticd MainPID unresolved; service_inactive owns that",
        )
        return None

    # Gate: the wedge only threatens a GATEWAY's mesh-TX. On a box with no
    # gateway, :4403 churn is the map's own reconnect overhead (e.g. moc5, a
    # collector) — NOT bot-dark — and firing the "gateway mesh-TX wedged" class
    # there mislabels it (honest_failure_modes: a signal that says the wrong
    # thing). Only meaningful where meshforge-gateway runs.
    gw_pid = gateway_main_pid if gateway_main_pid is not None else _resolve_main_pid(
        gateway_unit, systemctl_path=systemctl_path
    )
    if gw_pid is None:
        # NOTE: a None gw_pid conflates "no gateway unit on this box" (the
        # overwhelmingly common, legitimate case — inert) with "systemctl
        # errored" (unit state unobservable). A genuinely dead gateway
        # pages via service_inactive, so inert is kept; the reason stays
        # honest about the conflation.
        note_disposition(
            "meshtasticd_phoneapi_wedge", "inert",
            reason=(
                "no running gateway (or unit state unobservable); "
                "class gated to gateway boxes"
            ),
        )
        return None

    sp = state_path or DEFAULT_PHONEAPI_WEDGE_STATE_PATH

    if count_fn is None:
        def count_fn(pattern: str) -> Optional[int]:
            return _journal_count_match(
                unit, pattern, lookback, journalctl_path=journalctl_path
            )

    count = count_fn(PHONEAPI_FORCE_CLOSE_PATTERN)
    if count is None:
        # Unobservable (journalctl wedged/absent) — NEVER read as 0/healthy.
        # Break the streak so a blind tick can't carry toward a fire.
        _save_phoneapi_wedge_streak(sp, 0)
        note_disposition(
            "meshtasticd_phoneapi_wedge", "indeterminate",
            reason="journalctl unavailable/timeout; force-close count unobservable",
        )
        return None
    if count < threshold:
        _save_phoneapi_wedge_streak(sp, 0)  # not sustained → streak broken
        note_disposition("meshtasticd_phoneapi_wedge", "clean")
        return None

    # Harm guard (2026-06-27 moc): force-close churn wedges mesh-TX only when a
    # contender is HOLDING the radio — a sustained ESTABLISHED :4403 client
    # connection. Brief connect+close touches (e.g. fleet_snapshot._probe_radio's
    # status connect_ex) ALSO emit "Force close previous TCP connection" lines but
    # release the radio in <100ms, so the gateway's mesh-TX is never starved (the
    # moc benign case: 40/10min churn, mesh-TX healthy). A real radio-monopolising
    # wedge MUST hold a :4403 connection (you can't monopolise the radio with
    # sub-100ms touches), so a held connection reliably separates harm from noise.
    # _estab_inodes_to_port reads world-readable /proc/net/tcp{,6} — an EMPTY
    # set means the tables were read and genuinely show no held contender
    # (suppressing is safe); None means NEITHER table was readable — sockets
    # unobservable, which must not execute the benign-churn streak reset
    # (hold: a blind tick neither confirms nor breaks the streak).
    estab_inodes = _estab_inodes_to_port(phoneapi_port, proc_root=proc_root)
    if estab_inodes is None:
        note_disposition(
            "meshtasticd_phoneapi_wedge", "indeterminate",
            reason="proc/net tcp tables unreadable — sockets unobservable",
        )
        return None
    if not estab_inodes:
        _save_phoneapi_wedge_streak(sp, 0)  # brief-touch churn → benign; reset
        note_disposition("meshtasticd_phoneapi_wedge", "clean")
        return None

    streak = _load_phoneapi_wedge_streak(sp) + 1
    _save_phoneapi_wedge_streak(sp, streak)
    if streak < debounce_ticks:
        note_disposition(
            "meshtasticd_phoneapi_wedge", "indeterminate",
            reason="over-threshold churn held by debounce; awaiting confirm tick",
        )
        return None  # over threshold once — wait for a confirming tick

    detail = (
        f"meshtasticd PhoneAPI (:4403) is being thrashed by ≥2 contending "
        f"single-consumers — {count} '{PHONEAPI_FORCE_CLOSE_PATTERN}' lines "
        f"in the last {lookback} (threshold {threshold}), confirmed over "
        f"{streak} consecutive ticks. The single-consumer :4403 (#17/#75 "
        f"contention class) is being torn open/closed, so the gateway's "
        f"stateless-HTTP-protobuf mesh-TX wedges: bot output stops reaching "
        f"nodes while the RNS round-trip canary stays green (the mesh-RF leg "
        f"is unwatched — the 2026-06-13→15 moc incident). Recover: sudo "
        f"systemctl restart meshtasticd (clears the wedge), then stop the "
        f"2nd consumer — typically the map's _collect_meshtasticd :4403 "
        f"source — so it can't recur."
    )
    return Signal(
        cls="meshtasticd_phoneapi_wedge",
        subject="meshtasticd",
        severity="degraded",
        detail=detail,
        issue_ref=None,
        extra={
            "force_close_count": count,
            "lookback": lookback,
            "threshold": threshold,
            "debounce_streak": streak,
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: service inactive (general)
# ─────────────────────────────────────────────────────────────────────


def probe_service_inactive(
    service_name: str,
    *,
    expected_state: str = "active",
    systemctl_path: str = "systemctl",
) -> Optional[Signal]:
    """``systemctl is-active <service>`` differs from expected.

    Skips the probe entirely (returns None) for units in
    ``inactive`` if ``expected_state == "inactive"`` — supports
    operator-disabled units (e.g. moc3 is gateway-only per memory and
    its meshforge-map.service is intentionally inactive).
    """
    try:
        proc = subprocess.run(
            [systemctl_path, "is-active", service_name],
            capture_output=True, text=True, timeout=3,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        note_disposition(
            "service_inactive", "indeterminate",
            reason="systemctl unavailable/timeout; unit state unobservable",
        )
        return None
    actual = proc.stdout.strip() or "unknown"
    if actual == expected_state:
        note_disposition("service_inactive", "clean")
        return None
    if actual == "inactive" and expected_state == "active":
        severity = "degraded"
    elif actual == "failed":
        severity = "wedge"
    else:
        severity = "degraded"
    return Signal(
        cls="service_inactive",
        subject=service_name,
        severity=severity,
        detail=(
            f"systemctl is-active reports {actual!r}, expected "
            f"{expected_state!r}. Check `systemctl status {service_name}` "
            f"and journal."
        ),
        extra={"actual": actual, "expected": expected_state},
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: NomadNet user-unit crashloop (2026-06-19; the 10-day-silent class)
# ─────────────────────────────────────────────────────────────────────

# A SHORT live window, not a long one: the journal still holds the PRE-fix
# crashloop lines for a while after a remediation, so a 2h window would
# false-page right after every fix. A 15min window + a count floor gates
# "it's a LOOP not a one-off restart"; the newest-restart recency gate makes
# it "it's LIVE not history" — a just-fixed unit's newest restart ages past
# recency within minutes → INERT. (Tuned against the 2026-06-19 manager-box
# fix, whose 15-min restart count fell to 0 once settled, while the 2h window
# still showed 19 pre-fix restarts.)
NOMADNET_CRASHLOOP_LOOKBACK = "15min"
NOMADNET_CRASHLOOP_DEGRADED_N = 3      # ≥3 restarts in the window = looping
NOMADNET_CRASHLOOP_WEDGE_N = 8         # ≥8 = hard continuous loop (the 7842 class)
NOMADNET_CRASHLOOP_RECENCY_S = 300.0   # newest restart ≤5min old = LIVE, not pre-fix history
DEFAULT_NOMADNET_CRASHLOOP_STATE = (
    "/var/lib/meshforge/nomadnet_crashloop_debounce.json")


def _journal_user_unit_restart_ts(
    user_unit: str,
    pattern: str,
    lookback: str,
    journalctl_path: str = "journalctl",
) -> Optional[List[float]]:
    """Epoch timestamps of ``USER_UNIT=<user_unit>`` journal lines matching
    ``pattern`` within ``lookback``.

    The core ``_journal_*`` helpers hardcode ``-u <unit>`` (the SYSTEM journal
    namespace), which is **structurally blind to user units** — from the
    watchdog's root/system context a user unit returns rc 0 but EMPTY (this is
    half of why the 10-day crashloop went silent). Root must select user-unit
    logs via the ``USER_UNIT=`` journal field (a direct journal read — no sudo,
    no user-bus — so it works under the watchdog NoNewPrivileges sandbox).

    Returns the parsed epoch list (``[]`` = genuinely no restarts), or
    **None** on journalctl unavailable / timeout / rc∉(0,1) — the honest
    *unobservable* answer. A probe must NEVER read None as ``[]`` (empty ≠
    error — honest_failure_modes #1), or a journalctl wedge would mask the
    very crashloop this counts.
    """
    try:
        proc = subprocess.run(
            [
                journalctl_path, "-q", f"USER_UNIT={user_unit}",
                "--since", f"-{lookback}", "-g", pattern,
                "-o", "short-unix", "--no-pager",
            ],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode not in (0, 1):
        return None
    out = proc.stdout
    if not out:
        return []
    ts: List[float] = []
    for ln in out.splitlines():
        if not ln:
            continue
        parsed = _short_unix_ts(ln)
        if parsed is not None:
            ts.append(parsed)
    return ts


def _load_nomadnet_crashloop_streak(state_path: str) -> int:
    """Consecutive-over-threshold streak; any error → 0 (favour silence)."""
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            streak = int(json.load(fh).get("streak", 0))
        return streak if streak >= 0 else 0
    except (OSError, ValueError, TypeError):
        return 0


def _save_nomadnet_crashloop_streak(state_path: str, streak: int) -> None:
    """Persist the streak counter (atomic-rename, never raises).

    On a persistent write failure the debounce streak can never advance past
    1 (``_load`` reads 0 each tick) → the probe would silently NEVER fire
    during a real crashloop. So a swallowed ``OSError`` leaves a WITNESS in
    the watchdog journal (honest_failure_modes #9) rather than vanishing — a
    write-only failure here is itself a signal worth grepping.
    """
    try:
        parent = os.path.dirname(state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"streak": int(streak)}, fh, separators=(",", ":"))
        os.replace(tmp, state_path)
    except OSError as exc:
        logger.warning(
            "nomadnet_crashloop: could not persist debounce streak to %s "
            "(%s) — the probe may not advance past its debounce floor; "
            "check %s is writable.",
            state_path, exc, os.path.dirname(state_path) or state_path,
        )


def probe_nomadnet_crashloop(
    *,
    user_unit: str = "nomadnet.service",
    lookback: str = NOMADNET_CRASHLOOP_LOOKBACK,
    degraded_n: int = NOMADNET_CRASHLOOP_DEGRADED_N,
    wedge_n: int = NOMADNET_CRASHLOOP_WEDGE_N,
    recency_s: float = NOMADNET_CRASHLOOP_RECENCY_S,
    ts_fn=None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
    journalctl_path: str = "journalctl",
    now: Optional[float] = None,
) -> Optional[Signal]:
    """Fire when the NomadNet USER unit is crashlooping — the 2026-06-19
    class (NRestarts=7842, stuck ``activating (start-pre)``, exit 75 from the
    rnstatus boot-gate; undetected for 10 days).

    ``probe_service_inactive`` is BLIND here: from the watchdog's root/system
    context ``systemctl`` cannot see a user unit at all, and a unit thrashing
    in auto-restart is neither ``inactive`` nor ``failed``. The only
    root-readable signal is systemd's ``restart counter is at N`` line under
    the ``USER_UNIT=`` journal field. We count those in a SHORT window and gate
    on the newest being RECENT, so a LIVE loop fires but post-fix history does
    not (a 2h window would false-page right after every remediation).

    Self-guards None (INERT): zero/too-few restart lines (healthy, or the
    nomadnet-disabled box — moc5), a newest restart older than ``recency_s``
    (a loop that already stopped — e.g. just remediated), or journalctl
    unavailable/timeout (unobservable ≠ healthy — never read None as 0).
    2-tick debounce rides out a single tick landing mid-restart. Never raises.

    KNOWN BOUNDARY (calibrated — do not overclaim): this is a LIVE-loop
    detector. The slow exit-75 boot-gate loop (the #82 case) restarts forever
    (~125 s/cycle) and is covered continuously. The FAST exit-87 (rpc_key)
    path instead trips ``StartLimitBurst`` and PARKS the unit in ``failed``
    after a ~30 s burst — this probe catches that burst and fires ONCE within
    the ``recency_s`` window, then goes INERT. A steadily *parked-failed* user
    unit has no steady-state detector here: ``probe_service_inactive`` is
    blind to user units and the sandbox forbids the user-bus state query that
    would see ``failed``. The single fire (+ the ``propose_escalation``
    companion rule surfacing it in the brief) is the coverage for that case.
    """
    try:
        now = time.time() if now is None else now
        sp = state_path or DEFAULT_NOMADNET_CRASHLOOP_STATE
        pat = "restart counter is at"

        if ts_fn is None:
            def ts_fn(p):
                return _journal_user_unit_restart_ts(
                    user_unit, p, lookback, journalctl_path=journalctl_path)
        tslist = ts_fn(pat)

        if tslist is None:
            # unobservable — hold; do NOT reset the streak to a healthy 0.
            note_disposition(
                "nomadnet_crashloop", "indeterminate",
                reason="journalctl unavailable/timeout; restarts unobservable",
            )
            return None

        n = len(tslist)
        newest = max(tslist) if tslist else None
        live = (
            n >= degraded_n
            and newest is not None
            and (now - newest) <= recency_s
        )
        if not live:
            _save_nomadnet_crashloop_streak(sp, 0)   # below thresh / stale → INERT
            note_disposition(
                "nomadnet_crashloop", "inert",
                reason="no live restart loop (healthy, remediated, or no nomadnet)",
            )
            return None

        # Clamp at the debounce floor: once confirmed, a sustained loop fires
        # every tick (streak == debounce_ticks >= debounce_ticks) without the
        # persisted counter growing unbounded across a 7842-class loop, and a
        # torn/edited file with an absurd value can't be trusted as-is.
        streak = min(_load_nomadnet_crashloop_streak(sp) + 1, debounce_ticks)
        _save_nomadnet_crashloop_streak(sp, streak)
        if streak < debounce_ticks:
            note_disposition(
                "nomadnet_crashloop", "indeterminate",
                reason="live loop candidate held by debounce; awaiting confirm",
            )
            return None

        severity = "wedge" if n >= wedge_n else "degraded"
        age_min = (now - newest) / 60.0
        return Signal(
            cls="nomadnet_crashloop",
            subject=user_unit,
            severity=severity,
            detail=(
                f"NomadNet user unit crashlooping — {n} systemd restarts in "
                f"the last {lookback} (newest {age_min:.0f} min ago). The unit "
                f"is stuck auto-restarting — likely exit 75 from the rnstatus "
                f"boot-gate (rnsd not hosting), or exit 87 rpc_key mismatch. "
                f"probe_service_inactive is blind to user units. Check: "
                f"`journalctl --user -u {user_unit} -n 50` and "
                f"`systemctl --user status {user_unit}`. Recovery: fix the "
                f"start-pre gate / rpc_key, then "
                f"`systemctl --user restart {user_unit}`. "
                f"(The 2026-06-19 NRestarts=7842, 10-day-silent class.)"
            ),
            extra={
                "restarts": n, "lookback": lookback,
                "newest_age_s": round(now - newest, 1),
                "streak": streak, "degraded_n": degraded_n, "wedge_n": wedge_n,
            },
        )
    except Exception:
        note_disposition(
            "nomadnet_crashloop", "indeterminate",
            reason="probe raised unexpectedly; unobservable this tick",
        )
        return None



# ─────────────────────────────────────────────────────────────────────
# user_unit_inactive — enabled user .service daemons that are NOT running
# (the parked-failed / stopped / user-manager-down class, 2026-07-19)
# ─────────────────────────────────────────────────────────────────────

DEFAULT_USER_UNIT_INACTIVE_STATE = \
    "/var/lib/meshforge/user_unit_inactive_debounce.json"


def _enabled_user_services(user_home: str) -> Optional[set]:
    """User ``.service`` names enrolled always-on for the operator: symlinks
    in ``~/.config/systemd/user/default.target.wants/``. Only that target —
    nested ``*.service.wants/`` and other targets activate conditionally and
    would false-positive. ``None`` = the wants dir is unreadable (distinct
    from an empty enrollment). Timers are deliberately OUT of scope: they
    carry no invocation marker (verified 2026-07-19) and their staleness is
    the schedules/SLO layer's job."""
    wants = os.path.join(user_home, ".config", "systemd", "user",
                         "default.target.wants")
    if not os.path.isdir(wants):
        # No enrollment dir at all — a box with no always-on user daemons.
        return set()
    try:
        return {n for n in os.listdir(wants) if n.endswith(".service")}
    except OSError:
        return None


def _active_user_units(runtime_dir: str) -> Optional[set]:
    """Unit names with a live invocation marker under
    ``/run/user/<uid>/systemd/units/`` (``invocation:<unit>`` symlinks exist
    exactly while the unit is active — root-readable, no user bus needed).
    ``None`` = the user manager's runtime dir is absent/unreadable, which is
    NOT "nothing active" — it usually means the user manager itself is down
    (the #79 linger lesson) and is judged by the caller."""
    units_dir = os.path.join(runtime_dir, "systemd", "units")
    try:
        return {n[len("invocation:"):] for n in os.listdir(units_dir)
                if n.startswith("invocation:")}
    except OSError:
        return None


def probe_user_unit_inactive(
    *,
    operator=None,
    user_home: Optional[str] = None,
    runtime_dir: Optional[str] = None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """Fire when an enrolled always-on USER ``.service`` is not running.

    Closes the ``user_unit_inactivity_blind`` structural-dark row:
    ``probe_service_inactive`` cannot see user units from the root/system
    context, and ``probe_nomadnet_crashloop`` covers only a LIVE restart
    loop on one unit — a unit PARKED ``failed`` by StartLimitBurst, stopped
    by hand and forgotten, or dead because the user manager itself is gone
    (linger off / logout) had NO steady-state detector (its own docstring
    says so). Observation is bus-free and root-readable both sides:
    enrollment from ``default.target.wants/*.service`` symlinks on disk,
    liveness from ``invocation:*`` markers in ``/run/user/<uid>/systemd/units``.

    Legs: (1) enabled minus active → per-unit inactivity, degraded;
    (2) enabled non-empty but the user runtime dir absent → the USER MANAGER
    is down while daemons are enrolled (linger off / crashed), degraded.

    Honest self-guards: no resolvable operator → INERT; empty enrollment →
    INERT; wants/units dir unreadable → indeterminate (never "all healthy");
    2-tick debounce rides a deliberate restart window. A live crashloop is
    NOT this probe's fire (invocation marker present while thrashing —
    nomadnet_crashloop owns that mode). Never raises into the tick.
    """
    try:
        sp = state_path or DEFAULT_USER_UNIT_INACTIVE_STATE

        if operator is None and (user_home is None or runtime_dir is None):
            try:
                from utils.fleet_test_runner import _find_operator_user
                operator = _find_operator_user()
            except Exception:
                operator = None
            if operator is None:
                # No live user bus. Fall back to the rnsd service user so the
                # manager-down leg can still see an enrolled-but-dead setup.
                try:
                    from utils.rns_tree_perms import _read_rnsd_user
                    name = _read_rnsd_user()
                    if name and name != "root":
                        import pwd as _pwd
                        rec = _pwd.getpwnam(name)
                        operator = (rec.pw_uid, name)
                except Exception:
                    operator = None
            if operator is None:
                note_disposition("user_unit_inactive", "inert",
                                 reason="no resolvable operator user")
                return None
        if user_home is None or runtime_dir is None:
            uid, name = operator
            import pwd as _pwd
            try:
                home = _pwd.getpwuid(uid).pw_dir
            except KeyError:
                home = f"/home/{name}"
            user_home = user_home or home
            runtime_dir = runtime_dir or f"/run/user/{uid}"

        enabled = _enabled_user_services(user_home)
        if enabled is None:
            _save_nomadnet_crashloop_streak(sp, 0)
            note_disposition("user_unit_inactive", "indeterminate",
                             reason="user wants dir unreadable")
            return None
        if not enabled:
            _save_nomadnet_crashloop_streak(sp, 0)
            note_disposition("user_unit_inactive", "inert",
                             reason="no always-on user services enrolled")
            return None

        active = _active_user_units(runtime_dir)
        if active is None:
            # Enrolled daemons + no user manager runtime = everything dead.
            streak = min(_load_nomadnet_crashloop_streak(sp) + 1,
                         debounce_ticks)
            _save_nomadnet_crashloop_streak(sp, streak)
            if streak < debounce_ticks:
                note_disposition("user_unit_inactive", "indeterminate",
                                 reason="manager-down candidate under debounce")
                return None
            return Signal(
                cls="user_unit_inactive",
                subject="user-manager",
                severity="degraded",
                detail=(
                    f"user systemd manager not running but {len(enabled)} "
                    f"always-on user service(s) are enrolled "
                    f"({', '.join(sorted(enabled))}) — every one of them is "
                    f"dead. Usual cause: linger off after an image/host "
                    f"change, or the manager crashed. Fix: "
                    f"`loginctl enable-linger <operator>` then "
                    f"`systemctl --user daemon-reload` as the operator "
                    f"(the #79 deploy-restart-gap class)."
                ),
                extra={"enabled": sorted(enabled)},
            )

        inactive = sorted(enabled - active)
        if not inactive:
            _save_nomadnet_crashloop_streak(sp, 0)
            note_disposition("user_unit_inactive", "clean")
            return None

        streak = min(_load_nomadnet_crashloop_streak(sp) + 1, debounce_ticks)
        _save_nomadnet_crashloop_streak(sp, streak)
        if streak < debounce_ticks:
            note_disposition("user_unit_inactive", "indeterminate",
                             reason="inactivity candidate under debounce")
            return None

        subj = inactive[0] if len(inactive) == 1 else f"{len(inactive)} units"
        return Signal(
            cls="user_unit_inactive",
            subject=subj,
            severity="degraded",
            detail=(
                f"enrolled always-on user service(s) NOT running: "
                f"{', '.join(inactive)} — enabled in default.target.wants "
                f"but no live invocation marker. Parked `failed` "
                f"(StartLimitBurst), stopped-and-forgotten, or never started "
                f"this boot. Check: `systemctl --user status <unit>`; "
                f"recover: `systemctl --user reset-failed <unit> && "
                f"systemctl --user start <unit>`; if deliberate, "
                f"`systemctl --user disable <unit>` so enrollment matches "
                f"intent."
            ),
            extra={"inactive": inactive, "enabled": sorted(enabled),
                   "streak": streak},
        )
    except Exception:
        note_disposition("user_unit_inactive", "indeterminate",
                         reason="probe raised unexpectedly")
        return None
