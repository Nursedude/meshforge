"""boot_survival must not read systemd's UNEVALUATED ConditionResult as a
refusal.

Found 2026-10-26 (Fable non-author read): the audit skipped every enabled
unit with `ConditionResult=no` as "the box told it not to run here". But
`no` is systemd's DEFAULT before any condition is evaluated —
`gcr-ssh-agent.service` reads `ConditionResult=no` with zero Condition lines
in its unit file. Measured fleet-wide the same day: after the audit's own
filters, 1-2 such units per box, ConditionTimestampMonotonic=0, and in every
case NOTHING that wants them is active (`fbd-alert-slider` →
graphical-session.target on headless boxes, `wayvnc-control` → wayvnc,
`hciuart` → a serial device moc4 lacks). So:

  * condition EVALUATED and refused (timestamp != 0) → skipped, as before
  * unevaluated, and an ACTIVE unit wants it → a casualty: it should have
    started this boot and did not (the case the old skip hid)
  * unevaluated, nothing that wants it is active → NOT a casualty, but
    named on the OK line (`not-pulled-in:`) — a skip hides a verdict, never
    an observation

Runs the REAL script against a stubbed `systemctl`.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
AUDIT = REPO / "scripts" / "boot_survival_audit.sh"

STUB = r"""#!/usr/bin/env bash
scope=system; args=()
for a in "$@"; do
  if [ "$a" = "--user" ]; then scope=user; else args+=("$a"); fi
done
set -- "${args[@]}"
if [ "$scope" = user ]; then
  case "$1" in show-environment) exit 1;; esac; exit 0
fi
case "$1" in
  list-units)      : ;;
  list-unit-files) cat "$FIX/unitfiles" ;;
  show)
    [ "$2" = "-p" ] && { echo 40000000; exit 0; }
    grep -m1 "^$2 $4 " "$FIX/props" 2>/dev/null | cut -d' ' -f3- ;;
  # real systemctl answers `inactive` for a unit it has no state for — an
  # empty answer would let `!= ""` pass for `= active` (Fable C-2)
  is-active)       a=$(grep -m1 "^$2 " "$FIX/active" 2>/dev/null | cut -d' ' -f2-)
                   echo "${a:-inactive}" ;;
esac
exit 0
"""


def _run(tmp_path, unit, *, cond_ts, wanted_by="", active_wanters=(),
         kind="service", required_by="", extra_props=""):
    u = f"{unit}.{kind}"
    fix = tmp_path / "fix"
    fix.mkdir(exist_ok=True)
    (fix / "unitfiles").write_text(f"{u} enabled\n")
    props = (f"{u} Type simple\n{u} ConditionResult no\n"
             f"{u} ConditionTimestampMonotonic {cond_ts}\n"
             f"{u} WantedBy {wanted_by}\n{u} RequiredBy {required_by}\n"
             f"{u} InactiveEnterTimestampMonotonic 0\n" + extra_props)
    (fix / "props").write_text(props)
    (fix / "active").write_text(
        f"{u} inactive\n" + "".join(f"{w} active\n" for w in active_wanters))
    binn = tmp_path / "bin"
    binn.mkdir(exist_ok=True)
    (binn / "systemctl").write_text(STUB)
    (binn / "systemctl").chmod(0o755)
    env = dict(os.environ, PATH=f"{binn}:{os.environ['PATH']}", FIX=str(fix),
               HOME=str(tmp_path),
               CRON_VERDICT_LOG=str(tmp_path / "verdicts.log"),
               CRON_VERDICT_OUT_DIR=str(tmp_path / "cron_out"))
    env.pop("XDG_RUNTIME_DIR", None)
    p = subprocess.run(["bash", str(AUDIT)], env=env, capture_output=True,
                       text=True, timeout=120)
    return p.returncode, p.stdout + p.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
class TestUnevaluatedConditionIsNotARefusal:

    def test_an_evaluated_refusal_is_still_skipped(self, tmp_path):
        rc, out = _run(tmp_path, "smartd", cond_ts=5_000_000)
        assert rc == 0, out
        assert "smartd" not in out, out

    def test_unevaluated_with_an_active_wanter_is_a_casualty(self, tmp_path):
        """THE hidden case: multi-user.target came up, the unit never did."""
        rc, out = _run(tmp_path, "gw", cond_ts=0,
                       wanted_by="multi-user.target",
                       active_wanters=("multi-user.target",))
        assert rc == 1, out
        assert ("gw.service enabled-but-inactive never started this boot "
                "(wanted by multi-user.target, active)") in out, out

    def test_a_timer_follows_the_same_rule(self, tmp_path):
        rc, out = _run(tmp_path, "nightly", cond_ts=0, kind="timer",
                       wanted_by="timers.target",
                       active_wanters=("timers.target",))
        assert rc == 1, out
        assert "nightly.timer enabled-but-inactive" in out, out

    def test_unevaluated_and_nothing_wanting_it_is_up_is_named_not_failed(
            self, tmp_path):
        """The live fleet shape: a graphical unit on a headless box."""
        rc, out = _run(tmp_path, "fbd-alert-slider", cond_ts=0,
                       wanted_by="graphical-session.target")
        assert rc == 0, out
        assert ("not-pulled-in:fbd-alert-slider.service"
                "(wanted by graphical-session.target)") in out, out

    def test_unevaluated_with_no_wanters_is_named(self, tmp_path):
        rc, out = _run(tmp_path, "orphan", cond_ts=0)
        assert rc == 0, out
        assert "not-pulled-in:orphan.service(wanted by nothing)" in out, out

    def test_required_by_an_active_unit_is_a_casualty(self, tmp_path):
        rc, out = _run(tmp_path, "gw", cond_ts=0,
                       required_by="gateway.target",
                       active_wanters=("gateway.target",))
        assert rc == 1, out
        assert "(wanted by gateway.target, active)" in out, out

    def test_wanted_by_a_target_that_never_came_up_is_a_casualty(
            self, tmp_path):
        """Fable C-1: the walk was one level. foo.target is enabled into an
        ACTIVE multi-user.target but never came up, so gw never started —
        a boot casualty, not residue."""
        rc, out = _run(tmp_path, "gw", cond_ts=0, wanted_by="foo.target",
                       active_wanters=("multi-user.target",),
                       extra_props=("foo.target WantedBy multi-user.target\n"
                                    "foo.target RequiredBy \n"))
        assert rc == 1, out
        assert ("(wanted by foo.target, down; it is wanted by "
                "multi-user.target, active)") in out, out

    def test_a_chain_of_down_wanters_ending_nowhere_is_residue(self, tmp_path):
        rc, out = _run(tmp_path, "slider", cond_ts=0,
                       wanted_by="graphical-session.target",
                       extra_props=("graphical-session.target WantedBy "
                                    "gnome-session.target\n"
                                    "graphical-session.target RequiredBy \n"))
        assert rc == 0, out
        assert "not-pulled-in:slider.service" in out, out

    def test_unanswered_condition_timestamp_is_judged_not_skipped(
            self, tmp_path):
        """No answer is not 'evaluated' (hfm #2) — judge it by its wanters."""
        rc, out = _run(tmp_path, "gw", cond_ts="",
                       wanted_by="multi-user.target",
                       active_wanters=("multi-user.target",))
        assert rc == 1, out
