#!/usr/bin/env python3
"""Promote this box's role seed into its live mini-dudeai rules.

THE CLI for ``mini_dudeai.candidate.merge_seed_rules`` — closes the
"sessions today, TUI later" gap (before this, activating a new watchdog signal
class meant hand-running the merge in an ad-hoc Python session per box).

The fleet ships a per-role seed (``configs/mini_dudeai_rules.<role>.json``) that
is the canonical rule set for the box's role; the live ``~/mini_dudeai_rules.json``
is seeded from it then evolves per-box. When the seed gains a rule (a new failure
class), this folds it in via the provenance merge WITHOUT clobbering box-tuned or
box-local rules — exactly the gap ``probe_rules_seed_drift`` watches for.

Role→seed resolution is the SAME path the probe uses (deployment.json →
``_ROLE_TO_MINI_SEED`` → ``configs/mini_dudeai_rules.<seed>.json``), so the CLI
and the probe can never disagree.

Usage:
    python3 scripts/promote_seed_rules.py             # dry-run: what would change
    python3 scripts/promote_seed_rules.py --apply     # merge + atomic-write (backs up)
    python3 scripts/promote_seed_rules.py --role fleet_gateway --apply
    python3 scripts/promote_seed_rules.py --json       # machine-readable

Default is DRY-RUN (like provision_role.py); nothing is written without --apply.
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

# Make src importable when run as a script (tests add it themselves).
_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

DEFAULT_ROOT = "/opt/meshforge"


class PromoteError(Exception):
    """A resolution/read failure — surfaced loud, never guessed past."""


def resolve_target(meshforge_root=DEFAULT_ROOT, mini_home=None, role=None,
                   seed_name=None):
    """Return (role, seed_name, seed_path, live_path) using the SAME resolution
    as ``probe_rules_seed_drift`` — or raise PromoteError with a clear reason.

    ``seed_name`` targets a seed directly (``federator`` / ``fleet_gateway``),
    bypassing role lookup. Otherwise the box's role (deployment.json or
    ``role=``) maps to a seed via ``_ROLE_TO_MINI_SEED``. Never guesses: a
    missing role / unmapped role / unresolved home all raise.
    """
    from utils.watchdog_probes import (
        _ROLE_TO_MINI_SEED, _resolve_mini_home, _read_deployment_declaration,
    )
    from utils.watchdog_probes_mini import _MINI_RULES_NAME

    if seed_name is None:
        if role is None:
            try:
                from utils.rns_tree_perms import _read_rnsd_user
                service_user = _read_rnsd_user()
            except Exception:
                service_user = None
            role, _overrides = _read_deployment_declaration(service_user)
        if not role:
            raise PromoteError(
                "no declared role for this box (deployment.json) — pass "
                "--role <" + "|".join(sorted(_ROLE_TO_MINI_SEED)) + "> "
                "or --seed <" + "|".join(sorted(set(_ROLE_TO_MINI_SEED.values())))
                + ">")
        seed_name = _ROLE_TO_MINI_SEED.get(role)
        if not seed_name:
            known = ", ".join(sorted(set(_ROLE_TO_MINI_SEED.values())))
            raise PromoteError(
                f"role '{role}' has no mini-seed mapping (seeds: {known}); "
                f"pass --seed to target one explicitly")
    else:
        role = role or f"(seed:{seed_name})"

    seed_path = os.path.join(
        meshforge_root, "configs", f"mini_dudeai_rules.{seed_name}.json")
    home = mini_home or _resolve_mini_home()
    if not home:
        raise PromoteError("could not resolve the mini home (live-rules dir)")
    live_path = os.path.join(home, _MINI_RULES_NAME)
    return role, seed_name, seed_path, live_path


def _read_doc(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise PromoteError(f"{what} unreadable ({path}): {exc}")


def plan(meshforge_root=DEFAULT_ROOT, mini_home=None, role=None, seed_name=None):
    """Resolve + merge IN MEMORY (no write). Returns a plan dict; raises
    PromoteError on any unresolved/unreadable input."""
    from mini_dudeai.candidate import merge_seed_rules

    role, seed_name, seed_path, live_path = resolve_target(
        meshforge_root, mini_home, role, seed_name)
    live_doc = _read_doc(live_path, "live rules")
    seed_doc = _read_doc(seed_path, "role seed")
    live_rules = live_doc.get("rules") or []
    seed_rules = seed_doc.get("rules") or []
    merged, report = merge_seed_rules(live_rules, seed_rules, seed_name)
    # A WRITE only matters when the rule set actually moves. tuned/local/unchanged
    # leave the file byte-identical, so they don't count as a change.
    changed = bool(report.get("added") or report.get("refreshed")
                   or report.get("stamped"))
    return {
        "role": role, "seed_name": seed_name,
        "seed_path": seed_path, "live_path": live_path,
        "live_doc": live_doc, "merged": merged, "report": report,
        "changed": changed, "before": len(live_rules), "after": len(merged),
    }


def apply(p):
    """Back up the live rules (``<live>.bak``) then atomic-write the merge.
    Returns the backup path. Idempotent re-apply of an in-sync plan is a no-op
    write (same content)."""
    from mini_dudeai._util import atomic_write_json
    bak = p["live_path"] + ".bak"
    shutil.copyfile(p["live_path"], bak)
    doc = dict(p["live_doc"])
    doc["rules"] = p["merged"]
    atomic_write_json(p["live_path"], doc)
    return bak


_ID_BUCKETS = ("added", "refreshed", "stamped")  # the buckets that change the file
_COUNT_BUCKETS = ("tuned", "local", "unchanged")  # preserved-as-is


def _fmt_report(report):
    lines = []
    for k in _ID_BUCKETS:
        ids = report.get(k) or []
        if ids:
            lines.append(f"  {k:<10} {len(ids):>3}  {', '.join(ids)}")
    for k in _COUNT_BUCKETS:
        ids = report.get(k) or []
        if ids:
            lines.append(f"  {k:<10} {len(ids):>3}  (kept)")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Promote this box's role seed into its live mini-dudeai "
                    "rules (the merge_seed_rules CLI). Default: dry-run.")
    ap.add_argument("--apply", action="store_true",
                    help="merge + atomic-write (default: dry-run)")
    ap.add_argument("--role", help="override role (else from deployment.json)")
    ap.add_argument("--seed", help="target a seed directly (federator|fleet_gateway), "
                                   "bypassing role lookup")
    ap.add_argument("--mini-home", help="override the live-rules dir")
    ap.add_argument("--meshforge-root", default=DEFAULT_ROOT)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    try:
        p = plan(args.meshforge_root, args.mini_home, args.role, args.seed)
    except PromoteError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    applied = bool(args.apply and p["changed"])
    bak = apply(p) if applied else None

    if args.json:
        print(json.dumps({
            "ok": True, "role": p["role"], "seed": p["seed_name"],
            "changed": p["changed"], "applied": applied,
            "before": p["before"], "after": p["after"],
            "report": {k: v for k, v in p["report"].items() if v},
            "backup": bak,
        }))
        return 0

    print(f"role={p['role']}  seed={p['seed_name']}")
    print(f"live={p['live_path']}")
    if not p["changed"]:
        print(f"already in sync ({p['after']} rules) — nothing to promote.")
        return 0
    verb = "PROMOTED" if applied else "WOULD promote"
    print(f"{verb} ({p['before']} -> {p['after']} rules):")
    print(_fmt_report(p["report"]))
    if applied:
        print(f"backup: {bak}")
    else:
        print("\nRe-run with --apply to write (backs up to <live>.bak first).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
