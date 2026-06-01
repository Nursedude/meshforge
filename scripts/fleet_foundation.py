#!/usr/bin/env python3
"""fleet_foundation.py — audit/apply the born-correct permission foundation.

The CLI front-end for ``utils.fleet_foundation`` (the SSOT ratified at the
2026-06-01 architecture meeting). One definition of the foundation, invokable at
install (born-correct), by the provisioner (converge), and by an operator
in-app — never step outside the app to chase a perms drift.

    fleet_foundation.py audit                      # drift table; exit 1 if drift
    fleet_foundation.py apply --dry-run            # show actions, no changes
    fleet_foundation.py apply                      # converge THIS box (sudo)
    # options go ON the subcommand: `apply --user U --topology fleet|standalone`

Read-only by default. ``apply`` requires explicit invocation (and sudo).
"""

import argparse
import sys
from pathlib import Path

# Make src/ importable when run from scripts/
HERE = Path(__file__).resolve().parent
SRC = HERE.parent / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from utils.fleet_foundation import (  # noqa: E402
    apply_foundation,
    audit_foundation,
    canonical_foundation,
)


def _spec(args):
    return canonical_foundation(
        operator_user=args.user,
        topology=args.topology,
    )


def cmd_audit(args) -> int:
    spec = _spec(args)
    print(f"Foundation audit @ user={spec.operator_user} topology={spec.topology}")
    drift = audit_foundation(spec)
    if not drift:
        print("  [OK] foundation is correct (data-roots + RNS tree)")
        return 0
    for reason in drift:
        print(f"  [DRIFT] {reason}")
    print(f"\n-> {len(drift)} item(s). Fix: fleet_foundation.py apply")
    return 1


def cmd_apply(args) -> int:
    spec = _spec(args)
    mode = "DRY-RUN" if args.dry_run else "APPLY"
    print(f"Foundation {mode} @ user={spec.operator_user} topology={spec.topology}")
    actions = apply_foundation(spec, dry_run=args.dry_run)
    if not actions:
        print("  nothing to do — already correct")
        return 0
    for a in actions:
        print(f"  {a}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    # --user/--topology live on a parent parser added to EACH SUBCOMMAND (not the
    # top level): the canonical form is `audit --user U` / `apply --user U`. Adding
    # them at both levels makes the subparser default (None) clobber a top-level
    # value, so options go on the subcommand only. (The original bug was the
    # reverse — options top-level only — so `apply --user U` errored; caught
    # dogfooding the moc3 converge.)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--user', default=None,
                        help="operator user (default: resolved real user)")
    common.add_argument('--topology', default='fleet', choices=['fleet', 'standalone'])

    p = argparse.ArgumentParser(description="Audit/apply the permission foundation (SSOT)")
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('audit', parents=[common],
                   help="report foundation drift (exit 1 if any)")
    ap = sub.add_parser('apply', parents=[common],
                        help="converge this box to the foundation (sudo)")
    ap.add_argument('--dry-run', action='store_true', help="show actions, change nothing")
    return p


def main() -> int:
    args = build_parser().parse_args()
    return {'audit': cmd_audit, 'apply': cmd_apply}[args.cmd](args)


if __name__ == '__main__':
    sys.exit(main())
