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

Role→seed resolution mirrors the probe: the same ``_ROLE_TO_MINI_SEED`` map, the
same seed path (``mini_dudeai.candidate.seed_rules_path`` — the canonical layout
owner the probe is test-pinned to), and the same ``_resolve_mini_home`` — so the
CLI and the probe resolve a role to the same seed and the same files.

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
import stat
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
    from mini_dudeai.candidate import seed_rules_path

    known_seeds = sorted(set(_ROLE_TO_MINI_SEED.values()))
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
                "or --seed <" + "|".join(known_seeds) + ">")
        seed_name = _ROLE_TO_MINI_SEED.get(role)
        if not seed_name:
            raise PromoteError(
                f"role '{role}' has no mini-seed mapping "
                f"(seeds: {', '.join(known_seeds)}); "
                f"pass --seed to target one explicitly")
    elif seed_name not in known_seeds:
        # Reject an unknown/garbage --seed loudly (it would otherwise build an
        # arbitrary file path) — honest_failure_modes #3.
        raise PromoteError(
            f"unknown seed '{seed_name}' (known: {', '.join(known_seeds)})")

    # role may legitimately be None here when --seed targets a seed directly and
    # the box declares no role — kept honest (None), not papered over with a
    # synthetic label that would pollute the --json `role` field.
    seed_path = seed_rules_path(meshforge_root, seed_name)
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


def _doc_rules(doc, what):
    """Extract+validate the ``rules`` list from a loaded document.

    Rejects what the author cannot have meant — a non-object document or a
    non-list ``rules`` value — with a clear PromoteError, rather than letting a
    list crash on ``.get`` (uncaught AttributeError) or a dict get iterated as
    rules into a silently-corrupt merge (honest_failure_modes #3)."""
    if not isinstance(doc, dict):
        raise PromoteError(
            f"{what} is not a JSON object (got {type(doc).__name__})")
    rules = doc.get("rules")
    if rules is None:
        return []
    if not isinstance(rules, list):
        raise PromoteError(
            f"{what} has a non-list 'rules' field (got {type(rules).__name__})")
    return rules


def plan(meshforge_root=DEFAULT_ROOT, mini_home=None, role=None, seed_name=None):
    """Resolve + merge IN MEMORY (no write). Returns a plan dict; raises
    PromoteError on any unresolved/unreadable/malformed input."""
    from mini_dudeai.candidate import merge_seed_rules, SEED_MERGE_CHANGE_BUCKETS

    role, seed_name, seed_path, live_path = resolve_target(
        meshforge_root, mini_home, role, seed_name)
    # Snapshot the live file's mtime BEFORE reading it: apply() refuses if it
    # moved (a concurrent daemon promotion) so a stale merge never silently
    # clobbers the daemon's write. Stat-before-read is fail-closed — a write
    # racing our read trips the guard rather than slipping through.
    try:
        live_mtime_ns = os.stat(live_path).st_mtime_ns
    except OSError:
        live_mtime_ns = None  # absent → _read_doc raises the clean error next
    live_doc = _read_doc(live_path, "live rules")
    seed_doc = _read_doc(seed_path, "role seed")
    live_rules = _doc_rules(live_doc, "live rules")
    seed_rules = _doc_rules(seed_doc, "role seed")
    merged, report = merge_seed_rules(live_rules, seed_rules, seed_name)
    # A WRITE only matters when the rule set actually moves. tuned/local/unchanged
    # leave the file byte-identical, so they don't count as a change.
    changed = any(report.get(k) for k in SEED_MERGE_CHANGE_BUCKETS)
    return {
        "role": role, "seed_name": seed_name,
        "seed_path": seed_path, "live_path": live_path,
        "live_doc": live_doc, "merged": merged, "report": report,
        "changed": changed, "before": len(live_rules), "after": len(merged),
        "live_mtime_ns": live_mtime_ns,
    }


