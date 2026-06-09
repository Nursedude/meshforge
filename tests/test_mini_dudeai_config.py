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
from mini_dudeai.config import (
    _ACTION_REGISTRY,
    _ACTION_REQUIRED,
    _SOURCE_REGISTRY,
    _SOURCE_REQUIRED,
)
from mini_dudeai.sources.base import Condition, Source
from mini_dudeai.actions.base import Action, Outcome

_CONFIGS_DIR = os.path.join(os.path.dirname(__file__), "..", "configs")
_SCHEMA_PATH = os.path.join(_CONFIGS_DIR, "mini_dudeai_config.schema.json")
_EXAMPLE_PATH = os.path.join(_CONFIGS_DIR, "mini_dudeai_config.example.json")


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
        "sources": [{"kind": "json_file", "path": "~/x"}],  # missing condition_kind
    })
    assert any("sources[0]" in e and "condition_kind" in e for e in errs)


def test_validate_config_accepts_file_mtime_without_max_age_s():
    """max_age_s is builder-defaulted (1800) — requiring it was validator/builder
    drift (2026-06-09 review): validate_config rejected a config the builder
    fully supported."""
    errs = validate_config({
        "rules_path": "~/r.json",
        "sources": [{"kind": "file_mtime", "path": "~/x"}],
    })
    assert errs == []


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


# --- schema-vs-validator drift guard (Risk #7) --------------------------
#
# configs/mini_dudeai_config.schema.json is a draft-07 reference for humans
# and editors; the runtime validates with a hand-rolled, dep-free validator
# (config.validate_config) backed by _SOURCE_REGISTRY/_ACTION_REGISTRY and
# the _SOURCE_REQUIRED/_ACTION_REQUIRED tables. The schema and the in-code
# tables independently re-enumerate the same source/action kinds + their
# required fields, and nothing else cross-checks them — so they can silently
# drift. These tests pin them in sync. If one fails, update WHICHEVER of the
# schema or config.py is wrong so the two agree (the failure message names
# the exact drifted kind/field).
#
# We compare the schema against the BUILT-IN surface — the keys of
# _SOURCE_REQUIRED / _ACTION_REQUIRED — NOT the live _*_REGISTRY dicts. The
# schema deliberately documents only the shipped/built-in kinds (its
# description says custom register_source()/register_action() kinds are
# accepted at runtime but intentionally not enumerated), and those registries
# are process-global and get polluted by other tests that register dummy
# kinds. The _*_REQUIRED tables are the canonical built-in enumeration: their
# comment in config.py says "the built-in kinds", and the seed builders
# register exactly those. A separate test asserts every built-in key is also
# in the live registry, so the required-table can't list a phantom kind.


