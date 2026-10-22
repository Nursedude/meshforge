"""scripts/fork_roll_restart.py — after a fork roll, restart what still RUNS the old copy.

2026-10-16: the lxmf mf.11 AND mf.12 rolls each ended with honest_status's
`running substrate` leg WARNing on the same six MeshAnchor units (map,
fleet-collector, fleet-watchdog, meshanchor, mini-dudeai, meshcore-chat). The
roll's restart step found its targets with `pgrep -f <name pattern>` — a list
of process NAMES that knew the LXMRouter hosts and nothing that merely loads
lxmf from repo code. The check of record already measured the right set; the
roll did not ask it. The cure is one measurement, two consumers (hfm #5): the
restart set IS the substrate probe's SB set, so a unit that appears next month
is restarted without anyone adding its name.

Pinned here: SB of ANY basis is a target and SC is not; rnsd is NEVER
restarted (named, with the #69 order) even when behind; an unobservable
measurement restarts nothing; inactive units are never started; a restart
that does not change MainPID, or a re-measure that still shows SB, fails.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import utils.rnsd_restart_order as ro  # noqa: E402
from utils.observation import Seen, Unobservable  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "fork_roll_restart", ROOT / "scripts" / "fork_roll_restart.py")
frr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(frr)


def _u(unit, user=False, typ="simple", frag="/etc/systemd/system/x.service", pid="10"):
    return (user, {"Id": unit, "MainPID": pid, "ControlGroup": f"/x/{unit}",
                   "Type": typ, "FragmentPath": frag})


UNITS = [_u("rnsd.service"), _u("meshanchor-map.service"),
         _u("meshcore-chat.service", True), _u("meshforge-gateway.service"),
         _u("nomadnet.service", True), _u("ssh.service"),
         _u("run-u7.service", True, frag="/run/user/1000/systemd/transient/run-u7.service"),
         _u("meshforge-digest.service", True, typ="oneshot")]

BEHIND = ("SB rnsd.service rns=1.3.8+mf.5 0 entry\n"
          "SB meshanchor-map.service lxmf=1.0.1+mf.12 0 repo\n"
          "SB meshcore-chat.service lxmf=1.0.1+mf.12 0 repo\n"
          "SB nomadnet.service lxmf=1.0.1+mf.12 0 entry\n"
          "SC meshforge-gateway.service lxmf=1.0.1+mf.12 repo\n"
          "SB ssh.service lxmf=1.0.1+mf.12 0 repo\n"
          "SB run-u7.service lxmf=1.0.1+mf.12 0 entry\n"
          "SB meshforge-digest.service lxmf=1.0.1+mf.12 0 entry\n")


def _probe(stdout, rc=0):
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr="")


def _measure(out, tmp_path, units=UNITS, dists=("rns", "lxmf")):
    probe = tmp_path / "probe.py"
    probe.write_text("")
    with patch.object(ro, "_active_units", return_value=(units, True)), \
         patch.object(ro, "_cgroup_pids", return_value=[123]), \
         patch.object(ro.subprocess, "run", return_value=_probe(out)) as run:
        return frr.measure_stale(dists, probe), run


# ---- the measurement -----------------------------------------------------

def test_sb_of_any_basis_is_a_target_and_sc_is_not(tmp_path):
    obs, _ = _measure(BEHIND, tmp_path)
    assert isinstance(obs, Seen)
    assert obs.value.targets == [("meshanchor-map", False), ("meshcore-chat", True),
                                 ("nomadnet", True)]


def test_rnsd_behind_is_named_never_a_target(tmp_path):
    obs, _ = _measure(BEHIND, tmp_path)
    assert obs.value.rnsd_behind is True
    assert all(u != "rnsd" for u, _ in obs.value.targets)


def test_probe_is_asked_for_every_watched_dist_on_cgroup_pids_only(tmp_path):
    _, run = _measure("", tmp_path, dists=("rns", "lxmf"))
    argv = run.call_args.args[0]
    assert "--no-descend" in argv
    assert argv[-2:] == ["rns", "lxmf"]


def test_unprobed_units_are_named(tmp_path):
    obs, _ = _measure("SU meshanchor-map.service no-access\n", tmp_path)
    assert obs.value.unprobed == [("meshanchor-map", False, "no-access")]
    assert obs.value.targets == []


def test_measure_rns_clients_unchanged_by_the_shared_probe(tmp_path):
    """The hold path's contract: entry SB/SC → clients, repo SB/SC → named
    only (it asks "loads RNS", not "is behind" — so the SC gateway counts)."""
    probe = tmp_path / "probe.py"
    probe.write_text("")
    with patch.object(ro, "_active_units", return_value=(UNITS, True)), \
         patch.object(ro, "_cgroup_pids", return_value=[123]), \
         patch.object(ro.subprocess, "run", return_value=_probe(BEHIND)) as run:
        obs = ro._measure_rns_clients_impl(probe)
    assert run.call_args.args[0][-1] == "rns"
    assert obs.value.clients == [("nomadnet", True)]
    assert obs.value.repo_only == [("meshanchor-map", False), ("meshcore-chat", True),
                                   ("meshforge-gateway", False)]


# ---- acting on it ----------------------------------------------------------

class _Box:
    """A fake box: MainPIDs per unit, restart bumps the pid; measure is scripted."""

    def __init__(self, measures, active=None, bump=True, restart_ok=True, crash=()):
        self.crash = set(crash)       # units that crash-loop right after start
        self.shown = {}
        self.measures = list(measures)
        self.pids = {}
        self.active = active or {}
        self.bump = bump
        self.restart_ok = restart_ok
        self.restarted = []

    def measure(self, dists, probe=None):
        return self.measures.pop(0)

    def is_active(self, unit, user):
        return self.active.get(unit, True)

    def main_pid(self, unit, user):
        return self.pids.setdefault(unit, 100)

    def show(self, unit, user):
        n = self.shown[unit] = self.shown.get(unit, 0) + 1
        pid = self.pids.setdefault(unit, 100)
        if unit in self.crash and n > 1:   # the post-settle read
            return {"ActiveState": "activating", "SubState": "auto-restart",
                    "MainPID": "0", "NRestarts": "3"}
        return {"ActiveState": "active", "SubState": "running",
                "MainPID": str(pid), "NRestarts": "0"}

    def restart(self, unit, user=False, timeout=30):
        self.restarted.append((unit, user))
        if self.bump:
            self.pids[unit] = self.pids.setdefault(unit, 100) + 1
        return (self.restart_ok, "ok" if self.restart_ok else "boom")


def _stale(targets, rnsd=False, unprobed=()):
    return Seen(frr.Stale(targets=list(targets), rnsd_behind=rnsd,
                          unprobed=list(unprobed)))


def _run(box, apply=True):
    lines = []
    with patch.object(frr, "measure_stale", box.measure), \
         patch.object(frr, "_is_active", box.is_active), \
         patch.object(frr, "_main_pid", box.main_pid), \
         patch.object(frr, "_show", box.show), \
         patch.object(frr, "restart_service", box.restart):
        rc = frr.run(("rns", "lxmf"), apply=apply, out=lines.append, settle_s=0)
    return rc, "\n".join(lines)


def test_apply_restarts_each_target_in_its_own_scope_then_clean_is_rc0():
    box = _Box([_stale([("meshanchor-map", False), ("meshcore-chat", True)]),
                _stale([])])
    rc, text = _run(box)
    assert box.restarted == [("meshanchor-map", False), ("meshcore-chat", True)]
    assert rc == 0, text


def test_dry_run_restarts_nothing():
    box = _Box([_stale([("meshanchor-map", False)])])
    rc, text = _run(box, apply=False)
    assert box.restarted == []
    assert "meshanchor-map" in text
    assert rc == 0


def test_unobservable_measurement_restarts_nothing_and_is_unknown():
    box = _Box([Unobservable("probe missing")])
    rc, text = _run(box)
    assert box.restarted == []
    assert rc == 2 and "UNKNOWN" in text


def test_inactive_target_is_never_started():
    box = _Box([_stale([("meshanchor-map", False)]), _stale([])],
               active={"meshanchor-map": False})
    rc, _ = _run(box)
    assert box.restarted == []


def test_restart_that_keeps_mainpid_fails():
    box = _Box([_stale([("meshanchor-map", False)]), _stale([])], bump=False)
    rc, text = _run(box)
    assert rc == 1 and "MainPID unchanged" in text


def test_still_behind_after_restart_fails():
    box = _Box([_stale([("meshanchor-map", False)]),
                _stale([("meshanchor-map", False)])])
    rc, text = _run(box)
    assert rc == 1 and "still behind" in text


def test_rnsd_behind_is_reported_with_the_69_order_and_never_restarted():
    box = _Box([_stale([], rnsd=True), _stale([], rnsd=True)])
    rc, text = _run(box)
    assert box.restarted == []
    assert rc == 1 and "#69" in text


def test_unprobed_unit_makes_the_result_unknown():
    box = _Box([_stale([], unprobed=[("x", False, "no-access")]),
                _stale([], unprobed=[("x", False, "no-access")])])
    rc, text = _run(box)
    assert rc == 2 and "x" in text


def test_watched_dists_come_from_the_fork_pin_lines(tmp_path):
    req = tmp_path / "rns.txt"
    req.write_text("# MF-FORK-PIN rns 1.3.8+mf.5\nrns @ git+x\n"
                   "# MF-FORK-PIN lxmf 1.0.1+mf.12\nlxmf @ git+y\n")
    assert frr.watched_dists(req) == ("rns", "lxmf")


# ---- the tmux shape (live drill 2026-10-16, meshanchor-server) -------------
# meshcore-chat.service is Type=forking `tmux new-session -d -s meshcore-chat`:
# its cgroup holds ONLY the tmux server; the python (`-m utils.chat_client`)
# is a pane in app.slice/tmux-spawn-<uuid>.scope. Probing the unit's own
# cgroup saw a tmux binary and judged nothing, so the planted-stale drill
# listed 8 of the 9 units honest_status names. Both consumers (this restart
# AND the #69 hold) share the enumeration, so both were blind to it.
# Review (Fable 5.1, same day): folding EVERY tmux-spawn descendant also took
# the OPERATOR's own panes — the units use the default socket, so a
# `tmux new-session` at a terminal lands on the unit's server. Only the
# unit's OWN session's panes, as tmux itself lists them, are the unit's.

EXEC = ("{ path=/usr/bin/tmux ; argv[]=/usr/bin/tmux new-session -d -s meshcore-chat "
        "/home/u/.config/meshanchor/chat_client_wrapper.sh ; ignore_errors=no ; "
        "start_time=[x] ; pid=1 ; code=exited ; status=0 }")


def _fake_proc(tmp_path, procs, uid=1000):
    """procs: {pid: (ppid, cgroup)} → fake /proc root."""
    proc = tmp_path / "proc"
    for pid, (ppid, cg) in procs.items():
        d = proc / str(pid)
        d.mkdir(parents=True)
        (d / "stat").write_text(f"{pid} (x y) S {ppid} 1 1 0 -1\n")
        (d / "cgroup").write_text(f"0::{cg}\n")
        (d / "status").write_text(f"Name:\tx\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
    return str(proc)


APP = "/user.slice/user-1000.slice/user@1000.service/app.slice"
TREE = {10: (1, f"{APP}/meshcore-chat.service"),          # tmux server (MainPID)
        11: (10, f"{APP}/tmux-spawn-aaaa.scope"),          # the unit's pane
        12: (11, f"{APP}/tmux-spawn-aaaa.scope"),          # its child
        13: (10, f"{APP}/tmux-spawn-bbbb.scope"),          # the OPERATOR's pane
        14: (13, f"{APP}/tmux-spawn-bbbb.scope")}


def _panes(tmp_path, stdout="11\n", rc=0, euid=0, tree=TREE):
    proc = _fake_proc(tmp_path, tree)
    with patch.object(ro.subprocess, "run",
                      return_value=subprocess.CompletedProcess([], rc, stdout, "")) as run, \
         patch.object(ro.os, "geteuid", return_value=euid):
        got = ro._tmux_session_pids(EXEC, 10, ro._proc_children(proc), proc)
    return got, run


def test_tmux_unit_folds_its_own_session_panes_and_their_children(tmp_path):
    got, run = _panes(tmp_path)
    assert sorted(got) == [11, 12]
    argv = run.call_args.args[0]
    assert argv[:3] == ["tmux", "-S", "/tmp/tmux-1000/default"]
    assert argv[3:] == ["list-panes", "-s", "-t", "=meshcore-chat:", "-F", "#{pane_pid}"]


def test_operator_panes_on_the_same_server_are_never_folded(tmp_path):
    got, _ = _panes(tmp_path)
    assert 13 not in got and 14 not in got


def test_a_listed_pane_that_is_not_the_servers_child_is_refused(tmp_path):
    """tmux answered with a pid this server did not fork (reuse, wrong socket)."""
    got, _ = _panes(tmp_path, stdout="11\n999\n")
    assert sorted(got) == [11, 12]


def test_session_target_is_exact_not_a_prefix(tmp_path):
    """Second read: tmux 3.5a prefix-matches `-t =nomad` to "nomadnet"; only
    `=nomad:` is exact — else an operator session NAMED with the unit's name
    as a prefix is attributed to the unit when the unit's own session is gone."""
    _, run = _panes(tmp_path)
    t = run.call_args.args[0][run.call_args.args[0].index("-t") + 1]
    assert t.startswith("=") and t.endswith(":")


