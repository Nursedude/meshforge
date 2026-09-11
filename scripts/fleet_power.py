#!/usr/bin/env python3
"""fleet_power.py — the ordered fleet shutdown, and its mirror.

Born 2026-09-10, and the shape is MEASURED rather than reasoned. A manual
UPS-install shutdown of five boxes that afternoon cost SEVEN false
`POSTURE-DRIFT` pages, at both ends of the same hole:

    15:37-15:38  posture declared
    15:40:01     the */5 offline cron fires INSIDE the gap -> 3 pages
    15:40-15:41  the boxes actually halt
    ...
    15:50        boxes come back
    15:55:01     the cron fires again, still declared dormant -> 4 pages
    15:56:49     posture cleared by hand

Nothing was broken. `POSTURE-DRIFT` on "declared dormant but answering" is
exactly the signal you want if a box is burning battery it was meant to save.
The defect is that an ATOMIC state change was performed as two manual acts,
leaving an inconsistent intermediate state for a */5 poller to observe and
faithfully report. The gap does not have to be long -- only longer than the
shortest polling interval.

So this tool exists to make the change atomic from the fleet's point of view:

  * the declaration is STEP ONE OF THE SHUTDOWN, not a prior act, and all
    targets land in ONE write (`declare()` is pure, so N declarations compose
    into a single document before it touches the disk);
  * `resume` is the mandatory mirror -- it clears each box's posture AS IT
    RETURNS. A one-way button takes the return-leg pages every single time.

⚠️ The cure is the sequence, NEVER the instrument. Do not debounce
POSTURE-DRIFT, do not widen its cooldown.

ORDERING is not cosmetic. Two fleet boxes reach the NOC only through a hop
that is not itself a fleet box (`via` in the offline-boxes config). Power the
hop down first and the leaf is unreachable AND un-shutdownable -- a hard cut
mid-write. Order is therefore a topological sort: a box is always powered
down BEFORE the box it routes through.

Usage (dry-run is the DEFAULT; you must opt IN to action with --apply):

    scripts/fleet_power.py plan   <box>...
    scripts/fleet_power.py down   <box>... --until +4h --reason "UPS install" --apply
    scripts/fleet_power.py resume [<box>...] --wait 30m --apply

Freeze note: `.claude/rules/harness_restraint.md` bars new probes/gates/
detectors until 2026-10-09. This is an OPERATOR TOOL whose stated END is the
product -- the fleet surviving a power event -- not the harness. Exempt.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from typing import Dict, List, Optional, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))
sys.path.insert(0, _HERE)

from utils import fleet_posture as fp  # noqa: E402
from utils.paths import get_real_user_home  # noqa: E402

# The dependency graph lives in the offline monitor's box config. Reusing it is
# deliberate: a second graph WOULD drift from the first (hfm #5), and that file
# is already the place a `via` is declared and already drilled by the monitor's
# own tests.
DEFAULT_BOXES = os.environ.get(
    "MESHFORGE_OFFLINE_BOXES",
    str(get_real_user_home() / ".config" / "meshforge" / "fleet_offline_boxes.json"),
)

# Overridable so tests drive the REAL code paths with a fake ssh rather than
# re-implementing them (the offline monitor's pattern, hardened by incidents).
SSH_BASE = os.environ.get(
    "MESHFORGE_POWER_SSH",
    "ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new",
)
SSH_TIMEOUT = int(os.environ.get("MESHFORGE_POWER_SSH_TIMEOUT", "20"))

# What a planned multi-box shutdown legitimately costs. Printed so the next
# session finds it ANNOUNCED rather than discovering it in a WARN: on
# 2026-09-10 exactly these two fired and cost a session twenty minutes of
# forensics to attribute back to its own shutdown.
EXPECTED_SIDE_EFFECTS = (
    "one round-trip canary cycle may FAIL while peers are dark "
    "(resource_canary_degraded, self-clears on the next cycle)",
    "surviving boxes will report tracer_peer_unreachable for each dark peer",
)


class Refusal(Exception):
    """A refusal names what it looked at, never just that it said no."""


# --------------------------------------------------------------------------
# the graph
# --------------------------------------------------------------------------

def load_graph(path: str) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """(boxes_by_name, via_by_name). A `via` is an ssh DESTINATION and may not
    be a fleet box at all -- trdev and alaula are hops, not members."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        raise Refusal(f"no box config at {path} -- the dependency graph is unknown, "
                      "and an unordered fleet shutdown can strand a box behind a hop. "
                      "Refusing rather than guessing the order.")
    except (OSError, ValueError) as exc:
        raise Refusal(f"box config at {path} is unreadable/invalid ({exc}). "
                      "Refusing: an empty graph would LOOK like 'no dependencies', "
                      "which is the failure mode this ordering exists to prevent.")
    boxes: Dict[str, dict] = {}
    via: Dict[str, str] = {}
    for b in doc.get("boxes") or []:
        name = str(b.get("name") or "").strip()
        if not name:
            continue
        boxes[name] = b
        hop = str(b.get("via") or "").strip()
        if hop:
            via[name] = hop
    return boxes, via


