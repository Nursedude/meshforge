#!/usr/bin/env python3
"""Prove each scanner-based regression guard FIRES on a live violation.

Not part of the test suite — it spawns one pytest subprocess per contract, so
it costs real time and belongs on demand, not on every run.

    python3 scripts/guard_drill.py [repo_root]

Why it exists: on 2026-08-05 every scanner-based guard in
tests/test_regression_guards.py was found inert — SRC_DIR was
`<repo>/tests/../src`, so every scanned path contained `/tests/` and the
guards' own `/tests/` skip discarded every match. They had never worked, from
the file's first commit. All of them still PASSED, which is the point: a guard
that has never failed is not evidence that it works.

Fixing the shared cause was not enough. Re-drilling afterwards found two
contracts still silent for reasons of their own — a numeric `KNOWN_EXCEPTIONS`
budget that had become pure unused slack, and a test that collected violations
and then did nothing with them. Only a planted violation finds those.

Run this after touching the guards, the scanner, or any ALLOWLIST.
"""
import os
import subprocess
import sys

REPO = sys.argv[1] if len(sys.argv) > 1 else "/opt/meshforge"
APP = "meshforge" if REPO.endswith("meshforge") else "meshanchor"
DRILL = os.path.join(REPO, "src", "utils", "_audit_drill_tmp.py")

HEADER = '"""TEMPORARY audit drill file. Removed by the harness."""\n'

CASES = [
    ("TestTCPConnectionContract", HEADER + (
        "from meshtastic.tcp_interface import TCPInterface\n\n\n"
        "def drill():\n"
        "    return TCPInterface(hostname='localhost')\n")),
    ("TestRNSReticulumChokepoint", HEADER + (
        "import RNS\n\n\n"
        "def drill():\n"
        "    return RNS.Reticulum(configdir='/etc/reticulum')\n")),
    ("TestServiceCheckContract", HEADER + (
        "import subprocess\n\n\n"
        "def drill():\n"
        "    return subprocess.run(['systemctl', 'is-active', 'rnsd'],\n"
        "                          capture_output=True, timeout=5)\n")),
    ("TestPathHomeContract", HEADER + (
        "from pathlib import Path\n\n\n"
        "def drill():\n"
        "    cfg = Path.home() / '.config'\n"
        "    return cfg\n")),
    ("TestNoShellTrue", HEADER + (
        "import subprocess\n\n\n"
        "def drill(x):\n"
        "    return subprocess.run(f'echo {x}', shell=True, timeout=5)\n")),
    ("TestPipInvocationContract", HEADER + (
        "import subprocess\n\n\n"
        "def drill():\n"
        "    return subprocess.run(['pip3', 'install', 'meshtastic'], timeout=60)\n")),
    ("TestSqliteConnectContract", HEADER + (
        "import sqlite3\n\n\n"
        "def drill(path):\n"
        "    return sqlite3.connect(path)\n")),
    ("TestBackupNeverOverwrites", HEADER + (
        "from pathlib import Path\n\n\n"
        "def drill(backup_dir: Path, backup_id: str) -> Path:\n"
        "    return backup_dir / f'{backup_id}.json'\n")),
    ("TestStreakSaversAreOne", HEADER + (
        "import json\n\n\n"
        "def _save_drill_streak(state_path, streak):\n"
        "    with open(state_path, 'w') as fh:\n"
        "        json.dump({'streak': int(streak)}, fh)\n")),
]

results = []
for guard, body in CASES:
    try:
        with open(DRILL, "w", encoding="utf-8") as fh:
            fh.write(body)
        proc = subprocess.run(
            [sys.executable, "-m", "pytest",
             f"tests/test_regression_guards.py::{guard}", "-q", "--no-header",
             "-p", "no:cacheprovider"],
            cwd=REPO, capture_output=True, text=True, timeout=300)
        # pytest exits NON-ZERO for a node id that does not exist (4 = usage
        # error, 5 = nothing collected) — the same shape as "the guard fired".
        # Verified 2026-09-07: a bogus class gives rc=4, so a RENAMED OR
        # DELETED guard was being counted in "N/M contracts fired" and the
        # drill exited 0. The drill that exists to prove guards work would
        # bless a guard that does not exist ("a drill that defeats a guard
        # must first assert the guard EXISTS").
        missing = (proc.returncode in (4, 5)
                   or "no tests ran" in proc.stdout
                   or "collected 0 items" in proc.stdout)
        fired = (not missing) and proc.returncode != 0
        named = "_audit_drill_tmp" in proc.stdout
        results.append((guard, fired, named, missing))
    finally:
        if os.path.exists(DRILL):
            os.remove(DRILL)

assert not os.path.exists(DRILL), "drill file left behind!"

print(f"### {APP}: does each contract fire on a live violation?\n")
silent = []
missing_guards = []
for guard, fired, named, missing in results:
    if missing:
        verdict = "MISSING — no such contract; NOTHING was drilled"
        missing_guards.append(guard)
    elif fired and named:
        verdict = "FIRES  (and names the file)"
    elif fired:
        verdict = "fires  (did NOT name the drill file — check it caught the right thing)"
    else:
        verdict = "SILENT — a live violation passed"
        silent.append(guard)
    print(f"  {guard:34s} {verdict}")

# Layer C: announce the SCOPE actually exercised, not just the outcome. A drill
# that ran against nothing must not read as a clean sweep.
drilled = len(results) - len(missing_guards)
print(f"\nguard_drill: drilled {drilled} of {len(CASES)} declared contract(s); "
      f"{drilled - len(silent)} fired.")
if not results or drilled == 0:
    print("UNKNOWN: no contract was actually drilled — this proves NOTHING.")
    sys.exit(2)
if missing_guards:
    print("MISSING (renamed or deleted — the drill could not test them): "
          + ", ".join(missing_guards))
if silent:
    print("SILENT: " + ", ".join(silent))
if silent or missing_guards:
    sys.exit(1)
