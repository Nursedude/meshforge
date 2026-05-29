"""Config loader tests: JSON → engine round-trip."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from mini_dudeai import (
    build_engine_from_config,
    load_config,
    register_action,
    register_source,
    registered_action_kinds,
    registered_source_kinds,
    validate_config,
)
from mini_dudeai.sources.base import Condition, Source
from mini_dudeai.actions.base import Action, Outcome


def test_load_config_missing_path(tmp_path):
    with pytest.raises(ValueError):
        load_config(str(tmp_path / "nope.json"))


def test_load_config_non_object(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("[]")
    with pytest.raises(ValueError):
        load_config(str(p))


def test_build_engine_from_minimal_config(tmp_path):
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"rules": []}))
    config = {
        "rules_path": str(rules),
        "state_path": str(tmp_path / "state.json"),
        "history_path": str(tmp_path / "history.jsonl"),
        "sources": [
            {"kind": "file_mtime", "path": str(tmp_path / "watch_me"),
             "max_age_s": 10},
        ],
        "actions": {
            "none": {"kind": "none"},
            "ntfy": {"kind": "ntfy", "topic": "t"},
        },
    }
    engine, interval = build_engine_from_config(config)
    assert interval == 30  # default
    assert len(engine.sources) == 1
    assert set(engine.actions.keys()) == {"none", "ntfy"}


def test_unknown_source_kind_raises(tmp_path):
    config = {
        "rules_path": str(tmp_path / "r.json"),
        "sources": [{"kind": "made_up"}],
        "actions": {},
    }
    with pytest.raises(ValueError):
        build_engine_from_config(config)


def test_unknown_action_kind_raises(tmp_path):
    config = {
        "rules_path": str(tmp_path / "r.json"),
        "sources": [],
        "actions": {"x": {"kind": "made_up"}},
    }
    with pytest.raises(ValueError):
        build_engine_from_config(config)


def test_config_supports_dotted_items_path(tmp_path):
    """A JsonFileSource with items_path='a.b' should dig two levels."""
    data = tmp_path / "data.json"
    data.write_text(json.dumps({
        "a": {"b": [{"subject": "x", "detail": "y"}]}
    }))
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"rules": []}))
    config = {
        "rules_path": str(rules),
        "state_path": str(tmp_path / "state.json"),
        "history_path": str(tmp_path / "history.jsonl"),
        "sources": [
            {"kind": "json_file", "path": str(data),
             "condition_kind": "thing", "items_path": "a.b"},
        ],
        "actions": {},
    }
    engine, _ = build_engine_from_config(config)
    conds = list(engine.sources[0].collect())
    assert [c.subject for c in conds] == ["x"]
    assert conds[0].kind == "thing"


def test_config_subject_field_remapping(tmp_path):
    """items have 'peer_name' instead of 'subject' — remap it."""
    data = tmp_path / "data.json"
    data.write_text(json.dumps({
        "items": [{"peer_name": "moc3", "last_error": "EOF"}]
    }))
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"rules": []}))
    config = {
        "rules_path": str(rules),
        "state_path": str(tmp_path / "state.json"),
        "history_path": str(tmp_path / "history.jsonl"),
        "sources": [
            {"kind": "json_file", "path": str(data),
             "condition_kind": "peer_unhealthy", "items_path": "items",
             "subject_field": "peer_name", "detail_field": "last_error"},
        ],
        "actions": {},
    }
    engine, _ = build_engine_from_config(config)
    conds = list(engine.sources[0].collect())
    assert conds[0].subject == "moc3"
    assert conds[0].detail == "EOF"


def test_interval_override(tmp_path):
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"rules": []}))
    config = {
        "rules_path": str(rules),
        "state_path": str(tmp_path / "state.json"),
        "history_path": str(tmp_path / "history.jsonl"),
        "sources": [], "actions": {},
        "interval_s": 90,
    }
    _, interval = build_engine_from_config(config)
    assert interval == 90


# --- registry + validation + boot_health (Track A) ----------------------

def test_seed_kinds_registered():
    assert {"file_mtime", "json_file", "http_json", "boot_health"} <= set(
        registered_source_kinds())
    assert {"ntfy", "annotate", "propose_escalation", "none"} <= set(
        registered_action_kinds())


def test_register_custom_source_and_action_round_trip(tmp_path):
    """A stranger can register adapters by name and reference them from config."""
    class DummySource(Source):
        name = "dummy"
        def __init__(self, label):
            self.label = label
        def collect(self):
            yield Condition(kind="dummy", subject=self.label, detail="hi")

    class DummyAction(Action):
        name = "dummy_act"
        def execute(self, rule, cond, transition):
            return Outcome(action="dummy_act", ok=True)

    register_source("dummy", lambda spec: DummySource(spec["label"]))
    register_action("dummy_act", lambda spec: DummyAction())

    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"rules": []}))
    config = {
        "rules_path": str(rules),
        "state_path": str(tmp_path / "state.json"),
        "history_path": str(tmp_path / "history.jsonl"),
        "sources": [{"kind": "dummy", "label": "moo"}],
        "actions": {"act": {"kind": "dummy_act"}},
    }
    engine, _ = build_engine_from_config(config)
    conds = list(engine.sources[0].collect())
    assert conds[0].subject == "moo"
    assert "act" in engine.actions


def test_validate_config_missing_rules_path():
    errs = validate_config({"sources": [], "actions": {}})
    assert any("rules_path" in e for e in errs)


def test_validate_config_reports_missing_field_with_path():
    errs = validate_config({
        "rules_path": "~/r.json",
        "sources": [{"kind": "file_mtime", "path": "~/x"}],  # missing max_age_s
    })
    assert any("sources[0]" in e and "max_age_s" in e for e in errs)


def test_validate_config_unknown_kind_lists_registered():
    errs = validate_config({
        "rules_path": "~/r.json",
        "sources": [{"kind": "totally_made_up"}],
    })
    assert any("totally_made_up" in e and "registered" in e for e in errs)


def test_build_raises_with_all_errors_at_once(tmp_path):
    config = {
        # no rules_path, bad source, bad action — all should surface
        "sources": [{"kind": "http_json"}],          # missing url + condition_kind
        "actions": {"a": {"kind": "ntfy"}},          # missing topic
    }
    with pytest.raises(ValueError) as ei:
        build_engine_from_config(config)
    msg = str(ei.value)
    assert "rules_path" in msg and "url" in msg and "topic" in msg


def test_valid_config_passes_validation(tmp_path):
    assert validate_config({
        "rules_path": str(tmp_path / "r.json"),
        "sources": [{"kind": "file_mtime", "path": "~/x", "max_age_s": 5}],
        "actions": {"n": {"kind": "none"}},
    }) == []


def test_boot_health_builds_from_config(tmp_path):
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"rules": []}))
    config = {
        "rules_path": str(rules),
        "state_path": str(tmp_path / "state.json"),
        "history_path": str(tmp_path / "history.jsonl"),
        "sources": [{
            "kind": "boot_health",
            "state_path": str(tmp_path / "ms.json"),
            "clean_exit_path": str(tmp_path / "ce"),
            "assessment_path": str(tmp_path / "ba.json"),
            "power_log_path": str(tmp_path / "power.log"),
            "boot_window_s": 600,
        }],
        "actions": {},
    }
    engine, _ = build_engine_from_config(config)
    src = engine.sources[0]
    assert src.boot_window_s == 600
    assert src.power_log_path == str(tmp_path / "power.log")
