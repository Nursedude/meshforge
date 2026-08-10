"""Canonical guarded RNS (Reticulum) initialization chokepoint.

This module is the ONE place in MeshForge allowed to construct
``RNS.Reticulum()``. Every other caller routes through
:func:`open_reticulum` (the project-wide chokepoint) or, for the lab
echo/tracer daemons, the lower-level :func:`init_reticulum_with_watchdog`.
Lint rule **MF019** + ``TestRNSReticulumChokepoint`` enforce that no raw
construction exists elsewhere — the same "own it in code" enforcement
that tamed the meshtasticd TCP-contention class (Issue #17 / MF007).

Why a chokepoint (RNS T2-isolate arc, sub-arc B+C — 2026-05-29)
---------------------------------------------------------------
RNS upstream withdrew public support (the "Carrier Switch", Dec 2025), so
MeshForge OWNS the RNS dependency: we pin the version, contain its
failures, and carry our own patches. Containing failures means a single
guarded entry point that always does, every time:

1. **MF009 — configdir.** Never read the user's interface-bearing config
   (would cause EADDRINUSE when rnsd already owns the ports, Issue #12).
2. **Idempotent reuse.** RNS is a process singleton; the second
   ``RNS.Reticulum()`` raises ``OSError("Attempt to reinitialise ...")``.
   We return the existing instance instead of letting every caller
   re-implement the "reinitialise"/"already running" catch.
3. **Issue #69 — listener-owner preflight (fail-LOUD).** If a *foreign*
   daemon (e.g. a stray MeshAnchor ``daemon.py``) has claimed the
   ``@rns/<instance>`` shared-instance socket, every RNS client EOFs on
   the first RPC call. We raise a one-line operator-actionable error
   instead of the 30-minute-to-debug EOFError stack.
4. **Issue #68 — bounded connect probe (fail-OPEN).** rnsd can hard-wedge:
   the LISTEN socket is present but ``connect()`` blocks forever in the
   kernel ``unix_stream_connect`` (observed: a map server that stayed
   ``active (running)`` for 56 min but never bound ``:5000`` because the
   main-thread ``RNS.Reticulum()`` hung). A passive ``/proc/net/unix``
   existence scan PASSES against a wedged rnsd — only an *active* connect
   with a userland ``settimeout()`` can tell "present and accepting" from
   "present but wedged". So before constructing we probe; on timeout we
   return ``None`` (degrade) so the caller keeps serving its other legs
   instead of hanging the whole process. This is what makes NOC Home's
   "still routing on the other transport(s)" line literally true.

A hung ``RNS.Reticulum()`` constructor sits in an *uninterruptible* kernel
``connect()`` — SIGTERM queues behind it; only ``os._exit`` (or a probe
that prevents the construct from ever starting) escapes. So fail-open is
the PROBE's job; the watchdog around the construct (:func:`bounded_block`)
is only a last-resort backstop for the vanishingly rare "probe passed,
then rnsd wedged microseconds later" race.

See ``.claude/plans/rns_t2_isolate_arc.md`` and the
``project_rns_upstream_withdrawal_2026_05_29`` /
``project_upstream_dependency_governance_2026_05_29`` memories.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Iterator, Optional, Union

from utils.safe_import import safe_import
from utils import tx_guard

_RNS, _HAS_RNS = safe_import('RNS')

logger = logging.getLogger(__name__)

# Hard timeout for the RNS.Reticulum() constructor itself (watchdog backstop).
# Env override retains the legacy MESHFORGE_LAB_RNS_INIT_TIMEOUT name so the
# lab echo/tracer daemons keep their existing knob after the move from
# _lab_common.
RNS_INIT_TIMEOUT_S = float(os.environ.get("MESHFORGE_LAB_RNS_INIT_TIMEOUT", "60"))

# Timeout for the active AF_UNIX connect probe (Issue #68 fail-open gate).
# Short by design: a healthy rnsd accepts in sub-millisecond time, so a few
# seconds is generous; the point is to never block the calling thread.
DEFAULT_CONNECT_PROBE_TIMEOUT_S = float(
    os.environ.get("MESHFORGE_RNS_PROBE_TIMEOUT", "5")
)

# How long to wait for an *enabled* rnsd to claim `@rns/<instance>` before
# giving up (Issue #69 boot-race guard). The measured race window at boot is
# ~4s (echo claimed 13:00:05, rnsd lost at 13:00:09 — the federator box, 2026-06-06);
# 30s is generous without eating a oneshot unit's TimeoutStartSec budget.
DEFAULT_WAIT_FOR_RNSD_TIMEOUT_S = float(
    os.environ.get("MESHFORGE_RNS_WAIT_FOR_RNSD_TIMEOUT", "30")
)

# Cmdline substrings that are legitimate owners of an `@rns/<instance>`
# shared-instance LISTEN socket. Deliberately narrow: rnsd (canonical) and
# `reticulum` (some distros' wrapper). Other daemons that host an RNS
# instance via `share_instance = Yes` (MeshAnchor's daemon.py is the concrete
# Issue #69 instance, 2026-05-20) are NOT allowed — their RPC subprocess
# speaks a different dialect than rnsd and breaks every RNS client that joins
# as a shared-instance peer. The right deployment for a box hosting both
# projects is to run only one RNS host (rnsd) and have the other daemon join
# as a client.
_RNS_LISTENER_ALLOWED_PATTERNS = ("rnsd", "reticulum")

# Serializes the check-then-construct window in open_reticulum(). RNS is a
# process singleton: without this, two threads that both pass the
# _existing_instance() check race RNS.Reticulum(), and the loser sees
# OSError("Attempt to reinitialise Reticulum") — boilerplate that callers
# (rns_bridge pre-init, map collector) had to re-implement even though
# absorbing it is this chokepoint's stated contract (module docstring #2).
_CONSTRUCT_LOCK = threading.Lock()


def cmdline_is_rnsd_shaped(cmdline: str) -> bool:
    """STRICT owner test: is this cmdline rnsd itself (the fleet's designated
    @rns host)?

    Substring-over-cmdline is NOT enough here — every RNS client on this
    fleet is launched with ``--rnsconfig /etc/reticulum``, so the loose
    ``"reticulum" in cmdline`` test allowlisted the 2026-06-09 nomadnet
    hijacker and kept ``probe_rns_namespace_collision`` silent through a
    real 5-minute inversion (3 boxes struck in one reboot pass). Only the
    PROGRAM identity counts: argv[0]/argv[1] basename, the ``-m`` module
    form, or an rnsd pipx venv path.
    """
    if not cmdline:
        return False
    tokens = cmdline.split()
    for tok in tokens[:2]:
        base = tok.rsplit("/", 1)[-1].lower()
        if base == "rnsd" or base.startswith("rnsd"):
            return True
    if "RNS.Utilities.rnsd" in cmdline:
        return True
    if tokens and "/venvs/rnsd/" in tokens[0]:
        return True
    return False


def cmdline_is_rns_family(cmdline: str) -> bool:
    """LOOSE owner test: does this look like *some* RNS-protocol process
    (rnsd, nomadnet, meshchat, a client that self-hosted)? Used by the
    fail-loud preflight, whose job is only to catch NON-RNS squatters
    (the EOFError-on-first-RPC class) — an RNS-family wrong host keeps
    clients functional and is the PROBE's (paged) business, not a reason
    to refuse startup. Preserves the historical allowlist semantics.
    """
    if cmdline_is_rnsd_shaped(cmdline):
        return True
    low = (cmdline or "").lower()
    return any(pat in low for pat in _RNS_LISTENER_ALLOWED_PATTERNS)


# ---------------------------------------------------------------- listener owner


def _parse_ss_listener_line(line: str, instance_name: str) -> Optional[tuple]:
    """Extract ``(pid:int, cmd:str)`` from one ``ss -xnpl`` line for the
    ``@rns/<instance>`` socket. Returns None if the line doesn't match.

    Tolerant of the kernel's varying field count (the address column
    sometimes wraps). Anchors on the ``@rns/<instance>`` token and on the
    ``users:(("<cmd>",pid=<n>,...))`` tail that ``ss -p`` appends.

    Space-containing instance names (the federator box, 2026-06-06): ``ss`` splits
    its columns on whitespace, so an abstract socket named
    ``@rns/kilauea lab rns`` is *displayed* truncated at the first space
    (``@rns/kilauea``) — the full needle never matches and the Issue #69
    guard silently dies. Fall back to the truncated form, anchored on a
    trailing space so ``kilauea`` can't match a different ``kilauea-x``
    instance.
    """
    needle = f"@rns/{instance_name}"
    if needle not in line:
        if " " not in instance_name:
            return None
        truncated = f"@rns/{instance_name.split(' ', 1)[0]} "
        if truncated not in line:
            return None
    m = re.search(r'users:\(\("([^"]+)",pid=(\d+),', line)
    if not m:
        return None
    return int(m.group(2)), m.group(1)


def _read_instance_name_from_config(configdir: Union[str, os.PathLike]) -> Optional[str]:
    """Parse ``instance_name = <name>`` out of ``<configdir>/config``.

    Returns None if the file is absent or the directive isn't present — a
    missing config is normal during first-ever RNS init.
    """
    try:
        path = Path(configdir) / "config"
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if line.startswith("instance_name"):
                _, _, rhs = line.partition("=")
                name = rhs.strip()
                if name:
                    return name
    except OSError:
        pass
    return None


def check_rns_listener_owner(
    instance_name: str, _retry_on_vanished: bool = True
) -> Optional[str]:
    """Verify ``@rns/<instance_name>`` LISTEN owner looks like a real RNS
    process. Returns None on pass (or no listener found — let
    ``RNS.Reticulum()`` create one). Raises ``RuntimeError`` with a
    diagnostic message when the listener is owned by a process whose cmdline
    doesn't match any pattern in ``_RNS_LISTENER_ALLOWED_PATTERNS``.

    Why: a non-RNS process can claim ``@rns/<name>`` ahead of rnsd (e.g. a
    manually-launched MeshAnchor daemon that orphans to PID 1 and holds the
    abstract socket for hours). Every subsequent RNS client connects to that
    process, receives non-RNS-protocol bytes, and dies with EOFError from
    ``rpc_connection.recv()`` deep inside RNS — a trace that takes 30+
    minutes to root-cause. This preflight surfaces the collision in one line
    at process start (Issue #69).

    Owner vanished between ``ss`` and the ``/proc`` read: a dead pid cannot
    hold an abstract socket, so the realistic cause is listener teardown in
    progress (an rnsd restart racing this preflight), not a squatter. One
    fresh re-scan resolves that race — teardown finished (no listener) or
    the new owner is readable — instead of failing loud with a directive to
    ``kill`` a pid that already exited. An owner that is STILL unreadable on
    the second scan keeps the fail-loud contract (cannot prove it's allowed).
    """
    try:
        proc = subprocess.run(
            ["ss", "-xnpl"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        # ss missing or hung — don't fail-loud here; let RNS proceed and
        # surface its own error if there's a real problem.
        logger.debug("rns_init: listener preflight skipped (ss unavailable: %s)", exc)
        return None

    pids = set()
    for line in proc.stdout.splitlines():
        parsed = _parse_ss_listener_line(line, instance_name)
        if parsed:
            pids.add(parsed[0])

    if not pids:
        # No existing listener — RNS will create one. Nothing to check.
        return None

    # `ss -p` only reports the binary basename (e.g. "python3"), which is
    # identical for rnsd and any rogue python daemon. Read the full cmdline
    # from /proc to make the allowed-vs-suspicious determination.
    suspicious = []
    vanished = []
    full_cmdlines: dict = {}
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                cmdline = fh.read().replace(b"\x00", b" ").decode(
                    "utf-8", errors="replace").strip()
        except OSError:
            # Process died between ss and /proc read. A dead pid cannot own
            # the socket — defer judgment to one fresh re-scan below rather
            # than declaring a squatter we cannot name.
            full_cmdlines[pid] = ""
            vanished.append(pid)
            continue
        full_cmdlines[pid] = cmdline
        if not cmdline_is_rns_family(cmdline):
            suspicious.append(pid)

    if not suspicious and vanished:
        if _retry_on_vanished:
            time.sleep(0.2)
            return check_rns_listener_owner(
                instance_name, _retry_on_vanished=False)
        # Still unreadable on the fresh scan: cannot prove it's allowed —
        # keep the fail-loud contract with the honest dead-pid diagnostic.
        suspicious = vanished

    if suspicious:
        pid = suspicious[0]
        cmdline = full_cmdlines[pid] or "<unknown — process exited>"
        raise RuntimeError(
            f"RNS shared-instance listener @rns/{instance_name} is owned by "
            f"pid={pid} cmd={cmdline!r} — not an RNS process. Every RNS "
            f"client connecting to this socket will fail with EOFError on the "
            f"first RPC call. Fix: identify and stop this process "
            f"(`sudo kill {pid}`), then `sudo systemctl restart rnsd.service`."
        )

    owner_pid = next(iter(pids))
    logger.info(
        "rns_init: listener preflight OK — @rns/%s owned by pid=%d (%s)",
        instance_name, owner_pid, full_cmdlines[owner_pid],
    )
    return None


# ---------------------------------------------------------------- #68 connect probe


def _shared_instance_listener_present(instance_name: str) -> bool:
    """Passive: is an ``@rns/<instance>`` abstract socket listed in
    ``/proc/net/unix``? Reads a proc file, never connects — safe in any
    context. NOTE: presence does NOT imply the listener is *accepting*; a
    wedged rnsd still appears here. Use :func:`_probe_shared_instance_connect`
    to tell accepting from wedged.
    """
    target = f"@rns/{instance_name}"
    try:
        with open("/proc/net/unix", "r") as fh:
            for line in fh:
                if target in line:
                    return True
    except OSError:
        pass
    return False


def _probe_shared_instance_connect(
    instance_name: str, timeout_s: float
) -> bool:
    """Active, bounded connect to ``@rns/<instance>`` to tell a healthy rnsd
    from a wedged one (Issue #68). Returns True iff the shared-instance socket
    accepts a connection within ``timeout_s``; False on timeout (wedged) or
    refusal/absence.

    This is the fail-open gate. ``socket.settimeout()`` makes the connect
    *interruptible*, unlike RNS's internal uninterruptible connect that hangs
    the whole process in #68. The abstract-namespace address is
    ``"\\0rns/<instance>"`` (the kernel renders the leading null byte as
    ``@`` in ``ss``/``/proc/net/unix``). We connect and immediately close —
    rnsd's LocalInterface handles brief client churn cleanly, the same as any
    client that connects and disconnects.
    """
    addr = "\0rns/" + instance_name
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout_s)
        sock.connect(addr)
        return True
    except TimeoutError:
        logger.warning(
            "rns_init: @rns/%s connect probe timed out after %.1fs — rnsd "
            "appears wedged (Issue #68); degrading instead of constructing "
            "(would hang this thread in an uninterruptible connect).",
            instance_name, timeout_s,
        )
        return False
    except OSError as exc:
        # ConnectionRefusedError / FileNotFoundError / ENOENT etc. — the
        # listener is gone or not accepting. Distinct from the wedge above.
        logger.warning(
            "rns_init: @rns/%s connect probe failed (%s) — shared instance "
            "not accepting.", instance_name, exc,
        )
        return False
    finally:
        with contextlib.suppress(OSError):
            sock.close()


# ---------------------------------------------------------------- #69 boot race


def _rnsd_unit_enabled() -> bool:
    """True iff ``rnsd.service`` is enabled on this box — i.e. rnsd is the
    *designated* ``@rns/<instance>`` host even if it hasn't started yet.
    Lazy import keeps module import cheap; False on any error (no wait,
    behave as before).
    """
    try:
        from utils.service_check import is_service_enabled
        return is_service_enabled("rnsd")
    except Exception:
        return False


def _wait_for_rnsd_listener(
    instance_name: str,
    timeout_s: float = DEFAULT_WAIT_FOR_RNSD_TIMEOUT_S,
    poll_interval_s: float = 0.5,
) -> bool:
    """Issue #69 boot-race guard: the ``@rns/<instance>`` listener is absent
    but rnsd is ENABLED on this box — rnsd is the designated host and is
    almost certainly still starting (the measured window is ~4 seconds at
    boot). If the caller constructs ``RNS.Reticulum()`` now, IT becomes the
    shared-instance host: rnsd then silently joins as a client, none of its
    configured network interfaces ever come up, and every RNS destination on
    the box is ``no-route`` until an operator unwinds the ownership by hand
    (the federator box, 2026-06-06 — lab echo claimed the instance, the whole fleet
    tracer leg went dark).

    Polls the passive ``/proc/net/unix`` presence scan (cheap, no connect)
    until the listener appears or ``timeout_s`` elapses. Returns True when
    the listener appeared (caller proceeds and joins as a client), False on
    timeout (caller must NOT construct — fail loud or degrade, per its
    contract). The subsequent #68 connect probe still tells accepting from
    wedged; this only closes the who-binds-first race.
    """
    deadline = time.monotonic() + timeout_s
    logger.info(
        "rns_init: @rns/%s listener absent but rnsd.service is enabled — "
        "waiting up to %.0fs for rnsd to claim it instead of boot-claiming "
        "the shared instance (Issue #69 boot race).",
        instance_name, timeout_s,
    )
    while time.monotonic() < deadline:
        if _shared_instance_listener_present(instance_name):
            logger.info(
                "rns_init: @rns/%s listener appeared — joining as client.",
                instance_name,
            )
            return True
        time.sleep(poll_interval_s)
    logger.error(
        "rns_init: rnsd.service is enabled but never claimed @rns/%s within "
        "%.0fs — refusing to boot-claim the shared instance. Check "
        "`systemctl status rnsd`; this process will retry per its own "
        "restart/cycle policy.",
        instance_name, timeout_s,
    )
    return False


# ---------------------------------------------------------------- construct


@contextlib.contextmanager
def bounded_block(timeout_s: float, *, label: str) -> Iterator[None]:
    """Run the wrapped block under a hard timeout watchdog.

    Any region that might wedge on rnsd's RPC socket (LXMRouter init,
    announce, path-resolve, handle_outbound) can be wrapped here. A daemon
    watchdog thread calls ``os._exit(2)`` if the block doesn't exit within
    ``timeout_s``. Normal completion AND exceptions both disarm the watchdog.

    Why ``os._exit``: the kernel ``connect()`` in ``unix_wait_for_peer`` is
    uninterruptible from userland — SIGTERM queues behind the syscall. Only
    ``os._exit`` (or systemd-driven SIGKILL via ``TimeoutStartSec=``) gets
    the process out cleanly. See ``project_rnsd_rpc_listener_wedge.md`` for
    the wedge fingerprint and recovery recipe.
    """
    done = threading.Event()

    def _watchdog() -> None:
        if not done.wait(timeout=timeout_s):
            logger.error(
                "rns_init: %s did not complete after %.1fs — likely rnsd RPC "
                "listener wedge. Aborting process so systemd can restart us. "
                "See project_rnsd_rpc_listener_wedge.md.",
                label, timeout_s,
            )
            os._exit(2)

    watchdog = threading.Thread(
        target=_watchdog, daemon=True, name=f"bounded-{label}",
    )
    watchdog.start()
    try:
        yield
    finally:
        done.set()


def _construct_reticulum_with_watchdog(
    configdir: Optional[Union[str, os.PathLike]],
    *,
    loglevel: int,
    timeout_s: float,
):
    """The ONE allowed ``RNS.Reticulum()`` construction in MeshForge.

    Runs the constructor on the calling thread (it installs signal handlers,
    which Python only permits from the main thread — background-thread callers
    must suppress signal registration themselves) under a hard ``os._exit``
    watchdog (see :func:`bounded_block`). Returns the ``Reticulum`` instance;
    re-raises whatever the constructor raised on failure.
    """
    import RNS  # lazy — keeps module import cheap and patchable in tests
    with bounded_block(timeout_s, label="RNS.Reticulum()"):
        return RNS.Reticulum(configdir=configdir, loglevel=loglevel)


def init_reticulum_with_watchdog(
    configdir: Union[str, os.PathLike],
    *,
    loglevel: int = 2,
    timeout_s: float = RNS_INIT_TIMEOUT_S,
):
    """Lower-level primitive: #69 listener-owner preflight + watchdog
    construct, WITHOUT the #68 connect probe / idempotent reuse.

    Retained for the lab echo/tracer daemons (where RNS *is* the process's
    sole purpose, so a wedge SHOULD crash for systemd to restart). New
    project code should prefer :func:`open_reticulum`, which adds the
    fail-open probe and singleton reuse on top of this.
    """
    # RNS egress backstop — this entry point's contract permits raising, and
    # its callers (lab echo/tracer daemons) exist to put traffic on the real
    # Reticulum, so a refusal here must be LOUD rather than a silent None.
    tx_guard.assert_rns_tx_allowed(
        kind="rns_attach",
        detail=f"init_reticulum_with_watchdog(configdir={configdir})")

    instance_name = _read_instance_name_from_config(configdir)
    if instance_name:
        check_rns_listener_owner(instance_name)
        # Issue #69 boot race: never boot-claim an instance that an enabled
        # rnsd is about to host. Fail LOUD on timeout — these daemons run
        # under systemd Restart=/timer policies, so refusing now means a
        # clean retry later instead of a poisoned instance for the whole box.
        if (
            not _shared_instance_listener_present(instance_name)
            and _rnsd_unit_enabled()
            and not _wait_for_rnsd_listener(instance_name)
        ):
            raise RuntimeError(
                f"rnsd.service is enabled but has not claimed "
                f"@rns/{instance_name} — refusing to boot-claim the shared "
                f"instance (Issue #69 boot race). Check `systemctl status "
                f"rnsd`, then restart this service."
            )
    return _construct_reticulum_with_watchdog(
        configdir, loglevel=loglevel, timeout_s=timeout_s,
    )


def _existing_instance():
    """Return the live ``RNS.Reticulum`` singleton if one exists, else None.

    Uses the public ``RNS.Reticulum.get_instance()`` classmethod rather than
    the name-mangled ``_Reticulum__instance`` attr (fragile across rns minor
    versions — see Issue #44).
    """
    if not _HAS_RNS:
        return None
    try:
        return _RNS.Reticulum.get_instance()
    except Exception:
        return None


def open_reticulum(
    configdir: Optional[Union[str, os.PathLike]],
    *,
    loglevel: int = 2,
    require_listener: bool = False,
    probe: bool = True,
    connect_probe_timeout_s: float = DEFAULT_CONNECT_PROBE_TIMEOUT_S,
    init_timeout_s: float = RNS_INIT_TIMEOUT_S,
):
    """Project-wide guarded RNS-init chokepoint. Returns a ``RNS.Reticulum``
    instance, or ``None`` when RNS is unavailable/degraded (fail-open).

    Sequence (see module docstring for the why of each):
      1. RNS module missing            -> return None.
      2. Singleton already constructed -> return it (idempotent).
      3. #69 listener-owner preflight  -> raise on a FOREIGN owner (fail-loud).
      4. #68 bounded connect probe (+ #69 boot-race wait):
         - listener absent + rnsd.service ENABLED -> wait bounded for rnsd
           to claim it (boot race: a client constructing first becomes the
           @rns host and rnsd silently joins as an interface-less client —
           the federator box, 2026-06-06). Appears -> proceed as client; never
           appears -> return None regardless of ``require_listener``.
         - listener absent + ``require_listener`` -> return None (a pure
           consumer must never construct, or it becomes the @rns host — the
           2026-05-28 ~21h fleet outage). Absent + not required + rnsd not
           enabled -> construct (standalone is legitimate, e.g. gateway
           with no rnsd).
         - listener present but connect times out (wedged) -> return None.
         - listener present and accepting -> construct.
      5. Construct under the ``os._exit`` watchdog backstop.

    Args:
        configdir: RNS config dir (MF009 — always pass one). May be None to
            use RNS's own resolution; the probe then falls back to the
            box's configured instance name.
        require_listener: True for pure shared-instance *consumers* (map
            collector, node tracker) that must never create the host.
        probe: set False only to skip the #68 connect probe (tests).
        connect_probe_timeout_s: #68 probe budget (default 5s).
        init_timeout_s: watchdog budget around the constructor (default 60s).

    Raises:
        RuntimeError: a foreign daemon owns ``@rns/<instance>`` (Issue #69).
    """
    if not _HAS_RNS:
        logger.debug("rns_init: RNS module not installed — RNS unavailable")
        return None

    # RNS egress backstop (2026-08-09). Under pytest, decline to hold a LIVE
    # handle on the operator's Reticulum unless the test declared
    # tx_guard.allow_rns_egress(). Returning None is this function's OWN
    # documented degraded outcome and every caller already handles it — it is
    # exactly what a box with no rnsd (and every CI runner) gets, so the local
    # suite stops behaving differently from CI just because rnsd happens to be
    # running here. With no live instance, an unguarded announce has nothing
    # to transmit through.
    if not tx_guard.rns_attach_allowed():
        tx_guard.note_rns_attach_blocked(configdir)
        return None

    existing = _existing_instance()
    if existing is not None:
        return existing

    # Serialize check-then-construct: a second thread blocks here and finds
    # the singleton on its own re-check instead of racing the constructor.
    with _CONSTRUCT_LOCK:
        existing = _existing_instance()
        if existing is not None:
            return existing

        instance_name = (
            _read_instance_name_from_config(configdir) if configdir else None
        )
        if not instance_name:
            # Fall back to the box's configured instance so the preflight/
            # probe still have a target even when configdir is
            # None/unparseable.
            try:
                from utils.paths import ReticulumPaths
                instance_name = ReticulumPaths.get_configured_instance_name()
            except Exception:
                instance_name = None

        if instance_name:
            # (3) fail-LOUD on a foreign listener owner.
            check_rns_listener_owner(instance_name)

            # (4) fail-OPEN on absent (for consumers) or wedged rnsd.
            if probe:
                listener_present = _shared_instance_listener_present(
                    instance_name)
                if not listener_present and _rnsd_unit_enabled():
                    # Issue #69 boot race: rnsd is the designated host but
                    # hasn't claimed yet (it's probably still starting). Wait
                    # instead of boot-claiming the instance out from under it.
                    listener_present = _wait_for_rnsd_listener(instance_name)
                    if not listener_present:
                        # rnsd never showed. Constructing standalone here
                        # would poison the box the moment rnsd recovers, so
                        # degrade regardless of require_listener.
                        return None
                if not listener_present:
                    if require_listener:
                        logger.warning(
                            "rns_init: @rns/%s shared instance not present "
                            "and require_listener=True — skipping RNS init "
                            "so this process never becomes the @rns host "
                            "(rnsd must host it). Degraded; retry on a "
                            "later cycle.",
                            instance_name,
                        )
                        return None
                    # else: no listener, rnsd not enabled — standalone
                    # construction is legitimate (e.g. gateway with no rnsd).
                elif not _probe_shared_instance_connect(
                    instance_name, connect_probe_timeout_s
                ):
                    # Listener present but wedged (or stopped mid-probe) —
                    # degrade.
                    return None

        # (5) construct under the watchdog backstop. Absorb the singleton
        # "reinitialise" race with anything that constructed outside this
        # lock (module docstring #2 — callers must never need this catch).
        try:
            return _construct_reticulum_with_watchdog(
                configdir, loglevel=loglevel, timeout_s=init_timeout_s,
            )
        except OSError as exc:
            msg = str(exc).lower()
            if "reinitialise" in msg or "already running" in msg:
                existing = _existing_instance()
                if existing is not None:
                    return existing
            raise