def known_hosts(boxes: Dict[str, dict], via: Dict[str, str]) -> set:
    """Every name this tool will accept: fleet boxes PLUS the hops they route
    through. A hop is a legitimate shutdown target (it has power too) even
    though nothing monitors it and it gets no posture entry."""
    return set(boxes) | set(via.values())


def order_shutdown(targets: Sequence[str], via: Dict[str, str]) -> List[str]:
    """Topological: a box is powered down BEFORE the box it routes through.

    Emitted in layers, each layer sorted, so the order is deterministic and a
    test can pin it. A cycle is a loud refusal -- it means two boxes each
    claim to route through the other, and no order is safe.
    """
    remaining = list(dict.fromkeys(targets))  # de-dup, keep first-seen order
    out: List[str] = []
    while remaining:
        # A node is ready when nothing still-remaining depends on it, i.e. no
        # remaining box routes THROUGH it.
        ready = sorted(
            n for n in remaining
            if not any(via.get(r) == n for r in remaining if r != n)
        )
        if not ready:
            raise Refusal(
                "dependency cycle among " + ", ".join(sorted(remaining)) +
                " -- each routes through another, so no shutdown order is safe. "
                "Fix the `via` declarations before powering anything down.")
        out.extend(ready)
        remaining = [r for r in remaining if r not in ready]
    return out


# --------------------------------------------------------------------------
# reachability
# --------------------------------------------------------------------------

