"""watchdog_probes_rf_leg — is the RF leg actually CARRYING traffic?

Born 2026-09-08 from a self-inflicted outage. A capture tool left two RNodes in
promiscuous mode; both stopped receiving, and stayed that way. Every surface we
owned still said healthy: the interface read ``Status: Up``, the service was
active, the box was reachable, and traffic kept flowing — over TCP. The dead RF
leg was invisible for hours because a redundant path was masking it.

``Status: Up`` is PRESENCE. This probe asks for FUNCTION.

⚠️ Why this matters most for the pure-RF gateway variant: with IP backhaul, a
dead RF leg is an invisible degradation. Without one, it is a total outage. A
fallback mode that is never exercised alone rots silently, and IP being up is
exactly what hides the rot.

The discriminator (this is the whole design)
--------------------------------------------
"transmitting with zero received" is NOT a fault by itself — it is the normal,
correct state of a box whose RF network has no other node on it. The fleet's
only RNode sat at ``↑1.15 MB / ↓0 B`` for months, by design, because nothing
else was on the air. A probe that fired on that would be wrong every day.

What IS a fault is a leg that used to hear and has gone deaf. So:

* the interface must have been observed **receiving at least once**, ever —
  otherwise ``inert``, with the honest reason ("no RF peer has ever been heard
  here"), which is information rather than an alarm;
* then RX must stay **exactly flat** for LONGER THAN THE PEER HAS EVER BEEN
  SILENT BEFORE, while TX has moved by at least ``MIN_TX_DELTA_BYTES`` across
  that same silent window — "we are using the leg and nothing comes back".

Why "longer than the peer has ever been silent" (review 2026-09-09, finding 2)
-----------------------------------------------------------------------------
The first version fired after three consecutive ticks of "TX +200 B, RX flat".
That measures the gap BETWEEN a peer's announces, not deafness: the fleet's RF
peer runs ``id_interval = 600``, so it is legitimately silent for ~20 ticks at
a time, and any 3-tick TX burst in that window paged — the memory note's
"first firing was a false positive (radio healthy)". The same shape fired 90 s
after a routine rnsd restart, because the restart zeroed ``last_rx`` and "flat"
was trivially true against a zeroed baseline.

So the silence requirement is now self-calibrating: each time RX moves, the
number of flat ticks it took is recorded, and the longest such gap ever seen
(``rx_gap_max``) is the peer's observed cadence. The probe fires only once the
current silence exceeds ``GAP_FACTOR`` × that cadence — and never before
``MIN_SILENCE_TICKS`` (one ``id_interval`` at the 30 s tick), which is the floor
used while no cadence has been observed yet. Ticks, not wall-clock: durations
are forgeable on this fleet (RTC-less Pis, honest_failure_modes #6).

The TX requirement is CUMULATIVE over the silent window, not per tick. A quiet
leg's own TX is sparse too (its own ID every 600 s); a per-tick "+200 B" test
reset the streak on every tick without TX, so on a quiet leg the old probe could
never fire either — blind in the exact case it was written for.

Counters, and why this probe spawns ``rnstatus -j`` (finding 1)
---------------------------------------------------------------
rnstatus's human output renders byte counters through ``RNS.prettysize`` —
``"%.2f MB"`` above 1 MB, i.e. a 10,000 B display quantum. On the fleet's only
RNode (1.15 MB up) a whole tick of RF traffic is invisible in that text, so a
probe reading it was structurally blind to the fault and could page falsely
when the display happened to step. The raw integer ``rxb``/``txb`` are in
``rnstatus -j``. That is read here as a bounded SUBPROCESS, not by opening a
Reticulum RPC handle inside the watchdog: a wedged rnsd must not hang the
watchdog thread (#68/#72), and a subprocess with ``timeout=`` cannot. The
parsed text status the runner already fetched is still the gate — it decides
"rnstatus unobservable" and "no RNode here" without a second spawn on the
eight boxes that have no RF leg.

State
-----
Per-interface bookkeeping is persisted, memory-first (the ``_state_mem_fallback``
pattern from ``watchdog_probes_peer_rf``): a write failure goes through
``note_state_write_failure`` AND the in-process copy keeps the streak alive, so
an unwritable ``/var/lib/meshforge`` (#60 sandbox class, lint MF028) degrades to
"does not survive a restart" rather than "restarts from zero every tick and can
never fire" — the frozen-green shape of the 2026-09-02 audit (finding 8).
"""

