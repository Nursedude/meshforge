"""Persistent per-rule edge-state for the engine.

A rule's state tracks: last_fired_ts, currently_active (the edge memory),
active_since_ts + announced (see _empty_rule_state), fire_count,
fire_count_24h with a rolling window, and last_detail (so edge_down can
reference the most recent detail seen).

``currently_active`` answers "is this condition live NOW?" and is the flag
every read-only consumer (brief, rollup, dreams) gates on. It is deliberately
NOT "is this rule currently paging" — cooldown rate-limits the action, never
the observation.

State is keyed by (rule_id, condition_subject) — one rule firing on multiple
distinct subjects keeps separate edge states. Persisted as one JSON file,
atomic-written every tick.
"""
from __future__ import annotations

import time

from ._util import atomic_write_json, read_json


class StateLoadError(OSError):
    """A real read/parse failure of an EXISTING state file (not first-boot
    absence). Subclasses OSError so the engine's two callers degrade gracefully
    without a new except clause: tick() lets it propagate to RuleEngine.run()'s
    ``except Exception`` (which skips the cycle BEFORE save(), preserving the
    good file), and _reset_pending_streaks()'s ``except OSError`` swallows it."""


def _empty_rule_state(rule_id: str, subject: str) -> dict:
    return {
        "rule_id": rule_id,
        "subject": subject,
        "currently_active": False,
        # ts this activation began (0.0 = not active). Distinct from
        # last_fired_ts, which stamps the last time an ACTION ran: an
        # activation recorded under cooldown suppression never fires, so
        # last_fired_ts would date the PREVIOUS cycle and overstate "active
        # for" by the whole gap (the forgeable-duration class,
        # honest_failure_modes #6).
        "active_since_ts": 0.0,
        # whether an edge_up ACTION ran for the current activation. False =
        # the condition is live and recorded, but its rule was inside
        # cooldown when it returned, so nothing was sent. Gates the edge_down
        # action: never emit a CLEAR for an alarm that was never raised.
        # Absent on pre-2026-09-02 state files — readers must default it to
        # True (assume announced), the direction that preserves the old
        # behaviour for edges already in flight.
        "announced": False,
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
        data, err = read_json(self.path)
        # Genuine absence (first boot) is the ONLY case that may read as empty.
        # A real read/parse error on an EXISTING file (transient SD-card EIO,
        # external corruption) must NOT collapse to {"rules": {}} — that empty
        # is byte-identical to a fresh box, so the engine would mass-deactivate
        # every live edge (false 'recovered'), drop the pending_sends retry
        # queue (#81), then save() the wipe over the good file. Raise instead;
        # the caller skips the cycle and the good file survives. (read_json
        # returns "not found" for FileNotFoundError, an error string otherwise.)
        if err is not None and err != "not found":
            raise StateLoadError(f"refusing to load degraded state {self.path}: {err}")
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

    # ── durable first-sighting memory (2026-08-09) ────────────────────────
    # ``prune_24h`` DELETES a rule-state key after STALE_KEY_RETENTION_S of
    # silence, and ``get_or_init`` then rebuilds it with fire_count = 0. That
    # is correct for edge state and fatal for any claim about HISTORY: a
    # subject quiet for a week comes back byte-identical to one never seen,
    # which is how ``detect_new_subject`` announced "first-ever sighting" of
    # `ntfy` — live since 2026-06-18 — on 2026-08-06. Retired state read as
    # absent state (honest_failure_modes #2), one layer up.
    #
    # So first-sighting lives HERE, in a map the prune never touches: ts only,
    # no windows, ~60 bytes an entry (53 distinct pairs in this box's whole
    # recorded history, so ~3 KB at today's cardinality). ONE home for the
    # fact — a copy in the rule state would drift the moment prune ran (#5).
    SUBJECTS_SEEN_KEY = "subjects_seen"
    SUBJECTS_SEEN_MAX = 4096          # ~80× today's cardinality; a runaway stop
    SUBJECTS_EVICTED_KEY = "subjects_seen_evicted"

    @staticmethod
    def note_subject_seen(state: dict, rule_id: str, subject: str,
                          now_ts: float) -> float:
        """Stamp (and return) the first time this (rule, subject) ever fired.

        Idempotent: an existing stamp is never overwritten, so the answer
        survives every prune. At the cap the OLDEST stamps are evicted and the
        count is kept in ``subjects_seen_evicted`` — an eviction means a
        subject can look new again, and a memory that silently forgot is worse
        than one that says how much it forgot (honest_failure_modes #9).
        """
        seen = state.get(StateStore.SUBJECTS_SEEN_KEY)
        if not isinstance(seen, dict):
            seen = {}
            state[StateStore.SUBJECTS_SEEN_KEY] = seen
        key = StateStore.rule_key(rule_id, subject)
        prior = seen.get(key)
        if isinstance(prior, (int, float)) and prior > 0:
            return float(prior)
        seen[key] = now_ts
        if len(seen) > StateStore.SUBJECTS_SEEN_MAX:
            drop = len(seen) - StateStore.SUBJECTS_SEEN_MAX
            for k, _ in sorted(seen.items(), key=lambda kv: kv[1])[:drop]:
                del seen[k]
            state[StateStore.SUBJECTS_EVICTED_KEY] = int(
                state.get(StateStore.SUBJECTS_EVICTED_KEY, 0) or 0) + drop
        return now_ts

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