def _ssh(host: str, command: str, timeout: Optional[int] = None) -> Tuple[int, str]:
    argv = SSH_BASE.split() + [host, command]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout or SSH_TIMEOUT)
        return proc.returncode, (proc.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError as exc:
        return 255, str(exc)


def reachable(host: str) -> bool:
    rc, _ = _ssh(host, "true", timeout=12)
    return rc == 0


# --------------------------------------------------------------------------
# refusals -- each names what it looked at
# --------------------------------------------------------------------------

def self_name() -> str:
    """Overridable so the guard below is DRILLABLE. Without this the manager is
    never in the boxes config, so check_not_self could not fire on the one box
    it exists to protect -- a guard structurally incapable of refusing is not a
    guard (found by drilling it, 2026-09-10)."""
    return os.environ.get("MESHFORGE_POWER_SELF") or socket.gethostname()


def check_not_self(targets: Sequence[str]) -> None:
    me = self_name()
    short = me.split(".")[0].lower()
    for t in targets:
        tl = t.lower()
        # The '-' boundary matters: a fleet name is often an alias for a
        # longer real hostname (a box the fleet calls <name> may answer as
        # <prefix>-<name>), so a suffix match is needed -- but a BARE endswith
        # would also match a short box name against any hostname merely ENDING
        # in those letters, silently refusing a legitimate target.
        if tl in (me.lower(), short) or short.endswith("-" + tl):
            raise Refusal(
                f"'{t}' looks like THIS box ({me}). The manager must never be in "
                "the fan-out: it is the box running this command, the box that "
                "holds the declaration, and the only thing still watching. Power "
                "it down by hand, last, after everything else is confirmed dark.")


def check_no_stranding(targets: Sequence[str], via: Dict[str, str],
                       probe=reachable) -> List[str]:
    """A hop may only go down once everything behind it is ALSO going down.

    Returns the notes for hops whose dependants are already dark (fine);
    raises on a dependant that is UP and not in this operation -- shutting the
    hop would make it unreachable, undeclared, and it would page UNOBSERVABLE.
    """
    tset = set(targets)
    notes: List[str] = []
    for hop in sorted(tset):
        behind = sorted(b for b, h in via.items() if h == hop and b not in tset)
        for dep in behind:
            if probe(dep):
                raise Refusal(
                    f"'{hop}' is the only path for '{dep}', which is NOT in this "
                    f"operation and is currently UP. Powering '{hop}' down would "
                    f"leave '{dep}' unreachable, un-shutdownable, and paging "
                    f"UNOBSERVABLE. Add '{dep}' to the targets (it will be ordered "
                    f"first), or leave '{hop}' up.")
            notes.append(f"'{dep}' routes via '{hop}' and is already dark — not stranded")
    return notes


def bridge_boxes():
    """Delegate to the posture CLI's resolver rather than re-deriving it.
    Two consumers of one artifact share ONE implementation (hfm #5) -- a second
    copy of 'which boxes bridge' would drift from the declare-time validator
    and the two would disagree about whether a posture is legal."""
    try:
        import fleet_posture as posture_cli
        return posture_cli._bridge_boxes()
    except (ImportError, AttributeError) as exc:
        return None, f"bridge resolver unavailable ({exc}) — mesh-less refusal SKIPPED"


# --------------------------------------------------------------------------
# verbs
# --------------------------------------------------------------------------

def build_plan(targets: Sequence[str], boxes: Dict[str, dict], via: Dict[str, str],
               probe=reachable) -> dict:
    check_not_self(targets)
    known = known_hosts(boxes, via)
    unknown = [t for t in targets if t not in known]
    if unknown:
        raise Refusal(
            "unknown host(s): " + ", ".join(unknown) + ". Known: " +
            ", ".join(sorted(known)) + ". A name this tool does not know has no "
            "declared dependencies, so it cannot be safely ordered.")
    order = order_shutdown(targets, via)
    notes = check_no_stranding(targets, via, probe=probe)
    return {
        "order": order,
        "notes": notes,
        # A hop that is not a fleet box gets no posture entry: nothing watches
        # it, so declaring it would be a claim with no consumer.
        "declarable": [t for t in order if t in boxes],
        "hops_only": [t for t in order if t not in boxes],
    }


def cmd_plan(args) -> int:
    boxes, via = load_graph(args.boxes)
    plan = build_plan(args.box, boxes, via)
    print("shutdown order (leaf first, hop last):")
    for i, name in enumerate(plan["order"], 1):
        hop = via.get(name)
        tag = f"  -> routes via {hop}" if hop else ""
        kind = "" if name in plan["declarable"] else "   [hop; not a fleet box, no posture entry]"
        print(f"  {i}. {name}{tag}{kind}")
    for n in plan["notes"]:
        print(f"  note: {n}")
    return 0


def cmd_down(args) -> int:
    boxes, via = load_graph(args.boxes)
    plan = build_plan(args.box, boxes, via)
    order = plan["order"]

    until = fp.parse_until(args.until)
    if until is None:
        raise Refusal(f"could not parse --until '{args.until}' (try +4h, +2d, or ISO-8601)")

    # STEP ONE IS THE DECLARATION, and it is ONE write. declare() is pure, so
    # every target composes into a single document before it reaches the disk
    # -- there is no window between the first box being declared and the last.
    path = args.posture or fp.posture_path()
    doc = fp.load_doc(path)
    for name in plan["declarable"]:
        doc = fp.declare(doc, name, fp.STATE_DORMANT, until, reason=args.reason)

    bridges, bnote = bridge_boxes()
    errs = fp.validate(doc, bridge_boxes=bridges)
    if errs and not args.force:
        raise Refusal("posture validator refused:\n  " + "\n  ".join(errs) +
                      "\n  (--force records the refusal in the file instead of blocking)")

    print(f"targets     : {len(order)} ({', '.join(order)})")
    print(f"declarable  : {', '.join(plan['declarable']) or '(none)'}")
    if plan["hops_only"]:
        print(f"hops only   : {', '.join(plan['hops_only'])}  (no posture entry — nothing watches them)")
    print(f"until       : {fp.fmt_ts(until)}")
    print(f"bridges     : {bnote}")
    if errs:
        print(f"⚠️ validator : {len(errs)} refusal(s) OVERRIDDEN by --force")
    for n in plan["notes"]:
        print(f"note        : {n}")
    print("expected side effects (announced, not discovered later):")
    for e in EXPECTED_SIDE_EFFECTS:
        print(f"  - {e}")
    print("order       :")
    for i, name in enumerate(order, 1):
        print(f"  {i}. {name}")

    if not args.apply:
        print("\n=== DRY RUN — nothing declared, nothing powered off. Re-run with --apply ===")
        return 0

    backup = fp.write_doc(path, doc)
    print(f"\ndeclared {len(plan['declarable'])} box(es) in ONE write -> {path}"
          + (f" (backup {backup})" if backup else ""))

    # Verify the declaration is readable BEFORE powering anything off. A
    # shutdown under a declaration that did not land is the page storm this
    # tool exists to prevent, and it would be unrecoverable once the boxes are
    # dark. Re-READ rather than trusting the write.
    check = fp.read_posture(path)
    missing = [n for n in plan["declarable"] if n not in (check.boxes or {})]
    if missing:
        raise Refusal("declaration did not land for: " + ", ".join(missing) +
                      " — REFUSING to power anything down. Nothing has been shut off.")
    print("declaration re-read from disk and confirmed — proceeding")

    failed = []
    for name in order:
        print(f"\n--- {name} ---")
        rc, _ = _ssh(name, "sudo -n systemctl poweroff --no-block")
        if rc not in (0, 255):  # 255 = connection dropped as it goes down
            print(f"  poweroff returned rc={rc} — continuing to verify anyway")
        gone = False
        for _ in range(args.settle // 5 or 1):
            time.sleep(5)
            if not reachable(name):
                gone = True
                break
        if gone:
            print(f"  DOWN — {name} no longer answers")
        else:
            failed.append(name)
            print(f"  ⚠️ STILL ANSWERING after {args.settle}s")
            if any(via.get(b) == name for b in order):
                raise Refusal(
                    f"'{name}' is a hop for another target and did not go down. "
                    "STOPPING: continuing could strand a box behind a half-dead "
                    f"path. Boxes already off: {', '.join(order[:order.index(name)]) or '(none)'}")

    print("\n=== done ===")
    if failed:
        print(f"⚠️ {len(failed)} box(es) did not confirm down: {', '.join(failed)}")
        print("   They are DECLARED dormant but ANSWERING — expect POSTURE-DRIFT, correctly.")
        return 1
    print(f"all {len(order)} confirmed down. Run `fleet_power.py resume --apply` as they return —")
    print("the posture stays declared until then, and a returning box that is still")
    print("declared is what produces the drift pages this tool exists to prevent.")
    return 0


def cmd_resume(args) -> int:
    path = args.posture or fp.posture_path()
    posture = fp.read_posture(path)
    declared = sorted((posture.boxes or {}).keys())
    targets = list(args.box) if args.box else declared
    if not targets:
        print("no boxes declared and none named — nothing to resume.")
        return 0

    unknown = [t for t in targets if t not in declared]
    if unknown:
        print(f"note: not currently declared (nothing to clear): {', '.join(unknown)}")
        targets = [t for t in targets if t in declared]
    if not targets:
        return 0

    print(f"watching {len(targets)} box(es) to return: {', '.join(targets)}")
    print(f"deadline    : {args.wait}s")
    if not args.apply:
        print("\n=== DRY RUN — will not poll or clear. Re-run with --apply ===")
        return 0

    deadline = time.time() + args.wait
    pending = list(targets)
    returned: List[str] = []
    while pending and time.time() < deadline:
        for name in list(pending):
            if not reachable(name):
                continue
            # Clear IMMEDIATELY, per box, the moment it answers. Batching the
            # clears would reopen exactly the window this mirrors shut: a box
            # that is up and still declared is POSTURE-DRIFT on the very next
            # poll, and the poller is faster than a human.
            doc = fp.load_doc(path)
            doc, existed = fp.clear(doc, name)
            if existed:
                fp.write_doc(path, doc)
            pending.remove(name)
            returned.append(name)
            print(f"  {name} answered — posture CLEARED ({'was declared' if existed else 'already clear'})")
        if pending:
            time.sleep(10)

    print("\n=== done ===")
    print(f"returned+cleared : {', '.join(returned) or '(none)'}")
    if pending:
        print(f"⚠️ still dark    : {', '.join(pending)}")
        print("   Posture is INTENTIONALLY left declared for these — clearing a box that")
        print("   has not come back would turn a real outage into a DOWN page with no")
        print("   declaration behind it. Re-run resume, or investigate.")
        return 1
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--boxes", default=DEFAULT_BOXES, help="dependency graph (offline-boxes config)")
    ap.add_argument("--posture", default=None, help="posture file (default: the SSOT path)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="print the shutdown order; no side effects")
    p.add_argument("box", nargs="+")
    p.set_defaults(fn=cmd_plan)

    d = sub.add_parser("down", help="declare + power off, in dependency order")
    d.add_argument("box", nargs="+")
    d.add_argument("--until", default="+4h")
    d.add_argument("--reason", default="")
    d.add_argument("--settle", type=int, default=60, help="seconds to wait for each box to go dark")
    d.add_argument("--force", action="store_true", help="override the posture validator, recorded")
    d.add_argument("--apply", action="store_true", help="actually do it (default: dry run)")
    d.set_defaults(fn=cmd_down)

    r = sub.add_parser("resume", help="poll for boxes returning and clear posture per box")
    r.add_argument("box", nargs="*")
    r.add_argument("--wait", type=int, default=1800, help="seconds to keep watching")
    r.add_argument("--apply", action="store_true", help="actually poll+clear (default: dry run)")
    r.set_defaults(fn=cmd_resume)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except Refusal as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
