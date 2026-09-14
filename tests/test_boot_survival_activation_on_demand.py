"""boot_survival must not call an ACTIVATION-ON-DEMAND unit a boot casualty.

Found 2026-09-14, the day `cron_verdict_stale` began paging on FAIL. moc5
reported `boot_survival FAIL — cups.service enabled-but-inactive` while kiai,
with byte-identical unit state, reported OK. Neither box was broken: cups is
socket- AND path-activated and idle-exits about a minute after each use —

    00:01:15 Started cups.service
    00:02:16 cups.service: Deactivated successfully

— so whether the 07:17 audit caught it awake was a COIN FLIP, and the loser
got a page nobody could act on. `enabled + inactive` is that unit's correct
resting state, not evidence the boot left it down.

The audit already understood this class for `Type=dbus` ("activation-on-demand,
idle is healthy"); cups is `Type=notify`, so the Type test missed it. The fix
is the same judgement reached through the other mechanism: a non-empty
`TriggeredBy` means something else starts this unit on demand.

These run the REAL script against a stubbed `systemctl`, because the thing
under test is its classification of systemd's answers — not a Python mock of
what we wish systemd said.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
AUDIT = REPO / "scripts" / "boot_survival_audit.sh"

STUB = r"""#!/usr/bin/env bash
# Stubbed systemctl. USER scope answers "nothing here" so the fixtures only
# have to describe the system manager.
scope=system; args=()
for a in "$@"; do
  if [ "$a" = "--user" ]; then scope=user; else args+=("$a"); fi
done
set -- "${args[@]}"
if [ "$scope" = user ]; then
  case "$1" in show-environment) exit 1;; is-active) echo inactive;; esac
  exit 0
fi
case "$1" in
  list-units)      cat "$FIX/failed" 2>/dev/null ;;
  list-unit-files) cat "$FIX/unitfiles" 2>/dev/null ;;
  show)
    u="$2"; prop="$4"
    case "$prop" in
      Type)            grep -m1 "^$u " "$FIX/type" 2>/dev/null | cut -d' ' -f2- ;;
      TriggeredBy)     grep -m1 "^$u " "$FIX/triggeredby" 2>/dev/null | cut -d' ' -f2- ;;
      ConditionResult) echo "" ;;
      *)               echo "" ;;
    esac ;;
  is-active)       grep -m1 "^$2 " "$FIX/active" 2>/dev/null | cut -d' ' -f2- ;;
esac
exit 0
"""


def _run(tmp_path, *, unitfiles, type_, triggeredby, active, failed=""):
    fix = tmp_path / "fix"
    fix.mkdir(exist_ok=True)
    (fix / "unitfiles").write_text(unitfiles)
    (fix / "type").write_text(type_)
    (fix / "triggeredby").write_text(triggeredby)
    (fix / "active").write_text(active)
    (fix / "failed").write_text(failed)

    binn = tmp_path / "bin"
    binn.mkdir(exist_ok=True)
    stub = binn / "systemctl"
    stub.write_text(STUB)
    stub.chmod(0o755)
    # loginctl is only reached on the user-scope skip path; absent is fine.

    env = dict(os.environ)
    env["PATH"] = f"{binn}:{env['PATH']}"
    env["FIX"] = str(fix)
    env["HOME"] = str(tmp_path)
    # Never let a drill write the box's real verdict log — a FAIL line there
    # would manufacture exactly the page this fix removes.
    env["CRON_VERDICT_LOG"] = str(tmp_path / "verdicts.log")
    env["CRON_VERDICT_OUT_DIR"] = str(tmp_path / "cron_out")
    env.pop("XDG_RUNTIME_DIR", None)

    p = subprocess.run(["bash", str(AUDIT)], env=env, capture_output=True,
                       text=True, timeout=120)
    return p.returncode, p.stdout + p.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
class TestActivationOnDemandIsNotACasualty:

    def test_a_triggered_idle_unit_is_not_a_casualty(self, tmp_path):
        """THE cups shape: Type=notify, TriggeredBy a socket, idle right now."""
        rc, out = _run(
            tmp_path,
            unitfiles="cups.service enabled\n",
            type_="cups.service notify\n",
            triggeredby="cups.service cups.socket cups.path\n",
            active="cups.service inactive\n",
        )
        assert "cups.service" not in out, out
        assert rc == 0, out

    def test_an_untriggered_idle_unit_IS_still_a_casualty(self, tmp_path):
        """The guard must not over-exempt. Same type, same idle state, no
        trigger — nothing will start this one, so it is genuinely down."""
        rc, out = _run(
            tmp_path,
            unitfiles="meshforge-gateway.service enabled\n",
            type_="meshforge-gateway.service notify\n",
            triggeredby="",
            active="meshforge-gateway.service inactive\n",
        )
        assert "meshforge-gateway.service enabled-but-inactive" in out, out
        assert rc == 1, out

    def test_a_triggered_unit_that_FAILED_is_still_a_casualty(self, tmp_path):
        """What the narrowing does NOT hide, and the reason it is safe: a
        triggered unit that cannot start lands in `failed`, and the failed-unit
        sweep catches it regardless of enablement, type, or trigger. Only the
        'idle means dead' misreading goes away."""
        rc, out = _run(
            tmp_path,
            unitfiles="cups.service enabled\n",
            type_="cups.service notify\n",
            triggeredby="cups.service cups.socket\n",
            active="cups.service failed\n",
            failed="cups.service loaded failed failed CUPS Scheduler\n",
        )
        assert "cups.service FAILED" in out, out
        assert rc == 1, out

    def test_a_healthy_active_unit_still_counts_as_ok(self, tmp_path):
        rc, out = _run(
            tmp_path,
            unitfiles="meshforge-gateway.service enabled\n",
            type_="meshforge-gateway.service notify\n",
            triggeredby="",
            active="meshforge-gateway.service active\n",
        )
        assert "OK: 1 enabled unit(s) active" in out, out
        assert rc == 0, out
