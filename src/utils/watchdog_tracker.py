"""Watchdog signal lifecycle — first-seen, cleared, and the unobserved HOLD.

Split from ``watchdog_runner`` 2026-07-26 under the MF025 1,500-line cap (same
precedent as the probe-module splits). ``watchdog_runner`` re-exports the
public names, so existing importers are unaffected.

This module owns ONE contract — the answer to Pri-2: **only a positive
observation may clear a signal.** A probe that returns ``None`` because its
observation channel failed is indistinguishable, by return value alone, from
one that returns ``None`` because all is well. So the per-class disposition map
decides, and an unobserved class HOLDS its signals rather than clearing them
(honest_failure_modes #2 — never emit a recovery from data that is missing
because the channel failed).
"""
from __future__ import annotations

from dataclasses import replace
from typing import Dict, List, Optional, Tuple

from utils.watchdog_probe_core import Signal


#: Dispositions that represent a POSITIVE observation and therefore permit a
#: CLEARED transition. Anything else — indeterminate, unknown, a class missing
#: from the map, or no map at all — means the class was not observed this tick
#: and its signals are held. Deliberately a small allowlist rather than a
#: denylist of "bad" dispositions: a disposition added to the enum later must
#: fail CLOSED (hold) until someone decides it counts as an observation.
_CLEAR_PERMITTING_DISPOSITIONS = frozenset({"clean", "inert", "active"})


def observed_only(
    signals_with_first_seen: List[Tuple[Signal, float]],
) -> List[Tuple[Signal, float]]:
    """Drop signals that are only being HELD because their class went blind.

    Holding and acting are different questions. A held signal must still be
    REPORTED (silence would be a false recovery — the whole point of the hold),
    but it must never drive an ACTION: we do not currently know the condition
    is true, only that it was true when we could last see. Restarting a service
    on unobserved evidence is the same defect one layer along — a degraded
    observation converted into a real-world act.
    """
    return [(s, fs) for s, fs in signals_with_first_seen
            if not (s.extra or {}).get("unobserved_hold")]


def _class_was_observed(
    coverage: Optional[Dict[str, Dict[str, str]]], cls: str,
) -> bool:
    """True only when ``coverage`` positively says this class was observed."""
    if not isinstance(coverage, dict):
        return False  # dispatch crashed / no map — nothing was observed
    entry = coverage.get(cls)
    if not isinstance(entry, dict):
        return False  # absent or malformed entry is unobserved, not healthy
    return entry.get("disp") in _CLEAR_PERMITTING_DISPOSITIONS


