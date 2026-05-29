"""Engine tests — pure-core, no fleet I/O.

What we pin:
  - edge_up fires exactly once per transition
  - still-firing doesn't re-fire
  - edge_down fires when condition clears
  - cooldown_s suppresses rapid re-fires
  - source errors become source_error conditions
  - action raising doesn't crash the tick
  - candidate ruleset promotion (happy + reject)
"""
import json
import os
import sys

# Make the package importable when pytest runs from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from mini_dudeai import Action, Condition, NoopAction, Outcome, RuleEngine, Source


class StaticSource(Source):
    """Emit a fixed list of Conditions each tick. Replace .conditions to flip
    the world between ticks."""

    name = "static"

    def __init__(self, conditions=None):
        self.conditions = list(conditions or [])

    def collect(self):
        return list(self.conditions)


class RecordingAction(Action):
    """Capture every execute() call."""

    name = "recorder"

    def __init__(self):
        self.calls = []  # list of (rule_id, subject, transition)

    def execute(self, rule, cond, transition):
        self.calls.append((rule["id"], cond.subject, transition))
        return Outcome(action="recorder", ok=True)


class CrashingAction(Action):
    name = "crash"

    def execute(self, rule, cond, transition):
        raise RuntimeError("synthetic crash")


def _write_rules(tmp_path, rules):
    p = tmp_path / "rules.json"
    p.write_text(json.dumps({"rules": rules}))
    return str(p)


def _engine(tmp_path, sources, rules, actions=None):
    """Helper to build an engine over tmp paths."""
    rules_path = _write_rules(tmp_path, rules)
    actions = actions or {"recorder": RecordingAction()}
    return RuleEngine(
        sources=sources,
        actions=actions,
        rules_path=rules_path,
        state_path=str(tmp_path / "state.json"),
        history_path=str(tmp_path / "history.jsonl"),
        candidate_path=str(tmp_path / "rules.json.candidate"),
    )


# === edge transitions ============================================

def test_edge_up_fires_once(tmp_path):
    rec = RecordingAction()
    src = StaticSource([Condition(kind="x", subject="foo", detail="d")])
    engine = _engine(
        tmp_path, [src],
        [{"id": "r1", "match": {"kind": "x"}, "action": {"kind": "recorder"}}],
        actions={"recorder": rec},
    )
    engine.tick()
    engine.tick()  # condition still present — should NOT re-fire
    assert rec.calls == [("r1", "foo", "edge_up")]


def test_edge_down_fires_on_clear(tmp_path):
    rec = RecordingAction()
    src = StaticSource([Condition(kind="x", subject="foo", detail="d")])
    engine = _engine(
        tmp_path, [src],
        [{"id": "r1", "match": {"kind": "x"}, "action": {"kind": "recorder"}}],
        actions={"recorder": rec},
    )
    engine.tick()  # edge_up
    src.conditions = []  # world clears
    engine.tick()  # edge_down
    assert rec.calls == [("r1", "foo", "edge_up"), ("r1", "foo", "edge_down")]


def test_cooldown_suppresses_immediate_refire(tmp_path):
    rec = RecordingAction()
    src = StaticSource([Condition(kind="x", subject="foo", detail="d")])
    engine = _engine(
        tmp_path, [src],
        [{"id": "r1", "match": {"kind": "x"}, "action": {"kind": "recorder"},
          "cooldown_s": 3600}],
        actions={"recorder": rec},
    )
    engine.tick()                  # edge_up
    src.conditions = []
    engine.tick()                  # edge_down
    src.conditions = [Condition(kind="x", subject="foo", detail="d")]
    engine.tick()                  # would-be edge_up; cooldown suppresses
    assert [c[2] for c in rec.calls] == ["edge_up", "edge_down"]


def test_independent_subjects_track_separately(tmp_path):
    rec = RecordingAction()
    src = StaticSource([
        Condition(kind="x", subject="foo", detail=""),
        Condition(kind="x", subject="bar", detail=""),
    ])
    engine = _engine(
        tmp_path, [src],
        [{"id": "r1", "match": {"kind": "x"}, "action": {"kind": "recorder"}}],
        actions={"recorder": rec},
    )
    engine.tick()
    assert len(rec.calls) == 2
    assert {c[1] for c in rec.calls} == {"foo", "bar"}


# === matching ====================================================