from __future__ import annotations

import json
import math
import os
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from utils.rns_status_parser import _find_rnstatus_binary
from utils.watchdog_probe_core import (
    Signal,
    note_disposition,
    note_state_write_failure,
)

# The class name below is repeated as a LITERAL at every emit site, and that is
# deliberate — do not hoist it back to a module constant. Three gates read this
# module as TEXT and recognise only a literal ``Signal(cls="...")`` /
# ``note_disposition("...")``: the run_all_probes wiring gate, the
# disposition-adoption gate, and the closed-enum documentation gate. This module
# shipped 2026-09-08 emitting through a module constant and all three went blind
# at once — they reported the probe as UNWIRED and DISPOSITION-LESS while it was
# correctly called in run_all_probes and noting four honest dispositions. They
# failed CLOSED, which is the safe direction, but their message sent the reader
# to add a duplicate probe call. Every other signal class in the tree uses
# literals; this module was the only outlier. Keep it that way.
SIGNAL_CLASS_DOC = "rf_leg_silent"  # for humans; the code below uses literals

DEFAULT_STATE_PATH = "/var/lib/meshforge/rf_leg_state.json"

#: TX growth (bytes) across the whole silent window that counts as "we are
#: actually using this leg". Cumulative, not per tick — see the module header.
MIN_TX_DELTA_BYTES = 200.0

#: Floor on how many consecutive flat-RX ticks are required before firing,
#: applied once the cadence is CALIBRATED. 20 ticks × 30 s = 600 s = one
#: ``id_interval`` of the fleet's RNode config: a peer is allowed to be exactly
#: that quiet without anything being wrong.
MIN_SILENCE_TICKS = 20

#: Once a cadence HAS been observed (the longest gap between RX increases, in
#: ticks), the silence must exceed this multiple of it. Two missed announces in
#: a row is deafness; one is a lost packet.
GAP_FACTOR = 2.0

#: How many RX gaps must be observed before ``rx_gap_max`` is trusted as this
#: peer's cadence. Measured 2026-09-10 (delta
#: ``chronic_flap::rf_leg_silent_any::RNodeInterface[RNode LoRa]``): one gateway
#: box fired 15× and a second RNode box 5× — every one a false positive on a
#: healthy leg whose RX
#: counter kept climbing — because ONE observed gap was enough to set the
#: threshold. moc3's estimate climbed 16 → 52 ticks as each false fire taught it
#: more, so the flap was UNDER-calibration, not a noisy subject. An estimate
#: built from a single sample is a guess; say so and use the conservative floor.
MIN_GAP_OBSERVATIONS = 5

#: The threshold to use while the cadence is UNCALIBRATED (fewer than
#: ``MIN_GAP_OBSERVATIONS`` gaps seen). 120 ticks × 30 s = 1 h. This is
#: deliberately far above the calibrated floor: the fault this probe exists for
#: left two radios deaf for HOURS, so an hour of cold-start conservatism costs
#: nothing real, while the 20-tick floor cost 20 false fires in two days.
UNCALIBRATED_SILENCE_TICKS = 120

