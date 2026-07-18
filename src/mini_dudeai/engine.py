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
from ._util import (
    atomic_write_bytes, atomic_write_text, log_error, log_info, log_warning,
    read_json,
)
from .candidate import STRUCTURAL_MATCH_KEYS
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
    # extras filter — any extra match.* key (other than the structural keys)
    # must equal the corresponding entry in cond.extras. The structural set is
    # shared with candidate.lint_rules_document so a typo'd structural key
    # (which lands here and silently matches nothing) draws an authoring
    # warning instead of dying quiet.
    for k, v in m.items():
        if k in STRUCTURAL_MATCH_KEYS:
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
        # Last successfully-loaded ruleset + the rules-file mtime it came from.
        # Two jobs: (a) steady-state ticks skip the re-parse/re-validate when
        # the file hasn't changed; (b) a transient read error degrades to the
        # LAST-GOOD rules instead of an empty list — an empty list used to
        # silently deactivate every active condition (no edge_down) and then
        # re-fire them all as fresh edge_ups on the next good tick.
        self._rules_cache: tuple[int, list[dict]] | None = None
        self._last_good_rules: list[dict] | None = None
        # True when the most recent load_rules() could not produce ANY ruleset
        # (unreadable file and no last-good in memory). tick() then holds all
        # edge state instead of interpreting "no rules" as "nothing active".
        self._rules_unavailable = False
        # Per-source hold cache: the last successfully-collected conditions of
        # each source. When a source errors, its real conditions are absent
        # for the tick — and absence used to read as recovery (false edge_down
        # "CLEARED" pages). Holding the last-known conditions while the source
        # is blind keeps edge state honest: unobservable ≠ resolved.
        self._source_cache: dict[str, list[Condition]] = {}
        # Tick interval (set by run()); lets grace_s translate into a minimum
        # number of OBSERVED ticks so wall-clock jumps (NTP steps on the
        # RTC-less fleet) can't satisfy a debounce the box never observed.
        self._interval_s: float | None = None
        # Set by load_rules() when a candidate promoted; tick() converts it
        # into a history record (ruleset-change provenance).
        self._pending_promo_record: dict | None = None

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
            atomic_write_bytes(self.bak_path, payload)
        except OSError as e:
            log_error(f"rules: backup of canonical before promote failed "
                      f"(continuing): {type(e).__name__}: {e}")

    def restore_backup(self) -> dict:
        """Restore the canonical rules from the single-slot .bak.

        Recovery helper for a promotion that proved wrong — atomic copy of
        .bak back over the canonical file. Returns {"restored": bool, ...}."""
        if not os.path.exists(self.bak_path):
            return {"restored": False, "reason": "no backup present"}
        try:
            with open(self.bak_path, "rb") as src:
                payload = src.read()
            atomic_write_bytes(self.rules_path, payload)
            return {"restored": True}
        except OSError as e:
            return {"restored": False, "reason": f"restore failed: {e}"}

    def _promote_candidate(self) -> dict | None:
        if not self.candidate_path or not os.path.exists(self.candidate_path):
            return None
        cand, err = read_json(self.candidate_path)
        if err:
            return {"promoted": False, "reason": f"candidate unreadable: {err}"}
        valid, errs = self._validate_rules(cand)
        if errs:
            return {"promoted": False, "reason": f"validation errors: {errs[:3]}"}
        cur, _ = read_json(self.rules_path)
        cur_valid, _ = self._validate_rules(cur) if cur is not None else ([], [])
        if not valid and cur_valid:
            # A structurally-valid document with ZERO rules would silently
            # wipe the canonical ruleset and turn the agent dark with only an
            # INFO line. Refuse when the canonical file currently has rules —
            # an intentional wipe is an out-of-band operator action, not a
            # candidate promotion.
            return {"promoted": False,
                    "reason": f"candidate has 0 rules; refusing to wipe "
                              f"{len(cur_valid)} canonical rule(s)"}
        # Authoring lint: a candidate can be valid and still carry a typo'd
        # match key that makes a rule silently match nothing. Surface loudly
        # at the promotion boundary (warnings never block).
        from .candidate import lint_rules_document
        for w in lint_rules_document(cand):
            log_warning(f"rules: WARN (candidate): {w}")
        # Preserve the immediately-prior good rules before the destructive
        # overwrite, so a bad-but-valid candidate is recoverable (restore_backup).
        self._backup_canonical()
        try:
            os.replace(self.candidate_path, self.rules_path)
            old_ids = {r["id"] for r in cur_valid if r.get("id")}
            new_ids = {r["id"] for r in valid if r.get("id")}
            return {"promoted": True,
                    "added": sorted(new_ids - old_ids),
                    "removed": sorted(old_ids - new_ids),
                    "rule_count": len(valid)}
        except OSError as e:
            return {"promoted": False, "reason": f"replace failed: {e}"}

    def load_rules(self) -> tuple[list[dict], list[str]]:
        promo = self._promote_candidate()
        errs: list[str] = []
        if promo and promo.get("promoted"):
            # Stash for tick() to put on the history record: a ruleset change
            # is the single event that most changes mini's behavior, and the
            # auditable record used to be blind to it (journal line only).
            self._pending_promo_record = promo
            errs.append("INFO: promoted candidate rules to canonical "
                        f"(+{len(promo.get('added', []))} "
                        f"-{len(promo.get('removed', []))} "
                        f"→ {promo.get('rule_count', '?')} rules)")
        elif promo and not promo.get("promoted"):
            errs.append(f"WARN: candidate rules NOT promoted: {promo.get('reason')}")

        # mtime gate: the rules file changes on promotion or operator edit —
        # rarely. Steady-state ticks reuse the parsed+validated ruleset for
        # one stat() instead of a full JSON parse + validation (and instead
        # of re-printing the same validation errors every 30s forever).
        mtime_ns: int | None = None
        try:
            mtime_ns = os.stat(self.rules_path).st_mtime_ns
        except OSError:
            pass
        if (mtime_ns is not None and self._rules_cache is not None
                and self._rules_cache[0] == mtime_ns):
            self._rules_unavailable = False
            return self._rules_cache[1], errs

        data, err = read_json(self.rules_path)
        if err or not data:
            # Degrade to the LAST-GOOD ruleset, never to "no rules": an empty
            # ruleset reads downstream as "nothing is active", which silently
            # deactivates every live condition and re-fires them all later.
            if self._last_good_rules is not None:
                self._rules_unavailable = False
                errs.append(
                    f"WARN: rules file unreadable ({err or 'empty'}) — holding "
                    f"last-good ruleset ({len(self._last_good_rules)} rules)")
                return list(self._last_good_rules), errs
            self._rules_unavailable = True
            errs.append(f"rules file unreadable: {err or 'empty'} — no last-good "
                        f"ruleset in memory; holding ALL edge state this tick")
            return [], errs

        rules, verrs = self._validate_rules(data)
        errs.extend(verrs)
        from .candidate import lint_rules_document
        for w in lint_rules_document(data):
            errs.append(f"WARN: {w}")
        self._rules_unavailable = False
        self._last_good_rules = list(rules)
        if mtime_ns is not None:
            self._rules_cache = (mtime_ns, list(rules))
        return rules, errs

    # --- the tick -----------------------------------------------------

    # Per-source hold cap — bounds the state-file footprint of the hold cache.
    SOURCE_HOLD_CAP = 200

    # Failed-send retry bounds. A fire whose action failed (e.g. the [RED]
    # crash page dying in the boot+15s DNS race — 2-for-2 on real crashes,
    # 2026-06-11) is queued in the rule state and re-attempted once per tick
    # until delivered or exhausted. 10 total attempts at the 30s fleet
    # interval covers ~5 minutes of network outage; exhaustion leaves a loud
    # history record (every swallow gets a witness).
    SEND_RETRY_MAX_ATTEMPTS = 10
    # Per rule-state queue bound — an edge flapping while the network is down
    # is the only way this grows; overflow drops the OLDEST entry loudly.
    PENDING_SENDS_CAP = 4

    @staticmethod
    def _cond_to_dict(c: Condition) -> dict:
        return {"kind": c.kind, "subject": c.subject, "detail": c.detail,
                "source": c.source, "extras": c.extras}

    @staticmethod
    def _cond_from_dict(d: dict) -> Condition:
        return Condition(
            kind=str(d.get("kind", "?")), subject=str(d.get("subject", "?")),
            detail=str(d.get("detail", "")), source=str(d.get("source", "")),
            extras=d.get("extras") or {},
        )

    def _grace_min_ticks(self, grace_s: float) -> int:
        """Minimum OBSERVED ticks a grace streak must span before firing.

        Wall-clock span alone is forgeable: an NTP step (RTC-less fleet) or a
        daemon restart makes "now - pending_since" exceed grace_s without the
        box ever observing the condition persist. Requiring the streak to also
        span grace_s/interval observed ticks (min 2) makes the debounce mean
        what it says: matched continuously, as witnessed by this process.
        """
        if self._interval_s and self._interval_s > 0:
            return max(2, int(grace_s // self._interval_s))
        return 2

    def tick(self) -> dict:
        """One evaluation cycle. Returns the post-tick state dict."""
        now_ts = time.time()
        state = self.state_store.load()
        StateStore.prune_24h(state, now_ts)
        prev_rule_count = state.get("rule_count")
        rules, rule_errs = self.load_rules()
        for e in rule_errs:
            if e.startswith("INFO"):
                log_info(f"rules: {e}")
            elif e.startswith("WARN"):
                log_warning(f"rules: {e}")
            else:
                log_error(f"rules: {e}")
        if not rules and not self._rules_unavailable and prev_rule_count:
            log_warning(f"rules: WARN ruleset is EMPTY (was {prev_rule_count} "
                        f"rules) — the agent observes nothing")

        # Rehydrate the per-source hold cache after a restart, so a source
        # that is ALREADY failing when the daemon comes back doesn't read as
        # "everything recovered".
        if not self._source_cache and isinstance(state.get("source_hold"), dict):
            for name, items in state["source_hold"].items():
                if isinstance(items, list):
                    self._source_cache[name] = [
                        self._cond_from_dict(d) for d in items
                        if isinstance(d, dict)
                    ][:self.SOURCE_HOLD_CAP]

        # Collect from every source. A failing source (raised, or emitted a
        # source_error condition) has its REAL conditions held from the last
        # good collect: absence caused by blindness must not read as recovery
        # (false edge_down "CLEARED" while the problem persists). The
        # source_error condition still flows so rules can page the blindness.
        conds: list[Condition] = []
        for src in self.sources:
            name = getattr(src, "name", str(src))
            src_conds: list[Condition] = []
            failed = False
            try:
                for c in src.collect():
                    src_conds.append(c)
            except Exception as e:  # genuinely broken source — emit error and continue
                failed = True
                src_conds.append(Condition(
                    kind="source_error",
                    subject=name,
                    detail=f"source raised {type(e).__name__}: {e}",
                    source=name,
                ))
            if not failed and any(c.kind == "source_error" for c in src_conds):
                failed = True
            if failed:
                held = self._source_cache.get(name, [])
                if held:
                    log_warning(f"source {name}: errored — holding {len(held)} "
                                f"last-known condition(s) (unobservable ≠ "
                                f"resolved)")
                conds.extend(c for c in src_conds if c.kind == "source_error")
                conds.extend(held)
            else:
                self._source_cache[name] = src_conds[:self.SOURCE_HOLD_CAP]
                conds.extend(src_conds)
        state["source_hold"] = {
            n: [self._cond_to_dict(c) for c in lst]
            for n, lst in self._source_cache.items()
        }

        matched_keys: set[str] = set()
        history_entries: list[dict] = []
        fire_count = 0
        source_error_conds = [c for c in conds if c.kind == "source_error"]
        error_count = len(source_error_conds)

        # Ruleset-change provenance: a promotion is the event that most
        # changes mini's behavior — it goes on the same auditable record the
        # honesty audit certifies, not just a journal line.
        if self._pending_promo_record:
            promo = self._pending_promo_record
            self._pending_promo_record = None
            history_entries.append(self._history_entry(
                now_ts, "__ruleset__", "canonical", "ruleset_promoted",
                f"candidate promoted: +{promo.get('added', [])} "
                f"-{promo.get('removed', [])} → {promo.get('rule_count', '?')} "
                f"rules", Outcome(action="none", ok=True),
            ))

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
                if since_fire < 0:
                    # Wall clock stepped BACKWARD (NTP on the RTC-less fleet):
                    # a negative age would extend cooldown suppression by the
                    # step size. Re-anchor to now — suppression is then bounded
                    # by at most one cooldown from the step, never more.
                    rs["last_fired_ts"] = now_ts
                    since_fire = 0.0
                if cooldown and since_fire < cooldown:
                    continue
                # Grace/debounce: the condition must persist continuously for
                # >= grace_s before this rule fires. The match streak starts the
                # first tick the condition appears; a self-clearing transient
                # (e.g. a ~30s federator blip during our own restarts) never
                # reaches grace, so it is suppressed. The streak is reset below
                # when the condition is absent for a tick — and it must ALSO
                # span a minimum number of observed ticks, because a forward
                # NTP step (or a restart, see run()) can make the wall-clock
                # span exceed grace_s after a single observation.
                if grace:
                    if not rs.get("pending_since_ts"):
                        rs["pending_since_ts"] = now_ts
                        rs["pending_ticks"] = 1
                    else:
                        rs["pending_ticks"] = int(rs.get("pending_ticks", 1) or 1) + 1
                        if rs["pending_since_ts"] > now_ts:
                            rs["pending_since_ts"] = now_ts  # backward step — restart span
                    if (now_ts - rs["pending_since_ts"] < grace
                            or rs["pending_ticks"] < self._grace_min_ticks(grace)):
                        continue  # not persisted long enough — hold, no fire
                outcome = self._execute(rule, cond, "edge_up")
                rs["currently_active"] = True
                rs["pending_since_ts"] = 0.0  # streak consumed by the fire
                rs["pending_ticks"] = 0
                StateStore.record_fire(rs, now_ts)
                history_entries.append(self._history_entry(
                    now_ts, rule["id"], cond.subject, "edge_up", cond.detail, outcome,
                ))
                self._queue_failed_send(rule, rs, "edge_up", cond.detail,
                                        outcome, now_ts, history_entries)
                fire_count += 1

        # Edge-DOWN: was active, no longer matched this tick. SKIPPED entirely
        # when no ruleset could be loaded: with zero rules, every active state
        # looks unmatched, and "rules unobservable" must not read as "every
        # condition resolved" (it silently dropped active escalations with no
        # edge_down, then re-fired them all as fresh pages on the next good
        # tick).
        if self._rules_unavailable:
            log_error("rules: holding ALL edge state this tick (no ruleset "
                      "available — unobservable ≠ resolved)")
        else:
            for key, rs in list(state["rules"].items()):
                # Reset a grace streak that broke before it could fire: the
                # condition was building toward grace_s but is absent this tick, so
                # the next appearance starts a fresh streak (this is what makes a
                # transient never accumulate enough persistence to fire).
                if key not in matched_keys and rs.get("pending_since_ts"):
                    rs["pending_since_ts"] = 0.0
                    rs["pending_ticks"] = 0
                if not rs.get("currently_active"):
                    continue
                if key in matched_keys:
                    continue
                rule = next((r for r in rules if r["id"] == rs.get("rule_id")), None)
                if rule is None:
                    # The rule was removed from the ruleset while its condition
                    # was active. We can't run its action (the action config
                    # lived in the rule), but the deactivation must be LOUD and
                    # on the record — this used to be a silent state flip.
                    rs["currently_active"] = False
                    detail = ("rule removed from ruleset while active; "
                              "deactivated without running its action")
                    log_warning(f"rules: {rs.get('rule_id')!r} ({key}): {detail}")
                    history_entries.append(self._history_entry(
                        now_ts, rs.get("rule_id", "?"), rs.get("subject", "?"),
                        "edge_down", detail, Outcome(action="none", ok=True),
                    ))
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
                self._queue_failed_send(rule, rs, "edge_down",
                                        rs.get("last_detail", ""),
                                        outcome, now_ts, history_entries)
                fire_count += 1

            # Send-retry phase: fires whose action failed on a PRIOR tick get
            # re-attempted until delivered or exhausted. A failed page used to
            # be recorded honestly (outcome.ok=false) and then never tried
            # again — the operator was never paged for exactly the events the
            # detectors were built for. Runs only when a ruleset is available
            # (the action config lives in the rule); under _rules_unavailable
            # the queue holds, consistent with hold-all-edge-state semantics.
            self._retry_pending_sends(rules, state, now_ts, history_entries)

        state["last_tick_ts"] = now_ts
        state["last_tick_iso"] = datetime.datetime.fromtimestamp(now_ts).isoformat()
        state["rule_count"] = len(rules)
        state["condition_count"] = len(conds)
        state["error_count"] = error_count
        # Name the failing sources so a one-tick transient leaves an
        # identifiable witness after the next tick overwrites the count
        # (capped so a mass-failure tick can't bloat the state file).
        state["source_errors"] = [
            f"{c.subject}: {c.detail}"[:160] for c in source_error_conds[:8]
        ]
        state["fire_count"] = fire_count
        state["host"] = os.uname().nodename
        # Append history BEFORE saving state so the appends counter only
        # advances when the write actually landed — probe_history_write_failure
        # reads it as the honest "history should have grown" baseline (the old
        # fires-only baseline was blind to edge_down-only windows).
        hist_err = self.history.append(history_entries)
        if hist_err:
            log_error(f"history append failed: {hist_err}")
        elif history_entries:
            state["history_appends_total"] = (
                int(state.get("history_appends_total", 0) or 0)
                + len(history_entries))
        self.state_store.save(state)
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

    # --- failed-send retry ---------------------------------------------

    def _queue_failed_send(self, rule: dict, rs: dict, transition: str,
                           detail: str, outcome: Outcome, now_ts: float,
                           history_entries: list[dict]) -> None:
        """Queue an undelivered action for retry on subsequent ticks.

        The fire itself is already on the history record with ok=False; what
        never existed was any path that tried again. Config errors (unknown
        action kind) are not queued — retrying cannot fix the ruleset and the
        ok=False record already flags it. Queue overflow drops the OLDEST
        entry with a history record: a silently-vanished undelivered page is
        the exact defect class this exists to close.

        STATE-SET actions (Action.supersedes_pending_sends): any queued older
        send is superseded by THIS newer transition — delivered or queued —
        before normal queueing runs. Without this, a failed edge_up banner
        paint queued for retry outlives a SUCCESSFUL edge_down clear and
        re-paints a recovered condition on the glass (the display_alert
        resurrection: the ordering contract only held when BOTH edges
        failed). The queue for such actions therefore holds at most the one
        latest undelivered state; every supersede leaves a history witness.
        """
        action = self.actions.get(
            (rule.get("action") or {}).get("kind", "none")) if rule else None
        if getattr(action, "supersedes_pending_sends", False):
            pend = rs.get("pending_sends")
            if isinstance(pend, list) and pend:
                msg = (f"{len(pend)} undelivered send(s) superseded by a "
                       f"newer {transition} "
                       f"({'delivered' if outcome.ok else 'queued for retry'})"
                       f" — state-set action: replaying stale state would "
                       f"assert a condition that is no longer true")
                log_info(f"send-retry: {rs.get('rule_id')!r} "
                         f"({rs.get('subject')}): {msg}")
                history_entries.append(self._history_entry(
                    now_ts, rs.get("rule_id", "?"), rs.get("subject", "?"),
                    "send_retry_superseded", msg,
                    Outcome(action="none", ok=True),
                ))
                rs.pop("pending_sends", None)
        if outcome.ok:
            return
        if str(outcome.error or "").startswith("unknown action kind"):
            return
        pend = rs.setdefault("pending_sends", [])
        pend.append({
            "transition": transition,
            "detail": str(detail or ""),
            "attempts": 1,                # the original fire was attempt 1
            "first_failed_ts": now_ts,
            "attempted_at_ts": now_ts,    # retry phase skips this tick's own failures
            "error": outcome.error,
        })
        if len(pend) > self.PENDING_SENDS_CAP:
            dropped = pend.pop(0)
            msg = (f"pending-send queue full (cap {self.PENDING_SENDS_CAP}); "
                   f"dropped undelivered {dropped.get('transition')} after "
                   f"{int(dropped.get('attempts', 1) or 1)} attempt(s)")
            log_warning(f"send-retry: {rs.get('rule_id')!r} "
                        f"({rs.get('subject')}): {msg}")
            history_entries.append(self._history_entry(
                now_ts, rs.get("rule_id", "?"), rs.get("subject", "?"),
                "send_retry_dropped", msg,
                Outcome(action="none", ok=False, error="pending_sends overflow"),
            ))

    def _retry_pending_sends(self, rules: list[dict], state: dict,
                             now_ts: float,
                             history_entries: list[dict]) -> None:
        """Re-attempt every queued undelivered send, once per tick.

        Retries are deliveries of already-recorded fires, not new fires: they
        bypass cooldown, never touch edge state, and never call record_fire.
        Resolution (delivered / exhausted / rule-removed drop) goes on the
        history record; intermediate failures only update the queue entry —
        the entry itself is the witness while it waits. The queue lives in
        the persisted rule state, so retries survive a daemon restart — a
        crash page queued seconds before a SECOND crash still delivers from
        the next boot.
        """
        for rs in list(state.get("rules", {}).values()):
            if not isinstance(rs, dict):
                continue
            pend = rs.get("pending_sends")
            if not isinstance(pend, list) or not pend:
                continue
            rule = next((r for r in rules
                         if r["id"] == rs.get("rule_id")), None)
            keep: list[dict] = []
            for ent in pend:
                if not isinstance(ent, dict):
                    log_warning(f"send-retry: {rs.get('rule_id')!r} "
                                f"({rs.get('subject')}): dropping corrupt "
                                f"pending-send entry ({type(ent).__name__})")
                    continue
                if ent.get("attempted_at_ts") == now_ts:
                    keep.append(ent)  # queued THIS tick — first retry next tick
                    continue
                if rule is None:
                    # Rule removed with a send still undelivered: the action
                    # config lived in the rule, so the send is dead — loudly.
                    history_entries.append(self._history_entry(
                        now_ts, rs.get("rule_id", "?"), rs.get("subject", "?"),
                        "send_retry_dropped",
                        f"rule removed from ruleset with {ent.get('transition')} "
                        f"send still undelivered "
                        f"({int(ent.get('attempts', 1) or 1)} attempt(s))",
                        Outcome(action="none", ok=False, error="rule removed"),
                    ))
                    continue
                synth = Condition(
                    kind=rule.get("match", {}).get("kind", "?"),
                    subject=rs.get("subject", "?"),
                    detail=str(ent.get("detail", "")),
                )
                outcome = self._execute(
                    rule, synth, str(ent.get("transition", "edge_up")))
                ent["attempts"] = int(ent.get("attempts", 1) or 1) + 1
                ent["attempted_at_ts"] = now_ts
                if outcome.ok:
                    age = int(now_ts - float(
                        ent.get("first_failed_ts", now_ts) or now_ts))
                    detail = (f"{ent.get('transition')} delivered on attempt "
                              f"{ent['attempts']}, ~{age}s after first failure "
                              f"({str(ent.get('error', ''))[:120]})")
                    log_info(f"send-retry: {rs.get('rule_id')!r} "
                             f"({rs.get('subject')}): {detail}")
                    history_entries.append(self._history_entry(
                        now_ts, rs.get("rule_id", "?"), rs.get("subject", "?"),
                        "send_retry_delivered", detail, outcome))
                elif ent["attempts"] >= self.SEND_RETRY_MAX_ATTEMPTS:
                    detail = (f"{ent.get('transition')} NEVER delivered after "
                              f"{ent['attempts']} attempts; giving up "
                              f"(last error: {outcome.error})")
                    log_error(f"send-retry: {rs.get('rule_id')!r} "
                              f"({rs.get('subject')}): {detail}")
                    history_entries.append(self._history_entry(
                        now_ts, rs.get("rule_id", "?"), rs.get("subject", "?"),
                        "send_retry_exhausted", detail, outcome))
                else:
                    ent["error"] = outcome.error
                    keep.append(ent)
            if keep:
                rs["pending_sends"] = keep
            else:
                rs.pop("pending_sends", None)

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
        self._interval_s = interval_s
        # A grace streak is "matched continuously AS OBSERVED" — daemon
        # downtime is not observation. A persisted streak surviving a restart
        # let two unrelated transients bracketing the restart satisfy grace_s
        # instantly (the exact restart-window transients grace exists to
        # suppress), so the streak restarts with the daemon. (--once/cron mode
        # deliberately KEEPS streaks across invocations: there, each
        # invocation IS a tick.)
        self._reset_pending_streaks()
        log_info(f"mini-dudeai engine started · interval={interval_s}s · "
                 f"host={os.uname().nodename}")
        while not self._stop.is_set():
            state = None
            try:
                state = self.tick()
                if state.get("fire_count") or state.get("error_count"):
                    err_names = "; ".join(state.get("source_errors") or [])
                    log_info(f"tick {state.get('last_tick_iso')}: "
                             f"fires={state.get('fire_count')} "
                             f"src_errors={state.get('error_count')}"
                             + (f" ({err_names})" if err_names else ""))
            except Exception as e:
                # Observation tool must never die on a bad cycle.
                log_error(f"tick error (continuing): {type(e).__name__}: {e}")
            self._write_brief_safe(state)
            # Seed AFTER the tick, never before the first one: the tick runs
            # BootHealthSource.collect(), which must see the true pre-seed
            # marker state to assess (and latch) this boot's verdict —
            # seeding first would stamp a fresh marker and mask a real crash
            # that happened before the marker file first existed.
            self._seed_clean_exit_if_missing()
            self._stop.wait(interval_s)
        log_info("mini-dudeai engine stopped")
        self._write_clean_exit_marker()

    def _write_brief_safe(self, state: dict | None = None) -> None:
        """Atomic-write the warm-start brief if brief_path is set. Never raises —
        a brief-write failure must not take down the observation loop (same
        contract as the tick error handler). `state` is the tick's return
        value, threaded through so the brief doesn't re-read and re-parse the
        state file the tick wrote microseconds earlier."""
        if not self.brief_path:
            return
        try:
            from .brief import write_brief
            write_brief(self.state_store.path, self.history.path,
                        self.brief_path, state=state)
        except Exception as e:
            log_warning(f"brief write failed (continuing): "
                        f"{type(e).__name__}: {e}")

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
            atomic_write_text(self.clean_exit_path, f"{time.time():.3f}")
        except OSError as e:
            log_error(f"clean-exit marker write failed (continuing): "
                      f"{type(e).__name__}: {e}")

    def _reset_pending_streaks(self) -> None:
        """Zero every persisted grace streak (daemon startup). Best-effort."""
        try:
            state = self.state_store.load()
            touched = False
            for rs in state.get("rules", {}).values():
                if isinstance(rs, dict) and (rs.get("pending_since_ts")
                                             or rs.get("pending_ticks")):
                    rs["pending_since_ts"] = 0.0
                    rs["pending_ticks"] = 0
                    touched = True
            if touched:
                self.state_store.save(state)
        except OSError as e:
            log_warning(f"pending-streak reset failed (continuing): "
                        f"{type(e).__name__}: {e}")

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
