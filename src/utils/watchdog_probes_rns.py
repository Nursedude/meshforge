"""Watchdog probes — RNS substrate failure shapes.

Namespace collision (#69), main-thread / LXMF-process wedge (#68), shared
instance unresponsive, RPC wedge (#72), interface-down-peer-reachable.
Part of the ``watchdog_probes`` split (2026-06-09) — import via the
``utils.watchdog_probes`` hub, not from here.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
from collections import deque
from statistics import median
from typing import TYPE_CHECKING, List, Optional, Tuple

from utils.watchdog_probe_core import (  # noqa: F401
    Signal,
    _resolve_main_pid_status,
    note_disposition,
    note_unit_presence_gate,
)
from utils import tx_guard

if TYPE_CHECKING:
    from utils.rns_status_parser import RNSStatus

# ─────────────────────────────────────────────────────────────────────
# Shared: the @rns/* listener table
# ─────────────────────────────────────────────────────────────────────


def _rns_listener_tokens(
    *, ss_path: str = "ss", ss_output: Optional[str] = None,
) -> Optional[List[str]]:
    """Every ``@rns/<name>`` token the kernel is advertising, or None.

    None means the table is UNOBSERVABLE (ss missing/timed out/nonzero) —
    which must never be allowed to read as "no listeners exist"
    (honest_failure_modes #2). ``ss_output`` is injectable for tests.

    ⚠️ The tokens are the kernel's DISPLAY form: ``ss`` splits columns on
    whitespace, so a spaced instance_name is truncated at the first space
    (``volcano ai rns`` → ``volcano``). Compare with ``_token_is_ours``,
    never with raw equality.
    """
    if ss_output is None:
        try:
            proc = subprocess.run(
                [ss_path, "-xnpl"], capture_output=True, text=True, timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None
        if proc.returncode != 0:
            return None
        ss_output = proc.stdout
    return re.findall(r"@rns/(\S+)", ss_output)


def _token_is_ours(token: str, instance_name: str) -> bool:
    """Could this ss display token BE our instance's listener?

    Exact match, or the truncated-at-first-space display of our spaced
    name. Deliberately permissive: a false "yes" costs us a missed
    mismatch (under-fire), a false "no" would page on a healthy box.
    """
    if token == instance_name:
        return True
    return " " in instance_name and token == instance_name.split(" ", 1)[0]


# ─────────────────────────────────────────────────────────────────────
# Probe: RNS namespace collision (Issue #69)
# ─────────────────────────────────────────────────────────────────────


def _scan_rns_listener_owners(
    instance_name: str, *, ss_path: str = "ss",
) -> Optional[Tuple[dict, "subprocess.CompletedProcess"]]:
    """One ``ss -xnpl`` scan → ``({pid: comm}, proc)``, or None when ``ss``
    itself was unobservable (missing / timed out / OSError).

    N1 (2026-07-18, the #69/#82 class): ``ss`` splits columns on
    whitespace, so a SPACED instance_name ("volcano ai rns") is displayed
    truncated at the first space ("@rns/volcano") — an inline
    f"@rns/{instance_name}" needle matches NOTHING on exactly the
    incident boxes and the probe would note an affirmative CLEAN. Reuse
    the twin parser ``utils.rns_init._parse_ss_listener_line`` (Issue #82
    fix), which falls back to the truncated-needle form. rns_init imports
    no RNS / heavy modules at import time.
    """
    try:
        proc = subprocess.run(
            [ss_path, "-xnpl"], capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    from utils.rns_init import _parse_ss_listener_line

    owners: dict = {}  # pid -> ss comm name
    for line in proc.stdout.splitlines():
        parsed = _parse_ss_listener_line(line, instance_name)
        if parsed is not None:
            pid, comm = parsed
            owners[pid] = comm
    return owners, proc


def _classify_listener_owners(
    owners: dict, proc_root: str,
) -> Tuple[List[Tuple[int, str]], List[Tuple[int, str]], List[int]]:
    """Sort listener owners into ``(foreign, inverted, vanished)``.

    ``foreign`` — not RNS-family at all (the #69 squatter, wedge tier);
    ``inverted`` — RNS-family but not rnsd (degraded tier);
    ``vanished`` — ``/proc/<pid>/cmdline`` unreadable, i.e. the owner
    exited between ``ss`` and the read. A vanished owner is NEVER classified
    by its empty cmdline: the caller re-scans (finding 4, 2026-09-09).
    """
    from utils.rns_init import cmdline_is_rns_family, cmdline_is_rnsd_shaped

    foreign: List[Tuple[int, str]] = []
    inverted: List[Tuple[int, str]] = []
    vanished: List[int] = []
    for pid, comm in owners.items():
        if comm == "rnsd":
            continue  # ss-level comm fast-path: the designated host
        try:
            with open(f"{proc_root}/{pid}/cmdline", "rb") as fh:
                cmdline = fh.read().replace(b"\x00", b" ").decode(
                    "utf-8", errors="replace").strip()
        except OSError:
            vanished.append(pid)
            continue
        if cmdline_is_rnsd_shaped(cmdline):
            continue
        if cmdline_is_rns_family(cmdline):
            inverted.append((pid, cmdline))
        else:
            foreign.append((pid, cmdline))
    return foreign, inverted, vanished


def probe_rns_namespace_collision(
    instance_name: str,
    *,
    ss_path: str = "ss",
    proc_root: str = "/proc",
    rnsd_enabled: Optional[bool] = None,
) -> Optional[Signal]:
    """Verify ``@rns/<instance>`` LISTEN is owned by rnsd ITSELF.

    Two failure tiers (2026-06-09 rewrite — the old single-tier version
    allowlisted any cmdline containing "reticulum", which is EVERY fleet
    RNS client via ``--rnsconfig /etc/reticulum``; it stayed silent
    through a real 5-minute nomadnet inversion on moc1 while 3 boxes
    were struck in one reboot pass):

    - **wedge**: owner is not even RNS-family (the EOFError-on-first-RPC
      squatter class — original #69).
    - **degraded**: owner is RNS-family but NOT rnsd (nomadnet/meshchat/a
      client that self-hosted) while rnsd.service is enabled — clients
      work, but rnsd is STRANDED as a client; if the wrong host stops,
      every RNS client on the box EOFs. Recovery: stop the owner,
      restart rnsd, restart the owner (it rejoins as client).

    Fires nothing when rnsd.service is not enabled and the owner is
    RNS-family (a standalone host is a legitimate deployment), when no
    listener exists, or when ss is unavailable (indeterminate ≠ alarm).
    ``rnsd_enabled`` is injectable for tests; None → ask systemd.
    """
    if not instance_name:
        note_disposition("rns_namespace_collision", "inert",
                         reason="no rns instance name provided")
        return None

    scanned = _scan_rns_listener_owners(instance_name, ss_path=ss_path)
    if scanned is None:
        note_disposition("rns_namespace_collision", "indeterminate",
                         reason="ss unavailable/timed out; listeners unobservable")
        return None
    owners, proc = scanned

    if not owners:
        if proc.returncode != 0:
            note_disposition("rns_namespace_collision", "indeterminate",
                             reason="ss exited nonzero; listener table unobservable")
            return None
        # Matched ZERO listeners. That is only "clean" if there is no
        # @rns/* listener AT ALL — if the kernel is advertising some other
        # instance, we are looking for a name that does not exist on this
        # box and this probe is DARK, not healthy. The 2026-08-05 finding:
        # the watchdog was handed the box's HOSTNAME (from a stale /root
        # config) while rnsd served the configured spaced instance name
        # from /etc/reticulum, and this branch reported an
        # affirmative clean — the #69 foreign-owner detector blind and
        # green at the same time, on the box #69 happened to. The N1 fix
        # above hardened the PARSE for spaced names; nothing checked that
        # the NAME had a listener behind it at all.
        # rns_shared_instance_unresponsive owns the signal (it holds the
        # connect evidence) — one fault, one owner.
        others = _rns_listener_tokens(ss_output=proc.stdout)
        if others:
            note_disposition(
                "rns_namespace_collision", "indeterminate",
                reason=(f"no listener for instance_name {instance_name!r}; "
                        f"kernel advertises @rns/{others[0]} — probing a "
                        f"name that does not exist here (cannot judge)"),
            )
        else:
            note_disposition("rns_namespace_collision", "clean")
        return None  # no listener → not a collision

    foreign, inverted, vanished = _classify_listener_owners(owners, proc_root)

    if vanished and not foreign and not inverted:
        # TOCTOU (pass-3 finding 4, 2026-09-09). An owner whose
        # /proc/<pid>/cmdline is unreadable EXITED between ``ss`` and the
        # read — a dead pid cannot hold an abstract socket, so the realistic
        # cause is listener teardown in progress (an rnsd restart racing
        # this tick), not a squatter. The old branch fed the empty cmdline
        # to the family predicate, classified it ``foreign`` and paged a
        # WEDGE whose cure text said ``sudo kill <pid>`` for a pid that no
        # longer existed and may have been recycled. ``rns_init.
        # check_rns_listener_owner`` already carries the one-re-scan cure;
        # this is its twin. Re-scan once, no delay (a probe runs inside the
        # daemon loop — MF010): teardown finished (no listener) or the new
        # owner is readable, and anything still unreadable is honestly
        # ``indeterminate`` — never a kill directive against a ghost.
        rescan = _scan_rns_listener_owners(instance_name, ss_path=ss_path)
        gone = ", ".join(str(p) for p in vanished)
        if rescan is None:
            note_disposition(
                "rns_namespace_collision", "indeterminate",
                reason=(f"owner pid(s) {gone} exited between ss and /proc "
                        f"and the re-scan was unobservable — cannot classify"))
            return None
        owners, proc = rescan
        if not owners:
            note_disposition(
                "rns_namespace_collision", "indeterminate",
                reason=(f"owner pid(s) {gone} exited between ss and /proc; "
                        f"no @rns/{instance_name} listener on re-scan "
                        f"(teardown in progress — rnsd restart?)"))
            return None
        foreign, inverted, vanished = _classify_listener_owners(
            owners, proc_root)
        if vanished and not foreign and not inverted:
            note_disposition(
                "rns_namespace_collision", "indeterminate",
                reason=(f"owner pid(s) "
                        f"{', '.join(str(p) for p in vanished)} unreadable "
                        f"on two consecutive scans — cannot classify"))
            return None

    if foreign:
        pid, cmdline = foreign[0]
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
            extra={"pid": pid, "cmdline": cmdline_short, "tier": "foreign"},
        )

    if inverted:
        if rnsd_enabled is None:
            # Was called WITHOUT being imported until 2026-09-09 (pyflakes:
            # undefined name) — the runner passes rnsd_enabled=None, so the
            # #69 inverted tier raised NameError in production and blanked
            # the whole tick; every test had injected True/False.
            from utils.rns_init import _rnsd_unit_enabled
            rnsd_enabled = _rnsd_unit_enabled()
        if not rnsd_enabled:
            note_disposition("rns_namespace_collision", "inert",
                             reason="rnsd not enabled; standalone RNS-family host")
            return None  # standalone RNS-family host is a legitimate deployment
        pid, cmdline = inverted[0]
        cmdline_short = cmdline[:120] or "<process exited>"
        return Signal(
            cls="rns_namespace_collision",
            subject=f"@rns/{instance_name}",
            severity="degraded",
            detail=(
                f"@rns/{instance_name} is hosted by an RNS-family process "
                f"that is NOT rnsd: pid={pid} cmd={cmdline_short!r} — rnsd "
                f"is stranded as a client (#69 inversion; boot race). "
                f"Clients work but die together if this host stops. "
                f"Recovery: stop the owner, sudo systemctl restart rnsd, "
                f"start the owner again (it rejoins as client)."
            ),
            issue_ref=69,
            extra={"pid": pid, "cmdline": cmdline_short, "tier": "inverted"},
        )

    note_disposition("rns_namespace_collision", "clean")
    return None


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
        note_disposition("main_thread_wedge", "indeterminate",
                         reason="task stack unreadable; wedge scan impossible")
        return None  # can't read at all — return None, no false clear

    matched = next((p for p in _WEDGE_PATTERNS if p in stack), None)
    if matched is not None:
        return pid, matched, stack[:300]

    # Walk worker threads. /proc/<pid>/task lists every TID.
    task_dir = f"{proc_root}/{pid}/task"
    try:
        tids = [int(name) for name in os.listdir(task_dir) if name.isdigit()]
    except OSError:
        note_disposition("main_thread_wedge", "indeterminate",
                         reason="task dir unlistable; worker threads unscanned")
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
    note_disposition("main_thread_wedge", "clean")
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
        pid_status, pid = _resolve_main_pid_status(
            service_name, systemctl_path=systemctl_path
        )
        if pid is None:
            # ABSENT unit → inert: no task stacks to scan. "Should be running
            # but isn't" belongs to service_inactive, which answers for a
            # nonexistent unit too (verified 2026-08-12) — BUT only for units
            # in services_expected_active; watchdog_runner warns when a
            # wedge-checked unit is outside that list, or nobody would own
            # its absence. Policy in ONE place (2026-08-12 review).
            note_unit_presence_gate(
                "main_thread_wedge", pid_status,
                absent_reason=(
                    f"no {service_name} unit on this box; no threads to scan"
                ),
                unresolved_reason="MainPID unresolved; service inactive or systemctl error",
            )
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
        note_disposition("main_thread_wedge", "indeterminate",
                         reason="/proc unlistable; lxmf process scan impossible")
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
    if not signals:
        note_disposition("main_thread_wedge", "clean")
    return signals


# ─────────────────────────────────────────────────────────────────────
# Probe: RNS shared-instance unresponsive
# ─────────────────────────────────────────────────────────────────────


def probe_rns_shared_instance_responsive(
    instance_name: str,
    *,
    timeout_s: float = 2.0,
    ss_path: str = "ss",
    ss_output: Optional[str] = None,
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
    On ECONNREFUSED the listener table disambiguates (see that branch):
    rnsd genuinely refusing and rnsd absent are both indeterminate, but a
    listener existing under a DIFFERENT name means the watchdog itself is
    misconfigured and emits ``rns_instance_name_mismatch``.

    Distinct from rns_namespace_collision (which checks WHO owns the
    listener) — this checks WHETHER the owner is actually serving.
    Both fire on rnsd-side faults but at different layers, and both are
    keyed to ``instance_name``, which is why a wrong name blinds BOTH.
    """
    if not instance_name:
        for _cls in ("rns_shared_instance_unresponsive",
                     "rns_instance_name_mismatch"):
            note_disposition(_cls, "inert",
                             reason="no rns instance name provided")
        return None

    # Abstract Unix socket address: leading NUL byte then the name.
    # `@rns/<name>` in `ss -xnpl` is the kernel's display form.
    addr = "\x00rns/" + instance_name
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except OSError as exc:
        # EMFILE/ENFILE (the #73 fd-leak class) or ENOBUFS on the WATCHDOG'S
        # own process: the connect was never attempted, so this says nothing
        # about rnsd's accept path. Until 2026-09-09 this line sat outside
        # the try and the exception escaped the probe into the tick handler.
        for _cls in ("rns_shared_instance_unresponsive",
                     "rns_instance_name_mismatch"):
            note_disposition(
                _cls, "indeterminate",
                reason=(f"socket() failed before connect "
                        f"({type(exc).__name__}: {exc}) — the watchdog's own "
                        f"fd budget, not rnsd; accept path unobserved"))
        return None
    sock.settimeout(timeout_s)
    try:
        sock.connect(addr)
    except FileNotFoundError:
        # Effectively unreachable for an ABSTRACT socket: Linux answers a
        # nonexistent abstract address with ECONNREFUSED, never ENOENT
        # (measured 2026-08-05). Retained only so a future filesystem-path
        # socket keeps a correct branch.
        sock.close()
        for _cls in ("rns_shared_instance_unresponsive",
                     "rns_instance_name_mismatch"):
            note_disposition(_cls, "indeterminate",
                             reason="listener absent; rnsd down — "
                                    "service_inactive owns")
        return None
    except ConnectionRefusedError:
        # ECONNREFUSED is THREE different facts wearing one costume, and
        # the connect alone cannot tell them apart:
        #   a) rnsd is shutting down / not serving  → transient, not ours
        #   b) no @rns/* listener at all            → service_inactive owns
        #   c) a listener exists under a DIFFERENT name → WE are misconfigured
        # Until 2026-08-05 all three were reported as (a), so (c) —
        # permanent, self-inflicted blindness — wore the costume of a
        # benign transient for 8.8 days on the federator box while this
        # probe and rns_namespace_collision were both dark. Consult the
        # listener table to separate them. Only reached on the refused
        # path, so the healthy tick still spawns no subprocess.
        sock.close()
        tokens = _rns_listener_tokens(ss_path=ss_path, ss_output=ss_output)
        if tokens is None:
            for _cls in ("rns_shared_instance_unresponsive",
                         "rns_instance_name_mismatch"):
                note_disposition(
                    _cls, "indeterminate",
                    reason=("connect refused and listener table unobservable "
                            "(ss unavailable) — cannot judge"))
            return None
        if any(_token_is_ours(t, instance_name) for t in tokens):
            note_disposition(
                "rns_shared_instance_unresponsive", "indeterminate",
                reason="connect refused; rnsd shutting down or not serving")
            note_disposition(
                "rns_instance_name_mismatch", "clean",
                reason="a listener exists under our own name")
            return None
        if not tokens:
            for _cls in ("rns_shared_instance_unresponsive",
                         "rns_instance_name_mismatch"):
                note_disposition(
                    _cls, "indeterminate",
                    reason="listener absent; rnsd down — "
                           "service_inactive owns")
            return None
        return Signal(
            cls="rns_instance_name_mismatch",
            subject=f"@rns/{instance_name}",
            severity="degraded",
            detail=(
                f"the watchdog is probing @rns/{instance_name} but no such "
                f"listener exists — the kernel advertises "
                f"{', '.join('@rns/' + t for t in sorted(set(tokens))[:3])}. "
                f"Every RNS probe keyed to this name is DARK: "
                f"rns_shared_instance_unresponsive cannot see the #68 "
                f"connect-hang wedge, and rns_namespace_collision cannot "
                f"see the #69 foreign-owner class. Nothing is wrong with "
                f"rnsd — the watchdog is looking in the wrong place. Fix: "
                f"reconcile instance_name so the watchdog reads the config "
                f"rnsd actually runs with (its unit's --config), then "
                f"confirm with `sudo ss -xnpl | grep @rns/`. "
                f"See 2026-08-05 persistent_issues entry."
            ),
            extra={
                "probed_name": instance_name,
                "listener_tokens": sorted(set(tokens))[:8],
            },
        )
    except socket.timeout:
        sock.close()
        # A HANG proves something is bound at our exact name — the name is
        # right, the daemon behind it is wedged. Say so, or the mismatch
        # class reads dark on exactly the boxes that are in trouble.
        note_disposition("rns_instance_name_mismatch", "clean",
                         reason="connect to our own name hung — it is bound")
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
        for _cls in ("rns_shared_instance_unresponsive",
                     "rns_instance_name_mismatch"):
            note_disposition(_cls, "indeterminate",
                             reason="connect OSError; accept path unobservable")
        return None
    # Connected successfully — healthy. Close cleanly without sending
    # any RNS protocol bytes (which would confuse rnsd's accept loop).
    try:
        sock.close()
    except OSError:
        pass
    note_disposition("rns_shared_instance_unresponsive", "clean")
    note_disposition("rns_instance_name_mismatch", "clean",
                     reason="connect to our own name succeeded")
    return None


# ─────────────────────────────────────────────────────────────────────
# Probe: RNS RPC unresponsive — rnstatus hangs though the socket accepts
# ─────────────────────────────────────────────────────────────────────


# ── rnstatus-timeout confirmation (2026-09-08) ───────────────────────
#
# A single `rnstatus` run exceeding its bound is NOT evidence that rnsd's
# RPC is wedged. `rnstatus` is a fresh interpreter that imports RNS
# before it speaks one byte of RPC, so its WALL TIME measures the box's
# CPU/IO headroom at least as much as rnsd's health. Measured on moc3,
# 2026-09-08 — the whole false page, end to end:
#
#   06:36:34  apt-daily-upgrade starts (consumed 61s CPU, 273 MB, 35 MB swap)
#   06:37:41  rnstatus timed out  ->  signal NEW rns_rpc_unresponsive
#   06:37:49  ...while the gateway's own rnsd RPC calls were logging
#             `rpc[rnsd.path_table_read] ok 0.000s` every 10 seconds,
#             uninterrupted, straight THROUGH the declared "wedge"
#   06:37:53  apt-daily-upgrade finishes
#   06:38:26  signal CLEARED — no operator action, nothing restarted
#
# rnsd's RPC round-trip was sub-millisecond at the exact second we told
# the operator it was wedged. The claim was correctly derived from its
# measurement; the measurement was of the wrong quantity
# (calibrated_claims, "Coverage": ask what would still trip this check
# if the subject were perfectly healthy — answer: any CPU starvation of
# a subprocess spawn).
#
# ⚠️ Why this was dangerous and not merely noisy: the signal's detail
# tells the operator to restart rnsd, and rapid rnsd restart cycling is
# precisely what opens the `@rns` ownership race (#69, and the standing
# "do NOT rapid-cycle rnsd restarts fleet-wide" rule in
# persistent_issues). The instrument's recommended cure was the fleet's
# known outage trigger.
#
# The discriminator is PERSISTENCE. A genuine #68/#69 wedge holds until
# rnsd is restarted — minutes to hours. CPU contention passes within a
# tick or two (every false positive observed cleared inside ONE tick).
# So require N consecutive timed-out observations before converting them
# into a wedge claim. Until confirmed the class reads `indeterminate`,
# never `clean`: the blind moment gets its own witness rather than being
# averaged into health (honest_failure_modes #2).
#
# State is in-memory ON PURPOSE. The watchdog is a long-lived daemon
# (verified: one PID across ticks), and a streak counter on disk would
# inherit the 2026-09-02 debounce-saver trap — an unwritable
# /var/lib/meshforge froze three probes' streaks below their threshold
# so they could never fire at all. A daemon restart resets the streak,
# which is correct: a restart also resets the wedge context.
_RPC_CONFIRM_TICKS_ENV = "MESHFORGE_RNS_RPC_CONFIRM_TICKS"
_DEFAULT_RPC_CONFIRM_TICKS = 3

_rpc_timeout_streak = 0


# ── per-box rnstatus latency baseline (2026-09-08) ───────────────────
#
# The confirmation above fixed WHAT we measure and WHETHER it persists. It
# did not fix what we measure AGAINST. The wedge arm fires on a fixed 8s
# subprocess timeout, and the fleet is heterogeneous — Pi 4, Pi 5, a cloud
# VPS, USB vs SPI radios. Measured 2026-09-08: moc3's healthy rnstatus is
# ~1.2s (6 runs, 1.19-1.57s). So on that box the alarm needs a 6.7x
# degradation, and **a box degrading 1.2s -> 6s never trips 8s at all**.
# (Boundary note: comparison is strictly greater-than the threshold, so a
# run sitting exactly ON the multiple reads clean — deliberate, so the
# alarm needs a real excursion rather than a tie.)
# An absolute threshold on a heterogeneous fleet does not merely misfire;
# it FALLS SILENT on exactly the box that has drifted, which is the
# failure this whole file exists to refuse.
#
# So: judge each box against ITSELF. Keep a rolling window of healthy
# rnstatus durations and treat a sustained multiple of that box's own
# median as the same condition the wedge arm catches, seen earlier and on
# boxes whose normal is slow. Same signal class, severity `degraded` — no
# new class (the 09-03 volume brake: add a class only by replacing one).
#
# ⚠️ The trap in every adaptive baseline is that it LEARNS THE ILLNESS: a
# slow drift raises the median, the median raises the threshold, and the
# detector silently re-normalises around a sick box. Two guards:
#   1. Samples judged slow are NOT admitted. While the condition holds the
#      median stays at pre-degradation values, so it keeps alarming instead
#      of adapting away. Recovery resumes learning.
#   2. The baseline may only make the threshold TIGHTER than the hard
#      timeout, never looser — it can add sensitivity, never remove it.
# And a warming baseline is disclosed, never silent: until it has enough
# samples the class reports coverage judged=1 of enrolled=2 arms, so a
# half-armed detector cannot read as a tidy `clean`.
#
# In-memory (deque, process-lifetime) for the same reason as the streak: on
# disk it would inherit the 2026-09-02 debounce-saver trap, where an
# unwritable state dir froze three probes below threshold forever.
_RNSTATUS_BASELINE_MAXLEN = 60        # ~30 min of ticks at the 30s cadence
_RNSTATUS_BASELINE_MIN_SAMPLES = 12   # don't judge a box before we know it
_RNSTATUS_SLOW_FACTOR_ENV = "MESHFORGE_RNS_RPC_SLOW_FACTOR"
# 3.0, not 5.0: the arm only earns its keep in the band BELOW the hard
# timeout. On moc3's measured 1.2s median, 5x puts the threshold at 6.0s
# and leaves a 6.0-8.0s window to catch anything in — nearly nothing. 3x
# puts it at 3.6s and catches 3.6-8.0s, while a 3x slowdown on a box whose
# normal is stable is still plainly anomalous. On a box whose median is
# already slow the threshold exceeds the timeout and the WEDGE arm fires
# first, which is correct: this arm adds sensitivity, it never gates.
_DEFAULT_RNSTATUS_SLOW_FACTOR = 3.0   # multiple of the box's OWN median
_RNSTATUS_SLOW_FLOOR_S = 2.0          # never alarm on a sub-2s absolute run

_rnstatus_durations: "deque[float]" = deque(maxlen=_RNSTATUS_BASELINE_MAXLEN)
_rpc_slow_streak = 0
#: Subject of the latency (degraded) arm — deliberately NOT the wedge arm's
#: ``"rnsd"``, so the tracker sees a wedge after a slow phase as a NEW
#: transition (see the comment at the emit site).
_RPC_LATENCY_SUBJECT = "rnsd:latency"


def _rnstatus_slow_factor() -> float:
    """Multiple of this box's own median that counts as degraded."""
    raw = os.environ.get(_RNSTATUS_SLOW_FACTOR_ENV)
    if raw is None:
        return _DEFAULT_RNSTATUS_SLOW_FACTOR
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_RNSTATUS_SLOW_FACTOR
    return value if value > 1.0 else _DEFAULT_RNSTATUS_SLOW_FACTOR


def reset_rnstatus_baseline() -> None:
    """Clear the latency baseline and slow streak. For tests and cold start."""
    global _rpc_slow_streak
    _rnstatus_durations.clear()
    _rpc_slow_streak = 0


def _rnstatus_median() -> Optional[float]:
    """Median of the healthy-sample window, or None while warming."""
    if len(_rnstatus_durations) < _RNSTATUS_BASELINE_MIN_SAMPLES:
        return None
    return median(_rnstatus_durations)


def _rpc_confirm_ticks() -> int:
    """Consecutive timed-out ticks required before claiming a wedge.

    Resolved per call so an operator can retune without a restart.
    Default 3: at the ~30s watchdog cadence the condition must hold
    across ~60s of wall time, which clears every contention excursion
    observed (all <1 tick) while adding negligible latency to a real
    wedge that would otherwise persist for hours.
    """
    raw = os.environ.get(_RPC_CONFIRM_TICKS_ENV)
    if raw is None:
        return _DEFAULT_RPC_CONFIRM_TICKS
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_RPC_CONFIRM_TICKS
    return value if value >= 1 else _DEFAULT_RPC_CONFIRM_TICKS


def reset_rns_rpc_timeout_streak() -> None:
    """Clear the consecutive-timeout streak. For tests and cold start."""
    global _rpc_timeout_streak
    _rpc_timeout_streak = 0


def _cpu_pressure_context() -> str:
    """Short, best-effort load/PSI string carried WITH the wedge claim.

    Evidence, never gating logic: it lets the operator tell "rnsd is
    wedged" from "this Pi was buried" at a glance, on the page itself,
    instead of reconstructing it from journals afterwards — which is the
    whole cost this defect imposed. Unreadable /proc yields "unknown"
    rather than a confident-looking blank.
    """
    parts = []
    try:
        with open("/proc/loadavg", "r") as fh:
            parts.append("loadavg " + " ".join(fh.read().split()[:3]))
    except OSError:
        parts.append("loadavg unknown")
    # ONE PSI parser (finding 19): the host module owns ``some avg10``
    # reading for cgroup memory.pressure; /proc/pressure/cpu is the same
    # format. Unreadable/CONFIG_PSI-off → None → omitted, never a blank.
    from utils.watchdog_probes_host import _read_psi_some_avg10
    psi = _read_psi_some_avg10("/proc/pressure/cpu")
    if psi is not None:
        parts.append(f"cpu-psi-some10 {psi:g}")
    return "; ".join(parts)


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
    subprocess TIMEOUT sets ``timed_out=True``. Binary missing likewise
    returns None (no false alarm on RNS-less boxes).

    Distinguishing wedge from a BUSY BOX (2026-09-08): one timeout is not
    enough. ``rnstatus`` wall time also measures CPU/IO headroom, so
    ordinary background load (apt-daily-upgrade, an hourly analysis cron)
    starves it on a Pi while rnsd answers RPC sub-millisecond throughout.
    We therefore require ``_rpc_confirm_ticks()`` CONSECUTIVE timed-out
    observations before emitting; short of that the class is noted
    ``indeterminate`` with the streak and the box's load in the reason.
    See the block comment above for the measured incident and why a false
    page here is dangerous rather than merely noisy.

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

    global _rpc_timeout_streak, _rpc_slow_streak

    if not status.timed_out:
        _rpc_timeout_streak = 0
        if status.parse_error:
            # rnsd down / binary missing: the run measured nothing about
            # RPC latency, so it must not enter the baseline — and it breaks
            # the slow streak: "N CONSECUTIVE slow ticks" must not span an
            # rnsd restart (finding 2, 2026-09-09).
            _rpc_slow_streak = 0
            note_disposition("rns_rpc_unresponsive", "indeterminate",
                             reason="rnstatus failed fast (rnsd down or binary missing)")
            return None
        return _judge_rnstatus_latency(status.duration_s)

    # A timeout is the wedge arm's observation; the latency arm saw no
    # duration, so its consecutive-slow streak restarts (finding 2).
    _rpc_slow_streak = 0
    _rpc_timeout_streak += 1
    needed = _rpc_confirm_ticks()
    pressure = _cpu_pressure_context()

    if _rpc_timeout_streak < needed:
        # Observed, not yet decidable. A wedge persists; CPU contention
        # passes. Never `clean` here — that would launder the one moment
        # we genuinely cannot see into a healthy reading.
        note_disposition(
            "rns_rpc_unresponsive", "indeterminate",
            reason=(
                f"rnstatus exceeded its bound on {_rpc_timeout_streak} of "
                f"{needed} consecutive tick(s) required to distinguish a "
                f"wedge from CPU starvation of the rnstatus subprocess "
                f"({pressure})"
            ),
        )
        return None

    return Signal(
        cls="rns_rpc_unresponsive",
        subject="rnsd",
        severity="wedge",
        detail=(
            f"rnstatus exceeded its timeout on {_rpc_timeout_streak} "
            "consecutive watchdog ticks — rnsd accepts shared-instance "
            "connects but the RPC round-trip is wedged "
            "(rpc_connection.recv hang/EOF). New RNS clients that get past "
            "connect still stall in init and destination lookups silently "
            "fail. "
            f"Box at the time: {pressure}. "
            "CONFIRM BEFORE ACTING: `timeout 8 rnstatus >/dev/null; echo $?` "
            "(124 = still wedged) and check whether rnsd's OWN RPC is "
            "answering in the journal (`rpc[rnsd.path_table_read] ok`) — a "
            "loaded box can starve the rnstatus subprocess while rnsd is "
            "healthy. Only then: sudo systemctl restart rnsd.service, then "
            "restart RNS-using services (meshforge-map, meshforge-echo, "
            "tracer). Do NOT rapid-cycle rnsd fleet-wide — that opens the "
            "@rns ownership race. See Issue #68/#69."
        ),
        issue_ref=68,
        extra={
            "timed_out": True,
            "consecutive_timeouts": _rpc_timeout_streak,
            "confirm_ticks": needed,
            "cpu_pressure": pressure,
        },
    )


def _judge_rnstatus_latency(duration_s: "Optional[float]") -> Optional[Signal]:
    """Second arm of ``rns_rpc_unresponsive``: is rnstatus slow FOR THIS BOX?

    Called only on a healthy, parsed rnstatus. Returns a ``degraded``
    Signal when the run is a sustained multiple of this box's own median,
    else None — and always leaves a disposition, including the honest
    "still warming" one.
    """
    global _rpc_slow_streak

    if duration_s is None:
        # No subprocess ran (injected status in tests, binary missing).
        # The wedge arm judged; the latency arm could not.
        note_disposition("rns_rpc_unresponsive", "clean",
                         reason="rnstatus responsive; no duration observed "
                                "(latency arm not applicable)",
                         coverage={"judged": 1, "enrolled": 2})
        return None

    baseline = _rnstatus_median()
    if baseline is None:
        # Warming. Admit the sample and DISCLOSE the half-armed state —
        # a warming detector must not read as a tidy clean.
        _rnstatus_durations.append(duration_s)
        _rpc_slow_streak = 0
        note_disposition(
            "rns_rpc_unresponsive", "clean",
            reason=(
                f"rnstatus responsive ({duration_s:.2f}s); latency baseline "
                f"warming {len(_rnstatus_durations)}/"
                f"{_RNSTATUS_BASELINE_MIN_SAMPLES} samples"
            ),
            coverage={"judged": 1, "enrolled": 2},
        )
        return None

    factor = _rnstatus_slow_factor()
    # The baseline may only TIGHTEN the hard timeout, never loosen it.
    threshold = max(_RNSTATUS_SLOW_FLOOR_S, baseline * factor)

    if duration_s <= threshold:
        _rnstatus_durations.append(duration_s)
        _rpc_slow_streak = 0
        note_disposition(
            "rns_rpc_unresponsive", "clean",
            reason=(f"rnstatus {duration_s:.2f}s vs box median "
                    f"{baseline:.2f}s (both arms judged)"),
        )
        return None

    # Slow. Do NOT admit the sample — an adaptive baseline that learns its
    # own illness re-normalises around a sick box and falls silent.
    _rpc_slow_streak += 1
    needed = _rpc_confirm_ticks()
    if _rpc_slow_streak < needed:
        note_disposition(
            "rns_rpc_unresponsive", "indeterminate",
            reason=(
                f"rnstatus {duration_s:.2f}s vs box median {baseline:.2f}s "
                f"(>{factor:.1f}x) on {_rpc_slow_streak} of {needed} "
                f"consecutive tick(s) required; {_cpu_pressure_context()}"
            ),
        )
        return None

    # Its OWN subject (finding 2, 2026-09-09). The wedge arm emits
    # ``("rns_rpc_unresponsive", "rnsd")``; had this arm shared that key,
    # ``SignalTracker.update`` — which treats a same-key signal as "still
    # active" with no severity comparison — would have swallowed a real
    # wedge that followed a slow-phase page: no NEW transition, no "restart
    # rnsd" page, the escalation invisible. A distinct subject is the
    # smaller, safer change (a tracker-side "severity rose = new
    # transition" rule would re-page every class on every flap of severity).
    # The slow→timeout hand-off therefore reads as CLEARED latency + NEW
    # wedge, which is the truth of what was observed.
    return Signal(
        cls="rns_rpc_unresponsive",
        subject=_RPC_LATENCY_SUBJECT,
        severity="degraded",
        detail=(
            f"rnstatus is taking {duration_s:.2f}s against this box's own "
            f"median of {baseline:.2f}s (>{factor:.1f}x) on "
            f"{_rpc_slow_streak} consecutive ticks — the RPC round-trip is "
            "degrading but has NOT yet crossed the hard timeout, so the "
            "wedge arm is still silent. This is the early shape of #68/#69, "
            f"and on a box whose normal is slow it is the ONLY shape that "
            f"ever fires. Box at the time: {_cpu_pressure_context()}. "
            "Check whether the box is merely busy before touching rnsd: a "
            "sustained multiple with an IDLE box is rnsd; with a loaded box "
            "it is contention. Do NOT rapid-cycle rnsd fleet-wide."
        ),
        issue_ref=68,
        extra={
            "duration_s": round(duration_s, 3),
            "baseline_median_s": round(baseline, 3),
            "slow_factor": factor,
            "consecutive_slow": _rpc_slow_streak,
            "baseline_samples": len(_rnstatus_durations),
            "cpu_pressure": _cpu_pressure_context(),
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: RNS interface Down while peer reachable (2026-05-30)
# ─────────────────────────────────────────────────────────────────────

# A routable TCPInterface display_name embeds the peer host:port, e.g.
#   "Regional RNS/192.0.2.38:4242"
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
        with tx_guard.probe_connect(), \
                socket.create_connection((host, port), timeout=timeout):
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
        note_disposition("rns_interface_down_peer_reachable", "indeterminate",
                         reason="rnstatus errored; interface table unobservable")
        return None
    # N2 (2026-07-18): empty rnstatus output / unrecognized error text
    # yields parse_error=None WITH interfaces=[] — but a healthy rnstatus
    # always prints at least the shared-instance block, so an empty
    # interface table means the table was never actually observed. Noting
    # clean here would be an affirmative green on an unobserved surface.
    if not status.interfaces:
        note_disposition("rns_interface_down_peer_reachable", "indeterminate",
                         reason="rnstatus output empty/unrecognized — "
                                "interface table unobservable")
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
        note_disposition("rns_interface_down_peer_reachable", "clean")
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


