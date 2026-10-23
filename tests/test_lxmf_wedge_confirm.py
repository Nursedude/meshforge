"""DET-WEDGE (ROADMAP NEXT, 2026-10-22): ``probe_lxmf_process_wedge`` called a
lab load-run's own transient ``unix_wait_for_peer`` an rnsd wedge.

MEASURED (mini delta review, 2026-10-22): moc logged 58 ``main_thread_wedge``
fires on ``lab.lxmf_multi_user_synth``, ALL at minute :08 — one minute after
``meshforge-synth-soak.timer`` (``*:07``) — each active ~30 s and clearing on
its own. moc3's ``lab.lxmf_tracer`` / ``lab.lxmf_echo`` fires clustered at
minute :x0, the 10-min tracer timer. The probe judged ONE snapshot of the
stacks, and its cure text said "restart rnsd.service" — the #69 @rns
ownership-race trigger, for a condition that was already gone.

Same cure shape as the RNS-RPC sibling (``eb8a6529``): a wedge PERSISTS, a
load-run's connect backpressure passes. So the same PROCESS (pid — not the
thread: a wedged client may retry on a fresh one) must have a blocked thread
on N consecutive ticks before it is a wedge; until then the class reads
``indeterminate`` (never ``clean``), and the cure text says to confirm
before touching rnsd.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import utils.watchdog_probes_rns as wpr  # noqa: E402
from utils.watchdog_probe_core import (  # noqa: E402
    collect_dispositions, reset_dispositions,
)

_WEDGE_STACK = """\
[<0>] do_sys_poll+0x3b8/0x540
[<0>] unix_wait_for_peer+0x80/0xd0
[<0>] unix_stream_connect+0xa0/0x4f0
"""

_HEALTHY_STACK = """\
[<0>] do_sys_poll+0x3b8/0x540
[<0>] __arm64_sys_ppoll+0xb4/0x148
"""

_SYNTH = b"/usr/bin/python3\x00-m\x00lab.lxmf_multi_user_synth\x00"


def _lay(root: Path, pid: int, cmdline: bytes, threads: dict) -> None:
    """Write /proc/<pid>/{cmdline,task/<tid>/stack}; replaces any prior tree."""
    pid_dir = root / str(pid)
    pid_dir.mkdir(parents=True, exist_ok=True)
    (pid_dir / "cmdline").write_bytes(cmdline)
    for tid, stack in threads.items():
        tdir = pid_dir / "task" / str(tid)
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / "stack").write_text(stack)


def _wipe(root: Path) -> None:
    import shutil
    for child in root.iterdir():
        shutil.rmtree(child)


ENV = "MESHFORGE_LXMF_WEDGE_CONFIRM_TICKS"


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.delenv("MESHFORGE_RNS_RPC_CONFIRM_TICKS", raising=False)
    wpr.reset_lxmf_wedge_streaks()
    reset_dispositions()
    yield
    wpr.reset_lxmf_wedge_streaks()


def _probe(root: Path):
    return wpr.probe_lxmf_process_wedge(proc_root=str(root))


def test_one_tick_block_is_not_a_wedge_and_reads_indeterminate(tmp_path):
    """The measured case: blocked for ONE tick, gone the next. Never fires,
    and the blocked tick is a witnessed blind moment, not health."""
    needed = wpr._lxmf_wedge_confirm_ticks()
    _lay(tmp_path, 900, _SYNTH, {900: _HEALTHY_STACK, 901: _WEDGE_STACK})
    assert _probe(tmp_path) == []
    noted = collect_dispositions()["main_thread_wedge"]
    assert noted["disp"] == "indeterminate"
    assert f"pid=900 tid=901 'unix_wait_for_peer' 1/{needed}" in noted["reason"]

    _lay(tmp_path, 900, _SYNTH, {900: _HEALTHY_STACK, 901: _HEALTHY_STACK})
    assert _probe(tmp_path) == []


def test_same_process_held_for_n_ticks_fires_on_the_nth(tmp_path):
    needed = wpr._lxmf_wedge_confirm_ticks()
    assert needed >= 2, "the default must require confirmation"
    _lay(tmp_path, 900, _SYNTH, {900: _HEALTHY_STACK, 901: _WEDGE_STACK})
    for _ in range(needed - 1):
        assert _probe(tmp_path) == []
    signals = _probe(tmp_path)
    assert len(signals) == 1
    assert signals[0].subject == "lab.lxmf_multi_user_synth"
    assert signals[0].extra["tid"] == 901
    assert signals[0].extra["consecutive_ticks"] == needed


def test_held_wedge_on_a_rotating_thread_still_fires(tmp_path):
    """A wedged client that retries on a fresh thread each tick is still ONE
    wedged process. A per-tid streak would never accumulate (review 10-22)."""
    needed = wpr._lxmf_wedge_confirm_ticks()
    signals = []
    for i in range(needed):
        _wipe(tmp_path)
        _lay(tmp_path, 900, _SYNTH, {900: _HEALTHY_STACK, 950 + i: _WEDGE_STACK})
        signals = _probe(tmp_path)
    assert len(signals) == 1
    assert signals[0].extra["tid"] == 950 + needed - 1


def test_new_run_each_tick_never_accumulates(tmp_path):
    """Short-lived load runs: a NEW pid blocked each tick is backpressure on
    successive runs, not one wedged process. The streak is per pid."""
    needed = wpr._lxmf_wedge_confirm_ticks()
    for i in range(needed + 2):
        _wipe(tmp_path)
        pid = 1000 + 10 * i
        _lay(tmp_path, pid, _SYNTH, {pid: _WEDGE_STACK})
        assert _probe(tmp_path) == []


def test_a_healthy_tick_rearms_the_guard(tmp_path):
    """Falsifiable both ways: a healthy tick resets the count, and the guard
    then fires again after exactly N fresh blocked ticks."""
    needed = wpr._lxmf_wedge_confirm_ticks()
    _lay(tmp_path, 900, _SYNTH, {900: _WEDGE_STACK})
    for _ in range(needed - 1):
        assert _probe(tmp_path) == []
    _lay(tmp_path, 900, _SYNTH, {900: _HEALTHY_STACK})
    assert _probe(tmp_path) == []
    _lay(tmp_path, 900, _SYNTH, {900: _WEDGE_STACK})
    for _ in range(needed - 1):
        assert _probe(tmp_path) == []
    assert len(_probe(tmp_path)) == 1


def test_every_pending_process_is_named_in_the_witness(tmp_path):
    """Worst-wins keeps one reason, so the probe aggregates: a second blocked
    process (possibly the real wedge) must not vanish from it (review 10-22)."""
    _lay(tmp_path, 900, _SYNTH, {900: _WEDGE_STACK})
    _lay(tmp_path, 700, b"python3\x00-m\x00lab.lxmf_echo\x00", {700: _WEDGE_STACK})
    assert _probe(tmp_path) == []
    reason = collect_dispositions()["main_thread_wedge"]["reason"]
    assert "pid=900" in reason and "pid=700" in reason


def test_own_knob_of_one_restores_single_tick_firing(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV, "1")
    _lay(tmp_path, 900, _SYNTH, {900: _WEDGE_STACK})
    assert len(_probe(tmp_path)) == 1


def test_rpc_knob_does_not_retune_the_wedge_confirmation(tmp_path, monkeypatch):
    """Different quantities, different knobs (review 10-22): an operator
    speeding up the RPC detector must not bring back single-snapshot paging."""
    monkeypatch.setenv("MESHFORGE_RNS_RPC_CONFIRM_TICKS", "1")
    _lay(tmp_path, 900, _SYNTH, {900: _WEDGE_STACK})
    assert _probe(tmp_path) == []


@pytest.mark.parametrize("raw", ["not-a-number", "0", "-2"])
def test_bad_knob_values_fall_back_to_the_default(monkeypatch, raw):
    monkeypatch.setenv(ENV, raw)
    assert wpr._lxmf_wedge_confirm_ticks() == wpr._DEFAULT_LXMF_WEDGE_CONFIRM_TICKS


def test_cure_text_confirms_before_it_restarts_rnsd(tmp_path, monkeypatch):
    """The page must not lead with the #69 race trigger."""
    monkeypatch.setenv(ENV, "1")
    _lay(tmp_path, 900, _SYNTH, {900: _WEDGE_STACK})
    detail = _probe(tmp_path)[0].detail
    assert "CONFIRM BEFORE ACTING" in detail
    assert "Recovery: restart rnsd" not in detail
    confirm = detail.index("CONFIRM BEFORE ACTING")
    assert "restart rnsd" not in detail[:confirm].lower()
    assert "--since" in detail and "sudo journalctl" in detail


