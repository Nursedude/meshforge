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


# ── warm-brief consumer: routing_context_block ──────────────────────────────

def test_routing_context_block_renders_with_eval_records():
    recs = [_eval_rec({"triage": _pk(2, 2), "compile": _pk(1, 3),
                       "oracle": _pk(18, 18)})]
    block = mr.routing_context_block(env="qth", eval_records=recs)
    assert "routing context" in block and "env **qth**" in block
    assert "triage 1.0✓" in block          # passes the gate
    assert "compile 0.333✗" in block        # below gate → ✗
    assert "model_router --task-kind" in block


def test_routing_context_block_empty_without_eval_records():
    # A box that doesn't evaluate tier-L has no measured competence to report.
    assert mr.routing_context_block(env="fleet", eval_records=[]) == ""


# ── cadence-launcher consumer: --l-trusted-gate exit codes ──────────────────

def _rec_with_l_trusted(value):
    return mr.Recommendation(
        task_kind="cadence_triage", env="qth", base_tier="local",
        ceiling="frontier", recommended_tier="local", disposition="right-sized",
        why="test", evidence={"l_trusted": value}, ts=NOW)


def test_l_trusted_gate_exit_codes(monkeypatch):
    monkeypatch.setattr(mr, "_load_default_evidence", lambda: ([], {}))
    monkeypatch.setattr(mr, "_detect_role", lambda: None)
    for value, expected_rc in ((True, 0), (False, 2), (None, 3)):
        monkeypatch.setattr(mr, "route",
                            lambda *a, _v=value, **k: _rec_with_l_trusted(_v))
        rc = mr.main(["--task-kind", "cadence_triage", "--env", "qth",
                      "--l-trusted-gate"])
        assert rc == expected_rc, f"l_trusted={value} -> rc {rc}, want {expected_rc}"


# ── WS-E-2b: routing verdict re-derivation ──────────────────────────────────

def _eval_at(ts, per_kind, tier="local"):
    return {"ts": ts, "brain_tier": tier, "pass_rate": 1.0, "per_kind": per_kind}


def _routed_local_row(l_trusted=True, eval_kind="triage", ts=NOW,
                      recommended="local"):
    """A recorded routing row as record_routing writes it (with a stable id)."""
    rec = mr.Recommendation(
        task_kind="cadence_triage", env="fleet", base_tier="local",
        ceiling="local", recommended_tier=recommended, disposition="right-sized",
        why="test", evidence={"eval_kind": eval_kind, "l_trusted": l_trusted},
        ts=ts)
    return {"kind": mr.ROUTING_KIND,
            "id": mr.make_routing_id(ts, rec.task_kind, rec.env, recommended),
            **rec.to_row(), "status": "open"}


def test_make_routing_id_is_deterministic():
    a = mr.make_routing_id(NOW, "cadence_triage", "fleet", "local")
    b = mr.make_routing_id(NOW, "cadence_triage", "fleet", "local")
    c = mr.make_routing_id(NOW, "cadence_triage", "qth", "local")
    assert a == b and a != c and len(a) == 12


def test_record_routing_row_carries_stable_id(tmp_path):
    rec = mr.route("cadence_triage", "fleet",
                   eval_records=[_eval_rec({"triage": _pk(2, 2)})], now_ts=NOW)
    path = str(tmp_path / "routing.jsonl")
    mr.record_routing(rec, path=path)
    rows = mr.load_routing_events(path)
    assert rows[-1]["id"] == mr.make_routing_id(
        NOW, "cadence_triage", "fleet", "local")


def test_competence_after_requires_strictly_later_local_evidence():
    recs = [_eval_at(NOW - 10, {"triage": _pk(2, 2)}),      # too old
            _eval_at(NOW + 10, {"triage": _pk(1, 2)})]      # later, 0.5
    assert mr.competence_after(recs, "triage", NOW) == (0.5, 2)
    # nothing newer than a far-future anchor → UNKNOWN, never forged
    assert mr.competence_after(recs, "triage", NOW + 100) == (None, 0)


def test_competence_after_ignores_non_local_tier():
    recs = [_eval_at(NOW + 10, {"triage": _pk(1, 1)}, tier="api_small")]
    assert mr.competence_after(recs, "triage", NOW) == (None, 0)


def test_rederive_routing_holds_when_later_eval_still_passes():
    events = [_routed_local_row(l_trusted=True, ts=NOW)]
    later = [_eval_at(NOW + 100, {"triage": _pk(2, 2)})]     # 1.0 ≥ gate
    new = mr.rederive_routing(events, later, NOW + 200)
    assert len(new) == 1 and new[0]["outcome"] == "held"
    assert new[0]["routing_id"] == events[0]["id"]


def test_rederive_routing_breaks_when_later_eval_fails():
    events = [_routed_local_row(l_trusted=True, ts=NOW)]
    later = [_eval_at(NOW + 100, {"triage": _pk(1, 4)})]     # 0.25 < gate
    new = mr.rederive_routing(events, later, NOW + 200)
    assert len(new) == 1 and new[0]["outcome"] == "broke"


def test_rederive_routing_skips_non_asserting_and_non_local():
    # l_trusted not True (no positive competence claim) → no verdict
    a = mr.rederive_routing([_routed_local_row(l_trusted=False)],
                            [_eval_at(NOW + 100, {"triage": _pk(1, 4)})], NOW + 200)
    b = mr.rederive_routing([_routed_local_row(l_trusted=None)],
                            [_eval_at(NOW + 100, {"triage": _pk(1, 4)})], NOW + 200)
    # a higher-tier routing is never re-derived
    c = mr.rederive_routing([_routed_local_row(recommended="opus")],
                            [_eval_at(NOW + 100, {"triage": _pk(1, 4)})], NOW + 200)
    assert a == [] and b == [] and c == []


