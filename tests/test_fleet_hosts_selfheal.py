"""Guards for the self-healing /etc/hosts drift organ.

`fleet_hosts_selfheal.sh` was added 2026-07-27 after the cloud-init wipe
(a reboot silently destroyed moc5's whole fleet block). Detection already
worked; the gap was that repair needed a human.

The behaviours pinned here are the ones whose failure is SILENT:

  * UNOBSERVABLE must never trigger a write. /etc/hosts SHADOWS DNS, so
    healing from blindness writes a guess over the truth. Reported FAIL,
    never OK (honest_failure_modes #2 — unobservable != healthy).
  * A repair must be REPORTED, not swallowed. If a heal logged OK, a box
    whose address churns hourly would be indistinguishable from a box that
    never drifts — the repair would destroy the drift signal it exists to
    act on (honest_failure_modes #1, committed by the fix itself).
  * The repair is verified by RE-CHECKING the artifact, not by trusting
    --apply's exit code (calibrated_claims #7).
  * A refused `sudo -n` must be reported, not read as "nothing to do" (#9).

These drive the wrapper with a STUB generator so the rc=2 / apply-fails
branches — which cannot be produced safely on a live box — are exercised for
real. The in-sync and heal paths were additionally live-drilled against a
genuinely corrupted /etc/hosts on 2026-07-27.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WRAPPER = REPO / "scripts" / "fleet_hosts_selfheal.sh"


def _stub_generator(path: Path, check_rc: int, apply_rc: int = 0,
                    check_out: str = "", apply_out: str = "",
                    recheck_rc: int | None = None) -> None:
    """Write a fake gen_fleet_hosts.py.

    Records every invocation to `calls.log` so a test can assert that --apply
    was NOT run. `recheck_rc` lets the second --check differ from the first,
    modelling "the apply worked" vs "drift persists".
    """
    second = check_rc if recheck_rc is None else recheck_rc
    path.write_text(
        "#!/bin/bash\n"
        f'echo "$@" >> "$(dirname "$0")/calls.log"\n'
        'if [ "$1" = "--apply" ]; then\n'
        f'  printf "%s" {apply_out!r}\n'
        f'  exit {apply_rc}\n'
        "fi\n"
        f'n=$(grep -c -- "--check" "$(dirname "$0")/calls.log")\n'
        'if [ "$n" -le 1 ]; then\n'
        f'  printf "%s" {check_out!r}\n'
        f'  exit {check_rc}\n'
        "fi\n"
        f'printf "%s" {check_out!r}\n'
        f"exit {second}\n"
    )
    path.chmod(0o755)


def _stub_verdict(path: Path) -> None:
    """Fake cron_verdict.sh — appends '<name> <status> <msg>' to verdict.log."""
    path.write_text(
        "#!/bin/bash\n"
        'name="$1"; status="$2"; shift 2\n'
        'printf "%s %s %s\\n" "$name" "$status" "$*" '
        '>> "$(dirname "$0")/verdict.log"\n'
    )
    path.chmod(0o755)


@pytest.fixture
def rig(tmp_path):
    """A sandboxed copy of the wrapper next to stub siblings.

    The wrapper resolves its helpers relative to its own directory, so copying
    it into tmp_path is enough to isolate it from the real generator and the
    real /etc/hosts.
    """
    shutil.copy2(WRAPPER, tmp_path / WRAPPER.name)
    _stub_verdict(tmp_path / "cron_verdict.sh")

    def run():
        env = dict(os.environ)
        env["FLEET_HOSTS_SELFHEAL_LOCK"] = str(tmp_path / "lock")
        proc = subprocess.run(
            ["bash", str(tmp_path / WRAPPER.name)],
            capture_output=True, text=True, timeout=60, env=env,
        )
        vlog = tmp_path / "verdict.log"
        clog = tmp_path / "calls.log"
        return (
            proc,
            vlog.read_text().strip() if vlog.exists() else "",
            clog.read_text() if clog.exists() else "",
        )

    return tmp_path, run


class TestUnobservableNeverHeals:
    """rc=2 means we cannot SEE DNS. Writing then would shadow the truth."""

    def test_unknown_does_not_run_apply(self, rig):
        tmp, run = rig
        _stub_generator(tmp / "gen_fleet_hosts.py", check_rc=2,
                        check_out="UNKNOWN: no name resolved via DNS")
        _proc, verdict, calls = run()
        assert "--apply" not in calls, (
            "healed from a blind observation — that writes a guess into a "
            "file that SHADOWS DNS"
        )
        assert verdict.startswith("fleet_hosts_drift FAIL")

    def test_unknown_is_never_reported_ok(self, rig):
        tmp, run = rig
        _stub_generator(tmp / "gen_fleet_hosts.py", check_rc=2)
        _proc, verdict, _calls = run()
        assert " OK " not in f" {verdict} ", "unobservable reported as healthy"


class TestHealIsReportedNotSwallowed:
    """A silent repair destroys the very signal it is repairing."""

    def test_heal_reports_concern_and_names_the_drift(self, rig):
        tmp, run = rig
        _stub_generator(
            tmp / "gen_fleet_hosts.py", check_rc=1, apply_rc=0, recheck_rc=0,
            check_out="DRIFT moc4.mf.internal: hosts=- dns=192.0.2.99\n",
        )
        _proc, verdict, calls = run()
        assert "--apply" in calls, "drift detected but never repaired"
        assert "CONCERN" in verdict, (
            "a repair logged OK — an hourly-churning box would then look "
            "identical to one that never drifts"
        )
        assert "moc4.mf.internal" in verdict, "healed silently, without saying what moved"

    def test_in_sync_reports_ok_and_does_not_write(self, rig):
        tmp, run = rig
        _stub_generator(tmp / "gen_fleet_hosts.py", check_rc=0,
                        check_out="in sync: 12 fleet names")
        _proc, verdict, calls = run()
        assert "--apply" not in calls, "rewrote a block that was already correct"
        assert verdict.startswith("fleet_hosts_drift OK")


class TestRepairIsVerifiedAgainstTheArtifact:
    """--apply's exit code is a claim; the re-check is the evidence."""

    def test_persisting_drift_fails_even_when_apply_exits_zero(self, rig):
        tmp, run = rig
        _stub_generator(
            tmp / "gen_fleet_hosts.py", check_rc=1, apply_rc=0, recheck_rc=1,
            check_out="DRIFT moc4.mf.internal: hosts=- dns=192.0.2.99\n",
        )
        _proc, verdict, _calls = run()
        assert "FAIL" in verdict, (
            "trusted --apply's exit code over re-observing the file"
        )
        assert "PERSISTS" in verdict

    def test_recheck_runs_after_apply(self, rig):
        tmp, run = rig
        _stub_generator(
            tmp / "gen_fleet_hosts.py", check_rc=1, apply_rc=0, recheck_rc=0,
            check_out="DRIFT moc4.mf.internal: hosts=- dns=192.0.2.99\n",
        )
        _proc, _verdict, calls = run()
        seq = [ln.strip() for ln in calls.splitlines() if ln.strip()]
        assert seq.count("--check") == 2, f"no post-apply verification: {seq}"
        assert seq.index("--apply") < len(seq) - 1


