"""Watchdog tuning knobs — the ONE parser family (hfm #5) and its witness.

Split out of ``watchdog_probe_core`` (2026-11-26, MF025 size cap) when the
mini probes' import-time knobs joined the family; core re-exports every name
so existing importers are unchanged. A knob that silently does nothing is a
swallow with no witness (hfm #9): each IGNORED value warns once in the
journal AND is listed by ``rejected_knobs()`` for watchdog.json, which
survives journal rotation.
"""
from __future__ import annotations

import logging
import math
import os
import re

logger = logging.getLogger("watchdog")

_rejected_knobs: set = set()
#: every knob env name a parser has READ this process (rejected or not):
#: the subjects the ignored-knob probe can positively observe (r3)
_known_knobs: set = set()
_PLAIN_DECIMAL = re.compile(r"[0-9]+(?:\.[0-9]+)?")


def _warn_ignored_knob(env_name: str, raw: str, why: str, default) -> None:
    """One WARNING per (knob, value): an ignored knob is a swallow that must
    leave a witness (hfm #9), but not on every tick."""
    if (env_name, raw) in _rejected_knobs:
        return
    _rejected_knobs.add((env_name, raw))
    logger.warning("watchdog: %s=%r is not %s; IGNORED, using the default "
                   "of %s", env_name, raw, why, default)


def env_confirm_ticks(env_name: str, default: int) -> int:
    """One parser for every N-consecutive-ticks knob (hfm #5): resolved per
    call so an operator can retune without a restart.

    Only a plain positive integer is honoured. Anything else ("3.0", "1_000",
    "0", "O") falls back to ``default`` and leaves a WARNING, once per bad
    value: a knob that silently does nothing is a swallow with no witness
    (hfm #9), and the operator believes the retune is live."""
    _known_knobs.add(env_name)
    raw = os.environ.get(env_name)
    if raw is None:
        return default
    text = raw.strip()
    if text.isdigit() and text.isascii() and int(text) >= 1:
        return int(text)
    _warn_ignored_knob(env_name, raw, "a positive integer",
                       f"{default} tick(s)")
    return default


def env_int_at_least(env_name: str, default: int, floor: int) -> int:
    """An integer knob: plain ASCII digits only ("3.0", "+2", "1_0", "-1"
    are not meant), at least ``floor``. Same witness as the other parsers."""
    _known_knobs.add(env_name)
    raw = os.environ.get(env_name)
    if raw is None:
        return default
    text = raw.strip()
    if text.isdigit() and text.isascii() and int(text) >= floor:
        return int(text)
    _warn_ignored_knob(env_name, raw, f"an integer of at least {floor}", default)
    return default


def rejected_knobs() -> list:
    """Every knob value this process IGNORED, as [{knob, value}] sorted —
    the probe-visible witness (watchdog.json) beside the one-time journal
    WARNING, which rotates away (hfm #9)."""
    return [{"knob": k, "value": v} for k, v in sorted(_rejected_knobs)]


def env_float_above(env_name: str, default: float, floor: float) -> float:
    """A float knob: a plain decimal ("2.5", "4"), finite, above ``floor``.

    float() alone accepts "inf" and "nan" — and "inf" passes a ``> floor``
    check, so a multiplier knob could switch its alarm arm off unseen. It
    also takes "1_0", "+3" and non-ASCII digits, which ``env_confirm_ticks``
    rejects: one knob family, one notion of a valid value."""
    _known_knobs.add(env_name)
    raw = os.environ.get(env_name)
    if raw is None:
        return default
    text = raw.strip()
    if _PLAIN_DECIMAL.fullmatch(text):
        value = float(text)
        if math.isfinite(value) and value > floor:
            return value
    _warn_ignored_knob(env_name, raw, f"a finite number above {floor}", default)
    return default


def known_knobs() -> list:
    """Every knob env name a parser has read in this process, sorted."""
    return sorted(_known_knobs)
