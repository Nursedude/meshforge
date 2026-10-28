#!/usr/bin/env python3
"""rnsd_ordered_restart.py — restart rnsd in the #69 order, and prove the end state.

The rnsd step of a fork roll (fork_roll_restart.py names rnsd and refuses to
touch it), and any planned rnsd restart. Run ON the box:

    sudo -n python3 scripts/rnsd_ordered_restart.py            # dry run: the plan
    sudo -n python3 scripts/rnsd_ordered_restart.py --apply    # restart, verify

Order (utils.rnsd_restart_order.ordered_restart_rnsd): measured RNS clients
down → rnsd down → rnsd up → wait until rnsd OWNS @rns/<instance> → clients
up. If rnsd never takes the socket the clients are LEFT STOPPED and named.

WHY (2026-10-27). The 1.3.9 canary, the nine-box roll and the moc warm-up
drill each ran this step through a throwaway wrapper in ~/.local/share —
the one step every roll needs, living outside the repo. The wrapper also
stopped at "rnsd owned the socket before the clients started": ownership is
re-checked here AFTER they are back (#69, 09-19: a guard that verifies at the
one moment the answer cannot be wrong verifies nothing), and every restarted
unit must STAY up across a settle window (the meshanchor.service crash-loop
that "MainPID changed" passed, 10-21).

Exit: 0 = rnsd on a new MainPID, owns @rns/ after the clients are back, every
held client back and stayed up (dry run: rnsd owns @rns/ now, every client's
state known); 1 = proven not so; 2 = UNKNOWN (not root for --apply, or
something could not be observed). Run as root via sudo: SUDO_USER routes
user-scope units back to the operator's manager.

Do not run it on several boxes at once: space rnsd restarts and read each
box's result before the next (persistent_issues: never rapid-cycle rnsd).
"""
import argparse
import os
import sys
import threading
from pathlib import Path
from typing import Callable, Optional, Sequence

# Same root-bytecode cure as fork_roll_restart.py; set BEFORE first-party imports.
if os.geteuid() == 0 and not sys.pycache_prefix:
    sys.pycache_prefix = os.environ.get("MF_ROOT_PYCACHE", "/var/cache/meshforge/pycache-root")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import fork_roll_restart as frr  # noqa: E402  (_show / stayed_up: one definition)
import utils.rnsd_restart_order as ro  # noqa: E402


def _owners_line() -> str:
    owners = ro.listener_owners()
    if owners is None:
        return "unobservable (ss)"
    return "; ".join(f"pid {p}: {c[:60]}" for p, c in owners) or "no listener"


def _blind(hold, out: Callable[[str], None]) -> int:
    """2 when the client set was not fully measured, else 0. A restart that
    held only the curated floor can pass by luck — never call that verified."""
    rc = 0
    if not hold.measured:
        out("  UNKNOWN — client measurement unavailable: only the curated floor was held")
        rc = 2
    if not hold.user_scope_seen:
        out("  UNKNOWN — user scope not visible (root without SUDO_USER?): run via sudo")
        rc = 2
    return rc


def plan(out: Callable[[str], None] = print) -> int:
    """Dry run: what a hold would stop and who owns @rns/ now. Stops nothing."""
    rc = 0
    rnsd = frr._show("rnsd", False)
    out(f"rnsd: {rnsd.get('ActiveState', '?')}/{rnsd.get('SubState', '?')} "
        f"MainPID {rnsd.get('MainPID', '?')}")
    if not rnsd:
        out("  UNKNOWN — rnsd's unit state did not answer")
        rc = max(rc, 2)
    owns = ro.rnsd_owns_listener()
    out(f"@rns/{ro.instance_name()}: rnsd owns={owns} — {_owners_line()}")
    if owns is None:
        rc = max(rc, 2)
    elif not owns:
        out("  rnsd does NOT own the shared instance now — the restart is the repair; "
            "read the owner above first")
        rc = max(rc, 1)
    units, hold = ro.plan_rns_client_hold()
    inactive = []
    for unit, user in units:
        label = f"{unit} ({ro._scope(user)})"
        disposition = ro.client_disposition(unit, user)  # the hold's own decision
        if disposition == "unobservable":
            out(f"  UNKNOWN {label}: state unobservable — would NOT be stopped")
            rc = max(rc, 2)
        elif disposition == "stop":
            out(f"  would stop {label}")
        else:
            inactive.append(label)
    if inactive:
        out("  not running, left alone: " + ", ".join(f"{x}: inactive" for x in inactive))
    for line in hold.report_lines():
        out(line)
    rc = max(rc, _blind(hold, out))
    if os.geteuid() != 0:
        out("  UNKNOWN — not root: other uids' processes were not measured; run with sudo")
        rc = max(rc, 2)
    out(f"rc={rc} (dry run — nothing stopped; --apply to restart)")
    return rc


