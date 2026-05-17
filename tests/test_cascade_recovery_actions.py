"""Tests for the Fork B cascade recovery actor.

The actor sits between wedge_events (Fork A's bonded RPC watchdog
publishes here) and the operator-visible recovery surface
(/fleet/cascade.recovery + audit log). These tests pin:

* The subscriber contract — start()/stop() wiring against wedge_events.
* The log-only default — no side-effects without operator opt-in.
* Dedup, cooldown, gate semantics for the non-log action path.
* The systemctl_restart_rnsd action's subprocess + audit-row shape.
* Audit-log JSON-line format (the durable cross-process record).

Subprocess + cascade-state seams are injected so we never exec real
systemctl, never depend on a running CascadeDetector daemon, and don't
need real wall-clock time.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from utils import wedge_events
from utils import cascade_recovery_actions
from utils.cascade_recovery_actions import (
    ACTION_LOG_ONLY,
    ACTION_RESTART_RNSD,
    DEFAULT_COOLDOWN_S,
    DEFAULT_DEDUP_WINDOW_S,
    AuditEntry,
    CascadeRecoveryActor,
    default_audit_log_path,
    get_singleton,
)
from utils.wedge_events import WedgeEvent


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_module_state():
    """wedge_events and the actor singleton are module-level globals;
    reset both so test cases don't leak subscribers or state."""
    wedge_events._reset_for_tests()
    cascade_recovery_actions._reset_singleton_for_tests()
    yield
    wedge_events._reset_for_tests()
    cascade_recovery_actions._reset_singleton_for_tests()


@pytest.fixture
def tmp_audit_log(tmp_path) -> Path:
    """Per-test audit log path inside the pytest tmp_path tree."""
    return tmp_path / "cascade_recovery.log"


@pytest.fixture
def clock_at(monkeypatch):
    """Mutable clock for deterministic cooldown/dedup tests.

    Usage:
        def test_x(clock_at):
            clk = clock_at(1000.0)
            actor = CascadeRecoveryActor(..., clock=clk.now)
            clk.tick(60)
    """
    class Clk:
        def __init__(self, t0: float):
            self.t = t0
        def now(self) -> float:
            return self.t
        def tick(self, dt: float) -> None:
            self.t += dt

    def _factory(t0: float = 1_700_000_000.0) -> Clk:
        return Clk(t0)
    return _factory


def _make_event(
    label: str = "rnsd.has_path",
    target: str = "abcd1234",
    ts: float = 1.0,
    timeout_s: float = 3.0,
    source: str = "gateway.rns",
    note: str = "",
) -> WedgeEvent:
    return WedgeEvent(
        ts=ts, source=source, label=label, target=target,
        timeout_s=timeout_s, note=note,
    )


def _read_audit_lines(path: Path) -> List[Dict[str, Any]]:
    """Parse the audit log into a list of dicts (newest last)."""
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


# ── Construction + defaults ──────────────────────────────────────────


class TestConstruction:
    def test_default_action_is_log_only(self, tmp_audit_log):
        actor = CascadeRecoveryActor(audit_log_path=tmp_audit_log)
        assert actor._action == ACTION_LOG_ONLY

    def test_unknown_action_falls_back_to_log_only(self, tmp_audit_log, caplog):
        actor = CascadeRecoveryActor(
            action="reboot_universe",  # not in KNOWN_ACTIONS
            audit_log_path=tmp_audit_log,
        )
        assert actor._action == ACTION_LOG_ONLY

    def test_default_cooldown_matches_module_constant(self, tmp_audit_log):
        actor = CascadeRecoveryActor(audit_log_path=tmp_audit_log)
        assert actor._cooldown_s == DEFAULT_COOLDOWN_S

    def test_default_dedup_matches_module_constant(self, tmp_audit_log):
        actor = CascadeRecoveryActor(audit_log_path=tmp_audit_log)
        assert actor._dedup_window_s == DEFAULT_DEDUP_WINDOW_S

    def test_snapshot_has_expected_keys(self, tmp_audit_log):
        actor = CascadeRecoveryActor(audit_log_path=tmp_audit_log)
        snap = actor.snapshot()
        for key in (
            "started", "action", "cooldown_s", "dedup_window_s",
            "audit_log_path", "cooldown_remaining_s",
            "last_action_dispatch_ts", "stats",
        ):
            assert key in snap, f"missing key {key}"
        for key in (
            "events_received", "actions_dispatched", "actions_succeeded",
            "actions_failed", "cooldown_skipped", "dedup_skipped",
            "gate_not_satisfied",
        ):
            assert key in snap["stats"]