def _restamp_owner_mode(path, uid, gid, mode):
    """Best-effort restore of owner+mode after an atomic write.

    atomic_write_json/bytes go through mkstemp + os.replace, which hand the file
    the INVOKER's ownership and mode 0600. Run under sudo that would leave
    ~operator/mini_dudeai_rules.json root:root 0600 — and the operator's mini
    --user daemon could no longer read its own rules. So we re-stamp to the
    mini-home owner; chown before chmod (chown can clear setgid).

    NON-FATAL by design: the write has ALREADY succeeded and is published, so a
    restamp failure must NOT make apply() report the whole promote as failed —
    that was a half-state bug (file written, but exit 2 / "ERROR"). It also
    cannot strand the daemon: under root the chown always succeeds, and run as
    the operator the file is already operator-owned (only the group could
    differ, which owner-read ignores). On a denied full (uid,gid) chown — e.g. a
    home dir whose group the operator isn't a member of — we still fix the uid
    alone (the strand-critical part) and emit a loud WARNING (the witness,
    honest_failure_modes #9) rather than raising."""
    try:
        os.chown(path, uid, gid)
    except OSError:
        try:
            os.chown(path, uid, -1)  # uid is what the daemon's owner-read needs
        except OSError as exc:
            print(f"WARN: wrote {path} but could not set owner uid={uid}: {exc}"
                  f" — if the mini daemon can't read it, fix ownership manually",
                  file=sys.stderr)
    try:
        os.chmod(path, mode)
    except OSError as exc:
        print(f"WARN: wrote {path} but could not set mode {oct(mode)}: {exc}",
              file=sys.stderr)


def apply(p):
    """Atomic-write the merge into the live rules, preserving the file's owner
    and mode, after a CLI-private backup. Returns the backup path.

    Concurrency-guarded: if the live file changed since plan() snapshotted it
    (a concurrent daemon candidate-promotion), refuse rather than overwrite the
    daemon's write with our stale merge. The backup uses a CLI-private name
    (``<live>.promote.bak``) so it never clobbers the daemon's single-slot
    recovery backup (``<live>.bak``, written by Engine._backup_canonical)."""
    from mini_dudeai._util import atomic_write_bytes, atomic_write_json
    live = p["live_path"]

    # Optimistic concurrency: the merge in `p` was built from the file as it was
    # at plan() time. If it moved underneath us, our merge is stale.
    try:
        cur_mtime_ns = os.stat(live).st_mtime_ns
    except OSError as exc:
        raise PromoteError(f"live rules vanished before write ({live}): {exc}")
    if p.get("live_mtime_ns") is not None and cur_mtime_ns != p["live_mtime_ns"]:
        raise PromoteError(
            f"live rules changed since planning ({live}) — a concurrent "
            f"promotion? re-run to merge against the current file")

    # Who SHOULD own the live file: the owner of the mini home (the operator),
    # not the invoker. Captured before the write; re-stamped after.
    try:
        home_st = os.stat(os.path.dirname(live) or ".")
        owner_uid, owner_gid = home_st.st_uid, home_st.st_gid
    except OSError as exc:
        raise PromoteError(f"cannot stat the mini home for ownership ({live}): {exc}")
    try:
        mode = stat.S_IMODE(os.stat(live).st_mode)
    except OSError:
        mode = 0o644

    # CLI-private backup (manual restore) — NOT the daemon's <live>.bak slot.
    bak = live + ".promote.bak"
    try:
        with open(live, "rb") as fh:
            atomic_write_bytes(bak, fh.read())
    except OSError as exc:
        raise PromoteError(f"could not back up live rules ({bak}): {exc}")

    doc = dict(p["live_doc"])
    doc["rules"] = p["merged"]
    try:
        atomic_write_json(live, doc)
    except OSError as exc:
        raise PromoteError(f"could not write merged rules ({live}): {exc}")

    _restamp_owner_mode(live, owner_uid, owner_gid, mode)
    _restamp_owner_mode(bak, owner_uid, owner_gid, mode)
    return bak


def _fmt_report(report):
    # Bucket vocabulary sourced from the merge helper — one place owns the names
    # (honest_failure_modes #5). Lazy import keeps the module's no-src-deps load.
    from mini_dudeai.candidate import (
        SEED_MERGE_CHANGE_BUCKETS, SEED_MERGE_PRESERVE_BUCKETS)
    lines = []
    for k in SEED_MERGE_CHANGE_BUCKETS:
        ids = report.get(k) or []
        if ids:
            lines.append(f"  {k:<10} {len(ids):>3}  {', '.join(ids)}")
    for k in SEED_MERGE_PRESERVE_BUCKETS:
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
        applied = bool(args.apply and p["changed"])
        bak = apply(p) if applied else None
    except PromoteError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    role_disp = p["role"] or f"(by --seed:{p['seed_name']})"
    if args.json:
        print(json.dumps({
            "ok": True, "role": p["role"], "seed": p["seed_name"],
            "changed": p["changed"], "applied": applied,
            "before": p["before"], "after": p["after"],
            "report": {k: v for k, v in p["report"].items() if v},
            "backup": bak,
        }))
        return 0

    print(f"role={role_disp}  seed={p['seed_name']}")
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
        print("\nRe-run with --apply to write (backs up to <live>.promote.bak first).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
