"""Rule engine — pure core, no fleet I/O assumptions.

The engine is what mini-dudeai *is*: a tick loop that collects conditions
from sources, evaluates rules against them with edge-triggered + auto-off
semantics, executes the matched rule's action, and persists state +
history. Wireclaw.io's "rule loop" mapped onto Python.

The engine knows about Sources, Actions, StateStore, HistoryWriter, and the
Rules dict shape. It knows nothing about ntfy, meshforge, federation, or
any specific source/action — those live in adapters.
"""
from __future__ import annotations

import datetime
import fnmatch
import os
import signal
import threading
import time
from dataclasses import asdict
from typing import Any

from .actions.base import Action, Outcome
from ._util import atomic_write_json, read_json
from .history import HistoryWriter
from .sources.base import Condition, Source
from .state import StateStore


def _match_rule(rule: dict, cond: Condition) -> bool:
    """Return True iff this rule's match spec applies to this condition.

    Match rules:
      - match.kind must equal cond.kind
      - match.subject_glob (or legacy peer_glob/source_glob) globs cond.subject
      - any other match.* key must equal cond.extras.get(key) (e.g. match.class)
    """
    m = rule.get("match") or {}
    if not m:
        return False
    if m.get("kind") != cond.kind:
        return False
    # subject glob (with backwards-compat aliases)
    glob = m.get("subject_glob") or m.get("peer_glob") or m.get("source_glob") or "*"
    if not fnmatch.fnmatchcase(cond.subject, glob):
        return False
    # subject EXCLUSION globs — if the subject matches any, this rule does NOT
    # apply. Lets a catch-all rule (subject_glob="*") coexist with a specific
    # known-normal suppressor: e.g. an "unexpected peer unhealthy" escalation
    # that excludes "*moc3*" (gateway-only, permanent backoff is expected).
    for ex in m.get("subject_exclude_globs") or []:
        if fnmatch.fnmatchcase(cond.subject, ex):
            return False
    # extras filter — any extra match.* key (other than glob aliases and kind)
    # must equal the corresponding entry in cond.extras.
    glob_keys = {"kind", "subject_glob", "peer_glob", "source_glob",
                 "subject_exclude_globs"}
    for k, v in m.items():
        if k in glob_keys:
            continue
        if cond.extras.get(k) != v:
            return False
    return True


