"""boot_survival names WHY a unit never started when a dependency failed.

A unit whose start job is cancelled because a Requires=/BindsTo=/Requisite=
dependency failed never enters `failed` — it stays inactive/dead with its
Result unchanged, invisible to `systemctl --failed` (research doc
.claude/research/systemd_unit_state_semantics_2026_10_26.md §5). The audit
caught it as `enabled-but-inactive never started this boot` (3574da8f, lab
drill 10-26) but never said why. Two witnesses, neither required:

  * STATE — a required dependency that is `failed` right now (no journal);
  * JOURNAL — PID 1 / the user manager logs the cancelled job with the
    structured fields JOB_TYPE=start JOB_RESULT=dependency and UNIT= /
    USER_UNIT= (measured 2026-10-26). Matched by FIELD, never by text.

A journal the audit cannot read claims nothing (hfm #2): no cause text,
never "no dependency failed".

Runs the REAL script against stubbed `systemctl` and `journalctl`.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
AUDIT = REPO / "scripts" / "boot_survival_audit.sh"

SYSTEMCTL = r"""#!/usr/bin/env bash
scope=system; args=()
for a in "$@"; do
  if [ "$a" = "--user" ]; then scope=user; else args+=("$a"); fi
done
set -- "${args[@]}"
if [ "$scope" = user ]; then
  [ -n "$USER_ON" ] || { case "$1" in show-environment) exit 1;; esac; exit 0; }
  P=user_
else
  P=
fi
case "$1" in
  show-environment) exit 0 ;;
  list-units)      : ;;
  list-unit-files) cat "$FIX/${P}unitfiles" 2>/dev/null ;;
  show)
    [ "$2" = "-p" ] && { echo 40000000; exit 0; }
    grep -m1 "^$2 $4 " "$FIX/${P}props" 2>/dev/null | cut -d' ' -f3- ;;
  is-active)       a=$(grep -m1 "^$2 " "$FIX/${P}active" 2>/dev/null | cut -d' ' -f2-)
                   echo "${a:-inactive}" ;;
esac
exit 0
"""

# Logs every call; answers a matching record only when every structured
# match the script passed is one the fixture holds; "unreadable" mimics a
# box where the audit user is in neither adm nor systemd-journal.
JOURNALCTL = r"""#!/usr/bin/env bash
echo "$*" >> "$FIX/jcalls"
[ -e "$FIX/journal_unreadable" ] && {
  echo "No journal files were opened due to insufficient permissions." >&2
  exit 1; }
want=""; for a in "$@"; do case "$a" in *=*) want="$want $a";; esac; done
while read -r line; do
  ok=1; for w in $want; do case " $line " in *" $w "*) ;; *) ok=0;; esac; done
  # short-monotonic shape: "[  <sec.usec>] host manager[pid]: <msg>"
  [ "$ok" = 1 ] && { m=$(echo "$line" | grep -o 'MONO=[^ ]*' | cut -d= -f2)
                     printf '[%12s] host systemd[1]: Job failed with result dependency.\n' "${m:-5.000000}"
                     exit 0; }
