#!/usr/bin/env python3
"""fork_roll_restart.py — after an rns/lxmf fork roll, restart what still RUNS the old copy.

Step 2 of a fork roll, run ON the box after the install (step 1):

    sudo -n python3 scripts/fork_roll_restart.py            # dry run: the plan
    sudo -n python3 scripts/fork_roll_restart.py --apply    # restart, re-measure

WHY (2026-10-16). The lxmf mf.11 and mf.12 rolls each ended with
honest_status's `running substrate` leg WARNing on the same six MeshAnchor
units: the roll found its restart targets by process NAME (`pgrep -f
'lxmd|nomadnet|...'`), which knew the LXMRouter hosts and nothing that loads
lxmf from repo code. The check of record already measured the right set. So
the restart set here IS that measurement — honest_status's substrate probe
(scripts/hs_substrate_skew.py) over every active unit's own cgroup, via
utils.rnsd_restart_order.probe_substrate, the same pass the #69 hold uses.
A unit that starts loading lxmf next month is restarted without anyone
adding its name.

Targets: every ACTIVE unit with an SB row (process started before the copy it
would load was installed), entry OR repo basis — a restart is safe where a
hold-stop is not, and `repo` is exactly the six this was written for.
Never: rnsd (named with the #69 order — restarting it under its clients is
the squat window), ssh/cron/transient/oneshot (excluded by the enumeration),
an inactive unit (restart STARTS it — feedback_deploy_restarts_only_active_units).

Exit: 0 = re-measure shows nothing behind and nothing unobservable;
1 = something still behind, a restart failed, or MainPID did not change;
2 = UNKNOWN (the measurement could not run, or a unit could not be probed).
Run it as root (sudo): the probe must read other uids' processes, and
SUDO_USER routes user-scope restarts back to the operator's manager.
"""
import argparse
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from utils.observation import Failed, Observation, Seen, Unobservable  # noqa: E402
from utils.rnsd_restart_order import (SUBSTRATE_PROBE, probe_substrate,  # noqa: E402
                                      stop_deadline_s)
from utils.service_check import (_systemctl_query_argv, is_system_unit_active,  # noqa: E402
                                 is_user_unit_active, restart_service)

PIN_FILE = ROOT / "requirements" / "rns.txt"


@dataclass
class Stale:
    targets: List[Tuple[str, bool]] = field(default_factory=list)  # (unit, user)
    rnsd_behind: bool = False
    unprobed: List[Tuple[str, bool, str]] = field(default_factory=list)
    #: False = run as root without SUDO_USER: every user unit unmeasured
    user_scope_seen: bool = True
    #: tmux-wrapped units whose own panes could not be listed (not probed)
    tmux_unresolved: List[str] = field(default_factory=list)


def watched_dists(pin_file: Path = PIN_FILE) -> Tuple[str, ...]:
    """The `# MF-FORK-PIN <dist> <ver>` names — honest_status's HS_SUB_WANT."""
    out = []
    for ln in pin_file.read_text().splitlines():
        f = ln.split()
        if len(f) >= 3 and f[0] == "#" and f[1] == "MF-FORK-PIN" and f[2] not in out:
            out.append(f[2])
    return tuple(out)


def _short(unit: str) -> str:
    return unit[:-len(".service")] if unit.endswith(".service") else unit


def measure_stale(dists: Sequence[str], probe: Path = SUBSTRATE_PROBE) -> Observation[Stale]:
    obs = probe_substrate(dists, probe, include_rnsd=True)
    if not isinstance(obs, Seen):
        return obs
    p = obs.value
    out = Stale(user_scope_seen=p.user_scope_seen,
                tmux_unresolved=list(p.tmux_unresolved))
    for f in p.rows:
        key = (_short(f[1]), p.scope_of[f[1]])
        if f[0] == "SB":
            if key[0] == "rnsd":
                out.rnsd_behind = True
            elif key not in out.targets:
                out.targets.append(key)
        elif f[0] == "SU":
            u = (key[0], key[1], f[2])
            if u not in out.unprobed:
                out.unprobed.append(u)
    return Seen(out)


