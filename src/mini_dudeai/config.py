"""JSON config-file path: declarative wiring for users who don't want to
write Python.

Shape:

    {
      "interval_s": 30,
      "rules_path": "~/my_rules.json",
      "candidate_path": "~/my_rules.json.candidate",
      "state_path": "~/my_state.json",
      "history_path": "~/my_history.jsonl",
      "sources": [
        {"kind": "file_mtime", "path": "~/something.md", "max_age_s": 1800},
        {"kind": "json_file", "path": "/var/lib/.../foo.json",
         "condition_kind": "signal_class", "items_path": "signals"},
        {"kind": "http_json", "url": "http://localhost:5000/api/status",
         "condition_kind": "peer_unhealthy",
         "items_path": "federation.peer_status",
         "subject_field": "peer_name", "detail_field": "last_error"}
      ],
      "actions": {
        "ntfy": {"kind": "ntfy", "topic": "my-topic"},
        "annotate": {"kind": "annotate", "path": "~/annotations.md"},
        "propose_escalation": {"kind": "propose_escalation"},
        "none": {"kind": "none"}
      }
    }

Filtering (e.g. "only emit for peers in_backoff=True") needs Python config —
use the SDK directly or a preset. v0 JSON config emits one Condition per item
unconditionally.
"""
from __future__ import annotations

import os
from typing import Any

from ._util import read_json
from .actions import (
    Action, FileAnnotateAction, NoopAction, NtfyAction, ProposeEscalationAction,
)
from .engine import RuleEngine
from .sources import FileMtimeSource, HttpJsonSource, JsonFileSource, Source


def _dig(data: Any, dotted: str) -> Any:
    """Walk a dotted path through nested dicts. Returns [] if path misses."""
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return []
        cur = cur.get(part)
        if cur is None:
            return []
    return cur or []


def _make_extractor(items_path: str | None,
                    subject_field: str = "subject",
                    detail_field: str = "detail"):
    """Build an extractor that pulls items at items_path and remaps fields."""
    def extract(data):
        items = _dig(data, items_path) if items_path else (data if isinstance(data, list) else [])
        out = []
        for item in items:
            if not isinstance(item, dict):
                continue
            d = dict(item)
            if subject_field != "subject":
                d["subject"] = item.get(subject_field, "?")
                d.pop(subject_field, None)
            if detail_field != "detail":
                d["detail"] = item.get(detail_field, "")
                d.pop(detail_field, None)
            out.append(d)
        return out
    return extract


def _build_source(spec: dict) -> Source:
    kind = spec.get("kind")
    name = spec.get("name")
    if kind == "file_mtime":
        return FileMtimeSource(
            path=os.path.expanduser(spec["path"]),
            max_age_s=float(spec.get("max_age_s", 1800)),
            kind=spec.get("condition_kind", "file_stale"),
            subject=spec.get("subject"),
            name=name,
        )
    if kind == "json_file":
        return JsonFileSource(
            path=os.path.expanduser(spec["path"]),
            kind=spec["condition_kind"],
            extractor=_make_extractor(
                spec.get("items_path"),
                spec.get("subject_field", "subject"),
                spec.get("detail_field", "detail"),
            ),
            name=name,
        )
    if kind == "http_json":
        return HttpJsonSource(
            url=spec["url"],
            kind=spec["condition_kind"],
            extractor=_make_extractor(
                spec.get("items_path"),
                spec.get("subject_field", "subject"),
                spec.get("detail_field", "detail"),
            ),
            timeout=float(spec.get("timeout_s", 8)),
            name=name,
        )
    raise ValueError(f"unknown source kind {kind!r}")


def _build_action(spec: dict) -> Action:
    kind = spec.get("kind")
    if kind == "ntfy":
        return NtfyAction(
            topic=spec["topic"],
            base_url=spec.get("base_url", "https://ntfy.sh"),
            default_priority=spec.get("default_priority", "default"),
            default_tags=spec.get("default_tags"),
        )
    if kind == "annotate":
        return FileAnnotateAction(path=os.path.expanduser(spec["path"]))
    if kind == "propose_escalation":
        return ProposeEscalationAction()
    if kind == "none":
        return NoopAction()
    raise ValueError(f"unknown action kind {kind!r}")


def build_engine_from_config(config: dict) -> tuple[RuleEngine, float]:
    """Build a RuleEngine from a parsed config dict. Returns (engine, interval_s)."""
    sources = [_build_source(s) for s in config.get("sources") or []]
    actions = {k: _build_action(v) for k, v in (config.get("actions") or {}).items()}
    rules_path = os.path.expanduser(config["rules_path"])
    state_path = os.path.expanduser(config.get("state_path", rules_path + ".state"))
    history_path = os.path.expanduser(config.get("history_path", rules_path + ".history.jsonl"))
    candidate_path = config.get("candidate_path")
    if candidate_path:
        candidate_path = os.path.expanduser(candidate_path)
    engine = RuleEngine(
        sources=sources,
        actions=actions,
        rules_path=rules_path,
        state_path=state_path,
        history_path=history_path,
        candidate_path=candidate_path,
    )
    interval_s = float(config.get("interval_s", 30))
    return engine, interval_s


def load_config(path: str) -> dict:
    data, err = read_json(os.path.expanduser(path))
    if err:
        raise ValueError(f"config {path!r} unreadable: {err}")
    if not isinstance(data, dict):
        raise ValueError(f"config {path!r} top-level must be a JSON object")
    return data
