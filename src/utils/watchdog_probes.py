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
    "probe_foundation_drift",
    "probe_delivery_write_canary",
    "probe_service_inactive",
    "probe_tracer_peer_unreachable",
]
