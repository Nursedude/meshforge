"""The uptime gate — makes a watched node's ``never`` mean something.

WHY THIS EXISTS (2026-07-29)
---------------------------
The claw watch list reports, per node id, either an age or ``never``. ``never``
is the interesting state — it is how a MUTE TRANSMITTER shows up, the blind spot
``mesh_heard_age_s`` structurally cannot see (with neighbours chattering at 6-8
pkt/min the channel reads busy while our own gateway is silent).

But ``never`` is only as strong as the listening window behind it. Measured the
day the field shipped: seconds after arming, three of four watched fleet radios
read ``never`` — because the claw had been up for **ten seconds**, and those
radios transmit roughly once every couple of hours. A probe firing on ``never``
alone would page on every claw reboot, every flash, every power cycle. That is
the same defect this field exists to remove: an unobservable state rendered as a
confident claim.

So ``never`` is split into two verdicts by the listening window:

    SILENT        the claw listened for comfortably longer than this node's
                  expected transmit interval and heard nothing. Actionable.
    UNOBSERVABLE  the claw has not listened long enough for silence to mean
                  anything yet. NOT a finding, and never rendered as one.

THE BIAS IS DELIBERATE: the gate under-fires. A node that really has gone mute is
still caught on the first tick after the window elapses; a node that is merely
un-listened-for is never paged about. Reversing that trade buys nothing and costs
the field's credibility, which is the only reason anyone reads it.

The expected interval is a property of what that radio is configured to broadcast
(Meshtastic's NodeInfo default is ~3 h), so it is CONFIGURABLE with a conservative
default rather than hardcoded per fleet — the identity-in-code defect that cost
this fleet a 6.5 h outage the same day.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Meshtastic's default NodeInfo broadcast is ~3 h; position/telemetry intervals
# vary by config. 3 h is the conservative assumption for "this radio should have
# said something by now".
DEFAULT_EXPECTED_TX_INTERVAL_S = 3 * 3600

# How many expected intervals must elapse before silence is a finding. 3 gives a
# node two missed broadcasts of slack, so a single skipped beat (airtime
# contention, a duty-cycle pause, a marginal link) is not an incident.
DEFAULT_SILENCE_MULTIPLE = 3

HEARD = "heard"
SILENT = "silent"
UNOBSERVABLE = "unobservable"


def required_window_s(expected_interval_s: Optional[float] = None,
                      multiple: Optional[float] = None) -> float:
    """Listening seconds needed before ``never`` becomes a finding."""
    iv = (DEFAULT_EXPECTED_TX_INTERVAL_S if expected_interval_s is None
          else expected_interval_s)
    m = DEFAULT_SILENCE_MULTIPLE if multiple is None else multiple
    return float(iv) * float(m)


def classify_watch(
    watched: Optional[Dict[str, Any]],
    uptime_s: Optional[float],
    *,
    expected_intervals: Optional[Dict[str, float]] = None,
    default_interval_s: Optional[float] = None,
    multiple: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Per-node verdicts for a claw's watch list, gated on the listening window.

    ``watched`` is ``parse_lora_stats()["watched"]``; ``uptime_s`` is the claw's
    own uptime — the length of the listening window, because the firmware's
    counters reset at boot.

    Returns None when ``watched`` is None/empty: the field is absent (old
    firmware, or no ids configured), so there is NOTHING to say about our
    transmitters. That is not the same as "all fine" and must not read as it.

    Per node: ``{verdict, age_s, silent_for_at_least_s, required_window_s,
    reason}``.

    * ``heard``        — an age came back. The transmitter reaches this claw.
    * ``silent``       — ``never`` AND the window elapsed. ACTIONABLE.
                         ``silent_for_at_least_s`` is the claw's uptime, an
                         honest LOWER bound: the firmware knows only "not since
                         radio start", so true silence may be longer.
    * ``unobservable`` — ``never`` but the window has not elapsed; or uptime is
                         unknown; or the entry was garbled (``parse_error``).
                         Blindness, stated as blindness.
    """
    if not isinstance(watched, dict) or not watched:
        return None
    ivs = expected_intervals or {}
    out: Dict[str, Any] = {}
    for node, rec in watched.items():
        if not isinstance(rec, dict):
            continue
        need = required_window_s(ivs.get(node, default_interval_s), multiple)

        if rec.get("parse_error"):
            out[node] = {"verdict": UNOBSERVABLE, "age_s": None,
                         "silent_for_at_least_s": None,
                         "required_window_s": need,
                         "reason": "watch entry unreadable — cannot judge"}
            continue

        age = rec.get("age_s")
        if age is not None:
            out[node] = {"verdict": HEARD, "age_s": age,
                         "silent_for_at_least_s": None,
                         "required_window_s": need,
                         "reason": "heard %ss ago" % age}
            continue

        # never
        if uptime_s is None:
            out[node] = {"verdict": UNOBSERVABLE, "age_s": None,
                         "silent_for_at_least_s": None,
                         "required_window_s": need,
                         "reason": "not heard, but the claw's uptime is unknown "
                                   "— the listening window cannot be established"}
            continue
        if float(uptime_s) < need:
            out[node] = {"verdict": UNOBSERVABLE, "age_s": None,
                         "silent_for_at_least_s": float(uptime_s),
                         "required_window_s": need,
                         "reason": "not heard, but the claw has listened only "
                                   "%.0fs of the %.0fs this node's transmit "
                                   "interval needs — silence means nothing yet"
                                   % (float(uptime_s), need)}
            continue
        out[node] = {"verdict": SILENT, "age_s": None,
                     "silent_for_at_least_s": float(uptime_s),
                     "required_window_s": need,
                     "reason": "NOT heard in %.0fs of listening (>= the %.0fs "
                               "window) — this radio is not reaching this claw"
                               % (float(uptime_s), need)}
    return out or None


def summarise(verdicts: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Counts for a panel/probe, with the blind count kept SEPARATE.

    ``unobservable`` is never folded into either healthy or silent — the point of
    the gate is that "we have not looked long enough" keeps its own column
    instead of being averaged into a reassuring number.
    """
    if not verdicts:
        return None
    heard = [n for n, v in verdicts.items() if v.get("verdict") == HEARD]
    silent = [n for n, v in verdicts.items() if v.get("verdict") == SILENT]
    blind = [n for n, v in verdicts.items() if v.get("verdict") == UNOBSERVABLE]
    return {
        "heard": sorted(heard),
        "silent": sorted(silent),
        "unobservable": sorted(blind),
        "actionable": bool(silent),
    }
