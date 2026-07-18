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


def test_source_errors_named_in_state(tmp_path):
    """The failing source is NAMED in state['source_errors'] so a one-tick
    transient leaves an identifiable witness (not just a count), capped at
    8 entries x 160 chars so a mass-failure tick can't bloat the state file."""

    class ManyBroken(Source):
        name = "many"
        def collect(self):
            return [Condition(kind="source_error", subject=f"src{i}",
                              detail="x" * 500, source="many")
                    for i in range(12)]

    engine = _engine(tmp_path, [ManyBroken()], [])
    state = engine.tick()
    assert state["error_count"] == 12
    assert len(state["source_errors"]) == 8
    assert state["source_errors"][0].startswith("src0: xxx")
    assert all(len(e) <= 160 for e in state["source_errors"])
    # clean tick -> empty list, never absent (readers can trust the key)
    (tmp_path / "clean").mkdir(exist_ok=True)
    engine2 = _engine(tmp_path / "clean", [], [])
    state2 = engine2.tick()
    assert state2["source_errors"] == []


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


# === candidate promotion: single-slot backup + rollback (#6) ============

def _promote_engine(tmp_path, prior_rules, candidate_rules):
    """Build an engine with a canonical `prior_rules` and a candidate file."""
    rules_path = _write_rules(tmp_path, prior_rules)
    candidate = tmp_path / "rules.json.candidate"
    candidate.write_text(json.dumps({"rules": candidate_rules}))
    engine = RuleEngine(
        sources=[StaticSource([])],
        actions={"none": NoopAction()},
        rules_path=rules_path,
        state_path=str(tmp_path / "state.json"),
        history_path=str(tmp_path / "history.jsonl"),
        candidate_path=str(candidate),
    )
    return engine, rules_path


def test_promotion_writes_bak_equal_to_prior(tmp_path):
    """A successful promotion leaves a .bak byte-equal to the PRIOR canonical
    rules — the immediately-prior good version is recoverable."""
    prior = [{"id": "r1", "match": {"kind": "x"}, "action": {"kind": "none"}}]
    cand = [{"id": "r2", "match": {"kind": "y"}, "action": {"kind": "none"}}]
    engine, rules_path = _promote_engine(tmp_path, prior, cand)
    prior_bytes = open(rules_path, "rb").read()  # capture before overwrite

    engine.tick()

    # canonical now carries the candidate
    after = json.loads(open(rules_path).read())
    assert [r["id"] for r in after["rules"]] == ["r2"]
    # .bak preserves the prior good version, byte-for-byte
    bak = rules_path + ".bak"
    assert os.path.exists(bak)
    assert open(bak, "rb").read() == prior_bytes
    assert [r["id"] for r in json.loads(open(bak).read())["rules"]] == ["r1"]


def test_rejected_candidate_leaves_canonical_and_bak_untouched(tmp_path):
    """A malformed/rejected candidate must NOT overwrite canonical and must NOT
    create (or alter) the .bak — only a real promotion touches the backup."""
    prior = [{"id": "r1", "match": {"kind": "x"}, "action": {"kind": "none"}}]
    engine, rules_path = _promote_engine(
        tmp_path, prior, [{"id": "r2"}])  # missing match/action → invalid
    bak = rules_path + ".bak"
    assert not os.path.exists(bak)  # precondition

    engine.tick()

    after = json.loads(open(rules_path).read())
    assert [r["id"] for r in after["rules"]] == ["r1"]   # canonical unchanged
    assert not os.path.exists(bak)                        # no spurious backup


def test_first_run_promotion_no_canonical_skips_bak(tmp_path):
    """First-run case: no existing canonical file → promotion succeeds and
    writes NO .bak (nothing prior to preserve)."""
    candidate = tmp_path / "rules.json.candidate"
    candidate.write_text(json.dumps({"rules": [
        {"id": "r2", "match": {"kind": "y"}, "action": {"kind": "none"}}]}))
    rules_path = str(tmp_path / "rules.json")  # does NOT exist yet
    engine = RuleEngine(
        sources=[StaticSource([])],
        actions={"none": NoopAction()},
        rules_path=rules_path,
        state_path=str(tmp_path / "state.json"),
        history_path=str(tmp_path / "history.jsonl"),
        candidate_path=str(candidate),
    )

    engine.tick()

    assert [r["id"] for r in json.loads(open(rules_path).read())["rules"]] == ["r2"]
    assert not os.path.exists(rules_path + ".bak")  # no prior version existed