# ── Subscriber wiring ────────────────────────────────────────────────


class TestSubscriberWiring:
    def test_start_subscribes_to_wedge_events(self, tmp_audit_log):
        actor = CascadeRecoveryActor(audit_log_path=tmp_audit_log)
        actor.start()
        wedge_events.publish(_make_event())
        # An audit-log row indicates the subscriber received the event.
        assert len(_read_audit_lines(tmp_audit_log)) == 1

    def test_start_is_idempotent(self, tmp_audit_log):
        """Second start() must not double-subscribe (one event → one row)."""
        actor = CascadeRecoveryActor(audit_log_path=tmp_audit_log)
        actor.start()
        actor.start()
        wedge_events.publish(_make_event())
        assert len(_read_audit_lines(tmp_audit_log)) == 1

    def test_stop_unsubscribes(self, tmp_audit_log):
        actor = CascadeRecoveryActor(audit_log_path=tmp_audit_log)
        actor.start()
        actor.stop()
        wedge_events.publish(_make_event())
        assert len(_read_audit_lines(tmp_audit_log)) == 0

    def test_stop_without_start_is_noop(self, tmp_audit_log):
        actor = CascadeRecoveryActor(audit_log_path=tmp_audit_log)
        actor.stop()  # no raise


# ── Log-only action ──────────────────────────────────────────────────


class TestLogOnlyAction:
    def test_outcome_is_logged(self, tmp_audit_log):
        actor = CascadeRecoveryActor(audit_log_path=tmp_audit_log)
        actor.start()
        wedge_events.publish(_make_event(label="rnsd.has_path"))
        rows = _read_audit_lines(tmp_audit_log)
        assert len(rows) == 1
        assert rows[0]["action"] == ACTION_LOG_ONLY
        assert rows[0]["outcome"] == "logged"

    def test_no_subprocess_called(self, tmp_audit_log):
        """The log-only path must never call the subprocess seam."""
        called: List[Any] = []

        def fake_runner(cmd, timeout_s):
            called.append((cmd, timeout_s))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        actor = CascadeRecoveryActor(
            audit_log_path=tmp_audit_log,
            subprocess_runner=fake_runner,
        )
        actor.start()
        wedge_events.publish(_make_event())
        assert called == []

    def test_stats_counters_bump(self, tmp_audit_log):
        actor = CascadeRecoveryActor(audit_log_path=tmp_audit_log)
        actor.start()
        wedge_events.publish(_make_event())
        snap = actor.snapshot()
        assert snap["stats"]["events_received"] == 1
        assert snap["stats"]["actions_dispatched"] == 0

    def test_audit_row_contains_full_event(self, tmp_audit_log):
        actor = CascadeRecoveryActor(audit_log_path=tmp_audit_log)
        actor.start()
        wedge_events.publish(_make_event(
            label="rnsd.handle_outbound",
            target="deadbeef",
            timeout_s=15.0,
            note="link timeout",
        ))
        row = _read_audit_lines(tmp_audit_log)[0]
        assert row["event"]["label"] == "rnsd.handle_outbound"
        assert row["event"]["target"] == "deadbeef"
        assert row["event"]["timeout_s"] == 15.0
        assert row["event"]["note"] == "link timeout"


# ── Dedup ────────────────────────────────────────────────────────────