def test_rederive_routing_leaves_open_without_later_evidence():
    events = [_routed_local_row(ts=NOW)]
    # only same-or-older eval — circular, not independent
    assert mr.rederive_routing(events, [_eval_at(NOW, {"triage": _pk(2, 2)})],
                               NOW + 200) == []


def test_fold_routing_buckets_by_latest_verdict():
    row = _routed_local_row(ts=NOW)
    events = [row,
              {"kind": mr.ROUTING_VERDICT_KIND, "routing_id": row["id"],
               "ts": NOW + 1, "outcome": "broke"},
              {"kind": mr.ROUTING_VERDICT_KIND, "routing_id": row["id"],
               "ts": NOW + 2, "outcome": "held"}]   # latest wins
    state = mr.fold_routing(events)
    assert state["n_total"] == 1 and state["n_held"] == 1 and state["n_broke"] == 0
    assert state["ratio"] == 1.0


def test_rederive_routing_and_persist_round_trips(tmp_path):
    path = str(tmp_path / "routing.jsonl")
    # seed one asserting local routing
    from mini_dudeai.history import append_jsonl
    append_jsonl(path, [_routed_local_row(ts=NOW)], 1_000_000)
    later = [_eval_at(NOW + 100, {"triage": _pk(2, 2)})]
    state = mr.rederive_routing_and_persist(path=path, eval_records=later,
                                            now_ts=NOW + 200)
    assert state["n_held"] == 1
    # verdict was persisted — a second pass mints nothing new
    state2 = mr.rederive_routing_and_persist(path=path, eval_records=later,
                                             now_ts=NOW + 300)
    assert state2["n_held"] == 1 and state2["n_open"] == 0


def test_format_routing_track_record():
    assert mr.format_routing_track_record({"n_total": 0}) == ""
    row = _routed_local_row(ts=NOW)
    state = mr.fold_routing(
        [row, {"kind": mr.ROUTING_VERDICT_KIND, "routing_id": row["id"],
               "ts": NOW + 1, "outcome": "broke"}])
    out = mr.format_routing_track_record(state)
    assert "routing self-score" in out and "1 broke" in out
    assert "did not hold" in out


# ── WS-E-2b: haiku-watcher promotion ────────────────────────────────────────

def _overall(ts, pass_rate, tier):
    return {"ts": ts, "brain_tier": tier, "pass_rate": pass_rate, "per_kind": {}}


def test_haiku_promotion_no_candidate_data_is_honest():
    rec = mr.haiku_promotion_recommendation([_overall(NOW, 1.0, "local")])
    assert rec["status"] == "no_candidate_data" and rec["promote"] is None
    assert mr.format_haiku_promotion(rec) == ""   # silent, no noise


def test_haiku_promotion_earned_when_clearly_better():
    recs = [_overall(NOW, 0.80, "local"),
            _overall(NOW + 1, 0.95, "api_small")]   # Δ0.15 ≥ 0.10
    rec = mr.haiku_promotion_recommendation(recs)
    assert rec["status"] == "earned" and rec["promote"] is True
    assert "⬆️" in mr.format_haiku_promotion(rec)


def test_haiku_promotion_rejected_when_local_sufficient():
    recs = [_overall(NOW, 0.95, "local"),
            _overall(NOW + 1, 0.97, "api_small")]   # Δ0.02 ≤ 0.05
    rec = mr.haiku_promotion_recommendation(recs)
    assert rec["status"] == "rejected" and rec["promote"] is False


def test_haiku_promotion_rejected_below_gate():
    recs = [_overall(NOW, 0.60, "local"),
            _overall(NOW + 1, 0.70, "api_small")]   # candidate < gate
    rec = mr.haiku_promotion_recommendation(recs)
    assert rec["status"] == "rejected" and rec["promote"] is False


def test_haiku_promotion_operator_call_between_margins():
    recs = [_overall(NOW, 0.88, "local"),
            _overall(NOW + 1, 0.95, "api_small")]   # Δ0.07 between .05 and .10
    rec = mr.haiku_promotion_recommendation(recs)
    assert rec["status"] == "operator_call" and rec["promote"] is None


# ── WS-E-2b: CLI ────────────────────────────────────────────────────────────

def test_cli_rederive(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "routing.jsonl")
    from mini_dudeai.history import append_jsonl
    append_jsonl(path, [_routed_local_row(ts=NOW)], 1_000_000)
    monkeypatch.setenv("MODEL_ROUTING_LEDGER_PATH", path)
    monkeypatch.setattr(mr, "_load_default_evidence",
                        lambda: ([_eval_at(NOW + 100, {"triage": _pk(2, 2)})], {}))
    monkeypatch.setattr(mr, "_detect_role", lambda: None)
    rc = mr.main(["--rederive"])
    assert rc == 0 and "1 held" in capsys.readouterr().out


def test_cli_haiku_check(monkeypatch, capsys):
    monkeypatch.setattr(mr, "_load_default_evidence",
                        lambda: ([_overall(NOW, 0.80, "local"),
                                  _overall(NOW + 1, 0.95, "api_small")], {}))
    monkeypatch.setattr(mr, "_detect_role", lambda: None)
    rc = mr.main(["--haiku-check"])
    assert rc == 0 and "earned" in capsys.readouterr().out
