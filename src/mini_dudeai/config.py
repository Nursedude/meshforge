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
from typing import Any, Callable

from ._util import read_json
from .actions import (
    Action, FileAnnotateAction, NoopAction, NtfyAction, ProposeEscalationAction,
)
from .engine import RuleEngine
from .sources import (
    BootHealthSource, FileMtimeSource, HttpJsonSource, JsonFileSource, Source,
)

# Registries: config `kind` string -> builder callable that takes the spec dict
# and returns a Source/Action. Standalone users extend these by name at import
# time via register_source()/register_action() — no need to edit this module.
SourceBuilder = Callable[[dict], Source]
ActionBuilder = Callable[[dict], Action]
_SOURCE_REGISTRY: dict[str, SourceBuilder] = {}
_ACTION_REGISTRY: dict[str, ActionBuilder] = {}


def register_source(kind: str, builder: SourceBuilder) -> None:
    """Register a config-instantiable Source under `kind`.

    `builder` receives the source spec dict (the JSON object with its "kind"
    key) and returns a Source. Lets third-party / uConsole code add custom
    sources a JSON config can then reference by string:

        from mini_dudeai import register_source
        register_source("my_sensor", lambda spec: MySensorSource(spec["dev"]))
    """
    _SOURCE_REGISTRY[kind] = builder


def register_action(kind: str, builder: ActionBuilder) -> None:
    """Register a config-instantiable Action under `kind`. See register_source."""
    _ACTION_REGISTRY[kind] = builder


def registered_source_kinds() -> list[str]:
    """Sorted list of source kinds a config may reference (for validation/docs)."""
    return sorted(_SOURCE_REGISTRY)


def registered_action_kinds() -> list[str]:
    """Sorted list of action kinds a config may reference (for validation/docs)."""
    return sorted(_ACTION_REGISTRY)


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


# --- seed builders (the kinds shipped in the box) ---------------------

def _seed_file_mtime(spec: dict) -> Source:
    return FileMtimeSource(
        path=os.path.expanduser(spec["path"]),
        max_age_s=float(spec.get("max_age_s", 1800)),
        kind=spec.get("condition_kind", "file_stale"),
        subject=spec.get("subject"),
        name=spec.get("name"),
    )


def _seed_json_file(spec: dict) -> Source:
    return JsonFileSource(
        path=os.path.expanduser(spec["path"]),
        kind=spec["condition_kind"],
        extractor=_make_extractor(
            spec.get("items_path"),
            spec.get("subject_field", "subject"),
            spec.get("detail_field", "detail"),
        ),
        name=spec.get("name"),
    )


def _seed_http_json(spec: dict) -> Source:
    return HttpJsonSource(
        url=spec["url"],
        kind=spec["condition_kind"],
        extractor=_make_extractor(
            spec.get("items_path"),
            spec.get("subject_field", "subject"),
            spec.get("detail_field", "detail"),
        ),
        timeout=float(spec.get("timeout_s", 8)),
        name=spec.get("name"),
    )


def _seed_boot_health(spec: dict) -> Source:
    pl = spec.get("power_log_path")
    return BootHealthSource(
        state_path=os.path.expanduser(spec["state_path"]),
        clean_exit_path=os.path.expanduser(spec["clean_exit_path"]),
        assessment_path=os.path.expanduser(spec["assessment_path"]),
        power_log_path=os.path.expanduser(pl) if pl else None,
        boot_window_s=float(spec.get("boot_window_s", 900)),
        clean_slack_s=float(spec.get("clean_slack_s", 180)),
        uptime_path=spec.get("uptime_path", "/proc/uptime"),
        name=spec.get("name", "boot_health"),
    )


def _seed_ntfy(spec: dict) -> Action:
    return NtfyAction(
        topic=spec["topic"],
        base_url=spec.get("base_url", "https://ntfy.sh"),
        default_priority=spec.get("default_priority", "default"),
        default_tags=spec.get("default_tags"),
    )


_SOURCE_REGISTRY.update({
    "file_mtime": _seed_file_mtime,
    "json_file": _seed_json_file,
    "http_json": _seed_http_json,
    "boot_health": _seed_boot_health,
})
_ACTION_REGISTRY.update({
    "ntfy": _seed_ntfy,
    "annotate": lambda spec: FileAnnotateAction(path=os.path.expanduser(spec["path"])),
    "propose_escalation": lambda spec: ProposeEscalationAction(),
    "none": lambda spec: NoopAction(),
})


def _build_source(spec: dict) -> Source:
    kind = spec.get("kind")
    builder = _SOURCE_REGISTRY.get(kind)
    if builder is None:
        raise ValueError(
            f"unknown source kind {kind!r} (registered: {registered_source_kinds()})"
        )
    return builder(spec)


