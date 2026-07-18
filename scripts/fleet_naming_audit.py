#!/usr/bin/env python3
"""fleet_naming_audit.py — the fleet-naming drift audit (Arc 2 Phase 0).

Read-only. Per fleet host (from ``fleet_hosts``, the membership SSOT), one
RESULT line; then a hardcoded-IP sweep over the operator configs that the
DHCP-reshuffle class actually bites (``~/.ssh/config`` HostName lines,
``map_settings.json`` federation_peers + aredn_node_ips, ``fleet.json``
peers.*.ip). Exit code = actionable finding count (cron_verdict-wireable:
0 = no drift). A baseline run before the namespace exists legitimately
reads all-``ip_fallback``/``bare`` — resolution *method* is reported, but
only ``unresolved``, ``fallback_stale``, ``identity_mismatch``, and
``ip_with_name_available`` count as findings.

Honesty rules (#80): DHCP-reservation coverage is reported UNKNOWN — this
box cannot observe the DHCP server's lease table (a future optional
export hook may close that); an unreachable host under --verify-identity
is identity UNKNOWN, never a mismatch finding.

Generators (``--emit-dnsmasq`` / ``--emit-uci`` / ``--emit-mikrotik``)
render router config for the hardened segment FROM the operator registry
to stdout — generated output carries operator values and is never
committed (MF014); the repo ships only the template shapes in
``templates/openwrt/``.

Usage:
    python3 scripts/fleet_naming_audit.py               # human RESULT lines
    python3 scripts/fleet_naming_audit.py --json        # machine-readable
    python3 scripts/fleet_naming_audit.py --verify-identity   # + ssh-keyscan
    python3 scripts/fleet_naming_audit.py --emit-mikrotik     # RouterOS .rsc
Cron wiring:
    41 6 * * * python3 /opt/meshforge/scripts/fleet_naming_audit.py >/dev/null 2>&1; \
        /opt/meshforge/scripts/cron_verdict.sh fleet_naming $?
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.fleet_dup_collector import fleet_hosts_file, parse_fleet_hosts  # noqa: E402
from utils.fleet_naming import (  # noqa: E402
    Registry, Resolution, default_resolver, load_registry, resolve,
)
from utils.paths import get_real_user_home  # noqa: E402

_IPV4_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
KEYSCAN_TIMEOUT_S = 8


# ── per-host audit ────────────────────────────────────────────────────


def keyscan_fingerprints(target: str, *,
                         runner=subprocess.run
                         ) -> Tuple[List[str], str]:
    """(ALL SHA256 fingerprints, "") or ([], why-unobservable). A host
    serves several key types (rsa/ecdsa/ed25519) and the registry's
    expect_hostkey is ONE of them — comparing only the first scanned
    type would manufacture false MISMATCHes on healthy hosts
    (review-caught). Bounded; a dead host is UNKNOWN, never a mismatch."""
    try:
        scan = runner(["ssh-keyscan", "-T", str(KEYSCAN_TIMEOUT_S), target],
                      capture_output=True, text=True,
                      timeout=KEYSCAN_TIMEOUT_S + 4)
        if not scan.stdout.strip():
            return [], f"keyscan empty (rc={scan.returncode})"
        fp = runner(["ssh-keygen", "-lf", "-"], input=scan.stdout,
                    capture_output=True, text=True, timeout=8)
        fps = [m.group(1) for line in fp.stdout.splitlines()
               for m in [re.search(r"(SHA256:\S+)", line)] if m]
        if not fps:
            return [], "no SHA256 fingerprint in keygen output"
        return fps, ""
    except (subprocess.TimeoutExpired, OSError) as e:
        return [], f"keyscan failed: {type(e).__name__}"


def audit_host(alias: str, registry: Optional[Registry], *,
               resolver=None, verify_identity: bool = False,
               keyscan=keyscan_fingerprints) -> Dict:
    r: Resolution = resolve(alias, registry,
                            resolver=resolver or default_resolver)
    row: Dict = {"alias": alias, "method": r.method, "target": r.target,
                 "address": r.address, "findings": []}
    if r.method == "unresolved":
        row["findings"].append("unresolved")
    host = registry.hosts.get(alias) if registry else None
    if (host and host.ip_fallback and r.method in ("dns", "bare")
            and r.address and r.address != host.ip_fallback):
        # DNS moved on; the registry hint is stale — refresh it before the
        # day the name breaks and the fallback points at the wrong box.
        row["findings"].append(
            f"fallback_stale({host.ip_fallback}->{r.address})")
    if verify_identity:
        if not (host and host.expect_hostkey):
            row["identity"] = "UNDECLARED"
        elif r.target is None:
            row["identity"] = "UNKNOWN(unresolved)"
        else:
            fps, why = keyscan(r.target)
            if not fps:
                row["identity"] = f"UNKNOWN({why})"
            elif host.expect_hostkey in fps:
                row["identity"] = "OK"
            else:
                row["identity"] = f"MISMATCH({','.join(fps)})"
                row["findings"].append("identity_mismatch")
    return row


# ── hardcoded-IP sweep over operator configs ──────────────────────────


def _ip_to_alias(registry: Optional[Registry]) -> Dict[str, str]:
    if not registry:
        return {}
    return {h.ip_fallback: h.alias for h in registry.hosts.values()
            if h.ip_fallback}


def _scan_text(name: str, text: str, ip_alias: Dict[str, str],
               line_filter=None) -> List[str]:
    found: List[str] = []
    for i, line in enumerate(text.splitlines(), 1):
        if line_filter and not line_filter(line):
            continue
        for ip in _IPV4_RE.findall(line):
            alias = ip_alias.get(ip)
            if alias:
                found.append(f"ip_with_name_available:{name}:{i} "
                             f"{ip} -> {alias}")
    return found


def _scan_json_values(name: str, values: List[Tuple[str, str]],
                      ip_alias: Dict[str, str]) -> List[str]:
    """values = (keypath, string-value); findings cite the KEYPATH, not a
    line number — a re-serialized JSON has no honest line numbers to give
    (review-caught: the old re-dump reported everything as line 1)."""
    found: List[str] = []
    for keypath, val in values:
        for ip in _IPV4_RE.findall(str(val)):
            alias = ip_alias.get(ip)
            if alias:
                found.append(f"ip_with_name_available:{name} "
                             f"{keypath} {ip} -> {alias}")
    return found


def scan_operator_configs(registry: Optional[Registry],
                          home: Optional[Path] = None) -> List[str]:
    """Flag IP literals in the known DHCP-bitten NAMING SURFACES where the
    registry offers a name: ~/.ssh/config HostName lines,
    map_settings.json federation_peers/aredn_node_ips, fleet.json
    peers.*.ip — and ONLY those (an IP in an unrelated setting is not
    naming drift). An unparseable JSON config yields an ``unscannable``
    witness, never a raw full-file sweep (a transient torn write must not
    manufacture unrelated-looking findings). Read-only."""
    home = home or get_real_user_home()
    ip_alias = _ip_to_alias(registry)
    if not ip_alias:
        return []
    findings: List[str] = []
    ssh_cfg = home / ".ssh" / "config"
    if ssh_cfg.is_file():
        findings += _scan_text(
            str(ssh_cfg), ssh_cfg.read_text(encoding="utf-8",
                                            errors="replace"),
            ip_alias,
            line_filter=lambda l: l.strip().lower().startswith("hostname"))
    cfg_dir = home / ".config" / "meshforge"

    def _load(p: Path):
        try:
            return json.loads(p.read_text(encoding="utf-8",
                                          errors="replace")), None
        except ValueError as e:
            return None, f"unscannable:{p} (invalid JSON: {e})"

    p = cfg_dir / "map_settings.json"
    if p.is_file():
        doc, err = _load(p)
        if err:
            findings.append(err)
        else:
            vals: List[Tuple[str, str]] = []
            for key in ("federation_peers", "aredn_node_ips"):
                raw = doc.get(key)
                entries = [raw] if isinstance(raw, str) else (raw or [])
                vals += [(key, str(e)) for e in entries]
            findings += _scan_json_values(str(p), vals, ip_alias)
    p = cfg_dir / "fleet.json"
    if p.is_file():
        doc, err = _load(p)
        if err:
            findings.append(err)
        else:
            peers = doc.get("peers")
            vals = []
            if isinstance(peers, dict):
                for pname, pdoc in peers.items():
                    if isinstance(pdoc, dict) and pdoc.get("ip"):
                        vals.append((f"peers.{pname}.ip", str(pdoc["ip"])))
            findings += _scan_json_values(str(p), vals, ip_alias)
    return findings


# ── generators (hardened-segment router config, stdout only) ──────────


def emit_dnsmasq(registry: Registry) -> str:
    lines = [f"# generated by fleet_naming_audit.py --emit-dnsmasq — do NOT commit",
             f"domain={registry.domain or 'SET-DOMAIN'}",
             f"local=/{registry.domain or 'SET-DOMAIN'}/",
             "expand-hosts"]
    for h in registry.hosts.values():
        if h.mac and h.ip_fallback:
            lines.append(f"dhcp-host={h.mac},{h.alias},{h.ip_fallback}")
        elif h.ip_fallback:
            lines.append(f"address=/{h.alias}.{registry.domain}/{h.ip_fallback}")
        else:
            lines.append(f"# {h.alias}: no ip_fallback/mac in registry — "
                         f"dynamic only")
    return "\n".join(lines) + "\n"


def emit_uci(registry: Registry) -> str:
    d = registry.domain or "SET-DOMAIN"
    lines = ["# generated by fleet_naming_audit.py --emit-uci — do NOT commit",
             f"uci set dhcp.@dnsmasq[0].domain='{d}'",
             f"uci set dhcp.@dnsmasq[0].local='/{d}/'",
             "uci set dhcp.@dnsmasq[0].expandhosts='1'"]
    for h in registry.hosts.values():
        if h.mac and h.ip_fallback:
            lines += [f"uci add dhcp host",
                      f"uci set dhcp.@host[-1].name='{h.alias}'",
                      f"uci set dhcp.@host[-1].mac='{h.mac}'",
                      f"uci set dhcp.@host[-1].ip='{h.ip_fallback}'"]
        else:
            # every generator witnesses a skip identically — a host that
            # silently doesn't appear reads as "config complete" (#80)
            lines.append(f"# {h.alias}: no mac+ip_fallback in registry — "
                         f"skipped (dynamic only)")
    lines += ["uci commit dhcp", "/etc/init.d/dnsmasq restart"]
    return "\n".join(lines) + "\n"



# Comment tag on every generated RouterOS dns-static entry: the remove-by-tag
# line makes re-import idempotent (RouterOS happily duplicates same-name
# entries otherwise). Leases are deliberately NOT tag-removed — pre-existing
# manual DHCP reservations must survive a re-import.
MIKROTIK_TAG = "mf-fleet-naming"


def emit_mikrotik(registry: Registry) -> str:
    d = registry.domain or "SET-DOMAIN"
    lines = [
        "# generated by fleet_naming_audit.py --emit-mikrotik — do NOT commit",
        "# idempotent: tagged entries from a previous import are removed first;",
        "# dhcp leases are never auto-removed (manual reservations survive).",
        f'/ip dns static remove [find comment="{MIKROTIK_TAG}"]',
    ]
    for h in registry.hosts.values():
        if h.ip_fallback:
            lines.append(f"/ip dns static add name={h.alias}.{d} "
                         f"address={h.ip_fallback} "
                         f'comment="{MIKROTIK_TAG}"')
            if h.mac:
                lines.append(f"/ip dhcp-server lease add mac-address={h.mac} "
                             f"address={h.ip_fallback}")
        else:
            lines.append(f"# {h.alias}: no ip_fallback in registry — "
                         f"skipped (dynamic only)")
    return "\n".join(lines) + "\n"


# ── main ──────────────────────────────────────────────────────────────


def run_audit(hosts: List[str], registry: Optional[Registry],
              registry_errors: List[str], *, resolver=None,
              verify_identity: bool = False, home: Optional[Path] = None,
              keyscan=keyscan_fingerprints) -> Dict:
    rows = [audit_host(a, registry, resolver=resolver,
                       verify_identity=verify_identity, keyscan=keyscan)
            for a in hosts]
    config_findings = scan_operator_configs(registry, home=home)
    n = sum(len(r["findings"]) for r in rows) + len(config_findings)
    return {
        "registry_ok": registry is not None,
        "registry_errors": registry_errors,
        "reservation_coverage": "UNKNOWN (no DHCP-server export hook wired)",
        "hosts": rows,
        "config_findings": config_findings,
        "finding_count": n,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verify-identity", action="store_true",
                    help="ssh-keyscan each resolved target vs expect_hostkey")
    ap.add_argument("--registry", default=None,
                    help="registry path override")
    ap.add_argument("--hosts-from-registry", action="store_true",
                    help="audit the registry's own aliases instead of the "
                         "fleet_hosts ssh-target list (for per-box drift "
                         "crons — every DNS-client box carries the registry, "
                         "only the manager carries fleet_hosts)")
    ap.add_argument("--emit-dnsmasq", action="store_true")
    ap.add_argument("--emit-uci", action="store_true")
    ap.add_argument("--emit-mikrotik", action="store_true")
    args = ap.parse_args(argv)

    registry, errors = load_registry(args.registry)

    if args.emit_dnsmasq or args.emit_uci or args.emit_mikrotik:
        if registry is None:
            for e in errors:
                print(f"fleet_naming_audit: registry error: {e}",
                      file=sys.stderr)
            return 2
        if args.emit_dnsmasq:
            sys.stdout.write(emit_dnsmasq(registry))
        if args.emit_uci:
            sys.stdout.write(emit_uci(registry))
        if args.emit_mikrotik:
            sys.stdout.write(emit_mikrotik(registry))
        return 0

    if args.hosts_from_registry:
        if registry is None:
            for e in errors:
                print(f"fleet_naming_audit: registry error: {e}",
                      file=sys.stderr)
            return 2
        hosts = sorted(registry.hosts)
    else:
        hosts_path = fleet_hosts_file()
        if hosts_path is None:
            print("fleet_naming_audit: no fleet_hosts file found — nothing to "
                  "audit (could not verify)", file=sys.stderr)
            return 2
        hosts = parse_fleet_hosts(hosts_path.read_text(encoding="utf-8"))

    doc = run_audit(hosts, registry, errors,
                    verify_identity=args.verify_identity)
    if args.json:
        print(json.dumps(doc, indent=2))
    else:
        if not doc["registry_ok"]:
            for e in doc["registry_errors"]:
                print(f"WARN registry: {e} — auditing resolution only")
        print(f"reservation coverage: {doc['reservation_coverage']}")
        for r in doc["hosts"]:
            ident = f" identity={r['identity']}" if "identity" in r else ""
            finds = (" FINDINGS: " + ", ".join(r["findings"])
                     if r["findings"] else "")
            print(f"RESULT {r['alias']}: method={r['method']} "
                  f"target={r['target']} addr={r['address']}{ident}{finds}")
        for f in doc["config_findings"]:
            print(f"FINDING {f}")
        print(f"findings: {doc['finding_count']}")
    return min(doc["finding_count"], 125)


if __name__ == "__main__":
    raise SystemExit(main())
