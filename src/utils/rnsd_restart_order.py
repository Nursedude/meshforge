"""Restart rnsd in the order that cannot hand ``@rns/<instance>`` to a client.

Issue #69 repair order: **stop RNS clients → restart rnsd → verify rnsd OWNS
the listener → start clients.** Restarting rnsd with its clients still up
opens a window in which a client (NomadNet, lxmd, meshchat, a gateway that
outlived its wait) finds the shared-instance socket absent and hosts it
itself; rnsd then comes back as a client of the squatter, or dies on the
bind. If rnsd never takes the socket, the clients are LEFT STOPPED and
named: starting them is exactly what makes the squat.

Only units that are ACTIVE when the hold begins are touched, so a COMPLETED
deliberate stop (inactive/failed) stays a stop
(feedback_deploy_restarts_only_active_units). ``activating`` and
``deactivating`` count as active: both will be running again with nobody
starting them (a crashed unit under ``Restart=`` sits in ``deactivating``
for its whole TimeoutStopSec), so they are held and RESTARTED by release —
a stop still in flight when the hold begins is therefore undone; that window
is seconds and the alternative is a squatter (re-review R1/F3, 2026-09-29).

Ownership is read from the socket (``ss -xnpl`` → ``/proc/<pid>/cmdline``),
the same scan the #69 watchdog probe uses — never from "the port answers",
which a squatter satisfies too.
"""

import logging
import os
import re
import subprocess
import signal
import threading
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from utils.service_check import (_systemctl_query_argv, is_system_unit_active,
                                 is_user_unit_active, start_service,
                                 stop_service)

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


# Re-review F1 (2026-09-29, drilled on a throwaway unit): ``stop_service()``'s
# 30 s default is SHORTER than every RNS client's TimeoutStopSec (90 s on the
# fleet; the map 5 min), so a held unit that is slow to stop — which a
# ``deactivating`` one is BY DEFINITION — timed out the systemctl CLIENT while
# the stop JOB stayed queued in systemd and landed anyway: the unit ended
# ``failed``, sat in ``stop_failed``, and release (which starts only
# ``stopped``) never brought it back. Left down until a human noticed. Cure:
# wait as long as the unit itself may take (+ margin, capped), and when the
# client still gives up, RE-ASK THE UNIT — the message is not the state.
STOP_TIMEOUT_MARGIN_S = 10
STOP_TIMEOUT_CAP_S = 330         # the map's 5 min + margin; ``infinity`` lands here

_TIMESPAN_UNITS = {"y": 31557600.0, "month": 2629800.0, "w": 604800.0, "d": 86400.0,
                   "h": 3600.0, "min": 60.0, "s": 1.0, "ms": 0.001, "us": 1e-6,
                   "\u00b5s": 1e-6, "ns": 1e-9}


def parse_systemd_timespan(text: str) -> Optional[float]:
    """``systemctl show`` prints USec properties as ``1min 30s`` / ``5min`` /
    ``infinity``. Seconds; ``inf`` for infinity; None when unparseable."""
    t = (text or "").strip()
    if not t:
        return None
    if t == "infinity":
        return float("inf")
    parts = re.findall(r"(\d+(?:\.\d+)?)\s*([a-z\u00b5]+)", t)
    if not parts or not re.fullmatch(r"(?:\s*\d+(?:\.\d+)?\s*[a-z\u00b5]+)+\s*", t):
        return None
    total = 0.0
    for num, unit in parts:
        if unit not in _TIMESPAN_UNITS:
            return None
        total += float(num) * _TIMESPAN_UNITS[unit]
    return total


def unit_stop_timeout_s(unit: str, user: bool) -> Optional[float]:
    """The unit's own ``TimeoutStopSec`` (read-only ``systemctl show``);
    ``inf`` for infinity; None when the manager did not answer."""
    try:
        r = subprocess.run(
            _systemctl_query_argv(['show', unit, '-p', 'TimeoutStopUSec', '--value'], user=user),
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug("TimeoutStopUSec of %s unreadable: %s", unit, e)
        return None
    return parse_systemd_timespan(r.stdout) if r.returncode == 0 else None


def stop_deadline_s(unit: str, user: bool) -> int:
    """How long ``stop_service`` may block for this unit: its own stop timeout
    plus a margin, capped; the CAP when unreadable (F-B: too long costs
    seconds, too short costs a downed unit — the map's is 5 min)."""
    t = unit_stop_timeout_s(unit, user)
    if t is None:
        return STOP_TIMEOUT_CAP_S
    return int(min(t + STOP_TIMEOUT_MARGIN_S, STOP_TIMEOUT_CAP_S))


def _client_active(unit: str, user: bool) -> Optional[bool]:
    """Tri-state state of one client unit; None = unobservable (never skip)."""
    try:
        return is_user_unit_active(unit) if user else is_system_unit_active(unit)
    except Exception as e:
        logger.debug("client state check %s failed: %s", unit, e)
        return None


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
    not_stopped: List[str] = field(default_factory=list)  # F2: hold.stop_failed

    @property
    def ok(self) -> bool:
        # F-A (second read, 2026-09-29): a client that could NOT be stopped is
        # in the F1 shape (job lands later, never restarted) — never "Fixed".
        return (self.rnsd_owns is True and not self.start_failed
                and not self.not_stopped)

    def summary(self) -> str:
        if self.rnsd_owns is True:
            line = "rnsd owns the shared instance."
            if self.started:
                line += " Restarted clients: " + ", ".join(self.started) + "."
            if self.start_failed:
                line += " FAILED to restart: " + ", ".join(self.start_failed) + "."
            if self.not_stopped:
                line += (" Could NOT be stopped before the restart (check them): "
                         + ", ".join(self.not_stopped) + ".")
            return line
        why = ("ownership could not be observed (ss unavailable)"
               if self.rnsd_owns is None else
               "rnsd does NOT own the shared instance")
        held = ", ".join(self.left_stopped) or "none"
        own = "; ".join(f"PID {p}: {c[:60]}" for p, c in self.owners) or "no listener"
        ns = (" NOT stopped (may be the squatter): " + ", ".join(self.not_stopped) + "."
              if self.not_stopped else "")
        return (f"{why} — left STOPPED so they cannot squat it: {held}. "
                f"Listener: {own}.{ns}")


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
        # BOTH scopes tri-state (review S2): a squatter-in-waiting is never
        # skipped on a bool; None = unobservable, recorded.
        active = _client_active(unit, user)
        if active is None:
            # unobservable ≠ inactive: not stopped (we cannot reach it), but
            # recorded so the operator is told it may squat the socket.
            hold.unobservable.append((unit, user))
            continue
        if not active:
            continue
        deadline = stop_deadline_s(unit, user)
        ok, msg = stop_service(unit, user=user, timeout=deadline)
        if not ok and msg.startswith("Timeout"):
            # F1: our systemctl CLIENT gave up; the stop JOB is still queued
            # in systemd and lands on its own. Re-ask the unit.
            again = _client_active(unit, user)
            if again is False:
                ok, msg = True, f"{unit} stopped (client waited {deadline}s)"
            elif again is None:
                msg = f"{unit} state UNKNOWN after the client waited {deadline}s"
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
    result.not_stopped = [f"{u} ({_scope(user)}): {m}" for u, user, m in hold.stop_failed]
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