class TestDedup:
    def test_same_label_target_within_window_collapses(
        self, tmp_audit_log, clock_at
    ):
        clk = clock_at()
        actor = CascadeRecoveryActor(
            audit_log_path=tmp_audit_log,
            dedup_window_s=60.0,
            clock=clk.now,
        )
        actor.start()
        # First fire — fresh, outcome=logged.
        wedge_events.publish(_make_event(label="L", target="T"))
        clk.tick(10)  # well inside the window
        wedge_events.publish(_make_event(label="L", target="T"))
        rows = _read_audit_lines(tmp_audit_log)
        outcomes = [r["outcome"] for r in rows]
        assert outcomes == ["logged", "dedup_skipped"]

    def test_different_target_is_not_deduped(self, tmp_audit_log, clock_at):
        clk = clock_at()
        actor = CascadeRecoveryActor(
            audit_log_path=tmp_audit_log,
            dedup_window_s=60.0,
            clock=clk.now,
        )
        actor.start()
        wedge_events.publish(_make_event(label="L", target="A"))
        wedge_events.publish(_make_event(label="L", target="B"))
        rows = _read_audit_lines(tmp_audit_log)
        assert [r["outcome"] for r in rows] == ["logged", "logged"]

    def test_different_label_is_not_deduped(self, tmp_audit_log, clock_at):
        clk = clock_at()
        actor = CascadeRecoveryActor(
            audit_log_path=tmp_audit_log,
            dedup_window_s=60.0,
            clock=clk.now,
        )
        actor.start()
        wedge_events.publish(_make_event(label="L1", target="T"))
        wedge_events.publish(_make_event(label="L2", target="T"))
        rows = _read_audit_lines(tmp_audit_log)
        assert [r["outcome"] for r in rows] == ["logged", "logged"]

    def test_after_window_dedup_clears(self, tmp_audit_log, clock_at):
        clk = clock_at()
        actor = CascadeRecoveryActor(
            audit_log_path=tmp_audit_log,
            dedup_window_s=60.0,
            clock=clk.now,
        )
        actor.start()
        wedge_events.publish(_make_event(label="L", target="T"))
        clk.tick(61)  # past the window
        wedge_events.publish(_make_event(label="L", target="T"))
        rows = _read_audit_lines(tmp_audit_log)
        assert [r["outcome"] for r in rows] == ["logged", "logged"]

    def test_dedup_window_slides_with_each_dedup_hit(
        self, tmp_audit_log, clock_at
    ):
        """A sustained wedge that fires every dedup_window/2 must keep
        collapsing — i.e. the window slides forward, not anchored to
        the first fire. Important so a 5-minute outage doesn't quietly
        cross the 1-minute window and start re-firing."""
        clk = clock_at()
        actor = CascadeRecoveryActor(
            audit_log_path=tmp_audit_log,
            dedup_window_s=60.0,
            clock=clk.now,
        )
        actor.start()
        wedge_events.publish(_make_event(label="L", target="T"))  # logged
        clk.tick(30)
        wedge_events.publish(_make_event(label="L", target="T"))  # dedup
        clk.tick(30)
        wedge_events.publish(_make_event(label="L", target="T"))  # dedup (slides)
        clk.tick(30)
        wedge_events.publish(_make_event(label="L", target="T"))  # dedup (slides)
        outcomes = [r["outcome"] for r in _read_audit_lines(tmp_audit_log)]
        assert outcomes == [
            "logged", "dedup_skipped", "dedup_skipped", "dedup_skipped",
        ]


# ── Cooldown (non-log action) ────────────────────────────────────────