#: How long an observed gap stays in a leg's window, in TICKS. ``rx_gap_max``
#: was once a monotonic max that never decayed, so a GENUINE multi-hour
#: deafness episode — once it ended and the peer was heard again — taught the
#: probe that multi-hour silence is this peer's normal cadence, desensitising
#: it against the exact fault it was built to catch. The 2026-09-10 cure
#: bounded it, but bounded it by RX EVENTS (``GAP_WINDOW = 10``), and a count
#: of events is not a duration.
#:
#: On a CHATTY leg ten RX events is ten TICKS. A peer heard on consecutive
#: ticks appends ``streak + 1`` = 1, so the window filled with 1s, a genuine
#: long silence was evicted about five minutes after it was observed,
#: ``rx_gap_max`` collapsed to 1, ``required`` pinned at the 20-tick floor,
#: and the next real lull false-fired — the chronic_flap the 09-10 work set
#: out to end, reappearing in steady state. Measured live on a fleet box
#: on 2026-09-13: the window was ``[3,4,1,1,1,3,4,2,8,8]`` on a leg whose cadence
#: reaches 52, giving a threshold of 20 against a normal silence of 52.
#:
#: 720 ticks × 30 s = 6 h. Ten events on a ~50-tick leg was ~4 h, which is the
#: behaviour the bound was tuned on, and it is still bounded so a real
#: multi-hour outage ages out rather than being carried forever.
GAP_MEMORY_TICKS = 720

#: The observations must SPAN at least this many ticks before ``rx_gap_max`` is
#: trusted, however many of them there are. Five gaps of one tick observed
#: inside five ticks satisfy ``MIN_GAP_OBSERVATIONS`` while saying nothing
#: about a leg whose real cadence is 52: a burst of chatter is not evidence of
#: a short cadence, and without this gate a chatty-then-quiet leg calibrates to
#: the floor and fires on its first normal lull. Same hour of cold-start
#: conservatism as ``UNCALIBRATED_SILENCE_TICKS``, for the same reason.
MIN_GAP_SPAN_TICKS = 120

#: Defensive cap on stored gap entries. At most one gap is observed per tick,
#: so ``GAP_MEMORY_TICKS`` already bounds the list; this only limits the damage
#: from a corrupted or hand-edited state file.
GAP_ENTRIES_CAP = 1000

#: Bound on the ``rnstatus -j`` subprocess. Same as the runner's text call.
RAW_STATS_TIMEOUT_S = 8.0

#: In-process copy of the state, memory-first. A broken state dir usually keeps
#: the OLD file readable while every write fails, so disk must not outrank what
#: this process already observed (finding 8 — without this, an unwritable
#: ``/var/lib/meshforge`` reset the streak to 0 on every tick and the probe
#: could never fire, while note_state_write_failure promised the value was
#: "held in-process"). Keyed by state path so tests and legs never collide.
_state_mem_fallback: Dict[str, Dict[str, dict]] = {}


def _load_state(state_path: str) -> Dict[str, dict]:
    if state_path in _state_mem_fallback:
        return _state_mem_fallback[state_path]
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        # Absent or corrupt state is not an alarm; it just means no history yet.
        return {}


def _save_state(state_path: str, state: Dict[str, dict]) -> None:
    """Keep the in-process copy UNCONDITIONALLY, then try the disk."""
    _state_mem_fallback[state_path] = state
    try:
        parent = os.path.dirname(state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, separators=(",", ":"))
        os.replace(tmp, state_path)
    except OSError as e:
        note_state_write_failure(state_path, e)