def test_restore_backup_recovers_prior_rules(tmp_path):
    """After a promotion, restore_backup() copies the .bak back over canonical —
    the rollback path for a bad-but-valid candidate."""
    prior = [{"id": "r1", "match": {"kind": "x"}, "action": {"kind": "none"}}]
    cand = [{"id": "r2", "match": {"kind": "y"}, "action": {"kind": "none"}}]
    engine, rules_path = _promote_engine(tmp_path, prior, cand)
    engine.tick()
    assert [r["id"] for r in json.loads(open(rules_path).read())["rules"]] == ["r2"]

    res = engine.restore_backup()
    assert res["restored"] is True
    assert [r["id"] for r in json.loads(open(rules_path).read())["rules"]] == ["r1"]


def test_restore_backup_without_backup_reports_false(tmp_path):
    """restore_backup() with no .bak present is a safe no-op reporting restored=False."""
    rules_path = _write_rules(tmp_path, [{"id": "r1", "match": {"kind": "x"},
                                          "action": {"kind": "none"}}])
    engine = RuleEngine(
        sources=[StaticSource([])],
        actions={"none": NoopAction()},
        rules_path=rules_path,
        state_path=str(tmp_path / "state.json"),
        history_path=str(tmp_path / "history.jsonl"),
    )
    res = engine.restore_backup()
    assert res["restored"] is False
    # canonical untouched
    assert [r["id"] for r in json.loads(open(rules_path).read())["rules"]] == ["r1"]


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


# === grace / debounce (grace_s) ==================================

def test_grace_holds_until_condition_persists(tmp_path, monkeypatch):
    """A rule with grace_s does NOT fire until the condition has matched
    continuously for >= grace_s — the federator-flap suppressor."""
    import mini_dudeai.engine as eng
    clock = {"t": 1000.0}
    monkeypatch.setattr(eng.time, "time", lambda: clock["t"])

    rec = RecordingAction()
    src = StaticSource([Condition(kind="src_err", subject="federator", detail="blind")])
    engine = _engine(
        tmp_path, [src],
        [{"id": "fed", "match": {"kind": "src_err"}, "action": {"kind": "recorder"},
          "grace_s": 90}],
        actions={"recorder": rec},
    )
    engine.tick()                  # t=1000 streak starts, no fire
    assert rec.calls == []
    clock["t"] = 1030.0
    engine.tick()                  # t=1030, 30s < 90s, still holding
    assert rec.calls == []
    clock["t"] = 1100.0
    engine.tick()                  # t=1100, 100s >= 90s -> fire
    assert rec.calls == [("fed", "federator", "edge_up")]


def test_grace_resets_on_transient_clear(tmp_path, monkeypatch):
    """A self-clearing transient never accumulates enough persistence to fire:
    the streak resets each time the condition is absent for a tick."""
    import mini_dudeai.engine as eng
    clock = {"t": 1000.0}
    monkeypatch.setattr(eng.time, "time", lambda: clock["t"])

    rec = RecordingAction()
    cond = Condition(kind="src_err", subject="federator", detail="blind")
    src = StaticSource([cond])
    engine = _engine(
        tmp_path, [src],
        [{"id": "fed", "match": {"kind": "src_err"}, "action": {"kind": "recorder"},
          "grace_s": 90}],
        actions={"recorder": rec},
    )
    engine.tick()                  # t=1000 streak starts
    clock["t"] = 1030.0
    src.conditions = []            # transient cleared (restart finished)
    engine.tick()                  # t=1030 streak resets, no fire
    clock["t"] = 1060.0
    src.conditions = [cond]        # blip again (next restart)
    engine.tick()                  # t=1060 fresh streak, 0s elapsed
    clock["t"] = 1075.0
    engine.tick()                  # t=1075, only 15s into new streak
    assert rec.calls == []         # never fired despite two appearances
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["rules"]["fed::federator"]["pending_since_ts"] == 1060.0


