"""boot_survival must say WHEN a unit went down, not assume the boot did it.

Found 2026-10-26: the 07:17 audit FAILed on 8 headless boxes with
`boot left 1 unit(s) down: usr:xdg-desktop-portal-gtk.service FAILED`. No box
had rebooted. The unit had died 17 h earlier, at 10-25 14:04, when the 10-25
headless switch took its display away ("Error reading events from display:
Broken pipe"). The FAIL was right; the cause it named sent the reader to the
boot. Each casualty now carries when it went down, judged on the MONOTONIC
clock (time since boot — RTC-less Pis step their wall clock, hfm #6): the
unit's InactiveEnterTimestampMonotonic against its manager's
FinishTimestampMonotonic.

Runs the REAL script against a stubbed `systemctl`, like its sibling
test_boot_survival_activation_on_demand.py.
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
  [ -n "$USER_ON" ] || { case "$1" in show-environment) exit 1;; esac; exit 0; }
  P=user_
else
  P=
fi
case "$1" in
  show-environment) exit 0 ;;
  list-units)      cat "$FIX/${P}failed" 2>/dev/null ;;
  list-unit-files) cat "$FIX/${P}unitfiles" 2>/dev/null ;;
  show)
    if [ "$2" = "-p" ]; then           # the MANAGER's own property
      grep -m1 "^$3 " "$FIX/${P}manager" 2>/dev/null | cut -d' ' -f2-; exit 0
    fi
    u="$2"; prop="$4"
    grep -m1 "^$u $prop " "$FIX/${P}props" 2>/dev/null | cut -d' ' -f3- ;;
  is-active)       grep -m1 "^$2 " "$FIX/${P}active" 2>/dev/null | cut -d' ' -f2- ;;
esac
exit 0
"""

BOOT_DONE_US = 40_000_000          # boot finished 40 s after kernel start
HOUR_US = 3_600_000_000
GRACE_US = 600_000_000             # pinned: >= the @reboot run's sleep 420


def _run(tmp_path, *, failed="", unitfiles="", active="", props="",
         manager=f"FinishTimestampMonotonic {BOOT_DONE_US}\n", user=None):
    fix = tmp_path / "fix"
    fix.mkdir(exist_ok=True)
    for name, text in (("failed", failed), ("unitfiles", unitfiles),
                       ("active", active), ("props", props),
                       ("manager", manager)):
        (fix / name).write_text(text)
    for name, text in (user or {}).items():
        (fix / f"user_{name}").write_text(text)
    binn = tmp_path / "bin"
    binn.mkdir(exist_ok=True)
    stub = binn / "systemctl"
    stub.write_text(STUB)
    stub.chmod(0o755)
    env = dict(os.environ, PATH=f"{binn}:{os.environ['PATH']}", FIX=str(fix),
               HOME=str(tmp_path),
               CRON_VERDICT_LOG=str(tmp_path / "verdicts.log"),
               CRON_VERDICT_OUT_DIR=str(tmp_path / "cron_out"))
    env.pop("XDG_RUNTIME_DIR", None)
    if user is not None:
        env["USER_ON"] = "1"
    p = subprocess.run(["bash", str(AUDIT)], env=env, capture_output=True,
                       text=True, timeout=120)
    return p.returncode, p.stdout + p.stderr