def _build_action(spec: dict) -> Action:
    kind = spec.get("kind")
    builder = _ACTION_REGISTRY.get(kind)
    if builder is None:
        raise ValueError(
            f"unknown action kind {kind!r} (registered: {registered_action_kinds()})"
        )
    return builder(spec)


# Per-kind required fields for the built-in kinds. Third-party kinds registered
# via register_source/action are validated only for kind-existence (we can't
# know their required fields); they may add an entry here if they want field
# checks. Keep in sync with the seed builders above.
_SOURCE_REQUIRED: dict[str, list[str]] = {
    "file_mtime": ["path"],  # max_age_s is optional — _seed_file_mtime defaults it to 1800
    "json_file": ["path", "condition_kind"],
    "http_json": ["url", "condition_kind"],
    "boot_health": ["state_path", "clean_exit_path", "assessment_path"],
}
_ACTION_REQUIRED: dict[str, list[str]] = {
    "ntfy": ["topic"],
    "annotate": ["path"],
    "propose_escalation": [],
    "none": [],
}


def validate_config(config: dict) -> list[str]:
    """Return a list of human-readable config errors (empty list = valid).

    Hand-rolled (no jsonschema dep — keeps the Pi/uConsole runtime dep-free).
    Checks top-level shape, that every source/action `kind` is registered, and
    that built-in kinds carry their required fields. Reports the bad field +
    its path so a stranger can fix the config without reading the source.
    """
    errors: list[str] = []
    if not isinstance(config, dict):
        return ["config top-level must be a JSON object"]
    if not config.get("rules_path"):
        errors.append("missing required top-level field 'rules_path'")

    sources = config.get("sources")
    if sources is not None and not isinstance(sources, list):
        errors.append("'sources' must be a list")
        sources = []
    for i, spec in enumerate(sources or []):
        where = f"sources[{i}]"
        if not isinstance(spec, dict):
            errors.append(f"{where}: must be an object")
            continue
        kind = spec.get("kind")
        if kind not in _SOURCE_REGISTRY:
            errors.append(f"{where}: unknown source kind {kind!r} "
                          f"(registered: {registered_source_kinds()})")
            continue
        for field in _SOURCE_REQUIRED.get(kind, []):
            if field not in spec:
                errors.append(f"{where} (kind={kind}): missing required field {field!r}")

    actions = config.get("actions")
    if actions is not None and not isinstance(actions, dict):
        errors.append("'actions' must be an object mapping name -> spec")
        actions = {}
    for name, spec in (actions or {}).items():
        where = f"actions[{name!r}]"
        if not isinstance(spec, dict):
            errors.append(f"{where}: must be an object")
            continue
        kind = spec.get("kind")
        if kind not in _ACTION_REGISTRY:
            errors.append(f"{where}: unknown action kind {kind!r} "
                          f"(registered: {registered_action_kinds()})")
            continue
        for field in _ACTION_REQUIRED.get(kind, []):
            if field not in spec:
                errors.append(f"{where} (kind={kind}): missing required field {field!r}")
    return errors


def build_engine_from_config(config: dict) -> tuple[RuleEngine, float]:
    """Build a RuleEngine from a parsed config dict. Returns (engine, interval_s).

    Validates the config first (validate_config) and raises ValueError listing
    every problem, so users see all errors at once rather than one-per-run.
    """
    errors = validate_config(config)
    if errors:
        raise ValueError("invalid mini-dudeai config:\n  - " + "\n  - ".join(errors))
    sources = [_build_source(s) for s in config.get("sources") or []]
    actions = {k: _build_action(v) for k, v in (config.get("actions") or {}).items()}
    rules_path = os.path.expanduser(config["rules_path"])
    state_path = os.path.expanduser(config.get("state_path", rules_path + ".state"))
    history_path = os.path.expanduser(config.get("history_path", rules_path + ".history.jsonl"))
    candidate_path = config.get("candidate_path")
    if candidate_path:
        candidate_path = os.path.expanduser(candidate_path)
    # boot_health is a reader/writer PAIR: the source READS the clean-exit
    # marker, the engine WRITES it on graceful stop. Wiring only the reader
    # (the old behavior) made every planned reboot assess as a crash —
    # perpetual false unexpected_reboot fires for JSON-config users. If a
    # boot_health source is configured, plumb its marker path into the engine
    # so both halves exist.
    clean_exit_path = None
    for spec in config.get("sources") or []:
        if isinstance(spec, dict) and spec.get("kind") == "boot_health":
            clean_exit_path = os.path.expanduser(spec["clean_exit_path"])
            break
    engine = RuleEngine(
        sources=sources,
        actions=actions,
        rules_path=rules_path,
        state_path=state_path,
        history_path=history_path,
        candidate_path=candidate_path,
        clean_exit_path=clean_exit_path,
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
