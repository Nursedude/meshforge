#!/usr/bin/env python3
"""fleet_hostkey_stamp.py — seed the registry's ``expect_hostkey`` from the
live fleet, so ``fleet_naming_audit --verify-identity`` can tell whether a
name still reaches the box it names.

WHY THIS EXISTS (2026-09-11)
----------------------------
On 2026-09-11 the operator updated three AREDN hAPs and two fronts SWAPPED
LEASES: ``.249`` stopped fronting moc1 and started fronting trdev+lehua.
``/etc/hosts`` SHADOWS DNS, so until the records were corrected a fleet name
resolved to a DIFFERENT NODE — not unreachable, which is loud, but
confidently wrong, which is silent.

``fleet_naming_drift_check.py`` could not see it. Its two inputs are
``/etc/hosts`` (via getaddrinfo — nss ``files`` precedes ``dns``) and the
registry's ``ip_fallback``; both went stale in the SAME event, so they agreed
with each other and it printed ``OK: 14 hosts resolve+match registry``. A
checker that consumes the artifact it validates is self-confirming (the
2026-07-25 lesson, in a second skin).

The one leg that asks REALITY is ``identity_mismatch`` — ssh-keyscan against
the registry's ``expect_hostkey``, the same technique the operator's session
used by hand to prove who owned ``.249``. It was inert: no alias declared an
``expect_hostkey``, so ``--verify-identity`` returned ``UNDECLARED`` 14 times
and scanned nothing. Reader configured, writer not (honest_failure_modes #4).
This script is the writer.

THE THREE REFUSALS — the reason this is not a for-loop
------------------------------------------------------
1. **Collision.** If two aliases scan to the same key, they are one sshd, and
   stamping would record one box's identity as the other's. Measured that
   day: ``lehua.mf.internal:22`` and ``trdev.mf.internal:22`` return the
   IDENTICAL key, because one front forwards :22 to trdev while lehua's own
   sshd is on :2200. Both are refused; the fix is a correct ``ssh_port``,
   not a stamp.
2. **Empty scan.** A host that does not answer (tunnel-only boxes: kiai,
   alaula) is UNKNOWN, never a value. Leave ``expect_hostkey`` absent so the
   audit reads ``UNDECLARED`` — never fabricate an identity from a failed
   observation (honest_failure_modes #2).
3. **Disagreement with an existing stamp.** If an alias already carries an
   ``expect_hostkey`` and the live scan disagrees, that is EXACTLY the
   finding this whole mechanism exists to surface. Overwriting it would
   launder a real node swap into "stamped OK". Refused, loudly, always —
   re-stamping a changed key is a human decision (``--restamp <alias>``).

Usage:
    fleet_hostkey_stamp.py              # report what would change (default)
    fleet_hostkey_stamp.py --apply      # write the stamps
    fleet_hostkey_stamp.py --apply --restamp lehua   # accept ONE changed key
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.fleet_naming import (  # noqa: E402
    load_registry, registry_path, resolve,
)

KEYSCAN_TIMEOUT_S = 8
# Preference order when a host serves several key types. The audit matches
# against ANY scanned fingerprint, so which one we record is a readability
# choice, not a correctness one — but it must be DETERMINISTIC, or a re-run
# would "change" a key that never moved and trip refusal 3 against itself.
KEY_TYPE_PREFERENCE = ("ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-rsa")


def scan_typed(target: str, port: int = 22, *,
               runner=subprocess.run) -> Tuple[List[Tuple[str, str]], str]:
    """([(keytype, SHA256:fp), ...], "") or ([], why-unobservable).

    Typed, unlike the audit's comparison-side helper: authoring a stamp needs
    to pick one key deterministically, so it must know which type it picked.
    """
    try:
        scan = runner(["ssh-keyscan", "-T", str(KEYSCAN_TIMEOUT_S),
                       "-p", str(port), target],
                      capture_output=True, text=True,
                      timeout=KEYSCAN_TIMEOUT_S + 4)
        if not scan.stdout.strip():
            return [], f"keyscan empty (rc={scan.returncode})"
        fp = runner(["ssh-keygen", "-lf", "-"], input=scan.stdout,
                    capture_output=True, text=True, timeout=8)
        out: List[Tuple[str, str]] = []
        for line in fp.stdout.splitlines():
            m = re.search(r"(SHA256:\S+)", line)
            t = re.search(r"\(([A-Z0-9]+)\)\s*$", line.strip())
            if m:
                ktype = {"ED25519": "ssh-ed25519", "RSA": "ssh-rsa",
                         "ECDSA": "ecdsa-sha2-nistp256"}.get(
                             t.group(1) if t else "", "unknown")
                out.append((ktype, m.group(1)))
        if not out:
            return [], "no SHA256 fingerprint in keygen output"
        return out, ""
    except (subprocess.TimeoutExpired, OSError) as e:
        return [], f"keyscan failed: {type(e).__name__}"


def pick_fingerprint(pairs: List[Tuple[str, str]]) -> Optional[str]:
    """Deterministic choice among a host's key types."""
    for want in KEY_TYPE_PREFERENCE:
        for ktype, fp in pairs:
            if ktype == want:
                return fp
    return sorted(fp for _t, fp in pairs)[0] if pairs else None


