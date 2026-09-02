#!/usr/bin/env python3
"""fleet_role_stamp.py — derive each fleet box's declared role into the naming
registry, so a poller can tell "absent by design" from "broken".

WHY THIS EXISTS (2026-09-02)
----------------------------
The map's federation collector polled every box in fleet.json on :5000. A
`gateway-only` box (moc3) and a `field-node` box (lehua) have meshforge-map
`disabled`/`absent` in docs/fleet_roles.yaml BY DESIGN, so :5000 refuses
forever. That manufactured a permanent `federation_peer_unhealthy`, which every
box then suppressed with a hand-written known-normal rule whose stated
retirement trigger ("when moc3 rejoins federation") could never fire. A
known_benign factory: each new map-less box inherits a false alarm someone must
hand-silence, and the silencer is indistinguishable from silencing a real fault.

fleet_truth.py:117 already states the rule this restores: *set it ONLY from the
box's own declared role — never inferred from silence.*

SSOT AND WHY THIS IS A CACHE, NOT A SECOND DECLARATION
------------------------------------------------------
The role is declared in exactly ONE place: each box's own
~/.config/meshforge/deployment.json. This script asks each box what its role is
(the same `provision_role.py --print-role` call the manager's --fleet-check
already makes), resolves it against the repo-tracked role catalog, and writes
the ANSWER into the manager's fleet_naming.json — which
scripts/fleet_registry_sync.sh already fans out to every box daily, and which
_init_federation already loads.

So there is no new artifact, no new distribution path, and no second place to
author the fact. Re-running re-derives; it cannot drift from deployment.json
except by being stale, and `role_derived_at` says how stale
(honest_failure_modes #5 — two consumers of one artifact share ONE constant).

HONEST FAILURE MODES
  * serves_map is TRI-STATE. `absent` from the registry means UNKNOWN, and
    UNKNOWN must never collapse to False: only an explicit False stops a peer
    being polled, so an unknown box keeps being watched. Trading a noisy false
    alarm for a silent real one is the wrong direction (#2).
  * An UNREACHABLE box HOLDS its previous stamp and the run reports CONCERN —
    unobservable != "has no role" (#2). Wiping to None would silently re-enable
    polling; holding a stale-but-dated claim is the honest degradation.
  * A role NOT in the catalog leaves serves_map None and FAILs loudly, named.
    A closed vocabulary needs a closed consumer (#7).
  * An unreadable catalog changes NOTHING and FAILs. A validator that absorbs
    its own read error would stamp the whole fleet UNKNOWN in one run (#1).
  * Every leg prints its per-box outcome; --check never writes (#9).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from utils.fleet_naming import registry_path  # noqa: E402
from utils.fleet_hosts import (  # noqa: E402
    resolve_fleet_hosts, resolve_fleet_hosts_file)

# Operator-configurable, no host/key hardcoded here (MF014) — same convention
# as provision_role.py.
SSH_CMD = os.environ.get("MESHFORGE_SSH", "ssh")
SSH_TIMEOUT_S = 20

#: The service whose presence decides whether a box can answer /api/status.
MAP_SERVICE = "meshforge-map"

#: Sentinel for "the box answered and declares no MeshForge role" — a fact,
#: distinct from every transport failure. serves_map stays UNKNOWN (so the peer
#: keeps being polled), but the run is not degraded by it.
NO_ROLE_DECLARED = "declares no MeshForge role"

#: The catalog's service-state vocabulary. `enabled` is the ONLY value that
#: means "this box serves a map"; the rest mean it cannot. A value outside this
#: set is an authoring error, not a third meaning (#7).
STATE_SERVES = {"enabled": True, "disabled": False, "absent": False}


def _print_role_remote(host: str) -> tuple[str | None, str | None]:
    """(role, error). Asks the box what role it declares for ITSELF."""
    cmd = [SSH_CMD, "-o", "BatchMode=yes",
           "-o", f"ConnectTimeout={SSH_TIMEOUT_S}", host,
           "python3 /opt/meshforge/scripts/provision_role.py --print-role"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=SSH_TIMEOUT_S + 15)
    except subprocess.TimeoutExpired:
        return None, "ssh timeout"
    except OSError as e:
        return None, f"ssh failed: {e}"
    if r.returncode != 0:
        err = (r.stderr or "").strip().splitlines()
        return None, f"rc={r.returncode} {err[-1] if err else ''}".strip()
    role = (r.stdout or "").strip()
    # An EMPTY role from a box that ANSWERED is knowledge, not blindness: this
    # host declares no MeshForge role (the MeshAnchor box is the standing
    # example). The caller must not fold that into the unreachable bucket —
    # `inert` and `indeterminate` are different claims, and a permanent
    # CONCERN nobody can clear is how a real one stops being read.
    return (role or None), (None if role else NO_ROLE_DECLARED)


def _serves_map_for_role(catalog: dict, role: str) -> tuple[bool | None, str]:
    """(serves_map, note) from the role catalog. None = unknown, never False."""
    try:
        import provision_role as pr
        eff = pr.resolve_role(catalog, role)
    except KeyError:
        return None, f"role {role!r} not in catalog"
    except Exception as e:  # noqa: BLE001 - catalog shape is external input
        return None, f"role {role!r} unresolvable: {e}"
    svcs = (eff.get("services") or {})
    if MAP_SERVICE not in svcs:
        # The role says nothing about the map. That is UNKNOWN, not False —
        # a role that simply forgot to declare it must keep being polled.
        return None, f"role {role!r} does not declare {MAP_SERVICE}"
    state = str(svcs[MAP_SERVICE]).strip()
    if state not in STATE_SERVES:
        return None, (f"role {role!r} declares {MAP_SERVICE}={state!r}, "
                      f"outside the known vocabulary {sorted(STATE_SERVES)}")
    return STATE_SERVES[state], f"{MAP_SERVICE}={state}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true",
                    help="write the derived stamps into the registry")
    ap.add_argument("--check", action="store_true",
                    help="report what would change; never writes (default)")
    ap.add_argument("--registry", default=None, help="registry path override")
    args = ap.parse_args(argv)
    if not args.apply:
        args.check = True

    reg_path = Path(args.registry) if args.registry else registry_path()
    if not reg_path.exists():
        print(f"FAIL: no registry at {reg_path} — seeding a first copy is a "
              f"human decision, not this script's")
        return 1
    try:
        doc = json.loads(reg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"FAIL: registry unreadable ({e}) — changing nothing")
        return 1
    if not isinstance(doc.get("hosts"), dict):
        print("FAIL: registry has no 'hosts' object — changing nothing")
        return 1

    try:
        import provision_role as pr
        catalog = pr.load_roles(pr.DEFAULT_ROLES_FILE)
    except Exception as e:  # noqa: BLE001 - yaml/IO/shape, all fatal here
        print(f"FAIL: role catalog unreadable ({e}) — changing nothing. "
              f"Absorbing this would stamp the whole fleet UNKNOWN in one run.")
        return 1

    # resolve_fleet_hosts() returns [] for BOTH "no list" and "empty list";
    # this organ must refuse a silent no-op, so ask the file resolver which
    # it is (the fleet_pull posture).
    hosts_file = resolve_fleet_hosts_file()
    hosts = resolve_fleet_hosts()
    if hosts_file is None:
        print("FAIL: no fleet_hosts file resolved — this is a MANAGER-side "
              "organ; running it elsewhere is miswiring")
        return 1
    if not hosts:
        print(f"FAIL: {hosts_file} lists no hosts — refusing a silent no-op")
        return 1

    now = time.time()
    changes: list[str] = []
    held: list[str] = []
    noroles: list[str] = []
    failures: list[str] = []
    for host in hosts:
        entry = doc["hosts"].get(host)
        if entry is None or not isinstance(entry, dict):
            # membership-only or absent: nothing to stamp onto, and inventing
            # an entry here would author fleet membership as a side effect.
            failures.append(f"{host}: no registry entry to stamp")
            continue
        role, err = _print_role_remote(host)
        if role is None and err == NO_ROLE_DECLARED:
            noroles.append(f"{host}: {err} — left UNKNOWN, still polled")
            continue
        if role is None:
            prev_role = entry.get("role")
            prev_sm = entry.get("serves_map")
            held.append(f"{host}: {err}; holding role={prev_role!r} "
                        f"serves_map={prev_sm!r}")
            continue
        sm, note = _serves_map_for_role(catalog, role)
        if sm is None:
            failures.append(f"{host}: {note}")
        before = (entry.get("role"), entry.get("serves_map"))
        after = (role, sm)
        if before != after:
            changes.append(f"{host}: role {before[0]!r}->{role!r}, "
                           f"serves_map {before[1]!r}->{sm!r} ({note})")
        if args.apply:
            entry["role"] = role
            entry["role_derived_at"] = now
            if sm is None:
                # UNKNOWN is expressed by ABSENCE, never by a literal null that
                # a reader might coerce. Drop a stale True/False rather than
                # keep asserting something the catalog no longer supports.
                entry.pop("serves_map", None)
            else:
                entry["serves_map"] = sm

    for line in changes:
        print(f"  CHANGE  {line}")
    for line in held:
        print(f"  HOLD    {line}")
    for line in noroles:
        print(f"  INERT   {line}")
    for line in failures:
        print(f"  PROBLEM {line}")

    if args.apply and changes:
        from mini_dudeai._util import atomic_write_json
        bak = reg_path.with_suffix(reg_path.suffix + f".bak-rolestamp-{int(now)}")
        try:
            bak.write_text(reg_path.read_text(encoding="utf-8"),
                           encoding="utf-8")
        except OSError as e:
            print(f"FAIL: could not back up the registry ({e}) — not writing")
            return 1
        atomic_write_json(str(reg_path), doc)
        print(f"  wrote {reg_path} ({len(changes)} change(s)); backup {bak.name}")

    verdict_n = (f"{len(changes)} change(s), {len(held)} held, "
                 f"{len(noroles)} inert, {len(failures)} problem(s)")
    if failures:
        print(f"FAIL: {verdict_n}")
        return 1
    if held:
        # A heal/hold reports CONCERN, never OK — same doctrine as
        # fleet_registry_sync.sh and fleet_hosts_selfheal.sh: a repair that
        # reports OK destroys the signal.
        print(f"CONCERN: {verdict_n} — unobservable is not 'has no role'")
        return 0
    if changes and args.check:
        print(f"CONCERN: {verdict_n} — run with --apply to stamp")
        return 0
    print(f"OK: {verdict_n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