def test_subject_glob(tmp_path):
    rec = RecordingAction()
    src = StaticSource([
        Condition(kind="x", subject="moc3", detail=""),
        Condition(kind="x", subject="moc1", detail=""),
    ])
    engine = _engine(
        tmp_path, [src],
        [{"id": "r1", "match": {"kind": "x", "subject_glob": "moc3"},
          "action": {"kind": "recorder"}}],
        actions={"recorder": rec},
    )
    engine.tick()
    assert [c[1] for c in rec.calls] == ["moc3"]


def test_legacy_peer_glob_alias(tmp_path):
    """Old rules using peer_glob keep working (backwards compat)."""
    rec = RecordingAction()
    src = StaticSource([Condition(kind="federation_peer_unhealthy",
                                   subject="moc3-thing", detail="")])
    engine = _engine(
        tmp_path, [src],
        [{"id": "r1",
          "match": {"kind": "federation_peer_unhealthy", "peer_glob": "*moc3*"},
          "action": {"kind": "recorder"}}],
        actions={"recorder": rec},
    )
    engine.tick()
    assert [c[1] for c in rec.calls] == ["moc3-thing"]


def test_extras_filter(tmp_path):
    """match.class only matches conds whose extras.class equals it."""
    rec = RecordingAction()
    src = StaticSource([
        Condition(kind="signal_class", subject="s1", detail="",
                  extras={"class": "alpha"}),
        Condition(kind="signal_class", subject="s2", detail="",
                  extras={"class": "beta"}),
    ])
    engine = _engine(
        tmp_path, [src],
        [{"id": "r1",
          "match": {"kind": "signal_class", "class": "alpha"},
          "action": {"kind": "recorder"}}],
        actions={"recorder": rec},
    )
    engine.tick()
    assert [c[1] for c in rec.calls] == ["s1"]


def test_missing_subject_glob_matches_all(tmp_path):
    rec = RecordingAction()
    src = StaticSource([Condition(kind="x", subject="anything", detail="")])
    engine = _engine(
        tmp_path, [src],
        [{"id": "r1", "match": {"kind": "x"}, "action": {"kind": "recorder"}}],
        actions={"recorder": rec},
    )
    engine.tick()
    assert len(rec.calls) == 1


def test_subject_exclude_globs(tmp_path):
    """A catch-all rule fires on everything EXCEPT excluded subjects — lets an
    'unexpected peer' rule coexist with a known-normal suppressor (B2)."""
    rec = RecordingAction()
    src = StaticSource([
        Condition(kind="federation_peer_unhealthy", subject="peer-alpha", detail=""),
        Condition(kind="federation_peer_unhealthy", subject="peer-beta", detail=""),
    ])
    engine = _engine(
        tmp_path, [src],
        [{"id": "catch_all",
          "match": {"kind": "federation_peer_unhealthy", "subject_glob": "*",
                    "subject_exclude_globs": ["*alpha*"]},
          "action": {"kind": "recorder"}}],
        actions={"recorder": rec},
    )
    engine.tick()
    # alpha excluded; beta fires
    assert [c[1] for c in rec.calls] == ["peer-beta"]


def test_subject_exclude_globs_multiple_patterns(tmp_path):
    rec = RecordingAction()
    src = StaticSource([
        Condition(kind="x", subject="a", detail=""),
        Condition(kind="x", subject="b", detail=""),
        Condition(kind="x", subject="c", detail=""),
    ])
    engine = _engine(
        tmp_path, [src],
        [{"id": "r1",
          "match": {"kind": "x", "subject_glob": "*",
                    "subject_exclude_globs": ["a", "c"]},
          "action": {"kind": "recorder"}}],
        actions={"recorder": rec},
    )
    engine.tick()
    assert [c[1] for c in rec.calls] == ["b"]


# === resilience ==================================================

def test_action_crash_does_not_break_tick(tmp_path):
    src = StaticSource([Condition(kind="x", subject="foo", detail="")])
    engine = _engine(
        tmp_path, [src],
        [{"id": "r1", "match": {"kind": "x"}, "action": {"kind": "crash"}}],
        actions={"crash": CrashingAction()},
    )
    state = engine.tick()
    # No exception escaped. History has the recorded failure outcome.
    hist = (tmp_path / "history.jsonl").read_text().splitlines()
    assert len(hist) == 1
    entry = json.loads(hist[0])
    assert entry["outcome"]["ok"] is False
    assert "synthetic crash" in entry["outcome"]["error"]