def test_grace_real_outage_eventually_fires(tmp_path, monkeypatch):
    """A genuine sustained outage outlasts grace and pages, as intended."""
    import mini_dudeai.engine as eng
    clock = {"t": 5000.0}
    monkeypatch.setattr(eng.time, "time", lambda: clock["t"])

    rec = RecordingAction()
    src = StaticSource([Condition(kind="src_err", subject="federator", detail="down")])
    engine = _engine(
        tmp_path, [src],
        [{"id": "fed", "match": {"kind": "src_err"}, "action": {"kind": "recorder"},
          "grace_s": 90}],
        actions={"recorder": rec},
    )
    engine.tick()                  # streak starts
    clock["t"] = 5200.0            # 200s of continuous blindness
    engine.tick()
    assert rec.calls == [("fed", "federator", "edge_up")]


def test_no_grace_fires_immediately(tmp_path):
    """Rules without grace_s are unaffected (backward compatible)."""
    rec = RecordingAction()
    src = StaticSource([Condition(kind="x", subject="foo", detail="d")])
    engine = _engine(
        tmp_path, [src],
        [{"id": "r1", "match": {"kind": "x"}, "action": {"kind": "recorder"}}],
        actions={"recorder": rec},
    )
    engine.tick()
    assert rec.calls == [("r1", "foo", "edge_up")]


# === per-tick brief writing (opt-in via brief_path) ==============

def test_no_brief_when_brief_path_unset(tmp_path):
    """Default (brief_path=None): _write_brief_safe is a no-op, no file created."""
    src = StaticSource([Condition(kind="x", subject="foo", detail="d")])
    engine = _engine(
        tmp_path, [src],
        [{"id": "r1", "match": {"kind": "x"}, "action": {"kind": "recorder"}}],
    )
    assert engine.brief_path is None
    engine.tick()
    engine._write_brief_safe()
    assert not (tmp_path / "mini_dudeai_brief.md").exists()


def test_brief_written_after_tick_when_brief_path_set(tmp_path):
    """When brief_path is set, _write_brief_safe atomic-writes a readable brief
    reflecting current state."""
    src = StaticSource([Condition(kind="x", subject="foo", detail="d")])
    brief = tmp_path / "mini_dudeai_brief.md"
    engine = _engine(
        tmp_path, [src],
        [{"id": "r1", "match": {"kind": "x"}, "action": {"kind": "recorder"}}],
    )
    engine.brief_path = str(brief)
    engine.tick()
    engine._write_brief_safe()
    assert brief.exists()
    text = brief.read_text()
    assert "mini-dudeai warm brief" in text


def test_brief_write_failure_never_raises(tmp_path):
    """A brief-write failure must not propagate — the observation loop survives
    a bad brief cycle exactly like a bad tick."""
    src = StaticSource([Condition(kind="x", subject="foo", detail="d")])
    engine = _engine(
        tmp_path, [src],
        [{"id": "r1", "match": {"kind": "x"}, "action": {"kind": "recorder"}}],
    )
    # Point at an unwritable path (a directory) so write_brief raises internally.
    engine.brief_path = str(tmp_path)  # a directory, not a file
    engine.tick()
    engine._write_brief_safe()  # must not raise


def test_fleet_preset_sets_brief_path(tmp_path, monkeypatch):
    """The fleet preset wires brief_path to ~/mini_dudeai_brief.md by default."""
    from mini_dudeai.presets.meshforge_fleet import build_engine
    monkeypatch.setenv("MINI_DUDEAI_NTFY_TOPIC", "test-topic")
    eng = build_engine(
        home=str(tmp_path),
        enable_federation=False,
        enable_digest=False,
    )
    assert eng.brief_path == os.path.join(str(tmp_path), "mini_dudeai_brief.md")


# === clean-exit marker (unexpected-reboot wiring, 2026-06-06) =====
#
# The marker is BootHealthSource's linchpin: the engine stamps it on graceful
# stop, so a planned reboot reads clean and a crash (marker stale/absent)
# reads unclean. These tests pin the writer side; the reader side is pinned
# in test_mini_dudeai_boot_health.py.


