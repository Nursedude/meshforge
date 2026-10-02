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
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from utils.observation import Failed, Observation, Seen, Unobservable, assert_never
from utils.service_check import (_systemctl_query_argv, is_system_unit_active,
                                 is_user_unit_active, start_service,
                                 stop_service)

logger = logging.getLogger(__name__)

# (unit, user_scope). The FLOOR of the hold set, not the whole of it.
# The kernel cannot map a client to the socket (``ss -xnp`` shows peers as
# ``* 0``), so this list was curated — and on 2026-10-02 a fleet measurement
# showed it MISSING real clients: `lxmd` runs as a SYSTEM unit on one box (the
# list knew only a user one), `meshforge-lxmd` as a USER unit on another, and
# `meshcore-chat` on the MeshAnchor box. A TUI rnsd repair there would have
# left an lxmd up through the restart — the #69 window. The cure is not a
# longer list: hold_rns_clients() now MEASURES which live units load RNS
# (measure_rns_clients) and holds the union; this floor remains for when the
# measurement cannot run. Inactive/absent units are skipped.
RNS_CLIENT_UNITS: Tuple[Tuple[str, bool], ...] = (
    ("meshforge-gateway", False),
    ("meshforge-map", False),
    ("meshforge-daemon", False),
    ("meshanchor-daemon", False),
    ("meshanchor-map", False),
    ("nomadnet", True),
    ("meshforge-echo", True),
    ("lxmd", True),
    ("lxmd", False),
    ("meshforge-lxmd", True),
    ("meshcore-chat", True),
    ("meshanchor-echo", True),
    ("meshchatx", True),
)

#: The per-process substrate probe honest_status uses (it asks each live
#: python process's own interpreter where `import RNS` resolves). One
#: measurement, two consumers — never a second implementation (hfm #5).
SUBSTRATE_PROBE = (Path(__file__).resolve().parent.parent.parent
                   / "scripts" / "hs_substrate_skew.py")
MEASURE_TIMEOUT_S = 90

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
    #: units the substrate probe could not look inside (unit, user, why):
    #: NOT stopped (an unprobeable root process may be anything — stopping
    #: unattended-upgrades mid-run is its own harm), but named to the operator.
    unprobed: List[Tuple[str, bool, str]] = field(default_factory=list)
    #: repo code that imports RNS but is not proven a client: named, NOT stopped.
    repo_only: List[Tuple[str, bool]] = field(default_factory=list)
    #: units that could import RNS but showed no evidence either way.
    unjudged: int = 0
    #: transient / oneshot units never considered (review D re-read: a one-off
    #: `python3 -m RNS.Utilities.rnpath` in a systemd-run unit IS a client and
    #: would stay up unmentioned). Never-hold system units are not listed.
    not_considered: List[str] = field(default_factory=list)
    #: where the hold set came from — "measured + floor" or "floor only: why".
    client_set: str = ""

    def names(self) -> List[str]:
        return [f"{u} ({_scope(user)})" for u, user in self.stopped]

    def report_lines(self) -> List[str]:
        """The ONE rendering of a hold for every TUI caller (three sites used
        to hand-format it; the fourth fact would have drifted)."""
        out = [f"  RNS client set: {self.client_set}"] if self.client_set else []
        out += [f"  Stopped RNS client: {label}" for label in self.names()]
        out += [f"  Warning: could NOT stop RNS client {u}: {m} — it may squat @rns"
                for u, _user, m in self.stop_failed]
        out += [f"  Warning: state of RNS client {u} UNKNOWN — "
                f"{'user' if user else 'system'} manager did not answer "
                f"(timeout or bus unreachable); not stopped"
                for u, user in self.unobservable]
        out += [f"  Note: {u} ({_scope(user)}) runs repo code that imports RNS but "
                f"is not proven a client — NOT stopped; if it holds @rns it may squat"
                for u, user in self.repo_only]
        # Privilege-shaped blindness is one fact, not 36 lines (review D7).
        priv = [x for x in self.unprobed
                if x[2] == "no-access" or x[2].startswith("uid-")]
        named = [x for x in self.unprobed if x not in priv]
        out += [f"  Warning: could not see inside {u} ({_scope(user)}): {why} — "
                f"not stopped; if it uses RNS it may squat @rns"
                for u, user, why in named]
        if priv:
            out.append(f"  Note: {len(priv)} unit(s) not inspectable without root "
                       f"— not stopped (run the repair with sudo to measure them)")
        if self.not_considered:
            out.append(f"  Note: {len(self.not_considered)} transient/oneshot unit(s) "
                       f"not considered and not stopped: "
                       + ", ".join(self.not_considered[:6])
                       + (" ..." if len(self.not_considered) > 6 else ""))
        if self.unjudged:
            out.append(f"  Note: {self.unjudged} unit row(s) could import RNS but "
                       f"showed no evidence of loading it — not stopped")
        return out