class SignalTracker:
    """Tracks signal lifecycle so the runner can log edge transitions.

    Maps ``(class, subject)`` → (first_seen unix ts, last-known Signal). Each
    tick the runner passes the current signal set plus the coverage map; we
    diff against the previous tick and surface the deltas without re-logging
    steady state.
    """

    def __init__(self) -> None:
        #: key → (first_seen_ts, last_known Signal). The Signal is retained so
        #: a HELD key can be RE-EMITTED into the state file: ``write_state``
        #: serialises ``signals`` straight from the returned active list, so a
        #: held key that is merely kept in this table (and not re-emitted)
        #: still vanishes from watchdog.json — and every consumer, mini
        #: included, reads that disappearance as the recovery this fix exists
        #: to prevent. Holding must be visible where the CONSUMERS look, not
        #: only in this process's log.
        self._active: Dict[Tuple[str, str], Tuple[float, Signal]] = {}
        #: Keys currently withheld from clearing because their class was not
        #: observed. Kept so the runner can log the HOLD as an EDGE, matching
        #: the rest of this file's transition-only logging: a permanently-blind
        #: probe must not emit a WARNING every tick forever.
        self._held: set = set()

    def update(
        self, current: List[Signal], *, now: float,
        coverage: Optional[Dict[str, Dict[str, str]]],
    ) -> Tuple[List[Tuple[Signal, float]], List[Tuple[str, str]],
               List[Tuple[str, str]]]:
        """Diff against previous tick, gated by what was actually OBSERVED.

        ``coverage`` is this tick's per-class disposition map (the output of
        ``build_coverage``). It is a REQUIRED keyword — Pri-2, 2026-07-26 —
        because absence of a signal and absence of an OBSERVATION were
        previously indistinguishable here, and a defaulted parameter would let
        a call site silently keep the old, wrong semantics
        (honest_failure_modes #7: a closed contract needs closed consumers).

        **Only a positive observation may clear a signal.** A probe returning
        ``None`` because its channel failed is byte-identical, at this layer,
        to one returning ``None`` because all is well — so ~50 probes carrying
        the documented ``indeterminate`` self-guard could emit a false CLEARED
        the moment they went blind. Live instance: ``rules_seed_drift`` logged
        CLEARED six seconds after oomd killed 49 processes, on a box where the
        drift was real and re-fired next boot.

        Clearing is therefore permitted only for a class whose disposition is
        in ``_CLEAR_PERMITTING_DISPOSITIONS``:

        - ``clean`` / ``inert`` — the probe RAN and positively observed.
        - ``active``            — the class fired this tick, so a subject that
          vanished from it genuinely recovered.

        Everything else HOLDS the prior signal. That includes:

        - ``indeterminate`` / ``unknown`` — explicitly unobserved.
        - a class MISSING from the map — ``build_coverage`` emits an entry for
          every member of the closed enum, so this is unreachable in
          production; it fails safe rather than clearing on a partial map.
        - ``coverage is None`` — probe dispatch crashed mid-tick, so NOTHING
          was observed and every active signal is held. That branch previously
          cleared the entire box's signals on any probe-dispatch exception.

        A held signal keeps its original ``first_seen``, so a blind window
        cannot make an hours-old wedge read as brand new when it reappears, and
        is re-emitted carrying ``extra["unobserved_hold"] = True`` so consumers
        can distinguish "still true" from "last known, currently blind".

        Returns:
            (active, newly_cleared, newly_held) where:
              - active: (signal, first_seen_ts) for every signal to REPORT this
                tick — observed ones plus held ones. first_seen carries the
                original ts for anything persisting.
              - newly_cleared: (class, subject) keys that were active before,
                are absent now, AND whose class was positively observed.
              - newly_held: (class, subject) keys withheld from clearing this
                tick, reported ONLY on the tick the hold BEGINS — an edge, like
                the other two, so a permanently-blind probe does not log every
                tick forever. The runner logs these; a hold that left no witness
                would be exactly the swallow this fix exists to remove
                (honest_failure_modes #9), and the standing state stays visible
                as the class's ``indeterminate`` entry in the coverage map.
        """
        current_keys = {s.key() for s in current}
        previous_keys = set(self._active.keys())
        vanished = previous_keys - current_keys

        newly_cleared: List[Tuple[str, str]] = []
        still_held: set = set()
        newly_held: List[Tuple[str, str]] = []
        for key in vanished:
            if _class_was_observed(coverage, key[0]):
                newly_cleared.append(key)
            else:
                still_held.add(key)
                if key not in self._held:
                    newly_held.append(key)
        self._held = still_held

        first_seen_ts_for: List[Tuple[Signal, float]] = []
        for sig in current:
            key = sig.key()
            prior = self._active.get(key)
            if prior is None:
                # New transition
                self._active[key] = (now, sig)
                first_seen_ts_for.append((sig, now))
            else:
                # Still active — preserve original first_seen, refresh detail
                first_seen = prior[0]
                self._active[key] = (first_seen, sig)
                first_seen_ts_for.append((sig, first_seen))

        # Re-emit held signals so they persist into the state file. Marked, so
        # a consumer can tell "still true" from "last known, currently blind"
        # and never presents a stale detail as a fresh observation.
        for key in sorted(still_held):
            prior = self._active.get(key)
            if prior is None:  # defensive; a held key is by definition present
                continue
            first_seen, last_sig = prior
            first_seen_ts_for.append((
                replace(last_sig, extra={
                    **(last_sig.extra or {}),
                    "unobserved_hold": True,
                }),
                first_seen,
            ))

        # Only genuinely-cleared keys leave the table. Held keys stay active
        # (with their original first_seen) until an observation says otherwise.
        for key in newly_cleared:
            self._active.pop(key, None)

        return first_seen_ts_for, newly_cleared, newly_held
