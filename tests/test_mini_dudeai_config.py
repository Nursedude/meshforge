"""Config loader tests: JSON → engine round-trip."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from mini_dudeai import build_engine_from_config, load_config


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
