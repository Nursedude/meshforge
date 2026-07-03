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
from .brief import build_brief, recent_escalations, write_brief
from .candidate import validate_rules_document, write_candidate
from .dreams import (
    build_dreams,
    count_pending_deltas,
    resolve_delta,
    write_dreams,
)
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
from .memory_apply import (
    ApplyResult,
    MemoryCandidate,
    Provenance,
    apply_memory_candidate,
    supersede_memory,
    validate_candidate,
)
from .sources import (
    Condition,
    FileMtimeSource,
    HttpJsonSource,
    JsonFileSource,
    Source,
)
from .state import StateStore


def __getattr__(name):
    # PEP 562 lazy import: an eager `from .warmstart import render_warmstart`
    # puts mini_dudeai.warmstart in sys.modules during package init, which
    # makes `python3 -m mini_dudeai.warmstart` (SessionStart hook, /warmstart
    # skill) emit a runpy RuntimeWarning. Resolve it only when asked for.
    if name == "render_warmstart":
        from .warmstart import render_warmstart
        return render_warmstart
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "Action",
    "ApplyResult",
    "Condition",
    "FileAnnotateAction",
    "FileMtimeSource",
    "HistoryWriter",
    "HttpJsonSource",
    "JsonFileSource",
    "MemoryCandidate",
    "NoopAction",
    "NtfyAction",
    "Outcome",
    "ProposeEscalationAction",
    "Provenance",
    "RuleEngine",
    "Source",
    "StateStore",
    "apply_memory_candidate",
    "build_brief",
    "build_dreams",
    "build_engine_from_config",
    "count_pending_deltas",
    "load_config",
    "recent_escalations",
    "render_warmstart",
    "resolve_delta",
    "supersede_memory",
    "write_dreams",
    "register_action",
    "register_source",
    "registered_action_kinds",
    "registered_source_kinds",
    "validate_candidate",
    "validate_config",
    "validate_rules_document",
    "write_brief",
    "write_candidate",
]
