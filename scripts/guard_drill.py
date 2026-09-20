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

Layer D (2026-09-20) also drills the LAUNCHER: it runs the real
scripts/<app>-launcher.sh through symlinks named like the installed commands,
with a fake `sudo`/`env` first on PATH that prints the argv instead of
executing it, and asserts the exact argv launcher.py receives. Run it after
touching the launcher script, src/launcher.py's arg handling, or the
installer's /usr/local/bin symlinks — a `bash -n` cannot see a wrong argv.
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


# ---------------------------------------------------------------------------
# Layer D: the LAUNCHER argv contract — run the real script, intercept the
# privileged exec, assert the exact argv.
#
# WHY (2026-09-20). The range cfad33b2..e25ee21d symlinked the fleet's
# primary CLI (/usr/local/bin/meshforge, meshforge-tui) to
# scripts/meshforge-launcher.sh and deployed 9/9 having verified it with
# `bash -n` and `help` only. The adversarial review then found, by RUNNING
# it, that `meshforge-tui` had become launcher.py's interactive menu plus NOC
# service startup, and that a documented flag never reached the TUI. A syntax
# check cannot see either. This drill is the 4-line harness that found them,
# kept in the repo so no session has to re-invent it: a fake `sudo` (and a
# fake `env`, for the EUID==0 branch) first on PATH that PRINTS its argv
# instead of executing it, invoked through symlinks carrying the installed
# names so the script's basename dispatch is exercised for real.
#
# Contract = the argv tail after the interpreter. The interpreter itself is
# whatever mf_python() picks on this box (venv or python3), so it is not
# pinned; PYTHONPYCACHEPREFIX= must be present on every launch (the
# chown-sweep cause, TestPrivilegedPycachePrefix).
#
# Doctrine: a drill that has never failed is not evidence. So the drill also
# proves ITSELF on a temp COPY of the script with the alias dispatch line
# removed (never the tracked file — a `git checkout` "restore" on a dirty
# file reverted a session's own edits on 2026-09-20). If that copy passes,
# the drill is inert and exits non-zero.
# ---------------------------------------------------------------------------
import shutil
import stat
import tempfile

LAUNCHER = os.path.join(REPO, "scripts", f"{APP}-launcher.sh")
LAUNCHER_CASES = [
    # (installed name, argv, expected tail, prefix required)
    # `prefix required` = the launch runs a PRIVILEGED python, so the line
    # must carry PYTHONPYCACHEPREFIX=. A privileged shell script (-lora) or a
    # systemctl call (-map start) runs no python and is exempt.
    (f"{APP}-tui", [], "src/launcher.py --tui", True),
    (f"{APP}-tui", ["--no-startup-checks"], "src/launcher.py --tui --no-startup-checks", True),
    (APP, ["tui", "--no-startup-checks"], "src/launcher.py --tui --no-startup-checks", True),
    (APP, [], "src/launcher.py", True),
    (APP, ["--profile", "gateway"], "src/launcher.py --profile gateway", True),
    # The five other installed names (2026-09-20), intercepted at their sudo.
    (f"{APP}-noc", ["--status"], "-m core.orchestrator --status", True),
    (f"{APP}-lora", [], f"/opt/{APP}/scripts/configure_lora.sh", False),
    (f"{APP}-map", ["start"], f"systemctl start {APP}-map", False),
]
# Unprivileged commands run for REAL (no fake tools): the assertion is on
# their output, which is the thing a human types them for.
REAL_CASES = [
    # (installed name, argv, substring the output must contain, required rc
    #  or None). Run with NO display: the first version inherited DISPLAY=:0
    #  and `<app>-web` opened a browser tab on the operator's desktop mid-drill
    #  (2026-09-20) — a drill's side effects must stay inside the drill.
    (f"{APP}-status", ["--brief"], "", 0),
    (f"{APP}-web", [], "9443", None),       # either branch names the port
    (f"{APP}-map", ["url"], ":5000/", 0),
]
FAKE = "#!/bin/bash\necho \"FAKE-EXEC: $*\"\n"


def _run_launcher(script, name, argv, drop_env=()):
    """Invoke `script` through a symlink called `name`, with fake sudo/env
    first on PATH. Returns the intercepted argv line (or the raw output).
    `drop_env` removes variables first (the terminal launcher branches on
    DISPLAY / WAYLAND_DISPLAY)."""
    with tempfile.TemporaryDirectory(prefix="guard_drill_launcher_") as d:
        for tool in ("sudo", "env"):
            p = os.path.join(d, tool)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(FAKE)
            os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
        link = os.path.join(d, name)
        os.symlink(script, link)
        env = dict(os.environ, PATH=d + os.pathsep + os.environ.get("PATH", ""))
        for k in drop_env:
            env.pop(k, None)
        proc = subprocess.run([link] + argv, env=env, capture_output=True,
                              text=True, timeout=30)
        out = (proc.stdout + proc.stderr).strip()
        for line in out.splitlines():
            if line.startswith("FAKE-EXEC: "):
                return line[len("FAKE-EXEC: "):]
        return out or f"(no output, rc={proc.returncode})"


