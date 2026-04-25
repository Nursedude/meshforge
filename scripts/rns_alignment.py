#!/usr/bin/env python3
"""rns_alignment.py — audit + normalize RNS config across the fleet.

Usage:
    rns_alignment.py probe              # JSON snapshot of THIS host
    rns_alignment.py audit              # Print local drift table
    rns_alignment.py audit --fleet      # Probe each fleet host (reads ~/.config/meshforge/fleet_hosts)
    rns_alignment.py normalize          # Apply canonical layout to THIS host (sudo)
    rns_alignment.py normalize --dry-run # Show what normalize would do, no changes

Read-only by default. `normalize` requires explicit invocation.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Make src/ importable when run from scripts/
HERE = Path(__file__).resolve().parent
SRC = HERE.parent / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from utils.rns_alignment import (  # noqa: E402
    analyze_drift,
    normalize_local,
    plan_normalize,
    probe_local,
)


def cmd_probe(args) -> int:
    state = probe_local()
    state.drift_reasons = analyze_drift(state)
    print(state.to_json())
    return 0


def cmd_audit(args) -> int:
    if args.fleet:
        return _audit_fleet()
    return _audit_local()


def _audit_local() -> int:
    state = probe_local()
    state.drift_reasons = analyze_drift(state)
    _print_state_human(state)
    return 0 if state.aligned else 1


def _audit_fleet() -> int:
    hosts_file = Path.home() / '.config' / 'meshforge' / 'fleet_hosts'
    if not hosts_file.is_file():
        print(f"ERROR: {hosts_file} not found", file=sys.stderr)
        return 2
    hosts = []
    for raw in hosts_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        # Match fleet_sync.sh format — first whitespace-separated token is host
        hosts.append(line.split()[0])

    repo_root = HERE.parent
    probe_path = repo_root / 'scripts' / 'rns_alignment.py'
    if not probe_path.is_file():
        print(f"ERROR: {probe_path} missing", file=sys.stderr)
        return 2

    print(f"{'HOST':<14} {'RNSD':<7} {'CONFIGDIR':<24} {'NMD':<22} "
          f"{'RPC':<4} {'OWN':<10} STATUS")
    print('-' * 100)
    overall = 0
    for host in hosts:
        out = subprocess.run(
            ['ssh', '-o', 'ConnectTimeout=5', host,
             'cd /opt/meshforge && python3 scripts/rns_alignment.py probe'],
            capture_output=True, text=True, timeout=20,
        )
        if out.returncode != 0:
            print(f"{host:<14} unreachable: {out.stderr.strip()[:80]}")
            overall = 1
            continue
        try:
            d = json.loads(out.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            print(f"{host:<14} parse-error: {out.stdout[-120:]}")
            overall = 1
            continue
        rnsd = 'on' if d.get('rnsd_active') else 'off'
        configdir = d.get('rnsd_configdir') or '?'
        nmd_unit = d.get('nomadnet_unit_rnsconfig') or '(none)'
        etc = d.get('etc_config') or {}
        rpc = 'yes' if etc.get('has_rpc_key') else 'no'
        own = (etc.get('owner') or '?')[:10]
        drift = d.get('drift_reasons') or []
        status = 'ALIGNED' if not drift else f"DRIFT ({len(drift)})"
        if drift:
            overall = 1
        print(f"{host:<14} {rnsd:<7} {str(configdir):<24} {str(nmd_unit):<22} "
              f"{rpc:<4} {own:<10} {status}")
        for reason in drift:
            print(f"    - {reason}")
    return overall


def _print_state_human(state) -> None:
    print(f"Host: {state.hostname}")
    print(f"  rnsd:        {'active' if state.rnsd_active else 'inactive'} "
          f"(User={state.rnsd_user}, configdir={state.rnsd_configdir})")
    if state.etc_config:
        c = state.etc_config
        print(f"  /etc/reticulum/config: "
              f"exists={c.exists} owner={c.owner} "
              f"rpc_key={'yes' if c.has_rpc_key else 'no'} "
              f"instance_name={c.instance_name or '(default)'}")
    if state.user_home_config and state.user_home_config.exists:
        c = state.user_home_config
        print(f"  {c.path}: exists owner={c.owner}")
    if state.root_home_config and state.root_home_config.exists:
        c = state.root_home_config
        print(f"  {c.path}: exists owner={c.owner}")
    if state.mfclient_config and state.mfclient_config.exists:
        c = state.mfclient_config
        print(f"  {c.path}: owner={c.owner} "
              f"rpc_key={'yes' if c.has_rpc_key else 'no'}")
    if state.nomadnet_unit_installed:
        print(f"  NomadNet unit: --rnsconfig {state.nomadnet_unit_rnsconfig}")
    else:
        print(f"  NomadNet unit: not installed")
    print()
    if state.aligned:
        print(f"Status: ALIGNED")
    else:
        print(f"Status: DRIFT ({len(state.drift_reasons)} reason(s))")
        for r in state.drift_reasons:
            print(f"  - {r}")


def cmd_normalize(args) -> int:
    state = probe_local()
    state.drift_reasons = analyze_drift(state)
    if state.aligned:
        print(f"{state.hostname}: already aligned, nothing to do")
        return 0
    plan = plan_normalize(state)
    print(f"Plan ({len(plan)} actions):")
    for a in plan:
        print(f"  - {a.description}")
    if args.dry_run:
        print("\n[DRY-RUN] No changes applied.")
        return 0
    print()
    if not args.yes:
        ans = input("Apply? [y/N] ")
        if ans.strip().lower() != 'y':
            print("Aborted.")
            return 1
    executed = normalize_local(state, dry_run=False)
    print(f"\nApplied {len(executed)} action(s):")
    for d in executed:
        print(f"  ✓ {d}")
    # Re-probe to verify
    state2 = probe_local()
    state2.drift_reasons = analyze_drift(state2)
    if state2.aligned:
        print(f"\nVerified: {state2.hostname} is now ALIGNED")
        return 0
    print(f"\nWARNING: post-normalize drift remains:")
    for r in state2.drift_reasons:
        print(f"  - {r}")
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    sub = parser.add_subparsers(dest='cmd', required=True)

    sub.add_parser('probe', help='Emit JSON snapshot of THIS host')

    p_audit = sub.add_parser('audit', help='Show drift table')
    p_audit.add_argument('--fleet', action='store_true',
                         help='SSH each host in ~/.config/meshforge/fleet_hosts')

    p_norm = sub.add_parser('normalize', help='Apply canonical layout to THIS host')
    p_norm.add_argument('--dry-run', action='store_true', help='Show plan only')
    p_norm.add_argument('--yes', '-y', action='store_true', help='Skip confirmation')

    args = parser.parse_args(argv)
    if args.cmd == 'probe':
        return cmd_probe(args)
    if args.cmd == 'audit':
        return cmd_audit(args)
    if args.cmd == 'normalize':
        return cmd_normalize(args)
    parser.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
