"""Candidate authoring — the one API that turns a rules list into a validated
``.candidate`` file the runtime will atomic-promote.

The trust model: the runtime NEVER writes the canonical rules file. A "compiler"
*proposes* a candidate; the runtime *ratifies* it (validates + os.replace on the
next tick). This module is the proposing side — and it is deliberately shared by
every compiler front-end:

- the in-app TUI rule editor (MiniDudeaiHandler), and
- the standalone WireClaw-style chat-compiler (LLM English → rule), planned.

One candidate-authoring API, many front-ends. The validation here is the SAME
validation the engine runs at promote time — RuleEngine._validate_rules
delegates to ``validate_rules_document`` — so a candidate the form accepts is one
the daemon will actually promote (no authoring/promotion divergence).
"""
from __future__ import annotations

from typing import Any, List, Tuple

from ._util import atomic_write_json

# The match keys the engine treats STRUCTURALLY (kind equality, subject
# globbing, exclusions). Every OTHER match key becomes an extras equality
# filter — which is the open-vocabulary feature, but also the silent-death
# trap: a misspelled structural key ("suject_glob") turns into an extras
# filter no Condition ever satisfies, and the rule dies with the validator
# green. Shared with engine._match_rule so the matcher and the linter can
# never disagree about what "structural" means.
STRUCTURAL_MATCH_KEYS = frozenset({
    "kind", "subject_glob", "peer_glob", "source_glob", "subject_exclude_globs",
})

# Extras keys the fleet rules legitimately match on. Anything outside
# STRUCTURAL_MATCH_KEYS and this set draws a lint warning (not an error —
# the extras vocabulary is open by design).
WELL_KNOWN_EXTRAS_KEYS = frozenset({"class"})


def validate_rules_document(data: Any) -> Tuple[List[dict], List[str]]:
    """Canonical rules-document validator → (valid_rules, errors). Pure, never
    raises. A rules document is ``{"rules": [{id, match{}, action{}, ...}, ...]}``.
    """
    errors: List[str] = []
    out: List[dict] = []
    if not isinstance(data, dict) or "rules" not in data:
        errors.append("rules file has no top-level 'rules' list")
        return out, errors
    if not isinstance(data["rules"], list):
        # {"rules": null} (a truncated/buggy compiler output) used to validate
        # with zero errors and zero rules — and promotion would then silently
        # wipe the canonical ruleset. A non-list rules value is an ERROR.
        errors.append(
            f"'rules' must be a list (got {type(data['rules']).__name__})")
        return out, errors
    for i, r in enumerate(data.get("rules") or []):
        if not isinstance(r, dict):
            errors.append(f"rule[{i}] not an object")
            continue
        if not r.get("id"):
            errors.append(f"rule[{i}] missing id")
            continue
        if not isinstance(r.get("match"), dict):
            errors.append(f"rule[{r.get('id', i)}] missing match")
            continue
        if not isinstance(r.get("action"), dict):
            errors.append(f"rule[{r.get('id', i)}] missing action")
            continue
        out.append(r)
    return out, errors


def lint_rules_document(data: Any) -> List[str]:
    """Non-blocking authoring lint → list of warning strings. Pure, never raises.

    Catches the failure modes a structurally-valid document can still carry:
      - a match key that is neither structural nor a well-known extras key
        (almost always a typo'd structural key → the rule silently matches
        nothing, forever)
      - a document with zero rules (valid, but turns the agent dark)
    Warnings do NOT block promotion — the extras vocabulary is open by design —
    but the engine prints them at promote/load time so the silent-death class
    becomes loud.
    """
    warnings: List[str] = []
    if not isinstance(data, dict):
        return warnings
    rules = data.get("rules")
    if isinstance(rules, list) and not rules:
        warnings.append("document has 0 rules — the agent will observe nothing")
    for r in rules if isinstance(rules, list) else []:
        if not isinstance(r, dict):
            continue
        m = r.get("match")
        if not isinstance(m, dict):
            continue
        for k in m:
            if k in STRUCTURAL_MATCH_KEYS or k in WELL_KNOWN_EXTRAS_KEYS:
                continue
            warnings.append(
                f"rule {r.get('id', '?')!r}: match key {k!r} is not a structural "
                f"key {sorted(STRUCTURAL_MATCH_KEYS)} — it will be matched as an "
                f"extras equality filter; if that's a typo the rule will never "
                f"match anything")
    return warnings


def write_candidate(candidate_path: str, rules: List[dict]) -> Tuple[bool, List[str]]:
    """Validate a rules LIST and atomically write it as a candidate document.

    Returns (ok, errors). On ANY validation error, writes nothing (fail-loud) —
    the daemon would reject it anyway, so we catch it at authoring time and the
    front-end reports it in-app. On success the runtime promotes it (validate +
    os.replace) within one tick.
    """
    doc = {"rules": list(rules)}
    _, errors = validate_rules_document(doc)
    if errors:
        return False, errors
    try:
        atomic_write_json(candidate_path, doc)
    except OSError as e:
        return False, [f"write failed: {e}"]
    return True, []
