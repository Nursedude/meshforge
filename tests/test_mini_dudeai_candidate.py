"""Tests for the candidate-authoring API (mini_dudeai.candidate).

This is the shared substrate both the in-app rule editor and the future
WireClaw-style chat-compiler write through. It must reject anything the daemon
would reject (same validator the engine uses) and write nothing on failure.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mini_dudeai import validate_rules_document, write_candidate  # noqa: E402
from mini_dudeai.engine import RuleEngine  # noqa: E402

GOOD = {"id": "r1", "match": {"kind": "source_error"}, "action": {"kind": "ntfy"}}


def test_validate_accepts_good_rule():
    rules, errors = validate_rules_document({"rules": [GOOD]})
    assert errors == [] and len(rules) == 1


def test_validate_rejects_missing_fields():
    _, e_id = validate_rules_document({"rules": [{"match": {}, "action": {}}]})
    _, e_match = validate_rules_document({"rules": [{"id": "x", "action": {}}]})
    _, e_action = validate_rules_document({"rules": [{"id": "x", "match": {}}]})
    _, e_top = validate_rules_document({"nope": []})
    assert e_id and e_match and e_action and e_top


def test_engine_validator_is_the_same_source_of_truth():
    # RuleEngine._validate_rules must delegate to validate_rules_document so a
    # candidate the editor accepts is one the daemon promotes.
    eng = RuleEngine(sources=[], actions={}, rules_path="x", state_path="s",
                     history_path="h")
    a = eng._validate_rules({"rules": [GOOD]})
    b = validate_rules_document({"rules": [GOOD]})
    assert a == b


def test_write_candidate_writes_valid(tmp_path):
    cand = tmp_path / "rules.json.candidate"
    ok, errors = write_candidate(str(cand), [GOOD])
    assert ok and errors == []
    doc = json.loads(cand.read_text())
    assert doc == {"rules": [GOOD]}


def test_write_candidate_refuses_invalid_and_writes_nothing(tmp_path):
    cand = tmp_path / "rules.json.candidate"
    ok, errors = write_candidate(str(cand), [{"match": {}, "action": {}}])  # no id
    assert ok is False and errors
    assert not cand.exists()                       # fail-loud: nothing written


def test_write_candidate_roundtrips_grace_edit(tmp_path):
    # The exact operational case: add grace_s to a rule (this morning's hand-edit).
    cand = tmp_path / "rules.json.candidate"
    edited = {**GOOD, "grace_s": 90}
    ok, _ = write_candidate(str(cand), [edited])
    assert ok
    assert json.loads(cand.read_text())["rules"][0]["grace_s"] == 90
