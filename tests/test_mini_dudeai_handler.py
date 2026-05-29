"""Tests for the mini-dudeai → remediation-surface loop-closer.

Pins build_findings(): currently-active rules + fresh escalations become
findings; rules with a safe local fix get RemediationActions; remote-subject /
unmapped findings are informational (no action). Escalations come via the
recent_escalations SSOT, so this and the brief/digest never disagree.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "launcher_tui"))

from handlers.mini_dudeai import build_findings, _fixes_for, _posture  # noqa: E402

NOW = 1_780_000_000.0


def _esc_row(ts, rule, subject, detail):
    return {"ts": ts, "outcome": {"extras": {"escalation": {
        "rule": rule, "subject": subject, "detail": detail}}}}


def test_active_source_error_federator_gets_restart_fix():
    state = {"rules": {"k": {"rule_id": "source_error_federator",
                             "subject": "federator", "currently_active": True,
                             "last_detail": "/api/status unreachable"}}}
    findings = build_findings(state, [], NOW)
    assert len(findings) == 1
    f = findings[0]
    assert f["source"] == "active" and f["actions"]
    assert f["actions"][0].label == "Restart meshforge-map"
    assert f["actions"][0].requires_admin is True


def test_inactive_rules_are_not_findings():
    state = {"rules": {"k": {"rule_id": "source_error_federator",
                             "subject": "federator", "currently_active": False}}}
    assert build_findings(state, [], NOW) == []


def test_remote_peer_escalation_has_no_local_fix():
    hist = [_esc_row(NOW - 100, "federation_peer_unhealthy_unexpected",
                     "moc3", "in_backoff mult=10")]
    findings = build_findings({}, hist, NOW)
    assert len(findings) == 1
    assert findings[0]["source"] == "escalation"
    assert findings[0]["actions"] == []          # informational, no pretend-fix


def test_escalation_uses_recent_escalations_ssot_window():
    # An escalation older than the 24h window is dropped — proving we go through
    # recent_escalations, not a private re-read.
    stale = _esc_row(NOW - 90_000, "federation_peer_unhealthy_unexpected",
                     "moc3", "old")
    assert build_findings({}, [stale], NOW) == []


def test_active_rule_dedups_escalation_of_same_rule_subject():
    state = {"rules": {"k": {"rule_id": "source_error_federator",
                             "subject": "federator", "currently_active": True,
                             "last_detail": "d"}}}
    hist = [_esc_row(NOW - 50, "source_error_federator", "federator", "d")]
    findings = build_findings(state, hist, NOW)
    assert len(findings) == 1                     # not shown twice


def test_watchdog_source_error_maps_to_watchdog_restart():
    acts = _fixes_for("source_error_watchdog")
    assert acts and acts[0].label == "Restart meshforge-watchdog"


def test_unmapped_rule_has_no_actions():
    assert _fixes_for("some_unknown_rule") == []


def test_posture_flags_stale():
    out = _posture({"last_tick_ts": NOW - 99999, "rule_count": 12}, NOW)
    assert "STALE" in out


def test_posture_alive():
    out = _posture({"last_tick_ts": NOW - 10, "rule_count": 12, "error_count": 0}, NOW)
    assert "alive" in out and "12 rules" in out