def _read_raw_interface_stats(
        timeout_s: float = RAW_STATS_TIMEOUT_S) -> Tuple[Optional[List[dict]], str]:
    """The ``interfaces`` list of ``rnstatus -j`` — integer ``rxb``/``txb`` —
    or ``(None, why)``. None means "could not look", never "no traffic"."""
    binary = _find_rnstatus_binary()
    if not binary:
        return None, "rnstatus binary not found"
    try:
        proc = subprocess.run([binary, "-j"], capture_output=True, text=True,
                              timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return None, f"rnstatus -j timed out after {timeout_s:.0f}s (rnsd unresponsive?)"
    except OSError as e:
        return None, f"could not run rnstatus -j: {e}"
    if proc.returncode != 0:
        first = ((proc.stderr or proc.stdout or "").strip().splitlines() or ["(no output)"])[0]
        return None, f"rnstatus -j exited {proc.returncode}: {first[:120]}"
    try:
        doc = json.loads(proc.stdout or "")
    except ValueError as e:
        return None, f"rnstatus -j output is not JSON: {e}"
    ifaces = doc.get("interfaces") if isinstance(doc, dict) else None
    if not isinstance(ifaces, list):
        return None, "rnstatus -j carried no interfaces list"
    return ifaces, "ok"


def _int_counter(entry: dict, key: str) -> Optional[int]:
    val = entry.get(key)
    if isinstance(val, bool) or not isinstance(val, int):
        return None
    return val


def _load_gaps(prior: Dict[str, Any]) -> List[List[int]]:
    """``prior``'s gap window as ``[[gap, age_ticks], ...]``, newest last.

    Accepts three shapes so an upgrade never silently discards calibration:
    the current pair form; the 2026-09-10 event-windowed list of plain ints
    (loaded at age 0 — it carries no ages); and pre-window state holding only
    a monotonic ``rx_gap_max`` (seeded as ONE sample at age 0, which reads as
    uncalibrated until the peer re-earns it). Anything malformed is dropped
    rather than coerced: a bad entry must not become a plausible cadence.
    """
    raw = prior.get("recent_gaps")
    out: List[List[int]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, bool):
                continue
            if isinstance(item, int) and item > 0:
                out.append([item, 0])
            elif (isinstance(item, (list, tuple)) and len(item) == 2
                  and all(isinstance(v, int) and not isinstance(v, bool)
                          for v in item)
                  and item[0] > 0 and item[1] >= 0):
                out.append([int(item[0]), int(item[1])])
        return out
    seed = prior.get("rx_gap_max")
    if isinstance(seed, int) and not isinstance(seed, bool) and seed > 0:
        out.append([seed, 0])
    return out


def required_silence_ticks(rx_gap_max: int, *, min_silence_ticks: int = MIN_SILENCE_TICKS,
                           gap_factor: float = GAP_FACTOR,
                           gap_count: int = 0,
                           gap_span_ticks: int = 0,
                           min_gap_observations: int = MIN_GAP_OBSERVATIONS,
                           min_gap_span_ticks: int = MIN_GAP_SPAN_TICKS,
                           uncalibrated_ticks: int = UNCALIBRATED_SILENCE_TICKS) -> int:
    """How long RX must be flat before "silent" means "deaf" for this peer.

    ``gap_count`` is how many RX gaps have been observed; ``gap_span_ticks`` is
    how long ago the OLDEST of them was recorded. BOTH gates must pass before
    the cadence estimate is trusted — too few samples is a guess
    (``MIN_GAP_OBSERVATIONS``), and enough samples crowded into a few ticks is
    also a guess (``MIN_GAP_SPAN_TICKS``). Otherwise the conservative
    ``uncalibrated_ticks`` applies, never a threshold derived from one sighting
    or from one burst.

    Both gate arguments default to 0, i.e. to ``uncalibrated_ticks``: a caller
    that omits them gets the SAFE answer, never a narrower threshold.
    """
    if gap_count < min_gap_observations or gap_span_ticks < min_gap_span_ticks:
        return int(uncalibrated_ticks)
    if rx_gap_max <= 0:
        return int(min_silence_ticks)
    return max(int(min_silence_ticks), int(math.ceil(gap_factor * rx_gap_max)))


def probe_rf_leg_silent(
    *,
    status=None,
    raw_stats: Optional[List[dict]] = None,
    state_path: str = DEFAULT_STATE_PATH,
    min_tx_delta: float = MIN_TX_DELTA_BYTES,
    min_silence_ticks: int = MIN_SILENCE_TICKS,
    gap_factor: float = GAP_FACTOR,
    min_gap_observations: int = MIN_GAP_OBSERVATIONS,
    min_gap_span_ticks: int = MIN_GAP_SPAN_TICKS,
    gap_memory_ticks: int = GAP_MEMORY_TICKS,
    uncalibrated_ticks: int = UNCALIBRATED_SILENCE_TICKS,
    raw_timeout_s: float = RAW_STATS_TIMEOUT_S,
) -> Optional[Signal]:
    """Judge each RNode RF interface on bytes MOVED, not on interface state.

    ``status`` is the parsed text ``RNSStatus`` (the runner's shared call;
    None → run rnstatus). It is the GATE: unobservable → indeterminate, no
    RNode → inert. ``raw_stats`` is the ``rnstatus -j`` interfaces list
    (injectable for tests); None → spawn it, bounded by ``raw_timeout_s``.
    """
    if status is None:
        try:
            from utils.rns_status_parser import run_rnstatus
            status = run_rnstatus()
        except Exception as e:  # noqa: BLE001 - unobservable, never "healthy"
            note_disposition("rf_leg_silent", "indeterminate",
                             reason=f"rnstatus unavailable: {e}")
            return None

    # Finding 3: a timed-out / errored rnstatus has an EMPTY interfaces list,
    # which read as "no RNode configured here" (inert) — a #72 wedge on the
    # RNode box rendered as absent-by-design for its whole duration.
    parse_error = getattr(status, "parse_error", None)
    if parse_error or getattr(status, "timed_out", False):
        note_disposition("rf_leg_silent", "indeterminate",
                         reason=f"rnstatus unobservable: {parse_error or 'timed out'}")
        return None

    text_ifaces = [i for i in getattr(status, "interfaces", [])
                   if "RNodeInterface" in getattr(i, "type_name", "")]
    if not text_ifaces:
        note_disposition("rf_leg_silent", "inert",
                         reason="no RNode RF interface configured on this box")
        return None
    enrolled = len(text_ifaces)

    if raw_stats is None:
        raw_stats, why = _read_raw_interface_stats(raw_timeout_s)
        if raw_stats is None:
            note_disposition(
                "rf_leg_silent", "indeterminate",
                reason=(f"raw byte counters unreadable ({why}); the text counters "
                        "are display-quantised (10 kB above 1 MB) and cannot "
                        "judge one tick of RF traffic"),
                coverage={"judged": 0, "enrolled": enrolled})
            return None

    entries = [e for e in raw_stats if isinstance(e, dict)
               and "RNodeInterface" in str(e.get("type") or e.get("name") or "")]
    if not entries:
        note_disposition(
            "rf_leg_silent", "indeterminate",
            reason=(f"rnstatus text lists {enrolled} RNode interface(s) but "
                    "rnstatus -j carried none — cannot judge byte movement"),
            coverage={"judged": 0, "enrolled": enrolled})
        return None

    state = _load_state(state_path)
    findings: List[str] = []
    judged = 0
    unreadable: List[str] = []

    for entry in entries:
        key = str(entry.get("name") or f"RNodeInterface[{entry.get('short_name', '?')}]")
        tx = _int_counter(entry, "txb")
        rx = _int_counter(entry, "rxb")
        if tx is None or rx is None:
            unreadable.append(key)
            continue

        prior: Dict[str, Any] = state.get(key, {})
        ever_rx = bool(prior.get("ever_rx")) or rx > 0
        last_tx = int(prior.get("last_tx", 0))
        last_rx = int(prior.get("last_rx", 0))
        streak = int(prior.get("flat_streak", 0))
        tx_at_flat_start = int(prior.get("tx_at_flat_start", tx))
        # Tick-aged window of observed gaps (newest last), each [gap, age].
        # See GAP_MEMORY_TICKS for why the bound is a DURATION and not a count
        # of RX events. Age every entry by this tick and forget what has
        # outlived the memory — this happens on every branch below, including
        # a down interface and a counter reset, because time passed either way.
        recent_gaps = _load_gaps(prior)
        recent_gaps = [[g, a + 1] for g, a in recent_gaps
                       if a + 1 < gap_memory_ticks]
        if len(recent_gaps) > GAP_ENTRIES_CAP:
            recent_gaps = recent_gaps[-GAP_ENTRIES_CAP:]
        gap_valid = bool(prior.get("gap_valid", False))
        up = entry.get("status") is True

        if not up:
            # An interface that is down is another probe's subject entirely.
            streak, tx_at_flat_start, gap_valid = 0, tx, False
        elif tx < last_tx or rx < last_rx:
            # Counters went backwards: rnsd restarted. Not deafness — and the
            # next RX gap is measured from the restart, not from the last real
            # RX, so it must not calibrate the cadence (it would undercount).
            streak, tx_at_flat_start, gap_valid = 0, tx, False
        elif rx > last_rx:
            # The peer was heard. The silence that just ended is a cadence
            # observation — but only if it was measured from a real RX event.
            if gap_valid and prior:
                recent_gaps.append([streak + 1, 0])
            streak, tx_at_flat_start, gap_valid = 0, tx, True
        elif not ever_rx:
            # Never heard a peer. True, and not a fault — this is what a
            # single-node RF network looks like.
            streak, tx_at_flat_start = 0, tx
        else:
            streak += 1

        if ever_rx and up:
            judged += 1

        # `rx_gap_max` is now DERIVED from the bounded window, not accumulated,
        # so an outlier leaves the estimate once it ages out. Still persisted:
        # it is what the detail line quotes and what older readers expect.
        rx_gap_max = max((g for g, _ in recent_gaps), default=0)
        gap_span = max((a for _, a in recent_gaps), default=0)
        state[key] = {
            "ever_rx": ever_rx, "last_tx": tx, "last_rx": rx,
            "flat_streak": streak, "tx_at_flat_start": tx_at_flat_start,
            "rx_gap_max": rx_gap_max, "gap_valid": gap_valid,
            "recent_gaps": recent_gaps,
        }

        required = required_silence_ticks(
            rx_gap_max, min_silence_ticks=min_silence_ticks, gap_factor=gap_factor,
            gap_count=len(recent_gaps), gap_span_ticks=gap_span,
            min_gap_observations=min_gap_observations,
            min_gap_span_ticks=min_gap_span_ticks,
            uncalibrated_ticks=uncalibrated_ticks)
        tx_in_window = tx - tx_at_flat_start
        if up and ever_rx and streak >= required and tx_in_window >= min_tx_delta:
            if (len(recent_gaps) < min_gap_observations
                    or gap_span < min_gap_span_ticks):
                cadence = (f"cadence UNCALIBRATED ({len(recent_gaps)} of "
                           f"{min_gap_observations} gaps, spanning {gap_span} of "
                           f"{min_gap_span_ticks} ticks); conservative floor applied")
            else:
                cadence = (f"the longest of {len(recent_gaps)} silences observed "
                           f"over the last {gap_span} ticks was {rx_gap_max} ticks")
            findings.append(
                f"{key}: RX pinned at {rx} B for {streak} consecutive ticks "
                f"(threshold {required}; {cadence}) while TX moved {tx_in_window} B "
                "across that window, on a leg that has received before")

    _save_state(state_path, state)

    coverage = {"judged": judged, "enrolled": enrolled}
    if findings:
        note_disposition("rf_leg_silent", "degraded", reason="; ".join(findings),
                         coverage=coverage)
        return Signal(
            cls="rf_leg_silent",
            subject=findings[0].split(":")[0],
            severity="degraded",
            detail=(
                "RF leg is transmitting into silence — " + "; ".join(findings) +
                ". This leg has received before, so a peer exists. Two known "
                "causes, each with its own fix. (1) SF DRIFT: the radio "
                "reverted to its stored defaults after a reset and rnsd never "
                "re-validates — `rnstatus` Rate will not match the configured "
                "SF/BW (SF7/250 kHz = 10.94 kbps; 2026-09-22 read 585.94 bps = "
                "SF12). Fix: restart rnsd, then check the @rns/ owner is rnsd. "
                "(2) promiscuous mode left on by a tool — Rate reads right, RNS "
                "never clears it; a power cycle or an explicit CMD_PROMISC 0x00 "
                "is the fix. ⚠️ If IP backhaul is up, traffic is still flowing "
                "and NOTHING ELSE will report this."
            ),
            extra={"interfaces": findings},
        )

    if unreadable and judged == 0:
        note_disposition(
            "rf_leg_silent", "indeterminate",
            reason=f"rnstatus -j carried no integer rxb/txb for {', '.join(unreadable)}",
            coverage={"judged": 0, "enrolled": enrolled})
        return None

    if judged == 0:
        note_disposition("rf_leg_silent", "inert",
                         reason="no RF peer has ever been heard on this box's "
                                "RNode leg(s); nothing to judge yet",
                         coverage=coverage)
        return None

    note_disposition("rf_leg_silent", "clean", coverage=coverage)
    return None
