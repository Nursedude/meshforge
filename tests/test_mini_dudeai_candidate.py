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


# --- seed provenance (Issue #80 residual: seed CONTENT drift) ----------------

from mini_dudeai.candidate import (  # noqa: E402
    PROVENANCE_KEY, merge_seed_rules, rule_body_sha,
)

SEED_A = {"id": "a", "match": {"kind": "source_error"},
          "action": {"kind": "ntfy", "title": "t"}, "cooldown_s": 600}
SEED_B = {"id": "b", "match": {"kind": "signal_class", "class": "x"},
          "action": {"kind": "ntfy", "title": "u"}}


class TestRuleBodySha:
    def test_key_order_and_whitespace_invariant(self):
        reordered = {"cooldown_s": 600, "action": {"title": "t", "kind": "ntfy"},
                     "match": {"kind": "source_error"}, "id": "a"}
        assert rule_body_sha(SEED_A) == rule_body_sha(reordered)

    def test_provenance_stamp_excluded_from_hash(self):
        stamped = {**SEED_A, PROVENANCE_KEY: {"origin": "seed:x",
                                              "seed_sha": "deadbeef"}}
        assert rule_body_sha(stamped) == rule_body_sha(SEED_A)

    def test_body_change_changes_hash(self):
        assert rule_body_sha(SEED_A) != rule_body_sha(
            {**SEED_A, "cooldown_s": 60})


class TestMergeSeedRules:
    def test_missing_seed_rule_added_and_stamped(self):
        merged, report = merge_seed_rules([SEED_A], [SEED_A, SEED_B], "fed")
        assert report["added"] == ["b"]
        added = next(r for r in merged if r["id"] == "b")
        assert added[PROVENANCE_KEY] == {
            "origin": "seed:fed", "seed_sha": rule_body_sha(SEED_B)}

    def test_exact_copy_gets_stamp_bootstrap_ratchet(self):
        merged, report = merge_seed_rules([dict(SEED_A)], [SEED_A], "fed")
        assert report["stamped"] == ["a"]
        assert merged[0][PROVENANCE_KEY]["seed_sha"] == rule_body_sha(SEED_A)

    def test_idempotent_second_merge_is_unchanged(self):
        merged1, _ = merge_seed_rules([dict(SEED_A)], [SEED_A], "fed")
        merged2, report2 = merge_seed_rules(merged1, [SEED_A], "fed")
        assert report2["unchanged"] == ["a"]
        assert merged2 == merged1

    def test_stale_old_seed_copy_refreshed_to_current_seed(self):
        old_seed = {**SEED_A, "cooldown_s": 60}
        live = [{**old_seed,
                 PROVENANCE_KEY: {"origin": "seed:fed",
                                  "seed_sha": rule_body_sha(old_seed)}}]
        merged, report = merge_seed_rules(live, [SEED_A], "fed")
        assert report["refreshed"] == ["a"]
        assert merged[0]["cooldown_s"] == 600
        assert merged[0][PROVENANCE_KEY]["seed_sha"] == rule_body_sha(SEED_A)

    def test_box_tuned_rule_kept_verbatim(self):
        # stamped from the CURRENT seed, then tuned locally → tuning wins
        tuned = {**SEED_A, "cooldown_s": 30,
                 PROVENANCE_KEY: {"origin": "seed:fed",
                                  "seed_sha": rule_body_sha(SEED_A)}}
        merged, report = merge_seed_rules([tuned], [SEED_A], "fed")
        assert report["tuned"] == ["a"]
        assert merged[0]["cooldown_s"] == 30

    def test_unstamped_divergent_rule_is_tuned_not_refreshed(self):
        # pre-provenance hand edit: differs from seed, no stamp → exempt
        edited = {**SEED_A, "cooldown_s": 30}
        merged, report = merge_seed_rules([edited], [SEED_A], "fed")
        assert report["tuned"] == ["a"]
        assert merged[0]["cooldown_s"] == 30

    def test_box_local_rule_kept(self):
        local = {"id": "mf014_local", "match": {"kind": "source_error"},
                 "action": {"kind": "ntfy"}}
        merged, report = merge_seed_rules([local], [SEED_A], "fed")
        assert report["local"] == ["mf014_local"]
        assert report["added"] == ["a"]
        assert [r["id"] for r in merged] == ["mf014_local", "a"]

    def test_merged_output_validates(self):
        merged, _ = merge_seed_rules([dict(SEED_A)], [SEED_A, SEED_B], "fed")
        rules, errors = validate_rules_document({"rules": merged})
        assert errors == [] and len(rules) == 2


class TestSingleBannerConstraint:
    """2026-07-04 review fix: the claw seed's SINGLE-BANNER prose annotation
    is now a validator invariant — a second display_alert leg's empty-class
    clear would wipe the first's still-active banner (paging witness lost)."""

    def _banner(self, rid):
        return {"id": rid,
                "match": {"kind": "sensor_breach", "subject_glob": "*.t"},
                "action": {"kind": "nats",
                           "payload": {"tool": "display_alert",
                                       "class": "hot"},
                           "payload_down": {"tool": "display_alert",
                                            "class": ""}}}

    def test_second_display_alert_leg_is_an_error(self):
        doc = {"rules": [self._banner("b1"), self._banner("b2")]}
        _rules, errors = validate_rules_document(doc)
        assert any("display_alert" in e and "single-banner" in e
                   for e in errors)

    def test_one_banner_leg_is_fine(self):
        doc = {"rules": [self._banner("b1"), GOOD]}
        _rules, errors = validate_rules_document(doc)
        assert errors == []

    def test_payload_down_only_leg_counts(self):
        clear_only = {"id": "b2", "match": {"kind": "x"},
                      "action": {"kind": "nats",
                                 "payload": {"tool": "led_set"},
                                 "payload_down": {"tool": "display_alert",
                                                  "class": ""}}}
        _rules, errors = validate_rules_document(
            {"rules": [self._banner("b1"), clear_only]})
        assert errors  # a clear-only leg can still wipe b1's live banner

    def test_claw_seed_still_validates(self):
        seed = json.loads((Path(__file__).parent.parent / "configs" /
                           "mini_dudeai_rules.claw.json").read_text())
        rules, errors = validate_rules_document(seed)
        assert errors == []
        assert len(rules) == 5