#: Units a hold must NEVER stop, whatever the measurement says. A hold leaves
#: units STOPPED when rnsd fails to take the socket, so a false positive here
#: is an outage: review D (2026-10-02) measured ssh.service classed as an RNS
#: client (an `ssh box 'cd /opt/meshforge && python3 ...'` under sshd) — on a
#: tunnel-only box that is a lock-out. Prefix match on the unit name.
NEVER_HOLD_PREFIXES = ("ssh", "sshd", "cron", "crond", "atd", "dbus", "systemd-",
                       "user@", "getty@", "serial-getty@", "polkit", "run-",
                       "session-")


@dataclass
class MeasuredClients:
    """What one measurement saw. Only ``clients`` is ever stopped."""
    clients: List[Tuple[str, bool]] = field(default_factory=list)
    #: repo code that imports RNS, but the unit's own entry point does not
    #: declare it — NOT proven a client: named, never stopped (review D).
    repo_only: List[Tuple[str, bool]] = field(default_factory=list)
    unprobed: List[Tuple[str, bool, str]] = field(default_factory=list)
    unjudged: int = 0            # SX rows: could import RNS, no evidence
    skipped: List[str] = field(default_factory=list)   # never-hold / transient / oneshot
    user_scope_seen: bool = True


def _active_units() -> Tuple[List[Tuple[bool, Dict[str, str]]], bool]:
    """Every ACTIVE service, both scopes, with the facts a hold decision needs.
    -> (units, user_scope_seen). A user manager that does not answer (root
    without SUDO_USER) is reported, never read as "no user units"."""
    units: List[Tuple[bool, Dict[str, str]]] = []
    user_seen = True
    for user in (False, True):
        listed = subprocess.run(
            _systemctl_query_argv(['list-units', '--type=service', '--state=active',
                                   '--no-legend', '--no-pager'], user=user),
            capture_output=True, text=True, timeout=10)
        names = [ln.split()[0] for ln in listed.stdout.splitlines() if ln.split()]
        names = [u for u in names if u.endswith(".service")]
        if listed.returncode != 0 or not names:
            if user:
                user_seen = False
            continue
        shown = subprocess.run(
            _systemctl_query_argv(['show', '-p', 'Id', '-p', 'MainPID', '-p',
                                   'ControlGroup', '-p', 'Type', '-p', 'FragmentPath',
                                   *names], user=user),
            capture_output=True, text=True, timeout=20)
        for rec in shown.stdout.split("\n\n"):
            kv = dict(ln.split("=", 1) for ln in rec.splitlines() if "=" in ln)
            if kv.get("Id"):
                units.append((user, kv))
    return units, user_seen


def _cgroup_pids(cgroup: str) -> List[int]:
    """Processes in the unit's OWN cgroup (v2). Not its descendants: a
    session or a tmux scope forked from it lives in another cgroup."""
    if not cgroup:
        return []
    try:
        with open(f"/sys/fs/cgroup{cgroup}/cgroup.procs") as fh:
            return [int(x) for x in fh.read().split() if x.isdigit()]
    except OSError:
        return []


