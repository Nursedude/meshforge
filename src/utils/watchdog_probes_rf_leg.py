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
#: used until a peer cadence has been observed. 20 ticks × 30 s = 600 s = one
#: ``id_interval`` of the fleet's RNode config: a peer is allowed to be exactly
#: that quiet without anything being wrong.
MIN_SILENCE_TICKS = 20

#: Once a cadence HAS been observed (the longest gap between RX increases, in
#: ticks), the silence must exceed this multiple of it. Two missed announces in
#: a row is deafness; one is a lost packet.
GAP_FACTOR = 2.0

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


def required_silence_ticks(rx_gap_max: int, *, min_silence_ticks: int = MIN_SILENCE_TICKS,
                           gap_factor: float = GAP_FACTOR) -> int:
    """How long RX must be flat before "silent" means "deaf" for this peer."""
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
        rx_gap_max = int(prior.get("rx_gap_max", 0))
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
                rx_gap_max = max(rx_gap_max, streak + 1)
            streak, tx_at_flat_start, gap_valid = 0, tx, True
        elif not ever_rx:
            # Never heard a peer. True, and not a fault — this is what a
            # single-node RF network looks like.
            streak, tx_at_flat_start = 0, tx
        else:
            streak += 1

        if ever_rx and up:
            judged += 1

        state[key] = {
            "ever_rx": ever_rx, "last_tx": tx, "last_rx": rx,
            "flat_streak": streak, "tx_at_flat_start": tx_at_flat_start,
            "rx_gap_max": rx_gap_max, "gap_valid": gap_valid,
        }

        required = required_silence_ticks(
            rx_gap_max, min_silence_ticks=min_silence_ticks, gap_factor=gap_factor)
        tx_in_window = tx - tx_at_flat_start
        if up and ever_rx and streak >= required and tx_in_window >= min_tx_delta:
            cadence = (f"the longest silence this peer ever showed was {rx_gap_max} ticks"
                       if rx_gap_max else "no peer cadence observed yet; floor applied")
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
                ". This leg has received before, so a peer exists. Check the "
                "radio has not been left in a non-default MODE — promiscuous "
                "is the known cause, RNS never clears it, and a power cycle or "
                "an explicit CMD_PROMISC 0x00 is the fix. ⚠️ If IP backhaul is "
                "up, traffic is still flowing and NOTHING ELSE will report this."
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