def test_unknown_action_kind_recorded_not_raised(tmp_path):
    src = StaticSource([Condition(kind="x", subject="foo", detail="")])
    engine = _engine(
        tmp_path, [src],
        [{"id": "r1", "match": {"kind": "x"}, "action": {"kind": "nonsense"}}],
        actions={"none": NoopAction()},
    )
    engine.tick()  # must not raise
    entry = json.loads((tmp_path / "history.jsonl").read_text().splitlines()[0])
    assert entry["outcome"]["ok"] is False
    assert "nonsense" in entry["outcome"]["error"]


def test_source_error_emitted_as_condition(tmp_path):
    """A source that raises during collect() gets recorded as a source_error
    condition that rules can react to."""

    class BrokenSource(Source):
        name = "broken"
        def collect(self):
            raise RuntimeError("boom")

    rec = RecordingAction()
    engine = _engine(
        tmp_path, [BrokenSource()],
        [{"id": "r1",
          "match": {"kind": "source_error", "subject_glob": "broken"},
          "action": {"kind": "recorder"}}],
        actions={"recorder": rec},
    )
    state = engine.tick()
    assert state["error_count"] == 1
    assert [c[2] for c in rec.calls] == ["edge_up"]


# === candidate promotion ========================================

def test_candidate_promoted_when_valid(tmp_path):
    src = StaticSource([])
    rules_path = _write_rules(tmp_path, [{"id": "r1", "match": {"kind": "x"},
                                          "action": {"kind": "none"}}])
    candidate = tmp_path / "rules.json.candidate"
    candidate.write_text(json.dumps({"rules": [
        {"id": "r2", "match": {"kind": "y"}, "action": {"kind": "none"}}
    ]}))
    engine = RuleEngine(
        sources=[src],
        actions={"none": NoopAction()},
        rules_path=rules_path,
        state_path=str(tmp_path / "state.json"),
        history_path=str(tmp_path / "history.jsonl"),
        candidate_path=str(candidate),
    )
    engine.tick()
    after = json.loads(open(rules_path).read())
    assert [r["id"] for r in after["rules"]] == ["r2"]
    assert not candidate.exists()


def test_candidate_rejected_when_invalid(tmp_path):
    src = StaticSource([])
    rules_path = _write_rules(tmp_path, [{"id": "r1", "match": {"kind": "x"},
                                          "action": {"kind": "none"}}])
    candidate = tmp_path / "rules.json.candidate"
    candidate.write_text(json.dumps({"rules": [
        {"id": "r2"}  # missing match/action — invalid
    ]}))
    engine = RuleEngine(
        sources=[src],
        actions={"none": NoopAction()},
        rules_path=rules_path,
        state_path=str(tmp_path / "state.json"),
        history_path=str(tmp_path / "history.jsonl"),
        candidate_path=str(candidate),
    )
    engine.tick()
    # canonical unchanged
    after = json.loads(open(rules_path).read())
    assert [r["id"] for r in after["rules"]] == ["r1"]
    # candidate still present so cloud session can fix and retry
    assert candidate.exists()


# === observability ===============================================

def test_fire_counter_increments(tmp_path):
    rec = RecordingAction()
    src = StaticSource([Condition(kind="x", subject="foo", detail="")])
    engine = _engine(
        tmp_path, [src],
        [{"id": "r1", "match": {"kind": "x"}, "action": {"kind": "recorder"}}],
        actions={"recorder": rec},
    )
    engine.tick()                              # edge_up
    src.conditions = []
    engine.tick()                              # edge_down
    src.conditions = [Condition(kind="x", subject="foo", detail="")]
    # tick once more after cooldown expiry to flip back up
    # (no cooldown in this rule)
    engine.tick()                              # edge_up #2
    state = json.loads((tmp_path / "state.json").read_text())
    rs = state["rules"]["r1::foo"]
    assert rs["fire_count"] == 2
    assert rs["fire_count_24h"] == 2


def test_state_file_has_meta_fields(tmp_path):
    src = StaticSource([])
    engine = _engine(
        tmp_path, [src],
        [{"id": "r1", "match": {"kind": "x"}, "action": {"kind": "none"}}],
        actions={"none": NoopAction()},
    )
    engine.tick()
    state = json.loads((tmp_path / "state.json").read_text())
    for k in ("last_tick_ts", "last_tick_iso", "rule_count", "condition_count",
              "error_count", "fire_count", "host"):
        assert k in state, f"missing meta field: {k}"