def test_unit_leg_shares_the_confirm_first_cure_text(tmp_path):
    """The unit leg pages the same class; its old text led with 'restart
    rnsd.service' (review 10-22). One text for both legs (hfm #5)."""
    d = tmp_path / "4242" / "task" / "4242"
    d.mkdir(parents=True)
    (d / "stack").write_text(_WEDGE_STACK)
    sig = wpr.probe_main_thread_wedge("meshforge-echo.service", pid=4242,
                                      proc_root=str(tmp_path))
    assert sig is not None
    confirm = sig.detail.index("CONFIRM BEFORE ACTING")
    assert "restart rnsd" not in sig.detail[:confirm].lower()
    assert "restart only meshforge-echo.service" in sig.detail


def test_reset_is_reachable_through_the_facade():
    from utils.watchdog_probes import reset_lxmf_wedge_streaks
    assert reset_lxmf_wedge_streaks is wpr.reset_lxmf_wedge_streaks


def test_default_clears_the_longest_measured_transient():
    """Re-review 2026-10-22, MEASURED: moc3 lab.lxmf_echo stayed blocked
    105.8 s (~4 ticks at the ~30 s cadence) while the tracer drove it. Keyed
    per pid, a default of 3 would page on that; the default must exceed it."""
    assert wpr._lxmf_wedge_confirm_ticks() >= 5