def test_options_after_the_pane_command_are_not_tmux_options(tmp_path):
    """`... -s chat python3 -m x -s 1`: the trailing `-s 1` belongs to the pane."""
    proc = _fake_proc(tmp_path, TREE)
    ex = ("{ path=/usr/bin/tmux ; argv[]=/usr/bin/tmux new-session -d -s chat "
          "python3 -m x -s 1 -L other ; ignore_errors=no ; }")
    with patch.object(ro.subprocess, "run",
                      return_value=subprocess.CompletedProcess([], 0, "11\n", "")) as run, \
         patch.object(ro.os, "geteuid", return_value=0):
        ro._tmux_session_pids(ex, 10, ro._proc_children(proc), proc)
    argv = run.call_args.args[0]
    assert argv[2] == "/tmp/tmux-1000/default" and "=chat:" in argv


def test_tmux_unanswered_is_unresolved_not_empty(tmp_path):
    got, _ = _panes(tmp_path, rc=1, stdout="")
    assert got is None


def test_non_root_other_uid_server_is_unresolved(tmp_path):
    got, run = _panes(tmp_path, euid=1001)
    assert got is None and not run.called


def test_not_a_tmux_unit_is_not_asked(tmp_path):
    proc = _fake_proc(tmp_path, TREE)
    plain = "{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -m lab.lxmf_echo ; }"
    with patch.object(ro.subprocess, "run") as run:
        assert ro._tmux_session_pids(plain, 10, ro._proc_children(proc), proc) == []
    assert not run.called