class TestCooldown:
    def test_first_event_dispatches(self, tmp_audit_log, clock_at):
        """First wedge of the day → dispatch. Captures the dispatched
        audit row; the worker thread writes succeeded after."""
        clk = clock_at()
        ran: List[List[str]] = []

        def fake_runner(cmd, timeout_s):
            ran.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        actor = CascadeRecoveryActor(
            action=ACTION_RESTART_RNSD,
            audit_log_path=tmp_audit_log,
            cooldown_s=300.0,
            subprocess_runner=fake_runner,
            gate_requires_detector=False,
            clock=clk.now,
        )
        actor.start()
        wedge_events.publish(_make_event())
        # Worker thread is daemon — give it a moment to write the
        # succeeded row. Sub-second on a healthy system.
        deadline = time.time() + 2.0
        while time.time() < deadline:
            rows = _read_audit_lines(tmp_audit_log)
            if any(r["outcome"] == "succeeded" for r in rows):
                break
            time.sleep(0.01)
        outcomes = [r["outcome"] for r in _read_audit_lines(tmp_audit_log)]
        assert "dispatched" in outcomes
        assert "succeeded" in outcomes
        assert len(ran) == 1
        assert ran[0][:4] == ["sudo", "-n", "systemctl", "restart"]

    def test_second_event_within_cooldown_is_skipped(
        self, tmp_audit_log, clock_at
    ):
        clk = clock_at()

        def fake_runner(cmd, timeout_s):
            return subprocess.CompletedProcess(cmd, 0, "", "")

        actor = CascadeRecoveryActor(
            action=ACTION_RESTART_RNSD,
            audit_log_path=tmp_audit_log,
            cooldown_s=300.0,
            dedup_window_s=1.0,  # tight so dedup doesn't shadow cooldown
            subprocess_runner=fake_runner,
            gate_requires_detector=False,
            clock=clk.now,
        )
        actor.start()
        wedge_events.publish(_make_event(label="L", target="T1"))
        clk.tick(60.0)  # well past dedup, well inside cooldown
        wedge_events.publish(_make_event(label="L", target="T2"))
        outcomes = [r["outcome"] for r in _read_audit_lines(tmp_audit_log)]
        assert "cooldown_skipped" in outcomes
        snap = actor.snapshot()
        assert snap["stats"]["cooldown_skipped"] == 1

    def test_after_cooldown_dispatches_again(self, tmp_audit_log, clock_at):
        clk = clock_at()
        runs: List[Any] = []

        def fake_runner(cmd, timeout_s):
            runs.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        actor = CascadeRecoveryActor(
            action=ACTION_RESTART_RNSD,
            audit_log_path=tmp_audit_log,
            cooldown_s=300.0,
            dedup_window_s=1.0,
            subprocess_runner=fake_runner,
            gate_requires_detector=False,
            clock=clk.now,
        )
        actor.start()
        wedge_events.publish(_make_event(label="L", target="T1"))
        # Wait briefly for first dispatch worker to finish so a second
        # dispatch isn't racing the first.
        deadline = time.time() + 2.0
        while time.time() < deadline and not runs:
            time.sleep(0.01)
        clk.tick(301.0)  # past cooldown
        wedge_events.publish(_make_event(label="L", target="T2"))
        deadline = time.time() + 2.0
        while time.time() < deadline and len(runs) < 2:
            time.sleep(0.01)
        assert len(runs) == 2

    def test_cooldown_remaining_in_snapshot_decreases(
        self, tmp_audit_log, clock_at
    ):
        clk = clock_at()

        def fake_runner(cmd, timeout_s):
            return subprocess.CompletedProcess(cmd, 0, "", "")

        actor = CascadeRecoveryActor(
            action=ACTION_RESTART_RNSD,
            audit_log_path=tmp_audit_log,
            cooldown_s=300.0,
            dedup_window_s=1.0,
            subprocess_runner=fake_runner,
            gate_requires_detector=False,
            clock=clk.now,
        )
        actor.start()
        wedge_events.publish(_make_event())
        s1 = actor.snapshot()["cooldown_remaining_s"]
        clk.tick(60.0)
        s2 = actor.snapshot()["cooldown_remaining_s"]
        assert s1 > s2 >= 0
        clk.tick(241.0)
        s3 = actor.snapshot()["cooldown_remaining_s"]
        assert s3 == 0.0


# ── Cascade detector gate ────────────────────────────────────────────


