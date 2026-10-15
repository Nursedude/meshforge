#!/usr/bin/env python3
"""box_truth_grant — the ONE CLI for NOC read grants (spec Q8, security pass S10).

ON A TARGET BOX (who may read me):
  list                                  grants: fingerprint, center, last read time + source
  add --pubkey '<line>' --center NAME   plan; with --confirm <fp last 8> it writes
  revoke --fp SHA256:...                plan; with --confirm <fp last 8> it removes the key
                                        from BOTH authorized_keys files (--force for a line
                                        that is not one of ours)
  dedupe [--apply]                      remove exact duplicate authorized_keys lines

ON A CENTER (what I read):
  center-key [--create]                 show / create ~/.ssh/<app>_noc_ed25519
  push TARGET --confirm TARGET          grant this center's key on TARGET via its own
                                        `add` (identity-checked first, B6), verify a
                                        pinned read, register TARGET, ensure the spool cron
  unpush TARGET --confirm TARGET        revoke on TARGET; target stays listed, unflagged (reads DARK)
  rotate --confirm rotate               new key everywhere -> verify -> revoke old -> swap
  bulk-revoke --fp SHA256:... --confirm <last 8>   revoke one key on every pinned target

Every write prints its plan first; without the typed confirmation nothing is
written. Exit: 0 done, 1 refused/failed, 2 usage.

Spec: .claude/plans/noc_read_grant_2026_10_13.md
"""
import argparse
import datetime
import os
import sys
from pathlib import Path

_SRC = Path(os.path.realpath(__file__)).parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils import box_truth as bt  # noqa: E402
from utils import box_truth_app as app  # noqa: E402
from utils import box_truth_center as bc  # noqa: E402
from utils import box_truth_collect as bcol  # noqa: E402
from utils import box_truth_grants as bg  # noqa: E402
from utils import box_truth_reader as br  # noqa: E402
from utils.paths import get_real_user_home  # noqa: E402

EXPORT_PATH = str(Path(os.path.realpath(__file__)).parent / bt.EXPORT_BASENAME)


def _registry_expect(alias):
    return app.expect_hostkey_of(alias)[0]


def _when(ts):
    if not isinstance(ts, (int, float)):
        return "never"
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def cmd_list(home, _a):
    rows = bg.list_grants(home)
    if not rows:
        print("no NOC read grants on this box")
        return 0
    for r in rows:
        warn = ("  ⚠ FOREIGN — mentions the export but is NOT a restricted grant; "
                "it may be UNRESTRICTED (revoke --fp … --force)") if r["foreign"] else ""
        print(f"{r['fingerprint']}  {r['comment']:<24} last read {_when(r['last_read_ts'])}"
              f" from {r['last_read_src'] or '-'}  [{r['file']}:{r['line']}"
              f"{' from=' + r['from'] if r['from'] else ''}]{warn}")
    return 0


def cmd_add(home, a):
    # Q6: only an app that RUNS here may grant a read of this box.
    running, why = bcol.own_app_running(bcol.Sources(home=home))
    if running is not True:
        print(f"REFUSED: {why} — run this box's grant from the app it runs "
              f"(this is {app.APP} at {app.INSTALL_DIR})")
        return 1
    plan = bg.plan_add(home, a.pubkey, center=a.center, export_path=EXPORT_PATH,
                       from_=a.from_)
    print(plan.render())
    if plan.problems:
        return 1
    if a.confirm is None:
        print(f"\nnothing written — re-run with --confirm {plan.confirm_tail}")
        return 1
    try:
        bg.apply_add(home, plan, a.confirm)
    except bg.GrantRefused as e:
        print(f"REFUSED: {e}")
        return 1
    print(f"granted {plan.fingerprint} (read back OK)")
    return 0


