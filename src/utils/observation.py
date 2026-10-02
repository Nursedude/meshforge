"""Observation — the truth path's result type: Seen | Unobservable | Failed.

WHY (ADR `.claude/plans/adr_truth_kernel_2026_10_02.md`, measured): of 128
incidents in persistent_issues, 23% (two raters, kappa 0.91) would likely have
been prevented by a result type with distinct Seen / Unobservable / Failed
variants and NO implicit default. The domain's signature defect is a degraded
read mapped to a valid-looking value — ``[]`` because the read failed, ``None``
meaning both "absent" and "could not look", a string constant one module spells
"unobservable" and the next forgets (six hand-rolled copies existed when this
module landed). honest_failure_modes #1/#2, made a type instead of a habit.

THE THREE ANSWERS, and the one rule that binds them:

* ``Seen(value)``      — we looked and this is what is there. A definite
                         ABSENCE we observed is ``Seen(None)`` (or an empty
                         container): "the zone said no" is a measurement.
* ``Unobservable(why)`` — we could not look: no server answered, no access,
                         the channel is down, the clock cannot be trusted.
* ``Failed(why)``      — we looked and the reading itself was broken:
                         malformed, contradictory, an error mid-observation.

**Neither Unobservable nor Failed is ever healthy, empty, zero or absent.**
Downstream they HOLD prior state or surface blindness; they never fall through
to a default.

How that is enforced, rather than hoped:

1. No variant has a default accessor (no ``value_or``, no ``.get``). Read a
   value by matching ``Seen``; anything else forces a decision.
2. ``bool()`` RAISES on every variant. ``if obs:`` / ``obs or []`` — the exact
   collapse this type exists to end — fails the first time it runs, in any
   file, typed or not.
3. Blindness must carry its reason: an empty ``why`` is refused at
   construction (a swallow with no witness is honest_failure_modes #9).
4. Consumers ``match`` exhaustively and end with ``assert_never``; mypy
   rejects a missing variant (pinned by tests/test_truth_path_types.py, which
   runs in CI — a type that is only checked on the author's box is prose).

Limits (measured by review 2026-10-02 — what the checker does NOT see):
``if obs:`` IS caught twice (``bool()`` raises; mypy's ``warn_unreachable``
flags the dead branch because ``__bool__`` is ``NoReturn``). NOT caught:
``isinstance(o, Seen) and o.value or []``, ``getattr(o, "value", None)``,
``o == Seen(None)``, and a ``case _: pass`` catch-all (the gate requires
``assert_never`` in truth-path files for that one). Match on the variants.

Usage::

    match resolve(name):
        case Seen(value=None):        ...  # observed: not there
        case Seen(value=ip):          ...  # observed: there
        case Unobservable() | Failed(): ...  # hold / surface blindness
        case _ as unreachable:        assert_never(unreachable)
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Generic, NoReturn, TypeVar, Union

if sys.version_info >= (3, 11):
    from typing import assert_never
else:  # MeshAnchor still supports 3.10 (this file is byte-identical in both
    # repos): same contract — a statically unreachable call, loud at runtime.
    def assert_never(arg: NoReturn, /) -> NoReturn:
        raise AssertionError(f"unreachable: unhandled Observation {arg!r}")

__all__ = ["Seen", "Unobservable", "Failed", "Observation", "describe",
           "assert_never"]

T = TypeVar("T", covariant=True)


class _NoTruthiness:
    """Refuses ``bool()`` — an Observation is never implicitly True/False."""

    __slots__ = ()

    def __bool__(self) -> NoReturn:
        raise TypeError(
            f"{type(self).__name__} has no truth value: match on Seen / "
            "Unobservable / Failed instead (a blind read must never read as "
            "empty or healthy — utils/observation.py)")


@dataclass(frozen=True)
class Seen(_NoTruthiness, Generic[T]):
    """We looked; this is what is there (``None`` = observed absence)."""

    value: T


def _need_reason(kind: str, why: object) -> None:
    if not isinstance(why, str) or not why.strip():
        raise ValueError(f"{kind} needs a non-empty reason: blindness without "
                         "a witness is indistinguishable from health")


@dataclass(frozen=True)
class Unobservable(_NoTruthiness):
    """We could not look. Never empty, never healthy, never absent."""

    why: str

    def __post_init__(self) -> None:
        _need_reason("Unobservable", self.why)


@dataclass(frozen=True)
class Failed(_NoTruthiness):
    """We looked and the reading itself was broken."""

    why: str

    def __post_init__(self) -> None:
        _need_reason("Failed", self.why)


Observation = Union[Seen[T], Unobservable, Failed]


def describe(obs: Observation[object]) -> str:
    """One-line, log-safe rendering. Also the reference exhaustive match."""
    match obs:
        case Seen(value=v):
            return f"seen: {v!r}"
        case Unobservable(why=w):
            return f"UNOBSERVABLE: {w}"
        case Failed(why=w):
            return f"FAILED: {w}"
        case _ as unreachable:
            assert_never(unreachable)
