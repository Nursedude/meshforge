"""Restart rnsd in the order that cannot hand ``@rns/<instance>`` to a client.

Issue #69 repair order: **stop RNS clients → restart rnsd → verify rnsd OWNS
the listener → start clients.** Restarting rnsd with its clients still up
opens a window in which a client (NomadNet, lxmd, meshchat, a gateway that
outlived its wait) finds the shared-instance socket absent and hosts it
itself; rnsd then comes back as a client of the squatter, or dies on the
bind. If rnsd never takes the socket, the clients are LEFT STOPPED and
named: starting them is exactly what makes the squat.

Only units that are ACTIVE when the hold begins are touched, so a deliberate
stop stays a stop (feedback_deploy_restarts_only_active_units).

Ownership is read from the socket (``ss -xnpl`` → ``/proc/<pid>/cmdline``),
the same scan the #69 watchdog probe uses — never from "the port answers",
which a squatter satisfies too.
"""

import logging
import os
import signal
import threading
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from utils.service_check import (is_system_unit_active, is_user_unit_active,
                                 start_service, stop_service)

logger = logging.getLogger(__name__)

# (unit, user_scope). No SSOT for "RNS client units" existed before this;
# the kernel cannot map a client to the socket (``ss -xnp`` shows peers as
# ``* 0``), so the list is curated. Inactive/absent units are skipped.
RNS_CLIENT_UNITS: Tuple[Tuple[str, bool], ...] = (
    ("meshforge-gateway", False),
    ("meshforge-map", False),
    ("meshforge-daemon", False),
    ("meshanchor-daemon", False),
    ("meshanchor-map", False),
    ("nomadnet", True),
    ("meshforge-echo", True),
    ("lxmd", True),
    ("meshanchor-echo", True),
    ("meshchatx", True),
)

DEFAULT_OWNERSHIP_WAIT_S = 30.0


def _scope(user: bool) -> str:
    return "user" if user else "system"


@dataclass
class ClientHold:
    """Clients this restart stopped, so exactly those are started again."""
    stopped: List[Tuple[str, bool]] = field(default_factory=list)
    stop_failed: List[Tuple[str, bool, str]] = field(default_factory=list)
    unobservable: List[Tuple[str, bool]] = field(default_factory=list)

    def names(self) -> List[str]:
        return [f"{u} ({_scope(user)})" for u, user in self.stopped]


