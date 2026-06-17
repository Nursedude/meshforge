#!/usr/bin/env python3
"""meshtastic install-layout audit — enumerate every place meshtastic is
installed and flag FRAGMENTATION against the reviewed fleet floor.

The recurring update/install/env failure class (feedback_version_env_rigor):
the SAME pip package resolves from different site-packages for different
consumers — the venv the services run, the operator's ``~/.local`` user-site,
a system-wide ``/usr/local/lib/.../dist-packages`` that ``sudo python3`` / the
TUI-as-root reads, and root+user pipx CLI venvs. When those DIVERGE — above
all when a stray sits BELOW ``requirements/core.txt``'s reviewed floor — the
TUI shows a phantom "update available" though the service consumer is fine, and
a future root import path can silently pick up the stale copy (the 2026-06-17
phantom-update; the watchdog companion is the ``dep_install_fragmented`` probe).

This is the install-layout SSOT audit, mirroring ``scripts/db_audit.py``: it
names every install, its owner, the consumer-of-record (venv else user-site),
and the reconcile commands. It shares the location glob-set with the probe
(``_DEP_INSTALL_SITE_GLOBS``) so the two can't drift about WHERE to look.

Read-only by default. ``--fix`` PRINTS the reconcile commands; it does NOT
execute them — installs on this externally-managed fleet are canary-first,
helper-routed and operator-run (never a bare ``pip install``).

Exit 1 if FRAGMENTED (≥2 versions with a below-floor stray) or the
consumer-of-record is BELOW the floor (a real defect); 0 if OK / single clean
install. Suitable for cron / pre-merge.

Usage:
    python3 scripts/meshtastic_install_audit.py
    python3 scripts/meshtastic_install_audit.py --json
    python3 scripts/meshtastic_install_audit.py --fix      # print reconcile cmds
    python3 scripts/meshtastic_install_audit.py --user wh6gxz
"""

import argparse
import glob
import json
import os
import pwd
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# Add src/ to path so we can import the shared glob-set + readers + floor SSOT.
_SCRIPT_DIR = Path(__file__).resolve().parent
_SRC_DIR = _SCRIPT_DIR.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from utils.requirements_floor import read_floor, version_below  # noqa: E402
from utils.watchdog_probes_drift import (  # noqa: E402
    _DEP_INSTALL_SITE_GLOBS,
    _read_pkg_version_at_dirs,
)

_PKG = "meshtastic"


@dataclass
class InstallRecord:
    label: str                  # venv | system-dist | root-pipx | user-site | user-pipx
    path: str
    version: str
    owner: str = ""
    below_floor: bool = False


@dataclass
class AuditReport:
    pkg: str
    floor: Optional[str]
    records: List[InstallRecord] = field(default_factory=list)
    distinct_versions: List[str] = field(default_factory=list)
    consumer: Optional[str] = None      # label of the consumer-of-record
    consumer_version: Optional[str] = None
    verdict: str = "OK"                 # OK | FRAGMENTED | STALE_CONSUMER | NO_INSTALL | NO_FLOOR
    issues: List[str] = field(default_factory=list)


def _owner_of(path: str) -> str:
    try:
        st = os.stat(path)
    except OSError:
        return "?"
    try:
        user = pwd.getpwuid(st.st_uid).pw_name
    except KeyError:
        user = str(st.st_uid)
    return user


def enumerate_installs(
    pkg: str = _PKG,
    *,
    meshforge_root: Optional[str] = None,
    user: Optional[str] = None,
    user_home: Optional[str] = None,
) -> List[InstallRecord]:
    """Find every root-readable install of ``pkg`` (one record per concrete
    site-packages dir, so mixed-python-minor copies surface separately).

    Uses the SAME glob-set the ``dep_install_fragmented`` probe enumerates
    (``_DEP_INSTALL_SITE_GLOBS``) so audit and probe never disagree about where
    meshtastic can live."""
    if meshforge_root is None:
        meshforge_root = str(_SCRIPT_DIR.parent)
    if user_home is None and user and user != "root":
        try:
            user_home = pwd.getpwnam(user).pw_dir
        except (KeyError, OSError):
            user_home = f"/home/{user}"

    records: List[InstallRecord] = []
    for label, patterns in _DEP_INSTALL_SITE_GLOBS.items():
        if ("{home}" in "".join(patterns)) and not user_home:
            continue  # user-scoped location but no resolvable home — skip
        seen_dirs = set()
        for pat in patterns:
            try:
                expanded = pat.format(root=meshforge_root, home=user_home or "", pkg=pkg)
            except (KeyError, IndexError):
                continue
            for d in sorted(glob.glob(expanded)):
                if d in seen_dirs:
                    continue
                seen_dirs.add(d)
                ver = _read_pkg_version_at_dirs([d], pkg)
                if ver is not None:
                    records.append(
                        InstallRecord(label=label, path=d, version=ver, owner=_owner_of(d))
                    )
    return records


