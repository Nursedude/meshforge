"""scripts/rnsd_ordered_restart.py — the #69-ordered rnsd restart as a repo CLI.

2026-10-26/27: the 1.3.9 canary, the nine-box roll and the moc warm-up drill
all restarted rnsd through a throwaway wrapper in ~/.local/share — the
"things that get forgotten" class, on the one step every fork roll needs
(fork_roll_restart.py names rnsd and refuses to touch it).

Pinned here: the dry run stops NOTHING and prints the same plan the hold
uses; --apply needs root; exit 0 only when rnsd started on a NEW MainPID,
owns @rns/ BEFORE the clients start AND AFTER they are back, every held
client came back and stayed up; a proven failure is 1; anything unobservable
is 2, never 0.
"""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import utils.rnsd_restart_order as ro  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "rnsd_ordered_restart", ROOT / "scripts" / "rnsd_ordered_restart.py")
ror = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ror)

GW = ("meshforge-gateway", False)
NN = ("nomadnet", True)


def _state(pid, nrestarts="0", active="active", sub="running"):
    return {"ActiveState": active, "SubState": sub, "MainPID": str(pid),
            "NRestarts": nrestarts}


class Box:
    """A fake box: rnsd + two clients, scripted per test."""

    def __init__(self):
        self.started = True
        self.release = ro.ReleaseResult(rnsd_owns=True, rpc_ready=True,
                                        restart=ro.judge_rnsd_restart(
                                            _state(100), _state(200), _state(200)),
                                        started=["meshforge-gateway (system)",
                                                 "nomadnet (user)"])
        self.hold = ro.ClientHold(stopped=[GW, NN], client_set="measured + floor",
                                  measured=True)
        # successive _show() answers per unit: after, settled (R5: the CLI
        # no longer reads its own "before" — the library judged the restart)
        self.shows = {"rnsd": [_state(200), _state(200)],
                      "meshforge-gateway": [_state(300), _state(300)],
                      "nomadnet": [_state(400), _state(400)]}
        self.final_owns = True
        self.calls = []

    def ordered_restart_rnsd(self, *a, **k):
        self.calls.append("ordered_restart_rnsd")
        return self.started, self.release, self.hold

    def show(self, unit, user):
        q = self.shows[unit]
        return q.pop(0) if len(q) > 1 else q[0]

    def owns(self, *a, **k):
        return self.final_owns


def _apply(box, euid=0):
    lines = []
    with patch.object(ror.os, "geteuid", return_value=euid), \
         patch.object(ro, "ordered_restart_rnsd", box.ordered_restart_rnsd), \
         patch.object(ro, "rnsd_owns_listener", box.owns), \
         patch.object(ro, "listener_owners", return_value=[(200, "rnsd")]), \
         patch.object(ror.frr, "_show", box.show):
        rc = ror.apply(out=lines.append, settle_s=0)
    return rc, "\n".join(lines)


