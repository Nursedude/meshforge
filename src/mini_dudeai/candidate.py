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


def validate_rules_document(data: Any) -> Tuple[List[dict], List[str]]:
    """Canonical rules-document validator → (valid_rules, errors). Pure, never
    raises. A rules document is ``{"rules": [{id, match{}, action{}, ...}, ...]}``.
    """
    errors: List[str] = []
    out: List[dict] = []
    if not isinstance(data, dict) or "rules" not in data:
        errors.append("rules file has no top-level 'rules' list")
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
