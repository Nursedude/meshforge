"""Fleet preset — reproduces today's ~/mini_dudeai.py wiring exactly.

What this preset configures:
  - WatchdogJsonSource → /var/lib/meshforge/watchdog.json → kind="signal_class"
  - FederationStatusSource → http://localhost:5000/api/status → 2 kinds:
      kind="federation_peer_unhealthy" (per peer in_backoff or unreachable)
      kind="source_error" (when the URL is unreachable — federator down)
  - DigestStaleSource → ~/situation_digest.md → kind="digest_stale" if > 30m old
  - NtfyAction (fleet topic from env), FileAnnotateAction (digest annotations),
    ProposeEscalationAction, NoopAction.

The fleet ntfy topic is NOT hard-coded in source (MF014). Operator must set
MINI_DUDEAI_NTFY_TOPIC (or pass ntfy_topic= when calling build_engine).
"""
from __future__ import annotations

import os
from typing import Iterable

from ..actions import (
    FileAnnotateAction, NoopAction, NtfyAction, ProposeEscalationAction,
)
from ..engine import RuleEngine
from ..sources import FileMtimeSource, HttpJsonSource, JsonFileSource
from ..sources.base import Condition, Source

DEFAULT_FEDERATOR_URL = "http://localhost:5000/api/status"
DEFAULT_WATCHDOG_PATH = "/var/lib/meshforge/watchdog.json"
DEFAULT_DIGEST_STALE_THRESHOLD_S = 1800


def _watchdog_extractor(data):
    """Project watchdog.json signals[] to Condition-ready dicts.

    Maps Signal dataclass (cls, subject, severity, detail, issue_ref, extra)
    onto Condition shape. The condition kind becomes 'signal_class' so seed
    rules' match.kind: signal_class works directly. The 'class' filter lives
    in cond.extras and is matched by rule.match.class.
    """
    out = []
    for sig in data.get("signals") or []:
        out.append({
            "subject": sig.get("subject", "unknown"),
            "detail": sig.get("detail", ""),
            "class": sig.get("cls", "unknown"),
            "severity": sig.get("severity", "info"),
            "issue_ref": sig.get("issue_ref"),
        })
    return out


class FederationPeerSource(Source):
    """One Condition per UN-healthy peer pulled from /api/status.federation.peer_status.

    Only emits for peers that are in_backoff OR have last_error and !reachable.
    Healthy peers emit nothing. Federator unreachable → source_error Condition.
    """

    def __init__(self, url: str, timeout: float = 6.0,
                 name: str = "federation") -> None:
        self.url = url
        self.timeout = timeout
        self.name = name

    def collect(self) -> Iterable[Condition]:
        from .._util import fetch_json
        data, err = fetch_json(self.url, timeout=self.timeout)
        if err:
            yield Condition(
                kind="source_error",
                subject="federator",
                detail=f"/api/status unreachable: {err}",
                source=self.name,
            )
            return
        if not isinstance(data, dict):
            return
        peers = (data.get("federation") or {}).get("peer_status") or []
        for p in peers:
            name = p.get("peer_name") or p.get("hostname") or p.get("name") or "?"
            if p.get("in_backoff") or (p.get("last_error") and not p.get("reachable", True)):
                yield Condition(
                    kind="federation_peer_unhealthy",
                    subject=str(name),
                    detail=(f"in_backoff={p.get('in_backoff')} "
                            f"mult={p.get('backoff_multiplier')} "
                            f"last_err={p.get('last_error')!r}"),
                    source=self.name,
                    extras={
                        "backoff_multiplier": p.get("backoff_multiplier"),
                        "consecutive_failures": p.get("consecutive_failures"),
                    },
                )


def build_engine(
    home: str | None = None,
    rules_path: str | None = None,
    state_path: str | None = None,
    history_path: str | None = None,
    digest_path: str | None = None,
    annotate_path: str | None = None,
    watchdog_path: str | None = None,
    federator_url: str | None = None,
    ntfy_topic: str | None = None,
    digest_stale_threshold_s: int = DEFAULT_DIGEST_STALE_THRESHOLD_S,
) -> RuleEngine:
    """Wire up the engine the way the fleet's primary node runs it today.

    All paths/URLs/topics are overridable for testing. Operator-runtime defaults
    pull from env / standard locations.
    """
    home = home or os.path.expanduser("~")
    rules_path = rules_path or os.path.join(home, "mini_dudeai_rules.json")
    state_path = state_path or os.path.join(home, "mini_dudeai_state.json")
    history_path = history_path or os.path.join(home, "mini_dudeai_history.jsonl")
    digest_path = digest_path or os.path.join(home, "situation_digest.md")
    annotate_path = annotate_path or os.path.join(home, "mini_dudeai_digest_annotations.md")
    watchdog_path = watchdog_path or DEFAULT_WATCHDOG_PATH
    federator_url = federator_url or os.environ.get(
        "MINI_DUDEAI_FEDERATOR_URL", DEFAULT_FEDERATOR_URL,
    )
    ntfy_topic = ntfy_topic or os.environ.get("MINI_DUDEAI_NTFY_TOPIC")
    if not ntfy_topic:
        raise ValueError(
            "fleet preset requires MINI_DUDEAI_NTFY_TOPIC env var or ntfy_topic= arg. "
            "Operator-specific topics live in ~/.config/meshforge/mini_dudeai.env, "
            "loaded by the systemd unit via EnvironmentFile= (MF014 keeps them out of repo)."
        )

    sources = [
        JsonFileSource(
            path=watchdog_path,
            kind="signal_class",
            extractor=_watchdog_extractor,
            name="watchdog",
        ),
        FederationPeerSource(url=federator_url),
        FileMtimeSource(
            path=digest_path,
            max_age_s=float(digest_stale_threshold_s),
            kind="digest_stale",
            subject="situation_digest.md",
            name="digest",
        ),
    ]
    actions = {
        "ntfy": NtfyAction(topic=ntfy_topic),
        "annotate_digest": FileAnnotateAction(path=annotate_path),
        "propose_escalation": ProposeEscalationAction(),
        "none": NoopAction(),
    }
    return RuleEngine(
        sources=sources,
        actions=actions,
        rules_path=rules_path,
        state_path=state_path,
        history_path=history_path,
        candidate_path=rules_path + ".candidate",
    )