def apply(out: Callable[[str], None] = print, settle_s: float = 15.0,
          _wait: Optional[Callable[[float], None]] = None) -> int:
    if os.geteuid() != 0:
        out("UNKNOWN — --apply needs root (sudo -n python3 scripts/rnsd_ordered_restart.py "
            "--apply); nothing touched")
        return 2
    rc = 0
    started, release, hold = ro.ordered_restart_rnsd()
    for line in hold.report_lines():
        out(line)
    rc = max(rc, _blind(hold, out))
    if hold.unobservable:
        # not stopped because its state was unknown: it may have stayed up
        # through the restart — the squatter-in-waiting #69 names
        rc = max(rc, 2)
    out(f"  {release.summary()}")
    if not started:
        out("  FAIL rnsd did not start")
        rc = max(rc, 1)
    if release.verdict is None:      # unobservable is UNKNOWN, never a FAIL
        rc = max(rc, 2)
    elif release.verdict is False:
        rc = max(rc, 1)
    # R5 (review 2026-10-28): the restart is judged ONCE, in the library
    # (release.restart, folded into release.verdict above) — print its trace,
    # never a second MainPID judgement that could disagree with it
    out(f"  restart {release.rnsd_restart}: {release.restart.trace or 'no trace'}")
    rnsd_after = frr._show("rnsd", False)   # the stay-up baseline below
    back = [(u, s) for u, s in hold.stopped
            if f"{u} ({ro._scope(s)})" in release.started]
    if len(back) != len(release.started):
        out(f"  UNKNOWN could not match restarted clients to the hold "
            f"({release.started} vs {hold.names()}) — their stay-up was not checked")
        rc = max(rc, 2)
    after_states = {k: frr._show(*k) for k in back}
    if settle_s:
        (_wait or threading.Event().wait)(settle_s)
    for (unit, user), st in [(("rnsd", False), rnsd_after), *after_states.items()]:
        label = f"{unit} ({ro._scope(user)})"
        # stayed_up judges an empty SETTLED read only; an empty baseline must
        # read UNKNOWN too, never a FAIL against nothing
        up, why = ((None, "state unobservable right after start") if not st
                   else frr.stayed_up(st, frr._show(unit, user)))
        if up is None:
            out(f"  UNKNOWN {label}: {why}")
            rc = max(rc, 2)
        elif not up:
            out(f"  FAIL {label} did not stay up over {settle_s:g}s — {why}")
            rc = max(rc, 1)
    owns = ro.rnsd_owns_listener()
    out(f"  @rns/ AFTER the clients are back: rnsd owns={owns} — {_owners_line()}")
    if owns is None:
        rc = max(rc, 2)
    elif not owns:
        out("  FAIL a client took @rns/ after it started — stop it, restart rnsd, "
            "start it (#69 repair order)")
        rc = max(rc, 1)
    out("RESULT: OK" if rc == 0 else
        f"RESULT: NOT OK (rc={rc}) — do not move to the next box")
    return rc


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="restart rnsd in the #69 order (default: print the plan)")
    ap.add_argument("--settle", type=float, default=15.0,
                    help="seconds every restarted unit must stay up (default 15)")
    a = ap.parse_args(argv)
    return apply(settle_s=a.settle) if a.apply else plan()


if __name__ == "__main__":
    sys.exit(main())