def cmd_revoke(home, a):
    plan = bg.plan_revoke(home, a.fp, force=a.force)
    print(plan.render())
    if plan.problems:
        return 1
    if a.confirm is None:
        print(f"\nnothing written — re-run with --confirm {plan.confirm_tail}")
        return 1
    try:
        n = bg.apply_revoke(home, plan, a.confirm)
    except bg.GrantRefused as e:
        print(f"REFUSED: {e}")
        return 1
    print(f"revoked {plan.fingerprint}: {n} line(s) removed (read back OK)")
    return 0


def cmd_dedupe(home, a):
    plan = bg.plan_dedupe(home)
    print(plan.render())
    if not plan.removes:
        print("no exact duplicates")
        return 0
    if not a.apply:
        print("\nnothing written — re-run with --apply")
        return 1
    try:
        print(f"removed {bg.apply_dedupe(home, plan)} duplicate line(s)")
    except bg.GrantRefused as e:
        print(f"REFUSED: {e}")
        return 1
    return 0


def cmd_center_key(home, a):
    path = br.noc_key_path(home)
    try:
        key = bc.load_center_key(home)
    except FileNotFoundError:
        if not a.create:
            print(f"no center key at {path} — re-run with --create")
            return 1
        key = bc.create_center_key(home)
        print(f"created {path} (0600)")
    except bt.KeyRejected as e:
        print(f"center key at {path} unusable: {e}")
        return 1
    print(f"fingerprint: {key.fingerprint}\npublic key : {key.line()}")
    return 0


def _print(rep):
    print("\n".join(rep.steps))
    return 0 if rep.ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="NOC read grants (box_truth/v1)")
    sub = ap.add_subparsers(dest="verb", required=True)
    sub.add_parser("list")
    p = sub.add_parser("add")
    p.add_argument("--pubkey", required=True)
    p.add_argument("--center", required=True)
    p.add_argument("--from", dest="from_", default=None)
    p.add_argument("--confirm", default=None)
    p = sub.add_parser("revoke")
    p.add_argument("--fp", required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--confirm", default=None)
    p = sub.add_parser("dedupe")
    p.add_argument("--apply", action="store_true")
    p = sub.add_parser("center-key")
    p.add_argument("--create", action="store_true")
    for verb in ("push", "unpush"):
        p = sub.add_parser(verb)
        p.add_argument("target")
        p.add_argument("--confirm", required=True)
    p = sub.add_parser("rotate")
    p.add_argument("--confirm", required=True)
    p = sub.add_parser("bulk-revoke")
    p.add_argument("--fp", required=True)
    p.add_argument("--confirm", required=True)
    a = ap.parse_args(argv)

    # Grants live in the OPERATOR's ~/.ssh and crontab. Run as root (sudo),
    # this tool created root-owned dirs in the operator's home and wrote
    # root's crontab (delta read 2026-10-15) — refuse; the TUI already runs
    # it as the operator.
    if os.geteuid() == 0:
        print("REFUSED: run box_truth_grant.py as your own user, not root — "
              "grants live in your ~/.ssh and your crontab")
        return 1
    home = get_real_user_home()
    if a.verb == "list":
        return cmd_list(home, a)
    if a.verb == "add":
        return cmd_add(home, a)
    if a.verb == "revoke":
        return cmd_revoke(home, a)
    if a.verb == "dedupe":
        return cmd_dedupe(home, a)
    if a.verb == "center-key":
        return cmd_center_key(home, a)
    if a.verb == "push":
        return _print(bc.push(a.target, a.confirm, home=home,
                              expect=_registry_expect(a.target)))
    if a.verb == "unpush":
        return _print(bc.unpush(a.target, a.confirm, home=home,
                                expect=_registry_expect(a.target)))
    if a.verb == "rotate":
        return _print(bc.rotate(a.confirm, home=home, expect_of=_registry_expect))
    if a.verb == "bulk-revoke":
        return _print(bc.bulk_revoke(a.fp, a.confirm, home=home,
                                     expect_of=_registry_expect))
    return 2


if __name__ == "__main__":
    sys.exit(main())