class TestCascadeGate:
    def test_no_fingerprint_at_pre_fail_blocks_action(
        self, tmp_audit_log, clock_at
    ):
        clk = clock_at()
        ran: List[Any] = []

        def fake_runner(cmd, timeout_s):
            ran.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        def empty_state():
            return {"fingerprints": [], "summary": {}, "clean": []}

        actor = CascadeRecoveryActor(
            action=ACTION_RESTART_RNSD,
            audit_log_path=tmp_audit_log,
            cooldown_s=300.0,
            dedup_window_s=1.0,
            subprocess_runner=fake_runner,
            cascade_state_getter=empty_state,
            gate_requires_detector=True,
            clock=clk.now,
        )
        actor.start()
        wedge_events.publish(_make_event())
        outcomes = [r["outcome"] for r in _read_audit_lines(tmp_audit_log)]
        assert "gate_not_satisfied" in outcomes
        assert ran == []  # action never ran
        snap = actor.snapshot()
        assert snap["stats"]["gate_not_satisfied"] == 1

    def test_pre_fail_fingerprint_lets_action_through(
        self, tmp_audit_log, clock_at
    ):
        clk = clock_at()
        ran: List[Any] = []

        def fake_runner(cmd, timeout_s):
            ran.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        def pre_fail_state():
            return {
                "fingerprints": [{
                    "name": "rns_rpc_wedge",
                    "state": "pre_fail",
                    "evidence": "1 SYN-SENT",
                }],
                "summary": {"pre_fail": 1},
                "clean": ["tracer_stale_fire"],
            }

        actor = CascadeRecoveryActor(
            action=ACTION_RESTART_RNSD,
            audit_log_path=tmp_audit_log,
            cooldown_s=300.0,
            dedup_window_s=1.0,
            subprocess_runner=fake_runner,
            cascade_state_getter=pre_fail_state,
            gate_requires_detector=True,
            clock=clk.now,
        )
        actor.start()
        wedge_events.publish(_make_event())
        deadline = time.time() + 2.0
        while time.time() < deadline and not ran:
            time.sleep(0.01)
        assert len(ran) == 1

    def test_wedged_state_also_satisfies_gate(self, tmp_audit_log, clock_at):
        """A fingerprint already escalated past pre_fail still gates True
        — recovery is appropriate, not blocked because the state moved on."""
        clk = clock_at()
        ran: List[Any] = []

        def fake_runner(cmd, timeout_s):
            ran.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        def wedged_state():
            return {
                "fingerprints": [{"name": "any", "state": "wedged"}],
            }

        actor = CascadeRecoveryActor(
            action=ACTION_RESTART_RNSD,
            audit_log_path=tmp_audit_log,
            cooldown_s=300.0,
            dedup_window_s=1.0,
            subprocess_runner=fake_runner,
            cascade_state_getter=wedged_state,
            gate_requires_detector=True,
            clock=clk.now,
        )
        actor.start()
        wedge_events.publish(_make_event())
        deadline = time.time() + 2.0
        while time.time() < deadline and not ran:
            time.sleep(0.01)
        assert len(ran) == 1

    def test_suspected_state_does_not_satisfy_gate(
        self, tmp_audit_log, clock_at
    ):
        """One hit (state=suspected) is too soft a signal to justify
        a restart; require hysteresis-confirmed pre_fail or worse."""
        clk = clock_at()
        ran: List[Any] = []

        def fake_runner(cmd, timeout_s):
            ran.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        def suspected_state():
            return {"fingerprints": [{"name": "any", "state": "suspected"}]}

        actor = CascadeRecoveryActor(
            action=ACTION_RESTART_RNSD,
            audit_log_path=tmp_audit_log,
            cooldown_s=300.0,
            dedup_window_s=1.0,
            subprocess_runner=fake_runner,
            cascade_state_getter=suspected_state,
            gate_requires_detector=True,
            clock=clk.now,
        )
        actor.start()
        wedge_events.publish(_make_event())
        outcomes = [r["outcome"] for r in _read_audit_lines(tmp_audit_log)]
        assert "gate_not_satisfied" in outcomes
        assert ran == []

    def test_gate_disabled_bypasses_detector(self, tmp_audit_log, clock_at):
        clk = clock_at()
        ran: List[Any] = []

        def fake_runner(cmd, timeout_s):
            ran.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        def crashing_state():
            raise RuntimeError("detector unavailable")

        actor = CascadeRecoveryActor(
            action=ACTION_RESTART_RNSD,
            audit_log_path=tmp_audit_log,
            cooldown_s=300.0,
            dedup_window_s=1.0,
            subprocess_runner=fake_runner,
            cascade_state_getter=crashing_state,
            gate_requires_detector=False,  # ← bypass
            clock=clk.now,
        )
        actor.start()
        wedge_events.publish(_make_event())
        deadline = time.time() + 2.0
        while time.time() < deadline and not ran:
            time.sleep(0.01)
        assert len(ran) == 1

    def test_cascade_state_getter_raising_means_gate_not_satisfied(
        self, tmp_audit_log, clock_at
    ):
        """Defensive: if the cascade detector singleton crashes on
        snapshot(), the actor must fail closed (no action) rather than
        treat the missing snapshot as permission."""
        clk = clock_at()
        ran: List[Any] = []

        def fake_runner(cmd, timeout_s):
            ran.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        def crashing_state():
            raise RuntimeError("boom")

        actor = CascadeRecoveryActor(
            action=ACTION_RESTART_RNSD,
            audit_log_path=tmp_audit_log,
            cooldown_s=300.0,
            dedup_window_s=1.0,
            subprocess_runner=fake_runner,
            cascade_state_getter=crashing_state,
            gate_requires_detector=True,
            clock=clk.now,
        )
        actor.start()
        wedge_events.publish(_make_event())
        outcomes = [r["outcome"] for r in _read_audit_lines(tmp_audit_log)]
        assert "gate_not_satisfied" in outcomes
        assert ran == []