class TestApply:
    def test_unobservable_release_is_unknown_not_fail(self):
        # review finding 4: rpc_ready None / an UNVERIFIED rnsd read as rc 1
        for rel in (ro.ReleaseResult(rnsd_owns=True, rpc_ready=None),
                    ro.ReleaseResult(rnsd_owns=True, rpc_ready=True,
                                     restart=ro.judge_rnsd_restart({}, {}, {}))):
            box = Box()
            box.release = rel
            rc, text = _apply(box)
            assert rc == 2, text

    def test_proven_release_failure_is_1(self):
        box = Box()
        box.release = ro.ReleaseResult(rnsd_owns=True, rpc_ready=False)
        rc, text = _apply(box)
        assert rc == 1, text

    def test_the_whole_order_held_is_exit_0(self):
        rc, text = _apply(Box())
        assert rc == 0, text
        assert "MainPID 100 -> 200" in text
        assert text.count("MainPID") == 1, text   # R5: judged once, printed once
        assert "RESULT: OK" in text

    def test_needs_root_and_touches_nothing_without_it(self):
        box = Box()
        rc, text = _apply(box, euid=1000)
        assert rc == 2
        assert box.calls == []
        assert "root" in text

    def test_rnsd_did_not_start_is_1(self):
        box = Box()
        box.started = False
        rc, text = _apply(box)
        assert rc == 1, text

    def test_rnsd_never_owned_clients_left_stopped_is_1_and_named(self):
        box = Box()
        box.release = ro.ReleaseResult(rnsd_owns=False,
                                       left_stopped=["meshforge-gateway (system)"])
        rc, text = _apply(box)
        assert rc == 1
        assert "meshforge-gateway (system)" in text

    def test_ownership_unobservable_is_2_never_0(self):
        box = Box()
        box.release = ro.ReleaseResult(rnsd_owns=None)
        rc, _ = _apply(box)
        assert rc == 2

    def test_a_client_that_failed_to_start_is_1(self):
        box = Box()
        box.release.start_failed = ["nomadnet (user)"]
        box.release.started = ["meshforge-gateway (system)"]
        rc, _ = _apply(box)
        assert rc == 1

    def _judged(self, before, after_start, after_wait, **k):
        box = Box()
        box.release = ro.ReleaseResult(
            rnsd_owns=True, rpc_ready=True,
            restart=ro.judge_rnsd_restart(before, after_start, after_wait), **k)
        return box

    def test_rnsd_mainpid_unchanged_is_1(self):
        box = self._judged(_state(100), _state(100), _state(100),
                           left_stopped=["nomadnet (user)"])
        box.release.started = []
        rc, text = _apply(box)
        assert rc == 1
        assert "unchanged" in text

    def test_rnsd_mainpid_unobservable_is_2(self):
        box = self._judged({}, _state(200), _state(200))
        rc, text = _apply(box)
        assert rc == 2
        assert "FAIL" not in text   # R5: no second, conflicting judgement

    def test_ownership_is_re_checked_after_the_clients_are_back(self):
        """#69: a guard that checks only before the clients start verifies at
        the one moment a client cannot have squatted yet."""
        box = Box()
        box.final_owns = False
        rc, text = _apply(box)
        assert rc == 1
        assert "AFTER" in text

    def test_final_ownership_unobservable_is_2(self):
        box = Box()
        box.final_owns = None
        rc, _ = _apply(box)
        assert rc == 2

    def test_a_client_crash_looping_after_start_is_1(self):
        box = Box()
        box.shows["nomadnet"] = [_state(400), _state(401, nrestarts="1")]
        rc, text = _apply(box)
        assert rc == 1
        assert "nomadnet" in text

    def test_rnsd_dying_after_start_is_1(self):
        box = Box()
        box.shows["rnsd"] = [_state(200),   # after the restart, then settled:
                             _state(0, active="failed", sub="failed")]
        rc, _ = _apply(box)
        assert rc == 1

    def test_a_client_in_unknown_state_is_2_and_named(self):
        """Review 1: not stopped because unobservable — it may have stayed up."""
        box = Box()
        box.hold.unobservable = [("lxmd", True)]
        rc, text = _apply(box)
        assert rc == 2
        assert "lxmd" in text

    def test_unobservable_baseline_after_start_is_2_not_1(self):
        """Second read: an empty after-start snapshot is UNKNOWN, never FAIL."""
        box = Box()
        box.shows["nomadnet"] = [{}, _state(400)]
        rc, text = _apply(box)
        assert rc == 2, text
        assert "FAIL" not in text

    def test_floor_only_hold_is_2_even_when_it_worked(self):
        """Review 3: the measurement failed, only the curated floor was held."""
        box = Box()
        box.hold.measured = False
        rc, text = _apply(box)
        assert rc == 2
        assert "curated floor" in text

    def test_user_scope_blind_is_2(self):
        box = Box()
        box.hold.user_scope_seen = False
        rc, _ = _apply(box)
        assert rc == 2

    def test_rnsd_down_before_is_a_state_not_a_blind_spot(self):
        """Review 2: MainPID 0 = rnsd was not running (the repair case)."""
        box = self._judged(_state(0, active="failed", sub="failed"),
                           _state(200), _state(200),
                           started=["meshforge-gateway (system)", "nomadnet (user)"])
        rc, text = _apply(box)
        assert rc == 0, text
        assert "MainPID 0 -> 200" in text

    def test_rnsd_down_after_is_1(self):
        dead = _state(0, active="failed", sub="failed")
        box = self._judged(_state(100), dead, dead)
        box.release.started = []
        rc, _ = _apply(box)
        assert rc == 1

    def test_clients_that_cannot_be_matched_to_the_hold_are_2(self):
        """Review 6: a label-format change must not skip the stay-up check silently."""
        box = Box()
        box.release.started = ["meshforge-gateway.service (system)", "nomadnet (user)"]
        rc, _ = _apply(box)
        assert rc == 2


