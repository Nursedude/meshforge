"""hold_rns_clients() holds MEASURED RNS clients — and never the wrong units.

2026-10-02: a fleet measurement found RNS clients the curated
RNS_CLIENT_UNITS missed — system `lxmd` (moc1), user `meshforge-lxmd` (moc),
user `meshcore-chat` (MeshAnchor box). A TUI rnsd repair there would have left
an lxmd up through the restart: the #69 squat window. The cure measures which
live units load RNS; review D then measured the cure classing ssh.service as a
client (an `ssh box 'cd /opt/meshforge && python3 ...'` under sshd) — a hold
leaves units STOPPED when rnsd fails, so that was a lock-out. These tests pin
both halves: real clients are held; ssh/cron/transient/oneshot/repo-only never.
"""
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import utils.rnsd_restart_order as ro  # noqa: E402
from utils.observation import Failed, Seen, Unobservable  # noqa: E402

MEASURE = ro._measure_rns_clients_impl


def _u(unit, user=False, typ="simple", frag="/etc/systemd/system/x.service", pid="10"):
    return (user, {"Id": unit, "MainPID": pid, "ControlGroup": f"/x/{unit}",
                   "Type": typ, "FragmentPath": frag})


UNITS = [_u("rnsd.service"), _u("lxmd.service"), _u("meshforge-lxmd.service", True),
         _u("meshforge-map.service"), _u("ssh.service"), _u("cron.service"),
         _u("run-u42.service", True, frag="/run/user/1000/systemd/transient/run-u42.service"),
         _u("meshforge-digest.service", True, typ="oneshot"),
         _u("unattended-upgrades.service")]


def _probe(stdout, rc=0):
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr="")


def _measure(out, units=UNITS, user_seen=True, tmp_path=None):
    probe = tmp_path / "probe.py"
    probe.write_text("")
    with patch.object(ro, "_active_units", return_value=(units, user_seen)), \
         patch.object(ro, "_cgroup_pids", return_value=[123]), \
         patch.object(ro.subprocess, "run", return_value=_probe(out)) as run:
        return MEASURE(probe), run


def test_floor_carries_the_three_measured_misses():
    floor = set(ro.RNS_CLIENT_UNITS)
    assert ("lxmd", False) in floor and ("lxmd", True) in floor
    assert ("meshforge-lxmd", True) in floor
    assert ("meshcore-chat", True) in floor


def test_entry_is_a_client_repo_is_only_named(tmp_path):
    out = ("SB lxmd.service rns=1 0 entry\n"
           "SC meshforge-lxmd.service rns=1 entry\n"
           "SC meshforge-lxmd.service rns=1 entry\n"
           "SC meshforge-map.service rns=1 repo\n"
           "SX unattended-upgrades.service rns\n"
           "SU unattended-upgrades.service no-access\n")
    obs, run = _measure(out, tmp_path=tmp_path)
    assert isinstance(obs, Seen)
    m = obs.value
    assert m.clients == [("lxmd", False), ("meshforge-lxmd", True)]
    assert m.repo_only == [("meshforge-map", False)]
    assert m.unprobed == [("unattended-upgrades", False, "no-access")]
    assert m.unjudged == 1
    assert "--no-descend" in run.call_args.args[0]          # cgroup pids only


def test_ssh_cron_transient_oneshot_and_rnsd_are_never_candidates(tmp_path):
    """Review D's blocker: even with ENTRY evidence, these are never held."""
    out = ("SC ssh.service rns=1 entry\nSC cron.service rns=1 entry\n"
           "SC run-u42.service rns=1 entry\nSC meshforge-digest.service rns=1 entry\n"
           "SC rnsd.service rns=1 entry\n")
    obs, run = _measure(out, tmp_path=tmp_path)
    m = obs.value
    assert m.clients == [] and m.repo_only == []
    assert {"ssh.service", "cron.service", "run-u42.service",
            "meshforge-digest.service"} <= set(m.skipped)
    fed = run.call_args.kwargs["input"]
    for never in ("ssh.service", "cron.service", "run-u42.service",
                  "meshforge-digest.service", "rnsd.service"):
        assert never not in fed, f"{never} must not even be probed"


def test_blind_user_scope_is_said(tmp_path):
    obs, _ = _measure("SC lxmd.service rns=1 entry\n", user_seen=False, tmp_path=tmp_path)
    assert obs.value.user_scope_seen is False
    hold, _ = _hold_with(obs)
    assert "USER scope NOT visible" in hold.client_set


