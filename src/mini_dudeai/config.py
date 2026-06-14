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
    Action, FileAnnotateAction, NatsAction, NoopAction, NtfyAction,
    ProposeEscalationAction,
)
from .engine import RuleEngine
from .sources import (
    BootHealthSource, FileMtimeSource, HttpJsonSource, JsonFileSource,
    NatsSensorSource, Source,
)

# Registries: config `kind` string -> builder callable that takes the spec dict
# and returns a Source/Action. Standalone users extend these by name at import
# time via register_source()/register_action() — no need to edit this module.
SourceBuilder = Callable[[dict], Source]
ActionBuilder = Callable[[dict], Action]
_SOURCE_REGISTRY: dict[str, SourceBuilder] = {}
_ACTION_REGISTRY: dict[str, ActionBuilder] = {}
# Per-kind required spec fields — populated by register_source/register_action
# (declared at registration so builder and validation can't drift).
_SOURCE_REQUIRED: dict[str, list[str]] = {}
_ACTION_REQUIRED: dict[str, list[str]] = {}


def register_source(kind: str, builder: SourceBuilder,
                    required: tuple = ()) -> None:
    """Register a config-instantiable Source under `kind`.

    `builder` receives the source spec dict (the JSON object with its "kind"
    key) and returns a Source. `required` names the spec fields
    validate_config must enforce for this kind — declared AT registration so
    the builder and its validation can never be edited separately (a
    hand-maintained side table once required max_age_s while the builder
    happily defaulted it). Lets third-party / uConsole code add custom
    sources a JSON config can then reference by string:

        from mini_dudeai import register_source
        register_source("my_sensor",
                        lambda spec: MySensorSource(spec["dev"]),
                        required=("dev",))
    """
    _SOURCE_REGISTRY[kind] = builder
    _SOURCE_REQUIRED[kind] = list(required)


def register_action(kind: str, builder: ActionBuilder,
                    required: tuple = ()) -> None:
    """Register a config-instantiable Action under `kind`. See register_source."""
    _ACTION_REGISTRY[kind] = builder
    _ACTION_REQUIRED[kind] = list(required)


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


def _resolve_token(spec: dict) -> str | None:
    """Token from spec: `token_env` (env var NAME — preferred, keeps the
    secret out of the config file) wins over literal `token`. A named-but-
    missing env var is a loud config error, not a silent unauthenticated
    connect. Shared by the NATS source and the ntfy action seeds."""
    env_name = spec.get("token_env")
    if env_name:
        token = os.environ.get(env_name)
        if not token:
            raise ValueError(
                f"token_env names {env_name!r} but that env var is unset/empty")
        return token
    return spec.get("token") or None


# Back-compat alias — NATS seed callers keep their name; one shared impl so the
# two consumers can't drift (honest_failure_modes #5).
_resolve_nats_token = _resolve_token


def _seed_ntfy(spec: dict) -> Action:
    return NtfyAction(
        topic=spec["topic"],
        base_url=spec.get("base_url", "https://ntfy.sh"),
        default_priority=spec.get("default_priority", "default"),
        default_tags=spec.get("default_tags"),
        token=_resolve_token(spec),
    )


def _seed_nats_sensor(spec: dict) -> Source:
    return NatsSensorSource(
        server=spec["server"],
        sensors=spec["sensors"],
        kind=spec.get("condition_kind", "sensor_breach"),
        token=_resolve_nats_token(spec),
        timeout_s=float(spec.get("timeout_s", 5)),
        name=spec.get("name"),
    )


def _seed_nats_action(spec: dict) -> Action:
    return NatsAction(
        server=spec["server"],
        device=spec.get("device"),
        payload=spec.get("payload"),
        payload_down=spec.get("payload_down"),
        token=_resolve_nats_token(spec),
        timeout_s=float(spec.get("timeout_s", 5)),
    )


# Built-in kinds register through the same API third parties use; required
# fields travel WITH the registration (mirrored in
# mini_dudeai_config.schema.json — the drift test pins the two together).
register_source("file_mtime", _seed_file_mtime,
                required=("path",))  # max_age_s defaults to 1800 in the builder
register_source("json_file", _seed_json_file,
                required=("path", "condition_kind"))
register_source("http_json", _seed_http_json,
                required=("url", "condition_kind"))
register_source("boot_health", _seed_boot_health,
                required=("state_path", "clean_exit_path", "assessment_path"))
register_source("nats_sensor", _seed_nats_sensor,
                required=("server", "sensors"))
register_action("ntfy", _seed_ntfy, required=("topic",))
register_action("nats", _seed_nats_action, required=("server",))
register_action("annotate",
                lambda spec: FileAnnotateAction(path=os.path.expanduser(spec["path"])),
                required=("path",))
register_action("propose_escalation", lambda spec: ProposeEscalationAction())
register_action("none", lambda spec: NoopAction())

# Snapshot of the kinds shipped in the box, taken immediately after the
# built-in registrations: the schema's enum is pinned against THESE (the
# drift test), not against the live registry, which legitimately grows with
# third-party register_source/register_action calls.
BUILTIN_SOURCE_KINDS = frozenset(_SOURCE_REGISTRY)
BUILTIN_ACTION_KINDS = frozenset(_ACTION_REGISTRY)


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
