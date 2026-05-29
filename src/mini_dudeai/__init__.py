"""mini-dudeai — a wireclaw.io-shaped agent that runs a rule loop 24/7 atop
whatever signals you point it at.

Architecture (per wireclaw.io's load-bearing split):

    Rule loop (mini-dudeai, this package): always-on, no LLM, no latency.
    AI loop (cloud Claude session): edits the rules file when invoked.

Two ways to use it:

  1. SDK — build a RuleEngine programmatically with your own Sources + Actions:

        from mini_dudeai import RuleEngine, HttpJsonSource, NtfyAction
        engine = RuleEngine(
            sources=[HttpJsonSource("http://my-thing/health", ...)],
            actions={"ntfy": NtfyAction(topic="my-topic")},
            rules_path="my_rules.json",
            state_path="my_state.json",
            history_path="my_history.jsonl",
        )
        engine.run(interval_s=30)

  2. Config-file — declare sources + actions in JSON, run via the CLI:

        python3 -m mini_dudeai --config my_config.json
        # one-shot mode for cron:
        python3 -m mini_dudeai --config my_config.json --once

  3. Preset — pre-wired bundles for known use cases (fleet, etc.):

        python3 -m mini_dudeai --preset meshforge_fleet

See the README + configs/mini_dudeai_rules.example.json for working examples.
"""
from .actions import (
    Action,
    FileAnnotateAction,
    NoopAction,
    NtfyAction,
    Outcome,
    ProposeEscalationAction,
)
from .brief import build_brief, write_brief
from .config import (
    build_engine_from_config,
    load_config,
    register_action,
    register_source,
    registered_action_kinds,
    registered_source_kinds,
    validate_config,
)
from .engine import RuleEngine
from .history import HistoryWriter
from .sources import (
    Condition,
    FileMtimeSource,
    HttpJsonSource,
    JsonFileSource,
    Source,
)
from .state import StateStore

__all__ = [
    "Action",
    "Condition",
    "FileAnnotateAction",
    "FileMtimeSource",
    "HistoryWriter",
    "HttpJsonSource",
    "JsonFileSource",
    "NoopAction",
    "NtfyAction",
    "Outcome",
    "ProposeEscalationAction",
    "RuleEngine",
    "Source",
    "StateStore",
    "build_brief",
    "build_engine_from_config",
    "load_config",
    "register_action",
    "register_source",
    "registered_action_kinds",
    "registered_source_kinds",
    "validate_config",
    "write_brief",
]