@dataclass
class ReleaseResult:
    rnsd_owns: Optional[bool]          # None = ownership unobservable
    started: List[str] = field(default_factory=list)
    start_failed: List[str] = field(default_factory=list)
    left_stopped: List[str] = field(default_factory=list)
    owners: List[Tuple[int, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.rnsd_owns is True and not self.start_failed

    def summary(self) -> str:
        if self.rnsd_owns is True:
            line = "rnsd owns the shared instance."
            if self.started:
                line += " Restarted clients: " + ", ".join(self.started) + "."
            if self.start_failed:
                line += " FAILED to restart: " + ", ".join(self.start_failed) + "."
            return line
        why = ("ownership could not be observed (ss unavailable)"
               if self.rnsd_owns is None else
               "rnsd does NOT own the shared instance")
        held = ", ".join(self.left_stopped) or "none"
        own = "; ".join(f"PID {p}: {c[:60]}" for p, c in self.owners) or "no listener"
        return (f"{why} — left STOPPED so they cannot squat it: {held}. "
                f"Listener: {own}.")


def instance_name() -> str:
    from utils.paths import ReticulumPaths
    return ReticulumPaths.get_configured_instance_name() or "default"


def listener_owners(name: Optional[str] = None) -> Optional[List[Tuple[int, str]]]:
    """``[(pid, cmdline)]`` holding ``@rns/<name>``; ``[]`` if none; None if
    ``ss`` was unobservable. A pid whose cmdline vanished reads as ``""``."""
    from utils.watchdog_probes_rns import _scan_rns_listener_owners
    scan = _scan_rns_listener_owners(name or instance_name())
    if scan is None:
        return None
    owners, _proc = scan
    out = []
    for pid, comm in sorted(owners.items()):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                cmd = fh.read().replace(b"\x00", b" ").decode(
                    "utf-8", errors="replace").strip()
        except OSError:
            cmd = ""
        out.append((pid, cmd or comm))
    return out


def rnsd_owns_listener(name: Optional[str] = None) -> Optional[bool]:
    """True only when the listener exists and EVERY owner is rnsd itself.
    None when unobservable; absent, squatted or mid-teardown → False."""
    from utils.watchdog_probes_rns import (_classify_listener_owners,
                                           _scan_rns_listener_owners)
    scan = _scan_rns_listener_owners(name or instance_name())
    if scan is None:
        return None
    owners, _proc = scan
    if not owners:
        return False
    foreign, inverted, vanished = _classify_listener_owners(owners, "/proc")
    return not (foreign or inverted or vanished)


def terminate_pid(pid: int, wait_s: float = 3.0) -> bool:
    """SIGTERM one PID (never a pattern match) and wait for it to exit."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError as e:
        logger.warning("could not signal PID %s: %s", pid, e)
        return False
    ev = threading.Event()
    for _ in range(int(wait_s / 0.2)):
        if not os.path.exists(f"/proc/{pid}"):
            return True
        ev.wait(0.2)
    return not os.path.exists(f"/proc/{pid}")


def hold_rns_clients(units=RNS_CLIENT_UNITS) -> ClientHold:
    """Stop every ACTIVE RNS client unit; remember which ones."""
    hold = ClientHold()
    for unit, user in units:
        try:
            # BOTH scopes tri-state (review S2): the bool `.available` on a
            # ServiceStatus read `activating` and a systemctl timeout as
            # "not active → skip" — a squatter-in-waiting left unheld.
            active = (is_user_unit_active(unit) if user else
                      is_system_unit_active(unit))
        except Exception as e:
            logger.debug("client state check %s failed: %s", unit, e)
            active = None
        if active is None:
            # unobservable ≠ inactive: not stopped (we cannot reach it), but
            # recorded so the operator is told it may squat the socket.
            hold.unobservable.append((unit, user))
            continue
        if not active:
            continue
        ok, msg = stop_service(unit, user=user)
        if ok:
            hold.stopped.append((unit, user))
        else:
            hold.stop_failed.append((unit, user, msg))
    return hold


def release_rns_clients(
    hold: ClientHold,
    name: Optional[str] = None,
    wait_s: float = DEFAULT_OWNERSHIP_WAIT_S,
    poll_s: float = 0.5,
    _wait: Optional[Callable[[float], None]] = None,
) -> ReleaseResult:
    """Wait for rnsd to OWN the listener, then start the held clients.
    If it never does, leave them stopped and say which."""
    name = name or instance_name()
    wait = _wait or threading.Event().wait
    owns = rnsd_owns_listener(name)
    for _ in range(max(0, int(wait_s / poll_s))):
        if owns is True:
            break
        wait(poll_s)
        owns = rnsd_owns_listener(name)

    result = ReleaseResult(rnsd_owns=owns)
    if owns is not True:
        result.left_stopped = hold.names()
        result.owners = listener_owners(name) or []
        logger.warning("rnsd restart: %s", result.summary())
        return result
    for unit, user in hold.stopped:
        ok, msg = start_service(unit, user=user)
        label = f"{unit} ({_scope(user)})"
        (result.started if ok else result.start_failed).append(label)
        if not ok:
            logger.warning("client restart %s failed: %s", unit, msg)
    return result


def ordered_restart_rnsd(
    wait_s: float = DEFAULT_OWNERSHIP_WAIT_S,
    while_stopped: Optional[Callable[[], None]] = None,
) -> Tuple[bool, ReleaseResult, ClientHold]:
    """clients down → rnsd down → [while_stopped] → rnsd up → owns? → clients up.

    Returns ``(rnsd_started, release_result, hold)``.
    """
    hold = hold_rns_clients()
    stop_service('rnsd')
    if while_stopped is not None:
        while_stopped()
    started, msg = start_service('rnsd')
    if not started:
        logger.warning("rnsd start failed: %s", msg)
    release = release_rns_clients(hold, wait_s=wait_s)
    return started, release, hold