class RuleEngine:
    """The mini-dudeai brain.

    Args:
        sources: list of Source instances polled each tick
        actions: dict mapping action-kind string -> Action instance
            (e.g. {"ntfy": NtfyAction(topic=...), "none": NoopAction()})
        rules_path: filesystem path to canonical rules JSON
        state_path: where StateStore persists per-rule state
        history_path: where fire events get appended
        candidate_path: optional path the cloud session writes proposed
            new rules to; engine validates + atomic-promotes each tick.
            If None, candidate-promotion is disabled.
    """

    def __init__(
        self,
        sources: list[Source],
        actions: dict[str, Action],
        rules_path: str,
        state_path: str,
        history_path: str,
        candidate_path: str | None = None,
        brief_path: str | None = None,
        clean_exit_path: str | None = None,
    ) -> None:
        self.sources = sources
        self.actions = actions
        self.rules_path = rules_path
        self.candidate_path = candidate_path
        self.state_store = StateStore(state_path)
        self.history = HistoryWriter(history_path)
        # Opt-in: when set, run() writes a bare wall-clock float here on
        # graceful stop (SIGTERM/SIGINT/request_stop). BootHealthSource reads
        # it to tell a planned reboot (marker ~ last_tick) from a crash
        # (last_tick advanced past a stale/absent marker). None = no marker
        # (standalone default, unchanged).
        self.clean_exit_path = clean_exit_path
        # Opt-in: when set, run() atomic-writes a warm-start brief here after
        # every tick so any box (not just session hosts) carries a fresh,
        # readable view of mini's posture. None = no brief file (standalone
        # default, unchanged). Cheap: write_brief is a pure render of state +
        # a history tail + one small atomic write — the tick already did the
        # expensive collect/eval.
        self.brief_path = brief_path
        self._stop = threading.Event()

    # --- rules loading + candidate promotion --------------------------

    def _validate_rules(self, data: Any) -> tuple[list[dict], list[str]]:
        # Single source of truth: the same validator the candidate-authoring API
        # uses, so a candidate the TUI/chat-compiler accepts is one we promote.
        from .candidate import validate_rules_document
        return validate_rules_document(data)

    @property
    def bak_path(self) -> str:
        """Single-slot backup of the immediately-prior canonical rules.

        Written by _promote_candidate BEFORE it overwrites the canonical
        file, so the last good ruleset is always recoverable (restore_backup)
        after a promotion that turns out wrong."""
        return self.rules_path + ".bak"

    def _backup_canonical(self) -> None:
        """Copy the current canonical rules to .bak atomically (tmp + replace).

        Best-effort: a backup-write failure must not block promotion (the new
        candidate already validated). First-run no-op: nothing to back up when
        the canonical file doesn't exist yet."""
        if not os.path.exists(self.rules_path):
            return  # first run — no prior good version to preserve
        try:
            with open(self.rules_path, "rb") as src:
                payload = src.read()
            tmp = self.bak_path + ".tmp"
            with open(tmp, "wb") as dst:
                dst.write(payload)
            os.replace(tmp, self.bak_path)
        except OSError as e:
            print(f"rules: backup of canonical before promote failed "
                  f"(continuing): {type(e).__name__}: {e}", flush=True)

    def restore_backup(self) -> dict:
        """Restore the canonical rules from the single-slot .bak.

        Recovery helper for a promotion that proved wrong — atomic copy of
        .bak back over the canonical file. Returns {"restored": bool, ...}."""
        if not os.path.exists(self.bak_path):
            return {"restored": False, "reason": "no backup present"}
        try:
            with open(self.bak_path, "rb") as src:
                payload = src.read()
            tmp = self.rules_path + ".restore.tmp"
            with open(tmp, "wb") as dst:
                dst.write(payload)
            os.replace(tmp, self.rules_path)
            return {"restored": True}
        except OSError as e:
            return {"restored": False, "reason": f"restore failed: {e}"}

    def _promote_candidate(self) -> dict | None:
        if not self.candidate_path or not os.path.exists(self.candidate_path):
            return None
        cand, err = read_json(self.candidate_path)
        if err:
            return {"promoted": False, "reason": f"candidate unreadable: {err}"}
        _, errs = self._validate_rules(cand)
        if errs:
            return {"promoted": False, "reason": f"validation errors: {errs[:3]}"}
        # Preserve the immediately-prior good rules before the destructive
        # overwrite, so a bad-but-valid candidate is recoverable (restore_backup).
        self._backup_canonical()
        try:
            os.replace(self.candidate_path, self.rules_path)
            return {"promoted": True}
        except OSError as e:
            return {"promoted": False, "reason": f"replace failed: {e}"}

    def load_rules(self) -> tuple[list[dict], list[str]]:
        promo = self._promote_candidate()
        data, err = read_json(self.rules_path)
        if err or not data:
            return [], [f"rules file unreadable: {err or 'empty'}"]
        rules, errs = self._validate_rules(data)
        if promo and promo.get("promoted"):
            errs.append("INFO: promoted candidate rules to canonical")
        elif promo and not promo.get("promoted"):
            errs.append(f"WARN: candidate rules NOT promoted: {promo.get('reason')}")
        return rules, errs

    # --- the tick -----------------------------------------------------

    def tick(self) -> dict:
        """One evaluation cycle. Returns the post-tick state dict."""
        now_ts = time.time()
        state = self.state_store.load()
        StateStore.prune_24h(state, now_ts)
        rules, rule_errs = self.load_rules()
        for e in rule_errs:
            print(f"rules: {e}", flush=True)

        # Collect from every source. A source failure becomes a source_error
        # condition (the source handles its own try/except); engine doesn't
        # need to wrap.
        conds: list[Condition] = []
        for src in self.sources:
            try:
                for c in src.collect():
                    conds.append(c)
            except Exception as e:  # genuinely broken source — emit error and continue
                conds.append(Condition(
                    kind="source_error",
                    subject=getattr(src, "name", str(src)),
                    detail=f"source raised {type(e).__name__}: {e}",
                    source=getattr(src, "name", str(src)),
                ))

        matched_keys: set[str] = set()
        history_entries: list[dict] = []
        fire_count = 0
        error_count = sum(1 for c in conds if c.kind == "source_error")

        # Edge-UP: rule matches a live condition now.
        for rule in rules:
            cooldown = float(rule.get("cooldown_s", 0))
            grace = float(rule.get("grace_s", 0))
            for cond in conds:
                if not _match_rule(rule, cond):
                    continue
                key = StateStore.rule_key(rule["id"], cond.subject)
                matched_keys.add(key)
                rs = StateStore.get_or_init(state, rule["id"], cond.subject)
                rs["last_detail"] = cond.detail
                if rs.get("currently_active"):
                    continue  # still firing — no transition
                since_fire = now_ts - rs.get("last_fired_ts", 0.0)
                if cooldown and since_fire < cooldown:
                    continue
                # Grace/debounce: the condition must persist continuously for
                # >= grace_s before this rule fires. The match streak starts the
                # first tick the condition appears; a self-clearing transient
                # (e.g. a ~30s federator blip during our own restarts) never
                # reaches grace, so it is suppressed. The streak is reset below
                # when the condition is absent for a tick.
                if grace:
                    if not rs.get("pending_since_ts"):
                        rs["pending_since_ts"] = now_ts
                    if now_ts - rs["pending_since_ts"] < grace:
                        continue  # not persisted long enough — hold, no fire
                outcome = self._execute(rule, cond, "edge_up")
                rs["currently_active"] = True
                rs["pending_since_ts"] = 0.0  # streak consumed by the fire
                rs["last_fired_ts"] = now_ts
                rs["fire_count"] = rs.get("fire_count", 0) + 1
                rs["fires_window"].append(now_ts)
                rs["fire_count_24h"] = len(rs["fires_window"])
                history_entries.append(self._history_entry(
                    now_ts, rule["id"], cond.subject, "edge_up", cond.detail, outcome,
                ))
                fire_count += 1

        # Edge-DOWN: was active, no longer matched this tick.
        for key, rs in list(state["rules"].items()):
            # Reset a grace streak that broke before it could fire: the
            # condition was building toward grace_s but is absent this tick, so
            # the next appearance starts a fresh streak (this is what makes a
            # transient never accumulate enough persistence to fire).
            if key not in matched_keys and rs.get("pending_since_ts"):
                rs["pending_since_ts"] = 0.0
            if not rs.get("currently_active"):
                continue
            if key in matched_keys:
                continue
            rule = next((r for r in rules if r["id"] == rs.get("rule_id")), None)
            if rule is None:
                rs["currently_active"] = False
                continue
            synth_cond = Condition(
                kind=rule.get("match", {}).get("kind", "?"),
                subject=rs.get("subject", "?"),
                detail=rs.get("last_detail", ""),
            )
            outcome = self._execute(rule, synth_cond, "edge_down")
            rs["currently_active"] = False
            history_entries.append(self._history_entry(
                now_ts, rule["id"], rs.get("subject", "?"), "edge_down",
                rs.get("last_detail", ""), outcome,
            ))
            fire_count += 1

        state["last_tick_ts"] = now_ts
        state["last_tick_iso"] = datetime.datetime.fromtimestamp(now_ts).isoformat()
        state["rule_count"] = len(rules)
        state["condition_count"] = len(conds)
        state["error_count"] = error_count
        state["fire_count"] = fire_count
        state["host"] = os.uname().nodename
        self.state_store.save(state)
        hist_err = self.history.append(history_entries)
        if hist_err:
            print(f"history append failed: {hist_err}", flush=True)
        return state

    def _execute(self, rule: dict, cond: Condition, transition: str) -> Outcome:
        action_cfg = rule.get("action") or {}
        kind = action_cfg.get("kind", "none")
        action = self.actions.get(kind)
        if action is None:
            return Outcome(
                action=kind, ok=False,
                error=f"unknown action kind {kind!r} (not registered in engine.actions)",
            )
        try:
            return action.execute(rule, cond, transition)
        except Exception as e:
            return Outcome(
                action=kind, ok=False,
                error=f"action raised {type(e).__name__}: {e}",
            )

    @staticmethod
    def _history_entry(ts: float, rule_id: str, subject: str, transition: str,
                       detail: str, outcome: Outcome) -> dict:
        return {
            "ts": ts,
            "iso": datetime.datetime.fromtimestamp(ts).isoformat(),
            "rule_id": rule_id,
            "subject": subject,
            "transition": transition,
            "detail": detail,
            "outcome": asdict(outcome),
        }

    # --- daemon loop --------------------------------------------------

    def run(self, interval_s: float = 30.0) -> None:
        """Block, ticking every interval_s seconds until SIGTERM/SIGINT."""
        signal.signal(signal.SIGTERM, lambda *_: self._stop.set())
        signal.signal(signal.SIGINT, lambda *_: self._stop.set())
        print(f"mini-dudeai engine started · interval={interval_s}s · "
              f"host={os.uname().nodename}", flush=True)
        while not self._stop.is_set():
            try:
                state = self.tick()
                if state.get("fire_count") or state.get("error_count"):
                    print(f"tick {state.get('last_tick_iso')}: "
                          f"fires={state.get('fire_count')} "
                          f"src_errors={state.get('error_count')}", flush=True)
            except Exception as e:
                # Observation tool must never die on a bad cycle.
                print(f"tick error (continuing): {type(e).__name__}: {e}",
                      flush=True)
            self._write_brief_safe()
            # Seed AFTER the tick, never before the first one: the tick runs
            # BootHealthSource.collect(), which must see the true pre-seed
            # marker state to assess (and latch) this boot's verdict —
            # seeding first would stamp a fresh marker and mask a real crash
            # that happened before the marker file first existed.
            self._seed_clean_exit_if_missing()
            self._stop.wait(interval_s)
        print("mini-dudeai engine stopped", flush=True)
        self._write_clean_exit_marker()

    def _write_brief_safe(self) -> None:
        """Atomic-write the warm-start brief if brief_path is set. Never raises —
        a brief-write failure must not take down the observation loop (same
        contract as the tick error handler)."""
        if not self.brief_path:
            return
        try:
            from .brief import write_brief
            write_brief(self.state_store.path, self.history.path, self.brief_path)
        except Exception as e:
            print(f"brief write failed (continuing): {type(e).__name__}: {e}",
                  flush=True)

    def _write_clean_exit_marker(self) -> None:
        """Stamp the clean-exit marker (bare wall-clock float, atomic).

        Written on graceful stop so BootHealthSource can rule the prior
        shutdown clean on the next boot. Bare float string — NOT json —
        because BootHealthSource._read_float does float(read().strip()).
        Best-effort: a marker-write failure must not raise (same contract
        as _write_brief_safe)."""
        if not self.clean_exit_path:
            return
        try:
            tmp = self.clean_exit_path + ".tmp"
            with open(tmp, "w") as f:
                f.write(f"{time.time():.3f}")
            os.replace(tmp, self.clean_exit_path)
        except OSError as e:
            print(f"clean-exit marker write failed (continuing): "
                  f"{type(e).__name__}: {e}", flush=True)

    def _seed_clean_exit_if_missing(self) -> None:
        """Deploy-window seed: create the marker if it has never existed.

        On a fresh deploy the marker is absent until the first graceful stop;
        a planned reboot whose stop path is interrupted (e.g. SIGKILL after a
        hung shutdown) would then read as a crash. Seeding narrows that
        window. Called only AFTER a tick (see run()) so the first
        BootHealthSource assessment of a fresh boot latches before any seed
        can mask it. Never overwrites an existing marker."""
        if not self.clean_exit_path or os.path.exists(self.clean_exit_path):
            return
        self._write_clean_exit_marker()

    def request_stop(self) -> None:
        self._stop.set()