def _load_schema() -> dict:
    """Parse the reference JSON Schema (stdlib only — no jsonschema dep)."""
    with open(_SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _schema_kind_enum(schema: dict, section: str) -> set:
    """Return the `kind` enum set for the 'sources' or 'actions' section.

    sources are a list of objects; actions are a map (additionalProperties).
    """
    if section == "sources":
        item = schema["properties"]["sources"]["items"]
    else:
        item = schema["properties"]["actions"]["additionalProperties"]
    return set(item["properties"]["kind"]["enum"])


def _schema_required_by_kind(schema: dict, section: str) -> dict:
    """Map each kind to its schema-required field set, from the allOf if/then.

    A kind with no allOf branch has no required fields beyond `kind`, so it
    maps to an empty set — the same meaning as an empty list in the in-code
    _*_REQUIRED tables.
    """
    if section == "sources":
        item = schema["properties"]["sources"]["items"]
    else:
        item = schema["properties"]["actions"]["additionalProperties"]
    out: dict[str, set] = {k: set() for k in _schema_kind_enum(schema, section)}
    for branch in item.get("allOf", []):
        kind = branch["if"]["properties"]["kind"]["const"]
        out[kind] = set(branch["then"]["required"])
    return out


def test_schema_source_kinds_match_builtins():
    """Schema source-kind enum == the built-in kinds (BUILTIN_SOURCE_KINDS —
    the snapshot taken after built-in registration; the live registry/required
    tables legitimately grow with third-party register_source calls, including
    the dummies other tests in this file register)."""
    from mini_dudeai.config import BUILTIN_SOURCE_KINDS
    schema_kinds = _schema_kind_enum(_load_schema(), "sources")
    code_kinds = set(BUILTIN_SOURCE_KINDS)
    assert schema_kinds == code_kinds, (
        "source-kind drift between schema and config.BUILTIN_SOURCE_KINDS: "
        f"only in schema={sorted(schema_kinds - code_kinds)}, "
        f"only in code={sorted(code_kinds - schema_kinds)}"
    )


def test_schema_action_kinds_match_builtins():
    """Schema action-kind enum == the built-in kinds (BUILTIN_ACTION_KINDS)."""
    from mini_dudeai.config import BUILTIN_ACTION_KINDS
    schema_kinds = _schema_kind_enum(_load_schema(), "actions")
    code_kinds = set(BUILTIN_ACTION_KINDS)
    assert schema_kinds == code_kinds, (
        "action-kind drift between schema and config.BUILTIN_ACTION_KINDS: "
        f"only in schema={sorted(schema_kinds - code_kinds)}, "
        f"only in code={sorted(code_kinds - schema_kinds)}"
    )


def test_register_source_required_fields_enforced():
    """required= declared at registration flows into validate_config — the
    single-registration-point fix for the max_age_s validator/builder drift."""
    from mini_dudeai.config import (
        _SOURCE_REGISTRY, _SOURCE_REQUIRED, register_source,
    )
    register_source("drift_test_kind", lambda spec: None,
                    required=("dev_path",))
    try:
        errs = validate_config({
            "rules_path": "~/r.json",
            "sources": [{"kind": "drift_test_kind"}],
        })
        assert any("dev_path" in e for e in errs)
    finally:
        _SOURCE_REGISTRY.pop("drift_test_kind", None)
        _SOURCE_REQUIRED.pop("drift_test_kind", None)


def test_builtin_kinds_are_actually_registered():
    """Every built-in _*_REQUIRED key must resolve in the live registry.

    Guards the other direction: the required-table can't name a kind the
    validator wouldn't accept (a phantom built-in). Uses subset (not ==)
    because the global registries may carry dummy kinds from sibling tests.
    """
    assert set(_SOURCE_REQUIRED) <= set(_SOURCE_REGISTRY), (
        "config._SOURCE_REQUIRED names kinds absent from _SOURCE_REGISTRY: "
        f"{sorted(set(_SOURCE_REQUIRED) - set(_SOURCE_REGISTRY))}"
    )
    assert set(_ACTION_REQUIRED) <= set(_ACTION_REGISTRY), (
        "config._ACTION_REQUIRED names kinds absent from _ACTION_REGISTRY: "
        f"{sorted(set(_ACTION_REQUIRED) - set(_ACTION_REGISTRY))}"
    )


def test_schema_source_required_fields_match_code():
    """Per-kind required source fields: schema allOf == _SOURCE_REQUIRED,
    scoped to the built-in kinds (third-party/test registrations now also
    record required fields — by design — and the schema can't know them)."""
    from mini_dudeai.config import BUILTIN_SOURCE_KINDS
    schema_req = _schema_required_by_kind(_load_schema(), "sources")
    for kind in set(schema_req) | set(BUILTIN_SOURCE_KINDS):
        code = set(_SOURCE_REQUIRED.get(kind, []))
        sch = schema_req.get(kind, set())
        assert code == sch, (
            f"required-field drift for source kind {kind!r}: "
            f"only in schema={sorted(sch - code)}, "
            f"only in config._SOURCE_REQUIRED={sorted(code - sch)}"
        )


def test_schema_action_required_fields_match_code():
    """Per-kind required action fields: schema allOf == _ACTION_REQUIRED,
    scoped to built-in kinds (see the source twin above)."""
    from mini_dudeai.config import BUILTIN_ACTION_KINDS
    schema_req = _schema_required_by_kind(_load_schema(), "actions")
    for kind in set(schema_req) | set(BUILTIN_ACTION_KINDS):
        code = set(_ACTION_REQUIRED.get(kind, []))
        sch = schema_req.get(kind, set())
        assert code == sch, (
            f"required-field drift for action kind {kind!r}: "
            f"only in schema={sorted(sch - code)}, "
            f"only in config._ACTION_REQUIRED={sorted(code - sch)}"
        )


def test_example_config_passes_hand_rolled_validator():
    """The shipped example config must stay runnable under validate_config."""
    with open(_EXAMPLE_PATH, encoding="utf-8") as fh:
        example = json.load(fh)
    errs = validate_config(example)
    assert errs == [], (
        "configs/mini_dudeai_config.example.json no longer validates cleanly: "
        + "; ".join(errs)
    )