def classify(records: List[InstallRecord], floor: Optional[str]) -> AuditReport:
    """Pure verdict logic — no filesystem. Marks below-floor records, finds the
    consumer-of-record (venv else user-site), and decides the verdict:

    * NO_FLOOR      — floor unreadable (indeterminate; never "OK").
    * NO_INSTALL    — meshtastic not found anywhere.
    * STALE_CONSUMER— the consumer-of-record is below the floor (services stale).
    * FRAGMENTED    — ≥2 distinct versions with at least one below-floor stray.
    * OK            — single install, or all installs agree and are >= floor,
                      or divergence where everything is >= floor (a pipx CLI
                      legitimately ahead of the venv lib is intended, not a bug).
    """
    report = AuditReport(pkg=_PKG, floor=floor, records=list(records))
    if not floor:
        report.verdict = "NO_FLOOR"
        report.issues.append("requirements/core.txt floor unreadable — cannot judge")
        return report
    if not records:
        report.verdict = "NO_INSTALL"
        report.issues.append(f"{_PKG} not found in any known install location")
        return report

    for r in records:
        r.below_floor = version_below(r.version, floor)

    report.distinct_versions = sorted({r.version for r in records})
    by_label: Dict[str, str] = {}
    for r in records:
        by_label.setdefault(r.label, r.version)
    consumer = "venv" if "venv" in by_label else ("user-site" if "user-site" in by_label else None)
    report.consumer = consumer
    report.consumer_version = by_label.get(consumer) if consumer else None

    below = [r for r in records if r.below_floor]
    consumer_below = bool(
        consumer and report.consumer_version
        and version_below(report.consumer_version, floor)
    )

    if consumer_below:
        report.verdict = "STALE_CONSUMER"
        report.issues.append(
            f"consumer-of-record {consumer}={report.consumer_version} is below floor >={floor} "
            f"— the services run a stale meshtastic"
        )
    if len(report.distinct_versions) >= 2 and below:
        # FRAGMENTED is the headline if the consumer is fine; if the consumer is
        # ALSO below floor we keep STALE_CONSUMER as the (stronger) verdict.
        if report.verdict == "OK":
            report.verdict = "FRAGMENTED"
        report.issues.append(
            f"{len(report.distinct_versions)} distinct versions across "
            f"{len(records)} installs; below floor >={floor}: "
            + ", ".join(f"{r.label}@{r.path}={r.version}" for r in below)
        )
    return report


def _reconcile_commands(report: AuditReport) -> List[str]:
    """Location-appropriate reconcile commands (printed, never executed)."""
    floor = report.floor or "<floor>"
    cmds: List[str] = []
    targets = [r for r in report.records if r.below_floor] or [
        r for r in report.records if r.version not in (report.floor,)
    ]
    for r in {(t.label, t.owner): t for t in targets}.values():
        if r.label == "venv":
            venv_bin = str(Path(r.path).parents[3] / "bin" / "pip") if len(Path(r.path).parents) >= 4 else "<venv>/bin/pip"
            cmds.append(f"# venv (service consumer): {r.path}")
            cmds.append(f"{venv_bin} install --upgrade 'meshtastic>={floor}'")
        elif r.label == "system-dist":
            cmds.append(f"# system-wide (TUI-as-root reads this): {r.path}")
            cmds.append(
                f"sudo /usr/bin/python3 -m pip install --break-system-packages "
                f"--upgrade 'meshtastic>={floor}'"
            )
        elif r.label in ("root-pipx", "user-pipx"):
            who = "sudo " if r.label == "root-pipx" else f"sudo -u {r.owner} -H " if r.owner not in ("?", "") else ""
            cmds.append(f"# pipx CLI ({r.label}, owner {r.owner}): {r.path}")
            cmds.append(f"{who}pipx upgrade meshtastic")
        elif r.label == "user-site":
            cmds.append(f"# user-site (owner {r.owner}): {r.path}")
            cmds.append(
                f"sudo -u {r.owner} -H python3 -m pip install --user "
                f"--break-system-packages --upgrade 'meshtastic>={floor}'"
            )
    cmds.append("# then re-run this audit to confirm OK.")
    return cmds


def render_table(report: AuditReport, show_fix: bool) -> str:
    lines: List[str] = []
    lines.append(f"meshtastic install audit — fleet floor >= {report.floor or '?'}")
    header = f"{'LOCATION':<12} {'VERSION':<10} {'OWNER':<12} {'FLAG':<8} PATH"
    lines.append(header)
    lines.append("-" * max(len(header), 60))
    if not report.records:
        lines.append("(no meshtastic install found in any known location)")
    for r in report.records:
        flag = "<FLOOR" if r.below_floor else ""
        consumer_mark = "  <- consumer-of-record" if r.label == report.consumer else ""
        lines.append(
            f"{r.label:<12} {r.version:<10} {r.owner:<12} {flag:<8} {r.path}{consumer_mark}"
        )
    lines.append("")
    lines.append(f"VERDICT: {report.verdict}")
    for issue in report.issues:
        lines.append(f"  - {issue}")
    if report.verdict in ("FRAGMENTED", "STALE_CONSUMER"):
        lines.append("")
        lines.append("Reconcile (review, then run manually — never auto-executed):")
        for c in _reconcile_commands(report):
            lines.append(f"  {c}")
    elif show_fix:
        lines.append("")
        lines.append("(no defect — nothing to reconcile)")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--user", default=None,
                        help="operator user whose ~/.local + pipx to audit "
                             "(default: the real/sudo user)")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON instead of a table")
    parser.add_argument("--fix", action="store_true",
                        help="print reconcile commands (does NOT execute them)")
    args = parser.parse_args()

    user = args.user
    if user is None:
        try:
            from utils.paths import get_real_username
            user = get_real_username()
        except Exception:
            user = os.environ.get("SUDO_USER") or os.environ.get("USER")

    floor = read_floor(_PKG)
    records = enumerate_installs(_PKG, user=user)
    report = classify(records, floor)

    if args.json:
        print(json.dumps(asdict(report), indent=2))
    else:
        print(render_table(report, show_fix=args.fix))

    # Exit 1 on a real defect (fragmented / stale consumer); 0 otherwise.
    return 1 if report.verdict in ("FRAGMENTED", "STALE_CONSUMER") else 0


if __name__ == "__main__":
    sys.exit(main())
