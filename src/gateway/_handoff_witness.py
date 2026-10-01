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
        unconfirmed     a real hand-off, radio up, not heard — WARNING

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
        self._early: Deque[Tuple[float, str]] = deque(maxlen=64)
        self.counts: Dict[str, int] = {k: 0 for k in COUNTER_KEYS}

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
            for i, (t, heard) in enumerate(self._early):
                if now - t <= EARLY_COPY_KEEP_S and _rank(heard, norm) >= 0:
                    del self._early[i]
                    self.counts['cross_preset_handoff_confirmed'] += 1
                    return
            if len(self._pending) >= self._max_pending:
                self.counts['cross_preset_handoff_untracked'] += 1
                return
            self._pending.append([now, norm, content_id, blind])

    def observe(self, text: str) -> bool:
        """Confirm the best-matching pending hand-off this heard text carries."""
        heard = _norm(text)
        if not heard:
            return False
        with self._lock:
            best, best_rank = None, 99
            for i, entry in enumerate(self._pending):
                r = _rank(heard, entry[1])
                if 0 <= r < best_rank:
                    best, best_rank = i, r
                    if r == 0:
                        break
            if best is None:
                self._early.append((self._clock(), heard))
                return False
            del self._pending[best]
            self.counts['cross_preset_handoff_confirmed'] += 1
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
        for _, norm, cid, blind in expired:
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
                logger.warning(
                    "Cross-preset hand-off UNCONFIRMED: not heard on the "
                    f"secondary radio within {self._window_s:.0f}s (RNS path or "
                    f"peer gateway down?): {norm[:60]}")
            with self._lock:
                self.counts[key] += 1
            out.append(norm)
        return out

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return dict(self.counts)