class TestDryRun:
    def _plan(self, owns=True, active=None, owners=((200, "rnsd"),), measured=True,
              euid=0):
        active = active or {GW: True, NN: False}
        lines = []
        hold = ro.ClientHold(client_set="measured + floor", measured=measured)
        with patch.object(ro, "plan_rns_client_hold", return_value=([GW, NN], hold)), \
             patch.object(ro, "_client_active", lambda u, user: active[(u, user)]), \
             patch.object(ro, "rnsd_owns_listener", return_value=owns), \
             patch.object(ro, "listener_owners", return_value=list(owners)), \
             patch.object(ror.frr, "_show", return_value=_state(200)), \
             patch.object(ro, "stop_service") as stop, \
             patch.object(ro, "start_service") as start, \
             patch.object(ro, "ordered_restart_rnsd") as ordered, \
             patch.object(ror.os, "geteuid", return_value=euid):
            rc = ror.plan(out=lines.append)
        assert not stop.called and not start.called and not ordered.called
        return rc, "\n".join(lines)

    def test_names_what_would_be_held_and_stops_nothing(self):
        rc, text = self._plan()
        assert rc == 0, text
        assert "would stop meshforge-gateway (system)" in text
        assert "nomadnet (user): inactive" in text

    def test_dry_run_and_hold_share_one_decision(self):
        # residue (5): plan() used to re-implement the tri-state; a changed
        # disposition must move the dry run AND the hold together.
        with patch.object(ro, "client_disposition", lambda u, user: "unobservable"):
            rc, text = self._plan()
            hold = ro.hold_rns_clients(units=[GW])
        assert rc == 2 and "would stop" not in text
        assert hold.unobservable == [GW] and not hold.stopped

    def test_a_squatter_now_is_1(self):
        rc, text = self._plan(owns=False, owners=((555, "nomadnet"),))
        assert rc == 1
        assert "nomadnet" in text

    def test_ownership_unobservable_is_2(self):
        rc, _ = self._plan(owns=None)
        assert rc == 2

    def test_a_client_in_unknown_state_is_2(self):
        rc, text = self._plan(active={GW: True, NN: None})
        assert rc == 2
        assert "UNKNOWN" in text

    def test_rnsd_state_unobservable_is_2(self):
        lines = []
        hold = ro.ClientHold(measured=True)
        with patch.object(ro, "plan_rns_client_hold", return_value=([], hold)), \
             patch.object(ro, "rnsd_owns_listener", return_value=True), \
             patch.object(ro, "listener_owners", return_value=[(200, "rnsd")]), \
             patch.object(ror.frr, "_show", return_value={}), \
             patch.object(ror.os, "geteuid", return_value=0):
            assert ror.plan(out=lines.append) == 2

    def test_not_root_is_2(self):
        """Review 4: a non-root plan cannot see other uids' clients."""
        rc, _ = self._plan(euid=1000)
        assert rc == 2

    def test_floor_only_plan_is_2(self):
        rc, text = self._plan(measured=False)
        assert rc == 2
        assert "curated floor" in text


def test_rnsd_stop_waits_the_units_own_timeout():
    """Review 5: the rnsd stop uses the F1 deadline, not the 30 s client default."""
    calls = []
    with patch.object(ro, "hold_rns_clients", return_value=ro.ClientHold()), \
         patch.object(ro, "stop_deadline_s", lambda unit, user: 123), \
         patch.object(ro, "stop_service",
                      lambda unit, **k: calls.append((unit, k)) or (True, "ok")), \
         patch.object(ro, "start_service", return_value=(True, "ok")), \
         patch.object(ro, "release_rns_clients",
                      return_value=ro.ReleaseResult(rnsd_owns=True)) as release, \
         patch.object(ro, "show_unit", return_value={"MainPID": "7"}):  # never the host's rnsd
        ro.ordered_restart_rnsd()
    assert calls == [("rnsd", {"timeout": 123})]
    # F2: the baseline taken BEFORE the stop rides into release, which judges
    assert release.call_args.kwargs["rnsd_before"] == {"MainPID": "7"}


def test_main_defaults_to_the_dry_run():
    with patch.object(ror, "plan", return_value=0) as p, \
         patch.object(ror, "apply") as a:
        assert ror.main([]) == 0
    assert p.called and not a.called