def _check(line, expected, needs_prefix=True):
    """True when the intercepted argv IS `expected` or ends in it — and, for a
    privileged python launch, carries the prefix. (A launch with no
    interpreter, e.g. -lora's shell script, is the whole line.)"""
    line = line.rstrip()
    return ((not needs_prefix or "PYTHONPYCACHEPREFIX=" in line)
            and (line == expected or line.endswith(" " + expected)))


def _run_real(script, name, argv):
    """Run `script` through a symlink called `name` with NO fake tools —
    the command's real, unprivileged output. Returns (rc, stdout+stderr)."""
    with tempfile.TemporaryDirectory(prefix="guard_drill_real_") as d:
        link = os.path.join(d, name)
        os.symlink(script, link)
        env = dict(os.environ)
        for k in ("DISPLAY", "WAYLAND_DISPLAY"):   # never open a browser
            env.pop(k, None)
        proc = subprocess.run([link] + argv, env=env, capture_output=True,
                              text=True, timeout=40)
        return proc.returncode, (proc.stdout + proc.stderr)


print(f"\n### {APP}: does the launcher hand launcher.py the argv it documents?\n")
launcher_failed = []
launcher_ran = 0
if not os.path.exists(LAUNCHER):
    print(f"  MISSING — {LAUNCHER} does not exist; NOTHING was drilled")
else:
    for name, argv, expected, needs_prefix in LAUNCHER_CASES:
        label = " ".join([name] + argv)
        line = _run_launcher(LAUNCHER, name, argv)
        ok = _check(line, expected, needs_prefix)
        launcher_ran += 1
        if not ok:
            launcher_failed.append(label)
        print(f"  {label:40s} {'OK    ' if ok else 'WRONG '} -> …{line[-72:]}")

    for name, argv, must_contain, want_rc in REAL_CASES:
        label = " ".join([name] + argv) + " (real)"
        try:
            rc, out = _run_real(LAUNCHER, name, argv)
        except subprocess.TimeoutExpired:
            rc, out = -1, "(timed out)"
        ok = (must_contain in out and out.strip() != ""
              and (want_rc is None or rc == want_rc))
        launcher_ran += 1
        if not ok:
            launcher_failed.append(label)
        first = next((l for l in out.splitlines() if l.strip()), "(no output)")
        print(f"  {label:40s} {'OK    ' if ok else 'WRONG '} -> rc={rc} {first[:60]}")

    # The DESKTOP launcher, no-display branch: `exec $TUI_CMD` must reach the
    # SAME launcher with `tui`. The emulator branches (xterm/lxterminal/...)
    # hand $TUI_CMD over differently and cannot be exercised headless — the
    # verification of record for those is the operator clicking the icon once
    # after a pull (feedback_web_changes_have_no_local_consumer_of_record).
    terminal = os.path.join(REPO, "scripts", f"{APP}-terminal.sh")
    if os.path.exists(terminal):
        line = _run_launcher(terminal, f"{APP}-terminal", [],
                             drop_env=("DISPLAY", "WAYLAND_DISPLAY"))
        ok = _check(line, "src/launcher.py --tui")
        launcher_ran += 1
        if not ok:
            launcher_failed.append(f"{APP}-terminal (no display)")
        print(f"  {f'{APP}-terminal (no display)':40s} {'OK    ' if ok else 'WRONG '} -> …{line[-72:]}")

    # Self-test: the drill must FAIL on a copy with the alias line removed.
    with tempfile.TemporaryDirectory(prefix="guard_drill_broken_") as d:
        broken = os.path.join(d, os.path.basename(LAUNCHER))
        with open(LAUNCHER, encoding="utf-8") as fh:
            src = fh.read()
        marker = f"{APP}-tui) set -- tui \"$@\" ;;"
        if marker not in src:
            print(f"  {'self-test':40s} MISSING — alias dispatch line not found in "
                  "the launcher; re-aim the self-test (the script moved)")
            launcher_failed.append("self-test:marker-missing")
        else:
            with open(broken, "w", encoding="utf-8") as fh:
                fh.write(src.replace(marker, ""))
            shutil.copymode(LAUNCHER, broken)
            line = _run_launcher(broken, f"{APP}-tui", [])
            if _check(line, "src/launcher.py --tui"):
                print(f"  {'self-test':40s} SILENT — a copy WITHOUT the alias "
                      "dispatch still passed; this drill proves nothing")
                launcher_failed.append("self-test:inert")
            else:
                print(f"  {'self-test':40s} FIRES  (copy without the alias line "
                      f"-> …{line[-40:]})")

print(f"\nguard_drill: launcher drilled {launcher_ran} of {len(LAUNCHER_CASES) + len(REAL_CASES) + 1} "
      f"argv case(s); {launcher_ran - len([f for f in launcher_failed if not f.startswith('self-test')])} correct.")
if launcher_ran == 0:
    print("UNKNOWN: the launcher was not drilled — this proves NOTHING.")
    sys.exit(2)
if launcher_failed:
    print("LAUNCHER WRONG: " + ", ".join(launcher_failed)
          + "\n  fix: scripts/" + APP + "-launcher.sh basename dispatch + `tui)` "
          "case, and launch_interface() extra_args forwarding in src/launcher.py")

if silent or missing_guards or launcher_failed:
    sys.exit(1)
