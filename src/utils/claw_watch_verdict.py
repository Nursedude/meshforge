"""Watched-node verdicts: the wire vocabulary and its Observation codec.

ONE home for the three verdict strings a claw tick carries per watched node,
and for the conversion between that wire dict and ``utils.observation``.

WHY HERE, not in ``mini_dudeai.claw_rf_watch`` (2026-10-02, tri-state adopter
#2): the producer (``claw_rf_watch``, inside mini) and the consumer
(``watchdog_probes_claw_watch``, inside the watchdog) both need the vocabulary.
While it lived in mini, the consumer had to guard the import and carry a
FALLBACK copy of the strings for "mini package absent" — two copies of one
vocabulary, the drift hazard honest_failure_modes #5 names, kept honest only by
a flag-and-test. ``utils`` is importable everywhere both run, so the copy and
its flag are gone: there is nothing left to drift.

THE MAPPING (the point of the type):

    heard         Seen(Heard(age_s))          we listened and this radio reached us
    silent        Seen(NotHeard(lower_bound)) we listened LONG ENOUGH and it did
                                              not — an OBSERVED absence
    unobservable  Unobservable(why)           not listened long enough / cannot
                                              hear that segment / uptime unknown
    (unreadable)  Failed(why)                 the reading itself was broken

``Seen(NotHeard)`` is the only actionable answer, and it is a distinct value
type rather than ``Seen(None)`` so a heard verdict with a missing age can never
be read as silence.

The WIRE stays the three strings (tick files on every box, mixed versions
during a roll): ``Failed`` is written as ``unobservable`` with its reason, and
an unrecognised string read back is ``Failed`` — never an answer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Union

from utils.observation import Failed, Observation, Seen, Unobservable

__all__ = ["HEARD", "SILENT", "UNOBSERVABLE", "Heard", "NotHeard", "Hearing",
           "from_wire"]

HEARD = "heard"
SILENT = "silent"
UNOBSERVABLE = "unobservable"


@dataclass(frozen=True)
class Heard:
    """The transmitter reached this receiver ``age_s`` ago (as reported)."""

    age_s: Any


@dataclass(frozen=True)
class NotHeard:
    """Not heard across a qualified listening window.

    ``silent_for_at_least_s`` is a LOWER bound (the firmware only knows "not
    since radio start"); ``None`` when the wire did not carry a number.
    """

    silent_for_at_least_s: Optional[float]


Hearing = Union[Heard, NotHeard]


def _num(x: Any) -> Optional[float]:
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return float(x)
    return None


def from_wire(v: Dict[str, Any]) -> Observation[Hearing]:
    """Read one per-node wire verdict back into an Observation.

    Anything that is not one of the three known strings is ``Failed``: a
    vocabulary that grew must not be read as an answer (honest_failure_modes
    #7), and it is never healthy and never a finding.
    """
    verdict = v.get("verdict")
    if verdict == HEARD:
        return Seen(Heard(v.get("age_s")))
    if verdict == SILENT:
        return Seen(NotHeard(_num(v.get("silent_for_at_least_s"))))
    if verdict == UNOBSERVABLE:
        return Unobservable(str(v.get("reason") or "").strip()
                            or "claw reported unobservable (no reason given)")
    return Failed("unrecognised watch verdict %r" % (verdict,))
