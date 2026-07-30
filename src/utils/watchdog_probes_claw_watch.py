"""The watched-node probe — "is OUR transmitter on the air?"

Split into its own module (2026-07-29) rather than appended to
``watchdog_probes_liveness.py``: that file sits at 1,411 lines and this probe
would carry it past the 1,500-line cap (MF025, whose baseline only shrinks —
you split, you do not grant headroom). Same call, same day, as its sibling
``watchdog_probes_claw_uplink``.

WHY THIS EXISTS — the gap ``claw_rf_silent`` structurally cannot close
------------------------------------------------------------------------
``probe_claw_rf_silent`` reads ``lora.heard_age_s``: the age of the last packet
the claw heard from ANYONE. That makes it a channel witness, and a good one —
but it is blind to the failure the operator actually cares about. With
neighbours transmitting at 6-8 pkt/min, ``heard_age_s`` sits at ~5 s and reads
perfectly healthy while OUR OWN gateway's PA is dead, its region/preset is
wrong, or its coax is unplugged. The air is busy; we are the ones not on it.
A mute transmitter hides inside a healthy channel reading.

The watch list (shipped ``f400f3da``, gated ``5fd5933c``) closes that: each claw
reports, per WATCHED NODE ID, whether it has heard that specific transmitter.
This probe is that field's consumer — the thing that makes the list a check
rather than a number on a panel. Without it the watch list is a
writer-with-no-reader, the defect class this repo keeps paying for (#4, and the
node-cache ``service_type`` drop of 2026-07-21).

WHAT IT WILL AND WILL NOT SAY
-----------------------------
It fires on ``silent`` — never on ``never``. ``never`` is the raw firmware
reading and means only "not since radio start"; the uptime gate in
``mini_dudeai.claw_rf_watch`` converts it to ``silent`` only once the claw has
listened longer than the node's expected transmit interval (default 3 x 3 h).
The day the field shipped, three of four watched radios read ``never`` because
the claw had been up TEN SECONDS. A probe firing on that would page on every
claw reboot, flash and power cycle — the same defect one layer up.

So the gate is not re-derived here. The tick carries JUDGED verdicts and this
reads them, because two independent copies of one threshold drift
(honest_failure_modes #5) and the copy that drifts silently is the one nobody
is looking at.

ESCALATE-ONLY, and the seed rule says so: the 9 h window has never yet produced
a real ``silent`` on this fleet (both claws were flashed hours before this
shipped), so its false-positive rate is UNMEASURED. Paging on an unmeasured
threshold is the "worked once" trap wearing an RF hat — the same call
``claw_rf_silent`` and ``calibration_drift`` made, and calibration_drift soaked
34 days before promotion.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from utils.watchdog_probe_core import (
    Signal,
    _load_parity_streak,
    _save_parity_streak,
    note_disposition,
)
from utils.watchdog_probes_liveness import (
    _operator_home,
    _read_claw_ticks,
    _tick_reachable,
)

DEFAULT_CLAW_WATCH_DEBOUNCE_PATH = "/var/lib/meshforge/claw_watch_debounce.json"

#: Verdict vocabulary, imported from the module that OWNS the gate rather than
#: restated here. A probe carrying its own copy of these strings would keep
#: matching after a rename and quietly judge nothing (the closed-enum shape,
#: honest_failure_modes #7).
#:
#: The guard STAYS (the 2026-07-29 review recommended deleting it; that would
#: break this module on a box without the mini package, and the guarded-mini-
#: import is the deliberate convention in watchdog_probes_mini.py — four
#: instances, each commented "mini package absent in some contexts").
#: What the review was RIGHT about is that the author's claim "a rename is
#: test-pinned" was imprecise: a coordinated rename+revalue that also updates
#: claw_rf_watch's own tests would leave THIS module silently on the fallback,
#: comparing against values the owner no longer emits — every verdict then folds
#: to `blind` and the gate goes permanently quiet. Fail-dark, in the file that
#: ships MF027's sibling.
#: So the fallback is now OBSERVABLE instead of silent: the flag below records
#: that it was taken, and a test asserts it is False wherever mini is importable
#: (which is every fleet box and CI). Blindness with a witness, not blindness.
_VERDICT_CONSTANTS_FROM_FALLBACK = False
try:
    from mini_dudeai.claw_rf_watch import HEARD, SILENT, UNOBSERVABLE
except ImportError:                       # mini package absent in some contexts
    HEARD, SILENT, UNOBSERVABLE = "heard", "silent", "unobservable"
    _VERDICT_CONSTANTS_FROM_FALLBACK = True


def _fold_watch_verdicts(ticks: List[dict]) -> Tuple[Dict[str, dict], int]:
    """Per-NODE view across every reachable claw → ``(nodes, claws_reporting)``.

    A node id may be watched by several claws, so the per-claw verdicts have to
    be arbitrated before anything is claimed about the transmitter:

    * HEARD by ANY claw settles it — the radio is on the air. One deaf receiver
      is that receiver's problem, exactly as ``claw_rf_silent`` treats one deaf
      claw. Hearing is positive physical evidence and outranks every silence.
    * SILENT needs at least one claw whose listening window ELAPSED and heard
      nothing, and no claw that heard it. Deliberately NOT "every claw agrees":
      one freshly-rebooted claw sitting at ``unobservable`` would then mask a
      real finding the other claw has fully qualified — silence-manufacturing,
      the direction this must never fail in.
    * UNOBSERVABLE neither confirms nor refutes. It is kept in its own column
      and never folded into either answer (the whole point of the gate).

    Ticks whose device did not answer are skipped — that is
    ``claw_device_dark``'s call, and a dark claw has nothing to say about a
    transmitter. ``claws_reporting`` counts reachable ticks that actually
    carried verdicts, so "nobody is watching" can be told from "watchers say
    fine".
    """
    nodes: Dict[str, dict] = {}
    reporting = 0
    for t in ticks:
        if _tick_reachable(t) is False:
            continue
        verdicts = t.get("watch_verdicts")
        if not isinstance(verdicts, dict) or not verdicts:
            continue
        device = str(t.get("device") or "?")
        reporting += 1
        for node, v in verdicts.items():
            if not isinstance(v, dict):
                continue
            rec = nodes.setdefault(str(node), {
                "heard_by": [], "silent_by": [], "blind_by": [],
                "required_window_s": None, "silent_for_at_least_s": None,
            })
            need = v.get("required_window_s")
            if isinstance(need, (int, float)) and not isinstance(need, bool):
                # Widest window wins: if two claws disagree on how long this
                # node's interval is, the claim must clear the stricter bar.
                rec["required_window_s"] = max(
                    float(need), rec["required_window_s"] or 0.0)
            verdict = v.get("verdict")
            if verdict == HEARD:
                rec["heard_by"].append({"device": device, "age_s": v.get("age_s")})
            elif verdict == SILENT:
                held = v.get("silent_for_at_least_s")
                rec["silent_by"].append({"device": device,
                                         "silent_for_at_least_s": held})
                if isinstance(held, (int, float)) and not isinstance(held, bool):
                    # Longest observed silence is the honest lower bound — the
                    # firmware only knows "not since radio start", so the true
                    # duration is at least the longest window that missed it.
                    rec["silent_for_at_least_s"] = max(
                        float(held), rec["silent_for_at_least_s"] or 0.0)
            else:
                # UNOBSERVABLE and anything unrecognised. An unknown verdict
                # string is blindness, never healthy and never a finding: a
                # vocabulary that grew must not be read as an answer.
                rec["blind_by"].append({"device": device,
                                        "verdict": verdict,
                                        "reason": v.get("reason")})
    return nodes, reporting


def _classify_nodes(nodes: Dict[str, dict]) -> Tuple[List[str], List[str], List[str]]:
    """``(heard, silent, blind)`` node-id lists — the three columns, kept apart."""
    heard, silent, blind = [], [], []
    for node, rec in nodes.items():
        if rec["heard_by"]:
            heard.append(node)
        elif rec["silent_by"]:
            silent.append(node)
        else:
            blind.append(node)
    return sorted(heard), sorted(silent), sorted(blind)


def probe_claw_watched_node_silent(
    *,
    home: Optional[str] = None,
    ticks: Optional[List[dict]] = None,
    now: Optional[float] = None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """A WATCHED transmitter has not reached any claw for its full window.

    THE distinction this exists to make, and the reason ``claw_rf_silent`` could
    never make it: the channel can be busy while OUR radio is mute. Neighbours
    hold ``heard_age_s`` at a few seconds; this reads per-NODE verdicts, so a
    specific transmitter's silence is visible against that healthy backdrop.

    Fires only on the gated ``silent`` verdict — the claw listened longer than
    the node's expected transmit interval and heard nothing. ``never`` inside
    the window is ``unobservable`` and is NOT a finding.

    Self-guards None, and the reason travels with it:

    * no tick files → INERT (no claw edge node on this box)
    * every tick stale/unparseable → indeterminate (cron_verdict_stale owns the
      dead-capture page)
    * no reachable claw carried verdicts → indeterminate (no watch ids
      configured, or a capture/firmware predating the field) — "nobody is
      watching" is not "nothing is wrong"
    * every watched node still inside its window → indeterminate, NOT clean.
      This is the state after any claw reboot, and it matters that it holds
      rather than clears: the tracker may only clear on a positive observation,
      so a rebooted claw cannot heal a real finding by going blind
      (honest_failure_modes #2).

    2-tick debounce rides out a torn tick. Never raises into the tick.
    """
    try:
        now = time.time() if now is None else now
        sp = state_path or DEFAULT_CLAW_WATCH_DEBOUNCE_PATH
        if ticks is None:
            ticks, seen = _read_claw_ticks(home or _operator_home(), now)
        else:
            seen = len(ticks)

        if not seen:
            note_disposition("claw_watched_node_silent", "inert",
                             reason="no claw tick files (no claw edge node here)")
            _save_parity_streak(sp, 0)
            return None
        if not ticks:
            note_disposition("claw_watched_node_silent", "indeterminate",
                             reason="claw tick file(s) stale/unparseable")
            _save_parity_streak(sp, 0)
            return None

        nodes, reporting = _fold_watch_verdicts(ticks)
        if not reporting or not nodes:
            note_disposition(
                "claw_watched_node_silent", "indeterminate",
                reason="no reachable claw reported watch verdicts (no watch ids "
                       "configured, or a capture predating the watch list) — "
                       "nobody watching is not nothing wrong")
            _save_parity_streak(sp, 0)
            return None

        heard, silent, blind = _classify_nodes(nodes)

        if not silent:
            if not heard:
                # Every watched node is inside its listening window. Held, not
                # cleared — see the docstring.
                note_disposition(
                    "claw_watched_node_silent", "indeterminate",
                    reason=f"{len(blind)} watched node(s) still inside the "
                           f"listening window ({', '.join(blind)}) — no "
                           f"qualified verdict yet; silence means nothing here")
                _save_parity_streak(sp, 0)
                return None
            # The blind count keeps its OWN clause. Folding it into the healthy
            # number is the reassuring-summary defect the gate exists to stop.
            reason = (f"{len(heard)}/{len(nodes)} watched transmitter(s) heard "
                      f"on the air ({', '.join(heard)})")
            if blind:
                reason += (f"; {len(blind)} not yet observable long enough "
                           f"({', '.join(blind)}) — NOT counted as healthy")
            note_disposition("claw_watched_node_silent", "clean", reason=reason)
            _save_parity_streak(sp, 0)
            return None

        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            note_disposition(
                "claw_watched_node_silent", "indeterminate",
                reason=f"watched-node-silent candidate ({', '.join(silent)}), "
                       f"debounce {streak}/{debounce_ticks}")
            return None

        listed = []
        for node in silent:
            rec = nodes[node]
            held = rec["silent_for_at_least_s"]
            need = rec["required_window_s"]
            by = ", ".join(s["device"] for s in rec["silent_by"])
            if held is None:
                listed.append("%s (not heard by %s)" % (node, by))
                continue
            window = "" if not need else ", window %.0fs" % need
            listed.append("%s (not heard in >=%.0fs of listening by %s%s)"
                          % (node, held, by, window))
        blind_clause = ""
        if blind:
            blind_clause = ("; %d still un-observable and NOT counted either way"
                            % len(blind))

        return Signal(
            cls="claw_watched_node_silent",
            subject=silent[0] if len(silent) == 1 else f"{len(silent)} watched nodes",
            severity="degraded",
            detail=(
                f"watched transmitter(s) not reaching any claw: {'; '.join(listed)}. "
                f"This is the MUTE-TRANSMITTER case claw_rf_silent structurally "
                f"cannot see: with neighbours chattering the channel reads busy "
                f"(heard_age_s a few seconds) while OUR radio is not on the air. "
                f"Check that node's own TX leg — region/preset, PA, antenna and "
                f"coax — not the channel. The claws are independent receivers on "
                f"separate silicon, so this is not a box talking about itself. "
                f"{len(heard)} other watched node(s) ARE heard{blind_clause}. "
                f"NOTE: the listening window is PROVISIONAL — no real `silent` "
                f"verdict has been observed on this fleet yet, so this escalates "
                f"and does not page."
            ),
            extra={
                "silent_nodes": {n: nodes[n] for n in silent},
                "heard_nodes": heard,
                "unobservable_nodes": blind,
                "claws_reporting": reporting,
                "threshold_provisional": True,
            },
        )
    except Exception:
        note_disposition("claw_watched_node_silent", "indeterminate",
                         reason="probe raised unexpectedly; unobservable this tick")
        return None
