"""Tests for the Phase 2 watchdog auto-restart action layer.

Companion to ``test_watchdog_probes.py``. Phase 2 is gated OFF by
default; these tests pin every safety gate explicitly so we never
accidentally regress to "auto-restart on any wedge signal."

The decision logic is pure, so most tests exercise ``decide_restarts``
directly with constructed Signals and a controlled clock. The one
side-effect site (``execute_restart``) gets its own focused test that
mocks subprocess.run.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from utils.watchdog_actions import (
    AutoRestartRule,
    Phase2Config,
    RestartHistory,
    decide_restarts,
    parse_phase2_config,
    execute_restart,
    DEFAULT_CONSECUTIVE_TICKS,
    DEFAULT_COOLDOWN_S,
    DEFAULT_MAX_RESTARTS_PER_HOUR,
    RATE_LIMIT_WINDOW_S,
    OPERATOR_IN_FLIGHT_STATES,
)
from utils.watchdog_probes import Signal


TICK_S = 30.0


def _sig(cls, subject, severity="wedge", extra=None):
    return Signal(
        cls=cls, subject=subject, severity=severity,
        detail="test", extra=extra or {},
    )


def _rule(cls, service, **kw):
    return AutoRestartRule(signal_class=cls, service=service, **kw)


# ─────────────────────────────────────────────────────────────────────
# Default OFF — the single most important regression guard
# ─────────────────────────────────────────────────────────────────────


class TestDefaultOff:
    """If Phase2Config is left at defaults, nothing ever restarts.

    This is the contract we ship with on 2026-05-21. Any future change
    that breaks this contract should fail this class loudly — the whole
    point of Phase 2 being gated OFF is that the gate cannot be
    accidentally lifted by a refactor.
    """

    def test_default_config_disables_everything(self):
        config = Phase2Config()
        assert config.enabled is False
        decisions = decide_restarts(
            signals_with_first_seen=[(_sig("any", "any"), 0.0)],
            config=config, history=RestartHistory(),
            now=1000.0, tick_s=TICK_S,
        )
        assert decisions == []

    def test_enabled_false_with_populated_rules_still_no_restart(self):
        """A non-empty allowlist with enabled=False is observed-only."""
        config = Phase2Config(
            enabled=False,
            rules=(_rule("rns_shared_instance_unresponsive", "rnsd.service"),),
        )
        sig = _sig("rns_shared_instance_unresponsive", "rnsd.service")
        decisions = decide_restarts(
            signals_with_first_seen=[(sig, 0.0)],
            config=config, history=RestartHistory(),
            now=10_000.0, tick_s=TICK_S,  # well past any consecutive_ticks
        )
        assert decisions == []


# ─────────────────────────────────────────────────────────────────────
# Allowlist enforcement
# ─────────────────────────────────────────────────────────────────────


class TestAllowlistEnforcement:
    def test_signal_outside_allowlist_no_restart(self):
        config = Phase2Config(
            enabled=True,
            rules=(_rule("rns_shared_instance_unresponsive", "rnsd.service",
                         consecutive_ticks=1),),
        )
        # Wrong class
        decisions = decide_restarts(
            signals_with_first_seen=[(_sig("http_local_unresponsive", "rnsd.service"), 0.0)],
            config=config, history=RestartHistory(),
            now=1000.0, tick_s=TICK_S,
        )
        assert decisions == []

    def test_signal_class_matches_but_subject_doesnt_no_restart(self):
        config = Phase2Config(
            enabled=True,
            rules=(_rule("service_inactive", "rnsd.service", consecutive_ticks=1),),
        )
        decisions = decide_restarts(
            signals_with_first_seen=[(_sig("service_inactive", "meshforge-map.service",
                                           extra={"actual": "inactive"}), 0.0)],
            config=config, history=RestartHistory(),
            now=1000.0, tick_s=TICK_S,
        )
        assert decisions == []

    def test_allowlist_match_produces_decision(self):
        config = Phase2Config(
            enabled=True,
            rules=(_rule("rns_shared_instance_unresponsive", "rnsd.service",
                         consecutive_ticks=1),),
        )
        sig = _sig("rns_shared_instance_unresponsive", "rnsd.service")
        decisions = decide_restarts(
            signals_with_first_seen=[(sig, 0.0)],
            config=config, history=RestartHistory(),
            now=1000.0, tick_s=TICK_S,
        )
        assert len(decisions) == 1
        assert decisions[0].service == "rnsd.service"
        assert decisions[0].rule.signal_class == "rns_shared_instance_unresponsive"


# ─────────────────────────────────────────────────────────────────────
# Consecutive-ticks gate
# ─────────────────────────────────────────────────────────────────────


class TestConsecutiveTicksGate:
    """Today's data showed http_local_unresponsive self-clearing in 30s.
    The consecutive_ticks gate is what would have prevented a false-
    positive restart on that signal. Pin the behavior explicitly."""

    def test_signal_below_threshold_no_restart(self):
        config = Phase2Config(
            enabled=True,
            rules=(_rule("http_local_unresponsive", "meshforge-map.service",
                         consecutive_ticks=5),),
        )
        # Only 2 ticks have elapsed since first_seen=1000.0
        now = 1000.0 + 2 * TICK_S
        sig = _sig("http_local_unresponsive", "meshforge-map.service")
        decisions = decide_restarts(
            signals_with_first_seen=[(sig, 1000.0)],
            config=config, history=RestartHistory(),
            now=now, tick_s=TICK_S,
        )
        assert decisions == []

    def test_signal_at_threshold_triggers(self):
        config = Phase2Config(
            enabled=True,
            rules=(_rule("http_local_unresponsive", "meshforge-map.service",
                         consecutive_ticks=5),),
        )
        # 5 ticks elapsed — should trigger.
        now = 1000.0 + 5 * TICK_S
        sig = _sig("http_local_unresponsive", "meshforge-map.service")
        decisions = decide_restarts(
            signals_with_first_seen=[(sig, 1000.0)],
            config=config, history=RestartHistory(),
            now=now, tick_s=TICK_S,
        )
        assert len(decisions) == 1
        assert "persisted 6 ticks" in decisions[0].reason  # 0..5 = 6 observations

    def test_todays_http_local_30s_false_positive_blocked(self):
        """Regression guard for the moc 21:19 case: signal first_seen at
        21:19:23 and cleared at 21:19:53 — exactly 1 tick of observation.
        With default consecutive_ticks=5, no restart would have fired."""
        config = Phase2Config(
            enabled=True,
            rules=(_rule("http_local_unresponsive", "meshforge-map.service"),),  # defaults
        )
        first_seen = 1779438000.0
        now = first_seen + 30.0  # one tick later — exact reproduction of moc
        sig = _sig("http_local_unresponsive", "meshforge-map.service")
        decisions = decide_restarts(
            signals_with_first_seen=[(sig, first_seen)],
            config=config, history=RestartHistory(),
            now=now, tick_s=TICK_S,
        )
        assert decisions == []


# ─────────────────────────────────────────────────────────────────────
# Cooldown — same service can't be restarted again immediately
# ─────────────────────────────────────────────────────────────────────


class TestCooldown:
    def test_recent_restart_blocks_decision(self):
        config = Phase2Config(
            enabled=True,
            rules=(_rule("service_inactive", "rnsd.service",
                         consecutive_ticks=1, cooldown_s=600),),
        )
        history = RestartHistory()
        history.record("rnsd.service", 900.0)  # restarted 100s ago at now=1000
        sig = _sig("service_inactive", "rnsd.service", extra={"actual": "inactive"})
        decisions = decide_restarts(
            signals_with_first_seen=[(sig, 0.0)],
            config=config, history=history,
            now=1000.0, tick_s=TICK_S,
        )
        assert decisions == []

    def test_cooldown_expired_allows_decision(self):
        config = Phase2Config(
            enabled=True,
            rules=(_rule("service_inactive", "rnsd.service",
                         consecutive_ticks=1, cooldown_s=600),),
        )
        history = RestartHistory()
        history.record("rnsd.service", 100.0)  # restarted 900s ago at now=1000
        sig = _sig("service_inactive", "rnsd.service", extra={"actual": "inactive"})
        decisions = decide_restarts(
            signals_with_first_seen=[(sig, 0.0)],
            config=config, history=history,
            now=1000.0, tick_s=TICK_S,
        )
        assert len(decisions) == 1


# ─────────────────────────────────────────────────────────────────────
# Rate limit — N restarts per hour cap
# ─────────────────────────────────────────────────────────────────────


class TestRateLimit:
    def test_rate_limit_blocks_after_max(self):
        config = Phase2Config(
            enabled=True,
            rules=(_rule("service_inactive", "rnsd.service",
                         consecutive_ticks=1, cooldown_s=1, max_restarts_per_hour=2),),
        )
        history = RestartHistory()
        # Two restarts within the trailing hour. Cooldown of 1s means
        # only the rate limit can possibly block at now=10000.
        history.record("rnsd.service", 8000.0)
        history.record("rnsd.service", 9000.0)
        sig = _sig("service_inactive", "rnsd.service", extra={"actual": "inactive"})
        decisions = decide_restarts(
            signals_with_first_seen=[(sig, 0.0)],
            config=config, history=history,
            now=10_000.0, tick_s=TICK_S,
        )
        assert decisions == []

    def test_rate_limit_window_expires(self):
        config = Phase2Config(
            enabled=True,
            rules=(_rule("service_inactive", "rnsd.service",
                         consecutive_ticks=1, cooldown_s=1, max_restarts_per_hour=2),),
        )
        history = RestartHistory()
        # Two restarts >1h ago — should NOT count against rate limit.
        history.record("rnsd.service", 0.0)
        history.record("rnsd.service", 1000.0)
        sig = _sig("service_inactive", "rnsd.service", extra={"actual": "inactive"})
        decisions = decide_restarts(
            signals_with_first_seen=[(sig, 5000.0)],
            config=config, history=history,
            now=5000.0 + RATE_LIMIT_WINDOW_S + 1, tick_s=TICK_S,
        )
        assert len(decisions) == 1


# ─────────────────────────────────────────────────────────────────────
# Operator-in-flight suppression — today's moc1 case
# ─────────────────────────────────────────────────────────────────────


class TestOperatorInFlightSuppression:
    @pytest.mark.parametrize("state", sorted(OPERATOR_IN_FLIGHT_STATES))
    def test_operator_in_flight_state_blocks_restart(self, state):
        """The moc1 false-positive at 21:31 was a service_inactive
        signal with actual='deactivating' — the operator was mid
        ``systemctl restart``. Phase 2 must defer in these states."""
        config = Phase2Config(
            enabled=True,
            rules=(_rule("service_inactive", "meshforge-map.service",
                         consecutive_ticks=1),),
        )
        sig = _sig("service_inactive", "meshforge-map.service",
                   severity="degraded", extra={"actual": state})
        decisions = decide_restarts(
            signals_with_first_seen=[(sig, 0.0)],
            config=config, history=RestartHistory(),
            now=10_000.0, tick_s=TICK_S,
        )
        assert decisions == []

    def test_terminal_inactive_state_still_acts(self):
        """``inactive`` is terminal — the service has fully transitioned
        and isn't coming back without intervention. That's exactly when
        we DO want to restart (if allowlisted)."""
        config = Phase2Config(
            enabled=True,
            rules=(_rule("service_inactive", "rnsd.service",
                         consecutive_ticks=1),),
        )
        sig = _sig("service_inactive", "rnsd.service",
                   severity="degraded", extra={"actual": "inactive"})
        decisions = decide_restarts(
            signals_with_first_seen=[(sig, 0.0)],
            config=config, history=RestartHistory(),
            now=10_000.0, tick_s=TICK_S,
        )
        assert len(decisions) == 1


# ─────────────────────────────────────────────────────────────────────
# Dedup — multiple signals for the same service produce one decision
# ─────────────────────────────────────────────────────────────────────


class TestDedup:
    def test_two_signals_same_service_one_decision(self):
        config = Phase2Config(
            enabled=True,
            rules=(
                _rule("rns_shared_instance_unresponsive", "rnsd.service",
                      consecutive_ticks=1),
                _rule("service_inactive", "rnsd.service", consecutive_ticks=1),
            ),
        )
        sigs = [
            (_sig("rns_shared_instance_unresponsive", "rnsd.service"), 0.0),
            (_sig("service_inactive", "rnsd.service",
                  severity="degraded", extra={"actual": "inactive"}), 0.0),
        ]
        decisions = decide_restarts(
            signals_with_first_seen=sigs,
            config=config, history=RestartHistory(),
            now=10_000.0, tick_s=TICK_S,
        )
        assert len(decisions) == 1
        assert decisions[0].service == "rnsd.service"

    def test_two_services_two_decisions(self):
        config = Phase2Config(
            enabled=True,
            rules=(
                _rule("service_inactive", "rnsd.service", consecutive_ticks=1),
                _rule("service_inactive", "meshforge-map.service",
                      consecutive_ticks=1),
            ),
        )
        sigs = [
            (_sig("service_inactive", "rnsd.service",
                  severity="degraded", extra={"actual": "inactive"}), 0.0),
            (_sig("service_inactive", "meshforge-map.service",
                  severity="degraded", extra={"actual": "inactive"}), 0.0),
        ]
        decisions = decide_restarts(
            signals_with_first_seen=sigs,
            config=config, history=RestartHistory(),
            now=10_000.0, tick_s=TICK_S,
        )
        assert {d.service for d in decisions} == {
            "rnsd.service", "meshforge-map.service",
        }


# ─────────────────────────────────────────────────────────────────────
# Dry-run mode
# ─────────────────────────────────────────────────────────────────────


class TestDryRun:
    def test_dry_run_decisions_still_produced(self):
        """dry_run=True means decisions ARE produced (so they can be
        logged) but the runner is responsible for not executing them.
        The decision object carries dry_run so the runner can branch."""
        config = Phase2Config(
            enabled=True, dry_run=True,
            rules=(_rule("rns_shared_instance_unresponsive", "rnsd.service",
                         consecutive_ticks=1),),
        )
        sig = _sig("rns_shared_instance_unresponsive", "rnsd.service")
        decisions = decide_restarts(
            signals_with_first_seen=[(sig, 0.0)],
            config=config, history=RestartHistory(),
            now=1000.0, tick_s=TICK_S,
        )
        assert len(decisions) == 1
        assert decisions[0].dry_run is True


# ─────────────────────────────────────────────────────────────────────
# Config parsing — default OFF on anything ambiguous
# ─────────────────────────────────────────────────────────────────────


class TestConfigParsing:
    def test_empty_dict_defaults_off(self):
        cfg = parse_phase2_config({})
        assert cfg.enabled is False
        assert cfg.dry_run is False
        assert cfg.rules == ()

    def test_missing_section_defaults_off(self):
        cfg = parse_phase2_config({"other_section": {"enabled": True}})
        assert cfg.enabled is False

    def test_section_not_dict_defaults_off(self):
        cfg = parse_phase2_config({"phase2_auto_restart": "yes please"})
        assert cfg.enabled is False

    def test_enabled_must_be_literally_true(self):
        """Truthy values like 1 or 'true' are NOT accepted — we want an
        explicit JSON boolean. This stops typos from silently flipping
        the master switch."""
        for not_true in (1, "true", "yes", None, [], {}):
            cfg = parse_phase2_config({"phase2_auto_restart": {"enabled": not_true}})
            assert cfg.enabled is False, f"value {not_true!r} should not enable"

    def test_full_valid_config(self):
        raw = {
            "phase2_auto_restart": {
                "enabled": True,
                "dry_run": True,
                "allowlist": [
                    {
                        "signal_class": "rns_shared_instance_unresponsive",
                        "service": "rnsd.service",
                        "consecutive_ticks": 5,
                        "cooldown_s": 600,
                        "max_restarts_per_hour": 2,
                    },
                ],
            },
        }
        cfg = parse_phase2_config(raw)
        assert cfg.enabled is True
        assert cfg.dry_run is True
        assert len(cfg.rules) == 1
        rule = cfg.rules[0]
        assert rule.signal_class == "rns_shared_instance_unresponsive"
        assert rule.service == "rnsd.service"
        assert rule.consecutive_ticks == 5
        assert rule.cooldown_s == 600.0
        assert rule.max_restarts_per_hour == 2

    def test_malformed_rule_dropped_but_others_kept(self):
        raw = {
            "phase2_auto_restart": {
                "enabled": True,
                "allowlist": [
                    {"signal_class": "ok", "service": "ok.service"},
                    "not a dict",
                    {"signal_class": "missing_service"},
                    {"service": "missing_class.service"},
                    {"signal_class": "", "service": "empty.service"},
                ],
            },
        }
        cfg = parse_phase2_config(raw)
        assert cfg.enabled is True
        assert len(cfg.rules) == 1
        assert cfg.rules[0].signal_class == "ok"

    def test_invalid_knobs_fall_back_to_defaults(self):
        """Negative/zero/non-numeric knobs default rather than crash."""
        raw = {
            "phase2_auto_restart": {
                "enabled": True,
                "allowlist": [
                    {
                        "signal_class": "x", "service": "y.service",
                        "consecutive_ticks": -5,           # invalid
                        "cooldown_s": "ten minutes",       # invalid
                        "max_restarts_per_hour": True,     # bool, not int
                    },
                ],
            },
        }
        cfg = parse_phase2_config(raw)
        assert cfg.rules[0].consecutive_ticks == DEFAULT_CONSECUTIVE_TICKS
        assert cfg.rules[0].cooldown_s == DEFAULT_COOLDOWN_S
        assert cfg.rules[0].max_restarts_per_hour == DEFAULT_MAX_RESTARTS_PER_HOUR


# ─────────────────────────────────────────────────────────────────────
# Restart history
# ─────────────────────────────────────────────────────────────────────


class TestRestartHistory:
    def test_empty_history_returns_none(self):
        h = RestartHistory()
        assert h.last_restart_ts("any.service") is None
        assert h.restarts_in_window("any.service", 0.0) == 0

    def test_record_and_lookup(self):
        h = RestartHistory()
        h.record("a.service", 100.0)
        h.record("a.service", 200.0)
        h.record("b.service", 150.0)
        assert h.last_restart_ts("a.service") == 200.0
        assert h.last_restart_ts("b.service") == 150.0
        assert h.restarts_in_window("a.service", 50.0) == 2
        assert h.restarts_in_window("a.service", 150.0) == 1

    def test_old_entries_pruned_on_record(self):
        """Entries older than the rate-limit window get dropped when a
        fresh restart is recorded — keeps the dict bounded over uptime."""
        h = RestartHistory()
        h.record("svc", 0.0)
        h.record("svc", RATE_LIMIT_WINDOW_S + 100)  # one window later
        # Window-since-now = 100; only the second restart should remain.
        now = RATE_LIMIT_WINDOW_S + 100
        assert h.restarts_in_window("svc", now - RATE_LIMIT_WINDOW_S) == 1


# ─────────────────────────────────────────────────────────────────────
# execute_restart — the one side-effect site
# ─────────────────────────────────────────────────────────────────────


class TestRunnerIntegration:
    """End-to-end-ish: assert the run_loop wiring never restarts a
    service when Phase 2 config is at defaults.

    This is the contract that protects every fleet box from accidental
    auto-restart on the 2026-05-21 ship. If a future change ever causes
    this test to fail, that's a Phase 2 audit gate, not a green PR.
    """

    def test_run_loop_with_default_config_never_calls_execute(self):
        import threading
        from utils.watchdog_runner import run_loop

        # Wire a stop_event that fires immediately so we exit after one tick.
        stop = threading.Event()

        # Pre-set stop so the loop exits after writing state.
        stop.set()

        # Fake probes that return a wedge signal that, in a hypothetical
        # all-on config, would match a restart rule.
        from utils.watchdog_probes import Signal
        fake_signals = [
            Signal(cls="rns_shared_instance_unresponsive",
                   subject="rnsd.service",
                   severity="wedge", detail="test", extra={}),
        ]

        with patch("utils.watchdog_runner.run_all_probes",
                   return_value=fake_signals), \
             patch("utils.watchdog_runner.execute_restart") as mock_exec, \
             patch("utils.watchdog_runner.write_state"):
            run_loop(
                output_path=MagicMock(),
                tick_s=30.0,
                stop_event=stop,
                services_expected_active=(),
                services_wedge_check=(),
                # No phase2_config arg => default Phase2Config(enabled=False)
            )

        assert mock_exec.call_count == 0, (
            "execute_restart was called with Phase 2 at defaults — "
            "the default-OFF gate has regressed"
        )


class TestExecuteRestart:
    def test_invokes_systemctl_restart(self):
        completed = MagicMock(returncode=0, stderr="")
        with patch("utils.watchdog_actions.subprocess.run",
                   return_value=completed) as mock_run:
            ok = execute_restart("rnsd.service")
        assert ok is True
        args, kwargs = mock_run.call_args
        assert args[0] == ["systemctl", "restart", "rnsd.service"]
        assert kwargs["timeout"] == 30.0

    def test_non_zero_exit_returns_false(self):
        completed = MagicMock(returncode=1, stderr="not found")
        with patch("utils.watchdog_actions.subprocess.run",
                   return_value=completed):
            ok = execute_restart("rnsd.service")
        assert ok is False

    def test_timeout_returns_false(self):
        import subprocess as sp
        with patch("utils.watchdog_actions.subprocess.run",
                   side_effect=sp.TimeoutExpired(cmd="systemctl", timeout=30)):
            ok = execute_restart("rnsd.service")
        assert ok is False

    def test_missing_systemctl_returns_false(self):
        with patch("utils.watchdog_actions.subprocess.run",
                   side_effect=FileNotFoundError("systemctl")):
            ok = execute_restart("rnsd.service")
        assert ok is False