class MarkerProbeSource(Source):
    """Record whether the clean-exit marker exists at collect() time, then
    stop the engine — drives run() through exactly one tick. Pins the seeding
    ordering: the first tick must see the true (pre-seed) marker state."""

    name = "marker_probe"

    def __init__(self, marker_path):
        self.marker_path = marker_path
        self.engine = None  # set after construction
        self.marker_existed_at_collect = []

    def collect(self):
        self.marker_existed_at_collect.append(os.path.exists(self.marker_path))
        self.engine.request_stop()
        return []


def test_clean_exit_marker_written_on_graceful_stop(tmp_path):
    import time
    marker = tmp_path / "clean_exit"
    engine = _engine(tmp_path, [StaticSource()], [])
    engine.clean_exit_path = str(marker)
    engine.request_stop()           # graceful stop before any tick
    engine.run(interval_s=0.01)
    assert marker.exists()
    val = float(marker.read_text().strip())   # bare float, NOT json
    assert abs(time.time() - val) < 60


def test_no_marker_when_clean_exit_path_none(tmp_path):
    engine = _engine(tmp_path, [StaticSource()], [])
    assert engine.clean_exit_path is None     # standalone default unchanged
    engine.request_stop()
    engine.run(interval_s=0.01)               # must not raise
    assert not list(tmp_path.glob("*clean_exit*"))


def test_seed_happens_after_first_tick_not_before(tmp_path):
    """Deploy-window seed ordering: BootHealthSource (running inside the
    tick) must observe the marker ABSENT on the first tick of a fresh deploy
    — seeding before the tick would mask a real crash. After run() returns
    the marker exists (seed + graceful-stop write)."""
    marker = tmp_path / "clean_exit"
    probe = MarkerProbeSource(str(marker))
    engine = _engine(tmp_path, [probe], [])
    engine.clean_exit_path = str(marker)
    probe.engine = engine
    engine.run(interval_s=0.01)
    assert probe.marker_existed_at_collect == [False]
    assert marker.exists()


def test_seed_does_not_clobber_existing_marker(tmp_path):
    marker = tmp_path / "clean_exit"
    marker.write_text("123.0")
    engine = _engine(tmp_path, [StaticSource()], [])
    engine.clean_exit_path = str(marker)
    engine._seed_clean_exit_if_missing()
    assert marker.read_text() == "123.0"      # seed only creates, never overwrites


def test_marker_write_failure_does_not_raise(tmp_path):
    """Same contract as _write_brief_safe: a marker-write failure must not
    take down the stop path."""
    engine = _engine(tmp_path, [StaticSource()], [])
    engine.clean_exit_path = str(tmp_path)    # a directory — os.replace fails
    engine._write_clean_exit_marker()         # must not raise


# === failed-send retry (paging defect, 2026-06-11) =================
#
# Crash #4's [RED] page died in the boot+15s DNS race (URLError, name
# resolution) — recorded honestly (outcome.ok=false) and never tried again,
# 2-for-2 on real crashes. The engine now queues undelivered sends in the
# rule state and retries once per tick until delivered or exhausted.


class FlakyAction(Action):
    """Fail the first `fail_n` executes, succeed afterwards — the DNS-race shape."""

    name = "flaky"

    def __init__(self, fail_n):
        self.fail_n = fail_n
        self.calls = []  # (rule_id, subject, transition)

    def execute(self, rule, cond, transition):
        self.calls.append((rule["id"], cond.subject, transition))
        if len(self.calls) <= self.fail_n:
            return Outcome(action="flaky", ok=False,
                           error="URLError: Temporary failure in name resolution")
        return Outcome(action="flaky", ok=True)


def _hist(tmp_path):
    return [json.loads(line)
            for line in (tmp_path / "history.jsonl").read_text().splitlines()]


def _retry_engine(tmp_path, src, action, cooldown_s=3600):
    """Engine with one rule wired to `action` — cooldown ON by default so a
    delivered retry can't be confused with a cooldown-expired re-fire."""
    return _engine(
        tmp_path, [src],
        [{"id": "r1", "match": {"kind": "x"}, "action": {"kind": "flaky"},
          "cooldown_s": cooldown_s}],
        actions={"flaky": action},
    )


