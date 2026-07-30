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
    segments: Optional[Dict[str, str]] = None,
    claw_segment: Optional[str] = None,
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
                         unknown; or the entry was garbled (``parse_error``);
                         or the node is on a different RF SEGMENT (below).
                         Blindness, stated as blindness.

    THE SEGMENT GATE (2026-07-30)
    -----------------------------
    The uptime gate above answers "have we listened long enough?". It cannot
    answer "could this claw EVER hear this node?" — and on this fleet the answer
    is sometimes no, by design.

    MeshForge runs a deliberately TWO-PRESET fleet: LONG_FAST/ch20 plus a
    SHORT_TURBO/ch8 segment (the ST<>meshforge<>LF topology in
    ``docs/fleet_presets.yaml``; ST was chosen for throughput on the RNS leg).
    Different preset means different bandwidth, spreading factor AND centre
    frequency, so the two segments are mutually undemodulable on RF. The domain
    already states the rule — *"cross-preset visibility requires MQTT/RNS
    bridging, not RF"* — but the rule lived in prose, and this RF-only witness
    was built without inheriting it (the closed-enum shape, honest_failure_modes
    #7: a registry grew a consumer that does not know).

    Measured 2026-07-30: two watched nodes read ``silent`` after 17.9 h of
    listening with the reason "this radio is not reaching this claw". Both were
    the two SHORT_TURBO boxes; both were transmitting fine, proven by a third
    box on their own segment receiving them over the air. The verdict was
    literally true and totally misleading — an UNOBSERVABLE condition wearing
    SILENT's clothes, which is the exact defect this module exists to prevent.

    So a declared cross-segment node is ``unobservable``, and no amount of
    listening promotes it: the check runs BEFORE the uptime gate because more
    listening cannot fix a frequency mismatch.

    ``segments`` maps node id -> declared RF segment; ``claw_segment`` is this
    claw's own. Both unknown = gate INERT (never invent a mismatch from missing
    config — absence of a declaration is not evidence of a conflict).

    OBSERVATION OUTRANKS DECLARATION. If a node declared cross-segment is
    actually HEARD, the radio settles it and the verdict stays ``heard`` — but
    the entry is stamped ``segment_conflict`` so the wrong declaration surfaces
    instead of being silently tolerated. Declared values drift from reality on
    this fleet; a mismatch between what we declared and what the antenna heard
    is a finding about the CONFIG, and it gets a witness rather than a shrug.
    """
    if not isinstance(watched, dict) or not watched:
        return None
    ivs = expected_intervals or {}
    segs = segments or {}
    out: Dict[str, Any] = {}
    for node, rec in watched.items():
        if not isinstance(rec, dict):
            continue
        need = required_window_s(ivs.get(node, default_interval_s), multiple)
        node_seg = segs.get(node)
        cross = bool(claw_segment and node_seg and node_seg != claw_segment)

        if rec.get("parse_error"):
            out[node] = {"verdict": UNOBSERVABLE, "age_s": None,
                         "silent_for_at_least_s": None,
                         "required_window_s": need,
                         "reason": "watch entry unreadable — cannot judge"}
            continue

        age = rec.get("age_s")
        if age is not None:
            # Hearing is positive physical evidence and outranks every
            # declaration. A cross-segment node we can HEAR means the DECLARATION
            # is wrong, not the radio — stamp it so the drift is findable.
            rec_out = {"verdict": HEARD, "age_s": age,
                       "silent_for_at_least_s": None,
                       "required_window_s": need,
                       "reason": "heard %ss ago" % age}
            if cross:
                rec_out["segment_conflict"] = True
                rec_out["reason"] += (
                    " — but it is DECLARED on segment %r while this claw listens "
                    "on %r. The antenna outranks the config: the declaration is "
                    "wrong and should be corrected." % (node_seg, claw_segment))
            out[node] = rec_out
            continue

        # never. The segment gate runs FIRST: no amount of listening makes a
        # node on another frequency/modulation audible, so the uptime window is
        # not the right question for it.
        if cross:
            out[node] = {"verdict": UNOBSERVABLE, "age_s": None,
                         "silent_for_at_least_s": None,
                         "required_window_s": need,
                         "segment_conflict": True,
                         "reason": "not heard, and it CANNOT be heard here: this "
                                   "node is on RF segment %r while the claw "
                                   "listens on %r. Cross-segment visibility "
                                   "needs the MQTT/RNS bridge, not RF — silence "
                                   "on this claw says nothing about that radio."
                                   % (node_seg, claw_segment)}
            continue

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
    # Kept in its own column for the same reason `unobservable` is: a wrong
    # segment declaration is a real finding about the CONFIG, and folding it
    # into either healthy or silent is how it would never get fixed.
    conflicts = [n for n, v in verdicts.items() if v.get("segment_conflict")]
    return {
        "heard": sorted(heard),
        "silent": sorted(silent),
        "unobservable": sorted(blind),
        "segment_conflicts": sorted(conflicts),
        "actionable": bool(silent),
    }


def validate_watch_segments(
    watched_ids: Any,
    segments: Optional[Dict[str, str]],
    claw_segment: Optional[str],
) -> list:
    """Authoring-time check: watch entries this claw could never hear.

    ``classify_watch`` degrades a cross-segment entry to ``unobservable`` at
    RUNTIME, which keeps the field honest but leaves the useless entry in place
    forever, quietly consuming a watch slot and reporting nothing. The real cure
    is upstream: refuse it where a human wrote it (honest_failure_modes #3 —
    validators must reject what the author cannot have meant). Nobody means "watch
    for a transmitter on a frequency this receiver cannot tune".

    Returns a list of human-readable problems, EMPTY when the list is coherent.
    Never raises and never mutates: callers decide whether a problem blocks a
    save or just prints. A node with no declared segment is NOT a problem — an
    undeclared node is unknown, and unknown is not a conflict.
    """
    problems: list = []
    if not claw_segment or not segments:
        return problems
    try:
        ids = list(watched_ids or [])
    except TypeError:
        return ["watch list is not iterable — cannot validate"]
    for node in ids:
        seg = segments.get(node)
        if seg and seg != claw_segment:
            problems.append(
                "%s is declared on RF segment %r but this claw listens on %r — "
                "it can never be heard here. Watch it from a claw on its own "
                "segment, or observe it through the MQTT/RNS bridge instead."
                % (node, seg, claw_segment))
    return problems
