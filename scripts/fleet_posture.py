#!/usr/bin/env python3
"""fleet_posture.py — declare, clear, show and check the fleet's power posture.

The operator's door onto ~/.config/meshforge/fleet_posture.json (SSOT in
src/utils/fleet_posture.py — read that docstring first). Manager-side.
Declaring is a deliberate act: validate → write (atomic, backed up) →
print what every consumer will now do with it.

    scripts/fleet_posture.py show
    scripts/fleet_posture.py check
    scripts/fleet_posture.py declare moc4 dormant --until +3d --reason "storm tier-2"
    scripts/fleet_posture.py declare kit detached --until 2026-09-20T00:00:00Z
    scripts/fleet_posture.py declare moc shed --until +2d --services meshtasticd rnsd
    scripts/fleet_posture.py clear moc4

`--force` records a validator refusal in the file (`forced_reasons`) instead
of blocking — the refusal is written beside the declaration, never lost.
Bridge-capable boxes for the mesh-less refusal come from the role catalog
when this box carries fleet_hosts (the manager); elsewhere that leg is
skipped and SAID.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from utils import fleet_posture as fp  # noqa: E402


def _bridge_boxes():
    """Boxes whose declared role hosts a bridge/transport — from the role
    catalog via provision_role's fleet gather (manager only). Returns
    (set|None, note)."""
    try:
        sys.path.insert(0, _HERE)
        import provision_role as pr
        from pathlib import Path
        catalog = pr.load_roles(pr.DEFAULT_ROLES_FILE)
        hosts_file = Path(os.path.expanduser("~/.config/meshforge/fleet_hosts"))
        if not hosts_file.exists():
            return None, "no fleet_hosts here — mesh-less refusal skipped (not the manager)"
        # self_role is REQUIRED (provision_role.gather_fleet_roles(hosts,
        # self_role, ssh_cmd=...)). Omitting it raised TypeError every run and
        # the whole refusal was swallowed by the advisory except below, so this
        # guard had NEVER executed (found 2026-09-04).
        role_map = pr.gather_fleet_roles(
            pr.parse_fleet_hosts(hosts_file), pr.read_role())
        # gather_fleet_roles keys the local box as the literal "(self)", but the
        # posture document is keyed by REAL box name and validate() compares the
        # two sets directly (`bridges.issubset(silenced)`). Left as "(self)" the
        # guard would run, look healthy, and be structurally incapable of ever
        # refusing a posture that silences THIS box — a false-green guard, worse
        # than the loud skip it replaced. Re-key it to the hostname the operator
        # actually declares.
        self_key = socket.gethostname()
        if "(self)" in role_map:
            role_map[self_key] = role_map.pop("(self)")
        out = set()
        for host, role in role_map.items():
            if not role:
                continue
            svcs = (pr.resolve_role(catalog, role).get("services") or {})
            if svcs.get("meshforge-gateway") == "enabled":
                out.add(host)
        return out, f"bridge-capable boxes from roles: {', '.join(sorted(out)) or '(none)'}"
    except Exception as exc:  # the refusal leg is advisory; say why it is off
        return None, f"mesh-less refusal skipped: {type(exc).__name__}: {exc}"


def cmd_show(args) -> int:
    p = fp.read_posture(args.file)
    print(f"posture file: {p.path}")
    print(f"status: {p.status}" + (f" — {p.detail}" if p.detail else ""))
    if p.status != fp.DECLARED:
        print("effect: every box ACTIVE (watched as today)")
        return 0 if p.status == fp.UNDECLARED else 1
    print(f"declaration: {p.name or '(unnamed)'} by {p.declared_by or '?'} at "
          f"{fp.fmt_ts(p.declared_at) if p.declared_at else '?'}")
    for name, b in sorted(p.boxes.items()):
        flag = " [EXPIRED]" if b.expired else (" [HELD]" if b.held else "")
        print(f"  {name:20s} {b.state:9s}{flag}  {b.note}")
    return 0


def cmd_check(args) -> int:
    try:
        doc = fp.load_doc(args.file)
    except ValueError as exc:
        print(f"INVALID: {exc}")
        return 1
    bridges, note = _bridge_boxes()
    errs = fp.validate(doc, bridge_boxes=bridges)
    print(note)
    if errs:
        print("REFUSED:")
        for e in errs:
            print(f"  - {e}")
        return 1
    print("valid")
    return cmd_show(args)


def cmd_declare(args) -> int:
    now = time.time()
    until = None
    if args.state != fp.STATE_ACTIVE:
        until = fp.parse_until(args.until or "", now)
        if until is None:
            print(f"REFUSED: --until is mandatory for {args.state} "
                  f"(+36h / +3d / ISO-8601 with zone)")
            return 2
    try:
        doc = fp.load_doc(args.file)
    except ValueError as exc:
        print(f"REFUSED: existing file unreadable as JSON: {exc}")
        return 2
    new = fp.declare(doc, args.box, args.state, until, reason=args.reason or "",
                     services=args.services, now=now)
    bridges, note = _bridge_boxes()
    errs = fp.validate(new, now=now, bridge_boxes=bridges)
    print(note)
    if errs and not args.force:
        print("REFUSED (use --force to record the refusal and declare anyway):")
        for e in errs:
            print(f"  - {e}")
        return 1
    if errs:
        new.setdefault("forced_reasons", []).extend(
            {"at": fp.fmt_ts(now), "box": args.box, "refusal": e} for e in errs)
        print("FORCED past validator refusal(s) — recorded in the file:")
        for e in errs:
            print(f"  - {e}")
    backup = fp.write_doc(args.file, new)
    print(f"declared {args.box} {args.state}"
          + (f" until {fp.fmt_ts(until)}" if until else "")
          + (f" (backup {backup})" if backup else ""))
    print("consumers: offline monitor skips+witnesses silent boxes on its next "
          "*/5 tick and pages POSTURE-DRIFT if one answers")
    return 0


def cmd_clear(args) -> int:
    try:
        doc = fp.load_doc(args.file)
    except ValueError as exc:
        print(f"REFUSED: existing file unreadable as JSON: {exc}")
        return 2
    new, existed = fp.clear(doc, args.box)
    if not existed:
        print(f"{args.box}: nothing declared")
        return 0
    new["declared_at"] = fp.fmt_ts(time.time())
    backup = fp.write_doc(args.file, new)
    print(f"cleared {args.box} — ACTIVE (watched) again" + (f" (backup {backup})" if backup else ""))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--file", default=fp.posture_path(),
                    help="posture file (default: the SSOT path / $MESHFORGE_FLEET_POSTURE)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show").set_defaults(fn=cmd_show)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    d = sub.add_parser("declare")
    d.add_argument("box")
    d.add_argument("state", choices=fp.STATES)
    d.add_argument("--until", help="+36h / +3d / ISO-8601 with zone (mandatory unless active)")
    d.add_argument("--reason", default="")
    d.add_argument("--services", nargs="*", default=None,
                   help="shed: the reduced expected-active unit list")
    d.add_argument("--force", action="store_true")
    d.set_defaults(fn=cmd_declare)
    c = sub.add_parser("clear")
    c.add_argument("box")
    c.set_defaults(fn=cmd_clear)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