def test_failed_send_retries_next_tick_and_delivers(tmp_path, monkeypatch):
    import mini_dudeai.engine as eng
    clock = {"t": 1_000_000.0}  # realistic epoch scale: last_fired_ts=0 must read as "never", not "recently"
    monkeypatch.setattr(eng.time, "time", lambda: clock["t"])

    act = FlakyAction(fail_n=1)
    src = StaticSource([Condition(kind="x", subject="foo", detail="d")])
    engine = _retry_engine(tmp_path, src, act)
    engine.tick()                       # edge_up — send FAILS, queued
    assert [c[2] for c in act.calls] == ["edge_up"]
    clock["t"] = 1_000_030.0
    engine.tick()                       # no new edge; retry phase delivers
    assert [c[2] for c in act.calls] == ["edge_up", "edge_up"]
    hist = _hist(tmp_path)
    assert [h["transition"] for h in hist] == ["edge_up", "send_retry_delivered"]
    assert hist[0]["outcome"]["ok"] is False
    assert hist[1]["outcome"]["ok"] is True
    # queue drained
    state = json.loads((tmp_path / "state.json").read_text())
    assert "pending_sends" not in state["rules"]["r1::foo"]


def test_retry_not_attempted_in_failure_tick(tmp_path):
    """The retry phase must skip entries queued THIS tick — one attempt per
    tick, so a hard-down network costs one call per tick, not two."""
    act = FlakyAction(fail_n=99)
    src = StaticSource([Condition(kind="x", subject="foo", detail="d")])
    engine = _retry_engine(tmp_path, src, act)
    engine.tick()
    assert len(act.calls) == 1          # the fire only — no same-tick retry


def test_retry_delivery_bypasses_cooldown(tmp_path, monkeypatch):
    """A retry is delivery of an already-recorded fire, not a new fire: it
    runs inside the cooldown window and never touches fire bookkeeping."""
    import mini_dudeai.engine as eng
    clock = {"t": 1_000_000.0}  # realistic epoch scale: last_fired_ts=0 must read as "never", not "recently"
    monkeypatch.setattr(eng.time, "time", lambda: clock["t"])

    act = FlakyAction(fail_n=1)
    src = StaticSource([Condition(kind="x", subject="foo", detail="d")])
    engine = _retry_engine(tmp_path, src, act, cooldown_s=3600)
    engine.tick()
    clock["t"] = 1_000_030.0                 # 30s — deep inside cooldown
    engine.tick()
    assert [h["transition"] for h in _hist(tmp_path)][-1] == "send_retry_delivered"
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["rules"]["r1::foo"]["fire_count"] == 1   # retry ≠ new fire


def test_retry_exhausts_loudly_after_cap(tmp_path, monkeypatch):
    import mini_dudeai.engine as eng
    clock = {"t": 1_000_000.0}  # realistic epoch scale: last_fired_ts=0 must read as "never", not "recently"
    monkeypatch.setattr(eng.time, "time", lambda: clock["t"])

    act = FlakyAction(fail_n=999)       # network never comes back
    src = StaticSource([Condition(kind="x", subject="foo", detail="d")])
    engine = _retry_engine(tmp_path, src, act)
    for i in range(engine.SEND_RETRY_MAX_ATTEMPTS + 3):
        engine.tick()
        clock["t"] += 30.0
    hist = _hist(tmp_path)
    assert hist[-1]["transition"] == "send_retry_exhausted"
    assert hist[-1]["outcome"]["ok"] is False
    # exactly MAX attempts total (original + retries), then silence
    assert len(act.calls) == engine.SEND_RETRY_MAX_ATTEMPTS
    state = json.loads((tmp_path / "state.json").read_text())
    assert "pending_sends" not in state["rules"]["r1::foo"]