def test_probe_substrate_probes_the_panes_and_names_unresolved(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text("")
    units = [_u("meshcore-chat.service", True), _u("nomadnet.service", True)]
    units[0][1]["ExecStart"] = EXEC
    units[1][1]["ExecStart"] = EXEC.replace("meshcore-chat", "nomadnet")
    calls = []

    def fake_panes(execstart, mainpid, children, proc="/proc"):
        calls.append(execstart)
        return [77] if "meshcore-chat" in execstart else None

    with patch.object(ro, "_active_units", return_value=(units, True)), \
         patch.object(ro, "_cgroup_pids", return_value=[10]), \
         patch.object(ro, "_proc_children", return_value={}), \
         patch.object(ro, "_tmux_session_pids", side_effect=fake_panes), \
         patch.object(ro.subprocess, "run", return_value=_probe("")) as run:
        obs = ro.probe_substrate(("lxmf",), probe)
    fed = run.call_args.kwargs["input"]
    assert "user meshcore-chat.service 77" in fed
    assert obs.value.tmux_unresolved == ["nomadnet.service"]


def test_unresolved_tmux_unit_makes_the_restart_unknown():
    box = _Box([Seen(frr.Stale(tmux_unresolved=["nomadnet.service"])),
                Seen(frr.Stale(tmux_unresolved=["nomadnet.service"]))])
    rc, text = _run(box)
    assert rc == 2 and "nomadnet" in text


def test_unreadable_cgroup_is_empty_not_an_error(tmp_path):
    assert ro._cgroup_pids("/nope.service", str(tmp_path)) == []


# ---- review fixes (Fable 5.1, 2026-10-16) ------------------------------------

def test_invisible_user_scope_is_unknown_never_clean():
    """Run as root without SUDO_USER: the six user units it exists for are unseen."""
    box = _Box([Seen(frr.Stale(user_scope_seen=False)),
                Seen(frr.Stale(user_scope_seen=False))])
    rc, text = _run(box)
    assert rc == 2 and "user scope" in text


def test_measure_stale_carries_user_scope_seen(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text("")
    with patch.object(ro, "_active_units", return_value=(UNITS, False)), \
         patch.object(ro, "_cgroup_pids", return_value=[123]), \
         patch.object(ro.subprocess, "run", return_value=_probe("")):
        obs = frr.measure_stale(("lxmf",), probe)
    assert obs.value.user_scope_seen is False


@pytest.mark.parametrize("pid", ["", "0"])
def test_unobservable_mainpid_is_unknown_not_restarted(pid):
    box = _Box([_stale([("meshanchor-map", False)]), _stale([])])
    box.pids["meshanchor-map"] = pid
    box.bump = False
    rc, text = _run(box)
    assert rc == 2 and "MainPID unobservable" in text


def test_restart_waits_as_long_as_the_unit_may_take_to_stop():
    box = _Box([_stale([("meshanchor-map", False)]), _stale([])])
    seen = {}
    real = box.restart

    def restart(unit, user=False, timeout=30):
        seen["timeout"] = timeout
        return real(unit, user=user, timeout=timeout)

    box.restart = restart
    with patch.object(frr, "stop_deadline_s", return_value=310):
        _run(box)
    assert seen["timeout"] >= 310


# ── 2026-10-21: "MainPID changed" is not success ───────────────────────

def test_a_unit_that_crash_loops_after_restart_is_FAIL_not_restarted():
    """meshanchor.service passed 'MainPID changed' at 08:00:41 and crashed
    3.5 s later — 2,172 restarts before anyone looked. After the settle
    window the unit must still be active/running on the same MainPID."""
    box = _Box([_stale([("meshanchor", False)]), _stale([])], crash={"meshanchor"})
    rc, text = _run(box)
    assert rc == 1, text
    assert "did not stay up" in text and "auto-restart" in text


def test_stayed_up_names_every_way_it_failed():
    after = {"ActiveState": "active", "SubState": "running", "MainPID": "7", "NRestarts": "0"}
    ok, why = frr.stayed_up(after, dict(after, MainPID="9", NRestarts="2"))
    assert not ok and "MainPID 7 -> 9" in why and "NRestarts 0 -> 2" in why
    assert frr.stayed_up(after, dict(after)) == (True, "")
    assert frr.stayed_up(after, {})[0] is None