def test_four_tick_transient_on_a_long_lived_daemon_does_not_fire(tmp_path):
    """The measured moc3 case itself: one long-lived echo pid, a different
    worker caught blocked on each of 4 consecutive ticks, then healthy."""
    echo = b"python3\x00-m\x00lab.lxmf_echo\x00"
    for i in range(4):
        _wipe(tmp_path)
        _lay(tmp_path, 700, echo, {700: _HEALTHY_STACK, 760 + i: _WEDGE_STACK})
        assert _probe(tmp_path) == []
    _wipe(tmp_path)
    _lay(tmp_path, 700, echo, {700: _HEALTHY_STACK})
    assert _probe(tmp_path) == []


def test_unreadable_stack_holds_the_streak_instead_of_resetting(tmp_path):
    """hfm #2: not being able to look is not a healthy tick. A transient read
    failure mid-wedge must not reset the count (re-review 2026-10-22)."""
    needed = wpr._lxmf_wedge_confirm_ticks()
    _lay(tmp_path, 900, _SYNTH, {900: _WEDGE_STACK})
    for _ in range(needed - 1):
        assert _probe(tmp_path) == []
    stack = tmp_path / "900" / "task" / "900" / "stack"
    stack.unlink()                      # the read fails this tick
    assert _probe(tmp_path) == []
    assert wpr._lxmf_wedge_streaks[("lab.lxmf_multi_user_synth", 900)] == needed - 1
    stack.write_text(_WEDGE_STACK)      # readable again, still wedged
    assert len(_probe(tmp_path)) == 1


def test_a_raise_mid_walk_cannot_carry_a_stale_streak(tmp_path, monkeypatch):
    """A probe that raises must not leave last tick's streaks behind to make
    a skipped tick count as consecutive (re-review 2026-10-22)."""
    _lay(tmp_path, 900, _SYNTH, {900: _WEDGE_STACK})
    assert _probe(tmp_path) == []
    assert wpr._lxmf_wedge_streaks

    def boom(*_a, **_k):
        raise RuntimeError("mid-walk failure")

    monkeypatch.setattr(wpr, "_scan_pid_task_stacks", boom)
    with pytest.raises(RuntimeError):
        _probe(tmp_path)
    assert wpr._lxmf_wedge_streaks == {}


def test_cure_text_does_not_treat_rnstatus_124_as_proof(tmp_path, monkeypatch):
    """persistent_issues 2026-09-08: rnstatus wall time measures CPU headroom.
    The page must say so, and lead with the gateway's own RPC witness."""
    monkeypatch.setenv(ENV, "1")
    _lay(tmp_path, 900, _SYNTH, {900: _WEDGE_STACK})
    detail = _probe(tmp_path)[0].detail
    assert "CPU starvation" in detail
    assert "124 = rnsd RPC wedged" not in detail
    assert detail.index("rpc\\[rnsd") < detail.index("rnstatus")


def test_both_knobs_share_one_parser():
    assert wpr._lxmf_wedge_confirm_ticks.__code__.co_names.count("_env_confirm_ticks") == 1
    assert "_env_confirm_ticks" in wpr._rpc_confirm_ticks.__code__.co_names
