"""Cross-preset hand-off witness — did the message actually reach the other preset?

With true-origin on, mesh_bridge drops its own cross-preset copy of an ORIGINAL
LongFast broadcast whose content_id the M->R leg already claimed: the message
is left to the RNS path, and a peer gateway (moc3) re-broadcasts it on
SHORT_TURBO. Measured on moc 2026-09-30: 11 of 11 such hand-offs were heard
back on moc's own SHORT_TURBO radio 1-9 s later (tagged ``[RNS:<label>]``,
sometimes with a suffix line such as ``\\nSNR:… @kiai``).

Nothing confirmed that per message. If RNS or the peer gateway is down, the
message reaches SHORT_TURBO by neither path and the only trace was a counter
that called it a loop. This witness pairs events the bridge already sees:

    note(text, cid)  primary-leg cid-only broadcast drop
    observe(text)    any broadcast heard on the secondary (SHORT_TURBO) radio
    sweep()          every cleanup tick (10 s). An entry older than the window
                     and never heard back is, in this order:
        not_handed_off  the M->R leg never marked it as going to RNS (an
                        oracle-consumed query, an echo of our own inject) —
                        nothing was owed, no warning
        unobservable    the secondary radio was down at ANY tick in its window
                        — blind, not lost (honest_failure_modes #2)
        unconfirmed     a real hand-off, radio up, not heard — INFO per message
                        (moc 22:18:56: the peer DID transmit, this radio missed
                        it — half-duplex/RF loss is routine), WARNING only from
                        UNHEARD_STREAK_WARN in a row with no confirmation between
                        (an RNS or peer outage loses every hand-off; an RF miss
                        is isolated). The first confirmation after a warned run
                        logs the recovery. Each confirmation logs DEBUG + delay.

The run ("in a row"), reviewed 2026-09-30 (C/D):
  * Only a TAGGED copy (``[RNS:…]`` etc., the peer's mark) resets it. An
    untagged copy can be native secondary-preset traffic ("test", a beacon
    posted on both presets), so it may confirm a LONG hand-off but never
    clears an outage alarm; a short untagged copy confirms nothing.
  * An entry noted BEFORE the newest tag-confirmed hand-off still counts as
    unconfirmed but does not extend the run: outage-era leftovers expiring
    after recovery must not re-raise the alarm (C#1).
  * The WARNING names only what was seen ("not heard by this box's secondary
    radio") plus a discriminator: how many OTHER secondary broadcasts were
    heard during the run. Some ⇒ this radio receives, suspect RNS / the peer;
    none ⇒ this radio may be deaf (antenna, desense) or the mesh is quiet.
  * Counted in HAND-OFFS, not time, with no decay: at one hand-off an hour an
    outage warns after ~3 h, and three genuine RF misses days apart with no
    confirmation between would warn once. In memory only: a gateway restart
    zeroes the run and drops pending entries uncounted. The gauge
    ``cross_preset_handoff_unheard_streak`` is the LAST KNOWN run, not live.

Matching (contextless review, 2026-09-30): bridge tags stripped, whitespace
collapsed PER LINE. Heard text equal to the hand-off wins first; else heard
text that continues on a NEW LINE (the peer's suffix); else a heard text of
>= 24 chars the hand-off starts with (the peer's chunker splits long text,
tagging every chunk). No bare prefix: "hike?" must not confirm "hi". Copies
heard just BEFORE the drop is processed (MQTT backlog) are kept briefly and
matched at note time.

Scope, stated: primary -> secondary only (moc's measured direction; 24 h had
zero untagged secondary drops); only moc runs mesh_bridge with true-origin
on (fleet config, 2026-09-30). "Heard by this box's secondary radio" proves
on-air presence near this box, not reception by every node. Connectivity is
judged from ``_secondary_connected``, which only the SERIAL leg clears on a
dead link (moc's leg is serial; an MQTT/TCP secondary would read blind as
lost). Read-only: never changes what is forwarded or dropped.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, List, Tuple

from .base_handler import _strip_bridge_tags

logger = logging.getLogger(__name__)

#: Seconds a hand-off may take to be heard back. Measured 1-9 s on moc.
HANDOFF_CONFIRM_WINDOW_S = 30.0
#: Bound on outstanding hand-offs; overflow is COUNTED, never silent.
HANDOFF_MAX_PENDING = 256
#: A chunk/truncated copy must be at least this long to confirm by prefix.
MIN_PARTIAL_MATCH_CHARS = 24
#: Copies heard before their drop was noted are kept this long.
EARLY_COPY_KEEP_S = 15.0
#: Unheard hand-offs in a row (no confirmation between) before WARNING.
UNHEARD_STREAK_WARN = 3
STREAK_KEY = 'cross_preset_handoff_unheard_streak'

COUNTER_KEYS = (
    'cross_preset_handoff_confirmed',
    'cross_preset_handoff_unconfirmed',
    'cross_preset_handoff_unobservable',
    'cross_preset_handoff_not_handed_off',
    'cross_preset_handoff_untracked',
)


def _norm(text: str) -> str:
    lines = [" ".join(ln.split())
             for ln in _strip_bridge_tags(text or "").splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _rank(heard: str, handed: str) -> int:
    """0 = exact, 1 = suffix on a new line, 2 = long partial; -1 = no match."""
    if heard == handed:
        return 0
    if heard.startswith(handed + "\n"):
        return 1
    if len(heard) >= MIN_PARTIAL_MATCH_CHARS and handed.startswith(heard):
        return 2
    return -1


def _is_tagged(raw: str) -> bool:
    raw = (raw or "").lstrip()
    return _strip_bridge_tags(raw) != raw


def _confirms(rank: int, heard: str, tagged: bool) -> bool:
    """A match confirms only if it cannot be native secondary-preset traffic:
    a TAGGED copy at any rank, or an untagged one long enough to be specific."""
    return rank >= 0 and (tagged or len(heard) >= MIN_PARTIAL_MATCH_CHARS)


def _log_unheard(norm: str, window_s: float, streak: int, rx: int,
                 stale: bool) -> None:
    if stale:
        logger.info(
            "Cross-preset hand-off not heard by this box's secondary radio "
            f"within {window_s:.0f}s; noted before a later confirmed hand-off, "
            f"so not counted toward the run: {norm[:60]}")
    elif streak >= UNHEARD_STREAK_WARN:
        why = (f"it heard {rx} other broadcast(s) meanwhile, so it is "
               "receiving: RNS path or peer gateway down?" if rx else
               "it heard nothing else meanwhile: this radio may not be "
               "receiving, or the mesh is quiet")
        logger.warning(
            f"Cross-preset hand-offs: {streak} in a row not heard by this "
            f"box's secondary radio within {window_s:.0f}s ({why}): "
            f"{norm[:60]}")
    else:
        logger.info(
            "Cross-preset hand-off not heard by this box's secondary radio "
            f"within {window_s:.0f}s (isolated RF miss, or RNS/peer down; "
            f"{streak} in a row): {norm[:60]}")


class HandoffWitness:
    """Pairs hand-off drops with the copy heard on the other preset."""

    def __init__(self, observable: Callable[[], bool],
                 handed_off: Callable[[str], bool],
                 window_s: float = HANDOFF_CONFIRM_WINDOW_S,
                 max_pending: int = HANDOFF_MAX_PENDING,
                 clock: Callable[[], float] = time.monotonic):
        self._observable = observable
        self._handed_off = handed_off
        self._window_s = window_s
        self._max_pending = max_pending
        self._clock = clock
        self._lock = threading.Lock()
        # [noted_at, normalized text, content_id, blind_in_window]
        self._pending: List[list] = []
        self._early: Deque[Tuple[float, str, bool]] = deque(maxlen=64)
        self.counts: Dict[str, int] = {k: 0 for k in COUNTER_KEYS}
        self._streak = 0
        self._confirmed_through = float("-inf")  # newest tag-confirmed noted_at
        self._rx_in_run = 0     # other secondary broadcasts heard since reset

    def _confirm_locked(self, noted_at: float, tagged: bool) -> int:
        """Count a confirmation. Only a TAGGED copy resets the run (an untagged
        one may be native traffic); returns the run it ended, else 0."""
        self.counts['cross_preset_handoff_confirmed'] += 1
        if not tagged:
            return 0
        prev, self._streak, self._rx_in_run = self._streak, 0, 0
        self._confirmed_through = max(self._confirmed_through, noted_at)
        return prev

    def _log_confirmed(self, norm: str, delay_s: float, prev_streak: int) -> None:
        when = (f"after {delay_s:.1f}s" if delay_s >= 0
                else f"{-delay_s:.1f}s before the drop was processed")
        logger.debug(f"Cross-preset hand-off confirmed: heard on the secondary "
                     f"radio {when}: {norm[:60]}")
        if prev_streak >= UNHEARD_STREAK_WARN:
            logger.warning(
                "Cross-preset hand-off path recovered after "
                f"{prev_streak} unheard in a row: {norm[:60]}")

    def _seeing(self) -> bool:
        try:
            return bool(self._observable())
        except Exception:
            return False

    def note(self, text: str, content_id: str) -> None:
        norm = _norm(text)
        if not norm:
            return
        now = self._clock()
        blind = not self._seeing()
        with self._lock:
            for i, (t, heard, tagged) in enumerate(self._early):
                if (now - t <= EARLY_COPY_KEEP_S
                        and _confirms(_rank(heard, norm), heard, tagged)):
                    del self._early[i]
                    prev = self._confirm_locked(now, tagged)
                    break
            else:
                prev = None
            if prev is None and len(self._pending) >= self._max_pending:
                self.counts['cross_preset_handoff_untracked'] += 1
                return
            if prev is None:
                self._pending.append([now, norm, content_id, blind])
        if prev is not None:
            self._log_confirmed(norm, t - now, prev)   # negative: heard first

    def observe(self, text: str) -> bool:
        """Confirm the best-matching pending hand-off this heard text carries."""
        heard = _norm(text)
        if not heard:
            return False
        tagged = _is_tagged(text)
        with self._lock:
            best, best_rank = None, 99
            for i, entry in enumerate(self._pending):
                r = _rank(heard, entry[1])
                if _confirms(r, heard, tagged) and r < best_rank:
                    best, best_rank = i, r
                    if r == 0:
                        break
            if best is None:
                self._early.append((self._clock(), heard, tagged))
                self._rx_in_run += 1        # the radio IS receiving
                return False
            noted_at, norm = self._pending[best][0], self._pending[best][1]
            del self._pending[best]
            prev = self._confirm_locked(noted_at, tagged)
            now = self._clock()
        self._log_confirmed(norm, now - noted_at, prev)
        return True

    def sweep(self) -> List[str]:
        """Mark blindness for the tick; expire old entries; return their texts."""
        seeing = self._seeing()
        cutoff = self._clock() - self._window_s
        with self._lock:
            if not seeing:
                for entry in self._pending:
                    entry[3] = True
            expired = [e for e in self._pending if e[0] < cutoff]
            self._pending = [e for e in self._pending if e[0] >= cutoff]
        out = []
        for noted_at, norm, cid, blind in expired:
            try:
                owed = bool(self._handed_off(cid))
            except Exception:
                owed = True     # can't tell: never let doubt hide a loss
            if not owed:
                key = 'cross_preset_handoff_not_handed_off'
                logger.debug(f"cid-only drop was not a hand-off (consumed or "
                             f"echo); nothing owed: {norm[:60]}")
            elif blind:
                key = 'cross_preset_handoff_unobservable'
                logger.warning(
                    "Cross-preset hand-off UNOBSERVABLE: secondary radio down "
                    f"during the window, delivery unknown: {norm[:60]}")
            else:
                key = 'cross_preset_handoff_unconfirmed'
            with self._lock:
                self.counts[key] += 1
                stale = noted_at < self._confirmed_through
                if key == 'cross_preset_handoff_unconfirmed' and not stale:
                    self._streak += 1
                streak, rx = self._streak, self._rx_in_run
            if key == 'cross_preset_handoff_unconfirmed':
                _log_unheard(norm, self._window_s, streak, rx, stale)
            out.append(norm)
        return out

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return {**self.counts, STREAK_KEY: self._streak}