def test_retry_survives_daemon_restart(tmp_path, monkeypatch):
    """The queue lives in persisted rule state: a crash page queued seconds
    before a SECOND crash still delivers from the next boot's daemon."""
    import mini_dudeai.engine as eng
    clock = {"t": 1_000_000.0}  # realistic epoch scale: last_fired_ts=0 must read as "never", not "recently"
    monkeypatch.setattr(eng.time, "time", lambda: clock["t"])

    src = StaticSource([Condition(kind="x", subject="foo", detail="d")])
    engine = _retry_engine(tmp_path, src, FlakyAction(fail_n=999))
    engine.tick()                       # queued, then "the box dies"

    clock["t"] = 1_000_100.0
    act2 = FlakyAction(fail_n=0)        # next boot — network up
    engine2 = _retry_engine(tmp_path, src, act2)
    engine2.tick()
    assert ("r1", "foo", "edge_up") in act2.calls
    assert any(h["transition"] == "send_retry_delivered" for h in _hist(tmp_path))


def test_edge_down_clear_does_not_drop_pending_edge_up(tmp_path, monkeypatch):
    """The 08:53:39 lesson: the operator got 'cleared' for an alert they never
    received. A delivered cleared-notice must NOT cancel the undelivered
    alert — it still retries and lands."""
    import mini_dudeai.engine as eng
    clock = {"t": 1_000_000.0}  # realistic epoch scale: last_fired_ts=0 must read as "never", not "recently"
    monkeypatch.setattr(eng.time, "time", lambda: clock["t"])

    act = FlakyAction(fail_n=1)         # only the first send (the alert) fails
    src = StaticSource([Condition(kind="x", subject="foo", detail="d")])
    engine = _retry_engine(tmp_path, src, act)
    engine.tick()                       # edge_up FAILS, queued
    src.conditions = []                 # condition clears
    clock["t"] = 1_000_030.0
    engine.tick()                       # edge_down delivers; retry delivers the alert
    transitions = [c[2] for c in act.calls]
    assert transitions == ["edge_up", "edge_down", "edge_up"]
    assert any(h["transition"] == "send_retry_delivered" for h in _hist(tmp_path))


def test_unknown_action_kind_not_queued(tmp_path):
    """Config errors are not transient — retrying can't fix the ruleset, and
    the ok=False record already flags it."""
    src = StaticSource([Condition(kind="x", subject="foo", detail="d")])
    engine = _engine(
        tmp_path, [src],
        [{"id": "r1", "match": {"kind": "x"}, "action": {"kind": "nonsense"}}],
        actions={"none": NoopAction()},
    )
    engine.tick()
    engine.tick()
    state = json.loads((tmp_path / "state.json").read_text())
    assert "pending_sends" not in state["rules"]["r1::foo"]
    assert len(_hist(tmp_path)) == 1    # the one ok=False fire, no retry records


def test_pending_queue_cap_drops_oldest_loudly(tmp_path, monkeypatch):
    """An edge flapping while the network is down is bounded: overflow drops
    the OLDEST entry with a history record (a silently-vanished undelivered
    page is the defect class this whole mechanism closes)."""
    import mini_dudeai.engine as eng
    clock = {"t": 1_000_000.0}  # realistic epoch scale: last_fired_ts=0 must read as "never", not "recently"
    monkeypatch.setattr(eng.time, "time", lambda: clock["t"])

    act = FlakyAction(fail_n=999)
    cond = Condition(kind="x", subject="foo", detail="d")
    src = StaticSource([cond])
    engine = _retry_engine(tmp_path, src, act, cooldown_s=0)
    for i in range(engine.PENDING_SENDS_CAP + 2):   # flap: up,down,up,down,...
        engine.tick()
        src.conditions = [] if src.conditions else [cond]
        clock["t"] += 30.0
    hist = _hist(tmp_path)
    assert any(h["transition"] == "send_retry_dropped" for h in hist)
    state = json.loads((tmp_path / "state.json").read_text())
    assert len(state["rules"]["r1::foo"]["pending_sends"]) <= engine.PENDING_SENDS_CAP


