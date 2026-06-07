"""Per-box watchdog probes — pure functions, each returns Optional[Signal].

Companion to ``utils/watchdog_runner.py``. The runner ticks every 30s,
calls every probe in order, aggregates results into
``/var/lib/meshforge/watchdog.json`` (atomic-rename), and surfaces them
via ``/api/status.watchdog`` for the federation rollup.

Each probe targets one concrete failure shape already documented in
``.claude/foundations/persistent_issues.md``. Adding a new probe means
adding the failure class to the closed enum below + a probe function +
a row in ``persistent_issues.md``. The surface becomes the place new
classes get categorized instead of each one starting a fresh issue
thread.

Design constraints:

* **Pure**: probes take primitive args, return a ``Signal`` (or None).
  No global state, no logger calls — the runner handles edge-transition
  logging.
* **Bounded**: every external call has an explicit timeout. A probe
  that hangs the runner is worse than a probe that returns None.
* **Read-only**: probes NEVER restart services, write files, or send
  network traffic that side-effects the system being probed.
* **Honest about indeterminacy**: a probe that can't reach its data
  source returns None (no signal). False alarms are worse than missed
  alarms in Phase 1 — the operator stops trusting the panel.

The Phase 1 watchdog does NOT auto-restart anything. It emits signal
only. Phase 3 (opt-in, narrow allowlist) is gated on a month of
trusted signal.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, List, Optional, Tuple
from urllib.request import urlopen
from urllib.error import URLError

if TYPE_CHECKING:
    from utils.rns_status_parser import RNSStatus


# ─────────────────────────────────────────────────────────────────────
# Closed enum of failure classes — one per persistent_issues.md entry.
# Each class maps to a probe function below. To add a class, add it
# here AND add a row to persistent_issues.md.
# ─────────────────────────────────────────────────────────────────────

SIGNAL_CLASSES = (
    "rns_namespace_collision",        # Issue #69
    "main_thread_wedge",              # Issue #68 (renamed kept for backwards compat)
    "http_local_unresponsive",        # general; catches #61 socketserver-deadlock
    "delivery_write_canary",          # Issue #63
    "service_inactive",               # general; "should be running but isn't"
    "tracer_peer_unreachable",        # today's symptom; per-peer recurring no-route
    "rns_shared_instance_unresponsive",  # 2026-05-21: rnsd shared-instance hung
    "rns_interface_down_peer_reachable",  # 2026-05-30: stuck TCPInterface Down, peer reachable
    "rns_rpc_unresponsive",  # 2026-05-30: rnsd RPC wedged — rnstatus hangs though socket accepts (#68/#69)
    "fd_exhaustion",  # Issue #73 (2026-05-31): open fds approaching soft RLIMIT_NOFILE — fires BEFORE the wedge
    "foundation_perms_drift",  # 2026-06-01: born-correct permission foundation drifted (mf.4/#73) — non-root rnsd can't write its RNS tree
    "parity_drift",  # 2026-06-01: MeshForge<->MeshAnchor RNS-reliability parity diverged (lead-repo port debt)
    "rns_version_drift",  # 2026-06-01: rns/lxmf installed off the pinned +mf.N fork version (T2-isolate arc)
    "role_drift",  # 2026-06-03: live systemd unit state diverges from the box's effective declared role (fleet_roles.yaml + deployment.json overrides)
    "channel_feed_dark",  # 2026-06-04: no decoded text on a watched Meshtastic channel — the .32 dark-feed / PSK-rotation-canary lesson (silence is the failure mode)
    "queue_backlog",  # Issue #74 (2026-06-06): persistent-queue depth near shed threshold / dead-letter growth — backlog masks delivery failures
    "delivery_confirmation_stall",  # Issue #74 (2026-06-06): sends flow but confirmations collapsed — bridge self-report reads HEALTHY while shouting into a void
    "phoneapi_tcp_leak",  # Issue #75 (2026-06-07): map service holds an unaccounted persistent TCP to meshtasticd :4403 — leaked TCPInterface silently starves the :9443 web client (#17 contention class, leak form)
    "mqtt_root_drift",  # Issue #77 (2026-06-07): radio's observed MQTT publish root prefix diverges from the box's declared mqtt_bridge.root_topic — a zero-config radio join silently reintroduces the msh/US split
)

SEVERITIES = ("info", "degraded", "wedge")


@dataclass
class Signal:
    """One active failure signal. Identity = (class, subject)."""
    cls: str               # one of SIGNAL_CLASSES
    subject: str           # e.g. "meshforge-echo", "<peer-short-name>"
    severity: str          # one of SEVERITIES
    detail: str            # human-readable; goes straight to /fleet panel
    issue_ref: Optional[int] = None   # GitHub-ish issue number for cross-ref
    extra: dict = field(default_factory=dict)  # probe-specific data

    def key(self) -> Tuple[str, str]:
        """Stable identity for edge-transition tracking."""
        return (self.cls, self.subject)


# ─────────────────────────────────────────────────────────────────────
# Probe: RNS namespace collision (Issue #69)
# ─────────────────────────────────────────────────────────────────────


def probe_rns_namespace_collision(
    instance_name: str,
    *,
    ss_path: str = "ss",
    proc_root: str = "/proc",
) -> Optional[Signal]:
    """Verify ``@rns/<instance>`` LISTEN is owned by an rnsd-shaped process.

    Mirrors ``utils/rns_init.py::check_rns_listener_owner``, but
    callable from outside an RNS-using service so the watchdog can
    detect a foreign daemon hijack even if no MeshForge RNS client
    has tried (and failed) to start yet.

    Returns:
        Signal(severity=wedge, cls=rns_namespace_collision) when the
        listener is owned by a process whose cmdline doesn't match
        the narrow rnsd/reticulum allowlist.
        None on pass (or when no listener exists, or ss is missing —
        we don't false-alarm on indeterminate state).
    """
    if not instance_name:
        return None

    try:
        proc = subprocess.run(
            [ss_path, "-xnpl"], capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None

    needle = f"@rns/{instance_name}"
    pids: set = set()
    for line in proc.stdout.splitlines():
        if needle not in line:
            continue
        m = re.search(r'users:\(\("([^"]+)",pid=(\d+),', line)
        if m:
            pids.add(int(m.group(2)))

    if not pids:
        return None  # no listener → not a collision

    allowed_patterns = ("rnsd", "reticulum")
    suspicious: List[Tuple[int, str]] = []
    owner_cmdlines: dict = {}
    for pid in pids:
        try:
            with open(f"{proc_root}/{pid}/cmdline", "rb") as fh:
                cmdline = fh.read().replace(b"\x00", b" ").decode(
                    "utf-8", errors="replace").strip()
        except OSError:
            cmdline = ""
        owner_cmdlines[pid] = cmdline
        if not any(pat in cmdline.lower() for pat in allowed_patterns):
            suspicious.append((pid, cmdline))

    if not suspicious:
        return None

    pid, cmdline = suspicious[0]
    cmdline_short = cmdline[:120] or "<process exited>"
    return Signal(
        cls="rns_namespace_collision",
        subject=f"@rns/{instance_name}",
        severity="wedge",
        detail=(
            f"foreign daemon owns @rns/{instance_name}: "
            f"pid={pid} cmd={cmdline_short!r}. RNS clients will EOF on "
            f"first RPC. Recovery: sudo kill {pid}; "
            f"sudo systemctl restart rnsd.service"
        ),
        issue_ref=69,
        extra={"pid": pid, "cmdline": cmdline_short},
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: main-thread wedge (Issue #68)
# ─────────────────────────────────────────────────────────────────────

# Kernel-stack patterns that indicate the main thread is wedged in an
# uninterruptible syscall waiting on a Unix socket. Specifically NOT
# matching ``do_sys_poll`` — healthy HTTP servers spend most of their
# main-thread time there.
_WEDGE_PATTERNS = (
    "unix_wait_for_peer",     # connect() blocked waiting for accept (Issue #68)
    "unix_stream_connect",    # earlier in the same path
    "do_unix_stream_connect",
)


def _scan_pid_task_stacks(pid: int, proc_root: str) -> Optional[Tuple[int, str, str]]:
    """Walk every task under /proc/<pid>/task/*/stack for a wedge pattern.

    Returns ``(tid, matched_pattern, stack_excerpt)`` on first match,
    or None when no thread wedged. The main thread is checked first
    (cheap, no race), then worker threads — today's 2026-05-21 moc1
    investigation showed Issue #68's wedge can live in a WORKER thread
    of meshforge-echo while the main thread sits idle in futex_wait.
    """
    main_stack_path = f"{proc_root}/{pid}/task/{pid}/stack"
    try:
        with open(main_stack_path, "r") as fh:
            stack = fh.read()
    except (OSError, PermissionError):
        return None  # can't read at all — return None, no false clear

    matched = next((p for p in _WEDGE_PATTERNS if p in stack), None)
    if matched is not None:
        return pid, matched, stack[:300]

    # Walk worker threads. /proc/<pid>/task lists every TID.
    task_dir = f"{proc_root}/{pid}/task"
    try:
        tids = [int(name) for name in os.listdir(task_dir) if name.isdigit()]
    except OSError:
        return None
    for tid in tids:
        if tid == pid:
            continue  # already checked
        try:
            with open(f"{task_dir}/{tid}/stack", "r") as fh:
                worker_stack = fh.read()
        except (OSError, PermissionError):
            continue
        matched = next((p for p in _WEDGE_PATTERNS if p in worker_stack), None)
        if matched is not None:
            return tid, matched, worker_stack[:300]
    return None


def probe_main_thread_wedge(
    service_name: str,
    *,
    pid: Optional[int] = None,
    proc_root: str = "/proc",
    systemctl_path: str = "systemctl",
) -> Optional[Signal]:
    """Read ``/proc/<pid>/task/*/stack`` and match wedge patterns.

    Name kept for backwards compat with persistent_issues.md and
    existing tests, but now scans ALL threads (main + workers).
    Today's 2026-05-21 moc1 investigation showed the wedge can live in
    a worker thread while the main thread sits in normal
    ``futex_wait_queue``; the original main-thread-only probe missed it.

    Requires CAP_SYS_PTRACE or root — the watchdog runs as root.

    Falls back to ``systemctl show -p MainPID <service>`` to resolve
    PID when not provided. If the service is inactive, returns None
    (a different probe catches that).
    """
    if pid is None:
        pid = _resolve_main_pid(service_name, systemctl_path=systemctl_path)
        if pid is None or pid <= 1:
            return None

    found = _scan_pid_task_stacks(pid, proc_root)
    if found is None:
        return None
    tid, matched, excerpt = found

    thread_role = "main thread" if tid == pid else f"worker thread tid={tid}"
    return Signal(
        cls="main_thread_wedge",
        subject=service_name,
        severity="wedge",
        detail=(
            f"{thread_role} of pid={pid} blocked in kernel pattern "
            f"{matched!r} — likely rnsd Unix socket wedge. "
            f"Recovery: stop service, restart rnsd.service, then start "
            f"service. See Issue #68."
        ),
        issue_ref=68,
        extra={
            "pid": pid,
            "tid": tid,
            "thread_role": "main" if tid == pid else "worker",
            "pattern": matched,
            "stack_excerpt": excerpt,
        },
    )


# Process-name patterns for user-scope or non-systemd-known RNS-using
# processes. Today's incident: meshforge-echo.service is a user-scope
# unit; the watchdog runs as root and can't easily query systemctl
# --user without DBUS env setup. Instead we walk /proc, match by
# cmdline substring, and probe each match's task stacks.
_LXMF_PROCESS_PATTERNS = (
    "lab.lxmf_echo",
    "lab.lxmf_tracer",
    "lab.lxmf_multi_user_synth",
)


def probe_lxmf_process_wedge(
    *,
    proc_root: str = "/proc",
    patterns: Tuple[str, ...] = _LXMF_PROCESS_PATTERNS,
) -> List[Signal]:
    """Walk /proc, find RNS-using processes by cmdline substring, probe
    each one's task stacks for wedge patterns.

    Catches the class of wedge that lives in a user-scope service the
    watchdog can't reach via ``systemctl show -p MainPID`` from root.
    Subject identifies the wedged process by cmdline pattern so the
    operator knows which service to restart.

    Returns 0..N signals (one per wedged process). Distinct from
    ``probe_main_thread_wedge`` to keep the signal subjects clean:
    main_thread_wedge subjects are systemd unit names (operator
    actionable via systemctl restart <unit>); lxmf process subjects
    are cmdline patterns (operator knows which service from there).
    """
    signals: List[Signal] = []
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return signals

    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open(f"{proc_root}/{pid}/cmdline", "rb") as fh:
                cmdline = fh.read().replace(b"\x00", b" ").decode(
                    "utf-8", errors="replace",
                ).strip()
        except OSError:
            continue
        if not cmdline:
            continue
        matched_pat = next(
            (p for p in patterns if p in cmdline),
            None,
        )
        if matched_pat is None:
            continue

        found = _scan_pid_task_stacks(pid, proc_root)
        if found is None:
            continue
        tid, kernel_pattern, excerpt = found

        thread_role = "main thread" if tid == pid else f"worker thread tid={tid}"
        signals.append(Signal(
            cls="main_thread_wedge",
            subject=matched_pat,
            severity="wedge",
            detail=(
                f"{thread_role} of pid={pid} ({matched_pat}) blocked in "
                f"kernel pattern {kernel_pattern!r} — likely rnsd Unix "
                f"socket wedge. Recovery: restart rnsd.service, then "
                f"restart the owning user service. See Issue #68."
            ),
            issue_ref=68,
            extra={
                "pid": pid,
                "tid": tid,
                "thread_role": "main" if tid == pid else "worker",
                "pattern": kernel_pattern,
                "cmdline": cmdline[:200],
                "stack_excerpt": excerpt,
            },
        ))
    return signals


def _resolve_main_pid(
    service_name: str, *, systemctl_path: str = "systemctl",
) -> Optional[int]:
    """``systemctl show -p MainPID <service>`` parser. Returns None
    on any failure (including inactive service which reports
    ``MainPID=0``)."""
    try:
        proc = subprocess.run(
            [systemctl_path, "show", "-p", "MainPID", "--value", service_name],
            capture_output=True, text=True, timeout=3,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        pid = int(proc.stdout.strip())
    except (ValueError, TypeError):
        return None
    return pid if pid > 1 else None


# ─────────────────────────────────────────────────────────────────────
# Probe: RNS shared-instance unresponsive
# ─────────────────────────────────────────────────────────────────────


def probe_rns_shared_instance_responsive(
    instance_name: str,
    *,
    timeout_s: float = 2.0,
) -> Optional[Signal]:
    """Verify ``@rns/<instance>`` actually accepts new connect() attempts.

    Catches the 2026-05-21 moc1 wedge class: rnsd is `active (running)`,
    listener UP, no SYN-SENT pile-up, BUT new shared-instance connects
    hang in ``unix_wait_for_peer`` and never complete. Symptoms:
    ``rnstatus`` returns empty, new RNS-using services wedge during
    init, peer PINGs to local LXMF destinations get silently dropped
    because the destination registration never propagated through the
    broken accept path.

    Implementation: open an abstract Unix stream socket to
    ``@rns/<instance_name>`` with a short timeout. If connect succeeds
    quickly → healthy (return None). If it hangs past timeout → wedge.
    If the socket address doesn't exist → return None (rnsd isn't
    running; service_inactive probe owns that signal).

    Distinct from rns_namespace_collision (which checks WHO owns the
    listener) — this checks WHETHER the owner is actually serving.
    Both fire on rnsd-side faults but at different layers.
    """
    if not instance_name:
        return None

    # Abstract Unix socket address: leading NUL byte then the name.
    # `@rns/<name>` in `ss -xnpl` is the kernel's display form.
    addr = "\x00rns/" + instance_name
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout_s)
    try:
        sock.connect(addr)
    except FileNotFoundError:
        # No such socket — listener doesn't exist; service_inactive owns it.
        sock.close()
        return None
    except ConnectionRefusedError:
        # Listener exists but actively refused — rnsd may be shutting down.
        # Not a wedge in the connect-hang sense; let service state catch it.
        sock.close()
        return None
    except socket.timeout:
        sock.close()
        return Signal(
            cls="rns_shared_instance_unresponsive",
            subject=f"@rns/{instance_name}",
            severity="wedge",
            detail=(
                f"connect to @rns/{instance_name} hung past "
                f"{timeout_s:.1f}s. rnsd is running but not accepting "
                f"new shared-instance clients — new RNS-using services "
                f"will wedge during init, local LXMF destinations may "
                f"not register, peer PINGs silently drop. Recovery: "
                f"sudo systemctl restart rnsd.service then restart any "
                f"RNS-using services (meshforge-map, meshforge-echo). "
                f"See Issue #68 + 2026-05-21 moc1 investigation."
            ),
            issue_ref=68,
            extra={"address": f"@rns/{instance_name}", "timeout_s": timeout_s},
        )
    except OSError:
        sock.close()
        return None
    # Connected successfully — healthy. Close cleanly without sending
    # any RNS protocol bytes (which would confuse rnsd's accept loop).
    try:
        sock.close()
    except OSError:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────
# Probe: RNS RPC unresponsive — rnstatus hangs though the socket accepts
# ─────────────────────────────────────────────────────────────────────


def probe_rns_rpc_responsive(
    *,
    rnstatus_status: "Optional[RNSStatus]" = None,
    timeout_s: float = 8.0,
) -> Optional[Signal]:
    """Detect a wedged rnsd RPC: ``rnstatus`` itself hangs even though the
    shared-instance socket accepts the connection.

    This is the layer ``probe_rns_shared_instance_responsive`` cannot see.
    That probe is a bare ``connect()`` timer — it catches a connect that
    never completes (the SYN-SENT pile-up shape of #68), but returns
    healthy the moment the socket *accepts*. The 2026-05-20 #69 family
    (and the wedged-rnsd-RPC class the watchdog was missing) is the
    opposite: connect succeeds, then the RPC round-trip
    (``rpc_connection.recv()`` deep in ``RNS.Reticulum``) hangs or EOFs.
    ``rnstatus`` is the canonical RPC client, so running it bounded and
    observing a TIMEOUT is the direct test for "RPC wedged".

    Distinguishing wedge from clean-down: a genuinely down rnsd has no
    listener, so ``rnstatus`` fails FAST (binary-missing / "no shared
    instance" / connection-refused) — ``RNSStatus.timed_out`` stays False
    and we return None (``service_inactive`` owns rnsd-down). Only a
    subprocess TIMEOUT sets ``timed_out=True`` → wedge. Binary missing
    likewise returns None (no false alarm on RNS-less boxes).

    Args:
        rnstatus_status: a pre-fetched ``RNSStatus`` (the runner shares
            one ``run_rnstatus`` call across the rnstatus-consuming
            probes). When None, this probe runs ``rnstatus`` itself with
            ``timeout_s``.
        timeout_s: bounded rnstatus timeout when this probe runs it
            directly. Kept well under the 30s watchdog tick.
    """
    if rnstatus_status is None:
        from utils.rns_status_parser import run_rnstatus
        status = run_rnstatus(timeout_s=timeout_s)
    else:
        status = rnstatus_status

    if not status.timed_out:
        return None

    return Signal(
        cls="rns_rpc_unresponsive",
        subject="rnsd",
        severity="wedge",
        detail=(
            "rnstatus did not return within its timeout — rnsd accepts "
            "shared-instance connects but the RPC round-trip is wedged "
            "(rpc_connection.recv hang/EOF). New RNS clients that get past "
            "connect still stall in init and destination lookups silently "
            "fail. Recovery: sudo systemctl restart rnsd.service, then "
            "restart RNS-using services (meshforge-map, meshforge-echo, "
            "tracer). See Issue #68/#69."
        ),
        issue_ref=68,
        extra={"timed_out": True},
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: RNS interface Down while peer reachable (2026-05-30)
# ─────────────────────────────────────────────────────────────────────

# A routable TCPInterface display_name embeds the peer host:port, e.g.
#   "Regional RNS/192.168.86.38:4242"
# RNodeInterface / AutoInterface / the Shared Instance line carry no
# host:port and are correctly ignored — only a TCP peer can be probed
# for reachability. The host group is an IPv4 dotted-quad; rnsd renders
# the configured target_host:target_port verbatim.
_TCP_PEER_RE = re.compile(r"(?P<host>[0-9.]+):(?P<port>\d+)\s*$")


def _tcp_reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    """Bounded TCP-connect reachability test to ``(host, port)``.

    Returns True when a TCP connection can be established within
    ``timeout`` seconds, False on any ``OSError`` (refused, timed out,
    no route, bad address). Factored out as a module-level function so
    tests can monkeypatch it and do zero real network I/O.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe_rns_interface_down_peer_reachable(
    *,
    rnstatus_status: "Optional[RNSStatus]" = None,
    rnstatus_text: Optional[str] = None,
    reachable_timeout_s: float = 3.0,
) -> Optional[Signal]:
    """Detect a configured TCPInterface stuck ``Status: Down`` while its
    peer host:port is still TCP-reachable — the 2026-05-30 incident shape.

    The production incident: rnsd itself was healthy (Up, owned ``@rns``,
    answered ``rnstatus``) and the peer host:port + L3 were reachable,
    but the box's SOLE RNS uplink ``TCPInterface`` sat ``Status: Down``.
    The fleet was islanded until rnsd was restarted. The existing
    watchdog only caught this indirectly via ``tracer_peer_unreachable``;
    this probe catches it DIRECTLY at the interface layer.

    Logic:

    * Resolve interfaces from ``rnstatus_status`` (a pre-parsed
      ``RNSStatus`` — the runner shares one ``run_rnstatus`` call across
      the rnstatus-consuming probes), else parse ``rnstatus_text``
      (tests), else run ``rnstatus`` live — via ``utils.rns_status_parser``.
    * For each TCPInterface whose status is Down AND whose display_name
      embeds a ``host:port``, run a bounded TCP-connect reachability test.
    * If the connect SUCCEEDS → peer is reachable but the interface is
      stuck Down → emit a wedge signal (cure: restart rnsd).
    * If the connect FAILS → genuine peer/network outage, already owned
      by ``tracer_peer_unreachable``; do NOT emit here.

    Returns the FIRST qualifying interface's signal (consistent with the
    other single-return probes), with ``extra.down_reachable_count`` when
    more than one interface qualifies. Returns None when no interface
    qualifies, rnstatus is unreadable/errored, or rnsd is down (a
    different probe owns those).
    """
    if rnstatus_status is not None:
        status = rnstatus_status
    elif rnstatus_text is not None:
        from utils.rns_status_parser import parse_rnstatus
        status = parse_rnstatus(rnstatus_text)
    else:
        from utils.rns_status_parser import run_rnstatus
        status = run_rnstatus()

    # rnstatus errored (rnsd unreachable, binary missing, timeout) →
    # don't speculate. service_inactive owns rnsd-down; the
    # rns_rpc_unresponsive probe owns the rnstatus-timeout (wedged-RPC)
    # case (it keys on RNSStatus.timed_out, not on this parse_error).
    if status.parse_error:
        return None

    from utils.rns_status_parser import InterfaceStatus

    qualifying: List[Tuple[str, str, int]] = []  # (interface_label, host, port)
    for iface in status.interfaces:
        # Only TCP interfaces carry a routable peer host:port.
        if "tcp" not in iface.type_name.lower():
            continue
        if iface.status != InterfaceStatus.DOWN:
            continue
        m = _TCP_PEER_RE.search(iface.display_name)
        if not m:
            continue
        host = m.group("host")
        try:
            port = int(m.group("port"))
        except (TypeError, ValueError):
            continue
        if _tcp_reachable(host, port, timeout=reachable_timeout_s):
            qualifying.append((iface.full_name, host, port))

    if not qualifying:
        return None

    label, host, port = qualifying[0]
    extra = {
        "interface": label,
        "host": host,
        "port": port,
        "peer_reachable": True,
    }
    if len(qualifying) > 1:
        extra["down_reachable_count"] = len(qualifying)

    return Signal(
        cls="rns_interface_down_peer_reachable",
        subject=label,
        severity="wedge",
        detail=(
            f"rnstatus shows {label} Status: Down but its peer "
            f"{host}:{port} is TCP-reachable — stuck interface, not a "
            f"peer outage. rnsd is up but this uplink is wedged; the box "
            f"may be islanded. Recovery: sudo systemctl restart "
            f"rnsd.service. See 2026-05-30 incident."
        ),
        extra=extra,
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
# Probe: permission-foundation drift (mf.4 / Issue #73 perms class)
# ─────────────────────────────────────────────────────────────────────


def probe_foundation_drift(
    *,
    perms=None,
) -> Optional[Signal]:
    """Surface a born-correct permission-foundation drift in the RNS config tree.

    The foundation SSOT (utils.fleet_foundation + the shared utils.rns_tree_perms)
    declares that a non-root rnsd must own/be-able-to-write its ``/etc/reticulum``
    tree (configdir ``root:<rnsd_user> 1775``, logfile/storage ``<rnsd_user>``). A
    re-provision that recreates the tree ``root:root`` while rnsd runs non-root is
    the recurrence path (moc1/moc2/moc, 2026-06-01) — every ``RNS.log()`` write then
    fails, which self-deadlocked the daemon pre-fork-mf.4 and loses all logs after.
    The fleet caught moc this way *manually* on the first audit; this probe makes it
    a continuously-monitored signal that flows to /fleet + the mini deep-rollup, so a
    drifted box self-surfaces instead of waiting for a hand-run audit.

    Scope: this checks the **RNS-tree perms** leg only — it derives the rnsd user
    from rnsd's own systemd unit (``probe_rns_tree_perms``), so it is correct no
    matter which user the watchdog runs as (it runs as root, where
    ``get_real_username`` would mislead). The **data-roots** leg of the foundation
    (operator-user-owned ``~/.config`` etc.) depends on the operator identity and is
    owned by the explicit, operator-run ``scripts/fleet_foundation.py audit`` /
    provisioner, not this root-context probe.

    Severity is ``degraded`` (not ``wedge``): a drifted box typically still serves —
    it is one logfile rotation from the wedge — and the fix is perms-only with no
    restart. Returns None when the foundation is clean, when rnsd runs as root
    (root writes anything), when the perms weren't probed (indeterminate — never
    guess), or when the foundation modules can't be imported.
    """
    try:
        from utils.rns_tree_perms import logfile_perms_drift, probe_rns_tree_perms
    except Exception:
        return None  # foundation tooling absent — indeterminate, don't false-alarm
    if perms is None:
        try:
            perms = probe_rns_tree_perms()
        except Exception:
            return None
    reason = logfile_perms_drift(perms)
    if not reason:
        return None
    detail = (
        f"{reason} | born-correct permission foundation drifted (mf.4/#73 perms "
        f"class). Fix (perms-only, no restart): "
        f"sudo python3 scripts/fleet_foundation.py apply"
    )
    return Signal(
        cls="foundation_perms_drift",
        subject="rnsd",
        severity="degraded",
        detail=detail,
        issue_ref=73,
        extra={
            "rnsd_user": perms.rnsd_user,
            "configdir_owner": perms.configdir_owner,
            "configdir_mode": perms.configdir_mode,
            "logfile_owner": perms.logfile_owner,
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: MeshForge <-> MeshAnchor parity drift (lead-repo port debt)
# ─────────────────────────────────────────────────────────────────────

DEFAULT_PARITY_DEBOUNCE_PATH = "/var/lib/meshforge/parity_debounce.json"


def _load_parity_streak(state_path: str) -> int:
    """Read the consecutive-drift streak counter. Best-effort: any error → 0.

    A missing/unreadable/garbage state means 'no confirmed streak yet', which
    suppresses a first-seen drift — exactly the conservative direction the
    debounce wants (favour silence on uncertainty, not a false page).
    """
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            streak = int(json.load(fh).get("streak", 0))
        return streak if streak >= 0 else 0
    except (OSError, ValueError, TypeError):
        return 0


def _save_parity_streak(state_path: str, streak: int) -> None:
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


def probe_parity_drift(
    *,
    meshforge_root: str = "/opt/meshforge",
    meshanchor_root: str = "/opt/meshanchor",
    check_fn=None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """Surface MeshForge<->MeshAnchor RNS-reliability parity drift.

    The two sister NOCs share the fleet's RNS substrate; reliability-critical files
    (the RNS-init chokepoint, the bridge contract, the rns_tree_perms SSOT, the
    fork-pin, lint MF009/MF019, the wedge probes) must stay in lockstep —
    ``scripts/parity_check.py`` is the lead-repo gate. This makes that audit a
    continuously-monitored signal so a divergence (someone edits one repo and
    forgets to port) self-surfaces in /fleet + the mini deep-rollup instead of
    rotting until the next manual run.

    Only meaningful where BOTH repos are present (e.g. the box holding the
    MeshAnchor clone). Returns None when ``meshanchor_root`` isn't a directory (a
    MeshForge-only fleet box — not applicable), when the parity tool can't be
    loaded, when everything's in sync, or when the result is merely ``missing`` (a
    tracked file absent — indeterminate / possible mid-deploy window, don't
    false-alarm). Fires ``degraded`` only on definite content ``drift`` — nothing is
    failing at runtime; the fix is to port the flagged change (MeshForge leads).

    **Debounce**: a drift must persist for ``debounce_ticks`` *consecutive* ticks
    before firing (default 2). The two repos sync seconds apart during a fleet
    roll, so a single tick can catch one repo mid-update and see a transient
    divergence that self-heals before the next tick — the 2026-06-01
    ``rns_tree_perms.py`` SSOT-port race did exactly this. A consecutive-drift
    streak (persisted to ``state_path``, default
    ``/var/lib/meshforge/parity_debounce.json``) rides out those in-flight blips
    while still surfacing a genuine forgotten port within one extra tick. Any
    non-drift result (in_sync / missing / tool error) resets the streak.
    """
    if not os.path.isdir(meshanchor_root):
        return None  # both repos required; MeshForge-only box → not applicable
    if state_path is None:
        state_path = DEFAULT_PARITY_DEBOUNCE_PATH
    if check_fn is None:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "parity_check",
                os.path.join(meshforge_root, "scripts", "parity_check.py"),
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            check_fn = mod.check_parity
        except Exception:
            return None  # parity tool unavailable → indeterminate, don't alarm
    try:
        findings, overall = check_fn(meshforge_root, meshanchor_root)
    except Exception:
        # Indeterminate — don't let a tool error count toward the streak.
        _save_parity_streak(state_path, 0)
        return None
    if overall != "drift":
        _save_parity_streak(state_path, 0)  # in_sync / missing → streak broken
        return None

    streak = _load_parity_streak(state_path) + 1
    _save_parity_streak(state_path, streak)
    if streak < debounce_ticks:
        return None  # drift seen, but not yet confirmed across consecutive ticks

    drifted = [f for f in findings if getattr(f, "status", None) == "drift"]
    items = ", ".join(f.label for f in drifted) or "?"
    detail = (
        f"MeshForge<->MeshAnchor parity drift ({len(drifted)} item(s)): {items} | "
        f"confirmed over {streak} consecutive ticks | RNS-reliability files must "
        f"match (MeshForge is the lead repo). Port the change, then verify: "
        f"python3 scripts/parity_check.py"
    )
    return Signal(
        cls="parity_drift",
        subject="meshforge<->meshanchor",
        severity="degraded",
        detail=detail,
        extra={"drift_items": [f.label for f in drifted], "debounce_streak": streak},
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: RNS/LXMF fork-pin version drift (RNS T2-isolate arc)
# ─────────────────────────────────────────────────────────────────────


def _read_pkg_versions_for_user(user, pkgs):
    """Read installed versions of ``pkgs`` from the service user's site-packages —
    read-only, no privilege change. Returns ``{pkg: version}`` for those found, or
    None if the user's site dir can't be located/read.

    Why not just ``importlib.metadata.version()``? That reads the *current*
    interpreter's env — the watchdog runs as root, whose env may carry a different
    rns (verified live: root had 1.1.1 while the wh6gxz service env had 1.2.5+mf.4).
    And we can't switch user: the watchdog unit sets NoNewPrivileges + RestrictSUIDSGID,
    which block sudo AND runuser (both need setuid). But ProtectHome=no, so root can
    READ ``/home/<user>/.local/...`` directly and point importlib.metadata at it.
    """
    if not user or user == "root":
        return None
    try:
        import importlib.metadata as _im
        home = Path(f"/home/{user}")
        site_dirs = [str(p) for p in sorted(home.glob(".local/lib/python3*/site-packages"))
                     if p.is_dir()]
        if not site_dirs:
            return None
        found = {}
        for dist in _im.distributions(path=site_dirs):
            try:
                name = (dist.metadata["Name"] or "").lower()
            except Exception:
                continue
            if name in pkgs:
                found[name] = dist.version
        return found
    except Exception:
        return None


def probe_rns_version_drift(
    *,
    rnsd_user=None,
    pins=None,
    installed=None,
) -> Optional[Signal]:
    """Surface a box running rns/lxmf off the pinned MeshForge-fork version.

    The fleet pins rns/lxmf on the ``+mf.N`` marker (``requirements/rns.txt``,
    gated by ``scripts/rns_version_check.py``) — upstream withdrew public support, so
    a bump is a *reviewed* decision, never an automatic pip-latest. This makes the
    check a continuously-monitored signal so a box that missed a fork roll (e.g. moc3
    was stock 1.2.5 before the mf.4 roll) self-surfaces in /fleet + the mini rollup.

    The pin (``pins``) is env-independent (just reads requirements/rns.txt). The
    INSTALLED versions are read from the rnsd **service user's** site-packages
    (see ``_read_pkg_versions_for_user`` for why root's own env / sudo / runuser all
    fail here). Fires ``degraded`` only on a concrete mismatch (installed != pinned
    for a package we can actually see). Returns None when compliant, when the pin or
    the user env can't be read (indeterminate — never false-alarm), or when a package
    isn't visible in the user site (possible venv install elsewhere — don't guess).
    """
    if rnsd_user is None and installed is None:
        try:
            from utils.rns_tree_perms import _read_rnsd_user
            rnsd_user = _read_rnsd_user()
        except Exception:
            rnsd_user = None

    if pins is None:
        try:
            import importlib.util
            script = str(Path(__file__).resolve().parents[2] / "scripts" / "rns_version_check.py")
            spec = importlib.util.spec_from_file_location("rns_version_check", script)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            pins = mod.pinned_versions()
        except Exception:
            return None
    if not pins:
        return None  # no pin parseable (sub-arc A not applied) → indeterminate

    if installed is None:
        installed = _read_pkg_versions_for_user(rnsd_user, set(pins))
    if not installed:
        return None  # couldn't read the service env → indeterminate, no false alarm

    drift = []
    for pkg, want in pins.items():
        have = installed.get(pkg)
        if have is None:
            continue  # not visible in the user site (venv elsewhere?) — don't guess
        if have != want:
            drift.append(f"{pkg} installed={have} pinned={want}")
    if not drift:
        return None

    detail = (
        f"rns/lxmf off the pinned MeshForge-fork version ({'; '.join(drift)}). "
        f"Upstream withdrew support so the pin is deliberate — converge with a "
        f"REVIEWED bump: pip install --force-reinstall -r requirements/rns.txt, "
        f"then verify rnsd."
    )
    return Signal(
        cls="rns_version_drift",
        subject="rns/lxmf",
        severity="degraded",
        detail=detail,
        extra={"rnsd_user": rnsd_user, "drift": drift},
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: declared-role drift (fleet_roles.yaml + overrides vs live units)
# ─────────────────────────────────────────────────────────────────────

DEFAULT_ROLE_DRIFT_DEBOUNCE_PATH = "/var/lib/meshforge/role_drift_debounce.json"

# plan() verbs that mean "the box would change under converge" = real drift.
_ROLE_DRIFT_VERBS = ("enable", "disable", "mask")


def _read_deployment_declaration(service_user) -> Tuple[Optional[str], dict]:
    """Read ``(role, service_overrides)`` from the service user's deployment.json.

    The watchdog runs as sandboxed root: ``get_real_user_home()`` (which
    ``provision_role.py`` uses at import time) would resolve to ``/root`` here,
    so the home is derived from the service user and READ directly — never
    escalate/switch user (the rns_version_drift lesson). Any unreadability →
    ``(None, {})`` = indeterminate, never false-alarm.
    """
    if not service_user:
        return None, {}
    try:
        import pwd
        home = pwd.getpwnam(service_user).pw_dir
        path = os.path.join(home, ".config", "meshforge", "deployment.json")
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        role = data.get("role")
        ov = data.get("service_overrides") or {}
        return (role if isinstance(role, str) and role else None,
                ov if isinstance(ov, dict) else {})
    except (KeyError, OSError, ValueError, TypeError):
        return None, {}


def _plan_role_actions(role: str, overrides: dict, meshforge_root: str):
    """Default plan_fn: importlib-load ``scripts/provision_role.py`` (the
    converge SSOT) and return its ``plan()`` actions for this box's effective
    declaration — base role flattened through ``inherits`` plus the box's
    documented ``service_overrides``.

    Returns ``None`` when the tooling/catalog can't be loaded (indeterminate);
    raises ``KeyError`` for a role missing from the catalog (a REAL mismatch
    the caller counts as drift — e.g. deployment.json names a role the box's
    fleet_roles.yaml doesn't carry yet).
    """
    try:
        import importlib.util
        script = os.path.join(meshforge_root, "scripts", "provision_role.py")
        spec = importlib.util.spec_from_file_location("provision_role", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        catalog = mod.load_roles(mod.DEFAULT_ROLES_FILE)
    except Exception:
        return None  # tool/catalog unavailable → indeterminate, don't alarm
    role_def = mod.resolve_role(catalog, role)  # KeyError → unknown role
    return mod.plan(role_def, overrides)


def probe_role_drift(
    *,
    meshforge_root: str = "/opt/meshforge",
    deployment: Optional[Tuple[Optional[str], dict]] = None,
    plan_fn=None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """Surface a box whose live systemd unit state diverges from its declared role.

    The fleet's role model (``docs/fleet_roles.yaml`` + per-box
    ``deployment.json`` ``role``/``service_overrides``) is converged only when an
    operator runs ``provision_role.py --apply`` — between runs, nothing alerted on
    divergence. The 2026-06-03 architecture audit hit exactly this legibility gap
    (moc2 read as "full-gateway" while deliberately not bridging) — see
    ``.claude/research/fleet_architecture_2026_06_03.md`` §7-B. This probe makes
    the converge SSOT's own dry-run plan a continuously-monitored signal.

    Drift = any plan action whose verb is enable/disable/mask (the box would
    change under converge) or a BLOCKING warning (required unit not installed; a
    waiver missing its required ``reason``). **Documented overrides are honored**
    — ``plan()`` reports them as non-blocking advisories, which do NOT fire (the
    moc2 lesson: a declared, reasoned exception is not drift). A role missing
    from the catalog counts as drift (mismatched declaration).

    Returns ``None`` when the box declares no role (not applicable), when the
    tool/catalog can't be loaded (indeterminate — never false-alarm), or while a
    divergence hasn't yet persisted ``debounce_ticks`` consecutive ticks — role
    catalog (git) and unit state (converge/restarts) deploy independently, so a
    single tick can catch a fleet-roll window (same rationale as
    ``probe_parity_drift``). Severity ``degraded``: latent legibility debt, not
    an active failure.
    """
    if state_path is None:
        state_path = DEFAULT_ROLE_DRIFT_DEBOUNCE_PATH
    if deployment is None:
        try:
            from utils.rns_tree_perms import _read_rnsd_user
            service_user = _read_rnsd_user()
        except Exception:
            service_user = None
        deployment = _read_deployment_declaration(service_user)
    role, overrides = deployment
    if not role:
        _save_parity_streak(state_path, 0)
        return None  # box not role-declared (or unreadable) → not applicable

    if plan_fn is None:
        def plan_fn(r, ov):
            return _plan_role_actions(r, ov, meshforge_root)

    unknown_role = False
    try:
        actions = plan_fn(role, overrides)
    except KeyError:
        unknown_role = True
        actions = []
    except Exception:
        _save_parity_streak(state_path, 0)
        return None  # tool error → indeterminate, don't count toward streak
    if actions is None and not unknown_role:
        _save_parity_streak(state_path, 0)
        return None

    if unknown_role:
        items = [f"role '{role}' not in the fleet_roles.yaml catalog"]
    else:
        items = []
        for a in actions:
            verb = getattr(a, "verb", "")
            if verb in _ROLE_DRIFT_VERBS or (
                verb == "warn" and getattr(a, "required", False)
            ):
                items.append(
                    f"{getattr(a, 'item', '?')}: "
                    f"{getattr(a, 'current', '?')} -> {getattr(a, 'desired', '?')}"
                )
    if not items:
        _save_parity_streak(state_path, 0)
        return None

    streak = _load_parity_streak(state_path) + 1
    _save_parity_streak(state_path, streak)
    if streak < debounce_ticks:
        return None  # divergence seen, not yet confirmed across consecutive ticks

    shown = "; ".join(items[:4]) + (f" (+{len(items) - 4} more)" if len(items) > 4 else "")
    detail = (
        f"live unit state diverges from declared role '{role}' "
        f"({len(items)} item(s)): {shown} | confirmed over {streak} consecutive "
        f"ticks | documented service_overrides are honored (not drift). Review: "
        f"python3 scripts/provision_role.py (dry-run); converge with sudo "
        f"python3 scripts/provision_role.py --apply, or correct the declared role."
    )
    return Signal(
        cls="role_drift",
        subject=role,
        severity="degraded",
        detail=detail,
        extra={"items": items, "debounce_streak": streak},
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: delivery counters write canary (Issue #63)
# ─────────────────────────────────────────────────────────────────────


def probe_delivery_write_canary(
    *,
    host: str = "127.0.0.1",
    port: int = 5000,
    timeout_s: float = 3.0,
    error_threshold: int = 3,
) -> Optional[Signal]:
    """Read ``/api/gateway/delivery.health`` and surface preflight/runtime errors.

    Reuses the gateway's existing self-reported health block (Issue #63).
    The watchdog itself doesn't re-probe SQLite — that's the gateway's
    job; we just amplify the signal so an operator skimming /fleet sees
    "delivery counters can't write" the same way they see "RNS wedged".

    Severities:
      - wedge: preflight_ok=False (every write attempt is failing)
      - degraded: consecutive_write_errors >= error_threshold
      - None: healthy, or endpoint unreachable (a different probe surfaces that)
    """
    url = f"http://{host}:{port}/api/gateway/delivery"
    try:
        with urlopen(url, timeout=timeout_s) as resp:
            payload = json.loads(resp.read())
    except (URLError, socket.timeout, json.JSONDecodeError, OSError, ValueError):
        return None  # don't false-alarm on transport problems

    health = payload.get("health") if isinstance(payload, dict) else None
    if not isinstance(health, dict):
        return None

    preflight_ok = health.get("preflight_ok")
    consecutive = health.get("consecutive_write_errors") or 0
    last_err = health.get("last_write_error")
    db_path = health.get("db_path", "?")

    if preflight_ok is False:
        return Signal(
            cls="delivery_write_canary",
            subject="meshforge-gateway",
            severity="wedge",
            detail=(
                f"delivery_counters preflight FAILED at {db_path}. "
                f"Every record() is failing. Most likely systemd sandbox "
                f"ReadWritePaths missing the data bucket (Issue #58/#60). "
                f"Last error: {last_err}"
            ),
            issue_ref=63,
            extra={
                "db_path": db_path,
                "consecutive_write_errors": consecutive,
                "last_write_error": last_err,
            },
        )

    try:
        consec_int = int(consecutive)
    except (TypeError, ValueError):
        consec_int = 0
    if consec_int >= error_threshold:
        return Signal(
            cls="delivery_write_canary",
            subject="meshforge-gateway",
            severity="degraded",
            detail=(
                f"delivery_counters has {consec_int} consecutive write "
                f"errors. Last: {last_err}. Threshold for alarm: "
                f"{error_threshold}."
            ),
            issue_ref=63,
            extra={
                "consecutive_write_errors": consec_int,
                "last_write_error": last_err,
            },
        )

    return None


# ─────────────────────────────────────────────────────────────────────
# Probe: queue backpressure (Issue #74)
# ─────────────────────────────────────────────────────────────────────

DEFAULT_QUEUE_BACKLOG_STATE_PATH = "/var/lib/meshforge/queue_backlog_debounce.json"


def _load_dead_letter_baseline(state_path: str) -> Optional[int]:
    """Read the last-seen dead_letter count. Best-effort: any error → None.

    None means 'no baseline yet' — the first observation establishes
    the baseline and never fires, so a long-uptime box with a static
    historical dead-letter pile doesn't false-alarm on watchdog
    restart (the probe judges GROWTH, not absolute count).
    """
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            value = json.load(fh).get("dead_letter")
        return int(value) if value is not None and int(value) >= 0 else None
    except (OSError, ValueError, TypeError):
        return None


def _save_dead_letter_baseline(state_path: str, count: int) -> None:
    """Persist the dead_letter baseline (atomic-rename, never raises)."""
    try:
        parent = os.path.dirname(state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"dead_letter": int(count)}, fh, separators=(",", ":"))
        os.replace(tmp, state_path)
    except OSError:
        pass


def probe_queue_backlog(
    *,
    host: str = "127.0.0.1",
    port: int = 5000,
    timeout_s: float = 3.0,
    depth_degraded: float = 0.80,
    depth_wedge: float = 0.95,
    dead_letter_growth_degraded: int = 10,
    dead_letter_growth_wedge: int = 50,
    state_path: Optional[str] = None,
) -> Optional[Signal]:
    """Persistent-queue backpressure via ``/api/gateway/queue`` (Issue #74).

    A deep backlog masks delivery failures: messages sit 'pending'
    while the operator reads the gateway as healthy, and at the shed
    threshold ``_shed_overflow`` starts silently dropping LOW/NORMAL
    priority. Two legs, max severity wins:

      - depth: queue_depth / max_queue_size ≥ 95% → wedge (shed is
        imminent/active), ≥ 80% → degraded. Skipped when
        max_queue_size ≤ 0 (unlimited — no ceiling to judge against,
        mirrors the fd probe's "unlimited" guard).
      - dead-letter GROWTH since the last tick (baseline persisted to
        ``state_path``, parity-debounce pattern): ≥ 50 new → wedge
        (retries exhausting en masse), ≥ 10 new → degraded. A static
        historical pile never fires.

    Reads over localhost HTTP, never the queue DB directly — the
    watchdog is root in a hardened sandbox and the DB lives under the
    operator's home (the #60-class trap; derive context from the
    SERVICE). Transport/shape errors → None (http_local /
    service_inactive own those).
    """
    url = f"http://{host}:{port}/api/gateway/queue"
    try:
        with urlopen(url, timeout=timeout_s) as resp:
            payload = json.loads(resp.read())
    except (URLError, socket.timeout, json.JSONDecodeError, OSError, ValueError):
        return None
    if not isinstance(payload, dict) or "queue_depth" not in payload:
        return None

    try:
        queue_depth = int(payload.get("queue_depth") or 0)
        max_queue_size = int(payload.get("max_queue_size") or 0)
        dead_letter = int(payload.get("dead_letter") or 0)
    except (TypeError, ValueError):
        return None

    findings: List[Tuple[str, str]] = []  # (severity, detail-fragment)

    if max_queue_size > 0:
        usage = queue_depth / max_queue_size
        if usage >= depth_wedge:
            findings.append((
                "wedge",
                f"queue at {usage:.0%} of max ({queue_depth}/"
                f"{max_queue_size}) — shed threshold; LOW/NORMAL "
                f"priority messages are being dropped",
            ))
        elif usage >= depth_degraded:
            findings.append((
                "degraded",
                f"queue backlog building: {usage:.0%} of max "
                f"({queue_depth}/{max_queue_size})",
            ))

    sp = state_path or DEFAULT_QUEUE_BACKLOG_STATE_PATH
    baseline = _load_dead_letter_baseline(sp)
    _save_dead_letter_baseline(sp, dead_letter)
    if baseline is not None:
        growth = dead_letter - baseline
        if growth >= dead_letter_growth_wedge:
            findings.append((
                "wedge",
                f"dead-letter spiked +{growth} since last tick "
                f"(now {dead_letter}) — retries exhausting en masse",
            ))
        elif growth >= dead_letter_growth_degraded:
            findings.append((
                "degraded",
                f"dead-letter grew +{growth} since last tick "
                f"(now {dead_letter})",
            ))

    if not findings:
        return None

    severity = "wedge" if any(s == "wedge" for s, _ in findings) else "degraded"
    return Signal(
        cls="queue_backlog",
        subject="meshforge-gateway",
        severity=severity,
        detail=(
            "Persistent queue backpressure: "
            + "; ".join(d for _, d in findings)
            + ". Check /api/gateway/queue and the gateway journal for "
            "the failing destination."
        ),
        issue_ref=74,
        extra={
            "queue_depth": queue_depth,
            "max_queue_size": max_queue_size,
            "dead_letter": dead_letter,
            "dead_letter_baseline": baseline,
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: delivery confirmation stall (Issue #74)
# ─────────────────────────────────────────────────────────────────────


def probe_delivery_confirmation_stall(
    *,
    host: str = "127.0.0.1",
    port: int = 5000,
    timeout_s: float = 3.0,
    min_sent: int = 20,
    rate_degraded: float = 0.50,
    rate_wedge: float = 0.10,
) -> Optional[Signal]:
    """Sends flow but confirmations collapsed (Issue #74).

    Closes the honest-signal gap where the bridge's own status reads
    HEALTHY while nothing confirms: its rolling windows decay to
    quiet/healthy when events stop, so a gateway shouting into a void
    looks fine from inside. The watchdog judges from outside instead.

    Judges a WINDOWED rate from the snapshot's recent-events ring
    (last 50, newest-last) — the lifetime-cumulative confirmation_rate
    would mask a recent collapse on a long-lived box.

    Self-guards (silence is NOT failure here — the explicit inversion
    of channel_feed_dark):
      - transport/shape errors → None (other probes own those)
      - confirmation_rate is None (zero traffic ever) → None
      - ring SENT < min_sent → None (low-traffic box: one unconfirmed
        message tanks the ratio; busy gateways canary for the fleet)

    Severities: ring rate ≤ 10% → wedge, ≤ 50% → degraded.
    """
    url = f"http://{host}:{port}/api/gateway/delivery"
    try:
        with urlopen(url, timeout=timeout_s) as resp:
            payload = json.loads(resp.read())
    except (URLError, socket.timeout, json.JSONDecodeError, OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None

    if payload.get("confirmation_rate") is None:
        # delivery_counters returns None iff sent == 0 — no traffic
        # has ever flowed; nothing to judge.
        return None

    recent = payload.get("recent")
    if not isinstance(recent, list):
        return None

    ring_sent = sum(1 for e in recent
                    if isinstance(e, dict) and e.get("state") == "sent")
    ring_confirmed = sum(1 for e in recent
                         if isinstance(e, dict) and e.get("state") == "confirmed")
    if ring_sent < min_sent:
        return None

    ring_rate = ring_confirmed / ring_sent
    if ring_rate > rate_degraded:
        return None

    severity = "wedge" if ring_rate <= rate_wedge else "degraded"
    return Signal(
        cls="delivery_confirmation_stall",
        subject="meshforge-gateway",
        severity=severity,
        detail=(
            f"Delivery confirmations collapsed: {ring_confirmed}/"
            f"{ring_sent} confirmed in the recent-events window "
            f"({ring_rate:.0%}) while sends keep flowing. Receivers "
            f"aren't acking — check RNS paths to the fan-out peers "
            f"and /api/gateway/delivery drop_reasons."
        ),
        issue_ref=74,
        extra={
            "ring_sent": ring_sent,
            "ring_confirmed": ring_confirmed,
            "ring_rate": round(ring_rate, 3),
            "cumulative_confirmation_rate": payload.get("confirmation_rate"),
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


def _journal_newest_match(
    unit: str,
    pattern: str,
    lookback: str,
    journalctl_path: str = "journalctl",
) -> Optional[str]:
    """Newest journal line of ``unit`` matching ``pattern`` within ``lookback``.

    Returns the ``short-unix``-formatted line (epoch-seconds first token) or
    None on no match / journalctl unavailable / timeout. ``-r -n 1`` makes the
    busy-feed case cheap (journalctl stops at the first newest-first match);
    the no-match case is bounded by ``--since`` and the subprocess timeout.
    """
    try:
        proc = subprocess.run(
            [
                journalctl_path, "-u", unit, "--since", f"-{lookback}",
                "-g", pattern, "-r", "-n", "1", "-o", "short-unix",
                "-q", "--no-pager",
            ],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    # rc 1 = "no entries matched" on some systemd builds; only >1 is an error.
    if proc.returncode not in (0, 1):
        return None
    lines = proc.stdout.strip().splitlines()
    return lines[0] if lines else None


def _short_unix_ts(line: str) -> Optional[float]:
    """Parse the epoch timestamp from a ``-o short-unix`` journal line."""
    try:
        return float(line.split(None, 1)[0])
    except (ValueError, IndexError):
        return None


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


# ─────────────────────────────────────────────────────────────────────
# Probe: MQTT root-topic drift (Issue #77 — the msh/US split guard)
# ─────────────────────────────────────────────────────────────────────

DEFAULT_MQTT_ROOT_DEBOUNCE_PATH = "/var/lib/meshforge/mqtt_root_debounce.json"

# Extract the effective root prefix from a meshtasticd json-uplink journal
# line: "JSON publish message to msh/2/json/LongFast/!32962f10, 163 bytes:..."
# → "msh". A region-form root (mqtt.root="msh/US") yields "msh/US" — exactly
# the pre-unification moc2 drift shape this probe exists to catch.
_MQTT_PUBLISH_TOPIC_RE = re.compile(
    r"JSON publish message to (\S+?)/2/json/"
)

# The GatewayConfig dataclass default (gateway/config.py MQTTBridgeConfig).
# Used when gateway.json omits the key — that IS the consumer's effective
# value, so comparing against it is honest, not a guess.
_GATEWAY_DEFAULT_ROOT_TOPIC = "msh"


def _read_declared_root_topic(service_user) -> Optional[str]:
    """Read ``mqtt_bridge.root_topic`` from the service user's gateway.json.

    The watchdog runs as sandboxed root — home is derived from the service
    user and READ directly, never escalate (the rns_version_drift lesson).
    Returns the configured value, the GatewayConfig default when the key is
    absent (that is the consumer's effective root), or None when the file is
    unreadable/unparseable (indeterminate → caller stays silent).
    """
    if not service_user:
        return None
    try:
        import pwd
        home = pwd.getpwnam(service_user).pw_dir
        path = os.path.join(home, ".config", "meshforge", "gateway.json")
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        mqtt = data.get("mqtt_bridge")
        if not isinstance(mqtt, dict):
            return _GATEWAY_DEFAULT_ROOT_TOPIC
        root = mqtt.get("root_topic", _GATEWAY_DEFAULT_ROOT_TOPIC)
        if isinstance(root, str) and root:
            return root.strip().strip("/")
        return _GATEWAY_DEFAULT_ROOT_TOPIC
    except (KeyError, OSError, ValueError, TypeError):
        return None


def probe_mqtt_root_drift(
    *,
    unit: str = "meshtasticd.service",
    lookback: str = "6h",
    journalctl_path: str = "journalctl",
    systemctl_path: str = "systemctl",
    main_pid: Optional[int] = None,
    newest_line_fn=None,
    declared_root: Optional[str] = None,
    service_user_fn=None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """Fire when the radio's MQTT publish root drifts from the declared root.

    Issue #77 (2026-06-06 fleet unification): the fleet standardized on an
    explicit ``mqtt.root msh`` after the msh/US topic-root split silently
    fractured the uplink namespace (moc2 flipped 06-06 23:41). The remaining
    hole: a zero-config radio join (or a factory-reset radio) reintroduces a
    divergent root and every consumer pinned to the declared root partially
    or fully misses its feed — the dark-feed class again, but with the CAUSE
    visible hours before ``channel_feed_dark`` can prove the symptom.

    Local invariant, no fleet consensus needed (brokers are per-box islands):
    the root prefix the radio actually publishes under — observable in
    meshtasticd's json-uplink journal lines, ``JSON publish message to
    <root>[/<region>]/2/json/<channel>/!<id>`` — must equal the box's own
    declared consumer root (``gateway.json mqtt_bridge.root_topic``; an
    absent key means the GatewayConfig default ``msh``, which is the
    effective value, so the comparison stays honest).

    Observation is journal-only: never queries the radio (a per-tick CLI
    ``--get mqtt.root`` would open a TCP connection against the PhoneAPI —
    the #17 contention class this probe family exists to police).

    Self-guards (returns None):
    - meshtasticd inactive (``service_inactive`` owns that)
    - no json publish lines in the lookback (unobservable ≠ drift — the
      RX-only collector case, same gate as ``channel_feed_dark``)
    - gateway.json unreadable / service user unresolvable (indeterminate)
    - drift seen but not yet ``debounce_ticks`` consecutive ticks (rides out
      an operator mid-rotation: radio flipped before config, or vice versa)

    Recovery: ``meshtastic --host localhost --set mqtt.root <declared>``
    (or fix root_topic in gateway.json if the radio is the intended truth),
    then verify the next journal publish line carries the declared root.
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

    line = newest_line_fn("JSON publish message to ")
    if line is None:
        return None  # no json uplink at all — unobservable, not drift
    m = _MQTT_PUBLISH_TOPIC_RE.search(line)
    if m is None:
        return None  # publish line shape changed — indeterminate, not drift
    observed = m.group(1).strip("/")

    if declared_root is not None:
        declared = declared_root.strip().strip("/")
    else:
        user_fn = service_user_fn
        if user_fn is None:
            from utils.rns_tree_perms import _read_rnsd_user
            user_fn = _read_rnsd_user
        declared = _read_declared_root_topic(user_fn())
    if not declared:
        return None  # declared side indeterminate — never alarm on a guess

    sp = state_path or DEFAULT_MQTT_ROOT_DEBOUNCE_PATH
    if observed == declared:
        _save_parity_streak(sp, 0)
        return None

    streak = _load_parity_streak(sp) + 1
    _save_parity_streak(sp, streak)
    if streak < debounce_ticks:
        return None  # drift seen, not yet confirmed across consecutive ticks

    detail = (
        f"Radio publishes MQTT under root '{observed}' but this box's "
        f"declared consumer root is '{declared}' "
        f"(gateway.json mqtt_bridge.root_topic) — confirmed over {streak} "
        f"consecutive ticks. Consumers subscribed under '{declared}' are "
        f"partially or fully deaf to this radio's uplink (the msh/US split, "
        f"Issue #77; a zero-config radio join is the usual cause). Fix: "
        f"meshtastic --host localhost --set mqtt.root '{declared}' (or "
        f"correct root_topic in gateway.json if the radio is the intended "
        f"truth), then verify the next 'JSON publish message to' journal "
        f"line carries '{declared}'."
    )
    return Signal(
        cls="mqtt_root_drift",
        subject="meshtasticd",
        severity="degraded",
        detail=detail,
        issue_ref=77,
        extra={
            "observed_root": observed,
            "declared_root": declared,
            "streak": streak,
            "lookback": lookback,
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Helpers shared by the runner
# ─────────────────────────────────────────────────────────────────────


def signal_to_dict(sig: Signal, *, first_seen_ts: Optional[float] = None) -> dict:
    """Serialize a Signal to the on-disk JSON shape.

    ``first_seen_ts`` (the runner's edge-transition tracker) is
    injected here so the on-disk record carries when this signal first
    appeared in addition to the latest probe tick.
    """
    out = {
        "class": sig.cls,
        "subject": sig.subject,
        "severity": sig.severity,
        "detail": sig.detail,
    }
    if sig.issue_ref is not None:
        out["issue_ref"] = sig.issue_ref
    if first_seen_ts is not None:
        out["first_seen"] = first_seen_ts
    if sig.extra:
        out["extra"] = sig.extra
    return out


__all__ = [
    "SIGNAL_CLASSES",
    "SEVERITIES",
    "Signal",
    "signal_to_dict",
    "probe_rns_namespace_collision",
    "probe_main_thread_wedge",
    "probe_lxmf_process_wedge",
    "probe_rns_shared_instance_responsive",
    "probe_rns_interface_down_peer_reachable",
    "probe_rns_rpc_responsive",
    "_tcp_reachable",
    "probe_http_local",
    "probe_fd_exhaustion",
    "probe_phoneapi_tcp_leak",
    "probe_foundation_drift",
    "probe_parity_drift",
    "probe_rns_version_drift",
    "probe_role_drift",
    "probe_channel_feed_dark",
    "probe_mqtt_root_drift",
    "probe_delivery_write_canary",
    "probe_service_inactive",
    "probe_tracer_peer_unreachable",
]
