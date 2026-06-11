"""Persistent per-rule edge-state for the engine.

A rule's state tracks: last_fired_ts, currently_active (the edge memory),
fire_count, fire_count_24h with a rolling window, and last_detail (so
edge_down can reference the most recent detail seen).

State is keyed by (rule_id, condition_subject) — one rule firing on multiple
distinct subjects keeps separate edge states. Persisted as one JSON file,
atomic-written every tick.
"""
from __future__ import annotations

import time

from ._util import atomic_write_json, read_json


def _empty_rule_state(rule_id: str, subject: str) -> dict:
    return {
        "rule_id": rule_id,
        "subject": subject,
        "currently_active": False,
        "last_fired_ts": 0.0,
        "fire_count": 0,
        "fire_count_24h": 0,
        "fires_window": [],
        "last_detail": "",
        # grace/debounce: ts the current pre-fire match streak began (0.0 = none).
        # A rule with grace_s only fires once the condition has matched
        # continuously for >= grace_s, suppressing self-clearing transients.
        "pending_since_ts": 0.0,
        # observed ticks in the current streak — the wall-clock span alone is
        # forgeable by NTP steps / restarts (see RuleEngine._grace_min_ticks).
        "pending_ticks": 0,
    }


class StateStore:
    """Load/save per-rule edge state from a JSON file.

    Used by the engine each tick. Atomic-write on save; tolerant of missing
    or corrupt files (returns an empty state).
    """

    def __init__(self, path: str) -> None:
        self.path = path

    def load(self) -> dict:
        data, _ = read_json(self.path)
        if not isinstance(data, dict):
            return {"rules": {}}
        if "rules" not in data:
            data["rules"] = {}
        return data

    def save(self, state: dict) -> None:
        atomic_write_json(self.path, state)

    # Entries with no live signal (not active, no streak, empty window) age
    # out after this long — subjects are open-ended (peer names, transient
    # IPs, renamed rules), so without retirement state.json grows with
    # ALL-TIME subject cardinality and every tick pays load+parse+dump for
    # keys that can never fire again (they're recreated on the next match).
    STALE_KEY_RETENTION_S = 7 * 86400

    @staticmethod
    def prune_24h(state: dict, now_ts: float | None = None) -> None:
        """Trim fire windows to 24h and retire dead state keys. In-place."""
        now_ts = time.time() if now_ts is None else now_ts
        cutoff = now_ts - 86400
        rules = state.get("rules", {})
        for key in list(rules):
            rs = rules[key]
            if not isinstance(rs, dict):
                del rules[key]
                continue
            fires = rs.get("fires_window", []) or []
            rs["fires_window"] = [t for t in fires if t >= cutoff]
            rs["fire_count_24h"] = len(rs["fires_window"])
            if (rs.get("currently_active") or rs.get("pending_since_ts")
                    or rs.get("pending_sends")):
                continue  # live edge, building streak, or undelivered send — never retire
            if rs["fires_window"]:
                continue  # fired within 24h
            last = float(rs.get("last_fired_ts", 0) or 0)
            if last and (now_ts - last) < StateStore.STALE_KEY_RETENTION_S:
                continue  # recent enough to keep cooldown memory
            del rules[key]

    @staticmethod
    def record_fire(rs: dict, now_ts: float) -> None:
        """The single derivation site for fire bookkeeping. fire_count_24h is
        derivable state (always len(fires_window)); keeping the engine and
        prune_24h as independent writers invited drift."""
        rs["last_fired_ts"] = now_ts
        rs["fire_count"] = rs.get("fire_count", 0) + 1
        rs.setdefault("fires_window", []).append(now_ts)
        rs["fire_count_24h"] = len(rs["fires_window"])

    @staticmethod
    def get_or_init(state: dict, rule_id: str, subject: str) -> dict:
        """Get the per-rule per-subject state, creating it if missing."""
        key = f"{rule_id}::{subject}"
        rs = state["rules"].get(key)
        if rs is None:
            rs = _empty_rule_state(rule_id, subject)
            state["rules"][key] = rs
        return rs

    @staticmethod
    def rule_key(rule_id: str, subject: str) -> str:
        return f"{rule_id}::{subject}"