def _failed(u, mono, wall="Sun 2026-10-25 14:04:37 HST"):
    return dict(
        failed=f"{u} loaded failed failed x\n",
        props=(f"{u} Transient no\n"
               f"{u} InactiveEnterTimestampMonotonic {mono}\n"
               f"{u} InactiveEnterTimestamp {wall}\n"))


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
class TestCasualtySaysWhenItWentDown:

    def test_unit_that_died_after_boot_says_so_and_still_fails(self, tmp_path):
        """THE 10-26 shape: failed 17 h after boot finished."""
        rc, out = _run(tmp_path, **_failed("portal.service",
                                           BOOT_DONE_US + 17 * HOUR_US))
        assert rc == 1, out                       # still a FAIL
        assert "boot left" not in out, out        # the false cause is gone
        assert ("portal.service FAILED after boot "
                "(Sun 2026-10-25 14:04:37 HST)") in out, out

    def test_unit_that_died_during_boot_says_so(self, tmp_path):
        rc, out = _run(tmp_path, **_failed("gw.service", BOOT_DONE_US - 5))
        assert rc == 1, out
        assert "gw.service FAILED during boot" in out, out

    def test_never_started_is_not_a_death(self, tmp_path):
        """Fable 3rd read A2: t=0 means nothing died — say never started."""
        rc, out = _run(tmp_path, **_failed("gw.service", 0))
        assert "gw.service FAILED never started this boot" in out, out

    def test_grace_boundary_is_pinned(self, tmp_path):
        rc, out = _run(tmp_path, **_failed("a.service",
                                           BOOT_DONE_US + GRACE_US))
        assert "a.service FAILED during boot" in out, out
        rc, out = _run(tmp_path, **_failed("a.service",
                                           BOOT_DONE_US + GRACE_US + 1))
        assert "a.service FAILED after boot" in out, out

    @pytest.mark.parametrize("fin,t", [("0", "5"), ("[not set]", "5"),
                                       (str(BOOT_DONE_US), "[not set]"),
                                       (str(BOOT_DONE_US), "n/a")])
    def test_zero_or_odd_answers_are_unobserved(self, tmp_path, fin, t):
        rc, out = _run(tmp_path,
                       manager=f"FinishTimestampMonotonic {fin}\n",
                       **_failed("a.service", t))
        assert "a.service FAILED (when unobserved)" in out, out

    def test_restart_count_rides_an_after_boot_death(self, tmp_path):
        kw = _failed("a.service", BOOT_DONE_US + HOUR_US)
        kw["props"] += "a.service NRestarts 3\n"
        rc, out = _run(tmp_path, **kw)
        assert "after boot (Sun 2026-10-25 14:04:37 HST, NRestarts=3)" in out

    def _user(self, *, um_start, um_fin, unit_t, nrestarts=None):
        u = "portal.service"
        props = (f"{u} Transient no\n"
                 f"{u} InactiveEnterTimestampMonotonic {unit_t}\n"
                 f"{u} InactiveEnterTimestamp Mon 2026-10-26 07:32:30 HST\n")
        return dict(failed=f"{u} loaded failed failed x\n", props=props,
                    manager=(f"FinishTimestampMonotonic {um_fin}\n"
                             f"UserspaceTimestampMonotonic {um_start}\n"))

    def test_user_scope_judges_against_the_user_managers_finish(self, tmp_path):
        # user manager finished 100 s after the system one; a unit dying just
        # past SYSTEM fin+grace is still inside the USER manager's boot
        um_fin = BOOT_DONE_US + 100_000_000
        rc, out = _run(tmp_path, user=self._user(
            um_start=11_000_000, um_fin=um_fin,
            unit_t=BOOT_DONE_US + GRACE_US + 1))
        assert "usr:portal.service FAILED during boot" in out, out

    def test_unfinished_system_boot_never_reads_late(self, tmp_path):
        """Fable delta read A-1: system Finish=0 (boot not done, the @reboot
        run can meet it) — 'late' is undeterminable, never asserted."""
        rc, out = _run(tmp_path,
                       manager="FinishTimestampMonotonic 0\n",
                       user=self._user(um_start=700_000_000,
                                       um_fin=707_000_000, unit_t=0))
        assert "usr:portal.service FAILED (when unobserved)" in out, out
        assert "re)started" not in out, out

    def test_a_late_user_manager_never_reads_during_boot(self, tmp_path):
        """Fable 3rd read A1: a user manager that (re)started hours after
        boot has every unit inside ITS start window — that is not the boot."""
        late = BOOT_DONE_US + 17 * HOUR_US
        for t, want in ((late + 30_000_000,
                         "usr:portal.service FAILED after boot (Mon 2026-10-26 "
                         "07:32:30 HST; user manager (re)started after boot)"),
                        (0, "usr:portal.service FAILED never started since "
                            "the user manager (re)started")):
            rc, out = _run(tmp_path, user=self._user(
                um_start=late, um_fin=late + 7_000_000, unit_t=t))
            assert want in out, out
            assert "during boot" not in out, out

    def test_unanswered_timestamps_make_no_claim(self, tmp_path):
        """No answer is UNKNOWN, never 'during boot' (hfm #2)."""
        kw = _failed("gw.service", "")
        rc, out = _run(tmp_path, manager="", **kw)
        assert rc == 1, out
        assert "gw.service FAILED (when unobserved)" in out, out
        assert "during boot" not in out and "after boot" not in out, out

    def test_enabled_but_inactive_unit_says_when_too(self, tmp_path):
        u = "gw.service"
        rc, out = _run(
            tmp_path, unitfiles=f"{u} enabled\n", active=f"{u} inactive\n",
            props=(f"{u} Type notify\n"
                   f"{u} InactiveEnterTimestampMonotonic "
                   f"{BOOT_DONE_US + 2 * HOUR_US}\n"
                   f"{u} InactiveEnterTimestamp Mon 2026-10-26 01:00:00 HST\n"))
        assert rc == 1, out
        assert (f"{u} enabled-but-inactive after boot "
                "(Mon 2026-10-26 01:00:00 HST)") in out, out

    def test_verdict_header_counts_without_blaming_the_boot(self, tmp_path):
        rc, out = _run(tmp_path, **_failed("portal.service",
                                           BOOT_DONE_US + HOUR_US))
        logs = [tmp_path / "verdicts.log", tmp_path / "cron_verdicts.log"]
        line = "".join(p.read_text() for p in logs if p.exists())
        assert "FAIL 1 unit(s) down:" in line, (line, out)
