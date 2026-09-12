#!/usr/bin/env python3
"""fleet_platform.py — the update SURFACE: declared platform vs actual, and
what every pinned dependency is waiting on.

READ-ONLY by design, with one deliberate exception (`declare`/`clear`, which
write the operator's own decisions to ~/.config). Nothing here upgrades,
installs, holds, reboots, or touches a service. Acting stays in scripts run
knowingly — an update path with fleet-wide blast radius behind a menu item is
the 2026-07-24 shape, where a "restart every installed unit" sweep started a
service that was off BY DESIGN.

    scripts/fleet_platform.py show            # declared vs actual, per box
    scripts/fleet_platform.py show --local    # just this box (standalone)
    scripts/fleet_platform.py pins            # what we hold and what would move it
    scripts/fleet_platform.py declare moc4 bookworm --reason "standalone-compat canary"
    scripts/fleet_platform.py clear moc4

SSOT: src/utils/fleet_platform.py (read that docstring first).
Catalog: docs/fleet_platform.yaml (committed, generic).
Declarations: ~/.config/meshforge/fleet_platform_declared.json (instance, MF014).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "src"))

from utils.fleet_platform import (  # noqa: E402
    DRIFT, INERT, OK, UNKNOWN, Declaration, declared_path, judge_box,
    load_catalog, load_declarations, summarize,
)
from utils.paths import get_real_user_home  # noqa: E402

SSH_BASE = ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new"]
SSH_TIMEOUT = 75

# One command, one line out. Kept tiny on purpose: this runs on every box and
# a heavy probe would be machinery watching machinery.
#
# ⚠️ The apt simulation is BOUNDED SEPARATELY and allowed to fail. Measured
# 2026-09-11: `apt-get -s upgrade` takes 12s on moc and 24s on moc3, and with
# one outer timeout a slow apt took the whole probe down — moc3 read UNKNOWN
# ("not observed") on a box that was up, reachable, and answering. An
# EXPENSIVE OPTIONAL field must never destroy the CHEAP ESSENTIAL one: base
# and python are what the verdict turns on, and they cost nothing. A pending
# count that times out renders as '?', which is honest; a base that times out
# because of it was a lie about the box.
PROBE = (
    '. /etc/os-release 2>/dev/null; '
    'pend=$(timeout 45 apt-get -s upgrade 2>/dev/null | grep -c \'^Inst \'); '
    'printf "%s|%s|%s|%s" '
    '"$VERSION_CODENAME" '
    '"$(python3 -V 2>&1 | awk \'{print $2}\')" '
    '"$pend" '
    '"$(apt-mark showhold 2>/dev/null | tr \'\\n\' \',\')"'
)

GLYPH = {OK: "🟢", INERT: "⚪", DRIFT: "🔶", UNKNOWN: "❓"}


def _ssh(host: str, command: str) -> Tuple[int, str]:
    try:
        p = subprocess.run(SSH_BASE + [host, command], capture_output=True,
                           text=True, timeout=SSH_TIMEOUT)
        return p.returncode, (p.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError as e:
        return 255, str(e)


def _parse(out: str) -> Dict:
    parts = (out or "").split("|")
    while len(parts) < 4:
        parts.append("")
    pending = None
    try:
        pending = int(parts[2])
    except (TypeError, ValueError):
        pending = None
    holds = [h for h in parts[3].split(",") if h]
    return {"base": parts[0] or None, "python": parts[1] or None,
            "pending": pending, "holds": holds}


def observe(hosts: List[str], local_name: str) -> Dict[str, Dict]:
    """host -> observation. A host we could not reach yields base=None, which
    the judge turns into UNKNOWN — never into a healthy default."""
    out: Dict[str, Dict] = {}

    def one(h: str):
        if h == local_name:
            p = subprocess.run(["bash", "-c", PROBE], capture_output=True,
                               text=True, timeout=SSH_TIMEOUT)
            return h, _parse(p.stdout)
        rc, o = _ssh(h, PROBE)
        if rc != 0:
            why = ("probe timed out after "
                   f"{SSH_TIMEOUT}s — OUR limit, not necessarily the box"
                   ) if rc == 124 else f"ssh rc={rc}"
            return h, {"base": None, "python": None, "pending": None,
                       "holds": [], "why": why}
        return h, _parse(o)

    with ThreadPoolExecutor(max_workers=8) as ex:
        for h, obs in ex.map(one, hosts):
            out[h] = obs
    return out


def fleet_hosts() -> List[str]:
    p = get_real_user_home() / ".config" / "meshforge" / "fleet_hosts"
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [ln.split("#", 1)[0].strip() for ln in lines
            if ln.split("#", 1)[0].strip()]


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------

def cmd_show(args) -> int:
    catalog, errs = load_catalog(args.catalog)
    if catalog is None:
        print(f"FAIL: platform catalog unusable — {'; '.join(errs)}")
        return 1
    decls, status = load_declarations(args.declared)

    local = os.uname().nodename
    hosts = [local] if args.local else (fleet_hosts() or [local])
    if local not in hosts:
        hosts = hosts + [local]

    obs = observe(hosts, local)
    verdicts = [judge_box(h, obs[h].get("base"), catalog, decls,
                          actual_python=obs[h].get("python"),
                          decl_status=status,
                          unobserved_why=obs[h].get("why"))
                for h in hosts]
    # Problems first: a clean pane should read clean at a glance.
    order = {DRIFT: 0, UNKNOWN: 1, INERT: 2, OK: 3}
    verdicts.sort(key=lambda v: (order.get(v.verdict, 9), v.box))

    print(f"# platform posture — {len(hosts)} box(es) · "
          f"target={','.join(catalog.target_bases())} · "
          f"declarations={status}")
    if status in ("unreadable", "invalid"):
        print(f"  ⚠️  the declarations file is {status} — deliberate deviations "
              f"cannot be distinguished from drift, so they read UNKNOWN")
    print()
    for v in verdicts:
        o = obs.get(v.box, {})
        pend = o.get("pending")
        extra = []
        if pend is not None:
            extra.append(f"{pend} pending")
        if o.get("holds"):
            extra.append("holds=" + ",".join(o["holds"]))
        tail = ("  · " + " · ".join(extra)) if extra else ""
        stale = "  ⏳ declaration unreviewed" if v.stale_declaration else ""
        print(f"{GLYPH.get(v.verdict,'?')} {v.box:<20} "
              f"{(v.actual_base or '?'):<9} py{(v.actual_python or '?'):<8} "
              f"{v.verdict:<7} {v.detail}{tail}{stale}")

    s = summarize(verdicts)
    print(f"\n{s[OK]} ok · {s[INERT]} inert(declared) · {s[DRIFT]} drift · "
          f"{s[UNKNOWN]} unknown")
    if s[UNKNOWN]:
        print("  unknown is not a pass — a box we could not observe has an "
              "unknown platform, not a healthy one")
    # Exit code carries the finding, so a cron could judge it later WITHOUT
    # this script ever gaining the power to act.
    return 1 if s[DRIFT] else 0


def cmd_pins(args) -> int:
    catalog, errs = load_catalog(args.catalog)
    if catalog is None:
        print(f"FAIL: platform catalog unusable — {'; '.join(errs)}")
        return 1
    print(f"# pinned dependencies — {len(catalog.pins)} pin(s)\n")
    for name, pin in sorted(catalog.pins.items()):
        watched = "self-monitoring" if pin.self_monitoring else "⚠️ MANUAL ONLY"
        print(f"── {name}  [{pin.mechanism}]  @ {pin.current}   ({watched})")
        if pin.verified_at:
            print(f"   last verified: {pin.verified_at}")
        print(f"   why: {pin.why}")
        if pin.blast_radius:
            print(f"   blast radius: {pin.blast_radius}")
        for r in pin.recheck:
            print(f"   recheck [{r.kind}]: {r.means}")
        if not pin.self_monitoring:
            print("   ⚠️  nothing watches this pin. Its presence here is a "
                  "record, not a monitor — re-evaluating it is a human act.")
        print()
    return 0


def cmd_recheck(args) -> int:
    """Run each pin's declared predicates and say whether its reason stands.

    Read-only, and deliberately so: deciding a pin has lifted is a human act.
    The predicate exists to make that decision CHEAP, not automatic.
    """
    catalog, errs = load_catalog(args.catalog)
    if catalog is None:
        print(f"FAIL: platform catalog unusable — {'; '.join(errs)}")
        return 1
    from utils.pin_recheck import HOLDS, MANUAL, MOVED, UNKNOWN, evaluate_pin

    mark = {HOLDS: "🟢 holds  ", MOVED: "🔶 MOVED  ",
            UNKNOWN: "❓ unknown", MANUAL: "⚪ manual "}
    results = []
    names = args.pin or sorted(catalog.pins)
    for name in names:
        pin = catalog.pins.get(name)
        if pin is None:
            print(f"no such pin: {name!r} "
                  f"(have: {', '.join(sorted(catalog.pins))})")
            return 1
        res = evaluate_pin(pin)
        results.append(res)
        print(f"{mark.get(res.verdict, '?')}  {name}  @ {pin.current}")
        for pr in res.predicates:
            print(f"      [{pr.kind}] {pr.result}: {pr.detail}")
        print()

    moved = [r.name for r in results if r.verdict == MOVED]
    unk = [r.name for r in results if r.verdict == UNKNOWN]
    man = [r.name for r in results if r.verdict == MANUAL]
    held = [r.name for r in results if r.verdict == HOLDS]
    print(f"{len(held)} holds · {len(moved)} MOVED · {len(unk)} unknown · "
          f"{len(man)} manual-only")
    if moved:
        print(f"  MOVED: {', '.join(moved)} — a condition this pin depends on "
              f"has changed. Go read it; do NOT assume the pin can lift.")
    if unk:
        # Surfaced as its own line, never folded into a healthy total
        # (calibrated_claims #5).
        print(f"  UNKNOWN: {', '.join(unk)} — could NOT be evaluated. That is "
              f"not evidence the pin still holds.")
    if man:
        print(f"  MANUAL-ONLY: {', '.join(man)} — nothing watches these. They "
              f"will say this every time, because it stays true.")
    return 1 if moved else 0


def cmd_declare(args) -> int:
    catalog, errs = load_catalog(args.catalog)
    if catalog is None:
        print(f"FAIL: platform catalog unusable — {'; '.join(errs)}")
        return 1
    base = catalog.bases.get(args.base)
    if base is None:
        print(f"REFUSED: {args.base!r} is not a base in the catalog "
              f"({', '.join(sorted(catalog.bases))}). Declaring a base the "
              f"catalog does not know would create a deviation nothing can judge.")
        return 1
    if base.tier == "target":
        print(f"REFUSED: {args.base!r} is the TARGET base — it needs no "
              f"declaration, and recording one would imply a deviation that "
              f"does not exist.")
        return 1
    if base.tier == "deprecated":
        print(f"NOTE: {args.base!r} is DEPRECATED. Recording this does not make "
              f"it settled — the box will still read DRIFT, because a "
              f"declaration cannot un-deprecate a base. Use it to capture WHY "
              f"the retirement has not happened yet.")

    p = Path(args.declared) if args.declared else declared_path()
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        doc = {"boxes": {}}
    except (OSError, ValueError) as e:
        print(f"FAIL: declarations file present but unreadable ({e}) — "
              f"refusing to overwrite decisions I cannot read")
        return 1
    if not isinstance(doc.get("boxes"), dict):
        print("FAIL: declarations file has no 'boxes' object — refusing to "
              "replace a file I do not understand")
        return 1

    doc["boxes"][args.box] = {
        "base": args.base,
        "reason": args.reason,
        "reviewed": args.reviewed or time.strftime("%Y-%m-%d"),
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        bak = p.with_suffix(p.suffix + f".bak-{int(time.time())}")
        bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"  backup: {bak.name}")
    from mini_dudeai._util import atomic_write_json
    atomic_write_json(str(p), doc)
    print(f"declared {args.box} -> {args.base}: {args.reason}")
    print(f"  {args.box} will now read INERT (deliberate), not DRIFT."
          if base.tier == "supported" else
          f"  {args.box} still reads DRIFT — {args.base} is deprecated.")
    return 0


def cmd_clear(args) -> int:
    p = Path(args.declared) if args.declared else declared_path()
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"nothing to clear — no declarations file at {p}")
        return 0
    except (OSError, ValueError) as e:
        print(f"FAIL: declarations unreadable ({e}) — changing nothing")
        return 1
    if args.box not in (doc.get("boxes") or {}):
        print(f"nothing to clear — {args.box} carries no declaration")
        return 0
    doc["boxes"].pop(args.box)
    bak = p.with_suffix(p.suffix + f".bak-{int(time.time())}")
    bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    from mini_dudeai._util import atomic_write_json
    atomic_write_json(str(p), doc)
    print(f"cleared {args.box} (backup {bak.name}) — it will read DRIFT again "
          f"if it still deviates. That is the point: the decision is gone.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--catalog", default=None, help="catalog path override")
    ap.add_argument("--declared", default=None, help="declarations path override")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("show", help="declared vs actual, per box")
    s.add_argument("--local", action="store_true", help="this box only")
    s.set_defaults(fn=cmd_show)

    p = sub.add_parser("pins", help="what we hold and what would move it")
    p.set_defaults(fn=cmd_pins)

    rc_ = sub.add_parser("recheck", help="evaluate each pin's predicates (network)")
    rc_.add_argument("pin", nargs="*", help="pin name(s); default all")
    rc_.set_defaults(fn=cmd_recheck)

    d = sub.add_parser("declare", help="record a deliberate platform deviation")
    d.add_argument("box")
    d.add_argument("base")
    d.add_argument("--reason", required=True)
    d.add_argument("--reviewed", default=None, help="YYYY-MM-DD (default: today)")
    d.set_defaults(fn=cmd_declare)

    c = sub.add_parser("clear", help="remove a box's declaration")
    c.add_argument("box")
    c.set_defaults(fn=cmd_clear)

    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        args = ap.parse_args((argv or []) + ["show"])
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