def measure_rns_clients(probe: Path = SUBSTRATE_PROBE) -> Observation[MeasuredClients]:
    """Which ACTIVE units' OWN processes load RNS right now.

    The probe (honest_status's substrate probe, ``--no-descend``) is fed every
    pid in each candidate unit's own cgroup. A unit becomes a hold target only
    on ``entry`` evidence (its process's entry point needs RNS) and only if it
    is not on NEVER_HOLD_PREFIXES, not transient (/run) and not oneshot.
    rnsd is excluded. A missing or failing probe is Unobservable/Failed —
    never an empty client set.
    """
    if not probe.is_file():
        return Unobservable(f"substrate probe not present at {probe}")
    try:
        units, user_seen = _active_units()
    except (subprocess.SubprocessError, OSError) as e:
        return Unobservable(f"could not enumerate active units: {e}")
    if not units:
        return Unobservable("no active units enumerated (manager unreachable?)")
    out = MeasuredClients(user_scope_seen=user_seen)
    lines: List[str] = []
    scope_of: Dict[str, bool] = {}
    for is_user, u in units:
        unit = u["Id"]
        if unit == "rnsd.service":
            continue
        if (unit.startswith(NEVER_HOLD_PREFIXES)
                or u.get("FragmentPath", "").startswith("/run/")
                or u.get("Type") == "oneshot"):
            out.skipped.append(unit)
            continue
        pids = _cgroup_pids(u.get("ControlGroup", ""))
        if not pids and u.get("MainPID", "0") not in ("", "0"):
            pids = [int(u["MainPID"])]
        scope_of[unit] = is_user
        lines += [f"{_scope(is_user)} {unit} {p}" for p in pids]
    if not lines:
        return Unobservable("no candidate unit had a readable process")
    try:
        r = subprocess.run(["python3", str(probe), "--no-descend", "rns"],
                           input="\n".join(lines) + "\n",
                           capture_output=True, text=True,
                           timeout=MEASURE_TIMEOUT_S)
    except (subprocess.SubprocessError, OSError) as e:
        return Failed(f"substrate probe did not complete: {e}")
    if r.returncode != 0:
        return Failed(f"substrate probe rc={r.returncode}")
    for ln in r.stdout.splitlines():
        f = ln.split()
        if len(f) < 3:
            continue
        unit = f[1]
        if unit == "*":
            return Failed(f"substrate probe: {' '.join(f[2:])}")
        if unit not in scope_of:
            continue
        key = (unit[:-len(".service")] if unit.endswith(".service") else unit,
               scope_of[unit])
        if f[0] in ("SB", "SC"):
            bucket = out.clients if f[-1] == "entry" else out.repo_only
            if key not in bucket:
                bucket.append(key)
        elif f[0] == "SU":
            if (key[0], key[1], f[2]) not in out.unprobed:
                out.unprobed.append((key[0], key[1], f[2]))
        elif f[0] == "SX":
            out.unjudged += 1
    out.repo_only = [k for k in out.repo_only if k not in out.clients]
    return Seen(out)


#: The real measurement, reachable by its own tests: tests/conftest.py pins
#: ``measure_rns_clients`` OFF for the whole suite, because an unpinned call
#: probes the test host's LIVE processes (a verdict that depends on where the
#: suite runs pins nothing — feedback_tests_must_pin_ambient_state).
_measure_rns_clients_impl = measure_rns_clients


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


def hold_rns_clients(units: Optional[Sequence[Tuple[str, bool]]] = None) -> ClientHold:
    """Stop every ACTIVE RNS client unit; remember which ones.

    ``units=None`` (every real caller): the MEASURED clients unioned with the
    RNS_CLIENT_UNITS floor. An explicit list is used as given (tests).
    """
    hold = ClientHold()
    if units is not None:
        units = list(units)
    else:
        units = list(RNS_CLIENT_UNITS)
        obs = measure_rns_clients()
        match obs:
            case Seen(value=m):
                extra = [u for u in m.clients if u not in units]
                units += extra
                hold.unprobed = [x for x in m.unprobed if (x[0], x[1]) not in units]
                hold.repo_only = [x for x in m.repo_only if x not in units]
                hold.unjudged = m.unjudged
                # Report every skipped transient/oneshot unit — `run-*` too:
                # systemd-run names one-offs run-uNN, exactly where a one-off
                # RNS client lives. Only the system never-hold units are quiet.
                quiet = tuple(x for x in NEVER_HOLD_PREFIXES if x != "run-")
                hold.not_considered = [u for u in m.skipped if not u.startswith(quiet)]
                hold.client_set = (
                    f"measured, service cgroups only ({len(m.clients)} units whose own entry point "
                    f"loads RNS" + (f", {len(extra)} beyond the curated floor"
                                    if extra else "") + ") + curated floor"
                    + "; a client run from a login/tmux session is not seen"
                    + ("" if m.user_scope_seen else
                       " — USER scope NOT visible to this run (no SUDO_USER?): "
                       "user-scope clients outside the floor would stay up"))
            case Unobservable(why=w) | Failed(why=w):
                hold.client_set = (f"curated floor ONLY — measurement unavailable "
                                   f"({w}); a client outside the floor would stay up")
                logger.warning("rnsd restart: %s", hold.client_set)
            case _ as unreachable:
                assert_never(unreachable)
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
