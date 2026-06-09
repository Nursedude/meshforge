"""Watchdog probes — local service health failure shapes.

HTTP-local unresponsive (#61/#70), fd exhaustion (#73), PhoneAPI TCP leak
(#75), service-inactive, tracer peer unreachable, channel feed dark.
Part of the ``watchdog_probes`` split (2026-06-09) — import via the
``utils.watchdog_probes`` hub, not from here.
"""
from __future__ import annotations

import json
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
    _journal_newest_match,
    _resolve_main_pid,
    _short_unix_ts,
)

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
    lookback: str = "24h",
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

    # Freshness gate (Issue #74): existence within the 24h lookback
    # isn't enough — if the newest json line is ITSELF older than
    # dark_after_s, the whole json pipeline died, and firing
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


