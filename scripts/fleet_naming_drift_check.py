#!/usr/bin/env python3
"""fleet_naming_drift_check.py — cron judge for the fleet-naming/DNS leg.

Runs ``fleet_naming_audit.py --json`` and reduces it to one exit code for the
``cron_verdict.sh`` crontab idiom (0 -> OK, nonzero -> FAIL(n)):

  exit 0  every registry name resolves AND matches its registry ip_fallback,
          and no config finding beyond the operator's waiver file
  exit 1  DRIFT — a name is unresolved (m1 down / client drop-in broken), a
          DNS answer disagrees with the registry (zone import stale — the
          moc4-gap class found 2026-07-17), the registry itself is broken,
          or NEW raw-IP config debt appeared beyond the waivers
  exit 2  the audit could not run/parse — UNKNOWN, never read as healthy

Waivers live OUTSIDE the repo (operator-specific values, MF014) in
``~/.config/meshforge/fleet_naming_waivers.txt``: one substring per line,
``#`` comments allowed. A config finding containing any waiver substring is
accepted; everything else counts as drift. An absent waiver file means no
waivers (strict), never an error.

Crontab wiring (the #78 cron_verdict_stale probe judges wired crons only):

  17 6 * * * /opt/meshforge/scripts/fleet_naming_drift_check.py \
      >> ~/.local/state/meshforge/fleet_naming_drift.log 2>&1; \
      /opt/meshforge/scripts/cron_verdict.sh fleet_naming_drift $?
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_SRC_DIR = _SCRIPT_DIR.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from utils.paths import get_real_user_home  # noqa: E402

AUDIT_PATH = _SCRIPT_DIR / "fleet_naming_audit.py"
WAIVERS_PATH = get_real_user_home() / ".config" / "meshforge" / "fleet_naming_waivers.txt"
AUDIT_TIMEOUT_S = 90


def load_waivers(path: Path) -> list[str]:
    """One substring per line; '#' comments and blanks ignored; absent -> []."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def judge(report: dict, waivers: list[str]) -> tuple[int, str]:
    """Pure reduction of an audit --json report to (exit_code, message)."""
    problems: list[str] = []

    if not report.get("registry_ok", False):
        problems.append(
            "registry broken: " + "; ".join(report.get("registry_errors") or ["?"]))

    hosts = report.get("hosts") or []
    if not hosts:
        # An empty host list can't mean "all healthy" for a fleet registry
        # (honest_failure_modes #3) — treat as drift, loudly.
        problems.append("audit returned ZERO hosts — registry empty or unread")
    for h in hosts:
        for f in h.get("findings") or []:
            problems.append(f"{h.get('alias')}: {f}")

    unwaived = [
        f for f in (report.get("config_findings") or [])
        if not any(w in f for w in waivers)
    ]
    problems.extend(f"config: {f}" for f in unwaived)

    if problems:
        return 1, f"DRIFT ({len(problems)}): " + " | ".join(problems)
    n_waived = len(report.get("config_findings") or []) - len(unwaived)
    return 0, (f"OK: {len(hosts)} hosts resolve+match registry, "
               f"{n_waived} waived config finding(s)")


def main() -> int:
    try:
        proc = subprocess.run(
            [sys.executable, str(AUDIT_PATH), "--json", "--hosts-from-registry"],
            capture_output=True, text=True, timeout=AUDIT_TIMEOUT_S,
        )
        report = json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as e:
        print(f"UNKNOWN: audit did not produce a report ({type(e).__name__}: {e})")
        return 2

    code, msg = judge(report, load_waivers(WAIVERS_PATH))
    print(msg)
    return code


if __name__ == "__main__":
    sys.exit(main())