def test_missing_or_failing_probe_is_never_an_empty_set(tmp_path):
    assert isinstance(MEASURE(tmp_path / "absent.py"), Unobservable)
    probe = tmp_path / "probe.py"
    probe.write_text("")
    with patch.object(ro, "_active_units", return_value=(UNITS, True)), \
         patch.object(ro, "_cgroup_pids", return_value=[1]):
        for result in (_probe("", rc=1), _probe("SU * no-watched-dists\n")):
            with patch.object(ro.subprocess, "run", return_value=result):
                assert isinstance(MEASURE(probe), Failed)
        with patch.object(ro.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired("p", 90)):
            assert isinstance(MEASURE(probe), Failed)
    with patch.object(ro, "_active_units", return_value=([], True)):
        assert isinstance(MEASURE(probe), Unobservable)


def _hold_with(measured):
    stopped = []

    def stop(unit, user=False, timeout=None):
        stopped.append((unit, user))
        return True, "stopped"
    with patch.object(ro, "measure_rns_clients", return_value=measured), \
         patch.object(ro, "_client_active", return_value=True), \
         patch.object(ro, "stop_deadline_s", return_value=5), \
         patch.object(ro, "stop_service", side_effect=stop):
        return ro.hold_rns_clients(), stopped


def test_a_measured_entry_client_outside_the_floor_is_held():
    hold, stopped = _hold_with(Seen(ro.MeasuredClients(clients=[("new-rns-daemon", True)])))
    assert ("new-rns-daemon", True) in stopped and ("new-rns-daemon", True) in hold.stopped
    assert "beyond the curated floor" in hold.client_set


def test_repo_only_and_unprobed_are_named_not_stopped():
    m = ro.MeasuredClients(repo_only=[("meshforge-mini-dudeai", True)],
                           unprobed=[("thing", False, "probe-timeout")] +
                                    [(f"sys{i}", False, "no-access") for i in range(30)],
                           unjudged=3)
    hold, stopped = _hold_with(Seen(m))
    assert ("meshforge-mini-dudeai", True) not in stopped
    assert ("thing", False) not in stopped
    lines = hold.report_lines()
    assert any("meshforge-mini-dudeai" in ln and "NOT stopped" in ln for ln in lines)
    assert any("could not see inside thing" in ln for ln in lines)
    # 30 privilege-shaped rows collapse to ONE line (review D7)
    assert sum("not inspectable without root" in ln for ln in lines) == 1
    assert not any("sys7" in ln for ln in lines)
    assert any("3 unit row(s) could import RNS" in ln for ln in lines)


def test_unmeasurable_falls_back_to_floor_and_says_so():
    hold, stopped = _hold_with(Unobservable("substrate probe not present"))
    assert set(stopped) == set(ro.RNS_CLIENT_UNITS)
    assert hold.report_lines()[0].startswith("  RNS client set: curated floor ONLY")


def test_explicit_units_bypass_measurement():
    with patch.object(ro, "measure_rns_clients",
                      side_effect=AssertionError("must not measure")), \
         patch.object(ro, "_client_active", return_value=False):
        hold = ro.hold_rns_clients([("x", False)])
    assert hold.stopped == [] and hold.client_set == ""


def test_never_hold_list_covers_remote_access_and_the_scheduler():
    for unit in ("ssh.service", "sshd.service", "cron.service", "dbus.service",
                 "user@1000.service", "systemd-journald.service", "getty@tty1.service",
                 "run-u42.service"):
        assert unit.startswith(ro.NEVER_HOLD_PREFIXES), unit


def test_transient_and_oneshot_are_reported_not_hidden():
    """Review D re-read: skipped transient/oneshot units must be SAID."""
    m = ro.MeasuredClients(skipped=["ssh.service", "run-u42.service",
                                    "meshforge-digest.service"])
    hold, _ = _hold_with(Seen(m))
    line = [ln for ln in hold.report_lines() if "transient/oneshot" in ln]
    assert len(line) == 1 and "run-u42.service" in line[0]
    assert "ssh.service" not in line[0]           # never-hold system units: not noise
    assert "service cgroups only" in hold.client_set
    assert "login/tmux session is not seen" in hold.client_set