done < "$FIX/journal" 2>/dev/null
exit 0
"""


def _run(tmp_path, *, deps="", active="", journal="", unreadable=False,
         user=False, kind="Requires", inactive_exit=0):
    u = "gw.service"
    fix = tmp_path / "fix"
    fix.mkdir(exist_ok=True)
    P = "user_" if user else ""
    props = (f"{u} Type simple\n{u} ConditionResult no\n"
             f"{u} ConditionTimestampMonotonic 0\n"
             f"{u} WantedBy multi-user.target\n{u} RequiredBy \n"
             f"{u} InactiveEnterTimestampMonotonic 0\n"
             f"{u} InactiveExitTimestampMonotonic {inactive_exit}\n"
             + "".join(f"{u} {k} \n" for k in ("Requires", "BindsTo",
                                                 "Requisite") if k != kind)
             + f"{u} {kind} {deps}\n")
    (fix / f"{P}unitfiles").write_text(f"{u} enabled\n")
    (fix / f"{P}props").write_text(props)
    (fix / f"{P}active").write_text(   # overrides FIRST: the stub's grep -m1
        active + f"{u} inactive\nmulti-user.target active\n")
    (fix / "journal").write_text(journal)
    if unreadable:
        (fix / "journal_unreadable").write_text("")
    binn = tmp_path / "bin"
    binn.mkdir(exist_ok=True)
    for name, body in (("systemctl", SYSTEMCTL), ("journalctl", JOURNALCTL)):
        (binn / name).write_text(body)
        (binn / name).chmod(0o755)
    env = dict(os.environ, PATH=f"{binn}:{os.environ['PATH']}", FIX=str(fix),
               HOME=str(tmp_path),
               CRON_VERDICT_LOG=str(tmp_path / "verdicts.log"),
               CRON_VERDICT_OUT_DIR=str(tmp_path / "cron_out"))
    env.pop("XDG_RUNTIME_DIR", None)
    if user:
        env["USER_ON"] = "1"
    p = subprocess.run(["bash", str(AUDIT)], env=env, capture_output=True,
                       text=True, timeout=120)
    calls = (fix / "jcalls").read_text() if (fix / "jcalls").exists() else ""
    return p.returncode, p.stdout + p.stderr, calls


JREC = "UNIT=gw.service JOB_TYPE=start JOB_RESULT=dependency\n"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
class TestDependencyCauseIsNamed:

    def test_failed_dep_confirmed_by_journal(self, tmp_path):
        rc, out, _ = _run(tmp_path, deps="dep.service",
                          active="dep.service failed\n", journal=JREC)
        assert rc == 1, out
        assert "start cancelled: required dep.service failed" in out, out

    def test_failed_dep_with_unreadable_journal_names_state_only(
            self, tmp_path):
        rc, out, _ = _run(tmp_path, deps="dep.service",
                          active="dep.service failed\n", unreadable=True)
        assert rc == 1, out
        assert "required dep.service is failed" in out, out
        assert "cancelled" not in out, out

    def test_journal_alone_when_the_dep_was_since_reset(self, tmp_path):
        rc, out, _ = _run(tmp_path, deps="dep.service", journal=JREC)
        assert "start cancelled: a dependency failed (journal)" in out, out

    def test_no_witness_makes_no_claim(self, tmp_path):
        """Unreadable journal + no failed dep: say nothing, never 'no
        dependency failed' (hfm #2)."""
        rc, out, _ = _run(tmp_path, deps="dep.service", unreadable=True)
        assert rc == 1, out
        assert "dependency" not in out and "required" not in out, out

    def test_bindsto_counts(self, tmp_path):
        rc, out, _ = _run(tmp_path, deps="dep.service", kind="BindsTo",
                          active="dep.service failed\n")
        assert "required dep.service is failed" in out, out

    def test_an_unmet_requisite_is_not_called_a_failure(self, tmp_path):
        """Fable D-2: Requisite= is unmet when the dep is merely not active —
        nothing failed, and the line must not say so."""
        rc, out, _ = _run(tmp_path, deps="dep.service", kind="Requisite",
                          journal=JREC)
        assert "requisite dep.service not active" in out, out
        assert "failed" not in out.split("—", 1)[-1], out

    def test_a_record_older_than_the_last_start_is_not_the_cause(
            self, tmp_path):
        """Fable D-1: a start cancelled EARLY this boot, then a later clean
        run and stop — the old record must not be blamed."""
        rc, out, _ = _run(tmp_path, deps="dep.service",
                          journal=JREC.replace("\n", " MONO=5.000000\n"),
                          inactive_exit=7_200_000_000)
        assert "dependency" not in out, out

    def test_a_record_newer_than_the_last_start_still_counts(self, tmp_path):
        rc, out, _ = _run(tmp_path, deps="dep.service",
                          journal=JREC.replace("\n", " MONO=7300.000000\n"),
                          inactive_exit=7_200_000_000)
        assert "start cancelled: a dependency failed (journal)" in out, out

    def test_an_unparseable_record_time_claims_nothing(self, tmp_path):
        rc, out, _ = _run(tmp_path, deps="dep.service",
                          journal=JREC.replace("\n", " MONO=garbage\n"))
        assert "dependency" not in out, out

    def test_journal_is_matched_by_field_for_the_right_scope(self, tmp_path):
        _, _, calls = _run(tmp_path, deps="dep.service")
        assert "UNIT=gw.service" in calls and "JOB_RESULT=dependency" in calls
        assert "--user" not in calls and "-b" in calls.split()
        rc, out, calls = _run(tmp_path, deps="dep.service", user=True,
                              journal=("USER_UNIT=gw.service JOB_TYPE=start "
                                       "JOB_RESULT=dependency\n"))
        assert "--user" in calls and "USER_UNIT=gw.service" in calls, calls
        assert "start cancelled: a dependency failed (journal)" in out, out

    def test_a_healthy_unit_never_queries_the_journal(self, tmp_path):
        """Cost: the journal is read only for a casualty."""
        fix = tmp_path
        rc, out, calls = _run(fix, active="gw.service active\n")
        assert rc == 0, out
        assert calls == "", calls