class TestFailuresLeaveAWitness:
    def test_missing_generator_is_reported(self, rig):
        tmp, run = rig  # no generator stub written at all
        _proc, verdict, _calls = run()
        assert verdict.startswith("fleet_hosts_drift FAIL")
        assert "generator missing" in verdict

    def test_apply_failure_is_reported_not_swallowed(self, rig):
        tmp, run = rig
        _stub_generator(
            tmp / "gen_fleet_hosts.py", check_rc=1, apply_rc=2, recheck_rc=1,
            check_out="DRIFT moc4.mf.internal: hosts=- dns=192.0.2.99\n",
            apply_out="UNKNOWN: post-write verification failed",
        )
        _proc, verdict, _calls = run()
        assert "FAIL" in verdict
        assert "moc4.mf.internal" in verdict

    def test_wrapper_always_exits_zero_so_cron_does_not_double_log(self, rig):
        """The crontab's `|| cron_verdict.sh ... wrapper_crashed` guard must
        fire ONLY when the script died before writing its own verdict."""
        tmp, run = rig
        _stub_generator(tmp / "gen_fleet_hosts.py", check_rc=1, apply_rc=2,
                        recheck_rc=1, check_out="DRIFT x: hosts=- dns=1\n")
        proc, verdict, _calls = run()
        assert proc.returncode == 0, (
            "non-zero exit after writing a verdict would log a second, "
            "contradictory wrapper_crashed line"
        )
        assert verdict, "exited 0 without leaving any verdict at all"
