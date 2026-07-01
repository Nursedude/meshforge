"""Watchdog probes — local service health failure shapes.

HTTP-local unresponsive (#61/#70), fd exhaustion (#73), PhoneAPI TCP leak
(#75), service-inactive, tracer peer unreachable, channel feed dark.
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
from urllib.error import URLError

from utils.watchdog_probe_core import (
    Signal,
    _journal_count_match,
    _journal_newest_match,
    _resolve_main_pid,
    _short_unix_ts,
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
            # Any response (including 503) means the HTTP server loop
            # is alive. We don't gate on status — /healthz can return
            # 503 during warming and that's correct, not wedged.
            _ = resp.read(64)
            return None
    except socket.timeout:
        pass
    except URLError as exc:
        # ConnectionRefused means port isn't bound — usually that's
        # "service is inactive" not "wedged". Don't double-alarm here.
        if "Connection refused" in str(exc):
            return None
        # DNS/other URLErrors — return None; this probe doesn't
        # speculate on network configuration.
        return None
    except (OSError, ValueError):
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
        return None

    usage = _read_fd_usage(pid, proc_root=proc_root)
    if usage is None:
        return None
    open_count, soft = usage

    ratio = open_count / soft
    if ratio < degraded_ratio:
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


def _estab_inodes_to_port(port: int, *, proc_root: str = "/proc") -> set:
    """Inodes of ESTABLISHED TCP sockets whose REMOTE port is ``port``.

    Parses ``/proc/net/tcp`` + ``tcp6`` directly (fields: rem_address
    hex ``ip:port`` at [2], state at [3] — ``01`` = ESTABLISHED, inode
    at [9]). Read-only and dependency-free — works inside the hardened
    watchdog sandbox where ``ss``/sudo escalation is blocked.
    """
    inodes: set = set()
    for name in ("tcp", "tcp6"):
        path = Path(proc_root) / "net" / name
        try:
            lines = path.read_text().splitlines()[1:]
        except OSError:
            continue
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
        return None

    sp = state_path or DEFAULT_PHONEAPI_LEAK_STATE_PATH

    pid_inodes = _pid_socket_inodes(pid, proc_root=proc_root)
    if pid_inodes is None:
        return None
    candidates = pid_inodes & _estab_inodes_to_port(
        phoneapi_port, proc_root=proc_root
    )

    prev_pid, prev_counts = _load_phoneapi_leak_state(sp)
    # Consecutive-tick count per inode: +1 if seen last tick (same pid),
    # reset to 1 on a fresh inode or a service restart (pid change).
    counts = {
        inode: (prev_counts.get(inode, 0) + 1 if prev_pid == pid else 1)
        for inode in candidates
    }
    _save_phoneapi_leak_state(sp, pid, counts)

    if not candidates:
        return None
    persisted = {i for i, c in counts.items() if c >= persist_ticks}
    if not persisted:
        return None  # in-flight collect (lives minutes) — not yet a leak

    fetch = owner_fetch or (lambda: _fetch_persistent_owner(status_port))
    found, owner = fetch()
    if not found:
        return None  # status endpoint dark — http_local owns that
    if owner:
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
        return None
    if count < threshold:
        _save_phoneapi_wedge_streak(sp, 0)  # not sustained → streak broken
        return None

    # Harm guard (2026-06-27 moc): force-close churn wedges mesh-TX only when a
    # contender is HOLDING the radio — a sustained ESTABLISHED :4403 client
    # connection. Brief connect+close touches (e.g. fleet_snapshot._probe_radio's
    # status connect_ex) ALSO emit "Force close previous TCP connection" lines but
    # release the radio in <100ms, so the gateway's mesh-TX is never starved (the
    # moc benign case: 40/10min churn, mesh-TX healthy). A real radio-monopolising
    # wedge MUST hold a :4403 connection (you can't monopolise the radio with
    # sub-100ms touches), so a held connection reliably separates harm from noise.
    # _estab_inodes_to_port reads world-readable /proc/net/tcp — empty means
    # genuinely no held contender (not unobservable), so suppressing is safe; if
    # /proc were unreadable it would also be empty, but on a healthy box that
    # can't coincide with a real wedge (the holder's own socket would be in it).
    if not _estab_inodes_to_port(phoneapi_port, proc_root=proc_root):
        _save_phoneapi_wedge_streak(sp, 0)  # brief-touch churn → benign; reset
        return None

    streak = _load_phoneapi_wedge_streak(sp) + 1
    _save_phoneapi_wedge_streak(sp, streak)
    if streak < debounce_ticks:
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
        return None
    actual = proc.stdout.strip() or "unknown"
    if actual == expected_state:
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
            return None

        # Clamp at the debounce floor: once confirmed, a sustained loop fires
        # every tick (streak == debounce_ticks >= debounce_ticks) without the
        # persisted counter growing unbounded across a 7842-class loop, and a
        # torn/edited file with an absurd value can't be trusted as-is.
        streak = min(_load_nomadnet_crashloop_streak(sp) + 1, debounce_ticks)
        _save_nomadnet_crashloop_streak(sp, streak)
        if streak < debounce_ticks:
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
        return None


# ─────────────────────────────────────────────────────────────────────
# Probe: tracer peer unreachable across recent fires (today's symptom)
# ─────────────────────────────────────────────────────────────────────


def probe_tracer_peer_unreachable(
    *,
    tracer_dir: Optional[Path] = None,
    persistent_cycles: int = 3,
    lookback_s: float = 1800.0,
    now: Optional[float] = None,
) -> List[Signal]:
    """Classify peers with recurring no-route/timeout into transient vs persistent.

    Reads recent ``tracer-<unix>.json`` files from ``tracer_dir`` (or
    ``~/.local/state/meshforge/tracer`` per the lab tooling convention),
    walks the last ``lookback_s`` seconds of fires, groups results by
    peer, and emits one Signal per peer whose recent fires are dominated
    by no-route/timeout.

    Tier-1 (transient): peer failed in the most recent fire but had
    at least one success in the lookback window. Severity: info.

    Tier-2 (persistent): peer failed in the last ``persistent_cycles``
    consecutive fires with no successes between. Severity: wedge.

    Why a list return: one tracer_dir can report on many peers in one
    pass; flattening to N Signals keeps the probe API uniform.
    """
    if tracer_dir is None:
        tracer_dir = _default_tracer_dir()
    if tracer_dir is None or not tracer_dir.is_dir():
        return []

    if now is None:
        now = time.time()
    since_unix = now - lookback_s

    fires = _load_recent_fires(tracer_dir, since_unix=since_unix)
    if not fires:
        return []

    # Fires sorted newest-first inside _load_recent_fires.
    # Group results by peer: list of (fire_at_unix, result) newest-first.
    by_peer: dict = {}
    for fire in fires:
        for r in fire.get("results", []):
            if not isinstance(r, dict):
                continue
            peer = r.get("peer")
            if not isinstance(peer, str) or not peer:
                continue
            by_peer.setdefault(peer, []).append(
                (fire["fire_at_unix"], r.get("result"))
            )

    signals: List[Signal] = []
    for peer, history in by_peer.items():
        # history is newest-first thanks to fires being newest-first.
        if not history:
            continue
        latest_result = history[0][1]
        if latest_result == "ok":
            continue  # peer reachable right now → nothing to report

        # Count leading non-ok results.
        leading_fail = 0
        for _, result in history:
            if result == "ok":
                break
            leading_fail += 1

        if leading_fail >= persistent_cycles:
            signals.append(Signal(
                cls="tracer_peer_unreachable",
                subject=peer,
                severity="wedge",
                detail=(
                    f"{leading_fail} consecutive failed tracer fires "
                    f"({latest_result!r} latest). Persistent — likely real "
                    f"outage, not cold-start. Check {peer} echo service + "
                    f"RNS path."
                ),
                extra={
                    "leading_fail": leading_fail,
                    "latest_result": latest_result,
                    "tier": "persistent",
                },
            ))
        else:
            # Has at least one ok in the lookback window OR not enough
            # leading fails for tier-2. Either way, transient.
            signals.append(Signal(
                cls="tracer_peer_unreachable",
                subject=peer,
                severity="info",
                detail=(
                    f"{leading_fail} recent failed fire(s) to {peer} "
                    f"({latest_result!r} latest). Transient — cold-start "
                    f"or single blip; resolves with retries."
                ),
                extra={
                    "leading_fail": leading_fail,
                    "latest_result": latest_result,
                    "tier": "transient",
                },
            ))
    return signals


def _default_tracer_dir() -> Optional[Path]:
    """Resolve the tracer state dir per the same XDG/operator-home rules
    as ``utils/tracer_fires.py::_tracer_dir``. Kept local to avoid
    pulling in fleet_test_runner during watchdog startup."""
    import os
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / "meshforge" / "tracer"
    # Watchdog runs as root. Fall back to the operator's home via the
    # same lookup tracer_fires uses.
    try:
        from utils.tracer_fires import _operator_home  # noqa: WPS433
    except ImportError:
        return None
    home = _operator_home()
    if home is None:
        return None
    return home / ".local" / "state" / "meshforge" / "tracer"


def _load_recent_fires(
    tracer_dir: Path, *, since_unix: float,
) -> List[dict]:
    """Read all tracer-<unix>.json files in ``tracer_dir`` newer than
    ``since_unix``. Returns newest-first. Skips malformed files."""
    fires: List[dict] = []
    try:
        entries = list(tracer_dir.iterdir())
    except OSError:
        return []
    for fp in entries:
        name = fp.name
        if not name.startswith("tracer-") or not name.endswith(".json"):
            continue
        try:
            file_unix = float(name[len("tracer-"):-len(".json")])
        except (IndexError, ValueError):
            file_unix = None
        if file_unix is not None and file_unix < since_unix - 30:
            continue
        try:
            with fp.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        fire_at = data.get("fire_at_unix")
        if not isinstance(fire_at, (int, float)) or fire_at < since_unix:
            continue
        if not isinstance(data.get("results"), list):
            continue
        fires.append(data)
    fires.sort(key=lambda d: d["fire_at_unix"], reverse=True)
    return fires


# ─────────────────────────────────────────────────────────────────────
# Probe: channel feed dark (2026-06-04 — the .32 dark-feed lesson)
# ─────────────────────────────────────────────────────────────────────


def probe_channel_feed_dark(
    *,
    channel_name: str = "meshforge",
    unit: str = "meshtasticd.service",
    dark_after_s: float = 6 * 3600.0,
    lookback: Optional[str] = None,
    journalctl_path: str = "journalctl",
    systemctl_path: str = "systemctl",
    main_pid: Optional[int] = None,
    newest_line_fn=None,
    now: Optional[float] = None,
) -> Optional[Signal]:
    """Fire when a watched channel's decoded-text feed goes silent.

    The .32 dark-feed lesson (2026-06-04 PSK rotation): a consumer missed
    the re-key and its feed went silently dark — heartbeats stayed green
    because *silence looks identical to "no traffic"* unless something
    watches for it. On a normally-busy mesh, hours of zero decoded text on
    the meshforge channel mean a missed PSK re-key, a deaf radio (the moc2
    antenna case — ``channel_utilization=0.0`` tell), or a dead uplink path.

    Watched by NAME, not slot index (2026-06-06 federator false-alarm):
    the ``"channel":N`` field in the json payload is the box-LOCAL slot
    index, and slot layouts legitimately differ across the fleet (the
    federator carries a box-local channel at slot 2 and the fleet channel
    at slot 3 — the old ``"channel":2`` grep read a healthy feed as dark
    for days). Channel identity is the NAME (half of the decode gate
    ``hash(name, psk)``), and the json publish-topic journal line carries
    both the name and the payload:
    ``JSON publish message to msh/2/json/<name>/!<id>, N bytes:
    {...,"type":"text",...}`` — so one pattern matches name + text.

    Observation source: meshtasticd's MQTT-json uplink journal lines —
    the only channel-tagged decoded-text record available without touching
    the single-consumer ``/api/v1/fromradio`` (#17). Self-guards
    (returns None):

    - meshtasticd inactive (``service_inactive`` owns that), or
    - the box emits NO json-uplink lines at all in the lookback window
      (mqtt module unconfigured — e.g. a collector that only RXes;
      unobservable is not dark), or
    - journalctl unavailable/timeout.

    A box whose json pipeline is alive but shows no ``channel_name`` text
    for ``dark_after_s`` fires ``degraded`` — the sentinel boxes (busy
    gateways like moc) effectively canary the channel for the whole fleet
    via the mini signal_class flow.
    """
    pid = main_pid if main_pid is not None else _resolve_main_pid(
        unit, systemctl_path=systemctl_path
    )
    if pid is None:
        return None

    # Bound the journal scan to the darkness threshold. journalctl -g -r -n 1
    # is cheap on a busy feed (stops at the first newest match) but the
    # NO-MATCH case scans the ENTIRE --since window every tick — and that is
    # exactly a dark or collector feed. moc5 (2026-07-01) is a collector with
    # no json uplink at all, so the observability gate below re-scanned a fixed
    # 24h of meshtasticd journal each tick and pegged a core. The freshness
    # gate (dark_after_s) already discards any json line older than the
    # threshold, so a window longer than dark_after_s is pure wasted CPU.
    # Derive the window from dark_after_s — rounded UP to the next whole hour
    # — so the two can never drift (honest_failure_modes #5) and the scan is
    # bounded to what the decision actually needs. int()+1 guarantees
    # lookback >= dark_after_s (no false dark); the margin is (0, 1h] and is
    # exactly 1h only for whole-hour thresholds (floor semantics).
    if lookback is None:
        lookback = f"{int(dark_after_s // 3600) + 1}h"

    if newest_line_fn is None:
        def newest_line_fn(pattern: str) -> Optional[str]:
            return _journal_newest_match(
                unit, pattern, lookback, journalctl_path=journalctl_path
            )

    # Observability gate: any json-uplink line at all? None → this box
    # cannot see channel traffic (mqtt module off) — silence is not dark.
    any_json = newest_line_fn("serialized json message")
    if any_json is None:
        return None

    ts_now = now if now is not None else time.time()

    # Freshness gate (Issue #74): existence within the scan window (the
    # derived lookback above) isn't enough — if the newest json line is
    # ITSELF older than dark_after_s, the whole json pipeline died, and firing
    # channel_feed_dark would misdirect the operator toward PSK
    # re-key / deaf radio when the uplink module is the real failure.
    # Whole-pipeline-dark is unobservable for channel-SPECIFIC dark.
    json_ts = _short_unix_ts(any_json)
    if json_ts is not None and (ts_now - json_ts) >= dark_after_s:
        return None

    ch_text = newest_line_fn(f'json/{channel_name}/.*"type":"text"')

    if ch_text is None:
        age_desc = f"none within the {lookback} lookback window"
        age_s = None
    else:
        last_ts = _short_unix_ts(ch_text)
        if last_ts is None:
            return None  # unparseable journal line — indeterminate
        age_s = ts_now - last_ts
        if age_s < dark_after_s:
            return None  # feed is alive
        age_desc = f"last decoded {age_s / 3600.0:.1f}h ago"

    json_age = _short_unix_ts(any_json)
    json_age_desc = (
        f"{(ts_now - json_age) / 3600.0:.1f}h ago" if json_age is not None else "unknown"
    )
    detail = (
        f"No decoded text on Meshtastic channel '{channel_name}' "
        f"({age_desc}) while the json-uplink pipeline is observable (newest "
        f"json line {json_age_desc}). On a normally-busy mesh this is the "
        f"dark-feed tell: missed PSK re-key (decode gate = hash(name,psk)), "
        f"deaf radio (check channel_utilization in DeviceTelemetry), or dead "
        f"uplink path. Verify: journalctl -u meshtasticd | grep "
        f"'json/{channel_name}/' ; then send a test message from another "
        f"fleet box on the '{channel_name}' channel."
    )
    extra: dict = {
        "channel_name": channel_name,
        "dark_after_s": dark_after_s,
        "lookback": lookback,
    }
    if age_s is not None:
        extra["age_s"] = round(age_s, 1)
    return Signal(
        cls="channel_feed_dark",
        subject=f"meshtastic-{channel_name}",
        severity="degraded",
        detail=detail,
        extra=extra,
    )