def find_collisions(scanned: Dict[str, List[Tuple[str, str]]]
                    ) -> Dict[str, List[str]]:
    """alias -> the OTHER aliases sharing any fingerprint with it.

    Shared fingerprints mean one sshd answering under two names. That is
    legal (a NAT front), but it makes an identity stamp a lie for at least
    one of them, so both are refused until ssh_port separates them.
    """
    by_fp: Dict[str, List[str]] = {}
    for alias, pairs in scanned.items():
        for _t, fp in pairs:
            by_fp.setdefault(fp, []).append(alias)
    clash: Dict[str, List[str]] = {}
    for fp, aliases in by_fp.items():
        if len(aliases) > 1:
            for a in aliases:
                others = [x for x in aliases if x != a]
                clash.setdefault(a, [])
                for o in others:
                    if o not in clash[a]:
                        clash[a].append(o)
    return clash


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true",
                    help="write the derived stamps into the registry")
    ap.add_argument("--registry", default=None, help="registry path override")
    ap.add_argument("--restamp", action="append", default=[], metavar="ALIAS",
                    help="accept a CHANGED host key for this alias (repeatable). "
                         "A changed key is a node swap until a human says "
                         "otherwise — this flag is that human.")
    args = ap.parse_args(argv)

    reg_path = Path(args.registry) if args.registry else registry_path()
    registry, errs = load_registry(str(reg_path))
    if registry is None:
        print(f"FAIL: registry unusable ({'; '.join(errs)}) — changing nothing")
        return 1
    if not registry.hosts:
        print("FAIL: registry lists no hosts — refusing a silent no-op")
        return 1
    try:
        doc = json.loads(reg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"FAIL: registry unreadable ({e}) — changing nothing")
        return 1

    # --- observe every alias first; judge only once all are in hand, because
    # the collision test is a comparison BETWEEN aliases.
    scanned: Dict[str, List[Tuple[str, str]]] = {}
    targets: Dict[str, str] = {}
    unobservable: List[str] = []
    for alias, host in sorted(registry.hosts.items()):
        r = resolve(alias, registry)
        if r.target is None:
            unobservable.append(f"{alias}: unresolved — nothing to scan")
            continue
        port = host.ssh_port or 22
        pairs, why = scan_typed(r.target, port)
        if not pairs:
            unobservable.append(f"{alias}: {r.target}:{port} {why}")
            continue
        scanned[alias] = pairs
        targets[alias] = r.target

    collisions = find_collisions(scanned)

    changes: List[str] = []
    stamps: Dict[str, str] = {}
    problems: List[str] = []
    unchanged = 0
    for alias, pairs in sorted(scanned.items()):
        host = registry.hosts[alias]
        port = host.ssh_port or 22
        if alias in collisions:
            problems.append(
                f"{alias}: shares a host key with {collisions[alias]} — one "
                f"sshd, two names. Scanned :{port}. Set ssh_port so each "
                f"alias reaches its OWN sshd; NOT stamping either.")
            continue
        fp = pick_fingerprint(pairs)
        prior = host.expect_hostkey
        if prior and prior != fp:
            if alias in args.restamp:
                changes.append(f"{alias}: RESTAMP {prior} -> {fp} "
                               f"(operator-authorised)")
                stamps[alias] = fp
            else:
                problems.append(
                    f"{alias}: declared {prior} but :{port} now serves {fp} — "
                    f"this is the node-swap finding, NOT a stamping error. "
                    f"Investigate; re-stamp with --restamp {alias} only once "
                    f"you know why it moved.")
            continue
        if prior == fp:
            unchanged += 1
            continue
        changes.append(f"{alias}: stamp {fp} "
                       f"(from {targets[alias]}:{port})")
        stamps[alias] = fp

    for line in changes:
        print(f"  STAMP     {line}")
    for line in sorted(unobservable):
        print(f"  UNKNOWN   {line} — left UNDECLARED, never invented")
    for line in problems:
        print(f"  PROBLEM   {line}")

    if args.apply and stamps:
        now = int(time.time())
        bak = reg_path.with_suffix(reg_path.suffix + f".bak-keystamp-{now}")
        try:
            bak.write_text(reg_path.read_text(encoding="utf-8"),
                           encoding="utf-8")
        except OSError as e:
            print(f"FAIL: could not back up the registry ({e}) — not writing")
            return 1
        for alias, fp in stamps.items():
            entry = doc["hosts"].get(alias)
            if not isinstance(entry, dict):
                # membership-only stanza: creating one here would author
                # fleet data as a side effect of a stamping run.
                print(f"  PROBLEM   {alias}: no object stanza to stamp onto")
                continue
            entry["expect_hostkey"] = fp
        from mini_dudeai._util import atomic_write_json
        atomic_write_json(str(reg_path), doc)
        print(f"  wrote {reg_path} ({len(stamps)} stamp(s)); backup {bak.name}")

    n = (f"{len(changes)} stamp(s), {unchanged} unchanged, "
         f"{len(unobservable)} unobservable, {len(problems)} problem(s)")
    if problems:
        print(f"FAIL: {n}")
        return 1
    if unobservable:
        # Unobservable is not healthy and not a failure of this script; it is
        # a smaller fleet than we can vouch for. CONCERN keeps it visible.
        print(f"CONCERN: {n} — unobservable is not 'has no identity'")
        return 0
    if changes and not args.apply:
        print(f"CONCERN: {n} — run with --apply to stamp")
        return 0
    print(f"OK: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