# ── Restart action subprocess shape ──────────────────────────────────


class TestRestartActionSubprocess:
    def test_command_is_sudo_n_systemctl_restart_rnsd(
        self, tmp_audit_log, clock_at
    ):
        clk = clock_at()
        captured: List[List[str]] = []

        def fake_runner(cmd, timeout_s):
            captured.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        actor = CascadeRecoveryActor(
            action=ACTION_RESTART_RNSD,
            audit_log_path=tmp_audit_log,
            cooldown_s=300.0,
            dedup_window_s=1.0,
            subprocess_runner=fake_runner,
            gate_requires_detector=False,
            clock=clk.now,
        )
        actor.start()
        wedge_events.publish(_make_event())
        deadline = time.time() + 2.0
        while time.time() < deadline and not captured:
            time.sleep(0.01)
        assert captured == [["sudo", "-n", "systemctl", "restart", "rnsd"]]

    def test_subprocess_non_zero_rc_yields_failed_outcome(
        self, tmp_audit_log, clock_at
    ):
        clk = clock_at()

        def fake_runner(cmd, timeout_s):
            return subprocess.CompletedProcess(
                cmd, 1, "", "Failed to restart: unit not found",
            )

        actor = CascadeRecoveryActor(
            action=ACTION_RESTART_RNSD,
            audit_log_path=tmp_audit_log,
            cooldown_s=300.0,
            dedup_window_s=1.0,
            subprocess_runner=fake_runner,
            gate_requires_detector=False,
            clock=clk.now,
        )
        actor.start()
        wedge_events.publish(_make_event())
        deadline = time.time() + 2.0
        while time.time() < deadline:
            rows = _read_audit_lines(tmp_audit_log)
            if any(r["outcome"] == "failed" for r in rows):
                break
            time.sleep(0.01)
        rows = _read_audit_lines(tmp_audit_log)
        failed = [r for r in rows if r["outcome"] == "failed"]
        assert len(failed) == 1
        assert "rc=1" in failed[0]["error"]
        assert "unit not found" in failed[0]["error"]
        snap = actor.snapshot()
        assert snap["stats"]["actions_failed"] == 1
        assert snap["stats"]["actions_succeeded"] == 0

    def test_subprocess_timeout_yields_failed_outcome(
        self, tmp_audit_log, clock_at
    ):
        clk = clock_at()

        def fake_runner(cmd, timeout_s):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout_s)

        actor = CascadeRecoveryActor(
            action=ACTION_RESTART_RNSD,
            audit_log_path=tmp_audit_log,
            cooldown_s=300.0,
            dedup_window_s=1.0,
            subprocess_runner=fake_runner,
            gate_requires_detector=False,
            clock=clk.now,
        )
        actor.start()
        wedge_events.publish(_make_event())
        deadline = time.time() + 2.0
        while time.time() < deadline:
            rows = _read_audit_lines(tmp_audit_log)
            if any(r["outcome"] == "failed" for r in rows):
                break
            time.sleep(0.01)
        failed = [
            r for r in _read_audit_lines(tmp_audit_log)
            if r["outcome"] == "failed"
        ]
        assert len(failed) == 1
        assert "timeout" in failed[0]["error"].lower()
        snap = actor.snapshot()
        assert snap["stats"]["last_error"] == "timeout"

    def test_subprocess_raises_oserror_yields_failed(
        self, tmp_audit_log, clock_at
    ):
        clk = clock_at()

        def fake_runner(cmd, timeout_s):
            raise OSError("sudo not found")

        actor = CascadeRecoveryActor(
            action=ACTION_RESTART_RNSD,
            audit_log_path=tmp_audit_log,
            cooldown_s=300.0,
            dedup_window_s=1.0,
            subprocess_runner=fake_runner,
            gate_requires_detector=False,
            clock=clk.now,
        )
        actor.start()
        wedge_events.publish(_make_event())
        deadline = time.time() + 2.0
        while time.time() < deadline:
            rows = _read_audit_lines(tmp_audit_log)
            if any(r["outcome"] == "failed" for r in rows):
                break
            time.sleep(0.01)
        failed = [
            r for r in _read_audit_lines(tmp_audit_log)
            if r["outcome"] == "failed"
        ]
        assert len(failed) == 1
        assert "sudo not found" in failed[0]["error"]

    def test_action_runs_in_worker_thread_not_publisher(
        self, tmp_audit_log, clock_at
    ):
        """Critical: the subscriber must return promptly. Pinning the
        worker-thread invariant ensures a slow systemctl doesn't block
        the publishing thread (which is the gateway's watchdog)."""
        clk = clock_at()
        publisher_thread_name = threading.current_thread().name
        runner_thread_name = {"name": None}
        runner_event = threading.Event()

        def fake_runner(cmd, timeout_s):
            runner_thread_name["name"] = threading.current_thread().name
            runner_event.set()
            return subprocess.CompletedProcess(cmd, 0, "", "")

        actor = CascadeRecoveryActor(
            action=ACTION_RESTART_RNSD,
            audit_log_path=tmp_audit_log,
            cooldown_s=300.0,
            dedup_window_s=1.0,
            subprocess_runner=fake_runner,
            gate_requires_detector=False,
            clock=clk.now,
        )
        actor.start()
        wedge_events.publish(_make_event())
        assert runner_event.wait(2.0), "worker did not run within 2s"
        assert runner_thread_name["name"] != publisher_thread_name
        assert runner_thread_name["name"].startswith("cascade-recovery-")


