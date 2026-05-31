"""Tests for the mini-dudeai honesty audit organ (src/mini_dudeai/audit.py).

The audit organ certifies mini's self-report against on-disk reality. These
tests pin the three-valued honesty contract (PASS / UNVERIFIABLE / DEGRADED)
and the fail-closed exit code — AND pin the regression that nearly shipped a
liability: a check keyed on a field the real schema never used
(``resolved_by``) false-flagged genuinely-resolved deltas. The provenance
check must key on the ACTUAL schema (``resolved_ts`` / ``resolved_note``).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mini_dudeai import audit  # noqa: E402
from mini_dudeai.audit import (  # noqa: E402
    PASS,
    UNVERIFIABLE,
    DEGRADED,
    AuditContext,
    run_audit,
    check_liveness,
    check_history_integrity,
    check_outcome_truth,
    check_proposal_readback,
    check_ai_layer_honesty,
    check_provenance_gate,
)


NOW = 1_780_000_000.0


def _write_state(d: Path, *, last_tick_ts=NOW, fire_count=0, error_count=0):
    (d / audit.STATE_FILE).write_text(json.dumps({
        "rules": {}, "fire_count": fire_count, "error_count": error_count,
        "last_tick_ts": last_tick_ts,
        "last_tick_iso": "2026-05-30T00:00:00",
    }))


def _write_history(d: Path, entries):
    (d / audit.HISTORY_FILE).write_text(
        "".join(json.dumps(e) + "\n" for e in entries))


def _write_deltas(d: Path, rows):
    (d / audit.DELTAS_FILE).write_text(
        "".join(json.dumps(r) + "\n" for r in rows))


def _ctx(d: Path, *, now=NOW, **kw) -> AuditContext:
    return AuditContext.load(d, now=now, **kw)


# ─────────────────────────────────────────────────────────────────────
# liveness — the #1 false-green: looks configured, isn't ticking
# ─────────────────────────────────────────────────────────────────────


def test_liveness_pass_when_fresh(tmp_path):
    _write_state(tmp_path, last_tick_ts=NOW - 10)
    assert check_liveness(_ctx(tmp_path)).status == PASS


def test_liveness_degraded_when_state_absent(tmp_path):
    # No state file at all — mini has never ticked here.
    r = check_liveness(_ctx(tmp_path))
    assert r.status == DEGRADED
    assert "never ticked" in r.detail or "missing" in r.detail


def test_liveness_degraded_when_stale(tmp_path):
    _write_state(tmp_path, last_tick_ts=NOW - 999_999)
    r = check_liveness(_ctx(tmp_path, freshness_s=5400))
    assert r.status == DEGRADED
    assert "stale" in r.detail


def test_liveness_unverifiable_on_future_clock_skew(tmp_path):
    _write_state(tmp_path, last_tick_ts=NOW + 5000)
    assert check_liveness(_ctx(tmp_path)).status == UNVERIFIABLE


def test_liveness_degraded_when_no_numeric_ts(tmp_path):
    (tmp_path / audit.STATE_FILE).write_text(json.dumps({"last_tick_ts": "soon"}))
    assert check_liveness(_ctx(tmp_path)).status == DEGRADED


# ─────────────────────────────────────────────────────────────────────
# history integrity + outcome truth
# ─────────────────────────────────────────────────────────────────────


def test_history_unverifiable_when_absent(tmp_path):
    assert check_history_integrity(_ctx(tmp_path)).status == UNVERIFIABLE


def test_history_degraded_on_malformed_line(tmp_path):
    (tmp_path / audit.HISTORY_FILE).write_text(
        json.dumps({"ts": NOW, "outcome": {"ok": True}}) + "\n"
        + "{not json at all\n")
    r = check_history_integrity(_ctx(tmp_path))
    assert r.status == DEGRADED
    assert "malformed" in r.detail


def test_history_pass_when_clean(tmp_path):
    _write_history(tmp_path, [
        {"ts": NOW, "outcome": {"action": "ntfy", "ok": True}}])
    assert check_history_integrity(_ctx(tmp_path)).status == PASS


def test_outcome_truth_degraded_when_all_errors(tmp_path):
    _write_history(tmp_path, [
        {"ts": NOW, "outcome": {"action": "ntfy", "ok": False, "error": "boom"}}
        for _ in range(5)])
    r = check_outcome_truth(_ctx(tmp_path))
    assert r.status == DEGRADED
    assert "nothing is succeeding" in r.detail


def test_outcome_truth_pass_with_honest_mixed_errors(tmp_path):
    # Some errors among successes is honest reporting, not a false-green.
    entries = [{"ts": NOW, "outcome": {"action": "ntfy", "ok": True}}
               for _ in range(9)]
    entries.append({"ts": NOW, "outcome": {"action": "ntfy", "ok": False,
                                           "error": "x"}})
    _write_history(tmp_path, entries)
    r = check_outcome_truth(_ctx(tmp_path))
    assert r.status == PASS
    assert r.data["err"] == 1 and r.data["ok"] == 9


def test_outcome_truth_unverifiable_when_no_history(tmp_path):
    assert check_outcome_truth(_ctx(tmp_path)).status == UNVERIFIABLE


# ─────────────────────────────────────────────────────────────────────
# proposal read-back — close the write-only theater
# ─────────────────────────────────────────────────────────────────────


def test_proposal_unverifiable_when_no_deltas(tmp_path):
    assert check_proposal_readback(_ctx(tmp_path)).status == UNVERIFIABLE


def test_proposal_degraded_on_status_lie(tmp_path):
    # 'proposed' yet carrying a resolution = lying about its own state.
    _write_deltas(tmp_path, [
        {"ts": NOW, "status": "proposed", "resolved_ts": NOW, "key": "k"}])
    r = check_proposal_readback(_ctx(tmp_path))
    assert r.status == DEGRADED
    assert "lies about itself" in r.detail


def test_proposal_pass_when_consistent(tmp_path):
    _write_deltas(tmp_path, [
        {"ts": NOW, "status": "proposed", "key": "a"},
        {"ts": NOW, "status": "ratified", "resolved_ts": NOW, "key": "b"},
    ])
    assert check_proposal_readback(_ctx(tmp_path)).status == PASS


def test_proposal_surfaces_stale_unresolved(tmp_path):
    _write_deltas(tmp_path, [
        {"ts": NOW - 999_999, "status": "proposed", "key": "old"}])
    r = check_proposal_readback(_ctx(tmp_path, stale_proposal_s=86400))
    assert r.status == PASS
    assert "proposing is not" in r.detail


# ─────────────────────────────────────────────────────────────────────
# AI layer honesty — state reality plainly
# ─────────────────────────────────────────────────────────────────────


def test_ai_layer_reports_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(audit.importlib.util, "find_spec", lambda name: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = check_ai_layer_honesty(_ctx(tmp_path))
    assert r.status == PASS
    assert "ABSENT" in r.detail
    assert r.data["anthropic_installed"] is False


def test_ai_layer_reports_possible_when_present(tmp_path, monkeypatch):
    monkeypatch.setattr(audit.importlib.util, "find_spec",
                        lambda name: object())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    r = check_ai_layer_honesty(_ctx(tmp_path))
    assert r.status == PASS
    assert "POSSIBLE" in r.detail


# ─────────────────────────────────────────────────────────────────────
# provenance gate — the regression that nearly shipped a liability
# ─────────────────────────────────────────────────────────────────────


def test_provenance_pass_on_real_resolved_schema(tmp_path):
    """The exact shape that false-flagged: ratified deltas with
    resolved_ts + resolved_note and NO resolved_by field. The fixed check
    must PASS — these were externally resolved by a cloud session."""
    _write_deltas(tmp_path, [
        {"ts": NOW - 100, "status": "ratified",
         "resolved_ts": NOW, "resolved_note": "grace_s=90 shipped (commit x)",
         "key": "chronic_flap::a"},
        {"ts": NOW - 100, "status": "ratified",
         "resolved_ts": NOW, "resolved_note": "live on federator rule",
         "key": "chronic_flap::b"},
    ])
    r = check_provenance_gate(_ctx(tmp_path))
    assert r.status == PASS
    assert r.data["resolved_ratified"] == 2
    assert r.data["unresolved_ratified"] == 0


def test_provenance_degraded_when_ratified_without_evidence(tmp_path):
    """A ratified delta with NO resolution evidence = mini certifying
    itself. THIS is the real violation the gate must catch."""
    _write_deltas(tmp_path, [
        {"ts": NOW, "status": "ratified", "key": "self-certified"}])
    r = check_provenance_gate(_ctx(tmp_path))
    assert r.status == DEGRADED
    assert "NO external resolution" in r.detail


def test_provenance_unverifiable_when_no_deltas(tmp_path):
    assert check_provenance_gate(_ctx(tmp_path)).status == UNVERIFIABLE


# ─────────────────────────────────────────────────────────────────────
# full report + fail-closed exit
# ─────────────────────────────────────────────────────────────────────


def test_run_audit_healthy_path(tmp_path):
    _write_state(tmp_path, last_tick_ts=NOW - 10)
    _write_history(tmp_path, [
        {"ts": NOW, "outcome": {"action": "none", "ok": True}}])
    _write_deltas(tmp_path, [
        {"ts": NOW, "status": "ratified", "resolved_ts": NOW,
         "resolved_note": "x", "key": "k"}])
    report = run_audit(_ctx(tmp_path))
    assert report.healthy is True
    assert report.degraded == []


def test_run_audit_degraded_on_dead_mini(tmp_path):
    # No artifacts at all → liveness degrades → not healthy.
    report = run_audit(_ctx(tmp_path))
    assert report.healthy is False
    assert any(c.name == "liveness" and c.status == DEGRADED
               for c in report.checks)


def test_main_exit_code_fail_closed(tmp_path, capsys):
    """main() must exit nonzero when degraded — so a cron wrapper is
    fail-closed (a green genuinely means something)."""
    _write_state(tmp_path, last_tick_ts=NOW - 999_999)  # stale → degraded
    rc = audit.main(["--dir", str(tmp_path), "--freshness", "60"])
    assert rc == 2


def test_main_exit_zero_when_healthy(tmp_path):
    _write_state(tmp_path, last_tick_ts=time.time())
    rc = audit.main(["--dir", str(tmp_path), "--freshness", "5400"])
    assert rc == 0


def test_main_writes_ledger_line(tmp_path):
    _write_state(tmp_path, last_tick_ts=time.time())
    ledger = tmp_path / "ledger.jsonl"
    audit.main(["--dir", str(tmp_path), "--freshness", "5400",
                "--ledger", str(ledger)])
    lines = ledger.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert "healthy" in rec and "checks" in rec


def test_run_audit_never_crashes_on_check_exception(tmp_path, monkeypatch):
    """An audit organ that crashes is its own dishonesty. A check that
    raises must be recorded DEGRADED, not propagate."""
    def _boom(ctx):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(audit, "CHECKS", [_boom])
    report = run_audit(_ctx(tmp_path))
    assert report.healthy is False
    assert report.checks[0].status == DEGRADED
    assert "kaboom" in report.checks[0].detail