def test_rule_removed_drops_pending_loudly(tmp_path, monkeypatch):
    import mini_dudeai.engine as eng
    clock = {"t": 1_000_000.0}  # realistic epoch scale: last_fired_ts=0 must read as "never", not "recently"
    monkeypatch.setattr(eng.time, "time", lambda: clock["t"])

    act = FlakyAction(fail_n=999)
    src = StaticSource([Condition(kind="x", subject="foo", detail="d")])
    engine = _retry_engine(tmp_path, src, act)
    engine.tick()                       # queued
    # rule vanishes from the ruleset while its send is pending
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps({"rules": [
        {"id": "other", "match": {"kind": "y"}, "action": {"kind": "flaky"}}]}))
    os.utime(rules_path, (1, 1))        # force the mtime gate to re-read
    clock["t"] = 1_000_030.0
    engine.tick()
    hist = _hist(tmp_path)
    dropped = [h for h in hist if h["transition"] == "send_retry_dropped"]
    assert len(dropped) == 1
    assert "rule removed" in dropped[0]["detail"]
    state = json.loads((tmp_path / "state.json").read_text())
    assert "pending_sends" not in state["rules"]["r1::foo"]


def test_pending_send_blocks_state_retirement(tmp_path):
    """prune_24h must never retire a state key carrying an undelivered send."""
    from mini_dudeai.state import StateStore
    state = {"rules": {"r1::foo": {
        "rule_id": "r1", "subject": "foo", "currently_active": False,
        "last_fired_ts": 1.0, "fires_window": [], "fire_count_24h": 0,
        "pending_since_ts": 0.0,
        "pending_sends": [{"transition": "edge_up", "attempts": 2}],
    }}}
    StateStore.prune_24h(state, now_ts=1.0 + StateStore.STALE_KEY_RETENTION_S + 10)
    assert "r1::foo" in state["rules"]


def test_state_set_supersede_prevents_stale_banner_resurrection(tmp_path, monkeypatch):
    """CONFIRMED review finding (2026-07-04): a failed edge_up display_alert
    queued for retry outlived a SUCCESSFUL edge_down clear — the retry then
    re-painted a recovered condition on the glass. STATE-SET actions declare
    supersedes_pending_sends: a newer transition retires the stale queue
    entry (latest state wins), with a send_retry_superseded witness. EVENT
    actions (ntfy) keep the pinned deliver-late semantics above."""
    import mini_dudeai.engine as eng
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(eng.time, "time", lambda: clock["t"])

    class StateSetFlaky(FlakyAction):
        supersedes_pending_sends = True

    act = StateSetFlaky(fail_n=1)       # the edge_up paint fails once
    src = StaticSource([Condition(kind="x", subject="foo", detail="d")])
    engine = _retry_engine(tmp_path, src, act)
    engine.tick()                       # edge_up FAILS -> queued
    src.conditions = []                 # condition recovers
    clock["t"] = 1_000_030.0
    engine.tick()                       # edge_down delivers; stale edge_up retired
    clock["t"] = 1_000_060.0
    engine.tick()                       # nothing left to resurrect
    transitions = [c[2] for c in act.calls]
    assert transitions == ["edge_up", "edge_down"]
    hist = _hist(tmp_path)
    assert any(h["transition"] == "send_retry_superseded" for h in hist)
    assert not any(h["transition"] == "send_retry_delivered" for h in hist)


def test_state_set_newer_failed_send_supersedes_older_queued(tmp_path, monkeypatch):
    """Both edges fail: a state-set queue holds only the LATEST state (the
    clear) — replaying up-then-down would flash a stale banner in between."""
    import mini_dudeai.engine as eng
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(eng.time, "time", lambda: clock["t"])

    class StateSetFlaky(FlakyAction):
        supersedes_pending_sends = True

    act = StateSetFlaky(fail_n=2)       # edge_up AND edge_down both fail once
    src = StaticSource([Condition(kind="x", subject="foo", detail="d")])
    engine = _retry_engine(tmp_path, src, act)
    engine.tick()                       # edge_up FAILS -> queued
    src.conditions = []
    clock["t"] = 1_000_030.0
    engine.tick()                       # edge_down FAILS -> supersedes up, queues down
    clock["t"] = 1_000_060.0
    engine.tick()                       # retry delivers the CLEAR only
    transitions = [c[2] for c in act.calls]
    assert transitions == ["edge_up", "edge_down", "edge_down"]
    hist = _hist(tmp_path)
    assert any(h["transition"] == "send_retry_superseded" for h in hist)
    assert any(h["transition"] == "send_retry_delivered"
               and "edge_down" in h["detail"] for h in hist)