# ── Audit log format ─────────────────────────────────────────────────


class TestAuditLogFormat:
    def test_each_row_is_valid_json(self, tmp_audit_log):
        actor = CascadeRecoveryActor(audit_log_path=tmp_audit_log)
        actor.start()
        wedge_events.publish(_make_event())
        # Verify by parsing — _read_audit_lines does json.loads per row.
        rows = _read_audit_lines(tmp_audit_log)
        assert len(rows) == 1

    def test_row_has_ts_event_action_outcome(self, tmp_audit_log):
        actor = CascadeRecoveryActor(audit_log_path=tmp_audit_log)
        actor.start()
        wedge_events.publish(_make_event())
        row = _read_audit_lines(tmp_audit_log)[0]
        for key in ("ts", "event", "action", "outcome"):
            assert key in row

    def test_logged_row_has_no_error_field(self, tmp_audit_log):
        """The audit shape compresses error to absent when empty so
        operators can grep for failed/timeout outcomes cleanly."""
        actor = CascadeRecoveryActor(audit_log_path=tmp_audit_log)
        actor.start()
        wedge_events.publish(_make_event())
        row = _read_audit_lines(tmp_audit_log)[0]
        assert "error" not in row

    def test_parent_dir_is_created(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c" / "recovery.log"
        actor = CascadeRecoveryActor(audit_log_path=nested)
        actor.start()
        wedge_events.publish(_make_event())
        assert nested.exists()

    def test_audit_log_append_failure_is_swallowed(self, tmp_path, caplog):
        """If the audit log path is unwritable, the actor must keep
        running. Production cost of an unwritable log is loss of
        forensic trail — never crash the gateway's watchdog publish
        path because of it."""
        # Use a read-only parent dir as the unwritable path; OSError
        # bubbles inside _append_audit and gets logged.
        ro_dir = tmp_path / "ro"
        ro_dir.mkdir()
        os.chmod(ro_dir, 0o500)
        try:
            actor = CascadeRecoveryActor(audit_log_path=ro_dir / "log")
            actor.start()
            wedge_events.publish(_make_event())  # must not raise
        finally:
            os.chmod(ro_dir, 0o700)


# ── default_audit_log_path resolution ────────────────────────────────


class TestAuditLogPath:
    def test_env_override_wins(self, tmp_path, monkeypatch):
        custom = tmp_path / "custom.log"
        monkeypatch.setenv(
            "MESHFORGE_CASCADE_RECOVERY_LOG", str(custom),
        )
        assert default_audit_log_path() == custom

    def test_xdg_state_home_used_when_no_override(self, tmp_path, monkeypatch):
        monkeypatch.delenv(
            "MESHFORGE_CASCADE_RECOVERY_LOG", raising=False,
        )
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        path = default_audit_log_path()
        assert path == tmp_path / "meshforge" / "cascade_recovery.log"


# ── Singleton ────────────────────────────────────────────────────────


class TestSingleton:
    def test_returns_same_instance(self, tmp_path, monkeypatch):
        # Point the settings + log under tmp so the singleton's
        # construction doesn't write to the user's actual config.
        monkeypatch.setenv(
            "MESHFORGE_CASCADE_RECOVERY_LOG",
            str(tmp_path / "log"),
        )
        a = get_singleton()
        b = get_singleton()
        assert a is b

    def test_reset_for_tests_drops_singleton(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "MESHFORGE_CASCADE_RECOVERY_LOG",
            str(tmp_path / "log"),
        )
        a = get_singleton()
        cascade_recovery_actions._reset_singleton_for_tests()
        b = get_singleton()
        assert a is not b


# ── Subscriber exception isolation ───────────────────────────────────


class TestSubscriberExceptionIsolation:
    def test_handler_crash_writes_failure_audit_row(self, tmp_audit_log):
        """If _handle_event raises unexpectedly, the synthetic-failure
        audit row ensures the operator sees the actor went quiet."""
        actor = CascadeRecoveryActor(audit_log_path=tmp_audit_log)
        actor.start()

        # Force the inner handler to raise.
        def broken_handle(_evt):
            raise RuntimeError("test-injected handler crash")
        actor._handle_event = broken_handle  # type: ignore[method-assign]

        wedge_events.publish(_make_event())
        rows = _read_audit_lines(tmp_audit_log)
        assert len(rows) == 1
        assert rows[0]["outcome"] == "failed"
        assert "handler_crash" in rows[0]["error"]
