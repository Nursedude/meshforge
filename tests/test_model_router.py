"""Tests for the model router (WS-E) — model routing as a re-derivable call.

The invariants under test are the HARNESS ones: the recommendation is grounded
in the eval ledger (never trust tier-L for a kind it fails), clamped by the
capability gradient, honest about UNKNOWN competence (never a forged 1.0), and
model-agnostic (emits a tier, never a model id). Advisory only — nothing here
blocks work.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mini_dudeai import model_router as mr

NOW = 1_800_000_000.0


def _eval_rec(per_kind):
    return {"ts": NOW - 3600, "pass_rate": 1.0, "per_kind": per_kind}


def _pk(passed, total):
    return {"passed": passed, "total": total}


# ── the tier ladder ─────────────────────────────────────────────────────────

def test_tier_rank_is_ascending():
    assert (mr.tier_rank("rules") < mr.tier_rank("local")
            < mr.tier_rank("fast") < mr.tier_rank("opus")
            < mr.tier_rank("frontier"))
    assert mr.tier_rank("nonsense") == -1


# ── capability gradient clamping ────────────────────────────────────────────

def test_frontier_task_on_fleet_is_clamped_and_flagged():
    rec = mr.route("novel_design", "fleet", now_ts=NOW)
    assert rec.base_tier == "frontier" and rec.ceiling == "local"
    assert rec.recommended_tier == "local"
    assert rec.disposition == "capability_gap"
    assert "capability gap" in rec.why


def test_frontier_task_on_qth_is_right_sized():
    rec = mr.route("adversarial_review", "qth", now_ts=NOW)
    assert rec.recommended_tier == "frontier"
    assert rec.disposition == "right-sized"


def test_opus_task_on_fleet_clamped_to_local():
    rec = mr.route("dev", "fleet", now_ts=NOW)
    assert rec.base_tier == "opus" and rec.recommended_tier == "local"
    assert rec.disposition == "capability_gap"


def test_unknown_env_falls_back_to_safest_ceiling():
    rec = mr.route("dev", "mars", now_ts=NOW)
    assert rec.env == "fleet" and rec.ceiling == "local"


def test_unknown_task_kind_is_conservative_opus():
    rec = mr.route("who_knows", "qth", now_ts=NOW)
    assert rec.base_tier == "opus"


# ── eval-grounding: never trust tier-L for a kind it fails ──────────────────

def test_local_task_trusted_when_eval_passes():
    recs = [_eval_rec({"triage": _pk(2, 2)})]
    rec = mr.route("cadence_triage", "fleet", eval_records=recs, now_ts=NOW)
    assert rec.recommended_tier == "local"
    assert rec.evidence["l_trusted"] is True
    assert rec.evidence["eval_pass_rate"] == 1.0


def test_local_task_untrusted_when_eval_fails_on_fleet_stays_local_flagged():
    recs = [_eval_rec({"triage": _pk(0, 2)})]   # tier-L fails triage
    rec = mr.route("cadence_triage", "fleet", eval_records=recs, now_ts=NOW)
    # fleet ceiling IS local — nothing higher to escalate to, so it stays local
    # but flagged untrusted + capability_gap so a human escalates to qth.
    assert rec.recommended_tier == "local"
    assert rec.evidence["l_trusted"] is False
    assert rec.disposition == "capability_gap"
    assert "NOT" in rec.why


def test_local_task_eval_fail_on_qth_escalates_to_opus():
    recs = [_eval_rec({"triage": _pk(0, 2)})]
    rec = mr.route("cadence_triage", "qth", eval_records=recs, now_ts=NOW)
    assert rec.recommended_tier == "opus"   # qth can go higher than local
    assert rec.evidence["l_trusted"] is False


def test_unknown_eval_competence_is_not_forged_healthy():
    # No eval record covers 'compile' → competence UNKNOWN, never a fake pass.
    rec = mr.route("compile_rule", "fleet", eval_records=[], now_ts=NOW)
    assert rec.recommended_tier == "local"
    assert rec.evidence["l_trusted"] is None       # not True, not False
    assert rec.evidence["eval_pass_rate"] is None
    assert "UNKNOWN" in rec.why


# ── disposition vs the running tier (the model_advisor tell) ────────────────

def test_upshift_when_recommended_exceeds_running():
    rec = mr.route("adversarial_review", "qth", running_tier="opus", now_ts=NOW)
    assert rec.recommended_tier == "frontier" and rec.disposition == "upshift"
    assert "UPSHIFT" in rec.why


def test_downshift_when_running_exceeds_recommended():
    rec = mr.route("formatting", "qth", running_tier="frontier", now_ts=NOW)
    assert rec.recommended_tier == "fast" and rec.disposition == "downshift"


def test_right_sized_when_equal():
    rec = mr.route("dev", "qth", running_tier="opus", now_ts=NOW)
    assert rec.recommended_tier == "opus" and rec.disposition == "right-sized"


# ── evidence readers ────────────────────────────────────────────────────────

def test_eval_kind_competence_uses_latest_record_with_kind():
    recs = [_eval_rec({"triage": _pk(1, 2)}),
            _eval_rec({"oracle": _pk(5, 5)}),        # newer, no triage
            _eval_rec({"triage": _pk(2, 2)})]        # newest triage wins
    assert mr.eval_kind_competence(recs, "triage") == (1.0, 2)
    assert mr.eval_kind_competence(recs, "compile") == (None, 0)


def test_calib_reliability_groups_by_model():
    fold = {
        "held": [{"model_id": "m1"}, {"model_id": "m1"}, {"model_id": "m2"}],
        "broke": [{"model_id": "m1"}],
    }
    rel = mr.calib_reliability_by_model(fold)
    assert rel["m1"] == {"held": 2, "broke": 1, "ratio": round(2 / 3, 3)}
    assert rel["m2"]["ratio"] == 1.0


def test_running_model_reliability_surfaced_not_acted_on():
    rel = {"claude-x": {"held": 9, "broke": 1, "ratio": 0.9}}
    rec = mr.route("dev", "qth", model_reliability=rel,
                   running_model="claude-x", running_tier="opus", now_ts=NOW)
    assert rec.evidence["running_model_held_ratio"] == 0.9
    assert rec.recommended_tier == "opus"   # reliability did NOT change the tier


# ── env detection ───────────────────────────────────────────────────────────

def test_detect_env_precedence(monkeypatch):
    monkeypatch.delenv("MESHFORGE_ROUTER_ENV", raising=False)
    assert mr.detect_env("field") == "field"                 # explicit override
    monkeypatch.setenv("MESHFORGE_ROUTER_ENV", "qth")
    assert mr.detect_env() == "qth"                          # env var
    monkeypatch.delenv("MESHFORGE_ROUTER_ENV")
    assert mr.detect_env(role="primary") == "qth"            # manager box
    assert mr.detect_env() == "fleet"                        # default


# ── self-scoring ledger ─────────────────────────────────────────────────────

def test_record_routing_round_trip(tmp_path):
    rec = mr.route("cadence_triage", "fleet",
                   eval_records=[_eval_rec({"triage": _pk(2, 2)})], now_ts=NOW)
    path = str(tmp_path / "routing.jsonl")
    assert mr.record_routing(rec, path=path) is None
    rows = [json.loads(l) for l in open(path).read().splitlines() if l.strip()]
    assert rows[-1]["kind"] == "routing"
    assert rows[-1]["status"] == "open"       # verdict re-derived later, not now
    assert rows[-1]["recommended_tier"] == "local"


# ── model-agnostic invariant ────────────────────────────────────────────────

def test_router_never_emits_a_model_id():
    # Every recommendation is a TIER from the ladder, never a concrete model.
    for kind in mr.TASK_TIER:
        for env in mr.ENVS:
            rec = mr.route(kind, env, now_ts=NOW)
            assert rec.recommended_tier in mr.TIERS


# ── CLI ─────────────────────────────────────────────────────────────────────

def test_cli_json_output(capsys):
    rc = mr.main(["--task-kind", "novel_design", "--env", "fleet", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["recommended_tier"] == "local" and out["disposition"] == "capability_gap"


def test_cli_human_output(capsys):
    rc = mr.main(["--task-kind", "dev", "--env", "qth", "--running-tier", "opus"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dev @ qth -> opus (right-sized)" in out