def _is_active(unit: str, user: bool) -> Optional[bool]:
    try:
        return is_user_unit_active(unit) if user else is_system_unit_active(unit)
    except Exception:
        return None


def _main_pid(unit: str, user: bool) -> str:
    try:
        r = subprocess.run(_systemctl_query_argv(["show", unit, "-p", "MainPID", "--value"],
                                                 user=user),
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return ""


def _scope(user: bool) -> str:
    return "user" if user else "system"


def run(dists: Sequence[str], apply: bool, out: Callable[[str], None] = print,
        settle_s: float = 5.0) -> int:
    obs = measure_stale(dists)
    if not isinstance(obs, Seen):
        why = getattr(obs, "why", str(obs))
        out(f"UNKNOWN — substrate measurement unavailable ({why}); restarted nothing")
        return 2
    st = obs.value
    out(f"watched: {' '.join(dists)}; behind: "
        + (", ".join(f"{u} ({_scope(s)})" for u, s in st.targets) or "none"))
    rc = 0
    if apply:
        for unit, user in st.targets:
            active = _is_active(unit, user)
            if active is not True:
                out(f"  skip {unit} ({_scope(user)}): "
                    + ("inactive — never started by a roll" if active is False
                       else "state UNKNOWN"))
                if active is None:
                    rc = max(rc, 2)
                continue
            before = _main_pid(unit, user)
            # As long as the unit itself may take to stop, + a start margin:
            # a 120 s client wait under the map's 5 min TimeoutStopSec printed
            # FAIL for a restart whose job landed anyway (F1 shape, review).
            ok, msg = restart_service(unit, user=user,
                                      timeout=stop_deadline_s(unit, user) + 60)
            after = _main_pid(unit, user)
            if not ok:
                out(f"  FAIL {unit} ({_scope(user)}): {msg}")
                rc = max(rc, 1)
            elif before in ("", "0") or after in ("", "0"):
                out(f"  UNKNOWN {unit} ({_scope(user)}): restart issued, MainPID "
                    f"unobservable ({before or '?'} -> {after or '?'})")
                rc = max(rc, 2)
            elif before == after:
                out(f"  FAIL {unit} ({_scope(user)}): MainPID unchanged ({before})")
                rc = max(rc, 1)
            else:
                out(f"  restarted {unit} ({_scope(user)}) {before} -> {after}")
        if st.targets and settle_s:
            threading.Event().wait(settle_s)
        obs = measure_stale(dists)
        if not isinstance(obs, Seen):
            out(f"UNKNOWN — re-measure unavailable ({getattr(obs, 'why', obs)})")
            return 2
        st = obs.value
        if st.targets:
            out("  still behind after restart: "
                + ", ".join(f"{u} ({_scope(s)})" for u, s in st.targets))
            rc = max(rc, 1)
    if st.rnsd_behind:
        out("  rnsd is behind: NOT restarted here — use the #69 order "
            "(clients down → rnsd → owner check → clients), one box at a time")
        rc = max(rc, 1)
    if not st.user_scope_seen:
        out("  UNKNOWN — user scope not visible (root without SUDO_USER?): "
            "user-scope units unmeasured; run it with sudo from the operator")
        rc = max(rc, 2)
    if st.tmux_unresolved:
        out("  UNKNOWN — tmux unit(s) whose session panes could not be listed "
            "(their process was not probed): " + ", ".join(st.tmux_unresolved))
        rc = max(rc, 2)
    if st.unprobed:
        out("  UNKNOWN — could not probe: "
            + ", ".join(f"{u} ({_scope(s)}): {w}" for u, s, w in st.unprobed))
        rc = max(rc, 2)
    out(f"rc={rc}")
    return rc


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="restart the behind units (default: print the plan)")
    ap.add_argument("dists", nargs="*",
                    help="watched distributions (default: the MF-FORK-PIN names)")
    a = ap.parse_args(argv)
    return run(tuple(a.dists) or watched_dists(), apply=a.apply)


if __name__ == "__main__":
    sys.exit(main())
